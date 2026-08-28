"""폴링이 DART 지연을 견디는지 (2026-08-29 장애).

무슨 일이 있었나: 2026-08-28 20:37 KST부터 폴링이 연속 67회 ReadTimeout으로
실패했다. DART가 죽은 게 아니라 **느려졌다** — list.json이 한국에서 7.8~9.6초,
Railway US West 컨테이너에서 13.2초. 65바이트짜리 빈 응답(013)도 7.8초가
걸렸으니 전송량이 아니라 서버 지연이다. 우리 타임아웃은 10초였다.

그리고 타임아웃만 늘려도 낫지 않는다는 게 같이 드러났다: 바쁜 날은 2일 창에
2,145건 = 22페이지고, 한 페이지가 ~10초라 전체 순회에 195초가 걸린다.
폴링 주기는 60초다 — 애초에 완주가 불가능한 구조였다.

여기서 고정하는 성질:
  - 타임아웃이 실측 지연(13초)보다 넉넉하다
  - 일시적 타임아웃은 재시도하고, 계속 실패하면 예외로 올린다(빈 결과 위장 금지)
  - 아는 공시만 나오는 페이지에 닿으면 멈추되, 여유 한 페이지를 더 본다
  - 번호가 아니라 '새 항목 유무'로 끊는다 — rcept_no는 단조가 아니다
"""
import asyncio
import os
import pathlib
import sys

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
sys.path.insert(0, APP)

from datetime import datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
import dart  # noqa: E402

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class RecordingClient:
    """페이지를 요청한 만큼만 돌려주고, 몇 번 불렸는지 센다."""

    def __init__(self, pages, fail_times=0):
        self.pages = pages
        self.calls = []
        self.fail_times = fail_times
        self.timeouts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, timeout=None):
        if self.timeouts < self.fail_times:
            self.timeouts += 1
            raise httpx.ReadTimeout("timed out")
        page = params["page_no"]
        self.calls.append((page, timeout))
        return FakeResponse(self.pages[page - 1])


def install(client):
    dart.httpx.AsyncClient = lambda *a, **k: client
    return client


def page(nos, total_page):
    return {"status": "000", "total_page": total_page,
            "list": [{"rcept_no": n, "corp_name": "가", "report_nm": "유상증자결정",
                      "corp_code": "C", "rcept_dt": "20260828"} for n in nos]}


