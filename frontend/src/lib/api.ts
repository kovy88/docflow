export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type DocumentInfo = {
  id: number;
  filename: string;
  page_count: number;
  has_text: boolean;
};

export type TemplateField = {
  key: string;
  label: string;
  type: "string" | "number" | "date";
  description: string;
  required: boolean;
};

export type Template = {
  id: string;
  name: string;
  description: string;
  fields: TemplateField[];
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
  status: "ok" | "needs_review" | "approved" | "failed";
  fields: ExtractedField[];
  issues: Issue[];
  usage: {
    input_tokens?: number;
    output_tokens?: number;
    attempts?: number;
    mode?: string;
  };
  created_at: string;
  reviewed_at: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail =
      typeof body?.detail === "string"
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

export const api = {
  health: () => request<{ provider: string; model: string }>("/health"),
  templates: () => request<Template[]>("/templates"),
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
  review: (extractionId: number, values: Record<string, string | null>) =>
    request<Extraction>(
      `/extractions/${extractionId}`,
      json("PATCH", { values }),
    ),
  fileUrl: (documentId: number) => `${API_URL}/documents/${documentId}/file`,
};

/** Backend timestamps are UTC; SQLite drops the offset, Postgres keeps it. */
export const parseTimestamp = (s: string) =>
  new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(s) ? s : `${s}Z`);
