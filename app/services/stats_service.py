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
from models import Feedback, SeenDisclosure, TopicSubscription, User, Watchlist

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


async def collect_stats() -> dict:
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

    return {
        "users": rows,
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

    lines.append(
        "\n※ '받은 알림'은 우리가 보낸 양입니다. 공시가 많은 날 늘어날 뿐,"
        "\n열어봤다는 뜻이 아닙니다 — 클릭은 기록하지 않습니다."
    )
    return "\n".join(lines)
