"""Local filesystem storage — development and tests only.

Production refuses to boot with this backend (`Settings.validate_for_environment`),
because it cannot survive a container restart and cannot be shared between the API
and worker processes.

Filesystem I/O runs in a thread executor. Blocking the event loop on a synchronous
`open()` would stall every other request the process is serving — a small write is
still a syscall that can block on a slow disk, and the API handles uploads
concurrently.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import shutil
import time
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import structlog

from docflow.domain.errors import AuthenticationError, StorageError, StorageObjectNotFoundError
from docflow.storage.base import StorageBackend, StoredObject

logger = structlog.get_logger(__name__)


def _signature(secret: str, key: str, expires: int) -> str:
    # HMAC over key+expiry, not a bearer token: the URL returned by
    # `presigned_url()` is handed to `window.open()` by the frontend (see
    # documents/[id]/page.tsx), a plain navigation that carries no
    # Authorization header. `/api/v1/storage/{key}` therefore cannot require
    # the usual bearer-auth dependency — it has to carry its own proof, the
    # same way an S3 presigned URL's query-string signature does. The `Signed`
    # in the docflow module docstring means this, not a JWT.
    message = f"{key}:{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign_local_key(secret: str, key: str, *, expires_in: int) -> tuple[int, str]:
    expires = int(time.time()) + expires_in
    return expires, _signature(secret, key, expires)


def verify_local_signature(secret: str, key: str, *, expires: int, signature: str) -> None:
    if time.time() > expires:
        raise AuthenticationError("This download link has expired")
    expected = _signature(secret, key, expires)
    if not hmac.compare_digest(expected, signature):
        raise AuthenticationError("Invalid download link")


class LocalStorage(StorageBackend):
    def __init__(self, root: str | Path, *, public_base_url: str = "", secret: str = "") -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._public_base_url = public_base_url.rstrip("/")
        # Signs presigned-URL query params — see `_signature()`. Reuses the app's
        # JWT secret rather than a dedicated one: this backend is already refused
        # in production (`Settings.validate_for_environment`), so a second secret
        # to provision and rotate would be cost with no real benefit.
        self._secret = secret

    def verify_presigned(self, key: str, *, expires: int, signature: str) -> None:
        """Checked by `api/routes/storage.py` against *this instance's* secret.

        Deliberately not `settings.security.jwt_secret` read fresh from the route
        — tests override the storage backend with its own `LocalStorage` (see
        `conftest.py`'s `storage` fixture), and a URL minted by one secret must be
        verified against that same secret, not whatever the global settings
        singleton currently holds.
        """
        verify_local_signature(self._secret, key, expires=expires, signature=signature)

    def _path(self, key: str) -> Path:
        """Resolve a key to a path, refusing anything that escapes the root.

        Keys are server-generated, so this should be unreachable. It is here
        because "should be unreachable" is exactly the assumption that turns a
        future refactor into a path-traversal vulnerability, and the check costs a
        `resolve()`.
        """
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root):
            raise StorageError("Invalid storage key")
        return candidate

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a sibling temp file then rename. `os.replace` is atomic on
            # POSIX, so a crash mid-write leaves either the old object or nothing —
            # never a truncated file that later reads as a corrupt document.
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            raise StorageError("Could not write the object to local storage") from exc

        return StoredObject(key=key, size_bytes=len(data), content_type=content_type)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageObjectNotFoundError(f"Object not found: {key}") from exc
        except OSError as exc:
            raise StorageError("Could not read the object from local storage") from exc

    async def open(self, key: str) -> BinaryIO:
        return io.BytesIO(await self.get(key))

    async def delete(self, key: str) -> None:
        path = self._path(key)

        def _remove() -> None:
            path.unlink(missing_ok=True)

        await asyncio.to_thread(_remove)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).exists)

    async def presigned_url(
        self, key: str, *, expires_in: int = 300, filename: str | None = None
    ) -> str:
        """Local storage cannot presign against a real object store, so this
        mints its own short-lived, HMAC-signed URL and points it at
        `GET /api/v1/storage/{key}` (`api/routes/storage.py`), which verifies the
        signature instead of the usual bearer token — see `_signature()` for why
        it can't just require the normal auth dependency like every other route.
        """
        expires, signature = sign_local_key(self._secret, key, expires_in=expires_in)
        query = f"expires={expires}&sig={signature}"
        if filename:
            query += f"&filename={quote(filename)}"
        return f"{self._public_base_url}/api/v1/storage/{quote(key, safe='')}?{query}"

    async def health_check(self) -> bool:
        try:
            probe = self._root / ".healthcheck"
            await asyncio.to_thread(probe.write_text, "ok")
            await asyncio.to_thread(probe.unlink)
        except OSError:
            return False
        return True

    async def wipe(self) -> None:
        """Remove everything. Test fixtures only."""
        await asyncio.to_thread(shutil.rmtree, self._root, True)
        self._root.mkdir(parents=True, exist_ok=True)
