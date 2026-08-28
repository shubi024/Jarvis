import logging
import json
from typing import Dict, Any, Optional
import redis.asyncio as redis

from backend.infrastructure.config import settings

logger = logging.getLogger("JARVIS.Infrastructure.StateManager")

class StateManager:
    """
    State Manager for J.A.R.V.I.S.
    Handles ephemeral state caching, task progress tracking, and session data
    using Redis as a high-performance in-memory datastore.
    """
    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None

    async def connect(self):
        """Initializes the Redis connection for state management during application startup."""
        try:
            self.redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
            await self.redis_client.ping()
            logger.info("StateManager successfully connected to Redis cache.")
        except Exception as e:
            logger.warning(f"StateManager failed to connect to Redis: {e}. State caching will operate in fallback mode.")
            self.redis_client = None

    async def set_state(self, key: str, value: Dict[str, Any], expire_seconds: Optional[int] = None) -> bool:
        """Stores a state dictionary in Redis with an optional expiration time."""
        if not self.redis_client:
            return False
        try:
            payload = json.dumps(value)
            if expire_seconds:
                await self.redis_client.setex(key, expire_seconds, payload)
            else:
                await self.redis_client.set(key, payload)
            return True
        except Exception as e:
            logger.error(f"Failed to set state for key [{key}]: {e}")
            return False

    async def get_state(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieves and decodes a state dictionary from Redis."""
        if not self.redis_client:
            return None
        try:
            payload = await self.redis_client.get(key)
            if payload:
                return json.loads(payload)
            return None
        except Exception as e:
            logger.error(f"Failed to get state for key [{key}]: {e}")
            return None

    async def delete_state(self, key: str) -> bool:
        """Deletes a state key from Redis."""
        if not self.redis_client:
            return False
        try:
            await self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Failed to delete state for key [{key}]: {e}")
            return False

    async def close(self):
        """Closes the Redis connection cleanly during application shutdown."""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("StateManager Redis connection closed.")

state_manager = StateManager()