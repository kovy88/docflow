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
    assert calls[0]["tool_choice"]["function"]["name"] == "submit"


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


def test_tool_parse_error_falls_back_to_raw_completion():
    import httpx
    from openai import InternalServerError

    extractor, calls = extractor_with([])

    def broken_parser(**kwargs):
        calls.append(kwargs)
        request = httpx.Request("POST", "http://unused")
        raise InternalServerError(
            "XML syntax error", response=httpx.Response(500, request=request), body=None
        )

    prompts = []

    def raw(messages, schema):
        prompts.append(messages)
        return json.dumps(VALID), (100, 20)

    extractor.client.chat.completions.create = broken_parser
    extractor._raw_completion = raw
    out = extractor.extract(["č. 42"], TEMPLATE)
    assert out.answers["number"].value == "42"
    assert out.usage == {"input_tokens": 100, "output_tokens": 20, "attempts": 2, "mode": "raw"}
    assert len(calls) == 1 and len(prompts) == 1


def test_raw_completion_builds_chatml(monkeypatch):
    import io

    extractor = ToolCallExtractor("http://ollama:11434/v1", "qwen", timeout=1)
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"], seen["body"] = request.full_url, json.loads(request.data)
        return io.BytesIO(
            json.dumps({"response": "{}", "prompt_eval_count": 5, "eval_count": 1}).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    text, used = extractor._raw_completion([{"role": "user", "content": "hi"}], {"type": "object"})
    assert seen["url"] == "http://ollama:11434/api/generate"
    assert seen["body"]["raw"] is True
    assert seen["body"]["prompt"].startswith("<|im_start|>user\nhi<|im_end|>")
    assert seen["body"]["prompt"].endswith("<think>\n\n</think>\n\n")
    assert (text, used) == ("{}", (5, 1))


def test_suggest_template_is_made_valid():
    from app.extractor import suggest_template

    draft = {
        "name": "Dodací list",
        "description": "Doklad o předání zboží.",
        "fields": [
            {
                "key": "Číslo dodacího listu",
                "label": "Číslo",
                "type": "string",
                "description": "",
                "required": True,
            },
            {
                "key": "supplier_ico",
                "label": "IČO dodavatele",
                "type": "string",
                "description": "",
                "required": True,
            },
            {
                "key": "supplier ico",
                "label": "IČO 2",
                "type": "string",
                "description": "",
                "required": False,
            },
            {
                "key": "2nd_date",
                "label": "Datum",
                "type": "date",
                "description": "",
                "required": True,
            },
        ],
    }
    extractor, _ = extractor_with([json.dumps(draft)])
    template, usage = suggest_template(extractor, ["Dodací list č. 7"])
    assert template.id == "dodaci_list"
    assert [f.key for f in template.fields] == [
        "cislo_dodaciho_listu",
        "supplier_ico",
        "supplier_ico_2",
        "field_2nd_date",
    ]
    assert [r.field for r in template.rules] == ["supplier_ico", "supplier_ico_2"]
    assert usage["attempts"] == 1


def test_nested_list_items_encoded_as_strings():
    from app.extractor import loads_lenient

    raw = json.dumps({"fields": [json.dumps({"key": "a"})], "name": "x"})
    assert loads_lenient(raw) == {"fields": [{"key": "a"}], "name": "x"}


def test_openrouter_requests_strict_schema_and_no_data_retention():
    from app.extractor import OpenRouterExtractor

    extractor = OpenRouterExtractor("key", "qwen/qwen3.6-35b-a3b", timeout=1)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(VALID), tool_calls=None)
        usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    extractor.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    out = extractor.extract(["č. 42"], TEMPLATE)
    assert out.answers["number"].value == "42"
    fmt = calls[0]["response_format"]["json_schema"]
    assert fmt["strict"] is True and "$ref" not in json.dumps(fmt["schema"])
    assert calls[0]["extra_body"]["provider"] == {
        "require_parameters": True,
        "data_collection": "deny",
    }
