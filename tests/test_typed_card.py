"""정형 카드 결함 수정 검증 — 2026-08-18 평가(judge)가 찾은 4가지 결함의 회귀 방지.

  1. CB D-day: 'D+'(경과) 오표기 → 미래는 'D-n', 당일 'D-Day', 과거는 생략
  2. 유상증자: 증자방식 실제 키(ic_mthn) 미조회 → 카드 공백 → '미제공' 서술 유발
  3. 자기주식 처분: dppln_* 필드 전체 누락 (씨젠 사례)
  4. 합병: mgptncmp_cmpnm/mgsc_mgdt 미조회 + rs_sm_atn 주총 오표기(홀드아웃에서 확인)
"""
import datetime
import os
import pathlib
import sys

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
sys.path.insert(0, APP)

from summarizer import format_typed_disclosure  # noqa: E402

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


TODAY = datetime.date(2026, 8, 18)

# 1. CB D-day 표기
cb_future = format_typed_disclosure("가나전자", "전환사채권발행결정", {
    "bd_fta": "5,000,000,000", "cv_prc": "3,250",
    "cvrqpd_bgd": "2027년 08월 21일",
}, today=TODAY)
check("미래 전환청구는 D-368 (남은 일수)", "D-368" in cb_future)
check("'D+' 오표기 제거", "D+" not in cb_future)

cb_today = format_typed_disclosure("가나전자", "전환사채권발행결정", {
    "cvrqpd_bgd": "2026년 08월 18일",
}, today=TODAY)
check("당일은 D-Day 표기", "D-Day" in cb_today)

cb_past = format_typed_disclosure("가나전자", "전환사채권발행결정", {
    "cvrqpd_bgd": "2026년 01월 10일",
}, today=TODAY)
check("지난 날짜는 카운트다운 생략(날짜만 표기)", "D-" not in cb_past and "2026년 01월 10일" in cb_past)

# 2. 유상증자 증자방식 (실제 키 ic_mthn — 모아데이타 사례)
pi = format_typed_disclosure("모아데이타", "유상증자결정", {
    "nstk_ostk_cnt": "3,169,572", "fv_ps": "500",
    "bfic_tisstk_ostk": "36,381,379", "fdpp_op": "1,999,999,932",
    "ic_mthn": "제3자배정증자",
}, today=TODAY)
check("증자방식(ic_mthn) 표기", "제3자배정증자" in pi)
check("자금목적은 '배정액'으로 라벨링", "자금목적(운영)" in pi)
check("증자 전 발행주식수 표기", "36,381,379" in pi)

# 3. 자기주식 처분 필드 (씨젠 사례)
dp = format_typed_disclosure("씨젠", "주요사항보고서(자기주식처분결정)", {
    "dppln_stk_ostk": "183", "dpstk_prc_ostk": "32,550",
    "dppln_prc_ostk": "5,956,650",
    "dp_pp": "양도제한조건부주식(RSU) 부여에 따른 자기주식의 교부",
    "dpprpd_bgd": "2026년 08월 13일", "dpprpd_edd": "2026년 09월 11일",
}, today=TODAY)
check("처분 주식수 표기", "183" in dp)
check("처분단가 표기", "32,550" in dp)
check("처분예정금액 표기", "5,956,650" in dp)
check("처분목적(RSU) 표기", "RSU" in dp)
check("취득 라벨 미사용", "취득" not in dp)

