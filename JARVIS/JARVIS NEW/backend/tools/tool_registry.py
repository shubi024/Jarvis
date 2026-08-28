"""
backend/tools/tool_registry.py
Centralized Tool Registry for J.A.R.V.I.S.
Manages tool registration, metadata inspection, schema generation, 
and secure execution integrated with SecurityManager and ApprovalManager.
"""

import time
import logging
import inspect
from typing import Callable, Dict, Any, List, Optional, Union, Type

from backend.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("JARVIS.Tools.ToolRegistry")


class ToolRegistry:
    """
    Centralized Tool Registry for J.A.R.V.I.S.
    Supports both BaseTool class instances and decorated functions (legacy support).
    """

    def __init__(self):
        self._tools: Dict[str, Union[BaseTool, Callable]] = {}
        self._tool_metadata: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, tool: Union[BaseTool, Type[BaseTool]], override: bool = False) -> BaseTool:
        """
        Registers an instance or class inheriting from BaseTool.
        Prevents accidental collision unless override=True.
        """
        tool_instance = tool() if inspect.isclass(tool) else tool

        if not isinstance(tool_instance, BaseTool):
            raise TypeError(f"Expected instance of BaseTool, got {type(tool_instance)}")

        name = tool_instance.name
        if name in self._tools and not override:
            raise ValueError(f"Tool collision: '{name}' is already registered. Pass override=True to replace.")

        self._tools[name] = tool_instance
        schema_def = tool_instance.to_function_schema()

        self._tool_metadata[name] = {
            "name": name,
            "description": tool_instance.description,
            "category": tool_instance.category,
            "risk_level": tool_instance.risk_level,
            "requires_approval": tool_instance.requires_approval,
            "parameters": schema_def.get("function", {}).get("parameters", {}),
            "tags": [tool_instance.category, tool_instance.risk_level],
        }

        logger.info(f"Registered BaseTool: [{name}] | Category: {tool_instance.category} | Risk: {tool_instance.risk_level}")
        return tool_instance

    def register(
        self,
        name: str,
        description: str,
        category: str = "general",
        risk_level: str = "low",
        requires_approval: bool = False,
        tags: Optional[List[str]] = None,
        override: bool = False,
    ) -> Callable:
        """
        Decorator to register a standalone Python function as an executable J.A.R.V.I.S. tool.
        Maintained temporarily for backward compatibility.
        """
        def decorator(func: Callable) -> Callable:
            if name in self._tools and not override:
                raise ValueError(f"Tool collision: '{name}' is already registered. Pass override=True to replace.")

            self._tools[name] = func

            # Inspect signature to generate parameter schema
            sig = inspect.signature(func)
            parameters: Dict[str, Any] = {}
            required: List[str] = []

            for param_name, param in sig.parameters.items():
                if param_name in ["self", "cls", "db", "security_context", "user", "task_id"]:
                    continue

                param_type = "string"
                if param.annotation != inspect.Parameter.empty:
                    if param.annotation in (int, "int"):
                        param_type = "integer"
                    elif param.annotation in (float, "float"):
                        param_type = "number"
                    elif param.annotation in (bool, "bool"):
                        param_type = "boolean"
                    elif param.annotation in (list, List, "list", "List"):
                        param_type = "array"
                    elif param.annotation in (dict, Dict, "dict", "Dict"):
                        param_type = "object"

                parameters[param_name] = {
                    "type": param_type,
                    "description": f"Parameter '{param_name}' for tool '{name}'"
                }

                if param.default == inspect.Parameter.empty:
                    required.append(param_name)

            self._tool_metadata[name] = {
                "name": name,
                "description": description,
                "category": category,
                "risk_level": risk_level,
                "requires_approval": requires_approval,
                "tags": tags or [category, risk_level],
                "parameters": {
                    "type": "object",
                    "properties": parameters,
                    "required": required
                }
            }

            logger.info(f"Registered function tool: [{name}] (Risk: {risk_level} | Requires Approval: {requires_approval})")
            return func

        return decorator

    def get_tool(self, name: str) -> Optional[Union[BaseTool, Callable]]:
        """Retrieves a registered tool (BaseTool instance or callable) by name."""
        return self._tools.get(name)

    def get_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        """Retrieves tool metadata and parameter schema."""
        return self._tool_metadata.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        """Returns metadata for all registered tools."""
        return list(self._tool_metadata.values())

    def get_openai_schemas(self, categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Formats all registered tools into OpenAI / Groq / Gemini compatible
        function calling declarations.
        """
        schemas = []
        for name, meta in self._tool_metadata.items():
            if categories and meta.get("category") not in categories:
                continue

            schemas.append({
                "type": "function",
                "function": {
                    "name": meta["name"],
                    "description": meta["description"],
                    "parameters": meta.get("parameters", {"type": "object", "properties": {}, "required": []})
                }
            })
        return schemas

    async def execute(self, name: str, arguments: Dict[str, Any], task_id: str, **kwargs) -> Dict[str, Any]:
        """
        Executes a registered tool securely after validating permissions and 
        handling human-in-the-loop authorization gates via SecurityManager.
        """
        from backend.security.security_manager import security_manager

        tool = self.get_tool(name)
        if not tool:
            error_msg = f"Tool not found in registry: [{name}]"
            logger.error(error_msg)
            return {"success": False, "error": error_msg, "result": None}

        if not task_id:
            error_msg = f"A valid task_id is mandatory for secure tool execution [{name}]."
            logger.error(error_msg)
            return {"success": False, "error": error_msg, "result": None}

        metadata = self.get_metadata(name) or {}
        requires_approval = metadata.get("requires_approval", False)

        try:
            intent = f"EXECUTE_TOOL_{name.upper()}"

            target_agents = kwargs.get("target_agents")
            if not target_agents:
                error_msg = f"A valid 'target_agents' list is mandatory for secure tool execution [{name}]."
                logger.error(error_msg)
                return {"success": False, "error": error_msg, "result": None}

            # Canonical contract: evaluate_security_gate accepts no `db` parameter.
            # Approval binding re-validation (existing APPROVED approvals) is performed
            # inside SecurityManager itself; an "ALLOWED" decision is authoritative.
            auth_result = await security_manager.evaluate_security_gate(
                task_id=task_id,
                intent=intent,
                target_agents=target_agents,
                tool_name=name,
                parameters=arguments
            )

            permission_granted = auth_result.get("permission_granted", False)
            status = auth_result.get("status", "DENIED")
            approval_id = auth_result.get("approval_id")

            # Canonical statuses: "ALLOWED" | "APPROVAL_REQUIRED" | "DENIED"
            if status == "APPROVAL_REQUIRED":
                logger.info(f"Tool [{name}] execution halted pending human approval. Approval ID: {approval_id}")
                return {
                    "success": False,
                    "requires_approval": True,
                    "approval_id": approval_id,
                    "error": "Action requires explicit human approval before execution.",
                    "result": None
                }

            if not permission_granted or status == "DENIED":
                error_msg = f"Security access denied for tool [{name}]: {auth_result.get('reason', 'Unauthorized')}"
                logger.warning(error_msg)
                return {"success": False, "error": error_msg, "result": None}

        except Exception as sec_err:
            logger.error(f"Security evaluation failed for tool [{name}]: {sec_err}")
            return {"success": False, "error": f"Security evaluation error: {str(sec_err)}", "result": None}

        try:
            logger.info(f"Executing security-validated tool [{name}] for task [{task_id}]")

            if isinstance(tool, BaseTool):
                tool_result: ToolResult = await tool.execute(**arguments)
                return {
                    "success": tool_result.success,
                    "error": tool_result.error,
                    "result": tool_result.data,
                    "execution_time_ms": tool_result.execution_time_ms,
                    "metadata": tool_result.metadata
                }
            else:
                start_time = time.perf_counter()
                
                sig = inspect.signature(tool)
                call_kwargs = {}
                for param_name in sig.parameters:
                    if param_name in kwargs:
                        call_kwargs[param_name] = kwargs[param_name]

                if inspect.iscoroutinefunction(tool):
                    result = await tool(**arguments, **call_kwargs)
                else:
                    result = tool(**arguments, **call_kwargs)

                duration = round((time.perf_counter() - start_time) * 1000, 2)
                return {
                    "success": True, 
                    "error": None, 
                    "result": result,
                    "execution_time_ms": duration,
                    "metadata": {"legacy_decorator": True}
                }

        except Exception as e:
            logger.error(f"Error executing tool [{name}]: {e}")
            return {"success": False, "error": str(e), "result": None}

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a registered tool after the caller has passed the canonical security gate.

        AgentRuntime and VerificationEngine are the only callers of this low-level
        execution primitive.  Keeping it separate from ``execute`` prevents a
        second, incompatible security evaluation while preserving ``execute`` for
        legacy callers that supply their own task and database context.
        """
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Tool not found in registry: [{name}]")

        if isinstance(tool, BaseTool):
            result = await tool.execute(**arguments)
            if not result.success:
                raise RuntimeError(result.error or f"Tool [{name}] reported failure.")
            return result.data

        if inspect.iscoroutinefunction(tool):
            return await tool(**arguments)
        return tool(**arguments)


# Instantiate the global registry singleton
tool_registry = ToolRegistry()


def register_all_tools() -> None:
    """
    Explicitly registers all 34 audited J.A.R.V.I.S. tools across all 10 batches
    into the central tool_registry during application startup.
    """
    # --- Batch 1: Cloud Imports ---
    from backend.tools.cloud.google_ads import GoogleAdsTool
    from backend.tools.cloud.meta_ads import MetaAdsTool
    from backend.tools.cloud.cloudflare import CloudflareReaderTool, CloudflareWriterTool
    from backend.tools.cloud.google_services import GoogleServicesReaderTool, GoogleServicesWriterTool

    # --- Batch 2: Code Imports ---
    from backend.tools.code.code_executor import CodeExecutorTool
    from backend.tools.code.test_runner import TestRunnerTool
    from backend.tools.code.code_analyzer import CodeAnalyzerTool

    # --- Batch 3: Communication Imports ---
    from backend.tools.communication.messaging import MessagingTool
    from backend.tools.communication.notifications import NotificationTool
    from backend.tools.communication.email import EmailTool

    # --- Batch 4: Computer Imports ---
    from backend.tools.computer.keyboard_control import KeyboardControlTool
    from backend.tools.computer.mouse_control import MouseControlTool
    from backend.tools.computer.window_control import WindowReaderTool, WindowWriterTool
    from backend.tools.computer.app_launcher import AppLauncherTool
    from backend.tools.computer.computer_macro import ComputerMacroTool

    # --- Batch 5: Files Imports ---
    from backend.tools.files.file_manager import FileManagerReaderTool, FileManagerWriterTool
    from backend.tools.files.file_reader import FileReaderTool
    from backend.tools.files.file_writer import FileWriterTool
    from backend.tools.files.file_editor import FileEditorTool

    # --- Batch 6: System Imports ---
    from backend.tools.system.system_info import SystemInfoTool
    from backend.tools.system.process_manager import ProcessReaderTool, ProcessWriterTool
    from backend.tools.system.terminal import TerminalTool

    # --- Batch 7: Vision Imports ---
    from backend.tools.vision.screen_capture import ScreenCaptureTool
    from backend.tools.vision.screen_analyzer import ScreenAnalyzerTool

    # --- Batch 8: Voice Imports ---
    from backend.tools.voice.text_to_speech import TextToSpeechTool
    from backend.tools.voice.wake_word import WakeWordTool
    from backend.tools.voice.speech_to_text import SpeechToTextTool

    # --- Batch 9: Web Imports ---
    from backend.tools.web.web_fetch import WebFetchTool
    from backend.tools.web.web_search import WebSearchTool
    from backend.tools.web.browser_control import BrowserControlTool

    tools_to_register = [
        # Batch 1: Cloud
        GoogleAdsTool,
        MetaAdsTool,
        CloudflareReaderTool,
        CloudflareWriterTool,
        GoogleServicesReaderTool,
        GoogleServicesWriterTool,
        # Batch 2: Code
        CodeExecutorTool,
        TestRunnerTool,
        CodeAnalyzerTool,
        # Batch 3: Communication
        MessagingTool,
        NotificationTool,
        EmailTool,
        # Batch 4: Computer
        KeyboardControlTool,
        MouseControlTool,
        WindowReaderTool,
        WindowWriterTool,
        AppLauncherTool,
        ComputerMacroTool,
        # Batch 5: Files
        FileManagerReaderTool,
        FileManagerWriterTool,
        FileReaderTool,
        FileWriterTool,
        FileEditorTool,
        # Batch 6: System
        SystemInfoTool,
        ProcessReaderTool,
        ProcessWriterTool,
        TerminalTool,
        # Batch 7: Vision
        ScreenCaptureTool,
        ScreenAnalyzerTool,
        # Batch 8: Voice
        TextToSpeechTool,
        WakeWordTool,
        SpeechToTextTool,
        # Batch 9: Web
        WebFetchTool,
        WebSearchTool,
        BrowserControlTool
    ]

    for tool_cls in tools_to_register:
        tool_registry.register_tool(tool_cls)

    logger.info(f"Successfully registered {len(tools_to_register)} tools into the central registry.")
    print(f"✅ Successfully loaded and registered all {len(tools_to_register)} J.A.R.V.I.S. tools.")
