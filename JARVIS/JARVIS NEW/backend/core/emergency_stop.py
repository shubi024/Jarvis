"""
backend/core/emergency_stop.py
J.A.R.V.I.S. Global Emergency Stop Controller.
Operates above the execution stack to provide immediate, fail-safe halting of all consequential
tasks, agent runtimes, and schedules. Enforces strict authorization for system recovery.
"""

import asyncio
import logging
import json
import os
import uuid
from enum import Enum
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import worker_session
from backend.infrastructure.models import (
    TaskModel, TaskStatus as DBTaskStatus,
    ApprovalModel, ApprovalStatus as DBApprovalStatus,
    PermissionModel, PermissionStatus as DBPermissionStatus
)
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.security.permissions import PermissionLevel

from backend.core.task_queue import task_queue
from backend.core.scheduler import scheduler
from backend.security.security_manager import security_manager
from backend.core.task_contracts import TaskPackage, PermissionScope, ApprovalState, VerificationContract, ExpectedOutput
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Core.EmergencyStop")

STATE_FILE_PATH = os.getenv("JARVIS_EMERGENCY_STATE_FILE", ".jarvis_emergency_lock.json")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EmergencyStopState(str, Enum):
    ACTIVE = "ACTIVE"       # System is halted
    STOPPED = "STOPPED"     # Emergency stop is disengaged (System is running normally)
    RESETTING = "RESETTING" # System is evaluating security bounds to resume


