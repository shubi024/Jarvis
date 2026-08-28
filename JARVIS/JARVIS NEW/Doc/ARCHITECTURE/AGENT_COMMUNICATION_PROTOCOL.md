# AGENT COMMUNICATION PROTOCOL

**System:** J.A.R.V.I.S. Digital Household OS  
**Status:** LOCKED BLUEPRINT  
**Version:** 1.0

---

## 1. Purpose

This document defines how J.A.R.V.I.S. and its specialist agents communicate, delegate work, exchange results, coordinate workflows, handle blockers and failures, and preserve traceability.

This protocol is an architectural contract. It does not replace the JARVIS Master Specification, Agent Permission Matrix, Approval Architecture, Memory Architecture, or Task Lifecycle specification.

---

## 2. Authority Hierarchy

The system follows this authority model:

USER
  ↓
J.A.R.V.I.S.
  ↓
WORKFLOW
  ↓
SPECIALIST AGENTS

J.A.R.V.I.S. is the central orchestrator.

The user remains the final authority.

Agents are specialists, not independent authorities.

PLATO is the operational/project tracking specialist and does not override J.A.R.V.I.S.

---

## 3. Core Communication Principle

Agents may communicate directly only where a predefined workflow explicitly permits it.

Unrestricted agent-to-agent communication is prohibited.

Default communication path:

AGENT → J.A.R.V.I.S. → NEXT AGENT

Approved workflow communication may use:

AGENT A → AGENT B

but only within the scope and permissions of that workflow.

---

## 4. Global Behaviour Rule

Agents may proactively:

- Observe
- Think
- Analyze
- Research
- Detect patterns
- Identify risks
- Identify opportunities
- Recommend actions

Agents may not proactively execute consequential actions unless the applicable authority and approval rules explicitly permit the action.

**Nothing consequential happens without the user's approval.**

The Communication Protocol consumes approval and permission rules defined elsewhere; it does not create competing approval rules.

---

# 5. Workflow Definition System

Agent collaboration is organized through Workflow Definitions rather than arbitrary conversations.

Each workflow should define:

- Workflow ID
- Workflow name
- Trigger
- Objective
- Starting agent
- Agents involved
- Execution sequence
- Allowed direct communication links
- Required inputs
- Expected outputs
- Approval gates
- Failure/retry rules
- Completion condition
- Cancellation condition
- Relevant permission requirements

Examples include:

- Marketing Campaign
- Content Campaign
- Performance Analysis
- Project Execution
- Cross-Agent Research
- Client Communication
- Website Project

Workflows may evolve and new workflows may be created later without changing the fundamental communication hierarchy.

---

# 6. Task Package

Every delegated task must have a structured Task Package.

Minimum conceptual fields:

- Task ID
- Parent Workflow ID
- Requester
- Assigned Agent
- Objective
- Why the task is required
- Client / project context
- Relevant context
- Required inputs
- Available tools
- Permission scope
- Constraints
- Priority
- Deadline, when applicable
- Expected output
- DO requirements
- DO NOT requirements

The Task Package should contain only the context relevant to the assigned agent.

J.A.R.V.I.S. is responsible for translating broad user intent into an appropriate specialist task.

---

# 7. Context Isolation

Agents should not automatically receive the user's entire memory or J.A.R.V.I.S.'s entire conversational context.

Three conceptual layers exist:

### J.A.R.V.I.S. Memory
Broad understanding of the user's life, work, business, projects, preferences, routines, goals and relevant long-term context.

### Agent Knowledge
Specialist expertise belonging to an individual agent.

### Task Context
Only the information required for the current task.

J.A.R.V.I.S. provides the relevant Task Context to each agent.

This reduces unnecessary data exposure, confusion and accidental context leakage.

---

# 8. Task Lifecycle

The standard lifecycle is:

