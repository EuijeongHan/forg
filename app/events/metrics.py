"""이벤트 파생 지표 계산 — 전부 Python(Decimal). LLM이 숫자를 만들지 않는다."""
from decimal import Decimal


def calculate_dilution_rate(
    new_share_count: Decimal | None,
    existing_share_count: Decimal | None,
) -> Decimal | None:
    """잠재 희석률(%) = 신주 / (기존+신주) × 100.

    분모가 없으면 None을 반환한다 — 0으로 꾸미지 않는다(0은 '희석 없음'이라는
    다른 의미다). 분모(발행주식총수)는 stockTotqySttus 연동(가이드 §3.6) 후 공급.
    """
    if new_share_count is None:
        return None
    if existing_share_count is None or existing_share_count <= 0:
        return None
    total_after = existing_share_count + new_share_count
    return (new_share_count / total_after * Decimal("100")).quantize(Decimal("0.01"))
