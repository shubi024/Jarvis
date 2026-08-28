import logging
import asyncio
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.Notifications")

class NotificationInput(BaseModel):
    title: str = Field(description="The title header of the desktop notification.")
    message: str = Field(description="The main body text of the notification.")
    timeout: int = Field(default=10, ge=1, le=60, description="Duration in seconds.")

class NotificationTool(BaseTool):
    name = "desktop_notification"
    description = "Displays a native OS desktop notification pop-up message to the user."
    category = "communication"
    args_schema = NotificationInput
    risk_level = "low"
    requires_approval = False # Passive notification, no destructive state changes

    async def _run(self, title: str, message: str, timeout: int = 10) -> str:
        def _notify():
            try:
                from plyer import notification
            except ImportError:
                raise RuntimeError("The 'plyer' library is required. Run 'pip install plyer'.")
            
            notification.notify(title=title, message=message, app_name="J.A.R.V.I.S.", timeout=timeout)

        try:
            logger.info(f"Triggering desktop notification: '{title}'")
            await asyncio.to_thread(_notify)
            return f"Successfully displayed desktop notification: '{title}'."
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise e
            raise RuntimeError(f"Could not display notification: {str(e)}")