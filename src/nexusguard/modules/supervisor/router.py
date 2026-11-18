"""Authenticated reception endpoints; inference runs in the separate worker."""

from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.customer import CustomerIdentity, get_customer_identity
from nexusguard.infrastructure.database import get_session
from nexusguard.modules.aftersales.damage_collection import MAX_IMAGE_BYTES
from nexusguard.modules.aftersales.damage_schemas import (
    DamageItemAction,
    DamageReviewCommand,
    DamageReviewSubmit,
)
from nexusguard.modules.supervisor.reception_service import ReceptionService
from nexusguard.modules.supervisor.schemas import (
    ActionRequest,
    CreateReception,
    MessagePage,
    MessageRequest,
    OrderPage,
    ReceptionView,
)

router = APIRouter(prefix="/reception")


def get_service(
    request: Request,
    response: Response,
    identity: Annotated[CustomerIdentity, Depends(get_customer_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReceptionService:
    response.headers["Cache-Control"] = "no-store"
    return ReceptionService(
        session,
        identity,
        days=request.app.state.settings.reception_recent_order_days,
        trace_id=UUID(request.state.request_id),
    )


Service = Annotated[ReceptionService, Depends(get_service)]


@router.post("/sessions", response_model=ReceptionView, status_code=201)
async def create_reception(data: CreateReception, service: Service):
    return await service.create(data)


@router.get("/sessions/{session_id}", response_model=ReceptionView)
async def get_reception(session_id: UUID, service: Service):
    return await service.get(session_id)


@router.post("/sessions/{session_id}/actions", response_model=ReceptionView)
async def reception_action(session_id: UUID, data: ActionRequest, service: Service):
    return await service.act(session_id, data)


@router.get("/sessions/{session_id}/orders", response_model=OrderPage)
async def reception_orders(
    session_id: UUID,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    return await service.orders(session_id, limit, offset)


@router.post("/sessions/{session_id}/messages", response_model=ReceptionView)
async def reception_message(session_id: UUID, data: MessageRequest, service: Service):
    try:
        return await service.message(session_id, data)
    except IntegrityError as error:
        # Concurrent reuse of one message ID across sessions must not leak SQL diagnostics.
        raise HTTPException(409, "message_write_conflict") from error


@router.get("/sessions/{session_id}/messages", response_model=MessagePage)
async def reception_history(
    session_id: UUID,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    before: UUID | None = None,
):
    return await service.history(session_id, limit, before)


@router.post("/sessions/{session_id}/damage/{draft_id}/items", response_model=ReceptionView)
async def damage_item_action(
    session_id: UUID,
    draft_id: UUID,
    data: DamageItemAction,
    service: Service,
):
    return await service.damage_item(session_id, draft_id, data)


@router.post(
    "/sessions/{session_id}/damage/{draft_id}/items/{claim_id}/photos/{upload_id}",
    response_model=ReceptionView,
)
async def damage_photo_upload(
    session_id: UUID,
    draft_id: UUID,
    claim_id: UUID,
    upload_id: UUID,
    request: Request,
    service: Service,
    expected_version: Annotated[int, Query(ge=1)],
):
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(415, "damage_image_type_unsupported")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "damage_image_too_large")
        content.extend(chunk)
    data = bytes(content)
    # A file signature is a basic format check, never proof of damage or authenticity.
    valid_signature = (
        media_type == "image/png"
        and data.startswith(b"\x89PNG\r\n\x1a\n")
        or media_type == "image/jpeg"
        and data.startswith(b"\xff\xd8\xff")
    )
    if not valid_signature:
        raise HTTPException(422, "damage_image_signature_invalid")
    try:
        return await service.damage_photo(
            session_id,
            draft_id,
            claim_id,
            upload_id,
            data,
            media_type,
            sha256(data).hexdigest(),
            expected_version,
        )
    except IntegrityError as error:
        raise HTTPException(409, "damage_upload_conflict") from error


@router.post("/sessions/{session_id}/damage/{draft_id}/reviews", response_model=ReceptionView)
async def submit_damage_review(
    session_id: UUID, draft_id: UUID, data: DamageReviewSubmit, service: Service
):
    try:
        return await service.damage_review(
            session_id, draft_id, data.expected_version, submission=data
        )
    except IntegrityError as error:
        raise HTTPException(409, "damage_review_submission_conflict") from error


@router.post(
    "/sessions/{session_id}/damage/{draft_id}/reviews/{review_id}/withdraw",
    response_model=ReceptionView,
)
async def withdraw_damage_review(
    session_id: UUID,
    draft_id: UUID,
    review_id: UUID,
    data: DamageReviewCommand,
    service: Service,
):
    return await service.damage_review(
        session_id, draft_id, data.expected_version, withdraw_id=review_id
    )


@router.delete(
    "/sessions/{session_id}/damage/{draft_id}/photos/{attachment_id}",
    response_model=ReceptionView,
)
async def remove_damage_photo(
    session_id: UUID,
    draft_id: UUID,
    attachment_id: UUID,
    expected_version: Annotated[int, Query(ge=1)],
    service: Service,
):
    return await service.damage_review(
        session_id, draft_id, expected_version, remove_photo_id=attachment_id
    )
