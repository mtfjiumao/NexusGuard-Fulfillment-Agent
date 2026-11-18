"""Deterministic multi-turn collection. Callers own transactions and the reception row lock."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update

from nexusguard.infrastructure.models import SKU, AuditLog, OrderItem
from nexusguard.infrastructure.models.damage import DamageAttachment, DamageDraft, DamageDraftItem
from nexusguard.infrastructure.models.reception import ReceptionSession
from nexusguard.modules.aftersales.damage_extraction import DamageExtraction
from nexusguard.modules.aftersales.damage_review import DamageReviews
from nexusguard.modules.aftersales.damage_schemas import (
    DamageAttachmentView,
    DamageDraftView,
    DamageProductView,
    PendingDamageClaimView,
)
from nexusguard.modules.supervisor.repository import ReceptionRepository

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DRAFT_BYTES = 20 * 1024 * 1024
MAX_RETAINED_BYTES = 100 * 1024 * 1024
MAX_ATTACHMENTS = 10
MAX_ORDER_ITEMS = 200


def normalized(value: str) -> str:
    return "".join(value.casefold().split())


class DamageCollection:
    def __init__(self, repo: ReceptionRepository):
        self.repo = repo
        self.db = repo.db
        self.tenant_id = repo.identity.tenant_id

    async def draft(self, session_id: UUID, order_id: UUID | None) -> DamageDraft | None:
        if order_id is None:
            return None
        return await self.db.scalar(
            select(DamageDraft).where(
                DamageDraft.tenant_id == self.tenant_id,
                DamageDraft.user_id == self.repo.identity.user_id,
                DamageDraft.session_id == session_id,
                DamageDraft.order_id == order_id,
            )
        )

    async def products(self, order_id: UUID) -> list[DamageProductView]:
        if await self.repo.order(order_id) is None:
            raise HTTPException(404, "order_not_found")
        rows = (
            await self.db.execute(
                select(OrderItem, SKU.title)
                .join(SKU, (SKU.tenant_id == OrderItem.tenant_id) & (SKU.id == OrderItem.sku_id))
                .where(OrderItem.tenant_id == self.tenant_id, OrderItem.order_id == order_id)
                .order_by(OrderItem.id)
                .limit(MAX_ORDER_ITEMS + 1)
            )
        ).all()
        if len(rows) > MAX_ORDER_ITEMS:
            raise HTTPException(409, "damage_order_too_large")
        result = []
        for item, title in rows:
            # Only bounded catalog display fields; never addresses or raw arbitrary snapshots.
            snapshot_title = item.sku_snapshot.get("title")
            label = (
                snapshot_title
                if isinstance(snapshot_title, str) and snapshot_title.strip()
                else title
            )
            specs = item.sku_snapshot.get("specification")
            if isinstance(specs, str) and specs.strip():
                label += " / " + specs[:150]
            result.append(
                DamageProductView(
                    order_item_id=item.id, title=label[:450], purchased_quantity=item.quantity
                )
            )
        return result

    async def claims(self, draft_id: UUID) -> list[DamageDraftItem]:
        return list(
            (
                await self.db.scalars(
                    select(DamageDraftItem)
                    .where(
                        DamageDraftItem.tenant_id == self.tenant_id,
                        DamageDraftItem.draft_id == draft_id,
                    )
                    .order_by(DamageDraftItem.id)
                )
            ).all()
        )

    async def snapshot(self, session_id: UUID, order_id: UUID) -> dict:
        draft = await self.draft(session_id, order_id)
        products = await self.products(order_id)
        claims = await self.claims(draft.id) if draft else []
        included = [claim for claim in claims if claim.included]
        # A single already-selected item supports terse replies such as “一盒，发霉了”.
        focus = draft.focused_item_id if draft else None
        if focus is None and len(included) == 1:
            focus = included[0].order_item_id
        return {
            "order_id": str(order_id),
            "focused_item_id": str(focus) if focus else None,
            "products": [
                {
                    "item_id": str(p.order_item_id),
                    "title": p.title,
                    "purchased_quantity": p.purchased_quantity,
                }
                for p in products
            ],
            "collected": [
                {
                    "item_id": str(c.order_item_id),
                    "quantity": c.quantity,
                    "description": c.description,
                }
                for c in included
            ],
        }

    async def ensure(self, session: ReceptionSession, order_id: UUID) -> DamageDraft:
        draft = await self.draft(session.id, order_id)
        if draft is None:
            draft = DamageDraft(
                tenant_id=self.tenant_id,
                id=uuid4(),
                session_id=session.id,
                user_id=self.repo.identity.user_id,
                pending_claims=[],
                order_id=order_id,
                status="collecting",
            )
            self.db.add(draft)
            await self.db.flush()
        return draft

    async def claim_item(self, draft: DamageDraft, item_id: UUID) -> DamageDraftItem:
        claim = next((c for c in await self.claims(draft.id) if c.order_item_id == item_id), None)
        if claim is None:
            claim = DamageDraftItem(
                tenant_id=self.tenant_id,
                id=uuid4(),
                draft_id=draft.id,
                order_id=draft.order_id,
                order_item_id=item_id,
                included=True,
            )
            self.db.add(claim)
            await self.db.flush()
        claim.included = True
        return claim

    def keep_pending(self, draft: DamageDraft, patch: dict, message_id: UUID) -> None:
        if len(draft.pending_claims) >= 20:
            return
        # Assignment tracking needs replacement, not an in-place JSON list mutation.
        draft.pending_claims = [
            *draft.pending_claims,
            {
                "id": str(uuid4()),
                "message_id": str(message_id),
                "patch": patch,
            },
        ]

    async def apply(
        self,
        session: ReceptionSession,
        order_id: UUID,
        message_id: UUID,
        extraction: DamageExtraction,
    ) -> tuple[str, dict]:
        draft = await self.ensure(session, order_id)
        await DamageReviews(self.db, self.tenant_id).ensure_editable(draft.id)
        context = await self.snapshot(session.id, order_id)
        products = {UUID(p["item_id"]): p for p in context["products"]}
        issues = []
        # Only backend-resolved, unambiguous product references can update a claim.
        for item in extraction.items:
            product = products.get(item.item_id)
            if product is None:
                self.keep_pending(draft, item.model_dump(mode="json"), message_id)
                issues.append("描述已暂存，请在待关联信息中选择对应商品。")
                continue
            if item.reference_quote:
                matched = [
                    UUID(p["item_id"])
                    for p in context["products"]
                    if normalized(item.reference_quote) in normalized(p["title"])
                ]
                if matched != [item.item_id]:
                    self.keep_pending(draft, item.model_dump(mode="json"), message_id)
                    issues.append("商品名称或规格不够明确，已暂存描述，请选择对应商品。")
                    continue
            elif str(item.item_id) != context["focused_item_id"]:
                self.keep_pending(draft, item.model_dump(mode="json"), message_id)
                issues.append("补充信息已暂存，请选择它对应的商品。")
                continue
            # Reject the whole patch for this item; never clamp or increment a claimed count.
            if item.quantity is not None and item.quantity > product["purchased_quantity"]:
                issues.append(f"{product['title']} 的破损数量超过购买数量，请核对购买单位和总数。")
                continue
            claim = await self.claim_item(draft, item.item_id)
            if item.quantity is not None:
                claim.quantity = item.quantity
            if item.description_quote:
                claim.description = item.description_quote
            claim.last_message_id = message_id
        if len(draft.pending_claims) >= 20:
            issues.append("待确认信息较多，请先处理面板中的待关联信息，再补充新的内容。")
        if extraction.ambiguous:
            issues.append("有些描述仍有歧义，请明确商品规格、破损总数量或具体破损情况。")
        await self.db.flush()
        included = [c for c in await self.claims(draft.id) if c.included]
        if len(included) == 1:
            draft.focused_item_id = included[0].order_item_id
        elif draft.focused_item_id not in {c.order_item_id for c in included}:
            draft.focused_item_id = None
        view = await self.refresh(draft)
        self.audit(session, "damage.claims_updated", message_id, draft)
        prefix = "\n".join(dict.fromkeys(issues))
        return (
            (prefix + "\n" if prefix else "") + view.prompt,
            {
                "tool": "collect_damage",
                "draft_id": str(draft.id),
                "status": view.status,
                "issue_count": len(issues),
            },
        )

    async def view(self, draft: DamageDraft) -> DamageDraftView:
        products = await self.products(draft.order_id)
        claims = {c.order_item_id: c for c in await self.claims(draft.id)}
        # Metadata only. Never load image bytes just to render a draft or ask a question.
        attachments = (
            await self.db.execute(
                select(
                    DamageAttachment.id,
                    DamageAttachment.draft_item_id,
                    DamageAttachment.media_type,
                )
                .where(
                    DamageAttachment.tenant_id == self.tenant_id,
                    DamageAttachment.draft_id == draft.id,
                    DamageAttachment.removed_at.is_(None),
                )
                .order_by(DamageAttachment.created_at)
            )
        ).all()
        missing = []
        selected = []
        for product in products:
            claim = claims.get(product.order_item_id)
            if claim is None:
                continue
            product.draft_item_id = claim.id
            product.included = claim.included
            product.damaged_quantity = claim.quantity
            product.description = claim.description
            product.attachments = [
                DamageAttachmentView(attachment_id=a.id, media_type=a.media_type)
                for a in attachments
                if a.draft_item_id == claim.id
            ]
            if not claim.included:
                continue
            selected.append(product)
            if claim.quantity is None or claim.quantity > product.purchased_quantity:
                missing.append(f"{product.title}：破损总数量（按订单购买单位）")
            if not claim.description:
                missing.append(f"{product.title}：具体破损描述")
            if not product.attachments:
                missing.append(f"{product.title}：破损照片")
        if draft.pending_claims:
            missing.append(f"为 {len(draft.pending_claims)} 条待确认描述选择对应商品")
        if not selected:
            missing.insert(0, "选择需要申报的破损商品")
        status = "collecting" if missing else "ready_for_review"
        if missing:
            prompt = "已保存当前信息。请补充：" + "；".join(missing[:6]) + "。"
            if len(missing) > 6:
                prompt += "其余缺项可查看下方商品清单。"
            prompt += "可分多次补充，照片请使用对应商品的上传按钮。"
        else:
            prompt = "材料已收齐。请核对商品和材料，填写售后诉求后确认提交。"
        view = DamageDraftView(
            draft_id=draft.id,
            order_id=draft.order_id,
            status=status,
            focused_item_id=draft.focused_item_id,
            products=products,
            pending_claims=[
                PendingDamageClaimView(
                    pending_claim_id=UUID(p["id"]),
                    reference=p["patch"]["reference_quote"],
                    quantity=p["patch"]["quantity"],
                    description=p["patch"]["description_quote"],
                )
                for p in draft.pending_claims
            ],
            missing=missing,
            prompt=prompt,
        )
        await DamageReviews(self.db, self.tenant_id).fill_customer_view(view)
        return view

    async def refresh(self, draft: DamageDraft) -> DamageDraftView:
        await self.db.flush()
        view = await self.view(draft)
        draft.status = view.status
        draft.updated_at = datetime.now(UTC)
        return view

    async def current_view(self, session: ReceptionSession) -> DamageDraftView | None:
        return await self.current_view_for_order(session.id, session.bound_order_id)

    async def current_view_for_order(
        self, session_id: UUID, order_id: UUID | None
    ) -> DamageDraftView | None:
        draft = await self.draft(session_id, order_id)
        return await self.view(draft) if draft else None

    def audit(
        self, session: ReceptionSession, action: str, trace_id: UUID, draft: DamageDraft
    ) -> None:
        self.db.add(
            AuditLog(
                tenant_id=self.tenant_id,
                actor_type="system",
                actor_id="damage-collection",
                action=action,
                resource_type="damage_draft",
                resource_id=draft.id,
                trace_id=trace_id,
                details={"session_id": str(session.id), "order_id": str(draft.order_id)},
            )
        )

    async def item_action(
        self,
        session: ReceptionSession,
        draft_id: UUID,
        item_id: UUID | None,
        action: str,
        trace_id: UUID,
        pending_claim_id: UUID | None = None,
    ) -> None:
        draft = await self.draft(session.id, session.bound_order_id)
        if draft is None or draft.id != draft_id:
            raise HTTPException(404, "damage_draft_not_found")
        await DamageReviews(self.db, self.tenant_id).ensure_editable(draft.id)
        if action == "discard_pending":
            remaining = [p for p in draft.pending_claims if p["id"] != str(pending_claim_id)]
            if len(remaining) == len(draft.pending_claims):
                raise HTTPException(409, "damage_pending_already_resolved")
            draft.pending_claims = remaining
            await self.refresh(draft)
            session.updated_at = datetime.now(UTC)
            self.audit(session, "damage.pending_discarded", trace_id, draft)
            return
        products = {p.order_item_id: p for p in await self.products(draft.order_id)}
        if item_id not in products:
            raise HTTPException(404, "order_item_not_found")
        pending = None
        if pending_claim_id is not None:
            if action != "select":
                raise HTTPException(422, "damage_pending_requires_selection")
            pending = next(
                (p for p in draft.pending_claims if p["id"] == str(pending_claim_id)), None
            )
            if pending is None:
                raise HTTPException(409, "damage_pending_already_resolved")
            quantity = pending["patch"]["quantity"]
            if quantity is not None and quantity > products[item_id].purchased_quantity:
                raise HTTPException(422, "damage_quantity_exceeds_purchase")
        claim = await self.claim_item(draft, item_id)
        if pending:
            if pending["patch"]["quantity"] is not None:
                claim.quantity = pending["patch"]["quantity"]
            if pending["patch"]["description_quote"]:
                claim.description = pending["patch"]["description_quote"]
            claim.last_message_id = UUID(pending["message_id"])
            draft.pending_claims = [p for p in draft.pending_claims if p["id"] != pending["id"]]
        if action == "remove":
            claim.included = False
            if draft.focused_item_id == item_id:
                draft.focused_item_id = None
        else:
            draft.focused_item_id = item_id
        await self.refresh(draft)
        session.updated_at = datetime.now(UTC)
        self.audit(session, "damage.item_" + action, trace_id, draft)

    async def upload(
        self,
        session: ReceptionSession,
        draft_id: UUID,
        claim_id: UUID,
        upload_id: UUID,
        content: bytes,
        media_type: str,
        digest: str,
        expected_version: int,
    ) -> None:
        draft = await self.draft(session.id, session.bound_order_id)
        if draft is None or draft.id != draft_id:
            raise HTTPException(404, "damage_draft_not_found")
        claim = next(
            (c for c in await self.claims(draft.id) if c.id == claim_id and c.included), None
        )
        if claim is None:
            raise HTTPException(404, "damage_item_not_found")
        existing = (
            await self.db.execute(
                select(
                    DamageAttachment.draft_id,
                    DamageAttachment.draft_item_id,
                    DamageAttachment.content_hash,
                ).where(
                    DamageAttachment.tenant_id == self.tenant_id, DamageAttachment.id == upload_id
                )
            )
        ).first()
        if existing:
            if (existing.draft_id, existing.draft_item_id, existing.content_hash) != (
                draft.id,
                claim.id,
                digest,
            ):
                raise HTTPException(409, "upload_id_conflict")
            return
        if session.version != expected_version:
            raise HTTPException(409, "reception_version_conflict")
        await DamageReviews(self.db, self.tenant_id).ensure_editable(draft.id)
        duplicate = (
            await self.db.execute(
                select(DamageAttachment.id, DamageAttachment.removed_at).where(
                    DamageAttachment.tenant_id == self.tenant_id,
                    DamageAttachment.draft_item_id == claim.id,
                    DamageAttachment.content_hash == digest,
                )
            )
        ).one_or_none()
        if duplicate is not None and duplicate.removed_at is None:
            return
        active = DamageAttachment.removed_at.is_(None)
        size = func.octet_length(DamageAttachment.content)
        count, total, retained = (
            await self.db.execute(
                select(
                    func.count().filter(active),
                    func.coalesce(func.sum(size).filter(active), 0),
                    func.coalesce(func.sum(size), 0),
                ).where(
                    DamageAttachment.tenant_id == self.tenant_id,
                    DamageAttachment.draft_id == draft.id,
                )
            )
        ).one()
        if count >= MAX_ATTACHMENTS or total + len(content) > MAX_DRAFT_BYTES:
            raise HTTPException(413, "damage_attachment_limit")
        if duplicate is None:
            if retained + len(content) > MAX_RETAINED_BYTES:
                raise HTTPException(413, "damage_attachment_retention_limit")
            self.db.add(
                DamageAttachment(
                    tenant_id=self.tenant_id,
                    id=upload_id,
                    draft_id=draft.id,
                    draft_item_id=claim.id,
                    content=content,
                    content_hash=digest,
                    media_type=media_type,
                    verification_status="pending",
                )
            )
        else:
            await self.db.execute(
                update(DamageAttachment)
                .where(
                    DamageAttachment.tenant_id == self.tenant_id,
                    DamageAttachment.id == duplicate.id,
                )
                .values(removed_at=None)
            )
        await self.refresh(draft)
        session.updated_at = datetime.now(UTC)
        self.audit(session, "damage.photo_received", upload_id, draft)

    async def remove_photo(
        self, session: ReceptionSession, draft: DamageDraft, attachment_id: UUID, trace_id: UUID
    ) -> None:
        await DamageReviews(self.db, self.tenant_id).ensure_editable(draft.id)
        removed = await self.db.scalar(
            update(DamageAttachment)
            .where(
                DamageAttachment.tenant_id == self.tenant_id,
                DamageAttachment.draft_id == draft.id,
                DamageAttachment.id == attachment_id,
                DamageAttachment.removed_at.is_(None),
            )
            .values(removed_at=datetime.now(UTC))
            .returning(DamageAttachment.id)
        )
        if removed is None:
            raise HTTPException(404, "damage_attachment_not_found")
        await self.refresh(draft)
        session.updated_at = datetime.now(UTC)
        self.audit(session, "damage.photo_removed", trace_id, draft)
