from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

class TaskExecuteRequest(BaseModel):
    """Schema for requesting the execution of an AI task or intent through J.A.R.V.I.S."""
    intent: str = Field(..., description="Natural-language command or primary intent (e.g., 'analyze why CPA increased')")
    command: Optional[str] = Field(None, description="Explicit natural-language command; overrides `intent` when provided")
    target_agents: List[str] = Field(default_factory=list, description="Optional specialist agent hints (e.g., ['friday', 'veronica']); Brain may override via capability matching")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Payload parameters required for task execution")
    client_scope: Optional[str] = Field(None, description="Optional client scope identifier for isolation")
    project_scope: Optional[str] = Field(None, description="Optional project scope identifier for isolation")


class TaskResponse(BaseModel):
    """Schema representing the status and outcome of a J.A.R.V.I.S. task execution."""
    task_id: str = Field(..., description="Unique identifier for the task execution")
    intent: str = Field(..., description="The executed intent")
    status: str = Field(..., description="Current operational status (e.g., COMPLETED, WAITING_APPROVAL, FAILED)")
    requires_approval: bool = Field(False, description="Indicates whether the task is halted awaiting explicit human approval")
    approval_id: Optional[str] = Field(None, description="Unique approval ID if human authorization is required")
    risk_level: Optional[str] = Field("low", description="The risk level associated with the executed tool or task (e.g., low, high)")
    result_data: Dict[str, Any] = Field(default_factory=dict, description="Execution results or summary data from agents")


class ApprovalResolveRequest(BaseModel):
    """Schema for resolving a pending human-in-the-loop approval gate."""
    approval_id: str = Field(..., description="Unique approval ID to resolve")
    approved: bool = Field(..., description="True to approve execution, False to reject")
    resolved_by: str = Field(default="human_user", description="Identifier of the user granting or denying approval")


class ApprovalStatusResponse(BaseModel):
    """Schema representing the status of an approval record."""
    approval_id: str
    task_id: str
    intent: str
    status: str
    target_agents: List[str]
    parameters_summary: Dict[str, Any]
    risk_level: Optional[str] = "low"
    created_time: float
    resolved_time: Optional[float] = None
    resolved_by: Optional[str] = None