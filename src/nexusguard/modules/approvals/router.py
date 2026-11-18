"""Supervisor approval for high-risk damage refunds."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.reviewer import ReviewerIdentity, get_refund_supervisor
from nexusguard.domain.enums import ApprovalStatus
from nexusguard.domain.workflow import WorkflowState as State
from nexusguard.infrastructure.database import get_session
from nexusguard.infrastructure.models import (
    AfterSalesCase,
    ApprovalRequest,
    DamageReview,
    RiskAssessment,
    WorkflowInstance,
)
from nexusguard.modules.payments.refunds import audit_transition, expire, transition

router = APIRouter()
Supervisor = Annotated[ReviewerIdentity, Depends(get_refund_supervisor)]
Database = Annotated[AsyncSession, Depends(get_session)]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=500)


def requests_for(tenant_id: UUID):
    return (
        select(ApprovalRequest, DamageReview, RiskAssessment, AfterSalesCase)
        .join(
            DamageReview,
            (DamageReview.tenant_id == ApprovalRequest.tenant_id)
            & (DamageReview.refund_case_id == ApprovalRequest.case_id),
        )
        .join(
            RiskAssessment,
            (RiskAssessment.tenant_id == ApprovalRequest.tenant_id)
            & (RiskAssessment.id == ApprovalRequest.risk_assessment_id),
        )
        .join(
            AfterSalesCase,
            (AfterSalesCase.tenant_id == ApprovalRequest.tenant_id)
            & (AfterSalesCase.id == ApprovalRequest.case_id),
        )
        .where(ApprovalRequest.tenant_id == tenant_id)
    )


@router.get("/refunds")
async def queue(reviewer: Supervisor, db: Database, response: Response):
    response.headers["Cache-Control"] = "no-store"
    rows = await db.execute(
        requests_for(reviewer.tenant_id)
        .where(
            ApprovalRequest.status == ApprovalStatus.PENDING,
            ApprovalRequest.expires_at > datetime.now(UTC),
        )
        .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
        .limit(50)
    )
    return [
        {
            "approval_id": approval.id,
            "order_number": review.order_number,
            "user_id": case.user_id,
            "amount_cents": approval.amount_cents,
            "currency": approval.currency,
            "reasons": risk.triggered_rules,
            "requested_resolution": review.requested_resolution,
            "resolution_reason": review.resolution_reason,
            "resolution": review.resolution,
            "expires_at": approval.expires_at,
        }
        for approval, review, risk, case in rows
    ]


@router.post("/refunds/{approval_id}/decision")
async def decide(
    approval_id: UUID,
    data: Decision,
    reviewer: Supervisor,
    db: Database,
    request: Request,
):
    async with db.begin():
        row = (
            await db.execute(
                requests_for(reviewer.tenant_id).where(ApprovalRequest.id == approval_id)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(404, "refund_approval_not_found")
        approval, review, _, case = row
        if reviewer.reviewer_id == review.resolver_id:
            raise HTTPException(403, "independent_supervisor_required")
        workflow = await db.get(
            WorkflowInstance,
            (reviewer.tenant_id, approval.workflow_id),
            with_for_update=True,
        )
        await db.refresh(approval)
        if approval.status != ApprovalStatus.PENDING:
            if (
                approval.status == data.decision
                and approval.review_comment == data.reason
                and approval.reviewer_id == reviewer.reviewer_id
            ):
                return {"status": approval.status}
            raise HTTPException(409, "refund_approval_closed")
        if workflow.state != State.WAITING_APPROVAL:
            raise HTTPException(409, "refund_approval_closed")
        now = datetime.now(UTC)
        old = workflow.state
        if approval.expires_at <= now:
            expire(workflow, case, approval, now)
        else:
            approval.status = ApprovalStatus(data.decision)
            approval.reviewer_id = reviewer.reviewer_id
            approval.review_comment = data.reason
            approval.decided_at = now
            if approval.status == ApprovalStatus.APPROVED:
                transition(workflow, State.REVALIDATING, State.READY_TO_EXECUTE)
                workflow.next_run_at = now
            else:
                transition(workflow, State.REJECTED)
                workflow.next_run_at = None
                case.status = "rejected"
        audit_transition(
            db,
            workflow,
            old,
            UUID(request.state.request_id),
            actor_type="reviewer",
            actor_id=reviewer.reviewer_id,
        )
        return {"status": approval.status}
