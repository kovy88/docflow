"use client";

import Link from "next/link";
import { useState } from "react";
import { api, type DocumentInfo } from "@/lib/api";

export function DocumentList({ documents, onDelete }: {
  documents: DocumentInfo[];
  onDelete: (id: number) => void;
}) {
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function exportSelected() {
    setBusy(true);
    setError(null);
    try {
      const histories = await Promise.all(selected.map((id) => api.extractions(id)));
      // Always use the latest attempt. Never silently export an older approved version.
      const latest = histories.map((history) => history[0]);
      if (latest.some((e) => !e || e.status !== "approved" || !e.reviewed_at || e.issues.some((i) => i.severity === "error"))) {
        throw new Error("Nejnovější extrakce každého vybraného dokumentu musí být schválená a bez chyb. Otevři dokumenty a dokonči kontrolu.");
      }
      await api.exportBatch(latest.map((e) => e.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(doc: DocumentInfo) {
    if (!window.confirm(`Smazat „${doc.filename}“ včetně PDF, textu a všech extrakcí? Tuto akci nelze vrátit.`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteDocument(doc.id);
      setSelected((prev) => prev.filter((id) => id !== doc.id));
      onDelete(doc.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Moje dokumenty</h2>
        <button type="button" disabled={busy || selected.length === 0 || selected.length > 100}
          onClick={exportSelected} className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
          {busy ? "Pracuji…" : `Export do Excelu (${selected.length})`}
        </button>
      </div>
      <p className="mt-2 text-sm text-muted">Vyber nejvýše 100 dokumentů se stejnou šablonou. Export zahrne jejich nejnovější, lidsky schválené výsledky bez chybových nálezů.</p>
      {error && <p role="alert" className="mt-3 rounded-md bg-err-soft p-3 text-err">{error}</p>}
      <ul className="mt-3 divide-y divide-line rounded-lg border border-line bg-surface">
        {documents.map((doc) => (
          <li key={doc.id} className="flex items-center gap-3 px-4 py-3">
            <input type="checkbox" aria-label={`Vybrat ${doc.filename}`} disabled={busy}
              checked={selected.includes(doc.id)}
              onChange={(e) => setSelected((prev) => e.target.checked ? [...prev, doc.id] : prev.filter((id) => id !== doc.id))} />
            <Link href={`/documents/${doc.id}`} className="min-w-0 flex-1 truncate font-medium hover:text-accent">{doc.filename}</Link>
            <span className="text-sm text-muted">{doc.page_count} str.{doc.ocr_pages.length > 0 && " · OCR"}</span>
            <button type="button" disabled={busy} aria-label={`Smazat ${doc.filename}`}
              onClick={() => remove(doc)} className="text-sm text-err hover:underline disabled:opacity-50">Smazat</button>
          </li>
        ))}
      </ul>
    </section>
  );
}
