import { demoApi } from "@/lib/demo";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type DocumentInfo = {
  id: number;
  filename: string;
  page_count: number;
  has_text: boolean;
  /** 1-based pages whose text was read by OCR (scans). */
  ocr_pages: number[];
};

export type TemplateField = {
  key: string;
  label: string;
  type: "string" | "number" | "date";
  description: string;
  required: boolean;
};

export type Rule =
  | { type: "ico"; field: string }
  | { type: "sum"; fields: string[]; equals: string; tolerance?: number }
  | { type: "date_order"; earlier: string; later: string };

export type Template = {
  id: string;
  name: string;
  description: string;
  fields: TemplateField[];
  rules: Rule[];
  builtin?: boolean;
};

export type ExtractedField = {
  key: string;
  label: string;
  type: TemplateField["type"];
  raw: string | null;
  value: string | number | null;
  page: number | null;
  quote: string | null;
  confidence: number;
  grounded: boolean;
  corrected?: boolean;
};

export type Issue = {
  severity: "error" | "warning";
  code: string;
  message: string;
  fields: string[];
};

export type Extraction = {
  id: number;
  document_id: number;
  template_id: string;
  model: string;
  status: "ok" | "needs_review" | "approved" | "failed" | "running";
  fields: ExtractedField[];
  issues: Issue[];
  usage: {
    input_tokens?: number;
    output_tokens?: number;
    attempts?: number;
    mode?: string;
    latency_s?: number;
  };
  created_at: string;
  reviewed_at: string | null;
};

export type DemoDocument = {
  file: string;
  template_id: string;
  description: string;
};

export type EvalSummary = {
  documents: number;
  failed_documents: number;
  fields: number;
  field_accuracy: number | null;
  documents_fully_correct: number;
  per_template: Record<string, number | null>;
  per_field: Record<string, number | null>;
  per_difficulty?: Record<string, number | null>;
  rule_detection: {
    tp: number;
    fp: number;
    fn: number;
    precision: number | null;
    recall: number | null;
  };
  signals: Record<
    | "grounded_share"
    | "accuracy_grounded"
    | "accuracy_ungrounded"
    | "accuracy_high_confidence"
    | "accuracy_low_confidence",
    number | null
  >;
  latency_s: { mean: number | null; median: number | null; max: number | null };
  tokens_per_document: number | null;
  errors: {
    file: string;
    key: string;
    expected: unknown;
    predicted: unknown;
    raw: string | null;
  }[];
};

export type EvalRun = {
  id: string;
  model: string;
  prompt_version: number;
  provider: string;
  started: string;
  finished: string;
  summary: EvalSummary;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    // FastAPI validation errors come as a list of {loc, msg}.
    const detail = Array.isArray(body?.detail)
      ? body.detail
          .map((d: { loc?: string[]; msg: string }) =>
            [d.loc?.slice(1).join("."), d.msg.replace(/^Value error, /, "")]
              .filter(Boolean)
              .join(": "),
          )
          .join("; ")
      : typeof body?.detail === "string"
        ? body.detail
        : JSON.stringify(body?.detail);
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

const liveApi = {
  health: () => request<{ provider: string; model: string }>("/health"),
  templates: () => request<Template[]>("/templates"),
  template: (id: string) =>
    request<Template>(`/templates/${encodeURIComponent(id)}`),
  createTemplate: (t: Template) =>
    request<Template>("/templates", json("POST", t)),
  updateTemplate: (t: Template) =>
    request<Template>(`/templates/${encodeURIComponent(t.id)}`, json("PUT", t)),
  deleteTemplate: (id: string) =>
    fetch(`${API_URL}/templates/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    }),
  suggestTemplate: (documentId: number) =>
    request<Template>(`/documents/${documentId}/template-suggestion`, {
      method: "POST",
    }),
  documents: () => request<DocumentInfo[]>("/documents"),
  document: (id: number) => request<DocumentInfo>(`/documents/${id}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentInfo>("/documents", { method: "POST", body: form });
  },
  extractions: (documentId: number) =>
    request<Extraction[]>(`/documents/${documentId}/extractions`),
  extract: (documentId: number, templateId: string) =>
    request<Extraction>(
      `/documents/${documentId}/extractions`,
      json("POST", { template_id: templateId }),
    ),
  extraction: (extractionId: number) =>
    request<Extraction>(`/extractions/${extractionId}`),
  review: (extractionId: number, values: Record<string, string | null>) =>
    request<Extraction>(
      `/extractions/${extractionId}`,
      json("PATCH", { values }),
    ),
  fileUrl: (documentId: number) => `${API_URL}/documents/${documentId}/file`,
  exportUrl: (extractionId: number, format: "json" | "csv") =>
    `${API_URL}/extractions/${extractionId}/export?format=${format}`,
  demo: () => request<DemoDocument[]>("/demo"),
  createDemo: (file: string) =>
    request<DocumentInfo>(`/demo/${encodeURIComponent(file)}`, {
      method: "POST",
    }),
  evals: () => request<EvalRun[]>("/evals"),
};

export type Api = typeof liveApi;

/** Public read-only demo (static hosting, no backend): NEXT_PUBLIC_DEMO=1. */
export const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";
export const REPO_URL = "https://github.com/kovy88/docflow";

export const api: Api = DEMO ? demoApi : liveApi;

/** Backend timestamps are UTC; SQLite drops the offset, Postgres keeps it. */
export const parseTimestamp = (s: string) =>
  new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(s) ? s : `${s}Z`);

/** Czech plural: plural(3, "pole", "pole", "polí") → "3 pole". */
export const plural = (n: number, one: string, few: string, many: string) =>
  `${n} ${n === 1 ? one : n >= 2 && n <= 4 ? few : many}`;
