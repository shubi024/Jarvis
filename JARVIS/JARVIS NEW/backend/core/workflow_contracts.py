from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

# Import canonical definitions from task_contracts
from backend.core.task_contracts import SubtaskPackage, ExpectedOutput, TaskDependency

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# --- Canonical Workflow Enums ---

class WorkflowStatus(str, Enum):
    """Lifecycle states for a multi-agent workflow."""
    PLANNED = "PLANNED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    APPROVAL = "APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"

class ExecutionMode(str, Enum):
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL = "PARALLEL"

class FailureBehavior(str, Enum):
    FAIL_WORKFLOW = "FAIL_WORKFLOW"
    CONTINUE_ON_FAILURE = "CONTINUE_ON_FAILURE"
    RETRY_SUBTASK = "RETRY_SUBTASK"
    PAUSE_FOR_INTERVENTION = "PAUSE_FOR_INTERVENTION"


# --- Workflow Graph Components ---

class AgentAssignment(BaseModel):
    agent_id: str = Field(..., description="The ID of the assigned specialist agent (e.g., 'FRIDAY').")
    role_in_workflow: str = Field(..., description="What this agent is expected to accomplish in this specific workflow.")
    allowed_communication_paths: List[str] = Field(default_factory=list, description="IDs of other agents this agent can handoff to.")

class ExecutionGroup(BaseModel):
    group_id: str = Field(..., description="Unique ID for this execution phase/group.")
    mode: ExecutionMode = Field(..., description="Whether subtasks in this group run in parallel or sequentially.")
    subtask_ids: List[str] = Field(default_factory=list, description="List of SubtaskPackage IDs belonging to this group.")
    dependencies: List[str] = Field(default_factory=list, description="List of group_ids that must complete before this group starts.")

class HandoffDefinition(BaseModel):
    source_agent_id: str
    target_agent_id: str
    trigger_condition: str = Field(..., description="Condition under which the handoff occurs.")
    expected_payload_schema: Optional[Dict[str, Any]] = None
    is_jarvis_mediated: bool = Field(default=True, description="If True, JARVIS validates the payload before passing it.")

class ApprovalGate(BaseModel):
    gate_id: str
    blocking_subtask_ids: List[str] = Field(default_factory=list, description="Subtasks that are paused until this gate is cleared.")
    condition: str = Field(..., description="Why approval is needed (e.g., 'Budget exceeds $500').")
    required_role: str = Field(default="operator")

class FailureRule(BaseModel):
    trigger_condition: str = Field(..., description="e.g., 'timeout', 'api_error', 'unverifiable_output'")
    behavior: FailureBehavior
    max_retries: int = Field(default=0)
    fallback_subtask_id: Optional[str] = Field(default=None, description="Alternative subtask to run if this fails.")

class SynthesisStage(BaseModel):
    required_outputs: List[str] = Field(default_factory=list, description="List of subtask_ids whose outputs must be combined.")
    format: ExpectedOutput
    assigned_synthesizer: str = Field(default="JARVIS", description="Usually JARVIS core, but could be EDITH for content.")

class WorkflowMetadata(BaseModel):
    priority: int = Field(default=1)
    deadline: Optional[datetime] = None
    correlation_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# --- The Canonical Workflow Definition ---

class WorkflowDefinition(BaseModel):
    """
    The canonical orchestration graph defining how subtasks, parallel groups, 
    handoffs, and approvals coordinate to achieve a larger objective.
    """
    workflow_id: str
    parent_task_id: Optional[str] = None
    requester: str = Field(..., description="User or system entity requesting the workflow.")
    trigger: str = Field(default="user_command", description="What initiated this workflow.")
    objective: str = Field(..., description="The ultimate goal of this workflow.")
    
    # Assignments & Subtasks
    agents: List[AgentAssignment] = Field(default_factory=list)
    subtasks: List[SubtaskPackage] = Field(default_factory=list)
    
    # Orchestration Graph
    execution_groups: List[ExecutionGroup] = Field(default_factory=list)
    global_dependencies: List[TaskDependency] = Field(default_factory=list, description="Dependencies outside this workflow.")
    handoffs: List[HandoffDefinition] = Field(default_factory=list)
    
    # Gates & Rules
    approval_gates: List[ApprovalGate] = Field(default_factory=list)
    failure_rules: List[FailureRule] = Field(default_factory=list)
    completion_condition: str = Field(..., description="Exact condition required for the workflow to be COMPLETED.")
    
    # Final Stage
    synthesis: SynthesisStage
    
    # State & Metadata
    status: WorkflowStatus = Field(default=WorkflowStatus.PLANNED)
    metadata: WorkflowMetadata = Field(default_factory=WorkflowMetadata)
