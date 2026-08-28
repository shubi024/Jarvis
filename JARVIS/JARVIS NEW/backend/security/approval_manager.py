import logging
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from sqlalchemy import select, update
from backend.infrastructure.database import worker_session
from backend.infrastructure.models import ApprovalModel, ApprovalStatus as DBApprovalStatus, TaskModel
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType

logger = logging.getLogger("JARVIS.Security.ApprovalManager")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _as_utc_timestamp(dt: Optional[datetime]) -> float:
    """
    Normalizes DB-loaded datetimes to UTC epoch seconds.
    SQLite returns naive datetimes (tzinfo=None); interpreting them as local time
    would shift expiry comparisons by the host UTC offset and instantly expire rows.
    """
    if dt is None:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()

class ApprovalManager:
    """
    Approval Manager for J.A.R.V.I.S.
    Manages durable human authorization lifecycles with exact action, resource, scope, and parameter binding.
    """

    async def _emit_approval_event(self, topic: str, approval_id: str, payload: Dict[str, Any]):
        """Helper for standardized approval telemetry using EventType.APPROVAL."""
        event = JarvisEvent(
            event_type=EventType.APPROVAL,
            topic=topic,
            task_id=payload.get("task_id"),
            correlation_id=approval_id,
            source="ApprovalManager",
            payload=payload
        )
        await event_bus.publish(event)

    def _sanitize_parameters(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Redacts sensitive credentials or keys from parameter dictionaries before storing."""
        sanitized = {}
        sensitive_keys = {"password", "secret", "api_key", "token", "credential"}
        for k, v in parameters.items():
            if any(sk in k.lower() for sk in sensitive_keys):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = v
        return sanitized

    async def create_approval_request(
        self, 
        task_id: str, 
        intent: str, 
        target_agents: List[str], 
        tool_name: Optional[str],
        resource: Optional[str],
        action: Optional[str],
        parameters: Dict[str, Any],
        risk_level: str = "high",
        consequences: Optional[str] = None,
        client_scope: Optional[str] = None,
        project_scope: Optional[str] = None,
        account_scope: Optional[str] = None,
        requested_by: str = "JARVIS_RUNTIME",
        ttl_seconds: int = 3600
    ) -> str:
        """
        Creates a durable approval request with exact action, resource, scope, and parameter binding.
        Returns the unique approval request ID.
        """
        # --- THE SHIELD ---
        # If the task is a temporary background check, hide its ID from the strict database
        _VIRTUAL_TASK_PREFIXES = ("cap_chk_", "ana_chk_", "obs_t_", "plan_")
        if task_id and (task_id.startswith(_VIRTUAL_TASK_PREFIXES) or task_id == "SESSION_LIFECYCLE"):
            db_task_id = None
        else:
            db_task_id = task_id
        # ------------------
        
        approval_id = f"appr_{uuid.uuid4().hex[:12]}"

        # Defensive FK guard: orchestration-time gates may run before the task
        # row is committed. Never break the approval flow on a missing link —
        # store the approval unlinked instead of raising an IntegrityError.
        if db_task_id is not None:
            async with worker_session() as check_db:
                if await check_db.get(TaskModel, db_task_id) is None:
                    logger.warning(
                        f"Approval [{approval_id}] created WITHOUT task link: "
                        f"task [{db_task_id}] is not persisted yet."
                    )
                    db_task_id = None

        created_at = utc_now()
        expires_at = datetime.fromtimestamp(created_at.timestamp() + ttl_seconds, tz=timezone.utc)

        sanitized_parameters = self._sanitize_parameters(parameters)

        async with worker_session() as db:
            db_approval = ApprovalModel(
                approval_id=approval_id,
                task_id=db_task_id,  # This will be null for temporary checks
                intent=intent,
                target_agents=target_agents,
                tool_name=tool_name,
                resource=resource,
                action=action,
                parameters=sanitized_parameters,
                risk_level=risk_level,
                consequences=consequences or f"Execution of action '{action}' on resource '{resource}' under intent '{intent}' requires administrative verification.",
                client_scope=client_scope,
                project_scope=project_scope,
                account_scope=account_scope,
                status=DBApprovalStatus.PENDING,
                requested_by=requested_by,
                expires_at=expires_at,
                created_at=created_at,
                updated_at=created_at
            )
            db.add(db_approval)
            await db.commit()

        payload = {
            "approval_id": approval_id,
            "task_id": task_id,
            "intent": intent,
            "target_agents": target_agents,
            "tool_name": tool_name,
            "resource": resource,
            "action": action,
            "risk_level": risk_level,
            "client_scope": client_scope
        }

        await self._emit_approval_event("approval.requested", approval_id, payload)
        logger.info(f"Durable approval request created: [{approval_id}] for task [{task_id}] (Risk: {risk_level})")
        return approval_id

    async def resolve_approval(
        self, 
        approval_id: str, 
        approved: bool, 
        resolved_by: str = "human_operator",
        current_tool: Optional[str] = None,
        current_resource: Optional[str] = None,
        current_action: Optional[str] = None,
        current_client_scope: Optional[str] = None,
        current_project_scope: Optional[str] = None,
        current_account_scope: Optional[str] = None,
        current_parameters: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Resolves a pending approval request based on operator input, validating expiry 
        and detecting modified-action drift across parameters, tools, resources, actions, and scopes.
        """
        async with worker_session() as db:
            db_approval = await db.get(ApprovalModel, approval_id)
            if not db_approval:
                logger.warning(f"Attempted to resolve non-existent approval ID: [{approval_id}]")
                return False

            if db_approval.status != DBApprovalStatus.PENDING:
                logger.warning(f"Approval ID [{approval_id}] has already been resolved or expired.")
                return False

            # Check automatic expiration
            if db_approval.expires_at and _as_utc_timestamp(db_approval.expires_at) <= utc_now().timestamp():
                db_approval.status = DBApprovalStatus.EXPIRED
                db_approval.updated_at = utc_now()
                await db.commit()
                logger.warning(f"Approval ID [{approval_id}] has expired.")
                return False

            # Comprehensive Modified-Action Invalidation (Parameter & Binding Drift Detection)
            drift_detected = False
            drift_reasons = []

            if current_tool is not None and current_tool != db_approval.tool_name:
                drift_detected = True
                drift_reasons.append("tool mismatch")
            if current_resource is not None and current_resource != db_approval.resource:
                drift_detected = True
                drift_reasons.append("resource mismatch")
            if current_action is not None and current_action != db_approval.action:
                drift_detected = True
                drift_reasons.append("action mismatch")
            if current_client_scope is not None and current_client_scope != db_approval.client_scope:
                drift_detected = True
                drift_reasons.append("client_scope mismatch")
            if current_project_scope is not None and current_project_scope != db_approval.project_scope:
                drift_detected = True
                drift_reasons.append("project_scope mismatch")
            if current_account_scope is not None and current_account_scope != db_approval.account_scope:
                drift_detected = True
                drift_reasons.append("account_scope mismatch")

            if current_parameters is not None and db_approval.parameters:
                sanitized_current = self._sanitize_parameters(current_parameters)
                if sanitized_current != db_approval.parameters:
                    drift_detected = True
                    drift_reasons.append("parameter drift")

            if drift_detected:
                db_approval.status = DBApprovalStatus.REJECTED
                db_approval.rejection_reason = f"Modified-action invalidation: Drift detected ({', '.join(drift_reasons)})."
                db_approval.updated_at = utc_now()
                await db.commit()
                logger.error(f"Approval ID [{approval_id}] rejected due to structural drift: {drift_reasons}.")
                return False

            status = DBApprovalStatus.APPROVED if approved else DBApprovalStatus.REJECTED
            db_approval.status = status
            db_approval.resolved_by = resolved_by
            db_approval.resolved_at = utc_now()
            db_approval.updated_at = utc_now()
            await db.commit()

            task_id = db_approval.task_id
            intent = db_approval.intent
            risk_level = db_approval.risk_level

        # Emit canonical resolution event listened to by TaskQueue
        await self._emit_approval_event("approval.resolved", approval_id, {
            "approval_id": approval_id,
            "task_id": task_id,
            "intent": intent,
            "status": status.value,
            "resolved_by": resolved_by,
            "risk_level": risk_level
        })

        logger.info(f"Approval ID [{approval_id}] resolved with status: [{status.value}] by [{resolved_by}]")
        return True

    async def get_approval_status(self, approval_id: str) -> Optional[str]:
        """Returns the clean operational status for a given approval ID, checking expiration."""
        async with worker_session() as db:
            db_approval = await db.get(ApprovalModel, approval_id)
            if not db_approval:
                return None
            
            if db_approval.status == DBApprovalStatus.PENDING and db_approval.expires_at:
                if _as_utc_timestamp(db_approval.expires_at) <= utc_now().timestamp():
                    db_approval.status = DBApprovalStatus.EXPIRED
                    await db.commit()

            return db_approval.status.value

    async def get_approval_by_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Finds and returns the durable approval record associated with a specific task ID."""
        async with worker_session() as db:
            result = await db.execute(
                select(ApprovalModel).where(ApprovalModel.task_id == task_id)
            )
            db_approval = result.scalars().first()
            if not db_approval:
                return None

            return {
                "approval_id": db_approval.approval_id,
                "task_id": db_approval.task_id,
                "intent": db_approval.intent,
                "target_agents": db_approval.target_agents,
                "tool_name": db_approval.tool_name,
                "resource": db_approval.resource,
                "action": db_approval.action,
                "parameters_summary": db_approval.parameters,
                "client_scope": db_approval.client_scope,
                "project_scope": db_approval.project_scope,
                "risk_level": db_approval.risk_level,
                "consequences": db_approval.consequences,
                "status": db_approval.status.value,
                "created_time": db_approval.created_at.timestamp() if db_approval.created_at else None,
                "resolved_time": db_approval.resolved_at.timestamp() if db_approval.resolved_at else None,
                "resolved_by": db_approval.resolved_by
            }

    async def get_pending_approvals_for_hud(self) -> List[Dict[str, Any]]:
        """Queries all active pending approval requests for display on the administrative HUD."""
        async with worker_session() as db:
            now_ts = utc_now().timestamp()
            result = await db.execute(
                select(ApprovalModel).where(ApprovalModel.status == DBApprovalStatus.PENDING)
            )
            records = result.scalars().all()
            
            pending_list = []
            for rec in records:
                if rec.expires_at and _as_utc_timestamp(rec.expires_at) <= now_ts:
                    rec.status = DBApprovalStatus.EXPIRED
                    await db.commit()
                    continue

                pending_list.append({
                    "approval_id": rec.approval_id,
                    "task_id": rec.task_id,
                    "intent": rec.intent,
                    "target_agents": rec.target_agents,
                    "tool_name": rec.tool_name,
                    "resource": rec.resource,
                    "action": rec.action,
                    "risk_level": rec.risk_level,
                    "consequences": rec.consequences,
                    "client_scope": rec.client_scope,
                    "project_scope": rec.project_scope,
                    "created_at": rec.created_at.isoformat() if rec.created_at else None
                })
            return pending_list

approval_manager = ApprovalManager()