"""Validation layer: syntax (Pydantic), semantic and business rules."""

from docflow.validation.engine import (
    Issue,
    RuleContext,
    ValidationEngine,
    ValidationResult,
    registry,
    validate_syntax,
)

# Importing the catalogue registers every rule as a side effect. Explicit, so that
# a bare `import docflow.validation` is enough for the engine to resolve rule ids.
from docflow.validation import rules as _rules  # noqa: E402,F401

__all__ = [
    "Issue",
    "RuleContext",
    "ValidationEngine",
    "ValidationResult",
    "registry",
    "validate_syntax",
]
