"""자연어 공시 질의 (Stage 8) — native 툴콜링 루프 + 멀티턴.

설계 결정 (이슈 #3 베이스라인):
  - 프레임워크 없이 OpenAI 네이티브 tool-calling 루프. LangGraph 등은 이
    베이스라인의 측정치(Hit@k·정답률)를 이긴다는 근거가 생길 때 도입한다(ADR-001 보류).
  - 도구는 결정론적 3종만: 기업명 해석 → 공시 검색(DART, 과거 수년) → 정형 상세.
    답변의 모든 사실은 도구 결과에서만 나오고, 반드시 공시 접수번호를 인용한다.
  - 투자의견 금지(법적 하드 룰)는 시스템 프롬프트 + 기존 검증 자산으로 이중 방어.
  - 대화 이력은 chat_id별 프로세스 메모리(§6-4 제약과 동일 — SaaS 전환 시 외부화).

모델: 생성 체인과 동일한 gpt-4o-mini (대화형이라 지연·비용 우선).
평가: evals/query_eval.py — 검색 Hit(기대 공시가 도구 결과에 포함)·인용 정확도.
"""
import asyncio
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

QUERY_MODEL = "gpt-4o-mini"
MAX_TOOL_ROUNDS = 6          # 무한 루프 방지
HISTORY_MAX_TURNS = 8        # chat별 유지할 (user+assistant) 메시지 수
HISTORY_TTL_SECONDS = 1800   # 30분 지나면 새 대화로 취급

# chat_id -> {"messages": [...], "at": monotonic}
_sessions: dict[str, dict] = {}

# 평가 하네스가 검색 결과를 관찰할 수 있게 마지막 도구 결과를 남긴다
last_tool_trace: list[dict] = []

