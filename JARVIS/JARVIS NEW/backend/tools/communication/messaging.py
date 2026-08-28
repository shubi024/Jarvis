import os
import logging
import asyncio
from pydantic import BaseModel, Field, field_validator
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.Messaging")

class MessagingInput(BaseModel):
    recipient: str = Field(description="The destination phone number (E.164 format) or channel ID.")
    message: str = Field(description="The text body of the message to send.")

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, v: str) -> str:
        if not v.startswith("+") and not v.startswith("C") and len(v) < 3:
            raise ValueError("Invalid recipient format. Must be E.164 (e.g., '+1...') or identifier.")
        return v

class MessagingTool(BaseTool):
    name = "messaging"
    description = "Sends an SMS or instant message via Twilio to a specified recipient."
    category = "communication"
    args_schema = MessagingInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, recipient: str, message: str) -> str:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        from_number = os.getenv("TWILIO_PHONE_NUMBER")

        if not all([account_sid, auth_token, from_number]):
            raise ValueError("Missing Twilio credentials in environment (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER).")

        def _send_sms():
            try:
                from twilio.rest import Client
            except ImportError:
                raise RuntimeError("The 'twilio' library is required. Run 'pip install twilio'.")
            
            client = Client(account_sid, auth_token)
            return client.messages.create(body=message, from_=from_number, to=recipient)

        try:
            logger.info(f"Initiating SMS dispatch to {recipient} via Twilio")
            sent_msg = await asyncio.to_thread(_send_sms)
            return f"Successfully sent message to '{recipient}' (SID: {sent_msg.sid})."
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise e
            raise RuntimeError(f"Messaging transmission failed: {str(e)}")