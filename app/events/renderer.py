"""저장된 이벤트 JSON → 사람이 읽는 카드 문자열. 계산하지 않고 표시만 한다.

아직 알림 경로에 연결되지 않음 — Stage 2 본계약(설문 후)에서 기존
summarizer.format_typed_disclosure를 대체할 예정. HTML 이스케이프는
notifier.build_disclosure_message가 담당하므로 여기서는 평문만 만든다.
"""
from events.types import EVENT_CONVERTIBLE_BOND, EVENT_PAID_INCREASE


def _add_line(lines: list[str], label: str, value, suffix: str = ""):
    if value not in (None, "", "-"):
        lines.append(f"• {label}: {value}{suffix}")


def render_convertible_bond(event: dict) -> str:
    data = event.get("normalized_data", {})
    metrics = event.get("metrics", {})
    lines = ["[전환사채 발행결정]"]
    _add_line(lines, "발행금액", data.get("amount"), "원")
    _add_line(lines, "종류", data.get("bond_type"))
    _add_line(lines, "전환가액", data.get("conversion_price"), "원")
    _add_line(lines, "표면이자율", data.get("coupon_rate"), "%")
    _add_line(lines, "만기이자율", data.get("maturity_rate"), "%")
    _add_line(lines, "만기일", data.get("maturity_date"))
    _add_line(lines, "전환청구 가능일", data.get("conversion_start_date"))
    _add_line(lines, "리픽싱", data.get("reset_terms"))
    _add_line(lines, "잠재 희석률", metrics.get("dilution_rate"), "%")
    return "\n".join(lines)


def render_paid_increase(event: dict) -> str:
    data = event.get("normalized_data", {})
    metrics = event.get("metrics", {})
    lines = ["[유상증자 결정]"]
    _add_line(lines, "증자방식", data.get("increase_method"))
    _add_line(lines, "신주 발행수", data.get("new_share_count"), "주")
    _add_line(lines, "발행가액", data.get("issue_price"), "원")
    _add_line(lines, "배정방법", data.get("allotment_method"))
    _add_line(lines, "신주배정기준일", data.get("record_date"))
    _add_line(lines, "납입일", data.get("payment_date"))
    _add_line(lines, "잠재 희석률", metrics.get("dilution_rate"), "%")
    return "\n".join(lines)


_RENDERERS = {
    EVENT_CONVERTIBLE_BOND: render_convertible_bond,
    EVENT_PAID_INCREASE: render_paid_increase,
}


def render_event(event: dict) -> str | None:
    """지원 유형이면 카드 문자열, 아니면 None(호출측이 기존 경로 사용)."""
    renderer = _RENDERERS.get(event.get("event_type"))
    return renderer(event) if renderer else None
