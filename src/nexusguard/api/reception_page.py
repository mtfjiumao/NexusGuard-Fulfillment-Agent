"""Serve the Vite build; customer authorization remains in the reception APIs."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from nexusguard.api.reviewer import (
    ReviewerIdentity,
    get_damage_reviewer,
    get_logistics_reviewer,
    get_refund_supervisor,
)

WEB_DIRECTORY = Path(__file__).resolve().parents[1] / "web" / "dist"
router = APIRouter()


@router.get("/customer-service", include_in_schema=False)
async def reception_page() -> FileResponse:
    return frontend_page()


@router.get("/products/{sku_id}", include_in_schema=False)
async def product_page(sku_id: UUID) -> FileResponse:
    return frontend_page()


@router.get("/damage-review", include_in_schema=False)
async def damage_review_page(
    reviewer: Annotated[ReviewerIdentity, Depends(get_damage_reviewer)],
) -> FileResponse:
    return frontend_page()


@router.get("/refund-approvals", include_in_schema=False)
async def refund_approvals_page(
    reviewer: Annotated[ReviewerIdentity, Depends(get_refund_supervisor)],
) -> FileResponse:
    return frontend_page()


@router.get("/logistics-review", include_in_schema=False)
async def logistics_review_page(
    reviewer: Annotated[ReviewerIdentity, Depends(get_logistics_reviewer)],
) -> FileResponse:
    return frontend_page()


def frontend_page() -> FileResponse:
    index = WEB_DIRECTORY / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="frontend_build_required")
    return FileResponse(
        index,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                "form-action 'self'; frame-ancestors 'self'"
            ),
        },
    )
