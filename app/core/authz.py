"""Role-based authorization, enforced on the server.

Apps declare permissions as {permission_name: set_of_roles} in their InternalApp spec.
Service functions call `ensure_permission(user, perm)`; routes may additionally use
`permission_required(perm)` as a dependency. Templates may call `can(user, perm)` to
hide buttons, but that is cosmetic only: the backend check is what protects the action.
"""
from __future__ import annotations

from fastapi import Depends

from app.core.errors import PermissionDenied
from app.core.identity import User, require_user

_PERMISSIONS: dict[str, frozenset[str]] = {}

CORE_PERMISSIONS = {
    "audit.view": {"manager"},
}


def register_permissions(perms: dict[str, set[str]]) -> None:
    for name, roles in perms.items():
        if name in _PERMISSIONS and _PERMISSIONS[name] != frozenset(roles):
            raise ValueError(f"permission {name!r} already registered with different roles")
        _PERMISSIONS[name] = frozenset(roles)


def register_app_permissions(apps) -> None:
    """Register every app's declared permissions. Idempotent; call before any service code runs."""
    for spec in apps:
        register_permissions(spec.permissions)


def roles_for(permission: str) -> frozenset[str]:
    if permission not in _PERMISSIONS:
        # Fail closed on typos / unregistered permissions.
        raise KeyError(f"unknown permission {permission!r}")
    return _PERMISSIONS[permission]


def can(user: User | None, permission: str) -> bool:
    return user is not None and user.role in roles_for(permission)


def ensure_permission(user: User | None, permission: str, *,
                      target_type: str | None = None, target_id: str | int | None = None) -> None:
    if not can(user, permission):
        role = user.role if user else "anonymous"
        raise PermissionDenied(
            f"Role '{role}' is not allowed to perform '{permission}'.",
            permission=permission, target_type=target_type, target_id=target_id,
        )


def permission_required(permission: str):
    """FastAPI dependency factory: `user = Depends(permission_required("x.y"))`."""

    def dependency(user: User = Depends(require_user)) -> User:
        ensure_permission(user, permission)
        return user

    return dependency


register_permissions(CORE_PERMISSIONS)
