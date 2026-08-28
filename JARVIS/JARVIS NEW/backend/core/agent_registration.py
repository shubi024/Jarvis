"""
backend/core/agent_registration.py
Centralized backend registry for J.A.R.V.I.S. Specialist Agents.
Enforces strict initialization, capability validation, and identity standardization.
"""

import logging
from typing import Dict, List, Optional, Any

from backend.agents.base_agent import BaseAgent
from backend.tools.tool_registry import tool_registry

logger = logging.getLogger("JARVIS.Core.AgentRegistration")


class AgentRegistry:
    """
    Authoritative registry for all J.A.R.V.I.S. agents.
    Validates identity, prevents collisions, and strictly enforces tool dependencies.
    """
    
    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent, override: bool = False) -> BaseAgent:
        """
        Registers an initialized agent. 
        Prevents duplicates and hard-validates tool dependencies against the ToolRegistry.
        """
        if not isinstance(agent, BaseAgent):
            raise TypeError("Agent must inherit from BaseAgent")
        
        # Standardize on uppercase IDs (FRIDAY, PLATO, etc.)
        agent_id = agent.agent_id.upper()
        
        # Keep JARVIS separate as the master orchestrator/core rather than a specialist registration
        if agent_id == "JARVIS":
            raise ValueError("JARVIS is the master orchestrator/core and cannot be registered as a specialist agent.")

        if agent_id in self._agents and not override:
            raise ValueError(f"Agent collision: '{agent_id}' is already registered.")

        # Hard validation: Validate every capability against the ToolRegistry
        for tool_name in agent.get_declared_capabilities():
            if not tool_registry.get_tool(tool_name):
                raise ValueError(
                    f"Registration failed for agent [{agent_id}]: Declared tool '{tool_name}' "
                    f"does not exist in the ToolRegistry. Fix the agent's capabilities or register the tool first."
                )

        self._agents[agent_id] = agent
        logger.info(f"Registered Agent: [{agent_id}] | Role: {agent.role} | Capabilities: {len(agent.get_declared_capabilities())}")
        return agent

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """Retrieves an agent by ID (case-insensitive)."""
        return self._agents.get(agent_id.upper())

    def list_agents(self) -> List[Dict[str, Any]]:
        """Returns metadata for all registered specialist agents."""
        return [
            {
                "agent_id": agent.agent_id,
                "name": getattr(agent, "name", agent.agent_id),
                "role": agent.role,
                "capabilities": list(agent.get_declared_capabilities()),
                "status": "ACTIVE"
            }
            for agent in self._agents.values()
        ]

    def validate_agent_tool_access(self, agent_id: str, tool_name: str) -> bool:
        """Checks if a specific agent is explicitly authorized to use a specific tool."""
        agent = self.get_agent(agent_id)
        if not agent:
            return False
        return tool_name in agent.get_declared_capabilities()


# Singleton authoritative instance to be used across the backend and runtime
agent_registry = AgentRegistry()


def register_all_agents():
    """
    Wires and registers all core specialist agents (PLATO, EDITH, VERONICA, FRIDAY)
    into the central AgentRegistry after tools are initialized.
    """
    from backend.agents.plato import plato
    from backend.agents.edith import edith
    from backend.agents.veronica import veronica
    from backend.agents.friday import friday

    logger.info("Initializing Agent Registration sequence...")

    # Register into the authoritative singleton registry (single source of truth)
    agent_registry.register(plato)
    agent_registry.register(edith)
    agent_registry.register(veronica)
    agent_registry.register(friday)

    logger.info("All specialist agents successfully validated and registered.")