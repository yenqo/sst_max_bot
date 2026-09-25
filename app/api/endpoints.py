from fastapi import APIRouter, Request, BackgroundTasks
from app.bot.handlers import handle_update
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/webhook")
async def max_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        update = await request.json()
        background_tasks.add_task(handle_update, update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return {"status": "error"}

@router.get("/health")
async def health_check():
    return {"status": "alive"}
