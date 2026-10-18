from typing import Any

from app.checks import apply_rules, issue, locate_quote, normalize, quote_in
from app.config import settings
from app.extractor import Extractor, FieldAnswer
from app.templates import Template, TemplateField


def check_value(
    spec: TemplateField,
    raw: str | None,
    field: dict[str, Any],
    values: dict[str, Any],
    issues: list[dict[str, Any]],
) -> bool:
    """Normalise one raw value into field/values; record issues. False if the value is empty."""
    if raw is None or not raw.strip():
        if spec.required:
            issues.append(issue("error", "missing", f"Chybí povinné pole {spec.label}.", spec.key))
        return False
    try:
        field["value"] = values[spec.key] = normalize(raw, spec.type)
    except ValueError:
        field["value"] = None
        issues.append(
            issue(
                "error",
                "invalid_format",
                f"{spec.label}: '{raw}' není platný {spec.type}.",
                spec.key,
            )
        )
    return True


def run_extraction(pages: list[str], template: Template, extractor: Extractor) -> dict[str, Any]:
    if not any(pages):
        return {
            "status": "failed",
            "fields": [],
            "issues": [
                issue(
                    "error",
                    "no_text",
                    "PDF nemá textovou vrstvu (sken). OCR zatím není podporováno.",
                )
            ],
            "usage": {},
        }

    output = extractor.extract(pages, template)
    fields, issues, values = [], [], {}

    for spec in template.fields:
        answer = output.answers.get(spec.key) or FieldAnswer(
            value=None, page=None, quote=None, confidence=0
        )
        field: dict[str, Any] = {
            "key": spec.key,
            "label": spec.label,
            "type": spec.type,
            "raw": answer.value,
            "value": None,
            "page": answer.page,
            "quote": answer.quote,
            "confidence": round(answer.confidence, 3),
            "grounded": False,
        }
        fields.append(field)

        if not check_value(spec, answer.value, field, values, issues):
            continue

        # Grounding: the quote must exist in the PDF text and contain the value,
        # otherwise the value may be hallucinated or taken from the wrong place.
        quote = answer.quote or answer.value
        found = locate_quote(quote, pages, answer.page)
        field["grounded"] = found is not None and quote_in(answer.value, quote)
        if not field["grounded"]:
            issues.append(
                issue(
                    "warning",
                    "ungrounded",
                    f"{spec.label}: citaci se nepodařilo najít v dokumentu.",
                    spec.key,
                )
            )
        if found is not None and found != answer.page:
            field["page"] = found

        if spec.key in values and answer.confidence < settings.low_confidence:
            issues.append(
                issue(
                    "warning",
                    "low_confidence",
                    f"{spec.label}: nízká jistota modelu ({answer.confidence:.0%}).",
                    spec.key,
                )
            )

    issues += apply_rules(template, values)
    status = "needs_review" if issues else "ok"
    return {"status": status, "fields": fields, "issues": issues, "usage": output.usage}


def apply_review(
    template: Template, fields: list[dict[str, Any]], corrections: dict[str, str | None]
) -> dict[str, Any]:
    """Apply human corrections. Model-quality warnings (grounding, confidence) no longer
    apply once a person has checked the values; document rules are re-run on the result."""
    specs = {f.key: f for f in template.fields}
    unknown = set(corrections) - set(specs)
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
    reviewed, issues, values = [], [], {}
    for old in fields:
        field = {**old, "value": None}
        if field["key"] in corrections and corrections[field["key"]] != old["raw"]:
            field["raw"] = corrections[field["key"]]
            field["corrected"] = True
        check_value(specs[field["key"]], field["raw"], field, values, issues)
        reviewed.append(field)
    issues += apply_rules(template, values)
    return {"status": "approved", "fields": reviewed, "issues": issues}
