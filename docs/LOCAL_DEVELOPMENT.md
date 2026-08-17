# Local development

## Fastest path: Docker Compose

```bash
docker compose --profile full up -d --build
docker compose exec api docflow-seed
```

Frontend at `http://localhost:3000`, API at `http://localhost:8000` (docs at
`/docs`). Postgres and Redis are on non-default host ports (5433, 6380) so
they won't collide with anything already running on your machine. `--profile
full` is needed because the frontend service is opt-in in
[docker-compose.yml](../docker-compose.yml) — `docker compose up` without it
starts just Postgres, Redis, the API, and the worker, which is enough for
backend-only work (e.g. hitting the API directly, or running the frontend
separately with `npm run dev` for hot reload — see below).

`api` and `worker` mount `./backend` for hot reload (`uvicorn --reload`, and
the worker restarts on `docker compose restart worker` since arq doesn't
hot-reload). If you've run `uv sync` on the host, `backend/.venv` exists
locally — the compose file masks it with an anonymous volume
(`- /app/.venv` after the bind mount) specifically so the container keeps
its own Linux-built virtualenv instead of the host's, which would otherwise
be macOS/arm64 binaries failing to import inside the Linux container.

No LLM API key is required — see the fixture provider note below.

## Without Docker

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 22+, a local
Postgres and Redis, and (for OCR) `tesseract` and `poppler` installed on the
host (`brew install tesseract poppler` on macOS).

```bash
# Backend
cd backend
cp .env.example .env   # edit DOCFLOW_DB_URL / DOCFLOW_REDIS_URL for your local instances
uv sync --extra dev --extra ocr
uv run alembic upgrade head
uv run uvicorn docflow.main:app --reload   # API on :8000

# separate terminal — the worker
uv run arq docflow.worker.main.WorkerSettings

# separate terminal — demo data
uv run docflow-seed
```

```bash
# Frontend
cd frontend
cp .env.example .env.local
npm install
npm run dev   # :3000
```

See [backend/.env.example](../backend/.env.example) and
[frontend/.env.example](../frontend/.env.example) for every variable —
everything has a working local default except the LLM key.

## Running without a real LLM key

`DOCFLOW_LLM_PROVIDER` defaults to `fixture`: a deterministic, rule-based
extractor that implements the same `LLMProvider` interface a real model
would, so the entire pipeline (classification, extraction, validation,
confidence scoring, review routing) runs and produces genuinely different
output per document — it is not a mock that returns a canned response. It
is *not* a substitute for measuring real model accuracy; see
[EVALUATION.md](EVALUATION.md) for exactly what is and isn't measured with
it. Set `DOCFLOW_LLM_PROVIDER=anthropic` (or `openai`, or `google`) and the
matching `DOCFLOW_LLM_ANTHROPIC_API_KEY` / `DOCFLOW_LLM_OPENAI_API_KEY` /
`DOCFLOW_LLM_GOOGLE_API_KEY` to use a real model.

## Tests

```bash
cd backend
uv run pytest                          # 258 tests: unit + integration
uv run pytest tests/unit                # unit only — no database needed
uv run pytest tests/integration         # needs a real Postgres (docker-compose's, or DOCFLOW_DB_URL)
uv run pytest --cov=docflow             # with coverage
```

Integration tests run against a real Postgres — not SQLite, not a mock
session — with each test wrapped in a transaction that's rolled back at
teardown, so the suite doesn't need per-test cleanup or a fresh database.

```bash
cd frontend
npm run lint
npm run typecheck
npm run build   # production build — the strongest correctness check the frontend has short of E2E
```

## Linting & type checking

```bash
cd backend
uv run ruff check --fix .
uv run ruff format .
uv run mypy src
```

CI runs the same commands — see
[.github/workflows/backend.yml](../.github/workflows/backend.yml) and
[.github/workflows/frontend.yml](../.github/workflows/frontend.yml).

## Evaluation harness

```bash
cd backend
uv run docflow-eval                            # baseline + fixture, no API key needed
uv run docflow-eval --provider anthropic       # real model, needs a key
uv run docflow-eval --provider google --size 20  # Gemini; --size caps quota spend
```

See [EVALUATION.md](EVALUATION.md) for methodology and the actual measured
results.

## Database migrations

One baseline migration today
(`backend/alembic/versions/5bedce49434f_initial_schema.py`). To add a new one
after changing `db/models.py`:

```bash
cd backend
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

Autogenerate is a starting point, not a final answer — check the generated
migration before running it, particularly for anything Alembic can't infer
(data backfills, renames it sees as drop+add).

## Common issues

- **`ModuleNotFoundError: docflow` inside a container** — almost always the
  `.venv` bind-mount shadowing issue described above; confirm the anonymous
  volume mask is in `docker-compose.yml` for the affected service.
- **Storage writes fail with `Permission denied` in a fresh compose stack** —
  fixed by `backend/Dockerfile` pre-creating and `chown`ing `/data/storage`
  so Docker's volume initialization copies in the right ownership; if you
  hit this, check you're on a rebuilt image (`docker compose build api`),
  not a stale one from before that fix.
- **Frontend build reaches the API at the wrong URL** — `NEXT_PUBLIC_API_URL`
  is a *build-time* value; setting it under `environment:` in a compose file
  or `-e` on `docker run` does nothing after the fact. See the comment in
  `frontend/Dockerfile`.
