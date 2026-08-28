"""
backend/agents/veronica.py
V.E.R.O.N.I.C.A. - Creative Intelligence & Visual Design Specialist Agent.
Handles brand assets, visual guidelines, creative directions, design references, and screen observation.
"""

import logging
from typing import Dict, Any, List, Callable, Coroutine
from datetime import datetime, timezone

from backend.agents.base_agent import BaseAgent
from backend.core.task_contracts import TaskPackage, ResultPackage, ResultStatus, ActionRecord
from backend.core.execution_errors import ExecutionError

logger = logging.getLogger("JARVIS.Agents.Veronica")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VeronicaAgent(BaseAgent):
    """
    V.E.R.O.N.I.C.A. - Creative Intelligence & Visual Design Specialist Agent.
    Handles brand assets, visual guidelines, creative directions, design references, and screen observation.
    Executes creative and visual inspection tasks via secure tool_executor callbacks.
    """
    def __init__(self):
        super().__init__(
            agent_id="VERONICA",
            name="VERONICA",
            role="Creative Intelligence & Visual Design Specialist",
            capabilities=[
                "browser_control",
                "file_manager_reader",
                "file_reader",
                "web_search",
                "web_fetch",
                "screen_capture",
                "screen_analyzer"
            ]
        )
        self.creative_domain_tools = set(self.get_declared_capabilities())

    async def execute(
        self, 
        task_package: TaskPackage, 
        tool_executor: Callable[[str, dict], Coroutine[Any, Any, Any]]
    ) -> ResultPackage:
        """
        Executes creative design and visual inspection tasks based on scoped task context.
        Returns a strict ResultPackage contract.
        """
        task_id = task_package.task_id
        intent = task_package.intent.upper()
        objective = task_package.objective
        tool_params_map = task_package.tool_parameters or {}
        selected_tools = task_package.selected_tools or []
        client_scope = task_package.client_scope
        project_scope = task_package.project_scope

        logger.info(f"VeronicaAgent executing task [{task_id}] with intent: [{intent}], client: [{client_scope}], project: [{project_scope}]")

        findings: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}
        actions_performed: List[ActionRecord] = []
        limitations: List[str] = []
        errors: List[str] = []

        try:
            # 1. Filter tools strictly against creative domain and declared capabilities
            valid_tools_to_execute = []
            for tool_name in selected_tools:
                if tool_name not in self._capabilities:
                    limitations.append(f"Tool [{tool_name}] requested in TaskPackage but is outside VERONICA's declared capabilities.")
                    continue
                if tool_name not in self.creative_domain_tools:
                    limitations.append(f"Tool [{tool_name}] requested but is outside core creative design domain.")
                    continue
                valid_tools_to_execute.append(tool_name)

            if selected_tools and not valid_tools_to_execute:
                return ResultPackage(
                    status=ResultStatus.BLOCKED,
                    summary="VERONICA blocked execution because all requested tools are outside authorized creative capabilities or domain.",
                    findings={"objective": objective},
                    actions_performed=actions_performed,
                    evidence={},
                    limitations=limitations,
                    errors=["All selected tools rejected by creative domain boundary validation."],
                    next_action="Review task tool selection against VERONICA capabilities."
                )

            tools_executed_count = 0

            for tool_name in valid_tools_to_execute:
                params = dict(tool_params_map.get(tool_name, {}))
                params.setdefault("client_scope", client_scope)
                params.setdefault("project_scope", project_scope)
                
                try:
                    logger.info(f"VERONICA invoking creative/visual tool [{tool_name}] for task [{task_id}]")
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
                    logger.error(f"VERONICA critical execution error on tool [{tool_name}]: {ee.message}")
                    raise ee
                except Exception as tool_err:
                    logger.error(f"VERONICA tool execution failed for [{tool_name}]: {tool_err}")
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

            # 2. Strict Input Validation & Non-Fabrication Policy
            if tools_executed_count == 0 and not selected_tools:
                limitations.append("No valid creative/visual tools were selected or authorized in TaskPackage for this execution pass.")
                return ResultPackage(
                    status=ResultStatus.WAITING_INPUT,
                    summary="VERONICA requires selected visual tools or screen inspection parameters to perform design analysis.",
                    findings={"objective": objective},
                    actions_performed=actions_performed,
                    evidence={},
                    limitations=limitations,
                    errors=["Missing required tool selection for creative execution."],
                    next_action="Provide explicit domain-specific selected_tools in TaskPackage."
                )

            if not findings and errors:
                return ResultPackage(
                    status=ResultStatus.FAILED,
                    summary="VERONICA failed to execute any authorized creative tools successfully.",
                    findings={},
                    actions_performed=actions_performed,
                    evidence=evidence,
                    limitations=limitations,
                    errors=errors,
                    next_action="Review tool execution errors and verify screen assets or browser references."
                )

            if tools_executed_count > 0 and not findings:
                return ResultPackage(
                    status=ResultStatus.PARTIAL,
                    summary="VERONICA attempted tool execution but no verifiable visual findings were returned.",
                    findings={"objective": objective},
                    actions_performed=actions_performed,
                    evidence=evidence,
                    limitations=limitations,
                    errors=errors or ["Zero findings returned from visual tool execution."],
                    next_action="Verify target visual asset availability and re-attempt."
                )

            # 3. Evidence-Driven Creative Synthesis & Visual Evaluation
            creative_analysis = self._synthesize_creative_findings(findings)
            findings["creative_analysis"] = creative_analysis

            has_visual_failures = not creative_analysis["overall_success"] or len(errors) > 0
            has_genuine_visual_evidence = creative_analysis["visual_evidence_verified"]

            if has_visual_failures:
                status = ResultStatus.PARTIAL
                summary = f"VERONICA completed creative analysis with partial failures or unverified visual assets for objective: '{objective}'."
            elif not has_genuine_visual_evidence:
                status = ResultStatus.PARTIAL
                limitations.append("Completed tool execution, but lacked verified visual structure or screen layout evidence to confirm full design analysis.")
                summary = f"VERONICA performed preliminary design checks, but requires direct visual inspection data for complete evaluation."
            else:
                status = ResultStatus.COMPLETED
                summary = f"VERONICA successfully performed evidence-driven creative intelligence and visual evaluation for objective: '{objective}'."

            next_action = "Review evidence-backed visual design findings and recommendations."

        except ExecutionError as ee:
            logger.error(f"VeronicaAgent execution error in task [{task_id}]: {ee.message}")
            status = ResultStatus.FAILED
            errors.append(ee.message)
            return ResultPackage(
                status=status,
                summary=f"Execution error encountered during creative task: {ee.message}",
                findings=findings,
                actions_performed=actions_performed,
                evidence=evidence,
                limitations=limitations,
                errors=errors,
                next_action="Escalate execution error to operator."
            )
        except Exception as e:
            logger.error(f"VeronicaAgent unexpected error in task [{task_id}]: {e}", exc_info=True)
            status = ResultStatus.FAILED
            errors.append(str(e))
            return ResultPackage(
                status=status,
                summary=f"Unexpected runtime failure during creative analysis: {str(e)}",
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

    def _synthesize_creative_findings(self, findings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesizes structured creative intelligence findings by parsing actual visual observations,
        composition attributes, and detected UI/layout elements rather than injecting hardcoded checklists.
        """
        summary_report = {
            "overall_success": True,
            "visual_evidence_verified": False,
            "evaluated_assets": list(findings.keys()),
            "visual_observations": [],
            "evidence_driven_recommendations": []
        }

        for tool_name, result in findings.items():
            success_flag = False
            has_visual_data = False

            if isinstance(result, dict):
                if "success" in result:
                    success_flag = bool(result["success"])
                else:
                    success_flag = "error" not in result and "failed" not in result

                # Parse real visual inspection keys if returned by screen_analyzer or image tools
                visual_metrics = result.get("visual_metrics", result.get("analysis", result.get("data", {})))
                if isinstance(visual_metrics, dict) and visual_metrics:
                    has_visual_data = True
                    summary_report["visual_evidence_verified"] = True

                    # Extract dynamic visual observations
                    contrast = visual_metrics.get("contrast_ratio", visual_metrics.get("contrast"))
                    cta_visible = visual_metrics.get("cta_visible", visual_metrics.get("call_to_action"))
                    typography = visual_metrics.get("typography", visual_metrics.get("font_hierarchy"))
                    layout_notes = visual_metrics.get("layout_notes", visual_metrics.get("composition"))

                    if contrast is not None:
                        summary_report["visual_observations"].append(f"[{tool_name}] Measured contrast ratio: {contrast}.")
                        if str(contrast).lower() in {"low", "poor"} or (isinstance(contrast, (int, float)) and contrast < 4.5):
                            summary_report["evidence_driven_recommendations"].append(f"[{tool_name}] Improve text/background contrast ratio to meet accessibility standards.")

                    if cta_visible is not None:
                        summary_report["visual_observations"].append(f"[{tool_name}] Call-to-action visibility status: {cta_visible}.")
                        if str(cta_visible).lower() in {"false", "hidden", "unclear", "low"}:
                            summary_report["evidence_driven_recommendations"].append(f"[{tool_name}] Increase CTA prominence and placement hierarchy.")

                    if typography:
                        summary_report["visual_observations"].append(f"[{tool_name}] Typography hierarchy notes: {typography}.")

                    if layout_notes:
                        summary_report["visual_observations"].append(f"[{tool_name}] Layout composition notes: {layout_notes}.")
                else:
                    # Handle general unstructured text/file results
                    summary_report["visual_observations"].append(f"[{tool_name}] Tool executed; content reviewed qualitatively: {str(result)[:150]}")
            elif isinstance(result, (list, str)):
                success_flag = True
                summary_report["visual_observations"].append(f"[{tool_name}] Asset text data reviewed: {str(result)[:150]}")

            if not success_flag:
                summary_report["overall_success"] = False

        if not summary_report["evidence_driven_recommendations"] and summary_report["visual_evidence_verified"]:
            summary_report["evidence_driven_recommendations"].append("Visual elements verified successfully; maintain current composition consistency.")
        elif not summary_report["visual_evidence_verified"]:
            summary_report["evidence_driven_recommendations"].append("Provide direct visual screen or asset inspection tools to generate evidence-backed design recommendations.")

        return summary_report


veronica = VeronicaAgent()