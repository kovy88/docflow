import { getAccessCode, isLive } from "@/lib/access";
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
  /** "email" when the PDF came through e-mail intake. */
  source?: "email" | null;
  sender?: string | null;
  /** When the document was uploaded or received (UTC, see parseTimestamp). */
  created_at?: string;
  latest_extraction?: {
    id: number;
    status: Extraction["status"];
    template_id: string;
    reviewed_at: string | null;
  } | null;
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
    phase?: "queued" | "processing" | "completed" | "failed";
    started_at?: string;
    queue_wait_s?: number;
    input_tokens?: number;
    output_tokens?: number;
    attempts?: number;
    mode?: string;
    latency_s?: number;
    review_s?: number;
  };
  created_at: string;
  reviewed_at: string | null;
};

export type ReviewRow = {
  document_id: number;
  filename: string;
  extraction_id: number;
  template_id: string;
  pages: number;
  ocr: boolean;
  fields_total: number;
  fields_corrected: number;
  review_s: number | null;
  latency_s: number | null;
  reviewed_at: string;
};

export type ReviewReport = {
  summary: {
    documents: number;
    fields: number;
    fields_corrected: number;
    accuracy_before_review: number | null;
    documents_timed: number;
    review_s_median: number | null;
    review_s_total: number | null;
  };
  rows: ReviewRow[];
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

export type AccessInfo = {
  ok: boolean;
  workspace: string;
  llm_calls_left_today: number;
  /** Where the firm can send PDFs by e-mail; null when intake is off. */
  intake_address: string | null;
};

function authHeaders(): Record<string, string> {
  const code = getAccessCode();
  return code ? { "X-Access-Code": code } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  });
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
    const translations: Record<string, string> = {
      "Document not found": "Dokument nebyl nalezen. Možná byl smazán.",
      "Template not found": "Šablona nebyla nalezena. Možná byla smazána.",
      "Extraction not found": "Výsledek extrakce nebyl nalezen.",
    };
    // Upload errors (size, not a PDF, unreadable) already come in Czech from the backend.
    const friendly = translations[detail ?? ""] ?? detail;
    throw new Error(friendly || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

// Excel works for any template; POHODA and Money S3 import received invoices only.
export type ExportFormat = "xlsx" | "pohoda" | "money";

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

const liveApi = {
  deleteDocument: async (id: number) => {
    const res = await fetch(`${API_URL}/documents/${id}`, { method: "DELETE", headers: authHeaders() });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail || "Dokument se nepodařilo smazat.");
    }
  },
  exportBatch: async (extractionIds: number[], format: ExportFormat = "xlsx") => {
    const path = format === "xlsx" ? "/exports/xlsx" : "/exports/accounting";
    const body = format === "xlsx" ? { extraction_ids: extractionIds } : { extraction_ids: extractionIds, format };
    const res = await fetch(`${API_URL}${path}`, {
      ...json("POST", body),
      headers: { ...authHeaders(), "content-type": "application/json" },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(typeof body?.detail === "string" ? body.detail : "Export se nepodařil.");
    }
    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = format === "xlsx" ? "docflow-evidence.xlsx" : `docflow-${format}.xml`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
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
      headers: authHeaders(),
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
  extractBatch: (documentIds: number[], templateId: string) =>
    request<Extraction[]>(
      "/extractions/batch",
      json("POST", { document_ids: documentIds, template_id: templateId }),
    ),
  extraction: (extractionId: number) =>
    request<Extraction>(`/extractions/${extractionId}`),
  review: (
    extractionId: number,
    values: Record<string, string | null>,
    reviewSeconds?: number,
  ) =>
    request<Extraction>(
      `/extractions/${extractionId}`,
      json("PATCH", { values, review_seconds: reviewSeconds }),
    ),
  reviewReport: (templateId?: string) =>
    request<ReviewReport>(
      `/reports/review${templateId ? `?template_id=${encodeURIComponent(templateId)}` : ""}`,
    ),
  downloadReviewCsv: async (templateId?: string) => {
    const query = new URLSearchParams({ format: "csv" });
    if (templateId) query.set("template_id", templateId);
    const res = await fetch(`${API_URL}/reports/review?${query}`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await res.blob());
    link.download = "docflow-mereni.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  },
  /** Source for react-pdf: the file endpoint needs the access header too. */
  fileSource: (documentId: number): PdfSource => ({
    url: `${API_URL}/documents/${documentId}/file`,
    httpHeaders: authHeaders(),
  }),
  download: async (extractionId: number, format: "json" | "csv") => {
    const res = await fetch(
      `${API_URL}/extractions/${extractionId}/export?format=${format}`,
      { headers: authHeaders() },
    );
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await res.blob());
    link.download = `extraction-${extractionId}.${format}`;
    link.click();
    URL.revokeObjectURL(link.href);
  },
  demo: () => request<DemoDocument[]>("/demo"),
  createDemo: (file: string) =>
    request<DocumentInfo>(`/demo/${encodeURIComponent(file)}`, {
      method: "POST",
    }),
  evals: () => request<EvalRun[]>("/evals"),
  /** Validates an access code; resolves with its workspace (firm) and today's remaining
   *  model calls. */
  checkAccess: (code: string) =>
    request<AccessInfo>("/access", {
      headers: { "X-Access-Code": code },
    }),
  /** The current workspace, e.g. its e-mail address for sending PDFs. */
  access: () => request<AccessInfo>("/access"),
};

export type Api = typeof liveApi;
export type PdfSource =
  string | { url: string; httpHeaders: Record<string, string> };

export const REPO_URL = "https://github.com/kovy88/docflow";

/** Always the live backend: used to validate a code before switching to live mode. */
export const checkAccess = liveApi.checkAccess;

/** Each call goes to the live backend or to the precomputed demo, decided per call so that
 *  unlocking with an access code switches the whole app without a rebuild. */
export const api = Object.fromEntries(
  Object.keys(liveApi).map((key) => [
    key,
    (...args: unknown[]) =>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ((isLive() ? liveApi : demoApi)[key as keyof Api] as any)(...args),
  ]),
) as Api;

/** Backend timestamps are UTC; SQLite drops the offset, Postgres keeps it. */
export const parseTimestamp = (s: string) =>
  new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(s) ? s : `${s}Z`);

/** Czech plural: plural(3, "pole", "pole", "polí") → "3 pole". */
export const plural = (n: number, one: string, few: string, many: string) =>
  `${n} ${n === 1 ? one : n >= 2 && n <= 4 ? few : many}`;
