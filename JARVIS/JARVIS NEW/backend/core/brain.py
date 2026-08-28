"""
backend/core/brain.py
Full Final J.A.R.V.I.S. Orchestration Brain.
Coordinates canonical context retrieval, structured LLM planning, Workflow/TaskPackage construction,
payload-level security evaluation, queue submission, and intelligent result synthesis based on verified evidence.
"""

import uuid
import logging
import json
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

# Infrastructure & Subsystem Imports
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.infrastructure.api_engine import api_engine
from backend.memory.memory_manager import memory_manager, MemoryCategory
from backend.core.context_manager import context_manager
from backend.security.security_manager import security_manager
from backend.core.task_queue import task_queue
from backend.core.agent_registration import agent_registry
from backend.tools.tool_registry import tool_registry

# Canonical Contracts
from backend.core.task_contracts import (
    TaskPackage,
    SubtaskPackage,
    ResultPackage,
    ResultStatus,
    TaskStatus,
    ApprovalState,
    VerificationContract,
    ExpectedOutput,
    PermissionScope,
    TaskDependency,
    ExecutionMetadata,
)
from backend.core.workflow_contracts import (
    WorkflowDefinition, WorkflowStatus, ExecutionGroup, FailureRule,
    SynthesisStage, AgentAssignment, WorkflowMetadata,
)
from backend.core.execution_errors import ExecutionError, ErrorClassification
from backend.core.json_utils import extract_json_object, strip_code_fences

logger = logging.getLogger("JARVIS.Core.Brain")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# -------------------------------------------------------------------------
# Pydantic Structured Planning Schemas for APIEngine LLM Interaction
# -------------------------------------------------------------------------

class SubtaskPlan(BaseModel):
    # Defaults keep subtask parsing resilient when smaller LLM providers omit
    # required keys (observed in production: 9x ValidationError on a single plan).
    subtask_id: str = ""
    target_agent: str = "JARVIS"
    intent: str = "EXECUTE_OBJECTIVE"
    objective: str = ""
    selected_tools: List[str] = Field(default_factory=list)
    tool_parameters: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list)
    verification_type: str = "STATE_VERIFICATION"
    method: str = "TOOL_CHECK"
    expected_outcome: str = "Successful subtask execution"
    required_evidence: List[str] = Field(default_factory=list)

class JarvisPlanningOutput(BaseModel):
    # Defaults keep plan parsing resilient when smaller LLM providers omit fields
    # despite the strict-JSON instruction; downstream logic still behaves sanely.
    task_classification: str = Field(
        default="SIMPLE_TASK",
        description="CONVERSATION, INFORMATIONAL, SIMPLE_TASK, or COMPLEX_WORKFLOW"
    )
    intent: str = "UNKNOWN"
    objective: str = ""
    requires_execution: bool = True
    missing_information: Optional[str] = None
    target_agents: List[str] = Field(default_factory=list)
    agent_selection_rationale: str = Field(
        default="Defaulted: provider omitted rationale.",
        description="Explanation of capability matching and least-privilege."
    )
    selected_tools: List[str] = Field(default_factory=list)
    tool_parameters: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    subtasks: List[SubtaskPlan] = Field(default_factory=list)

    @field_validator("subtasks", mode="before")
    @classmethod
    def _normalize_subtasks(cls, value: Any) -> List[Dict[str, Any]]:
        """
        Smaller providers frequently emit subtasks with alternative key names
        ("description"/"action"/"title" for the objective, "agent"/"agent_id"
        for the target) or omit keys entirely. Normalize before validation
        instead of rejecting the entire plan.
        """
        if not isinstance(value, list):
            return []
        alias_map = {
            "objective": ("description", "action", "task", "title", "goal", "details"),
            "target_agent": ("agent", "agent_id", "assigned_agent", "assigned_to", "owner"),
            "intent": ("action_type", "type", "intent_name"),
        }
        normalized: List[Dict[str, Any]] = []
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                # Scalar strings are treated as plain objectives.
                if isinstance(item, str) and item.strip():
                    normalized.append({
                        "subtask_id": f"subtask_{idx + 1}",
                        "target_agent": "JARVIS",
                        "intent": "EXECUTE_OBJECTIVE",
                        "objective": item.strip(),
                    })
                continue
            entry = dict(item)
            for canonical, aliases in alias_map.items():
                if not str(entry.get(canonical, "") or "").strip():
                    for alias in aliases:
                        alias_val = entry.get(alias)
                        if isinstance(alias_val, str) and alias_val.strip():
                            entry[canonical] = alias_val.strip()
                            break
            if not str(entry.get("subtask_id", "") or "").strip():
                entry["subtask_id"] = f"subtask_{idx + 1}"
            normalized.append(entry)
        return normalized

    @field_validator("tool_parameters", mode="before")
    @classmethod
    def _coerce_tool_parameters(cls, value: Any) -> Dict[str, Dict[str, Any]]:
        """
        Smaller providers frequently emit flat/scalar tool parameters
        (e.g. {"action": "DESCRIBE_CONTENT"}). Coerce every value into a
        dict so the canonical Dict[str, Dict[str, Any]] contract holds.
        """
        if not isinstance(value, dict):
            return {}
        coerced: Dict[str, Dict[str, Any]] = {}
        for tool_name, params in value.items():
            if isinstance(params, dict):
                coerced[tool_name] = params
            elif isinstance(params, (list, tuple)):
                coerced[tool_name] = {"values": list(params)}
            elif params is None:
                coerced[tool_name] = {}
            else:
                coerced[tool_name] = {"value": params}
        return coerced
    verification_type: str = "OUTPUT_CHECK"
    method: str = "LLM_EVALUATION"
    expected_outcome: str = "Successful task completion"
    required_evidence: List[str] = Field(default_factory=list)
    initial_conversational_response: str = ""
    allow_global_memory_fallback: bool = False

