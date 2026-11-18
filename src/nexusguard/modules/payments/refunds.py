"""Durable simulation for refunds authorized by an after-sales reviewer."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.domain.enums import ApprovalStatus, Eligibility, FinancialActionStatus, RiskLevel
from nexusguard.domain.workflow import WorkflowState as State
from nexusguard.domain.workflow import validate_transition
from nexusguard.infrastructure.models import (
    AfterSalesCase,
    ApprovalRequest,
    AuditLog,
    DamageReview,
    FinancialAction,
    Order,
    OrderRefundBalance,
    PolicyDecision,
    RiskAssessment,
    UserRiskProfile,
    WorkflowInstance,
)

SimulationOutcome = Literal["success", "failure", "unknown"]
PROVIDER = "nexusguard_simulator"
STATE_TEXT = {
    State.WAITING_APPROVAL: "退款正在等待主管审批。",
    State.READY_TO_EXECUTE: "退款已排队，等待模拟渠道处理。",
    State.EXECUTING: "退款正在由模拟渠道处理。",
    State.RECONCILING: "模拟渠道结果待确认，退款额度已保留。",
    State.SUCCEEDED: "模拟退款成功。",
    State.FAILED: "模拟退款失败，请联系店铺核对处理结果。",
    State.REJECTED: "主管未同意执行退款。",
    State.EXPIRED: "退款审批已超时，请联系店铺。",
}


def digest(data: dict) -> str:
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def refund_status_text(state: State | None, rejection_reason: str | None = None) -> str | None:
    text = STATE_TEXT.get(state)
    if state == State.REJECTED and rejection_reason:
        return f"{text} 原因：{rejection_reason}"
    return text


def transition(workflow: WorkflowInstance, *states: State) -> None:
    for state in states:
        validate_transition(workflow.state, state)
        workflow.state = state


def audit_transition(
    db: AsyncSession,
    workflow: WorkflowInstance,
    old: State,
    trace_id: UUID,
    *,
    actor_type: str = "system",
    actor_id: str = PROVIDER,
) -> None:
    db.add(
        AuditLog(
            tenant_id=workflow.tenant_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action="refund." + workflow.state.value,
            resource_type="workflow",
            resource_id=workflow.id,
            from_state=old.value,
            to_state=workflow.state.value,
            trace_id=trace_id,
        )
    )


async def prepare_refund(
    db: AsyncSession, review: DamageReview, order: Order, trace_id: UUID
) -> None:
    amount = review.refund_cents
    if amount is None or review.outcome != "approved" or review.refund_currency != order.currency:
        raise ValueError("refund_requires_human_resolution")
    now = datetime.now(UTC)
    profile = await db.scalar(
        select(UserRiskProfile)
        .where(
            UserRiskProfile.tenant_id == order.tenant_id,
            UserRiskProfile.user_id == order.user_id,
            UserRiskProfile.window_end <= now,
            UserRiskProfile.calculated_at <= now,
        )
        .order_by(UserRiskProfile.calculated_at.desc(), UserRiskProfile.id.desc())
        .limit(1)
    )
    reasons = []
    if order.currency != "CNY":
        reasons.append("currency_requires_review")
    elif amount > 20_000:
        reasons.append("amount_over_200_cny")
    if profile is None or profile.eligible_order_count == 0:
        reasons.append("return_history_unavailable")
    elif profile.return_count * 10 > profile.eligible_order_count * 3:
        reasons.append("return_rate_over_30_percent")

    case_id, workflow_id, decision_id, risk_id = (uuid4() for _ in range(4))
    case = AfterSalesCase(
        tenant_id=order.tenant_id,
        id=case_id,
        case_number=f"DAMAGE-{review.id}",
        order_id=order.id,
        user_id=order.user_id,
        currency=order.currency,
        claim_type="damage_compensation",
        requested_amount_cents=amount,
        approved_amount_cents=amount,
        reason=review.requested_resolution,
    )
    db.add(case)
    await db.flush()
    facts = {
        "application_id": str(review.id),
        "revision": review.revision,
        "order_id": str(order.id),
        "currency": order.currency,
        "paid_amount_cents": order.paid_amount_cents,
        "approved_refund_cents": amount,
        "resolved_by": review.resolver_id,
    }
    workflow = WorkflowInstance(
        tenant_id=order.tenant_id,
        id=workflow_id,
        case_id=case_id,
        state=State.RECEIVED,
        checkpoint={"decision_id": str(decision_id), "risk_id": str(risk_id)},
        next_run_at=now,
    )
    decision = PolicyDecision(
        tenant_id=order.tenant_id,
        id=decision_id,
        case_id=case_id,
        currency=order.currency,
        decision_version=1,
        policy_version="human-damage-resolution-v1",
        fact_snapshot=facts,
        facts_hash=digest(facts),
        matched_rule_ids=["human_approval"],
        eligibility=Eligibility.ELIGIBLE,
        max_refund_cents=order.paid_amount_cents,
        proposed_refund_cents=amount,
        reason_codes=["human_approval"],
    )
    db.add_all([workflow, decision])
    await db.flush()
    db.add(
        RiskAssessment(
            tenant_id=order.tenant_id,
            id=risk_id,
            case_id=case_id,
            decision_id=decision_id,
            risk_rule_version="refund-risk-v1",
            risk_level=RiskLevel.HIGH if reasons else RiskLevel.LOW,
            requires_approval=bool(reasons),
            triggered_rules=reasons,
            profile_snapshot={
                "profile_id": str(profile.id) if profile else None,
                "return_count": profile.return_count if profile else None,
                "eligible_order_count": profile.eligible_order_count if profile else None,
            },
        )
    )
    await db.flush()
    transition(workflow, State.FACT_CHECKING, State.POLICY_EVALUATING, State.RISK_ASSESSING)
    if reasons:
        transition(workflow, State.WAITING_APPROVAL)
        approval_id = uuid4()
        workflow.next_run_at = now + timedelta(hours=24)
        workflow.checkpoint = {**workflow.checkpoint, "approval_id": str(approval_id)}
        db.add(
            ApprovalRequest(
                tenant_id=order.tenant_id,
                id=approval_id,
                case_id=case_id,
                workflow_id=workflow_id,
                decision_id=decision_id,
                risk_assessment_id=risk_id,
                approval_round=1,
                amount_cents=amount,
                currency=order.currency,
                scope_hash=decision.facts_hash,
                expires_at=workflow.next_run_at,
            )
        )
    else:
        transition(workflow, State.READY_TO_EXECUTE)
    review.refund_case_id = case_id
    audit_transition(db, workflow, State.RECEIVED, trace_id)


def scheduled_refunds():
    return select(WorkflowInstance).join(
        DamageReview,
        (DamageReview.tenant_id == WorkflowInstance.tenant_id)
        & (DamageReview.refund_case_id == WorkflowInstance.case_id),
    )


class RefundWorker:
    def __init__(self, db: AsyncSession, outcome: SimulationOutcome):
        self.db = db
        self.outcome = outcome

    async def run_once(self) -> bool:
        now = datetime.now(UTC)
        workflow = await self.db.scalar(
            scheduled_refunds()
            .where(WorkflowInstance.next_run_at <= now)
            .order_by(WorkflowInstance.next_run_at, WorkflowInstance.id)
            .limit(1)
            .with_for_update(of=WorkflowInstance, skip_locked=True)
        )
        if workflow is None:
            return False
        old = workflow.state
        case = await self.db.get(AfterSalesCase, (workflow.tenant_id, workflow.case_id))
        if old == State.WAITING_APPROVAL:
            approval = await self.db.get(
                ApprovalRequest, (workflow.tenant_id, UUID(workflow.checkpoint["approval_id"]))
            )
            expire(workflow, case, approval, now)
        elif old == State.READY_TO_EXECUTE:
            await self.start(workflow, case, now)
        elif old in {State.EXECUTING, State.RECONCILING}:
            await self.finish(workflow, case, now)
        else:
            raise RuntimeError("unexpected_refund_state")
        audit_transition(self.db, workflow, old, uuid4())
        return True

    async def start(self, workflow: WorkflowInstance, case: AfterSalesCase, now: datetime) -> None:
        order = await self.db.get(Order, (case.tenant_id, case.order_id), with_for_update=True)
        risk = await self.db.get(
            RiskAssessment, (case.tenant_id, UUID(workflow.checkpoint["risk_id"]))
        )
        decision = await self.db.get(
            PolicyDecision, (case.tenant_id, UUID(workflow.checkpoint["decision_id"]))
        )
        approval = None
        if risk.requires_approval:
            approval = await self.db.get(
                ApprovalRequest, (case.tenant_id, UUID(workflow.checkpoint["approval_id"]))
            )
            if (
                approval.status != ApprovalStatus.APPROVED
                or approval.scope_hash != decision.facts_hash
            ):
                raise RuntimeError("refund_approval_required")

        key = f"{order.id}:REFUND"
        existing_action = await self.db.scalar(
            select(FinancialAction.id).where(
                FinancialAction.tenant_id == order.tenant_id,
                FinancialAction.idempotency_key == key,
            )
        )
        balance = await self.balance(case)
        available = (
            balance.paid_cents - balance.refunded_cents - balance.reserved_cents
            if balance
            else order.paid_amount_cents
        )
        amount = decision.proposed_refund_cents
        transition(workflow, State.EXECUTING)
        workflow.next_run_at = now
        if existing_action or order.payment_status != "paid" or amount > available:
            self.fail(
                workflow,
                case,
                "refund_already_attempted" if existing_action else "budget_unavailable",
            )
            return
        if balance is None:
            balance = OrderRefundBalance(
                tenant_id=case.tenant_id,
                order_id=case.order_id,
                currency=order.currency,
                paid_cents=order.paid_amount_cents,
                refunded_cents=0,
                reserved_cents=0,
            )
            self.db.add(balance)
        balance.reserved_cents += amount
        action_id = uuid4()
        self.db.add(
            FinancialAction(
                tenant_id=case.tenant_id,
                id=action_id,
                order_id=case.order_id,
                case_id=case.id,
                decision_id=decision.id,
                risk_assessment_id=risk.id,
                eligibility=Eligibility.ELIGIBLE,
                approval_id=approval.id if approval else None,
                approval_status=ApprovalStatus.APPROVED if approval else None,
                idempotency_key=key,
                request_hash=decision.facts_hash,
                amount_cents=amount,
                currency=decision.currency,
                requires_approval=risk.requires_approval,
                status=FinancialActionStatus.EXECUTING,
                provider=PROVIDER,
                provider_request_id=f"{case.tenant_id}:{key}",
            )
        )
        workflow.checkpoint = {
            **workflow.checkpoint,
            "action_id": str(action_id),
            "simulation": self.outcome,
        }

    async def balance(self, case: AfterSalesCase) -> OrderRefundBalance | None:
        return await self.db.scalar(
            select(OrderRefundBalance)
            .where(
                OrderRefundBalance.tenant_id == case.tenant_id,
                OrderRefundBalance.order_id == case.order_id,
            )
            .with_for_update()
        )

    async def finish(self, workflow: WorkflowInstance, case: AfterSalesCase, now: datetime) -> None:
        action = await self.db.get(
            FinancialAction,
            (case.tenant_id, UUID(workflow.checkpoint["action_id"])),
            with_for_update=True,
        )
        scenario = workflow.checkpoint["simulation"]
        if scenario == "unknown" and workflow.state == State.EXECUTING:
            transition(workflow, State.RECONCILING)
            workflow.next_run_at = now + timedelta(seconds=15)
            action.status = FinancialActionStatus.RECONCILING
            return
        balance = await self.balance(case)
        if balance is None or balance.reserved_cents < action.amount_cents:
            raise RuntimeError("refund_reservation_missing")
        balance.reserved_cents -= action.amount_cents
        if scenario == "failure":
            action.status = FinancialActionStatus.FAILED
            action.last_error_code = "simulated_decline"
            self.fail(workflow, case, "simulated_decline")
            return
        balance.refunded_cents += action.amount_cents
        action.provider_transaction_id = f"sim-{action.id}"
        action.status = FinancialActionStatus.SUCCEEDED
        transition(workflow, State.SUCCEEDED)
        workflow.next_run_at = None
        case.status = "resolved"

    @staticmethod
    def fail(workflow: WorkflowInstance, case: AfterSalesCase, reason: str) -> None:
        transition(workflow, State.FAILED)
        workflow.last_error_code = reason
        workflow.next_run_at = None
        case.status = "cancelled"


def expire(
    workflow: WorkflowInstance, case: AfterSalesCase, approval: ApprovalRequest, now: datetime
) -> None:
    approval.status = ApprovalStatus.EXPIRED
    approval.decided_at = now
    transition(workflow, State.EXPIRED)
    workflow.next_run_at = None
    case.status = "cancelled"
