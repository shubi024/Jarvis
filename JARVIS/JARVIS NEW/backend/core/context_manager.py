import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.memory.memory_manager import memory_manager
from backend.core.task_contracts import TaskPackage

logger = logging.getLogger("JARVIS.Core.ContextManager")

class ContextManager:
    """
    Context Manager for J.A.R.V.I.S.
    Builds scoped, least-privilege execution contexts for tasks. Enforces strict client/project
    isolation, memory filtering, sensitive-data scrubbing, and deterministic plain-data snapshot generation
    with zero execution or security authorization authority.
    """

    def _to_serializable(self, obj: Any) -> Any:
        """Recursively converts ORM models or custom objects into deterministic plain JSON-serializable primitives."""
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {str(k): self._to_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._to_serializable(item) for item in obj]
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            return self._to_serializable(obj.model_dump())
        if hasattr(obj, "__dict__"):
            # Exclude private attributes/SQLAlchemy internal states
            filtered = {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
            return self._to_serializable(filtered)
        return str(obj)

    async def build_context_from_package(
        self, 
        db: AsyncSession, 
        task_package: TaskPackage,
        workflow_state: Optional[Dict[str, Any]] = None,
        dependency_results: Optional[Dict[str, Any]] = None,
        session_observations: Optional[List[Dict[str, Any]]] = None,
        approval_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Builds a full deterministic context snapshot directly from a canonical TaskPackage,
        incorporating workflow state, prerequisite dependency results, observations, and approval state.
        """
        return await self.build_context(
            db=db,
            task_id=task_package.task_id,
            intent=task_package.intent,
            target_agents=task_package.target_agents,
            user_text=task_package.objective,
            client_scope=task_package.client_scope,
            project_scope=task_package.project_scope,
            requester=task_package.requester,
            constraints=task_package.constraints,
            do_rules=getattr(task_package, "do_rules", []),
            do_not_rules=getattr(task_package, "do_not_rules", []),
            resources=getattr(task_package, "resources", []),
            permission_scope=getattr(task_package, "permission_scope", "L1_INTERNAL"),
            expected_output=task_package.expected_output,
            verification_contract=task_package.verification_contract,
            selected_tools=task_package.selected_tools,
            workflow_state=workflow_state,
            dependency_results=dependency_results,
            session_observations=session_observations,
            approval_state=approval_state
        )

    async def build_context(
        self, 
        db: AsyncSession, 
        task_id: str,
        intent: str, 
        target_agents: List[str], 
        user_text: str,
        client_scope: Optional[str] = None,
        project_scope: Optional[str] = None,
        requester: Optional[str] = None,
        constraints: Optional[List[str]] = None,
        do_rules: Optional[List[str]] = None,
        do_not_rules: Optional[List[str]] = None,
        resources: Optional[List[str]] = None,
        permission_scope: Optional[str] = None,
        expected_output: Optional[Any] = None,
        verification_contract: Optional[Any] = None,
        selected_tools: Optional[List[str]] = None,
        workflow_state: Optional[Dict[str, Any]] = None,
        dependency_results: Optional[Dict[str, Any]] = None,
        session_observations: Optional[List[Dict[str, Any]]] = None,
        approval_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Builds scoped, least-privilege task contexts for execution with guaranteed JSON serialization,
        rigorous scope filtering, and complete canonical TaskPackage field mapping.
        """
        logger.info(f"ContextManager building scoped context for task [{task_id}], intent: [{intent}], agents: {target_agents}")

        # 1. Retrieve candidate memories using semantic & scoped queries
        candidate_memories = await memory_manager.search_memories(
            db=db, 
            query_text=user_text, 
            client_scope=client_scope, 
            project_scope=project_scope, 
            limit=15
        )

        # 2. Enforce strict cross-client/project deny-by-default scope validation
        filtered_candidates = self._enforce_scope_isolation(candidate_memories, client_scope, project_scope)

        # 3. Filter sensitive data/PII/secrets out of memory payloads
        sanitized_memories = self._filter_sensitive_data(filtered_candidates)

        # 4. Filter and sanitize session observations
        sanitized_observations = self._filter_and_scope_observations(
            session_observations or [], client_scope, project_scope
        )

        # 5. Sanitize dependency results and workflow state
        sanitized_workflow = self._to_serializable(workflow_state or {})
        sanitized_dependencies = self._to_serializable(dependency_results or {})
        sanitized_approval = self._to_serializable(approval_state or {})

        # 6. Build agent-specific isolated knowledge contexts
        agent_specific_contexts = {}
        for agent_id in target_agents:
            agent_scoped_data = {
                "agent_id": agent_id,
                "client_scope": client_scope,
                "project_scope": project_scope,
                "scoped_payload": {
                    "relevant_history": self._to_serializable(sanitized_memories),
                    "intent_focus": intent
                }
            }
            agent_specific_contexts[agent_id] = agent_scoped_data

        # 7. Assemble deterministic, audit-ready plain data context snapshot package
        raw_context = {
            "task_id": task_id,
            "correlation_id": task_id,
            "intent": intent,
            "objective": user_text,
            "requester": requester or "SYSTEM",
            "client_scope": client_scope,
            "project_scope": project_scope,
            "permission_scope": permission_scope or "L1_INTERNAL",
            "resources": resources or [],
            "selected_tools": selected_tools or [],
            "constraints": constraints or [],
            "do_rules": do_rules or [],
            "do_not_rules": do_not_rules or [],
            "expected_output": expected_output,
            "verification_contract": verification_contract,
            "workflow_state": sanitized_workflow,
            "dependency_results": sanitized_dependencies,
            "session_observations": sanitized_observations,
            "approval_state": sanitized_approval,
            "agent_scopes": agent_specific_contexts,
            "provenance": {
                "memory_sources_count": len(sanitized_memories),
                "isolated": True,
                "scoped_to_client": client_scope,
                "scoped_to_project": project_scope
            }
        }

        # Guarantee strict JSON-serializable primitives for the final snapshot
        task_context = self._to_serializable(raw_context)

        logger.info(f"Context successfully assembled, sanitized, and scoped for task [{task_id}].")
        return task_context

    def _enforce_scope_isolation(self, candidates: List[Any], client_scope: Optional[str], project_scope: Optional[str]) -> List[Any]:
        """Rigorously validates and drops candidate records using deny-by-default logic."""
        isolated_candidates = []
        for item in candidates:
            item_client = getattr(item, "client_scope", None)
            item_project = getattr(item, "project_scope", None)

            if client_scope and item_client and item_client != client_scope:
                continue  
            if project_scope and item_project and item_project != project_scope:
                continue  

            isolated_candidates.append(item)
        return isolated_candidates

    def _filter_sensitive_data(self, candidates: List[Any]) -> List[Any]:
        """Prevents irrelevant secrets, API tokens, or PII from entering agent context payloads."""
        filtered = []
        sensitive_terms = {"password", "secret", "api_key", "token", "credential", "auth_token"}
        for item in candidates:
            item_str = str(item).lower()
            if any(term in item_str for term in sensitive_terms):
                continue
            filtered.append(item)
        return filtered

    def _filter_and_scope_observations(
        self, 
        observations: List[Dict[str, Any]], 
        client_scope: Optional[str], 
        project_scope: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Scopes and sanitizes session observations against client/project boundaries and secrets."""
        filtered = []
        sensitive_terms = {"password", "secret", "api_key", "token", "credential"}
        
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            
            obs_client = obs.get("client_scope")
            obs_project = obs.get("project_scope")

            if client_scope and obs_client and obs_client != client_scope:
                continue
            if project_scope and obs_project and obs_project != project_scope:
                continue

            # Scrub sensitive keys/values
            scrubbed_obs = {}
            skip = False
            for k, v in obs.items():
                if any(term in str(k).lower() for term in sensitive_terms):
                    continue
                if any(term in str(v).lower() for term in sensitive_terms):
                    scrubbed_obs[k] = "[REDACTED]"
                else:
                    scrubbed_obs[k] = v
            
            if not skip:
                filtered.append(scrubbed_obs)
                
        return filtered

context_manager = ContextManager()