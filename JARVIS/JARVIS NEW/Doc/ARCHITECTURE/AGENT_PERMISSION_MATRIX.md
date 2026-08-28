# AGENT PERMISSION MATRIX

**System:** J.A.R.V.I.S. Digital Household OS  
**Status:** LOCKED BLUEPRINT  
**Version:** 1.0

---

## 1. Purpose

This document defines the permission boundaries for J.A.R.V.I.S. and all specialist agents.

It converts the user's previously established security and authority rules into an implementation-ready permission model.

This document works together with:

- JARVIS Master Specification
- Individual Agent Master Specifications
- Agent Communication Protocol
- Approval Architecture
- Memory Architecture
- Task Lifecycle

It does not grant access by itself. Actual credentials, integrations and operating-system permissions must separately enforce these rules.

---

# 2. Fundamental Permission Model

The system uses five permission levels.

| Level | Name | Meaning |
|---|---|---|
| L0 | DENY | No access or execution |
| L1 | READ / OBSERVE | Read, inspect, observe and analyze permitted information |
| L2 | CREATE / DRAFT | L1 + create drafts, reports, plans, files and non-consequential work |
| L3 | APPROVAL REQUIRED | L2 + execute the specific action only after explicit user approval |
| L4 | PRE-AUTHORIZED EXECUTION | Execute a specific pre-authorized action within an exact defined boundary |

### Critical L4 rule

L4 is never unrestricted authority.

Every L4 grant must be bound to:

- Agent
- Resource
- Action
- Scope
- Purpose
- Authorization
- Duration/expiry where applicable

L4 cannot override hard security boundaries, protected resources or system-level restrictions.

---

# 3. Least-Privilege Principle

Agents receive only the minimum permission required to complete the current task.

An agent does not inherit J.A.R.V.I.S.'s full access.

J.A.R.V.I.S. may temporarily delegate the minimum required permission for a specific task when the user has explicitly authorized the delegation.

Example:

FRIDAY requiring campaign-read access does not receive an entire advertising business portfolio.

---

# 4. Temporary Permissions

Temporary permissions are supported.

They may be granted only through explicit user authorization.

A temporary grant should record:

- Agent
- Resource
- Action
- Scope
- Level
- Purpose
- Start time
- Expiry condition/time
- Authorizing user action

When the purpose or authorization expires, the permission is revoked.

---

# 5. Hard Filesystem Boundary

This is an absolute system boundary.

### Permitted locations

- User's Downloads location
- Local Disk E:

These locations remain subject to agent-level permissions.

### Denied locations

- Local Disk C: except the permitted Downloads location
- Any other drive
- System directories
- OS-critical locations
- Protected/private locations outside the approved scope

No L4 permission can override this boundary.

Agents must not attempt to bypass it.

---

# 6. Protected Resources

The following are denied by default and must not be treated as ordinary resources:

- Banking information
- Banking credentials
- Passwords
- Authentication secrets
- OTPs
- Private conversations
- Private family/friend communications
- Sensitive personal information
- System-critical files
- Restricted operating-system resources

### OTP rule

Agents may detect that an OTP is required and ask the user for it.

Agents must not independently retrieve, store, expose or reuse OTPs as unrestricted credentials.

The user remains the authentication authority.

---

# 7. Screen Observation

J.A.R.V.I.S. may observe the user's visible screen while the computer is unlocked and the user is actively using it, according to the JARVIS Master Specification.

Screen observation is primarily for:

- Understanding what the user is doing
- Helping when the user is stuck
- Contextual assistance
- Detecting repetitive work
- Identifying useful suggestions
- Understanding active applications/workflows

Observation does not automatically authorize action.

### Locked computer

When the laptop is locked:

- Screen observation stops
- Normal agent activity stops
- No regular task execution occurs

Only explicitly defined background roles/responsibilities may continue, subject to their own permissions and the JARVIS specification.

Anything outside those predefined responsibilities requires user approval.

---

# 8. J.A.R.V.I.S. Permission Profile

J.A.R.V.I.S. is the central orchestrator and user-facing authority layer.

### JARVIS may generally:

