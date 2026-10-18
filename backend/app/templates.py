"""Templates describe *what* to extract and *how* to check it. They are the only
domain-specific part of Docflow: an invoice, a contract or an insurance policy is
just a different JSON file in app/templates/."""

from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

TEMPLATE_DIR = Path(__file__).parent / "templates"


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


@cache
def load_templates() -> dict[str, Template]:
    templates = [Template.model_validate_json(p.read_text()) for p in TEMPLATE_DIR.glob("*.json")]
    return {t.id: t for t in sorted(templates, key=lambda t: t.id)}
