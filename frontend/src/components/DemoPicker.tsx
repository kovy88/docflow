"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type DemoDocument } from "@/lib/api";

const GROUPS: { id: string; label: string }[] = [
  { id: "invoice", label: "Faktury" },
  { id: "contract", label: "Smlouvy" },
  { id: "insurance_policy", label: "Pojistné smlouvy" },
];

/** Fictional documents from the evaluation set, so the app can be tried without own PDFs. */
export function DemoPicker() {
  const router = useRouter();
  const [demo, setDemo] = useState<DemoDocument[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .demo()
      .then(setDemo)
      .catch(() => setDemo([]));
  }, []);

  async function open(file: string) {
    setBusy(file);
    setError(null);
    try {
      const doc = await api.createDemo(file);
      router.push(`/documents/${doc.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  if (demo.length === 0) return null;

  return (
    <section className="mt-10">
      <h2 className="text-lg font-semibold tracking-tight">
        Vyzkoušet na ukázce
      </h2>
      <p className="mt-1 text-sm text-muted">
        Fiktivní dokumenty z evaluační sady. Ty označené červeně obsahují
        schválně vložené chyby, které má Docflow najít.
      </p>
      {error && (
        <p className="mt-3 rounded-md bg-err-soft px-3 py-2 text-sm text-err">
          {error}
        </p>
      )}
      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        {GROUPS.map((group) => (
          <div key={group.id}>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
              {group.label}
            </h3>
            <ul className="space-y-1">
              {demo
                .filter((d) => d.template_id === group.id)
                .map((d) => {
                  const planted = d.description.includes("vložené chyby");
                  return (
                    <li key={d.file}>
                      <button
                        type="button"
                        onClick={() => open(d.file)}
                        disabled={busy !== null}
                        title={d.description}
                        className="flex w-full items-center gap-2 rounded-md border border-line bg-surface px-2.5 py-1.5 text-left text-sm hover:border-accent disabled:opacity-50"
                      >
                        <span
                          className={`h-2 w-2 shrink-0 rounded-full ${planted ? "bg-err" : "bg-ok"}`}
                          aria-label={
                            planted ? "obsahuje vložené chyby" : "bez chyb"
                          }
                        />
                        <span className="truncate">
                          {busy === d.file
                            ? "Otevírám…"
                            : d.file.replace(".pdf", "")}
                        </span>
                      </button>
                    </li>
                  );
                })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
