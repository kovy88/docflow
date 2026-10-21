import type { Template } from "@/lib/api";

/** Preselect a template from the file name ("faktura_…", "contract_03.pdf"). */
export function guessTemplate(filename: string, templates: Template[]): string {
  const name = filename.toLowerCase();
  const hints: Record<string, string[]> = {
    invoice: ["invoice", "faktur"],
    lease: ["lease", "nájem", "najem"],
    contract: ["contract", "smlouv"],
    insurance_policy: ["policy", "pojist"],
  };
  const hit = Object.entries(hints).find(([, words]) =>
    words.some((w) => name.includes(w)),
  );
  return hit && templates.some((t) => t.id === hit[0]) ? hit[0] : templates[0]?.id ?? "invoice";
}

