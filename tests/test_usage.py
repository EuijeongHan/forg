"""사용 기록 검증 — 동사만 세고, 목적어와 사람은 남기지 않는다.

왜 필요했나: 이 서비스는 '우리가 보낸 것'만 기록해서 사용자가 실제로 쓰는지
알 방법이 없었다. 발송량은 그날 공시가 많았다는 뜻일 뿐이다.

왜 조심하나: 첫 사용자가 기관 애널리스트다. '오늘 어느 기업을 열어봤나'는
소속 기관의 리서치 방향이라, 그것만은 남기지 않는 게 설계의 핵심이다.

확인할 성질:
  - 명령 인자와 공시번호가 이벤트 이름에서 잘려 나간다
  - 테이블에 사용자 식별자 칼럼이 아예 없다
  - 운영자(본인 테스트)는 집계에 섞이지 않는다
  - 같은 날 같은 이벤트는 행이 늘지 않고 카운터만 오른다
  - 기록이 실패해도 사용자 요청은 계속된다
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "usage.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = "op-chat"
os.environ["OPERATOR_CHAT_IDS"] = "op-chat"
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

dart_stub = types.ModuleType("dart")
async def fetch_recent_disclosures(days=1, **_kw): return []
async def save_disclosures_to_db(d, **_kw): pass
async def fetch_rcept_times(date, **_kw): return {}
async def fetch_disclosure_detail(r, **_kw): return ""
async def fetch_typed_disclosure(c, r, n, d, **_kw): return {}
def is_important(nm): return False
def is_after_hours(t): return False
def today_kst(): return "20260918"
def kst_date_str(days_ago=0): return "20260918"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_rcept_times,
          fetch_disclosure_detail, fetch_typed_disclosure, is_important,
          is_after_hours, today_kst, kst_date_str):
    setattr(dart_stub, f.__name__, f)
sys.modules["dart"] = dart_stub

notif = types.ModuleType("notifier")
async def send_system_message(chat_id, text): pass
async def send_alert(*a, **k): return True
async def send_html_message(chat_id, html_text): return True
def escape_html(t): return t
def build_disclosure_message(*a): return ""
for name in ("send_system_message", "send_alert", "send_html_message",
             "escape_html", "build_disclosure_message"):
    setattr(notif, name, locals()[name])
sys.modules["notifier"] = notif

from sqlalchemy import select  # noqa: E402
from database import AsyncSessionLocal, init_db  # noqa: E402
from models import UsageDaily, User  # noqa: E402
from services import stats_service, usage_service  # noqa: E402
import bot  # noqa: E402

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


class FakeUpdate:
    def __init__(self, chat_id="g2", text=None, data=None):
        self.effective_chat = type("C", (), {"id": chat_id, "first_name": ""})()
        self.message = type("M", (), {"text": text})() if text is not None else None
        self.callback_query = type("Q", (), {"data": data})() if data is not None else None


async def rows():
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(UsageDaily))).scalars().all()


async def main():
    await init_db()

    # ── 테이블에 사람이 없다 ─────────────────────────────────────────
    cols = {c.name for c in UsageDaily.__table__.columns}
    check("사용자 식별자 칼럼 없음", "chat_id" in cols, False)
    check("담는 건 날짜·이벤트·횟수뿐", cols, {"id", "day", "event", "count"})

    # ── 이벤트 이름: 목적어를 잘라낸다 ───────────────────────────────
    name = usage_service.event_name
    check("검색어는 남지 않는다", name(FakeUpdate(text="/market 카카오 유상증자")), "market")
    check("공시번호는 남지 않는다", name(FakeUpdate(data="view:20260918000123")), "view")
    check("페이지 오프셋도 버린다", name(FakeUpdate(data="page:240")), "page")
    check("기업코드도 버린다", name(FakeUpdate(data="toggle:00126380")), "toggle")
    check("콜론 없는 콜백", name(FakeUpdate(data="confirm_add")), "confirm_add")
    check("봇 멘션 제거", name(FakeUpdate(text="/start@forg_alert_bot")), "start")
    check("대문자 정규화", name(FakeUpdate(text="/MY")), "my")
    check("일반 대화는 이벤트 아님", name(FakeUpdate(text="안녕")), "")
    check("빈 업데이트도 안전", name(FakeUpdate()), "")

    # ── 운영자는 섞이지 않는다 ───────────────────────────────────────
    check("운영자 조작은 기록 안 함", await usage_service.record("op-chat", "my"), False)
    check("운영자 기록 후에도 행 없음", len(await rows()), 0)
    check("빈 이벤트는 기록 안 함", await usage_service.record("g2", ""), False)

    # ── 사용자: 같은 날 같은 이벤트는 카운터만 오른다 ────────────────
    for _ in range(3):
        await usage_service.record("g2", "view")
    await usage_service.record("g2", "my")
    got = {(r.day, r.event): r.count for r in await rows()}
    check("행은 (날짜,이벤트)당 하나", len(got), 2)
    check("반복은 카운터 증가", got[("20260918", "view")], 3)
    check("다른 이벤트는 별도", got[("20260918", "my")], 1)
    check("날짜는 KST", all(d == "20260918" for d, _ in got))

    # ── 훅: 기록이 깨져도 사용자 요청은 계속된다 ─────────────────────
    orig = usage_service.record
    async def boom(*a, **k):
        raise RuntimeError("db down")
    usage_service.record = boom
    try:
        await bot._record_usage(FakeUpdate(text="/my"), None)   # 예외가 새면 실패
        check("기록 실패를 삼킨다", True)
    except Exception as e:
        check("기록 실패를 삼킨다", f"예외 누출: {e}", True)
    finally:
        usage_service.record = orig

    await bot._record_usage(FakeUpdate(chat_id="g2", text="/market 삼성"), None)
    got = {(r.day, r.event): r.count for r in await rows()}
    check("훅을 통해서도 동사만 기록", got.get(("20260918", "market")), 1)

    # ── /stats 출력 ──────────────────────────────────────────────────
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id="g2", first_name="건수"))
        await s.commit()
    data = await stats_service.collect_stats()
    check("사용 집계 포함", data["usage"]["events"]["view"], 3)
    check("사용한 날 집계", data["usage"]["active_days"], 1)
    text = stats_service.format_stats(data)
    check("사용 섹션 표시", "기능 사용" in text)
    check("사람이 읽는 라벨", "요약 열람 3" in text)
    check("운영자 제외 명시", "운영자 제외" in text)
    check("무엇을 기록 안 하는지 명시", "어느 기업인지는 기록하지 않습니다" in text)
    check("발송과 사용을 구분해 안내", "실제로 쓰는지는 '기능 사용'을 보세요" in text)

    # 배포 직후의 실제 상태: 사용자는 있는데 기록은 아직 0
    fresh = stats_service.format_stats({
        "users": [{"chat_id": "g2", "first_name": "건수", "is_active": True,
                   "joined": None, "watchlist": 4, "topics": ["공급계약"],
                   "sent": 37, "last_sent": None, "feedback": 1, "last_action": None}],
        "usage": {"window": 14, "events": {}, "active_days": 0, "last_day": None},
        "totals": {"users": 1, "active": 1, "sent": 37, "open_feedback": 1}})
    check("기록 없을 때 안내", "아직 기록 없음" in fresh)
    check("기록 0이어도 발송 수치는 그대로", "받은 알림: 37건" in fresh)

    if failures:
        print(f"\n{len(failures)}건 실패: {failures}")
        sys.exit(1)
    print("\n전부 통과")


asyncio.run(main())
