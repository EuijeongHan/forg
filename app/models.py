import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base
import enum

class TierEnum(str, enum.Enum):
    free = "free"
    pro = "pro"
    enterprise = "enterprise"

def now_utc():
    return datetime.now(timezone.utc)

def gen_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    chat_id = Column(String, primary_key=True)
    first_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    tier = Column(Enum(TierEnum), default=TierEnum.free)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    watchlist = relationship("Watchlist", back_populates="user")
    seen_disclosures = relationship("SeenDisclosure", back_populates="user")


class Watchlist(Base):
    __tablename__ = "watchlist"

    id = Column(String, primary_key=True, default=gen_uuid)
    chat_id = Column(String, ForeignKey("users.chat_id"), nullable=False)
    corp_code = Column(String, nullable=False)
    corp_name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    user = relationship("User", back_populates="watchlist")


class SeenDisclosure(Base):
    __tablename__ = "seen_disclosures"

    id = Column(String, primary_key=True, default=gen_uuid)
    receipt_no = Column(String, unique=True, nullable=False)
    chat_id = Column(String, ForeignKey("users.chat_id"), nullable=False)
    corp_name = Column(String, nullable=False)
    report_nm = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    user = relationship("User", back_populates="seen_disclosures")
