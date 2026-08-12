"""Phase 1 명령 개편 검증 — 단일 조회 경로와 빈 결과 구분.

개편안 §4가 요구한 성질을 확인한다.
  - /today는 관심기업, /market은 전체 시장
  - 검색어는 인자로만 오고 저장되지 않는다(영구 필터 없음)
  - "오늘 공시 없음"과 "검색어 결과 없음"이 구분된다
  - 결과 헤더가 범위·중요도·검색어·건수를 드러낸다
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "phase1.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

MARKET = [
    {"rcept_no": "M1", "corp_name": "가나전자", "report_nm": "유상증자결정",
     "corp_code": "C1", "rcept_dt": "20260812"},
    {"rcept_no": "M2", "corp_name": "다라화학", "report_nm": "감사보고서제출",
     "corp_code": "C2", "rcept_dt": "20260812"},
    {"rcept_no": "M3", "corp_name": "마바바이오", "report_nm": "전환사채발행결정",
     "corp_code": "C9", "rcept_dt": "20260812"},
]

state = {"market": list(MARKET)}

dart_stub = types.ModuleType("dart")
async def fetch_recent_disclosures(days=1): return list(state["market"])
async def save_disclosures_to_db(d): pass
async def fetch_today_disclosures_from_db(important_only=False):
    items = list(state["market"])
    return [d for d in items if is_important(d["report_nm"])] if important_only else items
def is_important(nm): return "결정" in nm or "감사보고서" in nm
async def fetch_disclosure_detail(r): return ""
async def fetch_typed_disclosure(c, r, n, d): return {}
def is_after_hours(t): return False
def today_kst(): return "20260812"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_today_disclosures_from_db,
          is_important, fetch_disclosure_detail, fetch_typed_disclosure,
          is_after_hours, today_kst):
    setattr(dart_stub, f.__name__, f)
sys.modules["dart"] = dart_stub

summ = types.ModuleType("summarizer")
async def summarize_disclosure(c, n, content): return "요약"
async def summarize_typed_disclosure(c, n, d): return "카드"
summ.summarize_disclosure = summarize_disclosure
summ.summarize_typed_disclosure = summarize_typed_disclosure
sys.modules["summarizer"] = summ

from services import disclosure_service as ds  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


async def main():
    # --- 범위 분리 ---
    r = await ds.query_disclosures(scope="market", important_only=True)
    check("market는 전체 시장 3건", len(r.items), 3)

    r = await ds.query_disclosures(scope="watchlist", corp_codes={"C1"}, important_only=True)
    check("watchlist는 등록 기업만", [d["rcept_no"] for d in r.items], ["M1"])

    r = await ds.query_disclosures(scope="watchlist", corp_codes=set(), important_only=True)
    check("빈 워치리스트는 0건", len(r.items), 0)
    check("빈 워치리스트는 검색어 탓이 아님", r.filtered_to_empty, False)

    # --- 일회성 검색어 ---
    r = await ds.query_disclosures(scope="market", important_only=True, query="유상증자")
    check("검색어로 좁힌 결과", [d["rcept_no"] for d in r.items], ["M1"])
    check("검색어는 결과에 기록된다", r.query, "유상증자")

    r2 = await ds.query_disclosures(scope="market", important_only=True)
    check("검색어는 저장되지 않는다(다음 조회 영향 없음)", len(r2.items), 3)

    # --- 빈 결과 구분 ---
    r = await ds.query_disclosures(scope="market", important_only=True, query="없는공시명")
    check("검색어 미스는 0건", len(r.items), 0)
    check("검색어 때문에 비었음을 구분", r.filtered_to_empty, True)
    check("검색 전 총건수 보존", r.total_before_query, 3)

    state["market"] = []
    r = await ds.query_disclosures(scope="market", important_only=True)
    check("진짜 공시 없음은 filtered_to_empty가 아님", r.filtered_to_empty, False)
    state["market"] = list(MARKET)

    # --- 헤더 ---
    r = await ds.query_disclosures(scope="watchlist", corp_codes={"C1", "C2"}, important_only=True)
    h = r.header()
    check("헤더에 범위 표기", "관심기업" in h, True)
    check("헤더에 중요 표기", "중요" in h, True)
    check("헤더에 건수 표기", "(2건)" in h, True)

    r = await ds.query_disclosures(scope="market", important_only=True, query="감사")
    check("헤더에 검색어 표기", "'감사'" in r.header(), True)
    check("헤더에 전체 시장 표기", "전체 시장" in r.header(), True)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
