"""LLM 프로바이더 감시 검증 — 잔액 조회 API가 없으므로 실호출 감시가 유일한 수단.

  - 오류 분류: billing/auth(사람이 고칠 것, 경보)와 other(일시, 폴백이 처리) 구분
  - 1일 1회 경보: 같은 날 중복 경보 없음, 'other'는 경보 없음
  - 캐너리 집계: provider_status가 /health 노출용 형태로 채워지는지
  - 전멸 경보: 3사 모두 실패 시에만 운영자 추가 경보
"""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile
import types

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "watchdog.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = "operator-chat"
os.environ["LLM_ALERT_MUTE"] = ""  # 호스트 .env의 음소거 설정이 새지 않게 고정
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

state = {"system_msgs": []}

dart_stub = types.ModuleType("dart")
async def fetch_recent_disclosures(days=1): return []
async def save_disclosures_to_db(d): pass
async def fetch_rcept_times(date): return {}
async def fetch_disclosure_detail(r): return ""
async def fetch_typed_disclosure(c, r, n, d): return {}
def is_important(nm): return False
def is_after_hours(t): return False
def today_kst(): return "20260818"
def kst_date_str(days_ago=0): return "20260817" if days_ago else "20260818"
for f in (fetch_recent_disclosures, save_disclosures_to_db, fetch_rcept_times,
          fetch_disclosure_detail, fetch_typed_disclosure, is_important,
          is_after_hours, today_kst, kst_date_str):
    setattr(dart_stub, f.__name__, f)
sys.modules["dart"] = dart_stub

notif = types.ModuleType("notifier")
async def send_alert(chat_id, corp_name, report_nm, receipt_no, summary, tier="important"):
    return True
async def send_system_message(chat_id, text):
    state["system_msgs"].append(text)
async def send_html_message(chat_id, html_text): return True
def escape_html(t): return t
notif.send_alert = send_alert
notif.send_system_message = send_system_message
notif.send_html_message = send_html_message
notif.escape_html = escape_html
sys.modules["notifier"] = notif

import summarizer  # noqa: E402  (실제 모듈 — 감시 로직이 검증 대상)
import tasks  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


async def main():
    # ── 파트 1: 오류 분류 (프로덕션 실측 오류 문자열 그대로) ──────────
    c = summarizer._classify_llm_error
    check("OpenAI 잔액 소진 → billing",
          c("Error code: 429 - insufficient_quota: You have no credits remaining"), "billing")
    check("Claude 잔액 부족 → billing",
          c("Your credit balance is too low to access the Anthropic API"), "billing")
    check("텔레그램식 토큰 거부 → auth",
          c("The token `123:ABC` was rejected by the server"), "auth")
    check("401 → auth", c("Error code: 401 - Unauthorized"), "auth")
    check("타임아웃 → other(폴백이 처리)", c("Request timed out"), "other")
    check("모델 404 → other", c("404 models/x is not found for API version v1beta"), "other")

    # ── 파트 2: 1일 1회 경보 ─────────────────────────────────────────
    state["system_msgs"].clear()
    summarizer._provider_alert_date.clear()
    await summarizer._alert_provider_issue("openai", "insufficient_quota: no credits")
    check("billing 오류 → 운영자 경보 1건", len(state["system_msgs"]), 1)
    check("경보에 프로바이더·원인 명시",
          "openai" in state["system_msgs"][0] and "잔액" in state["system_msgs"][0], True)
    await summarizer._alert_provider_issue("openai", "insufficient_quota: no credits")
    check("같은 날 중복 경보 없음", len(state["system_msgs"]), 1)
    await summarizer._alert_provider_issue("gemini", "Request timed out")
    check("'other' 오류는 경보 없음", len(state["system_msgs"]), 1)
    await summarizer._alert_provider_issue("claude", "credit balance is too low")
    check("다른 프로바이더는 별도 경보", len(state["system_msgs"]), 2)

    # ── 파트 2b: 음소거 — 의도적으로 방치한 프로바이더의 경보 소음 차단 ──
    state["system_msgs"].clear()
    summarizer._provider_alert_date.clear()
    os.environ["LLM_ALERT_MUTE"] = "claude"
    await summarizer._alert_provider_issue("claude", "credit balance is too low")
    check("음소거된 프로바이더는 경보 없음", len(state["system_msgs"]), 0)
    await summarizer._alert_provider_issue("openai", "insufficient_quota: no credits")
    check("음소거는 해당 프로바이더만 적용", len(state["system_msgs"]), 1)
    os.environ["LLM_ALERT_MUTE"] = ""
    summarizer._provider_alert_date.clear()
    await summarizer._alert_provider_issue("claude", "credit balance is too low")
    check("음소거 해제 시 경보 재개", len(state["system_msgs"]), 2)

    # ── 파트 3: 캐너리 집계 ──────────────────────────────────────────
    summarizer.provider_status.clear()
    async def ok_openai(prompt):
        summarizer._record_provider("openai", True)
        return "ok"
    async def dead_claude(prompt):
        summarizer._record_provider("claude", False, "credit balance is too low")
        return None
    async def silent_gemini(prompt):
        return None  # 상태 기록 없이 실패 → setdefault 경로
    summarizer.summarize_with_openai = ok_openai
    summarizer.summarize_with_claude = dead_claude
    summarizer.summarize_with_gemini = silent_gemini

    results = await summarizer.check_llm_providers()
    check("openai 정상 기록", results["openai"]["ok"], True)
    check("claude 실패+원인 기록", (results["claude"]["ok"], "credit" in results["claude"]["error"]),
          (False, True))
    check("무기록 실패도 상태 남김", results["gemini"]["ok"], False)

    # ── 파트 4: 캐너리 잡 — 부분 실패 vs 전멸 ────────────────────────
    state["system_msgs"].clear()
    async def partial(): return {"openai": {"ok": True, "error": None},
                                 "claude": {"ok": False, "error": "credit"},
                                 "gemini": {"ok": True, "error": None}}
    summarizer.check_llm_providers = partial
    await tasks.run_llm_canary()
    check("부분 실패 → 전멸 경보 없음(개별 경보는 summarizer 담당)",
          len(state["system_msgs"]), 0)
    check("/health용 상태 저장", tasks.poll_status["llm_providers"]["claude"]["ok"], False)
    check("캐너리 시각 기록", bool(tasks.poll_status.get("llm_canary_at")), True)

    async def all_dead(): return {"openai": {"ok": False, "error": "quota"},
                                  "claude": {"ok": False, "error": "credit"},
                                  "gemini": {"ok": False, "error": "404 model"}}
    summarizer.check_llm_providers = all_dead
    os.environ["LLM_ALERT_MUTE"] = "claude"  # 음소거는 개별 경보만 — 전멸 경보는 무조건
    await tasks.run_llm_canary()
    os.environ["LLM_ALERT_MUTE"] = ""
    check("전멸 → 요약 완전 중단 경보 1건 (음소거 무관)", len(state["system_msgs"]), 1)
    check("전멸 경보에 원인 나열", "quota" in state["system_msgs"][0], True)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
