"""
backend/main.py
J.A.R.V.I.S. Full Final Startup Lifecycle & FastAPI Application Orchestrator.
Wires and supervises all backend subsystems in strict sequence with comprehensive health checks,
WebSocket telemetry bridging, startup greeting triggers, and stateful graceful shutdowns.
"""

import logging
import asyncio
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Subsystem Imports
from backend.infrastructure.config import settings
from backend.infrastructure.database import engine, Base, worker_session
from backend.infrastructure.state_manager import state_manager
from backend.infrastructure.websocket_manager import websocket_manager
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.infrastructure.diagnostics import diagnostics_engine

from backend.tools.tool_registry import tool_registry, register_all_tools
from backend.core.agent_registration import agent_registry, register_all_agents
from backend.memory.memory_manager import memory_manager
from backend.security.security_manager import security_manager
from backend.security.approval_manager import approval_manager
from backend.security.audit_log import audit_log_manager
from backend.core.emergency_stop import emergency_stop
from backend.core.task_queue import task_queue
from backend.core.agent_runtime import agent_runtime
from backend.core.scheduler import scheduler
from backend.observation.observation_manager import observation_manager
from backend.observation.session_manager import session_manager
from backend.observation.os_session_monitor import os_session_monitor
from backend.tools.voice.wake_word import WakeWordTool
from backend.tools.voice.audio_stream_publisher import audio_stream_publisher
from backend.tools.voice.speech_to_text import speech_to_text_engine
from backend.tools.voice.text_to_speech import text_to_speech_engine
from backend.core.briefing_service import briefing_service
from backend.core.brain import brain

