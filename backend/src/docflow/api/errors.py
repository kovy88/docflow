"""Uniform error responses.

Every error the API returns has the same envelope:

    {"error": {"code": "...", "category": "...", "message": "...", "detail": {...}},
     "request_id": "..."}

A stable `code` is what lets a client branch on failures without string-matching
human-readable text. `request_id` is what lets a user paste one token into a
support request and have it found in the logs.

The other rule here is that **unexpected exceptions never reach the client**. A
raw traceback leaks file paths, library versions and sometimes document content.
Handled errors carry curated messages; everything else becomes a generic 500 whose
detail lives only in the logs.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from docflow.domain.errors import DocflowError, ErrorCategory

logger = structlog.get_logger(__name__)


def error_body(
    *,
    code: str,
    message: str,
    category: str = ErrorCategory.INTERNAL.value,
    detail: dict[str, Any] | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "category": category,
            "message": message,
            "detail": detail or {},
        },
        "request_id": request_id,
    }


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DocflowError)
    async def _docflow_error(request: Request, exc: DocflowError) -> JSONResponse:
        request_id = _request_id(request)
        # 5xx means we broke something; 4xx means the caller did. Only the former
        # warrants an error-level log and an alert.
        log = logger.error if exc.http_status >= 500 else logger.info
        log(
            "api.error",
            code=exc.code,
            category=exc.category.value,
            status=exc.http_status,
            path=request.url.path,
            request_id=request_id,
        )
        headers = {}
        if retry_after := exc.detail.get("retry_after_seconds"):
            headers["Retry-After"] = str(int(retry_after))
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(
                code=exc.code,
                message=exc.message,
                category=exc.category.value,
                detail=exc.detail,
                request_id=request_id,
            ),
            headers=headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_body(
                code="invalid_request",
                message="The request body or parameters are invalid",
                category=ErrorCategory.USER.value,
                detail={"errors": _readable_errors(exc)},
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code=_code_for_status(exc.status_code),
                message=str(exc.detail),
                category=(
                    ErrorCategory.USER.value
                    if exc.status_code < 500
                    else ErrorCategory.INTERNAL.value
                ),
                request_id=_request_id(request),
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.exception(
            "api.unhandled_exception",
            path=request.url.path,
            method=request.method,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body(
                code="internal_error",
                message="An unexpected error occurred. Quote the request id if you contact support.",
                request_id=request_id,
            ),
        )


def _readable_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for error in exc.errors()[:20]:
        location = [str(p) for p in error.get("loc", []) if p not in ("body", "query")]
        out.append(
            {
                "field": ".".join(location) or None,
                "message": error.get("msg", "is invalid"),
                "type": error.get("type"),
            }
        )
    return out


_STATUS_CODES = {
    400: "invalid_request",
    401: "authentication_failed",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    415: "unsupported_file_type",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def _code_for_status(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, f"http_{status_code}")
