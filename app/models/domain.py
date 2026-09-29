from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.core.timeutils import now
from app.database.session import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    max_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    created_at = Column(DateTime, default=now)

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    activity_type = Column(String, nullable=False) # e.g. Волейбол, Футбол
    location = Column(String, nullable=False)
    date_time = Column(DateTime, nullable=False)
    skill_level = Column(String, default="Любительский")
    min_quorum = Column(Integer, default=4)
    max_participants = Column(Integer, nullable=True)  # None = no limit
    is_confirmed = Column(Boolean, default=False)
    creator_id = Column(Integer, ForeignKey("users.id"))
    comment = Column(String(50), nullable=True)
    
    creator = relationship("User")
    participants = relationship("MeetingParticipant", back_populates="meeting")

    def is_full(self, joined_count: int) -> bool:
        return self.max_participants is not None and joined_count >= self.max_participants

    def capacity_label(self, joined_count: int) -> str:
        """'3/6', or '3/∞' for meetings without a participant limit."""
        limit = "∞" if self.max_participants is None else self.max_participants
        return f"{joined_count}/{limit}"

class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="joined") # 'joined', 'waitlist', 'cancelled'
    joined_at = Column(DateTime, default=now)
    
    meeting = relationship("Meeting", back_populates="participants")
    user = relationship("User")


class MeetingDraft(Base):
    """
    Text input pending in the meeting creation flow (one per user).
    The rest of the draft lives in button payloads; here we keep which input
    is expected and the comment text.
    """
    __tablename__ = "meeting_drafts"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    state = Column(String, nullable=False)  # encoded create_flow state waiting for the input
    comment = Column(String(50), nullable=True)
    awaiting = Column(String(16), nullable=True)  # "time" / "size" / "comment" / None
    updated_at = Column(DateTime, default=now, onupdate=now)
