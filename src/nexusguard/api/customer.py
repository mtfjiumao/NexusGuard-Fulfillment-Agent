"""Trust boundary for a host application's verified customer login."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class CustomerIdentity:
    tenant_id: UUID
    user_id: UUID


def get_customer_identity(request: Request) -> CustomerIdentity:
    """Only trusted authentication middleware may populate this server-side value."""
    identity = getattr(request.state, "customer_identity", None)
    if not isinstance(identity, CustomerIdentity):
        raise HTTPException(status_code=401, detail="customer_authentication_required")
    return identity
