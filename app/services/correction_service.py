"""정정공시 감지·원본 연결·변화 비교 (Stage 3 기반, 계획서 §4.3).

원칙: 제목만으로 원본을 확정하지 않는다 — 규칙 기반 연결은 confidence="rule"로
저장하고, 표시 시 '추정 연결'을 명시한다. 파이프라인 연결(알림 포맷 변경)은
Stage 3 본계약에서 — 여기서는 순수 로직과 저장만 제공한다.
"""
import re
import uuid

from sqlalchemy import select

from models import Disclosure, DisclosureRelation

# "[기재정정]", "[첨부정정]", "[정정명령부과]" 등 대괄호 접두어가 1개 이상
_PREFIX_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")


def strip_title_prefixes(report_nm: str) -> str:
    """대괄호 접두어를 제거한 기본 제목. '[기재정정]유상증자결정' → '유상증자결정'"""
    return _PREFIX_RE.sub("", report_nm or "").strip()


def is_correction(report_nm: str) -> bool:
    """대괄호 접두어 안에 '정정'이 있는 공시만 정정으로 판정.

    제목 본문에 '정정'이 들어가는 다른 공시(예: '정정명령 관련')를 오탐하지 않도록
    접두어 기준으로만 본다.
    """
    m = _PREFIX_RE.match(report_nm or "")
    return bool(m and "정정" in m.group(0))


async def find_original(session, correction: Disclosure) -> Disclosure | None:
    """정정본의 원본 후보를 규칙으로 추정 (계획서 §4.3 후보 조건).

    같은 corp_code + 접두어 제거 제목 동일 + 정정본보다 먼저 접수 → 가장 최근 것.
    확신이 없으므로 호출측은 confidence="rule"로 다뤄야 한다.
    """
    base_title = strip_title_prefixes(correction.report_nm)
    if not base_title:
        return None
    result = await session.execute(
        select(Disclosure).where(
            Disclosure.corp_code == correction.corp_code,
            Disclosure.rcept_no != correction.rcept_no,
            Disclosure.rcept_dt <= correction.rcept_dt,
        ).order_by(Disclosure.rcept_dt.desc(), Disclosure.rcept_no.desc())
    )
    for cand in result.scalars().all():
        if cand.rcept_no >= correction.rcept_no:
            continue  # 접수번호는 시간순 증가 — 정정본 이후 접수는 원본이 될 수 없다
        if strip_title_prefixes(cand.report_nm) == base_title:
            return cand
    return None


async def link_correction(session, correction: Disclosure) -> dict | None:
    """정정본을 원본과 연결(멱등)하고, 양쪽에 정형 스냅샷이 있으면 변화를 계산.

    반환: {"original": Disclosure, "changes": list|None, "confidence": "rule"} 또는
    원본을 못 찾으면 None. 커밋은 호출측이 한다.
    """
    from events.comparator import compare_normalized_data
    from events.normalizer import normalize_typed_disclosure

    original = await find_original(session, correction)
    if original is None:
        return None

    existing = await session.execute(
        select(DisclosureRelation.id).where(
            DisclosureRelation.from_disclosure_id == correction.id,
            DisclosureRelation.to_disclosure_id == original.id,
            DisclosureRelation.relation_type == "correction_of",
        )
    )
    if not existing.scalar_one_or_none():
        session.add(DisclosureRelation(
            id=str(uuid.uuid4()),
            from_disclosure_id=correction.id,
            to_disclosure_id=original.id,
            relation_type="correction_of",
            confidence="rule",
        ))

    changes = None
    if correction.raw_typed_data and original.raw_typed_data:
        base_title = strip_title_prefixes(correction.report_nm)
        before = normalize_typed_disclosure(base_title, original.raw_typed_data)["normalized_data"]
        after = normalize_typed_disclosure(base_title, correction.raw_typed_data)["normalized_data"]
        changes = compare_normalized_data(before, after)

    return {"original": original, "changes": changes, "confidence": "rule"}
