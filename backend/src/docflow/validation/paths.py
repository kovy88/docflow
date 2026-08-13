"""Dotted-path access over extracted payloads.

Field paths are the shared vocabulary between the schema registry, the validation
engine, the confidence scorer, the review UI and the corrections table. They come in
two flavours:

    concrete   `total`, `supplier.name`, `line_items.0.unit_price`
    template   `line_items[].unit_price`   (matches every row)

`expand` turns a template into the concrete paths present in a given payload, which
is what lets a rule say "every line item needs a description" without knowing how
many lines a document has.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

LIST_MARKER = "[]"
MISSING = object()


def get_path(data: Any, path: str) -> Any:
    """Read a concrete path. Returns `MISSING` when any segment is absent.

    `MISSING` rather than `None` because "the key is not there" and "the key is there
    and holds null" are different facts: the first is an extraction gap, the second
    is the model explicitly declining to answer, and the required-field rule treats
    them the same only by choice, not by accident.
    """
    current = data
    for segment in path.split("."):
        if current is None:
            return MISSING
        if isinstance(current, list):
            if not segment.isdigit():
                return MISSING
            index = int(segment)
            if index >= len(current):
                return MISSING
            current = current[index]
        elif isinstance(current, dict):
            if segment not in current:
                return MISSING
            current = current[segment]
        else:
            return MISSING
    return current


def get(data: Any, path: str, default: Any = None) -> Any:
    value = get_path(data, path)
    return default if value is MISSING else value


def set_path(data: dict[str, Any], path: str, value: Any) -> None:
    """Write a concrete path, creating intermediate containers as needed.

    Used by the review endpoint when a human edits a nested value. Missing list
    indices are an error rather than an auto-extend: silently growing a line-item
    list because a client sent `line_items.7.total` for a 3-line invoice would
    fabricate rows.
    """
    segments = path.split(".")
    current: Any = data
    for i, segment in enumerate(segments[:-1]):
        nxt = segments[i + 1]
        if isinstance(current, list):
            if not segment.isdigit():
                raise KeyError(f"expected list index at {segment!r} in {path!r}")
            index = int(segment)
            if index >= len(current):
                raise KeyError(f"list index {index} out of range in {path!r}")
            current = current[index]
            continue
        if not isinstance(current, dict):
            raise KeyError(f"cannot descend into {segment!r} in {path!r}")
        if segment not in current or current[segment] is None:
            current[segment] = [] if nxt.isdigit() else {}
        current = current[segment]

    last = segments[-1]
    if isinstance(current, list):
        if not last.isdigit():
            raise KeyError(f"expected list index at {last!r} in {path!r}")
        index = int(last)
        if index >= len(current):
            raise KeyError(f"list index {index} out of range in {path!r}")
        current[index] = value
    elif isinstance(current, dict):
        current[last] = value
    else:
        raise KeyError(f"cannot set {last!r} in {path!r}")


def expand(data: Any, template: str) -> Iterator[tuple[str, Any]]:
    """Expand a template path into `(concrete_path, value)` pairs."""
    if LIST_MARKER not in template:
        value = get_path(data, template)
        if value is not MISSING:
            yield template, value
        return

    head, _, tail = template.partition(LIST_MARKER)
    head = head.rstrip(".")
    container = get_path(data, head) if head else data
    if not isinstance(container, list):
        return
    tail = tail.lstrip(".")
    for index, item in enumerate(container):
        concrete_head = f"{head}.{index}" if head else str(index)
        if not tail:
            yield concrete_head, item
            continue
        # `tail` may contain further list markers, so recurse rather than
        # assuming a single level of nesting.
        for sub_path, sub_value in expand(item, tail):
            yield f"{concrete_head}.{sub_path}", sub_value


def iter_concrete_paths(data: Any, template: str) -> Iterator[str]:
    for path, _ in expand(data, template):
        yield path


def parent_path(path: str) -> str | None:
    if "." not in path:
        return None
    return path.rsplit(".", 1)[0]


def template_parent(template: str) -> str | None:
    """Parent of a *template* path, treating `[]` as part of its own segment."""
    if LIST_MARKER in template:
        head, _, tail = template.partition(LIST_MARKER)
        tail = tail.lstrip(".")
        if not tail:
            return head.rstrip(".") or None
        if "." not in tail:
            return f"{head.rstrip('.')}{LIST_MARKER}" if head else LIST_MARKER
        return f"{head}{LIST_MARKER}.{tail.rsplit('.', 1)[0]}"
    return parent_path(template)


def flatten(data: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Yield every leaf `(concrete_path, value)` in a payload.

    Containers are not yielded, only leaves — the review UI renders values, and a
    dict has no value to render.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict | list):
                yield from flatten(value, path)
            else:
                yield path, value
    elif isinstance(data, list):
        for index, item in enumerate(data):
            path = f"{prefix}.{index}" if prefix else str(index)
            if isinstance(item, dict | list):
                yield from flatten(item, path)
            else:
                yield path, item
    elif prefix:
        yield prefix, data


def to_template(concrete_path: str) -> str:
    """Inverse of `expand` for a single path: `line_items.2.total` -> `line_items[].total`."""
    parts = concrete_path.split(".")
    out: list[str] = []
    for part in parts:
        if part.isdigit():
            if out:
                out[-1] = f"{out[-1]}{LIST_MARKER}"
            continue
        out.append(part)
    return ".".join(out)
