"""운영자용 사용 현황 — 이미 있는 데이터를 읽어 보여주기만 한다.

동기: 사용자에게 매번 "쓰고 있냐"고 물어볼 수 없다. 그런데 무엇을 알 수 있는지에는
분명한 경계가 있다. 이 서비스는 **우리가 보낸 것**만 기록한다. 사용자가 요약을
열었는지, 무엇을 조회했는지는 어디에도 남지 않는다 — 조회 경로는 읽기 전용이고,
검색어는 `/keyword` 폐기 이후 의도적으로 저장하지 않는다.

그래서 두 값을 끝까지 분리해서 낸다:
  - 마지막 행동 = 사용자가 실제로 **한** 것(등록·구독·피드백) 중 가장 최근
  - 발송       = 우리가 **보낸** 양. 그날 공시가 많았다는 뜻이지 읽었다는 증거가 아니다

이 구분을 지우고 발송량만 크게 보여주면 '잘 쓰고 있다'로 오독된다. 집계는
새 수집이 아니므로 개인정보처리방침의 수집 항목은 그대로다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from database import AsyncSessionLocal
from models import (Feedback, SeenDisclosure, TopicSubscription, UsageDaily,
                    User, Watchlist)

# 이벤트 이름을 사람이 읽는 말로. 모르는 이름은 그대로 둔다 —
# 새 명령이 생겼을 때 조용히 빠지는 것보다 날것으로라도 보이는 편이 낫다.
_EVENT_LABELS = {
    "view": "요약 열람", "page": "목록 넘김", "topic": "유형 설정",
    "toggle": "기업 선택", "confirm_add": "등록 확정", "remove": "등록 해제",
    "my": "/my", "market": "/market", "ask": "/ask", "add": "/add",
    "list": "/list", "feedback": "/feedback", "inbox": "/inbox",
    "help": "/help", "start": "/start", "today": "/today(구)",
}

_KST = ZoneInfo("Asia/Seoul")


async def _grouped(session, model) -> dict[str, dict]:
    """chat_id별 건수와 최근 시각."""
    rows = await session.execute(
        select(model.chat_id, func.count(), func.max(model.created_at)).group_by(model.chat_id)
    )
    return {r[0]: {"n": r[1], "last": r[2]} for r in rows}


def _latest(*values):
    seen = [v for v in values if v]
    return max(seen) if seen else None


async def collect_stats(usage_window: int = 14) -> dict:
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
        watchlists = await _grouped(session, Watchlist)
        sent = await _grouped(session, SeenDisclosure)
        feedback = await _grouped(session, Feedback)
        subs = await _grouped(session, TopicSubscription)

        topics: dict[str, list[str]] = {}
        for chat_id, topic in await session.execute(
            select(TopicSubscription.chat_id, TopicSubscription.topic)
        ):
            topics.setdefault(chat_id, []).append(topic)

        usage_rows = await session.execute(select(UsageDaily))
        usage_by_day: dict[str, dict[str, int]] = {}
        for row in usage_rows.scalars().all():
            usage_by_day.setdefault(row.day, {})[row.event] = row.count

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
            # 사용자가 직접 한 것만 — 발송은 우리 행동이라 넣지 않는다.
            "last_action": _latest(
                watchlists.get(cid, {}).get("last"),
                subs.get(cid, {}).get("last"),
                feedback.get(cid, {}).get("last"),
            ),
        })

    recent_days = sorted(usage_by_day, reverse=True)[:usage_window]
    totals_by_event: dict[str, int] = {}
    for day in recent_days:
        for event, n in usage_by_day[day].items():
            totals_by_event[event] = totals_by_event.get(event, 0) + n

    return {
        "users": rows,
        "usage": {
            "window": usage_window,
            "events": dict(sorted(totals_by_event.items(), key=lambda kv: -kv[1])),
            "active_days": len(recent_days),
            "last_day": recent_days[0] if recent_days else None,
        },
        "totals": {
            "users": len(rows),
            "active": sum(1 for r in rows if r["is_active"]),
            "sent": sum(r["sent"] for r in rows),
            "open_feedback": open_feedback or 0,
        },
    }


def format_stats(data: dict, now: datetime | None = None) -> str:
    """텔레그램 한 통 분량으로 정리. 알 수 없는 것은 알 수 없다고 적는다."""
    now = now or datetime.now(_KST)

    def when(value):
        if not value:
            return "없음"
        local = value.astimezone(_KST) if value.tzinfo else value
        days = (now.date() - local.date()).days
        ago = "오늘" if days == 0 else ("어제" if days == 1 else f"{days}일 전")
        return f"{local:%m/%d %H:%M} ({ago})"

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

    usage = data.get("usage") or {}
    events = usage.get("events") or {}
    lines.append(f"\n📈 기능 사용 (최근 {usage.get('window', 14)}일 · 운영자 제외)")
    if not events:
        lines.append("  아직 기록 없음 — 누군가 명령이나 버튼을 쓰면 여기 쌓입니다.")
    else:
        lines.append("  " + " · ".join(
            f"{_EVENT_LABELS.get(e, e)} {n}" for e, n in events.items()))
        last = usage.get("last_day")
        if last:
            shown = f"{last[4:6]}/{last[6:8]}"
            days = (now.date() - datetime.strptime(last, "%Y%m%d").date()).days
            ago = "오늘" if days == 0 else ("어제" if days == 1 else f"{days}일 전")
            lines.append(f"  마지막 사용 {shown} ({ago}) · 사용한 날 "
                         f"{usage.get('active_days', 0)}/{usage.get('window', 14)}일")

    lines.append(
        "\n※ '받은 알림'은 우리가 보낸 양이라 공시가 많은 날 늘어납니다."
        "\n실제로 쓰는지는 '기능 사용'을 보세요. 무엇을 눌렀는지만 세고"
        "\n누가·어느 기업인지는 기록하지 않습니다."
    )
    return "\n".join(lines)
