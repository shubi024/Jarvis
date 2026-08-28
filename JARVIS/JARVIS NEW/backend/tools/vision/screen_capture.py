"""
backend/tools/vision/screen_capture.py
J.A.R.V.I.S. Secure Screen Capture Vision Tool.
Captures screen state securely for the ObservationManager. Enforces SessionManager eligibility,
EmergencyStop, explicit permission/scope validations, approval gates, bounded image constraints,
workspace path restrictions, capture timeouts, and event telemetry.
"""

import os
import uuid
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from PIL import Image, ImageGrab
from pydantic import BaseModel, Field

from backend.tools.base_tool import BaseTool
from backend.tools.files.file_security import secure_path_resolve, get_default_output_dir
from backend.infrastructure.database import worker_session
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.observation.session_manager import session_manager
from backend.core.emergency_stop import emergency_stop
from backend.security.security_manager import security_manager
from backend.core.task_contracts import TaskPackage, PermissionScope, ApprovalState, VerificationContract, ExpectedOutput
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Tools.ScreenCapture")

def utc_now():
    return datetime.now(timezone.utc)

class ScreenCaptureInput(BaseModel):
    quality: str = Field(default="standard", description="Capture quality preset: 'low', 'standard', or 'high'.")
    max_dimension: int = Field(default=1280, description="Maximum width or height to bound image size and control memory/API costs.")
    timeout_seconds: float = Field(default=10.0, description="Maximum duration allowed for capture execution before timeout.")

