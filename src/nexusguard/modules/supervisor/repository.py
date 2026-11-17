from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.customer import CustomerIdentity
from nexusguard.infrastructure.models import SKU, AfterSalesCase, Order, Shipment, User
from nexusguard.infrastructure.models.reception import ReceptionMessage, ReceptionSession


class ReceptionRepository:
    def __init__(self, session: AsyncSession, identity: CustomerIdentity):
        self.db = session
        self.identity = identity

    async def active_user(self) -> bool:
        return bool(
            await self.db.scalar(
                select(
                    exists().where(
                        User.tenant_id == self.identity.tenant_id,
                        User.id == self.identity.user_id,
                        User.status == "active",
                    )
                )
            )
        )

    def orders(self) -> Select[tuple[Order]]:
        return select(Order).where(
            Order.tenant_id == self.identity.tenant_id,
            Order.user_id == self.identity.user_id,
        )

    async def order(self, order_id: UUID | None) -> Order | None:
        if order_id is None:
            return None
        return await self.db.scalar(self.orders().where(Order.id == order_id))

    async def order_number(self, number: str | None) -> Order | None:
        return await self.db.scalar(self.orders().where(Order.order_number == number))

    @staticmethod
    def open_case():
        return exists().where(
            AfterSalesCase.tenant_id == Order.tenant_id,
            AfterSalesCase.order_id == Order.id,
            AfterSalesCase.status == "open",
        )

    async def recommend(self, now: datetime, days: int) -> Order | None:
        moving = exists().where(
            Shipment.tenant_id == Order.tenant_id,
            Shipment.order_id == Order.id,
            Shipment.status.in_(["shipped", "in_transit", "exception"]),
        )
        return await self.db.scalar(
            self.orders()
            .where(
                Order.ordered_at <= now,
                or_(
                    Order.ordered_at >= now - timedelta(days=days),
                    Order.fulfillment_status == "shipped",
                    moving,
                    self.open_case(),
                ),
            )
            .order_by(Order.ordered_at.desc(), Order.id.desc())
            .limit(1)
        )

    async def order_page(self, limit: int, offset: int):
        result = await self.db.execute(
            self.orders()
            .add_columns(self.open_case().label("has_open_case"))
            .order_by(Order.ordered_at.desc(), Order.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
        return result.all()

    async def has_open_case(self, order_id: UUID) -> bool:
        return bool(
            await self.db.scalar(
                select(
                    exists().where(
                        AfterSalesCase.tenant_id == self.identity.tenant_id,
                        AfterSalesCase.user_id == self.identity.user_id,
                        AfterSalesCase.order_id == order_id,
                        AfterSalesCase.status == "open",
                    )
                )
            )
        )

    async def sku(self, sku_id: UUID | None) -> SKU | None:
        if sku_id is None:
            return None
        return await self.db.scalar(
            select(SKU).where(
                SKU.tenant_id == self.identity.tenant_id,
                SKU.id == sku_id,
                SKU.sale_status == "on_sale",
            )
        )

    async def reception(self, reception_id: UUID, *, lock: bool = False) -> ReceptionSession | None:
        query = select(ReceptionSession).where(
            ReceptionSession.tenant_id == self.identity.tenant_id,
            ReceptionSession.user_id == self.identity.user_id,
            ReceptionSession.id == reception_id,
        )
        if lock:
            query = query.with_for_update()
        return await self.db.scalar(query)

    async def pending(self, reception_id: UUID) -> ReceptionMessage | None:
        return await self.db.scalar(
            select(ReceptionMessage).where(
                ReceptionMessage.tenant_id == self.identity.tenant_id,
                ReceptionMessage.session_id == reception_id,
                ReceptionMessage.status == "needs_order",
            )
        )

    async def latest(self, reception_id: UUID) -> ReceptionMessage | None:
        return await self.db.scalar(
            select(ReceptionMessage)
            .where(
                ReceptionMessage.tenant_id == self.identity.tenant_id,
                ReceptionMessage.session_id == reception_id,
            )
            .order_by(ReceptionMessage.created_at.desc(), ReceptionMessage.id.desc())
            .limit(1)
        )

    async def inflight(self, reception_id: UUID) -> bool:
        return bool(
            await self.db.scalar(
                select(
                    exists().where(
                        ReceptionMessage.tenant_id == self.identity.tenant_id,
                        ReceptionMessage.session_id == reception_id,
                        ReceptionMessage.status.in_(["queued", "processing", "ready_for_agent"]),
                    )
                )
            )
        )

    async def message(self, message_id: UUID) -> ReceptionMessage | None:
        return await self.db.get(ReceptionMessage, (self.identity.tenant_id, message_id))
