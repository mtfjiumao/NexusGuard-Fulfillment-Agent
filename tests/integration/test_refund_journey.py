"""Whole customer and staff journey using the local simulated refund provider."""

from base64 import b64decode
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from nexusguard.api.reviewer import ReviewerIdentity, get_damage_reviewer, get_refund_supervisor
from nexusguard.infrastructure.models import (
    FinancialAction,
    OrderRefundBalance,
    UserRiskProfile,
    WorkflowInstance,
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

pytestmark = pytest.mark.integration
STAFF = "/api/v1/aftersales/damage-reviews"
APPROVALS = "/api/v1/approvals/refunds"
PHOTO = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII="
)
SUPPLEMENT_PHOTO = PHOTO + b"additional-photo"


async def photo(client, view, content):
    draft = view["damage_draft"]
    item = next(row for row in draft["products"] if row["included"])
    url = (
        f"{ROOT}/sessions/{view['session_id']}/damage/{draft['draft_id']}"
        f"/items/{item['draft_item_id']}/photos/{uuid4()}?expected_version={view['version']}"
    )
    response = await client.post(url, content=content, headers={"Content-Type": "image/png"})
    assert response.status_code == 200, response.text
    return response.json()


async def submit_and_review(client, view, evidence, expected_photos=(PHOTO,)):
    draft_id = view["damage_draft"]["draft_id"]
    submission_id = uuid4()
    endpoint = f"{ROOT}/sessions/{view['session_id']}/damage/{draft_id}/reviews"
    data = {
        "submission_id": str(submission_id),
        "expected_version": view["version"],
        "requested_resolution": "退一件破损商品的钱",
    }
    first = await client.post(endpoint, json=data)
    assert first.status_code == 200, first.text
    duplicate = await client.post(endpoint, json=data)
    assert duplicate.status_code == 200, duplicate.text
    assert (
        duplicate.json()["damage_draft"]["review"]["revision"]
        == (first.json()["damage_draft"]["review"]["revision"])
    )
    base = f"{STAFF}/{submission_id}"
    detail = await client.get(base)
    assert detail.status_code == 200, detail.text
    entry = detail.json()["items"][0]
    actual_photos = []
    for attachment in entry["photos"]:
        image = await client.get(
            f"{base}/items/{entry['item_id']}/photos/{attachment['attachment_id']}"
        )
        assert image.status_code == 200
        actual_photos.append(image.content)
    assert sorted(actual_photos) == sorted(expected_photos)
    premature = await client.post(
        f"{base}/resolution",
        json={
            "outcome": "approved",
            "reason": "材料符合",
            "resolution": "同意退款",
            "refund_cents": 1990,
        },
    )
    assert premature.status_code == 409
    checked = await client.post(
        f"{base}/items/{entry['item_id']}/decision",
        json={"decision": evidence, "reason": "请补拍包装" if evidence != "passed" else "材料符合"},
    )
    assert checked.status_code == 200, checked.text
    pending = await client.get(STAFF, params={"status": "awaiting_resolution"})
    assert pending.status_code == 200, pending.text
    assert str(submission_id) in {item["review_id"] for item in pending.json()["reviews"]}
    return base


async def notices(client, view):
    response = await client.get(f"{ROOT}/sessions/{view['session_id']}/messages")
    assert response.status_code == 200, response.text
    return response.json()["application_notices"]


