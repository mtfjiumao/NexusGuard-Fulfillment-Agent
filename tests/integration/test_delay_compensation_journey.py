"""Verify two independently authorized refunds sharing one order balance."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from nexusguard.api.reviewer import (
    ReviewerIdentity,
    get_damage_reviewer,
    get_logistics_reviewer,
    get_refund_supervisor,
)
from nexusguard.infrastructure.models import (
    FinancialAction,
    Order,
    OrderRefundBalance,
    Shipment,
    UserRiskProfile,
)
from tests.integration.test_customer_journey import (
    ROOT,
    action,
    classified,
    collect,
    create,
    history,
    refresh,
    reply,
    send,
    setup,
)
from tests.integration.test_refund_journey import APPROVALS, PHOTO, photo, submit_and_review

pytestmark = pytest.mark.integration
LOGISTICS = "/api/v1/logistics"


@pytest.mark.parametrize(
    ("delivered", "amount", "succeeds"),
    [(False, 500, True), (True, 8010, True), (True, 8011, False)],
)
def test_delay_compensation_after_damage_refund(db, settings, delivered, amount, succeeds):
    async def journey(factory):
        ids, provider, gateway, worker, client, app = await setup(factory, settings)
        worker.refund_simulation = "success"
        now = datetime.now(UTC)
        promised = now - timedelta(hours=3)
        signed = now - timedelta(hours=1) if delivered else None
        app.dependency_overrides[get_damage_reviewer] = lambda: ReviewerIdentity(
            ids.tenant, "damage-reviewer", frozenset({"damage_review"})
        )
        app.dependency_overrides[get_logistics_reviewer] = lambda: ReviewerIdentity(
            ids.tenant, "logistics-reviewer", frozenset({"logistics_review"})
        )
        app.dependency_overrides[get_refund_supervisor] = lambda: ReviewerIdentity(
            ids.tenant, "supervisor", frozenset({"refund_approval"})
        )
        try:
            async with factory() as session, session.begin():
                order = await session.get(Order, (ids.tenant, ids.order))
                order.fulfillment_status = "delivered" if delivered else "shipped"
                order.delivered_at = signed
                session.add_all(
                    [
                        Shipment(
                            tenant_id=ids.tenant,
                            order_id=ids.order,
                            carrier="演示快递",
                            status="delivered" if delivered else "in_transit",
                            promised_delivery_at=promised,
                            delivered_at=signed,
                        ),
                        UserRiskProfile(
                            tenant_id=ids.tenant,
                            user_id=ids.user,
                            return_count=1,
                            eligible_order_count=10,
                            window_start=now - timedelta(days=90),
                            window_end=now,
                            calculation_version="journey",
                            calculated_at=now,
                        ),
                    ]
                )
            view = await action(client, await create(client, ids), "confirm")
            provider.queue(classified(), {"action": "inspect_damage"}, collect(ids.item), reply())
            view, _ = await send(client, view, "Test item 一个发霉")
            assert await worker.run_once()
            view = await photo(client, await refresh(client, view), PHOTO)
            damage = await submit_and_review(client, view, "passed")
            response = await client.post(
                f"{damage}/resolution",
                json={
                    "outcome": "approved",
                    "reason": "确认破损",
                    "resolution": "同意退款",
                    "refund_cents": 1990,
                },
            )
            assert response.status_code == 200, response.text
            assert await worker.run_once()
            assert await worker.run_once()
            view = await refresh(client, view)
            assert view["damage_draft"]["review"]["result"]["refund_status"] == "succeeded"
            if delivered:
                async with factory() as session, session.begin():
                    later = datetime.now(UTC)
                    session.add(
                        UserRiskProfile(
                            tenant_id=ids.tenant,
                            user_id=ids.user,
                            return_count=4,
                            eligible_order_count=10,
                            window_start=later - timedelta(days=90),
                            window_end=later,
                            calculation_version="journey",
                            calculated_at=later,
                        )
                    )

            provider.queue(
                classified("delay_compensation"),
                {"action": "query_aftersales", "topic": "delay_compensation"},
                reply("可提交申请，由人工处理。"),
            )
            view, _ = await send(client, view, "这单超时，申请赔付")
            assert await worker.run_once()
            offered = (await history(client, view))[-1]["replies"][0]
            assert offered["delay_application_order_id"] == str(ids.order)
            endpoint = f"{LOGISTICS}/sessions/{view['session_id']}/orders/{ids.order}/tickets"
            payload = {"ticket_id": offered["reply_id"], "kind": "delay_compensation"}
            created = await client.post(endpoint, json=payload)
            assert created.status_code == 200, created.text
            ticket = created.json()
            duplicate = await client.post(endpoint, json={**payload, "ticket_id": str(uuid4())})
            assert duplicate.status_code == 200 and duplicate.json()["id"] == ticket["id"]
            decision = {"result": "承运记录确认超时，同意赔付", "refund_cents": amount}
            resolution = f"{LOGISTICS}/tickets/{ticket['id']}/resolution"
            assert (await client.post(resolution, json=decision)).status_code == 403
            app.dependency_overrides[get_logistics_reviewer] = lambda: ReviewerIdentity(
                ids.tenant,
                "logistics-reviewer",
                frozenset({"logistics_review", "delay_compensation_review"}),
            )
            queue = await client.get(f"{LOGISTICS}/tickets")
            [staff_ticket] = queue.json()
            [shipment] = staff_ticket["facts"]["shipments"]
            assert datetime.fromisoformat(shipment["promised_delivery_at"]) == promised
            assert shipment["delivered_at"] == (signed.isoformat() if signed else None)
            for _ in range(2):
                resolved = await client.post(resolution, json=decision)
                assert resolved.status_code == 200, resolved.text
                assert resolved.json()["compensation_cents"] == amount
            if delivered:
                assert await worker.run_once()  # Notify without executing before approval.
                assert not await worker.run_once()
                approvals = await client.get(APPROVALS)
                [approval] = approvals.json()
                assert approval["amount_cents"] == amount
                assert "return_rate_over_30_percent" in approval["reasons"]
                approved = await client.post(
                    f"{APPROVALS}/{approval['approval_id']}/decision",
                    json={"decision": "approved", "reason": "同意执行核准赔付金额"},
                )
                assert approved.status_code == 200, approved.text
            assert await worker.run_once()
            if succeeds:
                assert await worker.run_once()
            assert not await worker.run_once()
            view = await action(client, await refresh(client, view), "general")
            page = await client.get(f"{ROOT}/sessions/{view['session_id']}/messages")
            [notice] = page.json()["logistics_notices"]
            assert "确认超时" in notice["text"]
            assert ("模拟退款成功" if succeeds else "模拟退款失败") in notice["text"]
            async with factory() as session:
                rows = list(
                    await session.scalars(
                        select(FinancialAction).where(
                            FinancialAction.tenant_id == ids.tenant,
                            FinancialAction.order_id == ids.order,
                        )
                    )
                )
                assert sorted(row.amount_cents for row in rows) == sorted(
                    [1990, amount] if succeeds else [1990]
                )
                assert all(row.idempotency_key == f"case:{row.case_id}:REFUND" for row in rows)
                balance = await session.get(OrderRefundBalance, (ids.tenant, ids.balance))
                assert balance.reserved_cents == 0
                assert balance.refunded_cents == (1990 + amount if succeeds else 1990)
        finally:
            await client.aclose()
            await gateway.aclose()

    db(journey)
