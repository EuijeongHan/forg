from dotenv import load_dotenv
import os

load_dotenv()

DART_API_KEY = os.getenv("DART_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# 중요 공시 유형 필터
IMPORTANT_REPORT_TYPES = [
    "유상증자",
    "무상증자",
    "합병",
    "분할",
    "전환사채",
    "신주인수권",
    "교환사채",
    "최대주주",
    "자기주식",
    "주식소각",
    "주식병합",
    "상장폐지",
    "감사보고서",
    "횡령",
    "배임",
    "공개매수",
    "주식교환",
    "감자",
    "사업목적변경",
    "소송",
    "거래정지",
    "단일판매",
    "풋백옵션",
    "영업정지",
    "회생절차",
    "부도",
]

# 폴링 주기 (초)
POLLING_INTERVAL = 60
