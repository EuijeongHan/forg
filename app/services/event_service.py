"""공시 → 이벤트 정규화 저장 (Stage 2 기반). 텔레그램 비의존.

원본은 Disclosure.raw_typed_data(P1-2)에 있으므로, 여기서는 정규화 산출물만
DisclosureEvent에 기록한다. 동일 공시 재처리에 멱등(disclosure_id unique + 선조회).
"""
import uuid
from sqlalchemy import select

from models import DisclosureEvent


async def record_typed_event(session, disclosure_row, report_nm: str) -> bool:
    """정형 스냅샷이 있는 Disclosure를 공통 이벤트로 정규화해 저장.

    저장했으면 True, 이미 있거나 스냅샷이 없으면 False. 커밋은 호출측이 한다.
    """
    from events.normalizer import normalize_typed_disclosure

    if disclosure_row is None or not disclosure_row.raw_typed_data:
        return False

    existing = await session.execute(
        select(DisclosureEvent.id).where(
            DisclosureEvent.disclosure_id == disclosure_row.id
        )
    )
    if existing.scalar_one_or_none():
        return False

    norm = normalize_typed_disclosure(report_nm, disclosure_row.raw_typed_data)
    session.add(DisclosureEvent(
        id=str(uuid.uuid4()),
        disclosure_id=disclosure_row.id,
        corp_code=disclosure_row.corp_code,
        event_type=norm["event_type"],
        occurred_on=disclosure_row.rcept_dt,
        normalized_data=norm["normalized_data"],
        metrics={},  # 희석률 등은 분모(발행주식총수) 연동 후 계산 — 0으로 꾸미지 않음
    ))
    return True
