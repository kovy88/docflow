"""Pipeline assembly.

The stage list is data. Reordering, adding or removing a stage is an edit to this
one list, and the runner needs no knowledge of what any stage does.

Ordering constraints are real and worth stating:

    file_validation  -> must precede everything (produces the bytes)
    text_extraction  -> must precede classification (produces the text)
    ocr              -> reports on text_extraction, so it follows
    classification   -> must precede schema_selection
    schema_selection -> must precede llm_extraction (chooses the output schema)
    llm_extraction   -> must precede validation and confidence
    baseline         -> must precede confidence (supplies the agreement signal)
    validation       -> must precede confidence (supplies the validation signal)
    confidence       -> must precede review_routing
"""

from __future__ import annotations

from docflow.config import Settings, get_settings
from docflow.llm.base import LLMProvider
from docflow.llm.registry import get_provider
from docflow.pipeline.stage import PipelineRunner, Stage
from docflow.pipeline.stages.classify import ClassificationStage, SchemaSelectionStage
from docflow.pipeline.stages.extract import (
    BaselineCrossCheckStage,
    ConfidenceScoringStage,
    LLMExtractionStage,
    ReviewRoutingStage,
    ValidationStage,
)
from docflow.pipeline.stages.ingest import (
    FileValidationStage,
    OCRStage,
    TextExtractionStage,
    TextPersistenceStage,
)
from docflow.schemas.registry import SchemaRegistry, get_registry
from docflow.storage import get_storage
from docflow.storage.base import StorageBackend


def build_stages(
    *,
    settings: Settings | None = None,
    storage: StorageBackend | None = None,
    provider: LLMProvider | None = None,
    registry: SchemaRegistry | None = None,
) -> list[Stage]:
    """Construct the stage list with explicit dependency injection.

    Every collaborator is an argument with a sensible default. Tests substitute a
    fake provider or an in-memory storage backend without patching module globals,
    which is what keeps the pipeline tests fast and free of import-order surprises.
    """
    settings = settings or get_settings()
    storage = storage or get_storage()
    provider = provider or get_provider()
    registry = registry or get_registry()

    return [
        FileValidationStage(storage, settings.upload),
        TextExtractionStage(settings.processing, max_pages=settings.upload.max_pages),
        OCRStage(),
        TextPersistenceStage(storage),
        ClassificationStage(registry, provider, settings.llm),
        SchemaSelectionStage(registry),
        LLMExtractionStage(provider, settings.llm),
        BaselineCrossCheckStage(),
        ValidationStage(),
        ConfidenceScoringStage(),
        ReviewRoutingStage(settings.processing),
    ]


def build_pipeline(**kwargs: object) -> PipelineRunner:
    return PipelineRunner(build_stages(**kwargs))  # type: ignore[arg-type]
