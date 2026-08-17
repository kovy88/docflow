"""Serves objects for the local storage backend's "presigned" URLs.

Exists only because `LocalStorage.presigned_url()` (`storage/local.py`) has
nothing real to point at — there is no object store to presign against. It
mints its own short-lived HMAC-signed URL instead and this route verifies
that signature. See `storage/local.py::_signature` for why this route does
not use the normal bearer-auth dependency every other route uses.

Unreachable in production: `Settings.validate_for_environment()` refuses to
boot with `DOCFLOW_STORAGE_BACKEND=local` outside local/staging, and the S3
backend never generates a URL under this path.
"""

from __future__ import annotations

import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response

from docflow.api.deps import StorageDep
from docflow.domain.errors import AuthenticationError
from docflow.storage.local import LocalStorage

router = APIRouter(tags=["storage"])


@router.get("/storage/{key:path}", summary="Download a locally-stored object")
async def download(
    key: str,
    storage: StorageDep,
    expires: int = Query(...),
    sig: str = Query(...),
    filename: str | None = Query(None),
) -> Response:
    # Reachable only when the local backend is active — `presigned_url()` never
    # points here otherwise (see storage/local.py), and the production boot
    # gate refuses `DOCFLOW_STORAGE_BACKEND=local` outside local/staging.
    if not isinstance(storage, LocalStorage):
        raise AuthenticationError("Invalid download link")
    storage.verify_presigned(key, expires=expires, signature=sig)

    data = await storage.get(key)
    content_type = mimetypes.guess_type(filename or key)[0] or "application/octet-stream"

    headers = {}
    if filename:
        # Same attachment-disposition reasoning as the S3 backend
        # (`storage/s3.py`): never render a stored document inline from the
        # API origin.
        headers["Content-Disposition"] = f'attachment; filename="{quote(filename)}"'

    return Response(content=data, media_type=content_type, headers=headers)
