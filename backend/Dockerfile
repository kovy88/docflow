# Single image serves both the API and the worker — `docker-compose.yml` and
# the Render blueprint point the same image at two different commands
# (`uvicorn ...` vs `arq ...`). One image to build, scan and version instead of
# two nearly-identical ones is the right trade at this scale; split them only if
# the API and worker ever need different base images or dependency sets.

FROM python:3.11-slim AS base

# poppler-utils: pdftoppm, used by pdf2image for OCR rasterisation.
# tesseract-ocr: the OCR engine itself, plus English + Czech language packs —
#   the two languages the target market's documents are actually written in.
# libmagic1: python-magic's native dependency for content-based MIME sniffing.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-ces \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies resolved from the lockfile before any application code is
# copied in, so a source-only change reuses this layer instead of
# reinstalling the whole tree on every build. `--no-install-project` installs
# only third-party dependencies here — the project itself isn't copied in
# yet, so there's nothing for hatchling to build. `--extra ocr` pulls in
# pytesseract/pdf2image to match the tesseract/poppler system packages above.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra ocr

# README.md must be present before the project install — hatchling reads
# pyproject.toml's `readme` field and fails the build without it.
COPY README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

# Now install the project itself; dependencies are already cached above so
# this is fast regardless of what changed in src/.
RUN uv sync --frozen --extra ocr

# /data/storage is the local-storage-backend mount point (docker-compose.yml
# binds a named volume there). Docker initializes a fresh named volume by
# copying in whatever already exists at that path in the image — including
# ownership — so creating and chowning it here is what makes the volume
# writable by the non-root user instead of landing root-owned.
RUN useradd --create-home --uid 1000 docflow \
    && mkdir -p /data/storage \
    && chown -R docflow:docflow /app /data/storage
USER docflow

ENV PATH="/app/.venv/bin:${PATH}"

# Neither CMD applies unconditionally — docker-compose.yml and the Render
# blueprint each override it per-service (`uvicorn ...` for the API,
# `arq docflow.worker.main.WorkerSettings` for the worker). This default runs
# the API, since that is the more common thing to want from `docker run` with
# no extra arguments.
EXPOSE 8000
CMD ["uvicorn", "docflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
