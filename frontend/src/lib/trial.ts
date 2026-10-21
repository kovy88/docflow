import { API_URL, type ExtractedField, type Extraction, type Issue } from "@/lib/api";

/** Public trial (/vyzkouset): talks to the live backend without the access code. */

export type RuleResult = {
  type: "ico" | "sum" | "date_order";
  description: string;
  fields: string[];
  result: "passed" | "failed" | "skipped";
};

export type TrialResult = {
  id: string;
  document_id: string;
  template_id: string;
  status: Extraction["status"];
  model: string;
  created_at: string;
  latency_s: number | null;
  fields: ExtractedField[];
  issues: Issue[];
  rules: RuleResult[];
  filename: string;
  page_count: number;
  expires_at: string;
};

export const TRIAL_TYPES = [
  {
    id: "invoice",
    label: "Faktura",
    hint: "dodavatel, IČO, částky, DPH, splatnost",
    sample: "/vzorky/faktura-fiktivni.pdf",
  },
  {
    id: "transport_order",
    label: "Objednávka přepravy / CMR",
    hint: "nakládka, vykládka, zboží, váha, SPZ, cena",
    sample: "/vzorky/objednavka-prepravy-fiktivni.pdf",
  },
  {
    id: "lease",
    label: "Nájemní smlouva",
    hint: "strany, předmět nájmu, nájemné, doba",
    sample: "/vzorky/najemni-smlouva-fiktivni.pdf",
  },
] as const;

export const TRIAL_MAX_MB = 10;
export const TRIAL_MAX_PAGES = 10;
export const TRIAL_PER_IP = 3; // backend TRIAL_PER_IP

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new Error("Server se právě probouzí (free hosting). Zkuste to prosím za minutu znovu.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string" ? body.detail : `Chyba ${res.status}, zkuste to znovu.`,
    );
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const trialApi = {
  wake: () => fetch(`${API_URL}/health`).catch(() => undefined),
  create: (file: File, templateId: string, ref: string | null) => {
    const form = new FormData();
    form.append("file", file);
    form.append("template_id", templateId);
    if (ref) form.append("ref", ref);
    return call<TrialResult>("/trial", { method: "POST", body: form });
  },
  get: (id: string) => call<TrialResult>(`/trial/${encodeURIComponent(id)}`),
  remove: (id: string) => call<void>(`/trial/${encodeURIComponent(id)}`, { method: "DELETE" }),
  exportUrl: (id: string, format: "xlsx" | "json") =>
    `${API_URL}/trial/${encodeURIComponent(id)}/export?format=${format}`,
};
