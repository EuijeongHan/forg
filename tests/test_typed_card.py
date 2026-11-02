"""정형 카드 결함 수정 검증 — 2026-08-18 평가(judge)가 찾은 4가지 결함의 회귀 방지.

  1. CB D-day: 'D+'(경과) 오표기 → 미래는 'D-n', 당일 'D-Day', 과거는 생략
  2. 유상증자: 증자방식 실제 키(ic_mthn) 미조회 → 카드 공백 → '미제공' 서술 유발
  3. 자기주식 처분: dppln_* 필드 전체 누락 (씨젠 사례)
  4. 합병: mgptncmp_cmpnm/mgsc_mgdt 미조회 + rs_sm_atn 주총 오표기(홀드아웃에서 확인)

2026-11-02 새 홀드아웃(35건)이 찾은 것:
  5. 교환사채가 '전환사채 발행결정'으로 표기 — 같은 분기를 쓰고 있었다.
     EB에는 cv_prc·cvrqpd_bgd가 없어 교환가액·교환기간·교환대상이 통째로 빠졌다.
  6. 신주인수권·무상증자는 분기 자체가 없어 '• 이사회결의일' 한 줄만 나갔다.
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


# 이 가드는 2026-08-18 홀드아웃의 1000배 오기(300억 → 30조)에서 나왔다.
# 정형 경로는 2026-11-02부터 💡를 아예 달지 않으므로(아래 참조), 가드 자체를
# 직접 호출해 검증한다 — 남아 있는 사용처는 비정형(공급계약) 경로다.
CARD = "[전환사채 발행결정]\n• 발행금액: 62,500,000,000원"


async def _guarded(comment):
    async def fake(prompt):
        return comment
    orig = summarizer._generate_with_fallback
    summarizer._generate_with_fallback = fake
    try:
        return await summarizer._append_ai_comment(CARD, "p", CB)
    finally:
        summarizer._generate_with_fallback = orig


bad = asyncio.run(_guarded("자금목적은 30,000,000,000,000원입니다."))
check("근거 없는 대형 금액이 든 코멘트는 폐기", "30,000,000,000,000" not in bad)
check("코멘트를 버려도 카드는 그대로 발송", "62,500,000,000원" in bad)
check("폐기 시 💡 블록 자체가 없음", "💡" not in bad)

good = asyncio.run(_guarded("자금목적(운영)은 30,000,000,000원입니다."))
check("정형 근거가 있는 코멘트는 유지", "💡" in good and "30,000,000,000원" in good)



# ── 빈 값('-')인 필드는 줄을 만들지 않는다 (2026-11-02 홀드아웃) ──────
cb_refix = format_typed_disclosure("광진실업", "주요사항보고서(전환사채권발행결정)", {
    'bd_fta': '10,000,000,000', 'cv_prc': '3,580',
    'act_mktprcfl_cvprc_lwtrsprc_bs': '-',
}, today=TODAY)
check("리픽싱이 '-'면 줄 자체가 없음", "리픽싱" not in cb_refix)
check("값이 있으면 리픽싱을 표시", "리픽싱: 시가하락 시 조정" in format_typed_disclosure(
    "x", "주요사항보고서(전환사채권발행결정)",
    {'bd_fta': '1', 'act_mktprcfl_cvprc_lwtrsprc_bs': '시가하락 시 조정'}, today=TODAY))

# ── D-day는 코드가 센다 — 2026-11-02 judge가 D-378이라 주장했으나 379일이 맞다
import datetime as _dt
check("D-day 계산이 달력과 일치",
      f"D-{(_dt.date(2027,11,16) - _dt.date(2026,11,2)).days}" in format_typed_disclosure(
          "광진실업", "주요사항보고서(전환사채권발행결정)",
          {'bd_fta': '1', 'cvrqpd_bgd': '2027년 11월 16일'},
          today=_dt.date(2026, 11, 2)))


# ── 5. 교환사채는 전환사채와 다른 상품이다 (2026-11-02 홀드아웃) ──────
# CB는 신주로 전환돼 희석되고, EB는 이미 있는 주식과 교환된다. HD현대 5,887억은
# 자회사(HD현대마린솔루션) 주식과 교환하는 사채인데 '전환사채'로 나갔다.
# 필드명은 실제 exbdIsDecsn 응답 2건으로 검증했다.
eb = format_typed_disclosure("HD현대", "주요사항보고서(교환사채권발행결정)", {
    'bd_fta': '588,667,443,000', 'bd_knd': '무기명식 무보증 사모 교환사채',
    'bd_intr_ex': '0.0', 'bd_intr_sf': '0.0', 'bd_mtd': '2031년 11월 26일',
    'ex_prc': '247,000', 'ex_rt': '100',
    'extg': '에이치디현대마린솔루션 주식회사 기명식 보통주', 'extg_stkcnt': '2,383,269',
    'exrqpd_bgd': '2026년 11월 27일', 'exrqpd_edd': '2031년 10월 26일',
    'fdpp_dtrp': '588,667,443,000',
}, today=TODAY)
check("EB: 전환사채로 표기하지 않음", "전환사채" not in eb)
check("EB: 교환사채로 표기", "[교환사채 발행결정]" in eb)
check("EB: 교환대상을 밝힘(무엇과 교환되는가)",
      "에이치디현대마린솔루션" in eb and "교환대상" in eb)
check("EB: 교환가액", "교환가액: 247,000원" in eb)
check("EB: 교환청구기간", "2026년 11월 27일 ~ 2031년 10월 26일" in eb)
check("EB: 전환가액이라는 말을 쓰지 않음", "전환가액" not in eb)

# ── 6. 신주인수권부사채 — 분기가 없어 한 줄만 나가던 것 ──────────────
# 2026-10-29 다스코 60억·희석률 8.97%가 '• 이사회결의일' 한 줄로 발송됐다.
bw = format_typed_disclosure("다스코", "주요사항보고서(신주인수권부사채권발행결정)", {
    'bddd': '2026년 10월 29일', 'bd_fta': '6,000,000,000',
    'bd_knd': '무기명식 무보증 사모 신주인수권부사채',
    'bd_intr_ex': '0.0', 'bd_intr_sf': '0.0', 'bd_mtd': '2031년 11월 06일',
    'ex_prc': '3,006', 'ex_rt': '100', 'bdwt_div_atn': '비분리',
    'nstk_pym_mth': '대용납입', 'nstk_isstk_cnt': '1,996,007',
    'nstk_isstk_tisstk_vs': '8.97', 'act_mktprcfl_cvprc_lwtrsprc': '2,706',
    'expd_bgd': '2027년 11월 06일', 'expd_edd': '2031년 10월 06일',
    'fdpp_dtrp': '6,000,000,000',
}, today=TODAY)
check("BW: 공통 폴백이 아니라 전용 카드", "[신주인수권부사채 발행결정]" in bw)
check("BW: 발행금액", "6,000,000,000원" in bw)
check("BW: 행사가액", "행사가액: 3,006원" in bw)
check("BW: 희석률을 발행주식수 옆에 붙임",
      "1,996,007주 (발행주식총수 대비 8.97%)" in bw)
check("BW: 분리형 여부", "분리형 여부: 비분리" in bw)
check("BW: 신주대금 납입방법", "대용납입" in bw)
check("BW: 리픽싱 최저 행사가액", "2,706원" in bw)
check("BW: 한 줄짜리가 아님", len([l for l in bw.splitlines() if l.startswith("•")]) >= 10)

# ── 무상증자 — 같은 폴백 구멍 (실제 fricDecsn 응답으로 검증) ─────────
fric = format_typed_disclosure("알파칩스", "주요사항보고서(무상증자결정)", {
    'bddd': '2026년 10월 07일', 'nstk_ostk_cnt': '6,661,742', 'fv_ps': '500',
    'bfic_tisstk_ostk': '6,661,742', 'nstk_asstd': '2026년 10월 22일',
    'nstk_ascnt_ps_ostk': '1', 'nstk_dividrk': '2026년 01월 01일',
    'nstk_lstprd': '2026년 11월 19일',
}, today=TODAY)
check("무상증자: 전용 카드", "[무상증자 결정]" in fric)
check("무상증자: 1주당 배정 주식수", "1주당 배정 주식수: 1주" in fric)
check("무상증자: 신주배정 기준일", "신주배정 기준일: 2026년 10월 22일" in fric)
check("무상증자: 신주 상장 예정일", "2026년 11월 19일" in fric)
check("무상증자를 유상증자로 표기하지 않음", "[유상증자" not in fric)

# ── 정형 경로에는 💡를 붙이지 않는다 ─────────────────────────────────
# 홀드아웃 35건 전부에 💡가 붙었는데 카드에 없는 숫자를 추가한 건 0건이었다.
# 정형 응답이 곧 데이터이므로 코멘트는 바꿔 말하는 것뿐이고, 그러다 프롬프트
# 머리말을 출력(3건)하고 회사명을 '유티아이'→'유티아'로 깨뜨렸다.
import asyncio  # noqa: E402
import summarizer  # noqa: E402

async def _typed(data, report="주요사항보고서(유상증자결정)"):
    called = []
    async def should_not_run(prompt):
        called.append(prompt)
        return "💡 덧붙인 말"
    orig = summarizer._generate_with_fallback
    summarizer._generate_with_fallback = should_not_run
    try:
        out = await summarizer.summarize_typed_disclosure("유티아이", report, data)
    finally:
        summarizer._generate_with_fallback = orig
    return out, called

out, called = asyncio.run(_typed({
    'ic_mthn': '제3자배정증자', 'nstk_ostk_cnt': '711,989', 'fv_ps': '500',
    'bfic_tisstk_ostk': '21,569,319', 'fdpp_op': '1,324,299,540',
}))
check("정형 카드에 💡 없음", "💡" not in out)
check("정형 경로는 LLM을 부르지 않음", called == [])
check("카드 수치는 그대로", "711,989주" in out and "1,324,299,540원" in out)

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
