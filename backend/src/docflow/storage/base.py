"""Object storage abstraction.

Documents never go in PostgreSQL. Large binary columns bloat the table, break
`pg_dump`, and force every backup to carry data that an object store handles
better and cheaper. The database holds metadata and a *key*; bytes live elsewhere.

Storage keys are generated server-side and are never derived from user input:

    org/{organization_id}/{yyyy}/{mm}/{document_id}/{kind}

Two properties matter. The organization prefix makes per-tenant lifecycle rules,
cost attribution and bulk deletion possible without a database query. And because
the key is server-generated, a hostile filename cannot escape its prefix — path
traversal has nothing to traverse.
"""

from __future__ import annotations

import abc
import datetime as dt
import uuid
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str
    etag: str | None = None


class StorageBackend(abc.ABC):
    """Interface implemented by the local and S3-compatible backends."""

    @abc.abstractmethod
    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject: ...

    @abc.abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abc.abstractmethod
    async def open(self, key: str) -> BinaryIO: ...

    @abc.abstractmethod
    async def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    async def presigned_url(
        self, key: str, *, expires_in: int = 300, filename: str | None = None
    ) -> str:
        """Time-limited URL for direct client download.

        Presigning keeps large file transfers off the API process entirely. The
        URL is short-lived and single-purpose, and is only ever minted after the
        caller has passed an authorization check — possession of a key is never
        sufficient to read an object.
        """

    async def health_check(self) -> bool:
        return True


def build_key(
    organization_id: uuid.UUID | str,
    document_id: uuid.UUID | str,
    *,
    kind: str = "original",
    extension: str = "",
    now: dt.datetime | None = None,
) -> str:
    """Deterministic, tenant-prefixed storage key."""
    moment = now or dt.datetime.now(dt.UTC)
    suffix = extension if extension.startswith(".") or not extension else f".{extension}"
    return f"org/{organization_id}/{moment:%Y/%m}/{document_id}/{kind}{suffix}"


def organization_prefix(organization_id: uuid.UUID | str) -> str:
    return f"org/{organization_id}/"
