import logging
from typing import Dict, Any, Union
from fastapi import WebSocket

from backend.infrastructure.event_bus import event_bus, JarvisEvent

logger = logging.getLogger("JARVIS.Infrastructure.WebSocketManager")

class WebSocketManager:
    """
    WebSocket Connection Manager for J.A.R.V.I.S.
    Manages active client connections, broadcasts real-time agent telemetry, 
    task execution updates, and approval notifications.
    Acts as a pure telemetry bridge between the EventBus and the HUD.
    """
    def __init__(self):
        # Maps client/session IDs to active WebSocket connections
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        """Accepts and registers a new WebSocket connection."""
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket client connected: [{client_id}]. Total active: {len(self.active_connections)}")

    def disconnect(self, client_id: str):
        """Removes a disconnected WebSocket client."""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"WebSocket client disconnected: [{client_id}]. Total active: {len(self.active_connections)}")

    async def send_personal_message(self, message: Union[Dict[str, Any], JarvisEvent], client_id: str):
        """Sends a JSON-normalized message or JarvisEvent to a specific connected client."""
        if client_id in self.active_connections:
            try:
                # Safely serialize Pydantic models with datetimes to JSON-compatible dicts
                payload = message.model_dump(mode="json") if isinstance(message, JarvisEvent) else message
                await self.active_connections[client_id].send_json(payload)
            except Exception as e:
                logger.error(f"Failed to send personal message to client [{client_id}]: {e}")
                self.disconnect(client_id)

    async def broadcast(self, message: Union[Dict[str, Any], JarvisEvent]):
        """Broadcasts a JSON-normalized message or JarvisEvent to all active WebSocket clients."""
        if not self.active_connections:
            return

        payload = message.model_dump(mode="json") if isinstance(message, JarvisEvent) else message
        
        disconnected_clients = []
        for client_id, websocket in self.active_connections.items():
            try:
                await websocket.send_json(payload)
            except Exception as e:
                logger.error(f"Failed to broadcast to client [{client_id}]: {e}")
                disconnected_clients.append(client_id)

        # Cleanup stale connections safely after iteration
        for client_id in disconnected_clients:
            self.disconnect(client_id)

    async def _event_bus_handler(self, event: JarvisEvent):
        """
        Internal callback triggered by the EventBus.
        Converts the canonical JarvisEvent into the HUD's canonical telemetry
        envelope and broadcasts it directly to all connected clients.
        """
        message = {
            "type": "telemetry",
            "topic": event.topic,
            "payload": event.payload,
            "task_id": event.task_id,
            "workflow_id": event.workflow_id,
            "source": event.source,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        }
        await self.broadcast(message)

    async def start(self):
        """Startup lifecycle hook: Subscribes to all EventBus telemetry."""
        await event_bus.subscribe("*", self._event_bus_handler)
        logger.info("WebSocketManager successfully subscribed to global EventBus telemetry.")

    async def stop(self):
        """Shutdown lifecycle hook: Unsubscribes and cleans up open connections."""
        await event_bus.unsubscribe("*", self._event_bus_handler)
        
        # Gracefully disconnect all connected HUD clients
        client_ids = list(self.active_connections.keys())
        for client_id in client_ids:
            try:
                await self.active_connections[client_id].close(code=1001, reason="JARVIS server shutting down")
            except Exception:
                pass
            self.disconnect(client_id)
            
        logger.info("WebSocketManager safely detached from EventBus and closed all sockets.")

websocket_manager = WebSocketManager()