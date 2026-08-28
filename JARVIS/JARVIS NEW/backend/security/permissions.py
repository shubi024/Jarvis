import logging
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any, Optional
from enum import IntEnum

from sqlalchemy import select
from backend.infrastructure.database import worker_session
from backend.infrastructure.models import PermissionModel, PermissionStatus as DBPermissionStatus
from backend.tools.tool_registry import tool_registry

logger = logging.getLogger("JARVIS.Security.Permissions")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _as_utc_timestamp(dt: Optional[datetime]) -> float:
    """
    Normalizes DB-loaded datetimes to UTC epoch seconds.
    SQLite returns naive datetimes; interpreting them as local time would shift
    grant-expiry comparisons by the host UTC offset.
    """
    if dt is None:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class PermissionLevel(IntEnum):
    """
    J.A.R.V.I.S. Authority Hierarchy — LOCKED Agent Permission Matrix vocabulary.

    L0: DENY                    No access or execution.
    L1: READ / OBSERVE          Read, inspect, observe and analyze permitted information.
    L2: CREATE / DRAFT          L1 + create drafts, reports, plans, files and non-consequential work.
    L3: APPROVAL REQUIRED       Execute the specific action only after explicit user approval.
    L4: PRE-AUTHORIZED EXECUTION  Execute a specific pre-authorized action within an exact defined boundary.

    Critical rules preserved from the locked matrix:
      - L4 is NEVER unrestricted authority; it must be bound to agent/resource/action/scope/purpose.
      - L4 cannot override hard security boundaries or protected resources.
      - Approval lives at L3; L4 exists only through an explicit user-granted durable record.
    """
    L0_DENY = 0
    L1_READ_OBSERVE = 1
    L2_CREATE_DRAFT = 2
    L3_APPROVAL_REQUIRED = 3
    L4_PRE_AUTHORIZED = 4

    # --- Deprecated legacy aliases (kept so existing callers/rows keep resolving).
    # New code MUST use the canonical names above.
    L0_PUBLIC = 0
    L1_INTERNAL = 1
    L2_SCOPED_MUTATIVE = 2
    L3_SENSITIVE = 3
    L4_CRITICAL = 4


# Tool risk -> required authority level per the locked matrix:
#   read-only tools            -> L1 (READ / OBSERVE)
#   draft/create tools         -> L2 (CREATE / DRAFT)
#   consequential tools        -> L3 (APPROVAL REQUIRED)
#   L4 is never derived from risk; it exists ONLY via explicit user grants.
RISK_TO_LEVEL = {
    "low": PermissionLevel.L1_READ_OBSERVE,
    "medium": PermissionLevel.L2_CREATE_DRAFT,
    "high": PermissionLevel.L3_APPROVAL_REQUIRED,
    "critical": PermissionLevel.L3_APPROVAL_REQUIRED,
}

# Trusted interactive local principals (personal-device deployment): the local
# operator plus voice-originated commands ("VoiceUser") coming from the same
# physical user via the microphone lifecycle.
LOCAL_OPERATOR_PRINCIPALS = {"User", "human_operator", "local_operator", "VoiceUser"}

# Trusted INTERNAL service principals used exclusively by first-party subsystems
# (e.g., the Observation pipeline's screen_capture/screen_analyzer virtual tasks).
# These are not external actors; they are only ever eligible for low-authority
# (L1 READ/OBSERVE) actions and can never reach consequential authority.
INTERNAL_SYSTEM_PRINCIPALS = {"SYSTEM_OBSERVER"}

# Union used for client/project scope-isolation exemptions.
TRUSTED_LOCAL_PRINCIPALS = LOCAL_OPERATOR_PRINCIPALS | INTERNAL_SYSTEM_PRINCIPALS


