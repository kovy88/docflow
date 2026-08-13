"""Prometheus metrics.

Chosen to answer the questions an operator actually asks at 3am, not to maximise
the number of series:

    Is the API healthy?          docflow_http_requests_total, latency histogram
    Is the queue keeping up?     docflow_documents_processed_total, duration
    Are we burning money?        docflow_llm_cost_usd_total, tokens
    Is quality degrading?        review rate, validation failures — derivable from
                                 the labelled counters below
    Is a provider failing?       docflow_llm_errors_total by error code

Label discipline matters more than metric count. Labels are bounded sets
(document type, status, provider, model) — never a document id, organization id or
user id, each of which would create unbounded cardinality and eventually take down
the Prometheus server. Per-tenant numbers come from SQL, which is built for it.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()

# ------------------------------------------------------------------------ http

http_requests = Counter(
    "docflow_http_requests_total",
    "HTTP requests by method, route template and status class",
    ["method", "route", "status"],
    registry=REGISTRY,
)

http_duration = Histogram(
    "docflow_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route"],
    # Tuned for an API whose slow path is a file upload, not an LLM call —
    # processing is asynchronous, so no HTTP request should take 30 seconds.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

# ------------------------------------------------------------------ processing

documents_processed = Counter(
    "docflow_documents_processed_total",
    "Documents that completed processing, by type and outcome",
    ["document_type", "status"],
    registry=REGISTRY,
)

processing_duration = Histogram(
    "docflow_processing_duration_seconds",
    "End-to-end document processing duration",
    ["document_type"],
    # Wide, log-ish buckets: a one-page text PDF and a 40-page scan that needs OCR
    # differ by two orders of magnitude.
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
    registry=REGISTRY,
)

stage_duration = Histogram(
    "docflow_stage_duration_seconds",
    "Per-pipeline-stage duration",
    ["stage"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60),
    registry=REGISTRY,
)

documents_needing_review = Counter(
    "docflow_documents_needing_review_total",
    "Documents routed to human review, by type and primary reason",
    ["document_type", "reason"],
    registry=REGISTRY,
)

validation_failures = Counter(
    "docflow_validation_failures_total",
    "Validation issues raised, by rule and severity",
    ["rule_id", "severity"],
    registry=REGISTRY,
)

# ------------------------------------------------------------------------- llm

llm_calls = Counter(
    "docflow_llm_calls_total",
    "LLM calls by provider, model and purpose",
    ["provider", "model", "purpose"],
    registry=REGISTRY,
)

llm_tokens = Counter(
    "docflow_llm_tokens_total",
    "LLM tokens by provider, model and direction",
    ["provider", "model", "direction"],
    registry=REGISTRY,
)

llm_cost = Counter(
    "docflow_llm_cost_usd_total",
    "Estimated LLM cost in USD (list prices; the provider invoice is authoritative)",
    ["provider", "model"],
    registry=REGISTRY,
)

llm_errors = Counter(
    "docflow_llm_errors_total",
    "LLM call failures by provider and error code",
    ["provider", "error_code"],
    registry=REGISTRY,
)

llm_latency = Histogram(
    "docflow_llm_latency_seconds",
    "LLM call latency",
    ["provider", "model"],
    buckets=(0.5, 1, 2, 3, 5, 8, 12, 20, 30, 60, 120),
    registry=REGISTRY,
)

# ----------------------------------------------------------------------- queue

queue_depth = Gauge(
    "docflow_queue_depth",
    "Jobs waiting in the queue",
    registry=REGISTRY,
)

job_retries = Counter(
    "docflow_job_retries_total",
    "Job retries by error code",
    ["error_code"],
    registry=REGISTRY,
)

jobs_dead_lettered = Counter(
    "docflow_jobs_dead_lettered_total",
    "Jobs that exhausted their retry budget",
    ["error_code"],
    registry=REGISTRY,
)


# ----------------------------------------------------------------- record APIs


def record_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    # Bucketing status as `2xx`/`4xx`/`5xx` keeps cardinality at three values per
    # route instead of one per distinct code, which is all an alert needs.
    http_requests.labels(method=method, route=route, status=f"{status // 100}xx").inc()
    http_duration.labels(method=method, route=route).observe(duration_seconds)


def record_document(
    *,
    document_type: str,
    status: str,
    duration_seconds: float,
    needs_review: bool,
    review_reason: str = "",
) -> None:
    documents_processed.labels(document_type=document_type, status=status).inc()
    processing_duration.labels(document_type=document_type).observe(duration_seconds)
    if needs_review:
        documents_needing_review.labels(
            document_type=document_type, reason=review_reason or "unspecified"
        ).inc()


def record_llm_call(
    *,
    provider: str,
    model: str,
    purpose: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_seconds: float,
) -> None:
    llm_calls.labels(provider=provider, model=model, purpose=purpose).inc()
    llm_tokens.labels(provider=provider, model=model, direction="input").inc(input_tokens)
    llm_tokens.labels(provider=provider, model=model, direction="output").inc(output_tokens)
    llm_cost.labels(provider=provider, model=model).inc(cost_usd)
    llm_latency.labels(provider=provider, model=model).observe(latency_seconds)


def record_llm_error(provider: str, error_code: str) -> None:
    llm_errors.labels(provider=provider, error_code=error_code).inc()


def record_stage(stage: str, duration_seconds: float) -> None:
    stage_duration.labels(stage=stage).observe(duration_seconds)


def record_validation_issue(rule_id: str, severity: str) -> None:
    validation_failures.labels(rule_id=rule_id, severity=severity).inc()


def render() -> bytes:
    return generate_latest(REGISTRY)


CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
