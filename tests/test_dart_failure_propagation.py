"""DART 장애 전파 검증 (2026-08-19 리뷰 P1-2).

장애가 빈 결과로 위장되면 안 된다. 확인할 성질:
  - dart 계층: 013만 정상 빈 결과. 비정상 status는 DartApiError,
    HTTP 오류는 원 예외 전파. 페이지네이션 중간 실패는 부분 결과 반환 금지
  - 파이프라인: 수집 실패 시 last_result=error, fail_streak 증가,
    last_success_at 미갱신 (기존엔 성공으로 기록돼 자가 경보가 침묵)
  - 봇: 조회 실패를 "공시 없음"이 아니라 오류로 안내
"""
import asyncio
import os
import pathlib
import sys

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = ""  # 운영자 경보 발송 차단 (임계 도달 시에도)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
sys.path.insert(0, APP)

import httpx  # noqa: E402

import dart  # noqa: E402  (실제 모듈 — status 분기가 검증 대상)

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


# ── 가짜 httpx: DART 실응답 형태를 페이지 순서대로 재생 ──────────────
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, timeout=None):
        return self._responses.pop(0)


def use_responses(responses):
    dart.httpx.AsyncClient = lambda *a, **k: FakeClient(list(responses))


async def main():
    # ── 파트 1: dart 계층 status 분기 ────────────────────────────────
    item = {"rcept_no": "R1", "corp_name": "가나전자", "report_nm": "유상증자결정",
            "corp_code": "C1", "rcept_dt": "20260819"}
    use_responses([FakeResponse({"status": "000", "list": [item], "total_page": 1})])
    rows = await dart.fetch_disclosures_range("20260819", "20260819")
    check("000 정상 수집", [r["rcept_no"] for r in rows], ["R1"])

    use_responses([FakeResponse({"status": "013", "message": "조회된 데이타가 없습니다."})])
    rows = await dart.fetch_disclosures_range("20260819", "20260819")
    check("013은 정상적인 빈 결과", rows, [])

    use_responses([FakeResponse({"status": "010", "message": "등록되지 않은 인증키입니다."})])
    try:
        await dart.fetch_disclosures_range("20260819", "20260819")
        check("키 오류(010)는 예외", "예외 없음", "DartApiError")
    except dart.DartApiError as e:
        check("키 오류(010)는 예외", "010" in str(e), True)

    use_responses([FakeResponse({"status": "020", "message": "요청 제한을 초과하였습니다."})])
    try:
        await dart.fetch_disclosures_range("20260819", "20260819")
        check("쿼터 초과(020)는 예외", "예외 없음", "DartApiError")
    except dart.DartApiError:
        check("쿼터 초과(020)는 예외", True, True)

    use_responses([FakeResponse({}, status_code=500)])
    try:
        await dart.fetch_disclosures_range("20260819", "20260819")
        check("HTTP 500은 예외 전파", "예외 없음", "HTTPStatusError")
    except httpx.HTTPStatusError:
        check("HTTP 500은 예외 전파", True, True)

    # 페이지네이션 중간 실패 → 부분 결과 반환 금지
    use_responses([
        FakeResponse({"status": "000", "list": [item], "total_page": 2}),
        FakeResponse({"status": "800", "message": "시스템 점검 중입니다."}),
    ])
    try:
        await dart.fetch_disclosures_range("20260819", "20260819")
        check("중간 페이지 실패는 부분 결과 대신 예외", "예외 없음", "DartApiError")
    except dart.DartApiError:
        check("중간 페이지 실패는 부분 결과 대신 예외", True, True)

    # ── 파트 2: 파이프라인 — 실패가 성공으로 기록되지 않음 ──────────
    import tasks

    async def boom(days=1):
        raise dart.DartApiError("DART status 010: 등록되지 않은 인증키입니다.")
    tasks.fetch_recent_disclosures = boom

    tasks.poll_status.update(
        {"last_result": None, "last_success_at": None, "fail_streak": 0}
    )
    await tasks.process_disclosures()
    check("수집 실패 → last_result=error", tasks.poll_status["last_result"], "error")
    check("수집 실패 → last_success_at 미갱신", tasks.poll_status["last_success_at"], None)
    check("수집 실패 → fail_streak 증가", tasks.poll_status["fail_streak"], 1)
    check("오류 내용 보존", "010" in (tasks.poll_status["last_error"] or ""), True)

    async def empty_ok(days=1):
        return []
    tasks.fetch_recent_disclosures = empty_ok
    await tasks.process_disclosures()
    check("정상 빈 결과 → empty로 회복", tasks.poll_status["last_result"], "empty")
    check("정상 빈 결과 → fail_streak 리셋", tasks.poll_status["fail_streak"], 0)
    check("정상 빈 결과 → last_success_at 갱신",
          tasks.poll_status["last_success_at"] is not None, True)

    # ── 파트 3: 봇 — 장애를 "공시 없음"으로 위장하지 않음 ───────────
    import bot
    from services import disclosure_service, watchlist_service

    class FakeMessage:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text, **kw):
            self.replies.append(text)

    class FakeUpdate:
        def __init__(self):
            self.effective_chat = type("C", (), {"id": "u1", "first_name": ""})()
            self.message = FakeMessage()

    async def codes(chat_id):
        return {"C1"}
    watchlist_service.get_corp_codes = codes

    async def q_boom(**kwargs):
        raise dart.DartApiError("DART status 800: 시스템 점검 중입니다.")
    disclosure_service.query_disclosures = q_boom

    u = FakeUpdate()
    await bot.today(u, type("Ctx", (), {"args": []})())
    check("/today 장애 시 오류 안내", "문제가 있습니다" in u.message.replies[-1], True)
    check("/today 장애 시 '공시 없음' 미출력",
          all("공시가 없습니다" not in r for r in u.message.replies), True)

    u2 = FakeUpdate()
    await bot.market(u2, type("Ctx", (), {"args": []})())
    check("/market 장애 시 오류 안내", "문제가 있습니다" in u2.message.replies[-1], True)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
