"""정규화된 이벤트 간 변화 비교 (Stage 3 기반, 계획서 §4.4~4.6).

정정 비교(같은 사건의 원본 vs 정정본)에 사용한다. 계산·비교는 전부 여기서(Python),
표시는 render_changes가, 좋고 나쁨의 판정은 하지 않는다 — 변화의 크기·확인 필요성만.
"""

# 표시 라벨 (normalized_data 키 → 한국어)
LABELS = {
    "amount": "발행금액",
    "bond_type": "사채 종류",
    "coupon_rate": "표면이자율",
    "maturity_rate": "만기이자율",
    "maturity_date": "만기일",
    "conversion_price": "전환가액",
    "conversion_start_date": "전환청구 가능일",
    "operating_funds": "자금목적(운영)",
    "debt_repayment_funds": "자금목적(채무상환)",
    "reset_terms": "리픽싱 조건",
    "increase_method": "증자방식",
    "new_share_count": "신주 발행수",
    "issue_price": "발행가액",
    "allotment_method": "배정방법",
    "record_date": "신주배정기준일",
    "payment_date": "납입일",
}

# 금액·수량·비율·일정·상대방 변경 = 중요 (계획서 §4.6)
MAJOR_FIELDS = {
    "amount", "coupon_rate", "maturity_rate", "maturity_date",
    "conversion_price", "conversion_start_date", "reset_terms",
    "new_share_count", "issue_price", "allotment_method",
    "record_date", "payment_date", "increase_method",
}


def compare_normalized_data(before: dict, after: dict) -> list[dict]:
    """변경된 필드만 반환. 각 항목: field/label/before/after/significance."""
    changes = []
    for key in sorted(set(before) | set(after)):
        old_value = before.get(key)
        new_value = after.get(key)
        if old_value == new_value:
            continue
        changes.append({
            "field": key,
            "label": LABELS.get(key, key),
            "before": old_value,
            "after": new_value,
            "significance": "major" if key in MAJOR_FIELDS else "minor",
        })
    return changes


def render_changes(changes: list[dict], major_only: bool = False) -> str:
    """변화 목록 → 표시 문자열 (평문 — HTML 이스케이프는 notifier가 담당)."""
    shown = [c for c in changes if c["significance"] == "major"] if major_only else changes
    if not shown:
        return "핵심 정형 항목의 변경이 없습니다."
    lines = ["[정정된 핵심 항목]"]
    for item in shown:
        before = item["before"] if item["before"] is not None else "없음"
        after = item["after"] if item["after"] is not None else "없음"
        mark = "❗" if item["significance"] == "major" else "•"
        lines.append(f"{mark} {item['label']}: {before} → {after}")
    return "\n".join(lines)
