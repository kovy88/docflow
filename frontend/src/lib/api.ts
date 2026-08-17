/**
 * Typed API client.
 *
 * Deliberately thin — no data-fetching library. The app has one consumer per
 * endpoint and a handful of screens; TanStack Query would add a cache layer,
 * a devtools dependency and its own mental model to solve a problem this size
 * does not have. If the app grew shared cross-screen state, that calculus flips.
 *
 * Two things this client does own: a single place that attaches credentials, and
 * a single place that turns the API's error envelope into a typed `ApiError`. Both
 * are the kind of thing that goes wrong when it is spread across components.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const TOKEN_KEY = "docflow.access_token";
const REFRESH_KEY = "docflow.refresh_token";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly detail: Record<string, unknown> = {},
    readonly requestId = "",
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** True when the caller should be sent back to sign in. */
  get isAuthError() {
    return this.status === 401 || this.code === "token_expired";
  }
}

export const tokens = {
  get access() {
    return typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
  },
  get refresh() {
    return typeof window === "undefined" ? null : localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh: string) {
    localStorage.setItem(TOKEN_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

type RequestOptions = {
  method?: string;
  body?: unknown;
  formData?: FormData;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Set false for endpoints that must not trigger a token refresh loop. */
  retryOnAuthFailure?: boolean;
};

async function raw(path: string, options: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { ...options.headers };
  const token = tokens.access;
  if (token) headers.Authorization = `Bearer ${token}`;
  // Let the browser set the multipart boundary; setting it by hand breaks uploads.
  if (options.body !== undefined && !options.formData) {
    headers["Content-Type"] = "application/json";
  }

  return fetch(`${API_URL}${path}`, {
    method: options.method ?? (options.body || options.formData ? "POST" : "GET"),
    headers,
    body: options.formData ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    signal: options.signal,
  });
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let response = await raw(path, options);

  // One transparent refresh attempt on 401. Bounded to a single retry so an
  // expired refresh token cannot produce an infinite loop of refresh calls.
  if (response.status === 401 && options.retryOnAuthFailure !== false && tokens.refresh) {
    const refreshed = await tryRefresh();
    if (refreshed) response = await raw(path, options);
  }

  if (response.status === 204) return undefined as T;

  const isJson = response.headers.get("content-type")?.includes("application/json");
  const payload = isJson ? await response.json().catch(() => null) : await response.text();

  if (!response.ok) {
    const envelope = (payload as { error?: Record<string, unknown>; request_id?: string })?.error;
    throw new ApiError(
      response.status,
      (envelope?.code as string) ?? `http_${response.status}`,
      (envelope?.message as string) ?? "Something went wrong",
      (envelope?.detail as Record<string, unknown>) ?? {},
      ((payload as { request_id?: string })?.request_id as string) ?? "",
    );
  }

  return payload as T;
}

async function tryRefresh(): Promise<boolean> {
  const refresh = tokens.refresh;
  if (!refresh) return false;
  try {
    const response = await fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!response.ok) {
      tokens.clear();
      return false;
    }
    const data = (await response.json()) as TokenResponse;
    tokens.set(data.access_token, data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ types */

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: { id: string; email: string; full_name: string | null };
  organization: {
    id: string;
    name: string;
    slug: string;
    plan: string;
    monthly_document_quota: number;
    role: string | null;
  };
};

export type DocumentStatus =
  | "uploaded"
  | "queued"
  | "processing"
  | "needs_review"
  | "completed"
  | "rejected"
  | "failed";

export type DocumentSummary = {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: DocumentStatus;
  document_type_key: string | null;
  page_count: number | null;
  used_ocr: boolean;
  processing_ms: number | null;
  created_at: string;
  error_code: string | null;
};

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type ExtractionField = {
  field_path: string;
  label: string | null;
  value: unknown;
  confidence: number | null;
  confidence_band: "high" | "medium" | "low" | null;
  source: string;
  is_required: boolean;
  needs_review: boolean;
  was_corrected: boolean;
  evidence_text: string | null;
  reasons: string[];
};

export type ValidationIssue = {
  rule_id: string;
  field_path: string | null;
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
};

export type Extraction = {
  id: string;
  document_id: string;
  status: string;
  revision: number;
  document_type_key: string;
  schema_version: number;
  data: Record<string, unknown>;
  fields: ExtractionField[];
  issues: ValidationIssue[];
  overall_confidence: number | null;
  needs_review: boolean;
  review_reasons: string[];
  created_at: string;
  provider: string | null;
  model: string | null;
  model_version: string | null;
  prompt_key: string | null;
  prompt_version: string | null;
  extractor: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number | null;
};

export type ProcessingStep = {
  stage: string;
  status: "running" | "succeeded" | "failed" | "skipped";
  sequence: number;
  duration_ms: number | null;
  started_at: string;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  detail: Record<string, unknown>;
};

export type Dashboard = {
  total_documents: number;
  by_status: Record<string, number>;
  needs_review: number;
  processed_this_period: number;
  quota: number;
  quota_used: number;
  success_rate: number | null;
  avg_processing_ms: number | null;
  review_rate: number | null;
  cost_usd_this_period: number;
  cost_per_document: number | null;
  pricing_as_of: string;
  daily: { day: string; events: number; cost_usd: number }[];
};

export type DocumentTypeInfo = {
  key: string;
  name: string;
  description: string;
  version: number;
  field_count: number;
  required_fields: string[];
  critical_fields: string[];
  review_threshold: number;
  rules: string[];
};

export type ApiKeyInfo = {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  api_key?: string;
};

/* ---------------------------------------------------------------- endpoints */

const v1 = (path: string) => `/api/v1${path}`;

export const auth = {
  register: (body: {
    email: string;
    password: string;
    full_name?: string;
    organization_name: string;
  }) => api<TokenResponse>(v1("/auth/register"), { body }),
  login: (body: { email: string; password: string }) =>
    api<TokenResponse>(v1("/auth/login"), { body }),
  session: () =>
    api<{
      user: TokenResponse["user"];
      organization: TokenResponse["organization"];
      role: string;
    }>(v1("/auth/session")),
  logout: (refreshToken: string) =>
    api<void>(v1("/auth/logout"), {
      body: { refresh_token: refreshToken },
      // A logout call must not itself trigger a token-refresh retry loop — the
      // whole point is to end the session, not extend it.
      retryOnAuthFailure: false,
    }),
};

export const documents = {
  list: (params: { limit?: number; offset?: number; status?: string; search?: string } = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    const suffix = query.toString() ? `?${query}` : "";
    return api<Page<DocumentSummary>>(v1(`/documents${suffix}`));
  },
  get: (id: string) => api<DocumentSummary>(v1(`/documents/${id}`)),
  upload: (file: File, documentType?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (documentType) form.append("document_type", documentType);
    return api<{
      document_id: string;
      job_id: string;
      status: string;
      duplicate: boolean;
      message: string | null;
    }>(v1("/documents"), { formData: form });
  },
  status: (id: string) =>
    api<{ status: DocumentStatus; job_status: string | null; error_message: string | null }>(
      v1(`/documents/${id}/status`),
    ),
  timeline: (id: string) => api<ProcessingStep[]>(v1(`/documents/${id}/timeline`)),
  extraction: (id: string) => api<Extraction>(v1(`/documents/${id}/extraction`)),
  reprocess: (id: string) => api<{ job_id: string }>(v1(`/documents/${id}/reprocess`), { method: "POST" }),
  edit: (id: string, edits: { field_path: string; value: unknown }[], note?: string) =>
    api<{ corrections_applied: number; remaining_errors: number; needs_review: boolean }>(
      v1(`/documents/${id}/extraction`),
      { method: "PATCH", body: { edits, note } },
    ),
  approve: (id: string, body: { note?: string; force?: boolean; duration_seconds?: number } = {}) =>
    api<{ status: string }>(v1(`/documents/${id}/approve`), { method: "POST", body }),
  reject: (id: string, reason: string) =>
    api<{ status: string }>(v1(`/documents/${id}/reject`), { method: "POST", body: { reason } }),
  remove: (id: string) => api<void>(v1(`/documents/${id}`), { method: "DELETE" }),
};

export const analytics = {
  dashboard: () => api<Dashboard>(v1("/dashboard")),
  documentTypes: () => api<DocumentTypeInfo[]>(v1("/document-types")),
  corrections: () =>
    api<{ document_type_key: string; field_path: string; corrections: number }[]>(
      v1("/analytics/corrections"),
    ),
  exportUrl: (format: "csv" | "json") => `${API_URL}${v1(`/export?format=${format}`)}`,
};

export const settings = {
  apiKeys: () => api<ApiKeyInfo[]>(v1("/settings/api-keys")),
  createApiKey: (name: string) => api<ApiKeyInfo>(v1("/settings/api-keys"), { body: { name } }),
  revokeApiKey: (id: string) => api<void>(v1(`/settings/api-keys/${id}`), { method: "DELETE" }),
};
