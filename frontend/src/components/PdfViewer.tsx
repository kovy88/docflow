"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

// Must be set in the same module that renders <Document> (see react-pdf README).
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

type Props = {
  url: string;
  page: number;
  onPageChange: (page: number) => void;
  /** Verbatim quote to highlight on the current page. */
  quote: string | null;
};

const canon = (s: string) =>
  s.normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** Mark the part of a text-layer item that belongs to the quote. Text items are usually
 *  lines, so the quote is either inside one item or spans several whole items. */
export function highlight(str: string, quote: string | null): string {
  const safe = escapeHtml(str);
  if (!quote) return safe;
  const q = canon(quote);
  const item = canon(str);
  if (item.length < 2 || !q) return safe;
  if (q.includes(item)) return `<mark>${safe}</mark>`;
  const at = str.toLowerCase().indexOf(quote.toLowerCase().trim());
  if (at === -1) return safe;
  const end = at + quote.trim().length;
  return `${escapeHtml(str.slice(0, at))}<mark>${escapeHtml(str.slice(at, end))}</mark>${escapeHtml(str.slice(end))}`;
}

export default function PdfViewer({ url, page, onPageChange, quote }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [pages, setPages] = useState(0);
  const [width, setWidth] = useState(600);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(280, entry.contentRect.width - 32)),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const renderText = useCallback(
    ({ str }: { str: string }) => highlight(str, quote),
    [quote],
  );

  const button =
    "rounded border border-line bg-surface px-2 py-1 text-sm hover:bg-background disabled:opacity-40";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line bg-surface px-3 py-2">
        <button
          type="button"
          className={button}
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          aria-label="Předchozí strana"
        >
          ←
        </button>
        <span className="text-sm tabular-nums text-muted">
          {page} / {pages || "–"}
        </span>
        <button
          type="button"
          className={button}
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
          aria-label="Další strana"
        >
          →
        </button>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            className={button}
            onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}
            aria-label="Oddálit"
          >
            −
          </button>
          <span className="w-12 text-center text-sm tabular-nums text-muted">
            {Math.round(zoom * 100)} %
          </span>
          <button
            type="button"
            className={button}
            onClick={() => setZoom((z) => Math.min(3, z + 0.25))}
            aria-label="Přiblížit"
          >
            +
          </button>
        </div>
      </div>
      <div
        ref={container}
        className="min-h-0 flex-1 overflow-auto bg-background p-4"
      >
        <Document
          file={url}
          onLoadSuccess={({ numPages }) => setPages(numPages)}
          loading={<p className="text-muted">Načítám PDF…</p>}
          error={<p className="text-err">PDF se nepodařilo načíst.</p>}
        >
          <Page
            pageNumber={Math.min(page, pages || 1)}
            width={width * zoom}
            customTextRenderer={renderText}
            className="mx-auto w-fit shadow-md"
          />
        </Document>
      </div>
    </div>
  );
}
