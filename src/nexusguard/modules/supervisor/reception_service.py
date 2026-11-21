from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from nexusguard.api.customer import CustomerIdentity
from nexusguard.infrastructure.models import AuditLog, DamageReview, Order, OutboxEvent
from nexusguard.infrastructure.models.reception import (
    ReceptionMessage,
    ReceptionReply,
    ReceptionSession,
)
from nexusguard.modules.aftersales.damage_collection import DamageCollection
from nexusguard.modules.aftersales.damage_review import DamageReviews
from nexusguard.modules.aftersales.damage_schemas import DamageItemAction, DamageReviewSubmit
from nexusguard.modules.logistics.tickets import ticket_notices
from nexusguard.modules.supervisor.reception_rules import suggestions
from nexusguard.modules.supervisor.repository import ReceptionRepository
from nexusguard.modules.supervisor.schemas import (
    ActionRequest,
    Button,
    CreateReception,
    MessagePage,
    MessageReceipt,
    MessageRequest,
    MessageView,
    OrderCard,
    OrderPage,
    ReceptionAction,
    ReceptionState,
    ReceptionView,
    ReplyView,
)

S = ReceptionState
A = ReceptionAction
PROMPTS = {
    S.CONFIRMING: "你想咨询这笔订单吗？也可以直接输入你的问题。",
    S.ALTERNATIVES: "你想咨询其他订单，还是商品及其他问题？",
    S.SELECTING: "请选择你想咨询的订单，也可以直接输入问题。",
    S.GENERAL: "有什么可以帮你？你可以选择下面的问题，也可以直接提问。",
    S.BOUND: "已选定这笔订单，你可以选择下面的问题，也可以直接输入。",
    S.CLARIFYING: "请明确或选择你想咨询的订单，以免处理错订单。",
}
BUTTONS = {
    S.CONFIRMING: [(A.CONFIRM, "是"), (A.DENY, "不是")],
    S.ALTERNATIVES: [(A.OTHER_ORDERS, "咨询其他订单"), (A.GENERAL, "商品及其他问题")],
    S.SELECTING: [(A.GENERAL, "取消选择，咨询其他问题")],
    S.GENERAL: [(A.OTHER_ORDERS, "咨询历史订单")],
    S.BOUND: [(A.OTHER_ORDERS, "切换订单"), (A.GENERAL, "商品及其他问题")],
    S.CLARIFYING: [(A.OTHER_ORDERS, "选择订单"), (A.GENERAL, "商品及其他问题")],
}


