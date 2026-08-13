"""Versioned prompt templates.

Importing this package registers every prompt, so `get_prompt("extraction_system")`
resolves without the caller needing to know which module defines it.
"""

from docflow.prompts.registry import Prompt, PromptRegistry, get_prompt, registry
from docflow.prompts import extraction as extraction  # noqa: F401 — registration side effect

__all__ = ["Prompt", "PromptRegistry", "get_prompt", "registry"]
