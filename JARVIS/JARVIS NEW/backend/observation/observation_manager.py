"""
backend/observation/observation_manager.py
J.A.R.V.I.S. Continuous Observation Subsystem.
Maintains situational awareness via configured screen/system capture and analysis.
Enforces secure canonical execution paths via AgentRuntime, explicit data sanitization/redaction, 
authoritative SessionManager state ownership, resource controls, and pattern-based suggestion generation 
without autonomous action execution.
"""

import asyncio
import logging
import os
import uuid
import json
import re
import time
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import worker_session
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.infrastructure.api_engine import api_engine
from backend.memory.memory_manager import memory_manager, MemoryCategory
from backend.security.security_manager import security_manager
from backend.core.emergency_stop import emergency_stop
from backend.observation.session_manager import session_manager
from backend.core.agent_runtime import agent_runtime
from backend.core.task_contracts import TaskPackage, PermissionScope, ApprovalState, VerificationContract, ExpectedOutput, ExecutionMetadata
from backend.core.execution_errors import ExecutionError, ErrorClassification
from backend.core.json_utils import extract_json_object, strip_code_fences

logger = logging.getLogger("JARVIS.ObservationManager")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class ObservationManager:
    """
    Long-running continuous awareness subsystem for J.A.R.V.I.S.
    Analyzes system state and screen context through the canonical AgentRuntime execution path
    and authoritative SessionManager ownership, detecting patterns to generate actionable suggestions 
    while enforcing strict privacy and sanitization boundaries.
    """

    PATTERN_ANALYSIS_PROMPT = """You are J.A.R.V.I.S. Observation Analyzer.
Review the recent sequence of sanitized screen and system observations provided.
Identify significant contextual changes, user stuck-states, or important patterns (e.g., repeating errors, prolonged inactivity on a critical task, anomalous behavior).
DO NOT execute actions. DO NOT recommend automatic interventions.
Strictly output a JSON object:
{
    "pattern_detected": boolean,
    "confidence": float (0.0 to 1.0),
    "suggestion_or_warning": "Clear, concise observation or warning string (or null)",
    "evidence": ["List of specific UI/state changes supporting the pattern"]
}"""

    # Comprehensive Regex Patterns for Redaction & Data Boundary Protection.
    # NOTE: `\s` must be a single backslash inside these raw strings; a double
    # backslash matches a literal backslash and silently disables the pattern.
    SECRET_PATTERNS = [
        re.compile(r"api[_-]?key['\"\s]*[:=]['\"\s]*([a-zA-Z0-9_\-]{16,})", re.IGNORECASE),
        re.compile(r"bearer\s+[a-zA-Z0-9_\-.]+", re.IGNORECASE),
        re.compile(r"password['\"\s]*[:=]['\"\s]*([^\s&'\"]+)", re.IGNORECASE),
        re.compile(r"otp['\"\s]*[:=]['\"\s]*(\d{4,8})", re.IGNORECASE),
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),  # Credit card pattern approximation
        re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE) # Common OpenAI/LLM key patterns
    ]

    def __init__(self):
        self.is_running: bool = False
        self.is_paused: bool = False
        self.interval_seconds: float = 30.0
        
        self.loop_task: Optional[asyncio.Task] = None
        self._history_buffer: List[Dict[str, Any]] = []
        self._max_history_frames: int = 6
        self._ticks_since_last_pattern_check: int = 0
        # Signature of the last analyzed screen so we can skip the expensive
        # LLM analysis when nothing on screen actually changed.
        self._last_screen_signature: Optional[str] = None
        # Minimum time between deep LLM screen analyses (seconds). Screenshots of a
        # nominally static screen still differ byte-for-byte (clock, cursor, dither),
        # so without this cooldown the analyzer re-ran its 8-17s call every cycle.
        self.MIN_ANALYSIS_INTERVAL_SECONDS = 180.0
        self._last_analysis_ts: float = 0.0
        
        self.client_scope: Optional[str] = None
        self.project_scope: Optional[str] = None

    async def start(self):
        """Initializes and starts the continuous observation loop."""
        if not self.is_running:
            self.is_running = True
            self.is_paused = False
            
            self.loop_task = asyncio.create_task(self._observation_loop())
            logger.info(f"ObservationManager started. Interval: {self.interval_seconds}s.")
            await self._emit_event("observation.started", {"interval": self.interval_seconds})

    async def stop(self):
        """Gracefully halts the observation subsystem."""
        self.is_running = False
        if self.loop_task:
            self.loop_task.cancel()
            try:
                await self.loop_task
            except asyncio.CancelledError:
                pass
        
        self._history_buffer.clear()
        logger.info("ObservationManager stopped gracefully.")
        await self._emit_event("observation.stopped", {})

    def pause(self):
        """Temporarily suspends captures."""
        self.is_paused = True
        logger.info("ObservationManager paused.")

    def resume(self):
        """Resumes capture and analysis."""
        self.is_paused = False
        logger.info("ObservationManager resumed.")

    def set_scope(self, client_scope: Optional[str], project_scope: Optional[str]):
        """Sets active privacy and memory context scopes."""
        self.client_scope = client_scope
        self.project_scope = project_scope

    async def _emit_event(self, topic: str, payload: Dict[str, Any]):
        event = JarvisEvent(
            event_type=EventType.OBSERVATION,
            topic=topic,
            timestamp=utc_now(),
            correlation_id=f"obs_{uuid.uuid4().hex[:8]}",
            task_id="SYSTEM_OBSERVATION",
            source="ObservationManager",
            payload=payload
        )
        await event_bus.publish(event)

    async def _can_observe(self) -> bool:
        """
        Evaluates Emergency Stop, pause state, and consults SessionManager 
        as the authoritative owner of system lock and active-use state.
        """
        if self.is_paused:
            return False
            
        try:
            emergency_stop.assert_system_running()
        except ExecutionError:
            return False

        # SessionManager authoritative session & lock state check
        try:
            session_state = await session_manager.get_session_state()
            if not session_state or session_state.get("is_locked", False) or not session_state.get("is_active", True):
                return False
            # Voice-conversation priority: while the operator is in an active
            # wake/voice session, screen observation (capture + an 8-12s vision
            # LLM analysis) must NOT compete with the primary interaction loop.
            # Observation automatically resumes when the voice session ends.
            if session_state.get("is_wake_session", False):
                logger.debug("Voice session active; deferring observation cycle.")
                return False
        except Exception as e:
            logger.debug(f"SessionManager state lookup failed or reported inactive session: {e}")
            return False
            
        return True

    async def _execute_via_agent_runtime(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """
        Routes observation captures and analysis through the canonical AgentRuntime execution path,
        guaranteeing full security evaluation, telemetry, and scope enforcement.
        """
        task_id = f"obs_t_{uuid.uuid4().hex[:8]}"
        virtual_task = TaskPackage(
            task_id=task_id,
            parent_task_id=None,
            intent="SYSTEM_OBSERVATION_CAPTURE",
            objective=f"Perform authorized observation via {tool_name}",
            target_agents=["JARVIS"],
            selected_tools=[tool_name],
            tool_parameters={tool_name: parameters},
            # The trusted interactive local operator is the observation principal by
            # design; "SYSTEM_OBSERVER" is not a recognized principal and would be
            # hard-denied by the PermissionEngine's deny-by-default fallback.
            requester="User",
            client_scope=self.client_scope,
            project_scope=self.project_scope,
            permission_scope=PermissionScope(
                permission_level="L1",
                allowed_actions=[f"tool:{tool_name}"],
                allowed_resources=["system/observation"],
                forbidden_resources=[],
                scope={"domain": "observation"},
                purpose="System observation capture.",
                max_risk_level="low"
            ),
            resources=[],
            verification_contract=VerificationContract(
                verification_type="STATE_VERIFICATION",
                method="TOOL_CHECK",
                expected_outcome="Successful capture execution",
                required_evidence=[]
            ),
            expected_output=ExpectedOutput(
                format="JSON_STRUCTURED",
                description="Tool execution findings",
                schema_definition={}
            ),
            approval_state=ApprovalState.NOT_REQUIRED,
            execution_metadata=ExecutionMetadata(
                correlation_id=task_id,
                started_at=utc_now(),
                completed_at=None,
                retry_count=0,
                duration_ms=0.0
            )
        )

        result_package = await agent_runtime.execute(virtual_task)
        if result_package.status.value != "COMPLETED" and result_package.status != "COMPLETED":
            raise ExecutionError(
                message=f"AgentRuntime failed observation tool execution for [{tool_name}]",
                classification=ErrorClassification.TRANSIENT_PROVIDER
            )
            
        return result_package.findings.get("JARVIS", {}).get(tool_name)

    def _sanitize_observation_text(self, text: str) -> str:
        """Exhaustively redacts sensitive keys, secrets, OTPs, and personal financial data at the data boundary."""
        if not text:
            return ""
        
        sanitized = text
        for pattern in self.SECRET_PATTERNS:
            sanitized = pattern.sub("[REDACTED_SENSITIVE_DATA]", sanitized)
        return sanitized

    async def _observation_loop(self):
        while self.is_running:
            try:
                if await self._can_observe():
                    await self._perform_observation()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Observation loop error: {e}", exc_info=True)
                await self._emit_event("observation.failed", {"error": str(e)})
                
            await asyncio.sleep(self.interval_seconds)

    async def _perform_observation(self):
        capture_start = utc_now()
        
        # 1. Screen Capture via canonical AgentRuntime path
        try:
            capture_result = await self._execute_via_agent_runtime("screen_capture", {"quality": "standard"})
            if not capture_result or not isinstance(capture_result, dict) or "image_data" not in capture_result:
                return
        except Exception as e:
            logger.debug(f"AgentRuntime screen capture failed: {e}")
            return

        # 2. Screen Analyzer via canonical AgentRuntime path
        # Cheap change-detection gate: if the captured frame is byte-identical to
        # the last one we analyzed, skip the LLM analysis entirely. The observation
        # loop historically re-ran an 8-17s vision LLM call every cycle even when
        # the user's screen never changed, wasting API + CPU and slowing the main
        # JARVIS interaction. Only analyze when there is a meaningful reason to.
        analysis_result = None
        try:
            # Analysis cooldown: even if the byte-hash differs (a static screen still
            # varies by clock/cursor/dither), do not re-run the LLM analyzer more
            # often than every MIN_ANALYSIS_INTERVAL_SECONDS. Deep analysis is now
            # bounded to ~once per 3 minutes instead of every 30s cycle.
            if (time.time() - self._last_analysis_ts) < self.MIN_ANALYSIS_INTERVAL_SECONDS:
                logger.debug("Observation analysis on cooldown; skipping vision analysis.")
                return

            sig_path = capture_result.get("image_data") if isinstance(capture_result, dict) else None
            if sig_path and os.path.exists(sig_path):
                with open(sig_path, "rb") as _sf:
                    frame_signature = hashlib.sha256(_sf.read()).hexdigest()
            else:
                frame_signature = None

            if frame_signature is not None and frame_signature == self._last_screen_signature:
                logger.debug("Observation screen unchanged; skipping vision analysis.")
                return

            analyzer_params = {
                "image_data": capture_result["image_data"],
                "extract_context": True,
                "mask_sensitive": True,
                "instructions": "Extract active UI application, foreground activity, and context. Mask credentials/OTPs."
            }
            analysis_result = await self._execute_via_agent_runtime("screen_analyzer", analyzer_params)
            if not analysis_result or not isinstance(analysis_result, dict):
                return
            # Persist the signature only after a successful analysis, so a transient
            # analysis failure retries rather than being treated as "no change".
            if frame_signature:
                self._last_screen_signature = frame_signature
            self._last_analysis_ts = time.time()
        except Exception as e:
            logger.debug(f"AgentRuntime screen analyzer failed: {e}")
            return
        finally:
            # Screenshot lifecycle ownership: the temporary capture is deleted by the
            # ObservationManager immediately after analysis (or on failure), so
            # screenshots can never accumulate on disk across observation cycles.
            image_path = capture_result.get("image_data") if isinstance(capture_result, dict) else None
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                    logger.debug(f"Temporary observation screenshot cleaned up: {image_path}")
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up observation screenshot {image_path}: {cleanup_err}")

        # 3. Explicit Data Sanitization & Redaction at the Boundary
        raw_context = analysis_result.get("context", "Unknown state")
        raw_app = analysis_result.get("foreground_app", "Unknown app")
        
        sanitized_context = self._sanitize_observation_text(raw_context)
        sanitized_app = self._sanitize_observation_text(raw_app)

        observation_record = {
            "timestamp": capture_start.isoformat(),
            "source": "screen_analyzer",
            "context": sanitized_context,
            "foreground_app": sanitized_app,
            "significant_change": analysis_result.get("significant_change", False)
        }

        self._history_buffer.append(observation_record)
        if len(self._history_buffer) > self._max_history_frames:
            self._history_buffer.pop(0)

        self._ticks_since_last_pattern_check += 1
        should_check_patterns = (
            observation_record["significant_change"] or 
            self._ticks_since_last_pattern_check >= self._max_history_frames
        )

        if should_check_patterns and len(self._history_buffer) >= 2:
            self._ticks_since_last_pattern_check = 0
            await self._detect_patterns_and_suggest()

        await self._emit_event("observation.completed", {"duration_ms": (utc_now() - capture_start).total_seconds() * 1000.0})

    async def _detect_patterns_and_suggest(self):
        history_summary = json.dumps(self._history_buffer, indent=2)
        prompt = f"Recent Sanitized Observation Frames:\n{history_summary}"

        candidate = None
        for attempt in range(2):
            try:
                eval_result = await api_engine.call_llm(
                    prompt=prompt,
                    system_prompt=self.PATTERN_ANALYSIS_PROMPT,
                    temperature=0.1
                )

                if not eval_result.get("success"):
                    return

                candidate = extract_json_object(
                    strip_code_fences(eval_result.get("response", ""))
                )
                if isinstance(candidate, dict):
                    break
                # First pass returned prose/empty — retry once with a stronger
                # constraint instead of silently skipping the whole cycle.
                if attempt == 0:
                    logger.debug("Observation analyzer JSON parse failed; retrying once with stronger constraint.")
                    prompt += (
                        "\n\nIMPORTANT: Your previous reply was not valid JSON. "
                        "Reply with ONLY a single JSON object. No prose, no markdown, no code fences."
                    )
                candidate = None
            except Exception as e:
                candidate = None
                if attempt == 1:
                    logger.debug(f"Observation pattern analysis failed on final attempt: {e}")

        if not isinstance(candidate, dict):
            # Non-fatal by design: the analyzer LLM occasionally returns prose or an
            # empty body; the next observation tick retries naturally.
            logger.warning("Pattern detection evaluation skipped: Observation analyzer returned no decodable JSON object.")
            return

        parsed_data = candidate

        if parsed_data.get("pattern_detected") and parsed_data.get("confidence", 0.0) >= 0.75:
            suggestion = parsed_data.get("suggestion_or_warning")
            evidence = parsed_data.get("evidence", [])

            if suggestion:
                sanitized_suggestion = self._sanitize_observation_text(suggestion)
                sanitized_evidence = [self._sanitize_observation_text(ev) for ev in evidence]
                await self._record_suggestion_to_memory(sanitized_suggestion, sanitized_evidence, parsed_data["confidence"])

    async def _record_suggestion_to_memory(self, suggestion: str, evidence: List[str], confidence: float):
        content = f"Observation Pattern Detected: {suggestion}. Evidence: {', '.join(evidence)}"

        # Suppress low-value self-referential "nothing changed" observations. A
        # static screen is the normal idle state, and this exact pattern was being
        # detected and saved on nearly every cycle, spamming the memory vault with
        # junk that readers then fetch as if it were meaningful context.
        _c = content.lower()
        if any(token in _c for token in (
            "static screen", "no change", "no significant change", "unchanged",
            "repetitive", "no progression", "no user interaction", "remained static",
            "stuck in a repetitive", "repeatedly executing",
        )):
            logger.debug("Suppressed low-value static-screen observation memory.")
            return

        try:
            async with worker_session() as db:
                await memory_manager.create_structured_memory(
                    db=db,
                    memory_id=f"mem_obs_{uuid.uuid4().hex[:8]}",
                    content=content,
                    category=MemoryCategory.EPISODIC,
                    client_scope=self.client_scope,
                    project_scope=self.project_scope,
                    user_id="SYSTEM",
                    confidence=confidence,
                    source_task_id="SYSTEM_OBSERVATION",
                    source_provenance="JARVIS Continuous Observation"
                )
                
            logger.info(f"Observation Suggestion Recorded: {suggestion}")
            await self._emit_event("observation.suggestion_generated", {
                "suggestion": suggestion,
                "confidence": confidence,
                "evidence": evidence
            })
            
        except Exception as e:
            logger.error(f"Failed to persist observation suggestion to memory: {e}")


observation_manager = ObservationManager()