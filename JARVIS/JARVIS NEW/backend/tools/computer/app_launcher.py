import os
import sys
import logging
import subprocess
import asyncio
from typing import Optional, List
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.AppLauncher")

class AppLauncherInput(BaseModel):
    app_alias: str = Field(description="The pre-approved alias of the application to launch (e.g., 'browser', 'editor').")
    arguments: Optional[List[str]] = Field(default=None, description="Command-line arguments.")

class AppLauncherTool(BaseTool):
    name = "app_launcher"
    description = "Launches pre-approved applications on the host computer. Arbitrary commands are strictly blocked."
    category = "computer"
    args_schema = AppLauncherInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, app_alias: str, arguments: Optional[List[str]] = None) -> str:
        # 1. Strict Allowlist Enforcement
        # Expected format: "browser=chrome.exe;editor=notepad.exe;terminal=wt.exe"
        allowlist_raw = os.getenv("JARVIS_APP_ALLOWLIST", "editor=notepad.exe;calculator=calc.exe")
        allowed_apps = dict(item.split("=") for item in allowlist_raw.split(";") if "=" in item)

        if app_alias.lower() not in allowed_apps:
            raise SecurityError(
                f"App launch blocked: '{app_alias}' is not in the approved allowlist. "
                f"Approved aliases: {list(allowed_apps.keys())}"
            )

        executable_path = allowed_apps[app_alias.lower()]
        cmd = [executable_path]
        if arguments: cmd.extend(arguments)
            
        def _launch():
            kwargs = {}
            if sys.platform == "win32":
                kwargs.update(creationflags=0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                kwargs.update(start_new_session=True)
                
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs
            )
            return process.pid

        try:
            pid = await asyncio.to_thread(_launch)
            logger.info(f"Launched approved application: {cmd} (PID: {pid})")
            return f"Successfully launched '{app_alias}' (Executable: {executable_path}, PID: {pid})."
        except FileNotFoundError:
            raise RuntimeError(f"Approved executable not found: '{executable_path}'. Verify system PATH.")
        except Exception as e:
            if isinstance(e, RuntimeError): raise e
            raise RuntimeError(f"Error launching application: {str(e)}")

class SecurityError(RuntimeError):
    pass