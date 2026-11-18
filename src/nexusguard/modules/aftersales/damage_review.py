"""Versioned submissions and staff decisions. Callers own the transaction."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.reviewer import ReviewerIdentity
from nexusguard.infrastructure.models import (
    ApprovalRequest,
    AuditLog,
    DamageReview,
    DamageReviewItem,
    Order,
    OutboxEvent,
    WorkflowInstance,
)
from nexusguard.infrastructure.models.damage import DamageAttachment, DamageDraft
from nexusguard.infrastructure.models.reception import ReceptionSession
from nexusguard.modules.aftersales.damage_schemas import (
    DamageApplicationDecision,
    DamageApplicationNotice,
    DamageApplicationResult,
    DamageDraftView,
    DamageReviewDecision,
    DamageReviewItemView,
    DamageReviewQueue,
    DamageReviewSummary,
    DamageReviewView,
    StaffDamageReviewItemView,
    StaffDamageReviewView,
)
from nexusguard.modules.payments.refunds import prepare_refund, refund_status_text

APPLICATION_RESOLVED = "damage.application_resolved"
OUTCOME_LABELS = {
    "approved": "已同意",
    "needs_supplement": "需要补充材料",
    "rejected": "未同意",
}


def application_notice(review: DamageReview) -> str:
    text = (
        f"订单 {review.order_number} 的第 {review.revision} 版售后申请"
        f"已由人工处理。结果为「{OUTCOME_LABELS[review.outcome]}」。\n"
        f"原因：{review.resolution_reason}\n处理说明：{review.resolution}"
    )
    if review.refund_cents is not None:
        units, cents = divmod(review.refund_cents, 100)
        text += f"\n审核确认金额为 {units}.{cents:02} {review.refund_currency}。"
    return text


class DamageReviews:
    def __init__(self, db: AsyncSession, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    async def latest(self, draft_id: UUID) -> DamageReview | None:
        return await self.db.scalar(
            select(DamageReview)
            .where(DamageReview.tenant_id == self.tenant_id, DamageReview.draft_id == draft_id)
            .order_by(DamageReview.revision.desc())
            .limit(1)
        )

    async def ensure_editable(self, draft_id: UUID) -> None:
        review = await self.latest(draft_id)
        if review is not None and review.locked_for_processing:
            raise HTTPException(409, "damage_review_pending")
        if review is not None and review.outcome == "approved":
            raise HTTPException(409, "damage_application_already_resolved")

    async def agent_summary(self, draft_id: UUID) -> dict | None:
        review = await self.latest(draft_id)
        if review is None:
            return None
        items = await self.items(review)
        result = await self.result(review)
        return {
            "revision": review.revision,
            "evidence_status": review.status,
            "locked_for_processing": review.locked_for_processing,
            "requested_resolution": review.requested_resolution,
            "result": result.model_dump(mode="json") if result else None,
            "item_count": len(items),
            "items": [
                {"title": item.title[:200], "decision": item.decision, "reason": item.reason}
                for item in items[:12]
            ],
        }

    async def items(self, review: DamageReview) -> list[DamageReviewItem]:
        return list(
            await self.db.scalars(
                select(DamageReviewItem)
                .where(
                    DamageReviewItem.tenant_id == self.tenant_id,
                    DamageReviewItem.review_id == review.id,
                )
                .order_by(DamageReviewItem.draft_item_id)
            )
        )

    @staticmethod
    def snapshot(view: DamageDraftView) -> dict[UUID, dict]:
        return {
            p.draft_item_id: {
                "title": p.title,
                "purchased_quantity": p.purchased_quantity,
                "damaged_quantity": p.damaged_quantity,
                "description": p.description,
                "photos": [
                    {"attachment_id": str(a.attachment_id), "media_type": a.media_type}
                    for a in sorted(p.attachments, key=lambda a: a.attachment_id)
                ],
            }
            for p in view.products
            if p.included and p.draft_item_id is not None
        }

    async def result(self, review: DamageReview) -> DamageApplicationResult | None:
        if review.outcome is None:
            return None
        workflow, reason = await self.refund_progress(review)
        status_text = refund_status_text(workflow.state, reason) if workflow else None
        return DamageApplicationResult(
            outcome=review.outcome,
            resolution=review.resolution,
            reason=review.resolution_reason,
            refund_cents=review.refund_cents,
            refund_currency=review.refund_currency,
            resolved_at=review.resolved_at,
            refund_status=workflow.state.value if workflow else None,
            refund_status_text=(
                status_text or ("待退款执行。" if review.refund_cents is not None else None)
            ),
        )

    async def refund_progress(
        self, review: DamageReview
    ) -> tuple[WorkflowInstance | None, str | None]:
        if review.refund_case_id is None:
            return None, None
        row = (
            await self.db.execute(
                select(WorkflowInstance, ApprovalRequest.review_comment)
                .outerjoin(ApprovalRequest, self.approval_join())
                .where(
                    WorkflowInstance.tenant_id == self.tenant_id,
                    WorkflowInstance.case_id == review.refund_case_id,
                )
            )
        ).one_or_none()
        return (row[0], row[1]) if row else (None, None)

    @staticmethod
    def approval_join():
        return (
            (ApprovalRequest.tenant_id == WorkflowInstance.tenant_id)
            & (ApprovalRequest.workflow_id == WorkflowInstance.id)
            & (ApprovalRequest.approval_round == 1)
        )

    async def summary(self, review: DamageReview) -> DamageReviewSummary:
        return DamageReviewSummary(
            review_id=review.id,
            revision=review.revision,
            order_number=review.order_number,
            status=review.status,
            created_at=review.created_at,
            requested_resolution=review.requested_resolution,
            locked_for_processing=review.locked_for_processing,
            result=await self.result(review),
        )

    @staticmethod
    def item_data(item: DamageReviewItem) -> dict:
        return {
            "item_id": item.id,
            "title": item.title,
            "purchased_quantity": item.purchased_quantity,
            "damaged_quantity": item.damaged_quantity,
            "description": item.description,
            "photos": item.photos,
            "decision": item.decision,
            "reason": item.reason,
            "reviewed_at": item.reviewed_at,
        }

    async def fill_customer_view(self, view: DamageDraftView) -> None:
        review = await self.latest(view.draft_id)
        view.can_submit = view.status == "ready_for_review"
        if review is None:
            return
        items = await self.items(review)
        view.review = DamageReviewView(
            **(await self.summary(review)).model_dump(),
            items=[DamageReviewItemView(**self.item_data(item)) for item in items],
        )
        if review.locked_for_processing:
            view.can_submit = False
            view.prompt = (
                "申请已提交，正在逐项核验材料。需要修改时，可以先撤回。"
                if review.status == "pending"
                else "材料已逐项核验，正在等待人工给出处理结论。"
            )
        elif review.outcome == "approved":
            view.can_submit = False
            view.prompt = "这份售后申请已由人工同意。请查看处理说明。"
        elif view.can_submit and review.outcome is not None:
            before = {
                item.draft_item_id: {
                    key: getattr(item, key)
                    for key in (
                        "title",
                        "purchased_quantity",
                        "damaged_quantity",
                        "description",
                        "photos",
                    )
                }
                for item in items
            }
            view.can_submit = before != self.snapshot(view)
            if not view.can_submit:
                view.prompt = "请查看人工处理说明，补充或修改材料后可以重新提交。"

    async def submit(
        self,
        draft: DamageDraft,
        view: DamageDraftView,
        order_number: str,
        submission_id: UUID,
        requested_resolution: str,
        trace_id: UUID,
    ) -> None:
        if not view.can_submit:
            raise HTTPException(409, "damage_review_submission_unavailable")
        previous = view.review
        review = DamageReview(
            tenant_id=self.tenant_id,
            id=submission_id,
            draft_id=draft.id,
            revision=previous.revision + 1 if previous else 1,
            order_number=order_number,
            status="pending",
            requested_resolution=requested_resolution,
        )
        self.db.add(review)
        await self.db.flush()
        self.db.add_all(
            [
                DamageReviewItem(
                    tenant_id=self.tenant_id,
                    review_id=review.id,
                    draft_item_id=item_id,
                    **fields,
                )
                for item_id, fields in self.snapshot(view).items()
            ]
        )
        self.audit(review, "damage.review_submitted", "user", str(draft.user_id), trace_id)

    async def withdraw(self, draft: DamageDraft, review_id: UUID, trace_id: UUID) -> None:
        review = await self.latest(draft.id)
        if review is None or review.id != review_id:
            raise HTTPException(404, "damage_review_not_found")
        if review.status == "withdrawn":
            return
        if not review.locked_for_processing:
            raise HTTPException(409, "damage_review_already_completed")
        review.status = "withdrawn"
        self.audit(review, "damage.review_withdrawn", "user", str(draft.user_id), trace_id)

    def audit(
        self,
        review: DamageReview,
        action: str,
        actor_type: str,
        actor_id: str,
        trace_id: UUID,
        details: dict | None = None,
    ) -> None:
        self.db.add(
            AuditLog(
                tenant_id=self.tenant_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                resource_type="damage_review",
                resource_id=review.id,
                to_state=review.status,
                trace_id=trace_id,
                details={"revision": review.revision, **(details or {})},
            )
        )

    async def get(self, review_id: UUID) -> DamageReview:
        review = await self.db.get(DamageReview, (self.tenant_id, review_id))
        if review is None:
            raise HTTPException(404, "damage_review_not_found")
        return review

    async def staff_view(self, review: DamageReview) -> StaffDamageReviewView:
        order = await self.order(review)
        return StaffDamageReviewView(
            **(await self.summary(review)).model_dump(),
            resolver_id=review.resolver_id,
            order_paid_cents=order.paid_amount_cents,
            order_currency=order.currency,
            items=[
                StaffDamageReviewItemView(**self.item_data(item), reviewer_id=item.reviewer_id)
                for item in await self.items(review)
            ],
        )

    async def queue(self, status: str, offset: int, limit: int) -> DamageReviewQueue:
        filters = {
            "pending": DamageReview.status == "pending",
            "awaiting_resolution": (
                DamageReview.status.in_(("passed", "needs_supplement", "rejected"))
                & DamageReview.requested_resolution.is_not(None)
                & DamageReview.outcome.is_(None)
            ),
            "resolved": DamageReview.outcome.is_not(None),
            "withdrawn": DamageReview.status == "withdrawn",
            "legacy": (
                DamageReview.requested_resolution.is_(None)
                & DamageReview.status.in_(("passed", "needs_supplement", "rejected"))
            ),
        }
        rows = list(
            await self.db.scalars(
                select(DamageReview)
                .where(DamageReview.tenant_id == self.tenant_id, filters[status])
                .order_by(DamageReview.created_at, DamageReview.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )
        return DamageReviewQueue(
            reviews=[await self.summary(row) for row in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        )

    async def decide(
        self,
        review_id: UUID,
        item_id: UUID,
        data: DamageReviewDecision,
        reviewer: ReviewerIdentity,
        trace_id: UUID,
    ) -> StaffDamageReviewView:
        review = await self.get(review_id)
        session = await self.lock_session(review)
        items = await self.items(review)
        item = next((row for row in items if row.id == item_id), None)
        if item is None:
            raise HTTPException(404, "damage_review_item_not_found")
        if item.decision is not None:
            if (item.decision, item.reason, item.reviewer_id) == (
                data.decision,
                data.reason,
                reviewer.reviewer_id,
            ):
                return await self.staff_view(review)
            raise HTTPException(409, "damage_review_already_decided")
        if review.status != "pending":
            raise HTTPException(409, "damage_review_closed")
        item.decision = data.decision
        item.reason = data.reason
        item.reviewer_id = reviewer.reviewer_id
        item.reviewed_at = datetime.now(UTC)
        decisions = {row.decision for row in items}
        if None not in decisions:
            if "rejected" in decisions:
                review.status = "rejected"
            elif "needs_supplement" in decisions:
                review.status = "needs_supplement"
            else:
                review.status = "passed"
        session.updated_at = datetime.now(UTC)
        self.audit(
            review,
            "damage.item_reviewed",
            "reviewer",
            reviewer.reviewer_id,
            trace_id,
            {"item_id": str(item.id), "decision": data.decision},
        )
        await self.db.flush()
        return await self.staff_view(review)

    async def lock_session(self, review: DamageReview) -> ReceptionSession:
        # Customer edits, withdrawal, and staff decisions share the same parent lock.
        session = await self.db.scalar(
            select(ReceptionSession)
            .join(
                DamageDraft,
                (DamageDraft.tenant_id == ReceptionSession.tenant_id)
                & (DamageDraft.session_id == ReceptionSession.id),
            )
            .where(DamageDraft.tenant_id == self.tenant_id, DamageDraft.id == review.draft_id)
            .with_for_update(of=ReceptionSession)
        )
        if session is None:
            raise HTTPException(404, "damage_review_not_found")
        await self.db.refresh(review)
        return session

    async def order(self, review: DamageReview) -> Order:
        order = await self.db.scalar(
            select(Order)
            .join(
                DamageDraft,
                (DamageDraft.tenant_id == Order.tenant_id)
                & (DamageDraft.order_id == Order.id)
                & (DamageDraft.user_id == Order.user_id),
            )
            .where(DamageDraft.tenant_id == self.tenant_id, DamageDraft.id == review.draft_id)
        )
        if order is None:
            raise HTTPException(404, "order_not_found")
        return order

    async def resolve(
        self,
        review_id: UUID,
        data: DamageApplicationDecision,
        reviewer: ReviewerIdentity,
        trace_id: UUID,
    ) -> StaffDamageReviewView:
        review = await self.get(review_id)
        session = await self.lock_session(review)
        if review.outcome is not None:
            if (
                review.outcome,
                review.resolution,
                review.resolution_reason,
                review.refund_cents,
            ) == (
                data.outcome,
                data.resolution,
                data.reason,
                data.refund_cents,
            ) and review.resolver_id == reviewer.reviewer_id:
                return await self.staff_view(review)
            raise HTTPException(409, "damage_application_already_resolved")
        if review.requested_resolution is None or review.status in {"pending", "withdrawn"}:
            raise HTTPException(409, "damage_application_not_ready")
        order = await self.order(review)
        if data.refund_cents is not None and data.refund_cents > order.paid_amount_cents:
            raise HTTPException(422, "refund_exceeds_order_paid_amount")
        review.outcome = data.outcome
        review.resolution = data.resolution
        review.resolution_reason = data.reason
        review.refund_cents = data.refund_cents
        review.refund_currency = order.currency if data.refund_cents is not None else None
        review.resolver_id = reviewer.reviewer_id
        review.resolved_at = datetime.now(UTC)
        if data.refund_cents is not None:
            await prepare_refund(self.db, review, order, trace_id)
        self.db.add(
            OutboxEvent(
                tenant_id=self.tenant_id,
                aggregate_type="damage_review",
                aggregate_id=review.id,
                event_type=APPLICATION_RESOLVED,
                payload={},
            )
        )
        session.updated_at = datetime.now(UTC)
        self.audit(
            review,
            "damage.application_resolved",
            "reviewer",
            reviewer.reviewer_id,
            trace_id,
            {"outcome": data.outcome},
        )
        await self.db.flush()
        return await self.staff_view(review)

    async def notices(self, session_id: UUID, user_id: UUID) -> list[DamageApplicationNotice]:
        rows = await self.db.execute(
            select(DamageReview, WorkflowInstance.state, ApprovalRequest.review_comment)
            .join(
                DamageDraft,
                (DamageDraft.tenant_id == DamageReview.tenant_id)
                & (DamageDraft.id == DamageReview.draft_id),
            )
            .outerjoin(
                WorkflowInstance,
                (WorkflowInstance.tenant_id == DamageReview.tenant_id)
                & (WorkflowInstance.case_id == DamageReview.refund_case_id),
            )
            .outerjoin(ApprovalRequest, self.approval_join())
            .where(
                DamageDraft.tenant_id == self.tenant_id,
                DamageDraft.session_id == session_id,
                DamageDraft.user_id == user_id,
                DamageReview.notified_at.is_not(None),
                DamageReview.notice.is_not(None),
            )
            .order_by(DamageReview.notified_at.desc(), DamageReview.id.desc())
            .limit(50)
        )
        notices = []
        for row, state, reason in rows:
            progress = refund_status_text(state, reason)
            notices.append(
                DamageApplicationNotice(
                    review_id=row.id,
                    created_at=row.notified_at,
                    text=row.notice + ("\n" + progress if progress else ""),
                )
            )
        return notices

    async def photo(self, review_id: UUID, item_id: UUID, attachment_id: UUID) -> DamageAttachment:
        review = await self.get(review_id)
        item = await self.db.get(DamageReviewItem, (self.tenant_id, item_id))
        if (
            item is None
            or item.review_id != review.id
            or not any(p["attachment_id"] == str(attachment_id) for p in item.photos)
        ):
            raise HTTPException(404, "damage_review_photo_not_found")
        attachment = await self.db.get(DamageAttachment, (self.tenant_id, attachment_id))
        if (
            attachment is None
            or attachment.draft_id != review.draft_id
            or attachment.draft_item_id != item.draft_item_id
        ):
            raise HTTPException(404, "damage_review_photo_not_found")
        return attachment
