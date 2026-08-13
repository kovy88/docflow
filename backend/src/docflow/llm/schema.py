"""JSON Schema normalisation for structured outputs.

Pydantic emits schemas for *its* validator, which is strictly richer than what
provider structured-output engines accept. Both major providers reject or ignore
constraint keywords (`minLength`, `maximum`, `pattern`, …) and both require
`additionalProperties: false` on every object.

Handing the raw Pydantic schema to a provider therefore fails in one of two ways:
a hard 400 that takes the whole document down, or — worse — a silently ignored
constraint that lets malformed data through. This module makes the schema explicit
and provider-safe, and is unit tested against every registered document type so a
new field cannot quietly break extraction.

Constraints are not lost by dropping them here: they are still enforced by Pydantic
when the response is validated, which is the layer that should own them. The schema
sent to the model is a *shape* description, not the validation contract.
"""

from __future__ import annotations

import copy
from typing import Any

# Keywords no provider structured-output engine honours. Kept as an explicit
# denylist rather than an allowlist so that a harmless new annotation keyword does
# not silently strip the schema down to nothing.
UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "contains",
        "minContains",
        "maxContains",
        "minProperties",
        "maxProperties",
        "patternProperties",
        "propertyNames",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
        "not",
        "unevaluatedProperties",
        "unevaluatedItems",
        "prefixItems",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

SUPPORTED_STRING_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)

# Docflow's own field metadata. Useful to us, meaningless to a provider — folded
# into `description` and then stripped by `_walk`.
_INTERNAL_KEYWORDS = frozenset({"docflow"})

_CONTAINER_KEYS = ("properties", "$defs", "definitions")
_SUBSCHEMA_LISTS = ("anyOf", "allOf", "oneOf")


def normalize_schema(
    schema: dict[str, Any], *, require_all_properties: bool = False
) -> dict[str, Any]:
    """Return a provider-safe copy of `schema`.

    `require_all_properties` implements the OpenAI strict-mode rule that every
    declared property must appear in `required`. Optionality is then expressed by
    unioning the type with `null` rather than by omitting the key — which is
    actually the behaviour we want from an extraction model anyway: an explicit
    `null` ("I looked and it is not there") is a more useful signal than a missing
    key ("I may or may not have looked").
    """
    normalized = _walk(copy.deepcopy(schema), require_all_properties=require_all_properties)
    normalized.pop("title", None)
    return normalized


