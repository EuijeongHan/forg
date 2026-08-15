"""알림 등급 분류 — 2026-08-16 실측 데이터로 검증된 규칙.

설계 원칙 (docs/planning/2026-07-27-product-rebuild-plan.md + 2026-08-16 결정):
  1. 워치리스트 기업 공시는 절대 버리지 않는다 — 등급만 나눈다.
  2. 제외 규칙은 삭제가 아니라 강등이다. '거래정지 해제'는 시장 전체 알림에서만
     빠지고, 워치리스트 기업이면 여전히 전달된다(정상화도 알아야 할 소식).
  3. 긴급(시장 전체)은 기본 ON — 놓침 방지 기능을 옵트인으로 두지 않는다.

실측 근거 (10영업일, 상장사 6,440건):
  긴급 일 0.6건 / 시장 공지 일 0.8건 / 제외된 노이즈 일 3.8건
  (해제·기간변경·전자등록 기술정지 등 — 반대 의미 오탐)
"""
import re

# ── 시장 전체 등급 (워치리스트 무관, 유가·코스닥만) ──────────────────────────

# 🚨 긴급: 확정된 중대 사건. LLM 예산 우회 + 무음 해제 + 사이클 최우선.
URGENT_PATTERNS = [
    r"회생절차",        # 개시신청·개시결정·출자법인 회생
    r"횡령",            # 실제 공시명: 횡령ㆍ배임혐의발생 (ㆍ = U+318D)
    r"배임",
    r"부도",
    r"상장폐지\s*결정",   # "상장폐지결정"과 "상장공시위원회 … 상장폐지 결정" 모두
    r"정리매매",
]

# 📌 시장 공지: 확정 전 단계지만 시장 전체가 알아야 할 신호.
MARKET_NOTICE_PATTERNS = [
    r"상장폐지",        # 우려 예고·개선기간 종료 안내·심사 (확정은 위에서 이미 잡힘)
    r"주권매매거래정지",  # 실질적 정지 발생만 — 아래 제외어가 거른다
]

# 시장 등급에서 제외(강등). 반대 의미(해제=정상화)와 기술적 절차(액면분할 등).
MARKET_EXCLUDE_PATTERNS = [
    r"해제",
    r"기간변경",
    r"전자등록",         # "주식의 병합, 분할 등 전자등록 변경, 말소"
    r"액면병합",
    r"액면분할",
    r"변경상장",
    r"미진행",           # "상장폐지 절차 미진행" = 반대 의미
]

# 상장 구분: 유가(Y)·코스닥(K)만 시장 전체 알림 대상. 코넥스·기타는 제외.
LISTED_MARKETS = {"Y", "K"}


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def classify_market_tier(report_nm: str, corp_cls: str | None = None) -> str | None:
    """시장 전체 알림 등급. "urgent" | "notice" | None.

    None이어도 워치리스트 경로(is_important)에서 다시 판정되므로
    여기서의 제외는 삭제가 아니라 강등이다(원칙 2).
    """
    if corp_cls is not None and corp_cls not in LISTED_MARKETS:
        return None
    name = report_nm or ""
    if _any(MARKET_EXCLUDE_PATTERNS, name):
        return None
    if _any(URGENT_PATTERNS, name):
        return "urgent"
    if _any(MARKET_NOTICE_PATTERNS, name):
        return "notice"
    return None


def sort_key(disclosure: dict) -> int:
    """폴링 사이클 내 처리 순서 — 긴급 먼저. 같은 사이클에 공시가 몰려도
    중대 사건이 일반 공시 뒤에서 대기하지 않게 한다."""
    tier = classify_market_tier(
        disclosure.get("report_nm", ""), disclosure.get("corp_cls")
    )
    return {"urgent": 0, "notice": 1}.get(tier, 2)
