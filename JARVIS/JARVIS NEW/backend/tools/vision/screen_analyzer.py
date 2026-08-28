"""
backend/tools/vision/screen_analyzer.py
J.A.R.V.I.S. Secure Screen and Vision Analyzer Tool.
Analyzes screenshots securely for ObservationManager and JARVIS agents. Enforces SessionManager eligibility,
EmergencyStop, explicit permission/scope validations, secure workspace path restriction, bounded timeouts,
API Engine vision calls, exhaustive sensitive-data redaction, normalized response formatting, and clean retention ownership.
"""

import os
import uuid
import base64
import re
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field, AliasChoices

from backend.tools.base_tool import BaseTool
from backend.tools.files.file_security import secure_path_resolve
from backend.infrastructure.database import worker_session
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.infrastructure.api_engine import api_engine
from backend.observation.session_manager import session_manager
from backend.core.emergency_stop import emergency_stop
from backend.security.security_manager import security_manager
from backend.core.task_contracts import TaskPackage, PermissionScope, ApprovalState, VerificationContract, ExpectedOutput
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Tools.ScreenAnalyzer")

def utc_now():
    return datetime.now(timezone.utc)

class ScreenAnalyzerInput(BaseModel):
    model_config = {"populate_by_name": True}

    # Accept both `image_path` and the `image_data` alias emitted by the
    # screen_capture output contract / observation pipeline.
    image_path: str = Field(
        validation_alias=AliasChoices("image_path", "image_data", "image"),
        description="The secure workspace path to the screenshot or image to analyze."
    )
    # Accept both `query` and the `instructions` alias used by the observation pipeline.
    query: str = Field(
        default="Describe what is visible on this screen in detail, identifying active UI elements and context without fabricating unverified claims.",
        validation_alias=AliasChoices("query", "instructions"),
        description="The specific visual question or instruction about the image."
    )
    timeout_seconds: float = Field(default=20.0, description="Maximum duration allowed for vision analysis execution before timeout.")
    cleanup_image: bool = Field(default=False, description="Whether to automatically delete the temporary image file (delegated to capture lifecycle ownership by default).")