class ScreenCaptureTool(BaseTool):
    name = "screen_capture"
    description = "Securely captures primary display state within workspace bounds for observation analysis."
    category = "vision"
    args_schema = ScreenCaptureInput
    risk_level = "low"
    requires_approval = False
    
    async def _emit_capture_event(self, topic: str, payload: dict):
        event = JarvisEvent(
            event_type=EventType.OBSERVATION,
            topic=topic,
            timestamp=utc_now(),
            correlation_id=f"cap_{uuid.uuid4().hex[:8]}",
            task_id="SCREEN_CAPTURE",
            source="ScreenCaptureTool",
            payload=payload
        )
        await event_bus.publish(event)

    async def _run(self, quality: str = "standard", max_dimension: int = 1280, timeout_seconds: float = 10.0) -> Dict[str, Any]:
        """
        Executes secure screen capture, verifying session eligibility, system constraints,
        explicit permission scope, approval gates, bounded dimensions, capture timeouts, and telemetry.
        """
        # 1. Enforce Emergency Stop check
        try:
            emergency_stop.assert_system_running()
        except Exception as e:
            raise ExecutionError(
                message=f"Screen capture blocked: Global Emergency Stop is active. {str(e)}",
                classification=ErrorClassification.SECURITY_FAILURE
            )

        # 2. Enforce SessionManager eligibility / Locked-state check
        if not await session_manager.is_observation_eligible():
            raise ExecutionError(
                message="Screen capture blocked: Session is locked, inactive, or observation is ineligible.",
                classification=ErrorClassification.AUTHORIZATION_FAILURE
            )

        # 3. Canonical Permission, Scope, and Approval State Validation via SecurityManager
        virtual_task = TaskPackage(
            task_id=f"cap_chk_{uuid.uuid4().hex[:8]}",
            parent_task_id=None,
            intent="SYSTEM_OBSERVATION_CAPTURE",
            objective="Perform secure display screen capture.",
            target_agents=["JARVIS"],
            selected_tools=["screen_capture"],
            tool_parameters={"screen_capture": {"quality": quality, "max_dimension": max_dimension}},
            requester="SYSTEM_OBSERVER",
            permission_scope=PermissionScope(
                permission_level="L1",
                allowed_actions=["tool:screen_capture"],
                allowed_resources=["system/observation"],
                forbidden_resources=[],
                scope={"domain": "observation"},
                purpose="Observation screen capture permission validation.",
                max_risk_level="low"
            ),
            resources=[],
            verification_contract=VerificationContract(
                verification_type="STATE_VERIFICATION",
                method="TOOL_CHECK",
                expected_outcome="Successful authorized capture",
                required_evidence=[]
            ),
            expected_output=ExpectedOutput(
                format="JSON_STRUCTURED",
                description="Capture findings",
                schema_definition={}
            ),
            approval_state=ApprovalState.NOT_REQUIRED
        )

        async with worker_session() as db:
            security_decision = await security_manager.evaluate_tool_execution(
                task_package=virtual_task,
                tool_name="screen_capture",
                parameters={"quality": quality, "max_dimension": max_dimension}
            )
            decision_status = str(security_decision.get("status", "")).upper()
            
            # Explicitly reject/propagate denials or approval-required decisions
            if decision_status in ["DENIED", "WAITING_APPROVAL", "PENDING"]:
                raise ExecutionError(
                    message=f"Screen capture rejected by SecurityManager policy. Status: {decision_status}",
                    classification=ErrorClassification.AUTHORIZATION_FAILURE
                )

        # Resolve a writable directory inside the LOCKED filesystem boundary
        # (E:\JARVIS\output\vision preferred, Downloads\JARVIS\output\vision fallback).
        try:
            safe_dir = get_default_output_dir(os.path.join("output", "vision"))
        except Exception as e:
            raise ExecutionError(
                message=f"Vision output path resolution failed: {str(e)}",
                classification=ErrorClassification.SECURITY_FAILURE
            )

        correlation_id = f"cap_{uuid.uuid4().hex[:8]}"
        file_name = f"screenshot_{int(utc_now().timestamp())}_{uuid.uuid4().hex[:4]}.jpg"
        file_path = os.path.join(safe_dir, file_name)

        await self._emit_capture_event("screen_capture.started", {"quality": quality, "max_dimension": max_dimension})

        def _capture_and_bound_screen():
            img = ImageGrab.grab()
            orig_width, orig_height = img.size

            if orig_width > max_dimension or orig_height > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

            save_quality = 85 if quality == "standard" else (60 if quality == "low" else 95)
            img.save(file_path, "JPEG", quality=save_quality)

            return {
                "file_path": file_path,
                "width": img.size[0],
                "height": img.size[1],
                "original_width": orig_width,
                "original_height": orig_height
            }

        try:
            # 4. Enforce capture timeout and cancellation around blocking ImageGrab operation
            metadata = await asyncio.wait_for(
                asyncio.to_thread(_capture_and_bound_screen),
                timeout=timeout_seconds
            )
            
            # Output contract preserving file reference for downstream analyzer consumers
            payload = {
                "image_path": metadata["file_path"],
                "image_data": metadata["file_path"],
                "dimensions": {"width": metadata["width"], "height": metadata["height"]},
                "timestamp": utc_now().isoformat(),
                "correlation_id": correlation_id,
                "display": "primary"
            }

            logger.info(f"Visual capture successful. Saved securely to {metadata['file_path']}")
            await self._emit_capture_event("screen_capture.completed", payload)
            
            return payload

        except asyncio.TimeoutError:
            logger.error("Screen capture operation timed out.")
            await self._emit_capture_event("screen_capture.failed", {"error": "Timeout"})
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            raise ExecutionError(
                message=f"Screen capture timed out after {timeout_seconds} seconds.",
                classification=ErrorClassification.TIMEOUT
            )
        except Exception as e:
            logger.error(f"Visual capture execution failed: {str(e)}")
            await self._emit_capture_event("screen_capture.failed", {"error": str(e)})
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            if isinstance(e, ExecutionError):
                raise e
            raise ExecutionError(
                message=f"Could not capture screen: {str(e)}",
                classification=ErrorClassification.TRANSIENT_PROVIDER
            )

screen_capture_tool = ScreenCaptureTool()