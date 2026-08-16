"use client";

/**
 * The extraction review panel — the screen this whole product exists to make
 * good. Every field shows its value, confidence and validation status together,
 * because the reviewer's question is never "what value did the model produce"
 * in isolation, it's "should I trust this value" — and that answer needs all
 * three at once.
 *
 * Editing is staged locally and submitted as one batch on save, rather than one
 * PATCH per field. A field-at-a-time save would mean a reviewer fixing five
 * fields waits on five round trips and can end up approving mid-edit.
 */

import { useMemo, useState } from "react";
import type { Extraction, ExtractionField, ValidationIssue } from "@/lib/api";
import { documents, ApiError } from "@/lib/api";
import { Badge, Button, ConfidenceBadge, Input, Textarea } from "@/components/ui";
import { cn, formatFieldLabel } from "@/lib/utils";
import { useToast } from "@/components/toast-context";

type PendingEdits = Record<string, unknown>;

export function ExtractionPanel({
  extraction,
  onChanged,
}: {
  extraction: Extraction;
  onChanged: () => void;
}) {
  const [edits, setEdits] = useState<PendingEdits>({});
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectForm, setShowRejectForm] = useState(false);
  const notify = useToast();

  const issuesByField = useMemo(() => {
    const map = new Map<string, ValidationIssue[]>();
    for (const issue of extraction.issues) {
      if (!issue.field_path) continue;
      const list = map.get(issue.field_path) ?? [];
      list.push(issue);
      map.set(issue.field_path, list);
    }
    return map;
  }, [extraction.issues]);

  const documentIssues = extraction.issues.filter((i) => !i.field_path);
  const hasEdits = Object.keys(edits).length > 0;
  const hasBlockingErrors = extraction.issues.some((i) => i.severity === "error");

  function setEdit(path: string, value: unknown) {
    setEdits((prev) => ({ ...prev, [path]: value }));
  }

  async function saveEdits() {
    if (!hasEdits) return;
    setSaving(true);
    try {
      const payload = Object.entries(edits).map(([field_path, value]) => ({ field_path, value }));
      const result = await documents.edit(extraction.document_id, payload);
      notify("ok", `${result.corrections_applied} field(s) corrected.`);
      setEdits({});
      onChanged();
    } catch (err) {
      notify("danger", err instanceof ApiError ? err.message : "Could not save corrections.");
    } finally {
      setSaving(false);
    }
  }

  async function approve(force = false) {
    setApproving(true);
    try {
      await documents.approve(extraction.document_id, { force });
      notify("ok", "Extraction approved.");
      onChanged();
    } catch (err) {
      notify("danger", err instanceof ApiError ? err.message : "Could not approve.");
    } finally {
      setApproving(false);
    }
  }

  async function reject() {
    if (!rejectReason.trim()) return;
    setRejecting(true);
    try {
      await documents.reject(extraction.document_id, rejectReason);
      notify("brand", "Extraction rejected.");
      setShowRejectForm(false);
      onChanged();
    } catch (err) {
      notify("danger", err instanceof ApiError ? err.message : "Could not reject.");
    } finally {
      setRejecting(false);
    }
  }

  const isFinal = extraction.status === "approved" || extraction.status === "rejected";

  return (
    <div className="space-y-4">
      {/* Overall status strip */}
      <div className="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-ink">
            Confidence:{" "}
            {extraction.overall_confidence !== null
              ? `${(extraction.overall_confidence * 100).toFixed(0)}%`
              : "—"}
          </span>
          {extraction.needs_review && <Badge tone="warn">Needs review</Badge>}
          {extraction.status === "approved" && <Badge tone="ok">Approved</Badge>}
          {extraction.status === "rejected" && <Badge tone="danger">Rejected</Badge>}
        </div>
        <span className="text-xs text-subtle">
          rev. {extraction.revision} · {extraction.extractor === "llm" ? extraction.model : "rule-based"}
        </span>
      </div>

      {/*
       * `review_reasons` is a snapshot written once when the extraction was
       * created — it is never cleared on approval. Rendering it unconditionally
       * would leave an orange "why this needs review" box sitting directly
       * under a green "Approved" badge, telling the reviewer two contradictory
       * things in the same screen. Once the extraction reaches a final state
       * the concern it names has already been resolved (that is what approving
       * means), so the live warning treatment only applies while still pending.
       */}
      {!isFinal && extraction.review_reasons.length > 0 && (
        <div className="rounded-lg bg-warn/10 px-4 py-3">
          <p className="text-xs font-semibold text-warn">Why this needs review</p>
          <ul className="mt-1.5 space-y-1">
            {extraction.review_reasons.map((reason, i) => (
              <li key={i} className="text-sm text-ink">
                • {reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {isFinal && extraction.status === "approved" && extraction.review_reasons.length > 0 && (
        <p className="text-xs text-subtle">
          Approved despite {extraction.review_reasons.length} review flag
          {extraction.review_reasons.length === 1 ? "" : "s"} — see the corrected field
          {extraction.fields.filter((f) => f.was_corrected).length === 1 ? "" : "s"} above.
        </p>
      )}

      {documentIssues.length > 0 && (
        <div className="space-y-1.5">
          {documentIssues.map((issue, i) => (
            <IssueRow key={i} issue={issue} />
          ))}
        </div>
      )}

      {/* Field list */}
      <div className="divide-y divide-line rounded-lg border border-line">
        {extraction.fields.map((field) => (
          <FieldRow
            key={field.field_path}
            field={field}
            issues={issuesByField.get(field.field_path) ?? []}
            editedValue={edits[field.field_path]}
            onChange={(value) => setEdit(field.field_path, value)}
            disabled={isFinal}
          />
        ))}
        {extraction.fields.length === 0 && (
          <p className="px-4 py-6 text-center text-sm text-subtle">No fields were extracted.</p>
        )}
      </div>

      {/* Actions */}
      {!isFinal && (
        <div className="flex items-center justify-between gap-3 border-t border-line pt-4">
          <div className="flex gap-2">
            {!showRejectForm ? (
              <Button variant="outline" size="sm" onClick={() => setShowRejectForm(true)}>
                Reject
              </Button>
            ) : (
              <div className="flex items-center gap-2">
                <Input
                  placeholder="Reason for rejecting…"
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  className="w-64"
                />
                <Button variant="danger" size="sm" loading={rejecting} onClick={reject}>
                  Confirm reject
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setShowRejectForm(false)}>
                  Cancel
                </Button>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            {hasEdits && (
              <Button variant="secondary" size="sm" loading={saving} onClick={saveEdits}>
                Save {Object.keys(edits).length} correction{Object.keys(edits).length === 1 ? "" : "s"}
              </Button>
            )}
            {hasBlockingErrors ? (
              <Button
                variant="outline"
                size="sm"
                loading={approving}
                disabled={hasEdits}
                onClick={() => approve(true)}
                title="Validation errors remain — this overrides them"
              >
                Approve anyway
              </Button>
            ) : (
              <Button size="sm" loading={approving} disabled={hasEdits} onClick={() => approve(false)}>
                Approve
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function FieldRow({
  field,
  issues,
  editedValue,
  onChange,
  disabled,
}: {
  field: ExtractionField;
  issues: ValidationIssue[];
  editedValue: unknown;
  onChange: (value: unknown) => void;
  disabled: boolean;
}) {
  const depth = field.field_path.split(".").length - 1;
  const currentValue = editedValue !== undefined ? editedValue : field.value;
  const displayValue = currentValue === null || currentValue === undefined ? "" : String(currentValue);
  const isEmpty = displayValue.trim() === "";
  const isEdited = editedValue !== undefined;
  const hasErrorIssue = issues.some((i) => i.severity === "error");
  const isObjectPlaceholder = typeof field.value === "object" && field.value !== null;

  if (isObjectPlaceholder) {
    // Object/list container rows (e.g. "supplier", "line_items") carry no
    // editable value of their own — their children do. Render as a subtle
    // section header instead of an empty input.
    return (
      <div
        className="bg-muted/40 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-subtle"
        style={{ paddingLeft: `${16 + depth * 16}px` }}
      >
        {field.label ?? formatFieldLabel(field.field_path)}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "px-4 py-3 transition-colors",
        field.needs_review && "bg-warn/5",
        hasErrorIssue && "bg-danger/5",
      )}
    >
      <div className="flex items-start gap-4" style={{ paddingLeft: `${depth * 16}px` }}>
        <div className="w-40 shrink-0 pt-1.5">
          <p className="text-sm font-medium text-ink">
            {field.label ?? formatFieldLabel(field.field_path)}
            {field.is_required && <span className="ml-0.5 text-danger">*</span>}
          </p>
          <p className="font-mono text-[11px] text-subtle">{field.field_path}</p>
        </div>

        <div className="flex-1">
          {disabled ? (
            <p className={cn("text-sm", isEmpty ? "italic text-subtle" : "text-ink")}>
              {isEmpty ? "Not found" : displayValue}
            </p>
          ) : displayValue.length > 60 ? (
            <Textarea
              value={displayValue}
              onChange={(e) => onChange(e.target.value)}
              rows={2}
              className={cn("text-sm", isEdited && "border-brand")}
            />
          ) : (
            <Input
              value={displayValue}
              placeholder="Not found"
              onChange={(e) => onChange(e.target.value)}
              className={cn("text-sm", isEdited && "border-brand")}
            />
          )}

          {issues.length > 0 && (
            <div className="mt-1.5 space-y-1">
              {issues.map((issue, i) => (
                <IssueRow key={i} issue={issue} compact />
              ))}
            </div>
          )}

          {field.evidence_text && (
            <p className="mt-1.5 truncate text-xs italic text-subtle" title={field.evidence_text}>
              &ldquo;{field.evidence_text}&rdquo;
            </p>
          )}
        </div>

        <div className="flex w-32 shrink-0 flex-col items-end gap-1 pt-1">
          <ConfidenceBadge band={field.confidence_band} />
          {field.source === "human" && <Badge tone="brand">Corrected</Badge>}
          {field.confidence !== null && (
            <span className="font-mono text-[11px] text-subtle">{(field.confidence * 100).toFixed(0)}%</span>
          )}
        </div>
      </div>
    </div>
  );
}

function IssueRow({ issue, compact }: { issue: ValidationIssue; compact?: boolean }) {
  const tone = issue.severity === "error" ? "danger" : issue.severity === "warning" ? "warn" : "neutral";
  const dot = { danger: "bg-danger", warn: "bg-warn", neutral: "bg-subtle" }[tone];
  return (
    <div className={cn("flex items-start gap-1.5", compact ? "text-xs" : "rounded-lg bg-surface px-3 py-2 text-sm")}>
      <span className={cn("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", dot)} />
      <span className={tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : "text-subtle"}>
        {issue.message}
      </span>
    </div>
  );
}
