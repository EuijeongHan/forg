"""Stage 8 질의 루프 검증 — LLM·네트워크 없이 루프 역학을 확인한다.

  - 툴콜 라운드: 모델이 요청한 도구가 올바른 인자로 실행되고 결과가 되돌아가는지
  - 멀티턴: 두 번째 질문에 직전 문답이 문맥으로 전달되는지 + 초기화
  - 비용 가드: 한도 초과 시 LLM 호출 없이 안내 반환
  - 상한: 도구만 반복 요청하는 모델에서 MAX_TOOL_ROUNDS 후 안전 종료
  - 도구 예외: 실행 실패가 루프를 죽이지 않고 error로 전달되는지
"""
import asyncio
import json
import pathlib
import sys
import types
from types import SimpleNamespace as NS

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
sys.path.insert(0, APP)

state = {"budget": True, "searches": [], "typed_calls": [], "captured_messages": []}

dart_stub = types.ModuleType("dart")
async def fetch_corp_disclosures(corp_code, bgn_de, end_de, max_pages=10):
    state["searches"].append((corp_code, bgn_de, end_de))
    return [{"rcept_no": "R-CB-1", "corp_name": "씨젠", "report_nm": "전환사채발행결정",
             "rcept_dt": "20260810"}]
async def fetch_typed_disclosure(c, r, n, d):
    state["typed_calls"].append(r)
    return {"bd_fta": "5,000,000,000"}
dart_stub.fetch_corp_disclosures = fetch_corp_disclosures
def kst_date_str(days_ago=0): return "20260719" if days_ago == 30 else "20250818"
def today_kst(): return "20260818"
dart_stub.kst_date_str = kst_date_str
dart_stub.today_kst = today_kst
dart_stub.fetch_typed_disclosure = fetch_typed_disclosure
sys.modules["dart"] = dart_stub

corp_stub = types.ModuleType("services.corp_service")
async def search_corps(name):
    return [("00788773", "씨젠")]
corp_stub.search_corps = search_corps

summ = types.ModuleType("summarizer")
def _budget_allows(): return state["budget"]
async def _notify_budget_once(): pass
summ._budget_allows = _budget_allows
summ._notify_budget_once = _notify_budget_once
sys.modules["summarizer"] = summ

import services  # noqa: E402  (실제 패키지 로드 후 하위 모듈만 교체)
sys.modules["services.corp_service"] = corp_stub

from services import query_service as qs  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


def tool_call(cid, tool, **args):
    return NS(id=cid, function=NS(name=tool, arguments=json.dumps(args)))


def resp(content=None, tool_calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls))])


def scripted(responses):
    """스크립트된 응답 시퀀스로 _create_completion을 대체한다."""
    it = iter(responses)
    async def fake(messages, tools):
        state["captured_messages"] = [dict(m) if isinstance(m, dict) else m for m in messages]
        return next(it)
    return fake


