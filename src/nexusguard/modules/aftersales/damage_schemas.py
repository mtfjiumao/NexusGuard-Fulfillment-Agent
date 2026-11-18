from datetime import datetime
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


class DamagePhotoView(BaseModel):
    attachment_id: UUID
    media_type: str


class DamageAttachmentView(DamagePhotoView):
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


ReviewDecision = Literal["passed", "needs_supplement", "rejected"]
ReviewStatus = Literal["pending", "needs_supplement", "rejected", "passed", "withdrawn"]
ApplicationOutcome = Literal["approved", "needs_supplement", "rejected"]
ApplicationStatus = Literal["pending", "awaiting_resolution", "resolved", "withdrawn", "legacy"]


class DamageReviewCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class DamageReviewSubmit(DamageReviewCommand):
    submission_id: UUID
    requested_resolution: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(str_strip_whitespace=True)


class DamageReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: ReviewDecision
    reason: str = Field(min_length=1, max_length=500)


class DamageApplicationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    outcome: ApplicationOutcome
    resolution: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    refund_cents: int | None = Field(default=None, strict=True, gt=0, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def validate_amount(self):
        if self.refund_cents is not None and self.outcome != "approved":
            raise ValueError("refund amount requires an approved outcome")
        return self


class DamageApplicationResult(DamageApplicationDecision):
    refund_currency: str | None
    resolved_at: datetime
    refund_status: str | None = None
    refund_status_text: str | None = None


class DamageApplicationNotice(BaseModel):
    review_id: UUID
    created_at: datetime
    text: str


class DamageReviewItemView(BaseModel):
    item_id: UUID
    title: str
    purchased_quantity: int
    damaged_quantity: int
    description: str
    photos: list[DamagePhotoView]
    decision: ReviewDecision | None
    reason: str | None
    reviewed_at: datetime | None


class DamageReviewSummary(BaseModel):
    review_id: UUID
    revision: int
    order_number: str
    status: ReviewStatus
    created_at: datetime
    requested_resolution: str | None
    locked_for_processing: bool
    result: DamageApplicationResult | None


class StaffDamageReviewItemView(DamageReviewItemView):
    reviewer_id: str | None


class DamageReviewView(DamageReviewSummary):
    items: list[DamageReviewItemView]


class StaffDamageReviewView(DamageReviewSummary):
    items: list[StaffDamageReviewItemView]
    resolver_id: str | None
    order_paid_cents: int
    order_currency: str


class DamageReviewQueue(BaseModel):
    reviews: list[DamageReviewSummary]
    next_offset: int | None


class DamageDraftView(BaseModel):
    draft_id: UUID
    order_id: UUID
    status: Literal["collecting", "ready_for_review"]
    focused_item_id: UUID | None
    products: list[DamageProductView]
    pending_claims: list[PendingDamageClaimView]
    missing: list[str]
    prompt: str
    review: DamageReviewView | None = None
    can_submit: bool = False
