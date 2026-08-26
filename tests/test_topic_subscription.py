"""유형 구독(/topic) 검증 — 첫 사용자 요청에서 나온 기능 (2026-08-26).

"모든 기업들의 단일판매/공급계약체결 공시만 실시간으로 받을 수도 있나요"
워치리스트가 '어느 기업'이라면 토픽은 '어떤 유형'이다. 확인할 성질:
  - 구독자는 등록하지 않은 기업의 공시도 받는다
  - 구독하지 않은 사람에게는 가지 않는다 (하루 18건은 원치 않는 이에겐 소음)
  - 워치리스트로 받는 사람과 구독으로 받는 사람의 머리말이 다르다
  - 둘 다 해당해도 한 번만 간다
  - /deletedata가 구독도 지운다
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "topic.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = "operator-chat"
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

state = {"disclosures": [], "sent": []}

dart_stub = types.ModuleType("dart")
async def fetch_recent_disclosures(days=1): return list(state["disclosures"])
async def save_disclosures_to_db(d): pass
async def fetch_rcept_times(date): return {}
async def fetch_disclosures_range(bgn_de, end_de):
    return [d for d in state["disclosures"] if bgn_de <= d["rcept_dt"] <= end_de]
async def fetch_today_disclosures_from_db(important_only=False):
    items = list(state["disclosures"])
    return [d for d in items if is_important(d["report_nm"])] if important_only else items
async def fetch_disclosure_detail(r): return "본문"
async def fetch_typed_disclosure(c, r, n, d): return {}
def is_after_hours(t): return False
def today_kst(): return "20260826"
def kst_date_str(days_ago=0): return "20260825" if days_ago else "20260826"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_rcept_times,
          fetch_disclosures_range, fetch_today_disclosures_from_db, fetch_disclosure_detail,
          fetch_typed_disclosure, is_after_hours, today_kst, kst_date_str):
    setattr(dart_stub, f.__name__, f)
from config import IMPORTANT_REPORT_TYPES  # noqa: E402
def is_important(nm): return any(k in nm for k in IMPORTANT_REPORT_TYPES)
dart_stub.is_important = is_important
sys.modules["dart"] = dart_stub

summ = types.ModuleType("summarizer")
async def summarize_disclosure(c, n, content, bypass_budget=False): return "요약"
async def summarize_typed_disclosure(c, n, d, bypass_budget=False): return "카드"
summ.summarize_disclosure = summarize_disclosure
summ.summarize_typed_disclosure = summarize_typed_disclosure
sys.modules["summarizer"] = summ

notif = types.ModuleType("notifier")
async def send_alert(chat_id, corp_name, report_nm, receipt_no, summary, tier="important"):
    state["sent"].append((receipt_no, chat_id, tier))
    return True
async def send_system_message(chat_id, text): pass
async def send_html_message(chat_id, html_text): return True
def escape_html(t): return t
notif.send_alert = send_alert
notif.send_system_message = send_system_message
notif.send_html_message = send_html_message
notif.escape_html = escape_html
sys.modules["notifier"] = notif

from database import AsyncSessionLocal, init_db  # noqa: E402
from models import TopicSubscription, User, Watchlist  # noqa: E402
from services import subscription_service, user_service  # noqa: E402
from topics import TOPICS, match_topic  # noqa: E402
import tasks  # noqa: E402

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


def sent_to(chat_id):
    return [(r, t) for r, c, t in state["sent"] if c == chat_id]


async def main():
    await init_db()

    # ── 파트 1: 보고서명 매칭 (2026-08-26 실측 변형 전부) ────────────
    for nm in ["단일판매ㆍ공급계약체결",
               "[기재정정]단일판매ㆍ공급계약체결",
               "단일판매ㆍ공급계약체결(자율공시)",
               "[기재정정]단일판매ㆍ공급계약체결(자율공시)",
               "단일판매ㆍ공급계약체결(자회사의 주요경영사항)",
               "[기재정정]단일판매ㆍ공급계약해지"]:
        check(f"매칭: {nm[:24]}", match_topic(nm), "공급계약")
    check("무관한 공시는 매칭 안 됨", match_topic("주요사항보고서(유상증자결정)"), None)
    check("무관한 공시2", match_topic("감사보고서제출"), None)

    # ── 파트 2: 구독 토글 ────────────────────────────────────────────
    check("처음엔 구독 없음", await subscription_service.list_topics("geonsoo"), set())
    check("구독 켜기", await subscription_service.toggle_topic("geonsoo", "건수", "공급계약"), True)
    check("구독 목록 반영", await subscription_service.list_topics("geonsoo"), {"공급계약"})
    check("미등록자도 계정 자동 생성", (await user_service.get_user("geonsoo")) is not None, True)
    check("다시 누르면 해제", await subscription_service.toggle_topic("geonsoo", "건수", "공급계약"), False)
    check("해제 반영", await subscription_service.list_topics("geonsoo"), set())
    check("없는 토픽은 거부", await subscription_service.toggle_topic("geonsoo", "건수", "없는토픽"), False)
    await subscription_service.toggle_topic("geonsoo", "건수", "공급계약")  # 다시 구독

    # ── 파트 3: 파이프라인 ───────────────────────────────────────────
    # noona: 구독 안 함, 가나전자만 워치리스트
    # geonsoo: 구독함, 워치리스트 없음
    # both: 구독 + 가나전자 워치리스트
    async with AsyncSessionLocal() as s:
        for cid, name in [("noona", "누나"), ("both", "둘다")]:
            s.add(User(chat_id=cid, first_name=name, is_active=True))
        await s.flush()
        for cid in ("noona", "both"):
            s.add(Watchlist(id=f"w-{cid}", chat_id=cid, corp_code="C1", corp_name="가나전자"))
        s.add(TopicSubscription(chat_id="both", topic="공급계약"))
        await s.commit()

    state["disclosures"] = [
        # 워치리스트 밖 기업의 공급계약 — 구독자만 받아야 한다
        {"rcept_no": "T1", "corp_name": "머나먼전자", "report_nm": "단일판매ㆍ공급계약체결",
         "corp_code": "C9", "rcept_dt": "20260826", "corp_cls": "K"},
        # 워치리스트 기업의 공급계약 — 워치리스트+구독 양쪽 해당
        {"rcept_no": "T2", "corp_name": "가나전자", "report_nm": "[기재정정]단일판매ㆍ공급계약체결",
         "corp_code": "C1", "rcept_dt": "20260826", "corp_cls": "Y"},
        # 공급계약이 아닌 워치리스트 공시 — 구독과 무관
        {"rcept_no": "T3", "corp_name": "가나전자", "report_nm": "주요사항보고서(유상증자결정)",
         "corp_code": "C1", "rcept_dt": "20260826", "corp_cls": "Y"},
    ]
    state["sent"].clear()
    await tasks.process_disclosures()

    check("구독자는 등록 안 한 기업 공시도 받음", sent_to("geonsoo"), [("T1", "topic"), ("T2", "topic")])
    check("미구독자는 워치리스트 밖 공급계약 안 받음",
          [r for r, _ in sent_to("noona")], ["T2", "T3"])
    check("미구독자의 워치리스트 건은 important 머리말",
          sorted(t for _, t in sent_to("noona")), ["important", "important"])
    both = sent_to("both")
    check("둘 다 해당해도 T2는 한 번만", [r for r, _ in both].count("T2"), 1)
    check("워치리스트 사용자에겐 important 우선", dict(both)["T2"], "important")
    check("구독으로만 오는 건 topic", dict(both)["T1"], "topic")

    # 재폴링 시 중복 발송 없음
    state["sent"].clear()
    await tasks.process_disclosures()
    check("재폴링 중복 발송 없음", state["sent"], [])

    # ── 파트 4: 삭제 ─────────────────────────────────────────────────
    counts = await user_service.delete_user_data("geonsoo")
    check("삭제 집계에 구독 포함", counts["topics"], 1)
    check("삭제 후 구독 없음", await subscription_service.list_topics("geonsoo"), set())

    # ── 파트 5: 봇 화면 ──────────────────────────────────────────────
    import bot
    check("토픽 키보드에 등록된 유형 수", len(bot._topic_keyboard(set()).inline_keyboard), len(TOPICS))
    on_text = bot._topic_text({"공급계약"})
    check("구독 중 표시", "✅ 구독 중" in on_text, True)
    check("볼륨을 미리 알려줌", "18건" in on_text, True)
    off_text = bot._topic_text(set())
    check("미구독 표시", "⬜ 미구독" in off_text, True)


    # ── 파트 6: 그날 나온 토픽 공시 몰아보기 ────────────────────────
    # 구독은 실시간 수신, 조회는 /market 검색어. 사용자 질문:
    # "토픽 등록하고 그날 시장에서 그 토픽 검색하려면?"
    from services import disclosure_service as ds
    r = await ds.query_disclosures(scope="market", important_only=True, query="공급계약")
    check("'/market 공급계약'이 그날 공급계약만 뽑음",
          sorted(d["rcept_no"] for d in r.items), ["T1", "T2"])
    check("헤더에 검색어 표기", "'공급계약'" in r.header(), True)
    r2 = await ds.query_disclosures(scope="market", important_only=True, query="단일판매")
    check("'단일판매'로도 동일하게 조회", sorted(d["rcept_no"] for d in r2.items), ["T1", "T2"])
    r3 = await ds.query_disclosures(scope="market", important_only=True,
                                    query="공급계약", date="20260825")
    check("날짜 조합 조회도 토픽 검색어와 함께 동작", r3.date, "20260825")


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
