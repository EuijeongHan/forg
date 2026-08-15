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
    # 2026-08-16 커버리지 결함 수정: 아래 유형이 빠져 상장사 공시의 87%가
    # 버려지고 있었다(실측). 워치리스트 기업의 실적·5%룰·내부자 매매를
    # 알리지 못하는 알림 서비스는 애널리스트에게 결격이다.
    "대량보유",      # 주식등의대량보유상황보고서 (5%룰)
    "주요주주",      # 임원ㆍ주요주주특정증권등소유상황보고서 (내부자 매매)
    "영업(잠정)실적",  # 연결재무제표기준영업(잠정)실적(공정공시) — 괄호 포함이 실제 표기
    "손익구조",      # 매출액또는손익구조 30%(대규모법인 15%) 이상 변동
    "배당",          # 현금ㆍ현물배당결정
    "투자판단",      # 투자판단관련주요경영사항
]

# 폴링 주기 (초)
POLLING_INTERVAL = 60

# LLM 일일 호출 한도 (비용 가드레일) — 초과 시 요약 생략, 카드/제목만 발송
LLM_DAILY_CALL_LIMIT = int(os.getenv("LLM_DAILY_CALL_LIMIT", "500"))

# 이벤트 정규화 저장 (Stage 2 기반) — 기본 OFF. 알림 내용에는 영향 없음(DB 기록만).
ENABLE_EVENT_CARDS = os.getenv("ENABLE_EVENT_CARDS", "false").lower() == "true"
