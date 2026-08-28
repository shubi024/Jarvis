import logging
import asyncio
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.KeyboardControl")

class KeyboardControlInput(BaseModel):
    target_window: str = Field(description="A substring of the target application's window title to guarantee context.")
    action: Literal["type", "press", "hotkey"] = Field(description="Action to perform: type, press, or hotkey.")
    text: Optional[str] = Field(default=None, description="Text string to type.")
    keys: Optional[List[str]] = Field(default=None, description="Keys to press.")

class KeyboardControlTool(BaseTool):
    name = "keyboard_control"
    description = "Controls the keyboard with just-in-time active window focus verification."
    category = "computer"
    args_schema = KeyboardControlInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, target_window: str, action: str, text: Optional[str] = None, keys: Optional[List[str]] = None) -> str:
        BANNED_KEYS = {"win", "winleft", "winright", "command", "option", "power", "sleep"}
        MAX_TYPE_LENGTH = 500

        def _assert_active_window(gw_module):
            """Just-in-time check to verify the target window holds active focus immediately prior to an action."""
            active_win = gw_module.getActiveWindow()
            if not active_win or target_window.lower() not in active_win.title.lower():
                current = active_win.title if active_win else "None"
                raise SecurityError(
                    f"Context mismatch immediately before execution: Expected '{target_window}', but active window is '{current}'. Aborting action."
                )
            return active_win

        def _execute_keyboard():
            import pyautogui
            import pygetwindow as gw
            pyautogui.FAILSAFE = True

            # 1. Initial lookup and focus attempt
            matched = gw.getWindowsWithTitle(target_window)
            if not matched:
                raise RuntimeError(f"Context Error: No open window found matching '{target_window}'.")
            
            target = matched[0]
            try:
                target.activate()
            except Exception:
                pass

            # 2. Input validation & Guardrails
            VALID_KEYS = set(pyautogui.KEYBOARD_KEYS)

            if action == "type":
                if not text:
                    raise ValueError("Text parameter required.")
                if len(text) > MAX_TYPE_LENGTH:
                    raise SecurityError(f"Text payload exceeds maximum allowed length ({MAX_TYPE_LENGTH} chars).")
                
                # JIT assertion right before typing
                active = _assert_active_window(gw)
                pyautogui.write(text, interval=0.01)
                return f"Successfully typed text into '{active.title}'."

            elif action in ["press", "hotkey"]:
                if not keys:
                    raise ValueError("Keys parameter required.")
                normalized = [k.lower() for k in keys]

                invalid_keys = [k for k in normalized if k not in VALID_KEYS]
                if invalid_keys:
                    raise ValueError(f"Invalid keys: {invalid_keys}.")

                banned_usage = [k for k in normalized if k in BANNED_KEYS]
                if banned_usage:
                    raise SecurityError(f"Execution blocked: Restricted system keys {banned_usage}.")

                if action == "hotkey" and "alt" in normalized and "f4" in normalized:
                    raise SecurityError("Execution blocked: Alt+F4 is restricted.")

                # JIT assertion right before pressing
                active = _assert_active_window(gw)
                if action == "press":
                    pyautogui.press(normalized)
                    return f"Successfully pressed keys in '{active.title}': {', '.join(normalized)}."
                else:
                    pyautogui.hotkey(*normalized)
                    return f"Successfully executed hotkey in '{active.title}': {' + '.join(normalized)}."

        try:
            return await asyncio.to_thread(_execute_keyboard)
        except SecurityError as sec_err:
            logger.error(str(sec_err))
            raise RuntimeError(str(sec_err))
        except Exception as e:
            if "FailSafeException" in type(e).__name__:
                raise RuntimeError("Operation aborted by user (PyAutoGUI FailSafe triggered).")
            if isinstance(e, RuntimeError):
                raise e
            raise RuntimeError(f"Keyboard control failed: {str(e)}")

class SecurityError(RuntimeError):
    pass