SYSTEM_PROMPT = """당신은 DART 공시 검색 비서입니다. 오늘 날짜(KST): {today}

규칙:
1. 모든 사실은 도구 결과에서만 가져온다. 도구 결과에 없는 내용은 "확인되지 않았다"고 말한다.
2. 언급하는 공시마다 접수번호를 이 형식의 링크로 붙인다:
   https://dart.fss.or.kr/dsaf001/main.do?rcpNo=접수번호
3. 매수/매도/호재/악재/목표가 등 투자 판단·권유 표현을 절대 쓰지 않는다.
   해석 요청을 받아도 "사실 전달까지가 역할"이라고 답한다.
4. 기업명이 모호하면 resolve_company 결과의 후보를 보여주고 되묻는다.
5. 상대 기간("최근 한 달")은 날짜를 직접 계산하지 말고 days_back으로 넘긴다.
   기간 미지정 시 days_back=365로 검색하고, 그렇게 했다고 밝힌다.
6. 답변은 간결하게. 목록은 최신순 최대 10건.
7. 일반 텍스트로만 답한다. 마크다운 서식(**, ##, `- ` 글머리 기호 대신 · 사용) 금지
   — 텔레그램에 서식 문자가 그대로 노출된다."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_company",
            "description": "기업명으로 상장사를 찾아 DART corp_code를 얻는다. 검색 전 반드시 호출.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "기업명(부분 일치 가능)"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_disclosures",
            "description": "기업의 공시 목록을 DART에서 조회한다. 수년 범위 가능. "
                           "'최근 N일/개월/년' 같은 상대 기간은 날짜를 직접 계산하지 말고 "
                           "days_back으로 지정하라 (서버가 오늘 기준으로 계산한다).",
            "parameters": {
                "type": "object",
                "properties": {
                    "corp_code": {"type": "string", "description": "resolve_company로 얻은 8자리 코드"},
                    "days_back": {"type": "integer",
                                  "description": "오늘부터 거슬러 검색할 일수 (예: 최근 한 달=30, 1년=365). 상대 기간은 반드시 이걸 사용"},
                    "bgn_de": {"type": "string", "description": "시작일 YYYYMMDD — 사용자가 명시적 날짜를 준 경우만"},
                    "end_de": {"type": "string", "description": "종료일 YYYYMMDD — 사용자가 명시적 날짜를 준 경우만"},
                    "keyword": {"type": "string", "description": "공시명 필터 (예: 전환사채, 자기주식 처분). 띄어쓰기 무시, 모든 단어 포함 기준. 선택"},
                },
                "required": ["corp_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_typed_details",
            "description": "특정 공시의 정형 수치(발행금액·전환가 등)를 조회한다. 숫자 질문에 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "corp_code": {"type": "string"},
                    "rcept_no": {"type": "string"},
                    "report_nm": {"type": "string"},
                    "rcept_dt": {"type": "string", "description": "YYYYMMDD"},
                },
                "required": ["corp_code", "rcept_no", "report_nm", "rcept_dt"],
            },
        },
    },
]


async def _exec_tool(name: str, args: dict) -> str:
    """도구 실행 — 전부 결정론적(DART/캐시). 결과는 JSON 문자열로 모델에 준다."""
    from dart import fetch_corp_disclosures, fetch_typed_disclosure
    from services.corp_service import search_corps

    if name == "resolve_company":
        results = await search_corps(args["name"])
        payload = [{"corp_code": c, "corp_name": n} for c, n in results[:8]]
    elif name == "search_disclosures":
        # 날짜 산출은 서버가 한다 — 모델이 상대 기간을 직접 계산하면 자기 학습
        # 시점의 '오늘'(예: 2023)로 셈해 엉뚱한 연도를 검색한다 (query eval r2에서
        # SK네트웍스 MISS의 원인). D-day 사례와 동일 원칙: 달력 계산은 코드가.
        from dart import kst_date_str, today_kst
        bgn = args.get("bgn_de") or kst_date_str(int(args.get("days_back") or 365))
        end = args.get("end_de") or today_kst()
        rows = await fetch_corp_disclosures(args["corp_code"], bgn, end)
        kw = (args.get("keyword") or "").strip()
        if kw:
            # 공백 무시 토큰 매칭 — 실제 공시명은 붙여쓴다("자기주식처분결정").
            # 모델이 "자기주식 처분"처럼 띄어 보내면 정확 부분문자열은 전멸한다
            # (2026-08-18 query eval r1에서 Hit 0.5의 원인). 필터가 전부
            # 걸러내면 무필터로 돌려줘 재현율은 도구가, 정밀도는 모델이 맡는다.
            tokens = [t.replace(" ", "") for t in kw.replace(",", " ").split() if t]
            filtered = [
                r for r in rows
                if all(t in r.get("report_nm", "").replace(" ", "") for t in tokens)
            ]
            rows = filtered or rows
        rows = sorted(rows, key=lambda r: r.get("rcept_dt", ""), reverse=True)[:20]
        payload = [{
            "rcept_no": r.get("rcept_no"), "corp_name": r.get("corp_name"),
            "report_nm": r.get("report_nm"), "rcept_dt": r.get("rcept_dt"),
        } for r in rows]
    elif name == "get_typed_details":
        typed = await fetch_typed_disclosure(
            args["corp_code"], args["rcept_no"], args["report_nm"], args["rcept_dt"]
        )
        payload = typed or {"error": "정형 데이터 없음 — 이 유형은 원문 확인 필요"}
    else:
        payload = {"error": f"unknown tool {name}"}

    last_tool_trace.append({"tool": name, "args": args, "result": payload})
    return json.dumps(payload, ensure_ascii=False)


def _get_history(chat_id: str) -> list:
    s = _sessions.get(chat_id)
    if not s or time.monotonic() - s["at"] > HISTORY_TTL_SECONDS:
        _sessions[chat_id] = {"messages": [], "at": time.monotonic()}
    return _sessions[chat_id]["messages"]


def reset_session(chat_id: str) -> None:
    _sessions.pop(chat_id, None)


async def _create_completion(messages, tools):
    """LLM 호출 지점 — 테스트에서 이 함수만 교체하면 전체 루프를 검증할 수 있다."""
    import os
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return await client.chat.completions.create(
        model=QUERY_MODEL, max_tokens=900, messages=messages, tools=tools,
    )


async def answer_query(chat_id: str, question: str) -> str:
    """질문 1건 처리. 멀티턴 이력을 잇고, 도구 루프를 돌고, 답을 돌려준다."""
    # LLM 비용 가드는 요약과 공유 (지연 임포트 — 테스트 스텁 호환)
    from summarizer import _budget_allows, _notify_budget_once
    if not _budget_allows():
        await _notify_budget_once()
        return "오늘 AI 질의 한도에 도달했습니다. 내일 다시 시도해주세요."

    last_tool_trace.clear()
    history = _get_history(chat_id)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT.format(today=today)}]
        + history
        + [{"role": "user", "content": question}]
    )

    for _ in range(MAX_TOOL_ROUNDS):
        response = await _create_completion(messages, TOOLS)
        msg = response.choices[0].message

        if not msg.tool_calls:
            answer = (msg.content or "").strip() or "답을 만들지 못했습니다. 다시 질문해주세요."
            # 멀티턴 이력 갱신 (system 제외, 최근 N개 유지)
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": answer})
            del history[:-HISTORY_MAX_TURNS]
            _sessions[chat_id]["at"] = time.monotonic()
            return answer

        # 도구 호출 라운드 — 병렬 실행
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        outs = await asyncio.gather(*[
            _exec_tool(tc.function.name, json.loads(tc.function.arguments or "{}"))
            for tc in msg.tool_calls
        ], return_exceptions=True)
        for tc, out in zip(msg.tool_calls, outs):
            if isinstance(out, Exception):
                out = json.dumps({"error": f"{type(out).__name__}: {out}"}, ensure_ascii=False)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})

    return "질문이 너무 복잡해 검색을 마치지 못했습니다. 기업명과 기간을 좁혀 다시 시도해주세요."
