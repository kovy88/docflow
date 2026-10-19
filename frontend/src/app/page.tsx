"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { DocumentList } from "@/components/DocumentList";
import { PilotOffer } from "@/components/PilotOffer";
import { DemoPicker } from "@/components/DemoPicker";
import { useLive } from "@/lib/access";
import { api, REPO_URL, type DocumentInfo } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const live = useLive();
  const input = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<DocumentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    api
      .documents()
      .then(setDocuments)
      .catch((e: Error) => setError(`Backend není dostupný: ${e.message}`));
  }, []);

  // The page stays alive (cacheComponents) while a document is open; reset on the way out.
  useEffect(() => () => setUploading(false), []);

  async function upload(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const doc = await api.upload(file);
      router.push(`/documents/${doc.id}`);
    } catch (e) {
      setError((e as Error).message);
      setUploading(false);
    }
  }

  if (!live)
    return (
      <div className="mx-auto w-full max-w-4xl px-4 py-10">
        <PilotOffer />
        <h2 className="text-2xl font-semibold tracking-tight">Vyzkoušejte veřejné demo</h2>
        <p className="mt-2 text-muted">
          Ukázka vytěžování dat z faktur, smluv a pojistek. Ke každé
          hodnotě ukáže, kde v PDF je, a zkontroluje ji pravidly (IČO, součty,
          pořadí dat). Vyber dokument níže: uvidíš skutečný výstup modelu
          qwen3.6 běžícího lokálně přes Ollamu. Klikni na pole a v PDF se
          zvýrazní citace.
        </p>
        <p className="mt-3 text-sm">
          <a
            href={REPO_URL}
            className="font-medium text-accent hover:underline"
          >
            Zdrojový kód a výsledky evaluace na GitHubu →
          </a>
        </p>
        <div className="mt-10">
          <DemoPicker />
        </div>
      </div>
    );

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Dokumenty</h1>
      <p className="mt-1 text-muted">
        Nahraj PDF, vyber šablonu a Docflow vytáhne data. Ke každé hodnotě
        ukáže, kde v dokumentu je, a zkontroluje ji podle pravidel.
      </p>

      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          upload(e.dataTransfer.files[0]);
        }}
        disabled={uploading}
        className={`mt-6 flex w-full flex-col items-center gap-1 rounded-lg border-2 border-dashed px-6 py-10 transition ${
          dragging
            ? "border-accent bg-accent-soft"
            : "border-line bg-surface hover:border-accent"
        }`}
      >
        <span className="font-medium">
          {uploading ? "Nahrávám…" : "Přetáhni PDF sem nebo klikni pro výběr"}
        </span>
        <span className="text-sm text-muted">
          PDF s textem nebo sken, max. 20 MB
        </span>
      </button>
      <input
        ref={input}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => upload(e.target.files?.[0])}
      />

      {error && (
        <p className="mt-4 rounded-md bg-err-soft px-3 py-2 text-sm text-err">
          {error}
        </p>
      )}

      {documents && documents.length > 0 && (
        <DocumentList documents={documents} onDelete={(id) => setDocuments((prev) => prev?.filter((d) => d.id !== id) ?? [])} />
      )}

      <div className="mt-10">
        <DemoPicker />
      </div>


    </div>
  );
}
