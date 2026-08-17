# Deployment

Backend (API + worker + Redis) on Render, frontend on Vercel, Postgres on a
managed provider (Supabase recommended — see below). No Kubernetes; see
[ADR-011](adr/011-render-over-kubernetes.md) for why that's a deliberate
choice, not a gap.

**This has been deployed to live Render and Vercel accounts and verified
end-to-end**: frontend at https://frontend-nine-brown-40.vercel.app, API at
https://docflow-api-o6o1.onrender.com, backed by Supabase (Postgres +
S3-compatible storage) and Render Key Value (Redis). Registration, login and
the dashboard were exercised through the live UI, not just curled directly.
The `docflow-worker` background worker is also deployed and live, on
Render's Starter plan ($7/mo — the free tier has no background-worker
service type at all, so this is a real cost, not a config flag), confirmed
via the Render API (`GET /v1/services/{id}/deploys` reporting `status: live`)
rather than assumed from `render.yaml` existing. Two remaining, deliberate
trade-offs in that live deployment:

- **The API service runs on Render's free web-service tier**, which spins
  down after inactivity. The first request after a quiet period takes several
  seconds (cold start) before the app responds; subsequent requests are
  normal speed. (The worker is unaffected — background workers don't spin
  down the same way, and it's on a paid plan regardless.)
- **`DOCFLOW_LLM_PROVIDER=google`** (Gemini 3.6 Flash), not the `anthropic`
  default this file's blueprint originally specified — no Anthropic key was
  available at deploy time. Gemini's free-tier key is capped at 20
  requests/day for this model; see [EVALUATION.md](EVALUATION.md).

## Backend: Render

[render.yaml](../render.yaml) is a Render Blueprint — connect the repo in
the Render dashboard ("New +" → "Blueprint") or run `render blueprint
launch`, and it provisions:

- **`docflow-api`** — web service, `backend/Dockerfile`, health check on
  `/readiness`.
- **`docflow-worker`** — background worker, the same image, running
  `arq docflow.worker.main.WorkerSettings` instead of `uvicorn`. One image
  built and versioned once, run as two services — see the comment at the top
  of `backend/Dockerfile` for why that's the right trade at this scale.
- **`docflow-redis`** — managed Redis, private network only.

Every `sync: false` variable in render.yaml (database URL, storage
credentials, LLM API key, CORS origins) is prompted for at blueprint launch
and stored as a Render secret — never written to the file. `DOCFLOW_SECURITY_JWT_SECRET`
on the API service uses `generateValue: true` (Render generates it); the
worker's copy must be set to the **same** value manually, since JWTs signed
by the API need to validate the same way wherever they're checked — the
worker doesn't currently validate JWTs itself, but keeping the secret
identical across both services avoids a foot-gun if that ever changes.

Postgres is deliberately **not** declared in render.yaml. Render's own
managed Postgres is a reasonable choice; this deployment defaults to
Supabase instead — set `DOCFLOW_DB_URL` to its connection string. Either
way, the application only needs a standard `postgresql+asyncpg://` URL;
nothing in the code is Supabase-specific, so swapping hosts is a
configuration change, not a code change.

### Production safety gate

`Settings.validate_for_environment()` runs at startup and **refuses to boot**
when `DOCFLOW_ENVIRONMENT=production` and any of these hold: the JWT secret
is still the dev default or under 32 characters, storage backend is `local`
(not durable/shared across replicas), the LLM provider is `fixture`, the
selected provider's API key is missing, CORS allows `*`, or `DOCFLOW_DEBUG`
is true. A service that starts with an insecure default is worse than one
that fails to start — this makes that failure loud and immediate instead of
a silent production footgun.

## Frontend: Vercel

[frontend/vercel.json](../frontend/vercel.json) pins the region to `fra1`
(Frankfurt) — close to both the Render deployment (also `frankfurt`) and the
target Czech/Central-European market — and sets explicit build/install
commands. Root Directory must be set to `frontend` in the Vercel project
settings (this is a monorepo; Vercel doesn't infer that from `vercel.json`
alone). Set `NEXT_PUBLIC_API_URL` to the deployed Render API's public URL in
the Vercel project's environment variables — as a **build-time** variable,
since `NEXT_PUBLIC_*` values are inlined into the client bundle at build,
not read at request time (see `frontend/Dockerfile`'s comment for the full
explanation and the bug this caused during local Docker Compose
verification).

## Docker images

One backend image (API + worker), one frontend image, both multi-stage:

- **Backend** (`backend/Dockerfile`) — installs OCR system dependencies
  (`tesseract-ocr` with English + Czech language packs, `poppler-utils`,
  `libmagic1`), resolves Python dependencies from `uv.lock` in a layer
  separate from application code (so a source-only change doesn't
  reinstall the dependency tree), runs as a non-root user. `/data/storage`
  (the local-storage-backend mount point, used in Compose, not production)
  is pre-created and `chown`ed in the image specifically so Docker's
  volume-initialization-from-image behavior gives a fresh named volume the
  right ownership — without that, a clean `docker compose up` starts with a
  root-owned volume the non-root container can't write to, and every upload
  fails. This was a real bug caught by actually running the full stack, not
  a hypothetical.
- **Frontend** (`frontend/Dockerfile`) — Next.js standalone output, also
  non-root. `NEXT_PUBLIC_API_URL` must be passed as `--build-arg`.

```bash
docker build -t docflow-backend:local ./backend
docker build -t docflow-frontend:local --build-arg NEXT_PUBLIC_API_URL=https://api.example.com ./frontend
```

## Environment variables

Full reference: [backend/.env.example](../backend/.env.example) and
[frontend/.env.example](../frontend/.env.example) document every variable
with its default and purpose. The ones that matter specifically for
production, beyond what's in render.yaml already:

| Variable | Production value |
|---|---|
| `DOCFLOW_ENVIRONMENT` | `production` — enables the safety gate above |
| `DOCFLOW_STORAGE_BACKEND` | `s3` — local storage is rejected in production |
| `DOCFLOW_LLM_PROVIDER` | `anthropic` or `openai` — `fixture` is rejected |
| `DOCFLOW_SECURITY_CORS_ORIGINS` | Your actual frontend origin(s), never `*` |
| `DOCFLOW_OBS_LOG_FORMAT` | `json` (default) — structured logs for whatever aggregates them |

## Scaling notes

- **API** scales horizontally behind Render's load balancer; it's stateless
  (sessions are JWTs, rate limiting is Redis-backed so the limit is shared
  across replicas rather than multiplied by replica count).
- **Worker** has no HTTP endpoint to autoscale against. Start at one
  instance and raise `numInstances` in render.yaml (or move to a plan with
  autoscaling) once `docflow_queue_depth` in Prometheus shows sustained
  backlog rather than transient bursts.
- **Database** connection pool (`DOCFLOW_DB_POOL_SIZE`,
  `DOCFLOW_DB_MAX_OVERFLOW`) needs headroom for `worker_concurrency`
  (default 8) times the number of worker instances, plus the API's own
  pool — size Postgres's `max_connections` accordingly if running many
  worker replicas.

## What's not built

- Blue/green or canary deployment — Render's default rolling deploy is what
  ships today.
- Autoscaling policy — instance counts are static in render.yaml; scaling is
  a manual dashboard/config change today, not automatic.
- A CDN/WAF layer in front of the API — expected to sit in front of the
  deployment (Render's own edge, or Cloudflare in front of it), not
  something this codebase provides itself.
