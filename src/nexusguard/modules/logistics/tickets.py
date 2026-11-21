"""Customer-confirmed logistics investigations and staff results."""

from datetime import UTC, datetime
from typing import Literal, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.reviewer import ReviewerIdentity
from nexusguard.infrastructure.models import (
    AfterSalesCase,
    ApprovalRequest,
    AuditLog,
    LogisticsTicket,
    Order,
    OutboxEvent,
    WorkflowInstance,
)
from nexusguard.modules.payments.refunds import (
    HumanRefundDecision,
    prepare_refund,
    refund_status_text,
)
from nexusguard.modules.supervisor.read_tools import ReadOnlyTools
from nexusguard.modules.supervisor.repository import ReceptionRepository

TICKET_RESOLVED = "logistics.ticket_resolved"
TicketKind = Literal["investigation", "delay_compensation"]
DELAY_REVIEW_PERMISSION = "delay_compensation_review"


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    kind: TicketKind = "investigation"


class TicketResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    result: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00]+$")
    refund_cents: int | None = Field(default=None, strict=True, gt=0, le=9_007_199_254_740_991)


class TicketView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: TicketKind
    order_number: str
    created_at: datetime
    resolved_at: datetime | None
    result: str | None
    refund_status_text: str | None = None
    compensation_approved: bool = False
    compensation_cents: int | None = None
    currency: str | None = None


class StaffTicketView(TicketView):
    facts: dict
    reviewer_id: str | None
    refund_case_id: UUID | None


TicketSchema = TypeVar("TicketSchema", bound=TicketView)


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


def delayed_shipments(facts: dict) -> list[dict]:
    order = facts["order"]
    if order["payment_status"] != "paid" or order["fulfillment_status"] == "cancelled":
        return []
    observed = datetime.fromisoformat(facts["observed_at"])
    affected = []
    for shipment in facts["shipments"]:
        promised = datetime.fromisoformat(shipment["promised_delivery_at"])
        delivered = shipment["delivered_at"]
        if not delivered and len(facts["shipments"]) == 1:
            delivered = order["delivered_at"]
        if delivered:
            late = datetime.fromisoformat(delivered) > promised
        else:
            late = (
                shipment["status"] != "delivered"
                and order["fulfillment_status"] != "delivered"
                and observed > promised
            )
        if late:
            affected.append({**shipment, "delivered_at": delivered})
    return affected


def tickets_for_order(repo: ReceptionRepository, order: Order, kind: TicketKind):
    return select(LogisticsTicket).where(
        LogisticsTicket.tenant_id == repo.identity.tenant_id,
        LogisticsTicket.user_id == repo.identity.user_id,
        LogisticsTicket.order_id == order.id,
        LogisticsTicket.kind == kind,
    )


async def latest_ticket(
    repo: ReceptionRepository, order: Order, kind: TicketKind = "investigation"
) -> TicketView | None:
    ticket = await repo.db.scalar(
        tickets_for_order(repo, order, kind)
        .order_by(LogisticsTicket.created_at.desc(), LogisticsTicket.id.desc())
        .limit(1)
    )
    if ticket is None:
        return None
    return await ticket_view(repo.db, ticket, TicketView)


async def ticket_view(
    db: AsyncSession, ticket: LogisticsTicket, schema: type[TicketSchema]
) -> TicketSchema:
    view = schema.model_validate(ticket)
    if ticket.refund_case_id is not None:
        view.compensation_approved = True
        row = (
            await db.execute(
                select(
                    WorkflowInstance.state,
                    ApprovalRequest.review_comment,
                    AfterSalesCase.approved_amount_cents,
                    AfterSalesCase.currency,
                )
                .join(
                    AfterSalesCase,
                    (AfterSalesCase.tenant_id == WorkflowInstance.tenant_id)
                    & (AfterSalesCase.id == WorkflowInstance.case_id),
                )
                .outerjoin(ApprovalRequest, approval_join())
                .where(
                    WorkflowInstance.tenant_id == ticket.tenant_id,
                    WorkflowInstance.case_id == ticket.refund_case_id,
                )
            )
        ).one()
        state, reason, view.compensation_cents, view.currency = row
        view.refund_status_text = refund_status_text(state, reason)
    return view


def approval_join():
    return (
        (ApprovalRequest.tenant_id == WorkflowInstance.tenant_id)
        & (ApprovalRequest.workflow_id == WorkflowInstance.id)
        & (ApprovalRequest.approval_round == 1)
    )


