"""서비스 계층(user/watchlist) DB 로직 스모크 테스트 — 임시 SQLite, 외부 서비스 불필요.

Phase 0(서비스 계층 분리, PR #20) 검증용으로 작성. 리팩터로 세션·쿼리 로직이
깨지지 않았는지를 실제 DB 연산으로 확인한다.
"""
import asyncio
import logging
import pathlib
import sys
import tempfile
import os

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "services.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from database import init_db, engine  # noqa: E402
import models  # noqa: E402,F401
from services import user_service, watchlist_service  # noqa: E402

CID = "test-123"
results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


async def main():
    # init_db는 create_all 후 alembic upgrade를 시도한다 — SQLite에선 마이그레이션이
    # 실패해도 로그만 남기고 계속되는 설계(프로덕션 폴백 경로와 동일)라 문제없다.
    await init_db()

    # --- user_service ---
    await user_service.ensure_user(CID, "Alice")
    u = await user_service.get_user(CID)
    check("user created", u is not None and u.first_name == "Alice")
    await user_service.ensure_user(CID, "Alice")
    u = await user_service.get_user(CID)
    check("ensure_user idempotent", u is not None)

    synced = await user_service.set_today_keywords(CID, "전환사채,유상증자")
    check("set_today not synced by default", synced is False)
    check("get_today_keywords split", await user_service.get_today_keywords(CID) == ["전환사채", "유상증자"])

    u2 = await user_service.toggle_sync(CID)
    check("toggle_sync on + copies today->mytoday",
          u2.sync_keywords is True and u2.mytoday_keywords == "전환사채,유상증자")

    synced2 = await user_service.set_today_keywords(CID, "감자")
    check("set_today syncs when sync on",
          synced2 is True and await user_service.get_mytoday_keywords(CID) == ["감자"])

    await user_service.clear_today_keywords(CID)
    check("clear_today", await user_service.get_today_keywords(CID) == [])

    # --- watchlist_service ---
    added, skipped = await watchlist_service.add_watchlist(
        CID, "Alice", {"00126380": "삼성전자", "00164779": "SK하이닉스"})
    check("add_watchlist added 2", set(added) == {"삼성전자", "SK하이닉스"} and skipped == [])

    added2, skipped2 = await watchlist_service.add_watchlist(CID, "Alice", {"00126380": "삼성전자"})
    check("add_watchlist skips duplicate", added2 == [] and skipped2 == ["삼성전자"])

    codes = await watchlist_service.get_corp_codes(CID)
    check("get_corp_codes", codes == {"00126380", "00164779"})

    found = await watchlist_service.find_by_name(CID, "삼성")
    check("find_by_name ilike", len(found) == 1 and found[0].corp_name == "삼성전자")

    check("remove_by_code deletes", await watchlist_service.remove_by_code(CID, "00126380") is True)
    check("remove missing returns False", await watchlist_service.remove_by_code(CID, "00126380") is False)

    lst = await watchlist_service.list_watchlist(CID)
    check("list after remove", len(lst) == 1 and lst[0].corp_name == "SK하이닉스")

    await engine.dispose()
    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