async def main():
    # ── 타임아웃 여유 ────────────────────────────────────────────────
    # 실측 최악값 13.2초(US West). 10초였기 때문에 장애가 났다.
    check("타임아웃이 실측 지연보다 넉넉함", dart.DART_TIMEOUT >= 30.0)

    c = install(RecordingClient([page(["A"], 1)]))
    await dart.fetch_disclosures_range("20260828", "20260828")
    check("설정된 타임아웃이 실제 호출에 전달됨", c.calls[0][1], dart.DART_TIMEOUT)

    # ── 일시적 타임아웃은 재시도 ────────────────────────────────────
    c = install(RecordingClient([page(["A"], 1)], fail_times=1))
    rows = await dart.fetch_disclosures_range("20260828", "20260828")
    check("한 번 타임아웃 나도 재시도해서 수집", [r["rcept_no"] for r in rows], ["A"])

    # 계속 실패하면 예외 — 빈 결과로 위장하면 '공시 없음'과 구분이 안 된다(§4.1)
    c = install(RecordingClient([page(["A"], 1)], fail_times=99))
    try:
        await dart.fetch_disclosures_range("20260828", "20260828")
        check("계속 타임아웃이면 예외", "예외 없음", "ReadTimeout")
    except httpx.ReadTimeout:
        check("계속 타임아웃이면 예외", True)

    # ── 조기 종료 ────────────────────────────────────────────────────
    # 22페이지 중 1페이지에만 새 공시. 2·3페이지가 연속으로 새 것 없음 → 3에서 정지.
    pages = [page([f"P1-{i}" for i in range(3)], 22)] + \
            [page([f"P{p}-{i}" for i in range(3)], 22) for p in range(2, 23)]
    known = {f"P{p}-{i}" for p in range(2, 23) for i in range(3)}
    c = install(RecordingClient(pages))
    rows = await dart.fetch_disclosures_range("20260828", "20260828", known_rcept_nos=known)
    check("아는 페이지 2번 연속이면 정지", [p for p, _ in c.calls], [1, 2, 3])
    check("22페이지를 다 돌지 않음", len(c.calls) < 22)
    # 조회된 것은 새 것만이 아니라 전부 — 저장 후 발송 전에 죽은 사이클을 복구해야 한다
    check("멈추기 전 페이지는 통째로 반환", len(rows), 9)

    # 여유 한 페이지: 새 것 없는 페이지 하나만으로는 멈추지 않는다
    pages = [page(["OLD1"], 5), page(["NEW"], 5), page(["OLD2"], 5),
             page(["OLD3"], 5), page(["OLD4"], 5)]
    c = install(RecordingClient(pages))
    rows = await dart.fetch_disclosures_range(
        "20260828", "20260828", known_rcept_nos={"OLD1", "OLD2", "OLD3", "OLD4"})
    check("한 페이지 비었다고 성급히 멈추지 않음", [p for p, _ in c.calls], [1, 2, 3, 4])
    check("한 페이지 건너뛴 새 공시는 잡는다", "NEW" in [r["rcept_no"] for r in rows])

    # known을 안 주면(봇 조회·과거 날짜) 전량 순회 — 조기 종료는 폴링 전용이다
    pages = [page([f"P{p}"], 4) for p in range(1, 5)]
    c = install(RecordingClient(pages))
    await dart.fetch_disclosures_range("20260828", "20260828")
    check("known 없으면 전량 순회", [p for p, _ in c.calls], [1, 2, 3, 4])

    # ── 번호가 아니라 '새 항목 유무'로 끊는다 ────────────────────────
    # 실측: 한 페이지 안에서 900977 다음에 900979가 온다(거래소·DART 별개 수열).
    # 고수위 번호로 잘랐다면 900979를 이미 본 것으로 오판했을 것이다.
    pages = [page(["20260828900984", "20260828900977", "20260828900979"], 2),
             page(["20260828001903"], 2)]
    c = install(RecordingClient(pages))
    rows = await dart.fetch_disclosures_range(
        "20260828", "20260828", known_rcept_nos={"20260828900984"})
    got = [r["rcept_no"] for r in rows]
    check("비단조 번호 사이의 공시를 놓치지 않음", "20260828900979" in got)
    check("정렬을 가정하지 않고 전 페이지 수집", len(got), 4)

    # ── 전체 스윕 ────────────────────────────────────────────────────
    # 조기 종료는 200건 밖으로 밀려난 공시를 다시 볼 기회가 없다. 그 창을
    # 15분으로 묶는 것이 스윕이고, 기동 직후 첫 사이클도 스윕이어야 한다.
    import tasks

    seen_mode = []

    async def fake_fetch(days=1, incremental=False, **_kw):
        seen_mode.append(incremental)
        return []

    orig_fetch, orig_sweep = tasks.fetch_recent_disclosures, tasks._last_full_sweep
    tasks.fetch_recent_disclosures = fake_fetch
    tasks._last_full_sweep = None
    try:
        await tasks._run_pipeline()
        check("기동 후 첫 사이클은 전체 스윕", seen_mode[-1], False)
        await tasks._run_pipeline()
        check("바로 다음 사이클은 증분", seen_mode[-1], True)
        tasks._last_full_sweep = datetime.now(tasks._KST) - tasks.FULL_SWEEP_INTERVAL
        await tasks._run_pipeline()
        check("주기가 지나면 다시 전체 스윕", seen_mode[-1], False)
        check("스윕 주기는 15분", tasks.FULL_SWEEP_INTERVAL, timedelta(minutes=15))

        # 실패한 스윕은 소진되지 않는다 — 다음 사이클이 다시 스윕해야 한다
        async def boom(days=1, incremental=False, **_kw):
            seen_mode.append(incremental)
            raise httpx.ReadTimeout("timed out")

        tasks._last_full_sweep = None
        tasks.fetch_recent_disclosures = boom
        try:
            await tasks._run_pipeline()
        except httpx.ReadTimeout:
            pass
        tasks.fetch_recent_disclosures = fake_fetch
        await tasks._run_pipeline()
        check("실패한 스윕은 다음 사이클에 다시 시도", seen_mode[-1], False)
    finally:
        tasks.fetch_recent_disclosures, tasks._last_full_sweep = orig_fetch, orig_sweep

    if failures:
        print(f"\n{len(failures)}건 실패: {failures}")
        sys.exit(1)
    print("\n전부 통과")


asyncio.run(main())
