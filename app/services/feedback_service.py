"""Feedback business logic (telegram-independent)."""
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
