"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, plural, type Template } from "@/lib/api";

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .templates()
      .then(setTemplates)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Šablony</h1>
          <p className="mt-1 max-w-2xl text-muted">
            Šablona říká, co z dokumentu vytáhnout a jak to zkontrolovat. Je to
            jediná část Docflow závislá na oboru. Novou šablonu můžeš vytvořit
            ručně, nebo ji nechat navrhnout modelem přímo z nahraného dokumentu.
          </p>
        </div>
        <Link
          href="/templates/new"
          className="rounded-md bg-accent px-3 py-1.5 font-medium text-white hover:opacity-90"
        >
          Nová šablona
        </Link>
      </div>
      {error && (
        <p className="mt-4 rounded-md bg-err-soft px-3 py-2 text-err">
          {error}
        </p>
      )}
      <ul className="mt-6 grid gap-3 sm:grid-cols-2">
        {templates?.map((t) => (
          <li key={t.id}>
            <Link
              href={`/templates/${t.id}`}
              className="block h-full rounded-lg border border-line bg-surface p-4 hover:border-accent"
            >
              <div className="flex items-center gap-2">
                <span className="font-semibold">{t.name}</span>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${t.builtin ? "bg-background text-muted" : "bg-accent-soft text-accent"}`}
                >
                  {t.builtin ? "vestavěná" : "vlastní"}
                </span>
              </div>
              {t.description && (
                <p className="mt-1 text-sm text-muted">{t.description}</p>
              )}
              <p className="mt-2 text-xs text-muted">
                {plural(t.fields.length, "pole", "pole", "polí")} ·{" "}
                {plural(t.rules.length, "pravidlo", "pravidla", "pravidel")} ·{" "}
                <span className="font-mono">{t.id}</span>
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
