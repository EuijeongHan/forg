import asyncio
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

# 텔레그램 명령 수신(폴링)이 붙어 있는지. 알림 발송은 이 값과 무관하게 동작한다.
bot_polling_started = False

BOT_START_ATTEMPTS = 3
BOT_START_BACKOFF_SECONDS = 5


async def _start_bot_polling():
    """텔레그램 명령 폴링을 재시도하며 시작한다.

    실패해도 예외를 올리지 않는다 — 텔레그램 접속이 일시적으로 막혀도
    DART 폴링·알림 발송은 계속돼야 하기 때문이다(알림은 별도 HTTP 호출).
    """
    global bot_polling_started
    for attempt in range(1, BOT_START_ATTEMPTS + 1):
        try:
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling()
            bot_polling_started = True
            print("텔레그램 봇 시작 완료")
            return
        except Exception as e:
            print(f"텔레그램 봇 시작 실패 ({attempt}/{BOT_START_ATTEMPTS}): {e}")
            try:
                await bot_app.shutdown()
            except Exception:
                pass
            if attempt < BOT_START_ATTEMPTS:
                await asyncio.sleep(BOT_START_BACKOFF_SECONDS * attempt)

    print("텔레그램 명령 수신 비활성 — 공시 폴링·알림 발송은 계속합니다")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB 초기화
    try:
        await init_db()
        print("DB 초기화 완료")
    except Exception as e:
        print(f"DB 초기화 실패: {e}")
        raise

    # 텔레그램 봇 시작 (실패해도 파이프라인은 기동한다)
    await _start_bot_polling()

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

    startup_notice = "✅ forG 서비스가 시작되었습니다."
    if not bot_polling_started:
        startup_notice += "\n⚠️ 텔레그램 명령 수신은 비활성 상태입니다(알림 발송은 정상)."
    await send_system_message(TELEGRAM_CHAT_ID, startup_notice)

    yield

    # 종료 — 시작에 실패한 구성요소는 건너뛴다
    scheduler.shutdown()
    if bot_polling_started:
        try:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
        except Exception as e:
            print(f"텔레그램 봇 종료 중 오류: {e}")
    print("서비스 종료")


app = FastAPI(title="forG", lifespan=lifespan)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "scheduler": scheduler.running,
        "jobs": [job.id for job in scheduler.get_jobs()],
        "bot_polling": bot_polling_started,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
