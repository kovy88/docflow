"""Stage contract and runner.

A stage is a named, independently testable unit with one method. The runner owns
everything cross-cutting — timing, step records, structured logging, error
classification — so no stage contains that boilerplate and every stage is timed
and recorded identically.

Two properties are deliberate:

* **A stage that raises stops the pipeline**, unless it declares itself optional.
  OCR is optional (a document with a partial text layer is still processable);
  extraction is not. This is a property of the stage, not a `try` block the runner
  guesses at.

* **The context is always returned**, even on failure. The caller persists whatever
  completed, so a document that dies at extraction still shows its page count and
  detected type in the UI instead of an empty error card.
"""

from __future__ import annotations

import abc
import datetime as dt
import time

import structlog

from docflow.domain.enums import ProcessingStage, StepStatus
from docflow.domain.errors import DocflowError
from docflow.pipeline.context import PipelineContext, StepRecord

logger = structlog.get_logger(__name__)


class Stage(abc.ABC):
    """One pipeline stage."""

    stage: ProcessingStage
    # When True, a failure is recorded and the pipeline continues.
    optional: bool = False

    @abc.abstractmethod
    async def run(self, ctx: PipelineContext) -> None:
        """Read from and write to `ctx`. Raise to signal failure."""

    def should_run(self, ctx: PipelineContext) -> bool:
        """Skip conditions. A skipped stage is recorded, not silently omitted."""
        return True

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        """Non-sensitive summary persisted with the step, shown in the timeline."""
        return {}


class PipelineRunner:
    def __init__(self, stages: list[Stage]) -> None:
        self._stages = stages

    @property
    def stages(self) -> list[Stage]:
        return list(self._stages)

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        log = logger.bind(**ctx.log_fields())

        for sequence, stage in enumerate(self._stages, start=1):
            if not stage.should_run(ctx):
                ctx.steps.append(
                    StepRecord(
                        stage=stage.stage,
                        status=StepStatus.SKIPPED,
                        sequence=sequence,
                        started_at=_now(),
                        finished_at=_now(),
                        duration_ms=0,
                    )
                )
                log.debug("pipeline.stage_skipped", stage=stage.stage.value)
                continue

            started_at = _now()
            clock = time.perf_counter()
            record = StepRecord(
                stage=stage.stage,
                status=StepStatus.RUNNING,
                sequence=sequence,
                started_at=started_at,
            )
            ctx.steps.append(record)

            try:
                await stage.run(ctx)
            except DocflowError as exc:
                _finish(record, StepStatus.FAILED, clock)
                record.error_code = exc.code
                record.error_message = exc.message
                log.warning(
                    "pipeline.stage_failed",
                    stage=stage.stage.value,
                    error_code=exc.code,
                    category=exc.category.value,
                    retryable=exc.retryable,
                    duration_ms=record.duration_ms,
                )
                if stage.optional:
                    continue
                ctx.failed = True
                ctx.error_code = exc.code
                ctx.error_message = exc.message
                return ctx
            except Exception as exc:  # noqa: BLE001
                _finish(record, StepStatus.FAILED, clock)
                record.error_code = "internal_error"
                # The exception type is safe to record; its message may contain
                # document content or file paths, so it is not.
                record.error_message = "An unexpected error occurred in this stage"
                log.exception("pipeline.stage_crashed", stage=stage.stage.value)
                if stage.optional:
                    continue
                ctx.failed = True
                ctx.error_code = "internal_error"
                ctx.error_message = f"Processing failed during {stage.stage.value}"
                return ctx
            else:
                _finish(record, StepStatus.SUCCEEDED, clock)
                record.detail = dict(stage.detail(ctx))
                log.info(
                    "pipeline.stage_completed",
                    stage=stage.stage.value,
                    duration_ms=record.duration_ms,
                    **record.detail,
                )

        return ctx


def _finish(record: StepRecord, status: StepStatus, clock: float) -> None:
    record.status = status
    record.finished_at = _now()
    record.duration_ms = int((time.perf_counter() - clock) * 1000)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
