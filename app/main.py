from contextlib import asynccontextmanager
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from database import init_db
from tasks import process_disclosures
from bot import create_bot_app
from config import POLLING_INTERVAL, TELEGRAM_CHAT_ID
from notifier import send_system_message

scheduler = AsyncIOScheduler()
bot_app = create_bot_app()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB 초기화
    try:
        await init_db()
        print("DB 초기화 완료")
    except Exception as e:
        print(f"DB 초기화 실패: {e}")
        raise

    # 텔레그램 봇 시작
    try:
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling()
        print("텔레그램 봇 시작 완료")
    except Exception as e:
        print(f"텔레그램 봇 시작 실패: {e}")
        raise

    # 스케줄러 시작 (앱 시작 즉시 첫 폴링 실행)
    scheduler.add_job(
        process_disclosures,
        "interval",
        seconds=POLLING_INTERVAL,
        id="dart_polling",
        next_run_time=datetime.now(timezone.utc),
    )
    scheduler.start()
    print(f"DART 폴링 시작 (주기: {POLLING_INTERVAL}초)")

    await send_system_message(TELEGRAM_CHAT_ID, "✅ forG 서비스가 시작되었습니다.")

    yield

    # 종료
    scheduler.shutdown()
    await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()
    print("서비스 종료")


app = FastAPI(title="forG", lifespan=lifespan)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "scheduler": scheduler.running,
        "jobs": [job.id for job in scheduler.get_jobs()],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
