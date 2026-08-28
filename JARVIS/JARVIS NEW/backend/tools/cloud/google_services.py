import os
import datetime
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field
from backend.tools.base_tool import BaseTool

logger = logging.getLogger("JARVIS.Tools.GoogleServices")

def _get_google_service(service_name: str, version: str):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    
    token_path = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
    if not os.path.exists(token_path): raise FileNotFoundError(f"Token not found at {token_path}")
    creds = Credentials.from_authorized_user_file(token_path, ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/drive.readonly"])
    return build(service_name, version, credentials=creds)

class GoogleServicesReadInput(BaseModel):
    service_type: Literal["calendar", "drive"] = Field(description="Google service to query.")
    action: Literal["list_events", "search_files"] = Field(description="Read action.")
    query: Optional[str] = Field(default=None, description="Search string for Drive.")

class GoogleServicesReaderTool(BaseTool):
    name = "google_services_reader"
    description = "Read-only Google Workspace actions (list calendar events, search drive)."
    category = "cloud"
    args_schema = GoogleServicesReadInput
    risk_level = "low"
    requires_approval = False

    async def _run(self, service_type: str, action: str, query: Optional[str] = None) -> str:
        if service_type == "calendar" and action == "list_events":
            service = _get_google_service("calendar", "v3")
            now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
            events = service.events().list(calendarId="primary", timeMin=now, maxResults=10, singleEvents=True, orderBy="startTime").execute().get("items", [])
            return "\n".join([f"• {e['start'].get('dateTime', e['start'].get('date'))}: {e.get('summary', 'Untitled')}" for e in events]) if events else "No events found."
            
        elif service_type == "drive" and action == "search_files":
            service = _get_google_service("drive", "v3")
            q_param = f"name contains '{query.replace('\\', '\\\\').replace('\'', '\\\'')}'" if query else None
            files = service.files().list(q=q_param, pageSize=10, fields="files(id, name)").execute().get("files", [])
            return "\n".join([f"• {f['name']} (ID: {f['id']})" for f in files]) if files else "No files found."
        
        raise ValueError(f"Unsupported read action.")

class GoogleServicesWriteInput(BaseModel):
    event_title: str = Field(description="Calendar event title.")
    event_start: str = Field(description="Start time (ISO).")
    event_end: str = Field(description="End time (ISO).")

class GoogleServicesWriterTool(BaseTool):
    name = "google_services_writer"
    description = "Creates events in Google Calendar."
    category = "cloud"
    args_schema = GoogleServicesWriteInput
    risk_level = "medium"
    requires_approval = True

    async def _run(self, event_title: str, event_start: str, event_end: str) -> str:
        service = _get_google_service("calendar", "v3")
        event_body = {"summary": event_title, "start": {"dateTime": event_start}, "end": {"dateTime": event_end}}
        created = service.events().insert(calendarId="primary", body=event_body).execute()
        return f"Successfully created event '{event_title}' (ID: {created.get('id')})."