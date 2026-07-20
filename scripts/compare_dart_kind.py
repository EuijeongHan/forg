"""DART vs KIND 일자별 공시 비교 리포트 — 계획서 §6.2, Stage 5 선행 조건.

현재 상태: DART 측 수집·집계는 동작. KIND 측은 **실제 응답 형식을 브라우저
개발자도구로 확인하기 전까지 구현하지 않는다**(계획서 §6.1 — 추측 금지).
KIND 확인 절차:
  1. kind.krx.co.kr 공시 검색 화면에서 날짜별 목록 요청의 실제 URL·파라미터 확인
  2. 응답(HTML/JSON) 샘플을 tests/fixtures/kind/에 저장(민감정보 없음 확인)
  3. fixture 기반 parser 단위 테스트 작성 후 fetch_kind_for_date 구현

실행: python scripts/compare_dart_kind.py [YYYYMMDD=오늘]
"""
import asyncio
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import httpx  # noqa: E402
from config import DART_API_KEY  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


async def fetch_dart_for_date(client: httpx.AsyncClient, day: str) -> list[dict]:
    items, page = [], 1
    while True:
        r = await client.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={"crtfc_key": DART_API_KEY, "bgn_de": day, "end_de": day,
                    "page_no": page, "page_count": 100},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "013":
            break
        if data.get("status") != "000":
            raise RuntimeError(f"DART status {data.get('status')}")
        items.extend(data.get("list", []))
        if page >= int(data.get("total_page", 1)):
            break
        page += 1
    return items


async def fetch_kind_for_date(client: httpx.AsyncClient, day: str) -> list[dict]:
    raise NotImplementedError(
        "KIND 실제 응답 형식을 확인한 뒤 구현하세요 (모듈 docstring의 절차 참조)."
    )


async def main(day: str):
    if not DART_API_KEY:
        sys.exit("DART_API_KEY가 없습니다 (.env 확인)")
    async with httpx.AsyncClient() as client:
        dart_items = await fetch_dart_for_date(client, day)
        by_cls = Counter(it.get("corp_cls", "?") for it in dart_items)
        print(f"날짜: {day}")
        print(f"DART 공시: {len(dart_items)}건 (법인구분: {dict(by_cls)})")
        try:
            kind_items = await fetch_kind_for_date(client, day)
            print(f"KIND 공시: {len(kind_items)}건")
            # TODO: 공통/각측 고유 비교 — KIND 구현 후
        except NotImplementedError as e:
            print(f"KIND: 미구현 — {e}")


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(KST).strftime("%Y%m%d")
    asyncio.run(main(day))
