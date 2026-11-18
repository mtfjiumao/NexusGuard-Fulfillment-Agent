from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import CreatedAt, TenantEntity


class DamageReview(TenantEntity, CreatedAt, Base):
    __tablename__ = "damage_reviews"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id"], ["damage_drafts.tenant_id", "damage_drafts.id"]
        ),
        sa.UniqueConstraint("tenant_id", "draft_id", "revision"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "refund_case_id"], ["aftersales_cases.tenant_id", "aftersales_cases.id"]
        ),
        sa.UniqueConstraint("tenant_id", "refund_case_id"),
        sa.CheckConstraint("revision > 0", name="positive_revision"),
        sa.CheckConstraint(
            "status IN ('pending', 'needs_supplement', 'rejected', 'passed', 'withdrawn')",
            name="review_status",
        ),
        sa.CheckConstraint(
            "requested_resolution IS NULL OR length(btrim(requested_resolution)) BETWEEN 1 AND 500",
            name="requested_resolution",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR (outcome IN ('approved', 'needs_supplement', 'rejected')"
            " AND requested_resolution IS NOT NULL"
            " AND status IN ('passed', 'needs_supplement', 'rejected')"
            " AND resolved_at IS NOT NULL AND resolver_id IS NOT NULL"
            " AND resolution IS NOT NULL AND resolution_reason IS NOT NULL"
            " AND ((refund_cents IS NULL AND refund_currency IS NULL)"
            " OR (outcome = 'approved' AND refund_cents > 0 AND refund_currency IS NOT NULL"
            " AND refund_currency ~ '^[A-Z]{3}$')))",
            name="resolution_fields",
        ),
        sa.Index("ix_damage_review_queue", "tenant_id", "status", "created_at", "id"),
        sa.Index(
            "uq_damage_review_pending",
            "tenant_id",
            "draft_id",
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        ),
    )

    draft_id: Mapped[UUID] = mapped_column(sa.Uuid)
    revision: Mapped[int] = mapped_column(sa.Integer)
    order_number: Mapped[str] = mapped_column(sa.String(128))
    status: Mapped[str] = mapped_column(sa.String(24), server_default="pending")
    notice: Mapped[str | None] = mapped_column(sa.Text)
    requested_resolution: Mapped[str | None] = mapped_column(sa.String(500))
    outcome: Mapped[str | None] = mapped_column(sa.String(24))
    resolution: Mapped[str | None] = mapped_column(sa.String(500))
    resolution_reason: Mapped[str | None] = mapped_column(sa.String(500))
    refund_cents: Mapped[int | None] = mapped_column(sa.BigInteger)
    refund_currency: Mapped[str | None] = mapped_column(sa.String(3))
    resolver_id: Mapped[str | None] = mapped_column(sa.String(128))
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    refund_case_id: Mapped[UUID | None] = mapped_column(sa.Uuid)

    @property
    def locked_for_processing(self) -> bool:
        return self.status == "pending" or (
            self.requested_resolution is not None
            and self.outcome is None
            and self.status != "withdrawn"
        )


class DamageReviewItem(TenantEntity, Base):
    __tablename__ = "damage_review_items"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "review_id"], ["damage_reviews.tenant_id", "damage_reviews.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_item_id"],
            ["damage_draft_items.tenant_id", "damage_draft_items.id"],
        ),
        sa.UniqueConstraint("tenant_id", "review_id", "draft_item_id"),
        sa.CheckConstraint(
            "damaged_quantity BETWEEN 1 AND purchased_quantity", name="valid_quantity"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(photos) = 'array' AND photos <> '[]'::jsonb", name="nonempty_photos"
        ),
        sa.CheckConstraint(
            "(decision IS NULL AND reason IS NULL AND reviewer_id IS NULL AND reviewed_at IS NULL)"
            " OR (decision IS NOT NULL AND decision IN ('passed', 'needs_supplement', 'rejected')"
            " AND length(btrim(reason)) > 0 AND reason IS NOT NULL"
            " AND length(btrim(reviewer_id)) > 0 AND reviewer_id IS NOT NULL"
            " AND reviewed_at IS NOT NULL)",
            name="decision_fields",
        ),
    )

    review_id: Mapped[UUID] = mapped_column(sa.Uuid)
    draft_item_id: Mapped[UUID] = mapped_column(sa.Uuid)
    title: Mapped[str] = mapped_column(sa.String(450))
    purchased_quantity: Mapped[int] = mapped_column(sa.Integer)
    damaged_quantity: Mapped[int] = mapped_column(sa.Integer)
    description: Mapped[str] = mapped_column(sa.String(500))
    photos: Mapped[list[dict]] = mapped_column(JSONB)
    decision: Mapped[str | None] = mapped_column(sa.String(24))
    reason: Mapped[str | None] = mapped_column(sa.String(500))
    reviewer_id: Mapped[str | None] = mapped_column(sa.String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
