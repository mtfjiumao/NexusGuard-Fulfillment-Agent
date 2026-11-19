from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexusguard.modules.aftersales.damage_schemas import DamageApplicationNotice, DamageDraftView
from nexusguard.modules.presales.catalog import ProductCard


class ReceptionState(StrEnum):
    CONFIRMING = "confirming"
    ALTERNATIVES = "alternatives"
    SELECTING = "selecting"
    GENERAL = "general"
    BOUND = "bound"
    CLARIFYING = "clarifying"


class ReceptionAction(StrEnum):
    CONFIRM = "confirm"
    DENY = "deny"
    OTHER_ORDERS = "other_orders"
    GENERAL = "general"
    SELECT_ORDER = "select_order"


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateReception(InputModel):
    entry_point: Literal["order", "store", "other"]
    order_id: UUID | None = None
    sku_id: UUID | None = None

    @model_validator(mode="after")
    def validate_entry(self):
        if self.entry_point == "order" and self.order_id is None:
            raise ValueError("order entry requires order_id")
        if self.entry_point != "order" and self.order_id is not None:
            raise ValueError("order_id is only allowed for order entry")
        return self


class ActionRequest(InputModel):
    action: ReceptionAction
    expected_version: int = Field(ge=1)
    order_id: UUID | None = None

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.action == ReceptionAction.SELECT_ORDER) != (self.order_id is not None):
            raise ValueError("only select_order requires order_id")
        return self


class MessageRequest(InputModel):
    message_id: UUID
    expected_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4000)


class OrderCard(BaseModel):
    order_id: UUID
    order_number: str
    ordered_at: datetime
    payment_status: str
    fulfillment_status: str
    paid_amount_cents: int
    currency: str
    has_open_aftersales: bool


class Button(BaseModel):
    action: ReceptionAction
    label: str


class Suggestion(BaseModel):
    key: str
    text: str


class MessageReceipt(BaseModel):
    message_id: UUID
    status: Literal[
        "queued",
        "processing",
        "needs_order",
        "ready_for_agent",
        "completed",
        "failed",
        "superseded",
    ]
    order_id: UUID | None


class ReceptionView(BaseModel):
    session_id: UUID
    version: int
    state: ReceptionState
    prompt: str
    candidate_order: OrderCard | None
    bound_order: OrderCard | None
    buttons: list[Button]
    suggestions: list[Suggestion] = Field(min_length=2, max_length=3)
    allow_free_text: Literal[True] = True
    latest_message: MessageReceipt | None = None
    damage_draft: DamageDraftView | None = None


class OrderPage(BaseModel):
    orders: list[OrderCard]
    next_offset: int | None


class ReplyView(BaseModel):
    reply_id: UUID
    phase: Literal["clarification", "answer", "error"]
    text: str
    created_at: datetime
    product_cards: list[ProductCard] = Field(default_factory=list, max_length=3)
    address_form_order_id: UUID | None = None


class MessageView(MessageReceipt):
    text: str
    created_at: datetime
    replies: list[ReplyView]


class MessagePage(BaseModel):
    messages: list[MessageView]
    next_before: UUID | None
    application_notices: list[DamageApplicationNotice] = Field(default_factory=list)
