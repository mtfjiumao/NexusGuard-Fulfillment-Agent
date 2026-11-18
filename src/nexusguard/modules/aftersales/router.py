"""Staff endpoints for submitted damage materials."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.reviewer import ReviewerIdentity, get_damage_reviewer
from nexusguard.infrastructure.database import get_session
from nexusguard.modules.aftersales.damage_review import DamageReviews
from nexusguard.modules.aftersales.damage_schemas import (
    ApplicationStatus,
    DamageApplicationDecision,
    DamageReviewDecision,
    DamageReviewQueue,
    StaffDamageReviewView,
)

router = APIRouter()
Reviewer = Annotated[ReviewerIdentity, Depends(get_damage_reviewer)]
Database = Annotated[AsyncSession, Depends(get_session)]


@router.get("/damage-reviews", response_model=DamageReviewQueue)
async def damage_review_queue(
    response: Response,
    reviewer: Reviewer,
    db: Database,
    status: ApplicationStatus = "pending",
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    response.headers["Cache-Control"] = "no-store"
    return await DamageReviews(db, reviewer.tenant_id).queue(status, offset, limit)


@router.get("/damage-reviews/{review_id}", response_model=StaffDamageReviewView)
async def damage_review_detail(
    review_id: UUID,
    response: Response,
    reviewer: Reviewer,
    db: Database,
):
    response.headers["Cache-Control"] = "no-store"
    reviews = DamageReviews(db, reviewer.tenant_id)
    return await reviews.staff_view(await reviews.get(review_id))


@router.post(
    "/damage-reviews/{review_id}/items/{item_id}/decision",
    response_model=StaffDamageReviewView,
)
async def decide_damage_item(
    review_id: UUID,
    item_id: UUID,
    data: DamageReviewDecision,
    request: Request,
    response: Response,
    reviewer: Reviewer,
    db: Database,
):
    response.headers["Cache-Control"] = "no-store"
    async with db.begin():
        return await DamageReviews(db, reviewer.tenant_id).decide(
            review_id, item_id, data, reviewer, UUID(request.state.request_id)
        )


@router.post("/damage-reviews/{review_id}/resolution", response_model=StaffDamageReviewView)
async def resolve_damage_application(
    review_id: UUID,
    data: DamageApplicationDecision,
    request: Request,
    response: Response,
    reviewer: Reviewer,
    db: Database,
):
    response.headers["Cache-Control"] = "no-store"
    async with db.begin():
        return await DamageReviews(db, reviewer.tenant_id).resolve(
            review_id, data, reviewer, UUID(request.state.request_id)
        )


@router.get("/damage-reviews/{review_id}/items/{item_id}/photos/{attachment_id}")
async def damage_review_photo(
    review_id: UUID,
    item_id: UUID,
    attachment_id: UUID,
    reviewer: Reviewer,
    db: Database,
):
    photo = await DamageReviews(db, reviewer.tenant_id).photo(review_id, item_id, attachment_id)
    return Response(
        photo.content,
        media_type=photo.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'inline; filename="evidence"'
            + (".png" if photo.media_type == "image/png" else ".jpg"),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )
