"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ExtractionPanel } from "@/components/ExtractionPanel";
import {
  api,
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

  if (error)
    return (
      <p className="m-6 rounded-md bg-err-soft px-3 py-2 text-err">{error}</p>
    );
  if (!doc) return <p className="p-6 text-muted">Načítám…</p>;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line bg-surface px-4 py-2 text-sm">
        <Link href="/" className="text-muted hover:text-foreground">
          Dokumenty
        </Link>
        <span className="text-muted">/</span>
        <span className="font-medium">{doc.filename}</span>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] grid-rows-[60vh_auto] overflow-y-auto lg:grid-cols-[minmax(0,1fr)_minmax(420px,520px)] lg:grid-rows-1 lg:overflow-hidden">
        <section
          className="min-h-0 border-line lg:border-r"
          aria-label="Náhled dokumentu"
        >
          <PdfViewer
            url={api.fileUrl(documentId)}
            page={page}
            onPageChange={setPage}
            quote={quote}
          />
        </section>
        <section
          className="min-h-0 bg-surface lg:overflow-y-auto"
          aria-label="Vytěžená data"
        >
          <ExtractionPanel
            document={doc}
            templates={templates}
            extractions={extractions}
            onExtractionsChange={setExtractions}
            onFocusField={(field) => {
              setQuote(field?.quote ?? null);
              if (field?.page) setPage(field.page);
            }}
          />
        </section>
      </div>
    </div>
  );
}
