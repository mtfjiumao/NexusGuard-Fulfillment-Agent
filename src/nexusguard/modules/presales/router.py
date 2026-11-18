"""Product detail for customer-facing catalog links."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.customer import CustomerIdentity, get_customer_identity
from nexusguard.infrastructure.database import get_session
from nexusguard.infrastructure.models.catalog import SKU
from nexusguard.modules.presales.catalog import ProductCard, on_sale, product_card
from nexusguard.modules.supervisor.repository import ReceptionRepository

router = APIRouter()


@router.get("/products/{sku_id}", response_model=ProductCard)
async def product_detail(
    sku_id: UUID,
    response: Response,
    identity: Annotated[CustomerIdentity, Depends(get_customer_identity)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    if not await ReceptionRepository(db, identity).active_user():
        raise HTTPException(403, "customer_unavailable")
    sku = await db.scalar(on_sale(identity.tenant_id).where(SKU.id == sku_id))
    if sku is None:
        raise HTTPException(404, "product_unavailable")
    response.headers["Cache-Control"] = "private, no-store"
    return product_card(sku)
