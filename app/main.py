import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
from app.api.endpoints import router as api_router
from app.database.session import engine, Base, AsyncSessionLocal
from app.models.domain import Meeting
from app.bot.handlers import handle_update
from app.api.max_client import max_client
from datetime import datetime, timedelta

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

async def seed_data():
    async with AsyncSessionLocal() as session:
        # Check if meetings already exist
        from sqlalchemy.future import select
        res = await session.execute(select(Meeting).limit(1))
        if res.scalars().first():
            return # Already seeded
            
        logger.info("Seeding initial data...")
        meetings = [
            Meeting(
                title="Волейбол в кампусе №3",
                activity_type="Волейбол",
                location="Спортплощадка 3 корпуса",
                date_time=datetime.now() + timedelta(days=1, hours=2),
                min_quorum=4,
                max_participants=6
            ),
            Meeting(
                title="Утренняя пробежка",
                activity_type="Бег",
                location="Парк у озера",
                date_time=datetime.now() + timedelta(hours=10),
                min_quorum=2,
                max_participants=10
            )
        ]
        session.add_all(meetings)
        await session.commit()
        logger.info("Data seeded successfully.")

async def polling_task():
    logger.info("Starting polling...")
    offset = 0
    while True:
        try:
            updates = await max_client.get_updates(offset=offset)
            for update in updates:
                update_id = update.get("update_id", 0)
                if update_id >= offset:
                    offset = update_id + 1
                await handle_update(update)
        except Exception as e:
            logger.error(f"Polling error: {e}")
        await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_data()
    
    polling_job = None
    if settings.bot_mode == "polling":
        polling_job = asyncio.create_task(polling_task())
        
    yield
    
    # Shutdown
    if polling_job:
        polling_job.cancel()
    await engine.dispose()

app = FastAPI(title="VSbore API", lifespan=lifespan)
app.include_router(api_router)
