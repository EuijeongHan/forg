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
    """Set /today keywords. Returns True if also synced to /mytoday."""
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