REQUEST
→ TASK CREATED
→ TASK VALIDATED
→ TASK ASSIGNED
→ RUNNING
→ RESULT GENERATED
→ RESULT VALIDATED
→ J.A.R.V.I.S. SYNTHESIS
→ APPROVAL WHERE REQUIRED
→ COMPLETED

Possible alternate states include:

- WAITING_INPUT
- WAITING_APPROVAL
- BLOCKED
- RETRYING
- FAILED
- PARTIAL
- CANCELLED

A workflow may move between these states according to its conditions.

---

# 9. Task Validation

Before execution, J.A.R.V.I.S. should validate:

- The task is understandable
- The correct agent is selected
- Required inputs are available
- Required permissions exist
- The requested action is within scope
- Approval requirements are known
- The expected result is defined

If routine information is missing, the task may enter `WAITING_INPUT`.

If important or critical information is missing, J.A.R.V.I.S. may notify the user.

---

# 10. Result Package

Agents must return structured results rather than an unqualified "done".

A Result Package should contain, as applicable:

- Task ID
- Status
- Summary
- Findings
- Evidence / sources
- Actions actually performed
- Recommendations
- Files or assets created
- Limitations
- Errors
- Items requiring approval
- Next recommended action

Possible result statuses:

- `COMPLETED`
- `PARTIAL`
- `WAITING_INPUT`
- `WAITING_APPROVAL`
- `BLOCKED`
- `FAILED`
- `CANCELLED`

---

# 11. Result Validation

Returning a response does not automatically mean a task succeeded.

J.A.R.V.I.S. should be able to distinguish between:

### COMPLETED
The requested work was actually completed.

### PARTIAL
Some requested work was completed but meaningful portions remain incomplete.

### BLOCKED
The agent cannot continue because an external dependency prevents progress.

### FAILED
The task failed after applicable recovery/retry behaviour.

Agents must never claim completion when they only created a plan, encountered an access problem, or partially completed the requested work.

---

# 12. Agent Handoffs

When one specialist's work becomes another specialist's input, J.A.R.V.I.S. normally converts the first Result Package into a new Task Package.

Example:

FRIDAY
→ Performance Analysis Result
→ J.A.R.V.I.S.
→ New Creative Task Package
→ VERONICA

This prevents unnecessary transfer of internal context.

The receiving agent gets the relevant findings and requirements, not the previous agent's entire internal state.

---

# 13. Cross-Agent Example: Premium Campaign

User request:

"JARVIS, create a premium Instagram campaign for this jewellery client."

Possible workflow:

USER
→ J.A.R.V.I.S.
→ FRIDAY
→ strategy result
→ J.A.R.V.I.S.
→ EDITH
→ messaging result
→ J.A.R.V.I.S.
→ VERONICA
→ creative result
→ J.A.R.V.I.S.
→ final synthesis/review
→ USER APPROVAL
→ PLATO for approved execution/tracking where applicable

Within a predefined workflow, controlled direct communication may occur between specialists where useful.

Example:

FRIDAY → VERONICA:
Performance-derived creative requirements.

EDITH → VERONICA:
Approved messaging and copy requirements.

J.A.R.V.I.S. remains responsible for orchestration.

---

# 14. Agent Questions

Agents do not normally interrupt the user directly.

If an agent needs information, access or clarification, it returns:

`WAITING_INPUT`

J.A.R.V.I.S. decides whether:

- The workflow can wait quietly
- The user should be asked now
- Another available source can resolve the issue
- The task should be paused

The user-facing interaction remains centralized through J.A.R.V.I.S.

---

# 15. Blocker Policy

### Routine blocker

The workflow may enter:

`WAITING_INPUT`

J.A.R.V.I.S. does not unnecessarily interrupt the user.

### Important / critical blocker

J.A.R.V.I.S. notifies the user when the blocker materially prevents progress or requires a decision.

When the required input becomes available:

WAITING_INPUT
→ INPUT RECEIVED
→ RESUME WORKFLOW

Workflow state and task context must be preserved during the wait.

---

# 16. Failure and Retry Policy

