"""Persistent conversation reception, separate from after-sales execution workflows."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import CreatedAt, MutableRecord, TenantEntity


class ReceptionSession(TenantEntity, MutableRecord, Base):
    __tablename__ = "reception_sessions"
    __table_args__ = (
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_order_id", "user_id"],
            ["orders.tenant_id", "orders.id", "orders.user_id"],
            name="fk_reception_candidate_owner",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bound_order_id", "user_id"],
            ["orders.tenant_id", "orders.id", "orders.user_id"],
            name="fk_reception_bound_owner",
        ),
        sa.ForeignKeyConstraint(["tenant_id", "sku_id"], ["skus.tenant_id", "skus.id"]),
        sa.CheckConstraint("entry_point IN ('order', 'store', 'other')", name="entry_point"),
        sa.CheckConstraint(
            "state IN ('confirming', 'alternatives', 'selecting', "
            "'general', 'bound', 'clarifying')",
            name="reception_state",
        ),
        sa.CheckConstraint(
            "(state = 'confirming' AND candidate_order_id IS NOT NULL "
            "AND bound_order_id IS NULL) OR "
            "(state = 'bound' AND bound_order_id IS NOT NULL AND candidate_order_id IS NULL) OR "
            "(state IN ('alternatives', 'selecting', 'general', 'clarifying') "
            "AND candidate_order_id IS NULL AND bound_order_id IS NULL)",
            name="order_context",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.Index("ix_reception_user", "tenant_id", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(sa.Uuid)
    entry_point: Mapped[str] = mapped_column(sa.String(20))
    state: Mapped[str] = mapped_column(sa.String(20))
    candidate_order_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    bound_order_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    sku_id: Mapped[UUID | None] = mapped_column(sa.Uuid)


class ReceptionMessage(TenantEntity, CreatedAt, Base):
    __tablename__ = "reception_messages"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["reception_sessions.tenant_id", "reception_sessions.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"]),
        sa.CheckConstraint("length(btrim(text)) BETWEEN 1 AND 4000", name="message_length"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'needs_order', 'ready_for_agent', "
            "'completed', 'failed', 'superseded')",
            name="message_status",
        ),
        sa.CheckConstraint("status != 'needs_order' OR order_id IS NULL", name="unresolved_order"),
        sa.Index("ix_reception_messages_session", "tenant_id", "session_id", "created_at"),
        sa.Index(
            "uq_reception_pending_message",
            "tenant_id",
            "session_id",
            unique=True,
            postgresql_where=sa.text("status = 'needs_order'"),
        ),
    )

    session_id: Mapped[UUID] = mapped_column(sa.Uuid)
    text: Mapped[str] = mapped_column(sa.Text)
    order_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    status: Mapped[str] = mapped_column(sa.String(24))

    intent: Mapped[dict | None] = mapped_column(JSONB)
    context: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    worker_token: Mapped[UUID | None] = mapped_column(sa.Uuid)


class ReceptionReply(TenantEntity, CreatedAt, Base):
    __tablename__ = "reception_replies"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "message_id"], ["reception_messages.tenant_id", "reception_messages.id"]
        ),
        sa.UniqueConstraint("tenant_id", "message_id", "phase"),
        sa.CheckConstraint("phase IN ('clarification', 'answer', 'error')", name="reply_phase"),
        sa.CheckConstraint("length(btrim(text)) BETWEEN 1 AND 12000", name="reply_length"),
    )

    message_id: Mapped[UUID] = mapped_column(sa.Uuid)
    phase: Mapped[str] = mapped_column(sa.String(20))
    text: Mapped[str] = mapped_column(sa.Text)
    facts: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
