"""등급 알림 검증 — 분류 규칙 · 시장 전체 발송 · 긴급 우선/예산 우회 · 다이제스트.

2026-08-16 실측에서 나온 실제 공시명을 그대로 쓴다. 설계 원칙:
  1. 워치리스트 기업 공시는 버리지 않는다(등급만 나눔)
  2. 시장 등급 제외는 삭제가 아니라 강등(워치리스트 경로로는 전달)
  3. 긴급은 기본 ON — 워치리스트 없는 사용자도 받는다
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "tiers.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = "operator-chat"
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

state = {"disclosures": [], "sent": [], "digests": [], "bypass_seen": []}

dart_stub = types.ModuleType("dart")
async def fetch_recent_disclosures(days=1): return list(state["disclosures"])
async def save_disclosures_to_db(d): pass
async def fetch_rcept_times(date): return {}
async def fetch_disclosure_detail(r): return "본문"
async def fetch_typed_disclosure(c, r, n, d): return {}
def is_after_hours(t): return False
def today_kst(): return "20260816"
def kst_date_str(days_ago=0): return "20260815" if days_ago else "20260816"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_rcept_times,
          fetch_disclosure_detail, fetch_typed_disclosure,
          is_after_hours, today_kst, kst_date_str):
    setattr(dart_stub, f.__name__, f)
# is_important는 실제 config 기준을 쓴다 (커버리지 확장 검증 목적)
from config import IMPORTANT_REPORT_TYPES  # noqa: E402
def is_important(nm): return any(k in nm for k in IMPORTANT_REPORT_TYPES)
dart_stub.is_important = is_important
sys.modules["dart"] = dart_stub

summ = types.ModuleType("summarizer")
async def summarize_disclosure(c, n, content, bypass_budget=False):
    if bypass_budget: state["bypass_seen"].append(n)
    return "요약"
async def summarize_typed_disclosure(c, n, d, bypass_budget=False):
    if bypass_budget: state["bypass_seen"].append(n)
    return "카드"
summ.summarize_disclosure = summarize_disclosure
summ.summarize_typed_disclosure = summarize_typed_disclosure
sys.modules["summarizer"] = summ

notif = types.ModuleType("notifier")
async def send_alert(chat_id, corp_name, report_nm, receipt_no, summary, tier="important"):
    state["sent"].append((receipt_no, chat_id, tier))
    return True
async def send_system_message(chat_id, text): pass
async def send_html_message(chat_id, html_text):
    state["digests"].append((chat_id, html_text))
    return True
def escape_html(t): return t
notif.send_alert = send_alert
notif.send_system_message = send_system_message
notif.send_html_message = send_html_message
notif.escape_html = escape_html
sys.modules["notifier"] = notif

import tasks  # noqa: E402
from alert_tiers import classify_market_tier  # noqa: E402
from database import AsyncSessionLocal, init_db  # noqa: E402
from models import Disclosure, SeenDisclosure, User, Watchlist  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


def d(rcept, corp, name, cls="Y"):
    return {"rcept_no": rcept, "corp_name": f"{corp}사", "report_nm": name,
            "corp_code": corp, "rcept_dt": "20260816", "corp_cls": cls}


async def main():
    await init_db()
    tasks._is_business_hours_kst = lambda now=None: False

    # ── 파트 1: 분류 규칙 (실측 공시명) ─────────────────────────────
    check("회생절차 개시결정 → 긴급",
          classify_market_tier("[기재정정]회생절차개시결정", "K"), "urgent")
    check("횡령ㆍ배임(ㆍ=U+318D) → 긴급",
          classify_market_tier("횡령ㆍ배임혐의발생", "K"), "urgent")
    check("상장공시위원회 '상장폐지 결정'(공백) → 긴급",
          classify_market_tier("기타시장안내 (유가증권시장 상장공시위원회 개최 결과 및 상장폐지 결정)", "Y"),
          "urgent")
    check("정리매매절차 재개 → 긴급",
          classify_market_tier("기타시장안내 (상장폐지결정 등 효력정지 가처분 신청 기각에 따른 정리매매절차 재개)", "K"),
          "urgent")
    check("상장폐지 우려 예고 → 시장 공지",
          classify_market_tier("기타시장안내 (주권 상장폐지 우려 예고)", "K"), "notice")
    check("실질적 거래정지(공급계약) → 시장 공지",
          classify_market_tier("주권매매거래정지 (단일판매공급계약)", "K"), "notice")
    check("거래정지 '해제' → 시장 등급 아님(강등)",
          classify_market_tier("주권매매거래정지해제 (상장폐지에 따른 정리매매 개시)", "K"), None)
    check("기술적 정지(전자등록) → 시장 등급 아님",
          classify_market_tier("주권매매거래정지 (주식의 병합, 분할 등 전자등록 변경, 말소)", "K"), None)
    check("상장폐지 절차 '미진행' → 시장 등급 아님",
          classify_market_tier("기타시장안내 (정기보고서 미제출 관련 상장폐지 절차 미진행)", "Y"), None)
    check("코넥스(N)는 시장 등급 제외",
          classify_market_tier("회생절차개시결정", "N"), None)
    check("강등돼도 워치리스트 경로는 잡는다(원칙 2)",
          is_important("주권매매거래정지해제 (상장폐지에 따른 정리매매 개시)"), True)

    # ── 파트 2: 커버리지 확장 (87% 결함 수정) ────────────────────────
    check("5%룰(대량보유) → 중요",
          is_important("주식등의대량보유상황보고서(일반)"), True)
    check("내부자 매매 → 중요",
          is_important("임원ㆍ주요주주특정증권등소유상황보고서"), True)
    check("잠정실적 → 중요",
          is_important("연결재무제표기준영업(잠정)실적(공정공시)"), True)
    check("손익구조 변동 → 중요",
          is_important("매출액또는손익구조30%(대규모법인은15%)이상변경"), True)
    check("배당 결정 → 중요", is_important("현금ㆍ현물배당결정"), True)
    check("반기보고서 → 참고(즉시 알림 아님)", is_important("반기보고서 (2026.06)"), False)
    check("IR 개최 → 참고", is_important("기업설명회(IR)개최(안내공시)"), False)

    # ── 파트 3: 파이프라인 — 시장 전체 발송·긴급 우선·예산 우회 ────────
    async with AsyncSessionLocal() as s:
        s.add(User(chat_id="U1", first_name="워치유저"))
        s.add(User(chat_id="U2", first_name="빈손유저"))  # 워치리스트 없음
        await s.flush()
        s.add(Watchlist(id="w1", chat_id="U1", corp_code="C1", corp_name="C1사"))
        await s.commit()

    state["disclosures"] = [
        d("R-IMP", "C1", "유상증자결정"),               # 워치리스트 중요 (U1만)
        d("R-URG", "C9", "회생절차개시결정"),            # 긴급 (전원, C9는 누구의 워치도 아님)
        d("R-REF", "C1", "반기보고서 (2026.06)"),        # 참고 (즉시 발송 없음)
        d("R-NTC", "C8", "주권매매거래정지 (영업양수도)"),  # 시장 공지 (전원)
    ]
    await tasks.process_disclosures()

    sent = state["sent"]
    check("긴급은 워치리스트 없는 U2에게도 발송(기본 ON)",
          ("R-URG", "U2", "urgent") in sent, True)
    check("긴급은 U1에게도 발송", ("R-URG", "U1", "urgent") in sent, True)
    check("시장 공지도 전원 발송", ("R-NTC", "U2", "notice") in sent, True)
    check("워치리스트 중요는 U1만",
          ("R-IMP", "U1", "important") in sent and ("R-IMP", "U2", "important") not in sent, True)
    check("참고 등급은 즉시 발송 없음", any(r == "R-REF" for r, _, _ in sent), False)
    check("긴급이 사이클 맨 앞에서 처리(입력 2번째→발송 1번째)",
          sent[0][0], "R-URG")
    check("긴급 요약은 LLM 예산 우회", "회생절차개시결정" in state["bypass_seen"], True)
    check("중복 발송 방지: 재폴링 시 추가 발송 없음",
          (len(state["sent"]), await tasks.process_disclosures())[0] == len(state["sent"]), True)

    # ── 파트 4: 다이제스트 ───────────────────────────────────────────
    async with AsyncSessionLocal() as s:
        s.add(Disclosure(rcept_no="R-REF", corp_code="C1", corp_name="C1사",
                         corp_cls="Y", report_nm="반기보고서 (2026.06)", rcept_dt="20260816"))
        s.add(Disclosure(rcept_no="R-REF2", corp_code="C7", corp_name="C7사",
                         corp_cls="Y", report_nm="기업설명회(IR)개최(안내공시)", rcept_dt="20260816"))
        await s.commit()

    await tasks.send_daily_digest()
    check("다이제스트는 워치리스트 보유자(U1)에게만", len(state["digests"]), 1)
    check("다이제스트 수신자 U1", state["digests"][0][0], "U1")
    check("워치기업 참고 공시 포함", "반기보고서" in state["digests"][0][1], True)
    check("남의 기업(C7) 공시는 미포함", "기업설명회" in state["digests"][0][1], False)

    await tasks.send_daily_digest()
    check("다이제스트 중복 발송 없음(SeenDisclosure 기록)", len(state["digests"]), 1)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
