"""
backend/core/task_queue.py
Canonical J.A.R.V.I.S. Task Execution Backbone.
Strictly orchestrates TaskPackages and WorkflowDefinitions: priority scheduling, 
dependency resolution, ExecutionGroup gating, retry handling, verification handoff, 
and final workflow synthesis.
"""

import asyncio
import logging
import uuid
import time
import dataclasses
from typing import Dict, Any, List, Union, Optional
from datetime import datetime, timezone, date
from enum import Enum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import worker_session
from backend.infrastructure.models import (
    TaskModel, VerificationModel, UserModel,
    TaskStatus as DBTaskStatus, VerificationStatus as DBVerificationStatus
)
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.core.task_contracts import (
    TaskPackage, SubtaskPackage, TaskStatus, ResultStatus, ApprovalState,
    TaskDependency, ResultPackage, VerificationContract,
    ExecutionMetadata
)
from backend.core.workflow_contracts import WorkflowDefinition, WorkflowStatus
from backend.core.execution_errors import ExecutionError, ErrorClassification
from backend.core.agent_runtime import agent_runtime
from backend.core.verification_engine import verification_engine

logger = logging.getLogger("JARVIS.Core.TaskQueue")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _json_safe(value: Any) -> Any:
    """
    Recursively converts non-JSON-serializable values (datetime/date/Enum) into
    JSON-safe primitives before they are written to PostgreSQL JSON columns.
    Without this, task completion records (actions_performed, evidence) that embed
    `datetime` timestamps or TaskStatus enums crash the final persistence UPDATE
    with 'Object of type datetime is not JSON serializable' — the tool ran fine but
    the task could never complete and the user got no response.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value if hasattr(value, "value") else str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value

async def _ensure_requester(db: AsyncSession, requester_id: Any) -> str:
    """
    Ensures the requester principal exists in jarvis_users. Voice/UI entry
    points submit tasks under ad-hoc principals (e.g. 'VoiceUser',
    'human_user') which historically violated the jarvis_tasks.requester_id
    foreign key and crashed the whole command pipeline. Idempotent.
    """
    requester_id = str(requester_id or "system")[:64]
    if not await db.get(UserModel, requester_id):
        db.add(UserModel(user_id=requester_id, username=requester_id, role="operator"))
        # Explicitly flush the user row NOW. Without this, SQLAlchemy must defer the
        # INSERT ordering to its dependency tracker — and because jarvis_tasks only
        # declares a plain ForeignKey (no relationship()), the task row can be
        # written first, violating jarvis_tasks.requester_id_fkey and crashing the
        # whole command pipeline (observed for 'human_user' via the WS path).
        await db.flush()
    return requester_id

@dataclasses.dataclass(order=True)
class QueueItem:
    priority: int
    timestamp: float
    task_id: str = dataclasses.field(compare=False)
    is_workflow: bool = dataclasses.field(compare=False, default=False)


class TaskQueue:
    """
    J.A.R.V.I.S. Task Execution Backbone.
    Strictly orchestrates lifecycle, dependencies, retries, and persistence.
    """
    def __init__(self, max_concurrency: int = 5):
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.workers: List[asyncio.Task] = []
        self.max_concurrency = max_concurrency
        self.is_running = False
        self.active_execution_tasks: Dict[str, asyncio.Task] = {}

    async def start(self):
        if not self.is_running:
            self.is_running = True
            await event_bus.subscribe("approval.resolved", self._handle_approval_resolved)
            
            self.workers = [
                asyncio.create_task(self._worker_loop(i)) 
                for i in range(self.max_concurrency)
            ]
            logger.info(f"TaskQueue started with {self.max_concurrency} concurrent workers.")
            asyncio.create_task(self._recover_pending_tasks())

    async def stop(self):
        self.is_running = False
        for worker in self.workers:
            worker.cancel()
        
        active_task_ids = list(self.active_execution_tasks.keys())
        for task_id in active_task_ids:
            await self.cancel_task(task_id, reason="System shutdown during execution.")
            
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        self.active_execution_tasks.clear()
        # Persist the shutdown boundary: any task row still in an active state
        # (e.g. stuck VERIFYING when a worker was cancelled mid-flight) is
        # explicitly marked CANCELLED here, so the next startup's crash
        # recovery finds a clean slate instead of orphaned work.
        await self._fail_unsafe_tasks(reason="Marked cancelled during graceful shutdown.")
        logger.info("TaskQueue stopped gracefully. Active tasks marked as cancelled.")

    async def _fail_unsafe_tasks(self, reason: str):
        """Marks every task left in an active execution state (IN_PROGRESS/
        EXECUTING/VERIFYING) as CANCELLED, committing the boundary to the DB."""
        try:
            async with worker_session() as db:
                result = await db.execute(
                    select(TaskModel).where(
                        TaskModel.status.in_([DBTaskStatus.IN_PROGRESS, DBTaskStatus.EXECUTING, DBTaskStatus.VERIFYING])
                    )
                )
                unsafe_tasks = result.scalars().all()
                for db_task in unsafe_tasks:
                    logger.warning(f"Task {db_task.task_id} left in unsafe state {db_task.status} at shutdown. Marking CANCELLED.")
                    db_task.status = DBTaskStatus.CANCELLED
                    db_task.cancellation_reason = reason
                    db_task.updated_at = utc_now()
                await db.commit()
                for db_task in unsafe_tasks:
                    await self._emit_event(EventType.TASK, "task.cancelled", db_task.task_id, {"reason": "graceful_shutdown"})
        except Exception as e:
            logger.error(f"Shutdown task-state sweep failed (startup recovery will reconcile): {e}")

    async def _emit_event(self, event_type: EventType, topic: str, correlation_id: str, payload: Dict[str, Any]):
        event = JarvisEvent(
            event_type=event_type,
            topic=topic,
            timestamp=utc_now(),
            correlation_id=correlation_id,
            task_id=correlation_id,
            source="TaskQueue",
            payload=payload
        )
        await event_bus.publish(event)

    async def _recover_pending_tasks(self):
        """
        Scans DB for orphaned tasks. 
        Safe states (QUEUED, RETRYING) are requeued preserving priority.
        Unsafe states (EXECUTING, VERIFYING) are marked FAILED to prevent duplicate consequences.
        """
        async with worker_session() as db:
            result = await db.execute(
                select(TaskModel.task_id, TaskModel.priority).where(
                    TaskModel.status.in_([DBTaskStatus.QUEUED, DBTaskStatus.RETRYING])
                )
            )
            safe_tasks = result.all()
            for tid, prio in safe_tasks:
                logger.info(f"Recovering safe pending task: {tid}")
                await self.queue.put(QueueItem(priority=prio or 5, timestamp=time.time(), task_id=tid))

            result_unsafe = await db.execute(
                select(TaskModel).where(
                    TaskModel.status.in_([DBTaskStatus.IN_PROGRESS, DBTaskStatus.EXECUTING, DBTaskStatus.VERIFYING])
                )
            )
            unsafe_tasks = result_unsafe.scalars().all()
            for db_task in unsafe_tasks:
                logger.warning(f"Task {db_task.task_id} orphaned in unsafe state {db_task.status}. Marking FAILED.")
                db_task.status = DBTaskStatus.FAILED
                db_task.failure_reason = "System crashed during active execution/verification. Manual reconciliation required."
                db_task.updated_at = utc_now()
                await db.commit()
                await self._emit_event(EventType.TASK, "task.failed", db_task.task_id, {"error": "orphaned_crash_reconciliation"})

    async def submit(self, payload: Union[TaskPackage, WorkflowDefinition]):
        """Canonical queue manager for TaskPackage and WorkflowDefinition execution dispatch."""
        if isinstance(payload, WorkflowDefinition):
            await self._enqueue_workflow(payload)
        elif isinstance(payload, TaskPackage):
            await self._enqueue_task(payload)
        else:
            raise ValueError("Unsupported payload type submitted to TaskQueue.")

    async def register_task(self, payload: Union[TaskPackage, WorkflowDefinition]):
        """
        Persists the canonical task/workflow PARENT record WITHOUT enqueueing it.

        Called by the Brain BEFORE orchestration-time security gating so that
        records created by the gate (durable approval requests, audit records)
        can satisfy their foreign keys against jarvis_tasks. The later
        _enqueue_task / _enqueue_workflow call upserts onto the same row.
        Status PLANNED keeps the row invisible to crash-recovery requeueing.
        """
        task_id = getattr(payload, "task_id", None) or getattr(payload, "workflow_id", None)
        if not task_id:
            return

        workflow_metadata = getattr(payload, "metadata", None)
        priority = getattr(workflow_metadata, "priority", None) if workflow_metadata is not None else None
        if priority is None:
            priority = getattr(payload, "priority", 5)
        priority = priority or 5

        async with worker_session() as db:
            if await db.get(TaskModel, task_id):
                return
            requester = await _ensure_requester(db, getattr(payload, "requester", None))
            db_task = TaskModel(
                task_id=task_id,
                requester_id=requester,
                intent=getattr(payload, "intent", None) or "WORKFLOW_EXECUTION",
                objective=getattr(payload, "objective", "") or "",
                status=DBTaskStatus.PLANNED,
                priority=priority,
                context_snapshot=payload.model_dump(mode="json"),
                created_at=utc_now(),
            )
            db.add(db_task)
            await db.commit()
        logger.info(f"Task [{task_id}] registered (pre-gate persistence, not yet enqueued).")

    async def _enqueue_task(self, task_package: TaskPackage):
        task_package.status = TaskStatus.QUEUED
        priority = getattr(task_package, "priority", 5)
        
        async with worker_session() as db:
            await _ensure_requester(db, task_package.requester)
            db_task = await db.get(TaskModel, task_package.task_id)
            if not db_task:
                db_task = TaskModel(
                    task_id=task_package.task_id,
                    requester_id=task_package.requester,
                    intent=task_package.intent,
                    objective=task_package.objective,
                    status=DBTaskStatus.QUEUED,
                    priority=priority,
                    retry_count=task_package.execution_metadata.retry_count if task_package.execution_metadata else 0,
                    context_snapshot=task_package.model_dump(mode="json"),
                    created_at=utc_now()
                )
                db.add(db_task)
            else:
                db_task.status = DBTaskStatus.QUEUED
                db_task.priority = priority
                db_task.context_snapshot = task_package.model_dump(mode="json")
                db_task.updated_at = utc_now()
            await db.commit()

        # If Approval is pending, do not push to execution queue yet
        if getattr(task_package, "approval_state", ApprovalState.NOT_REQUIRED) in [ApprovalState.PENDING, ApprovalState.REQUIRED]:
            await self._emit_event(EventType.TASK, "task.blocked", task_package.task_id, {"reason": "awaiting_approval"})
            return

        await self._emit_event(EventType.TASK, "task.queued", task_package.task_id, {"priority": priority})
        await self.queue.put(QueueItem(priority=priority, timestamp=time.time(), task_id=task_package.task_id))

    async def _enqueue_workflow(self, workflow: WorkflowDefinition):
        # Canonical contract: Brain/Scheduler mark approval-gated workflows as WAITING
        # BEFORE submission. Such workflows are persisted but NOT enqueued until the
        # "approval.resolved" event resumes them.
        is_waiting_for_approval = workflow.status == WorkflowStatus.WAITING
        if not is_waiting_for_approval:
            workflow.status = WorkflowStatus.QUEUED
        priority = getattr(workflow.metadata, "priority", 5) if workflow.metadata else 5
        
        async with worker_session() as db:
            requester = await _ensure_requester(db, workflow.requester)
            db_workflow = await db.get(TaskModel, workflow.workflow_id)
            if db_workflow:
                # Parent row was pre-registered by register_task() before the
                # security gate — upsert onto it instead of duplicate-inserting.
                db_workflow.requester_id = requester
                db_workflow.intent = "WORKFLOW_EXECUTION"
                db_workflow.objective = workflow.objective
                db_workflow.status = DBTaskStatus.APPROVAL if is_waiting_for_approval else DBTaskStatus.QUEUED
                db_workflow.priority = priority
                db_workflow.context_snapshot = workflow.model_dump(mode="json")
                db_workflow.updated_at = utc_now()
            else:
                db_workflow = TaskModel(
                    task_id=workflow.workflow_id,
                    requester_id=requester,
                    intent="WORKFLOW_EXECUTION",
                    objective=workflow.objective,
                    status=DBTaskStatus.APPROVAL if is_waiting_for_approval else DBTaskStatus.QUEUED,
                    priority=priority,
                    context_snapshot=workflow.model_dump(mode="json"),
                    created_at=utc_now()
                )
                db.add(db_workflow)
            
            for subtask in workflow.subtasks:
                subtask.status = TaskStatus.QUEUED
                db_subtask = TaskModel(
                    task_id=subtask.subtask_id,
                    workflow_id=workflow.workflow_id,
                    parent_task_id=workflow.parent_task_id or workflow.workflow_id,
                    intent=subtask.input_data.get("intent", "WORKFLOW_SUBTASK"),
                    objective=subtask.objective,
                    # PLANNED rows are invisible to crash-recovery requeueing while the
                    # workflow sits at the approval gate.
                    status=DBTaskStatus.PLANNED if is_waiting_for_approval else DBTaskStatus.QUEUED,
                    priority=priority,
                    context_snapshot=subtask.model_dump(mode="json"),
                    created_at=utc_now()
                )
                db.add(db_subtask)
            await db.commit()

        if is_waiting_for_approval:
            await self._emit_event(EventType.WORKFLOW, "workflow.blocked", workflow.workflow_id, {"reason": "awaiting_approval"})
            return

        await self._emit_event(EventType.WORKFLOW, "workflow.queued", workflow.workflow_id, {"subtasks_count": len(workflow.subtasks)})

        # Dependency-driven dispatch (spec §9/§10): only enqueue subtasks whose
        # dependencies are already satisfied; the rest stay PLANNED and are
        # enqueued by _check_workflow_completion as their parents complete.
        async with worker_session() as db:
            await self._enqueue_ready_subtasks(db, workflow.workflow_id)

    async def cancel_task(self, task_id: str, reason: str = "Cancelled by request.") -> bool:
        async with worker_session() as db:
            db_task = await db.get(TaskModel, task_id)
            if not db_task or db_task.status in [DBTaskStatus.COMPLETED, DBTaskStatus.FAILED, DBTaskStatus.CANCELLED]:
                return False

            db_task.status = DBTaskStatus.CANCELLED
            db_task.failure_reason = reason
            db_task.updated_at = utc_now()
            await db.commit()

        if task_id in self.active_execution_tasks:
            self.active_execution_tasks[task_id].cancel()

        await self._emit_event(EventType.TASK, "task.cancelled", task_id, {"reason": reason})
        return True

    async def _handle_approval_resolved(self, event: JarvisEvent):
        """Resumes tasks that were paused at the approval boundary."""
        task_id = event.payload.get("task_id")
        status = event.payload.get("status")
        if not task_id: 
            return
            
        async with worker_session() as db:
            db_task = await db.get(TaskModel, task_id)
            if db_task and db_task.status in [DBTaskStatus.QUEUED, DBTaskStatus.IN_PROGRESS, DBTaskStatus.APPROVAL]:
                # IMPORTANT: assign a COPY of the JSON snapshot. Reassigning the same
                # mutated object is invisible to SQLAlchemy's unit of work (old is new
                # by identity), so the JSON column would silently never persist.
                snapshot = dict(db_task.context_snapshot or {})

                if status == "APPROVED":
                    snapshot["approval_state"] = ApprovalState.APPROVED.value
                    db_task.context_snapshot = snapshot
                    is_workflow_parent = isinstance(snapshot, dict) and "synthesis" in snapshot
                    # A workflow parent never executes on its own: it only anchors
                    # synthesis. It stays WAITING until every child reaches a
                    # terminal state and completion checking enqueues synthesis.
                    db_task.status = DBTaskStatus.WAITING if is_workflow_parent else DBTaskStatus.QUEUED
                    db_task.updated_at = utc_now()
                    await db.commit()

                    await self._emit_event(EventType.TASK, "task.resumed", task_id, {"reason": "approval_granted"})

                    if is_workflow_parent:
                        # Dependency-aware dispatch ONLY: subtasks whose dependencies
                        # are all COMPLETED are enqueued; dependent ones stay PLANNED
                        # until their turn. Synthesis is triggered exclusively by
                        # _check_workflow_completion once ALL children are terminal,
                        # so incomplete results can never be synthesized early.
                        await self._enqueue_ready_subtasks(db, task_id)
                        await self._check_workflow_completion(db, task_id)
                    else:
                        await self.queue.put(QueueItem(priority=db_task.priority or 5, timestamp=time.time(), task_id=task_id))
                else:
                    db_task.status = DBTaskStatus.FAILED
                    db_task.failure_reason = "Human approval rejected by ApprovalManager."
                    db_task.updated_at = utc_now()
                    await db.commit()
                    await self._emit_event(EventType.TASK, "task.failed", task_id, {"error": "approval_rejected"})

    async def _check_dependencies(self, db: AsyncSession, dependencies: List[Any]) -> str:
        """Validates execution readiness against parent Workflow ExecutionGroup rules."""
        for dep in dependencies:
            dep_id = dep.get("task_id") if isinstance(dep, dict) else getattr(dep, "task_id", None)
            if not dep_id:
                continue
                
            dep_task = await db.get(TaskModel, dep_id)
            if not dep_task:
                return "WAITING"
            
            if dep_task.status in [DBTaskStatus.FAILED, DBTaskStatus.CANCELLED]:
                return "BLOCKED_PERMANENTLY"
            if dep_task.status != DBTaskStatus.COMPLETED:
                return "WAITING"
        return "READY"

    async def _worker_loop(self, worker_id: int):
        """Worker-safe async execution maintaining strict concurrency limits."""
        while self.is_running:
            try:
                queue_item: QueueItem = await self.queue.get()
                task_id = queue_item.task_id
                
                execution_coro = self._execute_lifecycle(task_id, queue_item.is_workflow)
                task_handle = asyncio.create_task(execution_coro)
                self.active_execution_tasks[task_id] = task_handle

                try:
                    await asyncio.wait_for(task_handle, timeout=3600.0) 
                except asyncio.TimeoutError:
                    logger.error(f"Task {task_id} exceeded structural timeout.")
                    await self.cancel_task(task_id, reason="Queue hard timeout exceeded.")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Worker exception for task {task_id}: {e}", exc_info=True)
                finally:
                    self.active_execution_tasks.pop(task_id, None)
                    self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1)

    async def _execute_lifecycle(self, task_id: str, is_workflow_synthesis: bool = False):
        if is_workflow_synthesis:
            return await self._execute_workflow_synthesis(task_id)

        async with worker_session() as db:
            result = await db.execute(
                update(TaskModel).where(
                    TaskModel.task_id == task_id,
                    TaskModel.status.in_([DBTaskStatus.QUEUED, DBTaskStatus.RETRYING])
                ).values(
                    status=DBTaskStatus.IN_PROGRESS, 
                    updated_at=utc_now()
                ).returning(TaskModel)
            )
            db_task = result.scalar_one_or_none()
            if not db_task:
                return 
            await db.commit()

            context_data = db_task.context_snapshot
            is_subtask = "subtask_id" in context_data
            
            if is_subtask:
                subtask_pkg = SubtaskPackage(**context_data)
                
                c_scope = None
                p_scope = None
                if subtask_pkg.permission_scope and isinstance(subtask_pkg.permission_scope.scope, dict):
                    c_scope = subtask_pkg.permission_scope.scope.get("client")
                    p_scope = subtask_pkg.permission_scope.scope.get("project")

                # Transform to canonical TaskPackage for execution runtime
                task_package = TaskPackage(
                    task_id=subtask_pkg.subtask_id,
                    parent_task_id=subtask_pkg.workflow_id,
                    intent=subtask_pkg.input_data.get("intent", "WORKFLOW_SUBTASK"),
                    objective=subtask_pkg.objective,
                    target_agents=[subtask_pkg.assigned_agent],
                    selected_tools=subtask_pkg.input_data.get("tools", []),
                    tool_parameters=subtask_pkg.input_data.get("parameters", {}),
                    requester=db_task.requester_id or "SYSTEM",
                    client_scope=c_scope,
                    project_scope=p_scope,
                    permission_scope=subtask_pkg.permission_scope,
                    resources=[],
                    verification_contract=subtask_pkg.verification_contract,
                    expected_output=subtask_pkg.expected_output,
                    approval_state=ApprovalState.NOT_REQUIRED,
                    execution_metadata=ExecutionMetadata(
                        correlation_id=subtask_pkg.workflow_id,
                        started_at=utc_now(),
                        completed_at=None,
                        retry_count=db_task.retry_count,
                        duration_ms=0.0
                    )
                )
                dependencies = getattr(subtask_pkg, "dependencies", [])
                max_allowed_retries = 3 
            else:
                task_package = TaskPackage(**context_data)
                task_package.status = TaskStatus.IN_PROGRESS
                if task_package.execution_metadata:
                    task_package.execution_metadata.retry_count = db_task.retry_count
                    task_package.execution_metadata.started_at = utc_now()
                dependencies = getattr(task_package, "dependencies", [])
                max_allowed_retries = getattr(task_package, "max_retries", 3)

            # Check Approval State for Standalone Tasks
            if not is_subtask and getattr(task_package, "approval_state", None) in [ApprovalState.REQUIRED, ApprovalState.PENDING]:
                db_task.status = DBTaskStatus.QUEUED
                await db.commit()
                await self._emit_event(EventType.TASK, "task.blocked", task_id, {"reason": "awaiting_approval"})
                return

            # Enforce DAG Dependencies
            dep_state = await self._check_dependencies(db, dependencies)
            
            if dep_state == "BLOCKED_PERMANENTLY":
                db_task.status = DBTaskStatus.FAILED
                db_task.failure_reason = "Workflow subtask failed because dependencies failed or were cancelled."
                await db.commit()
                await self._emit_event(EventType.TASK, "task.failed", task_id, {"error": "dependencies_failed"})
                await self._check_workflow_completion(db, db_task.workflow_id)
                return
            elif dep_state == "WAITING":
                db_task.status = DBTaskStatus.QUEUED
                await db.commit()
                asyncio.create_task(self._delayed_requeue(QueueItem(priority=db_task.priority or 5, timestamp=time.time(), task_id=task_id), delay=10.0))
                return

            db_task.status = DBTaskStatus.EXECUTING
            await db.commit()
            await self._emit_event(EventType.TASK, "task.executing", task_id, {"agent": getattr(task_package, "target_agent", "UNKNOWN")})

            # Execute
            try:
                result_package = await agent_runtime.execute(task_package)
            except ExecutionError as e:
                is_retryable = e.classification in [
                    ErrorClassification.TRANSIENT_PROVIDER, 
                    ErrorClassification.TIMEOUT, 
                    ErrorClassification.NETWORK_FAILURE
                ]
                
                if is_retryable and db_task.retry_count < max_allowed_retries:
                    db_task.retry_count += 1
                    db_task.status = DBTaskStatus.RETRYING
                    await db.commit()
                    
                    await self._emit_event(EventType.TASK, "task.retrying", task_id, {"attempt": db_task.retry_count, "reason": str(e.classification)})
                    asyncio.create_task(self._delayed_requeue(QueueItem(priority=db_task.priority or 5, timestamp=time.time(), task_id=task_id), delay=5.0 * db_task.retry_count))
                    return
                else:
                    db_task.status = DBTaskStatus.FAILED
                    db_task.failure_reason = str(e)
                    await db.commit()
                    
                    await self._emit_event(EventType.TASK, "task.failed", task_id, {"error": str(e), "classification": str(e.classification)})
                    if db_task.workflow_id:
                        await self._check_workflow_completion(db, db_task.workflow_id)
                    return
            except Exception as e:
                db_task.status = DBTaskStatus.FAILED
                db_task.failure_reason = f"Unhandled Execution Error: {str(e)}"
                await db.commit()
                
                await self._emit_event(EventType.TASK, "task.failed", task_id, {"error": db_task.failure_reason})
                if db_task.workflow_id:
                    await self._check_workflow_completion(db, db_task.workflow_id)
                return

            # Verification Handoff
            db_task.status = DBTaskStatus.VERIFYING
            await db.commit()
            await self._emit_event(EventType.TASK, "task.verifying", task_id, {})

            try:
                verified_package = await verification_engine.verify(task_package, result_package)
                
                verification_error = next(iter(verified_package.errors), None)

                # Canonical mapping: ResultStatus -> durable VerificationStatus.
                # ("COMPLETED" has no direct DBVerificationStatus member; paused states
                #  remain PENDING because verification was deferred, not failed.)
                _verif_status_map = {
                    ResultStatus.COMPLETED: DBVerificationStatus.VERIFIED,
                    ResultStatus.PARTIAL: DBVerificationStatus.PARTIAL,
                    ResultStatus.FAILED: DBVerificationStatus.FAILED,
                    ResultStatus.WAITING_APPROVAL: DBVerificationStatus.PENDING,
                    ResultStatus.WAITING_INPUT: DBVerificationStatus.PENDING,
                    ResultStatus.BLOCKED: DBVerificationStatus.PENDING,
                }
                _db_verif_status = _verif_status_map.get(verified_package.status, DBVerificationStatus.UNVERIFIABLE)

                db_verification = VerificationModel(
                    verification_id=uuid.uuid4().hex,
                    task_id=task_id,
                    verification_contract=task_package.verification_contract.model_dump() if getattr(task_package, "verification_contract", None) else {},
                    expected_result={"expected": getattr(task_package.verification_contract, "expected_outcome", "Unknown")},
                    verification_method=getattr(task_package.verification_contract, "method", "UNKNOWN"),
                    verifier="JARVIS_VERIFICATION_ENGINE",
                    verification_status=_db_verif_status,
                    confidence=1.0,
                    actual_observed_result=verified_package.findings,
                    failure_reason=verification_error,
                    verified_at=utc_now(),
                    created_at=utc_now(),
                    updated_at=utc_now()
                )
                db.add(db_verification)

            except Exception as e:
                db_task.status = DBTaskStatus.FAILED
                db_task.failure_reason = f"Verification engine error: {str(e)}"
                await db.commit()
                
                await self._emit_event(EventType.TASK, "task.failed", task_id, {"error": db_task.failure_reason})
                if db_task.workflow_id:
                    await self._check_workflow_completion(db, db_task.workflow_id)
                return

            # Final Persistence (paused lifecycle states must NEVER collapse into FAILED)
            if verified_package.status == ResultStatus.COMPLETED:
                db_task.status = DBTaskStatus.COMPLETED
            elif verified_package.status == ResultStatus.PARTIAL:
                db_task.status = DBTaskStatus.PARTIAL
            elif verified_package.status == ResultStatus.WAITING_APPROVAL:
                # Park at the approval boundary; resumed by the "approval.resolved" event.
                db_task.status = DBTaskStatus.APPROVAL
                db_task.updated_at = utc_now()
                await db.commit()
                await self._emit_event(EventType.TASK, "task.blocked", task_id, {"reason": "awaiting_approval"})
                if db_task.workflow_id:
                    await self._check_workflow_completion(db, db_task.workflow_id)
                return
            elif verified_package.status == ResultStatus.WAITING_INPUT:
                db_task.status = DBTaskStatus.WAITING_INPUT
            elif verified_package.status == ResultStatus.BLOCKED:
                db_task.status = DBTaskStatus.BLOCKED
            else:
                db_task.status = DBTaskStatus.FAILED
                db_task.failure_reason = f"Verification rejected outcome: {verified_package.status}"

            db_task.result_summary = verified_package.summary
            db_task.evidence = _json_safe(verified_package.evidence)
            db_task.actions_performed = _json_safe([a.model_dump() for a in verified_package.actions_performed])
            db_task.limitations = verified_package.limitations
            
            task_package.result = verified_package
            if getattr(task_package, "execution_metadata", None):
                task_package.execution_metadata.completed_at = utc_now()
                
            db_task.context_snapshot = task_package.model_dump(mode="json")
            db_task.updated_at = utc_now()
            
            await db.commit()
            
            status_val = db_task.status.value if hasattr(db_task.status, 'value') else str(db_task.status)
            await self._emit_event(EventType.TASK, f"task.{status_val.lower()}", task_id, {"summary": db_task.result_summary})

            if db_task.workflow_id:
                await self._check_workflow_completion(db, db_task.workflow_id)

    async def _check_workflow_completion(self, db: AsyncSession, workflow_id: str):
        """Monitors child tasks, unblocks newly-ready subtasks, and triggers synthesis when complete."""
        result = await db.execute(select(TaskModel).where(TaskModel.workflow_id == workflow_id))
        siblings = result.scalars().all()
        
        all_terminal = True
        for sib in siblings:
            if sib.task_id == workflow_id:
                continue
            if sib.status not in [DBTaskStatus.COMPLETED, DBTaskStatus.FAILED, DBTaskStatus.PARTIAL, DBTaskStatus.CANCELLED]:
                all_terminal = False
                break

        # Dispatch any PLANNED subtasks whose dependencies just became satisfied.
        # This realizes the ExecutionGroup graph: parallel groups fan out across
        # workers; sequential groups wait for their declared dependencies.
        await self._enqueue_ready_subtasks(db, workflow_id)

        if all_terminal:
            await self.queue.put(QueueItem(priority=0, timestamp=time.time(), task_id=workflow_id, is_workflow=True))

    async def _enqueue_ready_subtasks(self, db: AsyncSession, workflow_id: str):
        """Enqueues PLANNED subtasks of a workflow whose dependencies are all COMPLETED."""
        result = await db.execute(
            select(TaskModel).where(
                TaskModel.workflow_id == workflow_id,
                TaskModel.status == DBTaskStatus.PLANNED,
                TaskModel.task_id != workflow_id
            )
        )
        planned = result.scalars().all()
        if not planned:
            return

        for sub in planned:
            snapshot = dict(sub.context_snapshot or {})
            dep_ids = []
            for dep in snapshot.get("dependencies", []):
                dep_id = dep.get("task_id") if isinstance(dep, dict) else getattr(dep, "task_id", None)
                if dep_id:
                    dep_ids.append(dep_id)

            ready = True
            for dep_id in dep_ids:
                dep_task = await db.get(TaskModel, dep_id)
                if not dep_task or dep_task.status != DBTaskStatus.COMPLETED:
                    ready = False
                    break

            if ready:
                sub.status = DBTaskStatus.QUEUED
                sub.updated_at = utc_now()
                await db.commit()
                await self._emit_event(EventType.TASK, "task.queued", sub.task_id, {"workflow_id": workflow_id})
                await self.queue.put(QueueItem(priority=sub.priority or 5, timestamp=time.time(), task_id=sub.task_id))

    async def _execute_workflow_synthesis(self, workflow_id: str):
        """Hands completed workflow datasets back to the Brain for verified outcome synthesis."""
        # Local dynamic import to definitively break startup circular dependency
        from backend.core.brain import brain 
        
        async with worker_session() as db:
            result = await db.execute(select(TaskModel).where(TaskModel.workflow_id == workflow_id))
            all_tasks = result.scalars().all()
            
            parent_task = next((t for t in all_tasks if t.task_id == workflow_id), None)
            if not parent_task or parent_task.status in [DBTaskStatus.COMPLETED, DBTaskStatus.FAILED, DBTaskStatus.CANCELLED]:
                return

            # Defensive race guard: NEVER synthesize while any child is still
            # active. If we somehow got here early (e.g., a stale queue item),
            # defer silently — each subsequent child completion event will call
            # _check_workflow_completion, which re-enqueues synthesis when all
            # children have actually reached a terminal state.
            terminal_states = [DBTaskStatus.COMPLETED, DBTaskStatus.FAILED, DBTaskStatus.PARTIAL, DBTaskStatus.CANCELLED]
            active_children = [
                t for t in all_tasks
                if t.task_id != workflow_id and t.status not in terminal_states
            ]
            if active_children:
                logger.warning(
                    f"Workflow synthesis deferred for [{workflow_id}]: "
                    f"{len(active_children)} subtask(s) still active."
                )
                return

            results: List[ResultPackage] = []
            has_failures = False
            for sib in all_tasks:
                if sib.task_id == workflow_id: continue
                if sib.status in [DBTaskStatus.FAILED, DBTaskStatus.CANCELLED, DBTaskStatus.PARTIAL]:
                    has_failures = True
                    
                if sib.context_snapshot and "result" in sib.context_snapshot and sib.context_snapshot["result"]:
                    try:
                        results.append(ResultPackage(**sib.context_snapshot["result"]))
                    except Exception as e:
                        logger.error(f"Failed to parse result package for synthesis: {e}")
            
            try:
                workflow_data = WorkflowDefinition(**parent_task.context_snapshot)
                
                client_scope = None
                project_scope = None
                
                if workflow_data.subtasks and workflow_data.subtasks[0].permission_scope:
                    scope_val = workflow_data.subtasks[0].permission_scope.scope
                    if isinstance(scope_val, dict):
                        client_scope = scope_val.get("client")
                        project_scope = scope_val.get("project")

                await brain.synthesize_and_record_outcome(
                    db=db,
                    task_id=workflow_id,
                    results=results,
                    requester=workflow_data.requester,
                    client_scope=client_scope,
                    project_scope=project_scope
                )
                
                parent_task.status = DBTaskStatus.PARTIAL if has_failures else DBTaskStatus.COMPLETED
                parent_task.updated_at = utc_now()
                await db.commit()
                
                status_val = parent_task.status.value if hasattr(parent_task.status, 'value') else str(parent_task.status)
                await self._emit_event(EventType.WORKFLOW, f"workflow.{status_val.lower()}", workflow_id, {"subtasks_evaluated": len(results)})
                
            except Exception as e:
                logger.error(f"Workflow Synthesis failed for {workflow_id}: {e}", exc_info=True)
                parent_task.status = DBTaskStatus.FAILED
                parent_task.failure_reason = f"Workflow synthesis engine error: {str(e)}"
                parent_task.updated_at = utc_now()
                await db.commit()
                await self._emit_event(EventType.WORKFLOW, "workflow.failed", workflow_id, {"error": str(e)})

    async def _delayed_requeue(self, queue_item: QueueItem, delay: float):
        await asyncio.sleep(delay)
        await self.queue.put(queue_item)

task_queue = TaskQueue()
