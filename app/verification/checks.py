"""결정론적 요약 검증 — LLM 없이 값싸게 큰 오류를 차단 (Stage 7 cascade ①②).

두 축:
  1. 투자의견 금칙 표현 검출 (§7 법적 제약 · 법률 메모 쟁점1)
  2. 정형 필드 교차검증 — 요약의 금액을 한국어 단위 정규화해 정형 응답 값과 대조.
     '4,605주'를 '4,605조'로 잘못 쓴 단위 환각을 결정론적으로 잡는다(§4.2).

설계 원칙: **과도한 거부 금지**(법률 메모의 과잉거부 경고). 투자의견만 hard fail,
미검증 대형 숫자는 advisory warning. 발송을 침묵으로 막지 않는다(계획서 §8.3).
"""
import re
from decimal import Decimal, InvalidOperation

# --- 1. 투자의견 금칙 ---
# 사실 전달을 넘어 매매/평가 의견으로 읽히는 표현만 좁게 잡는다.
# '투자 판단', '투자자', 면책 문구("투자 판단 전 원문 확인")는 금칙이 아니다.
_OPINION_PATTERNS = [
    r"매수\s*(?:추천|의견|권유|타이밍)",
    r"매도\s*(?:추천|의견|권유|타이밍)",
    r"목표\s*주?가",
    r"저평가", r"고평가",
    r"비중\s*(?:확대|축소)",
    r"손절", r"익절",
    r"유망\s*(?:주|종목|기업)",
    r"(?:강력|적극)\s*추천",
    r"추천\s*종목",
]
_OPINION_RE = re.compile("|".join(_OPINION_PATTERNS))


def check_investment_opinion(text: str) -> list[str]:
    """금칙 투자의견 표현 목록(중복 제거). 없으면 빈 리스트."""
    if not text:
        return []
    return sorted({m.group(0) for m in _OPINION_RE.finditer(text)})


# --- 2. 한국어 숫자 정규화 + 정형 교차검증 ---
_UNIT_MULT = {"조": Decimal(10) ** 12, "억": Decimal(10) ** 8, "만": Decimal(10) ** 4}
# 숫자 + (선택)한글 크기단위 + (선택)원/주/%  — 콤마·소수 허용
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만)?\s*(원|주|%)?")

# 교차검증 대상: 이 크기 이상의 '금액/수량'만 본다(날짜·D-day 등 소수치 오탐 방지).
_LARGE_THRESHOLD = Decimal(10) ** 6


def _to_decimal(digits: str) -> Decimal | None:
    try:
        return Decimal(digits.replace(",", ""))
    except InvalidOperation:
        return None


def extract_amounts(text: str) -> set[Decimal]:
    """텍스트의 금액/수량을 정규화한 절대값 집합(%는 제외).

    '150억원' → 1.5e10, '15,000,000,000' → 1.5e10 (동일). '3.5%'는 비율이라 제외.
    """
    out: set[Decimal] = set()
    if not text:
        return out
    for m in _NUM_RE.finditer(text):
        digits, unit, suffix = m.group(1), m.group(2), m.group(3)
        if suffix == "%":
            continue
        base = _to_decimal(digits)
        if base is None:
            continue
        if unit:
            base *= _UNIT_MULT[unit]
        out.add(base)
    return out


def extract_ratios(text: str) -> set[Decimal]:
    """텍스트의 비율(%) 값 집합."""
    out: set[Decimal] = set()
    if not text:
        return out
    for m in _NUM_RE.finditer(text):
        if m.group(3) == "%":
            base = _to_decimal(m.group(1))
            if base is not None:
                out.add(base)
    return out


def _typed_text(typed_data: dict) -> str:
    return " ".join(str(v) for v in typed_data.values() if v not in (None, "", "-"))


def cross_check_amounts(summary: str, typed_data: dict) -> dict:
    """요약 금액을 정형 값과 대조. grounded/unverified_large 반환.

    unverified_large = 정형 응답에서 근거를 찾지 못한 대형 금액(단위 환각 후보).
    """
    typed_amounts = extract_amounts(_typed_text(typed_data))
    summary_amounts = extract_amounts(summary)
    grounded = summary_amounts & typed_amounts
    unverified = summary_amounts - typed_amounts
    unverified_large = sorted(x for x in unverified if x >= _LARGE_THRESHOLD)
    return {
        "grounded": sorted(grounded),
        "unverified_large": unverified_large,
    }


def verify_summary(summary: str, typed_data: dict | None = None) -> dict:
    """요약 검증 판정 (advisory).

    verdict:
      - "fail": 투자의견 금칙 표현 존재 (hard rule, §7)
      - "warning": 정형 근거 없는 대형 금액 존재 (단위/숫자 환각 의심)
      - "pass": 위 없음, 교차검증 수행됨
      - "unavailable": 정형 데이터 없음(비정형 경로) — 투자의견만 검사, 숫자 교차검증 불가
    checks: 세부 근거. 발송 정책(카드만/링크만 등)은 호출측이 verdict로 결정한다.
    """
    opinions = check_investment_opinion(summary)
    checks = {"investment_opinion": opinions}

    if opinions:
        return {"verdict": "fail", "checks": checks}

    if not typed_data:
        checks["cross_check"] = "unavailable"
        return {"verdict": "unavailable", "checks": checks}

    cross = cross_check_amounts(summary, typed_data)
    checks["cross_check"] = cross
    if cross["unverified_large"]:
        return {"verdict": "warning", "checks": checks}
    return {"verdict": "pass", "checks": checks}
