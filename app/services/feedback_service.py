"""Feedback business logic (telegram-independent)."""
from sqlalchemy import select
from database import AsyncSessionLocal
from models import Feedback
from services.user_service import get_or_create_user


async def save_feedback(chat_id: str, first_name: str, text: str) -> Feedback:
    """피드백을 저장한다. 미등록 사용자면 계정부터 만든다(FK 보장)."""
    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, chat_id, first_name)
        row = Feedback(chat_id=chat_id, text=text)
        session.add(row)
        await session.commit()
        return row


async def open_items(limit: int = 20) -> list[Feedback]:
    """미처리 피드백(오래된 순). 사용자 요청은 1순위라 먼저 온 것부터 본다."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Feedback).where(Feedback.status == "new")
            .order_by(Feedback.created_at).limit(limit)
        )
        return list(result.scalars().all())


async def open_count() -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Feedback).where(Feedback.status == "new"))
        return len(result.scalars().all())


async def my_items(chat_id: str, limit: int = 10) -> list[Feedback]:
    """내가 낸 요청과 그 처리 여부 — 신고한 사람이 결과를 확인할 수 있어야 한다."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Feedback).where(Feedback.chat_id == chat_id)
            .order_by(Feedback.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())


async def mark_done(feedback_id: str) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Feedback).where(Feedback.id == feedback_id))
        row = result.scalar_one_or_none()
        if not row or row.status == "done":
            return False
        row.status = "done"
        await session.commit()
        return True
