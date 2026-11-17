import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from nexusguard.domain.workflow import WorkflowState
from nexusguard.infrastructure.models import (
    AfterSalesCase,
    ApprovalRequest,
    AuditLog,
    CaseItem,
    FinancialAction,
    InboxEvent,
    Order,
    OrderRefundBalance,
    OutboxEvent,
    Tenant,
    User,
    WorkflowInstance,
)
from tests.integration.helpers import (
    add_approval,
    approval_values,
    audit_values,
    case_values,
    financial_values,
    outbox_values,
    seed,
)

pytestmark = pytest.mark.integration


def test_cross_tenant_order_owner_is_rejected(db):
    async def scenario(factory):
        ids = await seed(factory)
        other = uuid4()
        async with factory() as session, session.begin():
            await session.execute(insert(Tenant).values(id=other, name="Other tenant"))
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    insert(Order).values(
                        tenant_id=other,
                        order_number="foreign-order",
                        user_id=ids.user,
                        paid_amount_cents=100,
                        ordered_at=ids.now,
                        shipping_address_snapshot={},
                    )
                )
        assert error.value.orig.sqlstate == "23503"

    db(scenario)


@pytest.mark.parametrize("mismatch", ["owner", "currency"])
def test_case_must_match_order_owner_and_currency(db, mismatch):
    async def scenario(factory):
        ids = await seed(factory)
        changes = {"currency": "USD"}
        if mismatch == "owner":
            wrong_user = uuid4()
            async with factory() as session, session.begin():
                await session.execute(
                    insert(User).values(
                        tenant_id=ids.tenant, id=wrong_user, external_id="other-user"
                    )
                )
            changes = {"user_id": wrong_user}
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    insert(AfterSalesCase).values(
                        **case_values(ids, id=uuid4(), case_number="case-2", **changes)
                    )
                )
        assert error.value.orig.sqlstate == "23503"

    db(scenario)


def test_case_item_cannot_reference_another_orders_item(db):
    async def scenario(factory):
        first = await seed(factory)
        second = await seed(factory)
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    insert(CaseItem).values(
                        tenant_id=first.tenant,
                        case_id=first.case,
                        order_id=first.order,
                        order_item_id=second.item,
                        quantity=1,
                        requested_amount_cents=100,
                    )
                )
        assert error.value.orig.sqlstate == "23503"

    db(scenario)


def test_concurrent_duplicate_financial_requests_have_one_winner(db):
    async def scenario(factory):
        ids = await seed(factory)
        gate = asyncio.Event()

        async def submit():
            await gate.wait()
            try:
                async with factory() as session, session.begin():
                    await session.execute(insert(FinancialAction).values(**financial_values(ids)))
                return "created"
            except IntegrityError as error:
                assert error.orig.sqlstate == "23505"
                return "duplicate"

        tasks = [asyncio.create_task(submit()) for _ in range(2)]
        gate.set()
        assert sorted(await asyncio.gather(*tasks)) == ["created", "duplicate"]
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(FinancialAction)
                    .where(FinancialAction.tenant_id == ids.tenant)
                )
                == 1
            )

    db(scenario)


@pytest.mark.parametrize(
    "values",
    [
        {"reserved_cents": -1},
        {"refunded_cents": -1},
        {"reserved_cents": 10001},
        {"refunded_cents": 6000, "reserved_cents": 5000},
    ],
)
def test_invalid_refund_balances_are_rejected(db, values):
    async def scenario(factory):
        ids = await seed(factory)
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    update(OrderRefundBalance)
                    .where(
                        OrderRefundBalance.tenant_id == ids.tenant,
                        OrderRefundBalance.id == ids.balance,
                    )
                    .values(**values)
                )
        assert error.value.orig.sqlstate == "23514"

    db(scenario)


def test_balance_cannot_copy_a_different_paid_amount(db):
    async def scenario(factory):
        ids = await seed(factory)
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    update(OrderRefundBalance)
                    .where(
                        OrderRefundBalance.tenant_id == ids.tenant,
                        OrderRefundBalance.id == ids.balance,
                    )
                    .values(paid_cents=20000)
                )
        assert error.value.orig.sqlstate == "23503"

    db(scenario)