Temporary failures should be retried automatically at least three times where retrying is technically and logically appropriate.

Conceptual flow:

ATTEMPT 1
→ FAILURE
→ RETRY 2
→ FAILURE
→ RETRY 3
→ FAILURE
→ CLASSIFY FAILURE
→ J.A.R.V.I.S. EVALUATION

Not every failure should be blindly retried.

### Examples of potentially retryable failures

- Temporary API timeout
- Temporary network failure
- Recoverable service error
- Temporary rate limiting, using appropriate delay

### Examples of generally non-retryable failures

- Invalid credentials
- Missing permissions
- User approval required
- Permanently invalid input
- Unsupported operation

After applicable retries fail, the task should be marked `FAILED` or `BLOCKED`, and J.A.R.V.I.S. decides the appropriate next step.

---

# 17. Agent Status

Agents should expose standardized operational states:

- `IDLE`
- `READY`
- `ASSIGNED`
- `RUNNING`
- `WAITING_INPUT`
- `WAITING_APPROVAL`
- `BLOCKED`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

These states allow J.A.R.V.I.S. and PLATO to understand current workload and workflow progress.

---

# 18. No Fabricated Execution

This is a mandatory system rule.

An agent must never claim that an action occurred when it did not.

Examples:

If Meta Ads access failed:

`ACCESS_REQUIRED`

not:

"I analyzed the campaign."

If a creative could not be generated:

`FAILED`

not:

"Creative completed."

If only a strategy was prepared:

`PARTIAL`

not:

"Campaign completed."

The system must distinguish between:

- Planned
- Attempted
- Completed
- Partially completed
- Failed
- Blocked

---

# 19. Unique Task Identification

Every task receives a unique identifier.

Example:

`JARVIS-2026-000421`

Subtasks may inherit the parent workflow relationship:

`JARVIS-2026-000421`
- `FRIDAY-000421-A`
- `EDITH-000421-B`
- `VERONICA-000421-C`

This enables traceability across:

- Agent actions
- Workflow steps
- Results
- Approvals
- Failures
- Files
- Project history

---

# 20. Agent Disagreement

Agents may disagree.

They should present:

- Their conclusion
- Reasoning
- Supporting evidence
- Confidence or limitations where applicable

J.A.R.V.I.S. evaluates conflicting specialist results.

For consequential decisions, J.A.R.V.I.S. presents the relevant conclusion and reasoning to the user.

Agents do not override each other through authority.

---

# 21. User Authority

The user remains the final authority.

J.A.R.V.I.S. is the central orchestrator.

Specialist agents provide expertise.

PLATO manages operational organization and tracking.

No agent may elevate its own authority above J.A.R.V.I.S. or the user.

---

# 22. Separation of Responsibilities

### J.A.R.V.I.S.
- Understands user intent
- Orchestrates
- Delegates
- Coordinates workflows
- Synthesizes results
- Manages user interaction
- Determines when user input is required

### FRIDAY
- Performance marketing
- Advertising strategy
- Campaign analytics
- Business/marketing data
- Optimization recommendations

### VERONICA
- Creative intelligence
- Visual design
- Creative direction
- Asset and trend research

### EDITH
- Content
- SEO
- Copywriting
- Communication
- Messaging

### PLATO
- Project organization
- Task tracking
- SOPs
- Project folders/notes
- Operational execution tracking

No agent should silently absorb another agent's core authority.

---

# 23. Future Technical Implementation

The implementation may eventually represent these concepts using structured schemas, queues, event messages, workflow records and persistent task state.

The technical implementation must preserve the behavioural rules in this document.

Technology should implement the protocol, not redefine it.

---

## Final Principle

J.A.R.V.I.S. should behave as the intelligent conductor of the system.

The specialists provide depth.

Workflows provide structure.

Task Packages provide context.

Result Packages provide accountability.

Permissions provide boundaries.

Approvals provide user control.

PLATO provides operational continuity.

The user remains the final authority.

**END OF LOCKED SPECIFICATION**
