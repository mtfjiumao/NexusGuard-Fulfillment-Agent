from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import MutableRecord, TenantEntity


class SKU(TenantEntity, MutableRecord, Base):
    __tablename__ = "skus"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "sku_code"),
        sa.CheckConstraint("price_cents >= 0", name="nonnegative_price"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        sa.CheckConstraint("sale_status IN ('on_sale', 'off_sale')", name="sale_status"),
        sa.CheckConstraint("version > 0", name="positive_version"),
    )

    sku_code: Mapped[str] = mapped_column(sa.String(128))
    product_id: Mapped[str] = mapped_column(sa.String(128))
    title: Mapped[str] = mapped_column(sa.String(300))
    category: Mapped[str] = mapped_column(sa.String(64))
    specifications: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    price_cents: Mapped[int] = mapped_column(sa.BigInteger)
    currency: Mapped[str] = mapped_column(sa.String(3), server_default="CNY")
    sale_status: Mapped[str] = mapped_column(sa.String(20), server_default="on_sale")
    shipping_sla_id: Mapped[str | None] = mapped_column(sa.String(128))


class Inventory(TenantEntity, MutableRecord, Base):
    __tablename__ = "inventory"
    __table_args__ = (
        sa.ForeignKeyConstraint(["tenant_id", "sku_id"], ["skus.tenant_id", "skus.id"]),
        sa.UniqueConstraint("tenant_id", "sku_id", "warehouse_id"),
        sa.CheckConstraint("reserved >= 0 AND on_hand >= reserved", name="valid_stock"),
        sa.CheckConstraint("version > 0", name="positive_version"),
    )

    sku_id: Mapped[UUID] = mapped_column(sa.Uuid)
    warehouse_id: Mapped[str] = mapped_column(sa.String(128))
    on_hand: Mapped[int] = mapped_column(sa.Integer)
    reserved: Mapped[int] = mapped_column(sa.Integer, server_default="0")
