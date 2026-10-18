"use client";

import { type Dispatch, type SetStateAction, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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
  onFocusField: (field: ExtractedField | null) => void;
};

const STATUS: Record<
  Extraction["status"],
  { label: string; className: string }
> = {
  ok: { label: "Bez nálezů", className: "bg-ok-soft text-ok" },
  needs_review: { label: "Ke kontrole", className: "bg-warn-soft text-warn" },
  approved: { label: "Schváleno", className: "bg-accent-soft text-accent" },
  failed: { label: "Selhalo", className: "bg-err-soft text-err" },
  running: { label: "Běží", className: "bg-accent-soft text-accent" },
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

/** Preselect a template from the file name ("faktura_…", "contract_03.pdf"). */
function guessTemplate(filename: string, templates: Template[]): string {
  const name = filename.toLowerCase();
  const hints: Record<string, string[]> = {
    invoice: ["invoice", "faktur"],
    contract: ["contract", "smlouv"],
    insurance_policy: ["policy", "pojist"],
  };
  const hit = Object.entries(hints).find(([, words]) =>
    words.some((w) => name.includes(w)),
  );
  return hit?.[0] ?? templates[0]?.id ?? "invoice";
}

export function ExtractionPanel({
  document,
  templates,
  extractions,
  onExtractionsChange,
  onFocusField,
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
        if (fresh && fresh.status !== "running") {
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
      router.push("/templates/new");
    } catch (e) {
      setError((e as Error).message);
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
            disabled={busy || !document.has_text}
            className="rounded-md bg-accent px-3 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Extrahuji…" : "Extrahovat"}
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
        {!document.has_text && (
          <p className="text-sm text-err">
            PDF nemá textovou vrstvu, OCR zatím není podporováno.
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
            {extraction && (
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS[extraction.status].className}`}
              >
                {STATUS[extraction.status].label}
              </span>
            )}
          </div>
        )}
      </div>

      {extraction?.status === "running" ? (
        <div className="space-y-2 p-4">
          <p className="flex items-center gap-2 font-medium">
            <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
            Extrahuji… <Elapsed since={extraction.created_at} />
          </p>
          <p className="text-sm text-muted">
            Model {extraction.model} běží lokálně, jeden dokument trvá zhruba
            2–3 minuty. Extrakce běží na serveru, stránku můžeš zavřít a vrátit
            se později.
          </p>
        </div>
      ) : extraction ? (
        <ReviewForm
          key={`${extraction.id}-${extraction.reviewed_at}`}
          extraction={extraction}
          onSaved={replace}
          onFocusField={onFocusField}
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

function formatValue(field: ExtractedField) {
  if (field.value === null) return null;
  if (field.type === "number")
    return Number(field.value).toLocaleString("cs-CZ");
  if (field.type === "date")
    return new Date(String(field.value)).toLocaleDateString("cs-CZ");
  return null; // strings are shown in the input as-is
}

/** Editable field list. Keyed by extraction + review time, so drafts reset on change. */
function ReviewForm({
  extraction,
  onSaved,
  onFocusField,
}: {
  extraction: Extraction;
  onSaved: (extraction: Extraction) => void;
  onFocusField: (field: ExtractedField | null) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>(() =>
    Object.fromEntries(extraction.fields.map((f) => [f.key, f.raw ?? ""])),
  );
  const [focused, setFocused] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const changed = extraction.fields.filter(
    (f) => drafts[f.key] !== (f.raw ?? ""),
  );
  const docIssues = extraction.issues.filter((i) => i.fields.length !== 1);
  const errors = extraction.issues.filter((i) => i.severity === "error").length;
  const warnings = extraction.issues.length - errors;
  const tokens =
    (extraction.usage.input_tokens ?? 0) +
    (extraction.usage.output_tokens ?? 0);

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
      onSaved(await api.review(extraction.id, values));
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
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
          · {warnings} upozornění · {extraction.model}
          {tokens > 0 && ` · ${tokens.toLocaleString("cs-CZ")} tokenů`}
          {extraction.usage.latency_s != null &&
            ` · ${Math.round(extraction.usage.latency_s)} s`}
        </p>
        {docIssues.map((issue, i) => (
          <IssueLine key={i} issue={issue} />
        ))}
      </div>

      <ul className="divide-y divide-line">
        {extraction.fields.map((field) => {
          const fieldIssues = extraction.issues.filter(
            (i) => i.fields.length === 1 && i.fields[0] === field.key,
          );
          const normalized = formatValue(field);
          const active = focused === field.key;
          return (
            <li
              key={field.key}
              onClick={() => focus(field)}
              className={`cursor-pointer px-4 py-3 ${active ? "bg-accent-soft" : "hover:bg-background"}`}
            >
              <div className="flex items-center gap-2 text-sm">
                <label htmlFor={`f-${field.key}`} className="font-medium">
                  {field.label}
                </label>
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
                  {field.page && <span>str. {field.page}</span>}
                </span>
              </div>
              <input
                id={`f-${field.key}`}
                value={drafts[field.key]}
                onChange={(e) =>
                  setDrafts({ ...drafts, [field.key]: e.target.value })
                }
                onFocus={() => focus(field)}
                placeholder="nenalezeno"
                className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1.5 font-mono text-sm focus:border-accent focus:outline-none"
              />
              {normalized && (
                <p className="mt-1 text-xs text-muted">→ {normalized}</p>
              )}
              {active && field.quote && (
                <p className="mt-1 text-xs text-muted">
                  Citace: <q className="italic">{field.quote}</q>
                </p>
              )}
              {fieldIssues.map((issue, i) => (
                <IssueLine key={i} issue={issue} compact />
              ))}
            </li>
          );
        })}
      </ul>

      <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t border-line bg-surface p-4">
        <button
          type="button"
          onClick={approve}
          disabled={saving || extraction.status === "failed"}
          className="rounded-md bg-foreground px-3 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {saving
            ? "Ukládám…"
            : changed.length
              ? `Uložit opravy (${changed.length}) a schválit`
              : "Schválit"}
        </button>
        <span className="ml-auto flex gap-2 text-sm">
          <a
            className="text-accent hover:underline"
            href={api.exportUrl(extraction.id, "csv")}
          >
            CSV
          </a>
          <a
            className="text-accent hover:underline"
            href={api.exportUrl(extraction.id, "json")}
          >
            JSON
          </a>
        </span>
        {extraction.reviewed_at && (
          <span className="text-sm text-muted">
            Zkontrolováno{" "}
            {parseTimestamp(extraction.reviewed_at).toLocaleString("cs-CZ")}
          </span>
        )}
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
