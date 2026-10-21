"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { TemplateEditor } from "@/components/TemplateEditor";
import { api, type Template } from "@/lib/api";
import { useLive } from "@/lib/access";

export function TemplateDetail({ id }: { id: string }) {
  const live = useLive();
  const [template, setTemplate] = useState<Template | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .template(id)
      .then(setTemplate)
      .catch((e: Error) => setError(e.message));
  }, [id]);

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10">
      <Link
        href="/templates"
        className="text-sm text-muted hover:text-foreground"
      >
        ← Šablony
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">
        {template?.name ?? id}
      </h1>
      {error && (
        <p className="mt-4 rounded-md bg-err-soft px-3 py-2 text-err">
          {error}
        </p>
      )}
      {template && (
        <div className="mt-6">
          <TemplateEditor
            key={template.id}
            initial={template}
            mode={!live || template.builtin ? "view" : "edit"}
          />
        </div>
      )}
    </div>
  );
}