async def main():
    # ── 1. 툴콜 2라운드 → 최종 답변 ──────────────────────────────
    qs._create_completion = scripted([
        resp(tool_calls=[tool_call("c1", "resolve_company", name="씨젠")]),
        resp(tool_calls=[tool_call("c2", "search_disclosures",
                                   corp_code="00788773", bgn_de="20250818", end_de="20260818")]),
        resp(content="씨젠의 전환사채 공시 1건: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=R-CB-1"),
    ])
    answer = await qs.answer_query("U1", "씨젠 최근 1년 CB 공시 찾아줘")
    check("최종 답변 반환", "R-CB-1" in answer, True)
    check("검색 도구가 올바른 인자로 실행", state["searches"], [("00788773", "20250818", "20260818")])
    check("도구 트레이스 기록(평가 하네스용)", [t["tool"] for t in qs.last_tool_trace],
          ["resolve_company", "search_disclosures"])

    # ── 1b. 키워드 공백 무시 토큰 매칭 (query eval r1 Hit 0.5 원인 수정) ──
    out = json.loads(await qs._exec_tool("search_disclosures", {
        "corp_code": "00788773", "bgn_de": "20260101", "end_de": "20260818",
        "keyword": "전환사채 발행",   # 실제 공시명은 "전환사채발행결정" (붙여씀)
    }))
    check("띄어 쓴 키워드도 매칭", [r["rcept_no"] for r in out], ["R-CB-1"])
    out2 = json.loads(await qs._exec_tool("search_disclosures", {
        "corp_code": "00788773", "bgn_de": "20260101", "end_de": "20260818",
        "keyword": "없는유형",
    }))
    check("필터 전멸 시 무필터 폴백(재현율 보존)", len(out2), 1)

    # ── 1c. 상대 기간은 서버가 계산 (r2 SK네트웍스 MISS 원인 수정) ──
    state["searches"].clear()
    await qs._exec_tool("search_disclosures", {"corp_code": "00788773", "days_back": 30})
    check("days_back=30 → 서버 계산 날짜", state["searches"], [("00788773", "20260719", "20260818")])
    state["searches"].clear()
    await qs._exec_tool("search_disclosures", {"corp_code": "00788773"})
    check("기간 미지정 → 기본 1년", state["searches"], [("00788773", "20250818", "20260818")])

    # ── 2. 멀티턴 — 직전 문답이 문맥으로 전달 ────────────────────
    qs._create_completion = scripted([
        resp(tool_calls=[tool_call("c3", "get_typed_details", corp_code="00788773",
                                   rcept_no="R-CB-1", report_nm="전환사채발행결정", rcept_dt="20260810")]),
        resp(content="발행금액은 5,000,000,000원입니다."),
    ])
    answer2 = await qs.answer_query("U1", "그 공시 발행금액이 얼마야?")
    check("후속 질문 답변", "5,000,000,000" in answer2, True)
    check("정형 상세 도구 실행", state["typed_calls"], ["R-CB-1"])
    sent_roles = [(m.get("role"), (m.get("content") or "")) for m in state["captured_messages"]
                  if isinstance(m, dict)]
    had_prev_q = any("씨젠 최근 1년" in (c or "") for _, c in sent_roles)
    had_prev_a = any("R-CB-1" in (c or "") for r, c in sent_roles if r == "assistant")
    check("직전 질문이 문맥에 포함", had_prev_q, True)
    check("직전 답변이 문맥에 포함", had_prev_a, True)

    # ── 3. 초기화 ────────────────────────────────────────────────
    qs.reset_session("U1")
    qs._create_completion = scripted([resp(content="새 대화입니다.")])
    await qs.answer_query("U1", "안녕")
    fresh_roles = [m.get("role") for m in state["captured_messages"] if isinstance(m, dict)]
    check("초기화 후 이력 없음(system+user만)", fresh_roles, ["system", "user"])

    # ── 4. 비용 가드 ─────────────────────────────────────────────
    state["budget"] = False
    called = {"n": 0}
    async def never(messages, tools):
        called["n"] += 1
        return resp(content="호출되면 안 됨")
    qs._create_completion = never
    limited = await qs.answer_query("U2", "질문")
    check("한도 초과 시 안내 반환", "한도" in limited, True)
    check("한도 초과 시 LLM 미호출", called["n"], 0)
    state["budget"] = True

    # ── 5. 도구 무한 요청 → 라운드 상한 종료 ─────────────────────
    async def always_tools(messages, tools):
        return resp(tool_calls=[tool_call("cx", "resolve_company", name="씨젠")])
    qs._create_completion = always_tools
    capped = await qs.answer_query("U3", "복잡한 질문")
    check("라운드 상한 후 안전 종료", "좁혀" in capped, True)

    # ── 6. 도구 예외 격리 ────────────────────────────────────────
    async def boom(c, r, n, d, **_kw):
        raise RuntimeError("typed API down")
    dart_stub.fetch_typed_disclosure = boom
    qs._create_completion = scripted([
        resp(tool_calls=[tool_call("c9", "get_typed_details", corp_code="x",
                                   rcept_no="R", report_nm="n", rcept_dt="20260101")]),
        resp(content="정형 데이터를 확인하지 못했습니다."),
    ])
    survived = await qs.answer_query("U4", "숫자 알려줘")
    check("도구 예외에도 루프 생존", "확인하지 못했" in survived, True)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
