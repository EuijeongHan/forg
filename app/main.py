import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from database import init_db
from tasks import process_disclosures, run_llm_canary, send_daily_digest
from bot import create_bot_app
from config import POLLING_INTERVAL, TELEGRAM_CHAT_ID
from notifier import send_system_message

_KST = ZoneInfo("Asia/Seoul")

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
        # 한 사이클이 주기를 넘겨도 폴링이 겹치지 않게 한다. 겹치면 같은 공시를
        # 두 번 처리하려 들고 DART 호출도 배로 나간다. 밀린 실행은 1회로 합친다.
        max_instances=1,
        coalesce=True,
    )
    # 참고 등급 다이제스트 — 즉시 알림 대상이 아닌 관심기업 공시를 하루 1회 묶음.
    # 18:30 KST: 장 마감(15:30) 후 당일 공시가 대부분 접수된 시점.
    scheduler.add_job(
        send_daily_digest,
        CronTrigger(hour=18, minute=30, timezone=_KST),
        id="daily_digest",
        max_instances=1,
        coalesce=True,
    )
    # LLM 프로바이더 캐너리 — 배포 90초 후 1회(키 교체 즉시 검증) + 매일 08:30 KST
    # (공시 시작 전). 잔액 조회 API가 없어 실호출이 유일한 검증이다.
    scheduler.add_job(
        run_llm_canary,
        CronTrigger(hour=8, minute=30, timezone=_KST),
        id="llm_canary",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=90),
    )
    scheduler.start()
    print(f"DART 폴링 시작 (주기: {POLLING_INTERVAL}초) + 다이제스트 18:30 KST")

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
    from tasks import poll_status

    # 사이클을 놓치고 있는지: 마지막 폴링이 주기의 3배를 넘겼으면 정체로 본다.
    stale = False
    last_run = poll_status.get("last_run_at")
    if last_run:
        try:
            elapsed = (datetime.now(_KST) - datetime.fromisoformat(last_run)).total_seconds()
            stale = elapsed > POLLING_INTERVAL * 3
        except ValueError:
            stale = False

    degraded = (
        not scheduler.running
        or stale
        or poll_status.get("last_result") == "error"
        or not bot_polling_started
    )

    return {
        "status": "degraded" if degraded else "ok",
        "scheduler": scheduler.running,
        "jobs": [job.id for job in scheduler.get_jobs()],
        "bot_polling": bot_polling_started,
        "polling_stale": stale,
        "poll": poll_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
