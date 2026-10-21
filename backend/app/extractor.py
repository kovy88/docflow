"""LLM extraction. The model returns, for every template field, the raw value plus the
page and a verbatim quote it came from, so every answer can be checked against the PDF."""

import json
import re
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Literal, Protocol, TypeVar

from openai import InternalServerError, OpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, Field, ValidationError, create_model

from app.templates import IcoRule, Template, TemplateField

T = TypeVar("T", bound=BaseModel)

INSTRUCTIONS = """You extract structured data from business documents (often in Czech).
Rules:
- Copy each value exactly as written in the document; do not translate, compute or guess.
- If a value is not present in the document, return null for value, page and quote.
- page is the number from the <page number=...> tag where the value appears.
- quote is a short verbatim snippet (max ~150 characters) from that page containing the value.
- confidence is your probability (0-1) that the value is correct and correctly assigned.
- Never fill a field with a value that belongs to a different field: the issue date is not
  the due date, the total is not the tax base.
- If the document gives no explicit value for a field (e.g. "splatné ihned", or a duration
  such as "12 měsíců" instead of an end date), return null instead of inferring one."""
# Bump when INSTRUCTIONS change, so evaluation runs stay comparable.
PROMPT_VERSION = 3  # 3: field labels/descriptions reach Ollama and OpenRouter


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

    def structured(
        self, system: str, user: str, schema_model: type[T]
    ) -> tuple[T, dict[str, Any]]: ...


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
                # Keep the field's own description next to the ref; it carries the label.
                siblings = {k: resolve(v) for k, v in node.items() if k != "$ref"}
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]]) | siblings
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
    return _unstring(data, depth=0)


def _unstring(node: Any, depth: int) -> Any:
    if isinstance(node, str) and depth > 0 and node.lstrip()[:1] in ("{", "["):
        try:
            node = json.loads(node)
        except json.JSONDecodeError:
            return node
    if isinstance(node, dict):
        return {k: _unstring(v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_unstring(v, depth + 1) for v in node]
    return node


class ToolCallExtractor:
    """For local models served by Ollama.

    Grammar-constrained output is not available for every model/quantisation, so the
    schema is passed as a forced tool call and the arguments are validated with Pydantic.
    Ollama runs the model's own output parser on every reply; when the model emits
    malformed tool-call markup that parser fails with HTTP 500 (8 % of documents in our
    evaluation). The fallback is a raw completion via /api/generate, which bypasses the
    renderer and parser: the ChatML prompt is built here and plain JSON is parsed by us.
    Output that fails validation is retried with the error as feedback."""

    tool_name = "submit"

    def __init__(self, base_url: str, model: str, timeout: float, retries: int = 1):
        self.client = OpenAI(base_url=base_url, api_key="ollama", timeout=timeout)
        self.native_url = base_url.removesuffix("/").removesuffix("/v1")
        self.model = model
        self.timeout = timeout
        self.retries = retries

    def structured(self, system: str, user: str, schema_model: type[T]) -> tuple[T, dict[str, Any]]:
        schema = inline_refs(schema_model.model_json_schema())
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        usage = {"input_tokens": 0, "output_tokens": 0, "attempts": 0, "mode": "tool"}
        invalid = 0  # validation retries; the switch to raw mode does not count
        while True:
            usage["attempts"] += 1
            try:
                if usage["mode"] == "tool":
                    raw, used = self._tool_call(messages, schema)
                else:
                    raw, used = self._raw_completion(messages, schema)
            except InternalServerError:
                if usage["mode"] == "raw":
                    raise
                usage["mode"] = "raw"
                continue
            usage["input_tokens"] += used[0]
            usage["output_tokens"] += used[1]
            try:
                return schema_model.model_validate(loads_lenient(raw)), usage
            except (ValueError, ValidationError) as exc:
                invalid += 1
                if invalid > self.retries:
                    raise RuntimeError(f"Model output does not match the schema: {exc}") from exc
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Invalid output, fix it:\n{exc}"})

    def _tool_call(self, messages: list[dict], schema: dict) -> tuple[str, tuple[int, int]]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            reasoning_effort="none",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": self.tool_name,
                        "description": "Submit the result.",
                        "parameters": schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": self.tool_name}},
        )
        message = response.choices[0].message
        raw = (
            message.tool_calls[0].function.arguments if message.tool_calls else message.content
        ) or ""
        u = response.usage
        return raw, (u.prompt_tokens, u.completion_tokens) if u else (0, 0)

    def _raw_completion(self, messages: list[dict], schema: dict) -> tuple[str, tuple[int, int]]:
        instruction = "Answer with a single JSON object matching this schema, no other text:\n"
        turns = messages + [
            {"role": "user", "content": instruction + json.dumps(schema, ensure_ascii=False)}
        ]
        # ChatML as used by Qwen; the empty think block disables reasoning.
        prompt = "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in turns)
        prompt += "<|im_start|>assistant\n<think>\n\n</think>\n\n"
        body = {
            "model": self.model,
            "prompt": prompt,
            "raw": True,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": 32768},
        }
        request = urllib.request.Request(
            f"{self.native_url}/api/generate",
            json.dumps(body).encode(),
            {"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Ollama raw completion failed: {exc}") from exc
        return data.get("response", ""), (
            data.get("prompt_eval_count", 0),
            data.get("eval_count", 0),
        )

    def extract(self, pages: list[str], template: Template) -> ExtractionOutput:
        return extract_with(self, pages, template)


class OpenAIExtractor:
    def __init__(self, api_key: str, model: str, timeout: float):
        self.client = OpenAI(api_key=api_key, timeout=timeout)
        self.model = model

    def structured(self, system: str, user: str, schema_model: type[T]) -> tuple[T, dict[str, Any]]:
        response = self.client.responses.parse(
            model=self.model, instructions=system, input=user, text_format=schema_model
        )
        if response.output_parsed is None:
            raise RuntimeError("Model returned no parsable output")
        usage = response.usage
        return response.output_parsed, {
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
        }

    def extract(self, pages: list[str], template: Template) -> ExtractionOutput:
        return extract_with(self, pages, template)


class OpenRouterExtractor:
    """Hosted open models via OpenRouter (OpenAI-compatible API): the same Qwen model that
    was evaluated locally runs in production. Strict JSON-schema output is requested from
    providers that support it; output is still validated and retried like everywhere else.
    Providers that store or train on prompts are excluded, documents may be confidential."""

    def __init__(self, api_key: str, model: str, timeout: float, retries: int = 1):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            timeout=timeout,
            default_headers={"X-Title": "Docflow"},
        )
        self.model = model
        self.retries = retries

    def structured(self, system: str, user: str, schema_model: type[T]) -> tuple[T, dict[str, Any]]:
        schema = inline_refs(to_strict_json_schema(schema_model))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        usage = {"input_tokens": 0, "output_tokens": 0, "attempts": 0}
        while True:
            usage["attempts"] += 1
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "result", "strict": True, "schema": schema},
                },
                extra_body={
                    "provider": {"require_parameters": True, "data_collection": "deny"},
                    "reasoning": {"enabled": False},
                },
            )
            if response.usage:
                usage["input_tokens"] += response.usage.prompt_tokens
                usage["output_tokens"] += response.usage.completion_tokens
            raw = response.choices[0].message.content or ""
            try:
                return schema_model.model_validate(loads_lenient(raw)), usage
            except (ValueError, ValidationError) as exc:
                if usage["attempts"] > self.retries:
                    raise RuntimeError(f"Model output does not match the schema: {exc}") from exc
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Invalid output, fix it:\n{exc}"})

    def extract(self, pages: list[str], template: Template) -> ExtractionOutput:
        return extract_with(self, pages, template)


