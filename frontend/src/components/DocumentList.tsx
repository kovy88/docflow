"use client";

import Link from "next/link";
import { useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api, type DocumentInfo, type ExportFormat } from "@/lib/api";

export function DocumentList({ documents, onDelete }: {
  documents: DocumentInfo[];
  onDelete: (id: number) => void;
}) {
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const searchText = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("cs");
  const search = searchText(query.trim());
  const filtered = documents.filter((doc) => {
    const extraction = doc.latest_extraction;
    const matchesStatus = status === "all" || (status === "review"
      ? extraction && !extraction.reviewed_at && ["needs_review", "ok"].includes(extraction.status)
      : status === "draft" ? !extraction : extraction?.status === status);
    return matchesStatus && searchText(doc.filename).includes(search);
  });
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages);
  const shown = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);

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
      await api.exportBatch(latest.map((e) => e.id), format);
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

  const exportable = filtered.filter((d) => d.latest_extraction?.status === "approved");
  const toReview = filtered.filter((d) => {
    const e = d.latest_extraction;
    return e && !e.reviewed_at && (e.status === "needs_review" || e.status === "ok");
  });

  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Moje dokumenty</h2>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Formát exportu" value={format} onChange={(e) => setFormat(e.target.value as ExportFormat)}
            className="h-9 rounded-md border border-line-strong bg-surface px-3 text-sm">
            <option value="xlsx">Excel</option>
            <option value="pohoda">POHODA (XML, přijaté faktury)</option>
            <option value="money">Money S3 (XML, přijaté faktury)</option>
          </select>
          <button type="button" disabled={busy || selected.length === 0 || selected.length > 100}
            onClick={exportSelected} className="h-9 rounded-md bg-accent px-3 text-sm font-medium text-white disabled:opacity-50">
            {busy ? "Pracuji…" : `Exportovat (${selected.length})`}
          </button>
        </div>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
        <label className="text-sm">
          <span className="text-muted">Hledat dokument</span>
          <input type="search" value={query} onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            placeholder="Název souboru" className="mt-1 h-9 w-full rounded-md border border-line-strong bg-surface px-3" />
        </label>
        <label className="text-sm">
          <span className="text-muted">Stav dokumentu</span>
          <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}
            className="mt-1 h-9 w-full rounded-md border border-line-strong bg-surface px-3">
            <option value="all">Všechny stavy</option>
            <option value="review">Ke kontrole</option>
            <option value="approved">Schváleno</option>
            <option value="failed">Chyba</option>
            <option value="running">Zpracovává se</option>
            <option value="draft">Bez extrakce</option>
          </select>
        </label>
      </div>
      <p aria-live="polite" className="mt-2 text-sm text-muted">
        Nalezeno {filtered.length} z {documents.length} dokumentů.
        {selected.length > 0 && ` Vybráno ${selected.length} napříč stránkami a filtry.`}
        {(query || status !== "all") && <button type="button" className="ml-2 text-accent hover:underline"
          onClick={() => { setQuery(""); setStatus("all"); setPage(1); }}>Zrušit filtry</button>}
        {selected.length > 0 && <button type="button" disabled={busy} className="ml-2 text-accent hover:underline"
          onClick={() => setSelected([])}>Zrušit výběr</button>}
      </p>
      <p className="mt-2 text-sm text-muted">Vyber nejvýše 100 dokumentů se stejnou šablonou. Export zahrne jejich nejnovější, lidsky schválené výsledky bez chybových nálezů.
        {format !== "xlsx" && " Účetní export: jen faktury v CZK s jednou sazbou DPH; bez DUZP na dokladu se použije datum vystavení."}</p>
      {(exportable.length > 0 || toReview.length > 0) && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
          {exportable.length > 0 && (
            <button type="button" disabled={busy} className="text-accent hover:underline disabled:opacity-50"
              onClick={() => setSelected((previous) => [...new Set([...previous, ...exportable.map((d) => d.id)])].slice(0, 100))}>
              Vybrat schválené ve výsledcích ({Math.min(exportable.length, 100)})
            </button>
          )}
          {toReview.length > 0 && (
            <Link href={`/documents/${toReview[toReview.length - 1].id}`} className="text-accent hover:underline">
              Zkontrolovat další ({toReview.length}) →
            </Link>
          )}
        </div>
      )}
      {error && <p role="alert" className="mt-3 rounded-md bg-err-soft p-3 text-err">{error}</p>}
      {shown.length > 0 && <ul className="mt-3 divide-y divide-line rounded-lg border border-line bg-surface">
        {shown.map((doc) => (
          <li key={doc.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <input type="checkbox" aria-label={`Vybrat ${doc.filename}`} disabled={busy || (selected.length >= 100 && !selected.includes(doc.id))}
              checked={selected.includes(doc.id)}
              onChange={(e) => setSelected((prev) => e.target.checked ? [...prev, doc.id] : prev.filter((id) => id !== doc.id))} />
            <Link href={`/documents/${doc.id}`} title={doc.filename} className="min-w-0 flex-1 basis-32 truncate font-medium hover:text-accent">{doc.filename}</Link>
            {doc.latest_extraction && <StatusBadge status={doc.latest_extraction.status} />}
            <span className="text-sm text-muted">{doc.page_count} str.{doc.ocr_pages.length > 0 && " · OCR"}</span>
            <button type="button" disabled={busy} aria-label={`Smazat ${doc.filename}`}
              onClick={() => remove(doc)} className="text-sm text-err hover:underline disabled:opacity-50">Smazat</button>
          </li>
        ))}
      </ul>}
      {filtered.length === 0 && <p className="mt-3 rounded-lg border border-line bg-surface p-5 text-muted">Žádný dokument neodpovídá hledání a zvolenému stavu.</p>}
      {pages > 1 && <nav aria-label="Stránkování dokumentů" className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm">
        <button type="button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}
          className="h-9 rounded-md border border-line-strong px-3 disabled:opacity-50">Předchozí</button>
        <span aria-live="polite">Strana {currentPage} z {pages}</span>
        <button type="button" disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)}
          className="h-9 rounded-md border border-line-strong px-3 disabled:opacity-50">Další</button>
      </nav>}
    </section>
  );
}
