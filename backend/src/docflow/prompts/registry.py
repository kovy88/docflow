"""Versioned prompt registry.

A prompt is part of the software. Changing one changes the product's output as
surely as changing a function body does, so prompts live in source control, carry
explicit version identifiers, and are recorded on every extraction they produce.

This is what makes these questions answerable months later:

    "Which prompt produced this wrong value?"        -> extractions.prompt_version
    "Did v3 actually improve the due-date field?"    -> field_corrections, grouped
                                                        by prompt_version
    "Can we reproduce a result from March?"          -> prompt_versions table holds
                                                        the exact text

Versions are strings (`v1`, `v2`) rather than integers so a variant can be named
(`v2-cz`) without renumbering. The registry refuses to register the same
`(key, version)` twice, which makes silent edits to a released prompt impossible:
you must bump the version, and the bump shows up in the data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Prompt:
    key: str
    version: str
    template: str
    notes: str = ""
    # Free-form record of what changed and why, for the ADR trail.
    changelog: str = ""

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()

    @property
    def identifier(self) -> str:
        return f"{self.key}:{self.version}"

    def render(self, **values: Any) -> str:
        """Render with `{placeholder}` substitution.

        Uses `str.replace` rather than `str.format` deliberately: document text and
        JSON schemas both contain `{` and `}`, and `format` would either crash or —
        worse — interpret braces inside untrusted document content as format
        specifiers. That would be an injection vector in the templating layer,
        before the model is even involved.
        """
        out = self.template
        for name, value in values.items():
            out = out.replace("{" + name + "}", str(value))
        return out


class PromptRegistry:
    def __init__(self) -> None:
        self._prompts: dict[tuple[str, str], Prompt] = {}
        self._latest: dict[str, str] = {}

    def register(self, prompt: Prompt, *, latest: bool = True) -> Prompt:
        key = (prompt.key, prompt.version)
        if key in self._prompts:
            raise ValueError(
                f"Prompt {prompt.identifier} is already registered. "
                "Bump the version rather than editing a released prompt."
            )
        self._prompts[key] = prompt
        if latest:
            self._latest[prompt.key] = prompt.version
        return prompt

    def get(self, key: str, version: str | None = None) -> Prompt:
        resolved = version or self._latest.get(key)
        if resolved is None:
            raise KeyError(f"No prompt registered under {key!r}")
        try:
            return self._prompts[(key, resolved)]
        except KeyError as exc:
            raise KeyError(f"No prompt {key!r} version {resolved!r}") from exc

    def versions(self, key: str) -> list[str]:
        return sorted(v for k, v in self._prompts if k == key)

    def all(self) -> list[Prompt]:
        return sorted(self._prompts.values(), key=lambda p: (p.key, p.version))


registry = PromptRegistry()


def get_prompt(key: str, version: str | None = None) -> Prompt:
    return registry.get(key, version)
