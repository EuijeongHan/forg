"""운영자용 사용 현황 — 무엇이 쓰이고, 무엇이 헛돌고, 어디서 품질이 새는가.

세 가지를 한 화면에 낸다.
  1. 사용자별: 등록·구독·발송, 그리고 마지막 행동(사용자가 직접 한 것)
  2. 사용: 최근 N일 기능별 횟수, 검색어와 그 결과 수, 많이 연 공시
  3. 품질 경보: 원문이 비었는데 요약이 나간 건, 요약 실패 문구가 나간 건

'받은 알림'(우리가 보낸 양)과 '사용'(사용자가 한 것)은 끝까지 섞지 않는다.
발송량은 공시가 많은 날 늘어날 뿐이라, 합치면 참여도처럼 보이는 거짓 신호가 된다.
사업 지표는 운영자 행동을 빼고 센다 — 본인 테스트가 수치의 대부분이 되면 쓸모가 없다.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from database import AsyncSessionLocal
from models import (Feedback, SeenDisclosure, TopicSubscription, UsageEvent, User,
                    Watchlist)

_KST = ZoneInfo("Asia/Seoul")
_SEARCH_EVENTS = ("my", "market", "ask")

# 이벤트 이름을 사람이 읽는 말로. 모르는 이름은 그대로 둔다 —
# 새 명령이 생겼을 때 조용히 빠지는 것보다 날것으로라도 보이는 편이 낫다.
_EVENT_LABELS = {
    "view": "요약 열람", "page": "목록 넘김", "topic": "유형 설정",
    "toggle": "기업 선택", "confirm_add": "등록 확정", "remove": "등록 해제",
    "my": "/my", "market": "/market", "ask": "/ask", "add": "/add",
    "list": "/list", "feedback": "/feedback", "inbox": "/inbox",
    "help": "/help", "start": "/start", "today": "/today(구)",
}
_FAIL_MARKERS = ("실패했습니다", "한도에 도달")


async def _grouped(session, model) -> dict[str, dict]:
    """chat_id별 건수와 최근 시각."""
    rows = await session.execute(
        select(model.chat_id, func.count(), func.max(model.created_at)).group_by(model.chat_id)
    )
    return {r[0]: {"n": r[1], "last": r[2]} for r in rows}


def _latest(*values):
    seen = [v for v in values if v]
    return max(seen) if seen else None


def _as_utc(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _usage_summary(events: list, window: int) -> dict:
    """운영자를 뺀 최근 이벤트로 사업·품질 지표를 만든다."""
    by_event = Counter(e.event for e in events)
    days = {_as_utc(e.created_at).astimezone(_KST).strftime("%Y%m%d") for e in events}

    searches: dict[str, dict] = defaultdict(lambda: {"n": 0, "results": [], "zero": 0})
    for e in events:
        if e.event in _SEARCH_EVENTS and e.query:
            s = searches[e.query.strip()]
            s["n"] += 1
            if e.result_count is not None:
                s["results"].append(e.result_count)
                if e.result_count == 0:
                    s["zero"] += 1

    opened: dict[str, dict] = {}
    empty_source, failed = [], []
    for e in events:
        if e.event != "view" or not e.rcept_no:
            continue
        d = e.detail or {}
        o = opened.setdefault(e.rcept_no, {"n": 0, "corp_name": e.corp_name,
                                           "report_nm": d.get("report_nm")})
        o["n"] += 1
        if d.get("path") == "raw" and d.get("source_len") == 0:
            empty_source.append(e.rcept_no)
        if any(m in (d.get("summary") or "") for m in _FAIL_MARKERS):
            failed.append(e.rcept_no)

    return {
        "window": window,
        "events": dict(by_event.most_common()),
        "users": len({e.chat_id for e in events}),
        "active_days": len(days),
        "last_day": max(days) if days else None,
        "searches": sorted(
            ({"query": q, "n": v["n"], "zero": v["zero"],
              "avg": (sum(v["results"]) / len(v["results"])) if v["results"] else None}
             for q, v in searches.items()),
            key=lambda x: -x["n"]),
        "opened": sorted(
            ({"rcept_no": r, **v} for r, v in opened.items()), key=lambda x: -x["n"]),
        "empty_source": sorted(set(empty_source)),
        "failed": sorted(set(failed)),
    }


async def collect_stats(usage_window: int = 14) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=usage_window)
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
        watchlists = await _grouped(session, Watchlist)
        sent = await _grouped(session, SeenDisclosure)
        feedback = await _grouped(session, Feedback)
        subs = await _grouped(session, TopicSubscription)
        used = await _grouped(session, UsageEvent)

        topics: dict[str, list[str]] = {}
        for chat_id, topic in await session.execute(
            select(TopicSubscription.chat_id, TopicSubscription.topic)
        ):
            topics.setdefault(chat_id, []).append(topic)

        recent = (await session.execute(
            select(UsageEvent).where(UsageEvent.is_operator.is_(False),
                                     UsageEvent.created_at >= cutoff)
        )).scalars().all()

        open_feedback = await session.scalar(
            select(func.count()).select_from(Feedback).where(Feedback.status == "new")
        )

    rows = []
    for user in users:
        cid = user.chat_id
        rows.append({
            "chat_id": cid,
            "first_name": user.first_name or "",
            "is_active": bool(user.is_active),
            "joined": user.created_at,
            "watchlist": watchlists.get(cid, {}).get("n", 0),
            "topics": sorted(topics.get(cid, [])),
            "sent": sent.get(cid, {}).get("n", 0),
            "last_sent": sent.get(cid, {}).get("last"),
            "feedback": feedback.get(cid, {}).get("n", 0),
            "events": used.get(cid, {}).get("n", 0),
            # 사용자가 직접 한 것만 — 발송은 우리 행동이라 넣지 않는다
            "last_action": _latest(
                watchlists.get(cid, {}).get("last"),
                subs.get(cid, {}).get("last"),
                feedback.get(cid, {}).get("last"),
                used.get(cid, {}).get("last"),
            ),
        })

    return {
        "users": rows,
        "usage": _usage_summary(recent, usage_window),
        "totals": {
            "users": len(rows),
            "active": sum(1 for r in rows if r["is_active"]),
            "sent": sum(r["sent"] for r in rows),
            "open_feedback": open_feedback or 0,
        },
    }


def format_stats(data: dict, now: datetime | None = None) -> str:
    """텔레그램 한 통 분량으로 정리. 목록은 상위 5개로 자른다(4096자 한도)."""
    now = now or datetime.now(_KST)

    def ago(days: int) -> str:
        return "오늘" if days == 0 else ("어제" if days == 1 else f"{days}일 전")

    def when(value):
        if not value:
            return "없음"
        local = _as_utc(value).astimezone(_KST)
        return f"{local:%m/%d %H:%M} ({ago((now.date() - local.date()).days)})"

    t = data["totals"]
    lines = [
        f"📊 사용 현황 — 사용자 {t['users']}명 (활성 {t['active']})",
        f"누적 발송 {t['sent']}건 · 미처리 요청 {t['open_feedback']}건",
    ]
    if not data["users"]:
        lines.append("\n아직 사용자가 없습니다.")
        return "\n".join(lines)

    for r in data["users"]:
        name = r["first_name"] or "(이름 없음)"
        flag = "" if r["is_active"] else " · ⚠️ 비활성"
        lines.append(f"\n— {name} ({r['chat_id']}){flag}")
        topics = ", ".join(r["topics"]) if r["topics"] else "없음"
        lines.append(f"  관심기업 {r['watchlist']}개 · 유형구독 {topics}")
        lines.append(f"  마지막 행동: {when(r['last_action'])} · 보낸 요청 {r['feedback']}건")
        lines.append(f"  받은 알림: {r['sent']}건 · 최근 {when(r['last_sent'])}")

    u = data.get("usage") or {}
    window = u.get("window", 14)
    lines.append(f"\n📈 최근 {window}일 (운영자 제외)")
    if not u.get("events"):
        lines.append("  아직 기록 없음 — 누군가 명령이나 버튼을 쓰면 여기 쌓입니다.")
    else:
        last = u.get("last_day")
        tail = ""
        if last:
            d = (now.date() - datetime.strptime(last, "%Y%m%d").date()).days
            tail = f" · 마지막 {last[4:6]}/{last[6:8]} ({ago(d)})"
        lines.append(f"  이용자 {u.get('users', 0)}명 · 사용한 날 "
                     f"{u.get('active_days', 0)}/{window}{tail}")
        lines.append("  " + " · ".join(
            f"{_EVENT_LABELS.get(e, e)} {n}" for e, n in list(u["events"].items())[:8]))

    searches = u.get("searches") or []
    if searches:
        lines.append("\n🔎 검색어 (횟수 · 평균 결과)")
        for s in [x for x in searches if not x["zero"]][:5]:
            avg = f"{s['avg']:.0f}건" if s["avg"] is not None else "-"
            lines.append(f"  {s['query'][:30]} ×{s['n']} · {avg}")
        zero = [x for x in searches if x["zero"]]
        if zero:
            lines.append("  ⚠️ 0건: " + " · ".join(
                f"{x['query'][:20]} ×{x['zero']}" for x in zero[:5]))

    opened = u.get("opened") or []
    if opened:
        lines.append("\n📄 많이 연 공시")
        for o in opened[:5]:
            label = " ".join(x for x in (o.get("corp_name"), o.get("report_nm")) if x)
            lines.append(f"  {(label or o['rcept_no'])[:45]} ×{o['n']}")

    alarms = []
    if u.get("empty_source"):
        alarms.append(f"  원문 없이 요약 {len(u['empty_source'])}건: "
                      + ", ".join(u["empty_source"][:3]))
    if u.get("failed"):
        alarms.append(f"  요약 실패 {len(u['failed'])}건: " + ", ".join(u["failed"][:3]))
    if alarms:
        lines.append("\n🚨 품질 경보 (사용자가 실제로 본 요약)")
        lines.extend(alarms)

    lines.append(
        "\n※ '받은 알림'은 우리가 보낸 양이라 공시가 많은 날 늘어납니다."
        f"\n실제 사용은 '최근 {window}일'을 보세요."
    )
    return "\n".join(lines)
