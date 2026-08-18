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

TRUNCATION_NOTICE = "\n\n... (내용이 잘렸습니다. 원문을 확인해주세요.)"


def build_disclosure_message(
    corp_name: str, report_nm: str, receipt_no: str, summary: str,
    budget: int = MAX_MESSAGE_LENGTH,
) -> str:
    """공시 상세 HTML 메시지 조립 — 모든 사용자/공시 문자열은 여기서 이스케이프한다.

    자동 알림(send_alert)과 봇 조회(/today 상세)가 공용으로 사용한다.

    길이 초과 시 완성된 HTML을 자르지 않고 요약 '평문'을 줄여 재조립한다.
    태그·엔티티 중간이 잘린 HTML은 텔레그램이 거부하고, 발송 실패는 매 폴링
    재시도되므로 그 공시는 영구 미발송이 된다(2026-08-19 리뷰 P1). 원문
    링크는 어떤 경우에도 보존한다 — "원문을 확인해주세요"라는 안내가 링크를
    지운 채 나가면 안 된다.
    """
    dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"

    def assemble(summary_html: str) -> str:
        return (
            f"🏢 <b>{escape_html(corp_name)}</b>\n"
            f"📋 {escape_html(report_nm)}\n\n"
            f"📝 <b>요약</b>\n"
            f"{summary_html}\n\n"
            f'🔗 <a href="{dart_url}">원문 보기</a>'
        )

    message = assemble(escape_html(summary))
    if len(message) <= budget:
        return message

    # 이스케이프 팽창(& → &amp; 등)이 있으므로 항상 '조립 결과' 길이로 판정하며,
    # 평문을 초과분 이상씩 줄여가므로 루프는 유한하다.
    cut = summary
    while cut:
        message = assemble(escape_html(cut) + escape_html(TRUNCATION_NOTICE))
        if len(message) <= budget:
            return message
        cut = cut[: len(cut) - max(len(message) - budget, 16)]
    return assemble(escape_html(TRUNCATION_NOTICE.strip()))

# 등급별 머리말. 🚨는 긴급 전용으로 예약한다 — 모든 알림이 🚨면 아무것도 긴급하지 않다.
TIER_HEADERS = {
    "urgent": "🚨 <b>긴급 — 시장 중대 공시</b>\n워치리스트와 무관하게 전체 시장에서 발송되는 알림입니다.",
    "notice": "📌 <b>시장 공지</b>",
    "important": "⚠️ <b>중요 공시 알림</b>",
}


async def send_alert(
    chat_id: str,
    corp_name: str,
    report_nm: str,
    receipt_no: str,
    summary: str,
    tier: str = "important",
) -> bool:
    """텔레그램 공시 알림 발송 (재시도 포함). 성공 여부를 반환한다.

    반환값이 False면 호출측(tasks.py)은 SeenDisclosure를 기록하지 않아
    다음 폴링에서 재시도된다.

    tier: "urgent"(시장 전체 중대) | "notice"(시장 공지) | "important"(워치리스트).
    긴급은 사용자가 일반 알림을 무음으로 돌려도 항상 소리가 나도록
    disable_notification=False를 명시한다.
    """
    header = TIER_HEADERS.get(tier, TIER_HEADERS["important"])
    # 길이 제한은 build_disclosure_message가 요약 평문 단위로 처리한다(HTML 무손상)
    message = header + "\n\n" + build_disclosure_message(
        corp_name, report_nm, receipt_no, summary,
        budget=MAX_MESSAGE_LENGTH - len(header) - 2,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            bot = get_bot()
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_notification=False,
            )
            print(f"알림 발송 완료 [{tier}]: {corp_name} - {report_nm}")
            return True

        except TelegramError as e:
            print(f"알림 발송 실패 ({attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

    print(f"알림 발송 최종 실패: {corp_name} - {report_nm}")
    return False


async def send_html_message(chat_id: str, html_text: str) -> bool:
    """HTML 메시지 단건 발송 (다이제스트용). 호출측이 escape_html을 책임진다.

    길이 초과 시 '줄 경계'에서 자른다 — 다이제스트는 한 줄이 완결된 HTML
    (<b>…</b>, <a>…</a>)이므로 줄 단위 절단은 태그를 깨지 않는다. 임의
    인덱스 절단은 태그 중간을 잘라 발송 자체가 거부된다(리뷰 P1과 동일).
    """
    if len(html_text) > MAX_MESSAGE_LENGTH:
        notice = "\n\n... (이하 생략 — DART에서 확인해주세요.)"
        cut = html_text[: MAX_MESSAGE_LENGTH - len(notice)]
        if "\n" in cut:
            cut = cut[: cut.rfind("\n")]
        html_text = cut + notice
    try:
        bot = get_bot()
        await bot.send_message(chat_id=chat_id, text=html_text, parse_mode=ParseMode.HTML)
        return True
    except TelegramError as e:
        print(f"다이제스트 발송 실패: {e}")
        return False


async def send_system_message(chat_id: str, text: str):
    """시스템 메시지 발송"""
    try:
        bot = get_bot()
        await bot.send_message(chat_id=chat_id, text=text)
    except TelegramError as e:
        print(f"시스템 메시지 발송 실패: {e}")
