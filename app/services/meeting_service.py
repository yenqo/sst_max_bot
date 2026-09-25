from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.models.domain import User, Meeting, MeetingParticipant
from datetime import datetime
from typing import Optional, List, Tuple

async def get_or_create_user(db: AsyncSession, max_id: str, username: str = None) -> User:
    result = await db.execute(select(User).where(User.max_id == max_id))
    user = result.scalars().first()
    if not user:
        user = User(max_id=max_id, username=username)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user

async def get_active_meetings(db: AsyncSession) -> List[Meeting]:
    result = await db.execute(
        select(Meeting)
        .where(Meeting.date_time > datetime.now())
        .options(selectinload(Meeting.participants))
    )
    return list(result.scalars().all())

async def get_my_meetings(db: AsyncSession, user_id: int) -> List[Meeting]:
    result = await db.execute(
        select(Meeting)
        .join(MeetingParticipant)
        .where(
            MeetingParticipant.user_id == user_id,
            MeetingParticipant.status.in_(["joined", "waitlist"]),
            Meeting.date_time > datetime.now()
        )
        .options(selectinload(Meeting.participants))
    )
    return list(result.scalars().all())

async def get_meeting_by_id(db: AsyncSession, meeting_id: int) -> Optional[Meeting]:
    result = await db.execute(
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .options(selectinload(Meeting.participants))
    )
    return result.scalars().first()

async def join_meeting(db: AsyncSession, meeting_id: int, user_id: int) -> Tuple[str, Meeting, Optional[List[User]]]:
    """
    Returns (status, meeting, newly_confirmed_users)
    status can be 'joined', 'waitlist', 'already_joined', 'not_found'
    newly_confirmed_users is a list of users to notify if the quorum is reached just now.
    """
    meeting = await get_meeting_by_id(db, meeting_id)
    if not meeting:
        return "not_found", None, None
        
    result = await db.execute(
        select(MeetingParticipant)
        .where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == user_id)
    )
    existing_participant = result.scalars().first()
    
    if existing_participant and existing_participant.status in ("joined", "waitlist"):
        return "already_joined", meeting, None

    active_count = sum(1 for p in meeting.participants if p.status == "joined")
    
    status = "joined" if active_count < meeting.max_participants else "waitlist"
    
    if existing_participant:
        existing_participant.status = status
        existing_participant.joined_at = datetime.now()
    else:
        participant = MeetingParticipant(meeting_id=meeting_id, user_id=user_id, status=status)
        db.add(participant)
    
    await db.commit()
    
    # Safely query new active count directly from DB to avoid relationship cache issues
    res_count = await db.execute(
        select(func.count(MeetingParticipant.id))
        .where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.status == "joined")
    )
    new_active_count = res_count.scalar() or 0
    
    newly_confirmed_users = None
    
    if status == "joined" and not meeting.is_confirmed and new_active_count >= meeting.min_quorum:
        meeting.is_confirmed = True
        db.add(meeting)
        await db.commit()
        
        # Fetch all joined users to notify them
        res_joined = await db.execute(
            select(MeetingParticipant.user_id)
            .where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.status == "joined")
        )
        joined_user_ids = [uid for uid in res_joined.scalars().all()]
        if joined_user_ids:
            res_users = await db.execute(select(User).where(User.id.in_(joined_user_ids)))
            newly_confirmed_users = list(res_users.scalars().all())
            
    return status, meeting, newly_confirmed_users, new_active_count

async def leave_meeting(db: AsyncSession, meeting_id: int, user_id: int) -> Tuple[str, Meeting, Optional[User]]:
    """
    Returns (status, meeting, promoted_user)
    status can be 'left', 'not_in_meeting', 'not_found'
    """
    meeting = await get_meeting_by_id(db, meeting_id)
    if not meeting:
        return "not_found", None, None
        
    result = await db.execute(
        select(MeetingParticipant)
        .where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == user_id)
    )
    participant = result.scalars().first()
    
    if not participant or participant.status == "cancelled":
        return "not_in_meeting", meeting, None
        
    was_joined = participant.status == "joined"
    participant.status = "cancelled"
    await db.commit()
    
    promoted_user = None
    if was_joined:
        # Check if anyone is in waitlist directly from DB
        res_waitlist = await db.execute(
            select(MeetingParticipant)
            .where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.status == "waitlist")
            .order_by(MeetingParticipant.joined_at.asc())
            .limit(1)
        )
        promoted_participant = res_waitlist.scalars().first()
        
        if promoted_participant:
            promoted_participant.status = "joined"
            await db.commit()
            
            res_user = await db.execute(select(User).where(User.id == promoted_participant.user_id))
            promoted_user = res_user.scalars().first()
            
    # Safely query new active count directly from DB
    res_count = await db.execute(
        select(func.count(MeetingParticipant.id))
        .where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.status == "joined")
    )
    new_active_count = res_count.scalar() or 0

    if meeting.is_confirmed and new_active_count < meeting.min_quorum:
        meeting.is_confirmed = False
        db.add(meeting)
        await db.commit()
        
    return "left", meeting, promoted_user