- Understand user commands
- Observe permitted screen context
- Search the internet when appropriate
- Launch permitted applications
- Coordinate specialist agents
- Read permitted system state
- Read/write permitted project resources
- Ask the user for missing information or approval
- Recommend actions
- Coordinate workflows
- Monitor agent state
- Synthesize agent results

### JARVIS must not:

- Access denied filesystem locations
- Access passwords as unrestricted secrets
- Access banking credentials
- Retrieve OTPs independently
- access private conversations merely because they exist
- Perform consequential actions outside approved authority
- bypass agent permissions
- bypass user approval
- grant itself additional privileges

---

# 9. FRIDAY Permission Profile

FRIDAY is the performance marketing, advertising strategy, analytics and business intelligence specialist.

## Advertising Platforms

### Meta Ads

| Action | Level |
|---|---:|
| View authorized ad accounts | L1 |
| View campaigns/ad sets/ads | L1 |
| Read performance metrics | L1 |
| Analyze campaign performance | L2 |
| Create reports | L2 |
| Research audiences/targeting | L2 |
| Prepare campaign strategy | L2 |
| Draft campaign structures | L2 |
| Create live campaigns | L3 |
| Edit targeting | L3 |
| Edit live creative settings | L3 |
| Change budgets/bids | L3 |
| Pause/resume live campaigns | L3 |
| Publish/launch campaigns | L3 |
| Delete campaigns/assets | L3 |
| Billing/payment settings | L0 |
| Passwords/OTP/authentication secrets | L0 |

### Google Ads

| Action | Level |
|---|---:|
| View authorized accounts | L1 |
| View campaigns/ad groups/ads | L1 |
| Read performance data | L1 |
| Analyze performance | L2 |
| Create reports | L2 |
| Research keywords/targeting | L2 |
| Prepare campaign strategy | L2 |
| Draft campaigns | L2 |
| Create live campaigns | L3 |
| Edit targeting/keywords | L3 |
| Change bids/budgets | L3 |
| Pause/resume campaigns | L3 |
| Publish/enable campaigns | L3 |
| Delete campaigns/assets | L3 |
| Billing/payment settings | L0 |
| Passwords/OTP/authentication secrets | L0 |

## Analytics & Tracking

### GA4

- Read authorized properties/data: L1
- Analyze traffic/conversions: L2
- Create analysis/reports: L2
- Prepare measurement recommendations: L2
- Change configurations: L3
- Delete/alter critical configurations: L3
- Credentials/authentication secrets: L0

### Google Tag Manager

- Read containers/tags/triggers/variables: L1
- Analyze tracking implementation: L2
- Prepare tracking changes: L2
- Create drafts/workspace changes: L2
- Publish container changes: L3
- Delete critical tracking configuration: L3
- Credentials/authentication secrets: L0

FRIDAY may access authorized website tracking systems when required for performance marketing analysis.

---

# 10. VERONICA Permission Profile

VERONICA is the creative intelligence and visual design specialist.

### Canva / Creative Tools

| Action | Level |
|---|---:|
| View approved brand assets | L1 |
| Research trends and references | L1/L2 |
| Analyze previous client creatives | L2 |
| Create concepts | L2 |
| Create drafts | L2 |
| Create banners/posters/ad creatives | L2 |
| Create design files in approved project location | L2 |
| Revise drafts | L2 |
| Publish/post creative externally | L3 |
| Alter live client campaigns | L3 |
| Delete important client assets | L3 |
| Access passwords/OTP | L0 |

### Brand Assets

VERONICA may access authorized:

- Client brand assets
- Approved project folders
- Canva projects
- Visual references
- Competitor creative references
- Social media creative history

Access remains limited to the relevant project/client scope.

### Creative Learning

VERONICA may learn from:

- Client-approved creatives
- Client-rejected creatives
- Brand guidelines
- Competitor work
- Current trends
- User feedback

Learning does not grant permission to publish or modify live accounts.

---

# 11. EDITH Permission Profile

EDITH is the content, SEO, communication and language specialist.

### Content Resources

| Action | Level |
|---|---:|
| Read approved client content | L1 |
| Analyze content and tone | L1/L2 |
| Research SEO/topics/trends | L1/L2 |
| Draft captions | L2 |
| Draft ad copy | L2 |
| Draft website content | L2 |
| Draft SEO content | L2 |
| Draft emails/messages | L2 |
| Create content documents | L2 |
| Revise content | L2 |
| Send external communication | L3 |
| Publish content | L3 |
| Delete important content/assets | L3 |
| Access passwords/OTP | L0 |

