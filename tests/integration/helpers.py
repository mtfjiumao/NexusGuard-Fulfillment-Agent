from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import insert

from nexusguard.infrastructure.models import (
    SKU,
    AfterSalesCase,
    ApprovalRequest,
    AuditLog,
    FinancialAction,
    Order,
    OrderItem,
    OrderRefundBalance,
    OutboxEvent,
    PolicyDecision,
    RiskAssessment,
    Tenant,
    User,
    WorkflowInstance,
)


async def seed(factory, *, high_risk=False, amount=8000, state="received"):
    ids = SimpleNamespace(
        **{
            name: uuid4()
            for name in (
                "tenant",
                "user",
                "sku",
                "order",
                "item",
                "case",
                "workflow",
                "decision",
                "risk",
                "balance",
                "approval",
            )
        }
    )
    ids.amount = amount
    ids.high_risk = high_risk
    ids.now = datetime.now(UTC)
    async with factory() as session, session.begin():
        await session.execute(insert(Tenant).values(id=ids.tenant, name="Integration tenant"))
        await session.execute(
            insert(User).values(tenant_id=ids.tenant, id=ids.user, external_id="user-1")
        )
        await session.execute(
            insert(SKU).values(
                tenant_id=ids.tenant,
                id=ids.sku,
                sku_code="sku-1",
                product_id="product-1",
                title="Test item",
                category="general",
                price_cents=10000,
            )
        )
        await session.execute(
            insert(Order).values(
                tenant_id=ids.tenant,
                id=ids.order,
                order_number="order-1",
                user_id=ids.user,
                payment_status="paid",
                paid_amount_cents=10000,
                ordered_at=ids.now,
                paid_at=ids.now,
                shipping_address_snapshot={"fixture": True},
            )
        )
        await session.execute(
            insert(OrderItem).values(
                tenant_id=ids.tenant,
                id=ids.item,
                order_id=ids.order,
                sku_id=ids.sku,
                quantity=1,
                paid_amount_cents=10000,
                category_snapshot="general",
                sku_snapshot={},
            )
        )
        await session.execute(insert(AfterSalesCase).values(**case_values(ids)))
        await session.execute(
            insert(WorkflowInstance).values(
                tenant_id=ids.tenant,
                id=ids.workflow,
                case_id=ids.case,
                state=state,
            )
        )
        await session.execute(
            insert(PolicyDecision).values(
                tenant_id=ids.tenant,
                id=ids.decision,
                case_id=ids.case,
                currency="CNY",
                decision_version=1,
                policy_version="fixture-v1",
                fact_snapshot={"verified": True},
                facts_hash="a" * 64,
                matched_rule_ids=["FIXTURE"],
                eligibility="eligible",
                max_refund_cents=10000,
                proposed_refund_cents=amount,
                reason_codes=["FIXTURE"],
            )
        )
        await session.execute(
            insert(RiskAssessment).values(
                tenant_id=ids.tenant,
                id=ids.risk,
                case_id=ids.case,
                decision_id=ids.decision,
                risk_rule_version="fixture-v1",
                risk_level="high" if high_risk else "low",
                requires_approval=high_risk,
                triggered_rules=[],
                profile_snapshot={},
            )
        )
        await session.execute(
            insert(OrderRefundBalance).values(
                tenant_id=ids.tenant,
                id=ids.balance,
                order_id=ids.order,
                currency="CNY",
                paid_cents=10000,
            )
        )
    return ids


def case_values(ids, **changes):
    values = dict(
        tenant_id=ids.tenant,
        id=ids.case,
        case_number="case-1",
        order_id=ids.order,
        user_id=ids.user,
        currency="CNY",
        claim_type="no_reason_return",
        requested_amount_cents=ids.amount,
        reason="Fixture request",
    )
    return values | changes


def financial_values(ids, **changes):
    values = dict(
        tenant_id=ids.tenant,
        case_id=ids.case,
        order_id=ids.order,
        decision_id=ids.decision,
        risk_assessment_id=ids.risk,
        idempotency_key=f"{ids.order}:REFUND",
        request_hash="b" * 64,
        amount_cents=ids.amount,
        currency="CNY",
        requires_approval=ids.high_risk,
        provider="fixture",
        provider_request_id=str(uuid4()),
    )
    return values | changes


def approval_values(ids, *, status="pending", **changes):
    values = dict(
        tenant_id=ids.tenant,
        id=ids.approval,
        case_id=ids.case,
        workflow_id=ids.workflow,
        decision_id=ids.decision,
        risk_assessment_id=ids.risk,
        approval_round=1,
        amount_cents=ids.amount,
        currency="CNY",
        scope_hash="c" * 64,
        status=status,
        expires_at=ids.now + timedelta(hours=24),
    )
    if status in {"approved", "rejected"}:
        values.update(
            reviewer_id="reviewer-1", decided_at=ids.now, review_comment="Fixture decision"
        )
    return values | changes


async def add_approval(factory, ids, *, status="pending"):
    async with factory() as session, session.begin():
        await session.execute(insert(ApprovalRequest).values(**approval_values(ids, status=status)))


def audit_values(ids):
    return dict(
        tenant_id=ids.tenant,
        actor_type="system",
        actor_id="fixture",
        action="APPROVAL_REQUESTED",
        resource_type="workflow",
        resource_id=ids.workflow,
        trace_id=uuid4(),
    )


def outbox_values(ids):
    return dict(
        tenant_id=ids.tenant,
        aggregate_type="workflow",
        aggregate_id=ids.workflow,
        event_type="APPROVAL_REQUESTED",
        payload={"workflow_id": str(ids.workflow)},
    )


# Exported model names keep assertions readable in integration tests.
__all__ = [
    "AuditLog",
    "FinancialAction",
    "OutboxEvent",
    "add_approval",
    "approval_values",
    "audit_values",
    "case_values",
    "financial_values",
    "outbox_values",
    "seed",
]
