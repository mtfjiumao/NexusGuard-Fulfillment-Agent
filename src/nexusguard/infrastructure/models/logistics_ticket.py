from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import CreatedAt, TenantEntity

COMPENSATION_FIELDS = (
    "kind IN ('investigation', 'delay_compensation')"
    " AND (refund_case_id IS NULL OR (kind = 'delay_compensation' AND resolved_at IS NOT NULL))"
)


class LogisticsTicket(TenantEntity, CreatedAt, Base):
    __tablename__ = "logistics_tickets"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id", "user_id"],
            ["orders.tenant_id", "orders.id", "orders.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["reception_sessions.tenant_id", "reception_sessions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "refund_case_id"], ["aftersales_cases.tenant_id", "aftersales_cases.id"]
        ),
        sa.UniqueConstraint("tenant_id", "refund_case_id"),
        sa.CheckConstraint(COMPENSATION_FIELDS, name="compensation_fields"),
        sa.CheckConstraint(
            "(resolved_at IS NULL AND reviewer_id IS NULL AND result IS NULL"
            " AND notified_at IS NULL)"
            " OR (resolved_at IS NOT NULL AND reviewer_id IS NOT NULL AND result IS NOT NULL"
            " AND length(btrim(result)) BETWEEN 1 AND 1000)",
            name="resolution_fields",
        ),
        sa.Index(
            "uq_logistics_open_order_kind",
            "tenant_id",
            "order_id",
            "kind",
            unique=True,
            postgresql_where=sa.text("resolved_at IS NULL"),
        ),
        sa.Index("ix_logistics_ticket_session", "tenant_id", "session_id", "created_at"),
        sa.Index("ix_logistics_ticket_queue", "tenant_id", "resolved_at", "created_at"),
    )

    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    kind: Mapped[str] = mapped_column(sa.String(24), server_default="investigation")
    refund_case_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    user_id: Mapped[UUID] = mapped_column(sa.Uuid)
    session_id: Mapped[UUID] = mapped_column(sa.Uuid)
    order_number: Mapped[str] = mapped_column(sa.String(128))
    facts: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[str | None] = mapped_column(sa.String(1000))
    reviewer_id: Mapped[str | None] = mapped_column(sa.String(128))
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
