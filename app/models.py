import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Enum, UniqueConstraint
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
    today_keywords = Column(String, nullable=True)
    mytoday_keywords = Column(String, nullable=True)
    sync_keywords = Column(Boolean, default=False)
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
    # 발송 중복 기준은 사용자별(receipt_no, chat_id)이다. receipt_no 단독 unique는
    # 두 번째 사용자 삽입에서 UniqueViolation → 세션 롤백 → 매 폴링 재발송을 일으킨다.
    __table_args__ = (
        UniqueConstraint("receipt_no", "chat_id", name="uq_seen_disclosure_receipt_chat"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    receipt_no = Column(String, nullable=False, index=True)
    chat_id = Column(String, ForeignKey("users.chat_id"), nullable=False)
    corp_name = Column(String, nullable=False)
    report_nm = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    user = relationship("User", back_populates="seen_disclosures")


class Disclosure(Base):
    __tablename__ = "disclosures"

    id = Column(String, primary_key=True, default=gen_uuid)
    rcept_no = Column(String, unique=True, nullable=False, index=True)
    corp_code = Column(String, nullable=False)
    corp_name = Column(String, nullable=False)
    stock_code = Column(String, nullable=True)
    corp_cls = Column(String, nullable=True)
    report_nm = Column(String, nullable=False)
    rcept_dt = Column(String, nullable=False, index=True)
    flr_nm = Column(String, nullable=True)
    is_important = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)
