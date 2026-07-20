"""P1-2 정형 스냅샷 검증 — typed 응답이 Disclosure.raw_typed_data에 1회 저장되는지."""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types
import uuid

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "snap.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

TYPED = {"bd_fta": "15,000,000,000", "cv_prc": "3,250", "rcept_no": "R100"}

dart_stub = types.ModuleType("dart")
DISCLOSURE = {
    "rcept_no": "R100", "corp_name": "테스트전자",
    "report_nm": "전환사채 발행결정", "corp_code": "C0001", "rcept_dt": "20260721",
}
async def fetch_recent_disclosures(days=1): return [dict(DISCLOSURE)]
async def save_disclosures_to_db(d): pass
async def fetch_rcept_times(date): return {}
async def fetch_disclosure_detail(r): return "본문"
async def fetch_typed_disclosure(c, r, n, d): return dict(TYPED)
def is_important(nm): return True
def is_after_hours(t): return False
def today_kst(): return "20260721"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_rcept_times,
          fetch_disclosure_detail, fetch_typed_disclosure, is_important,
          is_after_hours, today_kst):
    setattr(dart_stub, f.__name__, f)
sys.modules["dart"] = dart_stub

summ = types.ModuleType("summarizer")
async def summarize_disclosure(c, n, content): return "요약"
async def summarize_typed_disclosure(c, n, d): return "카드"
summ.summarize_disclosure = summarize_disclosure
summ.summarize_typed_disclosure = summarize_typed_disclosure
sys.modules["summarizer"] = summ

notif = types.ModuleType("notifier")
async def send_alert(chat_id, corp_name, report_nm, receipt_no, summary): return True
async def send_system_message(chat_id, text): pass
notif.send_alert = send_alert
notif.send_system_message = send_system_message
sys.modules["notifier"] = notif

from sqlalchemy import select  # noqa: E402
from database import AsyncSessionLocal, engine, Base  # noqa: E402
import models  # noqa: E402,F401
from models import User, Watchlist, Disclosure  # noqa: E402
import tasks  # noqa: E402

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


async def get_row():
    async with AsyncSessionLocal() as s:
        r = await s.execute(select(Disclosure).where(Disclosure.rcept_no == "R100"))
        return r.scalar_one_or_none()


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id="u1", first_name="u1"))
        s.add(Watchlist(id=str(uuid.uuid4()), chat_id="u1", corp_code="C0001", corp_name="테스트전자"))
        s.add(Disclosure(rcept_no="R100", corp_code="C0001", corp_name="테스트전자",
                         report_nm="전환사채 발행결정", rcept_dt="20260721", is_important=True))
        await s.commit()

    tasks._is_business_hours_kst = lambda now=None: False
    await tasks.process_disclosures()
    row = await get_row()
    check("스냅샷 저장됨", row is not None and row.raw_typed_data is not None)
    check("스냅샷 내용 = typed 응답", row.raw_typed_data.get("bd_fta") == "15,000,000,000")

    # 재실행: 덮어쓰지 않음 + 에러 없음 (전원 발송됨 → 요약 스킵 경로라 스냅샷 훅도 미도달)
    await tasks.process_disclosures()
    row2 = await get_row()
    check("재실행에도 원본 보존", row2.raw_typed_data == row.raw_typed_data)

    # Disclosure 행이 없는 공시(스텁이 저장 안 함)여도 크래시 없음
    DISCLOSURE["rcept_no"] = "R999"
    TYPED["rcept_no"] = "R999"
    await tasks.process_disclosures()
    check("행 부재 시 무해(no-crash)", True)

    await engine.dispose()
    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
