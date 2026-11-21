"""Customer-confirmed logistics investigations and staff results."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.reviewer import ReviewerIdentity
from nexusguard.infrastructure.models import AuditLog, LogisticsTicket, Order, OutboxEvent
from nexusguard.modules.supervisor.read_tools import ReadOnlyTools
from nexusguard.modules.supervisor.repository import ReceptionRepository

TICKET_RESOLVED = "logistics.ticket_resolved"


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID


class TicketResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    result: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00]+$")


class TicketView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_number: str
    created_at: datetime
    resolved_at: datetime | None
    result: str | None


class StaffTicketView(TicketView):
    facts: dict
    reviewer_id: str | None


class TicketNotice(BaseModel):
    id: UUID
    created_at: datetime
    text: str


def exception_shipments(facts: dict) -> list[dict]:
    if not facts.get("shipments") or facts["order"]["fulfillment_status"] == "cancelled":
        return []
    observed_at = datetime.fromisoformat(facts["observed_at"])
    return [
        shipment
        for shipment in facts["shipments"]
        if shipment["status"] == "exception"
        or (
            shipment["status"] != "delivered"
            and datetime.fromisoformat(shipment["promised_delivery_at"]) < observed_at
        )
    ]


def tickets_for_order(repo: ReceptionRepository, order: Order):
    return select(LogisticsTicket).where(
        LogisticsTicket.tenant_id == repo.identity.tenant_id,
        LogisticsTicket.user_id == repo.identity.user_id,
        LogisticsTicket.order_id == order.id,
    )


async def latest_ticket(repo: ReceptionRepository, order: Order) -> TicketView | None:
    ticket = await repo.db.scalar(
        tickets_for_order(repo, order)
        .order_by(LogisticsTicket.created_at.desc(), LogisticsTicket.id.desc())
        .limit(1)
    )
    return TicketView.model_validate(ticket) if ticket else None


async def submit_ticket(
    repo: ReceptionRepository, session_id: UUID, order_id: UUID, ticket_id: UUID, trace_id: UUID
) -> TicketView:
    if not await repo.active_user():
        raise HTTPException(403, "customer_unavailable")
    reception = await repo.reception(session_id, lock=True)
    if reception is None:
        raise HTTPException(404, "reception_not_found")
    if reception.bound_order_id != order_id:
        raise HTTPException(409, "order_binding_changed")
    order = await repo.db.scalar(repo.orders().where(Order.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(404, "order_not_found")
    existing = await repo.db.get(LogisticsTicket, (repo.identity.tenant_id, ticket_id))
    if existing:
        if existing.session_id != session_id or existing.order_id != order_id:
            raise HTTPException(409, "logistics_ticket_id_conflict")
        return TicketView.model_validate(existing)
    pending = await repo.db.scalar(
        tickets_for_order(repo, order).where(LogisticsTicket.resolved_at.is_(None))
    )
    if pending:
        return TicketView.model_validate(pending)
    _, facts = await ReadOnlyTools(repo).logistics(order)
    affected = exception_shipments(facts)
    if not affected:
        raise HTTPException(409, "logistics_exception_cleared")
    ticket = LogisticsTicket(
        tenant_id=order.tenant_id,
        id=ticket_id,
        order_id=order.id,
        user_id=order.user_id,
        session_id=reception.id,
        order_number=order.order_number,
        facts={**facts, "shipments": affected},
    )
    repo.db.add(ticket)
    repo.db.add(
        AuditLog(
            tenant_id=order.tenant_id,
            actor_type="user",
            actor_id=str(order.user_id),
            action="logistics.ticket_requested",
            resource_type="logistics_ticket",
            resource_id=ticket_id,
            to_state="open",
            trace_id=trace_id,
        )
    )
    await repo.db.flush()
    return TicketView.model_validate(ticket)


async def resolve_ticket(
    db: AsyncSession,
    reviewer: ReviewerIdentity,
    ticket_id: UUID,
    result: str,
    trace_id: UUID,
) -> StaffTicketView:
    ticket = await db.get(LogisticsTicket, (reviewer.tenant_id, ticket_id), with_for_update=True)
    if ticket is None:
        raise HTTPException(404, "logistics_ticket_not_found")
    if ticket.resolved_at is not None:
        if ticket.result == result and ticket.reviewer_id == reviewer.reviewer_id:
            return StaffTicketView.model_validate(ticket)
        raise HTTPException(409, "logistics_ticket_closed")
    ticket.result = result
    ticket.resolved_at = datetime.now(UTC)
    ticket.reviewer_id = reviewer.reviewer_id
    db.add(
        AuditLog(
            tenant_id=ticket.tenant_id,
            actor_type="reviewer",
            actor_id=reviewer.reviewer_id,
            action=TICKET_RESOLVED,
            resource_type="logistics_ticket",
            resource_id=ticket.id,
            from_state="open",
            to_state="resolved",
            trace_id=trace_id,
        )
    )
    db.add(
        OutboxEvent(
            tenant_id=ticket.tenant_id,
            event_type=TICKET_RESOLVED,
            aggregate_type="logistics_ticket",
            aggregate_id=ticket.id,
            payload={},
        )
    )
    return StaffTicketView.model_validate(ticket)


async def ticket_notices(repo: ReceptionRepository, session_id: UUID) -> list[TicketNotice]:
    rows = await repo.db.scalars(
        select(LogisticsTicket)
        .where(
            LogisticsTicket.tenant_id == repo.identity.tenant_id,
            LogisticsTicket.user_id == repo.identity.user_id,
            LogisticsTicket.session_id == session_id,
            LogisticsTicket.notified_at.is_not(None),
        )
        .order_by(LogisticsTicket.notified_at.desc(), LogisticsTicket.id.desc())
        .limit(50)
    )
    return [
        TicketNotice(
            id=row.id,
            created_at=row.notified_at,
            text=f"订单 {row.order_number} 的人工查件结果\n{row.result}",
        )
        for row in rows
    ]
