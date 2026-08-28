import logging
import asyncio
import webbrowser
from typing import Literal
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool
from backend.tools.web.web_security import validate_secure_url

logger = logging.getLogger("JARVIS.Tools.BrowserControl")

class BrowserControlInput(BaseModel):
    action: Literal["open_tab", "open_window"] = Field(description="How to open the URL ('open_tab' or 'open_window').")
    url: str = Field(description="The complete URL to open in the host's default web browser.")

class BrowserControlTool(BaseTool):
    name = "browser_control"
    description = "Opens a verified, policy-checked URL in the host machine's default GUI web browser."
    category = "web"
    args_schema = BrowserControlInput
    risk_level = "medium"
    requires_approval = True

    async def _run(self, action: str, url: str) -> str:
        # Enforce web security/SSRF validation policy before launching local browser
        safe_url = validate_secure_url(url)

        def _open_browser():
            if action == "open_tab":
                success = webbrowser.open(safe_url, new=2)
                if not success: raise RuntimeError("The OS failed to launch the default browser.")
                return f"Successfully opened '{safe_url}' in a new tab on the host's default browser."
            elif action == "open_window":
                success = webbrowser.open(safe_url, new=1)
                if not success: raise RuntimeError("The OS failed to launch the default browser.")
                return f"Successfully opened '{safe_url}' in a completely new browser window."
            else:
                raise ValueError(f"Invalid browser action: {action}")

        try:
            result = await asyncio.to_thread(_open_browser)
            logger.info(f"Browser action '{action}' executed for URL: {safe_url}")
            return result
        except Exception as e:
            if isinstance(e, (PermissionError, RuntimeError, ValueError)): raise e
            logger.error(f"Failed to control browser for URL '{url}': {str(e)}")
            raise RuntimeError(f"Could not open the browser: {str(e)}")