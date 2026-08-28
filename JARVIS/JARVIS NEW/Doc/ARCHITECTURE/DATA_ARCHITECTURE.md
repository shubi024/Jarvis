# DATA ARCHITECTURE

**System:** J.A.R.V.I.S. Digital Household OS  
**Status:** LOCKED BLUEPRINT  
**Version:** 1.0

## 1. Principle

Data must be separated by purpose, scope and sensitivity.

## 2. Core Entities

```text
User
Agent
Task
Subtask
Project
Client
Memory
Observation
Permission
Approval
Tool
Integration
Event
AuditRecord
CredentialReference
```

## 3. Logical Stores

```text
USER_PROFILE
PREFERENCES
PROJECTS
CLIENTS
TASKS
MEMORIES
OBSERVATIONS
AGENT_STATE
PERMISSIONS
APPROVALS
INTEGRATIONS
EVENTS
AUDIT_LOG
```

## 4. Client Isolation

Client records must have explicit ownership/scope.

Queries must enforce client scope before returning data.

## 5. Memory Scope

Memory records should include:
- owner/scope
- category
- confidence
- sensitivity
- source
- timestamps
- active/superseded state

## 6. Task Data

Tasks should retain:
- objective
- parent task
- subtasks
- agent assignments
- dependencies
- permissions
- approval state
- execution state
- result
- verification
- timestamps

## 7. Secrets

Secrets are represented by secure references, not ordinary database text.

Never store passwords or OTPs in normal memory tables.

## 8. Audit Data

Audit records should be append-oriented and protected from ordinary agent modification.

## 9. Data Lifecycle

Support:
- active
- archived
- superseded
- deleted/forgotten where permitted

Historical data must not automatically influence current execution.

## 10. Backup / Recovery

Backups should preserve system integrity without weakening permission or secret protections.

## 11. Implementation Principle

The first implementation should use a simple reliable relational data model, with room for vector/semantic retrieval later.

Do not over-engineer the first backend.

**END OF LOCKED SPECIFICATION**
