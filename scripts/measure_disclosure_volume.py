"""최근 N일 DART 공시 볼륨 측정 — 기획안 P1-4의 의사결정 데이터.

용도: '대량보유(5%룰)·임원 소유보고를 IMPORTANT_REPORT_TYPES에 추가하면
알림량이 얼마나 늘어나는가'를 추측이 아니라 실측으로 답한다.

실행 (forg-git/에서, .env의 DART_API_KEY 사용):
    python scripts/measure_disclosure_volume.py [일수=7]

- 읽기 전용(list.json GET만). API 키는 출력하지 않는다.
- status 000/013 규약 준수 (CLAUDE.md §4.1).
"""
import asyncio
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import httpx  # noqa: E402
from config import DART_API_KEY, IMPORTANT_REPORT_TYPES  # noqa: E402

KST = ZoneInfo("Asia/Seoul")

# 측정 그룹: 라벨 → report_nm 부분 문자열 (매칭 규칙은 dart.is_important와 동일 방식)
GROUPS = {
    "대량보유(5%룰)": ["대량보유"],
    "임원·주요주주 소유": ["소유상황"],
    "정정공시": ["정정"],
    "현행 중요필터 매칭": IMPORTANT_REPORT_TYPES,
}


async def fetch_day(client: httpx.AsyncClient, day: str) -> list[dict]:
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
        status = data.get("status")
        if status == "013":
            break
        if status != "000":
            print(f"  {day}: DART status {status} {data.get('message','')} — 중단")
            break
        items.extend(data.get("list", []))
        if page >= int(data.get("total_page", 1)):
            break
        page += 1
    return items


def match_any(report_nm: str, keywords: list[str]) -> bool:
    return any(k in report_nm for k in keywords)


async def main(days: int):
    if not DART_API_KEY:
        sys.exit("DART_API_KEY가 없습니다 (.env 확인)")
    today = datetime.now(KST)
    day_list = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]

    per_day: dict[str, Counter] = {}
    totals = Counter()
    async with httpx.AsyncClient() as client:
        for day in day_list:
            items = await fetch_day(client, day)
            c = Counter(total=len(items))
            for it in items:
                nm = it.get("report_nm", "")
                for label, kws in GROUPS.items():
                    if match_any(nm, kws):
                        c[label] += 1
            per_day[day] = c
            totals.update(c)
            print(f"{day}: 전체 {c['total']:5d} | " + " | ".join(
                f"{label} {c[label]:4d}" for label in GROUPS))

    n = len(day_list)
    print("\n=== 요약 (일평균, %는 전체 대비) ===")
    total_avg = totals["total"] / n if n else 0
    print(f"전체 공시: {totals['total']}건 (일평균 {total_avg:.0f})")
    for label in GROUPS:
        cnt = totals[label]
        pct = cnt / totals["total"] * 100 if totals["total"] else 0
        print(f"{label}: {cnt}건 (일평균 {cnt/n:.1f}, {pct:.1f}%)")
    print("\n해석 가이드: '대량보유+임원소유' 건수는 전체 시장 기준이다. forG 알림은")
    print("워치리스트 기업 한정이므로 실제 사용자 체감 증가분은 (해당 건수 × 워치 비율).")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    asyncio.run(main(days))
