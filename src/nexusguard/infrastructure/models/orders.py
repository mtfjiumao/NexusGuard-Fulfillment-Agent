from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import CreatedAt, MutableRecord, TenantEntity


class Order(TenantEntity, MutableRecord, Base):
    __tablename__ = "orders"
    __table_args__ = (
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
        sa.UniqueConstraint("tenant_id", "order_number"),
        sa.UniqueConstraint("tenant_id", "id", "user_id", name="uq_order_customer_scope"),
        sa.UniqueConstraint(
            "tenant_id", "id", "user_id", "currency", name="uq_order_owner_currency"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", "currency", "paid_amount_cents", name="uq_order_paid_scope"
        ),
        sa.CheckConstraint("paid_amount_cents >= 0", name="nonnegative_paid"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        sa.CheckConstraint("payment_status IN ('unpaid', 'paid', 'closed')", name="payment_status"),
        sa.CheckConstraint(
            "fulfillment_status IN ('unshipped', 'processing', 'shipped', "
            "'delivered', 'cancelled')",
            name="fulfillment_status",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.Index("ix_orders_user_created", "tenant_id", "user_id", "created_at"),
    )

    order_number: Mapped[str] = mapped_column(sa.String(128))
    user_id: Mapped[UUID] = mapped_column(sa.Uuid)
    payment_status: Mapped[str] = mapped_column(sa.String(20), server_default="unpaid")
    fulfillment_status: Mapped[str] = mapped_column(sa.String(20), server_default="unshipped")
    paid_amount_cents: Mapped[int] = mapped_column(sa.BigInteger)
    currency: Mapped[str] = mapped_column(sa.String(3), server_default="CNY")
    ordered_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    shipping_address_snapshot: Mapped[dict] = mapped_column(JSONB)


class OrderItem(TenantEntity, CreatedAt, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        sa.ForeignKeyConstraint(["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "sku_id"], ["skus.tenant_id", "skus.id"]),
        sa.UniqueConstraint("tenant_id", "id", "order_id", name="uq_order_item_scope"),
        sa.CheckConstraint("quantity > 0 AND paid_amount_cents >= 0", name="valid_item_amount"),
        sa.Index("ix_order_items_order", "tenant_id", "order_id"),
    )

    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    sku_id: Mapped[UUID] = mapped_column(sa.Uuid)
    quantity: Mapped[int] = mapped_column(sa.Integer)
    paid_amount_cents: Mapped[int] = mapped_column(sa.BigInteger)
    category_snapshot: Mapped[str] = mapped_column(sa.String(64))
    sku_snapshot: Mapped[dict] = mapped_column(JSONB)
    policy_reference: Mapped[str | None] = mapped_column(sa.String(128))


class Shipment(TenantEntity, MutableRecord, Base):
    __tablename__ = "shipments"
    __table_args__ = (
        sa.ForeignKeyConstraint(["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"]),
        sa.CheckConstraint(
            "status IN ('pending', 'shipped', 'in_transit', 'delivered', 'exception')",
            name="shipment_status",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.Index("ix_shipments_order", "tenant_id", "order_id"),
    )

    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    carrier: Mapped[str] = mapped_column(sa.String(64))
    tracking_no: Mapped[str | None] = mapped_column(sa.String(128))
    status: Mapped[str] = mapped_column(sa.String(20), server_default="pending")
    promised_delivery_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class LogisticsEvent(TenantEntity, CreatedAt, Base):
    __tablename__ = "logistics_events"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "shipment_id"], ["shipments.tenant_id", "shipments.id"]
        ),
        sa.UniqueConstraint("tenant_id", "source", "source_event_id"),
        sa.Index("ix_logistics_events_shipment_time", "tenant_id", "shipment_id", "occurred_at"),
    )

    shipment_id: Mapped[UUID] = mapped_column(sa.Uuid)
    source: Mapped[str] = mapped_column(sa.String(64))
    source_event_id: Mapped[str] = mapped_column(sa.String(128))
    event_type: Mapped[str] = mapped_column(sa.String(64))
    occurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    payload: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
