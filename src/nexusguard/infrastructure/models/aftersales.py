from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.domain.enums import Eligibility, RiskLevel
from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import (
    CreatedAt,
    MutableRecord,
    TenantEntity,
    enum_type,
)


class AfterSalesCase(TenantEntity, MutableRecord, Base):
    __tablename__ = "aftersales_cases"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id", "user_id", "currency"],
            ["orders.tenant_id", "orders.id", "orders.user_id", "orders.currency"],
        ),
        sa.UniqueConstraint("tenant_id", "case_number"),
        sa.UniqueConstraint("tenant_id", "id", "order_id", name="uq_case_order"),
        sa.UniqueConstraint("tenant_id", "id", "currency", name="uq_case_currency"),
        sa.CheckConstraint("requested_amount_cents > 0", name="positive_requested_amount"),
        sa.CheckConstraint(
            "approved_amount_cents IS NULL OR "
            "(approved_amount_cents > 0 AND approved_amount_cents <= requested_amount_cents)",
            name="valid_approved_amount",
        ),
        sa.CheckConstraint(
            "claim_type IN ('no_reason_return', 'damage_compensation', "
            "'delay_compensation', 'exchange')",
            name="claim_type",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'rejected', 'cancelled')", name="case_status"
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.Index("ix_cases_order", "tenant_id", "order_id"),
    )

    case_number: Mapped[str] = mapped_column(sa.String(128))
    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    user_id: Mapped[UUID] = mapped_column(sa.Uuid)
    currency: Mapped[str] = mapped_column(sa.String(3))
    claim_type: Mapped[str] = mapped_column(sa.String(32))
    requested_amount_cents: Mapped[int] = mapped_column(sa.BigInteger)
    approved_amount_cents: Mapped[int | None] = mapped_column(sa.BigInteger)
    reason: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(20), server_default="open")


class CaseItem(TenantEntity, CreatedAt, Base):
    __tablename__ = "case_items"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "order_id"],
            ["aftersales_cases.tenant_id", "aftersales_cases.id", "aftersales_cases.order_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_item_id", "order_id"],
            ["order_items.tenant_id", "order_items.id", "order_items.order_id"],
        ),
        sa.UniqueConstraint("tenant_id", "case_id", "order_item_id"),
        sa.CheckConstraint("quantity > 0 AND requested_amount_cents >= 0", name="valid_item_claim"),
    )

    case_id: Mapped[UUID] = mapped_column(sa.Uuid)
    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    order_item_id: Mapped[UUID] = mapped_column(sa.Uuid)
    quantity: Mapped[int] = mapped_column(sa.Integer)
    requested_amount_cents: Mapped[int] = mapped_column(sa.BigInteger)


class Evidence(TenantEntity, MutableRecord, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"], ["aftersales_cases.tenant_id", "aftersales_cases.id"]
        ),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'valid', 'invalid', 'needs_review')",
            name="evidence_status",
        ),
        sa.CheckConstraint(
            "verification_status = 'pending' OR "
            "(verified_at IS NOT NULL AND verification_source IS NOT NULL "
            "AND verification_reason IS NOT NULL)",
            name="verification_details",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.Index("ix_evidence_case", "tenant_id", "case_id"),
    )

    case_id: Mapped[UUID] = mapped_column(sa.Uuid)
    object_key: Mapped[str] = mapped_column(sa.String(512))
    content_hash: Mapped[str] = mapped_column(sa.String(64))
    verification_status: Mapped[str] = mapped_column(sa.String(20), server_default="pending")
    verification_source: Mapped[str | None] = mapped_column(sa.String(128))
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    verification_reason: Mapped[str | None] = mapped_column(sa.Text)


class PolicyDecision(TenantEntity, CreatedAt, Base):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "currency"],
            ["aftersales_cases.tenant_id", "aftersales_cases.id", "aftersales_cases.currency"],
        ),
        sa.UniqueConstraint("tenant_id", "case_id", "decision_version"),
        sa.UniqueConstraint("tenant_id", "id", "case_id", name="uq_decision_case"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "case_id",
            "proposed_refund_cents",
            "currency",
            "eligibility",
            name="uq_decision_refund_scope",
        ),
        sa.CheckConstraint(
            "eligibility = 'eligible' OR proposed_refund_cents IS NULL",
            name="no_ineligible_refund",
        ),
        sa.CheckConstraint("decision_version > 0", name="positive_decision_version"),
        sa.CheckConstraint("facts_hash ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        sa.CheckConstraint(
            "max_refund_cents >= 0 AND "
            "(proposed_refund_cents IS NULL OR "
            "(proposed_refund_cents > 0 AND proposed_refund_cents <= max_refund_cents))",
            name="valid_refund_proposal",
        ),
    )

    case_id: Mapped[UUID] = mapped_column(sa.Uuid)
    currency: Mapped[str] = mapped_column(sa.String(3))
    decision_version: Mapped[int] = mapped_column(sa.Integer)
    policy_version: Mapped[str] = mapped_column(sa.String(128))
    fact_snapshot: Mapped[dict] = mapped_column(JSONB)
    facts_hash: Mapped[str] = mapped_column(sa.String(64))
    matched_rule_ids: Mapped[list] = mapped_column(JSONB)
    eligibility: Mapped[Eligibility] = mapped_column(enum_type(Eligibility, "eligibility"))
    max_refund_cents: Mapped[int] = mapped_column(sa.BigInteger)
    proposed_refund_cents: Mapped[int | None] = mapped_column(sa.BigInteger)
    reason_codes: Mapped[list] = mapped_column(JSONB)


class RiskAssessment(TenantEntity, CreatedAt, Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "decision_id", "case_id"],
            ["policy_decisions.tenant_id", "policy_decisions.id", "policy_decisions.case_id"],
        ),
        sa.UniqueConstraint("tenant_id", "decision_id"),
        sa.UniqueConstraint("tenant_id", "id", "decision_id", "case_id", name="uq_risk_scope"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "decision_id",
            "case_id",
            "requires_approval",
            name="uq_risk_approval_scope",
        ),
        sa.CheckConstraint(
            "(risk_level = 'high' AND requires_approval) OR "
            "(risk_level = 'low' AND NOT requires_approval)",
            name="risk_approval_consistent",
        ),
    )

    case_id: Mapped[UUID] = mapped_column(sa.Uuid)
    decision_id: Mapped[UUID] = mapped_column(sa.Uuid)
    risk_rule_version: Mapped[str] = mapped_column(sa.String(128))
    risk_level: Mapped[RiskLevel] = mapped_column(enum_type(RiskLevel, "risk_level"))
    requires_approval: Mapped[bool] = mapped_column(sa.Boolean)
    triggered_rules: Mapped[list] = mapped_column(JSONB)
    profile_snapshot: Mapped[dict] = mapped_column(JSONB)
