from datetime import datetime, timezone
import enum
from sqlalchemy import Column, String, DateTime, Text, JSON, Boolean, ForeignKey, Enum as SQLEnum, Float, Integer
from sqlalchemy.orm import relationship
from backend.infrastructure.database import Base

def utc_now():
    return datetime.now(timezone.utc)

# --- Strict Lifecycle Enums ---

class TaskStatus(str, enum.Enum):
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
    BLOCKED = "BLOCKED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"

class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

class PermissionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"

class VerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    UNVERIFIABLE = "UNVERIFIABLE"

# --- Core Identity & Scoping Models ---

class UserModel(Base):
    __tablename__ = "jarvis_users"
    user_id = Column(String(64), primary_key=True, index=True)
    username = Column(String(128), unique=True, index=True)
    role = Column(String(64), default="operator")
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class ClientModel(Base):
    __tablename__ = "jarvis_clients"
    client_id = Column(String(64), primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class ProjectModel(Base):
    __tablename__ = "jarvis_projects"
    project_id = Column(String(64), primary_key=True, index=True)
    client_id = Column(String(64), ForeignKey("jarvis_clients.client_id"), nullable=True, index=True)
    name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

# --- Agent & Tool Architecture ---

class AgentModel(Base):
    __tablename__ = "jarvis_agents"
    agent_id = Column(String(64), primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    role = Column(String(128), nullable=False)
    status = Column(String(64), default="IDLE")
    current_task_id = Column(String(64), nullable=True)
    is_enabled = Column(Boolean, default=True)
    health = Column(String(64), default="HEALTHY")
    configuration_metadata = Column(JSON, default=dict)
    last_activity_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class ToolModel(Base):
    __tablename__ = "jarvis_tools"
    tool_id = Column(String(64), primary_key=True, index=True)
    name = Column(String(128), unique=True, index=True)
    category = Column(String(64), nullable=False)
    version = Column(String(32), nullable=True)
    risk_level = Column(String(32), default="low", nullable=False)
    is_enabled = Column(Boolean, default=True)
    provider = Column(String(64), nullable=True)
    configuration_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class IntegrationModel(Base):
    __tablename__ = "jarvis_integrations"
    integration_id = Column(String(64), primary_key=True, index=True)
    name = Column(String(128), nullable=False, index=True)
    provider = Column(String(64), nullable=False)
    status = Column(String(64), default="ACTIVE")
    configuration_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class CredentialReferenceModel(Base):
    __tablename__ = "jarvis_credential_references"
    reference_id = Column(String(64), primary_key=True, index=True)
    integration_id = Column(String(64), ForeignKey("jarvis_integrations.integration_id"), nullable=True)
    service_name = Column(String(128), nullable=False, index=True)
    reference_key = Column(String(256), nullable=False) # e.g., KMS, AWS Secret ARN, Vault path
    client_scope = Column(String(64), ForeignKey("jarvis_clients.client_id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

# --- Execution Engine: Workflows, Tasks & Subtasks ---

class WorkflowModel(Base):
    __tablename__ = "jarvis_workflows"
    workflow_id = Column(String(64), primary_key=True, index=True)
    parent_task_id = Column(String(64), index=True, nullable=True)
    objective = Column(Text, nullable=False)
    target_agents = Column(JSON, default=list)
    dependencies = Column(JSON, default=list)
    approval_gates = Column(JSON, default=list)
    execution_state = Column(String(64), default="PLANNED")
    completion_state = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class TaskModel(Base):
    __tablename__ = "jarvis_tasks"
    task_id = Column(String(64), primary_key=True, index=True)
    parent_task_id = Column(String(64), ForeignKey("jarvis_tasks.task_id"), nullable=True, index=True)
    workflow_id = Column(String(64), ForeignKey("jarvis_workflows.workflow_id"), nullable=True, index=True)
    requester_id = Column(String(64), ForeignKey("jarvis_users.user_id"), nullable=True)
    
    intent = Column(String(128), nullable=False, index=True)
    objective = Column(Text, nullable=False)
    status = Column(SQLEnum(TaskStatus), nullable=False, default=TaskStatus.RECEIVED, index=True)
    priority = Column(Integer, default=1)
    deadline = Column(DateTime(timezone=True), nullable=True)
    
    # Execution Rules
    constraints = Column(JSON, default=list)
    expected_output = Column(Text, nullable=True)
    do_requirements = Column(JSON, default=list)
    do_not_requirements = Column(JSON, default=list)
    required_resources = Column(JSON, default=list)
    
    # Assignments & Scope
    permission_scope = Column(JSON, default=dict)
    client_scope = Column(String(64), ForeignKey("jarvis_clients.client_id"), nullable=True, index=True)
    project_scope = Column(String(64), ForeignKey("jarvis_projects.project_id"), nullable=True, index=True)
    risk_level = Column(String(32), default="low", nullable=False)
    target_agents = Column(JSON, default=list)
    selected_tools = Column(JSON, default=list)
    parameters = Column(JSON, default=dict)
    
    # Metadata & State
    context_snapshot = Column(JSON, default=dict)
    execution_metadata = Column(JSON, default=dict)
    retry_count = Column(Integer, default=0)
    cancellation_reason = Column(Text, nullable=True)
    failure_reason = Column(Text, nullable=True)
    
    # Results
    result_summary = Column(Text, nullable=True)
    actions_performed = Column(JSON, default=list)
    evidence = Column(JSON, default=list)
    limitations = Column(JSON, default=list)
    
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class SubtaskModel(Base):
    __tablename__ = "jarvis_subtasks"
    subtask_id = Column(String(64), primary_key=True, index=True)
    parent_task_id = Column(String(64), ForeignKey("jarvis_tasks.task_id"), nullable=False, index=True)
    workflow_id = Column(String(64), ForeignKey("jarvis_workflows.workflow_id"), nullable=True)
    
    objective = Column(Text, nullable=False)
    assigned_agent = Column(String(64), ForeignKey("jarvis_agents.agent_id"), nullable=True)
    dependencies = Column(JSON, default=list)
    input_data = Column(JSON, default=dict)
    expected_output = Column(Text, nullable=True)
    permission_scope = Column(JSON, default=dict)
    
    status = Column(SQLEnum(TaskStatus), nullable=False, default=TaskStatus.QUEUED, index=True)
    result_data = Column(JSON, default=dict)
    
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class AppConfigModel(Base):
    """Small durable configuration store for authoritative system/session state."""
    __tablename__ = "jarvis_app_config"
    config_key = Column(String(128), primary_key=True)
    config_value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class ScheduleModel(Base):
    """Durable delayed/recurring execution definitions owned by the scheduler."""
    __tablename__ = "jarvis_schedules"
    schedule_id = Column(String(64), primary_key=True, index=True)
    requester_id = Column(String(64), nullable=True, index=True)
    payload_type = Column(String(64), nullable=False)
    payload_template = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, default="ACTIVE", index=True)
    recurrence_rule = Column(String(128), nullable=True)
    timezone = Column(String(64), nullable=False, default="UTC")
    next_run_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class ScheduleRunModel(Base):
    """Immutable scheduler run record supporting recovery and exactly-once review."""
    __tablename__ = "jarvis_schedule_runs"
    run_id = Column(String(64), primary_key=True, index=True)
    schedule_id = Column(String(64), ForeignKey("jarvis_schedules.schedule_id"), nullable=False, index=True)
    execution_id = Column(String(64), nullable=False, index=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(32), nullable=False, default="SUBMITTED", index=True)

class VerificationModel(Base):
    __tablename__ = "jarvis_verifications"
    verification_id = Column(String(64), primary_key=True, index=True)
    task_id = Column(String(64), ForeignKey("jarvis_tasks.task_id"), nullable=True, index=True)
    subtask_id = Column(String(64), ForeignKey("jarvis_subtasks.subtask_id"), nullable=True, index=True)
    
    verification_contract = Column(JSON, nullable=False)
    expected_result = Column(JSON, nullable=True)
    actual_observed_result = Column(JSON, nullable=True)
    verification_method = Column(String(64), nullable=False)
    verifier = Column(String(64), nullable=False)
    
    verification_status = Column(SQLEnum(VerificationStatus), default=VerificationStatus.PENDING, index=True)
    failure_reason = Column(Text, nullable=True)
    confidence = Column(Float, default=1.0)
    retry_fallback_recommendation = Column(Text, nullable=True)
    
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

# --- Security & Approvals ---

class ApprovalModel(Base):
    __tablename__ = "jarvis_approvals"
    approval_id = Column(String(64), primary_key=True, index=True)
    # Nullable: approvals may be raised for system-level actions (e.g.
    # SYSTEM_OBSERVATION_CAPTURE) that are not bound to a durable task row.
    task_id = Column(String(64), ForeignKey("jarvis_tasks.task_id"), nullable=True, index=True)
    workflow_id = Column(String(64), nullable=True)
    intent = Column(String(128), nullable=False, default="UNKNOWN")
    target_agents = Column(JSON, default=list)
    
    agent_id = Column(String(64), nullable=True)
    tool_name = Column(String(128), nullable=True)
    resource = Column(String(256), nullable=False)
    action = Column(String(128), nullable=False)
    
    client_scope = Column(String(64), ForeignKey("jarvis_clients.client_id"), nullable=True)
    project_scope = Column(String(64), ForeignKey("jarvis_projects.project_id"), nullable=True)
    account_scope = Column(String(128), nullable=True)
    
    proposed_parameters = Column(JSON, default=dict)
    parameters = Column(JSON, default=dict)
    parameters_diff = Column(JSON, default=dict)
    risk_level = Column(String(32), nullable=False)
    consequences = Column(JSON, default=list)
    authorization_scope = Column(JSON, default=dict)
    
    status = Column(SQLEnum(ApprovalStatus), default=ApprovalStatus.PENDING, index=True)
    reason = Column(Text, nullable=True)
    requested_by = Column(String(64), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    resolved_by = Column(String(64), ForeignKey("jarvis_users.user_id"), nullable=True)
    
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class PermissionModel(Base):
    __tablename__ = "jarvis_permissions"
    permission_id = Column(String(64), primary_key=True, index=True)
    principal_id = Column(String(64), nullable=False, index=True)
    resource = Column(String(128), nullable=False)
    action = Column(String(64), nullable=False)
    scope = Column(String(128), nullable=True)
    permission_level = Column(String(16), nullable=False) # L0-L4
    status = Column(SQLEnum(PermissionStatus), nullable=False, default=PermissionStatus.ACTIVE, index=True)
    # Client/project isolation scopes consumed by the PermissionEngine's exact
    # grant matching ("*" wildcard supported). Nullable: local-operator grants may
    # legitimately be unscoped.
    client_scope = Column(String(64), ForeignKey("jarvis_clients.client_id"), nullable=True, index=True)
    project_scope = Column(String(64), ForeignKey("jarvis_projects.project_id"), nullable=True, index=True)
    
    purpose = Column(Text, nullable=True)
    authorization_source = Column(String(128), nullable=True)
    is_temporary = Column(Boolean, default=False)
    is_revocable = Column(Boolean, default=True)
    
    start_time = Column(DateTime(timezone=True), default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=True)
# NOTE: The canonical durable memory model is `MemoryModel` in
# backend/memory/memory_manager.py (table `jarvis_structured_memories`).
# The former duplicate legacy `MemoryModel` (`jarvis_memories`) declared here was
# removed to eliminate the two-competing-memory-tables split; nothing referenced it.
# Existing `jarvis_memories` tables in old databases are left orphaned and can be
# dropped manually during migration cleanup.
    
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class ObservationModel(Base):
    __tablename__ = "jarvis_observations"
    observation_id = Column(String(64), primary_key=True, index=True)
    session_id = Column(String(64), index=True, nullable=False)
    source = Column(String(64), nullable=False)
    observation_type = Column(String(64), nullable=False)
    
    raw_reference_location = Column(String(256), nullable=True)
    extracted_context = Column(JSON, default=dict)
    confidence = Column(Float, default=1.0)
    
    privacy_flagged = Column(Boolean, default=False)
    retention_state = Column(String(32), default="ephemeral")
    
    client_scope = Column(String(64), ForeignKey("jarvis_clients.client_id"), nullable=True)
    project_scope = Column(String(64), ForeignKey("jarvis_projects.project_id"), nullable=True)
    
    captured_at = Column(DateTime(timezone=True), nullable=False)
    analysis_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

# --- Audit & System Telemetry ---

class EventModel(Base):
    __tablename__ = "jarvis_events"
    event_id = Column(String(64), primary_key=True, index=True)
    event_type = Column(String(128), nullable=False, index=True)
    correlation_id = Column(String(64), nullable=True, index=True)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

class AuditRecordModel(Base):
    __tablename__ = "jarvis_audit_records"
    audit_id = Column(String(64), primary_key=True, index=True)
    actor = Column(String(64), nullable=False, index=True)
    agent_id = Column(String(64), ForeignKey("jarvis_agents.agent_id"), nullable=True, index=True)
    task_id = Column(String(64), ForeignKey("jarvis_tasks.task_id"), nullable=True, index=True)
    
    action = Column(String(128), nullable=False)
    resource = Column(String(256), nullable=False)
    result = Column(String(64), nullable=False)
    
    approval_id = Column(String(64), ForeignKey("jarvis_approvals.approval_id"), nullable=True)
    security_decision = Column(String(64), nullable=True)
    
    state_before = Column(JSON, nullable=True)
    state_after = Column(JSON, nullable=True)
    correlation_id = Column(String(64), nullable=True, index=True)
    
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
