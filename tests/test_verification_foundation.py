"""Stage 7 결정론 검증 계층 검증 — 투자의견 금칙 + 정형 교차검증(단위 환각 검출).

외부 의존 없음(순수 함수). 핵심: '4,605주'를 '4,605조'로 쓴 단위 환각을 잡는지,
정상 요약을 과도하게 거부하지 않는지.
"""
import pathlib
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))

from verification.checks import (  # noqa: E402
    check_investment_opinion, extract_amounts, extract_ratios,
    cross_check_amounts, verify_summary,
)

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


# --- 투자의견 금칙 ---
check("금칙: 매수 추천 검출", check_investment_opinion("이 종목 매수 추천합니다") == ["매수 추천"])
check("금칙: 목표주가 검출", "목표주가" in "".join(check_investment_opinion("목표주가 5만원")))
check("금칙: 비중 확대 검출", check_investment_opinion("비중 확대 의견") == ["비중 확대"])
check("허용: 면책 문구는 금칙 아님",
      check_investment_opinion("투자 판단 전 원문을 확인하세요. 투자자 유의사항 참고.") == [])
check("허용: 사실 요약은 금칙 아님",
      check_investment_opinion("전환사채 150억원 발행. 전환가액 3,250원.") == [])

# --- 한국어 숫자 정규화 ---
check("정규화: 150억원 == 1.5e10", Decimal("15000000000") in extract_amounts("발행금액 150억원"))
check("정규화: 콤마 금액", Decimal("15000000000") in extract_amounts("15,000,000,000원"))
check("정규화: 150억 == 15,000,000,000 매칭",
      extract_amounts("150억원") & extract_amounts("15,000,000,000") == {Decimal("15000000000")})
check("정규화: 4,605주 == 4605", extract_amounts("4,605주") == {Decimal("4605")})
check("정규화: %는 금액서 제외", extract_amounts("표면이자율 3.5%") == set())
check("비율 추출: 3.5%", extract_ratios("표면이자율 3.5%") == {Decimal("3.5")})

# --- 교차검증: 단위 환각 검출 (§4.2 핵심) ---
typed = {"bd_fta": "15,000,000,000", "cv_prc": "3,250", "nstk_ostk_cnt": "4,605"}
good = cross_check_amounts("발행금액 150억원, 전환가액 3,250원, 신주 4,605주", typed)
check("교차검증 정상: 미검증 대형 금액 없음", good["unverified_large"] == []
      and Decimal("15000000000") in good["grounded"])

# '4,605주'를 '4,605조'로 환각 → 4605e12는 정형에 없음 → 대형 미검증으로 검출
bad = cross_check_amounts("신주 4,605조 발행", typed)
check("교차검증 환각: 4,605조 미검증 대형 검출",
      Decimal("4605000000000000") in bad["unverified_large"])

# 정형에 없는 대형 금액(엉뚱한 500억) → 검출
bad2 = cross_check_amounts("발행금액 500억원", typed)
check("교차검증: 근거 없는 500억 검출", Decimal("50000000000") in bad2["unverified_large"])

# --- verify_summary 판정 ---
check("verdict fail: 투자의견",
      verify_summary("매수 추천. 발행금액 150억원", typed)["verdict"] == "fail")
check("verdict warning: 단위 환각",
      verify_summary("신주 4,605조 발행", typed)["verdict"] == "warning")
check("verdict pass: 정상 정형",
      verify_summary("발행금액 150억원, 전환가액 3,250원", typed)["verdict"] == "pass")
check("verdict unavailable: 정형 데이터 없음",
      verify_summary("발행금액 150억원", None)["verdict"] == "unavailable")
check("verdict fail 우선: 투자의견은 정형 없어도 fail",
      verify_summary("매수 추천합니다", None)["verdict"] == "fail")
# 과잉거부 방지: 날짜·소액은 warning 아님
check("과잉거부 방지: 날짜(20260801)·D-day는 warning 아님",
      verify_summary("전환청구 가능일 2026년 08월 01일, D+30일. 발행금액 150억원", typed)["verdict"] == "pass")

passed = sum(1 for _, ok in results if ok)
print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
sys.exit(0 if passed == len(results) else 1)
