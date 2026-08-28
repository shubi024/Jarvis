"""
backend/core/briefing_service.py
J.A.R.V.I.S. Weekly Chief-of-Staff Briefing (JARVIS Master Spec §17).

Provides the concise periodic briefing covering: priorities, unfinished tasks,
patterns, opportunities, problems, useful observations, and things worth
reconsidering.

Design:
  - A weekly asyncio loop anchored to a durable last-briefing timestamp
    (AppConfigModel) so restarts never skip or duplicate a cycle.
  - Evidence gathered strictly from real system state: open tasks, failures,
    pending approvals, verified learnings and observation suggestions.
  - The composed briefing is published as `briefing.weekly` telemetry for the HUD
    and stored as an episodic memory record.
  - Read-only by design: briefings recommend; they never execute.
"""

import uuid
import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from sqlalchemy import select

from backend.infrastructure.database import worker_session
from backend.infrastructure.models import TaskModel, TaskStatus as DBTaskStatus, ApprovalModel, ApprovalStatus as DBApprovalStatus
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.infrastructure.api_engine import api_engine
from backend.memory.memory_manager import memory_manager, MemoryCategory

logger = logging.getLogger("JARVIS.Core.BriefingService")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

BRIEFING_CONFIG_KEY = "jarvis_last_weekly_briefing"
DEFAULT_INTERVAL_DAYS = 7

BRIEFING_PROMPT = """You are J.A.R.V.I.S., delivering the user's concise weekly Chief-of-Staff briefing.
Using ONLY the verified evidence provided, compose a calm, professional, structured briefing.
Cover: current priorities, unfinished work, notable patterns, opportunities, problems/risks,
useful observations, and anything worth reconsidering. Be specific and evidence-backed.
Never fabricate facts that are not present in the evidence.
Respond strictly in JSON:
{
    "briefing_text": "The full briefing text addressed to 'Sir'.",
    "top_priorities": ["Up to 5 short priority strings"],
    "risks": ["Notable risks or problems worth attention"]
}
"""


