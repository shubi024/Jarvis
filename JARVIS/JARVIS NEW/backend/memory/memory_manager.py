import logging
import json
from typing import Dict, Any, List, Optional
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import Column, String, DateTime, Text, Integer, Float, Boolean
from datetime import datetime, timezone

from backend.infrastructure.database import Base
from backend.infrastructure.state_manager import state_manager

logger = logging.getLogger("JARVIS.Memory.MemoryManager")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# jarvis_structured_memories / jarvis_messages use DateTime columns that map to
# PostgreSQL TIMESTAMP WITHOUT TIME ZONE. Passing timezone-aware datetimes there
# raises asyncpg "can't subtract offset-naive and offset-aware datetimes" and
# aborts every write. Store naive UTC and treat it as UTC on read (_as_utc).
def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _as_utc(dt: datetime) -> datetime:
    """
    Normalizes DB-loaded datetimes to timezone-aware UTC.
    SQLite returns naive datetimes; subtracting them from aware datetimes raises
    TypeError, silently emptying search results via the broad exception handler.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class MemoryCategory(str):
    PERSONAL = "personal"
    BUSINESS = "business"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROJECT = "project"
    CLIENT = "client"
    PROCEDURAL = "procedural"


class MemoryStatus(str):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DEACTIVATED = "deactivated"


class MemoryModel(Base):
    """
    Canonical SQLAlchemy ORM model for JARVIS durable structured memories.
    Enforces scope-first separation, confidence tracking, source provenance, and lifecycle states.
    """
    __tablename__ = "jarvis_structured_memories"

    memory_id = Column(String(64), primary_key=True, index=True)
    category = Column(String(32), index=True, nullable=False, default=MemoryCategory.SEMANTIC)
    client_scope = Column(String(64), index=True, nullable=True)
    project_scope = Column(String(64), index=True, nullable=True)
    user_id = Column(String(64), index=True, nullable=True)
    
    content = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0, nullable=False)
    source_task_id = Column(String(64), index=True, nullable=True)
    source_provenance = Column(Text, nullable=True)
    
    sensitivity_level = Column(String(32), default="standard", nullable=False) # standard, sensitive, restricted
    status = Column(String(32), index=True, default=MemoryStatus.ACTIVE, nullable=False)
    
    created_at = Column(DateTime, default=utc_now_naive, nullable=False, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, nullable=False)
    superseded_by = Column(String(64), nullable=True)


class MessageModel(Base):
    """SQLAlchemy ORM model for persistent conversation message memory storage in PostgreSQL with scopes."""
    __tablename__ = "jarvis_messages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False, default="default")
    client_scope = Column(String(64), index=True, nullable=True)
    project_scope = Column(String(64), index=True, nullable=True)
    user_id = Column(String(64), index=True, nullable=True)
    sender = Column(String(64), index=True, nullable=False)
    text = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=utc_now_naive, nullable=False, index=True)


class MemoryManager:
    """
    Hybrid Memory Manager for J.A.R.V.I.S.
    Combines Redis short-term working memory caching with a secure, scoped, confidence-ranked,
    and history-isolated PostgreSQL structured memory vault supporting complete lifecycle actions.
    """
    def __init__(self, default_ttl_seconds: int = 86400):
        self.default_ttl = default_ttl_seconds

    def _validate_sensitivity(self, content: str, sensitivity_level: str) -> tuple[bool, str]:
        """
        Enforces policy-driven sensitivity checks. Restricted levels reject secrets/OTPs entirely,
        while standard levels scrub explicit token markers.
        """
        if not content:
            return True, ""
        
        lower_content = content.lower()
        restricted_markers = {"password", "secret", "api_key", "token", "credential", "otp", "auth_token"}
        
        if sensitivity_level in {"sensitive", "restricted"}:
            for marker in restricted_markers:
                if marker in lower_content:
                    return False, f"Memory rejected: '{marker}' detected under strict '{sensitivity_level}' sensitivity policy."
                    
        return True, content

    # --- Redis Short-Term / Working Memory ---
    async def store_memory(self, session_id: str, memory_key: str, data: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """Stores or updates a specific working memory block in Redis for a session."""
        composite_key = f"jarvis:memory:{session_id}:{memory_key}"
        expiry = ttl if ttl is not None else self.default_ttl
        success = await state_manager.set_state(composite_key, data, expire_seconds=expiry)
        if success:
            logger.debug(f"Stored memory block [{memory_key}] for session [{session_id}]")
        return success

    async def get_memory(self, session_id: str, memory_key: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific working memory block from Redis."""
        composite_key = f"jarvis:memory:{session_id}:{memory_key}"
        return await state_manager.get_state(composite_key)

    async def clear_memory(self, session_id: str) -> bool:
        """Clears all working memory blocks and sliding cache keys associated with a session in Redis."""
        if not state_manager.redis_client:
            return False

        try:
            pattern = f"jarvis:memory:{session_id}:*"
            keys_to_delete = []
            async for key in state_manager.redis_client.scan_iter(match=pattern):
                keys_to_delete.append(key)
            
            history_key = f"jarvis:history:{session_id}"
            keys_to_delete.append(history_key)

            if keys_to_delete:
                await state_manager.redis_client.delete(*keys_to_delete)
            
            logger.info(f"Successfully cleared all Redis memory blocks and history for session [{session_id}]")
            return True
        except Exception as e:
            logger.error(f"Failed to clear memory blocks for session [{session_id}]: {e}")
            return False

    # --- PostgreSQL Persistent Structured Memory Vault ---
    async def create_structured_memory(
        self,
        db: AsyncSession,
        memory_id: str,
        content: str,
        category: str = MemoryCategory.SEMANTIC,
        client_scope: Optional[str] = None,
        project_scope: Optional[str] = None,
        user_id: Optional[str] = None,
        confidence: float = 1.0,
        source_task_id: Optional[str] = None,
        source_provenance: Optional[str] = None,
        sensitivity_level: str = "standard"
    ) -> Optional[MemoryModel]:
        """Creates a new durable structured memory with scope binding and strict sensitivity policy checks."""
        is_safe, reason = self._validate_sensitivity(content, sensitivity_level)
        if not is_safe:
            logger.warning(f"Memory creation blocked by sensitivity policy: {reason}")
            return None

        try:
            db_memory = MemoryModel(
                memory_id=memory_id,
                content=content,
                category=category,
                client_scope=client_scope,
                project_scope=project_scope,
                user_id=user_id,
                confidence=confidence,
                source_task_id=source_task_id,
                source_provenance=source_provenance,
                sensitivity_level=sensitivity_level,
                status=MemoryStatus.ACTIVE,
                created_at=utc_now_naive(),
                updated_at=utc_now_naive()
            )
            db.add(db_memory)
            await db.commit()
            await db.refresh(db_memory)
            logger.info(f"Created structured memory [{memory_id}] under category [{category}].")
            return db_memory
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to create structured memory [{memory_id}]: {e}")
            raise

    async def supersede_memory(
        self,
        db: AsyncSession,
        old_memory_id: str,
        new_memory_id: str,
        new_content: str,
        source_task_id: Optional[str] = None
    ) -> Optional[MemoryModel]:
        """Supersedes an outdated memory with a new record, maintaining historical isolation."""
        try:
            old_mem = await db.get(MemoryModel, old_memory_id)
            if not old_mem:
                logger.warning(f"Attempted to supersede non-existent memory ID: [{old_memory_id}]")
                return None

            old_mem.status = MemoryStatus.SUPERSEDED
            old_mem.superseded_by = new_memory_id
            old_mem.updated_at = utc_now_naive()

            new_mem = MemoryModel(
                memory_id=new_memory_id,
                content=new_content,
                category=old_mem.category,
                client_scope=old_mem.client_scope,
                project_scope=old_mem.project_scope,
                user_id=old_mem.user_id,
                confidence=old_mem.confidence,
                source_task_id=source_task_id,
                source_provenance=f"Supersedes memory {old_memory_id}",
                sensitivity_level=old_mem.sensitivity_level,
                status=MemoryStatus.ACTIVE,
                created_at=utc_now_naive(),
                updated_at=utc_now_naive()
            )
            db.add(new_mem)
            await db.commit()
            await db.refresh(new_mem)
            logger.info(f"Memory [{old_memory_id}] successfully superseded by [{new_memory_id}].")
            return new_mem
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to supersede memory [{old_memory_id}]: {e}")
            raise

    async def resolve_conflict(
        self,
        db: AsyncSession,
        conflicting_memory_ids: List[str],
        resolved_memory_id: str,
        resolved_content: str,
        source_task_id: Optional[str] = None
    ) -> Optional[MemoryModel]:
        """Resolves contradictory facts by archiving/superseding conflicting records and creating a verified replacement."""
        try:
            scopes = (None, None, None)
            for mem_id in conflicting_memory_ids:
                mem = await db.get(MemoryModel, mem_id)
                if mem:
                    mem.status = MemoryStatus.SUPERSEDED
                    mem.superseded_by = resolved_memory_id
                    mem.updated_at = utc_now_naive()
                    scopes = (mem.client_scope, mem.project_scope, mem.user_id)

            resolved_mem = MemoryModel(
                memory_id=resolved_memory_id,
                content=resolved_content,
                category=MemoryCategory.SEMANTIC,
                client_scope=scopes[0],
                project_scope=scopes[1],
                user_id=scopes[2],
                confidence=1.0,
                source_task_id=source_task_id,
                source_provenance=f"Conflict resolution superseding IDs: {conflicting_memory_ids}",
                sensitivity_level="standard",
                status=MemoryStatus.ACTIVE,
                created_at=utc_now_naive(),
                updated_at=utc_now_naive()
            )
            db.add(resolved_mem)
            await db.commit()
            await db.refresh(resolved_mem)
            logger.info(f"Resolved conflict across memories {conflicting_memory_ids} into [{resolved_memory_id}].")
            return resolved_mem
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to resolve memory conflict: {e}")
            raise

    async def archive_memory(self, db: AsyncSession, memory_id: str) -> bool:
        """Transitions a memory state to ARCHIVED."""
        mem = await db.get(MemoryModel, memory_id)
        if mem:
            mem.status = MemoryStatus.ARCHIVED
            mem.updated_at = utc_now_naive()
            await db.commit()
            return True
        return False

    async def deactivate_memory(self, db: AsyncSession, memory_id: str) -> bool:
        """Transitions a memory state to DEACTIVATED."""
        mem = await db.get(MemoryModel, memory_id)
        if mem:
            mem.status = MemoryStatus.DEACTIVATED
            mem.updated_at = utc_now_naive()
            await db.commit()
            return True
        return False

    async def search_memories(
        self,
        db: AsyncSession,
        query_text: str,
        client_scope: Optional[str] = None,
        project_scope: Optional[str] = None,
        category: Optional[str] = None,
        allow_global_fallback: bool = False,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Performs scope-first, confidence-ranked memory retrieval. 
        Enforces strict client/project scope separation, preventing global memory 
        from overriding scoped execution unless allow_global_fallback is explicitly set.
        """
        try:
            stmt = select(MemoryModel).where(MemoryModel.status == MemoryStatus.ACTIVE)

            # Strict Scope Isolation Policy
            if client_scope and project_scope:
                if allow_global_fallback:
                    stmt = stmt.where(
                        ((MemoryModel.client_scope == client_scope) & (MemoryModel.project_scope == project_scope)) |
                        ((MemoryModel.client_scope == None) & (MemoryModel.project_scope == None))
                    )
                else:
                    stmt = stmt.where(
                        (MemoryModel.client_scope == client_scope) & (MemoryModel.project_scope == project_scope)
                    )
            elif client_scope:
                if allow_global_fallback:
                    stmt = stmt.where((MemoryModel.client_scope == client_scope) | (MemoryModel.client_scope == None))
                else:
                    stmt = stmt.where(MemoryModel.client_scope == client_scope)
            else:
                # If no scope provided, restrict entirely to global / un-scoped memories
                stmt = stmt.where((MemoryModel.client_scope == None) & (MemoryModel.project_scope == None))

            if category:
                stmt = stmt.where(MemoryModel.category == category)

            result = await db.execute(stmt)
            memories = result.scalars().all()

            scored_memories = []
            query_tokens = set(query_text.lower().split())

            for mem in memories:
                mem_text_lower = mem.content.lower()
                matches = sum(1 for token in query_tokens if token in mem_text_lower)
                relevance = matches / max(len(query_tokens), 1)
                
                recency_factor = 1.0 / (1.0 + max(0.0, (utc_now() - _as_utc(mem.created_at)).total_seconds() / 86400.0))
                composite_score = (relevance * 0.5) + (mem.confidence * 0.3) + (recency_factor * 0.2)

                if relevance > 0 or len(query_tokens) == 0:
                    scored_memories.append((composite_score, mem))

            scored_memories.sort(key=lambda x: x[0], reverse=True)

            ranked_results = []
            for score, mem in scored_memories[:limit]:
                ranked_results.append({
                    "memory_id": mem.memory_id,
                    "category": mem.category,
                    "content": mem.content,
                    "confidence": mem.confidence,
                    "client_scope": mem.client_scope,
                    "project_scope": mem.project_scope,
                    "source_task_id": mem.source_task_id,
                    "ranking_score": score,
                    "created_at": mem.created_at.isoformat() if mem.created_at else None
                })

            return ranked_results
        except Exception as e:
            logger.error(f"Failed to search structured memories: {e}")
            return []

    # --- PostgreSQL Persistent Conversation Vault ---
    async def save_message(
        self, 
        db: AsyncSession, 
        sender: str, 
        text: str, 
        session_id: str = "default",
        client_scope: Optional[str] = None,
        project_scope: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Optional[MessageModel]:
        """Saves a conversation message to the persistent PostgreSQL Vault with user/scope isolation."""
        is_safe, scrubbed_text = self._validate_sensitivity(text, "sensitive")
        if not is_safe:
            scrubbed_text = "[SENSITIVE CONTENT REDACTED]"

        try:
            db_message = MessageModel(
                session_id=session_id, 
                client_scope=client_scope,
                project_scope=project_scope,
                user_id=user_id,
                sender=sender, 
                text=scrubbed_text, 
                timestamp=utc_now().replace(tzinfo=None)
            )
            db.add(db_message)
            await db.commit()
            await db.refresh(db_message)
            logger.debug(f"Persistent conversation message saved [{sender}] in session [{session_id}]")
            return db_message
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to save message to persistent vault: {e}")
            raise

    async def get_recent_history(
        self, 
        db: AsyncSession, 
        session_id: str = "default", 
        client_scope: Optional[str] = None,
        project_scope: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Retrieves scoped recent chat history from PostgreSQL for context injection."""
        try:
            stmt = select(MessageModel).where(MessageModel.session_id == session_id)
            if client_scope:
                stmt = stmt.where((MessageModel.client_scope == client_scope) | (MessageModel.client_scope == None))
            if project_scope:
                stmt = stmt.where((MessageModel.project_scope == project_scope) | (MessageModel.project_scope == None))

            stmt = stmt.order_by(MessageModel.timestamp.desc()).limit(limit)
            result = await db.execute(stmt)
            messages = result.scalars().all()
            
            formatted_history = [
                {
                    "sender": msg.sender, 
                    "text": msg.text, 
                    "timestamp": msg.timestamp.isoformat()
                }
                for msg in reversed(messages)
            ]
            return formatted_history
        except Exception as e:
            logger.error(f"Failed to fetch chat history from persistent vault: {e}")
            return []

memory_manager = MemoryManager()