"""/stats 검증 — 운영자 전용, 그리고 '보낸 것'과 '사용자가 한 것'의 분리.

왜 만들었나: 운영자가 사용자에게 매번 "쓰고 있냐"고 물어볼 수 없다.
왜 조심해야 하나: 이 서비스는 **보낸 것**만 기록한다. 클릭·조회·검색어는
어디에도 남지 않으므로, 발송량을 참여도로 보여주면 그 자체가 거짓말이 된다.

확인할 성질:
  - 운영자가 아니면 집계가 나가지 않는다
  - 집계 수치가 맞다 (관심기업·구독·발송·요청)
  - '마지막 행동'은 사용자가 직접 한 것만 센다 — 우리가 보낸 시각은 제외
  - 출력에 '열어봤다는 뜻이 아니다'라는 경계가 남는다
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types
from datetime import datetime, timezone

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "stats.db"
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

from database import AsyncSessionLocal, init_db  # noqa: E402
from models import Feedback, SeenDisclosure, TopicSubscription, User, Watchlist  # noqa: E402
from services import stats_service  # noqa: E402
import bot  # noqa: E402

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, chat_id):
        self.effective_chat = type("C", (), {"id": chat_id, "first_name": ""})()
        self.message = FakeMessage()


def at(day, hour=9):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


async def seed():
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id="op-chat", first_name="누나", created_at=at(1)))
        s.add(User(chat_id="g2", first_name="건수", created_at=at(1)))
        s.add(User(chat_id="g3", first_name="차단한사람", is_active=False, created_at=at(2)))

        s.add(Watchlist(id="w0", chat_id="op-chat", corp_code="C0",
                        corp_name="삼성전자", created_at=at(1)))
        for i, name in enumerate(("SK하이닉스", "한미반도체", "리노공업")):
            s.add(Watchlist(id=f"w{i+1}", chat_id="g2", corp_code=f"C{i+1}",
                            corp_name=name, created_at=at(1)))
        s.add(TopicSubscription(id="t1", chat_id="g2", topic="공급계약", created_at=at(2)))
        s.add(Feedback(id="f1", chat_id="g2", text="정보가 너무 적게 온다",
                       status="new", created_at=at(5)))
        # 발송은 사용자의 행동이 아니다 — 일부러 '마지막 행동'보다 훨씬 뒤로 둔다
        for i in range(5):
            s.add(SeenDisclosure(id=f"s{i}", receipt_no=f"R{i}", chat_id="g2",
                                 corp_name="SK하이닉스", report_nm="단일판매ㆍ공급계약체결",
                                 created_at=at(15 + (i % 3), 10 + i)))
        await s.commit()


async def main():
    await init_db()
    await seed()

    # ── 권한 ─────────────────────────────────────────────────────────
    u = FakeUpdate("g2")
    await bot.stats(u, None)
    check("비운영자는 집계를 못 본다", "운영자 전용" in u.message.replies[0])
    check("비운영자에게 수치가 새지 않음",
          any(x in u.message.replies[0] for x in ("건수", "관심기업", "발송")), False)

    u = FakeUpdate("op-chat")
    await bot.stats(u, None)
    out = u.message.replies[0]
    check("운영자는 집계를 본다", "사용 현황" in out)

    # ── 집계 정확성 ──────────────────────────────────────────────────
    data = await stats_service.collect_stats()
    rows = {r["chat_id"]: r for r in data["users"]}
    check("사용자 수", data["totals"]["users"], 3)
    check("활성 사용자 수(차단자 제외)", data["totals"]["active"], 2)
    check("관심기업 수", rows["g2"]["watchlist"], 3)
    check("등록 없는 사용자는 0", rows["g3"]["watchlist"], 0)
    check("유형 구독", rows["g2"]["topics"], ["공급계약"])
    check("구독 없으면 빈 목록", rows["op-chat"]["topics"], [])
    check("발송 건수", rows["g2"]["sent"], 5)
    check("미처리 요청 수", data["totals"]["open_feedback"], 1)
    check("비활성 표시", rows["g3"]["is_active"], False)

    # ── 핵심: '마지막 행동'에 우리 발송을 섞지 않는다 ────────────────
    # 발송은 9/15~17인데 사용자가 마지막으로 직접 한 것은 9/5 피드백이다.
    # 이걸 섞으면 "9/17까지 활발히 사용 중"이라는 거짓 신호가 된다.
    last_action = rows["g2"]["last_action"]
    check("마지막 행동은 사용자가 직접 한 것(9/5 피드백)", last_action.day, 5)
    check("마지막 발송은 따로 표시(9/17)", rows["g2"]["last_sent"].day, 17)
    check("행동과 발송이 다른 값", last_action != rows["g2"]["last_sent"])
    check("행동이 없는 사용자는 None", rows["g3"]["last_action"], None)

    # ── 출력이 한계를 숨기지 않는다 ──────────────────────────────────
    text = stats_service.format_stats(data, now=datetime(2026, 9, 18, tzinfo=timezone.utc))
    check("발송을 참여로 오독하지 않게 경고", "열어봤다는 뜻이 아닙니다" in text)
    check("클릭 미기록을 명시", "클릭은 기록하지 않습니다" in text)
    check("'마지막 행동'과 '받은 알림'을 나눠 표기",
          "마지막 행동" in text and "받은 알림" in text)
    check("사용자 이름 표기", "건수" in text)
    check("경과일 표기", "일 전" in text)

    # ── 사용자가 없을 때 ─────────────────────────────────────────────
    empty = stats_service.format_stats({"users": [], "totals": {
        "users": 0, "active": 0, "sent": 0, "open_feedback": 0}})
    check("빈 상태 안내", "아직 사용자가 없습니다" in empty)

    if failures:
        print(f"\n{len(failures)}건 실패: {failures}")
        sys.exit(1)
    print("\n전부 통과")


asyncio.run(main())
