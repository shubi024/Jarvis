import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.security.permissions import permission_engine, PermissionLevel
from backend.security.approval_manager import approval_manager
from backend.tools.tool_registry import tool_registry
from backend.core.task_contracts import TaskPackage

logger = logging.getLogger("JARVIS.Security.SecurityManager")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class SecurityDecision(BaseModel):
    """
    Canonical security decision object consumed by TaskQueue, AgentRuntime, and VerificationEngine.
    """
    status: str  # "ALLOWED", "APPROVAL_REQUIRED", "DENIED"
    permission_granted: bool
    approval_required: bool
    risk_level: str
    approval_id: Optional[str] = None
    reason: str
    correlation_id: str

    def get(self, key: str, default: Any = None) -> Any:
        """Compatibility accessor for legacy orchestration callers."""
        return getattr(self, key, default)


class SecurityManager:
    """
    Central security gateway for J.A.R.V.I.S.
    Enforces deny-by-default behavior, emergency-stop checks, scope matching, 
    permission validation, and approval creation with zero execution authority.
    """
    def __init__(self):
        # System-wide emergency stop flag
        self.emergency_stop_active = False

    def trigger_emergency_stop(self):
        """Activates system-wide emergency stop, immediately blocking all consequential execution."""
        self.emergency_stop_active = True
        logger.critical("EMERGENCY STOP ACTIVATED. All security gates locked.")

    def reset_emergency_stop(self):
        """Resets system-wide emergency stop."""
        self.emergency_stop_active = False
        logger.info("Emergency stop deactivated. Security gates normal.")

    async def _emit_security_event(self, topic: str, task_id: str, correlation_id: str, payload: Dict[str, Any]):
        """Helper for standardized security audit telemetry."""
        event = JarvisEvent(
            event_type=EventType.SECURITY,
            topic=topic,
            task_id=task_id,
            correlation_id=correlation_id,
            source="SecurityManager",
            payload=payload
        )
        await event_bus.publish(event)

    def _sanitize_log_data(self, data: Any) -> Any:
        """Ensures logs and events never expose credentials, passwords, or secrets."""
        if isinstance(data, dict):
            sanitized = {}
            sensitive_keys = {"password", "secret", "api_key", "token", "credential", "auth"}
            for k, v in data.items():
                if any(sk in k.lower() for sk in sensitive_keys):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = self._sanitize_log_data(v)
            return sanitized
        elif isinstance(data, list):
            return [self._sanitize_log_data(item) for item in data]
        return data

    @staticmethod
    def _canonicalize_parameters(parameters: Any) -> str:
        """Stable, order-independent JSON representation of a parameter set for equality checks."""
        try:
            return json.dumps(parameters, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            return str(parameters)

    def _parameters_match(self, stored_params: Any, current_params: Any) -> bool:
        """
        Compares a stored approval's parameter snapshot against the currently requested
        parameter set. Legacy approvals without a recorded snapshot pass vacuously;
        any recorded snapshot must match EXACTLY (order-independent).
        """
        if stored_params is None:
            return True
        return self._canonicalize_parameters(stored_params) == self._canonicalize_parameters(current_params)

    async def evaluate_tool_execution(
        self,
        task_package: TaskPackage,
        tool_name: str,
        parameters: Dict[str, Any]
    ) -> SecurityDecision:
        """
        Convenience wrapper requested by AgentRuntime & VerificationEngine to evaluate 
        a specific tool action against the security gateway using the canonical TaskPackage.
        """
        return await self.evaluate_security_gate(
            task_id=task_package.task_id,
            intent=task_package.intent,
            target_agents=task_package.target_agents,
            parameters=parameters,
            tool_name=tool_name,
            task_package=task_package
        )

    async def evaluate_task_package(self, task_package_or_workflow: Any, db: Any = None) -> SecurityDecision:
        """Evaluate a planned task before it reaches the queue.

        This is an orchestration-time gate only. AgentRuntime repeats the exact
        tool-level gate immediately before execution, so a plan cannot gain
        authority merely by waiting in the queue.
        """
        task_id = getattr(task_package_or_workflow, "task_id", None) or getattr(task_package_or_workflow, "workflow_id", "unknown_task")
        target_agents = list(getattr(task_package_or_workflow, "target_agents", []))
        if not target_agents:
            target_agents = [assignment.agent_id for assignment in getattr(task_package_or_workflow, "agents", [])]
        intent = getattr(task_package_or_workflow, "intent", "WORKFLOW_EXECUTION")
        selected_tools = list(getattr(task_package_or_workflow, "selected_tools", []))
        tool_parameters = getattr(task_package_or_workflow, "tool_parameters", {})

        for tool_name in selected_tools:
            decision = await self.evaluate_security_gate(
                task_id=task_id,
                intent=intent,
                target_agents=target_agents or ["JARVIS"],
                parameters=tool_parameters.get(tool_name, {}),
                tool_name=tool_name,
                task_package=task_package_or_workflow if isinstance(task_package_or_workflow, TaskPackage) else None,
            )
            if decision.status != "ALLOWED":
                return decision

        return await self.evaluate_security_gate(
            task_id=task_id,
            intent=intent,
            target_agents=target_agents or ["JARVIS"],
            parameters={},
            task_package=task_package_or_workflow if isinstance(task_package_or_workflow, TaskPackage) else None,
        )

    async def evaluate_security_gate(
        self, 
        task_id: str,
        intent: str, 
        target_agents: List[str], 
        parameters: Dict[str, Any],
        tool_name: Optional[str] = None,
        task_package: Optional[TaskPackage] = None
    ) -> SecurityDecision:
        """
        Evaluates an incoming request against emergency-stop, permissions, scopes, and human approval gates.
        Returns a canonical SecurityDecision object.
        """
        correlation_id = task_id
        sanitized_params = self._sanitize_log_data(parameters)
        logger.info(f"SecurityManager evaluating security gate for task [{task_id}], intent: [{intent}], tool: [{tool_name}]")

        # 1. Emergency-Stop Awareness: Fail closed if system-wide lock is active
        if self.emergency_stop_active:
            reason = "Security violation: System emergency stop is currently active. All execution is blocked."
            logger.critical(reason)
            await self._emit_security_event("security.denied", task_id, correlation_id, {"reason": reason})
            return SecurityDecision(
                status="DENIED",
                permission_granted=False,
                approval_required=False,
                risk_level="critical",
                reason=reason,
                correlation_id=correlation_id
            )

        # 2. Tool Risk Evaluation and Authority-Level Derivation (locked matrix:
        #    L1=READ, L2=DRAFT, L3=APPROVAL REQUIRED; L4 is never derived from risk —
        #    it exists only via explicit durable user grants).
        from backend.security.permissions import RISK_TO_LEVEL

        risk_level = "low"
        tool_requires_approval = False
        derived_level = PermissionLevel.L1_READ_OBSERVE

        if tool_name:
            tool = tool_registry.get_tool(tool_name)
            if tool:
                risk_level = getattr(tool, "risk_level", "low").lower()
                tool_requires_approval = getattr(tool, "requires_approval", False)
                
                derived_level = max(derived_level, RISK_TO_LEVEL.get(risk_level, PermissionLevel.L3_APPROVAL_REQUIRED))
            else:
                reason = f"Security violation: Unregistered tool [{tool_name}] requested."
                logger.warning(reason)
                await self._emit_security_event("security.denied", task_id, correlation_id, {"tool": tool_name, "reason": reason})
                return SecurityDecision(
                    status="DENIED",
                    permission_granted=False,
                    approval_required=False,
                    risk_level="high",
                    reason=reason,
                    correlation_id=correlation_id
                )
        else:
            intent_upper = intent.upper()
            if any(act in intent_upper for act in ["EXECUTE", "DELETE", "UPDATE", "WRITE", "MODIFY", "CRITICAL"]):
                derived_level = PermissionLevel.L3_APPROVAL_REQUIRED
                tool_requires_approval = True

        # Extract context fields from TaskPackage if available
        principal_id = task_package.requester if task_package else parameters.get("requester_id")
        client_scope = task_package.client_scope if task_package else parameters.get("client_scope")
        project_scope = task_package.project_scope if task_package else parameters.get("project_scope")
        resource_target = tool_name or intent
        action_type = intent

        # 3. Integrate PermissionEngine with exact parameters and derived hierarchy levels
        permission_decision, approval_needed_by_policy = await permission_engine.evaluate_request(
            target_agents=target_agents,
            intent=intent,
            parameters=parameters,
            tool_name=tool_name,
            principal_id=principal_id,
            client_scope=client_scope,
            project_scope=project_scope,
            resource_target=resource_target,
            action_type=action_type,
            required_level=derived_level
        )

        if permission_decision == "DENIED":
            reason = f"Security violation: Permission denied by PermissionEngine for intent [{intent}] (Tool: {tool_name})."
            logger.warning(reason)
            await self._emit_security_event("security.denied", task_id, correlation_id, {"intent": intent, "tool": tool_name, "reason": reason})
            return SecurityDecision(
                status="DENIED",
                permission_granted=False,
                approval_required=False,
                risk_level=risk_level,
                reason=reason,
                correlation_id=correlation_id
            )

        # 4. Integrate ApprovalManager with strict re-validation against drift
        requires_human_approval = tool_requires_approval or approval_needed_by_policy or risk_level in {"high", "critical"}

        if requires_human_approval:
            # Revalidate existing approval against current requested attributes (preventing silent attribute substitution)
            existing_approval = await approval_manager.get_approval_by_task(task_id)
            if existing_approval and existing_approval.get("status") == "APPROVED":
                # Strict attribute parity check
                stored_tool = existing_approval.get("tool_name")
                stored_resource = existing_approval.get("resource")
                stored_action = existing_approval.get("action")

                # Strict attribute parity check — extended to PARAMETERS and SCOPES:
                # a changed parameter set or changed client/project scope must never
                # silently reuse an old human approval (approval parameter drift).
                parameters_match = self._parameters_match(
                    existing_approval.get("parameters_summary"), parameters
                )
                scopes_match = (
                    (existing_approval.get("client_scope") or None) == (client_scope or None)
                    and (existing_approval.get("project_scope") or None) == (project_scope or None)
                )

                if (stored_tool == tool_name or not stored_tool) and \
                   (stored_resource == resource_target or not stored_resource) and \
                   (stored_action == action_type or not stored_action) and \
                   parameters_match and scopes_match:
                    logger.info(f"Existing valid and matched approval found for task [{task_id}]; proceeding.")
                    return SecurityDecision(
                        status="ALLOWED",
                        permission_granted=True,
                        approval_required=False,
                        risk_level=risk_level,
                        approval_id=existing_approval.get("approval_id"),
                        reason="Pre-existing valid human approval verified with matching action, parameter, and scope bindings.",
                        correlation_id=correlation_id
                    )
                else:
                    logger.warning(f"Existing approval found for task [{task_id}], but action/parameter/scope bindings drifted. Requiring fresh approval.")

            logger.info(f"Action intent [{intent}] for task [{task_id}] (Tool: {tool_name}, Risk: {risk_level}) requires human authorization gate.")
            
            # Create a pending durable approval request
            approval_id = await approval_manager.create_approval_request(
                task_id=task_id,
                intent=intent,
                target_agents=target_agents,
                tool_name=tool_name,
                resource=resource_target,
                action=action_type,
                parameters=parameters,
                risk_level=risk_level,
                client_scope=client_scope,
                project_scope=project_scope,
                requested_by=principal_id or "JARVIS_RUNTIME"
            )

            await self._emit_security_event("security.approval_required", task_id, correlation_id, {
                "approval_id": approval_id,
                "tool": tool_name,
                "risk_level": risk_level
            })

            return SecurityDecision(
                status="APPROVAL_REQUIRED",
                permission_granted=True,
                approval_required=True,
                risk_level=risk_level,
                approval_id=approval_id,
                reason=f"Action requires explicit human authorization due to risk classification ({risk_level}).",
                correlation_id=correlation_id
            )

        # 5. Fully Allowed
        await self._emit_security_event("security.allowed", task_id, correlation_id, {"intent": intent, "tool": tool_name})
        return SecurityDecision(
            status="ALLOWED",
            permission_granted=True,
            approval_required=False,
            risk_level=risk_level,
            reason="Request successfully passed all security, scope, and permission gates.",
            correlation_id=correlation_id
        )

security_manager = SecurityManager()
