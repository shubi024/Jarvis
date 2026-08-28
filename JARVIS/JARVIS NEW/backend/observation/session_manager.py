"""
backend/observation/session_manager.py
J.A.R.V.I.S. Central Session State Manager.
Authoritative owner of session lifecycle, locked/unlocked state, user presence, 
wake-session state, voice listening eligibility, and observation eligibility.
Enforces safe startup defaults, complete state restoration, database durability,
and Emergency Stop overrides.
"""

import asyncio
import logging
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import worker_session
from backend.infrastructure.models import AppConfigModel
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.core.emergency_stop import emergency_stop
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.SessionManager")

STATE_FILE_PATH = os.getenv("JARVIS_SESSION_STATE_FILE", ".jarvis_session_state.json")
DB_CONFIG_KEY = "jarvis_session_state_canonical"

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class SessionManager:
    """
    Authoritative Session State Manager for J.A.R.V.I.S.
    Maintains clean state transitions for screen locks, user presence, wake words, 
    voice listening, and observation eligibility with durable database backing and safe startup defaults.
    """

    def __init__(self):
        # 1. Safe startup default: Assume locked & inactive until explicitly established
        self._is_locked: bool = True
        self._is_active: bool = False
        self._is_wake_session: bool = False
        self._is_voice_listening: bool = False
        self._startup_greeting_triggered: bool = False
        self._last_user_activity: datetime = utc_now()
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Initializes session manager, loading durable database state with file fallback."""
        await self._load_persisted_state()
        logger.info(f"SessionManager initialized. Locked: {self._is_locked}, Active: {self._is_active}")

    async def _emit_session_event(self, topic: str, payload: Dict[str, Any]):
        """Publishes session state change telemetry."""
        event = JarvisEvent(
            event_type=EventType.SYSTEM,
            topic=topic,
            timestamp=utc_now(),
            correlation_id=f"sess_{int(utc_now().timestamp())}",
            task_id="SESSION_LIFECYCLE",
            source="SessionManager",
            payload=payload
        )
        await event_bus.publish(event)

    async def _persist_state(self):
        """Persists authoritative session state to durable database storage with local JSON fail-safe fallback."""
        state_doc = {
            "is_locked": self._is_locked,
            "is_active": self._is_active,
            "is_wake_session": self._is_wake_session,
            "is_voice_listening": self._is_voice_listening,
            "startup_greeting_triggered": self._startup_greeting_triggered,
            "last_user_activity": self._last_user_activity.isoformat(),
            "updated_at": utc_now().isoformat()
        }
        
        # 1. Durable Canonical Database Persistence
        try:
            async with worker_session() as db:
                config_row = await db.get(AppConfigModel, DB_CONFIG_KEY)
                if not config_row:
                    config_row = AppConfigModel(
                        config_key=DB_CONFIG_KEY,
                        config_value=state_doc,
                        updated_at=utc_now()
                    )
                    db.add(config_row)
                else:
                    config_row.config_value = state_doc
                    config_row.updated_at = utc_now()
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to persist session state to database: {e}")

        # 2. Local Fallback File Lock
        try:
            with open(STATE_FILE_PATH, "w") as f:
                json.dump(state_doc, f)
        except Exception as e:
            logger.error(f"Failed to persist session state to local file fallback: {e}")

    async def _load_persisted_state(self):
        """Recovers all authoritative session flags from database storage, failing over to local file if necessary."""
        doc = None
        
        # 1. Try loading from database first
        try:
            async with worker_session() as db:
                config_row = await db.get(AppConfigModel, DB_CONFIG_KEY)
                if config_row and isinstance(config_row.config_value, dict):
                    doc = config_row.config_value
        except Exception as e:
            logger.warning(f"Could not load session state from database, checking local fallback: {e}")

        # 2. Fall back to local file if database doc is missing
        if not doc and os.path.exists(STATE_FILE_PATH):
            try:
                with open(STATE_FILE_PATH, "r") as f:
                    doc = json.load(f)
            except Exception as e:
                logger.error(f"Failed to parse local session state fallback file: {e}")

        # 3. Apply recovered state across ALL authoritative fields
        if doc:
            self._is_locked = doc.get("is_locked", True)
            self._is_active = doc.get("is_active", False)
            self._is_wake_session = doc.get("is_wake_session", False)
            self._is_voice_listening = doc.get("is_voice_listening", False)
            # startup_greeting_triggered is deliberately NOT restored: the greeting is
            # defined as "once per BOOT CYCLE", so it always resets to False here and
            # a fresh greeting fires after every reboot/restart.
            self._startup_greeting_triggered = False
            activity_str = doc.get("last_user_activity")
            if activity_str:
                try:
                    self._last_user_activity = datetime.fromisoformat(activity_str)
                except Exception:
                    pass

    async def get_session_state(self) -> Dict[str, Any]:
        """Authoritative status API for current session state."""
        emergency_active = False
        try:
            emergency_stop.assert_system_running()
        except ExecutionError:
            emergency_active = True

        return {
            "is_locked": self._is_locked or emergency_active,
            "is_active": self._is_active and not emergency_active,
            "is_wake_session": self._is_wake_session and not emergency_active,
            "is_voice_listening": self._is_voice_listening and not emergency_active,
            "startup_greeting_triggered": self._startup_greeting_triggered,
            "emergency_stop_active": emergency_active,
            "last_user_activity": self._last_user_activity.isoformat()
        }

    async def lock_session(self, reason: str = "User lock"):
        """Transitions session to locked state, disabling observations and listening."""
        async with self._lock:
            if self._is_locked:
                return
            self._is_locked = True
            self._is_voice_listening = False
            self._is_wake_session = False
            await self._persist_state()
            
        logger.info(f"Session LOCKED. Reason: {reason}")
        await event_bus.publish(JarvisEvent(
            event_type=EventType.SYSTEM,
            topic="session.locked",
            timestamp=utc_now(),
            correlation_id="sess_lock",
            task_id="SESSION_LIFECYCLE",
            source="SessionManager",
            payload={"reason": reason}
        ))
        await self._emit_session_event("session.state_changed", await self.get_session_state())

    async def unlock_session(self, reason: str = "User unlock"):
        """Transitions session to unlocked state, re-enabling eligibility."""
        async with self._lock:
            try:
                emergency_stop.assert_system_running()
            except ExecutionError:
                logger.warning("Unlock rejected: Global Emergency Stop is active.")
                return

            if not self._is_locked:
                return
            self._is_locked = False
            self._last_user_activity = utc_now()
            await self._persist_state()

        logger.info(f"Session UNLOCKED. Reason: {reason}")
        await event_bus.publish(JarvisEvent(
            event_type=EventType.SYSTEM,
            topic="session.unlocked",
            timestamp=utc_now(),
            correlation_id="sess_unlock",
            task_id="SESSION_LIFECYCLE",
            source="SessionManager",
            payload={"reason": reason}
        ))
        await self._emit_session_event("session.state_changed", await self.get_session_state())

    async def set_user_presence(self, is_active: bool):
        """Updates user presence and active-use tracking."""
        async with self._lock:
            if self._is_active == is_active:
                return
            self._is_active = is_active
            if is_active:
                self._last_user_activity = utc_now()
            await self._persist_state()

        await self._emit_session_event("session.presence_changed", {"is_active": is_active})

    async def set_wake_session(self, is_wake: bool):
        """Sets wake-session state."""
        async with self._lock:
            if self._is_wake_session == is_wake:
                return
            self._is_wake_session = is_wake
            await self._persist_state()
        await self._emit_session_event("session.wake_state_changed", {"is_wake_session": is_wake})

    async def set_voice_listening(self, is_listening: bool):
        """Controls voice listening state, enforcing locked constraints."""
        async with self._lock:
            try:
                emergency_stop.assert_system_running()
            except ExecutionError:
                is_listening = False

            if self._is_locked:
                is_listening = False

            if self._is_voice_listening == is_listening:
                return
            self._is_voice_listening = is_listening
            await self._persist_state()

        await self._emit_session_event("session.voice_listening_changed", {"is_voice_listening": is_listening})

    async def check_startup_greeting(self) -> bool:
        """Triggers and ensures startup greeting occurs exactly once per boot cycle."""
        async with self._lock:
            if self._startup_greeting_triggered:
                return False
            self._startup_greeting_triggered = True
            await self._persist_state()
        
        await self._emit_session_event("session.startup_greeting_triggered", {})
        return True

    async def is_observation_eligible(self) -> bool:
        """Determines if ObservationManager is allowed to run based on session and safety state."""
        state = await self.get_session_state()
        if state["emergency_stop_active"]:
            return False
        if state["is_locked"]:
            return False
        if not state["is_active"]:
            return False
        return True

    async def is_voice_listening_eligible(self) -> bool:
        """Determines if voice listening primitives are permitted."""
        state = await self.get_session_state()
        if state["emergency_stop_active"] or state["is_locked"]:
            return False
        return self._is_voice_listening


session_manager = SessionManager()