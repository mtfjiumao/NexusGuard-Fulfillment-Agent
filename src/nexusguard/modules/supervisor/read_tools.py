"""Scoped read tools. No payment, coupon, address mutation or arbitrary SQL tool exists."""

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from nexusguard.infrastructure.models import (
    AfterSalesCase,
    Inventory,
    LogisticsEvent,
    Order,
    Shipment,
)
from nexusguard.modules.supervisor.repository import ReceptionRepository

PAYMENT = {"unpaid": "待付款", "paid": "已付款", "closed": "已关闭"}
FULFILLMENT = {
    "unshipped": "未发货",
    "processing": "出库处理中",
    "shipped": "已发货",
    "delivered": "已签收",
    "cancelled": "已取消",
}
SHIPMENT = {
    "pending": "待发运",
    "shipped": "已发运",
    "in_transit": "运输中",
    "delivered": "已签收",
    "exception": "物流异常",
}
CASE_STATUS = {"open": "处理中", "resolved": "已处理", "rejected": "已驳回", "cancelled": "已取消"}


def stamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def order_facts(order: Order) -> dict:
    return {
        "order_number": order.order_number,
        "payment_status": order.payment_status,
        "fulfillment_status": order.fulfillment_status,
        "ordered_at": stamp(order.ordered_at),
        "delivered_at": stamp(order.delivered_at),
    }