# Configure core logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("JARVIS.Main")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# Global reference tracking state for safe startup/shutdown
# NOTE: WebSocket connections/telemetry are owned solely by the canonical
# WebSocketManager (backend.infrastructure.websocket_manager). main.py no longer
# maintains a competing bridge implementation.

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan manager executing the strict 14-step startup sequence
    and the corresponding stateful reverse-order shutdown sequence.
    """
    
    # Track initialization milestones for safe reverse teardown
    initialized_steps = {
        "db": False,
        "redis": False,
        "event_bus": False,
        "tools": False,
        "agents": False,
        "memory": False,
        "security": False,
        "emergency_stop": False,
        "queue": False,
        "runtime": False,
        "scheduler": False,
        "session_observation": False,
        "voice": False,
        "websocket_bridge": False,
        "briefing": False
    }

    logger.info(f"Initializing {settings.PROJECT_NAME} v{settings.VERSION} [{settings.ENVIRONMENT}]...")

    try:
        # 1. Database Initialization & Verified Connectivity
        if settings.ENVIRONMENT == "development":
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Step 1/14: Database schema auto-created/verified.")
        else:
            async with engine.begin() as conn:
                pass
            logger.info("Step 1/14: Production database connectivity verified.")
        initialized_steps["db"] = True

        # 2. State Manager / Redis Initialization
        await state_manager.connect()
        logger.info("Step 2/14: State Manager & Redis connected.")
        initialized_steps["redis"] = True

        # 3. EventBus Initialization
        await event_bus.connect()
        logger.info("Step 3/14: EventBus initialized.")
        initialized_steps["event_bus"] = True

        # 4. ToolRegistry Initialization & Validation
        register_all_tools()
        logger.info("Step 4/14: ToolRegistry validated.")
        initialized_steps["tools"] = True

        # 5. AgentRegistry Registration (Explicitly ensuring specialist agents are registered)
        register_all_agents()
        logger.info("Step 5/14: Specialist agents registered successfully via AgentRegistry.")
        initialized_steps["agents"] = True

        # 6. MemoryManager + ContextManager Readiness
        async with worker_session() as db:
            if hasattr(memory_manager, "initialize"):
                await memory_manager.initialize(db)
        logger.info("Step 6/14: Memory and Context subsystems ready.")
        initialized_steps["memory"] = True

        # 7. SecurityManager + ApprovalManager + AuditLog Readiness
        if hasattr(security_manager, "initialize"):
            await security_manager.initialize()
        if hasattr(approval_manager, "initialize"):
            await approval_manager.initialize()
        await audit_log_manager.start()
        logger.info("Step 7/14: Security, Approval and Audit subsystems initialized.")
        initialized_steps["security"] = True

        # 8. EmergencyStop Initialization (Explicit initialization & state recovery before execution services)
        if hasattr(emergency_stop, "initialize"):
            await emergency_stop.initialize()
        logger.info("Step 8/14: EmergencyStop controller explicitly initialized and armed.")
        initialized_steps["emergency_stop"] = True

        # 9. TaskQueue Start
        await task_queue.start()
        logger.info("Step 9/14: TaskQueue orchestration started.")
        initialized_steps["queue"] = True

        # 10. AgentRuntime Readiness
        if hasattr(agent_runtime, "initialize"):
            await agent_runtime.initialize()
        logger.info("Step 10/14: AgentRuntime operational.")
        initialized_steps["runtime"] = True

        # 11. Scheduler Start
        await scheduler.start()
        logger.info("Step 11/14: Task Scheduler started.")
        initialized_steps["scheduler"] = True

        # 12. ObservationManager & SessionManager & OS Boundary Monitor Initialization
        await session_manager.initialize()
        await observation_manager.start()
        await os_session_monitor.start()
        logger.info("Step 12/14: Observation, Session Manager and OS boundary monitor online.")
        initialized_steps["session_observation"] = True

        # 13. Voice Subsystem Startup Lifecycle (Real readiness check and SessionManager state synchronization)
        voice_state = await session_manager.get_session_state()
        if speech_to_text_engine is None or text_to_speech_engine is None:
            raise RuntimeError("Voice components (STT/TTS) failed verification during initialization.")
        await audio_stream_publisher.start()
        logger.info(f"Step 13/14: Voice subsystems verified (continuous audio publisher active). Listening eligibility: {voice_state.get('is_voice_listening', False)}")
        initialized_steps["voice"] = True

        # 14. WebSocket Telemetry Bridge Setup — canonical WebSocketManager is the
        # SINGLE EventBus→HUD bridge (replaces main.py's former inline duplicate).
        await websocket_manager.start()
        logger.info("Step 14/14: WebSocket telemetry bridge connected to EventBus.")
        initialized_steps["websocket_bridge"] = True

        # Perform Initial System Health & Status Check
        health_report = await diagnostics_engine.run_full_diagnostics()
        if health_report.get("status") not in ["healthy", "degraded"]:
            raise RuntimeError("Initial system health check failed verification.")

        # Trigger Startup Greeting once per boot cycle through SessionManager using canonical UTC datetime
        greeting_triggered = await session_manager.check_startup_greeting()
        if greeting_triggered:
            _hour = datetime.now().hour
            _period = "morning" if _hour < 12 else ("afternoon" if _hour < 17 else "evening")
            greeting_text = f"Good {_period}, sir. J.A.R.V.I.S. is online and all systems are operating at peak efficiency."
            logger.info("Startup greeting trigger authorized and emitted.")
            await event_bus.publish(JarvisEvent(
                event_type=EventType.SYSTEM,
                topic="system.startup_greeting",
                timestamp=utc_now(),
                correlation_id="startup_greet",
                task_id="SYSTEM_STARTUP",
                source="Main",
                payload={"message": greeting_text}
            ))
            # Spec §5: the greeting should be SPOKEN when voice output is available.
            try:
                await text_to_speech_engine.speak(text=greeting_text, allow_when_locked=True)
            except Exception as greet_tts_err:
                logger.debug(f"Spoken startup greeting unavailable: {greet_tts_err}")

        # Weekly Chief-of-Staff Briefing loop (spec §17)
        await briefing_service.start()
        initialized_steps["briefing"] = True

        logger.info("J.A.R.V.I.S. READY — All systems fully initialized and operational.")

        yield  # Application running loop

    except Exception as e:
        logger.critical(f"CRITICAL STARTUP FAILURE: {e}", exc_info=True)
        raise RuntimeError(f"Safe partial-start prevention triggered: {e}") from e

    finally:
        # --- Stateful Graceful Shutdown in Reverse Dependency Order ---
        logger.info("Initiating J.A.R.V.I.S. graceful shutdown sequence in reverse order...")

        # WebSocket Telemetry Bridge cleanup
        if initialized_steps["websocket_bridge"]:
            try:
                await websocket_manager.stop()
            except Exception:
                pass

        # Briefing service shutdown
        if initialized_steps["briefing"]:
            try:
                await briefing_service.stop()
            except Exception:
                pass

        # Voice subsystem shutdown (Stop audio publisher, TTS playback and reset listeners)
        if initialized_steps["voice"]:
            try:
                await audio_stream_publisher.stop()
                await text_to_speech_engine.interrupt()
            except Exception:
                pass

        # Observation & Session shutdown (Stop observation manager, OS monitor and lock session safely)
        if initialized_steps["session_observation"]:
            try:
                await observation_manager.stop()
                await os_session_monitor.stop()
                await session_manager.lock_session(reason="System shutdown")
            except Exception:
                pass

        # Scheduler shutdown
        if initialized_steps["scheduler"]:
            try:
                await scheduler.stop()
            except Exception:
                pass

        # TaskQueue shutdown (Authoritative execution shutdown authority)
        if initialized_steps["queue"]:
            try:
                await task_queue.stop()
            except Exception:
                pass

        # Audit log detach
        if initialized_steps["security"]:
            try:
                await audit_log_manager.stop()
            except Exception:
                pass

        # EventBus close
        if initialized_steps["event_bus"]:
            try:
                await event_bus.close()
            except Exception:
                pass

        # StateManager close
        if initialized_steps["redis"]:
            try:
                await state_manager.close()
            except Exception:
                pass

        # Database engine dispose
        if initialized_steps["db"]:
            try:
                await engine.dispose()
            except Exception:
                pass

        logger.info("Shutdown complete. All J.A.R.V.I.S. subsystems offline safely.")


# Initialize FastAPI application
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="J.A.R.V.I.S. Autonomous AI Backend OS",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# Secure CORS Middleware Configuration
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# Global unhandled exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "An internal system error occurred."}
    )

# --- Transport Authentication Gate ---
# Shared gate for ALL routes (system, task, approval): if JARVIS_AUTH_TOKEN is
# configured a valid Bearer token is REQUIRED; otherwise only loopback peers pass.

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}

def _client_is_local(client_host: Optional[str]) -> bool:
    """The interactive local operator is trusted by design; remote clients are not."""
    return client_host in LOCAL_HOSTS

async def require_local_or_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """
    Transport-level gate for consequential REST routes.
    - If JARVIS_AUTH_TOKEN is configured, a valid Bearer token is REQUIRED (even locally).
    - Otherwise, only loopback connections are accepted.
    """
    if settings.JARVIS_AUTH_TOKEN:
        if authorization == f"Bearer {settings.JARVIS_AUTH_TOKEN}":
            return
        raise HTTPException(status_code=401, detail="Invalid or missing authorization token.")
    client_host = request.client.host if request.client else None
    if _client_is_local(client_host):
        return
    raise HTTPException(status_code=401, detail="Remote access requires JARVIS_AUTH_TOKEN.")

# --- Base System Routes ---

@app.get("/", tags=["System"], dependencies=[Depends(require_local_or_token)])
async def root():
    """Root endpoint verifying API accessibility and real readiness."""
    session_state = await session_manager.get_session_state()
    return {
        "system": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "online" if not session_state.get("emergency_stop_active") else "emergency_stopped",
        "environment": settings.ENVIRONMENT,
        "session": session_state
    }

@app.get("/api/v1/health", tags=["System"], dependencies=[Depends(require_local_or_token)])
async def health_check():
    """Comprehensive diagnostics reporting subsystem health status."""
    report = await diagnostics_engine.run_full_diagnostics()
    status_code = 200 if report.get("status") in ["healthy", "degraded"] else 503
    return JSONResponse(status_code=status_code, content=report)


# --- Task Execution & Human Approval API ---
# These endpoints close the human-in-the-loop cycle: commands enter via REST,
# pending approvals are listed for the HUD, and operators resolve them.

from backend.infrastructure.schemas import TaskExecuteRequest, TaskResponse, ApprovalResolveRequest
from backend.core.task_contracts import ResultStatus

@app.post("/api/v1/tasks/execute", response_model=TaskResponse, tags=["Tasks"],
          dependencies=[Depends(require_local_or_token)])
async def execute_task(request: TaskExecuteRequest):
    """Submits a natural-language command through the Brain orchestration pipeline."""
    command_text = request.command or request.intent
    async with worker_session() as db:
        result = await brain.process_command(
            db=db,
            user_text=command_text,
            requester="human_user",
            client_scope=request.client_scope,
            project_scope=request.project_scope,
        )
    status_val = result.get("status", "UNKNOWN")
    return TaskResponse(
        task_id=result.get("task_id", ""),
        intent=request.intent,
        status=status_val,
        requires_approval=(status_val == ResultStatus.WAITING_APPROVAL.value),
        approval_id=result.get("approval_id"),
        result_data={"response": result.get("response")},
    )


@app.get("/api/v1/approvals/pending", tags=["Approvals"],
         dependencies=[Depends(require_local_or_token)])
async def list_pending_approvals():
    """Lists all pending human-in-the-loop approval requests for the administrative HUD."""
    approvals = await approval_manager.get_pending_approvals_for_hud()
    return {"success": True, "count": len(approvals), "approvals": approvals}


@app.post("/api/v1/approvals/resolve", tags=["Approvals"],
          dependencies=[Depends(require_local_or_token)])
async def resolve_approval_endpoint(request: ApprovalResolveRequest):
    """Resolves a pending approval gate (approve/reject); TaskQueue resumes or fails the task via event."""
    resolved = await approval_manager.resolve_approval(
        approval_id=request.approval_id,
        approved=request.approved,
        resolved_by=request.resolved_by,
    )
    if not resolved:
        return JSONResponse(
            status_code=409,
            content={"success": False, "error": "Approval cannot be resolved (missing, already resolved, expired, or action drift detected)."}
        )
    current_status = await approval_manager.get_approval_status(request.approval_id)
    return {"success": True, "approval_id": request.approval_id, "status": current_status}


# --- WebSocket Communication Bridge ---

_wake_word_tool_instance: Optional[WakeWordTool] = None

def _get_wake_word_tool() -> WakeWordTool:
    """Returns the registered WakeWordTool instance for voice lifecycle control."""
    global _wake_word_tool_instance
    if _wake_word_tool_instance is None:
        candidate = tool_registry.get_tool("wake_word_control")
        _wake_word_tool_instance = candidate if isinstance(candidate, WakeWordTool) else WakeWordTool()
    return _wake_word_tool_instance


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time frontend HUD communication and command execution.

    Transport gate mirrors REST policy: valid `?token=` when JARVIS_AUTH_TOKEN is set,
    otherwise loopback connections only. Unauthenticated sockets never reach accept().
    """
    provided_token = websocket.query_params.get("token")
    client_host = websocket.client.host if websocket.client else None

    if settings.JARVIS_AUTH_TOKEN:
        authorized = (provided_token == settings.JARVIS_AUTH_TOKEN)
    else:
        authorized = _client_is_local(client_host)

    if not authorized:
        logger.warning(f"[J.A.R.V.I.S. WS] Rejected unauthenticated connection from {client_host}.")
        await websocket.close(code=4401)
        return

    client_id = f"hud_{uuid.uuid4().hex[:8]}"
    await websocket_manager.connect(websocket, client_id)
    logger.info("[J.A.R.V.I.S.] Frontend HUD client connected via WebSocket.")
    
    try:
        while True:
            data = await websocket.receive_json()
            logger.debug(f"[J.A.R.V.I.S. WS] Received message: {data}")
            
            action = data.get("action")
            
            if action == "ping":
                await websocket.send_json({
                    "type": "pong", 
                    "message": "J.A.R.V.I.S. matrix responsive",
                    "session_state": await session_manager.get_session_state()
                })
            
            elif action == "execute_command":
                command = data.get("payload", {}).get("command", "Empty Command")
                logger.info(f"[J.A.R.V.I.S. WS] Routing command to Brain: {command}")
                try:
                    async with worker_session() as session:
                        result = await brain.process_command(db=session, user_text=command)
                    await websocket.send_json({
                        "type": "command_result",
                        "response": result.get("response"),
                        "data": result
                    })
                except Exception as exec_err:
                    # Error isolation: a failure inside a single command must never
                    # propagate and disconnect the HUD. Emit a clear failure state so
                    # the UI can transition out of "...checking" instead of hanging.
                    logger.error(f"[J.A.R.V.I.S. WS] Command execution failed: {exec_err}", exc_info=True)
                    try:
                        await websocket.send_json({
                            "type": "command_error",
                            "response": (
                                "I encountered an internal error while processing that request. "
                                "Please try again."
                            ),
                            "error": str(exec_err),
                        })
                    except Exception:
                        pass

            elif action == "voice_toggle":
                """HUD VoiceButton control: toggles wake-word listening on/off (local operator only)."""
                payload = data.get("payload", {})
                desired = payload.get("enable")
                wake_tool = _get_wake_word_tool()
                try:
                    if desired is None:
                        current = await session_manager.get_session_state()
                        desired = not current.get("is_voice_listening", False)
                    outcome = await wake_tool._run(action="start" if desired else "stop")
                    listening = bool(desired)
                except Exception as voice_err:
                    outcome = f"Voice toggle failed: {str(voice_err)}"
                    listening = False
                await websocket.send_json({
                    "type": "voice_state",
                    "listening": listening,
                    "message": outcome,
                })

            elif action == "voice_status":
                current = await session_manager.get_session_state()
                await websocket.send_json({
                    "type": "voice_state",
                    "listening": bool(current.get("is_voice_listening")),
                    "wake_session": bool(current.get("is_wake_session")),
                    "message": "Voice status retrieved.",
                })

            else:
                await websocket.send_json({"status": "acknowledged", "received": data})
                
    except WebSocketDisconnect:
        websocket_manager.disconnect(client_id)
        logger.info("[J.A.R.V.I.S.] Frontend HUD client disconnected from WebSocket.")
    except Exception as e:
        websocket_manager.disconnect(client_id)
        logger.error(f"[J.A.R.V.I.S. WS] Error processing message: {e}", exc_info=True)
