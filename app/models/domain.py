from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database.session import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    max_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    activity_type = Column(String, nullable=False) # e.g. Волейбол, Футбол
    location = Column(String, nullable=False)
    date_time = Column(DateTime, nullable=False)
    skill_level = Column(String, default="Любительский")
    min_quorum = Column(Integer, default=4)
    max_participants = Column(Integer, default=6)
    is_confirmed = Column(Boolean, default=False)
    creator_id = Column(Integer, ForeignKey("users.id"))
    
    creator = relationship("User")
    participants = relationship("MeetingParticipant", back_populates="meeting")

class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="joined") # 'joined', 'waitlist', 'cancelled'
    joined_at = Column(DateTime, default=datetime.now)
    
    meeting = relationship("Meeting", back_populates="participants")
    user = relationship("User")
