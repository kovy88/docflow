"use client";

import {
  type Dispatch,
  type SetStateAction,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { guessTemplate } from "@/lib/templates";
import { StatusBadge } from "@/components/StatusBadge";
import { useLive } from "@/lib/access";
import {
  api,
  parseTimestamp,
  type DocumentInfo,
  type ExtractedField,
  type Extraction,
  type Issue,
  type Template,
} from "@/lib/api";

type Props = {
  document: DocumentInfo;
  templates: Template[];
  extractions: Extraction[];
  onExtractionsChange: Dispatch<SetStateAction<Extraction[]>>;
  onFocusField: (field: ExtractedField | null, revealSource?: boolean) => void;
  /** Next document waiting for review; approving moves there. */
  nextId?: number | null;
};

/** Time since the extraction was queued (survives page reloads). */
function Elapsed({ since }: { since: string }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const timer = setInterval(tick, 1000);
    const first = setTimeout(tick, 0);
    return () => {
      clearInterval(timer);
      clearTimeout(first);
    };
  }, []);
  const seconds = now
    ? Math.max(0, Math.floor((now - parseTimestamp(since).getTime()) / 1000))
    : 0;
  return (
    <span className="tabular-nums">{`${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`}</span>
  );
}


export function ExtractionPanel({
  document,
  templates,
  extractions,
  onExtractionsChange,
  onFocusField,
  nextId = null,
}: Props) {
  const [templateId, setTemplateId] = useState(
    extractions[0]?.template_id ?? guessTemplate(document.filename, templates),
  );
  const [selectedId, setSelectedId] = useState<number | null>(
    extractions[0]?.id ?? null,
  );
  const [starting, setStarting] = useState(false);
  const [suggestingSince, setSuggestingSince] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = useLive();

  // Cached routes keep their state; a finished suggestion must not leave the restored
  // document locked in its loading state when the user returns from the editor.
  useEffect(() => () => setSuggestingSince(null), []);

  const extraction = extractions.find((e) => e.id === selectedId) ?? null;
  const runningIds = extractions
    .filter((e) => e.status === "running")
    .map((e) => e.id)
    .join(",");
  const busy = starting || runningIds !== "" || suggestingSince !== null;
  const router = useRouter();

  // Extractions run in the background on the server; poll until they finish.
  useEffect(() => {
    if (!runningIds) return;
    const timer = setInterval(async () => {
      for (const id of runningIds.split(",").map(Number)) {
        const fresh = await api.extraction(id).catch(() => null);
        if (fresh) {
          onExtractionsChange((prev) =>
            prev.map((e) => (e.id === fresh.id ? fresh : e)),
          );
        }
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [runningIds, onExtractionsChange]);

  function select(id: number) {
    setSelectedId(id);
    onFocusField(null);
  }

  function replace(updated: Extraction) {
    onExtractionsChange((prev) =>
      [updated, ...prev.filter((e) => e.id !== updated.id)].sort(
        (a, b) => b.id - a.id,
      ),
    );
    select(updated.id);
  }

  async function suggest() {
    setSuggestingSince(new Date().toISOString());
    setError(null);
    try {
      const draft = await api.suggestTemplate(document.id);
      sessionStorage.setItem(
        "docflow:template-draft",
        JSON.stringify({ ...draft, fromAi: true }),
      );
      router.push(`/templates/new?draft=${crypto.randomUUID()}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSuggestingSince(null);
    }
  }

  async function run() {
    setStarting(true);
    setError(null);
    try {
      replace(await api.extract(document.id, templateId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-10 space-y-3 border-b border-line bg-surface p-4">
        {live ? <>
        <div className="flex gap-2">
          <select
            value={templateId}
            onChange={(e) => setTemplateId(e.target.value)}
            disabled={busy}
            className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1.5"
            aria-label="Šablona"
          >
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={run}
            disabled={busy || !document.has_text || !templateId}
            className={`rounded-md px-3 py-1.5 font-medium disabled:opacity-50 ${extraction ? "border border-line-strong bg-surface hover:bg-subtle" : "bg-accent text-white hover:bg-accent-hover"}`}
          >
            {busy ? "Zpracovává se…" : "Extrahovat"}
          </button>
        </div>
        <p className="text-xs text-muted">
          {suggestingSince ? (
            <>
              Model navrhuje šablonu… <Elapsed since={suggestingSince} />
            </>
          ) : (
            <>
              Jiný typ dokumentu?{" "}
              <button
                type="button"
                onClick={suggest}
                disabled={busy || !document.has_text}
                className="font-medium text-accent hover:underline disabled:opacity-50"
              >
                Navrhnout šablonu pomocí AI
              </button>
            </>
          )}
        </p>
        </> : (
          <p className="text-sm text-muted">Ukázkový výsledek je jen pro čtení. Vyber údaj nebo jeho zdroj a ověř ho v PDF. Úpravy a schválení jsou dostupné po zadání přístupového kódu.</p>
        )}
        {!document.has_text && (
          <p className="text-sm text-err">
            PDF nemá textovou vrstvu a OCR v něm nenašlo text (chybí
            Tesseract?).
          </p>
        )}
        {document.ocr_pages.length > 0 && (
          <p className="rounded-md bg-warn-soft px-3 py-2 text-sm text-warn">
            Sken: text {document.ocr_pages.length === 1 ? "strany" : "stran"}{" "}
            {document.ocr_pages.join(", ")} byl přečten OCR a může obsahovat
            chyby. Citace se u nich ověřují volněji a v náhledu se nezvýrazní.
          </p>
        )}
        {error && (
          <p className="rounded-md bg-err-soft px-3 py-2 text-sm text-err">
            {error}
          </p>
        )}

        {extractions.length > 0 && (
          <div className="flex items-center gap-2 text-sm">
            <select
              value={selectedId ?? ""}
              onChange={(e) => select(Number(e.target.value))}
              className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1"
              aria-label="Historie extrakcí"
            >
              {extractions.map((e) => (
                <option key={e.id} value={e.id}>
                  #{e.id} ·{" "}
                  {templates.find((t) => t.id === e.template_id)?.name ??
                    e.template_id}{" "}
                  · {parseTimestamp(e.created_at).toLocaleString("cs-CZ")}
                </option>
              ))}
            </select>
            {extraction && <StatusBadge status={extraction.status} />}
          </div>
        )}
      </div>

      {extraction?.status === "running" ? (
        <div className="space-y-2 p-4">
          <p className="flex items-center gap-2 font-medium">
            <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
            {extraction.usage.phase === "queued" ? "Čeká ve frontě…" : extraction.usage.phase === "processing" ? "Extrahuji…" : "Zpracovává se…"}{" "}
            <Elapsed since={extraction.usage.started_at ?? extraction.created_at} />
          </p>
          <p className="text-sm text-muted">
            {extraction.usage.phase === "queued"
              ? "Dokument čeká na zahájení zpracování. Čas ukazuje dobu čekání."
              : extraction.usage.phase === "processing"
                ? `Dokument zpracovává model ${extraction.model}. Čas ukazuje dobu samotné extrakce.`
                : `Dokument čeká ve frontě nebo ho zpracovává model ${extraction.model}. Čas se měří od zařazení do fronty.`}
            {extraction.usage.phase === "processing" && extraction.usage.queue_wait_s != null && ` Ve frontě čekal ${Math.round(extraction.usage.queue_wait_s)} s.`}
            {" "}Extrakce běží na serveru, stránku můžeš zavřít a vrátit se později.
          </p>
        </div>
      ) : extraction ? (
        <ReviewForm
          key={`${extraction.id}-${extraction.reviewed_at}`}
          extraction={extraction}
          onSaved={(saved) => {
            replace(saved);
            if (nextId !== null) router.push(`/documents/${nextId}`);
          }}
          onFocusField={onFocusField}
          ocrPages={document.ocr_pages}
          hasNext={nextId !== null}
        />
      ) : (
        !busy && (
          <p className="p-4 text-muted">
            Vyber šablonu a spusť extrakci. Výsledek se uloží a najdeš ho tu i
            později.
          </p>
        )
      )}
    </div>
  );
}

/** Milliseconds the page was visible since mount: review time for the pilot measurement,
 *  without the time the tab spent in the background. */
function useVisibleTime() {
  const total = useRef(0);
  const since = useRef<number | null>(null);
  useEffect(() => {
    const sync = () => {
      if (document.hidden && since.current !== null) {
        total.current += performance.now() - since.current;
        since.current = null;
      } else if (!document.hidden && since.current === null) {
        since.current = performance.now();
      }
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  return () =>
    total.current +
    (since.current === null ? 0 : performance.now() - since.current);
}

function formatValue(field: ExtractedField) {
  if (field.value === null) return null;
  if (field.type === "number")
    return Number(field.value).toLocaleString("cs-CZ");
  if (field.type === "date")
    return new Date(String(field.value)).toLocaleDateString("cs-CZ");
  return null; // strings are shown in the input as-is
}

/** Below this model confidence a value is listed among the ones to check first. */
const LOW_CONFIDENCE = 0.8;

/** Why a field needs a closer look; empty when it has a verified source and no findings. */
function attentionReasons(field: ExtractedField, issues: Issue[]): string[] {
  const reasons: string[] = [];
  if (issues.some((i) => i.severity === "error")) reasons.push("chyba kontroly");
  else if (issues.length) reasons.push("upozornění");
  if (field.corrected) return reasons;
  if (field.raw !== null && !field.grounded) reasons.push("citace nenalezena v PDF");
  if (field.raw !== null && field.confidence < LOW_CONFIDENCE) reasons.push("nízká jistota");
  return reasons;
}

/** Editable field list. Keyed by extraction + review time, so drafts reset on change. */
function ReviewForm({
  extraction,
  onSaved,
  onFocusField,
  ocrPages,
  hasNext,
}: {
  extraction: Extraction;
  onSaved: (extraction: Extraction) => void;
  onFocusField: (field: ExtractedField | null, revealSource?: boolean) => void;
  ocrPages: number[];
  hasNext: boolean;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>(() =>
    Object.fromEntries(extraction.fields.map((f) => [f.key, f.raw ?? ""])),
  );
  const [focused, setFocused] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = useLive();
  const reviewMs = useVisibleTime();
  const list = useRef<HTMLDivElement>(null);
  const approveButton = useRef<HTMLButtonElement>(null);

  const changed = extraction.fields.filter(
    (f) => drafts[f.key] !== (f.raw ?? ""),
  );
  const docIssues = extraction.issues.filter((i) => i.fields.length !== 1);
  const errors = extraction.issues.filter((i) => i.severity === "error").length;
  const warnings = extraction.issues.length - errors;
  const tokens =
    (extraction.usage.input_tokens ?? 0) +
    (extraction.usage.output_tokens ?? 0);

  const fieldIssues = (key: string) =>
    extraction.issues.filter((i) => i.fields.length === 1 && i.fields[0] === key);
  const rows = extraction.fields.map((field) => ({
    field,
    issues: fieldIssues(field.key),
    reasons: attentionReasons(field, fieldIssues(field.key)),
  }));
  const toCheck = rows.filter((r) => r.reasons.length > 0);
  const verified = rows.filter((r) => r.reasons.length === 0);
  const canApprove =
    live && !saving && extraction.status !== "failed" &&
    !(extraction.status === "approved" && changed.length === 0);

  /** Enter moves to the next value (to-check first), past the last one to the approve
   *  button; Ctrl/⌘ + Enter approves from anywhere in the list. */
  function onFieldKey(e: React.KeyboardEvent<HTMLInputElement>, key: string) {
    if (e.key !== "Enter" || e.shiftKey || e.altKey) return;
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      if (canApprove) approve();
      return;
    }
    const order = [...toCheck, ...verified].map((r) => r.field.key);
    const next = order[order.indexOf(key) + 1];
    const input = next
      ? list.current?.querySelector<HTMLInputElement>(`[data-field-key="${CSS.escape(next)}"]`)
      : null;
    if (input) {
      input.focus();
      input.scrollIntoView({ block: "nearest" });
    } else approveButton.current?.focus();
  }

  function focus(field: ExtractedField) {
    setFocused(field.key);
    onFocusField(field);
  }

  async function approve() {
    const values = Object.fromEntries(
      changed.map((f) => [
        f.key,
        drafts[f.key].trim() === "" ? null : drafts[f.key],
      ]),
    );
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.review(extraction.id, values, Math.min(reviewMs() / 1000, 4 * 3600)));
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  function renderRow({ field, issues, reasons }: (typeof rows)[number]) {
    const normalized = formatValue(field);
    const active = focused === field.key;
    return (
      <li
        key={field.key}
        onClick={() => focus(field)}
        className={`cursor-pointer px-4 py-3 ${active ? "bg-accent-soft" : "hover:bg-background"}`}
      >
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label htmlFor={`f-${extraction.id}-${field.key}`} className="font-medium">
            {field.label}
          </label>
          {reasons.map((reason) => (
            <span key={reason} className="rounded bg-warn-soft px-1.5 py-0.5 text-xs text-warn">
              {reason}
            </span>
          ))}
          <span className="ml-auto flex items-center gap-2 text-xs text-muted">
            {field.corrected && (
              <span className="text-accent">opraveno</span>
            )}
            {field.raw !== null && !field.corrected && (
              <span
                title={
                  field.grounded
                    ? "Citace nalezena v PDF"
                    : "Citace v PDF nenalezena"
                }
                className={field.grounded ? "text-ok" : "text-warn"}
              >
                {field.grounded ? "✓ ověřeno" : "⚠ neověřeno"}
              </span>
            )}
            {field.raw !== null && !field.corrected && (
              <span title="Jistota modelu">
                {Math.round(field.confidence * 100)} %
              </span>
            )}
            {field.page && (
              <button type="button" onClick={() => { focus(field); onFocusField(field, true); }} className="text-accent hover:underline"
                aria-label={`Zobrazit zdroj: ${field.label}, strana ${field.page}`}>
                Zdroj · str. {field.page} ↗
                {ocrPages.includes(field.page) && " (OCR)"}
              </button>
            )}
          </span>
        </div>
        <input
          id={`f-${extraction.id}-${field.key}`}
          data-field-key={field.key}
          value={drafts[field.key]}
          readOnly={!live}
          onChange={(e) =>
            setDrafts({ ...drafts, [field.key]: e.target.value })
          }
          onFocus={() => focus(field)}
          onKeyDown={(e) => onFieldKey(e, field.key)}
          enterKeyHint="next"
          placeholder="nenalezeno"
          className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1.5 font-mono text-sm focus:border-accent focus:outline-none"
        />
        {normalized && (
          <p className="mt-1 text-xs text-muted">→ {normalized}</p>
        )}
        {active && field.quote && (
          <p className="mt-2 border-l-2 border-line-strong pl-3 text-sm text-muted">
            <q>{field.quote}</q>
          </p>
        )}
        {issues.map((issue, i) => (
          <IssueLine key={i} issue={issue} compact />
        ))}
      </li>
    );
  }

  return (
    <>
      <div className="space-y-2 border-b border-line p-4">
        <p className="text-sm text-muted">
          {errors}{" "}
          {errors === 1
            ? "chyba"
            : errors >= 2 && errors <= 4
              ? "chyby"
              : "chyb"}{" "}
          · {warnings} upozornění
        </p>
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">Podrobnosti zpracování</summary>
          <p className="mt-1 break-words">{extraction.model}
            {tokens > 0 && ` · ${tokens.toLocaleString("cs-CZ")} tokenů`}
            {live && extraction.usage.latency_s != null && ` · extrakce ${Math.round(extraction.usage.latency_s)} s`}
            {live && extraction.usage.queue_wait_s != null && ` · čekání ${Math.round(extraction.usage.queue_wait_s)} s`}
          </p>
        </details>
        {docIssues.map((issue, i) => (
          <IssueLine key={i} issue={issue} />
        ))}
      </div>

      <div ref={list}>
        {toCheck.length > 0 ? (
          <h3 className="border-b border-line bg-warn-soft px-4 py-2 text-sm font-medium text-warn">
            Zkontrolovat ({toCheck.length})
          </h3>
        ) : (
          <p className="border-b border-line bg-ok-soft px-4 py-2 text-sm text-ok">
            Všechny údaje mají citaci v PDF a prošly kontrolami.
          </p>
        )}
        <ul className="divide-y divide-line">
          {toCheck.map(renderRow)}
        </ul>
        {toCheck.length > 0 && verified.length > 0 && (
          <h3 className="border-y border-line bg-background px-4 py-2 text-sm font-medium text-muted">
            Ověřené údaje ({verified.length})
          </h3>
        )}
        <ul className="divide-y divide-line">
          {verified.map(renderRow)}
        </ul>
      </div>

      <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t border-line bg-surface p-4">
        {live && (
          <span className="flex gap-2 text-sm">
            {(["csv", "json"] as const).map((format) => (
              <button
                key={format}
                type="button"
                className="text-accent hover:underline"
                onClick={() =>
                  api
                    .download(extraction.id, format)
                    .catch((e: Error) => setError(e.message))
                }
              >
                {format.toUpperCase()}
              </button>
            ))}
          </span>
        )}
        {extraction.reviewed_at && (
          <span className="text-sm text-muted">
            Zkontrolováno{" "}
            {parseTimestamp(extraction.reviewed_at).toLocaleString("cs-CZ")}
          </span>
        )}
        {live && (
          <span className="ml-auto flex flex-wrap items-center justify-end gap-3">
            <span className="hidden text-xs text-muted sm:inline">
              <kbd className="rounded border border-line px-1">Enter</kbd> další údaj ·{" "}
              <kbd className="rounded border border-line px-1">Ctrl/⌘ + Enter</kbd> schválit
            </span>
            <button
              ref={approveButton}
              type="button"
              onClick={approve}
              disabled={!canApprove}
              className="rounded-md bg-accent px-3 py-2 font-medium text-white hover:bg-accent-hover disabled:opacity-50"
            >
              {saving
                ? "Ukládám…"
                : extraction.status === "approved" && changed.length === 0
                  ? "Schváleno"
                  : `${changed.length ? `Uložit opravy (${changed.length}) a schválit` : "Schválit"}${hasNext ? " a další →" : ""}`}
            </button>
          </span>
        )}
        {!live && <span className="text-sm text-muted">Veřejné demo · výsledek nelze měnit ani schválit.</span>}
        {error && <span className="text-sm text-err">{error}</span>}
      </div>
    </>
  );
}

function IssueLine({ issue, compact }: { issue: Issue; compact?: boolean }) {
  const tone =
    issue.severity === "error"
      ? "bg-err-soft text-err"
      : "bg-warn-soft text-warn";
  return (
    <p
      className={`rounded-md ${compact ? "mt-1 px-2 py-1 text-xs" : "px-3 py-2 text-sm"} ${tone}`}
    >
      {issue.message}
    </p>
  );
}
