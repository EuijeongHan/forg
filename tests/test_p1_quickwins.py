"""P1 퀵윈 검증 — LLM 일일 비용 가드(P1-1) + 봇 조회 요약 캐시(P1-3).

파트 1: 실제 summarizer 모듈 — 한도 도달 시 provider 미호출·폴백 텍스트,
        typed는 카드만 반환, KST 날짜 변경 시 리셋.
파트 2: 실제 disclosure_service — 동일 receipt 재조회 시 요약 1회만 생성,
        실패 요약은 캐시 안 함.
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "p1.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ.pop("TELEGRAM_CHAT_ID", None)  # 운영자 경보 전송 경로 차단
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


async def part1_budget():
    import config
    import summarizer

    provider_calls = {"n": 0}

    async def fake_provider(prompt):
        provider_calls["n"] += 1
        return "요약결과"

    summarizer.summarize_with_openai = fake_provider
    summarizer.summarize_with_claude = fake_provider
    summarizer.summarize_with_gemini = fake_provider

    config.LLM_DAILY_CALL_LIMIT = 3
    summarizer._llm_calls_today = 0
    summarizer._llm_count_date = None
    summarizer._budget_op_alerted = False

    outs = [await summarizer.summarize_disclosure("A사", "유상증자 결정", "본문") for _ in range(5)]
    check("P1-1: 한도(3)까지만 provider 호출", provider_calls["n"] == 3)
    check("P1-1: 한도 내 정상 요약", outs[0] == "요약결과" and outs[2] == "요약결과")
    check("P1-1: 한도 초과 시 폴백 텍스트", "한도에 도달" in outs[3] and "한도에 도달" in outs[4])

    # typed: 한도 초과 상태 → 카드만 반환 (LLM 미호출)
    n_before = provider_calls["n"]
    card_out = await summarizer.summarize_typed_disclosure(
        "A사", "전환사채 발행결정", {"bd_fta": "10,000,000,000", "cv_prc": "3,000"})
    check("P1-1: typed 한도 초과 시 카드만", card_out.startswith("[전환사채 발행결정]")
          and "💡" not in card_out and provider_calls["n"] == n_before)

    # 날짜 변경 시 리셋
    summarizer._llm_count_date = "19990101"
    out = await summarizer.summarize_disclosure("A사", "유상증자 결정", "본문")
    check("P1-1: KST 날짜 변경 시 카운터 리셋", out == "요약결과" and provider_calls["n"] == n_before + 1)


async def part2_cache():
    gen_calls = {"n": 0}

    dart_stub = types.ModuleType("dart")
    async def fetch_recent_disclosures(days=1): return []
    async def fetch_disclosure_detail(r): return "본문"
    async def fetch_typed_disclosure(c, r, n, d): return {"bd_fta": "1"}
    for f in (fetch_recent_disclosures, fetch_disclosure_detail, fetch_typed_disclosure):
        setattr(dart_stub, f.__name__, f)
    sys.modules["dart"] = dart_stub

    summ_stub = types.ModuleType("summarizer")
    async def summarize_typed_disclosure(c, n, d):
        gen_calls["n"] += 1
        return "카드요약"
    async def summarize_disclosure(c, n, content):
        gen_calls["n"] += 1
        return "요약"
    summ_stub.summarize_typed_disclosure = summarize_typed_disclosure
    summ_stub.summarize_disclosure = summarize_disclosure
    sys.modules["summarizer"] = summ_stub

    from services import disclosure_service
    disclosure_service._summary_cache.clear()

    hint = {"corp_name": "A사", "report_nm": "전환사채 발행결정",
            "corp_code": "C1", "rcept_dt": "20260721"}
    r1 = await disclosure_service.summarize_by_receipt("R001", hint)
    r2 = await disclosure_service.summarize_by_receipt("R001", hint)
    check("P1-3: 동일 공시 재조회 시 요약 1회만 생성", gen_calls["n"] == 1)
    check("P1-3: 캐시 결과 동일", r1 == r2 and r1["summary"] == "카드요약")

    # 실패 요약은 캐시하지 않음 → 재시도 가능
    async def failing(c, n, d):
        gen_calls["n"] += 1
        return "요약 생성에 실패했습니다. DART에서 직접 확인해주세요."
    summ_stub.summarize_typed_disclosure = failing
    await disclosure_service.summarize_by_receipt("R002", hint)
    await disclosure_service.summarize_by_receipt("R002", hint)
    check("P1-3: 실패 요약은 캐시 안 함(재시도 2회)", gen_calls["n"] == 3)


async def main():
    await part1_budget()
    await part2_cache()
    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
