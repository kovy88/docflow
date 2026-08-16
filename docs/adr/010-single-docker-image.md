# ADR-010: One Docker image for API and worker

## Context

The API and worker are two processes running the same application code
against the same dependencies (SQLAlchemy models, pipeline stages, LLM
providers) — the worker imports and runs the same pipeline the API enqueues
work for. They differ only in entrypoint: `uvicorn docflow.main:app` vs.
`arq docflow.worker.main.WorkerSettings`.

## Decision

One image (`backend/Dockerfile`), one set of dependencies, one set of OCR
system packages (`tesseract-ocr`, `poppler-utils`, `libmagic1`) installed
once. `docker-compose.yml` and [render.yaml](../../render.yaml) each point the
same image at two different commands.

## Alternatives considered

- **Two separate Dockerfiles/images.** Would let each image be minimally
  smaller (though not by much — both processes import the same pipeline
  code, so there's little to actually exclude), at the cost of two build
  pipelines to keep in sync, two places a dependency bump has to land, and
  two vulnerability-scan surfaces for identical code. Rejected: the
  processes aren't different applications, they're different entrypoints
  into the same one.
- **A shared base image, two thin images built from it (API-only and
  worker-only layers on top).** A middle ground — reduces some duplication
  but still requires building, tagging, and deploying two images per
  release instead of one, for a project where the two processes have never
  needed different dependencies. Worth revisiting specifically if the API
  and worker ever need different base images or dependency sets (e.g., the
  worker needing a GPU-enabled base image the API doesn't) — not before.

## Consequences

- One image to build, scan (`bandit`, `pip-audit`), and version — a
  security fix or dependency bump is one rebuild, not two kept in sync by
  hand.
- The image is somewhat larger than an API-only image would be (OCR system
  packages the API process itself never calls directly — only the worker's
  pipeline does), which is judged worth it for build/deploy simplicity at
  this scale.
- CI's Docker job (`.github/workflows/backend.yml`) validates one build,
  not two.
- If the API and worker ever do need to diverge (different base image,
  different Python dependency set, independent scaling of image size), this
  decision is the one to revisit — the split point is already clean, since
  they're already separate `CMD`s in separate service definitions in both
  `docker-compose.yml` and [render.yaml](../../render.yaml).
