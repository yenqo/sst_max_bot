from datetime import datetime
from zoneinfo import ZoneInfo
from app.core.config import settings

TZ = ZoneInfo(settings.timezone)


def now() -> datetime:
    """
    Current local time of the service (naive, in settings.timezone).
    All meeting times are stored and shown in this timezone,
    regardless of the container's system timezone (UTC in Docker).
    """
    return datetime.now(TZ).replace(tzinfo=None)