class JarvisBrain:
    """
    Canonical Orchestrator Brain for J.A.R.V.I.S.
    Enforces centralized cognition, least-privilege agent selection, payload-level security gating,
    canonical workflow generation, and evidence-verified response synthesis.
    """

    JARVIS_CORE_TOOLS = {
        "text_to_speech", "speech_to_text", "wake_word_control", 
        "screen_capture", "screen_analyzer", "system_info"
    }

    SYSTEM_PROMPT = """You are J.A.R.V.I.S., the centralized orchestration intelligence.
Your role is to understand user commands using provided context/memory, formulate deterministic execution plans,
and assign work to the smallest capable specialist agent per the locked master specifications:
- FRIDAY: Performance Marketing & Business Intelligence — Meta Ads, Google Ads, GA4/GTM analytics,
  campaign strategy, targeting, funnels, business data and growth analysis.
- PLATO: Life & Work Operations & Execution Intelligence — projects, tasks/subtasks, deadlines,
  milestones, SOPs, file/folder organization and operational tracking. PLATO never commands other agents.
- VERONICA: Creative Intelligence & Visual Design — branding, social/ad creatives, visual concepts,
  design assets, creative direction and trend research.
- EDITH: Content, Communication & Language Intelligence — copywriting, SEO content, captions,
  ad copy, brand messaging, emails and business communication drafts.
- JARVIS: Direct conversation, informational queries, core system/voice/screen actions, or orchestration.

If the task requires multiple agents or steps, classify as COMPLEX_WORKFLOW and generate dependencies in `subtasks`.
Independent subtasks MUST NOT declare dependencies on each other so they can run in PARALLEL groups;
dependent subtasks must declare the exact `subtask_id`s they wait on.
Choose agents based on strict capability matching, task complexity, and least-privilege specialization.

Respond STRICTLY with a valid JSON object matching the `JarvisPlanningOutput` schema.
Your ENTIRE reply must be exactly one JSON object with EXACTLY these top-level keys and types:
{
  "task_classification": "CONVERSATION | INFORMATIONAL | SIMPLE_TASK | COMPLEX_WORKFLOW",
  "intent": "<short SCREAMING_SNAKE_CASE intent>",
  "objective": "<restated objective>",
  "requires_execution": true | false,
  "missing_information": null | "<what is missing>",
  "target_agents": ["<agent ids>"],
  "agent_selection_rationale": "<explanation of capability matching and least-privilege>",
  "selected_tools": ["<tool names>"],
  "tool_parameters": {},
  "subtasks": [],
  "verification_type": "OUTPUT_CHECK",
  "method": "LLM_EVALUATION | TOOL_CHECK | STATE_COMPARISON",
  "expected_outcome": "<expected outcome>",
  "required_evidence": [],
  "initial_conversational_response": "<the conversational reply shown to the user right now>",
  "allow_global_memory_fallback": false
}
All listed keys are REQUIRED. Never omit any key. No markdown blocks, no backticks, no extra text.
"""

    SYNTHESIS_PROMPT = """You are J.A.R.V.I.S. Synthesize the final response based strictly on VERIFIED execution evidence.
If there are multiple subtask results, resolve conflicts and combine them logically.
Identify specific, verified facts or learnings that should be committed to long-term memory. Do not hallucinate learnings without evidence.
Respond strictly in JSON:
{
    "final_response_text": "The conversational reply to the user.",
    "verified_learnings": "A concise string of factual, verified learnings to store in memory (or null if none supported by evidence)."
}
"""

    CONVERSATION_SYSTEM_PROMPT = """You are J.A.R.V.I.S., a warm, capable, companion-like AI assistant.
The user is speaking conversationally. Address them directly (call them "sir"); reply in character and be concise.
NEVER answer in JSON. Do not mention tasks, tools, agents, or systems.
Do not narrate, quote, or describe the user's message — respond straight to them in the first person.
Handle casual, incomplete, and contextually dependent statements gracefully.
If the user clearly wants an action/tool/service performed (opening apps, screenshot, web search, sending files, controlling the computer, etc.), reply with exactly the word: TASK
Otherwise, just give your natural conversational reply as plain text.
"""

    async def process_command(
        self, 
        db: AsyncSession, 
        user_text: str,
        requester: str = "human_user",
        client_scope: Optional[str] = None,
        project_scope: Optional[str] = None,
        session_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Main J.A.R.V.I.S. Brain Orchestration Pipeline.
        """
        logger.info(f"Brain received user command: '{user_text}' [Requester: {requester}, Scope: {client_scope}/{project_scope}]")

        if not user_text or not user_text.strip():
            return {"response": "Sir, the command payload is empty.", "status": ResultStatus.FAILED.value}

        await memory_manager.save_message(
            db=db, sender=requester, text=user_text, session_id=session_id,
            client_scope=client_scope, project_scope=project_scope, user_id=requester
        )

        # FAST PATH — conversation-first routing.
        # Pure conversational, informational, or simple deterministic requests
        # (greetings, thanks, status, time, date, casual chat) are serviced here
        # with a single lightweight exchange, returning immediately. They must NOT
        # build heavy canonical context, invoke the planner, create tasks, or hit
        # the security/observation machinery. This is what makes JARVIS feel like
        # a fast companion rather than a task system.
        fast_reply = await self._fast_path_reply(user_text)
        if fast_reply:
            await memory_manager.save_message(
                db=db, sender="JARVIS", text=fast_reply, session_id=session_id,
                client_scope=client_scope, project_scope=project_scope, user_id=requester
            )
            logger.debug("Brain served request via conversation fast path.")
            return {"response": fast_reply, "status": ResultStatus.COMPLETED.value, "mode": "conversation"}

        # 1. Build Canonical Context using ContextManager
        task_context = await context_manager.build_context(
            db=db,
            task_id=f"plan_{uuid.uuid4().hex[:8]}",
            intent="PLANNING",
            target_agents=[],
            user_text=user_text,
            client_scope=client_scope,
            project_scope=project_scope
        )

        # 2. Formulate Cognitive Plan
        plan = await self._understand_and_plan(user_text, task_context)
        
        if not plan:
            error_msg = ("Sir, I received your request, but the planning engine returned no usable "
                         "response from the active AI provider. I have logged the incident. Please try "
                         "a more specific command, or say 'Wake up, JARVIS' to refresh the session.")
            logger.warning("Cognitive planning produced no usable plan: the provider response was empty, non-parseable, or schema-invalid.")
            await memory_manager.save_message(db, sender="JARVIS", text=error_msg, session_id=session_id)
            return {"response": error_msg, "status": ResultStatus.FAILED.value}

        # 3. Handle Ambiguity
        if plan.missing_information:
            logger.info(f"Brain detected missing information: {plan.missing_information}")
            response_msg = f"{plan.initial_conversational_response} I require additional details: {plan.missing_information}"
            await memory_manager.save_message(db, sender="JARVIS", text=response_msg, session_id=session_id)
            return {"response": response_msg, "status": ResultStatus.WAITING_INPUT.value}

        # 4. Direct Conversational Handling
        if not plan.requires_execution or plan.task_classification in {"CONVERSATION", "INFORMATIONAL"}:
            await memory_manager.save_message(db, sender="JARVIS", text=plan.initial_conversational_response, session_id=session_id)
            return {"response": plan.initial_conversational_response, "status": ResultStatus.COMPLETED.value}

        # 5. Validate Capability Assignments
        validation_error = self._validate_and_normalize_plan(plan)
        if validation_error:
            logger.error(f"Plan validation rejected: {validation_error}")
            await memory_manager.save_message(db, sender="JARVIS", text=validation_error, session_id=session_id)
            return {"response": validation_error, "status": ResultStatus.FAILED.value}

        # 6. Construct Executable Payload (Canonical TaskPackage or WorkflowDefinition)
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        executable_payload = self._build_executable_payload(task_id, plan, requester, client_scope, project_scope)

        # 6b. Persist the task record BEFORE orchestration-time security gating.
        # The gate may create durable approval requests and audit records that
        # carry foreign keys to jarvis_tasks; those inserts would otherwise
        # violate the FK and crash the calling channel (observed on the WS path).
        # register_task also ensures the requester principal exists in
        # jarvis_users (e.g. 'VoiceUser' from the wake-word loop).
        await task_queue.register_task(executable_payload)

        # 7. Payload-Level Security Evaluation
        security_decision = await security_manager.evaluate_task_package(
            task_package_or_workflow=executable_payload,
            db=db
        )

        if security_decision.get("status") == "DENIED":
            denied_msg = f"Security Gate Denied: {security_decision.get('reason')}"
            logger.warning(denied_msg)
            # The pre-gate registered record must not linger as a recoverable task.
            await task_queue.cancel_task(task_id, reason=f"Security gate denied: {security_decision.get('reason')}")
            await memory_manager.save_message(db, sender="JARVIS", text=denied_msg, session_id=session_id)
            return {"response": denied_msg, "status": ResultStatus.BLOCKED.value}

        # 8. Apply strict ApprovalState mapping based on SecurityManager decision
        # Canonical contract: SecurityManager emits "APPROVAL_REQUIRED" (never "WAITING_APPROVAL").
        if security_decision.get("status") == "APPROVAL_REQUIRED":
            if isinstance(executable_payload, TaskPackage):
                executable_payload.approval_state = ApprovalState.PENDING
            elif isinstance(executable_payload, WorkflowDefinition):
                executable_payload.status = WorkflowStatus.WAITING
                
            approval_id = security_decision.get("approval_id")
            pending_msg = f"Action requires authorization. Approval request [{approval_id}] registered."
            await memory_manager.save_message(db, sender="JARVIS", text=pending_msg, session_id=session_id)
            
            await task_queue.submit(executable_payload)
            return {
                "response": pending_msg,
                "status": ResultStatus.WAITING_APPROVAL.value,
                "task_id": task_id,
                "approval_id": approval_id
            }
        else:
            if isinstance(executable_payload, TaskPackage):
                executable_payload.approval_state = ApprovalState.NOT_REQUIRED

        # 9. Emit Canonical Queue Event & Dispatch
        event_type = EventType.WORKFLOW if isinstance(executable_payload, WorkflowDefinition) else EventType.TASK
        event_topic = "workflow.queued" if isinstance(executable_payload, WorkflowDefinition) else "task.queued"
        
        await event_bus.publish(JarvisEvent(
            event_type=event_type,
            topic=event_topic,
            timestamp=utc_now(),
            correlation_id=task_id,
            task_id=task_id,
            source="brain",
            payload={
                "intent": plan.intent,
                "classification": plan.task_classification
            }
        ))

        await task_queue.submit(executable_payload)

        ack_text = f"{plan.initial_conversational_response} [Task queued: {task_id}]"
        await memory_manager.save_message(db, sender="JARVIS", text=ack_text, session_id=session_id)
        return {"response": ack_text, "task_id": task_id, "status": "QUEUED"}

    def _derive_permission_scope(self, plan: JarvisPlanningOutput, client_scope: Optional[str]) -> PermissionScope:
        """
        Derives dynamic PermissionScope from the tools declared in the LLM plan using the
        LOCKED authority ladder: low->L1 (READ), medium->L2 (DRAFT), high/critical->L3
        (APPROVAL REQUIRED). L4 is NEVER derived here — it exists only via explicit
        user-granted durable permissions evaluated by the PermissionEngine.
        """
        max_risk = "low"
        allowed_actions = ["execute"]
        
        for tool_name in plan.selected_tools:
            tool_rec = tool_registry.get_tool(tool_name)
            if tool_rec:
                risk = getattr(tool_rec, "risk_level", "low").lower()
                if risk == "critical":
                    max_risk = "critical"
                elif risk == "high" and max_risk not in ["critical"]:
                    max_risk = "high"
                elif risk == "medium" and max_risk in ["low"]:
                    max_risk = "medium"
                allowed_actions.append(f"tool:{tool_name}")
            else:
                allowed_actions.append(f"tool:{tool_name}")

        # Locked matrix semantics: consequential tools require approval (L3).
        level_map = {"low": "L1", "medium": "L2", "high": "L3", "critical": "L3"}
        
        return PermissionScope(
            permission_level=level_map.get(max_risk, "L1"),
            allowed_actions=allowed_actions,
            allowed_resources=[],
            forbidden_resources=["system/core"],
            scope={"domain": "task_execution", "client": client_scope or "default"},
            purpose=plan.objective,
            max_risk_level=max_risk
        )

    @staticmethod
    def _build_execution_groups(subtasks) -> List[ExecutionGroup]:
        """
        Builds true dependency-aware ExecutionGroups (spec §9 Parallel Execution):
        subtasks whose dependencies are all satisfied by earlier groups are placed
        together into a PARALLEL group; strictly-dependent chains form SEQUENTIAL groups.
        """
        groups: List[ExecutionGroup] = []
        placed: Dict[str, int] = {}
        remaining = list(subtasks)

        while remaining:
            ready = [st for st in remaining if all(dep.task_id in placed for dep in st.dependencies)]
            if not ready:
                # Circular-dependency safety valve: place one task to guarantee progress.
                ready = [remaining[0]]

            group_index = len(groups)
            ids = [st.subtask_id for st in ready]
            dep_ids = sorted({d.task_id for st in ready for d in st.dependencies})

            groups.append(ExecutionGroup(
                group_id=f"grp_{group_index}",
                mode="PARALLEL" if len(ids) > 1 else "SEQUENTIAL",
                subtask_ids=ids,
                dependencies=dep_ids,
            ))

            for st in ready:
                placed[st.subtask_id] = group_index
            remaining = [st for st in remaining if st.subtask_id not in placed]

        return groups

    def _build_executable_payload(
        self, 
        task_id: str, 
        plan: JarvisPlanningOutput,
        requester: str,
        client_scope: Optional[str],
        project_scope: Optional[str]
    ) -> Any:
        """Constructs canonical TaskPackage or WorkflowDefinition."""
        
        verif_contract = VerificationContract(
            verification_type=plan.verification_type,
            method=plan.method,
            expected_outcome=plan.expected_outcome,
            required_evidence=plan.required_evidence
        )

        expected_output = ExpectedOutput(
            format="JSON_STRUCTURED",
            description=plan.objective,
            schema_definition={"findings": "dict", "actions_performed": "list", "evidence": "dict"}
        )

        perm_scope = self._derive_permission_scope(plan, client_scope)
        
        execution_metadata = ExecutionMetadata(
            correlation_id=task_id,
            started_at=None,
            completed_at=None,
            retry_count=0,
            duration_ms=0.0
        )

        # Translate global memory policy to constraint
        derived_constraints = []
        if not plan.allow_global_memory_fallback:
            derived_constraints.append("Strictly isolate memory search and context to current client/project scope.")
        else:
            derived_constraints.append("Global memory fallback is authorized for this execution.")

        if plan.task_classification == "COMPLEX_WORKFLOW" and plan.subtasks:
            subpackages = []
            
            for st in plan.subtasks:
                sub_verif = VerificationContract(
                    verification_type=st.verification_type,
                    method=st.method,
                    expected_outcome=st.expected_outcome,
                    required_evidence=st.required_evidence
                )
                
                sub_task = SubtaskPackage(
                    subtask_id=st.subtask_id,
                    parent_task_id=task_id,
                    workflow_id=task_id,
                    objective=st.objective,
                    assigned_agent=st.target_agent,
                    dependencies=[TaskDependency(task_id=dep) for dep in st.dependencies],
                    input_data={"intent": st.intent, "tools": st.selected_tools, "parameters": st.tool_parameters},
                    expected_output=expected_output,
                    permission_scope=perm_scope,
                    verification_contract=sub_verif,
                    status=TaskStatus.PLANNED,
                    result=None
                )
                subpackages.append(sub_task)
                
            # True dependency-aware parallel/sequential grouping (spec §9)
            execution_groups = self._build_execution_groups(plan.subtasks)
                
            return WorkflowDefinition(
                workflow_id=task_id,
                parent_task_id=None,
                requester=requester,
                trigger="USER_COMMAND",
                objective=plan.objective,
                agents=[AgentAssignment(agent_id=a, role_in_workflow="worker", allowed_communication_paths=["JARVIS"]) for a in plan.target_agents],
                subtasks=subpackages,
                execution_groups=execution_groups,
                global_dependencies=[],
                handoffs=[],
                approval_gates=[],
                failure_rules=[
                    FailureRule(
                        trigger_condition="ANY_FAILURE",
                        behavior="RETRY_SUBTASK",
                        max_retries=2,
                        fallback_subtask_id=None
                    )
                ],
                completion_condition="ALL_GROUPS_COMPLETED",
                synthesis=SynthesisStage(
                    required_outputs=["final_summary", "verified_learnings"],
                    format=ExpectedOutput(
                        format="JSON_STRUCTURED",
                        description="Synthesized multi-agent result",
                        schema_definition={"final_summary": "str", "verified_learnings": "str"}
                    ),
                    assigned_synthesizer="JARVIS"
                ),
                status=WorkflowStatus.PLANNED,
                metadata=WorkflowMetadata(
                    priority=1,
                    correlation_id=task_id,
                    created_at=utc_now(),
                    started_at=None,
                    completed_at=None,
                    deadline=None
                )
            )
        else:
            primary_agent = plan.target_agents[0] if plan.target_agents else "JARVIS"
            return TaskPackage(
                task_id=task_id,
                parent_task_id=None,
                intent=plan.intent,
                objective=plan.objective,
                target_agents=[primary_agent],
                selected_tools=plan.selected_tools,
                tool_parameters=plan.tool_parameters,
                requester=requester,
                client_scope=client_scope,
                project_scope=project_scope,
                permission_scope=perm_scope,
                resources=[],
                verification_contract=verif_contract,
                expected_output=expected_output,
                approval_state=ApprovalState.NOT_REQUIRED,
                execution_metadata=execution_metadata,
                constraints=derived_constraints,
                do_rules=[],
                do_not_rules=[],
                subtasks=[]
            )

    def _tool_catalog_section(self) -> str:
        """
        Builds (and caches) the authoritative ToolRegistry catalog injected into the
        planning prompt, so the planner can ONLY select tools that actually exist.
        Without this, weaker models invent tool names like 'open_chrome' or
        'meta_ads_navigate' which are then rejected by plan validation.
        """
        if getattr(self, "_cached_tool_catalog", None) is None:
            try:
                entries = []
                for meta in tool_registry.list_tools():
                    name = meta.get("name", "unknown")
                    category = meta.get("category", "general")
                    risk = meta.get("risk_level", "low")
                    desc = (meta.get("description", "") or "").strip().rstrip(".")[:110]
                    entries.append(f"- {name} | category={category} | risk={risk} | {desc}")
                self._cached_tool_catalog = (
                    "\n\nAUTHORITATIVE TOOL CATALOG (selected_tools MUST be exact names from this list):\n"
                    + "\n".join(entries)
                    + "\n\nTool selection guidance:\n"
                    "- To open/launch/close an application, use 'app_launcher' with the app name.\n"
                    "- To navigate/control a browser page or URL, use 'browser_control'.\n"
                    "- NEVER invent tool names not present above; prefer NO tools over a guessed name.\n"
                )
            except Exception as e:
                logger.warning(f"Tool catalog build failed; planning without catalog: {e}")
                self._cached_tool_catalog = ""
        return self._cached_tool_catalog

    async def _understand_and_plan(self, user_text: str, context_data: Dict[str, Any]) -> Optional[JarvisPlanningOutput]:
        """Invokes APIEngine LLM with ContextManager payload and parses into JarvisPlanningOutput."""
        # Pure conversational / introspection commands are serviced deterministically so the
        # operator always receives an acknowledgment even when the planning LLM is degraded,
        # timing out, or returning an empty / non-parseable response.
        conversational = self._conversational_fallback(user_text)
        if conversational is not None:
            return conversational

        try:
            context_prompt = f"User Command: {user_text}\n\nCanonical Context:\n{json.dumps(context_data, indent=2)}"

            result = await api_engine.call_llm(
                prompt=context_prompt,
                system_prompt=f"{self.SYSTEM_PROMPT}{self._tool_catalog_section()}",
                temperature=0.1,
                max_tokens=2048,
            )

            # Tolerant extraction: strip code fences, tolerate surrounding prose,
            # then decode the FIRST balanced JSON object found in the payload.
            candidate = extract_json_object(strip_code_fences(result.get("response", "")))
            if not isinstance(candidate, dict):
                raise ValueError("Planner response contained no decodable JSON object.")
            parsed = candidate

            plan = JarvisPlanningOutput(**parsed)

            # Conversational fallback: never leave the user without a reply when a
            # weaker provider omits `initial_conversational_response`.
            if not plan.initial_conversational_response.strip():
                plan.initial_conversational_response = (
                    plan.objective.strip() or f"Acknowledged: '{user_text}'."
                )

            return plan
        except Exception as e:
            logger.error(f"Failed to formulate cognitive plan: {e}", exc_info=True)
            return None

    async def _fast_path_reply(self, user_text: str) -> Optional[str]:
        """
        Conversation-first fast path. Returns a natural reply string for requests
        that should never enter the task/planning pipeline, or None when the input
        clearly needs tool/task execution (defer to full planning).

        Deterministic fast paths (greetings/status/time/date/thanks) are answered
        instantly with zero LLM cost; short casual messages get ONE lightweight
        conversational LLM exchange.
        """
        normalized = (user_text or "").strip().lower()
        if not normalized:
            return None

        # --- Deterministic instant replies (no LLM) ---
        deterministic = self._deterministic_conversation(normalized)
        if deterministic is not None:
            return deterministic

        # --- Lightweight causal conversation ---
        # Only short, conversational-length messages that are not clearly asking
        # for a tool/action. Longer or action-y inputs fall through to planning.
        if len(user_text) > 140:
            return None
        if self._looks_like_tool_request(normalized):
            return None
        # Messages addressed to a named specialist agent must route through the
        # full pipeline so that agent (VERONICA, FRIDAY, PLATO, EDITH) responds.
        if any(name in normalized for name in ("veronica", "friday", "plato", "edith")):
            return None

        try:
            result = await api_engine.call_llm(
                prompt=f"User said: {user_text}\n\nThe user is in a conversation with you.",
                system_prompt=self.CONVERSATION_SYSTEM_PROMPT,
                temperature=0.7,
                max_tokens=180,
            )
            reply = str(result.get("response") or result.get("content") or "").strip()
            if not reply or reply.upper() == "TASK":
                return None
            return reply
        except Exception as e:
            logger.debug(f"Conversation fast path LLM failed; falling through: {e}")
            return None

    @staticmethod
    def _looks_like_tool_request(normalized: str) -> bool:
        """Heuristics flagging inputs that need real tool/task execution."""
        tool_hints = [
            "open ", "launch", "take a screenshot", "screenshot", "take screenshot",
            "screenshot of", "search for", "search the web", "search web", "look up",
            "create ", "write a file", "send ", "email ", "message ", "post ",
            "design ", "build ", "deploy", "generate ", "stop ", "close ",
            "play ", "pause", "shutdown the computer", "restart", "lock the",
            "what's on my screen", "analyze", "make ", "draft ", "schedule ",
            # Screen awareness — route to real screen analysis, not a chat reply.
            "look at my screen", "look at the screen", "look at screen", "on my screen",
            "what's on my screen", "what is on my screen", "what are you seeing",
            "what you are seeing", "what you see", "what can you see", "seeing on my screen",
            "analyze my screen", "what's happening on my screen", "show me my screen",
            # Business/ad intelligence domains — handled by specialists/tools, not chat.
            "meta ads", "google ads", "my ads", "ad campaign", "facebook ads",
            "ad account", "ad manager", "marketing", "campaign", "check my",
            "report on", "track my",
            # Everyday assistant commands — must reach the tool layer, not chat.
            "remind", "timer", "alarm", "volume", "brightness", "wifi", "bluetooth",
            "do not disturb", "notifications", "screen record", "record my screen",
            "play some music", "turn on", "turn off", "toggle",
        ]
        return any(hint in normalized for hint in tool_hints)

    @staticmethod
    def _deterministic_conversation(normalized: str) -> Optional[str]:
        """Pure deterministic conversational/introspection replies (zero LLM cost)."""
        from datetime import datetime as _dt
        # Time / date queries. The patterns are deliberately word-order tolerant:
        # spoken forms like "what the time it is?" or "time is it" previously
        # missed the fast path and went to the LLM, which rambled and timed out TTS.
        if any(p in normalized for p in ("what time", "what is the time", "current time",
                                         "the time right now", "what's the time",
                                         "whats the time", "present time", "what the time",
                                         "time is it", "time it is", "tell me the time",
                                         "the time now", "time right now")):
            return f"The current time is {_dt.now().strftime('%I:%M %p').lstrip('0')}."
        if any(p in normalized for p in ("what day", "today's date", "what's the date",
                                         "what is the date", "date today", "what day is it")):
            return f"Today is {_dt.now().strftime('%A, %B %d, %Y')}."
        if "what year" in normalized:
            return f"The current year is {_dt.now().year}."

        # Greetings / status / acknowledgement.
        if any(p in normalized for p in ("hello", "hi jar", "hey", "good morning",
                                         "good afternoon", "good evening", "how are you",
                                         "how's it going", "how are you doing")):
            return "Hello, sir. All systems are online, and I am at your service. How can I help?"
        if any(p in normalized for p in ("can you hear me", "are you there", "you there",
                                         "could you hear me", "are you awake", "system status",
                                         "all systems", "status", "are you online")):
            return "Yes, sir. I can hear you clearly and all systems are reporting normal health."
        if any(p in normalized for p in ("thank you", "thanks", "thanks a lot", "well done",
                                         "good job", "appreciate", "nice", "great", "awesome")):
            return "At your service, sir. Is there anything else you need?"
        if "who are you" in normalized or "your name" in normalized:
            return "I am J.A.R.V.I.S., your AI assistant — always listening and ready to help, sir."
        # Wake / standby acknowledgments (zero-LLM). These were previously consuming
        # a conversational LLM call every time the user said 'wake up jarvis'.
        if normalized in ("jarvis", "wake", "wake up", "wake up jarvis", "hey jarvis",
                          "hi jarvis", "hello jarvis", "awake", "standby", "are you awake"):
            return "Yes, sir. I am awake and standing by at your service."
        return None

    def _conversational_fallback(self, user_text: str) -> Optional[JarvisPlanningOutput]:
        """
        Deterministic acknowledgments for pure conversational / introspection commands,
        so J.A.R.V.I.S. always replies even when the planning LLM is unavailable.
        Returns a CONVERSATION plan, or None when real planning is required.
        """
        normalized = (user_text or "").strip().lower()
        responders = [
            (["session off", "go off", "shutdown", "stand down", "good night", "go offline"],
             "SESSION_END",
             "Yes, sir. Ending the active session. I remain on standby."),
            (["hello", "hi jar", "hey", "good morning", "good afternoon", "good evening",
              "how are you", "how's it going"],
             "GREETING",
             "Hello, sir. All systems are online and I am at your service."),
            (["can you hear me", "are you there", "you there", "could you hear me",
              "are you awake", "system status", "all systems", "status"],
             "SYSTEM_STATUS",
             "Yes, sir. All systems are reporting normal health and operation."),
            (["thank you", "thanks", "thanks a lot", "well done", "good job", "appreciate"],
             "ACKNOWLEDGEMENT",
             "At your service, sir. Is there anything else you need?"),
        ]
        for tokens, intent, reply in responders:
            if any(tok in normalized for tok in tokens):
                return self._build_conversational_plan(intent, reply)

        # Standalone wake / stray punctuation or a bare wake-word is safely acknowledged.
        if normalized in (".", "", "jarvis", "wake", "wake up", "wake up jarvis"):
            return self._build_conversational_plan("AWAKENING", "Yes, sir. I am awake and standing by.")

        return None

    @staticmethod
    def _build_conversational_plan(intent: str, reply: str) -> JarvisPlanningOutput:
        """Constructs a minimal CONVERSATION plan so process_command replies immediately."""
        return JarvisPlanningOutput(
            task_classification="CONVERSATION",
            intent=intent,
            objective=reply,
            requires_execution=False,
            initial_conversational_response=reply,
            agent_selection_rationale="Conversational/introspection command — no specialist agent or execution required.",
        )

    def _validate_and_normalize_plan(self, plan: JarvisPlanningOutput) -> Optional[str]:
        """Validates that EVERY assigned agent is authorized and capable for EVERY assigned tool."""
        validated_agents = []
        for agent_id in plan.target_agents:
            clean_id = agent_id.strip().upper()
            if clean_id == "JARVIS":
                validated_agents.append("JARVIS")
                continue
            
            if not agent_registry.get_agent(clean_id):
                return f"Planning Error: Target specialist agent '{agent_id}' is not registered."
            validated_agents.append(clean_id)

        if not validated_agents and plan.selected_tools:
            validated_agents.append("JARVIS")

        plan.target_agents = list(dict.fromkeys(validated_agents))

        # Strict agent <-> capability <-> selected tool cross-validation
        for tool_name in plan.selected_tools:
            if not tool_registry.get_tool(tool_name):
                return f"Planning Error: Tool '{tool_name}' does not exist in ToolRegistry."

            tool_claimed = False
            for agent_id in plan.target_agents:
                if agent_id == "JARVIS" and tool_name in self.JARVIS_CORE_TOOLS:
                    tool_claimed = True
                    break
                elif agent_id != "JARVIS":
                    agent_inst = agent_registry.get_agent(agent_id)
                    if agent_inst and tool_name in agent_inst.get_declared_capabilities():
                        tool_claimed = True
                        break

            if not tool_claimed:
                return f"Boundary Error: The target agents {plan.target_agents} do not have declared capability for tool '{tool_name}'."

        return None

    def _extract_agent_id(self, result: ResultPackage) -> str:
        """Safely extracts the executing agent ID from the canonical ActionRecord array."""
        if result.actions_performed:
            return result.actions_performed[0].agent_id
        return "UNKNOWN"

    async def synthesize_and_record_outcome(
        self,
        db: AsyncSession,
        task_id: str,
        results: List[ResultPackage],
        requester: str,
        client_scope: Optional[str],
        project_scope: Optional[str]
    ) -> Dict[str, Any]:
        """
        Performs multi-agent result synthesis via LLM strictly based on verification state.
        Only extracts and commits verified learnings to permanent memory if success is confirmed.
        """
        logger.info(f"Synthesizing final outcome for task [{task_id}] across {len(results)} results.")

        all_completed = all(r.status == ResultStatus.COMPLETED for r in results)
        any_failed = any(r.status == ResultStatus.FAILED for r in results)
        
        # Determine dynamic baseline fallback string
        if all_completed:
            baseline_response = "Task execution completed successfully."
        elif any_failed:
            baseline_response = "Task execution concluded with failures."
        else:
            baseline_response = "Task execution concluded with partial or pending results."
            
        aggregated_findings = json.dumps([{
            "agent": self._extract_agent_id(r),
            "status": str(r.status),
            "findings": r.findings,
            "evidence": r.evidence,
            "errors": r.errors
        } for r in results], indent=2)
        
        synthesis_prompt = f"Task Results (Verified Evidence):\n{aggregated_findings}"

        synthesis_res = await api_engine.call_llm(
            prompt=synthesis_prompt,
            system_prompt=self.SYNTHESIS_PROMPT,
            temperature=0.2
        )

        final_response_text = baseline_response
        verified_learnings = None

        if synthesis_res.get("success"):
            try:
                raw_syn = synthesis_res["response"].strip()
                if raw_syn.startswith("```json"): raw_syn = raw_syn[7:]
                elif raw_syn.startswith("```"): raw_syn = raw_syn[3:]
                if raw_syn.endswith("```"): raw_syn = raw_syn[:-3]
                
                syn_data = json.loads(raw_syn.strip())
                final_response_text = syn_data.get("final_response_text", final_response_text)
                verified_learnings = syn_data.get("verified_learnings")
            except Exception as e:
                logger.error(f"Failed to parse LLM synthesis response: {e}")

        if verified_learnings and all_completed:
            await memory_manager.create_structured_memory(
                db=db,
                memory_id=f"mem_syn_{uuid.uuid4().hex[:8]}",
                content=verified_learnings,
                category=MemoryCategory.EPISODIC,
                client_scope=client_scope,
                project_scope=project_scope,
                user_id=requester,
                confidence=0.9,
                source_task_id=task_id,
                source_provenance="JARVIS Synthesis"
            )

        await memory_manager.save_message(
            db=db, sender="JARVIS", text=final_response_text,
            client_scope=client_scope, project_scope=project_scope, user_id=requester
        )

        return {
            "task_id": task_id,
            "response": final_response_text,
            "results_processed": len(results)
        }

brain = JarvisBrain()
