import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Enum, UniqueConstraint, JSON, Integer
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
    # 정형 API 응답 원본 스냅샷 (P1-2) — 정정 비교·검증 레이어·golden set의 미래 재료.
    # 문서 전문이 아니라 수치 필드 dict만 저장한다(저작권·용량 고려).
    raw_typed_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)


class DisclosureEvent(Base):
    """공시가 뜻하는 '사건'의 공통 구조 (Stage 2 기반, 계획서 §3.3).

    Disclosure(제출된 문서)와 분리해 정정 비교·타임라인·검색의 토대가 된다.
    신규 테이블이므로 create_all이 생성한다(기존 테이블 ALTER 아님 — 마이그레이션 불필요).
    """
    __tablename__ = "disclosure_events"
    __table_args__ = (
        UniqueConstraint("disclosure_id", name="uq_disclosure_event_disclosure"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    disclosure_id = Column(String, ForeignKey("disclosures.id"), nullable=False, index=True)
    corp_code = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    occurred_on = Column(String, nullable=True)
    # 정규화 산출물 — 원본(raw)은 Disclosure.raw_typed_data에 있음
    normalized_data = Column(JSON, nullable=False, default=dict)
    metrics = Column(JSON, nullable=False, default=dict)
    normalization_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
