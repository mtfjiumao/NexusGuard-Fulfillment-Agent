"""Trust boundary for a host application's verified staff login."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class ReviewerIdentity:
    tenant_id: UUID
    reviewer_id: str
    permissions: frozenset[str]


def get_damage_reviewer(request: Request) -> ReviewerIdentity:
    return require_reviewer_permission(request, "damage_review")


def get_refund_supervisor(request: Request) -> ReviewerIdentity:
    return require_reviewer_permission(request, "refund_approval")


def require_reviewer_permission(request: Request, permission: str) -> ReviewerIdentity:
    identity = getattr(request.state, "reviewer_identity", None)
    if not isinstance(identity, ReviewerIdentity):
        raise HTTPException(401, "reviewer_authentication_required")
    if (
        permission not in identity.permissions
        or not identity.reviewer_id.strip()
        or len(identity.reviewer_id) > 128
    ):
        raise HTTPException(403, "reviewer_permission_required")
    return identity
