"""Durable Outbox consumer. Run: python -m nexusguard.modules.supervisor.agent_worker."""

import asyncio
import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexusguard.api.customer import CustomerIdentity
from nexusguard.core.config import Settings, get_settings
from nexusguard.core.logging import configure_logging
from nexusguard.infrastructure.database import build_engine
from nexusguard.infrastructure.llm import LLMError, ModelGateway
from nexusguard.infrastructure.models import (
    AuditLog,
    DamageReview,
    InboxEvent,
    LogisticsTicket,
    Order,
    OutboxEvent,
)
from nexusguard.infrastructure.models.reception import (
    ReceptionMessage,
    ReceptionReply,
    ReceptionSession,
)
from nexusguard.modules.aftersales.damage_collection import DamageCollection
from nexusguard.modules.aftersales.damage_extraction import DamageExtraction, validate_damage
from nexusguard.modules.aftersales.damage_review import (
    APPLICATION_RESOLVED,
    DamageReviews,
    application_notice,
)
from nexusguard.modules.logistics.address import ADDRESS_BLOCKED_TEXT, address_change_blocker
from nexusguard.modules.logistics.tickets import (
    TICKET_RESOLVED,
    delayed_shipments,
    exception_shipments,
    latest_ticket,
)
from nexusguard.modules.payments.refunds import RefundWorker, SimulationOutcome
from nexusguard.modules.presales.catalog import search_products
from nexusguard.modules.supervisor.conversation_agent import (
    AgentAnswer,
    CaseAction,
    CatalogAction,
    CollectAction,
    SKUAction,
    ToolAction,
    converse,
)
from nexusguard.modules.supervisor.intent import ORDER_INTENTS, IntentResult, recognize_intent
from nexusguard.modules.supervisor.read_tools import ReadOnlyTools, order_facts
from nexusguard.modules.supervisor.reception_service import ReceptionService
from nexusguard.modules.supervisor.repository import ReceptionRepository
from nexusguard.modules.supervisor.schemas import ReceptionState as S

logger = logging.getLogger(__name__)
CONSUMER = "reception-agent-v1"
ACTIVE = {"queued", "ready_for_agent", "processing"}


@dataclass(frozen=True)
class Job:
    tenant_id: UUID
    event_id: UUID
    message_id: UUID
    session_id: UUID
    user_id: UUID
    token: UUID
    text: str
    context: dict
    intent: dict | None
    order_id: UUID | None


@dataclass(frozen=True)
class PreparedTurn:
    answer: AgentAnswer
    session_version: int
    order_id: UUID | None
    order_version: int | None
    damage: DamageExtraction | None