# 4. 합병 실제 키 (한국카본 사례)
mg = format_typed_disclosure("한국카본", "주요사항보고서(회사합병결정)", {
    "mgptncmp_cmpnm": "(주)한국글로벌솔루션",
    "mgptncmp_rl_cmpn": "자회사",
    "nmgcmp_rlst_atn": "해당사항없음",
    "mg_rt": "(주)한국카본 : (주)한국글로벌솔루션\n= 1.0000000 : 0.00",
    "mgsc_mgdt": "2026년 11월 02일",
    "mg_stn": "소규모합병",
    "mgsc_gmtsck_prd": "-",          # 소규모합병이라 주총 없음
    "rs_sm_atn": "예",               # 주총과 무관한 필드 (아래 회귀 방지)
}, today=TODAY)
check("합병상대(mgptncmp_cmpnm) 표기", "한국글로벌솔루션" in mg)
check("관계는 mgptncmp_rl_cmpn(자회사) — r2 오표기 회귀 방지", "상대법인과의 관계: 자회사" in mg)
check("다른 의미의 필드(해당사항없음)를 관계로 쓰지 않음", "해당사항없음" not in mg)
check("합병기일(mgsc_mgdt) 표기", "2026년 11월 02일" in mg)
# 2026-08-26 홀드아웃 평가: rs_sm_atn을 '주주총회 소집 여부'로 쓰던 것은 오표기였다.
# 실측상 rs_sm_atn='예'인데 주총이 없는 소규모합병(SK이노베이션)과 '아니오'인데 주총이
# 있는 건(SK아이이테크놀로지)이 공존한다 — 상관관계 자체가 없다.
check("rs_sm_atn을 주총 소집 여부로 쓰지 않음", "주주총회 소집 여부" not in mg)
check("뜻 모를 필드값을 그대로 노출하지 않음", "rs_sm_atn" not in mg)
check("합병 형태(mg_stn) 표기 — 주총이 없는 이유가 드러남", "합병 형태: 소규모합병" in mg)
check("주총 예정일이 '-'면 표기하지 않음", "주주총회 예정일" not in mg)

# 주총이 실제로 있는 합병은 날짜로 표기된다
mg2 = format_typed_disclosure("에어부산", "주요사항보고서(회사합병결정)", {
    "mgptncmp_cmpnm": "(주)진에어",
    "mgsc_gmtsck_prd": "2026년 12월 07일",
    "mg_stn": "해당사항없음",
    "rs_sm_atn": "아니오",
}, today=TODAY)
check("주총 예정일(mgsc_gmtsck_prd) 표기", "주주총회 예정일: 2026년 12월 07일" in mg2)
check("합병 형태 '해당사항없음'은 표기 생략", "합병 형태" not in mg2)
check("합병비율 개행 정리", "\n= 1" not in mg)

# ── 💡 코멘트 숫자 가드 (2026-08-26 홀드아웃) ────────────────────────
# 카드 수치는 정형 API에서 오므로 늘 옳다. 위험한 건 그것을 다시 말하는 코멘트로,
# 홀드아웃에서 300억을 30조로 옮겨 적은 1000배 오기가 실제로 나왔다. LLM 출력은
# 확률적이라 "이번엔 안 틀렸다"로는 검증이 안 되므로, 코멘트를 주입해 결정론적으로
# 확인한다.
import asyncio  # noqa: E402
import summarizer  # noqa: E402

CB = {"bd_fta": "62,500,000,000", "fdpp_op": "30,000,000,000",
      "cv_prc": "33,255", "bd_mtd": "2056년 09월 04일"}


async def _summary_with_comment(comment):
    async def fake(prompt):
        return comment
    orig = summarizer._generate_with_fallback
    summarizer._generate_with_fallback = fake
    try:
        return await summarizer.summarize_typed_disclosure(
            "서진시스템", "주요사항보고서(전환사채권발행결정)", CB)
    finally:
        summarizer._generate_with_fallback = orig


bad = asyncio.run(_summary_with_comment("자금목적은 30,000,000,000,000원입니다."))
check("근거 없는 대형 금액이 든 코멘트는 폐기", "30,000,000,000,000" not in bad)
check("코멘트를 버려도 카드는 그대로 발송", "62,500,000,000원" in bad)
check("폐기 시 💡 블록 자체가 없음", "💡" not in bad)

good = asyncio.run(_summary_with_comment("자금목적(운영)은 30,000,000,000원입니다."))
check("정형 근거가 있는 코멘트는 유지", "💡" in good and "30,000,000,000원" in good)

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
