"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ExtractionPanel } from "@/components/ExtractionPanel";
import {
  api,
  parseTimestamp,
  type DocumentInfo,
  type Extraction,
  type Template,
} from "@/lib/api";

// pdf.js needs browser APIs, so the viewer is never rendered on the server.
const PdfViewer = dynamic(() => import("@/components/PdfViewer"), {
  ssr: false,
  loading: () => <p className="p-4 text-muted">Načítám prohlížeč…</p>,
});

export function Workspace({ documentId }: { documentId: number }) {
  const [doc, setDoc] = useState<DocumentInfo | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [extractions, setExtractions] = useState<Extraction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [quote, setQuote] = useState<string | null>(null);
  const [nextId, setNextId] = useState<number | null>(null);
  const [mobileView, setMobileView] = useState<"data" | "pdf">("data");
  const [sourceField, setSourceField] = useState<string | null>(null);
  const dataPanel = useRef<HTMLElement>(null);
  const reviewVersion = extractions.map((e) => `${e.id}:${e.status}:${e.reviewed_at}`).join(",");

  function showData() {
    setMobileView("data");
    requestAnimationFrame(() => {
      if (sourceField) dataPanel.current?.querySelector<HTMLInputElement>(`[data-field-key="${CSS.escape(sourceField)}"]`)?.focus({ preventScroll: true });
    });
  }
  // Stable object: react-pdf reloads the document whenever `file` changes identity.
  const file = useMemo(() => api.fileSource(documentId), [documentId]);

  useEffect(() => {
    Promise.all([
      api.document(documentId),
      api.templates(),
      api.extractions(documentId),
    ])
      .then(([d, t, e]) => {
        setDoc(d);
        setTemplates(t);
        setExtractions(e);
      })
      .catch((e: Error) => setError(e.message));
  }, [documentId]);

  // Next document waiting for review (oldest first), refreshed after every change here.
  useEffect(() => {
    api
      .documents()
      .then((docs) => {
        const waiting = docs.filter((d) => {
          const e = d.latest_extraction;
          return d.id !== documentId && e && !e.reviewed_at && (e.status === "needs_review" || e.status === "ok");
        });
        setNextId(waiting.at(-1)?.id ?? null);
      })
      .catch(() => setNextId(null));
  }, [documentId, reviewVersion]);

  if (error)
    return (
      <p className="m-6 rounded-md bg-err-soft px-3 py-2 text-err">{error}</p>
    );
  if (!doc) return <p className="p-6 text-muted">Načítám…</p>;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2 text-sm">
        <Link href="/" className="text-muted hover:text-foreground">
          Dokumenty
        </Link>
        <span className="text-muted">/</span>
        <span className="min-w-0 break-all font-medium">{doc.filename}</span>
        <span
          className={`rounded px-1.5 py-0.5 text-xs ${doc.source === "email" ? "bg-accent-soft text-accent" : "bg-background text-muted"}`}
        >
          {doc.source === "email"
            ? `E-mailem${doc.sender ? ` od ${doc.sender}` : ""}`
            : "Nahráno v aplikaci"}
          {doc.created_at &&
            ` · ${parseTimestamp(doc.created_at).toLocaleString("cs-CZ", { dateStyle: "short", timeStyle: "short" })}`}
        </span>
        {doc.ocr_pages.length > 0 && (
          <span
            className="rounded bg-warn-soft px-1.5 py-0.5 text-xs text-warn"
            title="Text těchto stran byl získán OCR (Tesseract), ne z textové vrstvy PDF."
          >
            OCR: str. {doc.ocr_pages.join(", ")}
          </span>
        )}
        {nextId !== null && (
          <Link href={`/documents/${nextId}`} className="ml-auto text-accent hover:underline">
            Další ke kontrole →
          </Link>
        )}
      </div>
      <div aria-label="Zobrazení dokumentu" className="flex shrink-0 items-center gap-4 border-b border-line bg-surface px-4 lg:hidden">
        <button type="button" aria-pressed={mobileView === "data"} aria-controls={`document-data-${documentId}`} onClick={showData}
          className={`min-h-10 border-b-2 px-2 ${mobileView === "data" ? "border-accent text-accent" : "border-transparent text-muted"}`}>Údaje</button>
        <button type="button" aria-pressed={mobileView === "pdf"} aria-controls={`document-preview-${documentId}`} onClick={() => setMobileView("pdf")}
          className={`min-h-10 border-b-2 px-2 ${mobileView === "pdf" ? "border-accent text-accent" : "border-transparent text-muted"}`}>PDF</button>
        {mobileView === "pdf" && sourceField && <button type="button" onClick={showData} className="ml-auto text-sm text-accent hover:underline">Zpět k údaji</button>}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)] overflow-hidden lg:grid-cols-[minmax(0,1fr)_minmax(420px,520px)]">
        <section
          id={`document-preview-${documentId}`}
          className={`min-h-0 border-line lg:block lg:border-r ${mobileView === "pdf" ? "block" : "hidden"}`}
          aria-label="Náhled dokumentu"
        >
          <PdfViewer
            file={file}
            page={page}
            onPageChange={setPage}
            quote={quote}
          />
        </section>
        <section
          ref={dataPanel}
          id={`document-data-${documentId}`}
          className={`min-h-0 overflow-y-auto bg-surface lg:block ${mobileView === "data" ? "block" : "hidden"}`}
          aria-label="Vytěžená data"
        >
          <ExtractionPanel
            document={doc}
            templates={templates}
            extractions={extractions}
            onExtractionsChange={setExtractions}
            nextId={nextId}
            onFocusField={(field, revealSource) => {
              setQuote(field?.quote ?? null);
              if (field?.page) setPage(field.page);
              if (revealSource && field) {
                setSourceField(field.key);
                setMobileView("pdf");
              }
            }}
          />
        </section>
      </div>
    </div>
  );
}
