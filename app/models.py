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

    # 중복 등록 방지는 watchlist_service의 조회에만 의존하고 있었다. 조회~삽입
    # 사이에 같은 사용자의 요청이 겹치면 중복 행이 생기고, 그 기업 공시는
    # 알림이 2번 나간다. DB 차원에서 막는다.
    __table_args__ = (
        UniqueConstraint("chat_id", "corp_code", name="uq_watchlist_chat_corp"),
    )

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


class TopicSubscription(Base):
    """시장 전체 유형 구독 — 워치리스트와 독립적으로 '어떤 유형'을 받을지.

    신규 테이블이므로 create_all이 생성한다(disclosure_events·feedback과 동일 관례).
    """
    __tablename__ = "topic_subscriptions"
    __table_args__ = (
        UniqueConstraint("chat_id", "topic", name="uq_topic_sub_chat_topic"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    chat_id = Column(String, ForeignKey("users.chat_id"), nullable=False, index=True)
    topic = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)


class Feedback(Base):
    """사용자 피드백 (/feedback) — 목적함수(놓침 0)의 실측 데이터 수집 경로.

    운영자 텔레그램 전달과 별개로 DB에 남긴다 — 전달이 실패해도 접수는
    유실되지 않는다. 신규 테이블이므로 create_all이 생성한다
    (DisclosureEvent와 동일 — 기존 테이블 ALTER가 아니라 마이그레이션 불필요).
    """
    __tablename__ = "feedback"

    id = Column(String, primary_key=True, default=gen_uuid)
    chat_id = Column(String, ForeignKey("users.chat_id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)


class DisclosureRelation(Base):
    """공시 간 연결 (Stage 3 기반, 계획서 §4.2). 우선 정정본→원본(correction_of).

    from_disclosure_id = 정정본, to_disclosure_id = 원본. confidence는 연결 방법
    ("rule"=규칙 기반 추정) — 원문 접수번호로 확정하기 전까지 알림에는 '추정 연결' 표기.
    """
    __tablename__ = "disclosure_relations"
    __table_args__ = (
        UniqueConstraint("from_disclosure_id", "to_disclosure_id", "relation_type",
                         name="uq_disclosure_relation"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    from_disclosure_id = Column(String, ForeignKey("disclosures.id"), nullable=False, index=True)
    to_disclosure_id = Column(String, ForeignKey("disclosures.id"), nullable=False, index=True)
    relation_type = Column(String, nullable=False)  # correction_of
    confidence = Column(String, nullable=False, default="rule")
    created_at = Column(DateTime(timezone=True), default=now_utc)
