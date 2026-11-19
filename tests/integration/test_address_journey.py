"""Exercise the customer form and warehouse entry point against real PostgreSQL."""

from contextlib import AsyncExitStack

import pytest
from sqlalchemy import select

from nexusguard.api.customer import CustomerIdentity, get_customer_identity
from nexusguard.infrastructure.models import AuditLog, Order
from nexusguard.modules.logistics.address import start_packing
from tests.integration.test_customer_journey import (
    action,
    classified,
    create,
    history,
    refresh,
    send,
    setup,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("warehouse_first", [False, True])
def test_address_change_and_warehouse_gate(db, settings, warehouse_first):
    async def journey(factory):
        ids, provider, gateway, worker, client, app = await setup(factory, settings)
        new_address = {
            "recipient": "演示顾客",
            "phone": "00000000000",
            "region": "演示省演示市演示区",
            "street": "演示路 2 号",
        }
        async with AsyncExitStack() as stack:
            stack.push_async_callback(gateway.aclose)
            await stack.enter_async_context(client)
            view = await action(client, await create(client, ids), "confirm")
            provider.queue(classified("address_change"))
            view, _ = await send(client, view, "这单改个地址")
            assert await worker.run_once()
            view = await refresh(client, view)
            replies = (await history(client, view))[-1]["replies"]
            assert replies[0]["address_form_order_id"] == str(ids.order)
            assert provider.models == ["qwen3.8-flash"]
            endpoint = (
                f"/api/v1/logistics/sessions/{view['session_id']}"
                f"/orders/{ids.order}/shipping-address"
            )
            form = await client.get(endpoint)
            assert form.status_code == 200 and form.headers["cache-control"] == "private, no-store"
            payload = {"expected_version": form.json()["version"], "address": new_address}

            if warehouse_first:
                async with factory() as session, session.begin():
                    packed = await start_packing(session, ids.tenant, ids.order)
                    assert packed.shipping_address_snapshot == {"fixture": True}
                rejected = await client.put(endpoint, json=payload)
                assert rejected.status_code == 409
                assert rejected.json()["detail"] == "warehouse_processing"
            else:
                for _ in range(2):
                    updated = await client.put(endpoint, json=payload)
                    assert updated.status_code == 200, updated.text
                app.dependency_overrides[get_customer_identity] = lambda: CustomerIdentity(
                    ids.tenant, ids.item
                )
                denied = await client.put(endpoint, json=payload)
                assert denied.status_code == 403
                async with factory() as session, session.begin():
                    packed = await start_packing(session, ids.tenant, ids.order)
                    assert packed.shipping_address_snapshot == new_address

            async with factory() as session:
                order = await session.get(Order, (ids.tenant, ids.order))
                assert order.fulfillment_status == "processing"
                assert order.shipping_address_snapshot == (
                    {"fixture": True} if warehouse_first else new_address
                )
                audits = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.tenant_id == ids.tenant,
                            AuditLog.resource_id == ids.order,
                            AuditLog.action == "order.shipping_address_changed",
                        )
                    )
                )
                assert len(audits) == int(not warehouse_first)
                assert all(new_address["street"] not in str(row.details) for row in audits)

    db(journey)
