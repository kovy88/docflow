"""LLM extraction. The model returns, for every template field, the raw value plus the
page and a verbatim quote it came from, so every answer can be checked against the PDF."""

import json
from typing import Any, Protocol

from openai import InternalServerError, OpenAI
from pydantic import BaseModel, Field, ValidationError, create_model

from app.templates import Template

INSTRUCTIONS = """You extract structured data from business documents (often in Czech).
Rules:
- Copy each value exactly as written in the document; do not translate, compute or guess.
- If a value is not present in the document, return null for value, page and quote.
- page is the number from the <page number=...> tag where the value appears.
- quote is a short verbatim snippet (max ~150 characters) from that page containing the value.
- confidence is your probability (0-1) that the value is correct and correctly assigned."""


class FieldAnswer(BaseModel):
    value: str | None = Field(description="Value exactly as written, or null if absent")
    page: int | None = Field(description="Page number where the value appears")
    quote: str | None = Field(description="Verbatim snippet from the page containing the value")
    confidence: float = Field(description="Probability 0-1 that the value is correct")


class ExtractionOutput(BaseModel):
    answers: dict[str, FieldAnswer]
    usage: dict[str, Any] = {}


class Extractor(Protocol):
    model: str

    def extract(self, pages: list[str], template: Template) -> ExtractionOutput: ...


def response_model(template: Template) -> type[BaseModel]:
    fields: dict[str, Any] = {
        f.key: (FieldAnswer, Field(description=f"{f.label}. {f.description}".strip()))
        for f in template.fields
    }
    return create_model(f"{template.id.title()}Extraction", **fields)


def render_pages(pages: list[str]) -> str:
    return "\n\n".join(f'<page number="{i}">\n{text}\n</page>' for i, text in enumerate(pages, 1))


def instructions_for(template: Template) -> str:
    return f"{INSTRUCTIONS}\n\nDocument type: {template.name}. {template.description}"


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace $ref with the referenced definition; small local models handle flat schemas better."""
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def loads_lenient(raw: str) -> Any:
    """Parse model JSON: tolerate code fences/prose around it and nested objects
    encoded as JSON strings (seen with local models)."""
    start, end = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[start : end + 1] if start != -1 else raw)
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str) and value.lstrip().startswith("{"):
                try:
                    data[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
    return data


class ToolCallExtractor:
    """For local models served by Ollama. Grammar-constrained output is not available for
    every model/quantisation, so the schema is passed as a forced tool call and the
    arguments are validated with Pydantic. If Ollama cannot parse the tool-call markup,
    the next attempt asks for plain JSON in the message instead; invalid output is retried
    with the validation error as feedback."""

    tool_name = "submit_extraction"

    def __init__(self, base_url: str, model: str, timeout: float, retries: int = 1):
        self.client = OpenAI(base_url=base_url, api_key="ollama", timeout=timeout)
        self.model = model
        self.retries = retries

    def extract(self, pages: list[str], template: Template) -> ExtractionOutput:
        schema_model = response_model(template)
        schema = inline_refs(schema_model.model_json_schema())
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": instructions_for(template)},
            {"role": "user", "content": render_pages(pages)},
        ]
        tool = {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": "Submit the extracted fields.",
                "parameters": schema,
            },
        }
        json_only = {
            "role": "user",
            "content": "Answer with a single JSON object matching this schema, no other text:\n"
            + json.dumps(schema, ensure_ascii=False),
        }
        usage = {"input_tokens": 0, "output_tokens": 0, "attempts": 0, "mode": "tool"}
        for attempt in range(self.retries + 1):
            usage["attempts"] = attempt + 1
            kwargs: dict[str, Any] = {}
            if usage["mode"] == "tool":
                kwargs = {
                    "tools": [tool],
                    "tool_choice": {"type": "function", "function": {"name": self.tool_name}},
                }
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages + ([json_only] if usage["mode"] == "json" else []),
                    temperature=0,
                    reasoning_effort="none",
                    **kwargs,
                )
            except InternalServerError:
                # Ollama returns 500 when it cannot parse the model's tool-call markup.
                if attempt == self.retries or usage["mode"] == "json":
                    raise
                usage["mode"] = "json"
                continue
            if response.usage:
                usage["input_tokens"] += response.usage.prompt_tokens
                usage["output_tokens"] += response.usage.completion_tokens
            message = response.choices[0].message
            raw = (
                message.tool_calls[0].function.arguments if message.tool_calls else message.content
            ) or ""
            try:
                parsed = schema_model.model_validate(loads_lenient(raw))
            except (ValueError, ValidationError) as exc:
                if attempt == self.retries:
                    raise RuntimeError(f"Model output does not match the template: {exc}") from exc
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Invalid output, fix it:\n{exc}"})
                continue
            return ExtractionOutput(
                answers={f.key: getattr(parsed, f.key) for f in template.fields}, usage=usage
            )
        raise AssertionError("unreachable")


class OpenAIExtractor:
    def __init__(self, api_key: str, model: str, timeout: float):
        self.client = OpenAI(api_key=api_key, timeout=timeout)
        self.model = model

    def extract(self, pages: list[str], template: Template) -> ExtractionOutput:
        response = self.client.responses.parse(
            model=self.model,
            instructions=instructions_for(template),
            input=render_pages(pages),
            text_format=response_model(template),
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("Model returned no parsable output")
        usage = response.usage
        return ExtractionOutput(
            answers={f.key: getattr(parsed, f.key) for f in template.fields},
            usage={
                "input_tokens": usage.input_tokens if usage else None,
                "output_tokens": usage.output_tokens if usage else None,
            },
        )