@pytest.mark.parametrize(
    ("scenario", "high_risk", "approved"),
    [
        ("success", False, True),
        ("failure", False, True),
        ("unknown", True, True),
        ("success", True, False),
    ],
)
def test_damage_application_and_simulated_refund(db, settings, scenario, high_risk, approved):
    async def journey(factory):
        ids, provider, gateway, worker, client, app = await setup(factory, settings)
        worker.refund_simulation = scenario
        reviewer = ReviewerIdentity(ids.tenant, "reviewer", frozenset({"damage_review"}))
        supervisor = ReviewerIdentity(ids.tenant, "supervisor", frozenset({"refund_approval"}))
        app.dependency_overrides[get_damage_reviewer] = lambda: reviewer
        now = datetime.now(UTC)
        async with factory() as session, session.begin():
            session.add(
                UserRiskProfile(
                    tenant_id=ids.tenant,
                    user_id=ids.user,
                    return_count=4 if high_risk else 3,
                    eligible_order_count=10,
                    window_start=now - timedelta(days=90),
                    window_end=now,
                    calculation_version="journey",
                    calculated_at=now,
                )
            )
        try:
            view = await create(client, ids)
            if scenario == "success" and not high_risk:
                provider.queue(
                    classified("recommendation", "none", "general"),
                    {"action": "search_products", "keyword": "Test item"},
                )
                view, _ = await send(client, view, "我想买 Test item")
                assert await worker.run_once()
                card = (await history(client, view))[-1]["replies"][0]["product_cards"][0]
                detail = await client.get(f"/api/v1/presales{card['url']}")
                assert detail.status_code == 200 and detail.json()["sku_id"] == str(ids.sku)
                view = await create(client, ids)
            view = await action(client, view, "confirm")
            provider.queue(classified(), {"action": "inspect_damage"}, collect(ids.item), reply())
            view, _ = await send(client, view, "Test item 一个发霉")
            assert await worker.run_once()
            view = await photo(client, await refresh(client, view), PHOTO)
            if scenario == "success" and not high_risk:
                base = await submit_and_review(client, view, "needs_supplement")
                supplement = await client.post(
                    f"{base}/resolution",
                    json={
                        "outcome": "needs_supplement",
                        "reason": "包装看不清",
                        "resolution": "请补拍包装",
                    },
                )
                assert supplement.status_code == 200, supplement.text
                assert await worker.run_once()
                assert "请补拍包装" in (await notices(client, view))[0]["text"]
                view = await photo(client, await refresh(client, view), SUPPLEMENT_PHOTO)
                base = await submit_and_review(client, view, "passed", (PHOTO, SUPPLEMENT_PHOTO))
            else:
                base = await submit_and_review(client, view, "passed")
            assert len(await notices(client, view)) == int(scenario == "success" and not high_risk)
            payload = {
                "outcome": "approved",
                "reason": "确认为破损",
                "resolution": "同意退一件商品的钱",
                "refund_cents": 1990,
            }
            for _ in range(2):
                resolved = await client.post(f"{base}/resolution", json=payload)
                assert resolved.status_code == 200, resolved.text
            initial = "waiting_approval" if high_risk else "ready_to_execute"
            assert resolved.json()["result"]["refund_status"] == initial
            if high_risk:
                assert await worker.run_once()  # Deliver the human decision while refund is parked.
                assert not await worker.run_once()
                assert (await client.get(APPROVALS)).status_code == 401
                app.dependency_overrides[get_refund_supervisor] = lambda: supervisor
                queue = await client.get(APPROVALS)
                assert queue.status_code == 200, queue.text
                approval = queue.json()[0]
                assert "return_rate_over_30_percent" in approval["reasons"]
                assert "等待主管审批" in (await notices(client, view))[0]["text"]
                decision = {"decision": "approved" if approved else "rejected", "reason": "已复核"}
                for _ in range(2):
                    response = await client.post(
                        f"{APPROVALS}/{approval['approval_id']}/decision", json=decision
                    )
                    assert response.status_code == 200, response.text
            if approved:
                assert await worker.run_once()
                assert await worker.run_once()
            if scenario == "unknown":
                state = (await refresh(client, view))["damage_draft"]["review"]["result"]
                assert state["refund_status"] == "reconciling"
                async with factory() as session, session.begin():
                    balance = await session.scalar(
                        select(OrderRefundBalance).where(
                            OrderRefundBalance.tenant_id == ids.tenant,
                        )
                    )
                    assert balance.reserved_cents == 1990 and balance.refunded_cents == 0
                    workflow = await session.scalar(
                        select(WorkflowInstance).where(
                            WorkflowInstance.tenant_id == ids.tenant,
                            WorkflowInstance.state == "reconciling",
                        )
                    )
                    workflow.next_run_at = datetime.now(UTC)
                assert await worker.run_once()
            state = (await refresh(client, view))["damage_draft"]["review"]["result"]
            expected = (
                "rejected" if not approved else "failed" if scenario == "failure" else "succeeded"
            )
            assert state["refund_status"] == expected
            if not approved:
                assert "已复核" in state["refund_status_text"]
            view = await action(client, await refresh(client, view), "general")
            assert state["refund_status_text"] in (await notices(client, view))[0]["text"]
            assert not await worker.run_once()
            async with factory() as session:
                actions = list(
                    await session.scalars(
                        select(FinancialAction).where(
                            FinancialAction.tenant_id == ids.tenant,
                        )
                    )
                )
                assert len(actions) == int(approved)
                if approved:
                    assert actions[0].idempotency_key == f"{ids.order}:REFUND"
                    balance = await session.scalar(
                        select(OrderRefundBalance).where(
                            OrderRefundBalance.tenant_id == ids.tenant,
                        )
                    )
                    assert balance.reserved_cents == 0
                    assert balance.refunded_cents == (1990 if expected == "succeeded" else 0)
        finally:
            await client.aclose()
            await gateway.aclose()

    db(journey)
