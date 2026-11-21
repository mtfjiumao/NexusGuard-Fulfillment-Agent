from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update

from nexusguard.api.reviewer import ReviewerIdentity, get_logistics_reviewer
from nexusguard.infrastructure.models import Order, Shipment
from tests.integration.test_customer_journey import (
    ROOT,
    action,
    classified,
    create,
    history,
    refresh,
    reply,
    send,
    setup,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("cleared", [False, True])
def test_investigation_confirmation_and_original_session_notice(db, settings, cleared):
    async def journey(factory):
        ids, provider, gateway, worker, client, app = await setup(factory, settings)
        package = uuid4()
        other_order = uuid4()
        now = datetime.now(UTC)
        async with factory() as db, db.begin():
            db.add(
                Shipment(
                    tenant_id=ids.tenant,
                    id=package,
                    order_id=ids.order,
                    carrier="演示快递",
                    status="exception",
                    promised_delivery_at=now + timedelta(hours=1),
                )
            )
            db.add(
                Order(
                    tenant_id=ids.tenant,
                    id=other_order,
                    user_id=ids.user,
                    order_number="another",
                    ordered_at=now,
                    paid_amount_cents=0,
                    shipping_address_snapshot={},
                )
            )
        try:
            view = await action(client, await create(client, ids), "confirm")
            provider.queue(classified("tracking"), {"action": "query_logistics"}, reply("物流异常"))
            view, _ = await send(client, view, "请查询物流")
            assert await worker.run_once()
            rows = await history(client, view)
            offered = rows[-1]["replies"][0]
            assert offered["logistics_ticket_order_id"] == str(ids.order)
            endpoint = f"/api/v1/logistics/sessions/{view['session_id']}/orders/{ids.order}/tickets"
            payload = {"ticket_id": offered["reply_id"]}
            if cleared:
                async with factory() as db, db.begin():
                    await db.execute(
                        update(Shipment)
                        .where(
                            Shipment.tenant_id == ids.tenant,
                            Shipment.id == package,
                        )
                        .values(status="in_transit")
                    )
                denied = await client.post(endpoint, json=payload)
                assert denied.status_code == 409
                assert denied.json()["detail"] == "logistics_exception_cleared"
                return
            first = await client.post(endpoint, json=payload)
            assert first.status_code == 200, first.text
            for retry in (payload, {"ticket_id": str(uuid4())}):
                again = await client.post(endpoint, json=retry)
                assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
            queue = "/api/v1/logistics/tickets"
            assert (await client.get(queue)).status_code == 401
            app.dependency_overrides[get_logistics_reviewer] = lambda: ReviewerIdentity(
                ids.tenant, "staff", frozenset({"logistics_review"})
            )
            tickets = (await client.get(queue)).json()
            assert (
                len(tickets) == 1 and tickets[0]["facts"]["shipments"][0]["status"] == "exception"
            )
            view = await action(client, await refresh(client, view), "other_orders")
            view = await action(client, view, "select_order", order_id=str(other_order))
            endpoint = f"{queue}/{first.json()['id']}/resolution"
            for _ in range(2):
                assert (
                    await client.post(endpoint, json={"result": "承运方已接手查询"})
                ).status_code == 200
            assert await worker.run_once()
            assert not await worker.run_once()
            page = await client.get(f"{ROOT}/sessions/{view['session_id']}/messages")
            notices = page.json()["logistics_notices"]
            assert len(notices) == 1 and "承运方已接手查询" in notices[0]["text"]
        finally:
            await client.aclose()
            await gateway.aclose()

    db(journey)
