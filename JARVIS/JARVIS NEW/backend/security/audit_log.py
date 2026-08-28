"""
backend/security/audit_log.py
J.A.R.V.I.S. Persistent Append-Only Audit Log (Data Architecture: AUDIT_LOG store;
Security Architecture §15; Permission Matrix §25).

Subscribes to the EventBus and durably persists permission-sensitive events into
the `jarvis_audit_records` table so JARVIS can always answer:
    "Who did what, why, under which permission, and whether the user approved it."

Guarantees:
  - Append-only: records are never updated or deleted by this service.
  - Secret-free: payloads pass through strict sanitization before persistence.
  - Error-isolated: audit failures never break the emitting subsystem.
"""

import uuid
import logging
from typing import Dict, Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import worker_session
from backend.infrastructure.models import AuditRecordModel, TaskModel
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType

logger = logging.getLogger("JARVIS.Security.AuditLog")

def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


# Event topics that constitute permission-sensitive audit evidence.
AUDITED_TOPIC_PREFIXES = (
    "security.",          # allowed / denied / approval_required decisions
    "approval.",          # requested / resolved lifecycle
    "emergency_stop.",    # activation / reset attempts
    "task.failed",        # execution failures with classification
    "schedule.skipped",   # authorization-denied scheduled executions
    "session.locked",     # session boundary transitions
    "session.unlocked",
)

SENSITIVE_KEYS = {"password", "secret", "api_key", "token", "credential", "auth", "otp"}


def _sanitize_payload(data: Any) -> Any:
    """Recursively redacts secrets from event payloads before durable storage."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if any(sk in str(k).lower() for sk in SENSITIVE_KEYS):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = _sanitize_payload(v)
        return sanitized
    if isinstance(data, list):
        return [_sanitize_payload(item) for item in data]
    return data


class AuditLogManager:
    """
    Durable append-only audit writer driven by EventBus telemetry.
    """

    def __init__(self):
        self._subscribed = False

    async def start(self):
        """Attaches the wildcard EventBus subscriber (idempotent)."""
        if not self._subscribed:
            await event_bus.subscribe("*", self._on_event)
            self._subscribed = True
            logger.info("AuditLogManager attached to EventBus wildcard telemetry.")

    async def stop(self):
        """Detaches the subscriber on shutdown."""
        if self._subscribed:
            await event_bus.unsubscribe("*", self._on_event)
            self._subscribed = False

    async def _on_event(self, event: JarvisEvent):
        """EventBus callback: persists selected permission-sensitive events."""
        try:
            topic = event.topic or ""
            if not any(topic.startswith(prefix) for prefix in AUDITED_TOPIC_PREFIXES):
                return

            await self.record(
                actor=event.source or "system",
                action=topic,
                resource=event.payload.get("tool") or event.payload.get("resource") or topic,
                result=self._derive_result(topic, event),
                task_id=event.task_id,
                agent_id=event.payload.get("agent"),
                approval_id=event.payload.get("approval_id"),
                security_decision=event.payload.get("reason") or event.payload.get("status"),
                state_after=_sanitize_payload(event.payload),
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            # Audit must never break the emitter — but failures are loudly logged.
            logger.error(f"Audit persistence failed for topic [{event.topic}]: {e}")

    @staticmethod
    def _derive_result(topic: str, event: JarvisEvent) -> str:
        """Maps an audited event to a compact result classification."""
        payload = event.payload or {}
        if topic.startswith("security.allowed"):
            return "ALLOWED"
        if topic.startswith("security.denied"):
            return "DENIED"
        if topic.startswith("security.approval_required"):
            return "APPROVAL_REQUIRED"
        if topic == "approval.resolved":
            return str(payload.get("status", "RESOLVED"))
        if topic.startswith("emergency_stop"):
            return "EMERGENCY_STOP"
        if topic == "task.failed":
            return "FAILED"
        if topic == "schedule.skipped":
            return "SKIPPED"
        return "RECORDED"

    async def record(
        self,
        actor: str,
        action: str,
        resource: str,
        result: str,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        approval_id: Optional[str] = None,
        security_decision: Optional[str] = None,
        state_before: Optional[Dict[str, Any]] = None,
        state_after: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> str:
        """
        Appends one immutable audit record. Returns the audit_id.
        Uses the provided session when given; otherwise opens its own.
        """
        audit_id = f"audit_{uuid.uuid4().hex[:12]}"

        # --- THE SHIELD ---
        # If the task is a temporary background check, hide its ID from the strict database
        _VIRTUAL_TASK_PREFIXES = ("cap_chk_", "ana_chk_", "obs_t_", "plan_")
        if task_id and (task_id.startswith(_VIRTUAL_TASK_PREFIXES) or task_id == "SESSION_LIFECYCLE"):
            db_task_id = None
        else:
            db_task_id = task_id
        # ------------------
        
        # Defensive FK guard: audit events for real task_ ids may fire before
        # the task row is committed (orchestration-time gates). Store the
        # record unlinked rather than failing the whole transaction.
        if db_task_id is not None:
            async with worker_session() as check_db:
                if await check_db.get(TaskModel, db_task_id) is None:
                    db_task_id = None

        record = AuditRecordModel(
            audit_id=audit_id,
            actor=str(actor)[:64],
            agent_id=agent_id,
            task_id=db_task_id,
            action=str(action)[:128],
            resource=str(resource)[:256],
            result=str(result)[:64],
            approval_id=approval_id,
            security_decision=str(security_decision)[:64] if security_decision else None,
            state_before=_sanitize_payload(state_before) if state_before else None,
            state_after=_sanitize_payload(state_after) if state_after else None,
            correlation_id=correlation_id,
            created_at=utc_now(),
        )

        if db is not None:
            db.add(record)
            await db.flush()
        else:
            async with worker_session() as session:
                session.add(record)
                await session.commit()

        logger.debug(f"Audit record appended [{audit_id}] action={action} result={result}")
        return audit_id


audit_log_manager = AuditLogManager()