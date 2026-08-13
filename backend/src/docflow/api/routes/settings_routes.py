"""Organization settings: API keys and webhook endpoints."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, status

from docflow.api.deps import CurrentPrincipal, RequireAdmin, SessionDep
from docflow.api.schemas import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreatedApiKeyResponse,
    CreateWebhookRequest,
    WebhookResponse,
    WebhookSecretResponse,
)
from docflow.db.repositories import ApiKeyRepository, WebhookRepository
from docflow.domain.errors import ResourceNotFoundError
from docflow.security.tokens import generate_api_key
from docflow.services.webhook_service import WebhookService

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/api-keys", response_model=list[ApiKeyResponse], summary="List API keys")
async def list_api_keys(
    session: SessionDep, principal: CurrentPrincipal
) -> list[ApiKeyResponse]:
    keys = await ApiKeyRepository(session).list_for_org(principal.organization_id)
    return [ApiKeyResponse.model_validate(k) for k in keys]


@router.post(
    "/api-keys",
    response_model=CreatedApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
    description=(
        "Returns the full key **once**. Only a SHA-256 digest is stored, so a lost "
        "key cannot be recovered — create a new one and revoke the old."
    ),
)
async def create_api_key(
    payload: CreateApiKeyRequest,
    session: SessionDep,
    principal: RequireAdmin,
) -> CreatedApiKeyResponse:
    generated = generate_api_key()
    expires_at = (
        dt.datetime.now(dt.UTC) + dt.timedelta(days=payload.expires_in_days)
        if payload.expires_in_days
        else None
    )
    key = await ApiKeyRepository(session).create(
        organization_id=principal.organization_id,
        name=payload.name,
        prefix=generated.prefix,
        hashed_key=generated.hashed,
        created_by_id=principal.user_id,
        expires_at=expires_at,
    )
    return CreatedApiKeyResponse(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        created_at=key.created_at,
        last_used_at=None,
        expires_at=key.expires_at,
        revoked_at=None,
        api_key=generated.plaintext,
    )


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
)
async def revoke_api_key(
    key_id: uuid.UUID, session: SessionDep, principal: RequireAdmin
) -> None:
    revoked = await ApiKeyRepository(session).revoke(key_id, principal.organization_id)
    if not revoked:
        raise ResourceNotFoundError("API key not found or already revoked")


@router.get("/webhooks", response_model=list[WebhookResponse], summary="List webhooks")
async def list_webhooks(
    session: SessionDep, principal: CurrentPrincipal
) -> list[WebhookResponse]:
    endpoints = await WebhookRepository(session, principal.organization_id).list_all()
    return [WebhookResponse.model_validate(e) for e in endpoints]


@router.post(
    "/webhooks",
    response_model=WebhookSecretResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a webhook endpoint",
    description=(
        "The signing secret is returned once. Verify deliveries with "
        "`HMAC-SHA256({timestamp}.{body})` — see docs/API.md for a reference "
        "implementation."
    ),
)
async def create_webhook(
    payload: CreateWebhookRequest,
    session: SessionDep,
    principal: RequireAdmin,
) -> WebhookSecretResponse:
    service = WebhookService(session, organization_id=principal.organization_id)
    endpoint, secret = await service.register(
        url=payload.url, description=payload.description, events=payload.events
    )
    return WebhookSecretResponse(
        id=endpoint.id,
        url=endpoint.url,
        description=endpoint.description,
        events=endpoint.events,
        is_active=endpoint.is_active,
        last_success_at=None,
        last_failure_at=None,
        consecutive_failures=0,
        secret=secret,
    )


@router.delete(
    "/webhooks/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook endpoint",
)
async def delete_webhook(
    endpoint_id: uuid.UUID, session: SessionDep, principal: RequireAdmin
) -> None:
    deleted = await WebhookRepository(session, principal.organization_id).delete(
        endpoint_id
    )
    if not deleted:
        raise ResourceNotFoundError("Webhook endpoint not found")
