"""Information collection drafts, deliberately separate from monetary after-sales cases."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import CreatedAt, MutableRecord, TenantEntity


class DamageDraft(TenantEntity, MutableRecord, Base):
    __tablename__ = "damage_drafts"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["reception_sessions.tenant_id", "reception_sessions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id", "user_id"],
            ["orders.tenant_id", "orders.id", "orders.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "focused_item_id", "order_id"],
            ["order_items.tenant_id", "order_items.id", "order_items.order_id"],
        ),
        sa.UniqueConstraint("tenant_id", "session_id", "order_id"),
        sa.UniqueConstraint("tenant_id", "id", "order_id", name="uq_damage_draft_order"),
        sa.CheckConstraint("status IN ('collecting', 'ready_for_review')", name="draft_status"),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.CheckConstraint("jsonb_typeof(pending_claims) = 'array'", name="pending_claims_array"),
    )

    pending_claims: Mapped[list] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    session_id: Mapped[UUID] = mapped_column(sa.Uuid)
    user_id: Mapped[UUID] = mapped_column(sa.Uuid)
    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    focused_item_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    status: Mapped[str] = mapped_column(sa.String(24), server_default="collecting")


class DamageDraftItem(TenantEntity, MutableRecord, Base):
    __tablename__ = "damage_draft_items"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id", "order_id"],
            ["damage_drafts.tenant_id", "damage_drafts.id", "damage_drafts.order_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_item_id", "order_id"],
            ["order_items.tenant_id", "order_items.id", "order_items.order_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "last_message_id"],
            ["reception_messages.tenant_id", "reception_messages.id"],
        ),
        sa.UniqueConstraint("tenant_id", "draft_id", "order_item_id"),
        sa.UniqueConstraint("tenant_id", "id", "draft_id", name="uq_damage_item_draft"),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="positive_quantity"),
        sa.CheckConstraint(
            "description IS NULL OR length(btrim(description)) BETWEEN 1 AND 500",
            name="description_length",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
    )

    draft_id: Mapped[UUID] = mapped_column(sa.Uuid)
    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    order_item_id: Mapped[UUID] = mapped_column(sa.Uuid)
    included: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.true())
    quantity: Mapped[int | None] = mapped_column(sa.Integer)
    description: Mapped[str | None] = mapped_column(sa.String(500))
    last_message_id: Mapped[UUID | None] = mapped_column(sa.Uuid)


class DamageAttachment(TenantEntity, CreatedAt, Base):
    __tablename__ = "damage_attachments"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_item_id", "draft_id"],
            [
                "damage_draft_items.tenant_id",
                "damage_draft_items.id",
                "damage_draft_items.draft_id",
            ],
        ),
        sa.UniqueConstraint("tenant_id", "draft_item_id", "content_hash"),
        sa.Index("ix_damage_attachments_draft", "tenant_id", "draft_id"),
        sa.CheckConstraint("media_type IN ('image/jpeg', 'image/png')", name="image_type"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        sa.CheckConstraint("octet_length(content) BETWEEN 1 AND 5242880", name="image_size"),
        sa.CheckConstraint("verification_status = 'pending'", name="unreviewed_evidence"),
    )

    draft_id: Mapped[UUID] = mapped_column(sa.Uuid)
    draft_item_id: Mapped[UUID] = mapped_column(sa.Uuid)
    media_type: Mapped[str] = mapped_column(sa.String(32))
    content_hash: Mapped[str] = mapped_column(sa.String(64))
    content: Mapped[bytes] = mapped_column(sa.LargeBinary)
    verification_status: Mapped[str] = mapped_column(sa.String(20), server_default="pending")
