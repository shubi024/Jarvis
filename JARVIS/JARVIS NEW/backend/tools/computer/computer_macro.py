"""
backend/tools/computer/computer_macro.py
J.A.R.V.I.S. Computer-Control Macro Composer (JARVIS Master Spec §7: operate the computer).

Composes multiple primitive computer actions (launch app, keyboard, mouse, waits)
into ONE ordered, approval-gated execution. This closes the "single-tool only" gap:
multi-step computer workflows become a single consequential action that passes the
security gate once, then executes its steps sequentially with strict validation:

  - Every step action must be in a fixed allowlist (no arbitrary code/commands).
  - Keyboard/mouse steps reuse the hardened primitives' own guardrails by invoking
    the registered tool instances directly (window-focus JIT checks, bounds checks,
    banned keys, OS safe margins all still apply per step).
  - The macro itself is risk_level=high + requires_approval=True, so composing an
    automation NEVER bypasses the human approval boundary.
  - Honest reporting: each step records success/failure; stop_on_error halts on the
    first failure and the result reflects exactly what executed.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.ComputerMacro")

# Fixed allowlist of composable primitive actions.
ALLOWED_ACTIONS = {
    "launch_app",     # params: app_alias, arguments?
    "type_text",      # params: target_window, text
    "press_key",      # params: target_window, keys[]
    "hotkey",         # params: target_window, keys[]
    "mouse_move",     # params: target_window, x, y
    "mouse_click",    # params: target_window, x, y, button?, clicks?
    "wait",           # params: seconds (0.1 - 10)
}

MAX_STEPS = 20


class MacroStep(BaseModel):
    action: str = Field(description=f"One of: {', '.join(sorted(ALLOWED_ACTIONS))}.")
    params: Dict[str, Any] = Field(default_factory=dict)


class ComputerMacroInput(BaseModel):
    name: str = Field(description="Human-readable name of this macro for audit purposes.")
    steps: List[MacroStep] = Field(description=f"Ordered steps (max {MAX_STEPS}).")
    stop_on_error: bool = Field(default=True, description="Halt remaining steps after any failure.")


class ComputerMacroTool(BaseTool):
    name = "computer_macro"
    description = (
        "Executes an ordered multi-step computer automation composed ONLY from approved "
        "primitives (launch_app, type_text, press_key, hotkey, mouse_move, mouse_click, wait). "
        "Every primitive's own security guardrails remain active per step."
    )
    category = "computer"
    args_schema = ComputerMacroInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, name: str, steps: List[Dict[str, Any]], stop_on_error: bool = True) -> Dict[str, Any]:
        # 1. Structural validation BEFORE executing anything
        if not steps:
            raise ValueError("Macro rejected: no steps provided.")
        if len(steps) > MAX_STEPS:
            raise ValueError(f"Macro rejected: {len(steps)} steps exceeds maximum of {MAX_STEPS}.")

        normalized_steps: List[MacroStep] = []
        for idx, raw in enumerate(steps):
            try:
                step = raw if isinstance(raw, MacroStep) else MacroStep(**raw)
            except Exception as ve:
                raise ValueError(f"Macro rejected: invalid step #{idx}: {ve}")

            if step.action not in ALLOWED_ACTIONS:
                raise ValueError(
                    f"Macro rejected: step #{idx} uses non-allowlisted action '{step.action}'. "
                    f"Permitted: {sorted(ALLOWED_ACTIONS)}"
                )
            normalized_steps.append(step)

        # 2. Sequential execution with per-step guardrails and honest recording
        results: List[Dict[str, Any]] = []
        executed_count = 0

        for idx, step in enumerate(normalized_steps):
            step_result: Dict[str, Any] = {"step": idx, "action": step.action, "status": "PENDING"}
            try:
                output = await self._execute_step(step)
                step_result["status"] = "SUCCESS"
                step_result["output"] = str(output)[:500]
                executed_count += 1
            except Exception as step_err:
                step_result["status"] = "FAILED"
                step_result["error"] = str(step_err)[:500]
                results.append(step_result)

                if stop_on_error:
                    remaining = len(normalized_steps) - idx - 1
                    return {
                        "macro": name,
                        "status": "FAILED",
                        "steps_executed": executed_count,
                        "steps_total": len(normalized_steps),
                        "steps": results,
                        "note": f"Halted after failure at step {idx}; {remaining} step(s) not executed.",
                    }
            finally:
                if step_result["status"] == "PENDING":
                    step_result["status"] = "SKIPPED"
                if step_result not in results:
                    results.append(step_result)

        return {
            "macro": name,
            "status": "COMPLETED",
            "steps_executed": executed_count,
            "steps_total": len(normalized_steps),
            "steps": results,
        }

    async def _execute_step(self, step: MacroStep) -> str:
        """Dispatches one validated step to its hardened primitive implementation."""
        params = dict(step.params or {})
        action = step.action

        # Lazy imports avoid circulars and keep startup light.
        from backend.tools.computer.app_launcher import AppLauncherTool
        from backend.tools.computer.keyboard_control import KeyboardControlTool
        from backend.tools.computer.mouse_control import MouseControlTool

        if action == "launch_app":
            tool = AppLauncherTool()
            return await tool._run(
                app_alias=params.get("app_alias"),
                arguments=params.get("arguments"),
            )

        if action in ("type_text", "press_key", "hotkey"):
            tool = KeyboardControlTool()
            kb_action = {"type_text": "type", "press_key": "press", "hotkey": "hotkey"}[action]
            return await tool._run(
                target_window=params.get("target_window"),
                action=kb_action,
                text=params.get("text"),
                keys=params.get("keys"),
            )

        if action in ("mouse_move", "mouse_click"):
            tool = MouseControlTool()
            return await tool._run(
                target_window=params.get("target_window"),
                action="move" if action == "mouse_move" else "click",
                x=params.get("x"),
                y=params.get("y"),
                button=params.get("button", "left"),
                clicks=params.get("clicks", 1),
            )

        if action == "wait":
            seconds = float(params.get("seconds", 0.5))
            if not (0.1 <= seconds <= 10.0):
                raise ValueError("Wait must be between 0.1 and 10 seconds.")
            await asyncio.sleep(seconds)
            return f"Waited {seconds}s."

        raise ValueError(f"Unhandled action '{action}' reached executor (should be impossible).")


computer_macro_tool = ComputerMacroTool()