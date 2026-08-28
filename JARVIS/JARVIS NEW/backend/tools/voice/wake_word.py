"""
backend/tools/voice/wake_word.py
J.A.R.V.I.S. Wake-Word and Voice Listening Lifecycle Tool.
Controls background audio detection, wake states, continuous conversation loops, 
timeout handling, emergency stop/lock guardrails, and SessionManager authority.
"""

import logging
import asyncio
import re
from difflib import SequenceMatcher
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from backend.tools.base_tool import BaseTool
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.infrastructure.database import worker_session
from backend.observation.session_manager import session_manager
from backend.core.emergency_stop import emergency_stop
from backend.core.brain import brain
from backend.tools.voice.text_to_speech import text_to_speech_engine

logger = logging.getLogger("JARVIS.Tools.WakeWord")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class WakeWordInput(BaseModel):
    action: str = Field(description="Action to perform: 'start', 'stop', or 'status'.")

class WakeWordTool(BaseTool):
    name = "wake_word_control"
    description = "Controls the background wake-word listener and continuous voice interaction session."
    category = "voice"
    args_schema = WakeWordInput
    risk_level = "low"
    requires_approval = False

    def __init__(self):
        super().__init__()
        self._listening_task: Optional[asyncio.Task] = None
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._unsubscribe_token: Optional[str] = None
        # Queue of (task_id, summary) arrivals from task.completed events, so the
        # voice session can speak the FINAL result of a background task.
        self._task_completion_queue: asyncio.Queue = asyncio.Queue()
        self._task_unsub_token: Optional[str] = None

    async def _emit_voice_event(self, topic: str, payload: Dict[str, Any]):
        event = JarvisEvent(
            event_type=EventType.VOICE,
            topic=topic,
            timestamp=utc_now(),
            correlation_id="voice_session",
            task_id="VOICE_LISTENING",
            source="WakeWordTool",
            payload=payload
        )
        await event_bus.publish(event)

    async def _handle_raw_audio_input(self, event: JarvisEvent):
        """Internal subscriber handler routing raw audio/utterance frames to the queue."""
        if event.payload:
            await self._audio_queue.put(event.payload)

    async def _handle_task_completed(self, event: JarvisEvent):
        """Routes task.completed events (result summaries) to the completion queue so the
        voice session can speak the FINAL outcome of a background task, not just the ack."""
        try:
            if not event.payload:
                return
            tid = event.task_id or event.correlation_id or ""
            summary = event.payload.get("summary") or event.payload.get("result_summary") or ""
            if tid and summary:
                await self._task_completion_queue.put((tid, summary))
        except Exception as e:
            logger.debug(f"Task completion event handler error: {e}")

    async def _run(self, action: str) -> str:
        action = action.lower()
        
        try:
            emergency_stop.assert_system_running()
        except Exception as e:
            raise RuntimeError(f"Wake word control rejected: Global Emergency Stop is active. {str(e)}")

        try:
            session_state = await session_manager.get_session_state()

            if action == "start":
                if session_state.get("is_locked"):
                    return "Wake word activation denied: System is currently locked."

                if session_state.get("is_voice_listening"):
                    return "Wake word detection is already active and listening."

                # Authoritative state transition via SessionManager (Sole Source of Truth)
                await session_manager.set_wake_session(False) # Starts in standby waiting for wake word
                await session_manager.set_voice_listening(True)

                # Subscribe to incoming audio stream events
                if not self._unsubscribe_token:
                    self._unsubscribe_token = await event_bus.subscribe("system.audio.raw_utterance", self._handle_raw_audio_input)
                # Subscribe to task completion events so tool-task results can be spoken.
                if not self._task_unsub_token:
                    self._task_unsub_token = await event_bus.subscribe("task.completed", self._handle_task_completed)

                await event_bus.publish(JarvisEvent(
                    event_type=EventType.VOICE,
                    topic="system.audio.wakeword",
                    timestamp=utc_now(),
                    correlation_id="wakeword_start",
                    task_id="WAKE_WORD_SYSTEM",
                    source="WakeWordTool",
                    payload={"command": "start"}
                ))
                
                if not self._listening_task or self._listening_task.done():
                    self._listening_task = asyncio.create_task(self._continuous_conversation_loop())

                logger.info("Wake word listener control: ACTIVATED.")
                await self._emit_voice_event("voice.listening_started", {"status": "listening"})
                return "Wake word detection activated. Listening for 'JARVIS' or 'Wake up JARVIS'."
                
            elif action == "stop":
                await session_manager.set_wake_session(False)
                await session_manager.set_voice_listening(False)
                
                if self._unsubscribe_token:
                    await event_bus.unsubscribe("system.audio.raw_utterance", self._handle_raw_audio_input)
                    self._unsubscribe_token = None

                if self._task_unsub_token:
                    await event_bus.unsubscribe("task.completed", self._handle_task_completed)
                    self._task_unsub_token = None

                await event_bus.publish(JarvisEvent(
                    event_type=EventType.VOICE,
                    topic="system.audio.wakeword",
                    timestamp=utc_now(),
                    correlation_id="wakeword_stop",
                    task_id="WAKE_WORD_SYSTEM",
                    source="WakeWordTool",
                    payload={"command": "stop"}
                ))
                
                if self._listening_task and not self._listening_task.done():
                    self._listening_task.cancel()
                    
                logger.info("Wake word listener control: DEACTIVATED.")
                await self._emit_voice_event("voice.listening_stopped", {"status": "sleeping"})
                return "Wake word detection deactivated. System is deaf to background audio."
                
            elif action == "status":
                is_listening = session_state.get("is_voice_listening", False)
                is_wake = session_state.get("is_wake_session", False)
                
                if is_wake:
                    status_str = "active_wake_session"
                elif is_listening:
                    status_str = "listening_for_wakeword"
                else:
                    status_str = "sleeping"
                    
                logger.info(f"Wake word listener control: STATUS CHECK ({status_str}).")
                return f"Wake word detection is currently in state: {status_str.upper()}."
                
            else:
                raise ValueError(f"Invalid action '{action}'. Permitted values are 'start', 'stop', or 'status'.")
                
        except Exception as e:
            if isinstance(e, RuntimeError): 
                raise e
            raise RuntimeError(f"Wake word control failed: {str(e)}")

    WAKE_WORD = "jarvis"
    WAKE_MATCH_RATIO = 0.7
    # Time the continuous voice session stays open without new audio before
    # returning to wake standby. Was 60s — too eager for normal pause-and-think.
    INACTIVITY_TIMEOUT_SECONDS = 120.0

    # Single-word conversational fillers / interjections that are not real commands.
    # These would otherwise be pushed into the Brain and trigger pointless planning.
    FILLER_WORDS = {
        "so", "ok", "okay", "k", "yeah", "yep", "yes", "no", "nope", "hmm", "hm",
        "um", "uh", "ah", "hey", "hi", "hello", "the", "a", "an", "and", "or", "but",
        "well", "sir", "thanks", "thank", "please", "go", "okay", "jarvis", "wake",
        "wait", "hold", "right", "sure", "yup",
    }

    @classmethod
    def _matches_wake_word(cls, transcript_lower: str) -> bool:
        """
        Fuzzy wake-word matching tolerant of common STT misrecognitions
        ('jardis', 'jervis', etc.). Exact phrase matches still short-circuit.
        """
        if not transcript_lower:
            return False
        if cls.WAKE_WORD in transcript_lower or "wake up" in transcript_lower:
            return True
        for token in re.findall(r"[a-z']+", transcript_lower):
            if len(token) < 4:
                continue
            if SequenceMatcher(None, token, cls.WAKE_WORD).ratio() >= cls.WAKE_MATCH_RATIO:
                return True
        return False

    @classmethod
    def _strip_wake_phrase(cls, transcript: str) -> str:
        """
        Removes the wake-word portion from a transcript, returning the remaining
        command text ('' when the utterance was only the wake word).
        """
        cleaned = transcript.strip()
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        start = 0
        wake_match = re.search(r"\bwake\s+up\b", lowered)
        if wake_match:
            start = wake_match.end()
        else:
            tokens = lowered.split()
            for i, token in enumerate(tokens):
                bare = token.strip("',.!?")
                if bare and len(bare) >= 4 and SequenceMatcher(None, bare, cls.WAKE_WORD).ratio() >= cls.WAKE_MATCH_RATIO:
                    # Drop the wake token (plus nothing else — leading fillers are rare)
                    start = len(" ".join(tokens[: i + 1]))
                    break
        return cleaned[start:].strip(" ,.!:;?")

    async def _detect_wake_word_from_stream(self) -> Optional[str]:
        """
        Consumes real incoming audio frames from the audio queue and matches
        against canonical wake triggers ('jarvis' or 'wake up jarvis').

        Returns the FULL matched transcript (not just a boolean) so that a wake
        word spoken together with the command ("Jarvis, what's the time") is not
        silently discarded — the caller strips the wake phrase and dispatches
        the remainder directly. Returns None when nothing matched.
        """
        try:
            while await session_manager.is_voice_listening_eligible():
                try:
                    # Non-blocking or short timeout pull from the active stream queue
                    frame = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                    transcript = str(frame.get("transcript", "")).strip()
                    if transcript and self._matches_wake_word(transcript.lower()):
                        return transcript
                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            logger.error(f"Error reading wake word stream: {e}")
        return None

    @classmethod
    def _is_filler(cls, transcript: str) -> bool:
        """True for pure filler/interjection utterances that should not be dispatched."""
        stripped = (transcript or "").strip(" .,!?:;'\"")
        if not stripped:
            return True
        words = stripped.lower().split()
        if len(words) <= 2 and all(w.strip(".,!?'\"") in cls.FILLER_WORDS for w in words):
            return True
        # Very short single words that are clearly not commands (e.g. stray "so").
        if len(words) == 1 and len(words[0]) <= 4:
            return True
        return False

    async def _poll_next_utterance(self) -> Optional[str]:
        """
        Pulls subsequent command text from the active audio stream after wake confirmation,
        maintaining continuous conversation mode without requiring the wake word again.
        """
        try:
            frame = await asyncio.wait_for(self._audio_queue.get(), timeout=4.0)
            transcript = str(frame.get("transcript", "")).strip()
            # If the user (or an echo) only repeated the bare wake word, keep
            # waiting for the actual command instead of dispatching it.
            if transcript and self._matches_wake_word(transcript.lower()) and len(transcript.split()) <= 2:
                return None
            if self._is_filler(transcript):
                return None
            return transcript
        except asyncio.TimeoutError:
            return None

    async def _continuous_conversation_loop(self):
        """
        Manages the two-phase lifecycle using SessionManager as authoritative state owner: 
        1. Wait for real Wake Word stream trigger.
        2. Enter continuous listening mode until timeout or sleep command.
        """
        inactivity_timeout_seconds = self.INACTIVITY_TIMEOUT_SECONDS
        
        while await session_manager.is_voice_listening_eligible():
            try:
                # Phase 1: Standby for real incoming wake-word trigger from stream
                wake_transcript = await self._detect_wake_word_from_stream()
                if wake_transcript is None:
                    if not await session_manager.is_voice_listening_eligible():
                        break
                    continue

                await self._emit_voice_event("voice.wake_detected", {"trigger": wake_transcript})
                await session_manager.set_wake_session(True)

                # The utterance usually carries the wake word AND the command together
                # ("Jarvis, what's the time"). Strip the wake phrase and dispatch the
                # remainder directly instead of discarding it and forcing a repeat.
                pending_command = self._strip_wake_phrase(wake_transcript)

                # Phase 2: Continuous Session Loop after wake confirmation
                idle_start = utc_now()
                while await session_manager.is_voice_listening_eligible():
                    state = await session_manager.get_session_state()
                    if not state.get("is_wake_session"):
                        break

                    # Check inactivity timeout
                    if (utc_now() - idle_start).total_seconds() > inactivity_timeout_seconds:
                        logger.info("Voice session inactivity timeout reached. Returning to wake standby.")
                        await self._emit_voice_event("voice.session_timeout", {})
                        await session_manager.set_wake_session(False)
                        break

                    if pending_command:
                        user_utterance = pending_command
                        pending_command = None
                    else:
                        user_utterance = await self._poll_next_utterance()
                    if self._is_filler(user_utterance):
                        await asyncio.sleep(0.1)
                        continue
                    if not user_utterance or not user_utterance.strip():
                        await asyncio.sleep(0.1)
                        continue

                    # Reset idle timer on valid user audio input
                    idle_start = utc_now()
                    utterance_lower = user_utterance.strip().lower()

                    if any(phrase in utterance_lower for phrase in ("stop listening", "go to sleep", "shut down", "go to bed", "sleep")):
                        logger.info("Voice session termination command received.")
                        await self._run("stop")
                        break

                    await self._emit_voice_event("voice.command_received", {"transcript": user_utterance})

                    # Dispatch to Brain Orchestrator using authoritative worker session
                    async with worker_session() as db:
                        brain_response = await brain.process_command(
                            db=db,
                            user_text=user_utterance,
                            requester="VoiceUser",
                            session_id="voice_active_session"
                        )

                    response_text = re.sub(
                        r"\s*\[Task queued: [a-zA-Z0-9_]+\]\s*$", "",
                        brain_response.get("response", "Command processed.")
                    )
                    await self._emit_voice_event("voice.response_dispatched", {"response": response_text})

                    # Speak the response aloud (spec §6: continuous voice session).
                    # Failures are logged and emitted but never break the session loop.
                    try:
                        await text_to_speech_engine.speak(text=response_text)
                    except Exception as tts_err:
                        logger.warning(f"Voice response playback failed: {tts_err}")
                        await self._emit_voice_event("voice.speak_failed", {"error": str(tts_err)})

                    # Speak the FINAL result for background/tool tasks. process_command
                    # returns immediately with "queued"; the real outcome arrives on the
                    # task.completed event. Listening for it gives the user a completion
                    # confirmation instead of silence after the acknowledgment.
                    dispatched_task_id = brain_response.get("task_id")
                    if dispatched_task_id:
                        try:
                            while True:
                                done_id, done_summary = await asyncio.wait_for(
                                    self._task_completion_queue.get(), timeout=25.0
                                )
                                if done_id != dispatched_task_id:
                                    continue
                                if done_summary and done_summary != response_text:
                                    try:
                                        await text_to_speech_engine.speak(text=done_summary)
                                    except Exception as tts_err2:
                                        logger.warning(f"Task completion voice playback failed: {tts_err2}")
                                break
                        except asyncio.TimeoutError:
                            logger.debug("Timed out waiting for task completion voice confirmation.")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in continuous conversation loop: {e}", exc_info=True)
                await asyncio.sleep(5.0)