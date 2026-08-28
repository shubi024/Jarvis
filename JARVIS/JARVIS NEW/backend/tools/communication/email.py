import os
import smtplib
import logging
import asyncio
from email.message import EmailMessage
from pydantic import BaseModel, Field, field_validator
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.Email")

class EmailInput(BaseModel):
    to_address: str = Field(description="The recipient's email address.")
    subject: str = Field(description="The subject line of the email.")
    body: str = Field(description="The main text content of the email.")

    @field_validator("to_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if "@" not in v or "." not in v:
            raise ValueError(f"Invalid email address format: '{v}'")
        return v

class EmailTool(BaseTool):
    name = "email_sender"
    description = "Sends an email to a specified recipient. Use only when explicitly requested by the user."
    category = "communication"
    args_schema = EmailInput
    risk_level = "high"
    requires_approval = True

    async def _run(self, to_address: str, subject: str, body: str) -> str:
        smtp_host = os.getenv("SMTP_HOST")
        raw_port = os.getenv("SMTP_PORT", "587")
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASSWORD")
        use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

        if not all([smtp_host, smtp_user, smtp_pass]):
            raise ValueError("Missing SMTP credentials in environment (SMTP_HOST, SMTP_USER, SMTP_PASSWORD).")

        try:
            smtp_port = int(raw_port)
        except ValueError:
            raise ValueError("Invalid SMTP_PORT value in environment. Must be an integer.")

        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = smtp_user
        msg['To'] = to_address

        def _send_email():
            if use_ssl:
                with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                    server.login(smtp_user, smtp_pass)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.send_message(msg)

        try:
            logger.info(f"Attempting to send email to {to_address} with subject '{subject}'")
            await asyncio.to_thread(_send_email)
            return f"Successfully sent email to '{to_address}' with subject '{subject}'."
        except smtplib.SMTPAuthenticationError:
            raise RuntimeError("SMTP Authentication failed. Check your SMTP_USER and SMTP_PASSWORD.")
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise e
            raise RuntimeError(f"Email transmission failed: {str(e)}")