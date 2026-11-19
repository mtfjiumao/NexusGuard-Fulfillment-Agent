"""Customer address updates and the shared warehouse fulfillment gate."""

from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.infrastructure.models import AuditLog, Order
from nexusguard.modules.supervisor.repository import ReceptionRepository


class ShippingAddress(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    recipient: str = Field(min_length=1, max_length=60, pattern=r"^[^\x00-\x1f\x7f]+$")
    phone: str = Field(pattern=r"^\+?[0-9]{7,15}$")
    region: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")
    street: str = Field(min_length=1, max_length=250, pattern=r"^[^\x00-\x1f\x7f]+$")


class AddressChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    address: ShippingAddress


class AddressForm(BaseModel):
    order_number: str
    version: int
    address: dict[str, str]


def address_change_blocker(order: Order) -> str | None:
    if order.payment_status == "closed" or order.fulfillment_status == "cancelled":
        return "order_closed"
    if order.fulfillment_status == "processing":
        return "warehouse_processing"
    if order.fulfillment_status != "unshipped":
        return "order_already_shipped"
    return None


ADDRESS_BLOCKED_TEXT = {
    "order_closed": "订单已关闭或取消，无法自助修改收货地址。",
    "warehouse_processing": "订单已进入出库，无法自助修改收货地址。",
    "order_already_shipped": "订单已发货，无法自助修改收货地址。",
}


async def address_order(
    repo: ReceptionRepository, session_id: UUID, order_id: UUID, *, lock: bool = False
) -> Order:
    if not await repo.active_user():
        raise HTTPException(403, "customer_unavailable")
    reception = await repo.reception(session_id, lock=lock)
    if reception is None:
        raise HTTPException(404, "reception_not_found")
    if reception.bound_order_id != order_id:
        raise HTTPException(409, "order_binding_changed")
    query = repo.orders().where(Order.id == order_id)
    order = await repo.db.scalar(query.with_for_update() if lock else query)
    if order is None:
        raise HTTPException(404, "order_not_found")
    if blocker := address_change_blocker(order):
        raise HTTPException(409, blocker)
    return order


async def change_address(
    repo: ReceptionRepository,
    session_id: UUID,
    order_id: UUID,
    data: AddressChangeRequest,
    trace_id: UUID,
) -> None:
    order = await address_order(repo, session_id, order_id, lock=True)
    address = data.address.model_dump()
    if order.shipping_address_snapshot == address:
        # Retrying identical data after an uncertain response does not create another write.
        return
    if order.version != data.expected_version:
        raise HTTPException(409, "order_version_conflict")
    previous_version = order.version
    order.shipping_address_snapshot = address
    await repo.db.flush()
    repo.db.add(
        AuditLog(
            tenant_id=order.tenant_id,
            actor_type="user",
            actor_id=str(repo.identity.user_id),
            action="order.shipping_address_changed",
            resource_type="order",
            resource_id=order.id,
            from_state=order.fulfillment_status,
            to_state=order.fulfillment_status,
            trace_id=trace_id,
            details={
                "session_id": str(session_id),
                "previous_version": previous_version,
                "order_version": order.version,
            },
        )
    )


async def start_packing(db: AsyncSession, tenant_id: UUID, order_id: UUID) -> Order:
    """Reserve an order for fulfillment and return its current address under the same lock."""
    order = await db.scalar(
        select(Order).where(Order.tenant_id == tenant_id, Order.id == order_id).with_for_update()
    )
    if order is None:
        raise HTTPException(404, "order_not_found")
    if blocker := address_change_blocker(order):
        raise HTTPException(409, blocker)
    if order.payment_status != "paid":
        raise HTTPException(409, "order_not_paid")
    order.fulfillment_status = "processing"
    await db.flush()
    db.add(
        AuditLog(
            tenant_id=order.tenant_id,
            actor_type="system",
            actor_id="warehouse",
            action="order.packing_started",
            resource_type="order",
            resource_id=order.id,
            from_state="unshipped",
            to_state="processing",
            trace_id=uuid4(),
            details={"order_version": order.version},
        )
    )
    return order
