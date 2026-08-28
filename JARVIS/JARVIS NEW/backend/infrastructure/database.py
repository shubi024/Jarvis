import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

from backend.infrastructure.config import settings

logger = logging.getLogger("JARVIS.Infrastructure.Database")

# Create asynchronous database engine
engine_kwargs = {
    "echo": settings.DEBUG,
    "future": True,
}

# Test configuration support without contaminating production
JARVIS_ENV = os.getenv("JARVIS_ENV", "production")
if JARVIS_ENV == "test":
    # Fallback to an in-memory SQLite DB for testing.
    # StaticPool keeps a single shared connection so worker_session()/background
    # services observe the same schema and data as the request-path fixtures.
    from sqlalchemy.pool import StaticPool
    DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    engine_kwargs["poolclass"] = StaticPool
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Use PostgreSQL settings from config
    DATABASE_URL = settings.DATABASE_URL

# Apply connection pooling suitable for background workers + API requests
# (SQLite does not support these arguments in the same way PostgreSQL does)
if "postgresql" in DATABASE_URL:
    engine_kwargs["pool_size"] = 20
    engine_kwargs["max_overflow"] = 10

engine = create_async_engine(DATABASE_URL, **engine_kwargs)

# One canonical AsyncSessionFactory for the whole application
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Backwards-compatible name retained for infrastructure probes and integrations.
# AsyncSessionFactory remains the authoritative session factory.
AsyncSessionLocal = AsyncSessionFactory

# Base class for SQLAlchemy declarative models
Base = declarative_base()

DEFAULT_OPERATOR_USER_ID = "human_user"


async def _seed_default_operator(conn) -> None:
    """
    Seeds the trusted local operator identity referenced by approval resolution.
    The interactive local operator is trusted by design (see config.JARVIS_AUTH_TOKEN),
    so a durable row must exist to satisfy FK constraints (jarvis_approvals.resolved_by).
    Idempotent: inserts only when missing.
    Uses Core insert() because AsyncConnection has no ORM Session.add().
    """
    from sqlalchemy import select, insert
    from backend.infrastructure.models import UserModel

    result = await conn.execute(
        select(UserModel.user_id).where(UserModel.user_id == DEFAULT_OPERATOR_USER_ID)
    )
    if result.scalar_one_or_none() is None:
        await conn.execute(
            insert(UserModel).values(
                user_id=DEFAULT_OPERATOR_USER_ID,
                username="Human Operator",
                role="operator",
            )
        )
        logger.info("Seeded default operator user [%s].", DEFAULT_OPERATOR_USER_ID)


async def init_db():
    """Startup lifecycle: Initializes database tables."""
    try:
        async with engine.begin() as conn:
            logger.info("Initializing database schema...")
            # Note: In production, Alembic is recommended over create_all
            await conn.run_sync(Base.metadata.create_all)
            await _seed_default_operator(conn)
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

async def close_db():
    """Shutdown lifecycle: Disposes the engine and cleans up the connection pool."""
    logger.info("Closing database engine...")
    await engine.dispose()
    logger.info("Database engine closed.")

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency generator for FastAPI/request paths.
    Yields an asynchronous database session, ensuring safe transaction management.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            logger.error(f"Database session error occurred: {e}. Rolling back transaction.")
            await session.rollback()
            raise
        finally:
            await session.close()

@asynccontextmanager
async def worker_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for background services, queue workers, and agents.
    Ensures background services can create their own independent DB sessions safely.
    
    Usage:
        async with worker_session() as db:
            await db.execute(...)
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            logger.error(f"Worker database transaction failed: {e}. Rolling back.")
            await session.rollback()
            raise
        finally:
            await session.close()
