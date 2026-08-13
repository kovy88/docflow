"""Upload validation — the first security boundary.

An uploaded file is hostile input. Before anything else touches it we establish:
size, real type, and structural sanity. Three rules govern this module:

1. **Never trust the client.** `Content-Type` headers and filename extensions are
   attacker-controlled. Type is determined by inspecting the leading bytes, and the
   declared type is only used to *contradict* the sniffed one, never to confirm it.

2. **Bound everything before allocating.** Size is enforced while streaming, not
   after buffering, so a 10 GB upload cannot exhaust memory on its way to being
   rejected.

3. **Reject early, cheaply.** Every check here is orders of magnitude cheaper than
   the LLM call it might prevent, so the ordering runs cheapest-first.

What this deliberately does *not* do is claim to be an antivirus. Real malware
scanning needs a maintained signature database (ClamAV or a scanning API) and is a
deployment concern, not an application one. The hook is defined in
`scan_for_malware` and documented as a no-op; pretending otherwise would be a
security claim we cannot back.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import BinaryIO

import structlog

from docflow.config import UploadSettings
from docflow.domain.errors import (
    CorruptDocumentError,
    EncryptedDocumentError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    ValidationRequestError,
)

logger = structlog.get_logger(__name__)

READ_CHUNK = 64 * 1024

# Magic-byte signatures. A short explicit table beats a heavyweight dependency
# here: we support seven types, and knowing exactly what is accepted matters more
# than breadth.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"RIFF", "image/webp"),  # refined below — RIFF is a container
)

# DOCX and friends are ZIP archives; the specific type needs the archive read.
_ZIP_MAGIC = b"PK\x03\x04"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_FILENAME_SAFE = re.compile(r"[^\w\s.\-()\[\]]", re.UNICODE)
MAX_FILENAME_LENGTH = 200


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    declared_content_type: str | None = None

    @property
    def type_mismatch(self) -> bool:
        """True when the client lied (or was wrong) about the file type.

        Not fatal on its own — browsers routinely send `application/octet-stream` —
        but recorded in the audit log because a deliberate mismatch is a signal.
        """
        if not self.declared_content_type:
            return False
        return self.declared_content_type.split(";")[0].strip() != self.content_type


def sniff_content_type(head: bytes) -> str | None:
    """Determine the type from leading bytes."""
    if head.startswith(_ZIP_MAGIC):
        return _DOCX_MIME if b"word/" in head[:2048] else None
    for signature, mime in _SIGNATURES:
        if head.startswith(signature):
            if mime == "image/webp":
                return "image/webp" if head[8:12] == b"WEBP" else None
            return mime
    if _looks_like_text(head):
        return "text/plain"
    return None


def _looks_like_text(head: bytes) -> bool:
    """UTF-8 decodable and free of control characters that binaries carry."""
    if not head:
        return False
    if b"\x00" in head:
        return False
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
    return printable / len(text) > 0.9


def sanitize_filename(filename: str) -> str:
    """Make a filename safe to store and display.

    Strips directory components (path traversal), control characters and anything
    outside a conservative character class. The result is only ever used as a
    *display* label — storage keys are generated server-side and never derived from
    user input, so a hostile filename has nowhere to go even if this were bypassed.
    """
    if not filename:
        return "document"
    # Both separators, because a Windows client can send either.
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in base if c.isprintable())
    base = _FILENAME_SAFE.sub("_", base).strip(". ")
    if not base:
        return "document"
    if len(base) > MAX_FILENAME_LENGTH:
        stem, _, ext = base.rpartition(".")
        keep = MAX_FILENAME_LENGTH - len(ext) - 1
        base = f"{stem[:keep]}.{ext}" if ext and keep > 0 else base[:MAX_FILENAME_LENGTH]
    return base


def validate_upload(
    stream: BinaryIO,
    *,
    filename: str,
    declared_content_type: str | None,
    settings: UploadSettings,
) -> tuple[ValidatedUpload, bytes]:
    """Validate and buffer an upload. Returns metadata plus the file bytes.

    Buffering in memory is a deliberate, bounded choice: `max_bytes` defaults to
    20 MB and is enforced *during* the read, so the worst case is bounded and small.
    Streaming straight to object storage would avoid the buffer but would mean
    writing unvalidated bytes to storage first and cleaning up after — more moving
    parts and a worse failure mode for a saving that does not matter at this size.
    """
    clean_name = sanitize_filename(filename)

    buffer = bytearray()
    digest = hashlib.sha256()
    total = 0

    while chunk := stream.read(READ_CHUNK):
        total += len(chunk)
        if total > settings.max_bytes:
            raise FileTooLargeError(
                f"File exceeds the {settings.max_bytes // (1024 * 1024)} MB limit",
                detail={"limit_bytes": settings.max_bytes},
            )
        digest.update(chunk)
        buffer.extend(chunk)

    if total == 0:
        raise ValidationRequestError("The uploaded file is empty")

    data = bytes(buffer)
    sniffed = sniff_content_type(data[:4096])
    if sniffed is None:
        raise UnsupportedFileTypeError(
            "The file type could not be determined or is not supported",
            detail={"declared": declared_content_type},
        )
    if sniffed not in settings.allowed_mime_types:
        raise UnsupportedFileTypeError(
            f"{sniffed} is not a supported document type",
            detail={"detected": sniffed, "supported": sorted(settings.allowed_mime_types)},
        )

    if sniffed == "application/pdf":
        _assert_pdf_usable(data)

    upload = ValidatedUpload(
        filename=clean_name,
        content_type=sniffed,
        size_bytes=total,
        checksum_sha256=digest.hexdigest(),
        declared_content_type=declared_content_type,
    )
    if upload.type_mismatch:
        logger.info(
            "upload.type_mismatch",
            declared=declared_content_type,
            detected=sniffed,
            filename=clean_name,
        )
    return upload, data


def _assert_pdf_usable(data: bytes) -> None:
    """Fail fast on PDFs we cannot read, with a reason the user can act on.

    Discovering "this PDF is password protected" three stages into the pipeline,
    after a storage write and a queue round trip, wastes work and produces a worse
    error message than catching it at the door.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    import io

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            # An empty user password is common and harmless — many tools set an
            # owner password only. Try it before declaring the file unreadable.
            try:
                if reader.decrypt("") == 0:
                    raise EncryptedDocumentError(
                        "This PDF is password protected. Remove the password and upload again."
                    )
            except EncryptedDocumentError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise EncryptedDocumentError(
                    "This PDF is password protected. Remove the password and upload again."
                ) from exc
        if len(reader.pages) == 0:
            raise CorruptDocumentError("The PDF contains no pages")
    except (EncryptedDocumentError, CorruptDocumentError):
        raise
    except PdfReadError as exc:
        raise CorruptDocumentError("The PDF could not be read — the file may be damaged") from exc
    except Exception as exc:  # noqa: BLE001
        raise CorruptDocumentError("The PDF could not be parsed") from exc


def scan_for_malware(data: bytes, *, filename: str) -> None:
    """Malware scanning hook — **currently a no-op**.

    Deliberately unimplemented rather than faked. A real implementation calls out
    to ClamAV or a scanning API; that is infrastructure the deployment must
    provide, and stubbing a `return True` here would let the security documentation
    claim a control that does not exist.

    `docs/SECURITY.md` lists this as a known gap. What genuinely mitigates the risk
    today is that uploaded bytes are never executed, never rendered by a browser
    from our origin (downloads are served with `Content-Disposition: attachment`
    from a separate storage host), and are only ever parsed by PDF/DOCX libraries
    running inside the worker container.
    """
    return None
