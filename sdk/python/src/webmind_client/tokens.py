"""Sign short-lived WebMind user tokens in your backend (never in the browser)."""

from __future__ import annotations

import time
from typing import Any

import jwt


def sign_user_token(
    secret: str,
    *,
    user_id: str,
    roles: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    tenant: str | None = None,
    ttl_seconds: int = 600,
    algorithm: str = "HS256",
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    """Return a JWT that WebMind accepts as the identity of ``user_id``.

    ``secret`` is the HS* secret (or an RS*/ES* private key in PEM) matching the WebMind
    server's ``security.authentication.jwt`` configuration. Keep ``ttl_seconds`` short.
    """
    if not user_id:
        raise ValueError("user_id is required")
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "roles": list(roles or []),
        "attributes": dict(attributes or {}),
        "iat": now,
        "exp": now + int(ttl_seconds),
    }
    if tenant:
        claims["tenant"] = tenant
    if issuer:
        claims["iss"] = issuer
    if audience:
        claims["aud"] = audience
    return jwt.encode(claims, secret, algorithm=algorithm)