class ReceptionService:
    def __init__(self, db: AsyncSession, identity: CustomerIdentity, *, days: int, trace_id: UUID):
        self.db = db
        self.identity = identity
        self.repo = ReceptionRepository(db, identity)
        self.days = days
        self.trace_id = trace_id

    async def authorize(self) -> None:
        if not await self.repo.active_user():
            raise HTTPException(403, "customer_unavailable")

    async def load(self, reception_id: UUID, *, lock: bool = False) -> ReceptionSession:
        reception = await self.repo.reception(reception_id, lock=lock)
        if reception is None:
            raise HTTPException(404, "reception_not_found")
        return reception

    @staticmethod
    def check_version(reception: ReceptionSession, expected: int) -> None:
        if reception.version != expected:
            raise HTTPException(409, "reception_version_conflict")

    @staticmethod
    def set_context(
        reception: ReceptionSession, state: ReceptionState, order_id: UUID | None = None
    ) -> None:
        reception.state = state.value
        reception.candidate_order_id = order_id if state == S.CONFIRMING else None
        reception.bound_order_id = order_id if state == S.BOUND else None

    def audit(self, reception: ReceptionSession, action: str, previous: str | None) -> None:
        self.db.add(
            AuditLog(
                tenant_id=self.identity.tenant_id,
                actor_type="user",
                actor_id=str(self.identity.user_id),
                action=action,
                resource_type="reception_session",
                resource_id=reception.id,
                from_state=previous,
                to_state=reception.state,
                trace_id=self.trace_id,
                details={
                    "candidate_order_id": str(reception.candidate_order_id)
                    if reception.candidate_order_id
                    else None,
                    "bound_order_id": str(reception.bound_order_id)
                    if reception.bound_order_id
                    else None,
                },
            )
        )

    def publish_message(self, message: ReceptionMessage) -> None:
        self.db.add(
            OutboxEvent(
                tenant_id=self.identity.tenant_id,
                aggregate_type="reception_session",
                aggregate_id=message.session_id,
                event_type="reception.message_ready",
                payload={"session_id": str(message.session_id), "message_id": str(message.id)},
            )
        )

    async def settle_pending(
        self, reception: ReceptionSession, *, order_id: UUID | None = None, discard: bool = False
    ) -> ReceptionMessage | None:
        pending = await self.repo.pending(reception.id)
        if pending:
            pending.order_id = order_id
            pending.status = "superseded" if discard else "ready_for_agent"
            if not discard:
                self.publish_message(pending)
        return pending

    async def create(self, data: CreateReception) -> ReceptionView:
        async with self.db.begin():
            await self.authorize()
            order = await self.repo.order(data.order_id) if data.entry_point == "order" else None
            if order is None:
                order = await self.repo.recommend(datetime.now(UTC), self.days)
            sku = await self.repo.sku(data.sku_id)
            reception = ReceptionSession(
                tenant_id=self.identity.tenant_id,
                id=uuid4(),
                user_id=self.identity.user_id,
                entry_point=data.entry_point,
                sku_id=sku.id if sku else None,
            )
            self.set_context(
                reception, S.CONFIRMING if order else S.GENERAL, order.id if order else None
            )
            self.db.add(reception)
            await self.db.flush()
            self.audit(reception, "reception.created", None)
            return await self.view(reception)

    async def get(self, reception_id: UUID) -> ReceptionView:
        async with self.db.begin():
            await self.authorize()
            return await self.view(await self.load(reception_id))

    async def ensure_idle(self, reception: ReceptionSession) -> None:
        if await self.repo.inflight(reception.id):
            raise HTTPException(409, "agent_processing")

    async def act(self, reception_id: UUID, data: ActionRequest) -> ReceptionView:
        async with self.db.begin():
            await self.authorize()
            reception = await self.load(reception_id, lock=True)
            await self.ensure_idle(reception)
            self.check_version(reception, data.expected_version)
            previous = reception.state
            latest = None
            if data.action == A.CONFIRM:
                if reception.state != S.CONFIRMING:
                    raise HTTPException(409, "confirmation_unavailable")
                order = await self.repo.order(reception.candidate_order_id)
                if order is None:
                    self.set_context(reception, S.CLARIFYING)
                else:
                    self.set_context(reception, S.BOUND, order.id)
                    latest = await self.settle_pending(reception, order_id=order.id)
            elif data.action == A.DENY:
                if reception.state != S.CONFIRMING:
                    raise HTTPException(409, "confirmation_unavailable")
                self.set_context(reception, S.ALTERNATIVES)
            elif data.action == A.OTHER_ORDERS:
                self.set_context(reception, S.SELECTING)
            elif data.action == A.GENERAL:
                self.set_context(reception, S.GENERAL)
                latest = await self.settle_pending(reception, discard=True)
            elif data.action == A.SELECT_ORDER:
                if reception.state not in {S.SELECTING, S.CLARIFYING}:
                    raise HTTPException(409, "order_selection_unavailable")
                order = await self.repo.order(data.order_id)
                if order is None:
                    raise HTTPException(404, "order_not_found")
                self.set_context(reception, S.BOUND, order.id)
                latest = await self.settle_pending(reception, order_id=order.id)
            reception.updated_at = datetime.now(UTC)
            self.audit(reception, f"reception.{data.action.value}", previous)
            await self.db.flush()
            return await self.view(reception, latest)

    async def orders(self, reception_id: UUID, limit: int, offset: int) -> OrderPage:
        async with self.db.begin():
            await self.authorize()
            reception = await self.load(reception_id)
            if reception.state not in {S.SELECTING, S.CLARIFYING}:
                raise HTTPException(409, "order_selection_unavailable")
            rows = await self.repo.order_page(limit, offset)
            return OrderPage(
                orders=[self.card(order, open_case) for order, open_case in rows[:limit]],
                next_offset=offset + limit if len(rows) > limit else None,
            )

    async def message(self, reception_id: UUID, data: MessageRequest) -> ReceptionView:
        async with self.db.begin():
            await self.authorize()
            reception = await self.load(reception_id, lock=True)
            existing = await self.repo.message(data.message_id)
            if existing:
                if existing.session_id != reception.id or existing.text != data.text:
                    raise HTTPException(409, "message_id_conflict")
                return await self.view(reception, existing)
            await self.ensure_idle(reception)
            self.check_version(reception, data.expected_version)
            previous = reception.state
            # Persist before inference. Never hold this lock across a model request.
            await self.settle_pending(reception, discard=True)
            await self.db.flush()
            message = ReceptionMessage(
                tenant_id=self.identity.tenant_id,
                id=data.message_id,
                session_id=reception.id,
                text=data.text,
                status="queued",
                order_id=None,
                context={
                    "state": reception.state,
                    "candidate_order_id": str(reception.candidate_order_id)
                    if reception.candidate_order_id
                    else None,
                    "bound_order_id": str(reception.bound_order_id)
                    if reception.bound_order_id
                    else None,
                    "sku_id": str(reception.sku_id) if reception.sku_id else None,
                },
            )
            self.db.add(message)
            self.publish_message(message)
            reception.updated_at = datetime.now(UTC)
            self.audit(reception, "reception.message_received", previous)
            await self.db.flush()
            return await self.view(reception, message)

    async def damage_item(
        self, reception_id: UUID, draft_id: UUID, data: DamageItemAction
    ) -> ReceptionView:
        async with self.db.begin():
            await self.authorize()
            reception = await self.load(reception_id, lock=True)
            await self.ensure_idle(reception)
            self.check_version(reception, data.expected_version)
            await DamageCollection(self.repo).item_action(
                reception,
                draft_id,
                data.order_item_id,
                data.action,
                self.trace_id,
                data.pending_claim_id,
            )
            await self.db.flush()
            return await self.view(reception)

    async def damage_photo(
        self,
        reception_id: UUID,
        draft_id: UUID,
        claim_id: UUID,
        upload_id: UUID,
        content: bytes,
        media_type: str,
        digest: str,
        expected_version: int,
    ) -> ReceptionView:
        async with self.db.begin():
            await self.authorize()
            reception = await self.load(reception_id, lock=True)
            await self.ensure_idle(reception)
            await DamageCollection(self.repo).upload(
                reception,
                draft_id,
                claim_id,
                upload_id,
                content,
                media_type,
                digest,
                expected_version,
            )
            await self.db.flush()
            return await self.view(reception)

    async def damage_review(
        self,
        reception_id: UUID,
        draft_id: UUID,
        expected_version: int,
        *,
        submission: DamageReviewSubmit | None = None,
        withdraw_id: UUID | None = None,
        remove_photo_id: UUID | None = None,
    ) -> ReceptionView:
        async with self.db.begin():
            await self.authorize()
            reception = await self.load(reception_id, lock=True)
            collection = DamageCollection(self.repo)
            draft = await collection.draft(reception.id, reception.bound_order_id)
            if draft is None or draft.id != draft_id:
                raise HTTPException(404, "damage_draft_not_found")
            reviews = DamageReviews(self.db, self.identity.tenant_id)
            if submission is not None:
                existing = await self.db.get(
                    DamageReview, (self.identity.tenant_id, submission.submission_id)
                )
                if existing is not None:
                    if (
                        existing.draft_id != draft.id
                        or existing.requested_resolution != submission.requested_resolution
                    ):
                        raise HTTPException(409, "damage_review_submission_id_conflict")
                    return await self.view(reception)
            await self.ensure_idle(reception)
            self.check_version(reception, expected_version)
            if submission is not None:
                order = await self.repo.order(draft.order_id)
                if order is None:
                    raise HTTPException(404, "order_not_found")
                await reviews.submit(
                    draft,
                    await collection.view(draft),
                    order.order_number,
                    submission.submission_id,
                    submission.requested_resolution,
                    self.trace_id,
                )
            elif withdraw_id is not None:
                await reviews.withdraw(draft, withdraw_id, self.trace_id)
            elif remove_photo_id is not None:
                await collection.remove_photo(reception, draft, remove_photo_id, self.trace_id)
            reception.updated_at = datetime.now(UTC)
            await self.db.flush()
            return await self.view(reception)

    async def history(self, reception_id: UUID, limit: int, before: UUID | None) -> MessagePage:
        async with self.db.begin():
            await self.authorize()
            await self.load(reception_id)
            query = select(ReceptionMessage).where(
                ReceptionMessage.tenant_id == self.identity.tenant_id,
                ReceptionMessage.session_id == reception_id,
            )
            if before:
                cursor = await self.repo.message(before)
                if cursor is None or cursor.session_id != reception_id:
                    raise HTTPException(404, "message_not_found")
                query = query.where(
                    tuple_(ReceptionMessage.created_at, ReceptionMessage.id)
                    < tuple_(cursor.created_at, cursor.id)
                )
            rows = list(
                (
                    await self.db.scalars(
                        query.order_by(
                            ReceptionMessage.created_at.desc(), ReceptionMessage.id.desc()
                        ).limit(limit + 1)
                    )
                ).all()
            )
            page = list(reversed(rows[:limit]))
            replies = (
                list(
                    (
                        await self.db.scalars(
                            select(ReceptionReply)
                            .where(
                                ReceptionReply.tenant_id == self.identity.tenant_id,
                                ReceptionReply.message_id.in_([row.id for row in page]),
                            )
                            .order_by(ReceptionReply.created_at, ReceptionReply.id)
                        )
                    ).all()
                )
                if page
                else []
            )
            return MessagePage(
                messages=[
                    MessageView(
                        message_id=row.id,
                        status=row.status,
                        order_id=row.order_id,
                        text=row.text,
                        created_at=row.created_at,
                        replies=[
                            ReplyView(
                                reply_id=reply.id,
                                phase=reply.phase,
                                text=reply.text,
                                created_at=reply.created_at,
                                product_cards=reply.facts.get("product_cards", []),
                                address_form_order_id=reply.facts.get("address_form_order_id"),
                                logistics_ticket_order_id=reply.facts.get(
                                    "logistics_ticket_order_id"
                                ),
                                delay_application_order_id=reply.facts.get(
                                    "delay_application_order_id"
                                ),
                            )
                            for reply in replies
                            if reply.message_id == row.id
                        ],
                    )
                    for row in page
                ],
                next_before=page[0].id if len(rows) > limit else None,
                application_notices=(
                    await DamageReviews(self.db, self.identity.tenant_id).notices(
                        reception_id, self.identity.user_id
                    )
                    if before is None
                    else []
                ),
                logistics_notices=await ticket_notices(self.repo, reception_id)
                if before is None
                else [],
            )

    @staticmethod
    def card(order: Order, open_case: bool) -> OrderCard:
        return OrderCard(
            order_id=order.id,
            order_number=order.order_number,
            ordered_at=order.ordered_at,
            payment_status=order.payment_status,
            fulfillment_status=order.fulfillment_status,
            paid_amount_cents=order.paid_amount_cents,
            currency=order.currency,
            has_open_aftersales=open_case,
        )

    async def view(
        self, reception: ReceptionSession, latest: ReceptionMessage | None = None
    ) -> ReceptionView:
        candidate = await self.repo.order(reception.candidate_order_id)
        bound = await self.repo.order(reception.bound_order_id)
        active = bound or candidate
        open_case = await self.repo.has_open_case(active.id) if active else False
        sku = await self.repo.sku(reception.sku_id)
        state = S(reception.state)
        if latest is None:
            latest = await self.repo.latest(reception.id)
        return ReceptionView(
            session_id=reception.id,
            damage_draft=await DamageCollection(self.repo).current_view(reception),
            version=reception.version,
            state=state,
            prompt=PROMPTS[state],
            candidate_order=self.card(candidate, open_case) if candidate else None,
            bound_order=self.card(bound, open_case) if bound else None,
            buttons=[Button(action=action, label=label) for action, label in BUTTONS[state]],
            suggestions=suggestions(active, open_case=open_case, sku=sku),
            latest_message=MessageReceipt(
                message_id=latest.id, status=latest.status, order_id=latest.order_id
            )
            if latest
            else None,
        )
