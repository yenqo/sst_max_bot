import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# Columns added after the first release: (table, column, DDL type).
# create_all() creates missing tables but does not alter existing ones.
ADDED_COLUMNS = [
    ("meetings", "comment", "VARCHAR(50)"),
    ("meeting_drafts", "awaiting", "VARCHAR(16)"),
]


def _add_missing_columns(sync_conn):
    inspector = inspect(sync_conn)
    for table, column, ddl_type in ADDED_COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            logger.info(f"Added column {table}.{column}")


async def init_db():
    """Creates tables and applies simple additive migrations."""
    import app.models.domain  # noqa: F401  (register models)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
