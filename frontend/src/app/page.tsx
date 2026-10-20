"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { BatchPanel } from "@/components/BatchPanel";
import { DocumentList } from "@/components/DocumentList";
import { DataAndTech, Hero, PilotOffer, Steps } from "@/components/PilotOffer";
import { DemoPicker } from "@/components/DemoPicker";
import { useLive } from "@/lib/access";
import { api, type DocumentInfo } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const live = useLive();
  const input = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<DocumentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  // Documents of the last multi-file upload, waiting for a template and a batch run.
  const [batch, setBatch] = useState<DocumentInfo[]>([]);
  const [failures, setFailures] = useState<string[]>([]);

  useEffect(() => {
    api
      .documents()
      .then(setDocuments)
      .catch((e: Error) => setError(`Backend není dostupný: ${e.message}`));
  }, []);

  // While extractions run in the background, refresh the list so statuses update.
  const running = documents?.some((d) => d.latest_extraction?.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setTimeout(
      () => api.documents().then(setDocuments).catch(() => {}),
      5000,
    );
    return () => clearTimeout(timer);
  }, [documents, running]);

  // The page stays alive (cacheComponents) while a document is open; reset on the way out.
  useEffect(() => () => setUploading(false), []);

  async function upload(list: FileList | null | undefined) {
    const files = Array.from(list ?? []);
    if (files.length === 0) return;
    setUploading(true);
    setError(null);
    setFailures([]);
    if (files.length === 1) {
      try {
        const doc = await api.upload(files[0]);
        router.push(`/documents/${doc.id}`);
      } catch (e) {
        setError((e as Error).message);
        setUploading(false);
      }
      return;
    }
    // One by one: OCR of scans is heavy, and the order of the list stays predictable.
    const uploaded: DocumentInfo[] = [];
    const failed: string[] = [];
    for (const [i, file] of files.entries()) {
      setProgress(`Nahrávám ${i + 1}/${files.length}: ${file.name}`);
      try {
        uploaded.push(await api.upload(file));
      } catch (e) {
        failed.push(`${file.name}: ${(e as Error).message}`);
      }
    }
    setProgress(null);
    setUploading(false);
    setFailures(failed);
    setBatch(uploaded);
    setDocuments((prev) => [...uploaded.toReversed(), ...(prev ?? [])]);
  }

  if (!live)
    return (
      <div className="mx-auto grid w-full max-w-6xl gap-20 px-4 py-12 md:py-16">
        <Hero />
        <div id="demo" className="scroll-mt-6 rounded-lg border border-line bg-surface p-6">
          <DemoPicker />
        </div>
        <Steps />
        <PilotOffer />
        <DataAndTech />
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
          upload(e.dataTransfer.files);
        }}
        disabled={uploading}
        className={`mt-6 flex w-full flex-col items-center gap-1 rounded-lg border-2 border-dashed px-6 py-10 transition ${
          dragging
            ? "border-accent bg-accent-soft"
            : "border-line bg-surface hover:border-accent"
        }`}
      >
        <span className="font-medium">
          {uploading
            ? (progress ?? "Nahrávám…")
            : "Přetáhni PDF sem nebo klikni pro výběr"}
        </span>
        <span className="text-sm text-muted">
          PDF s textem nebo sken, max. 20 MB. Víc souborů najednou zpracuješ dávkově.
        </span>
      </button>
      <input
        ref={input}
        type="file"
        accept="application/pdf"
        multiple
        className="hidden"
        onChange={(e) => {
          upload(e.target.files);
          e.target.value = ""; // allow picking the same files again
        }}
      />

      {failures.length > 0 && (
        <div role="alert" className="mt-4 rounded-md border-l-[3px] border-err bg-err-soft px-3 py-2 text-sm text-err">
          <p className="font-medium">Tyto soubory se nenahrály:</p>
          <ul className="mt-1 list-disc pl-5">
            {failures.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
      )}

      {batch.length > 0 && (
        <BatchPanel
          documents={batch}
          onStarted={(extractions) => {
            const byDoc = new Map(extractions.map((e) => [e.document_id, e]));
            setDocuments((prev) =>
              prev?.map((d) => {
                const e = byDoc.get(d.id);
                return e
                  ? { ...d, latest_extraction: { id: e.id, status: e.status, template_id: e.template_id, reviewed_at: null } }
                  : d;
              }) ?? null,
            );
            setBatch([]);
          }}
          onCancel={() => setBatch([])}
        />
      )}

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
