import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Any, Optional
from pydantic import BaseModel, Field

import redis.asyncio as redis
from backend.infrastructure.config import settings

logger = logging.getLogger("JARVIS.Infrastructure.EventBus")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# --- Canonical Event Contracts ---

class EventType(str, Enum):
    TASK = "task"
    WORKFLOW = "workflow"
    AGENT = "agent"
    TOOL = "tool"
    APPROVAL = "approval"
    VERIFICATION = "verification"
    SECURITY = "security"
    SYSTEM = "system"
    VOICE = "voice"
    OBSERVATION = "observation"

class JarvisEvent(BaseModel):
    """
    Canonical Event Envelope.
    Guarantees perfectly structured telemetry for the Queue, DB, and WebSocket HUD.
    """
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_type: EventType
    topic: str = Field(..., description="Routing topic, e.g., 'task.completed'")
    timestamp: datetime = Field(default_factory=utc_now)
    correlation_id: Optional[str] = None
    task_id: Optional[str] = None
    workflow_id: Optional[str] = None
    source: str = Field(default="system", description="Who emitted the event (e.g., 'FRIDAY', 'Brain')")
    payload: Dict[str, Any] = Field(default_factory=dict)


# --- Event Bus Infrastructure ---

class EventBus:
    """
    Asynchronous Event Bus for J.A.R.V.I.S.
    Provides typed event publishing, async subscription, error isolation, 
    and cross-process Redis Pub/Sub bridging.
    """
    
    REDIS_CHANNEL = "jarvis_global_event_bus"

    def __init__(self):
        self.instance_id = uuid.uuid4().hex
        self.redis_client: Optional[redis.Redis] = None
        self.pubsub: Optional[redis.client.PubSub] = None
        self.subscribers: Dict[str, List[Callable]] = {}
        self._listener_task: Optional[asyncio.Task] = None

    async def connect(self):
        """Initializes the Redis connection and starts the cross-process listener loop."""
        try:
            self.redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
            await self.redis_client.ping()
            
            # Setup cross-process Pub/Sub listener
            self.pubsub = self.redis_client.pubsub()
            await self.pubsub.subscribe(self.REDIS_CHANNEL)
            self._listener_task = asyncio.create_task(self._redis_listener_loop())
            
            logger.info("EventBus successfully connected to Redis Pub/Sub and listener loop started.")
        except Exception as e:
            logger.warning(f"EventBus failed to connect to Redis: {e}. Falling back to single-process in-memory dispatch.")
            self.redis_client = None

    async def _redis_listener_loop(self):
        """Background task that receives Redis events and dispatches them locally."""
        try:
            if not self.pubsub:
                return
                
            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    try:
                        raw_data = json.loads(message["data"])
                        origin = raw_data.get("origin")
                        
                        # Ignore broadcasts that originated from this exact process instance
                        if origin == self.instance_id:
                            continue
                            
                        event_data = raw_data.get("event")
                        if event_data:
                            event = JarvisEvent(**event_data)
                            await self._dispatch_local(event)
                            
                    except Exception as e:
                        logger.error(f"EventBus failed to parse incoming Redis message: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("EventBus Redis listener loop cleanly cancelled.")
        except Exception as e:
            logger.error(f"EventBus Redis listener loop encountered a fatal error: {e}")

    async def publish(self, event: JarvisEvent):
        """
        Publishes a normalized JarvisEvent.
        Dispatches to local subscribers immediately, then broadcasts to Redis for other workers.
        """
        # 1. Dispatch locally first for immediate responsiveness
        await self._dispatch_local(event)

        # 2. Broadcast across processes via Redis
        if self.redis_client:
            try:
                # Wrap the event with our instance ID to prevent local echo loops
                envelope = {
                    "origin": self.instance_id,
                    "event": json.loads(event.model_dump_json())
                }
                await self.redis_client.publish(self.REDIS_CHANNEL, json.dumps(envelope))
            except Exception as e:
                logger.error(f"Failed to publish event [{event.topic}] to Redis: {e}")

    async def _dispatch_local(self, event: JarvisEvent):
        """Safely routes the event to matching local callbacks with strict error isolation."""
        callbacks_to_invoke = []
        
        # Exact topic match
        if event.topic in self.subscribers:
            callbacks_to_invoke.extend(self.subscribers[event.topic])
            
        # Wildcard match (e.g., used by WebSocket Manager or DB Audit Persister)
        if "*" in self.subscribers:
            callbacks_to_invoke.extend(self.subscribers["*"])

        for callback in callbacks_to_invoke:
            try:
                await callback(event)
            except Exception as e:
                # Error isolation: One bad subscriber must never break the event bus
                logger.error(f"Subscriber callback failed for topic [{event.topic}]: {e}", exc_info=True)

    async def subscribe(self, topic: str, callback: Callable):
        """
        Registers an async callback for a specific event topic or wildcard ('*').
        Matches the expected async interface to resolve the await mismatch.
        """
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        if callback not in self.subscribers[topic]:
            self.subscribers[topic].append(callback)
            logger.debug(f"Registered subscriber for topic: [{topic}]")

    async def unsubscribe(self, topic: str, callback: Callable):
        """Safely removes a previously registered subscriber."""
        if topic in self.subscribers and callback in self.subscribers[topic]:
            self.subscribers[topic].remove(callback)
            logger.debug(f"Unregistered subscriber from topic: [{topic}]")

    async def close(self):
        """Closes the event bus, cancels the listener loop, and shuts down Redis cleanly."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
                
        if self.pubsub:
            await self.pubsub.close()
            
        if self.redis_client:
            await self.redis_client.aclose()
            logger.info("EventBus Redis connection cleanly closed.")

# Canonical singleton instance
event_bus = EventBus()