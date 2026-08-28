"""
backend/agents/friday.py
F.R.I.D.A.Y. - Performance Marketing & Business Intelligence Specialist Agent.
Handles Meta Ads, Google Ads, Cloudflare analytics, Google services, campaign research, and visual metric inspection.
"""

import logging
from typing import Dict, Any, List, Callable, Coroutine
from datetime import datetime, timezone

from backend.agents.base_agent import BaseAgent
from backend.core.task_contracts import TaskPackage, ResultPackage, ResultStatus, ActionRecord
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Agents.Friday")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FridayAgent(BaseAgent):
    """
    F.R.I.D.A.Y. - Performance Marketing & Business Intelligence Specialist Agent.
    Executes Meta Ads, Google Ads, Cloudflare, Google services, web research, and visual analysis
    via secure tool_executor callbacks, returning strict ResultPackages with evidence-backed findings.
    """
    def __init__(self):
        super().__init__(
            agent_id="FRIDAY",
            name="FRIDAY",
            role="Performance Marketing & Business Intelligence Specialist",
            capabilities=[
                "google_ads_manager",
                "meta_ads_manager",
                "cloudflare_reader",
                "cloudflare_writer",
                "google_services_reader",
                "google_services_writer",
                "web_search",
                "web_fetch",
                "screen_analyzer"
            ]
        )
        # Define allowed marketing domains to prevent blind execution of irrelevant tools
        self.marketing_domain_tools = {
            "google_ads_manager",
            "meta_ads_manager",
            "cloudflare_reader",
            "google_services_reader",
            "web_search",
            "web_fetch",
            "screen_analyzer"
        }

    async def execute(
        self, 
        task_package: TaskPackage, 
        tool_executor: Callable[[str, dict], Coroutine[Any, Any, Any]]
    ) -> ResultPackage:
        """
        Executes marketing intelligence tasks using authorized tools and returns a structured ResultPackage.
        """
        task_id = task_package.task_id
        intent = task_package.intent.upper()
        objective = task_package.objective
        tool_params_map = task_package.tool_parameters or {}
        selected_tools = task_package.selected_tools or []
        client_scope = task_package.client_scope
        project_scope = task_package.project_scope

        logger.info(f"FridayAgent executing task [{task_id}] with intent: [{intent}], client: [{client_scope}]")

        findings: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}
        actions_performed: List[ActionRecord] = []
        limitations: List[str] = []
        errors: List[str] = []

        try:
            # 1. Filter tools strictly against marketing domain and declared capabilities
            valid_tools_to_execute = []
            for tool_name in selected_tools:
                if tool_name not in self._capabilities:
                    limitations.append(f"Tool [{tool_name}] requested in TaskPackage but is outside FRIDAY's declared capabilities.")
                    continue
                if tool_name not in self.marketing_domain_tools and "WRITER" not in tool_name.upper():
                    limitations.append(f"Tool [{tool_name}] requested but is outside core marketing intelligence domain.")
                    continue
                valid_tools_to_execute.append(tool_name)

            tools_executed_count = 0

            for tool_name in valid_tools_to_execute:
                params = tool_params_map.get(tool_name, {"client_scope": client_scope, "project_scope": project_scope})
                
                try:
                    logger.info(f"FRIDAY invoking domain tool [{tool_name}] for task [{task_id}]")
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
                        details=f"Successfully executed domain tool {tool_name} for scope [{client_scope}]"
                    ))
                except Exception as tool_err:
                    logger.error(f"FRIDAY tool execution failed for [{tool_name}]: {tool_err}")
                    errors.append(f"Tool [{tool_name}] failed: {str(tool_err)}")
                    actions_performed.append(ActionRecord(
                        task_id=task_id,
                        agent_id=self.agent_id,
                        tool_name=tool_name,
                        status="FAILED",
                        timestamp=utc_now(),
                        details=str(tool_err)
                    ))

            # 2. Strict Non-Fabrication & Completion Policy
            if tools_executed_count == 0 and not selected_tools:
                limitations.append("No valid marketing tools were selected or authorized in TaskPackage for this execution pass.")
                return ResultPackage(
                    status=ResultStatus.WAITING_INPUT,
                    summary="FRIDAY requires selected marketing tools or explicit data source parameters to perform analysis.",
                    findings={"objective": objective},
                    actions_performed=actions_performed,
                    evidence={},
                    limitations=limitations,
                    errors=["Missing required tool selection for marketing analysis."],
                    next_action="Provide explicit domain-specific selected_tools in TaskPackage."
                )

            if not findings and errors:
                return ResultPackage(
                    status=ResultStatus.FAILED,
                    summary="FRIDAY failed to execute any authorized marketing intelligence tools successfully.",
                    findings={},
                    actions_performed=actions_performed,
                    evidence=evidence,
                    limitations=limitations,
                    errors=errors,
                    next_action="Review tool errors and re-authenticate or adjust parameters."
                )

            # 3. Dynamic Evidence-Based Synthesis & Analysis (Persisting derived metric evidence)
            analyzed_insights, evidence_metrics = self._synthesize_evidence(findings)
            findings["analysis"] = analyzed_insights
            if evidence_metrics:
                evidence["derived_metrics"] = evidence_metrics

            # 4. Status Determination: Require sufficient quantitative/evidence backing before COMPLETED
            has_quantitative_evidence = len(evidence_metrics) > 0
            if errors:
                status = ResultStatus.PARTIAL
            elif not has_quantitative_evidence:
                status = ResultStatus.PARTIAL
                limitations.append("Analysis completed with qualitative findings; quantitative metric backing was limited or unavailable.")
            else:
                status = ResultStatus.COMPLETED

            summary = f"FRIDAY successfully analyzed performance and campaign intelligence for objective: '{objective}'."
            next_action = "Review evidence-backed marketing findings with campaign manager."

        except ExecutionError as ee:
            logger.error(f"FridayAgent execution error in task [{task_id}]: {ee.message}")
            status = ResultStatus.FAILED
            errors.append(ee.message)
            summary = f"Execution error encountered during marketing analysis: {ee.message}"
            next_action = "Escalate execution error to operator."
        except Exception as e:
            logger.error(f"FridayAgent unexpected error in task [{task_id}]: {e}", exc_info=True)
            status = ResultStatus.FAILED
            errors.append(str(e))
            summary = f"Unexpected runtime failure during execution: {str(e)}"
            next_action = "Report system anomaly."

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

    def _synthesize_evidence(self, findings: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Dynamically analyzes normalized raw findings to generate real, evidence-backed insights,
        calculating performance metrics (CTR, CPC, CPA, ROAS) and returning them for evidence persistence.
        """
        insights = {
            "performance_diagnosis": [],
            "identified_risks": [],
            "actionable_recommendations": []
        }
        evidence_extracted = {}

        for tool_name, result in findings.items():
            if not isinstance(result, dict):
                continue
            
            metrics = result.get("metrics", result.get("data", {}))
            if isinstance(metrics, dict) and metrics:
                spend = float(metrics.get("spend", metrics.get("cost", 0.0)))
                conversions = float(metrics.get("conversions", metrics.get("goals", 0.0)))
                clicks = float(metrics.get("clicks", 0.0))
                impressions = float(metrics.get("impressions", 0.0))
                revenue = float(metrics.get("revenue", metrics.get("value", 0.0)))

                ctr = (clicks / impressions) * 100 if impressions > 0 else 0.0
                cpc = spend / clicks if clicks > 0 else 0.0
                cpa = spend / conversions if conversions > 0 else 0.0
                roas = revenue / spend if spend > 0 else 0.0

                evidence_extracted[tool_name] = {
                    "spend": spend,
                    "conversions": conversions,
                    "clicks": clicks,
                    "impressions": impressions,
                    "revenue": revenue,
                    "derived_ctr": round(ctr, 2),
                    "derived_cpc": round(cpc, 2),
                    "derived_cpa": round(cpa, 2),
                    "derived_roas": round(roas, 2)
                }

                if spend > 0 and conversions == 0:
                    insights["identified_risks"].append(f"[{tool_name}] Campaign is incurring spend (${spend}) with zero recorded conversions.")
                    insights["actionable_recommendations"].append(f"Pause or reallocate budget from non-converting campaigns in [{tool_name}].")
                elif roas > 0:
                    insights["performance_diagnosis"].append(f"[{tool_name}] Operating with a measured ROAS of {round(roas, 2)}.")
                    if roas < 1.0:
                        insights["actionable_recommendations"].append(f"Optimize targeting in [{tool_name}] to improve ROAS above break-even (1.0).")
                    else:
                        insights["actionable_recommendations"].append(f"Scale budget incrementally on profitable segments in [{tool_name}] yielding ROAS > {round(roas, 2)}.")
            else:
                insights["performance_diagnosis"].append(f"[{tool_name}] Data retrieved successfully; qualitative evaluation recorded.")

        if not insights["actionable_recommendations"]:
            insights["actionable_recommendations"].append("Monitor campaign performance metrics weekly against historical baselines.")

        return insights, evidence_extracted


friday = FridayAgent()