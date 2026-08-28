"""
backend/observation/os_session_monitor.py
J.A.R.V.I.S. OS Session Boundary Monitor (JARVIS Master Spec §5, §11; Security Architecture §9).

Bridges the physical laptop state to the authoritative SessionManager:
  - Detects Windows workstation LOCK / UNLOCK transitions and drives
    session_manager.lock_session() / unlock_session().
  - On unlock, emits the canonical startup/unlock greeting event and speaks a brief
    system-status line through TTS when voice output is available.

Detection method: `OpenInputDesktop` — while the workstation is locked, the input
desktop belongs to Winlogon and cannot be opened by a user-session process.
This is a lightweight, dependency-free, polling-based approach suitable for a
personal-device deployment. Non-Windows platforms are detected but no-op.

Privacy boundary preserved: locking stops observation via SessionManager; this
monitor only reports the OS transition itself.
"""

import sys
import logging
import asyncio
from typing import Optional

from backend.observation.session_manager import session_manager
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType

logger = logging.getLogger("JARVIS.Observation.OSSessionMonitor")

def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _is_workstation_locked_windows() -> Optional[bool]:
    """
    Returns True if the workstation is locked, False if unlocked,
    None if detection failed (caller must not change state on None).
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        DESKTOP_READOBJECTS = 0x0001
        h_desktop = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if h_desktop:
            user32.CloseDesktop(h_desktop)
            return False  # Input desktop openable -> interactive session active -> unlocked
        # Access denied / no input desktop -> Winlogon owns it -> locked
        return True
    except Exception as e:
        logger.debug(f"Lock-state probe failed: {e}")
        return None


class OSSessionMonitor:
    """
    Polls the OS session state and synchronizes it with the authoritative SessionManager.
    """

    POLL_INTERVAL_SECONDS = 3.0

    def __init__(self):
        self.is_running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._last_known_locked: Optional[bool] = None

    async def start(self):
        """Starts the monitor loop (idempotent). No-ops on unsupported platforms."""
        if self.is_running:
            return

        if not sys.platform.startswith("win"):
            logger.info("OSSessionMonitor: platform does not expose lock-state probing; monitor idle.")
            return

        self.is_running = True
        self._loop_task = asyncio.create_task(self._monitor_loop())
        logger.info("OSSessionMonitor started (Windows lock/unlock boundary active).")

    async def stop(self):
        """Gracefully halts the monitor."""
        self.is_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        logger.info("OSSessionMonitor stopped.")

    async def _monitor_loop(self):
        while self.is_running:
            try:
                locked_now = await asyncio.to_thread(_is_workstation_locked_windows)

                if locked_now is not None and locked_now != self._last_known_locked:
                    await self._handle_transition(locked_now)
                    self._last_known_locked = locked_now

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"OS session monitor loop error: {e}")

            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

    async def _handle_transition(self, now_locked: bool):
        """Applies an OS lock/unlock transition to the authoritative SessionManager."""
        state = await session_manager.get_session_state()

        if now_locked and not state.get("is_locked"):
            logger.info("OS transition detected: WORKSTATION LOCKED.")
            await session_manager.lock_session(reason="OS workstation lock detected")

        elif not now_locked and state.get("is_locked"):
            logger.info("OS transition detected: WORKSTATION UNLOCKED.")
            await session_manager.unlock_session(reason="OS workstation unlock detected")
            await session_manager.set_user_presence(True)
            await self._emit_unlock_greeting()

    async def _emit_unlock_greeting(self):
        """
        Spec §5 behavior: greet the user with a brief system-status summary on unlock.
        Emits the canonical HUD event; attempts spoken delivery best-effort.
        """
        from datetime import datetime

        hour = datetime.now().hour
        if hour < 12:
            period = "morning"
        elif hour < 17:
            period = "afternoon"
        else:
            period = "evening"

        greeting = f"Good {period}, sir. All systems are operating at peak efficiency."

        await event_bus.publish(JarvisEvent(
            event_type=EventType.SYSTEM,
            topic="system.unlock_greeting",
            timestamp=utc_now(),
            correlation_id="unlock_greeting",
            task_id="SESSION_LIFECYCLE",
            source="OSSessionMonitor",
            payload={"message": greeting}
        ))

        # Best-effort spoken greeting; never blocks or crashes the monitor.
        try:
            from backend.tools.voice.text_to_speech import text_to_speech_engine
            await text_to_speech_engine.speak(text=greeting, allow_when_locked=False)
        except Exception as tts_err:
            logger.debug(f"Spoken greeting unavailable: {tts_err}")


os_session_monitor = OSSessionMonitor()