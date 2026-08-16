"use client";

import type { ProcessingStep } from "@/lib/api";
import { cn, formatDuration, titleCase } from "@/lib/utils";

const STAGE_ORDER = [
  "file_validation",
  "text_extraction",
  "ocr",
  "metadata_extraction",
  "classification",
  "schema_selection",
  "llm_extraction",
  "baseline_crosscheck",
  "business_validation",
  "confidence_scoring",
  "review_routing",
];

export function ProcessingTimeline({ steps }: { steps: ProcessingStep[] }) {
  const ordered = [...steps].sort(
    (a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage),
  );
  const visible = ordered.filter((s) => s.status !== "skipped");

  if (visible.length === 0) {
    return <p className="text-sm text-subtle">Processing has not started yet.</p>;
  }

  return (
    <ol className="space-y-0">
      {visible.map((step, index) => (
        <li key={step.stage} className="relative flex gap-3 pb-5 last:pb-0">
          {index < visible.length - 1 && (
            <div
              className={cn(
                "absolute left-[9px] top-5 h-full w-px",
                step.status === "succeeded" ? "bg-ok/40" : "bg-line",
              )}
            />
          )}
          <StepDot status={step.status} />
          <div className="flex-1 pt-0.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-ink">{titleCase(step.stage)}</span>
              {step.duration_ms !== null && (
                <span className="font-mono text-xs text-subtle">{formatDuration(step.duration_ms)}</span>
              )}
            </div>
            {step.error_message && (
              <p className="mt-0.5 text-xs text-danger">{step.error_message}</p>
            )}
            {step.detail && Object.keys(step.detail).length > 0 && (
              <p className="mt-0.5 truncate text-xs text-subtle">{summarizeDetail(step.detail)}</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function StepDot({ status }: { status: ProcessingStep["status"] }) {
  if (status === "succeeded") {
    return (
      <div className="z-10 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-ok text-white">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" className="h-2.5 w-2.5">
          <path d="M20 6L9 17l-5-5" />
        </svg>
      </div>
    );
  }
  if (status === "failed") {
    return (
      <div className="z-10 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-danger text-white">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" className="h-2.5 w-2.5">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </div>
    );
  }
  return <div className="z-10 h-[18px] w-[18px] shrink-0 animate-pulse rounded-full border-2 border-brand bg-surface" />;
}

function summarizeDetail(detail: Record<string, unknown>): string {
  const parts = Object.entries(detail)
    .filter(([key]) => key !== "input" && key !== "output")
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${value}`);
  return parts.join(" · ");
}
