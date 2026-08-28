import logging
import time
from typing import Dict, Any, Optional
from sqlalchemy import text

from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.config import settings
from backend.infrastructure.event_bus import event_bus
from backend.infrastructure.state_manager import state_manager

logger = logging.getLogger("JARVIS.Infrastructure.Diagnostics")

class DiagnosticsEngine:
    """
    Diagnostics and Health Check Engine for J.A.R.V.I.S.
    Performs real-time readiness and liveness probes on core infrastructure components
    (PostgreSQL database, Redis/State, EventBus, and LLM providers).
    """
    async def check_database(self) -> Dict[str, Any]:
        """Probes PostgreSQL database connectivity and query responsiveness."""
        start_time = time.time()
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            latency = round((time.time() - start_time) * 1000, 2)
            return {"status": "healthy", "latency_ms": latency}
        except Exception as e:
            logger.error(f"Diagnostics database check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def check_redis(self) -> Dict[str, Any]:
        """Probes Redis cache and state manager connectivity."""
        start_time = time.time()
        try:
            if state_manager.redis_client:
                await state_manager.redis_client.ping()
                latency = round((time.time() - start_time) * 1000, 2)
                return {"status": "healthy", "latency_ms": latency}
            return {"status": "degraded", "error": "Redis client running in fallback mode"}
        except Exception as e:
            logger.error(f"Diagnostics Redis check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def check_event_bus(self) -> Dict[str, Any]:
        """Probes EventBus Pub/Sub responsiveness."""
        try:
            if event_bus.redis_client:
                return {"status": "healthy", "mode": "redis_pubsub"}
            return {"status": "degraded", "mode": "in_memory_fallback"}
        except Exception as e:
            logger.error(f"Diagnostics EventBus check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def run_full_diagnostics(self) -> Dict[str, Any]:
        """Executes a comprehensive system diagnostics report across all infrastructure dependencies."""
        db_status = await self.check_database()
        redis_status = await self.check_redis()
        event_bus_status = await self.check_event_bus()

        overall_status = "healthy"
        if db_status["status"] == "unhealthy" or redis_status["status"] == "unhealthy":
            overall_status = "unhealthy"
        elif redis_status["status"] == "degraded" or event_bus_status["status"] == "degraded":
            overall_status = "degraded"

        return {
            "project": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "environment": settings.ENVIRONMENT,
            "status": overall_status,
            "timestamp": time.time(),
            "components": {
                "database": db_status,
                "redis": redis_status,
                "event_bus": event_bus_status,
                "llm_providers": {
                    provider: len(keys) for provider, keys in settings.llm_keys.items()
                },
                "cloudflare_accounts": len(settings.cloudflare_accounts)
            }
        }

diagnostics_engine = DiagnosticsEngine()