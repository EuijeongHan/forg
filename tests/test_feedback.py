"""/feedback 검증 — 저장 우선, 운영자 전달, 빈 입력 안내, 데이터 삭제 포함.

'놓친 공시' 신고가 목적함수(놓침 0)의 실측 데이터이므로 확인할 성질:
  - 접수는 DB 저장이 먼저다 — 운영자 전달이 실패해도 유실되지 않는다
  - 미등록 사용자도 신고할 수 있다(계정 자동 생성, FK 보장)
  - /deletedata의 "전체 삭제" 약속에 피드백이 포함된다
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "feedback.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = "op-chat"
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

state = {"operator_msgs": [], "forward_fails": False}

dart_stub = types.ModuleType("dart")
async def fetch_recent_disclosures(days=1): return []
async def save_disclosures_to_db(d): pass
async def fetch_disclosure_detail(r): return ""
async def fetch_typed_disclosure(c, r, n, d): return {}
def is_important(nm): return False
def is_after_hours(t): return False
def today_kst(): return "20260818"
def kst_date_str(days_ago=0): return "20260818"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_disclosure_detail,
          fetch_typed_disclosure, is_important, is_after_hours, today_kst, kst_date_str):
    setattr(dart_stub, f.__name__, f)
sys.modules["dart"] = dart_stub

notif = types.ModuleType("notifier")
async def send_system_message(chat_id, text):
    if state["forward_fails"]:
        raise RuntimeError("telegram down")
    state["operator_msgs"].append((chat_id, text))
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
from models import Feedback, User  # noqa: E402
from services import user_service  # noqa: E402
import bot  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeChat:
    def __init__(self, chat_id, first_name=""):
        self.id = chat_id
        self.first_name = first_name


class FakeUpdate:
    def __init__(self, chat_id, first_name=""):
        self.effective_chat = FakeChat(chat_id, first_name)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, args):
        self.args = args


async def feedback_rows(chat_id):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Feedback).where(Feedback.chat_id == chat_id))
        return result.scalars().all()


async def main():
    await init_db()

    # ── 빈 입력: 사용법 안내, 저장 없음 ──────────────────────────────
    u = FakeUpdate("g1", "건수")
    await bot.feedback(u, FakeContext([]))
    check("빈 입력 → 사용법 안내", "예)" in u.message.replies[0], True)
    check("빈 입력 → 놓친 공시 우선 안내", "안 온 경우" in u.message.replies[0], True)
    check("빈 입력은 저장하지 않음", len(await feedback_rows("g1")), 0)

    # ── 정상 접수: 저장 + 운영자 전달 + 사용자 확인 ─────────────────
    await bot.feedback(u, FakeContext(["삼성전자", "CB", "알림이", "안", "왔어요"]))
    rows = await feedback_rows("g1")
    check("피드백 DB 저장", [r.text for r in rows], ["삼성전자 CB 알림이 안 왔어요"])
    check("운영자에게 즉시 전달", len(state["operator_msgs"]), 1)
    op_chat, op_text = state["operator_msgs"][0]
    check("전달 대상은 운영자 채팅", op_chat, "op-chat")
    check("전달문에 발신자·내용 포함",
          "건수(g1)" in op_text and "알림이 안 왔어요" in op_text, True)
    check("사용자에게 접수 확인", "접수" in u.message.replies[-1], True)

    # ── 미등록 사용자: 계정 자동 생성(FK 보장) ──────────────────────
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == "g1"))
        check("미등록 사용자 자동 생성", result.scalar_one_or_none() is not None, True)

    # ── 전달 실패 ≠ 접수 실패 ───────────────────────────────────────
    state["forward_fails"] = True
    u2 = FakeUpdate("g2")
    await bot.feedback(u2, FakeContext(["요약", "숫자가", "틀렸어요"]))
    check("운영자 전달 실패에도 저장됨", len(await feedback_rows("g2")), 1)
    check("운영자 전달 실패에도 접수 확인", "접수" in u2.message.replies[-1], True)
    state["forward_fails"] = False

    # ── /deletedata의 전체 삭제 약속에 피드백 포함 ──────────────────
    counts = await user_service.delete_user_data("g1")
    check("삭제 집계에 피드백 포함", counts["feedback"], 1)
    check("삭제 후 피드백 없음", len(await feedback_rows("g1")), 0)
    check("다른 사용자 피드백은 유지", len(await feedback_rows("g2")), 1)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