class AgentWorker:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        gateway: ModelGateway | None,
        settings: Settings,
        *,
        refund_simulation: SimulationOutcome | None = None,
    ):
        self.factory = factory
        self.gateway = gateway
        self.settings = settings
        self.refund_simulation = refund_simulation

    @staticmethod
    def acknowledge(db: AsyncSession, event: OutboxEvent) -> None:
        event.published_at = datetime.now(UTC)
        db.add(InboxEvent(tenant_id=event.tenant_id, consumer=CONSUMER, event_id=event.id))

    async def claim(self) -> Job | None:
        async with self.factory() as db, db.begin():
            event = await db.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "reception.message_ready",
                    OutboxEvent.aggregate_type == "reception_session",
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.available_at <= datetime.now(UTC),
                )
                .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if event is None:
                return None
            try:
                message_id = UUID(event.payload["message_id"])
                session_id = UUID(event.payload["session_id"])
            except (KeyError, ValueError, TypeError):
                # Never log arbitrary payload or customer text.
                logger.error("invalid_reception_event event_id=%s", event.id)
                self.acknowledge(db, event)
                return None
            message = await db.get(
                ReceptionMessage, (event.tenant_id, message_id), with_for_update=True
            )
            if (
                message is None
                or message.session_id != session_id
                or event.aggregate_id != session_id
                or message.status not in ACTIVE
            ):
                self.acknowledge(db, event)
                return None
            # API mutations serialize on this row and refuse while a turn is in flight.
            session = await db.get(ReceptionSession, (event.tenant_id, session_id))
            if session is None:
                self.acknowledge(db, event)
                return None
            if event.retry_count >= 3:
                message.status = "failed"
                message.worker_token = None
                db.add(
                    ReceptionReply(
                        tenant_id=event.tenant_id,
                        message_id=message.id,
                        phase="error",
                        text="这条问题多次处理中断，请重新发送问题；没有执行业务变更。",
                    )
                )
                self.acknowledge(db, event)
                return None
            token = uuid4()
            message.status = "processing"
            message.worker_token = token
            event.retry_count += 1
            # Entire model deadline includes queueing; reserve additional DB commit time.
            event.available_at = datetime.now(UTC) + timedelta(
                seconds=(1 + self.settings.agent_max_steps) * self.settings.llm_timeout_seconds + 60
            )
            return Job(
                event.tenant_id,
                event.id,
                message.id,
                session_id,
                session.user_id,
                token,
                message.text,
                dict(message.context),
                message.intent,
                message.order_id,
            )

    async def understand(self, job: Job) -> tuple[IntentResult, str]:
        if job.intent and job.order_id:
            return IntentResult.model_validate(job.intent["result"]), job.intent["model"]
        context = {
            "state": job.context.get("state"),
            "has_candidate": bool(job.context.get("candidate_order_id")),
            "has_bound": bool(job.context.get("bound_order_id")),
        }
        # A small bounded history supports “是的/不是，我想退款” without replaying an entire chat.
        async with self.factory() as db, db.begin():
            repo = ReceptionRepository(db, CustomerIdentity(job.tenant_id, job.user_id))
            if not await repo.active_user():
                raise LLMError("customer_unavailable")
            current = await db.get(ReceptionMessage, (job.tenant_id, job.message_id))
            session = await repo.reception(job.session_id)
            active_draft = await DamageCollection(repo).draft(session.id, session.bound_order_id)
            context["active_damage_collection"] = active_draft is not None
            previous = list(
                (
                    await db.scalars(
                        select(ReceptionMessage)
                        .where(
                            ReceptionMessage.tenant_id == job.tenant_id,
                            ReceptionMessage.session_id == job.session_id,
                            ReceptionMessage.created_at < current.created_at,
                            ReceptionMessage.status != "superseded",
                        )
                        .order_by(ReceptionMessage.created_at.desc())
                        .limit(4)
                    )
                ).all()
            )
            context["untrusted_history"] = [row.text[:600] for row in reversed(previous)]
        return await recognize_intent(self.gateway, text=job.text, context=context)

    async def load_turn(
        self, db: AsyncSession, job: Job
    ) -> tuple[ReceptionRepository, ReceptionSession, ReceptionMessage]:
        repo = ReceptionRepository(db, CustomerIdentity(job.tenant_id, job.user_id))
        session = await repo.reception(job.session_id)
        message = await repo.message(job.message_id)
        if (
            session is None
            or message is None
            or message.session_id != session.id
            or message.worker_token != job.token
            or message.status != "processing"
            or not await repo.active_user()
        ):
            raise LLMError("agent_turn_unavailable")
        return repo, session, message

    async def prepare_agent(self, job: Job, intent: IntentResult) -> PreparedTurn | None:
        # Resolve without mutating. Each model call runs after the short DB scope closes.
        async with self.factory() as db:
            repo, session, message = await self.load_turn(db, job)
            order, gate = await self.resolve(repo, session, message, intent, mutate=False)
            if gate:
                return None
            session_version = session.version
            order_id = order.id if order else None
            order_version = order.version if order else None
            damage_review = None
            review_topics = {
                "damage",
                "case_progress",
                "case_materials",
                "refund",
                "return",
                "exchange",
            }
            if order_id and review_topics.intersection(intent.intents):
                draft = await DamageCollection(repo).draft(session.id, order_id)
                if draft:
                    damage_review = await DamageReviews(db, job.tenant_id).agent_summary(draft.id)
            damage_locked = bool(damage_review and damage_review["locked_for_processing"])
            approved = bool(
                damage_review and (damage_review["result"] or {}).get("outcome") == "approved"
            )
            history_rows = list(
                (
                    await db.scalars(
                        select(ReceptionMessage)
                        .where(
                            ReceptionMessage.tenant_id == job.tenant_id,
                            ReceptionMessage.session_id == job.session_id,
                            ReceptionMessage.id != job.message_id,
                            ReceptionMessage.created_at <= message.created_at,
                            ReceptionMessage.status == "completed",
                            or_(
                                ReceptionMessage.order_id == order_id,
                                ReceptionMessage.order_id.is_(None),
                            ),
                        )
                        .order_by(ReceptionMessage.created_at.desc(), ReceptionMessage.id.desc())
                        .limit(4)
                    )
                ).all()
            )
            replies = (
                list(
                    (
                        await db.scalars(
                            select(ReceptionReply).where(
                                ReceptionReply.tenant_id == job.tenant_id,
                                ReceptionReply.message_id.in_([row.id for row in history_rows]),
                                ReceptionReply.phase == "answer",
                            )
                        )
                    ).all()
                )
                if history_rows
                else []
            )
            context = {
                "untrusted_customer_text": job.text,
                "intents": intent.intents,
                "order": order_facts(order) if order else None,
                "address_change_blocker": (
                    address_change_blocker(order)
                    if order is not None and "address_change" in intent.intents
                    else None
                ),
                "sku_available": session.sku_id is not None,
                "damage_review": damage_review,
                "untrusted_history": [
                    {
                        "user": row.text[:1200],
                        "assistant": next(
                            (reply.text[:1600] for reply in replies if reply.message_id == row.id),
                            "",
                        ),
                    }
                    for row in reversed(history_rows)
                ],
                "capabilities": {
                    "financial_actions": False,
                    "address_mutation": False,
                    "policy_decision": False,
                    "evidence_review": False,
                },
            }
        proposed_damage = None

        async def execute(command: ToolAction) -> dict:
            nonlocal proposed_damage
            # No commit in this scope: even collection is a preview, rolled back on close.
            # Final state, reply, audit and acknowledgment are committed together in finish().
            async with self.factory() as db:
                repo, session, message = await self.load_turn(db, job)
                current_order, gate = await self.resolve(
                    repo, session, message, intent, mutate=False
                )
                if (
                    gate
                    or session.version != session_version
                    or (current_order.id if current_order else None) != order_id
                    or (current_order.version if current_order else None) != order_version
                ):
                    raise LLMError("agent_context_changed")
                tools = ReadOnlyTools(repo)
                if isinstance(command, CatalogAction):
                    keyword = (command.keyword or "").strip()
                    if not keyword or keyword.casefold() not in job.text.casefold():
                        return {"guidance": "你想看看哪种商品？", "products": []}
                    products = await search_products(db, job.tenant_id, keyword)
                    if products:
                        guidance = "店里有匹配的商品，点开可以看详情。"
                    else:
                        guidance = f"暂时没找到与「{keyword}」匹配的在售商品。要看看其他商品吗？"
                    return {
                        "guidance": guidance,
                        "products": [product.model_dump(mode="json") for product in products],
                    }
                if isinstance(command, SKUAction):
                    guidance, facts = await tools.presales(command.topic, session.sku_id)
                    return {"guidance": guidance, "facts": facts}
                if current_order is None:
                    return {"error": "order_required", "guidance": "请先选择并确认咨询订单。"}
                if command.action == "query_order":
                    return {
                        "facts": order_facts(current_order),
                        "guidance": "只能查询；没有预计发货数据，也未修改收货地址。",
                    }
                if command.action == "query_logistics":
                    guidance, facts = await tools.logistics(current_order)
                    ticket = await latest_ticket(repo, current_order)
                    if ticket is not None:
                        facts["investigation"] = ticket.model_dump(mode="json", exclude={"id"})
                    facts["can_request_investigation"] = bool(
                        exception_shipments(facts) and (ticket is None or ticket.resolved_at)
                    )
                    return {"guidance": guidance, "facts": facts}
                if isinstance(command, CaseAction):
                    if command.topic == "delay_compensation":
                        _, facts = await tools.logistics(current_order)
                        ticket = await latest_ticket(repo, current_order, "delay_compensation")
                        if ticket is not None:
                            facts["delay_application"] = ticket.model_dump(
                                mode="json", exclude={"id"}
                            )
                        facts["can_request_delay_compensation"] = bool(
                            delayed_shipments(facts)
                            and (
                                ticket is None
                                or (ticket.resolved_at and not ticket.compensation_approved)
                            )
                        )
                        return {
                            "guidance": "说明承诺送达时间和签收状态。赔付由人工决定。",
                            "facts": facts,
                        }
                    guidance, facts = await tools.aftersales(command.topic, current_order)
                    if damage_review and command.topic in review_topics:
                        facts["damage_review"] = damage_review
                        guidance = "根据破损售后申请记录说明进度或转述已保存的人工结论。"
                    elif command.topic in {"refund", "return", "exchange"}:
                        guidance += "如果商品破损，请引导顾客说明破损并从面板提交售后申请。"
                    return {"guidance": guidance, "facts": facts}
                collection = DamageCollection(repo)
                if isinstance(command, CollectAction):
                    if damage_locked or approved:
                        return {
                            "error": "damage_review_pending",
                            "guidance": (
                                "该申请已由人工同意，请查看处理说明。"
                                if approved
                                else "申请正在人工处理，需要修改时请先在面板撤回。"
                            ),
                        }
                    patch = validate_damage(command.extraction, job.text)
                    guidance, _ = await collection.apply(
                        session, current_order.id, message.id, patch
                    )
                    proposed_damage = patch
                    view = await collection.current_view_for_order(session.id, current_order.id)
                    return {
                        "guidance": guidance,
                        "status": view.status,
                        "missing": view.missing[:12],
                        "preview_only": True,
                    }
                snapshot = await collection.snapshot(session.id, current_order.id)
                for product in snapshot["products"]:
                    product["title"] = product["title"][:200]
                snapshot["collected_count"] = len(snapshot["collected"])
                snapshot["collected"] = snapshot["collected"][:20]
                view = await collection.current_view_for_order(session.id, current_order.id)
                snapshot["draft_status"] = view.status if view else None
                snapshot["damage_review"] = damage_review
                snapshot["missing"] = view.missing[:12] if view else ["选择破损商品并补充信息"]
                snapshot["photo_counts"] = (
                    [
                        {"item_id": str(p.order_item_id), "count": len(p.attachments)}
                        for p in view.products
                        if p.included
                    ]
                    if view
                    else []
                )
                snapshot["pending_claims"] = (
                    [
                        {
                            "reference": p.reference,
                            "quantity": p.quantity,
                            "description": p.description,
                        }
                        for p in view.pending_claims
                    ]
                    if view
                    else []
                )
                if approved:
                    snapshot["guidance"] = "该申请已由人工同意，请查看处理说明。"
                elif damage_locked:
                    snapshot["guidance"] = "申请正在人工处理，需要修改时请先在面板撤回。"
                else:
                    snapshot["guidance"] = "请核对材料，齐全后由顾客填写诉求并确认提交。"
                return snapshot

        try:
            answer = await converse(
                self.gateway,
                context,
                execute,
                max_steps=self.settings.agent_max_steps,
                require_damage="damage" in intent.intents and not (damage_locked or approved),
            )
        except HTTPException:
            raise LLMError("agent_tool_unavailable") from None
        return PreparedTurn(answer, session_version, order_id, order_version, proposed_damage)

    async def finish(
        self,
        job: Job,
        intent: IntentResult | None,
        model: str | None,
        error: str | None = None,
        turn: PreparedTurn | None = None,
    ) -> None:
        async with self.factory() as db, db.begin():
            # Fencing: an expired worker cannot commit after another worker reclaimed its turn.
            event = await db.get(OutboxEvent, (job.tenant_id, job.event_id), with_for_update=True)
            if event is None or event.published_at is not None:
                return
            identity = CustomerIdentity(tenant_id=job.tenant_id, user_id=job.user_id)
            repo = ReceptionRepository(db, identity)
            session = await repo.reception(job.session_id, lock=True)
            message = await db.get(
                ReceptionMessage, (job.tenant_id, job.message_id), with_for_update=True
            )
            if (
                message is None
                or message.worker_token != job.token
                or message.status != "processing"
            ):
                return
            if session is None or not await repo.active_user():
                error = "customer_unavailable"
            if error:
                text = "暂时没能准确理解或处理这条问题，请稍后重新发送；没有执行业务变更。"
                phase, status, facts = "error", "failed", {"error_code": error}
            else:
                if intent is None:
                    raise ValueError("missing_validated_intent")
                previous_state = session.state
                message.intent = {"model": model, "result": intent.model_dump()}
                if turn is not None:
                    message.intent = {
                        **message.intent,
                        "agent_model": turn.answer.model,
                        "agent_steps": turn.answer.steps,
                        "damage": turn.damage.model_dump(mode="json") if turn.damage else None,
                    }
                try:
                    text, phase, status, facts = await self.route(
                        repo, session, message, intent, turn
                    )
                except LLMError as failure:
                    # Context validation precedes business writes.
                    text = "订单或会话信息发生变化，请重新发送问题；本轮未保存业务变更。"
                    phase, status, facts = "error", "failed", {"error_code": failure.code}
                session.updated_at = datetime.now(UTC)
                db.add(
                    AuditLog(
                        tenant_id=job.tenant_id,
                        actor_type="agent",
                        actor_id=CONSUMER,
                        action="reception.agent_processed",
                        resource_type="reception_session",
                        resource_id=session.id,
                        from_state=previous_state,
                        to_state=session.state,
                        trace_id=job.token,
                        details={
                            "message_id": str(message.id),
                            "model": model,
                            "agent_model": turn.answer.model if turn else None,
                            "agent_steps": turn.answer.steps if turn else 0,
                            "intents": intent.intents,
                            "action": intent.action,
                            "injection_suspected": intent.injection_suspected,
                            "tools": [call["tool"] for call in facts.get("calls", [])],
                        },
                    )
                )
            message.status = status
            message.worker_token = None
            db.add(
                ReceptionReply(
                    tenant_id=job.tenant_id,
                    message_id=message.id,
                    phase=phase,
                    text=text,
                    facts=facts,
                )
            )
            self.acknowledge(db, event)

    async def resolve(
        self,
        repo: ReceptionRepository,
        session: ReceptionSession,
        message: ReceptionMessage,
        intent: IntentResult,
        *,
        mutate: bool,
    ) -> tuple[Order | None, tuple[str, str, str, dict] | None]:
        if intent.injection_suspected:
            return None, (
                "我可以帮你查询订单或了解售后需求。退款、改址等操作需按业务规则核验，"
                "请直接说明实际遇到的问题。",
                "answer",
                "completed",
                {},
            )
        resumed = message.order_id is not None
        order = await repo.order(message.order_id) if resumed else None
        order_needed = bool(ORDER_INTENTS.intersection(intent.intents))
        if not resumed:
            # Explicit references are additionally grounded in customer text by the classifier.
            if intent.order_reference == "explicit":
                order = await repo.order_number(intent.order_number)
            elif intent.order_reference == "current" and intent.action != "switch_order":
                order = await repo.order(session.bound_order_id)
            elif intent.order_reference == "candidate" and intent.action != "switch_order":
                order = await repo.order(session.candidate_order_id)
            if intent.action == "general" and not order_needed:
                if mutate:
                    ReceptionService.set_context(session, S.GENERAL)
                order = None
            needs_order = (
                (order_needed and order is None)
                or (intent.order_reference in {"explicit", "unclear"} and order is None)
                or (intent.action in {"confirm_order", "switch_order"} and order is None)
            )
            if needs_order:
                # Do not leak whether an explicit order exists for another customer.
                if mutate:
                    ReceptionService.set_context(session, S.CLARIFYING)
                    message.order_id = None
                return None, (
                    "请在订单列表中选择你要咨询的订单。选定后，我会接着处理刚才的问题。",
                    "clarification",
                    "needs_order",
                    {},
                )
            if intent.needs_clarification and order_needed and "damage" not in intent.intents:
                return None, (
                    "请再说明具体问题及对应订单；这条问题尚未进入业务处理。",
                    "answer",
                    "completed",
                    {},
                )
            if order and mutate:
                ReceptionService.set_context(session, S.BOUND, order.id)
                message.order_id = order.id
        elif order is None:
            return None, ("当前无法核实订单，请重新选择后提问。", "error", "failed", {})
        return order, None

    async def route(
        self,
        repo: ReceptionRepository,
        session: ReceptionSession,
        message: ReceptionMessage,
        intent: IntentResult,
        turn: PreparedTurn | None,
    ) -> tuple[str, str, str, dict]:
        order, gate = await self.resolve(repo, session, message, intent, mutate=False)
        if gate:
            await self.resolve(repo, session, message, intent, mutate=True)
            return gate
        if order is not None:
            # Serialize final fact-version validation against concurrent order updates.
            await repo.db.refresh(order, with_for_update=True)
        address_text = ""
        address_form_order_id = None
        if order is not None and "address_change" in intent.intents:
            blocker = address_change_blocker(order)
            if blocker is not None:
                address_text = ADDRESS_BLOCKED_TEXT[blocker]
            else:
                address_form_order_id = str(order.id)
                address_text = "请在表单里填写并确认新的收货地址。"
            if intent.intents == ["address_change"]:
                await self.resolve(repo, session, message, intent, mutate=True)
                return (
                    address_text,
                    "answer",
                    "completed",
                    {
                        "address_form_order_id": address_form_order_id,
                    },
                )
        if (
            turn is None
            or session.version != turn.session_version
            or (order.id if order else None) != turn.order_id
            or (order.version if order else None) != turn.order_version
        ):
            raise LLMError("agent_context_changed")
        await self.resolve(repo, session, message, intent, mutate=True)
        calls = list(turn.answer.calls)
        products = {}
        ticket_order_id = None
        delay_order_id = None
        for call in calls:
            if call["tool"] == "search_products":
                for product in call["result"]["products"]:
                    products.setdefault(product["sku_id"], product)
            if (
                order is not None
                and call["tool"] == "query_logistics"
                and call["result"]["facts"].get("can_request_investigation")
            ):
                ticket_order_id = str(order.id)
            if (
                order is not None
                and call["tool"] == "query_aftersales"
                and call["result"]["facts"].get("can_request_delay_compensation")
            ):
                delay_order_id = str(order.id)
        if turn.damage is not None:
            _, fact = await DamageCollection(repo).apply(session, order.id, message.id, turn.damage)
            calls.append(fact)
        return (
            "\n\n".join(filter(None, (turn.answer.text, address_text))),
            "answer",
            "completed",
            {
                "calls": calls,
                "product_cards": list(products.values())[:3],
                "address_form_order_id": address_form_order_id,
                "logistics_ticket_order_id": ticket_order_id,
                "delay_application_order_id": delay_order_id,
                "agent_model": turn.answer.model,
                "agent_steps": turn.answer.steps,
            },
        )

    async def notify_customer_result(self) -> bool:
        async with self.factory() as db, db.begin():
            event = await db.scalar(
                select(OutboxEvent)
                .where(
                    or_(
                        (
                            OutboxEvent.event_type.in_(
                                (APPLICATION_RESOLVED, "damage.review_completed")
                            )
                            & (OutboxEvent.aggregate_type == "damage_review")
                        ),
                        (OutboxEvent.event_type == TICKET_RESOLVED)
                        & (OutboxEvent.aggregate_type == "logistics_ticket"),
                    ),
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.available_at <= datetime.now(UTC),
                )
                .order_by(OutboxEvent.available_at, OutboxEvent.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if event is None:
                return False
            key = (event.tenant_id, event.aggregate_id)
            if event.event_type == TICKET_RESOLVED:
                ticket = await db.get(LogisticsTicket, key)
                if ticket and ticket.resolved_at is not None and ticket.notified_at is None:
                    ticket.notified_at = datetime.now(UTC)
            elif event.event_type == APPLICATION_RESOLVED:
                review = await db.get(DamageReview, key)
                if review and review.outcome is not None and review.notified_at is None:
                    review.notice = application_notice(review)
                    review.notified_at = datetime.now(UTC)
            self.acknowledge(db, event)
            return True

    async def run_once(self) -> bool:
        notified = await self.notify_customer_result()
        refund_advanced = False
        if self.refund_simulation is not None:
            async with self.factory() as db, db.begin():
                refund_advanced = await RefundWorker(db, self.refund_simulation).run_once()
        job = await self.claim()
        if job is None:
            return notified or refund_advanced
        try:
            intent, model = await self.understand(job)
            turn = (
                None
                if intent.intents == ["address_change"]
                else await self.prepare_agent(job, intent)
            )
        except LLMError as error:
            await self.finish(job, None, None, error.code)
        else:
            await self.finish(job, intent, model, turn=turn)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception as error:
                # Exceptions may contain SQL parameters or prompts; log type only.
                logger.error("agent_iteration_failed error_type=%s", type(error).__name__)
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings)
    gateway = None
    try:
        if settings.llm_api_key and settings.llm_api_key.get_secret_value().strip():
            gateway = ModelGateway(settings)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, stop.set)
        worker = AgentWorker(async_sessionmaker(engine, expire_on_commit=False), gateway, settings)
        # One shared bounded gateway; independent processes can claim with SKIP LOCKED.
        await asyncio.gather(*(worker.run(stop) for _ in range(settings.llm_max_concurrency)))
    finally:
        if gateway:
            await gateway.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
