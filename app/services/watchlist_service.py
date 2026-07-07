"""Watchlist business logic (telegram-independent)."""
import uuid
from sqlalchemy import select
from database import AsyncSessionLocal
from models import Watchlist
from services.user_service import get_or_create_user


async def add_watchlist(
    chat_id: str, first_name: str, selected: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Add selected {corp_code: corp_name}. Returns (added_names, skipped_names)."""
    added: list[str] = []
    skipped: list[str] = []
    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, chat_id, first_name)
        for corp_code, corp_name in selected.items():
            existing = await session.execute(
                select(Watchlist).where(
                    Watchlist.chat_id == chat_id,
                    Watchlist.corp_code == corp_code,
                )
            )
            if existing.scalar_one_or_none():
                skipped.append(corp_name)
                continue
            session.add(Watchlist(
                id=str(uuid.uuid4()),
                chat_id=chat_id,
                corp_code=corp_code,
                corp_name=corp_name,
            ))
            added.append(corp_name)
        await session.commit()
    return added, skipped


async def find_by_name(chat_id: str, query: str) -> list[Watchlist]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Watchlist).where(
                Watchlist.chat_id == chat_id,
                Watchlist.corp_name.ilike(f"%{query}%"),
            )
        )
        return list(result.scalars().all())


async def remove_by_code(chat_id: str, corp_code: str) -> bool:
    """Delete a watchlist entry. Returns True if something was deleted."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Watchlist).where(
                Watchlist.chat_id == chat_id,
                Watchlist.corp_code == corp_code,
            )
        )
        watchlist = result.scalar_one_or_none()
        if not watchlist:
            return False
        await session.delete(watchlist)
        await session.commit()
        return True


async def list_watchlist(chat_id: str) -> list[Watchlist]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Watchlist).where(Watchlist.chat_id == chat_id))
        return list(result.scalars().all())


async def get_corp_codes(chat_id: str) -> set[str]:
    items = await list_watchlist(chat_id)
    return {w.corp_code for w in items}
