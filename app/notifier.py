import asyncio
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
from config import TELEGRAM_BOT_TOKEN

MAX_MESSAGE_LENGTH = 4000
MAX_RETRIES = 3
RETRY_DELAY = 2  # 초

def get_bot() -> Bot:
    return Bot(token=TELEGRAM_BOT_TOKEN)

def escape_html(text: str) -> str:
    """HTML 특수문자 이스케이프"""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

async def send_alert(chat_id: str, corp_name: str, report_nm: str, receipt_no: str, summary: str):
    """텔레그램 공시 알림 발송 (재시도 포함)"""

    dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"

    message = (
        f"🚨 <b>중요 공시 알림</b>\n\n"
        f"🏢 <b>{escape_html(corp_name)}</b>\n"
        f"📋 {escape_html(report_nm)}\n\n"
        f"📝 <b>요약</b>\n"
        f"{escape_html(summary)}\n\n"
        f'🔗 <a href="{dart_url}">원문 보기</a>'
    )

    # 메시지 길이 제한
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH] + "\n\n... (내용이 잘렸습니다. 원문을 확인해주세요.)"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            bot = get_bot()
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
            )
            print(f"알림 발송 완료: {corp_name} - {report_nm}")
            return

        except TelegramError as e:
            print(f"알림 발송 실패 ({attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

    print(f"알림 발송 최종 실패: {corp_name} - {report_nm}")


async def send_system_message(chat_id: str, text: str):
    """시스템 메시지 발송"""
    try:
        bot = get_bot()
        await bot.send_message(chat_id=chat_id, text=text)
    except TelegramError as e:
        print(f"시스템 메시지 발송 실패: {e}")
