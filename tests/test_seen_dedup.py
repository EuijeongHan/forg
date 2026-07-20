"""I-1(복합 unique) + I-2(요약 중복 생성 방지) 행동 검증 — 임시 SQLite + 스텁 모듈.

이슈 #22 / PR #23 검증용. tasks.process_disclosures를 실제로 구동하되
dart/summarizer/notifier는 스텁으로 대체해 외부 호출 없이 파이프라인 로직만 본다.
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types
import uuid

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "dedup.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

calls = {"summarize": 0, "typed_fetch": 0, "alerts": []}

# --- tasks.py 외부의존 스텁 (httpx/telegram/anthropic 불필요) ---
dart = types.ModuleType("dart")
DISCLOSURE = {
    "rcept_no": "20260721000001", "corp_name": "테스트전자",
    "report_nm": "유상증자 결정", "corp_code": "C0001", "rcept_dt": "20260721",
}
async def fetch_recent_disclosures(days=1): return [dict(DISCLOSURE)]
async def save_disclosures_to_db(d): pass
async def fetch_rcept_times(date): return {}
async def fetch_disclosure_detail(r): return "본문"
async def fetch_typed_disclosure(c, r, n, d):
    calls["typed_fetch"] += 1
    return {}
def is_important(nm): return "유상증자" in nm
def is_after_hours(t): return False
def today_kst(): return "20260721"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_rcept_times,
          fetch_disclosure_detail, fetch_typed_disclosure, is_important,
          is_after_hours, today_kst):
    setattr(dart, f.__name__, f)
sys.modules["dart"] = dart

summarizer = types.ModuleType("summarizer")
async def summarize_disclosure(c, n, content):
    calls["summarize"] += 1
    return "요약본"
async def summarize_typed_disclosure(c, n, d):
    calls["summarize"] += 1
    return "카드"
summarizer.summarize_disclosure = summarize_disclosure
summarizer.summarize_typed_disclosure = summarize_typed_disclosure
sys.modules["summarizer"] = summarizer

notifier = types.ModuleType("notifier")
async def send_alert(chat_id, corp_name, report_nm, receipt_no, summary):
    calls["alerts"].append(chat_id)
    return True
notifier.send_alert = send_alert
async def send_system_message(chat_id, text): pass
notifier.send_system_message = send_system_message
sys.modules["notifier"] = notifier

from sqlalchemy import select, func  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from database import AsyncSessionLocal, engine, Base  # noqa: E402
import models  # noqa: E402,F401
from models import User, Watchlist, SeenDisclosure  # noqa: E402
from tasks import process_disclosures  # noqa: E402

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


async def seed(chat_id):
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id=chat_id, first_name=chat_id))
        s.add(Watchlist(id=str(uuid.uuid4()), chat_id=chat_id,
                        corp_code="C0001", corp_name="테스트전자"))
        await s.commit()


async def seen_count():
    async with AsyncSessionLocal() as s:
        r = await s.execute(select(func.count()).select_from(SeenDisclosure))
        return r.scalar_one()


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed("userA")
    await seed("userB")

    # cycle 1: 두 사용자 모두 미발송 → 요약 1회, 알림 2건, Seen 2행(같은 receipt_no)
    await process_disclosures()
    check("cycle1: summary 1회만 생성", calls["summarize"] == 1)
    check("cycle1: 알림 2건", sorted(calls["alerts"]) == ["userA", "userB"])
    check("cycle1: 동일 receipt 2행 허용 (I-1)", await seen_count() == 2)

    # cycle 2: 전원 발송 완료 → 요약/정형API 재호출 0 (I-2)
    s_before, t_before, a_before = calls["summarize"], calls["typed_fetch"], len(calls["alerts"])
    await process_disclosures()
    check("cycle2: 요약 재생성 없음 (I-2)", calls["summarize"] == s_before)
    check("cycle2: 정형 API 재호출 없음 (I-2)", calls["typed_fetch"] == t_before)
    check("cycle2: 중복 알림 없음", len(calls["alerts"]) == a_before)

    # cycle 3: 새 사용자 등장 → 그 사용자에게만 발송
    await seed("userC")
    await process_disclosures()
    check("cycle3: 신규 사용자에게만 알림", calls["alerts"][a_before:] == ["userC"])
    check("cycle3: 요약 1회만 추가 생성", calls["summarize"] == s_before + 1)
    check("cycle3: Seen 3행", await seen_count() == 3)

    # 제약 직접 검증
    dup_rejected = False
    try:
        async with AsyncSessionLocal() as s:
            s.add(SeenDisclosure(id=str(uuid.uuid4()), receipt_no=DISCLOSURE["rcept_no"],
                                 chat_id="userA", corp_name="x", report_nm="y", summary=None))
            await s.commit()
    except IntegrityError:
        dup_rejected = True
    check("복합 unique: (receipt,chat) 중복 삽입 거부", dup_rejected)

    await engine.dispose()
    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
