"""게이트 3 — 실제 DART 응답으로 재사용 후보 모듈 재검증.

검증 대상:
  A. list.json 연동 (수집 경로)
  B. 정형 API: 전환사채(cvbdIsDecsn)·유상증자(piicDecsn) 실응답 필드 계약
  C. normalizer가 실응답을 파싱하는지 (Decimal 정규화)
  D. 정정공시 제목 로직 (strip_title_prefixes / is_correction) + 원본 후보 실존재
  E. 접수시각 스크래핑 (search.ax 정규식)
  F. 공시 원문 추출 (viewer.do)
  G. corpCode.xml 기업코드 캐시
API 호출을 아끼기 위해 유형별 샘플 3건 이내로 제한.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import httpx  # noqa: E402

KEY = os.environ["DART_API_KEY"]
BASE = "https://opendart.fss.or.kr/api"
KST = ZoneInfo("Asia/Seoul")

results = []


def report(tag, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    results.append((mark, tag, detail))
    print(f"{mark} {tag}: {detail}")


async def list_range(client, bgn, end):
    items, page = [], 1
    while True:
        r = await client.get(f"{BASE}/list.json", params={
            "crtfc_key": KEY, "bgn_de": bgn, "end_de": end,
            "page_no": page, "page_count": 100}, timeout=20)
        data = r.json()
        if data.get("status") != "000":
            return items, data.get("status"), data.get("message")
        items.extend(data.get("list", []))
        if page >= int(data.get("total_page", 1)) or page >= 30:
            return items, "000", ""
        page += 1


async def main():
    now = datetime.now(KST)
    end = now.strftime("%Y%m%d")
    bgn = (now - timedelta(days=6)).strftime("%Y%m%d")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # --- A. list.json ---
        items, status, msg = await list_range(client, bgn, end)
        report("A list.json", status == "000" and len(items) > 0,
               f"status={status} {msg} 건수={len(items)} ({bgn}~{end})")
        if not items:
            return

        cb = [d for d in items if "전환사채" in d["report_nm"] and "정정" not in d["report_nm"]][:3]
        pi = [d for d in items if "유상증자" in d["report_nm"] and "정정" not in d["report_nm"]][:3]
        corrections = [d for d in items if d["report_nm"].lstrip().startswith("[") and "정정" in d["report_nm"].split("]")[0]][:5]
        report("A 샘플 확보", bool(cb or pi),
               f"CB={len(cb)} 유상증자={len(pi)} 정정={len(corrections)}")

        # --- B/C. 정형 API + normalizer ---
        from dart import fetch_typed_disclosure
        from events.normalizer import normalize_typed_disclosure

        for label, samples, must_have in (
            ("전환사채", cb, ("bd_fta", "cv_prc")),
            ("유상증자", pi, ("nstk_ostk_cnt", "nstk_ispr")),
        ):
            hit = 0
            for d in samples:
                typed = await fetch_typed_disclosure(
                    d["corp_code"], d["rcept_no"], d["report_nm"], d["rcept_dt"])
                if not typed:
                    print(f"  INFO {label} 정형응답 없음: {d['corp_name']} {d['rcept_no']}")
                    continue
                hit += 1
                missing = [k for k in must_have if k not in typed]
                norm = normalize_typed_disclosure(d["report_nm"], typed)
                nd = norm["normalized_data"]
                parsed = {k: v for k, v in nd.items() if v is not None}
                print(f"  INFO {label} {d['corp_name']}: 필드누락={missing or '없음'} "
                      f"정규화 non-null {len(parsed)}/{len(nd)} 예시={dict(list(parsed.items())[:3])}")
                if missing:
                    report(f"B {label} 필드 계약", False, f"{d['corp_name']} 누락 {missing}")
            if samples:
                report(f"B/C {label} 정형+정규화", hit > 0, f"{hit}/{len(samples)}건 정형응답·파싱 성공")
            else:
                print(f"  INFO {label}: 기간 내 샘플 없음")

        # --- D. 정정 제목 로직 + 원본 실존재 ---
        from services.correction_service import strip_title_prefixes, is_correction
        ok_d, found_orig = 0, 0
        for d in corrections:
            base_title = strip_title_prefixes(d["report_nm"])
            if is_correction(d["report_nm"]) and base_title and "[" not in base_title:
                ok_d += 1
            # 같은 회사의 과거 공시 중 접두어 제거 제목이 일치하는 원본 후보 검색 (list 데이터 내)
            orig = [o for o in items
                    if o["corp_code"] == d["corp_code"]
                    and o["rcept_no"] < d["rcept_no"]
                    and strip_title_prefixes(o["report_nm"]) == base_title]
            if orig:
                found_orig += 1
        if corrections:
            report("D 정정 제목 로직", ok_d == len(corrections), f"{ok_d}/{len(corrections)} 판정 정상")
            print(f"  INFO 원본 후보를 7일 창 내에서 찾은 정정: {found_orig}/{len(corrections)} (창 밖 원본은 정상적으로 미발견)")
        else:
            print("  INFO 기간 내 정정공시 샘플 없음")

        # --- E. 접수시각 스크래핑 ---
        from dart import fetch_rcept_times
        # 주말이면 최근 평일로
        probe = now
        while probe.weekday() >= 5:
            probe -= timedelta(days=1)
        times = await fetch_rcept_times(probe.strftime("%Y%m%d"))
        report("E 접수시각 스크래핑", len(times) > 0, f"{probe.strftime('%Y%m%d')} 기준 {len(times)}건 파싱")

        # --- F. 원문 추출 ---
        from dart import fetch_disclosure_detail
        target = (cb or pi or items)[0]
        text = await fetch_disclosure_detail(target["rcept_no"])
        report("F 원문 추출", len(text) > 200,
               f"{target['corp_name']} {len(text)}자 추출")

        # --- G. corpCode.xml ---
        r = await client.get(f"{BASE}/corpCode.xml", params={"crtfc_key": KEY}, timeout=60)
        ok_g = r.status_code == 200 and len(r.content) > 100_000
        report("G corpCode.xml", ok_g, f"{len(r.content):,} bytes")

    fails = [x for x in results if x[0] == "FAIL"]
    print(f"\n=== GATE3 SUMMARY: {len(results) - len(fails)}/{len(results)} passed ===")
    sys.exit(1 if fails else 0)


asyncio.run(main())
