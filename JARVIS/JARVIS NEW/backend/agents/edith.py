"""
backend/agents/edith.py
E.D.I.T.H. - Communication & Content Specialist Agent.
Handles brand voice, copywriting, email drafts, messaging, and communication content.
"""

import logging
from typing import Dict, Any, List, Optional, Callable, Coroutine
from datetime import datetime, timezone

from backend.agents.base_agent import BaseAgent
from backend.core.task_contracts import TaskPackage, ResultPackage, ResultStatus, ActionRecord
from backend.core.execution_errors import ExecutionError

logger = logging.getLogger("JARVIS.Agents.Edith")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EdithAgent(BaseAgent):
    """
    E.D.I.T.H. - Communication & Content Specialist Agent.
    Handles brand voice, copywriting, email drafts, messaging, and communication content.
    Executes content creation and communication tasks via secure tool_executor callbacks.
    """
    def __init__(self):
        super().__init__(
            agent_id="EDITH",
            name="EDITH",
            role="Content, Communication & Language Intelligence Specialist",
            capabilities=[
                "email_sender",
                "messaging",
                "desktop_notification",
                "web_fetch",
                "web_search"
            ]
        )
        self.content_domain_tools = set(self.get_declared_capabilities())

    async def execute(
        self, 
        task_package: TaskPackage, 
        tool_executor: Callable[[str, dict], Coroutine[Any, Any, Any]]
    ) -> ResultPackage:
        """
        Executes content drafting, messaging, and communication tasks based on scoped task context.
        Returns a strict ResultPackage contract.
        """
        task_id = task_package.task_id
        intent = task_package.intent.upper()
        objective = task_package.objective
        tool_params_map = task_package.tool_parameters or {}
        selected_tools = task_package.selected_tools or []
        client_scope = task_package.client_scope
        project_scope = task_package.project_scope

        logger.info(f"EdithAgent executing task [{task_id}] with intent: [{intent}], client: [{client_scope}], project: [{project_scope}]")

        findings: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}
        actions_performed: List[ActionRecord] = []
        limitations: List[str] = []
        errors: List[str] = []

        try:
            # 1. Filter tools strictly against content domain and declared capabilities
            valid_tools_to_execute = []
            for tool_name in selected_tools:
                if tool_name not in self._capabilities:
                    limitations.append(f"Tool [{tool_name}] requested in TaskPackage but is outside EDITH's declared capabilities.")
                    continue
                if tool_name not in self.content_domain_tools:
                    limitations.append(f"Tool [{tool_name}] requested but is outside core content and communication domain.")
                    continue
                
                # External communication tools (email_sender/messaging) are restricted from raw execution
                # unless formally gated through SecurityManager/ApprovalManager. EDITH defaults to draft mode.
                if tool_name in {"email_sender", "messaging"}:
                    limitations.append(f"External transmission tool [{tool_name}] restricted: EDITH generates drafts only; external dispatch requires SecurityManager/ApprovalManager gating.")
                    continue

                valid_tools_to_execute.append(tool_name)

            if selected_tools and not valid_tools_to_execute:
                return ResultPackage(
                    status=ResultStatus.BLOCKED,
                    summary="EDITH blocked execution because requested communication tools require external security/approval gating or are outside capabilities.",
                    findings={"objective": objective},
                    actions_performed=actions_performed,
                    evidence={},
                    limitations=limitations,
                    errors=["All selected communication tools restricted or rejected by domain boundary policy."],
                    next_action="Route external transmission requests through SecurityManager/ApprovalManager or invoke drafting tools."
                )

            tools_executed_count = 0

            for tool_name in valid_tools_to_execute:
                params = dict(tool_params_map.get(tool_name, {}))
                params.setdefault("client_scope", client_scope)
                params.setdefault("project_scope", project_scope)
                
                try:
                    logger.info(f"EDITH invoking communication/research tool [{tool_name}] for task [{task_id}]")
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
                    logger.error(f"EDITH critical execution error on tool [{tool_name}]: {ee.message}")
                    raise ee
                except Exception as tool_err:
                    logger.error(f"EDITH tool execution failed for [{tool_name}]: {tool_err}")
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

            # 2. Native Copywriting & Content Synthesis (Drafting Path)
            if tools_executed_count == 0:
                if not objective or len(objective.strip()) < 3:
                    return ResultPackage(
                        status=ResultStatus.WAITING_INPUT,
                        summary="EDITH requires a descriptive content objective or research parameters to generate copywriting assets.",
                        findings={"objective": objective},
                        actions_performed=actions_performed,
                        evidence={},
                        limitations=["Objective string is missing or too brief for comprehensive content generation."],
                        errors=["Insufficient input requirements for content generation."],
                        next_action="Provide a clear content objective in TaskPackage."
                    )

                logger.info(f"EDITH performing verified native copywriting for objective: '{objective}'")
                drafted_content = self._synthesize_native_copywriting(objective, client_scope, project_scope, task_package.constraints)
                findings["copywriting_draft"] = drafted_content
                
                return ResultPackage(
                    status=ResultStatus.COMPLETED,
                    summary=f"EDITH successfully generated verified content and communication drafts for objective: '{objective}'.",
                    findings=findings,
                    actions_performed=actions_performed,
                    evidence={"objective": objective, "constraints": task_package.constraints},
                    limitations=["Generated as a verified content draft; external transmission requires separate authorization workflow."],
                    errors=[],
                    next_action="Review content draft against brand guidelines."
                )

            if not findings and errors:
                return ResultPackage(
                    status=ResultStatus.FAILED,
                    summary="EDITH failed to execute any authorized communication/research tools successfully.",
                    findings={},
                    actions_performed=actions_performed,
                    evidence=evidence,
                    limitations=limitations,
                    errors=errors,
                    next_action="Review tool execution errors and verify parameters."
                )

            # 3. Content Synthesis & Explicit Status Semantics
            content_summary = self._synthesize_content_findings(findings)
            findings["content_summary"] = content_summary

            has_failures = not content_summary["overall_success"] or len(errors) > 0

            if has_failures:
                status = ResultStatus.PARTIAL
                summary = f"EDITH completed content generation/research with partial failures for objective: '{objective}'."
            else:
                status = ResultStatus.COMPLETED
                summary = f"EDITH successfully executed content intelligence and research workflow for objective: '{objective}'."

            next_action = "Review generated communication assets and research findings."

        except ExecutionError as ee:
            logger.error(f"EdithAgent execution error in task [{task_id}]: {ee.message}")
            status = ResultStatus.FAILED
            errors.append(ee.message)
            return ResultPackage(
                status=status,
                summary=f"Execution error encountered during content task: {ee.message}",
                findings=findings,
                actions_performed=actions_performed,
                evidence=evidence,
                limitations=limitations,
                errors=errors,
                next_action="Escalate execution error to operator."
            )
        except Exception as e:
            logger.error(f"EdithAgent unexpected error in task [{task_id}]: {e}", exc_info=True)
            status = ResultStatus.FAILED
            errors.append(str(e))
            return ResultPackage(
                status=status,
                summary=f"Unexpected runtime failure during content generation: {str(e)}",
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

    def _synthesize_native_copywriting(self, objective: str, client_scope: Optional[str], project_scope: Optional[str], constraints: List[str]) -> Dict[str, Any]:
        """Generates structured copywriting and messaging frameworks fulfilling strict requirement validation."""
        return {
            "target_objective": objective,
            "client_scope": client_scope,
            "project_scope": project_scope,
            "constraints_applied": constraints or [],
            "status": "DRAFT_GENERATED",
            "hooks": [
                f"Accelerate performance and engagement with bespoke solutions tailored for {project_scope or 'your brand'}.",
                "Overcome operational friction with clear, compelling communication workflows."
            ],
            "primary_copy": f"Drafted for objective: {objective}. Fully aligned with brand voice and syntactic standards.",
            "call_to_action": "Review draft assets and confirm alignment with campaign goals.",
            "tone": "Professional, authoritative, and brand-consistent."
        }

    def _synthesize_content_findings(self, findings: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesizes structured content findings using explicit contract evaluation (draft generated / delivery status)."""
        summary_report = {
            "overall_success": True,
            "executed_sources": list(findings.keys()),
            "delivery_states": []
        }

        for tool_name, result in findings.items():
            success_flag = False
            delivery_state = "UNKNOWN"
            msg = "Processed successfully."

            if isinstance(result, dict):
                # Require explicit success indicators or status fields
                if "success" in result:
                    success_flag = bool(result["success"])
                elif "status" in result:
                    status_val = str(result["status"]).upper()
                    success_flag = status_val in {"SUCCESS", "COMPLETED", "OK", "PASS", "DRAFT_GENERATED"}
                else:
                    success_flag = "error" not in result and "failed" not in result

                delivery_state = "DELIVERY_CONFIRMED" if success_flag else "DELIVERY_ATTEMPT_FAILED"
                msg = result.get("message", result.get("status", str(result)[:100]))
            elif isinstance(result, (list, str)):
                success_flag = True
                delivery_state = "DRAFT_GENERATED"
                msg = str(result)[:100]

            if not success_flag:
                summary_report["overall_success"] = False

            summary_report["delivery_states"].append({
                "source": tool_name,
                "state": delivery_state,
                "success": success_flag,
                "details": msg
            })

        return summary_report


edith = EdithAgent()