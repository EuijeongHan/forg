"""평가 하네스 검증 — 네트워크·LLM 없이 하네스 자체의 논리를 확인한다.

  - 티어0(결정론 verify_summary)이 pass/warning/fail을 올바르게 나누는지
    (실제 app/verification/checks를 사용 — 한국어 단위 정규화 경로 포함)
  - 티어1 judge 결과가 지표에 올바르게 집계되는지 (judge는 스텁)
  - 리포트에 두 티어와 문제 항목이 드러나는지
"""
import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # evals 패키지
sys.path.insert(0, str(ROOT / "app"))  # verification

from evals.summary_eval import aggregate, evaluate_items, render_report  # noqa: E402
from verification.checks import verify_summary  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


ITEMS = [
    {"rcept_no": "G1", "corp_name": "가나전자", "report_nm": "유상증자결정",
     "api": "piicDecsn", "typed_data": {"fdpp_fclt": "15,000,000,000"}},
    {"rcept_no": "G2", "corp_name": "다라화학", "report_nm": "전환사채발행결정",
     "api": "cvbdIsDecsn", "typed_data": {"bd_fta": "5,000,000,000"}},
    {"rcept_no": "G3", "corp_name": "마바바이오", "report_nm": "유상증자결정",
     "api": "piicDecsn", "typed_data": {"fdpp_fclt": "2,000,000,000"}},
]

# 요약 시나리오: 충실 / 단위 환각 / 투자의견 위반
SUMMARIES = {
    "G1": "시설자금 150억원 조달을 위한 유상증자 결정입니다.",          # 150억 == 정형값 → pass
    "G2": "발행금액 약 5조원 규모의 전환사채입니다.",                   # 5조 != 50억 → warning
    "G3": "유상증자 20억원. 주가 상승이 기대되므로 매수 추천드립니다.",  # 금칙어 → fail
}

JUDGES = {
    "G1": {"faithful": True, "errors": [], "opinion": False},
    "G2": {"faithful": False, "errors": ["발행금액 단위 오류(5조 vs 50억)"], "opinion": False},
    "G3": {"faithful": True, "errors": [], "opinion": True},
}


async def fake_summarize(corp_name, report_nm, typed_data):
    for k, item in ((i["rcept_no"], i) for i in ITEMS):
        if item["typed_data"] is typed_data:
            return SUMMARIES[k]
    raise AssertionError("unknown item")


async def fake_judge(summary, typed_data):
    for item in ITEMS:
        if item["typed_data"] is typed_data:
            return JUDGES[item["rcept_no"]]
    return None


async def main():
    results = await evaluate_items(ITEMS, fake_summarize, verify_summary, judge_fn=fake_judge)
    by = {r["rcept_no"]: r for r in results}

    # 티어0 결정론 판정 (실제 checks 사용)
    check("충실 요약(150억==정형값) → pass", by["G1"]["verdict"], "pass")
    check("단위 환각(5조) → warning", by["G2"]["verdict"], "warning")
    check("투자의견(매수 추천) → fail", by["G3"]["verdict"], "fail")
    check("금칙어가 checks에 기록됨", len(by["G3"]["checks"]["investment_opinion"]) > 0, True)

    # 집계
    m = aggregate(results)
    check("N 집계", m["n"], 3)
    check("verdict 분포", m["verdicts"],
          {"pass": 1, "warning": 1, "fail": 1, "unavailable": 0})
    check("의견 위반 1건", m["opinion_violations"], 1)
    check("judge 판정 수", m["judge"]["judged"], 3)
    check("judge 충실율 2/3", m["judge"]["faithful_rate"], round(2 / 3, 3))
    check("judge 의견 플래그 1건", m["judge"]["opinion_flags"], 1)
    check("지연 지표 존재", isinstance(m["latency_ms"]["avg"], int), True)

    # judge 없이도 동작 (가용 불가 시 폴백)
    results_nj = await evaluate_items(ITEMS, fake_summarize, verify_summary)
    m_nj = aggregate(results_nj)
    check("judge 미가용 시 judged=0", m_nj["judge"]["judged"], 0)
    check("judge 미가용 시 faithful_rate=None", m_nj["judge"]["faithful_rate"], None)

    # 리포트
    report = render_report(m, results, {"date": "2026-08-18", "golden": "test", "commit": "test"})
    check("리포트에 티어0 표기", "[티어0 결정론]" in report, True)
    check("리포트에 티어1 judge 표기", "[티어1 judge=" in report, True)
    check("문제 항목에 warning 건 포함", "다라화학" in report, True)
    check("문제 항목에 judge 지적 포함", "단위 오류" in report, True)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
