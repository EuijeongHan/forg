"""User + keyword/settings business logic (telegram-independent)."""
from sqlalchemy import select
from database import AsyncSessionLocal
from models import User


async def get_or_create_user(session, chat_id: str, first_name: str = "") -> User:
    """Fetch or create a user within an existing session (caller commits)."""
    result = await session.execute(select(User).where(User.chat_id == chat_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(chat_id=chat_id, first_name=first_name)
        session.add(user)
        await session.flush()
    return user


async def ensure_user(chat_id: str, first_name: str = "") -> None:
    """Create the user if missing, committing in a fresh session."""
    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, chat_id, first_name)
        await session.commit()


async def get_user(chat_id: str) -> User | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == chat_id))
        return result.scalar_one_or_none()


def _split_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


async def get_today_keywords(chat_id: str) -> list[str]:
    user = await get_user(chat_id)
    return _split_keywords(user.today_keywords) if user else []


async def get_mytoday_keywords(chat_id: str) -> list[str]:
    user = await get_user(chat_id)
    return _split_keywords(user.mytoday_keywords) if user else []


async def set_today_keywords(chat_id: str, keywords: str) -> bool:
    """(레거시) 저장형 키워드 설정 — /keyword 폐기로 현행 경로에선 호출되지 않는다.

    DB 컬럼과 함께 롤백 대비로만 유지한다. Returns True if also synced.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == chat_id))
        user = result.scalar_one_or_none()
        if not user:
            return False
        user.today_keywords = keywords
        synced = bool(user.sync_keywords)
        if synced:
            user.mytoday_keywords = keywords
        await session.commit()
        return synced


async def clear_today_keywords(chat_id: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == chat_id))
        user = result.scalar_one_or_none()
        if user:
            user.today_keywords = None
            await session.commit()


async def set_mytoday_keywords(chat_id: str, keywords: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == chat_id))
        user = result.scalar_one_or_none()
        if user:
            user.mytoday_keywords = keywords
            await session.commit()


async def clear_mytoday_keywords(chat_id: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == chat_id))
        user = result.scalar_one_or_none()
        if user:
            user.mytoday_keywords = None
            await session.commit()


async def toggle_sync(chat_id: str) -> User | None:
    """Flip keyword sync. Returns the updated (detached) user, or None if missing."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == chat_id))
        user = result.scalar_one_or_none()
        if not user:
            return None
        user.sync_keywords = not bool(user.sync_keywords)
        if user.sync_keywords and user.today_keywords:
            user.mytoday_keywords = user.today_keywords
        await session.commit()
        return user


async def delete_user_data(chat_id: str) -> dict[str, int]:
    """개인정보처리방침이 보장한 삭제 요청 경로의 실제 구현.

    이 사용자의 워치리스트·발송 기록·피드백·계정을 지운다. 되돌릴 수 없다.
    Disclosure(공시 원본)는 개인정보가 아니라 공개 공시 데이터이므로 남긴다.
    반환값은 삭제된 행 수 — 사용자에게 무엇이 지워졌는지 보여주기 위함이다.
    """
    from models import Feedback, SeenDisclosure, Watchlist

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Watchlist).where(Watchlist.chat_id == chat_id)
        )
        watchlists = result.scalars().all()

        result = await session.execute(
            select(SeenDisclosure).where(SeenDisclosure.chat_id == chat_id)
        )
        seen = result.scalars().all()

        result = await session.execute(
            select(Feedback).where(Feedback.chat_id == chat_id)
        )
        feedbacks = result.scalars().all()

        result = await session.execute(select(User).where(User.chat_id == chat_id))
        user = result.scalar_one_or_none()

        counts = {
            "watchlist": len(watchlists),
            "seen": len(seen),
            "feedback": len(feedbacks),
            "user": 1 if user else 0,
        }

        for row in watchlists:
            await session.delete(row)
        for row in seen:
            await session.delete(row)
        for row in feedbacks:
            await session.delete(row)
        # 사용자 행은 워치리스트가 참조하므로 마지막에 지운다
        if user:
            await session.delete(user)

        await session.commit()
    return counts
