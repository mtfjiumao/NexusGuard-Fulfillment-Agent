from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusguard.domain.enums import ApprovalStatus, Eligibility
from nexusguard.domain.workflow import WorkflowState
from nexusguard.infrastructure.database import Base
from nexusguard.infrastructure.models.common import MutableRecord, TenantEntity, enum_type


class WorkflowInstance(TenantEntity, MutableRecord, Base):
    __tablename__ = "workflow_instances"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"], ["aftersales_cases.tenant_id", "aftersales_cases.id"]
        ),
        sa.UniqueConstraint("tenant_id", "case_id"),
        sa.UniqueConstraint("tenant_id", "id", "case_id", name="uq_workflow_case"),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.CheckConstraint("jsonb_typeof(checkpoint) = 'object'", name="checkpoint_object"),
        sa.Index("ix_workflow_schedule", "state", "next_run_at"),
    )

    case_id: Mapped[UUID] = mapped_column(sa.Uuid)
    state: Mapped[WorkflowState] = mapped_column(
        enum_type(WorkflowState, "workflow_state"), server_default="received"
    )
    checkpoint: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    next_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(sa.String(128))


class ApprovalRequest(TenantEntity, MutableRecord, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_id", "case_id"],
            ["workflow_instances.tenant_id", "workflow_instances.id", "workflow_instances.case_id"],
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
            ["tenant_id", "risk_assessment_id", "decision_id", "case_id"],
            [
                "risk_assessments.tenant_id",
                "risk_assessments.id",
                "risk_assessments.decision_id",
                "risk_assessments.case_id",
            ],
        ),
        sa.UniqueConstraint("tenant_id", "workflow_id", "approval_round"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "case_id",
            "decision_id",
            "amount_cents",
            "currency",
            "action_type",
            "status",
            name="uq_approval_action_scope",
        ),
        sa.CheckConstraint("approval_round > 0 AND amount_cents > 0", name="positive_approval"),
        sa.CheckConstraint("action_type = 'refund'", name="approval_action"),
        sa.CheckConstraint("eligibility = 'eligible'", name="eligible_decision"),
        sa.CheckConstraint("scope_hash ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        sa.CheckConstraint("expires_at > created_at", name="valid_expiry"),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewer_id IS NULL AND decided_at IS NULL) OR "
            "(status IN ('approved', 'rejected') AND reviewer_id IS NOT NULL "
            "AND decided_at IS NOT NULL AND review_comment IS NOT NULL) OR "
            "(status = 'expired' AND decided_at IS NOT NULL)",
            name="review_details",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.Index("ix_approval_pending", "tenant_id", "status", "expires_at"),
    )

    case_id: Mapped[UUID] = mapped_column(sa.Uuid)
    workflow_id: Mapped[UUID] = mapped_column(sa.Uuid)
    decision_id: Mapped[UUID] = mapped_column(sa.Uuid)
    risk_assessment_id: Mapped[UUID] = mapped_column(sa.Uuid)
    eligibility: Mapped[Eligibility] = mapped_column(
        enum_type(Eligibility, "approval_eligibility"), server_default="eligible"
    )
    approval_round: Mapped[int] = mapped_column(sa.Integer)
    action_type: Mapped[str] = mapped_column(sa.String(32), server_default="refund")
    amount_cents: Mapped[int] = mapped_column(sa.BigInteger)
    currency: Mapped[str] = mapped_column(sa.String(3))
    scope_hash: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"), server_default="pending"
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    reviewer_id: Mapped[str | None] = mapped_column(sa.String(128))
    review_comment: Mapped[str | None] = mapped_column(sa.Text)
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
