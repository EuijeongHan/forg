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
    "유상증자",       # 유상증자결정
    "무상증자",       # 무상증자결정
    "합병",           # 합병결정
    "분할",           # 분할결정
    "전환사채",       # 전환사채 발행
    "신주인수권",     # BW 발행
    "최대주주",       # 최대주주 변동
    "자기주식",       # 자사주 취득/소각
    "주식소각",       # 주식소각
    "주식병합",       # 주식병합
    "상장폐지",       # 상장폐지
    "감사보고서",     # 감사보고서
    "횡령",           # 횡령
    "배임",           # 배임
    "공개매수",       # 공개매수
    "주식교환",       # 주식교환
    "감자",           # 감자
]

# 폴링 주기 (초)
POLLING_INTERVAL = 60
