"use client";

import Link from "next/link";
import { useState } from "react";
import { TemplateEditor } from "@/components/TemplateEditor";
import type { Template } from "@/lib/api";
import { useLive } from "@/lib/access";

const EMPTY: Template = {
  id: "",
  name: "",
  description: "",
  fields: [
    { key: "", label: "", type: "string", description: "", required: true },
  ],
  rules: [],
};

/** A draft may come from "Navrhnout šablonu" (AI) or "Vytvořit kopii" via sessionStorage. */
function takeDraft(): { template: Template; fromAi: boolean } {
  try {
    const raw = sessionStorage.getItem("docflow:template-draft");
    sessionStorage.removeItem("docflow:template-draft");
    if (raw) {
      const draft = JSON.parse(raw) as Template & { fromAi?: boolean };
      return {
        template: { ...EMPTY, ...draft },
        fromAi: Boolean(draft.fromAi),
      };
    }
  } catch {
    /* storage unavailable */
  }
  return { template: EMPTY, fromAi: false };
}

export function NewTemplate() {
  const live = useLive();
  const [{ template, fromAi }] = useState(takeDraft);
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10">
      <Link
        href="/templates"
        className="text-sm text-muted hover:text-foreground"
      >
        ← Šablony
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">
        Nová šablona
      </h1>
      {fromAi && (
        <p className="mt-2 rounded-md bg-accent-soft px-3 py-2 text-sm text-accent">
          Návrh připravil model podle nahraného dokumentu. Zkontroluj pole,
          případně přidej pravidla a ulož.
        </p>
      )}
      <div className="mt-6">
        {live ? <TemplateEditor initial={template} mode="new" /> : <p className="text-muted">Vytváření šablon je dostupné po zadání přístupového kódu. V demu si můžeš prohlédnout hotové šablony.</p>}
      </div>
    </div>
  );
}
