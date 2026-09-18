"""사용 기록 — 평가·품질 개선의 원자료.

무엇을 남기나 (UsageEvent 한 행 = 사용자 행동 하나):
  - 명령·버튼, 검색어, 기업, 공시번호, 시각
  - 조회의 결과 수 — 0건 검색은 키워드·필터가 헛돈다는 가장 직접적인 신호다
  - 요약을 열었다면 그때 보여준 요약 원문과 생성 경로(정형/원문, 원문 길이)

기록은 두 단계다. 앞단 훅(record)이 모든 업데이트를 받아 인자까지 파싱해 한 행을
만든다 — 핸들러 20곳에 심으면 나중에 추가되는 명령이 빠진다. 결과 수나 요약처럼
핸들러가 일을 끝내야 알 수 있는 값은 그 핸들러가 같은 행에 덧붙인다(enrich).
둘은 텔레그램 update_id로 이어진다.

어느 단계든 실패해도 사용자 요청을 깨뜨리지 않는다 — 기록은 부가 기능이다.
"""
from sqlalchemy import update as sql_update

from config import OPERATOR_CHAT_IDS
from database import AsyncSessionLocal
from models import UsageEvent

_QUERY_MAX = 500
_SUMMARY_MAX = 8000


def parse(update_obj) -> dict | None:
    """업데이트에서 이벤트와 그 인자를 뽑는다. 명령·버튼이 아니면 None."""
    query = getattr(update_obj, "callback_query", None)
    data = getattr(query, "data", None) if query is not None else None
    if data:
        head, _, rest = str(data).partition(":")
        ev: dict = {"event": head[:40]}
        if head == "view":
            ev["rcept_no"] = rest or None
        elif head == "toggle":
            ev["corp_code"] = rest or None
        elif head == "remove":
            code, _, name = rest.partition(":")
            ev["corp_code"], ev["corp_name"] = code or None, name or None
        elif head == "page":
            ev["detail"] = {"offset": int(rest)} if rest.isdigit() else None
        elif head == "topic":
            ev["detail"] = {"topic": rest}
        elif head == "fbdone":
            ev["detail"] = {"feedback_id": rest}
        return ev

    message = getattr(update_obj, "message", None)
    text = (getattr(message, "text", "") or "").strip()
    if not text.startswith("/"):
        return None
    cmd, _, args = text[1:].partition(" ")
    ev = {"event": cmd.split("@")[0].lower()[:40]}
    if args.strip():
        ev["query"] = args.strip()[:_QUERY_MAX]
    return ev


def _update_key(update_obj) -> str | None:
    uid = getattr(update_obj, "update_id", None)
    return str(uid) if uid is not None else None


async def record(update_obj) -> bool:
    """업데이트 하나를 한 행으로 남긴다."""
    ev = parse(update_obj)
    chat = getattr(update_obj, "effective_chat", None)
    if not ev or chat is None:
        return False
    chat_id = str(chat.id)
    async with AsyncSessionLocal() as session:
        session.add(UsageEvent(
            update_id=_update_key(update_obj),
            chat_id=chat_id,
            is_operator=chat_id in OPERATOR_CHAT_IDS,
            **ev,
        ))
        await session.commit()
    return True


async def enrich(update_obj, *, detail: dict | None = None, **fields) -> None:
    """핸들러가 일을 끝낸 뒤에야 아는 값을 같은 행에 덧붙인다.

    detail은 기존 detail에 병합한다(훅이 넣은 offset·topic 등을 지우지 않는다).
    기록이 없으면(훅 실패 등) 조용히 넘어간다.
    """
    key = _update_key(update_obj)
    if key is None:
        return
    if isinstance(detail, dict) and "summary" in detail and detail["summary"]:
        detail = {**detail, "summary": str(detail["summary"])[:_SUMMARY_MAX]}

    async with AsyncSessionLocal() as session:
        if detail:
            from sqlalchemy import select
            row = (await session.execute(
                select(UsageEvent).where(UsageEvent.update_id == key)
            )).scalars().first()
            if row is None:
                return
            row.detail = {**(row.detail or {}), **detail}
            for name, value in fields.items():
                setattr(row, name, value)
        elif fields:
            await session.execute(
                sql_update(UsageEvent).where(UsageEvent.update_id == key).values(**fields)
            )
        else:
            return
        await session.commit()
