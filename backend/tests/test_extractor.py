import json
from types import SimpleNamespace

import pytest

from app.extractor import ToolCallExtractor
from app.templates import Template

TEMPLATE = Template(
    id="mini", name="Mini", fields=[{"key": "number", "label": "Číslo", "type": "string"}]
)
VALID = {"number": {"value": "42", "page": 1, "quote": "č. 42", "confidence": 0.9}}


def completion(arguments: str):
    call = SimpleNamespace(function=SimpleNamespace(arguments=arguments))
    message = SimpleNamespace(tool_calls=[call], content=None)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def extractor_with(outputs: list[str]) -> tuple[ToolCallExtractor, list]:
    extractor = ToolCallExtractor("http://unused", "fake", timeout=1)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return completion(outputs[len(calls) - 1])

    extractor.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return extractor, calls


def test_tool_call_is_parsed():
    extractor, calls = extractor_with([json.dumps(VALID)])
    out = extractor.extract(["č. 42"], TEMPLATE)
    assert out.answers["number"].value == "42"
    assert out.usage == {"input_tokens": 10, "output_tokens": 5, "attempts": 1, "mode": "tool"}
    assert calls[0]["tool_choice"]["function"]["name"] == "submit_extraction"


def test_invalid_output_is_retried_with_error_feedback():
    extractor, calls = extractor_with(['{"number": {"value": "42"}}', json.dumps(VALID)])
    out = extractor.extract(["č. 42"], TEMPLATE)
    assert out.answers["number"].page == 1
    assert out.usage["attempts"] == 2
    assert "Invalid output" in calls[1]["messages"][-1]["content"]
    assert "tools" in calls[1]


def test_gives_up_after_retries():
    extractor, _ = extractor_with(["not json", "still not json"])
    with pytest.raises(RuntimeError):
        extractor.extract(["č. 42"], TEMPLATE)


def test_nested_objects_encoded_as_strings_are_accepted():
    extractor, _ = extractor_with([json.dumps({"number": json.dumps(VALID["number"])})])
    assert extractor.extract(["č. 42"], TEMPLATE).answers["number"].value == "42"


def test_tool_schema_has_no_refs():
    extractor, calls = extractor_with([json.dumps(VALID)])
    extractor.extract(["č. 42"], TEMPLATE)
    assert "$ref" not in json.dumps(calls[0]["tools"][0])


def test_fenced_json_in_content_is_accepted():
    extractor, _ = extractor_with([f"Here you go:\n```json\n{json.dumps(VALID)}\n```"])
    assert extractor.extract(["č. 42"], TEMPLATE).answers["number"].value == "42"


def test_tool_parse_error_falls_back_to_json_mode():
    import httpx
    from openai import InternalServerError

    extractor, calls = extractor_with([json.dumps(VALID)])
    real_create = extractor.client.chat.completions.create
    failures = []

    def flaky(**kwargs):
        if not failures:
            failures.append(1)
            request = httpx.Request("POST", "http://unused")
            raise InternalServerError(
                "XML syntax error", response=httpx.Response(500, request=request), body=None
            )
        return real_create(**kwargs)

    extractor.client.chat.completions.create = flaky
    out = extractor.extract(["č. 42"], TEMPLATE)
    assert out.usage["attempts"] == 2 and out.usage["mode"] == "json"
    assert "tools" not in calls[-1]
    assert "JSON object matching this schema" in calls[-1]["messages"][-1]["content"]