class ScreenAnalyzerTool(BaseTool):
    name = "screen_analyzer"
    description = "Securely analyzes screenshots or images using the central API engine with session guardrails and sensitive-data redaction."
    category = "vision"
    args_schema = ScreenAnalyzerInput
    risk_level = "low"
    requires_approval = False

    # Comprehensive Regex Patterns for Redaction & Sensitive Data Protection
    SECRET_PATTERNS = [
        re.compile(r"api[_-]?key['\"\s]*[:=]['\"\s]*([a-zA-Z0-9_\-]{16,})", re.IGNORECASE),
        re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]+", re.IGNORECASE),
        re.compile(r"password['\"\s]*[:=]['\"\s]*([^\s&'\"]+)", re.IGNORECASE),
        re.compile(r"otp['\"\s]*[:=]['\"\s]*(\d{4,8})", re.IGNORECASE),
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),  # Credit card pattern approximation
        re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE)  # Common LLM/API keys
    ]

    async def _emit_analyzer_event(self, topic: str, payload: dict):
        event = JarvisEvent(
            event_type=EventType.OBSERVATION,
            topic=topic,
            timestamp=utc_now(),
            correlation_id=f"ana_{uuid.uuid4().hex[:8]}",
            task_id="SCREEN_ANALYZER",
            source="ScreenAnalyzerTool",
            payload=payload
        )
        await event_bus.publish(event)

    def _sanitize_findings_text(self, text: str) -> str:
        """Exhaustively redacts credentials, secrets, tokens, OTPs, and financial data from text findings."""
        if not text:
            return ""
        sanitized = text
        for pattern in self.SECRET_PATTERNS:
            sanitized = pattern.sub("[REDACTED_SENSITIVE_DATA]", sanitized)
        return sanitized

    async def _run(self, image_path: str, query: str = "Describe what is visible on this screen in detail.", timeout_seconds: float = 20.0, cleanup_image: bool = False) -> Dict[str, Any]:
        """
        Executes secure image analysis, verifying session eligibility, system constraints,
        explicit permission scopes, workspace containment, data redaction, and precise error classification.
        """
        # 1. Enforce Emergency Stop check
        try:
            emergency_stop.assert_system_running()
        except Exception as e:
            raise ExecutionError(
                message=f"Screen analysis blocked: Global Emergency Stop is active. {str(e)}",
                classification=ErrorClassification.SECURITY_FAILURE
            )

        # 2. Enforce SessionManager eligibility / Locked-state check
        if not await session_manager.is_observation_eligible():
            raise ExecutionError(
                message="Screen analysis blocked: Session is locked, inactive, or observation is ineligible.",
                classification=ErrorClassification.AUTHORIZATION_FAILURE
            )

        # 3. Canonical Permission and Scope Validation via SecurityManager
        virtual_task = TaskPackage(
            task_id=f"ana_chk_{uuid.uuid4().hex[:8]}",
            parent_task_id=None,
            intent="SYSTEM_OBSERVATION_ANALYSIS",
            objective="Perform secure screen image analysis.",
            target_agents=["JARVIS"],
            selected_tools=["screen_analyzer"],
            tool_parameters={"screen_analyzer": {"image_path": image_path, "query": query}},
            requester="SYSTEM_OBSERVER",
            permission_scope=PermissionScope(
                permission_level="L1",
                allowed_actions=["tool:screen_analyzer"],
                allowed_resources=["system/observation"],
                forbidden_resources=[],
                scope={"domain": "observation"},
                purpose="Observation analysis permission validation.",
                max_risk_level="low"
            ),
            resources=[],
            verification_contract=VerificationContract(
                verification_type="STATE_VERIFICATION",
                method="TOOL_CHECK",
                expected_outcome="Successful authorized image analysis",
                required_evidence=[]
            ),
            expected_output=ExpectedOutput(
                format="JSON_STRUCTURED",
                description="Analysis findings",
                schema_definition={}
            ),
            approval_state=ApprovalState.NOT_REQUIRED
        )

        async with worker_session() as db:
            security_decision = await security_manager.evaluate_tool_execution(
                task_package=virtual_task,
                tool_name="screen_analyzer",
                parameters={"image_path": image_path}
            )
            decision_status = str(security_decision.get("status", "")).upper()
            
            if decision_status in ["DENIED", "WAITING_APPROVAL", "PENDING"]:
                raise ExecutionError(
                    message=f"Screen analysis rejected by SecurityManager policy. Status: {decision_status}",
                    classification=ErrorClassification.AUTHORIZATION_FAILURE
                )

        # 4. Enforce Workspace Boundary & Scoped File Access
        try:
            safe_path = secure_path_resolve(image_path)
        except Exception as e:
            raise ExecutionError(
                message=f"Path resolution security violation: {str(e)}",
                classification=ErrorClassification.SECURITY_FAILURE
            )

        def _read_and_encode_image():
            if not os.path.exists(safe_path):
                raise FileNotFoundError(f"Image not found at path: {safe_path}")
            if not os.path.isfile(safe_path):
                raise IsADirectoryError(f"Path is a directory, not an image: {safe_path}")
            with open(safe_path, "rb") as image_file:
                raw_bytes = image_file.read()
                return base64.b64encode(raw_bytes).decode('utf-8'), len(raw_bytes)

        await self._emit_analyzer_event("screen_analyzer.started", {"query": query, "image_path": image_path})
        start_time = utc_now()

        try:
            base64_image, file_size = await asyncio.to_thread(_read_and_encode_image)

            # 5. Delegate to central API Engine vision capability with timeout bounds
            vision_response = await asyncio.wait_for(
                api_engine.analyze_vision(
                    image_base64=base64_image,
                    query=query
                ),
                timeout=60.0
            )

            latency_ms = (utc_now() - start_time).total_seconds() * 1000.0

            # Normalize and strictly sanitize findings to protect sensitive data / tokens / passwords
            if isinstance(vision_response, dict):
                raw_findings = vision_response.get("findings", vision_response.get("text", str(vision_response)))
                raw_app = vision_response.get("foreground_app", "Unknown")
                confidence = vision_response.get("confidence", 0.92)
                provider = vision_response.get("provider", "default_vision_provider")
                model = vision_response.get("model", "default_vision_model")
                significant_change = vision_response.get("significant_change", False)
            else:
                raw_findings = str(vision_response)
                raw_app = "Unknown"
                confidence = 0.92
                provider = "default_vision_provider"
                model = "default_vision_model"
                significant_change = False

            sanitized_findings = self._sanitize_findings_text(raw_findings)
            sanitized_app = self._sanitize_findings_text(raw_app)

            normalized_result = {
                "findings": sanitized_findings,
                "context": sanitized_findings,  # Compatible alias for ObservationManager
                "foreground_app": sanitized_app,
                "significant_change": significant_change,
                "confidence": confidence,
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "timestamp": utc_now().isoformat(),
                "evidence": {
                    "source_image": image_path,
                    "file_size_bytes": file_size
                }
            }

            logger.info("Screen analysis completed successfully via central API Engine.")
            await self._emit_analyzer_event("screen_analyzer.completed", normalized_result)
            return normalized_result

        except asyncio.TimeoutError:
            logger.error("Screen analysis operation timed out.")
            await self._emit_analyzer_event("screen_analyzer.failed", {"error": "Timeout"})
            raise ExecutionError(
                message=f"Screen analysis timed out after {timeout_seconds} seconds.",
                classification=ErrorClassification.TIMEOUT
            )
        except FileNotFoundError as fnf_err:
            raise ExecutionError(
                message=str(fnf_err),
                classification=ErrorClassification.VALIDATION_FAILURE
            )
        except Exception as e:
            logger.error(f"Screen analysis failed: {str(e)}")
            await self._emit_analyzer_event("screen_analyzer.failed", {"error": str(e)})
            if isinstance(e, ExecutionError):
                raise e
            raise ExecutionError(
                message=f"Vision analysis failed: {str(e)}",
                classification=ErrorClassification.TRANSIENT_PROVIDER
            )
        finally:
            # 6. Optional localized cleanup (respects explicit ownership flags without breaking shared capture state)
            if cleanup_image and os.path.exists(safe_path):
                try:
                    os.remove(safe_path)
                    logger.debug(f"Temporary analyzed image cleaned up: {safe_path}")
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up image file {safe_path}: {cleanup_err}")

screen_analyzer_tool = ScreenAnalyzerTool()