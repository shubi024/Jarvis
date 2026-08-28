"""
backend/agents/base_agent.py
Formal contract and boundary definitions for J.A.R.V.I.S. Specialist Agents.
"""

import abc
import logging
from typing import Dict, Any, List, Tuple, Callable, Coroutine

from backend.core.task_contracts import TaskPackage, ResultPackage
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Agents.BaseAgent")


class BaseAgent(abc.ABC):
    """
    Abstract base contract for all J.A.R.V.I.S. specialist agents.
    Every specialist agent (PLATO, EDITH, VERONICA, FRIDAY) must implement this interface.
    Agents do not decide strategy; they receive a TaskPackage, execute specialized
    domain logic using the provided secure tool_executor, and return a strict ResultPackage.
    """
    
    def __init__(self, agent_id: str, name: str, role: str, capabilities: List[str]):
        # 1. Reject empty or invalid definitions
        if not agent_id or not isinstance(agent_id, str):
            raise ValueError("agent_id must be a non-empty string.")
        if not name or not isinstance(name, str):
            raise ValueError("name must be a non-empty string.")
        if not role or not isinstance(role, str):
            raise ValueError("role must be a non-empty string.")
        if not isinstance(capabilities, list):
            raise ValueError("capabilities must be a list of strings.")

        # 2. Enforce canonical uppercase identity and explicit role
        self.agent_id = agent_id.strip().upper()
        self.name = name.strip()
        self.role = role.strip()
        
        # 3. Freeze capabilities into an immutable tuple to prevent runtime tampering
        self._capabilities: Tuple[str, ...] = tuple(cap.strip() for cap in capabilities if cap.strip())
        
        if not self._capabilities:
            raise ValueError(f"Agent [{self.agent_id}] must declare at least one capability/tool.")

    @abc.abstractmethod
    async def execute(
        self, 
        task_package: TaskPackage, 
        tool_executor: Callable[[str, dict], Coroutine[Any, Any, Any]]
    ) -> ResultPackage:
        """
        Executes the assigned task.
        
        :param task_package: The strict payload defining intent, context, and selected tools.
        :param tool_executor: A securely bound async callback (provided by AgentRuntime) 
                              that the agent MUST use to invoke tools.
        :returns: A ResultPackage detailing status, findings, evidence, and limitations.
        """
        pass

    def get_declared_capabilities(self) -> Tuple[str, ...]:
        """Returns the immutable tuple of capabilities/permissions declared by this agent."""
        return self._capabilities

    async def invoke_tool_securely(
        self, 
        tool_name: str, 
        params: Dict[str, Any], 
        task_package: TaskPackage,
        tool_executor: Callable[[str, dict], Coroutine[Any, Any, Any]]
    ) -> Any:
        """
        Helper method for specialist agents to securely execute tools through the bound callback.
        Enforces least-privilege checks against declared capabilities and selected tools.
        """
        # 1. Least Privilege Check (Agent Capability Boundary)
        if tool_name not in self._capabilities:
            logger.error(f"SECURITY BLOCK: {self.agent_id} attempted unauthorized access to tool [{tool_name}] outside declared capabilities.")
            raise ExecutionError(
                message=f"Unauthorized tool execution: '{tool_name}' is not in agent capabilities.",
                classification=ErrorClassification.SECURITY_FAILURE
            )

        # 2. Selected-Tool Enforcement (TaskPackage Boundary)
        if tool_name not in task_package.selected_tools:
            logger.error(f"SECURITY BLOCK: {self.agent_id} attempted to invoke tool [{tool_name}] not present in TaskPackage selected_tools.")
            raise ExecutionError(
                message=f"Selected-tool violation: '{tool_name}' is not authorized for this specific task.",
                classification=ErrorClassification.SECURITY_FAILURE
            )

        # 3. Route execution through the securely bound runtime callback
        logger.info(f"Agent [{self.agent_id}] invoking tool [{tool_name}] via secure tool_executor callback.")
        return await tool_executor(tool_name, params)