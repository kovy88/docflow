"""S3-compatible object storage.

Works against AWS S3, Cloudflare R2, MinIO and Supabase Storage's S3 endpoint —
one implementation, four deployment targets, because they share a protocol. That
is the concrete payoff of programming against S3's API rather than a vendor SDK.

boto3 is synchronous, so every call runs in a thread executor. An async S3 client
(aioboto3) exists, but it adds a dependency and a second auth code path to save
thread-pool overhead that is irrelevant next to the network round trip.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any, BinaryIO

import structlog

from docflow.config import StorageSettings
from docflow.domain.errors import StorageError, StorageObjectNotFoundError
from docflow.storage.base import StorageBackend, StoredObject

logger = structlog.get_logger(__name__)


class S3Storage(StorageBackend):
    def __init__(self, settings: StorageSettings) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = settings.bucket
        self._ttl = settings.presign_ttl_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            region_name=settings.region,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                # Path-style addressing works everywhere; virtual-host style needs
                # DNS setup that MinIO and some R2 configurations do not provide.
                s3={"addressing_style": "path"},
            ),
        )

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        def _upload() -> dict[str, Any]:
            return self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                # Server-side encryption at rest. Cheap, and required by most
                # customers' security questionnaires.
                ServerSideEncryption="AES256",
            )

        try:
            response = await asyncio.to_thread(_upload)
        except Exception as exc:  # noqa: BLE001
            logger.error("storage.put_failed", key=key, error=type(exc).__name__)
            raise StorageError("Could not upload the object") from exc

        return StoredObject(
            key=key,
            size_bytes=len(data),
            content_type=content_type,
            etag=(response.get("ETag") or "").strip('"') or None,
        )

    async def get(self, key: str) -> bytes:
        def _download() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()

        try:
            return await asyncio.to_thread(_download)
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                raise StorageObjectNotFoundError(f"Object not found: {key}") from exc
            raise StorageError("Could not download the object") from exc

    async def open(self, key: str) -> BinaryIO:
        return io.BytesIO(await self.get(key))

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            self._client.delete_object(Bucket=self._bucket, Key=key)

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:  # noqa: BLE001
            raise StorageError("Could not delete the object") from exc

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:  # noqa: BLE001
                if _is_not_found(exc):
                    return False
                raise
            return True

        try:
            return await asyncio.to_thread(_head)
        except Exception as exc:  # noqa: BLE001
            raise StorageError("Could not check object existence") from exc

    async def presigned_url(
        self, key: str, *, expires_in: int | None = None, filename: str | None = None
    ) -> str:
        ttl = expires_in or self._ttl

        def _sign() -> str:
            params: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
            if filename:
                # Force download rather than inline rendering. A PDF or SVG served
                # inline from a storage origin is a stored-XSS vector; attachment
                # disposition removes the class of problem entirely.
                params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
            return self._client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=ttl
            )

        try:
            return await asyncio.to_thread(_sign)
        except Exception as exc:  # noqa: BLE001
            raise StorageError("Could not generate a download URL") from exc

    async def health_check(self) -> bool:
        def _head_bucket() -> bool:
            self._client.head_bucket(Bucket=self._bucket)
            return True

        try:
            return await asyncio.to_thread(_head_bucket)
        except Exception:  # noqa: BLE001
            logger.warning("storage.health_check_failed", bucket=self._bucket)
            return False


def _is_not_found(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in {"404", "NoSuchKey", "NotFound"}
