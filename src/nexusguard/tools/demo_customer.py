"""Local customer demo. Run this module with setup, then serve."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import PostgresDsn
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.customer import CustomerIdentity
from nexusguard.api.reviewer import ReviewerIdentity
from nexusguard.core.config import Settings, get_settings
from nexusguard.infrastructure.database import build_engine
from nexusguard.infrastructure.models import (
    SKU,
    LogisticsEvent,
    Order,
    OrderItem,
    Shipment,
    Tenant,
    User,
    UserRiskProfile,
)
from nexusguard.main import create_app
from nexusguard.modules.logistics.address import start_packing
from nexusguard.modules.payments.refunds import SimulationOutcome
from nexusguard.modules.supervisor.agent_worker import AgentWorker

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEMO_DB = "nexusguard_demo"
TENANT_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/tenant")
USER_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/customer")
ORDER_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/order")
SKU_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/sku")
ITEM_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/order-item")
BANANA_SKU_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/banana")
ADDRESS_ORDER_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/address-order")
ADDRESS_ITEM_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/address-item")
LOGISTICS_ORDER_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/logistics-order")
LOGISTICS_ITEM_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/logistics-item")
LOGISTICS_SHIPMENT_ID = uuid5(NAMESPACE_URL, "nexusguard/local-demo/logistics-shipment")


def demo_settings() -> Settings:
    settings = get_settings()
    url = make_url(str(settings.database_url))
    if settings.environment != "development" or url.host not in LOCAL_HOSTS or url.query:
        raise RuntimeError(
            "The demo requires development settings and a direct local database URL."
        )
    demo_url = url.set(database=DEMO_DB).render_as_string(hide_password=False)
    return settings.model_copy(update={"database_url": PostgresDsn(demo_url)})


async def setup(*, high_risk: bool = False) -> None:
    engine = build_engine(demo_settings())
    try:
        async with engine.begin() as db:
            config = Config("alembic.ini")

            def upgrade(connection):
                config.attributes["connection"] = connection
                command.upgrade(config, "head")

            await db.run_sync(upgrade)
            now = datetime.now(UTC)
            ordered = now - timedelta(days=2)
            rows = [
                insert(Tenant).values(id=TENANT_ID, name="演示店铺").on_conflict_do_nothing(),
                insert(User)
                .values(
                    tenant_id=TENANT_ID,
                    id=USER_ID,
                    external_id="demo-customer",
                )
                .on_conflict_do_nothing(),
                insert(SKU)
                .values(
                    tenant_id=TENANT_ID,
                    id=SKU_ID,
                    sku_code="DEMO-APPLE",
                    product_id="demo-apple",
                    title="红富士苹果",
                    category="fresh",
                    price_cents=1990,
                    specifications={"规格": "每盒 4 个"},
                )
                .on_conflict_do_nothing(),
                insert(SKU)
                .values(
                    tenant_id=TENANT_ID,
                    id=BANANA_SKU_ID,
                    sku_code="DEMO-BANANA",
                    product_id="demo-banana",
                    title="进口香蕉",
                    category="fresh",
                    price_cents=1290,
                    specifications={
                        "规格": "约 500 克 / 份",
                        "image_path": "/assets/reception/catalog/banana.jpg",
                    },
                )
                .on_conflict_do_nothing(),
                insert(Order)
                .values(
                    tenant_id=TENANT_ID,
                    id=ORDER_ID,
                    user_id=USER_ID,
                    order_number="DEMO-1001",
                    payment_status="paid",
                    fulfillment_status="delivered",
                    paid_amount_cents=3980,
                    ordered_at=ordered,
                    paid_at=ordered,
                    delivered_at=now - timedelta(days=1),
                    shipping_address_snapshot={},
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "id"],
                    set_={
                        "ordered_at": ordered,
                        "paid_at": ordered,
                        "delivered_at": now - timedelta(days=1),
                        "version": Order.version + 1,
                    },
                ),
                insert(OrderItem)
                .values(
                    tenant_id=TENANT_ID,
                    id=ITEM_ID,
                    order_id=ORDER_ID,
                    sku_id=SKU_ID,
                    quantity=2,
                    paid_amount_cents=3980,
                    category_snapshot="fresh",
                    sku_snapshot={"title": "红富士苹果", "specification": "每盒 4 个"},
                )
                .on_conflict_do_nothing(),
                insert(Order)
                .values(
                    tenant_id=TENANT_ID,
                    id=ADDRESS_ORDER_ID,
                    user_id=USER_ID,
                    order_number="DEMO-1002",
                    payment_status="paid",
                    fulfillment_status="unshipped",
                    paid_amount_cents=1290,
                    ordered_at=ordered - timedelta(minutes=5),
                    paid_at=ordered - timedelta(minutes=5),
                    shipping_address_snapshot={
                        "recipient": "演示顾客",
                        "phone": "00000000000",
                        "region": "演示省演示市演示区",
                        "street": "演示路 1 号",
                    },
                )
                .on_conflict_do_nothing(),
                insert(OrderItem)
                .values(
                    tenant_id=TENANT_ID,
                    id=ADDRESS_ITEM_ID,
                    order_id=ADDRESS_ORDER_ID,
                    sku_id=BANANA_SKU_ID,
                    quantity=1,
                    paid_amount_cents=1290,
                    category_snapshot="fresh",
                    sku_snapshot={"title": "进口香蕉", "specification": "约 500 克"},
                )
                .on_conflict_do_nothing(),
            ]
            for statement in rows:
                await db.execute(statement)
            await seed_logistics_demo(db, ordered)
            await db.execute(
                insert(UserRiskProfile).values(
                    tenant_id=TENANT_ID,
                    user_id=USER_ID,
                    return_count=4 if high_risk else 1,
                    eligible_order_count=10,
                    window_start=now - timedelta(days=90),
                    window_end=now,
                    calculation_version="local-demo",
                    calculated_at=now,
                )
            )
    finally:
        await engine.dispose()


async def seed_logistics_demo(db, ordered: datetime) -> None:
    ordered -= timedelta(hours=1)
    rows = [
        insert(Order).values(
            tenant_id=TENANT_ID,
            id=LOGISTICS_ORDER_ID,
            user_id=USER_ID,
            order_number="DEMO-1003",
            payment_status="paid",
            fulfillment_status="shipped",
            paid_amount_cents=1290,
            ordered_at=ordered,
            paid_at=ordered,
            shipping_address_snapshot={},
        ),
        insert(OrderItem).values(
            tenant_id=TENANT_ID,
            id=LOGISTICS_ITEM_ID,
            order_id=LOGISTICS_ORDER_ID,
            sku_id=BANANA_SKU_ID,
            quantity=1,
            paid_amount_cents=1290,
            category_snapshot="fresh",
            sku_snapshot={"title": "进口香蕉"},
        ),
        insert(Shipment).values(
            tenant_id=TENANT_ID,
            id=LOGISTICS_SHIPMENT_ID,
            order_id=LOGISTICS_ORDER_ID,
            carrier="演示快递",
            tracking_no="NG-DEMO-1003",
            status="exception",
            shipped_at=ordered + timedelta(hours=2),
            promised_delivery_at=ordered + timedelta(days=1),
        ),
        insert(LogisticsEvent).values(
            tenant_id=TENANT_ID,
            shipment_id=LOGISTICS_SHIPMENT_ID,
            source="demo",
            source_event_id="exception-1003",
            event_type="exception",
            occurred_at=ordered + timedelta(hours=16),
        ),
    ]
    for statement in rows:
        await db.execute(statement.on_conflict_do_nothing())


async def pack_demo_order() -> None:
    engine = build_engine(demo_settings())
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db, db.begin():
            order = await start_packing(db, TENANT_ID, ADDRESS_ORDER_ID)
        print(f"{order.order_number} 已进入出库，仓库读取到订单版本 {order.version}。")
    except HTTPException as error:
        raise SystemExit(str(error.detail)) from None
    finally:
        await engine.dispose()


def create_demo_app(
    *,
    reviewer: bool = False,
    supervisor: bool = False,
    refund_outcome: SimulationOutcome = "success",
) -> FastAPI:
    settings = demo_settings()
    app = create_app(settings)
    base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with base_lifespan(app):
            if reviewer or supervisor:
                yield
                return
            worker = AgentWorker(
                app.state.session_factory,
                app.state.model_gateway,
                settings,
                refund_simulation=refund_outcome,
            )
            stop = asyncio.Event()
            task = asyncio.create_task(worker.run(stop))
            try:
                yield
            finally:
                stop.set()
                await task

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def inject_demo_customer(request: Request, call_next):
        if (
            request.client is None
            or request.client.host not in LOCAL_HOSTS
            or request.url.hostname not in LOCAL_HOSTS
        ):
            return JSONResponse({"detail": "local_demo_only"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin is not None:
            if origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "local_demo_origin_required"}, status_code=403)
        if supervisor:
            request.state.reviewer_identity = ReviewerIdentity(
                TENANT_ID, "demo-supervisor", frozenset({"refund_approval"})
            )
        elif reviewer:
            request.state.reviewer_identity = ReviewerIdentity(
                TENANT_ID, "demo-reviewer", frozenset({"damage_review", "logistics_review"})
            )
        else:
            request.state.customer_identity = CustomerIdentity(TENANT_ID, USER_ID)
        return await call_next(request)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "serve", "pack"))
    parser.add_argument("--port", type=int, default=8001)
    roles = parser.add_mutually_exclusive_group()
    roles.add_argument("--reviewer", action="store_true", help="Use the local reviewer identity.")
    roles.add_argument("--supervisor", action="store_true", help="Use the local approval identity.")
    parser.add_argument(
        "--refund-outcome", choices=("success", "failure", "unknown"), default="success"
    )
    parser.add_argument("--high-risk", action="store_true", help="Seed a 40 percent return rate.")
    args = parser.parse_args()
    if args.action == "setup":
        asyncio.run(setup(high_risk=args.high_risk))
    elif args.action == "pack":
        asyncio.run(pack_demo_order())
    else:
        uvicorn.run(
            create_demo_app(
                reviewer=args.reviewer,
                supervisor=args.supervisor,
                refund_outcome=args.refund_outcome,
            ),
            host="127.0.0.1",
            port=args.port,
            proxy_headers=False,
        )


if __name__ == "__main__":
    main()
