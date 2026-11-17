from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.domain.enums import ApprovalStatus, Eligibility, FinancialActionStatus
from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import MutableRecord, TenantEntity, enum_type


class OrderRefundBalance(TenantEntity, MutableRecord, Base):
    __tablename__ = "order_refund_balances"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id", "currency", "paid_cents"],
            ["orders.tenant_id", "orders.id", "orders.currency", "orders.paid_amount_cents"],
        ),
        sa.UniqueConstraint("tenant_id", "order_id"),
        sa.CheckConstraint(
            "paid_cents >= 0 AND refunded_cents >= 0 AND reserved_cents >= 0 "
            "AND refunded_cents <= paid_cents AND reserved_cents <= paid_cents - refunded_cents",
            name="refund_budget",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
    )

    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    currency: Mapped[str] = mapped_column(sa.String(3))
    paid_cents: Mapped[int] = mapped_column(sa.BigInteger)
    refunded_cents: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    reserved_cents: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")


class FinancialAction(TenantEntity, MutableRecord, Base):
    __tablename__ = "financial_actions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "order_id"],
            ["aftersales_cases.tenant_id", "aftersales_cases.id", "aftersales_cases.order_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "decision_id", "case_id", "amount_cents", "currency", "eligibility"],
            [
                "policy_decisions.tenant_id",
                "policy_decisions.id",
                "policy_decisions.case_id",
                "policy_decisions.proposed_refund_cents",
                "policy_decisions.currency",
                "policy_decisions.eligibility",
            ],
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "approval_id",
                "case_id",
                "decision_id",
                "amount_cents",
                "currency",
                "action_type",
                "approval_status",
            ],
            [
                "approval_requests.tenant_id",
                "approval_requests.id",
                "approval_requests.case_id",
                "approval_requests.decision_id",
                "approval_requests.amount_cents",
                "approval_requests.currency",
                "approval_requests.action_type",
                "approval_requests.status",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "risk_assessment_id", "decision_id", "case_id", "requires_approval"],
            [
                "risk_assessments.tenant_id",
                "risk_assessments.id",
                "risk_assessments.decision_id",
                "risk_assessments.case_id",
                "risk_assessments.requires_approval",
            ],
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.UniqueConstraint("provider", "provider_request_id"),
        sa.UniqueConstraint("provider", "provider_transaction_id"),
        sa.CheckConstraint("eligibility = 'eligible'", name="eligible_decision"),
        sa.CheckConstraint(
            "(approval_id IS NULL AND approval_status IS NULL) OR "
            "(approval_id IS NOT NULL AND approval_status IS NOT NULL "
            "AND approval_status = 'approved')",
            name="approved_reference_only",
        ),
        sa.CheckConstraint("amount_cents > 0", name="positive_amount"),
        sa.CheckConstraint("action_type = 'refund'", name="financial_action"),
        sa.CheckConstraint("length(idempotency_key) > 0", name="nonempty_idempotency_key"),
        sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        sa.CheckConstraint(
            "NOT requires_approval OR approval_id IS NOT NULL", name="approval_reference_required"
        ),
        sa.CheckConstraint(
            "status != 'succeeded' OR provider_transaction_id IS NOT NULL",
            name="success_transaction_required",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.Index("ix_financial_order", "tenant_id", "order_id"),
        sa.Index("ix_financial_reconciliation", "status", "updated_at"),
    )

    order_id: Mapped[UUID] = mapped_column(sa.Uuid)
    case_id: Mapped[UUID] = mapped_column(sa.Uuid)
    decision_id: Mapped[UUID] = mapped_column(sa.Uuid)
    risk_assessment_id: Mapped[UUID] = mapped_column(sa.Uuid)
    eligibility: Mapped[Eligibility] = mapped_column(
        enum_type(Eligibility, "action_eligibility"), server_default="eligible"
    )
    approval_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    approval_status: Mapped[ApprovalStatus | None] = mapped_column(
        enum_type(ApprovalStatus, "action_approval_status")
    )
    action_type: Mapped[str] = mapped_column(sa.String(32), server_default="refund")
    idempotency_key: Mapped[str] = mapped_column(sa.String(255))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    amount_cents: Mapped[int] = mapped_column(sa.BigInteger)
    currency: Mapped[str] = mapped_column(sa.String(3))
    requires_approval: Mapped[bool] = mapped_column(sa.Boolean)
    status: Mapped[FinancialActionStatus] = mapped_column(
        enum_type(FinancialActionStatus, "financial_action_status"), server_default="pending"
    )
    provider: Mapped[str] = mapped_column(sa.String(64))
    provider_request_id: Mapped[str] = mapped_column(sa.String(128))
    provider_transaction_id: Mapped[str | None] = mapped_column(sa.String(128))
    last_error_code: Mapped[str | None] = mapped_column(sa.String(128))
