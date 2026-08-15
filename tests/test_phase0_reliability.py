"""Phase 0 운영 신뢰성 검증 — 폴링 상태 분류 · 건별 격리 · 데이터 삭제.

파트 1: poll_status가 success/empty/partial/error를 구분하는지.
  "조용한 정상(공시 없음)"과 "조용한 고장(수집 실패)"이 구분되지 않으면
  침묵 사망을 감지할 수 없다.
파트 2: 공시 한 건이 실패해도 나머지가 계속 처리되는지(건별 격리).
파트 3: delete_user_data가 사용자 데이터만 지우고 개수를 정확히 반환하는지.
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "phase0.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = "operator-chat"
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

state = {"mode": "empty", "fail_on": set(), "sent": []}

DISCLOSURES = [
    {"rcept_no": "R1", "corp_name": "A사", "report_nm": "유상증자결정",
     "corp_code": "C1", "rcept_dt": "20260812"},
    {"rcept_no": "R2", "corp_name": "B사", "report_nm": "유상증자결정",
     "corp_code": "C2", "rcept_dt": "20260812"},
    {"rcept_no": "R3", "corp_name": "C사", "report_nm": "유상증자결정",
     "corp_code": "C3", "rcept_dt": "20260812"},
]

dart_stub = types.ModuleType("dart")
async def fetch_recent_disclosures(days=1):
    if state["mode"] == "raise":
        raise RuntimeError("DART down (simulated)")
    if state["mode"] == "empty":
        return []
    return list(DISCLOSURES)
async def save_disclosures_to_db(d): pass
async def fetch_rcept_times(date): return {}
async def fetch_disclosure_detail(r): return "본문"
async def fetch_typed_disclosure(c, r, n, d):
    if r in state["fail_on"]:
        raise RuntimeError(f"typed API broke on {r} (simulated)")
    return {}
def is_important(nm): return True
def is_after_hours(t): return False
def today_kst(): return "20260812"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_rcept_times,
          fetch_disclosure_detail, fetch_typed_disclosure, is_important,
          is_after_hours, today_kst):
    setattr(dart_stub, f.__name__, f)
sys.modules["dart"] = dart_stub

summ = types.ModuleType("summarizer")
async def summarize_disclosure(c, n, content, bypass_budget=False): return "요약"
async def summarize_typed_disclosure(c, n, d, bypass_budget=False): return "카드"
summ.summarize_disclosure = summarize_disclosure
summ.summarize_typed_disclosure = summarize_typed_disclosure
sys.modules["summarizer"] = summ

notif = types.ModuleType("notifier")
async def send_alert(chat_id, corp_name, report_nm, receipt_no, summary, tier="important"):
    state["sent"].append(receipt_no)
    return True
async def send_system_message(chat_id, text): pass
notif.send_alert = send_alert
notif.send_system_message = send_system_message
sys.modules["notifier"] = notif

import tasks  # noqa: E402
from database import AsyncSessionLocal, init_db  # noqa: E402
from models import SeenDisclosure, User, Watchlist  # noqa: E402
from services import user_service  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


async def seed_user(chat_id="U1"):
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id=chat_id, first_name="tester"))
        await s.flush()
        for code in ("C1", "C2", "C3"):
            s.add(Watchlist(id=f"w-{chat_id}-{code}", chat_id=chat_id,
                            corp_code=code, corp_name=f"{code}사"))
        await s.commit()


async def main():
    await init_db()
    await seed_user()

    # --- 파트 1: 상태 분류 ---
    # 비지니스 시간 판정을 고정해 시계 의존을 없앤다
    tasks._is_business_hours_kst = lambda now=None: False

    state["mode"] = "empty"
    await tasks.process_disclosures()
    check("공시 0건 → empty", tasks.poll_status["last_result"], "empty")
    check("empty일 때 수집 건수 0", tasks.poll_status["last_fetch_count"], 0)

    state["mode"] = "raise"
    await tasks.process_disclosures()
    check("수집 실패 → error", tasks.poll_status["last_result"], "error")
    check("error는 마지막 오류를 남긴다", tasks.poll_status["last_error"] is not None, True)

    state["mode"] = "ok"
    state["fail_on"] = set()
    state["sent"] = []
    await tasks.process_disclosures()
    check("정상 처리 → success", tasks.poll_status["last_result"], "success")
    check("발송 3건 집계", tasks.poll_status["last_alert_count"], 3)
    check("오류 해소 시 last_error 초기화", tasks.poll_status["last_error"], None)

    # --- 파트 2: 건별 격리 ---
    # R2만 실패시킨다. 격리가 없으면 R3가 영구 미발송된다.
    async with AsyncSessionLocal() as s:
        for row in (await s.execute(__import__("sqlalchemy").select(SeenDisclosure))).scalars().all():
            await s.delete(row)
        await s.commit()

    state["fail_on"] = {"R2"}
    state["sent"] = []
    await tasks.process_disclosures()
    check("일부 실패 → partial", tasks.poll_status["last_result"], "partial")
    check("실패한 R2를 건너뛰고 R1·R3 발송", sorted(state["sent"]), ["R1", "R3"])

    # --- 파트 3: 데이터 삭제 ---
    counts = await user_service.delete_user_data("U1")
    check("워치리스트 3건 삭제", counts["watchlist"], 3)
    check("계정 1건 삭제", counts["user"], 1)
    check("발송 기록도 삭제", counts["seen"] > 0, True)

    async with AsyncSessionLocal() as s:
        sa = __import__("sqlalchemy")
        left_users = (await s.execute(sa.select(User).where(User.chat_id == "U1"))).scalars().all()
        left_watch = (await s.execute(sa.select(Watchlist).where(Watchlist.chat_id == "U1"))).scalars().all()
        left_seen = (await s.execute(sa.select(SeenDisclosure).where(SeenDisclosure.chat_id == "U1"))).scalars().all()
    check("삭제 후 계정 없음", len(left_users), 0)
    check("삭제 후 워치리스트 없음", len(left_watch), 0)
    check("삭제 후 발송 기록 없음", len(left_seen), 0)

    counts_again = await user_service.delete_user_data("U1")
    check("없는 사용자 삭제는 0건 반환(멱등)", counts_again["user"], 0)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
