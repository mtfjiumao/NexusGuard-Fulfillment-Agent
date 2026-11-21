"""Customer address changes and customer-confirmed logistics investigations."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.customer import CustomerIdentity, get_customer_identity
from nexusguard.api.reviewer import ReviewerIdentity, get_logistics_reviewer
from nexusguard.infrastructure.database import get_session
from nexusguard.infrastructure.models import LogisticsTicket
from nexusguard.modules.logistics.address import (
    AddressChangeRequest,
    AddressForm,
    address_order,
    change_address,
)
from nexusguard.modules.logistics.tickets import (
    StaffTicketView,
    TicketRequest,
    TicketResolution,
    TicketView,
    resolve_ticket,
    submit_ticket,
)
from nexusguard.modules.supervisor.repository import ReceptionRepository


class AddressRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            if request.method == "PUT":
                media_type = request.headers.get("content-type", "").partition(";")[0].strip()
                if media_type.lower() != "application/json":
                    return JSONResponse({"detail": "invalid_address"}, status_code=415)
            try:
                return await handler(request)
            except RequestValidationError:
                if not request.url.path.endswith("/shipping-address"):
                    raise
                # Default validation responses can echo full address fields.
                return JSONResponse({"detail": "invalid_address"}, status_code=422)

        return handle


class AddressChanged(BaseModel):
    status: Literal["saved"] = "saved"


router = APIRouter(route_class=AddressRoute)
Customer = Annotated[CustomerIdentity, Depends(get_customer_identity)]
Database = Annotated[AsyncSession, Depends(get_session)]
Reviewer = Annotated[ReviewerIdentity, Depends(get_logistics_reviewer)]
ADDRESS_PATH = "/sessions/{session_id}/orders/{order_id}/shipping-address"


@router.get(ADDRESS_PATH, response_model=AddressForm)
async def load_address_form(
    session_id: UUID, order_id: UUID, identity: Customer, db: Database, response: Response
) -> AddressForm:
    order = await address_order(ReceptionRepository(db, identity), session_id, order_id)
    response.headers["Cache-Control"] = "private, no-store"
    address = {
        field: value
        for field in ("recipient", "phone", "region", "street")
        if isinstance(value := order.shipping_address_snapshot.get(field), str)
    }
    return AddressForm(order_number=order.order_number, version=order.version, address=address)


@router.put(ADDRESS_PATH, response_model=AddressChanged)
async def save_address_form(
    session_id: UUID,
    order_id: UUID,
    data: AddressChangeRequest,
    identity: Customer,
    db: Database,
    request: Request,
) -> AddressChanged:
    async with db.begin():
        await change_address(
            ReceptionRepository(db, identity),
            session_id,
            order_id,
            data,
            UUID(request.state.request_id),
        )
    return AddressChanged()


@router.post("/sessions/{session_id}/orders/{order_id}/tickets", response_model=TicketView)
async def request_investigation(
    session_id: UUID,
    order_id: UUID,
    data: TicketRequest,
    identity: Customer,
    db: Database,
    request: Request,
    response: Response,
) -> TicketView:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        async with db.begin():
            return await submit_ticket(
                ReceptionRepository(db, identity),
                session_id,
                order_id,
                data.ticket_id,
                UUID(request.state.request_id),
            )
    except IntegrityError as error:
        raise HTTPException(409, "logistics_ticket_write_conflict") from error


@router.get("/tickets", response_model=list[StaffTicketView])
async def investigation_queue(
    reviewer: Reviewer,
    db: Database,
    response: Response,
    resolved: bool = False,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> list[StaffTicketView]:
    response.headers["Cache-Control"] = "private, no-store"
    query = select(LogisticsTicket).where(LogisticsTicket.tenant_id == reviewer.tenant_id)
    if resolved:
        query = query.where(LogisticsTicket.resolved_at.is_not(None)).order_by(
            LogisticsTicket.resolved_at.desc(),
            LogisticsTicket.id.desc(),
        )
    else:
        query = query.where(LogisticsTicket.resolved_at.is_(None)).order_by(
            LogisticsTicket.created_at,
            LogisticsTicket.id,
        )
    rows = await db.scalars(query.offset(offset).limit(20))
    return [StaffTicketView.model_validate(row) for row in rows]


@router.post("/tickets/{ticket_id}/resolution", response_model=StaffTicketView)
async def resolve_investigation(
    ticket_id: UUID,
    data: TicketResolution,
    reviewer: Reviewer,
    db: Database,
    request: Request,
    response: Response,
) -> StaffTicketView:
    response.headers["Cache-Control"] = "private, no-store"
    async with db.begin():
        return await resolve_ticket(
            db,
            reviewer,
            ticket_id,
            data.result,
            UUID(request.state.request_id),
        )
