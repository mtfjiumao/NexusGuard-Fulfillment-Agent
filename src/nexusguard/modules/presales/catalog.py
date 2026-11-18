"""Tenant-scoped catalog reads and customer-facing product cards."""

import re
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.infrastructure.models.catalog import SKU


class ProductCard(BaseModel):
    sku_id: UUID
    title: str
    specification: str | None
    price_cents: int
    currency: str
    url: str
    image_url: str | None


def product_card(sku: SKU) -> ProductCard:
    specification = sku.specifications.get("规格")
    image = sku.specifications.get("image_path")
    image_url = (
        image
        if isinstance(image, str)
        and re.fullmatch(r"/assets/reception/catalog/[\w-]{1,100}\.(png|jpe?g|webp|avif)", image)
        else None
    )
    return ProductCard(
        sku_id=sku.id,
        title=sku.title,
        specification=specification[:200] if isinstance(specification, str) else None,
        price_cents=sku.price_cents,
        currency=sku.currency,
        url=f"/products/{sku.id}",
        image_url=image_url,
    )


def on_sale(tenant_id: UUID) -> Select[tuple[SKU]]:
    return select(SKU).where(SKU.tenant_id == tenant_id, SKU.sale_status == "on_sale")


async def search_products(db: AsyncSession, tenant_id: UUID, keyword: str) -> list[ProductCard]:
    matches = await db.scalars(
        on_sale(tenant_id)
        .where(
            or_(
                SKU.title.icontains(keyword, autoescape=True),
                func.lower(SKU.sku_code) == keyword.lower(),
            )
        )
        .order_by(case((SKU.title == keyword, 0), else_=1), SKU.title, SKU.id)
        .limit(3)
    )
    return [product_card(sku) for sku in matches]
