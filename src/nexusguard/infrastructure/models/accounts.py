from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import CreatedAt, MutableRecord, TenantEntity


class Tenant(CreatedAt, Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid4, server_default=sa.text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(sa.String(120))


class User(TenantEntity, MutableRecord, Base):
    __tablename__ = "users"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "external_id"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="user_status"),
        sa.CheckConstraint("version > 0", name="positive_version"),
    )

    external_id: Mapped[str] = mapped_column(sa.String(128))
    status: Mapped[str] = mapped_column(sa.String(20), server_default="active")


class UserRiskProfile(TenantEntity, CreatedAt, Base):
    __tablename__ = "user_risk_profiles"
    __table_args__ = (
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
        sa.UniqueConstraint("tenant_id", "user_id", "calculation_version", "window_end"),
        sa.CheckConstraint(
            "return_count >= 0 AND eligible_order_count >= return_count", name="valid_counts"
        ),
        sa.CheckConstraint("window_start < window_end", name="valid_window"),
        sa.Index("ix_risk_profile_user_time", "tenant_id", "user_id", "calculated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(sa.Uuid)
    return_count: Mapped[int] = mapped_column(sa.Integer)
    eligible_order_count: Mapped[int] = mapped_column(sa.Integer)
    window_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    calculation_version: Mapped[str] = mapped_column(sa.String(64))
    calculated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
