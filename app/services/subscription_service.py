"""Topic subscription business logic (telegram-independent)."""
from sqlalchemy import select
from database import AsyncSessionLocal
from models import TopicSubscription, User
from services.user_service import get_or_create_user
from topics import TOPICS


async def list_topics(chat_id: str) -> set[str]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TopicSubscription.topic).where(TopicSubscription.chat_id == chat_id)
        )
        return set(result.scalars().all())


async def toggle_topic(chat_id: str, first_name: str, topic: str) -> bool:
    """구독을 켜고 끈다. 켜졌으면 True.

    미등록 사용자도 구독할 수 있게 계정을 먼저 만든다(FK 보장, /feedback과 동일).
    """
    if topic not in TOPICS:
        return False
    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, chat_id, first_name)
        result = await session.execute(
            select(TopicSubscription).where(
                TopicSubscription.chat_id == chat_id,
                TopicSubscription.topic == topic,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            await session.delete(row)
            await session.commit()
            return False
        session.add(TopicSubscription(chat_id=chat_id, topic=topic))
        await session.commit()
        return True


async def subscribers(session, topic: str) -> list[User]:
    """이 토픽을 구독한 활성 사용자. 폴링 파이프라인이 쓰는 세션을 그대로 받는다."""
    result = await session.execute(
        select(User)
        .join(TopicSubscription, TopicSubscription.chat_id == User.chat_id)
        .where(TopicSubscription.topic == topic, User.is_active == True)
    )
    return list(result.scalars().all())
