"""Shared persistence types; tenant identity must still come from authentication."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


def enum_type(enum: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class TenantEntity:
    tenant_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid4, server_default=sa.text("gen_random_uuid()")
    )


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class MutableRecord(CreatedAt):
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1, server_default="1")

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:
        # Core SQL must also increment version and use CAS or row locks.
        return {"version_id_col": cls.version}
