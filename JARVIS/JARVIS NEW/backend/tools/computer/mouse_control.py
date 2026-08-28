import logging
import asyncio
from typing import Optional, Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.MouseControl")

class MouseControlInput(BaseModel):
    target_window: str = Field(description="A substring of the target window title. Mouse coordinates must fall within its physical bounds.")
    action: Literal["move", "click", "drag", "scroll"] = Field(description="Action to perform.")
    x: Optional[int] = Field(default=None, description="X screen coordinate.")
    y: Optional[int] = Field(default=None, description="Y screen coordinate.")
    button: Literal["left", "right", "middle"] = Field(default="left", description="Mouse button.")
    clicks: int = Field(default=1, ge=1, le=5, description="Number of clicks (max 5).")
    scroll_amount: Optional[int] = Field(default=0, description="Scroll amount.")

class MouseControlTool(BaseTool):
    name = "mouse_control"
    description = "Controls the mouse with just-in-time window context, bounds checking, and OS safe zones."
    category = "computer"
    args_schema = MouseControlInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, target_window: str, action: str, x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1, scroll_amount: int = 0) -> str:
        SAFE_MARGIN_Y = 50

        def _assert_window_and_bounds(gw_module, pyauto_module):
            """Just-in-time verification of active focus and physical window boundary containment."""
            active_win = gw_module.getActiveWindow()
            if not active_win or target_window.lower() not in active_win.title.lower():
                current = active_win.title if active_win else "None"
                raise SecurityError(
                    f"Context mismatch immediately before mouse action: Expected '{target_window}', but active window is '{current}'. Aborting."
                )

            if x is not None or y is not None:
                sw, sh = pyauto_module.size()
                if x is not None and not (0 <= x <= sw):
                    raise ValueError(f"X {x} is out of screen bounds (0 - {sw}).")
                if y is not None:
                    if not (0 <= y <= sh):
                        raise ValueError(f"Y {y} is out of screen bounds (0 - {sh}).")
                    if y < SAFE_MARGIN_Y or y > (sh - SAFE_MARGIN_Y):
                        raise SecurityError(f"Action blocked: Y-coordinate {y} is inside OS margin ({SAFE_MARGIN_Y}px).")

                # JIT boundary check against actual current window position
                if not (active_win.left <= x <= active_win.right and active_win.top <= y <= active_win.bottom):
                    raise SecurityError(
                        f"Action blocked: Coordinates ({x}, {y}) are outside the active bounds of '{active_win.title}' "
                        f"(Left:{active_win.left}, Top:{active_win.top}, Right:{active_win.right}, Bottom:{active_win.bottom})."
                    )
            return active_win

        def _execute_mouse():
            import pyautogui
            import pygetwindow as gw
            pyautogui.FAILSAFE = True

            # 1. Initial activation
            matched = gw.getWindowsWithTitle(target_window)
            if not matched:
                raise RuntimeError(f"Context Error: No open window found matching '{target_window}'.")

            target = matched[0]
            try:
                target.activate()
            except Exception:
                pass

            # 2. JIT Verification and Execution
            if action == "move":
                if x is None or y is None:
                    raise ValueError("X and Y coordinates required for 'move'.")
                active = _assert_window_and_bounds(gw, pyautogui)
                pyautogui.moveTo(x, y, duration=0.25)
                return f"Moved mouse to ({x}, {y}) within '{active.title}'."

            elif action == "click":
                if x is None or y is None:
                    raise ValueError("Explicit X and Y coordinates required for safe clicking.")
                active = _assert_window_and_bounds(gw, pyautogui)
                pyautogui.click(x=x, y=y, button=button, clicks=clicks)
                return f"Clicked {button} button {clicks} time(s) at ({x}, {y}) inside '{active.title}'."

            elif action == "drag":
                if x is None or y is None:
                    raise ValueError("X and Y coordinates required for 'drag'.")
                active = _assert_window_and_bounds(gw, pyautogui)
                pyautogui.dragTo(x, y, duration=0.5, button=button)
                return f"Dragged mouse to ({x}, {y}) within '{active.title}'."

            elif action == "scroll":
                if not scroll_amount:
                    raise ValueError("scroll_amount required for 'scroll'.")
                active = _assert_window_and_bounds(gw, pyautogui)
                pyautogui.scroll(scroll_amount, x=x, y=y)
                return f"Scrolled by {scroll_amount} units in '{active.title}'."

        try:
            return await asyncio.to_thread(_execute_mouse)
        except SecurityError as sec_err:
            logger.error(str(sec_err))
            raise RuntimeError(str(sec_err))
        except Exception as e:
            if "FailSafeException" in type(e).__name__:
                raise RuntimeError("Operation aborted by user (PyAutoGUI FailSafe triggered).")
            if isinstance(e, RuntimeError):
                raise e
            raise RuntimeError(f"Mouse control failed: {str(e)}")

class SecurityError(RuntimeError):
    pass