def test_concurrent_reservations_cannot_overdraw_an_order(db):
    async def scenario(factory):
        ids = await seed(factory)
        gate = asyncio.Event()

        async def reserve():
            await gate.wait()
            try:
                async with factory() as session, session.begin():
                    await session.execute(
                        update(OrderRefundBalance)
                        .where(
                            OrderRefundBalance.tenant_id == ids.tenant,
                            OrderRefundBalance.id == ids.balance,
                        )
                        .values(
                            reserved_cents=OrderRefundBalance.reserved_cents + 8000,
                            version=OrderRefundBalance.version + 1,
                        )
                    )
                return "reserved"
            except IntegrityError as error:
                assert error.orig.sqlstate == "23514"
                return "insufficient"

        tasks = [asyncio.create_task(reserve()) for _ in range(2)]
        gate.set()
        assert sorted(await asyncio.gather(*tasks)) == ["insufficient", "reserved"]
        async with factory() as session:
            balance = await session.get(OrderRefundBalance, (ids.tenant, ids.balance))
            assert balance.reserved_cents == 8000
            assert balance.refunded_cents == 0

    db(scenario)


@pytest.mark.parametrize("forged", ["missing_approval", "false_low_risk"])
def test_high_risk_financial_action_cannot_bypass_approval(db, forged):
    async def scenario(factory):
        ids = await seed(factory, high_risk=True)
        changes = {"requires_approval": False} if forged == "false_low_risk" else {}
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    insert(FinancialAction).values(**financial_values(ids, **changes))
                )
        assert error.value.orig.sqlstate in {"23503", "23514"}

    db(scenario)


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_nonapproved_request_cannot_authorize_a_financial_action(db, status):
    async def scenario(factory):
        ids = await seed(factory, high_risk=True)
        await add_approval(factory, ids, status=status)
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    insert(FinancialAction).values(
                        **financial_values(
                            ids, approval_id=ids.approval, approval_status="approved"
                        )
                    )
                )
        assert error.value.orig.sqlstate == "23503"

    db(scenario)


def test_approved_request_can_create_exactly_scoped_financial_action(db):
    async def scenario(factory):
        ids = await seed(factory, high_risk=True)
        await add_approval(factory, ids, status="approved")
        async with factory() as session, session.begin():
            await session.execute(
                insert(FinancialAction).values(
                    **financial_values(ids, approval_id=ids.approval, approval_status="approved")
                )
            )
        async with factory() as session:
            action = await session.scalar(
                select(FinancialAction).where(FinancialAction.tenant_id == ids.tenant)
            )
            assert action.amount_cents == ids.amount
            assert action.status == "pending"

    db(scenario)


@pytest.mark.parametrize("changes", [{"amount_cents": 7999}, {"currency": "USD"}])
def test_approval_scope_cannot_change_decision_amount_or_currency(db, changes):
    async def scenario(factory):
        ids = await seed(factory, high_risk=True)
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    insert(ApprovalRequest).values(**approval_values(ids, **changes))
                )
        assert error.value.orig.sqlstate == "23503"

    db(scenario)


def test_interrupt_bundle_commits_checkpoint_approval_audit_and_outbox(db):
    async def scenario(factory):
        ids = await seed(factory, high_risk=True, state="risk_assessing")
        async with factory() as session, session.begin():
            await session.execute(
                update(WorkflowInstance)
                .where(
                    WorkflowInstance.tenant_id == ids.tenant,
                    WorkflowInstance.id == ids.workflow,
                    WorkflowInstance.version == 1,
                )
                .values(
                    state="waiting_approval",
                    checkpoint={"approval_id": str(ids.approval)},
                    version=2,
                )
            )
            await session.execute(insert(ApprovalRequest).values(**approval_values(ids)))
            await session.execute(insert(AuditLog).values(**audit_values(ids)))
            await session.execute(insert(OutboxEvent).values(**outbox_values(ids)))
        # A new session simulates loss of the original in-memory worker state.
        async with factory() as session:
            workflow = await session.get(WorkflowInstance, (ids.tenant, ids.workflow))
            assert workflow.state == WorkflowState.WAITING_APPROVAL
            assert workflow.checkpoint == {"approval_id": str(ids.approval)}
            for model in (ApprovalRequest, AuditLog, OutboxEvent):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(model).where(model.tenant_id == ids.tenant)
                    )
                    == 1
                )

    db(scenario)


