"""Object storage. One interface, local and S3-compatible implementations."""

from __future__ import annotations

from docflow.config import StorageSettings, get_settings
from docflow.storage.base import StorageBackend, StoredObject, build_key, organization_prefix

_backend: StorageBackend | None = None


def build_storage(settings: StorageSettings, *, public_base_url: str = "") -> StorageBackend:
    if settings.backend == "s3":
        from docflow.storage.s3 import S3Storage

        return S3Storage(settings)

    from docflow.storage.local import LocalStorage

    return LocalStorage(settings.local_root, public_base_url=public_base_url)


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        settings = get_settings()
        _backend = build_storage(settings.storage, public_base_url=settings.public_base_url)
    return _backend


def set_storage(backend: StorageBackend | None) -> None:
    """Override the singleton. Tests and the evaluation harness use this."""
    global _backend
    _backend = backend


__all__ = [
    "StorageBackend",
    "StoredObject",
    "build_key",
    "build_storage",
    "get_storage",
    "organization_prefix",
    "set_storage",
]