async def submit_ticket(
    repo: ReceptionRepository, session_id: UUID, order_id: UUID, data: TicketRequest, trace_id: UUID
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
    ticket_id = (
        uuid5(NAMESPACE_URL, f"nexusguard/delay-application/{data.ticket_id}")
        if data.kind == "delay_compensation"
        else data.ticket_id
    )
    existing = await repo.db.get(LogisticsTicket, (repo.identity.tenant_id, ticket_id))
    if existing:
        if (
            existing.session_id != session_id
            or existing.order_id != order_id
            or existing.kind != data.kind
        ):
            raise HTTPException(409, "logistics_ticket_id_conflict")
        return await ticket_view(repo.db, existing, TicketView)
    pending = await repo.db.scalar(
        tickets_for_order(repo, order, data.kind)
        .where(
            (LogisticsTicket.resolved_at.is_(None) | LogisticsTicket.refund_case_id.is_not(None))
            if data.kind == "delay_compensation"
            else LogisticsTicket.resolved_at.is_(None)
        )
        .order_by(LogisticsTicket.created_at.desc(), LogisticsTicket.id.desc())
        .limit(1)
    )
    if pending:
        return await ticket_view(repo.db, pending, TicketView)
    _, facts = await ReadOnlyTools(repo).logistics(order)
    affected = (
        delayed_shipments(facts)
        if data.kind == "delay_compensation"
        else exception_shipments(facts)
    )
    if not affected:
        raise HTTPException(409, "logistics_exception_cleared")
    ticket = LogisticsTicket(
        tenant_id=order.tenant_id,
        id=ticket_id,
        kind=data.kind,
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
            details={"kind": data.kind},
        )
    )
    await repo.db.flush()
    return TicketView.model_validate(ticket)


async def resolve_ticket(
    db: AsyncSession,
    reviewer: ReviewerIdentity,
    ticket_id: UUID,
    data: TicketResolution,
    trace_id: UUID,
) -> StaffTicketView:
    ticket = await db.get(LogisticsTicket, (reviewer.tenant_id, ticket_id), with_for_update=True)
    if ticket is None:
        raise HTTPException(404, "logistics_ticket_not_found")
    if ticket.kind == "delay_compensation" and DELAY_REVIEW_PERMISSION not in reviewer.permissions:
        raise HTTPException(403, "delay_compensation_review_required")
    if ticket.resolved_at is not None:
        case = (
            await db.get(AfterSalesCase, (ticket.tenant_id, ticket.refund_case_id))
            if ticket.refund_case_id
            else None
        )
        previous_amount = case.approved_amount_cents if case else None
        if (
            ticket.result == data.result
            and ticket.reviewer_id == reviewer.reviewer_id
            and data.refund_cents == previous_amount
        ):
            return await ticket_view(db, ticket, StaffTicketView)
        raise HTTPException(409, "logistics_ticket_closed")
    amount = data.refund_cents
    ticket.result = data.result
    ticket.resolved_at = datetime.now(UTC)
    ticket.reviewer_id = reviewer.reviewer_id
    if amount is not None:
        order = await db.get(Order, (ticket.tenant_id, ticket.order_id), with_for_update=True)
        if (
            ticket.kind != "delay_compensation"
            or order is None
            or order.payment_status != "paid"
            or order.fulfillment_status == "cancelled"
            or amount > order.paid_amount_cents
        ):
            raise HTTPException(422, "invalid_compensation_amount_or_order")
        ticket.refund_case_id = await prepare_refund(
            db,
            HumanRefundDecision(
                application_id=ticket.id,
                claim_type="delay_compensation",
                amount_cents=amount,
                reviewer_id=reviewer.reviewer_id,
                request="申请物流超时赔付",
                resolution="人工同意赔付",
                reason=data.result,
            ),
            order,
            trace_id,
        )
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
    await db.flush()
    return await ticket_view(db, ticket, StaffTicketView)


async def ticket_notices(repo: ReceptionRepository, session_id: UUID) -> list[TicketNotice]:
    rows = await repo.db.execute(
        select(
            LogisticsTicket, WorkflowInstance.state, ApprovalRequest.review_comment, AfterSalesCase
        )
        .outerjoin(
            WorkflowInstance,
            (WorkflowInstance.tenant_id == LogisticsTicket.tenant_id)
            & (WorkflowInstance.case_id == LogisticsTicket.refund_case_id),
        )
        .outerjoin(ApprovalRequest, approval_join())
        .outerjoin(
            AfterSalesCase,
            (AfterSalesCase.tenant_id == LogisticsTicket.tenant_id)
            & (AfterSalesCase.id == LogisticsTicket.refund_case_id),
        )
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
            text="\n".join(
                filter(
                    None,
                    (
                        f"订单 {row.order_number} 的"
                        + ("超时赔付结果" if row.kind == "delay_compensation" else "人工查件结果"),
                        row.result,
                        format_compensation(case) if case else None,
                        refund_status_text(state, comment),
                    ),
                )
            ),
        )
        for row, state, comment, case in rows
    ]


def format_compensation(case: AfterSalesCase) -> str:
    units, cents = divmod(case.approved_amount_cents, 100)
    return f"人工核准金额 {units}.{cents:02} {case.currency}。"
