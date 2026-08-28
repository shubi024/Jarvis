"""
backend/agents/plato.py
P.L.A.T.O. - Operations & Administrative Specialist Agent.
Handles file organization, task tracking, SOP management, and operational workflows.
"""

import logging
from typing import Dict, Any, List, Callable, Coroutine
from datetime import datetime, timezone

from backend.agents.base_agent import BaseAgent
from backend.core.task_contracts import TaskPackage, ResultPackage, ResultStatus, ActionRecord
from backend.core.execution_errors import ExecutionError

logger = logging.getLogger("JARVIS.Agents.Plato")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlatoAgent(BaseAgent):
    """
    P.L.A.T.O. - Operations & Administrative Specialist Agent.
    Handles file organization, task tracking, SOP management, and operational workflows.
    Executes operational and administrative tasks via secure tool_executor callbacks.
    """
    def __init__(self):
        super().__init__(
            agent_id="PLATO",
            name="PLATO",
            role="Operations & Administrative Specialist",
            capabilities=[
                "file_manager_reader",
                "file_manager_writer",
                "file_reader",
                "file_writer",
                "file_editor",
                "code_executor",
                "test_runner",
                "code_analyzer",
                "system_info",
                "window_reader",
                "window_writer",
                "computer_macro"
            ]
        )
        self.operational_domain_tools = set(self.get_declared_capabilities())

    async def execute(
        self, 
        task_package: TaskPackage, 
        tool_executor: Callable[[str, dict], Coroutine[Any, Any, Any]]
    ) -> ResultPackage:
        """
        Executes operational and administrative tasks based on scoped task context.
        Returns a strict ResultPackage contract.
        """
        task_id = task_package.task_id
        intent = task_package.intent.upper()
        objective = task_package.objective
        tool_params_map = task_package.tool_parameters or {}
        selected_tools = task_package.selected_tools or []
        client_scope = task_package.client_scope
        project_scope = task_package.project_scope

        logger.info(f"PlatoAgent executing task [{task_id}] with intent: [{intent}], client: [{client_scope}], project: [{project_scope}]")

        findings: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}
        actions_performed: List[ActionRecord] = []
        limitations: List[str] = []
        errors: List[str] = []

        try:
            # 1. Filter tools strictly against operational domain and declared capabilities
            valid_tools_to_execute = []
            for tool_name in selected_tools:
                if tool_name not in self._capabilities:
                    limitations.append(f"Tool [{tool_name}] requested in TaskPackage but is outside PLATO's declared capabilities.")
                    continue
                if tool_name not in self.operational_domain_tools:
                    limitations.append(f"Tool [{tool_name}] requested but is outside core operations domain.")
                    continue
                valid_tools_to_execute.append(tool_name)

            # Handle case where tools were requested but all were rejected as invalid/out-of-domain
            if selected_tools and not valid_tools_to_execute:
                return ResultPackage(
                    status=ResultStatus.BLOCKED,
                    summary="PLATO blocked execution because all requested tools are outside authorized capabilities or domain.",
                    findings={"objective": objective},
                    actions_performed=actions_performed,
                    evidence={},
                    limitations=limitations,
                    errors=["All selected tools rejected by domain boundary validation."],
                    next_action="Review task tool selection against PLATO capabilities."
                )

            tools_executed_count = 0

            for tool_name in valid_tools_to_execute:
                params = dict(tool_params_map.get(tool_name, {}))
                params.setdefault("client_scope", client_scope)
                params.setdefault("project_scope", project_scope)
                
                try:
                    logger.info(f"PLATO invoking operational tool [{tool_name}] for task [{task_id}]")
                    tool_result = await self.invoke_tool_securely(tool_name, params, task_package, tool_executor)
                    
                    findings[tool_name] = tool_result
                    evidence[f"{tool_name}_raw"] = tool_result
                    tools_executed_count += 1

                    actions_performed.append(ActionRecord(
                        task_id=task_id,
                        agent_id=self.agent_id,
                        tool_name=tool_name,
                        status="SUCCESS",
                        timestamp=utc_now(),
                        details={
                            "action": "execute_tool",
                            "tool": tool_name,
                            "scope": f"{client_scope}/{project_scope}",
                            "result_summary": str(tool_result)[:200]
                        }
                    ))
                except ExecutionError as ee:
                    logger.error(f"PLATO critical execution error on tool [{tool_name}]: {ee.message}")
                    raise ee
                except Exception as tool_err:
                    logger.error(f"PLATO tool execution failed for [{tool_name}]: {tool_err}")
                    errors.append(f"Tool [{tool_name}] failed: {str(tool_err)}")
                    actions_performed.append(ActionRecord(
                        task_id=task_id,
                        agent_id=self.agent_id,
                        tool_name=tool_name,
                        status="FAILED",
                        timestamp=utc_now(),
                        details={
                            "action": "execute_tool",
                            "tool": tool_name,
                            "error": str(tool_err)
                        }
                    ))

            # 2. Strict Non-Fabrication & Input Validation Policy
            if tools_executed_count == 0 and not selected_tools:
                limitations.append("No valid operational tools were selected or authorized in TaskPackage for this execution pass.")
                return ResultPackage(
                    status=ResultStatus.WAITING_INPUT,
                    summary="PLATO requires selected operational tools or explicit workspace parameters to perform administration tasks.",
                    findings={"objective": objective},
                    actions_performed=actions_performed,
                    evidence={},
                    limitations=limitations,
                    errors=["Missing required tool selection for operational execution."],
                    next_action="Provide explicit domain-specific selected_tools in TaskPackage."
                )

            if not findings and errors:
                return ResultPackage(
                    status=ResultStatus.FAILED,
                    summary="PLATO failed to execute any authorized operational tools successfully.",
                    findings={},
                    actions_performed=actions_performed,
                    evidence=evidence,
                    limitations=limitations,
                    errors=errors,
                    next_action="Review tool execution errors and verify workspace file paths or permissions."
                )

            if tools_executed_count > 0 and not findings:
                return ResultPackage(
                    status=ResultStatus.PARTIAL,
                    summary="PLATO attempted tool execution but no verifiable findings were returned.",
                    findings={"objective": objective},
                    actions_performed=actions_performed,
                    evidence=evidence,
                    limitations=limitations,
                    errors=errors or ["Zero findings returned from tool execution."],
                    next_action="Verify target resource state and re-attempt execution."
                )

            # 3. Operational Synthesis with Defensive Tool Success Validation
            operational_summary = self._synthesize_operations(findings)
            findings["operations_summary"] = operational_summary

            # Check if any synthesized operation reported a definitive failure
            has_operational_failures = not operational_summary["overall_success"] or len(errors) > 0

            if has_operational_failures:
                status = ResultStatus.PARTIAL
                summary = f"PLATO completed operations with partial failures or unverified outcomes for objective: '{objective}'."
            else:
                status = ResultStatus.COMPLETED
                summary = f"PLATO successfully executed operational workflow for objective: '{objective}'."

            next_action = "Review operational execution evidence and artifacts."

        except ExecutionError as ee:
            logger.error(f"PlatoAgent execution error in task [{task_id}]: {ee.message}")
            status = ResultStatus.FAILED
            errors.append(ee.message)
            return ResultPackage(
                status=status,
                summary=f"Execution error encountered during operational task: {ee.message}",
                findings=findings,
                actions_performed=actions_performed,
                evidence=evidence,
                limitations=limitations,
                errors=errors,
                next_action="Escalate execution error to operator."
            )
        except Exception as e:
            logger.error(f"PlatoAgent unexpected error in task [{task_id}]: {e}", exc_info=True)
            status = ResultStatus.FAILED
            errors.append(str(e))
            return ResultPackage(
                status=status,
                summary=f"Unexpected runtime failure during operations: {str(e)}",
                findings=findings,
                actions_performed=actions_performed,
                evidence=evidence,
                limitations=limitations,
                errors=errors,
                next_action="Report system anomaly."
            )

        return ResultPackage(
            status=status,
            summary=summary,
            findings=findings,
            actions_performed=actions_performed,
            evidence=evidence,
            limitations=limitations,
            errors=errors,
            next_action=next_action
        )

    def _synthesize_operations(self, findings: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesizes structured operational findings and enforces defensive success verification."""
        summary_report = {
            "overall_success": True,
            "completed_operations": list(findings.keys()),
            "status_notes": []
        }

        for tool_name, result in findings.items():
            success_flag = False
            msg = "Executed successfully."

            if isinstance(result, dict):
                # Defensively require explicit positive success indicators rather than assuming True
                if "success" in result:
                    success_flag = bool(result["success"])
                elif "status" in result:
                    status_val = str(result["status"]).upper()
                    success_flag = status_val in {"SUCCESS", "COMPLETED", "OK", "PASS"}
                else:
                    # If result dict exists without explicit error keys, treat as conditionally valid evidence
                    success_flag = "error" not in result and "failed" not in result

                msg = result.get("message", result.get("status", str(result)[:100]))
            elif isinstance(result, (list, str, int, float)):
                success_flag = True
                msg = str(result)[:100]

            if not success_flag:
                summary_report["overall_success"] = False

            summary_report["status_notes"].append({
                "tool": tool_name,
                "success": success_flag,
                "details": msg
            })

        return summary_report


plato = PlatoAgent()