from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# --- Canonical Enums ---

class TaskStatus(str, Enum):
    """Lifecycle states for a task moving through the orchestration engine."""
    RECEIVED = "RECEIVED"
    PLANNED = "PLANNED"
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    WAITING_INPUT = "WAITING_INPUT"
    APPROVAL = "APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class ResultStatus(str, Enum):
    """Definitive end states returned by Agents and Tools."""
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"

class ApprovalState(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    UNVERIFIABLE = "UNVERIFIABLE"


# --- Sub-components & Metadata ---

class TaskDependency(BaseModel):
    task_id: str = Field(..., description="The ID of the task that must complete first.")
    required_status: TaskStatus = Field(default=TaskStatus.COMPLETED)
    is_blocking: bool = Field(default=True)

class TaskResource(BaseModel):
    resource_type: str = Field(..., description="e.g., 'file', 'api_key', 'database'")
    uri: str = Field(..., description="Path, URL, or identifier for the resource.")
    is_required: bool = Field(default=True)

class ExpectedOutput(BaseModel):
    format: str = Field(..., description="e.g., 'json', 'markdown', 'binary_file'")
    description: str = Field(..., description="Human-readable description of what constitutes success.")
    schema_definition: Optional[Dict[str, Any]] = Field(default=None, description="Optional JSON schema for strict validation.")

class ActionRecord(BaseModel):
    task_id: str = Field(..., description="Task ID this action belongs to.")
    agent_id: Optional[str] = Field(default=None, description="Agent performing the action.")
    tool_name: Optional[str] = Field(default=None, description="Tool invoked.")
    status: str = Field(..., description="e.g., 'success', 'failure'")
    timestamp: datetime = Field(default_factory=utc_now)
    details: Dict[str, Any] = Field(default_factory=dict)

class ExecutionMetadata(BaseModel):
    correlation_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = Field(default=0)
    duration_ms: Optional[float] = None

class VerificationContract(BaseModel):
    verification_type: str = Field(..., description="e.g., 'STATE_VERIFICATION', 'OUTPUT_CHECK', 'API_VALIDATION'")
    expected_outcome: str = Field(...)
    required_evidence: List[str] = Field(default_factory=list)
    method: str = Field(default="automated", description="automated or human")

class PermissionScope(BaseModel):
    permission_level: str = Field(default="L1", description="L0 to L4 authorization tier.")
    allowed_actions: List[str] = Field(default_factory=list)
    allowed_resources: List[str] = Field(default_factory=list)
    forbidden_resources: List[str] = Field(default_factory=list)
    scope: Dict[str, Any] = Field(default_factory=dict, description="Client or project isolation scopes.")
    purpose: Optional[str] = None
    max_risk_level: str = Field(default="low")


# --- Core Execution Packages ---

class ResultPackage(BaseModel):
    status: ResultStatus = Field(..., description="The definitive end status of the execution.")
    summary: str = Field(..., description="Agent or tool summary of what occurred.")
    findings: Dict[str, Any] = Field(default_factory=dict, description="Structured data returned.")
    actions_performed: List[ActionRecord] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Proof of execution/state change.")
    limitations: List[str] = Field(default_factory=list, description="What couldn't be achieved.")
    errors: List[str] = Field(default_factory=list)
    next_action: Optional[str] = Field(default=None, description="Recommended next step if partial/failed.")

class SubtaskPackage(BaseModel):
    subtask_id: str
    parent_task_id: str
    workflow_id: Optional[str] = None
    
    objective: str
    assigned_agent: str
    
    dependencies: List[TaskDependency] = Field(default_factory=list)
    input_data: Dict[str, Any] = Field(default_factory=dict)
    expected_output: ExpectedOutput
    permission_scope: PermissionScope
    verification_contract: VerificationContract
    
    status: TaskStatus = Field(default=TaskStatus.PLANNED)
    result: Optional[ResultPackage] = None

class TaskPackage(BaseModel):
    """
    The absolute canonical task payload passed between Brain, Queue, and Agent Runtime.
    """
    task_id: str
    parent_task_id: Optional[str] = None
    workflow_id: Optional[str] = None
    requester: str = Field(..., description="User or system entity requesting the task.")
    
    intent: str
    objective: str
    status: TaskStatus = Field(default=TaskStatus.RECEIVED)
    dependencies: List[TaskDependency] = Field(default_factory=list)
    
    # Execution Rules
    priority: int = Field(default=1)
    deadline: Optional[datetime] = None
    constraints: List[str] = Field(default_factory=list)
    do_rules: List[str] = Field(default_factory=list)
    do_not_rules: List[str] = Field(default_factory=list)
    
    # Scope & Assignments
    client_scope: Optional[str] = None
    project_scope: Optional[str] = None
    target_agents: List[str] = Field(default_factory=list)
    selected_tools: List[str] = Field(default_factory=list)
    tool_parameters: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Parameters mapped by tool_name.")
    permission_scope: PermissionScope
    resources: List[TaskResource] = Field(default_factory=list)
    
    expected_output: ExpectedOutput
    
    # Approvals & Verification
    approval_state: ApprovalState = Field(default=ApprovalState.NOT_REQUIRED)
    approval_reference: Optional[str] = None
    
    verification_contract: VerificationContract
    verification_status: VerificationStatus = Field(default=VerificationStatus.PENDING)
    verification_evidence: Optional[Dict[str, Any]] = None
    
    # Execution State
    execution_metadata: ExecutionMetadata = Field(default_factory=ExecutionMetadata)
    subtasks: List[SubtaskPackage] = Field(default_factory=list)
    result: Optional[ResultPackage] = None