EDITH may handle the user's actual business communications when explicitly delegated, but sending consequential external communication remains approval-gated unless a future explicit L4 grant authorizes a narrowly defined action.

---

# 12. PLATO Permission Profile

PLATO is the Life & Work Operations and project execution/tracking specialist.

### Project Organization

| Action | Level |
|---|---:|
| Read approved project resources | L1 |
| Track tasks/status/deadlines | L2 |
| Create project folders | L2/L4 when explicitly pre-authorized |
| Create notes | L2/L4 when explicitly pre-authorized |
| Create SOPs | L2/L4 when explicitly pre-authorized |
| Create task structures | L2 |
| Move approved files within approved project scope | L4 when explicitly authorized |
| Organize project resources | L4 when explicitly authorized |
| Close completed project records | L4 when explicitly authorized |
| Delete important files | L3 |
| Delete projects | L3 |
| Send external communications | L3 |
| Change system settings | L0 |

PLATO does not directly command specialist agents. J.A.R.V.I.S. remains the orchestration authority.

---

# 13. Applications & Browser Control

Opening or controlling an application is distinct from accessing data inside the application.

Example:

"JARVIS, open Meta Ads."

This is an application/navigation action.

It does not automatically grant permission to modify advertising campaigns.

### Application actions

- Launch permitted application: L2/L4 when explicitly authorized
- Navigate to permitted account/profile: L2/L4 according to scope
- Read visible application state: L1
- Perform consequential application action: L3 unless a narrowly scoped L4 grant exists

### Browser profiles

If multiple profiles/accounts exist, J.A.R.V.I.S. should ask the user to select the correct profile/account when the correct target cannot be determined safely.

Agents must not guess between multiple potentially consequential accounts.

---

# 14. Social Media Accounts

Authorized social accounts may be:

- Observed/read: L1
- Analyzed: L2
- Draft content prepared: L2
- Draft posts prepared: L2
- Publishing/posting: L3
- Editing live published content: L3
- Deleting posts/content: L3
- Account settings/security changes: L0 or L3 depending on the specific non-security action
- Passwords/OTP/security secrets: L0

A future explicit L4 grant may authorize a narrow recurring publishing action, but it must specify account, platform, action and scope.

---

# 15. Google Drive / Cloud Files

Only authorized resources should be accessible.

### Default

- Read approved project/client files: L1
- Analyze files: L2
- Create files/reports: L2
- Organize files: L2
- Move files within explicitly approved project scope: L4 when granted
- Share externally: L3
- Delete files: L3
- Access private unrelated files: L0
- Credentials/authentication secrets: L0

The filesystem boundary applies to local files; cloud services have their own resource-level scope.

---

# 16. Internet Research

Authorized agents may use the internet for their specialist work.

### Research

- Search public information: L1
- Analyze research: L2
- Create research reports: L2
- Save research into approved project location: L2/L4 according to scope

### Restricted

Agents must not:

- Use private credentials without authorization
- Circumvent access controls
- Bypass paywalls/security mechanisms
- Enter or expose sensitive authentication information
- Make purchases or commitments without approval

---

# 17. Financial & Purchasing Actions

Financial resources are protected.

### Denied by default

- Banking credentials: L0
- Passwords: L0
- OTP retrieval: L0
- Payment authentication secrets: L0

### Financial actions

- Research prices/options: L1/L2
- Prepare purchase recommendation: L2
- Prepare transaction details: L2
- Execute purchase/payment: L3
- Banking/account changes: L3 or L0 depending on action; credentials remain L0

The system may ask the user to provide an OTP when required for an authorized transaction. The OTP itself is not treated as persistent agent memory.

---

# 18. Communication & Private Conversations

The system must distinguish business communication from private personal communication.

### Business

Authorized business communications may be:

- Read: L1
- Summarized: L2
- Drafted: L2
- Sent: L3

### Private

Private conversations involving family, friends, girlfriend or other personal relationships are not general-purpose agent data.

Default access:

`L0`

