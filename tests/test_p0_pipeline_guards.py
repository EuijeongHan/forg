"""P0 파이프라인 가드 검증 — 자정 경계 2일 창(P0-2) + 침묵 사망 자가 경보(P0-3).

파트 1: 스텁으로 tasks.process_disclosures 구동 —
  - 폴링이 fetch_recent_disclosures(days=2)를 호출하는지
  - 연속 실패 임계(5회)에서 운영자 경보 1회, 회복 시 리셋, 재발 시 재경보
  - 평일 장중 공시 0건 임계(10사이클)에서 경보 1회
파트 2: 실제 dart 모듈 — kst_date_str 날짜 산출.
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "p0.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = "operator-chat"  # 경보 수신자 (config가 임포트 시 읽음)
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

state = {"days_seen": [], "mode": "empty", "system_msgs": []}

dart_stub = types.ModuleType("dart")
async def fetch_recent_disclosures(days=1):
    state["days_seen"].append(days)
    if state["mode"] == "raise":
        raise RuntimeError("DART down (simulated)")
    return []  # mode == "empty"
async def save_disclosures_to_db(d): pass
async def fetch_rcept_times(date): return {}
async def fetch_disclosure_detail(r): return ""
async def fetch_typed_disclosure(c, r, n, d): return {}
def is_important(nm): return False
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
async def send_system_message(chat_id, text):
    state["system_msgs"].append((chat_id, text))
notif.send_alert = send_alert
notif.send_system_message = send_system_message
sys.modules["notifier"] = notif

import tasks  # noqa: E402
from tasks import process_disclosures  # noqa: E402

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


async def main():
    # --- P0-2: 폴링은 2일 창으로 조회 ---
    await process_disclosures()
    check("P0-2: 폴링이 days=2로 조회", state["days_seen"][-1] == 2)

    # --- P0-3a: 연속 실패 경보 (임계 5) ---
    state["mode"] = "raise"
    for _ in range(4):
        await process_disclosures()
    check("P0-3a: 임계 미만(4회)엔 경보 없음", len(state["system_msgs"]) == 0)
    await process_disclosures()  # 5회째
    check("P0-3a: 5회째 경보 1건", len(state["system_msgs"]) == 1
          and state["system_msgs"][0][0] == "operator-chat"
          and "연속 5회 실패" in state["system_msgs"][0][1])
    await process_disclosures()  # 6회째 — 중복 경보 금지
    check("P0-3a: 경보는 1회만 (스팸 방지)", len(state["system_msgs"]) == 1)

    # 회복 → 리셋 → 재발 시 재경보
    state["mode"] = "empty"
    tasks._is_business_hours_kst = lambda now=None: False  # 빈 결과 경보 간섭 차단
    await process_disclosures()  # 성공(빈손이지만 예외 아님) → 실패 카운터 리셋
    state["mode"] = "raise"
    for _ in range(5):
        await process_disclosures()
    check("P0-3a: 회복 후 재발 시 재경보", len(state["system_msgs"]) == 2)

    # --- P0-3b: 평일 장중 공시 0건 지속 경보 (임계 10) ---
    state["mode"] = "empty"
    await process_disclosures()  # 실패 스트릭 리셋용 성공 1회 (business=False라 empty 카운트 안 됨)
    tasks._is_business_hours_kst = lambda now=None: True
    base = len(state["system_msgs"])
    for _ in range(9):
        await process_disclosures()
    check("P0-3b: 임계 미만(9사이클)엔 경보 없음", len(state["system_msgs"]) == base)
    await process_disclosures()  # 10사이클째
    check("P0-3b: 10사이클째 경보 1건", len(state["system_msgs"]) == base + 1
          and "공시 0건" in state["system_msgs"][-1][1])
    await process_disclosures()  # 11사이클 — 중복 금지
    check("P0-3b: 경보는 1회만", len(state["system_msgs"]) == base + 1)

    # --- 파트 2: 실제 dart 모듈의 날짜 헬퍼 ---
    del sys.modules["dart"]
    import dart as real_dart
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    check("P0-2: kst_date_str(0) == 오늘(KST)",
          real_dart.kst_date_str(0) == kst_now.strftime("%Y%m%d"))
    check("P0-2: kst_date_str(1) == 어제(KST)",
          real_dart.kst_date_str(1) == (kst_now - timedelta(days=1)).strftime("%Y%m%d"))

    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
