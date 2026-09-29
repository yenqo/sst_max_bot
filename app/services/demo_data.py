"""
Demo data that never goes stale.

Each template describes a recurring demo meeting. ensure_demo_meetings() makes sure
every template has an upcoming meeting: if the previous one has passed (or starts
too soon to be useful), a new one is created at the next suitable slot and pre-filled
with demo participants. It runs at startup and then periodically, so the jury always
sees fresh meetings with different fill levels.

Reset all meetings and recreate demo data:
    docker compose exec app python -m app.services.demo_data --reset
"""
import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta, time

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.timeutils import now
from app.models.domain import User, Meeting, MeetingParticipant

logger = logging.getLogger(__name__)

DEMO_USER_PREFIX = "demo_"

# A meeting starting sooner than this is replaced by the next occurrence
MIN_LEAD_TIME = timedelta(hours=2)


@dataclass(frozen=True)
class DemoMeeting:
    title: str
    activity_type: str
    location: str
    start: time
    skill_level: str
    min_quorum: int
    max_participants: Optional[int]  # None = no limit
    demo_participants: int  # pre-filled participants (beyond max go to the waitlist)


DEMO_MEETINGS = [
    # 3/6, one more needed for quorum: the main "reach the quorum" scenario
    DemoMeeting("Волейбол у спорткомплекса МГУ", "Волейбол", "Волейбольная площадка у спорткомплекса МГУ",
                time(19, 0), "Любительский", 4, 6, 3),
    # 9/10, quorum reached, one place left
    DemoMeeting("Футбол 5×5 на стадионе МГУ", "Футбол", "Стадион МГУ, футбольное поле",
                time(20, 0), "Средний", 8, 10, 9),
    # 4/4 + waitlist: the "no places, join the waitlist" scenario
    DemoMeeting("Настольный теннис в спорткорпусе", "Настольный теннис", "Спортивный трёхзальный корпус МГУ",
                time(18, 30), "Любительский", 2, 4, 5),
    # 1/8, just started gathering
    DemoMeeting("Велопрогулка по набережной", "Велопрогулка", "Воробьёвская набережная",
                time(11, 0), "Любительский", 3, 8, 1),
    # 2/∞, quorum reached, no participant limit
    DemoMeeting("Утренняя пробежка по Воробьёвым горам", "Бег", "Смотровая площадка Воробьёвых гор",
                time(7, 30), "Любой", 2, None, 2),
]

DEMO_USER_COUNT = max(t.demo_participants for t in DEMO_MEETINGS)


def is_demo_user(user: User) -> bool:
    return user.max_id.startswith(DEMO_USER_PREFIX)


def next_slot(start: time, current: datetime) -> datetime:
    """Nearest datetime at `start` that is at least MIN_LEAD_TIME ahead."""
    slot = datetime.combine(current.date(), start)
    while slot - current < MIN_LEAD_TIME:
        slot += timedelta(days=1)
    return slot


async def _get_demo_users(db: AsyncSession) -> list:
    max_ids = [f"{DEMO_USER_PREFIX}{i}" for i in range(1, DEMO_USER_COUNT + 1)]
    result = await db.execute(select(User).where(User.max_id.in_(max_ids)))
    existing = {u.max_id: u for u in result.scalars().all()}

    for i, max_id in enumerate(max_ids, start=1):
        if max_id not in existing:
            user = User(max_id=max_id, username=f"Демо-участник {i}")
            db.add(user)
            existing[max_id] = user
    await db.flush()
    return [existing[max_id] for max_id in max_ids]


async def ensure_demo_meetings(db: AsyncSession) -> int:
    """Creates the next occurrence for every template without an upcoming meeting."""
    current = now()
    created = 0
    demo_users = None

    for tpl in DEMO_MEETINGS:
        result = await db.execute(
            select(Meeting.id).where(
                Meeting.title == tpl.title,
                Meeting.date_time > current + MIN_LEAD_TIME,
            ).limit(1)
        )
        if result.scalars().first():
            continue

        if demo_users is None:
            demo_users = await _get_demo_users(db)

        meeting = Meeting(
            title=tpl.title,
            activity_type=tpl.activity_type,
            location=tpl.location,
            date_time=next_slot(tpl.start, current),
            skill_level=tpl.skill_level,
            min_quorum=tpl.min_quorum,
            max_participants=tpl.max_participants,
            is_confirmed=min(tpl.demo_participants, tpl.max_participants or tpl.demo_participants) >= tpl.min_quorum,
        )
        db.add(meeting)
        await db.flush()

        for i, user in enumerate(demo_users[:tpl.demo_participants]):
            db.add(MeetingParticipant(
                meeting_id=meeting.id,
                user_id=user.id,
                status="waitlist" if tpl.max_participants is not None and i >= tpl.max_participants else "joined",
                # keep waitlist order stable
                joined_at=current - timedelta(minutes=tpl.demo_participants - i),
            ))
        created += 1

    await db.commit()
    if created:
        logger.info(f"Created {created} demo meeting(s)")
    return created


async def reset_meetings(db: AsyncSession) -> None:
    """Deletes all meetings and participations (users are kept)."""
    await db.execute(delete(MeetingParticipant))
    await db.execute(delete(Meeting))
    await db.commit()
    logger.info("All meetings deleted")


async def _main(reset: bool) -> None:
    from app.database.session import engine, AsyncSessionLocal, init_db

    await init_db()
    async with AsyncSessionLocal() as db:
        if reset:
            await reset_meetings(db)
        await ensure_demo_meetings(db)
    await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main(reset="--reset" in sys.argv))