Agents should not read, store, analyze or expose private conversations merely because they are technically accessible.

Any future exception would require explicit user authorization and a narrowly defined scope.

---

# 19. Credentials & Secrets

Credentials are never ordinary agent data.

### Default

| Resource | Level |
|---|---:|
| Passwords | L0 |
| API secrets | L0 |
| Banking credentials | L0 |
| OTPs | L0 |
| Authentication tokens | L0 |
| Private keys | L0 |

The system may use secure credential infrastructure at the technical layer without exposing the secret itself to the agent's reasoning context.

When user authentication is required, J.A.R.V.I.S. should request user participation.

---

# 20. Memory Permissions

Access to memory must follow relevance and scope.

### J.A.R.V.I.S.

May maintain broad user context according to the JARVIS Master Specification.

### Specialist agents

Should receive task-relevant memory, client-specific learning and specialist knowledge necessary for their role.

Agents should not automatically access unrelated private memories.

### Sensitive memory

Sensitive personal information remains restricted unless explicitly required and authorized for the task.

---

# 21. Observation vs Action

Observation permission does not imply action permission.

Example:

FRIDAY can observe:

> CPA has increased 34%.

This does not mean FRIDAY can:

> Increase budget.

Similarly:

VERONICA can observe:

> Client prefers minimal jewellery creatives.

This does not mean she can:

> Publish a new creative.

The system must keep:

**READ / OBSERVE**

separate from:

**ACT / MODIFY / EXECUTE**

---

# 22. Approval Boundary

When an action is L3:

1. Agent prepares the action or recommendation.
2. J.A.R.V.I.S. explains what will happen.
3. User approves or rejects.
4. Only after approval may execution occur.
5. Execution result is recorded.
6. J.A.R.V.I.S. reports the result.

The agent must not interpret silence as approval.

---

# 23. L4 Authorization Boundary

L4 may be granted only for a narrow, explicit action.

Example:

`PLATO + Move files + E:\ClientX\Project + Approved project organization + L4`

This does not grant:

- Delete permission
- External sharing permission
- Access to another client
- Access to another drive
- Access to private files

L4 is not contagious and cannot be delegated by an agent unless the user has explicitly authorized such delegation.

---

# 24. Permission Revocation

Permissions must be revocable.

A permission may be revoked when:

- User revokes it
- Temporary grant expires
- Project ends
- Workflow ends
- Resource scope changes
- Security concern is detected
- Agent is disabled
- Integration is disconnected

Revocation should take effect before future actions are executed.

---

# 25. Auditability

Permission-sensitive actions should be traceable.

The system should eventually record:

- Who requested the action
- Which agent performed it
- Resource
- Action
- Permission level
- Scope
- Approval reference, if applicable
- Timestamp
- Result
- Failure/retry information

This allows J.A.R.V.I.S. to explain:

> Who did what, why, under which permission, and whether the user approved it.

---

# 26. Security Overrides

No agent may:

- Escalate its own permissions
- Circumvent an L0 restriction
- Cross the filesystem hard boundary
- Access secrets because another agent can access them
- Treat user silence as approval
- Treat a recommendation as authorization
- Modify permissions for itself
- Disable security controls
- Hide an action from the user
- Falsify an audit record

Only the user-approved permission architecture may change an agent's authority.

---

# 27. Final Permission Model

The complete security hierarchy is:

USER
  ↓
EXPLICIT AUTHORIZATION
  ↓
PERMISSION GRANT
  ↓
AGENT
  ↓
RESOURCE
  ↓
ACTION
  ↓
SCOPE
  ↓
L0–L4 ENFORCEMENT
  ↓
AUDIT / RESULT

The hard filesystem boundary and protected-resource restrictions remain above individual agent permissions.

---

## Final Principle

J.A.R.V.I.S. should be powerful because it has **well-defined authority**, not because every agent has unrestricted access.

The system should follow:

**Least privilege.**

**Explicit authorization.**

**Narrow scope.**

**Temporary access where appropriate.**

**Approval for consequential actions.**

**No secrets exposed to agents.**

**No permission escalation.**

**No crossing hard security boundaries.**

**Complete traceability.**

The user remains the final authority.

**END OF LOCKED SPECIFICATION**
