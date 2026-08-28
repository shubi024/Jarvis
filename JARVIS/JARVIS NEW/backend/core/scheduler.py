"""
backend/core/scheduler.py
J.A.R.V.I.S. Time-Aware Task Scheduler.
Handles delayed, recurring, and precise-time scheduling of TaskPackages and WorkflowDefinitions.
Enforces idempotency, missed-run recovery, persistence, timezone-aware calendar math, 
and point-in-time execution security evaluations.
"""

import asyncio
import logging
import uuid
import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Union, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import worker_session
from backend.infrastructure.models import ScheduleModel, ScheduleRunModel
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.core.task_contracts import (
    TaskPackage, ApprovalState, TaskStatus
)
from backend.core.workflow_contracts import WorkflowDefinition, WorkflowStatus, ApprovalGate
from backend.security.security_manager import security_manager
from backend.core.task_queue import task_queue
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Core.Scheduler")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class JarvisScheduler:
    """
    Time-aware Orchestrator for J.A.R.V.I.S. schedules.
    Delegates task execution strictly to the TaskQueue while maintaining scheduled state 
    and enforcing just-in-time security evaluations.
    """
    
    def __init__(self):
        self.is_running = False
        self.loop_task: Optional[asyncio.Task] = None

    async def start(self):
        """Starts the scheduler loop, processing due schedules and missed runs."""
        if not self.is_running:
            self.is_running = True
            self.loop_task = asyncio.create_task(self._scheduler_loop())
            logger.info("JarvisScheduler started successfully.")

    async def stop(self):
        """Gracefully shuts down the scheduler loop without dropping active schedules."""
        self.is_running = False
        if self.loop_task:
            self.loop_task.cancel()
            try:
                await self.loop_task
            except asyncio.CancelledError:
                pass
        logger.info("JarvisScheduler stopped gracefully.")

    async def _emit_event(self, event_type: EventType, topic: str, correlation_id: str, payload: Dict[str, Any]):
        """Standardized telemetry publisher for scheduler lifecycle events."""
        event = JarvisEvent(
            event_type=event_type,
            topic=topic,
            timestamp=utc_now(),
            correlation_id=correlation_id,
            task_id=correlation_id,
            source="scheduler",
            payload=payload
        )
        await event_bus.publish(event)

    async def schedule_task(
        self,
        payload: Union[TaskPackage, WorkflowDefinition],
        run_at: datetime,
        recurrence: Optional[str] = None,
        tz_name: str = "UTC"
    ) -> str:
        """
        Registers a new delayed or recurring schedule. 
        Validates authorization at creation time before storing the payload template.
        """
        if run_at.tzinfo is None:
            raise ValueError("run_at must be timezone-aware. Use datetime(..., tzinfo=timezone.utc).")
        run_at_utc = run_at.astimezone(timezone.utc)
        
        async with worker_session() as db:
            # Authorization verification BEFORE accepting the schedule
            security_decision = await security_manager.evaluate_task_package(payload, db)
            if security_decision.get("status") == "DENIED":
                raise ExecutionError(
                    message=f"Schedule creation denied: {security_decision.get('reason')}",
                    classification=ErrorClassification.AUTHORIZATION_FAILURE
                )
                
            schedule_id = f"sched_{uuid.uuid4().hex[:8]}"
            payload_type = "WorkflowDefinition" if isinstance(payload, WorkflowDefinition) else "TaskPackage"
            
            # Persist Schedule
            db_schedule = ScheduleModel(
                schedule_id=schedule_id,
                requester_id=payload.requester,
                payload_type=payload_type,
                payload_template=payload.model_dump(mode="json"),
                status="ACTIVE",
                recurrence_rule=recurrence.strip().upper() if recurrence else None,
                timezone=tz_name,
                next_run_at=run_at_utc,
                last_run_at=None,
                created_at=utc_now(),
                updated_at=utc_now()
            )
            db.add(db_schedule)
            await db.commit()
            
        await self._emit_event(EventType.SYSTEM, "schedule.scheduled", schedule_id, {
            "run_at": run_at_utc.isoformat(),
            "recurrence": recurrence,
            "timezone": tz_name,
            "payload_type": payload_type
        })
        
        return schedule_id

    async def pause_schedule(self, schedule_id: str) -> bool:
        """Suspends a schedule from triggering until manually resumed."""
        async with worker_session() as db:
            db_schedule = await db.get(ScheduleModel, schedule_id)
            if not db_schedule or db_schedule.status != "ACTIVE":
                return False
            db_schedule.status = "PAUSED"
            db_schedule.updated_at = utc_now()
            await db.commit()
            
        await self._emit_event(EventType.SYSTEM, "schedule.paused", schedule_id, {})
        return True

    async def resume_schedule(self, schedule_id: str) -> bool:
        """Resumes a paused schedule. Missed runs will be caught in the next loop."""
        async with worker_session() as db:
            db_schedule = await db.get(ScheduleModel, schedule_id)
            if not db_schedule or db_schedule.status != "PAUSED":
                return False
            db_schedule.status = "ACTIVE"
            db_schedule.updated_at = utc_now()
            await db.commit()
            
        await self._emit_event(EventType.SYSTEM, "schedule.resumed", schedule_id, {})
        return True

    async def cancel_schedule(self, schedule_id: str) -> bool:
        """Permanently cancels a schedule. Running instances are not aborted."""
        async with worker_session() as db:
            db_schedule = await db.get(ScheduleModel, schedule_id)
            if not db_schedule or db_schedule.status in ["COMPLETED", "CANCELLED"]:
                return False
            db_schedule.status = "CANCELLED"
            db_schedule.updated_at = utc_now()
            await db.commit()
            
        await self._emit_event(EventType.SYSTEM, "schedule.cancelled", schedule_id, {})
        return True

    def get_status(self) -> Dict[str, Any]:
        """Scheduler health and status reporting."""
        return {
            "is_running": self.is_running,
            "system_time_utc": utc_now().isoformat(),
            "status": "HEALTHY" if self.is_running else "STOPPED"
        }

    async def _scheduler_loop(self):
        """Continuous polling loop scanning for due or missed schedules."""
        while self.is_running:
            try:
                await self._process_due_schedules()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop encountered error: {e}", exc_info=True)
            
            await asyncio.sleep(5.0)

    async def _process_due_schedules(self):
        """Atomically checks and claims due schedules to ensure strict idempotency."""
        now = utc_now()
        async with worker_session() as db:
            # Idempotent DB lock update to prevent duplicate multi-node triggers
            result = await db.execute(
                update(ScheduleModel).where(
                    ScheduleModel.status == "ACTIVE",
                    ScheduleModel.next_run_at <= now
                ).values(
                    status="TRIGGERING",
                    updated_at=now
                ).returning(ScheduleModel)
            )
            due_schedules = result.scalars().all()
            await db.commit()
            
            for schedule in due_schedules:
                asyncio.create_task(self._trigger_schedule(schedule.schedule_id))

    async def _trigger_schedule(self, schedule_id: str):
        """Instantiates execution payload, re-checks security, creates run record, and queues task."""
        async with worker_session() as db:
            db_schedule = await db.get(ScheduleModel, schedule_id)
            if not db_schedule:
                return
                
            try:
                # 1. Instantiate Canonical Payload with fresh IDs
                executable_payload = self._instantiate_payload(
                    schedule_id, 
                    db_schedule.payload_type, 
                    db_schedule.payload_template
                )
                
                # 2. Re-Check Security / Approvals at Execution Time 
                # (Stored approval must not silently carry over after material changes)
                security_decision = await security_manager.evaluate_task_package(executable_payload, db)
                
                if security_decision.get("status") == "DENIED":
                    logger.warning(f"Scheduled execution {schedule_id} denied at runtime. Skipping.")
                    db_schedule.status = "PAUSED"
                    await db.commit()
                    await self._emit_event(EventType.SYSTEM, "schedule.skipped", schedule_id, {"reason": "authorization_denied_at_runtime"})
                    return
                    
                # Canonical contract: SecurityManager emits "APPROVAL_REQUIRED".
                if security_decision.get("status") == "APPROVAL_REQUIRED":
                    if isinstance(executable_payload, TaskPackage):
                        executable_payload.approval_state = ApprovalState.PENDING
                    else:
                        # Workflow approval representation (canonical ApprovalGate fields)
                        executable_payload.approval_gates.append(
                            ApprovalGate(
                                gate_id=f"gate_{uuid.uuid4().hex[:8]}",
                                blocking_subtask_ids=[st.subtask_id for st in executable_payload.subtasks],
                                condition=security_decision.get("reason", "Scheduled execution requires human authorization."),
                                required_role="operator"
                            )
                        )
                        executable_payload.status = WorkflowStatus.WAITING
                else:
                    if isinstance(executable_payload, TaskPackage):
                        executable_payload.approval_state = ApprovalState.NOT_REQUIRED
                    
                exec_id = executable_payload.workflow_id if isinstance(executable_payload, WorkflowDefinition) else executable_payload.task_id

                now = utc_now()

                # 3. Create durable run record for exactly-once idempotency before TaskQueue submission
                run_record = ScheduleRunModel(
                    run_id=f"srun_{uuid.uuid4().hex[:8]}",
                    schedule_id=schedule_id,
                    execution_id=exec_id,
                    scheduled_for=db_schedule.next_run_at,
                    executed_at=now,
                    status="SUBMITTED"
                )
                db.add(run_record)
                
                # 4. Advance Time State
                next_run = self._calculate_next_run(db_schedule.next_run_at, db_schedule.recurrence_rule, getattr(db_schedule, "timezone", "UTC"))
                db_schedule.last_run_at = now
                if next_run:
                    db_schedule.next_run_at = next_run
                    db_schedule.status = "ACTIVE"
                else:
                    db_schedule.status = "COMPLETED"
                    
                db_schedule.updated_at = now
                await db.commit()

                # 5. Submit fully-compliant payload to TaskQueue
                await task_queue.submit(executable_payload)
                
                # Telemetry
                if db_schedule.next_run_at < now - timedelta(minutes=5):
                    await self._emit_event(EventType.SYSTEM, "schedule.missed_run_recovered", schedule_id, {"planned_for": db_schedule.next_run_at.isoformat()})
                
                await self._emit_event(EventType.SYSTEM, "schedule.triggered", schedule_id, {"execution_id": exec_id})
                
            except Exception as e:
                logger.error(f"Failed to trigger schedule {schedule_id}: {e}", exc_info=True)
                db_schedule.status = "PAUSED"
                db_schedule.updated_at = utc_now()
                await db.commit()
                await self._emit_event(EventType.SYSTEM, "schedule.failed", schedule_id, {"error": str(e)})

    def _instantiate_payload(self, schedule_id: str, payload_type: str, template: dict) -> Union[TaskPackage, WorkflowDefinition]:
        """Deep clones the payload, maps nested IDs uniquely, and purges previous approval state."""
        if payload_type == "TaskPackage":
            pkg = TaskPackage(**template)
            pkg.task_id = f"sch_{uuid.uuid4().hex[:8]}"
            pkg.approval_state = ApprovalState.NOT_REQUIRED  # Clear previous approval state
            
            if pkg.execution_metadata:
                pkg.execution_metadata.correlation_id = schedule_id
                pkg.execution_metadata.started_at = None
                pkg.execution_metadata.completed_at = None
                pkg.execution_metadata.retry_count = 0
            return pkg
        else:
            wf = WorkflowDefinition(**template)
            new_wf_id = f"sch_{uuid.uuid4().hex[:8]}"
            id_map = {wf.workflow_id: new_wf_id}
            
            wf.workflow_id = new_wf_id
            wf.status = WorkflowStatus.PLANNED
            wf.approval_gates = []  # Clear previous workflow approval gates
            
            if wf.metadata:
                wf.metadata.correlation_id = schedule_id
                wf.metadata.created_at = utc_now()
                wf.metadata.started_at = None
                wf.metadata.completed_at = None
            
            # Map subtask boundaries dynamically and clear their approval states
            for st in wf.subtasks:
                old_st_id = st.subtask_id
                new_st_id = f"sub_{uuid.uuid4().hex[:8]}"
                id_map[old_st_id] = new_st_id
                
                st.subtask_id = new_st_id
                st.workflow_id = new_wf_id
                st.parent_task_id = new_wf_id
                st.status = TaskStatus.PLANNED
                st.approval_state = ApprovalState.NOT_REQUIRED
                
                if st.execution_metadata:
                    st.execution_metadata.correlation_id = new_wf_id
                    st.execution_metadata.started_at = None
                    st.execution_metadata.completed_at = None
                    st.execution_metadata.retry_count = 0
            
            # Reconstruct isolated dependencies
            for st in wf.subtasks:
                for dep in st.dependencies:
                    if dep.task_id in id_map:
                        dep.task_id = id_map[dep.task_id]
                        
            for eg in wf.execution_groups:
                eg.subtask_ids = [id_map.get(sid, sid) for sid in eg.subtask_ids]
                eg.dependencies = [id_map.get(sid, sid) for sid in eg.dependencies]
                
            return wf

    def _calculate_next_run(self, current_time_utc: datetime, recurrence: Optional[str], tz_name: str) -> Optional[datetime]:
        """Calculates precise calendar-aware and timezone-aware interval offsets."""
        if not recurrence or recurrence.strip().upper() == "NONE":
            return None
            
        rule = recurrence.strip().upper()
        
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            logger.warning(f"Invalid timezone {tz_name} provided to scheduler. Falling back to UTC.")
            tz = timezone.utc

        local_time = current_time_utc.astimezone(tz)
        
        if rule == "HOURLY":
            next_local = local_time + timedelta(hours=1)
        elif rule == "DAILY":
            next_local = local_time + timedelta(days=1)
        elif rule == "WEEKLY":
            next_local = local_time + timedelta(weeks=1)
        elif rule == "MONTHLY":
            # Exact calendar logic maintaining DST boundary integrity
            month = local_time.month - 1 + 1
            year = local_time.year + month // 12
            month = month % 12 + 1
            day = min(local_time.day, calendar.monthrange(year, month)[1])
            next_local = local_time.replace(year=year, month=month, day=day)
        else:
            return None
            
        # Convert accurately back to UTC for system storage and evaluation
        return next_local.astimezone(timezone.utc)

scheduler = JarvisScheduler()
