import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
from app.api.endpoints import router as api_router
from app.database.session import engine, AsyncSessionLocal, init_db
from app.services.demo_data import ensure_demo_meetings
from app.bot.handlers import handle_update
from app.api.max_client import max_client

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

DEMO_REFRESH_INTERVAL = 60 * 60  # seconds


async def demo_data_task():
    """Keeps demo meetings in the future while the service is running."""
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await ensure_demo_meetings(session)
        except Exception as e:
            logger.error(f"Demo data refresh failed: {e}")
        await asyncio.sleep(DEMO_REFRESH_INTERVAL)

async def polling_task():
    logger.info("Starting polling...")
    try:
        me = await max_client.get_me()
        logger.info(f"Bot authorized as @{me.get('username')} ({me.get('name')})")
    except Exception as e:
        logger.error(f"Bot token check (GET /me) failed: {e}")

    marker = None
    while True:
        try:
            updates, marker = await max_client.get_updates(marker=marker)
            for update in updates:
                await handle_update(update)
            if not updates:
                # Avoid a tight loop when the API returns errors immediately
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Polling error: {e}")
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database...")
    await init_db()
    demo_job = asyncio.create_task(demo_data_task())

    polling_job = None
    if settings.bot_mode == "polling":
        polling_job = asyncio.create_task(polling_task())
        
    yield
    
    # Shutdown
    demo_job.cancel()
    if polling_job:
        polling_job.cancel()
    await max_client.close()
    await engine.dispose()

app = FastAPI(title="VSbore API", lifespan=lifespan)
app.include_router(api_router)
