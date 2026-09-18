"""사용 흔적 집계 — 무엇을 눌렀는지만, 누가·무엇에 대해는 남기지 않는다.

운영자가 사용자에게 매번 "쓰고 있냐"고 물어볼 수 없어서 만들었다. 그런데 이
서비스의 사용자는 기관 애널리스트고, '오늘 어느 기업을 열어봤나'는 소속 기관의
리서치 방향이다. 그래서 동사만 센다:

  기록한다   명령·버튼 이름, 날짜(KST), 횟수
  기록 안 한다  사용자 식별자, 정확한 시각, 기업명·공시번호·검색어

행 단위 이벤트를 쌓지 않고 (날짜, 이벤트) 카운터를 올린다. 행에 사람이 없으니
재식별할 대상이 없고, 나중에 사용자가 늘어도 그대로 집계로 읽힌다.
운영자 본인의 조작은 제외한다 — 테스트가 섞이면 수치를 믿을 수 없다.
"""
from sqlalchemy import select, update

from config import OPERATOR_CHAT_IDS
from database import AsyncSessionLocal
from models import UsageDaily


def event_name(update_obj) -> str:
    """텔레그램 업데이트에서 '동사'만 뽑는다. 인자는 버린다.

    '/market 카카오'는 'market'이 되고 검색어는 사라진다. 'view:20260918000123'은
    'view'가 되고 공시번호는 사라진다 — 무엇을 봤는지가 남으면 안 되는 부분이다.
    """
    query = getattr(update_obj, "callback_query", None)
    if query is not None and getattr(query, "data", None):
        return str(query.data).split(":", 1)[0][:40]

    message = getattr(update_obj, "message", None)
    text = (getattr(message, "text", "") or "").strip()
    if text.startswith("/"):
        return text[1:].split()[0].split("@")[0][:40].lower()
    return ""


async def record(chat_id: str, event: str) -> bool:
    """(날짜, 이벤트) 카운터를 1 올린다. 운영자면 기록하지 않는다."""
    if not event or str(chat_id) in OPERATOR_CHAT_IDS:
        return False

    from dart import today_kst
    day = today_kst()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(UsageDaily)
            .where(UsageDaily.day == day, UsageDaily.event == event)
            .values(count=UsageDaily.count + 1)
        )
        if result.rowcount == 0:
            session.add(UsageDaily(day=day, event=event, count=1))
        await session.commit()
    return True


async def recent(days: int = 14) -> list[dict]:
    """최근 N일치를 날짜 내림차순으로. 각 날짜의 이벤트별 횟수."""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(UsageDaily).order_by(UsageDaily.day.desc())
        )).scalars().all()

    by_day: dict[str, dict[str, int]] = {}
    for row in rows:
        by_day.setdefault(row.day, {})[row.event] = row.count
    return [{"day": d, "events": by_day[d]} for d in sorted(by_day, reverse=True)[:days]]
