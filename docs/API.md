# API reference

This is a curated tour, not the full schema — the authoritative, always-current
reference is the live OpenAPI docs at `/docs` (Swagger UI) or `/openapi.json`,
generated directly from the route definitions so it can't drift from the code
the way a hand-maintained field-by-field doc would.

Base URL in local development: `http://localhost:8000`. All routes below are
under `/api/v1` except health/metrics, which deliberately sit outside it
(below).

## Authentication

Two methods, both presented the same way: `Authorization: Bearer <token>`.

- **JWT** — `POST /api/v1/auth/register` or `POST /api/v1/auth/login` return
  an access token (30 min) and a refresh token (14 days).
  `POST /api/v1/auth/refresh` exchanges a refresh token for a new access
  token. This is what the frontend uses.
- **API key** — `POST /api/v1/settings/api-keys` (admin role required)
  returns a key of the form `dfk_<random>`, shown once. This is what
  integrations (n8n, scripts, server-to-server) use. Both methods resolve to
  the same internal principal; every endpoint below works with either.

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@company.com", "password": "at-least-10-chars", "organization_name": "Your Company"}'
```

## Endpoints

**Auth** (`/api/v1/auth`)
| Method | Path | |
|---|---|---|
| POST | `/register` | Create a user + organization, returns tokens |
| POST | `/login` | Returns tokens |
| POST | `/refresh` | Exchange a refresh token for a new access token |
| GET | `/session` | Current user + organization |

**Documents** (`/api/v1/documents`)
| Method | Path | |
|---|---|---|
| POST | `/` | Upload (multipart `file`; optional `Idempotency-Key` header) |
| GET | `/` | List, paginated (`limit`/`offset`) |
| GET | `/{id}` | Document metadata + status |
| GET | `/{id}/status` | Lightweight status poll |
| GET | `/{id}/timeline` | Per-stage processing steps, for the UI timeline |
| GET | `/{id}/extraction` | Current extraction: fields, confidence, validation issues |
| POST | `/{id}/reprocess` | Re-run the pipeline (refused if already in flight) |
| GET | `/{id}/download` | Time-limited signed URL to the original file |
| DELETE | `/{id}` | Delete |

**Review** (extraction correction and approval)
| Method | Path | |
|---|---|---|
| PATCH | `/api/v1/documents/{id}/extraction` | Correct one or more field values |
| POST | `/api/v1/documents/{id}/approve` | Approve — locks the extraction as final |
| POST | `/api/v1/documents/{id}/reject` | Reject |
| GET | `/api/v1/reviews/queue` | Documents currently `needs_review`, across the org |

**Analytics & export**
| Method | Path | |
|---|---|---|
| GET | `/api/v1/analytics/dashboard` | The numbers behind the dashboard (counts, review rate, cost) |
| GET | `/api/v1/analytics/usage` | Usage for a billing period |
| GET | `/api/v1/analytics/corrections` | Correction stats — which fields get fixed most |
| GET | `/api/v1/document-types` | Registered document types available to this org |
| GET | `/api/v1/export?format=csv\|json` | Bulk export of approved/completed extractions — see below |

**Settings** (admin role required for writes)
| Method | Path | |
|---|---|---|
| GET / POST | `/api/v1/settings/api-keys` | List / create |
| DELETE | `/api/v1/settings/api-keys/{id}` | Revoke |
| GET / POST | `/api/v1/settings/webhooks` | List / register |
| DELETE | `/api/v1/settings/webhooks/{id}` | Delete |

**Health** (no `/api/v1` prefix, no auth — for load balancers and Prometheus)
| Method | Path | |
|---|---|---|
| GET | `/health` | Liveness — process is up |
| GET | `/readiness` | Readiness — database, Redis, storage, and LLM provider all reachable |
| GET | `/metrics` | Prometheus exposition format |

### Pagination

List endpoints return `{"items": [...], "total": N, "limit": N, "offset": N}`.
`has_more` is derivable (`offset + len(items) < total`) rather than a
separate field to keep in sync.

### Export

`GET /api/v1/export` — the integration escape hatch for tools that don't
speak webhooks (most accounting software imports CSV). Query params:
`format` (`csv` default or `json`), `document_type` (filter), `since`
(ISO datetime), `limit` (1–5000, default 1000). Only current, approved/
completed extractions are included.

## Idempotency

`POST /api/v1/documents` accepts an `Idempotency-Key` header. A retried
request with the same key returns the original job rather than starting a
second one — layered on top of content-addressed dedup (identical file bytes
in the same organization are deduplicated by checksum regardless of whether
a key was sent). See [ARCHITECTURE.md](ARCHITECTURE.md#request-flow-upload-to-result).

## Webhooks

Subscribe via `POST /api/v1/settings/webhooks`:

```json
{"url": "https://your-endpoint.example.com/hooks/docflow",
 "events": ["document.processed", "document.needs_review"]}
