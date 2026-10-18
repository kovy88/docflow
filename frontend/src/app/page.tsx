"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { DemoPicker } from "@/components/DemoPicker";
import { api, type DocumentInfo } from "@/lib/api";

export default function Home() {
  const router = useRouter();
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

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10">
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
          PDF s textovou vrstvou, max. 20 MB
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

      <ul className="mt-8 divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">
        {documents === null && !error && (
          <li className="px-4 py-3 text-muted">Načítám…</li>
        )}
        {documents?.length === 0 && (
          <li className="px-4 py-3 text-muted">Zatím žádné dokumenty.</li>
        )}
        {documents?.map((doc) => (
          <li key={doc.id}>
            <Link
              href={`/documents/${doc.id}`}
              className="flex items-center justify-between px-4 py-3 hover:bg-background"
            >
              <span className="font-medium">{doc.filename}</span>
              <span className="text-sm text-muted">
                {doc.page_count} {doc.page_count === 1 ? "strana" : "stran"}
                {!doc.has_text && " · bez textu"}
              </span>
            </Link>
          </li>
        ))}
      </ul>

      <DemoPicker />
    </div>
  );
}
