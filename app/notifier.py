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

def build_disclosure_message(corp_name: str, report_nm: str, receipt_no: str, summary: str) -> str:
    """공시 상세 HTML 메시지 조립 — 모든 사용자/공시 문자열은 여기서 이스케이프한다.

    자동 알림(send_alert)과 봇 조회(/today 상세)가 공용으로 사용한다.
    """
    dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"
    return (
        f"🏢 <b>{escape_html(corp_name)}</b>\n"
        f"📋 {escape_html(report_nm)}\n\n"
        f"📝 <b>요약</b>\n"
        f"{escape_html(summary)}\n\n"
        f'🔗 <a href="{dart_url}">원문 보기</a>'
    )

async def send_alert(chat_id: str, corp_name: str, report_nm: str, receipt_no: str, summary: str) -> bool:
    """텔레그램 공시 알림 발송 (재시도 포함). 성공 여부를 반환한다.

    반환값이 False면 호출측(tasks.py)은 SeenDisclosure를 기록하지 않아
    다음 폴링에서 재시도된다.
    """
    message = "🚨 <b>중요 공시 알림</b>\n\n" + build_disclosure_message(
        corp_name, report_nm, receipt_no, summary
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
            return True

        except TelegramError as e:
            print(f"알림 발송 실패 ({attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

    print(f"알림 발송 최종 실패: {corp_name} - {report_nm}")
    return False


async def send_system_message(chat_id: str, text: str):
    """시스템 메시지 발송"""
    try:
        bot = get_bot()
        await bot.send_message(chat_id=chat_id, text=text)
    except TelegramError as e:
        print(f"시스템 메시지 발송 실패: {e}")