class ReadOnlyTools:
    def __init__(self, repo: ReceptionRepository):
        self.repo = repo
        self.db = repo.db
        self.tenant_id = repo.identity.tenant_id

    async def logistics(self, order: Order) -> tuple[str, dict]:
        # The order has already passed both tenant and user ownership checks.
        shipments = list(
            (
                await self.db.scalars(
                    select(Shipment)
                    .where(
                        Shipment.tenant_id == self.tenant_id,
                        Shipment.order_id == order.id,
                    )
                    .order_by(Shipment.created_at.desc(), Shipment.id.desc())
                    .limit(10)
                )
            ).all()
        )
        facts: dict = {
            "tool": "query_logistics",
            "order": order_facts(order),
            "observed_at": stamp(datetime.now(UTC)),
            "shipments": [],
        }
        if not shipments:
            return (
                f"订单 {order.order_number} 当前{FULFILLMENT[order.fulfillment_status]}，"
                "暂未查询到包裹记录；目前无法确认具体到达时间。",
                facts,
            )
        lines = [f"订单 {order.order_number} 查询到以下包裹（最多展示最近 10 个，时间为 UTC）："]
        for shipment in shipments:
            # Do not send carrier free-text payloads/instructions to the model or invent locations.
            events = list(
                (
                    await self.db.scalars(
                        select(LogisticsEvent)
                        .where(
                            LogisticsEvent.tenant_id == self.tenant_id,
                            LogisticsEvent.shipment_id == shipment.id,
                        )
                        .order_by(LogisticsEvent.occurred_at.desc(), LogisticsEvent.id.desc())
                        .limit(5)
                    )
                ).all()
            )
            last = events[0].occurred_at if events else shipment.shipped_at
            details = {
                "shipment_id": str(shipment.id),
                "carrier": shipment.carrier,
                "tracking_no": shipment.tracking_no,
                "status": shipment.status,
                "promised_delivery_at": stamp(shipment.promised_delivery_at),
                "last_update_at": stamp(last),
                "recent_events": [
                    {"type": e.event_type, "at": stamp(e.occurred_at)} for e in events
                ],
            }
            facts["shipments"].append(details)
            line = (
                f"{shipment.carrier} / {shipment.tracking_no or '暂无运单号'}："
                f"{SHIPMENT[shipment.status]}"
            )
            if last:
                line += f"，最近记录时间 {stamp(last)}"
            if shipment.status == "exception":
                line += "。系统标记物流异常，具体原因仍需承运方核实"
            elif shipment.status != "delivered" and shipment.promised_delivery_at < datetime.now(
                UTC
            ):
                line += "。已超过系统承诺送达时间；是否符合赔付条件仍需独立核验"
            line += "。" if not line.endswith("。") else ""
            lines.append(line)
        lines.append("数据库记录可能滞后；仅凭未更新还不能确认丢件或滞留原因。")
        return "\n".join(lines), facts

    async def presales(self, intent: str, sku_id: UUID | None) -> tuple[str, dict]:
        sku = await self.repo.sku(sku_id)
        facts: dict = {"tool": "query_sku", "sku_id": str(sku.id) if sku else None}
        if intent == "offers":
            return (
                "优惠券与活动规则尚未接入，暂时无法核实可用优惠。请说明商品和优惠券名称，后续需以实际规则核验。",
                facts,
            )
        if intent == "shipping_sla":
            return (
                "请说明需要咨询的商品。当前尚无可查询的发货时效规则，暂时不能承诺具体发货时间。",
                facts,
            )
        if sku is None:
            return "请先提供商品名称或从商品页进入，以便查询对应规格和库存。", facts
        facts.update({"title": sku.title, "sku_code": sku.sku_code})
        if intent == "stock":
            stock = await self.db.scalar(
                select(func.sum(Inventory.on_hand - Inventory.reserved)).where(
                    Inventory.tenant_id == self.tenant_id,
                    Inventory.sku_id == sku.id,
                )
            )
            stock = int(stock) if stock is not None else None
            facts["available_stock"] = stock
            if stock is None:
                return f"商品 {sku.title} 暂无可查询的库存记录；这不表示已售罄。", facts
            return f"商品 {sku.title} 当前可用库存为 {stock} 件，以下单时校验的库存为准。", facts
        # Values are displayed as plain text by Vue; never interpreted as instructions.
        specs = json.dumps(sku.specifications, ensure_ascii=False)
        facts["specifications"] = specs[:1500]
        return (
            f"商品 {sku.title} 的规格记录：{specs[:1500]}"
            if sku.specifications
            else f"商品 {sku.title} 暂无规格记录。",
            facts,
        )

    async def aftersales(self, intent: str, order: Order) -> tuple[str, dict]:
        facts: dict = {"tool": "query_aftersales", "order": order_facts(order)}
        if intent in {"case_progress", "case_materials"}:
            cases = list(
                (
                    await self.db.scalars(
                        select(AfterSalesCase)
                        .where(
                            AfterSalesCase.tenant_id == self.tenant_id,
                            AfterSalesCase.user_id == self.repo.identity.user_id,
                            AfterSalesCase.order_id == order.id,
                        )
                        .order_by(AfterSalesCase.created_at.desc(), AfterSalesCase.id.desc())
                        .limit(5)
                    )
                ).all()
            )
            facts["cases"] = [{"number": c.case_number, "status": c.status} for c in cases]
            if not cases:
                return (
                    f"订单 {order.order_number} 尚未查询到售后工单。"
                    "请说明具体问题，目前没有为你新建工单。",
                    facts,
                )
            result = "；".join(f"{c.case_number}：{CASE_STATUS[c.status]}" for c in cases)
            if intent == "case_materials":
                result += "。目前尚无材料要求清单可查询，请说明需要补充的材料类型或收到的通知内容"
            return f"订单 {order.order_number} 最近的售后记录：{result}。", facts
        labels = {
            "refund": "退款",
            "return": "退货",
            "exchange": "换货",
            "delay_compensation": "超时赔付",
        }
        requirements = "请说明希望处理的商品、原因及数量。"
        return (
            f"已了解订单 {order.order_number} 的{labels[intent]}诉求，当前订单"
            f"{FULFILLMENT[order.fulfillment_status]}。{requirements}"
            "后续还需核验商品规则和凭证，才能判断是否符合条件；本轮未创建售后工单或执行退款。",
            facts,
        )