def _walk(node: Any, *, require_all_properties: bool) -> Any:  # noqa: PLR0912 — one branch per JSON Schema node shape
    if isinstance(node, list):
        return [_walk(item, require_all_properties=require_all_properties) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in UNSUPPORTED_KEYWORDS:
            continue
        if key == "format" and value not in SUPPORTED_STRING_FORMATS:
            continue
        if key in _INTERNAL_KEYWORDS:
            continue
        out[key] = value

    # Fold our own field metadata into `description`, the one place a model is
    # actually trained to read guidance from, then drop the private key. This both
    # shrinks the payload and makes the per-field hints effective — leaving a
    # non-standard `docflow` object in the schema costs tokens for nothing and
    # risks rejection by a strict schema validator.
    meta = node.get("docflow")
    if isinstance(meta, dict):
        hint = meta.get("hint")
        label = meta.get("label")
        existing = out.get("description")
        parts = [p for p in (existing, hint) if p]
        if not parts and label and label.lower() != str(node.get("title", "")).lower():
            parts = [label]
        if parts:
            out["description"] = " — ".join(dict.fromkeys(parts))

    # `title` is Pydantic's humanised property name. The model already sees the
    # property key, so this is pure duplication.
    out.pop("title", None)

    for key in _CONTAINER_KEYS:
        if isinstance(out.get(key), dict):
            out[key] = {
                name: _walk(sub, require_all_properties=require_all_properties)
                for name, sub in out[key].items()
            }

    for key in _SUBSCHEMA_LISTS:
        if isinstance(out.get(key), list):
            out[key] = [
                _walk(sub, require_all_properties=require_all_properties) for sub in out[key]
            ]

    if "items" in out:
        out["items"] = _walk(out["items"], require_all_properties=require_all_properties)

    if _is_object(out):
        # Required on every object by both providers, and independently valuable:
        # it turns a hallucinated field name into a visible schema error instead of
        # an extra key that flows silently into an export.
        out["additionalProperties"] = False
        if require_all_properties and isinstance(out.get("properties"), dict):
            out["required"] = sorted(out["properties"].keys())

    return out


def _is_object(node: dict[str, Any]) -> bool:
    node_type = node.get("type")
    if node_type == "object":
        return True
    if isinstance(node_type, list) and "object" in node_type:
        return True
    return "properties" in node and "$ref" not in node


def ensure_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Make every non-required leaf explicitly nullable.

    Applied for strict-mode providers after `require_all_properties`, so that a
    field the model genuinely cannot find can still be returned as `null` instead
    of forcing it to invent a value. Forcing a model to fill a required string is
    a reliable way to manufacture hallucinations.
    """
    result = copy.deepcopy(schema)
    _make_optionals_nullable(result, result.get("$defs", {}))
    return result


def _make_optionals_nullable(node: Any, defs: dict[str, Any]) -> None:
    if isinstance(node, list):
        for item in node:
            _make_optionals_nullable(item, defs)
        return
    if not isinstance(node, dict):
        return

    properties = node.get("properties")
    if isinstance(properties, dict):
        for sub in properties.values():
            if isinstance(sub, dict):
                _nullify(sub)
                _make_optionals_nullable(sub, defs)

    for key in (*_SUBSCHEMA_LISTS, "items"):
        if key in node:
            _make_optionals_nullable(node[key], defs)
    for key in _CONTAINER_KEYS:
        if isinstance(node.get(key), dict):
            for sub in node[key].values():
                _make_optionals_nullable(sub, defs)


def _nullify(node: dict[str, Any]) -> None:
    if "anyOf" in node:
        if not any(isinstance(o, dict) and o.get("type") == "null" for o in node["anyOf"]):
            node["anyOf"].append({"type": "null"})
        return
    node_type = node.get("type")
    if node_type is None:
        # A bare `$ref` cannot be unioned in place without breaking the reference;
        # wrap it instead.
        if "$ref" in node:
            ref = node.pop("$ref")
            extras = {k: v for k, v in node.items() if k not in {"description", "title"}}
            for key in extras:
                node.pop(key, None)
            node["anyOf"] = [{"$ref": ref}, {"type": "null"}]
        return
    if isinstance(node_type, str) and node_type != "null":
        node["type"] = [node_type, "null"]
    elif isinstance(node_type, list) and "null" not in node_type:
        node["type"] = [*node_type, "null"]


def describe_schema_for_prompt(schema: dict[str, Any], *, max_chars: int = 4000) -> str:
    """Compact human-readable field list used in the self-repair prompt.

    Re-sending the full JSON Schema after a validation failure wastes tokens on
    structure the model already produced correctly. A flat list of paths, types and
    descriptions is enough to correct a specific field.
    """
    lines: list[str] = []
    defs = schema.get("$defs", {})
    _describe(schema, defs, prefix="", lines=lines, depth=0)
    text = "\n".join(lines)
    return text[:max_chars]


def _describe(
    node: dict[str, Any],
    defs: dict[str, Any],
    *,
    prefix: str,
    lines: list[str],
    depth: int,
) -> None:
    if depth > 4:
        return
    node = _resolve(node, defs)
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return
    required = set(node.get("required", []))
    for name, sub in properties.items():
        path = f"{prefix}{name}"
        resolved = _resolve(sub, defs)
        kind = _type_label(resolved)
        flag = " (required)" if name in required else ""
        desc = resolved.get("description") or sub.get("description") or ""
        lines.append(f"- {path}: {kind}{flag}{(' — ' + desc) if desc else ''}")
        if kind == "object":
            _describe(resolved, defs, prefix=f"{path}.", lines=lines, depth=depth + 1)
        elif kind == "array":
            item = _resolve(resolved.get("items", {}), defs)
            if item.get("properties"):
                _describe(item, defs, prefix=f"{path}[].", lines=lines, depth=depth + 1)


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    if ref := node.get("$ref"):
        key = str(ref).rsplit("/", 1)[-1]
        return defs.get(key, {})
    for option in node.get("anyOf", []):
        if isinstance(option, dict) and option.get("type") != "null":
            return _resolve(option, defs)
    return node


def _type_label(node: dict[str, Any]) -> str:
    node_type = node.get("type")
    if isinstance(node_type, list):
        node_type = next((t for t in node_type if t != "null"), None)
    if node_type:
        return str(node_type)
    if node.get("properties"):
        return "object"
    if node.get("enum"):
        return "enum"
    return "string"
