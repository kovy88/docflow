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
import io
import shutil
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import structlog

from docflow.domain.errors import StorageError, StorageObjectNotFoundError
from docflow.storage.base import StorageBackend, StoredObject

logger = structlog.get_logger(__name__)


class LocalStorage(StorageBackend):
    def __init__(self, root: str | Path, *, public_base_url: str = "") -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._public_base_url = public_base_url.rstrip("/")

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
        """Local storage cannot presign, so this points back at the API.

        The API's own download endpoint re-checks authorization and streams the
        bytes. Slower than a real presigned URL, and correct — which is the right
        trade for a development backend.
        """
        name = f"?filename={quote(filename)}" if filename else ""
        return f"{self._public_base_url}/api/v1/storage/{quote(key, safe='')}{name}"

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
