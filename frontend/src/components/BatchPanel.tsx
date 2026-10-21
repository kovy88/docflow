"use client";

import { useEffect, useState } from "react";
import { api, plural, type DocumentInfo, type Extraction, type Template } from "@/lib/api";
import { guessTemplate } from "@/lib/templates";

/** After a multi-file upload: pick one template and queue an extraction for every file. */
export function BatchPanel({
  documents,
  onStarted,
  onCancel,
}: {
  documents: DocumentInfo[];
  onStarted: (extractions: Extraction[]) => void;
  onCancel: () => void;
}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const withoutText = documents.filter((d) => !d.has_text);
  const ready = documents.filter((d) => d.has_text);

  useEffect(() => {
    api
      .templates()
      .then((t) => {
        setTemplates(t);
        const guesses = new Set(documents.map((d) => guessTemplate(d.filename, t)));
        setTemplateId((current) => current || (guesses.size === 1 ? [...guesses][0] : ""));
      })
      .catch((e: Error) => setError(e.message));
  }, [documents]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      onStarted(await api.extractBatch(ready.map((d) => d.id), templateId));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <section className="mt-6 rounded-lg border border-line bg-surface p-5" aria-label="Dávková extrakce">
      <h2 className="text-base font-semibold">
        Nahráno {plural(documents.length, "dokument", "dokumenty", "dokumentů")}
      </h2>
      <p className="mt-1 text-sm text-muted">
        Vyber šablonu a spusť extrakci pro všechny najednou. Dokumenty se zpracují postupně
        na serveru; stav uvidíš v seznamu a stránku můžeš mezitím zavřít.
      </p>
      {withoutText.length > 0 && (
        <p className="mt-2 text-sm text-warn">
          Bez čitelného textu (přeskočí se): {withoutText.map((d) => d.filename).join(", ")}
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <select
          value={templateId}
          onChange={(e) => setTemplateId(e.target.value)}
          disabled={busy}
          aria-label="Šablona pro dávku"
          className="h-9 min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-2"
        >
          <option value="">Vyber šablonu</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={start}
          disabled={busy || !templateId || ready.length === 0}
          className="h-9 rounded-md bg-accent px-3 font-medium text-white hover:bg-accent-hover disabled:opacity-50"
        >
          {busy ? "Spouštím…" : `Extrahovat (${ready.length})`}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="h-9 rounded-md border border-line-strong px-3 hover:bg-subtle disabled:opacity-50"
        >
          Později
        </button>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-err">{error}</p>}
    </section>
  );
}