class BriefingService:
    """
    Weekly Chief-of-Staff briefing generator with durable anchoring.
    """

    def __init__(self, interval_days: int = DEFAULT_INTERVAL_DAYS):
        self.interval_days = interval_days
        self.is_running: bool = False
        self._loop_task: Optional[asyncio.Task] = None

    async def start(self):
        """Starts the weekly briefing loop (idempotent)."""
        if not self.is_running:
            self.is_running = True
            self._loop_task = asyncio.create_task(self._briefing_loop())
            logger.info(f"BriefingService started (interval: {self.interval_days} days).")

    async def stop(self):
        """Gracefully halts the briefing loop."""
        self.is_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        logger.info("BriefingService stopped.")

    async def _briefing_loop(self):
        while self.is_running:
            try:
                due, last_run = await self._is_briefing_due()
                if due:
                    logger.info("Weekly briefing is due. Generating...")
                    await self.generate_weekly_briefing()
                else:
                    next_in = max(0.0, (last_run + timedelta(days=self.interval_days) - utc_now()).total_seconds())
                    # Sleep in bounded slices so stop() stays responsive.
                    await asyncio.sleep(min(next_in, 3600.0))
                    continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Briefing loop error: {e}", exc_info=True)
                await asyncio.sleep(600.0)

    async def _is_briefing_due(self) -> tuple[bool, Optional[datetime]]:
        """Checks the durable anchor to determine whether a briefing is overdue."""
        async with worker_session() as db:
            from backend.infrastructure.models import AppConfigModel
            row = await db.get(AppConfigModel, BRIEFING_CONFIG_KEY)

        if not row or not isinstance(row.config_value, dict) or not row.config_value.get("last_briefing_at"):
            return True, None

        try:
            last = datetime.fromisoformat(row.config_value["last_briefing_at"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            return (utc_now() - last).total_seconds() >= self.interval_days * 86400.0, last
        except Exception as e:
            logger.warning(f"Could not parse last briefing anchor: {e}")
            return True, None

    async def _mark_briefed(self):
        """Persists the new briefing anchor."""
        async with worker_session() as db:
            from backend.infrastructure.models import AppConfigModel
            row = await db.get(AppConfigModel, BRIEFING_CONFIG_KEY)
            doc = {"last_briefing_at": utc_now().isoformat()}
            if not row:
                row = AppConfigModel(config_key=BRIEFING_CONFIG_KEY, config_value=doc, updated_at=utc_now())
                db.add(row)
            else:
                row.config_value = doc
                row.updated_at = utc_now()
            await db.commit()

    async def gather_evidence(self) -> Dict[str, Any]:
        """Collects real system-state evidence for the briefing."""
        evidence: Dict[str, Any] = {}

        async with worker_session() as db:
            # Open / unfinished tasks
            result = await db.execute(
                select(TaskModel).where(
                    TaskModel.status.in_([
                        DBTaskStatus.QUEUED, DBTaskStatus.IN_PROGRESS, DBTaskStatus.EXECUTING,
                        DBTaskStatus.WAITING_INPUT, DBTaskStatus.APPROVAL, DBTaskStatus.BLOCKED,
                        DBTaskStatus.RETRYING, DBTaskStatus.PLANNED,
                    ])
                ).order_by(TaskModel.priority.asc(), TaskModel.created_at.desc()).limit(25)
            )
            open_tasks = result.scalars().all()
            evidence["open_tasks"] = [
                {
                    "task_id": t.task_id,
                    "intent": t.intent,
                    "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                    "priority": t.priority,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in open_tasks
            ]

            # Recent failures (last 7 days)
            week_ago = utc_now() - timedelta(days=7)
            result = await db.execute(
                select(TaskModel).where(
                    TaskModel.status == DBTaskStatus.FAILED,
                    TaskModel.updated_at >= week_ago
                ).limit(15)
            )
            failed_tasks = result.scalars().all()
            evidence["recent_failures"] = [
                {
                    "task_id": t.task_id,
                    "intent": t.intent,
                    "failure_reason": (t.failure_reason or "")[:300],
                }
                for t in failed_tasks
            ]

            # Pending approvals
            result = await db.execute(select(ApprovalModel).where(ApprovalModel.status == DBApprovalStatus.PENDING))
            pending = result.scalars().all()
            evidence["pending_approvals"] = [
                {
                    "approval_id": a.approval_id,
                    "intent": a.intent,
                    "risk_level": a.risk_level,
                }
                for a in pending
            ]

        # Recent verified learnings & observation suggestions (memory vault)
        async with worker_session() as db:
            from backend.memory.memory_manager import MemoryModel, MemoryStatus
            result = await db.execute(
                select(MemoryModel).where(
                    MemoryModel.status == MemoryStatus.ACTIVE,
                    MemoryModel.category.in_([MemoryCategory.EPISODIC, MemoryCategory.PROCEDURAL]),
                ).order_by(MemoryModel.created_at.desc()).limit(15)
            )
            recent_memories = result.scalars().all()
            evidence["recent_learnings_and_observations"] = [
                {
                    "category": m.category,
                    "content": m.content[:400],
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in recent_memories
            ]

        return evidence

    async def generate_weekly_briefing(self) -> Dict[str, Any]:
        """
        Generates, publishes, and stores the weekly Chief-of-Staff briefing.
        Returns the composed payload for callers/tests.
        """
        evidence = await self.gather_evidence()

        baseline = {
            "briefing_text": (
                f"Sir, your weekly briefing is ready. "
                f"{len(evidence.get('open_tasks', []))} task(s) remain open, "
                f"{len(evidence.get('recent_failures', []))} execution(s) failed this week, and "
                f"{len(evidence.get('pending_approvals', []))} approval(s) await your decision."
            ),
            "top_priorities": [t["intent"] for t in evidence.get("open_tasks", [])[:5]],
            "risks": [f["failure_reason"][:120] for f in evidence.get("recent_failures", [])[:3]],
        }

        final_payload = baseline
        try:
            llm_res = await api_engine.call_llm(
                prompt=f"Weekly Briefing Evidence:\n{json.dumps(evidence, indent=2)}",
                system_prompt=BRIEFING_PROMPT,
                temperature=0.3,
            )
            raw = llm_res.get("response", "").strip()
            if raw.startswith("```json"): raw = raw[7:]
            elif raw.startswith("```"): raw = raw[3:]
            if raw.endswith("```"): raw = raw[:-3]
            parsed = json.loads(raw.strip())
            if parsed.get("briefing_text"):
                final_payload = parsed
        except Exception as e:
            logger.warning(f"LLM briefing composition unavailable; using evidence-based baseline: {e}")

        # Publish to HUD telemetry
        await event_bus.publish(JarvisEvent(
            event_type=EventType.SYSTEM,
            topic="briefing.weekly",
            timestamp=utc_now(),
            correlation_id=f"brief_{uuid.uuid4().hex[:8]}",
            task_id="WEEKLY_BRIEFING",
            source="BriefingService",
            payload={
                "briefing_text": final_payload.get("briefing_text", ""),
                "top_priorities": final_payload.get("top_priorities", []),
                "risks": final_payload.get("risks", []),
            }
        ))

        # Store as episodic memory for continuity
        try:
            async with worker_session() as db:
                await memory_manager.create_structured_memory(
                    db=db,
                    memory_id=f"mem_brief_{uuid.uuid4().hex[:8]}",
                    content=f"Weekly Briefing: {final_payload.get('briefing_text', '')}",
                    category=MemoryCategory.EPISODIC,
                    user_id="SYSTEM",
                    confidence=1.0,
                    source_task_id="WEEKLY_BRIEFING",
                    source_provenance="JARVIS BriefingService",
                )
        except Exception as mem_err:
            logger.error(f"Failed to store briefing memory: {mem_err}")

        await self._mark_briefed()
        logger.info("Weekly Chief-of-Staff briefing generated and published.")
        return final_payload


briefing_service = BriefingService()