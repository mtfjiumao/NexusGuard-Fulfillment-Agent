from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DamageItemAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["select", "remove", "discard_pending"]
    order_item_id: UUID | None = None
    expected_version: int = Field(ge=1)
    pending_claim_id: UUID | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.action == "discard_pending":
            if self.pending_claim_id is None or self.order_item_id is not None:
                raise ValueError("discard_pending requires only pending_claim_id")
        elif self.order_item_id is None:
            raise ValueError("item action requires order_item_id")
        elif self.action == "remove" and self.pending_claim_id is not None:
            raise ValueError("remove cannot resolve pending claims")
        return self


class DamageAttachmentView(BaseModel):
    attachment_id: UUID
    media_type: str
    verification_status: Literal["pending"] = "pending"


class DamageProductView(BaseModel):
    order_item_id: UUID
    title: str
    purchased_quantity: int
    draft_item_id: UUID | None = None
    included: bool = False
    damaged_quantity: int | None = None
    description: str | None = None
    attachments: list[DamageAttachmentView] = Field(default_factory=list)


class PendingDamageClaimView(BaseModel):
    pending_claim_id: UUID
    reference: str | None
    quantity: int | None
    description: str | None


class DamageDraftView(BaseModel):
    draft_id: UUID
    order_id: UUID
    status: Literal["collecting", "ready_for_review"]
    focused_item_id: UUID | None
    products: list[DamageProductView]
    pending_claims: list[PendingDamageClaimView]
    missing: list[str]
    prompt: str
