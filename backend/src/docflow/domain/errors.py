"""Typed error taxonomy.

The single most important property here is `retryable`. A pipeline that retries
everything burns money on LLM calls that will never succeed; a pipeline that retries
nothing fails on transient network blips. Each error class declares its own answer,
and the worker's retry policy reads it rather than guessing from the exception type.

Categories map to who is responsible for the failure, which is what the UI needs in
order to say something useful:

    USER          — the caller did something wrong (bad file, unsupported type)
    DOCUMENT      — the file itself is the problem (corrupt, encrypted, empty)
    VALIDATION    — extraction produced data that failed the rules
    AI            — the model misbehaved (unparseable output, refusal, truncation)
    PROVIDER      — the upstream LLM API failed (rate limit, 5xx, timeout)
    INFRASTRUCTURE— our own dependencies failed (database, storage, queue)
    AUTHORIZATION — the caller may not do this
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    USER = "user"
    DOCUMENT = "document"
    VALIDATION = "validation"
    AI = "ai"
    PROVIDER = "provider"
    INFRASTRUCTURE = "infrastructure"
    AUTHORIZATION = "authorization"
    INTERNAL = "internal"


class DocflowError(Exception):
    """Base class for every error the application raises deliberately.

    `code` is a stable machine-readable string that the frontend switches on and
    that appears in logs and metrics. `message` is safe to show a user — it must
    never contain document contents, secrets or internal paths.
    """

    code: str = "internal_error"
    category: ErrorCategory = ErrorCategory.INTERNAL
    http_status: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.__doc__ or self.code
        self.detail = detail or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------- user


class ValidationRequestError(DocflowError):
    """The request was malformed."""

    code = "invalid_request"
    category = ErrorCategory.USER
    http_status = 400


class FileTooLargeError(DocflowError):
    """The uploaded file exceeds the configured size limit."""

    code = "file_too_large"
    category = ErrorCategory.USER
    http_status = 413


class UnsupportedFileTypeError(DocflowError):
    """The uploaded file type is not supported."""

    code = "unsupported_file_type"
    category = ErrorCategory.USER
    http_status = 415


class QuotaExceededError(DocflowError):
    """The organization has used its document quota for this billing period."""

    code = "quota_exceeded"
    category = ErrorCategory.USER
    http_status = 402


class RateLimitedError(DocflowError):
    """Too many requests."""

    code = "rate_limited"
    category = ErrorCategory.USER
    http_status = 429
    retryable = True


class DuplicateDocumentError(DocflowError):
    """An identical document has already been uploaded."""

    code = "duplicate_document"
    category = ErrorCategory.USER
    http_status = 409


# ----------------------------------------------------------------------- document


class DocumentError(DocflowError):
    """The document could not be processed."""

    code = "document_error"
    category = ErrorCategory.DOCUMENT
    http_status = 422


class CorruptDocumentError(DocumentError):
    """The file could not be parsed."""

    code = "corrupt_document"


class EncryptedDocumentError(DocumentError):
    """The document is password protected."""

    code = "encrypted_document"


class EmptyDocumentError(DocumentError):
    """No text could be extracted from the document."""

    code = "empty_document"


class TooManyPagesError(DocumentError):
    """The document has more pages than the configured limit."""

    code = "too_many_pages"


class TextExtractionError(DocumentError):
    """Text extraction failed."""

    code = "text_extraction_failed"


class OCRUnavailableError(DocumentError):
    """The document needs OCR but no OCR engine is available."""

    code = "ocr_unavailable"


# --------------------------------------------------------------------- validation


class SchemaValidationError(DocflowError):
    """Extracted data did not match the document schema."""

    code = "schema_validation_failed"
    category = ErrorCategory.VALIDATION
    http_status = 422
    # Deterministic: the same input will fail the same way forever. Retrying the
    # *pipeline* is pointless; the extraction step has its own bounded self-repair
    # loop that feeds errors back to the model.
    retryable = False


class BusinessRuleError(DocflowError):
    """A business rule rejected the extracted data."""

    code = "business_rule_failed"
    category = ErrorCategory.VALIDATION
    http_status = 422


class UnknownDocumentTypeError(DocflowError):
    """No schema is registered for this document type."""

    code = "unknown_document_type"
    category = ErrorCategory.VALIDATION
    http_status = 422


# ------------------------------------------------------------------------- ai/llm


class AIError(DocflowError):
    """The model produced an unusable result."""

    code = "ai_error"
    category = ErrorCategory.AI
    http_status = 502
    retryable = True


class MalformedModelOutputError(AIError):
    """The model returned output that could not be parsed as the requested structure."""

    code = "malformed_model_output"
    retryable = True


class ModelRefusalError(AIError):
    """The model declined to process the document."""

    code = "model_refusal"
    # A refusal is a stable property of the input; retrying produces another refusal.
    retryable = False


class OutputTruncatedError(AIError):
    """The model hit the output token limit before completing the structure."""

    code = "output_truncated"
    retryable = True


class CostLimitExceededError(AIError):
    """Processing this document would exceed the configured per-document cost ceiling."""

    code = "cost_limit_exceeded"
    retryable = False


# ----------------------------------------------------------------------- provider


class ProviderError(DocflowError):
    """The LLM provider returned an error."""

    code = "provider_error"
    category = ErrorCategory.PROVIDER
    http_status = 502
    retryable = True


class ProviderRateLimitError(ProviderError):
    """The LLM provider rate limited us."""

    code = "provider_rate_limited"
    retryable = True


class ProviderTimeoutError(ProviderError):
    """The LLM provider timed out."""

    code = "provider_timeout"
    retryable = True


class ProviderAuthError(ProviderError):
    """The LLM provider rejected our credentials."""

    code = "provider_auth_failed"
    # Retrying with the same bad key just burns the retry budget.
    retryable = False


class ProviderNotConfiguredError(ProviderError):
    """No LLM provider is configured."""

    code = "provider_not_configured"
    retryable = False


# ----------------------------------------------------------------- infrastructure


class InfrastructureError(DocflowError):
    """An internal dependency failed."""

    code = "infrastructure_error"
    category = ErrorCategory.INFRASTRUCTURE
    http_status = 503
    retryable = True


class StorageError(InfrastructureError):
    """Object storage failed."""

    code = "storage_error"


class StorageObjectNotFoundError(InfrastructureError):
    """The stored object is missing."""

    code = "storage_object_not_found"
    http_status = 404
    retryable = False


# ---------------------------------------------------------------- authentication


class AuthenticationError(DocflowError):
    """Authentication failed."""

    code = "authentication_failed"
    category = ErrorCategory.AUTHORIZATION
    http_status = 401


class InvalidCredentialsError(AuthenticationError):
    """Incorrect email or password."""

    code = "invalid_credentials"


class TokenExpiredError(AuthenticationError):
    """The token has expired."""

    code = "token_expired"


class AuthorizationError(DocflowError):
    """You do not have permission to perform this action."""

    code = "forbidden"
    category = ErrorCategory.AUTHORIZATION
    http_status = 403


class ResourceNotFoundError(DocflowError):
    """The requested resource does not exist.

    Deliberately also raised when a resource exists but belongs to another
    organization: returning 403 there would confirm the resource's existence and
    turn an authorization check into an enumeration oracle.
    """

    code = "not_found"
    category = ErrorCategory.AUTHORIZATION
    http_status = 404


class ConflictError(DocflowError):
    """The resource is not in a state that allows this operation."""

    code = "conflict"
    category = ErrorCategory.USER
    http_status = 409


RETRYABLE_CATEGORIES = frozenset(
    {ErrorCategory.PROVIDER, ErrorCategory.INFRASTRUCTURE}
)


def is_retryable(exc: BaseException) -> bool:
    """Decide whether the worker should retry after `exc`.

    Unknown exceptions are treated as retryable exactly once by the worker's attempt
    budget: an unexpected crash is more often a transient bug in an edge case than a
    permanent one, and the attempt cap bounds the damage either way.
    """
    if isinstance(exc, DocflowError):
        return exc.retryable
    if isinstance(exc, TimeoutError | ConnectionError | OSError):
        return True
    return True
