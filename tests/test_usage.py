"""이용 기록 검증 — 평가와 품질 개선에 쓸 수 있는 것이 실제로 남는가.

2026-09-18 설계 교체: 첫 버전(#82)은 동사만 세고 기업·공시번호·검색어를 버렸다.
사용자가 늘어도 '어떤 검색어가 헛도는지', '사용자가 본 요약이 맞았는지'를 알 수
없는 설계였다 — 둘 다 목적어 쪽에 있다. 행 단위 로그로 바꾸고 이걸 고정한다.

확인할 성질:
  - 검색어·공시번호·기업코드가 인자에서 추출된다
  - 조회 결과 수가 같은 행에 붙는다 — 0건 검색이 드러나야 한다
  - 요약 열람은 그때 보여준 요약과 생성 경로(원문 길이 포함)를 남긴다
  - 운영자 행동은 기록하되 플래그로 구분된다(사업 지표에서 빠진다)
  - /deletedata가 이용 기록까지 지운다
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
def build_disclosure_message(*a): return "msg"
for name in ("send_system_message", "send_alert", "send_html_message",
             "escape_html", "build_disclosure_message"):
    setattr(notif, name, locals()[name])
sys.modules["notifier"] = notif

from sqlalchemy import select  # noqa: E402
from database import AsyncSessionLocal, init_db  # noqa: E402
from models import UsageEvent, User  # noqa: E402
from services import disclosure_service, stats_service, usage_service, user_service  # noqa: E402
import bot  # noqa: E402

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


_next_uid = [1000]


class FakeMessage:
    def __init__(self, text=None):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeQuery:
    def __init__(self, data, chat_id):
        self.data = data
        self.from_user = type("U", (), {"id": chat_id})()
        self.message = FakeMessage()

    async def answer(self, *a, **k):
        pass


class FakeUpdate:
    def __init__(self, chat_id="g2", text=None, data=None):
        _next_uid[0] += 1
        self.update_id = _next_uid[0]
        self.effective_chat = type("C", (), {"id": chat_id, "first_name": ""})()
        self.message = FakeMessage(text) if text is not None else None
        self.callback_query = FakeQuery(data, chat_id) if data is not None else None


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []


async def events(**where):
    async with AsyncSessionLocal() as s:
        q = select(UsageEvent)
        for k, v in where.items():
            q = q.where(getattr(UsageEvent, k) == v)
        return (await s.execute(q.order_by(UsageEvent.created_at))).scalars().all()


class FakeResult:
    """disclosure_service.QueryResult와 같은 모양."""
    def __init__(self, items, query, before):
        self.items, self.query, self.total_before_query = items, query, before
        self.scope, self.date = "market", None

    @property
    def filtered_to_empty(self):
        return not self.items and self.total_before_query > 0

    def date_label(self):
        return "오늘"

    def header(self):
        return "헤더"


async def main():
    await init_db()

    # ── 인자 추출 ────────────────────────────────────────────────────
    parse = usage_service.parse
    check("검색어 추출", parse(FakeUpdate(text="/market 카카오 유상증자")),
          {"event": "market", "query": "카카오 유상증자"})
    check("공시번호 추출", parse(FakeUpdate(data="view:20260918000123")),
          {"event": "view", "rcept_no": "20260918000123"})
    check("기업코드 추출", parse(FakeUpdate(data="toggle:00126380")),
          {"event": "toggle", "corp_code": "00126380"})
    check("해제는 코드와 이름", parse(FakeUpdate(data="remove:00126380:삼성전자")),
          {"event": "remove", "corp_code": "00126380", "corp_name": "삼성전자"})
    check("페이지 오프셋", parse(FakeUpdate(data="page:240"))["detail"], {"offset": 240})
    check("토픽", parse(FakeUpdate(data="topic:공급계약"))["detail"], {"topic": "공급계약"})
    check("인자 없는 명령", parse(FakeUpdate(text="/my")), {"event": "my"})
    check("봇 멘션 제거", parse(FakeUpdate(text="/start@forg_alert_bot"))["event"], "start")
    check("일반 대화는 기록 안 함", parse(FakeUpdate(text="안녕")), None)

    # ── 훅: 한 행, 운영자는 플래그 ───────────────────────────────────
    await bot._record_usage(FakeUpdate(chat_id="g2", text="/market 카카오"), None)
    await bot._record_usage(FakeUpdate(chat_id="op-chat", text="/market 테스트"), None)
    rows = await events(event="market")
    check("행 단위로 쌓인다", len(rows), 2)
    check("사용자 행은 운영자 아님", [r.is_operator for r in rows if r.chat_id == "g2"], [False])
    check("운영자 행은 플래그", [r.is_operator for r in rows if r.chat_id == "op-chat"], [True])
    check("chat_id 저장", rows[0].chat_id, "g2")

    # ── 조회: 결과 수가 같은 행에 붙는다 ─────────────────────────────
    u = FakeUpdate(chat_id="g2", text="/market 한미반도체")
    await bot._record_usage(u, None)
    u.message = FakeMessage("/market 한미반도체")
    await bot._send_query_result(u, FakeResult([], "한미반도체", before=259))
    row = (await events(query="한미반도체"))[0]
    check("0건 검색의 결과 수", row.result_count, 0)
    check("검색어 없을 땐 있었다는 것도 남김", row.detail["before_query"], 259)
    check("'검색어가 다 걸러냈다' 표시", row.detail["filtered_to_empty"], True)

    u = FakeUpdate(chat_id="g2", text="/market 공급계약")
    await bot._record_usage(u, None)
    items = [{"rcept_no": f"R{i}", "corp_name": "X", "report_nm": "Y"} for i in range(12)]
    await bot._send_query_result(u, FakeResult(items, "공급계약", before=259))
    check("결과 있는 검색의 결과 수", (await events(query="공급계약"))[0].result_count, 12)

    # ── 요약 열람: 그때 보여준 요약과 경로 ───────────────────────────
    async def fake_summary(receipt_no, hint=None):
        return {"corp_name": "SK하이닉스", "report_nm": "단일판매ㆍ공급계약체결",
                "summary": "[단일판매·공급계약체결]\n• 계약금액: 1원", "dart_url": "",
                "resolved": None, "path": "raw", "source_len": 0}
    orig = disclosure_service.summarize_by_receipt
    disclosure_service.summarize_by_receipt = fake_summary
    try:
        bot.disclosure_cache["20260918000123"] = {"corp_name": "SK하이닉스", "corp_code": "00164779"}
        u = FakeUpdate(chat_id="g2", data="view:20260918000123")
        await bot._record_usage(u, None)
        await bot.view_disclosure_callback(u, None)
    finally:
        disclosure_service.summarize_by_receipt = orig
    row = (await events(event="view"))[0]
    check("열람 공시번호", row.rcept_no, "20260918000123")
    check("열람 기업명", row.corp_name, "SK하이닉스")
    check("열람 기업코드", row.corp_code, "00164779")
    check("그때 보여준 요약 원문", row.detail["summary"].startswith("[단일판매"))
    check("생성 경로", row.detail["path"], "raw")
    check("원문 길이(0 = 빈 원문)", row.detail["source_len"], 0)

    # ── 보강은 훅이 넣은 detail을 지우지 않는다 ──────────────────────
    u = FakeUpdate(chat_id="g2", data="page:20")
    await bot._record_usage(u, None)
    await usage_service.enrich(u, detail={"extra": 1})
    check("detail 병합", (await events(event="page"))[0].detail, {"offset": 20, "extra": 1})

    # ── 실패해도 사용자 요청은 계속 ──────────────────────────────────
    orig_record = usage_service.record
    async def boom(*a, **k):
        raise RuntimeError("db down")
    usage_service.record = boom
    try:
        await bot._record_usage(FakeUpdate(text="/my"), None)
        check("기록 실패를 삼킨다", True)
    except Exception as e:
        check("기록 실패를 삼킨다", f"예외 누출: {e}", True)
    finally:
        usage_service.record = orig_record
    try:
        await usage_service.enrich(FakeUpdate(text="/my"), result_count=1)   # 대응 행 없음
        check("대응 행 없는 보강도 조용히", True)
    except Exception as e:
        check("대응 행 없는 보강도 조용히", f"예외 누출: {e}", True)

    # 실제로 났던 버그: 보강 인자를 만들며 결과 객체 속성을 보호 구간 밖에서 읽어,
    # scope가 없는 결과가 오자 사용자가 조회 결과를 통째로 못 받았다.
    class Bare:
        items = []
    try:
        await bot._record_query_result(FakeUpdate(text="/my"), Bare())
        check("속성 빠진 결과도 조회를 깨뜨리지 않음", True)
    except Exception as e:
        check("속성 빠진 결과도 조회를 깨뜨리지 않음", f"예외 누출: {e}", True)

    # ── /stats: 운영자 제외, 0건 검색·품질 경보 표시 ────────────────
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id="g2", first_name="건수"))
        s.add(User(chat_id="op-chat", first_name="누나"))
        await s.commit()
    data = await stats_service.collect_stats()
    usage = data["usage"]
    check("사업 지표는 운영자 제외(이용자 수)", usage["users"], 1)
    check("운영자 검색어는 집계에 없음",
          [x["query"] for x in usage["searches"] if x["query"] == "테스트"], [])
    zero = [x["query"] for x in usage["searches"] if x["zero"]]
    check("0건 검색어가 드러난다", zero, ["한미반도체"])
    check("원문 없이 나간 요약이 경보로", usage["empty_source"], ["20260918000123"])
    text = stats_service.format_stats(data)
    check("0건 경고 표시", "⚠️ 0건: 한미반도체" in text)
    check("품질 경보 표시", "원문 없이 요약 1건: 20260918000123" in text)
    check("많이 연 공시 표시", "SK하이닉스 단일판매ㆍ공급계약체결 ×1" in text)
    check("검색어 평균 결과", "공급계약 ×1 · 12건" in text)

    # ── /deletedata가 이용 기록까지 지운다 ───────────────────────────
    before = len(await events(chat_id="g2"))
    counts = await user_service.delete_user_data("g2")
    check("삭제 대상에 이용 기록 포함", counts["usage"], before)
    check("삭제 후 남은 이용 기록 없음", len(await events(chat_id="g2")), 0)
    check("다른 사용자의 기록은 남음", len(await events(chat_id="op-chat")) > 0)

    if failures:
        print(f"\n{len(failures)}건 실패: {failures}")
        sys.exit(1)
    print("\n전부 통과")


asyncio.run(main())