class EmergencyStopController:
    """
    Global Fail-Safe Controller for J.A.R.V.I.S.
    Maintains authoritative state over system execution capabilities.
    """

    def __init__(self):
        self._state: EmergencyStopState = EmergencyStopState.STOPPED
        self._lock = asyncio.Lock()
        self._last_reason: str = "System Initialized"
        self._last_source: str = "SYSTEM"

    async def initialize(self):
        """Loads persistent state on startup to prevent bypass via reboot."""
        await self._load_persisted_state()
        if self._state == EmergencyStopState.ACTIVE:
            # Re-sync the SecurityManager fail-closed flag so BOTH authority states
            # agree after a reboot that occurred while Emergency Stop was active.
            security_manager.trigger_emergency_stop()
            logger.critical("J.A.R.V.I.S. initialized in EMERGENCY STOP state. Execution is blocked.")
        else:
            # Keep both authority states consistent after a normal reboot.
            security_manager.reset_emergency_stop()
            logger.info("Emergency Stop Controller initialized. System state is NORMAL.")

    async def _emit_audit_event(self, topic: str, payload: Dict[str, Any]):
        """Publishes critical audit telemetry regarding emergency stop operations."""
        event = JarvisEvent(
            event_type=EventType.SYSTEM,
            topic=topic,
            timestamp=utc_now(),
            correlation_id=f"estop_{uuid.uuid4().hex[:8]}",
            task_id="SYSTEM_GLOBAL",
            source="EmergencyStop",
            payload=payload
        )
        await event_bus.publish(event)

    async def _persist_state(self, state: EmergencyStopState, reason: str, source: str):
        """Dual-layer fail-safe persistence (File + DB) ensuring state survives complete outages."""
        self._state = state
        self._last_reason = reason
        self._last_source = source

        state_doc = {
            "state": state.value,
            "reason": reason,
            "source": source,
            "timestamp": utc_now().isoformat()
        }

        # 1. Fallback File Lock (Guarantees persistence even if DB is isolated/crashed)
        try:
            with open(STATE_FILE_PATH, "w") as f:
                json.dump(state_doc, f)
        except Exception as e:
            logger.error(f"Failed to persist emergency state to file lock: {e}")

        # 2. Database Sync (If reachable)
        try:
            async with worker_session() as db:
                if state == EmergencyStopState.ACTIVE:
                    await self._invalidate_pending_execution_db(db, reason)
        except Exception as e:
            logger.error(f"Database sync failed during emergency state persistence: {e}")

    async def _load_persisted_state(self):
        """Loads state from the fail-safe lock file."""
        if os.path.exists(STATE_FILE_PATH):
            try:
                with open(STATE_FILE_PATH, "r") as f:
                    doc = json.load(f)
                    self._state = EmergencyStopState(doc.get("state", "STOPPED"))
                    self._last_reason = doc.get("reason", "Unknown")
                    self._last_source = doc.get("source", "Unknown")
            except Exception as e:
                logger.error(f"Corrupted emergency lock file. Defaulting to safe-halt ACTIVE state: {e}")
                self._state = EmergencyStopState.ACTIVE

    async def _invalidate_pending_execution_db(self, db: AsyncSession, reason: str):
        """Sweeps database to forcefully cancel tasks, workflows, and pending approvals."""
        # 1. Cancel Tasks
        cancellable_states = [
            DBTaskStatus.QUEUED, 
            DBTaskStatus.IN_PROGRESS, 
            DBTaskStatus.EXECUTING, 
            DBTaskStatus.VERIFYING, 
            DBTaskStatus.RETRYING,
            DBTaskStatus.APPROVAL
        ]
        
        await db.execute(
            update(TaskModel).where(
                TaskModel.status.in_(cancellable_states)
            ).values(
                status=DBTaskStatus.CANCELLED,
                failure_reason=f"EMERGENCY STOP ACTIVATED: {reason}",
                updated_at=utc_now()
            )
        )
        
        # 2. Reject Pending Approvals (Strict Validation Guard)
        # Canonical enum only: "REVOKED" is not a member of ApprovalStatus and would
        # violate the SQLEnum constraint; rejected approvals can never be reused.
        await db.execute(
            update(ApprovalModel).where(
                ApprovalModel.status == DBApprovalStatus.PENDING
            ).values(
                status=DBApprovalStatus.REJECTED,
                rejection_reason=f"EMERGENCY STOP ACTIVATED: {reason}",
                updated_at=utc_now()
            )
        )
        await db.commit()

    def assert_system_running(self):
        """
        Structural guardrail. Raises an ExecutionError if the system is halted.
        Should be called by API Engines and entry points before dispatching tasks.
        """
        if self._state != EmergencyStopState.STOPPED:
            raise ExecutionError(
                message=f"Global Emergency Stop is active. Execution blocked. Reason: {self._last_reason}",
                classification=ErrorClassification.SECURITY_FAILURE
            )

    async def activate_emergency_stop(self, reason: str, source: str):
        """
        Immediately halts the execution stack, cancels active tasks, and suspends the queue/scheduler.
        Idempotent: Subsequent calls log the attempt but do not crash.
        """
        async with self._lock:
            if self._state == EmergencyStopState.ACTIVE:
                logger.warning(f"Emergency Stop re-activation ignored. Already ACTIVE. Source: {source}")
                return

            logger.critical(f"EMERGENCY STOP ACTIVATED. Source: {source} | Reason: {reason}")

            # 0. Synchronize the SecurityManager fail-closed flag so every security gate
            # across the system denies immediately (single authoritative halt signal).
            security_manager.trigger_emergency_stop()

            # 1. State locking & persistence
            await self._persist_state(EmergencyStopState.ACTIVE, reason, source)
            await self._emit_audit_event("emergency_stop.activated", {"reason": reason, "source": source})

            # 2. Explicitly Cancel Active Executions
            active_task_ids = list(task_queue.active_execution_tasks.keys())
            for tid in active_task_ids:
                try:
                    await task_queue.cancel_task(tid, reason=f"EMERGENCY STOP ACTIVATED: {reason}")
                except Exception as e:
                    logger.error(f"Failed to explicitly cancel active execution {tid} during Emergency Stop: {e}")

            # 3. Halt Subsystems (Injects CancelledError down to AgentRuntime/Tools)
            try:
                await task_queue.stop()
                await scheduler.stop()
            except Exception as e:
                logger.error(f"Error while stopping execution subsystems: {e}", exc_info=True)

            logger.critical("All J.A.R.V.I.S. execution subsystems have been forcefully halted.")

    async def _validate_reset_authorization(self, requester: str) -> bool:
        """
        Authorizes an emergency-stop reset.

        Policy:
        - The interactive local operator ("User"/"human_operator"/"local_operator") is the
          final system authority by design (personal-device deployment) and may reset.
        - Any other principal must hold an ACTIVE durable grant at L4 covering the
          "system/core" resource for the SYSTEM_RECOVERY action.
        """
        LOCAL_OPERATORS = {"User", "human_operator", "local_operator"}
        if requester in LOCAL_OPERATORS:
            logger.info(f"Emergency stop reset authorized for local operator [{requester}].")
            return True

        try:
            async with worker_session() as db:
                result = await db.execute(
                    select(PermissionModel).where(
                        PermissionModel.principal_id == requester,
                        PermissionModel.status == DBPermissionStatus.ACTIVE
                    )
                )
                grants = result.scalars().all()
                now_ts = utc_now().timestamp()
                for grant in grants:
                    if grant.expires_at and grant.expires_at.timestamp() <= now_ts:
                        continue

                    grant_lvl = getattr(grant, "permission_level", None)
                    if isinstance(grant_lvl, str):
                        try:
                            grant_lvl = PermissionLevel[grant_lvl]
                        except KeyError:
                            grant_lvl = PermissionLevel.L0_DENY
                    # Locked matrix: only an explicit L4 PRE-AUTHORIZED grant may
                    # authorize narrow system recovery.
                    if grant_lvl is None or grant_lvl < PermissionLevel.L4_PRE_AUTHORIZED:
                        continue

                    resource_ok = grant.resource in (None, "*", "system/core")
                    action_ok = grant.action in (None, "*", "SYSTEM_RECOVERY")
                    if resource_ok and action_ok:
                        logger.info(f"Emergency stop reset authorized via L4 grant [{grant.permission_id}] for [{requester}].")
                        return True
            return False
        except Exception as e:
            logger.error(f"Authorization evaluation failed during emergency reset attempt: {e}")
            return False

    async def reset_emergency_stop(self, requester: str, reason: str):
        """
        Safely evaluates authorization and restores execution capabilities.
        Does NOT automatically restart tasks that were cancelled during the halt.
        """
        async with self._lock:
            if self._state == EmergencyStopState.STOPPED:
                logger.info(f"Reset ignored. System is already running normally.")
                return

            logger.warning(f"Emergency Stop RESET requested by {requester}. Reason: {reason}")
            
            # 1. State Transition
            await self._persist_state(EmergencyStopState.RESETTING, reason, requester)
            await self._emit_audit_event("emergency_stop.reset_initiated", {"requester": requester, "reason": reason})

            # 2. Strict Security Evaluation
            is_authorized = await self._validate_reset_authorization(requester)
            if not is_authorized:
                logger.critical(f"UNAUTHORIZED reset attempt by {requester}. State remains HALTED.")
                await self._persist_state(EmergencyStopState.ACTIVE, "Unauthorized reset attempt blocked.", requester)
                await self._emit_audit_event("emergency_stop.reset_denied", {"requester": requester})
                raise ExecutionError(
                    message="Insufficient privileges to reset J.A.R.V.I.S. Emergency Stop.",
                    classification=ErrorClassification.SECURITY_FAILURE
                )

            # 3. Resume Subsystems safely
            try:
                await task_queue.start()
                await scheduler.start()
            except Exception as e:
                logger.critical(f"Failed to cleanly start subsystems during reset: {e}")
                # Fallback to active halt if recovery fails internally
                await self._persist_state(EmergencyStopState.ACTIVE, f"Subsystem recovery failure: {e}", "SYSTEM")
                raise ExecutionError(
                    message="System recovery encountered a fatal failure. Halting.",
                    classification=ErrorClassification.UNKNOWN_ERROR
                )

            # 4. Finalize Reset (release the SecurityManager fail-closed flag as well)
            security_manager.reset_emergency_stop()
            await self._persist_state(EmergencyStopState.STOPPED, "System Restored", requester)
            await self._emit_audit_event("emergency_stop.reset_completed", {"requester": requester, "reason": reason})
            logger.info("J.A.R.V.I.S. Emergency Stop successfully reset. Normal operations resumed.")


emergency_stop = EmergencyStopController()