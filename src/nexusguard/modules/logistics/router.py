"""Authenticated address form for the order bound to a reception session."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.customer import CustomerIdentity, get_customer_identity
from nexusguard.infrastructure.database import get_session
from nexusguard.modules.logistics.address import (
    AddressChangeRequest,
    AddressForm,
    address_order,
    change_address,
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
                # Default validation responses can echo full address fields.
                return JSONResponse({"detail": "invalid_address"}, status_code=422)

        return handle


class AddressChanged(BaseModel):
    status: Literal["saved"] = "saved"


router = APIRouter(route_class=AddressRoute)
Customer = Annotated[CustomerIdentity, Depends(get_customer_identity)]
Database = Annotated[AsyncSession, Depends(get_session)]
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