```

The response includes a signing secret, **shown once** — store it; it can't
be retrieved again, only rotated by re-registering. Events:
`document.processed`, `document.needs_review`, `document.failed`,
`document.approved`, `document.rejected`.

Delivery is a POST to your URL:

```json
{
  "event": "document.needs_review",
  "organization_id": "...",
  "occurred_at": "2026-08-16T10:33:55+00:00",
  "data": {
    "document_id": "...",
    "extraction_id": "...",
    "status": "needs_review",
    "needs_review": true,
    "confidence": 0.8688
  }
}
```

Headers:

| Header | Meaning |
|---|---|
| `X-Docflow-Event` | Same as the `event` field, for routing without a body parse |
| `X-Docflow-Delivery-Id` | Unique per delivery attempt — use to dedupe on the receiving end |
| `X-Docflow-Timestamp` | Unix timestamp the signature was computed over |
| `X-Docflow-Signature` | `sha256=<hex>` — `HMAC-SHA256(secret, f"{timestamp}.{body}")` |

Reference verification (Node):

```javascript
const crypto = require('crypto');
const expected = 'sha256=' + crypto
  .createHmac('sha256', secret)
  .update(`${timestamp}.${rawBody}`)
  .digest('hex');
// compare with crypto.timingSafeEqual, not ===
```

Delivery never follows redirects, and a registered URL is rejected upfront
if it resolves to a private/loopback/link-local address — see
[SECURITY.md#ssrf-protection-webhooks](SECURITY.md#ssrf-protection-webhooks)
for what that does and does not cover. A working example — email intake plus
review notifications — is in
[integrations/n8n](../integrations/n8n/README.md).

## Rate limits

Fixed window, per organization (per-IP for unauthenticated requests): 120
requests/minute by default, 30/minute for uploads specifically. Exceeding it
returns `429` with `Retry-After`, `X-RateLimit-Limit`, and
`X-RateLimit-Remaining` headers. See
[SECURITY.md#rate-limiting](SECURITY.md#rate-limiting) for why it's fixed-window
and fails open.

## Errors

Every error response has the same shape:

```json
{
  "error": {
    "code": "not_found",
    "category": "user",
    "message": "Not Found",
    "detail": {}
  },
  "request_id": "ea65db48cb2a4f64936c84c7312773b7"
}
```

`code` is stable and meant to be branched on programmatically; `message` is
for humans and may change wording; `request_id` is what to include in a
support request — it's in the structured logs server-side too. `category`
groups errors by cause (`user`, `document`, `validation`, `ai`, `provider`,
`infrastructure`, `authorization`, `internal`) — a client not written to
handle a specific `code` can still make broad decisions (retry `provider`
errors, don't retry `user` errors) from the category alone. Unexpected
server-side exceptions never reach the client as a raw traceback — they
become a generic `500`, with the detail only in server logs, keyed by
`request_id`.