def extract_with(llm: Extractor, pages: list[str], template: Template) -> ExtractionOutput:
    parsed, usage = llm.structured(
        instructions_for(template), render_pages(pages), response_model(template)
    )
    return ExtractionOutput(
        answers={f.key: getattr(parsed, f.key) for f in template.fields}, usage=usage
    )


# --------------------------------------------------------------------------- templates

SUGGEST_INSTRUCTIONS = """You design data-extraction templates for business documents
(often in Czech). Read the document and propose a reusable template for this *type* of
document, not for this specific instance:
- name: short Czech name of the document type (e.g. "Dodací list").
- description: one Czech sentence describing the document type.
- fields: 5 to 12 fields a business would most want to extract and check, most important
  first. key is short English snake_case; label is Czech; type is "number" for amounts and
  quantities, "date" for dates, otherwise "string"; description says in Czech how to
  recognise the value; required is true only if every document of this type has it.
- Include identifiers of the parties when the document shows them (IČO as a separate
  string field per party, keys ending in _ico), because they can be validated."""


class SuggestedField(BaseModel):
    key: str
    label: str
    type: Literal["string", "number", "date"]
    description: str
    required: bool


class SuggestedTemplate(BaseModel):
    name: str
    description: str
    fields: list[SuggestedField]


def slug(text: str, fallback: str = "field") -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = f"{fallback}_{s}".strip("_")
    return s[:48]


def suggest_template(llm: Extractor, pages: list[str]) -> tuple[Template, dict[str, Any]]:
    """Ask the model for a template draft, then make it valid: unique snake_case keys and
    an IČO checksum rule for every IČO-like field."""
    draft, usage = llm.structured(SUGGEST_INSTRUCTIONS, render_pages(pages), SuggestedTemplate)
    fields, seen = [], set()
    for f in draft.fields[:20]:
        key = slug(f.key)
        while key in seen:
            key += "_2"
        seen.add(key)
        fields.append(
            TemplateField(
                key=key, label=f.label, type=f.type, description=f.description, required=f.required
            )
        )
    rules = [
        IcoRule(type="ico", field=f.key)
        for f in fields
        if f.type == "string" and re.search(r"(^|_)ico($|_)|ičo", f"{f.key} {f.label.lower()}")
    ]
    template = Template(
        id=slug(draft.name, "template"),
        name=draft.name,
        description=draft.description,
        fields=fields,
        rules=rules,
    )
    return template, usage
