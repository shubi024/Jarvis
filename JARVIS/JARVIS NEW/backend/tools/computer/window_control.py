import logging
import asyncio
from typing import Optional, Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.WindowControl")

# --- READER TOOL (LOW RISK, NO APPROVAL) ---
class WindowReadInput(BaseModel):
    action: Literal["list", "get_active"] = Field(description="Read action for windows.")

class WindowReaderTool(BaseTool):
    name = "window_reader"
    description = "Lists currently open OS windows or gets the active window title."
    category = "computer"
    args_schema = WindowReadInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, action: str) -> str:
        def _read_windows():
            import pygetwindow as gw
            if action == "list":
                titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
                return f"Currently open windows: {titles}"
            elif action == "get_active":
                active_win = gw.getActiveWindow()
                if active_win and active_win.title: return f"Active window is: '{active_win.title}'"
                return "No explicitly active window detected."
                
        try:
            return await asyncio.to_thread(_read_windows)
        except Exception as e:
            raise RuntimeError(f"Window read failed: {str(e)}")

# --- WRITER TOOL (MEDIUM RISK, REQUIRES APPROVAL) ---
class WindowWriteInput(BaseModel):
    action: Literal["minimize", "maximize", "activate", "close"] = Field(description="Action to perform.")
    title: str = Field(description="A substring of the window title to target.")

class WindowWriterTool(BaseTool):
    name = "window_writer"
    description = "Minimizes, maximizes, activates, or closes specific OS windows."
    category = "computer"
    args_schema = WindowWriteInput
    risk_level = "medium"
    requires_approval = True

    async def _run(self, action: str, title: str) -> str:
        def _write_windows():
            import pygetwindow as gw
            matched = gw.getWindowsWithTitle(title)
            if not matched: raise ValueError(f"No window found matching: '{title}'")
            target = matched[0]
            
            if action == "minimize": target.minimize()
            elif action == "maximize": target.maximize()
            elif action == "activate": target.activate()
            elif action == "close": target.close()
            return f"Successfully executed '{action}' on window: '{target.title}'"

        try:
            return await asyncio.to_thread(_write_windows)
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"Window write failed: {str(e)}")