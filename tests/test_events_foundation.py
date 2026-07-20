"""Stage 2 기반(events 모듈) 검증 — normalizer·metrics·renderer 단위 + 저장 멱등 + 플래그 통합."""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types
import uuid
from decimal import Decimal

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "events.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


def part1_units():
    from events.normalizer import (clean_text, parse_decimal, normalize_convertible_bond,
                                   normalize_paid_increase, normalize_typed_disclosure)
    from events.metrics import calculate_dilution_rate
    from events.renderer import render_event

    check("parse_decimal '4,605주'", parse_decimal("4,605주") == Decimal("4605"))
    check("parse_decimal '-' → None", parse_decimal("-") is None)
    check("parse_decimal '' → None", parse_decimal("") is None)
    check("parse_decimal '3.5%'", parse_decimal("3.5%") == Decimal("3.5"))
    check("parse_decimal 쓰레기 → None", parse_decimal("협의 후 결정") is None)
    check("clean_text 공백만 → None", clean_text("   ") is None)

    cb = normalize_convertible_bond({"bd_fta": "15,000,000,000", "cv_prc": "3,250", "bd_knd": "무기명식 이권부 무보증 사모 전환사채"})
    check("CB normalizer: 단위 제거·문자열 보존",
          cb["normalized_data"]["amount"] == "15000000000"
          and cb["normalized_data"]["conversion_price"] == "3250"
          and cb["normalized_data"]["maturity_date"] is None)

    pi = normalize_paid_increase({"nstk_ostk_cnt": "1,000,000주", "nstk_ispr": "5,000", "allot_mthn": "제3자배정증자"})
    check("유상증자 normalizer", pi["normalized_data"]["new_share_count"] == "1000000"
          and pi["normalized_data"]["allotment_method"] == "제3자배정증자")

    check("dispatch: 전환사채", normalize_typed_disclosure("전환사채 발행결정", {})["event_type"] == "convertible_bond")
    check("dispatch: 유상증자", normalize_typed_disclosure("유상증자 결정", {})["event_type"] == "paid_in_capital_increase")
    check("dispatch: 미지원 → other", normalize_typed_disclosure("감자 결정", {})["event_type"] == "other")

    check("희석률: 분모 없으면 None (0 아님)", calculate_dilution_rate(Decimal("100"), None) is None)
    check("희석률: 100/(900+100)=10.00", calculate_dilution_rate(Decimal("100"), Decimal("900")) == Decimal("10.00"))

    card = render_event(cb)
    check("renderer CB: 헤더+값 줄", card.startswith("[전환사채 발행결정]") and "• 발행금액: 15000000000원" in card)
    check("renderer CB: None 필드 줄 생략", "만기일" not in card)
    check("renderer: 미지원 유형 None", render_event({"event_type": "other"}) is None)


TYPED = {"bd_fta": "15,000,000,000", "cv_prc": "3,250", "rcept_no": "R200"}

async def part2_persistence_and_flag():
    dart_stub = types.ModuleType("dart")
    D = {"rcept_no": "R200", "corp_name": "T전자", "report_nm": "전환사채 발행결정",
         "corp_code": "C0001", "rcept_dt": "20260721"}
    async def fetch_recent_disclosures(days=1): return [dict(D)]
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
    async def send_alert(**kw): return True
    async def send_system_message(chat_id, text): pass
    notif.send_alert = send_alert
    notif.send_system_message = send_system_message
    sys.modules["notifier"] = notif

    from sqlalchemy import select, func
    from database import AsyncSessionLocal, engine, Base
    import models  # noqa: F401
    from models import User, Watchlist, Disclosure, DisclosureEvent
    import config
    import tasks
    from services import event_service

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id="u1", first_name="u1"))
        s.add(Watchlist(id=str(uuid.uuid4()), chat_id="u1", corp_code="C0001", corp_name="T전자"))
        s.add(Disclosure(rcept_no="R200", corp_code="C0001", corp_name="T전자",
                         report_nm="전환사채 발행결정", rcept_dt="20260721", is_important=True))
        await s.commit()

    async def event_count():
        async with AsyncSessionLocal() as s:
            r = await s.execute(select(func.count()).select_from(DisclosureEvent))
            return r.scalar_one()

    tasks._is_business_hours_kst = lambda now=None: False

    # 플래그 OFF(기본): 이벤트 미기록
    config.ENABLE_EVENT_CARDS = False
    await tasks.process_disclosures()
    check("플래그 OFF: 이벤트 미기록 (프로덕션 기본 무영향)", await event_count() == 0)

    # 플래그 ON: 기록 — 단 이미 발송(Seen)된 공시는 요약경로 스킵이므로 새 유저로 재트리거
    config.ENABLE_EVENT_CARDS = True
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id="u2", first_name="u2"))
        s.add(Watchlist(id=str(uuid.uuid4()), chat_id="u2", corp_code="C0001", corp_name="T전자"))
        await s.commit()
    await tasks.process_disclosures()
    check("플래그 ON: 이벤트 1행 기록", await event_count() == 1)

    async with AsyncSessionLocal() as s:
        r = await s.execute(select(DisclosureEvent))
        ev = r.scalar_one()
        check("이벤트 내용: 타입·정규화 값", ev.event_type == "convertible_bond"
              and ev.normalized_data["amount"] == "15000000000"
              and ev.corp_code == "C0001")

    # 멱등: 직접 재호출해도 중복 저장 없음
    async with AsyncSessionLocal() as s:
        r = await s.execute(select(Disclosure).where(Disclosure.rcept_no == "R200"))
        row = r.scalar_one()
        added = await event_service.record_typed_event(s, row, "전환사채 발행결정")
        await s.commit()
    check("멱등: 재기록 시도 False·행 1개 유지", added is False and await event_count() == 1)

    await engine.dispose()


async def main():
    part1_units()
    await part2_persistence_and_flag()
    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
