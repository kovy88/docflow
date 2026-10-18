"""Templates describe *what* to extract and *how* to check it. They are the only
domain-specific part of Docflow: an invoice, a contract or an insurance policy is
just a different JSON file in app/templates/ (built-in) or a template saved from the UI."""

from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

TEMPLATE_DIR = Path(__file__).parent / "templates"


TYPE_NAMES = {"string": "text", "number": "číslo", "date": "datum"}


class TemplateField(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    type: Literal["string", "number", "date"] = "string"
    description: str = ""
    required: bool = True


class IcoRule(BaseModel):
    type: Literal["ico"]
    field: str


class SumRule(BaseModel):
    type: Literal["sum"]
    fields: list[str]
    equals: str
    tolerance: float = 0.01


class DateOrderRule(BaseModel):
    type: Literal["date_order"]
    earlier: str
    later: str


Rule = Annotated[IcoRule | SumRule | DateOrderRule, Field(discriminator="type")]


class Template(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: str = ""
    fields: list[TemplateField] = Field(min_length=1)
    rules: list[Rule] = []

    @model_validator(mode="after")
    def check_consistency(self):
        """User-made templates must be internally consistent before they reach the LLM."""
        types = {f.key: f.type for f in self.fields}
        if len(types) != len(self.fields):
            raise ValueError("klíče polí musí být unikátní")
        for rule in self.rules:
            if isinstance(rule, IcoRule):
                needed = {rule.field: "string"}
            elif isinstance(rule, SumRule):
                needed = dict.fromkeys([*rule.fields, rule.equals], "number")
            else:
                needed = {rule.earlier: "date", rule.later: "date"}
            for key, kind in needed.items():
                if key not in types:
                    raise ValueError(f"pravidlo {rule.type} odkazuje na neexistující pole {key!r}")
                if types[key] != kind:
                    raise ValueError(
                        f"pravidlo {rule.type} vyžaduje, aby pole {key!r} bylo typu {TYPE_NAMES[kind]}"
                    )
        return self


@cache
def load_templates() -> dict[str, Template]:
    templates = [Template.model_validate_json(p.read_text()) for p in TEMPLATE_DIR.glob("*.json")]
    return {t.id: t for t in sorted(templates, key=lambda t: t.id)}
