"""
backend/core/agent_runtime.py
Final J.A.R.V.I.S. Agent Runtime Environment.
Executes canonical TaskPackages by delegating to authorized specialist agents
or direct core tools, enforcing selected-tool boundaries, evaluating security gates,
and returning strict ResultPackages with accurate status preservation.
"""

import asyncio
import logging
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime, timezone

from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.core.task_contracts import (
    TaskPackage, ResultPackage, ResultStatus, ActionRecord
)
from backend.core.execution_errors import ExecutionError, ErrorClassification
from backend.tools.tool_registry import tool_registry
from backend.core.agent_registration import agent_registry
from backend.security.security_manager import security_manager

logger = logging.getLogger("JARVIS.Core.AgentRuntime")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalRequiredException(Exception):
    """Signals that a requested action requires human authorization."""
    def __init__(self, approval_id: Optional[str], reason: str):
        self.approval_id = approval_id
        self.reason = reason
        super().__init__(reason)


class AgentRuntime:
    """
    J.A.R.V.I.S. Agent Runtime Environment.
    Strictly coordinates specialist agents, enforces capability/tool boundaries, 
    evaluates runtime security context, and handles result aggregation.
    """

    async def _emit_runtime_event(self, topic: str, task_id: str, payload: Dict[str, Any]):
        """Helper for standardized runtime telemetry."""
        event = JarvisEvent(
            event_type=EventType.AGENT,
            topic=topic,
            timestamp=utc_now(),
            correlation_id=task_id,
            task_id=task_id,
            source="AgentRuntime",
            payload=payload
        )
        await event_bus.publish(event)

    def _resolve_overall_status(
        self, 
        statuses: List[ResultStatus], 
        has_errors: bool, 
        has_partial_success: bool
    ) -> ResultStatus:
        """
        Synthesizes the overall ResultStatus based on strict precedence, ensuring 
        agent states like WAITING, BLOCKED, or PARTIAL are never falsely marked COMPLETED.
        """
        # 1. Critical blocks and waiting states take precedence for orchestration
        if ResultStatus.WAITING_APPROVAL in statuses:
            return ResultStatus.WAITING_APPROVAL
        if ResultStatus.WAITING_INPUT in statuses:
            return ResultStatus.WAITING_INPUT
        if ResultStatus.BLOCKED in statuses:
            return ResultStatus.BLOCKED

        # 2. Failure vs Partial state evaluation
        if ResultStatus.FAILED in statuses or has_errors:
            return ResultStatus.PARTIAL if has_partial_success else ResultStatus.FAILED

        # 3. Explicit partial completion
        if ResultStatus.PARTIAL in statuses:
            return ResultStatus.PARTIAL

        # 4. Only if no errors, no blocks, and no partials
        return ResultStatus.COMPLETED

    def _create_tool_executor(
        self, 
        task_package: TaskPackage, 
        caller_id: str, 
        runtime_action_log: List[ActionRecord]
    ) -> Callable:
        """
        Creates a securely bound async callback for invoking tools.
        Enforces declared-tool boundaries and cleanly interrupts on APPROVAL_REQUIRED or DENIED.
        """
        async def invoke_tool(tool_name: str, parameters: Optional[Dict[str, Any]] = None) -> Any:
            if tool_name not in task_package.selected_tools:
                error_msg = f"Caller '{caller_id}' attempted to invoke undeclared/unauthorized tool: '{tool_name}'."
                logger.error(error_msg)
                raise ExecutionError(
                    message=error_msg,
                    classification=ErrorClassification.SECURITY_FAILURE
                )

            actual_params = dict(parameters) if parameters is not None else dict(task_package.tool_parameters.get(tool_name, {}))
            
            if task_package.client_scope:
                actual_params.setdefault("client_scope", task_package.client_scope)
            if task_package.project_scope:
                actual_params.setdefault("project_scope", task_package.project_scope)

            try:
                security_decision = await security_manager.evaluate_tool_execution(
                    task_package=task_package,
                    tool_name=tool_name,
                    parameters=actual_params
                )
                
                decision_status = getattr(security_decision, "status", None)
                reason = getattr(security_decision, "reason", "Action blocked by security gate.")

                if decision_status == "DENIED":
                    raise ExecutionError(
                        message=f"Security gate rejected tool execution '{tool_name}': {reason}",
                        classification=ErrorClassification.AUTHORIZATION_FAILURE
                    )
                    
                if decision_status == "APPROVAL_REQUIRED":
                    raise ApprovalRequiredException(
                        approval_id=getattr(security_decision, "approval_id", None),
                        reason=reason
                    )

            except ApprovalRequiredException:
                raise
            except ExecutionError:
                raise
            except Exception as e:
                logger.error(f"Security gate inspection failed for '{tool_name}': {e}", exc_info=True)
                raise ExecutionError(
                    message=f"Security inspection error for tool '{tool_name}': {str(e)}",
                    classification=ErrorClassification.SECURITY_FAILURE
                )

            await self._emit_runtime_event(
                "tool.started", 
                task_package.task_id, 
                {"tool": tool_name, "caller": caller_id}
            )
            start_time = utc_now()

            try:
                result = await tool_registry.execute_tool(tool_name, actual_params)
                duration_ms = (utc_now() - start_time).total_seconds() * 1000.0

                runtime_action_log.append(ActionRecord(
                    task_id=task_package.task_id,
                    agent_id=f"Runtime::{caller_id}",
                    tool_name=tool_name,
                    status="success",
                    timestamp=utc_now(),
                    details={"source": "runtime_authoritative", "duration_ms": duration_ms, "parameters_summary": list(actual_params.keys())}
                ))

                await self._emit_runtime_event(
                    "tool.finished", 
                    task_package.task_id, 
                    {"tool": tool_name, "status": "success", "duration_ms": duration_ms}
                )
                return result

            except Exception as e:
                runtime_action_log.append(ActionRecord(
                    task_id=task_package.task_id,
                    agent_id=f"Runtime::{caller_id}",
                    tool_name=tool_name,
                    status="failure",
                    timestamp=utc_now(),
                    details={"source": "runtime_authoritative", "error": str(e)}
                ))
                await self._emit_runtime_event(
                    "tool.finished", 
                    task_package.task_id, 
                    {"tool": tool_name, "status": "failure", "error": str(e)}
                )

                if isinstance(e, ExecutionError):
                    raise e
                raise ExecutionError(
                    message=f"Tool execution failed for '{tool_name}': {str(e)}",
                    classification=ErrorClassification.UNKNOWN_ERROR
                )

        return invoke_tool

    async def _execute_single_agent(self, agent_id: str, task_package: TaskPackage) -> ResultPackage:
        """Executes a single agent securely, extracting actions and catching approval limits cleanly."""
        agent_instance = agent_registry.get_agent(agent_id)
        if not agent_instance:
            raise ExecutionError(
                message=f"Target agent '{agent_id}' is not registered.",
                classification=ErrorClassification.VALIDATION_FAILURE
            )

        runtime_action_log: List[ActionRecord] = []
        tool_executor = self._create_tool_executor(task_package, agent_id, runtime_action_log)

        await self._emit_runtime_event("agent.started", task_package.task_id, {"agent": agent_id})
        
        try:
            agent_result: ResultPackage = await agent_instance.execute(
                task_package=task_package,
                tool_executor=tool_executor
            )

            # Canonical Action Merging: preserve runtime vs agent boundaries
            for a in agent_result.actions_performed:
                if not a.details: a.details = {}
                a.details["source"] = "agent_semantic"
                
            for a in runtime_action_log:
                agent_result.actions_performed.append(a)

            await self._emit_runtime_event(
                "agent.completed", 
                task_package.task_id, 
                {"agent": agent_id, "status": getattr(agent_result.status, "value", str(agent_result.status))}
            )
            return agent_result

        except ApprovalRequiredException as e:
            await self._emit_runtime_event("agent.paused", task_package.task_id, {"agent": agent_id, "reason": e.reason})
            return ResultPackage(
                status=ResultStatus.WAITING_APPROVAL,
                summary=f"Agent {agent_id} execution paused for mandatory tool approval.",
                findings={},
                actions_performed=runtime_action_log,
                evidence={"approval_id": e.approval_id},
                limitations=[],
                errors=[f"Approval Required: {e.reason}"],
                next_action=""
            )
        except ExecutionError as e:
            # Fatal or Retryable network/provider errors bubble up to Queue for resilience policy
            await self._emit_runtime_event(
                "agent.failed", 
                task_package.task_id, 
                {"agent": agent_id, "error": e.message, "classification": getattr(e.classification, "value", str(e.classification))}
            )
            raise e
        except Exception as e:
            logger.error(f"Unhandled crash in agent '{agent_id}': {str(e)}", exc_info=True)
            await self._emit_runtime_event(
                "agent.failed", 
                task_package.task_id, 
                {"agent": agent_id, "error": str(e), "classification": "UNKNOWN_ERROR"}
            )
            return ResultPackage(
                status=ResultStatus.FAILED,
                summary=f"Agent {agent_id} crashed unexpectedly.",
                findings={},
                actions_performed=runtime_action_log,
                evidence={},
                limitations=[],
                errors=[f"Unhandled crash: {str(e)}"]
            )

    async def _execute_direct_jarvis_tools(self, task_package: TaskPackage) -> ResultPackage:
        """Direct execution path when JARVIS core executes tools without a specialist agent."""
        logger.info(f"Executing direct JARVIS tools for task [{task_package.task_id}]")
        runtime_action_log: List[ActionRecord] = []
        tool_executor = self._create_tool_executor(task_package, "JARVIS", runtime_action_log)
        
        findings: Dict[str, Any] = {}
        errors: List[str] = []
        collected_statuses: List[ResultStatus] = []

        await self._emit_runtime_event("agent.started", task_package.task_id, {"agent": "JARVIS"})

        for tool_name in task_package.selected_tools:
            params = task_package.tool_parameters.get(tool_name, {})
            try:
                tool_output = await tool_executor(tool_name, params)
                findings[tool_name] = tool_output
                collected_statuses.append(ResultStatus.COMPLETED)
            except ApprovalRequiredException as e:
                collected_statuses.append(ResultStatus.WAITING_APPROVAL)
                errors.append(f"[JARVIS Direct] Tool '{tool_name}' requires approval: {e.reason}")
                findings["approval_id"] = e.approval_id
                break
            except ExecutionError as e:
                raise e # Bubble up to TaskQueue
            except Exception as e:
                collected_statuses.append(ResultStatus.FAILED)
                errors.append(f"[JARVIS Direct] Unexpected failure on tool '{tool_name}': {str(e)}")
                break

        final_status = self._resolve_overall_status(
            statuses=collected_statuses, 
            has_errors=bool(errors), 
            has_partial_success=bool(findings)
        )

        await self._emit_runtime_event(
            "agent.completed", 
            task_package.task_id, 
            {"agent": "JARVIS", "status": getattr(final_status, "value", str(final_status))}
        )

        return ResultPackage(
            status=final_status,
            summary=f"JARVIS executed direct tools. Final Status: {getattr(final_status, 'value', str(final_status))}.",
            findings={"JARVIS": findings},
            actions_performed=runtime_action_log,
            evidence={"tool_outputs_count": len(findings)},
            limitations=[],
            errors=errors
        )

    async def execute(self, task_package: TaskPackage) -> ResultPackage:
        """
        Canonical execution entry point. 
        Enforces execution scope, manages sequential execution across specialist agents, 
        and reliably captures runtime boundaries.
        """
        logger.info(f"AgentRuntime executing task [{task_package.task_id}]")
        
        target_agents = list(task_package.target_agents)
        
        # Direct execution fallback when no specific specialist is assigned
        if not target_agents or target_agents == ["JARVIS"]:
            return await self._execute_direct_jarvis_tools(task_package)

        global_findings: Dict[str, Any] = {}
        global_evidence: Dict[str, Any] = {}
        global_limitations: List[str] = []
        global_errors: List[str] = []
        global_statuses: List[ResultStatus] = []
        global_actions: List[ActionRecord] = []

        # Agent Execution Loop
        for agent_id in target_agents:
            res = await self._execute_single_agent(agent_id, task_package)
            
            # Merge Artifacts
            global_findings[agent_id] = res.findings
            global_evidence[agent_id] = res.evidence
            global_limitations.extend(res.limitations)
            global_errors.extend(res.errors)
            global_statuses.append(res.status)
            global_actions.extend(res.actions_performed)

            # Execution Interruption
            if res.status in [ResultStatus.FAILED, ResultStatus.BLOCKED, ResultStatus.WAITING_APPROVAL, ResultStatus.WAITING_INPUT]:
                break

        # Partial success requires at least one agent that actually COMPLETED/PARTIALed.
        # (Presence of a findings key alone would mark a fully-crashed run as PARTIAL.)
        final_status = self._resolve_overall_status(
            statuses=global_statuses, 
            has_errors=bool(global_errors), 
            has_partial_success=any(
                s in (ResultStatus.COMPLETED, ResultStatus.PARTIAL) for s in global_statuses
            )
        )

        return ResultPackage(
            status=final_status,
            summary=f"Runtime executed {len(target_agents)} agent(s). Final Status: {getattr(final_status, 'name', str(final_status))}.",
            findings=global_findings,
            actions_performed=global_actions,
            evidence=global_evidence,
            limitations=global_limitations,
            errors=global_errors
        )


agent_runtime = AgentRuntime()