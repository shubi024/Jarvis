"""
backend/tools/base_tool.py
Standardized Abstract Base Class for all J.A.R.V.I.S. tool implementations.
"""

import time
import inspect
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("JARVIS.Tools.BaseTool")


class RiskLevel(str, Enum):
    """Permitted risk levels for tools within J.A.R.V.I.S."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolResult(BaseModel):
    """Standardized output model for every tool execution."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class BaseTool(ABC):
    """
    Abstract Base Class for J.A.R.V.I.S. tools.
    Responsible for input validation, timing, execution, and result normalization.
    Security, permission gating, and human approvals are handled strictly upstream in ToolRegistry.
    """

    name: str
    description: str
    category: str = "general"
    args_schema: Optional[Type[BaseModel]] = None
    risk_level: str = RiskLevel.LOW.value
    requires_approval: bool = False

    def __init__(self):
        if not hasattr(self, "name") or not self.name:
            raise ValueError(f"Tool {self.__class__.__name__} must define a non-empty 'name' attribute.")
        if not hasattr(self, "description") or not self.description:
            raise ValueError(f"Tool {self.__class__.__name__} must define a non-empty 'description' attribute.")

        valid_risk_values = {level.value for level in RiskLevel}
        if self.risk_level not in valid_risk_values:
            raise ValueError(
                f"Tool '{self.name}' has invalid risk_level '{self.risk_level}'. "
                f"Must be one of: {', '.join(sorted(valid_risk_values))}"
            )

    @abstractmethod
    async def _run(self, **kwargs) -> Any:
        """
        Core implementation logic of the tool.
        Must be implemented by the subclass.
        """
        pass

    async def execute(self, **kwargs) -> ToolResult:
        """
        Pure execution wrapper responsible for argument validation,
        execution timing, error isolation, and ToolResult normalization.
        """
        start_time = time.perf_counter()
        logger.info(f"Executing tool: [{self.name}] | Category: {self.category} | Risk: {self.risk_level}")

        # 1. Input Validation via Pydantic Schema
        validated_kwargs = kwargs
        if self.args_schema:
            try:
                validated_model = self.args_schema(**kwargs)
                validated_kwargs = validated_model.model_dump()
            except ValidationError as ve:
                duration = round((time.perf_counter() - start_time) * 1000, 2)
                err_msg = f"Parameter validation failed for tool '{self.name}': {ve.errors()}"
                logger.error(err_msg)
                return ToolResult(
                    success=False,
                    error=err_msg,
                    execution_time_ms=duration,
                    metadata={"raw_args": kwargs}
                )

        # 2. Execution with timing & sync/async compatibility
        try:
            if inspect.iscoroutinefunction(self._run):
                result = await self._run(**validated_kwargs)
            else:
                result = self._run(**validated_kwargs)

            duration = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(f"Tool [{self.name}] completed successfully in {duration}ms.")

            return ToolResult(
                success=True,
                data=result,
                execution_time_ms=duration,
                metadata={"risk_level": self.risk_level, "category": self.category}
            )

        except Exception as exc:
            duration = round((time.perf_counter() - start_time) * 1000, 2)
            err_msg = f"Error during execution of tool '{self.name}': {str(exc)}"
            logger.exception(err_msg)

            return ToolResult(
                success=False,
                error=err_msg,
                execution_time_ms=duration,
                metadata={"risk_level": self.risk_level, "category": self.category}
            )

    def to_function_schema(self) -> Dict[str, Any]:
        """
        Converts the tool definition and Pydantic schema into a standard
        function-calling JSON schema.
        """
        parameters: Dict[str, Any] = {
            "type": "object",
            "properties": {},
            "required": []
        }

        if self.args_schema:
            json_schema = self.args_schema.model_json_schema()
            properties = json_schema.get("properties", {})
            required = json_schema.get("required", [])

            # Strip Pydantic meta-titles to keep context clean for the LLM
            cleaned_properties = {}
            for prop_name, prop_def in properties.items():
                clean_def = {k: v for k, v in prop_def.items() if k != "title"}
                cleaned_properties[prop_name] = clean_def

            parameters = {
                "type": "object",
                "properties": cleaned_properties,
                "required": required
            }

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters
            }
        }