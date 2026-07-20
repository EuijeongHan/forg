"""Stage 0 잔여(I-3~I-5) 행동 검증 — PR #27.

파트 1: SQLite + 스텁으로 tasks.process_disclosures — 발송 실패 시 미기록·재시도(I-3).
파트 2: 실제 notifier.build_disclosure_message — HTML 이스케이프(I-4). (python-telegram-bot 필요)
파트 3: 실제 dart.today_kst — KST 날짜(I-5). (httpx 필요)
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
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "stage0.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

calls = {"summarize": 0, "attempts": [], "delivered": []}
fail_users: set[str] = set()

# --- 스텁: dart / summarizer / notifier ---
dart_stub = types.ModuleType("dart")
DISCLOSURE = {
    "rcept_no": "20260721000777", "corp_name": "테스트전자",
    "report_nm": "유상증자 결정", "corp_code": "C0001", "rcept_dt": "20260721",
}
async def fetch_recent_disclosures(days=1): return [dict(DISCLOSURE)]
async def save_disclosures_to_db(d): pass
async def fetch_rcept_times(date): return {}
async def fetch_disclosure_detail(r): return "본문"
async def fetch_typed_disclosure(c, r, n, d): return {}
def is_important(nm): return "유상증자" in nm
def is_after_hours(t): return False
def today_kst(): return "20260721"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_rcept_times,
          fetch_disclosure_detail, fetch_typed_disclosure, is_important,
          is_after_hours, today_kst):
    setattr(dart_stub, f.__name__, f)
sys.modules["dart"] = dart_stub

summ = types.ModuleType("summarizer")
async def summarize_disclosure(c, n, content):
    calls["summarize"] += 1
    return "요약본"
async def summarize_typed_disclosure(c, n, d):
    calls["summarize"] += 1
    return "카드"
summ.summarize_disclosure = summarize_disclosure
summ.summarize_typed_disclosure = summarize_typed_disclosure
sys.modules["summarizer"] = summ

notif = types.ModuleType("notifier")
async def send_alert(chat_id, corp_name, report_nm, receipt_no, summary):
    calls["attempts"].append(chat_id)
    if chat_id in fail_users:
        return False  # I-3: 실패 반환 계약
    calls["delivered"].append(chat_id)
    return True
notif.send_alert = send_alert
async def send_system_message(chat_id, text): pass
notif.send_system_message = send_system_message
sys.modules["notifier"] = notif

from sqlalchemy import select, func  # noqa: E402
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


async def part1():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed("userA")
    await seed("userB")

    fail_users.add("userB")
    await process_disclosures()
    check("I-3: 실패 사용자는 Seen 미기록 (A만 기록)", await seen_count() == 1)
    check("I-3: 두 사용자 모두 발송 시도됨", sorted(calls["attempts"]) == ["userA", "userB"])
    check("I-3: 전달은 A만", calls["delivered"] == ["userA"])

    fail_users.clear()
    s_before = calls["summarize"]
    await process_disclosures()
    check("I-3: 다음 폴링에서 B 재시도·전달", calls["delivered"] == ["userA", "userB"])
    check("I-3: B 기록 완료 (총 2행)", await seen_count() == 2)
    check("회귀 I-2: 미발송자 있으면 요약 재생성 1회", calls["summarize"] == s_before + 1)

    s_before, a_before = calls["summarize"], len(calls["attempts"])
    await process_disclosures()
    check("회귀 I-2: 전원 발송 후 요약 재생성 0", calls["summarize"] == s_before)
    check("회귀 I-2: 추가 발송 시도 0", len(calls["attempts"]) == a_before)

    await engine.dispose()


def part2():
    del sys.modules["notifier"]  # 스텁 제거 → 실제 모듈
    import notifier as real_notifier
    msg = real_notifier.build_disclosure_message(
        "A&B <급등> 전자", "유상증자 <정정> & 발행", "20260721000777", "요약 <b>테스트</b> & 검증"
    )
    check("I-4: corp_name 이스케이프", "A&amp;B &lt;급등&gt; 전자" in msg)
    check("I-4: report_nm 이스케이프", "유상증자 &lt;정정&gt; &amp; 발행" in msg)
    check("I-4: summary 이스케이프", "요약 &lt;b&gt;테스트&lt;/b&gt; &amp; 검증" in msg)
    check("I-4: 원문 링크 포함", 'rcpNo=20260721000777' in msg)
    check("I-4: raw < > 미존재(허용 태그 제외)",
          msg.count("<") == msg.count("<b>") + msg.count("</b>") + msg.count("<a ") + msg.count("</a>"))


def part3():
    del sys.modules["dart"]  # 스텁 제거 → 실제 모듈
    import dart as real_dart
    from datetime import datetime
    from zoneinfo import ZoneInfo
    expected = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    check("I-5: today_kst == KST 오늘", real_dart.today_kst() == expected)


async def main():
    await part1()
    part2()
    part3()
    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