def test_failed_interrupt_rolls_back_every_record(db):
    async def scenario(factory):
        ids = await seed(factory, high_risk=True, state="risk_assessing")
        with pytest.raises(RuntimeError, match="simulated worker crash"):
            async with factory() as session, session.begin():
                await session.execute(
                    update(WorkflowInstance)
                    .where(
                        WorkflowInstance.tenant_id == ids.tenant,
                        WorkflowInstance.id == ids.workflow,
                    )
                    .values(state="waiting_approval", checkpoint={"approval_id": str(ids.approval)})
                )
                await session.execute(insert(ApprovalRequest).values(**approval_values(ids)))
                await session.execute(insert(AuditLog).values(**audit_values(ids)))
                await session.execute(insert(OutboxEvent).values(**outbox_values(ids)))
                raise RuntimeError("simulated worker crash")
        async with factory() as session:
            workflow = await session.get(WorkflowInstance, (ids.tenant, ids.workflow))
            assert workflow.state == WorkflowState.RISK_ASSESSING
            assert workflow.checkpoint == {}
            for model in (ApprovalRequest, AuditLog, OutboxEvent):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(model).where(model.tenant_id == ids.tenant)
                    )
                    == 0
                )

    db(scenario)


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_audit_history_cannot_be_rewritten(db, operation):
    async def scenario(factory):
        ids = await seed(factory)
        async with factory() as session, session.begin():
            await session.execute(insert(AuditLog).values(**audit_values(ids)))
        with pytest.raises(DBAPIError) as error:
            async with factory() as session, session.begin():
                statement = (
                    update(AuditLog).values(action="ALTERED")
                    if operation == "update"
                    else delete(AuditLog)
                )
                await session.execute(statement.where(AuditLog.tenant_id == ids.tenant))
        assert error.value.orig.sqlstate == "55000"

    db(scenario)


def test_stale_orm_worker_cannot_overwrite_workflow(db):
    async def scenario(factory):
        ids = await seed(factory)
        async with factory() as first, factory() as stale:
            current = await first.get(WorkflowInstance, (ids.tenant, ids.workflow))
            outdated = await stale.get(WorkflowInstance, (ids.tenant, ids.workflow))
            current.state = WorkflowState.FACT_CHECKING
            await first.commit()
            outdated.state = WorkflowState.FACT_CHECKING
            with pytest.raises(StaleDataError):
                await stale.commit()

    db(scenario)


def test_inbox_deduplicates_per_consumer(db):
    async def scenario(factory):
        ids = await seed(factory)
        event = uuid4()
        values = dict(tenant_id=ids.tenant, consumer="worker-1", event_id=event)
        async with factory() as session, session.begin():
            await session.execute(insert(InboxEvent).values(**values))
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(insert(InboxEvent).values(**values))
        assert error.value.orig.sqlstate == "23505"
        async with factory() as session, session.begin():
            await session.execute(insert(InboxEvent).values(**(values | {"consumer": "worker-2"})))

    db(scenario)


def test_invalid_workflow_state_is_rejected_by_postgres(db):
    async def scenario(factory):
        ids = await seed(factory)
        with pytest.raises(IntegrityError) as error:
            async with factory() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE workflow_instances SET state = 'skip_approval' "
                        "WHERE tenant_id = :tenant AND id = :id"
                    ),
                    {"tenant": ids.tenant, "id": ids.workflow},
                )
        assert error.value.orig.sqlstate == "23514"

    db(scenario)
