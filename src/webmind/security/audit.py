"""Audit logging for security-relevant decisions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete

from webmind.core.types import Principal
from webmind.storage.database import Database
from webmind.storage.models import AuditEvent

log = logging.getLogger("webmind.audit")


class AuditLogger:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        principal: Principal | None,
        action: str,
        *,
        decision: str,
        target: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> None:
        tenant = tenant_id or (principal.tenant_id if principal else "system")
        user_key = principal.user_key if principal else "system"
        event = AuditEvent(
            tenant_id=tenant,
            user_key=user_key,
            action=action,
            target=(target or "")[:512] or None,
            decision=decision,
            reason=reason,
            details=details or {},
        )
        try:
            with self.db.session() as s:
                s.add(event)
        except Exception:  # audit must never take the request down silently — log loudly
            log.exception("failed to persist audit event action=%s decision=%s", action, decision)
        level = logging.WARNING if decision == "deny" else logging.INFO
        log.log(
            level,
            "audit tenant=%s user=%s action=%s target=%s decision=%s reason=%s",
            tenant,
            user_key,
            action,
            target,
            decision,
            reason,
        )

    def purge(self, older_than_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        with self.db.session() as s:
            res = s.execute(delete(AuditEvent).where(AuditEvent.ts < cutoff))
            return res.rowcount or 0
