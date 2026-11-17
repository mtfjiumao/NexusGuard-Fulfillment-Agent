from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import CreatedAt, TenantEntity


class AuditLog(TenantEntity, CreatedAt, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'reviewer', 'system')", name="audit_actor_type"
        ),
        sa.Index(
            "ix_audit_resource_time", "tenant_id", "resource_type", "resource_id", "created_at"
        ),
    )

    actor_type: Mapped[str] = mapped_column(sa.String(20))
    actor_id: Mapped[str] = mapped_column(sa.String(128))
    action: Mapped[str] = mapped_column(sa.String(128))
    resource_type: Mapped[str] = mapped_column(sa.String(64))
    resource_id: Mapped[UUID] = mapped_column(sa.Uuid)
    from_state: Mapped[str | None] = mapped_column(sa.String(32))
    to_state: Mapped[str | None] = mapped_column(sa.String(32))
    reason_codes: Mapped[list] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    details: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    trace_id: Mapped[UUID] = mapped_column(sa.Uuid)


class OutboxEvent(TenantEntity, CreatedAt, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        sa.CheckConstraint("retry_count >= 0", name="nonnegative_retries"),
        sa.Index(
            "ix_outbox_unpublished",
            "available_at",
            "created_at",
            postgresql_where=sa.text("published_at IS NULL"),
        ),
    )

    aggregate_type: Mapped[str] = mapped_column(sa.String(64))
    aggregate_id: Mapped[UUID] = mapped_column(sa.Uuid)
    event_type: Mapped[str] = mapped_column(sa.String(128))
    payload: Mapped[dict] = mapped_column(JSONB)
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(sa.Integer, server_default="0")


class InboxEvent(CreatedAt, Base):
    __tablename__ = "inbox_events"

    tenant_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("tenants.id"), primary_key=True)
    consumer: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
