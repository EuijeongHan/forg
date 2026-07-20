"""DART 정형 응답 → 공통 이벤트 정규화.

- 표시용 원문은 raw_typed_data에 이미 보존되므로(P1-2) 여기서는 계산 가능한
  형태로만 바꾼다.
- 금융 숫자는 float 오차를 피하기 위해 Decimal로 파싱하고 JSON에는 문자열로 저장.
- 필드 키는 프로덕션에서 검증된 summarizer.format_typed_disclosure의 키를 따른다.
"""
from decimal import Decimal, InvalidOperation

from events.types import EVENT_CONVERTIBLE_BOND, EVENT_PAID_INCREASE, EVENT_OTHER

EMPTY_VALUES = (None, "", "-")


def clean_text(value) -> str | None:
    if value in EMPTY_VALUES:
        return None
    text = str(value).strip()
    return text or None


def parse_decimal(value) -> Decimal | None:
    """'4,605주' → Decimal('4605'). 파싱 불가·빈 값은 None (0으로 꾸미지 않는다)."""
    text = clean_text(value)
    if text is None:
        return None
    cleaned = (
        text.replace(",", "")
        .replace("원", "")
        .replace("주", "")
        .replace("%", "")
        .strip()
    )
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def decimal_to_json(value: Decimal | None) -> str | None:
    # JSON float는 금융 숫자에서 오차가 날 수 있으므로 문자열로 저장한다.
    return str(value) if value is not None else None


def normalize_convertible_bond(data: dict) -> dict:
    return {
        "event_type": EVENT_CONVERTIBLE_BOND,
        "normalized_data": {
            "amount": decimal_to_json(parse_decimal(data.get("bd_fta"))),
            "bond_type": clean_text(data.get("bd_knd")),
            "coupon_rate": decimal_to_json(parse_decimal(data.get("bd_intr_ex"))),
            "maturity_rate": decimal_to_json(parse_decimal(data.get("bd_intr_sf"))),
            "maturity_date": clean_text(data.get("bd_mtd")),
            "conversion_price": decimal_to_json(parse_decimal(data.get("cv_prc"))),
            "conversion_start_date": clean_text(data.get("cvrqpd_bgd")),
            "operating_funds": decimal_to_json(parse_decimal(data.get("fdpp_op"))),
            "debt_repayment_funds": decimal_to_json(parse_decimal(data.get("fdpp_dtrp"))),
            "reset_terms": clean_text(data.get("act_mktprcfl_cvprc_lwtrsprc_bs")),
        },
    }


def normalize_paid_increase(data: dict) -> dict:
    return {
        "event_type": EVENT_PAID_INCREASE,
        "normalized_data": {
            "increase_method": clean_text(data.get("iscls")),
            "new_share_count": decimal_to_json(parse_decimal(data.get("nstk_ostk_cnt"))),
            "issue_price": decimal_to_json(parse_decimal(data.get("nstk_ispr"))),
            "operating_funds": decimal_to_json(parse_decimal(data.get("fdpp_op"))),
            "allotment_method": clean_text(data.get("allot_mthn")),
            "record_date": clean_text(data.get("nstk_sdtpd_bgd")),
            "payment_date": clean_text(data.get("pymd")),
        },
    }


def normalize_typed_disclosure(report_nm: str, data: dict) -> dict:
    """report_nm으로 유형을 골라 정규화. 미지원 유형은 other(빈 정규화)."""
    if "전환사채" in report_nm:
        return normalize_convertible_bond(data)
    if "유상증자" in report_nm:
        return normalize_paid_increase(data)
    return {"event_type": EVENT_OTHER, "normalized_data": {}}
