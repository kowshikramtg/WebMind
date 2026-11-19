"""Deterministic policy engine (RBAC + ABAC + resource ACLs + tenant isolation).

This module is the single authority for access decisions. Language models never
participate in authorization: they only ever see content that has already passed
these checks, and they cannot grant capabilities.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from webmind.core.config import AccessRule, SecurityConfig
from webmind.core.errors import AuthorizationError
from webmind.core.types import Principal

# Capability vocabulary used by built-in tools and endpoints.
CAPABILITIES = {
    "chat": "Use the conversational assistant",
    "search": "Run direct retrieval queries",
    "navigate": "Website navigation answers",
    "documents:read": "Read indexed documents the principal can access",
    "records:read": "Query structured datasets the principal can access",
    "analytics": "Aggregations, statistics and charts over datasets",
    "export": "Export authorized records (CSV/XLSX/PDF/JSON)",
    "reports": "Generate PDF reports",
    "sync:status": "View synchronization status",
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(v) for v in value}
    return {str(value)}


class PolicyEngine:
    def __init__(self, cfg: SecurityConfig) -> None:
        self.cfg = cfg
        self._cap_cache: dict[frozenset[str], frozenset[str]] = {}

    # -- RBAC ------------------------------------------------------------------
    def capabilities_for(self, roles: Iterable[str]) -> frozenset[str]:
        key = frozenset(roles)
        if key in self._cap_cache:
            return self._cap_cache[key]
        caps: set[str] = set()
        seen: set[str] = set()
        stack = [r for r in key if r in self.cfg.roles]
        while stack:
            role = stack.pop()
            if role in seen:
                continue
            seen.add(role)
            rc = self.cfg.roles[role]
            caps.update(rc.capabilities)
            stack.extend(p for p in rc.inherits if p not in seen)
        result = frozenset(caps)
        self._cap_cache[key] = result
        return result

    def build_principal(
        self,
        *,
        tenant_id: str,
        user_id: str | None,
        roles: Iterable[str],
        attributes: Mapping[str, Any] | None = None,
        authenticated: bool,
    ) -> Principal:
        role_set = frozenset(str(r) for r in roles)
        return Principal(
            tenant_id=tenant_id,
            user_id=user_id,
            roles=role_set,
            attributes=dict(attributes or {}),
            authenticated=authenticated,
            capabilities=self.capabilities_for(role_set),
        )

    def require_capability(self, principal: Principal, capability: str) -> None:
        if not principal.has_capability(capability):
            raise AuthorizationError(f"this operation requires the '{capability}' capability", action=capability)

    # -- ABAC / ACL ------------------------------------------------------------
    def can_access(
        self, principal: Principal, rule: AccessRule | Mapping[str, Any] | None, *, tenant_id: str
    ) -> Decision:
        """Evaluate a resource/dataset access rule for ``principal``. Deny by default."""
        if tenant_id != principal.tenant_id:
            return Decision(False, "tenant mismatch")
        if rule is None:
            return Decision(False, "no access rule")
        if isinstance(rule, Mapping):
            rule = AccessRule.model_validate(rule)
        if rule.visibility == "public":
            return Decision(True, "public")
        if not principal.authenticated:
            return Decision(False, "restricted content requires authentication")
        if rule.roles and not (set(rule.roles) & set(principal.roles)):
            return Decision(False, "missing required role")
        for attr, expected in rule.attributes.items():
            have = _as_set(principal.attributes.get(attr))
            if not have or not (have & _as_set(expected)):
                return Decision(False, f"attribute '{attr}' does not match")
        if not rule.roles and not rule.attributes:
            # 'restricted' with no constraints = any authenticated user of the tenant.
            return Decision(True, "authenticated")
        return Decision(True, "rule matched")

    def exempt(self, principal: Principal, unless_roles: Iterable[str]) -> bool:
        return bool(set(unless_roles) & set(principal.roles))
