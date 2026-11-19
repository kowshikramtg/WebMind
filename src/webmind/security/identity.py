"""Identity propagation from the host application.

The host application authenticates its users (sessions, OAuth2/OIDC, SSO — whatever it
already uses) and passes WebMind a short-lived signed JWT describing the user. WebMind
verifies the signature and expiry and derives a :class:`Principal` from the claims.

WebMind never accepts identity from unsigned request fields, and an *invalid* token is
always rejected — it never silently downgrades to anonymous access.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import jwt

from webmind.core.config import AuthenticationConfig
from webmind.core.errors import AuthenticationError
from webmind.core.types import Principal
from webmind.security.policy import PolicyEngine

_TENANT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")
_MAX_ROLES = 64
_MAX_ATTRS = 64


class IdentityVerifier:
    def __init__(
        self, cfg: AuthenticationConfig, policy: PolicyEngine, *, default_tenant: str, base_dir: str = "."
    ) -> None:
        self.cfg = cfg
        self.policy = policy
        self.default_tenant = default_tenant
        self._key: Any = None
        if cfg.mode == "host_application":
            jcfg = cfg.jwt
            if any(a.startswith("HS") for a in jcfg.algorithms):
                assert jcfg.secret is not None  # validated by config
                self._key = jcfg.secret.get_secret_value()
            else:
                pk = jcfg.public_key or ""
                if "BEGIN" not in pk:
                    p = Path(pk)
                    if not p.is_absolute():
                        p = Path(base_dir) / p
                    pk = p.read_text(encoding="utf-8")
                self._key = pk

    def anonymous(self) -> Principal:
        return self.policy.build_principal(
            tenant_id=self.default_tenant,
            user_id=None,
            roles=self.cfg.anonymous_roles,
            authenticated=False,
        )

    def verify(self, token: str | None) -> Principal:
        if not token:
            if self.cfg.allow_anonymous:
                return self.anonymous()
            raise AuthenticationError("authentication required")
        if self.cfg.mode != "host_application" or self._key is None:
            raise AuthenticationError("token authentication is not enabled on this WebMind instance")
        jcfg = self.cfg.jwt
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=list(jcfg.algorithms),
                audience=jcfg.audience,
                issuer=jcfg.issuer,
                leeway=jcfg.leeway_seconds,
                options={
                    "require": ["exp", "iat", jcfg.user_id_claim],
                    "verify_aud": jcfg.audience is not None,
                    "verify_iss": jcfg.issuer is not None,
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"invalid token: {exc}") from exc

        iat, exp = claims.get("iat"), claims.get("exp")
        if not isinstance(iat, (int, float)) or not isinstance(exp, (int, float)):
            raise AuthenticationError("invalid token: iat/exp must be numeric")
        if exp - iat > jcfg.max_token_lifetime_seconds:
            raise AuthenticationError("invalid token: lifetime exceeds the configured maximum")
        if iat > time.time() + jcfg.leeway_seconds:
            raise AuthenticationError("invalid token: issued in the future")

        user_id = str(claims[jcfg.user_id_claim])
        roles = claims.get(jcfg.roles_claim, [])
        if isinstance(roles, str):
            roles = [roles]
        if not isinstance(roles, list) or len(roles) > _MAX_ROLES:
            raise AuthenticationError("invalid token: roles claim must be a list of strings")
        roles = [str(r) for r in roles]

        attrs = claims.get(jcfg.attributes_claim, {}) or {}
        if not isinstance(attrs, dict) or len(attrs) > _MAX_ATTRS:
            raise AuthenticationError("invalid token: attributes claim must be an object")
        clean: dict[str, Any] = {}
        for k, v in attrs.items():
            if isinstance(v, (str, int, float, bool)) or (
                isinstance(v, list) and all(isinstance(x, (str, int, float)) for x in v)
            ):
                clean[str(k)] = v
            else:
                raise AuthenticationError(f"invalid token: attribute '{k}' has an unsupported type")

        tenant = str(claims.get(jcfg.tenant_claim) or self.default_tenant)
        if not _TENANT_RE.match(tenant):
            raise AuthenticationError("invalid token: malformed tenant")

        return self.policy.build_principal(
            tenant_id=tenant, user_id=user_id, roles=roles, attributes=clean, authenticated=True
        )


def issue_token(
    secret: str,
    *,
    user_id: str,
    roles: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    tenant: str | None = None,
    ttl_seconds: int = 900,
    algorithm: str = "HS256",
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    """Helper for host backends (and tests) to mint a WebMind user token."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": user_id,
        "roles": roles or [],
        "attributes": attributes or {},
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if tenant:
        claims["tenant"] = tenant
    if issuer:
        claims["iss"] = issuer
    if audience:
        claims["aud"] = audience
    return jwt.encode(claims, secret, algorithm=algorithm)
