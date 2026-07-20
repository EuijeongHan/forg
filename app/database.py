import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")

# echo=False: 프로덕션 로그 볼륨·정보 노출 방지 (디버깅 시에만 임시로 켠다)
engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

def _run_alembic_upgrade():
    # alembic은 동기 API라 스레드에서 실행한다 (env.py가 자체 이벤트 루프 사용)
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    here = Path(__file__).resolve().parent
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "migrations"))
    command.upgrade(cfg, "head")


async def init_db():
    import asyncio

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # create_all은 기존 테이블을 ALTER하지 않으므로 스키마 변경은 마이그레이션으로 적용
    try:
        await asyncio.to_thread(_run_alembic_upgrade)
        print("DB 마이그레이션 적용 완료")
    except Exception as e:
        # 적용 실패가 기동을 막지 않게 한다 — 미적용 상태는 기존 동작과 동일
        print(f"DB 마이그레이션 적용 실패: {e}")