class PermissionEngine:
    """
    Permission Engine for J.A.R.V.I.S.
    Enforces strict deny-by-default rules against the LOCKED authority ladder,
    exact resource/action/scope matching, durable L4 pre-authorization grants,
    and decoupled human approval gating at L3.
    Performs pure authorization decisions with zero execution authority.
    """
    def __init__(self):
        self.valid_agents = {"friday", "veronica", "edith", "plato", "jarvis"}

    async def evaluate_request(
        self, 
        target_agents: List[str], 
        intent: str, 
        parameters: Dict[str, Any], 
        tool_name: Optional[str] = None,
        principal_id: Optional[str] = None,
        client_scope: Optional[str] = None,
        project_scope: Optional[str] = None,
        resource_target: Optional[str] = None,
        action_type: Optional[str] = None,
        required_level: Optional[PermissionLevel] = None
    ) -> Tuple[str, bool]:
        """
        Evaluates a request against strict deny-by-default policies, durable database grants, 
        exact resource/action scope matching, and authoritative tool metadata.

        Authority ladder semantics (locked):
          - Required level is derived from the tool's nature (read/draft/consequential).
          - L1/L2 requests may proceed within scope boundaries (observe + prepare freely).
          - L3 requests require explicit human approval UNLESS an exact durable L4
            pre-authorization grant covers this precise resource/action/scope.
          - Hard denials remain reserved for invalid agents/tools, missing scopes,
            unauthenticated L2+ attempts, and protected resources.

        Returns a tuple of: (decision: str ['ALLOWED', 'APPROVAL_REQUIRED', 'DENIED'], approval_required: bool).
        """
        logger.info(f"PermissionEngine evaluating request: Intent='{intent}', Agents={target_agents}, Tool='{tool_name}', Principal='{principal_id}'")

        # 1. Deny-by-default: Validate target agents strictly
        if not target_agents:
            logger.warning("Permission denied [Deny-by-default]: No target agents specified.")
            return "DENIED", False

        for agent_id in target_agents:
            if agent_id.lower() not in self.valid_agents:
                logger.warning(f"Permission denied [Hard boundary]: Unrecognized/unauthorized agent [{agent_id}] requested.")
                return "DENIED", False

        # 2. Derive authoritative minimum REQUIRED AUTHORITY LEVEL from the tool definition.
        #    Per the locked matrix, L4 is never derived from risk — it is granted explicitly.
        derived_level = required_level or PermissionLevel.L1_READ_OBSERVE
        tool_requires_approval = False

        if tool_name:
            tool = tool_registry.get_tool(tool_name)
            if not tool:
                logger.warning(f"Permission denied [Hard boundary]: Requested tool [{tool_name}] is not registered.")
                return "DENIED", False

            tool_risk = getattr(tool, "risk_level", "low").lower()
            tool_approval_flag = getattr(tool, "requires_approval", False)

            derived_level = max(derived_level, RISK_TO_LEVEL.get(tool_risk, PermissionLevel.L3_APPROVAL_REQUIRED))
            if tool_risk in {"high", "critical"} or tool_approval_flag:
                tool_requires_approval = True

        else:
            intent_upper = intent.upper()
            if any(act in intent_upper for act in ["EXECUTE", "DELETE", "UPDATE", "WRITE", "MODIFY", "CRITICAL"]):
                derived_level = max(derived_level, PermissionLevel.L3_APPROVAL_REQUIRED)
                tool_requires_approval = True

        # 3. Scope enforcement: L2+ requires valid client/project isolation.
        # Client-data isolation binds NON-local principals; the trusted interactive
        # local operator (personal-device deployment) proceeds to the durable-grant /
        # human-approval evaluation instead of being hard-denied for missing scopes.
        target_client = client_scope or parameters.get("client_scope")
        target_project = project_scope or parameters.get("project_scope")

        if derived_level >= PermissionLevel.L2_CREATE_DRAFT:
            is_local_principal = principal_id in TRUSTED_LOCAL_PRINCIPALS
            if not is_local_principal and (not target_client or not target_project):
                logger.warning(f"Permission denied [Client Isolation]: L{derived_level} action requires explicit client and project scope bounds.")
                return "DENIED", False

        # 4. Durable & Resource-Scoped Permission Lookup (Deny-by-default core check)
        has_valid_grant = False
        has_exact_l4_grant = False
        target_resource = resource_target or tool_name or intent
        target_action = action_type or intent

        if principal_id:
            async with worker_session() as db:
                result = await db.execute(
                    select(PermissionModel).where(
                        PermissionModel.principal_id == principal_id,
                        PermissionModel.status == DBPermissionStatus.ACTIVE
                    )
                )
                grants = result.scalars().all()
                
                now_ts = utc_now().timestamp()
                for grant in grants:
                    # Check expiry
                    if grant.expires_at and _as_utc_timestamp(grant.expires_at) <= now_ts:
                        continue
                    
                    # Verify Level hierarchy (supporting both permission_level and level schema conventions)
                    grant_lvl = getattr(grant, "permission_level", getattr(grant, "level", 0))
                    if isinstance(grant_lvl, str):
                        grant_lvl = PermissionLevel[grant_lvl] if grant_lvl in PermissionLevel.__members__ else PermissionLevel.L0_DENY
                    if grant_lvl < derived_level:
                        continue

                    # Verify exact resource match (wildcard '*' supported)
                    if grant.resource and grant.resource != "*" and grant.resource != target_resource:
                        continue

                    # Verify action match
                    if grant.action and grant.action != "*" and grant.action != target_action:
                        continue

                    # Verify client/project isolation scopes
                    if grant.client_scope and grant.client_scope != "*" and grant.client_scope != target_client:
                        continue
                    if grant.project_scope and grant.project_scope != "*" and grant.project_scope != target_project:
                        continue

                    # Valid grant located!
                    has_valid_grant = True
                    if grant_lvl >= PermissionLevel.L4_PRE_AUTHORIZED:
                        has_exact_l4_grant = True
                    grant_id = getattr(grant, "permission_id", getattr(grant, "id", "unknown"))
                    logger.info(f"Durable permission grant [{grant_id}] matched precisely for principal [{principal_id}] at L{grant_lvl}.")
                    break

        # 5. Strict Deny-by-Default Fallback Rule
        if not has_valid_grant:
            is_local_operator = principal_id in LOCAL_OPERATOR_PRINCIPALS
            if is_local_operator and derived_level <= PermissionLevel.L2_CREATE_DRAFT:
                # The local interactive operator may READ/OBSERVE (L1) and PREPARE/DRAFT (L2)
                # without a durable grant — consequential execution (L3+) remains gated below.
                return "ALLOWED", False

            if (
                principal_id in INTERNAL_SYSTEM_PRINCIPALS
                and derived_level <= PermissionLevel.L1_READ_OBSERVE
            ):
                # First-party observation subsystem (screen capture/analysis):
                # read-only L1 observation is authorized without a durable grant;
                # anything above L1 for a system principal remains denied/approval-gated.
                return "ALLOWED", False

            if not principal_id:
                if derived_level >= PermissionLevel.L2_CREATE_DRAFT:
                    logger.warning("Permission denied [Deny-by-default]: Unauthenticated request attempting L2+ action.")
                    return "DENIED", False
                # Unauthenticated L1 read-style request carries no consequential authority.
                return "ALLOWED", False

            # Authenticated principal WITHOUT a durable grant at L3+: elevated through
            # the human approval gate rather than unconditionally denied.
            # (Hard DENIAL remains reserved for invalid agents/tools, missing scopes, and
            #  unauthenticated L2+ attempts — approval never overrides a hard boundary.)
            logger.info(
                f"No durable grant for principal [{principal_id}] at L{derived_level}; "
                "routing to human approval gate."
            )
            return "APPROVAL_REQUIRED", True

        # 6. Separate Authorization from Human Approval Gating
        # Approval is required whenever the TOOL POLICY demands it OR the derived
        # authority level reaches L3 (APPROVAL REQUIRED) — including durable grants
        # at exactly L3. An exact L4 pre-authorization grant covering this precise
        # resource/action/scope is the ONLY path that skips approval.
        requires_approval_gate = (
            tool_requires_approval
            or derived_level >= PermissionLevel.L3_APPROVAL_REQUIRED
        )
        if requires_approval_gate and not has_exact_l4_grant:
            logger.info(f"Action requires explicit human approval gate (Tool Policy: {tool_requires_approval}, Derived Level: L{int(derived_level)}).")
            return "APPROVAL_REQUIRED", True

        return "ALLOWED", False


permission_engine = PermissionEngine()