"""
Authorization hooks (spec #40).

The real CMT Veda authentication system will be integrated later. Until
then, this module provides a clean, swappable seam: a `Principal` (who is
calling, and with what role) resolved from a bearer token, and FastAPI
dependencies that *enforce* role requirements server-side -- so
administrative operations never depend on the frontend simply hiding a
button.

To integrate real auth later: replace `resolve_principal()`'s body with a
call into the real identity system (e.g. verify a JWT issued by CMT Veda's
auth provider and map its claims to a Principal). Nothing else in the
engine needs to change, because every router depends on `require_role(...)`
rather than on this module's internals.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.models.enums import Role

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str  # opaque identifier for audit logging (spec #28 performed_by)
    role: Role


def resolve_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    settings = get_settings()

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = credentials.credentials
    if token in settings.admin_tokens:
        return Principal(subject=f"admin:{token[-6:]}", role=Role.discovery_administrator)
    if token in settings.reviewer_tokens:
        return Principal(subject=f"reviewer:{token[-6:]}", role=Role.reviewer)
    if token in settings.service_tokens:
        return Principal(subject=f"service:{token[-6:]}", role=Role.service)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def require_role(*allowed: Role):
    """FastAPI dependency factory: 403s unless the caller has one of `allowed` roles."""

    def _dependency(principal: Principal = Depends(resolve_principal)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {[r.value for r in allowed]}",
            )
        return principal

    return _dependency


# Common shorthand dependencies used across routers.
require_admin = require_role(Role.discovery_administrator)
require_reviewer_or_admin = require_role(Role.reviewer, Role.discovery_administrator)
require_any_authenticated = require_role(Role.discovery_administrator, Role.reviewer, Role.service)
