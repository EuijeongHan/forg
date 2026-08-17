"""정형(typed) 골든셋 빌더 — 실제 DART 응답으로 평가용 정답 데이터를 만든다.

최근 N일의 상장사 공시 중 정형 API가 있는 유형(dart.TYPED_APIS)을 골라
정형 응답(수치 필드 dict)을 수집한다. 문서 전문이 아니라 수치 필드만 저장한다
(raw_typed_data와 동일한 저작권·용량 정책).

사용:
  python3 evals/build_golden_typed.py --days 14 --per-type 6
출력: evals/golden/typed_golden.jsonl (커밋 대상 — 평가 재현성)
"""
import argparse
import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


async def build(days: int, per_type: int, out_path: str):
    from dart import fetch_recent_disclosures, fetch_typed_disclosure, get_api_for_report

    disclosures = await fetch_recent_disclosures(days=days)
    listed = [d for d in disclosures if d.get("corp_cls") in ("Y", "K")]
    print(f"최근 {days}일 상장사 공시 {len(listed)}건")

    by_api: dict[str, list] = {}
    for d in listed:
        api = get_api_for_report(d.get("report_nm", ""))
        if not api:
            continue
        if len(by_api.get(api, [])) >= per_type:
            continue
        typed = await fetch_typed_disclosure(
            d.get("corp_code", ""), d.get("rcept_no", ""),
            d.get("report_nm", ""), d.get("rcept_dt", ""),
        )
        if not typed:
            continue
        by_api.setdefault(api, []).append({
            "rcept_no": d.get("rcept_no"),
            "corp_name": d.get("corp_name"),
            "corp_code": d.get("corp_code"),
            "report_nm": d.get("report_nm"),
            "rcept_dt": d.get("rcept_dt"),
            "corp_cls": d.get("corp_cls"),
            "api": api,
            "typed_data": typed,
        })
        print(f"  + {api:14s} {d.get('corp_name','')[:12]:12s} {d.get('report_nm','')[:30]}")

    items = [x for v in by_api.values() for x in v]
    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for x in items:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    print(f"\n골든셋 {len(items)}건 저장: {out}")
    for api, v in sorted(by_api.items()):
        print(f"  {api:14s} {len(v)}건")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--per-type", type=int, default=6)
    ap.add_argument("--out", default="evals/golden/typed_golden.jsonl")
    args = ap.parse_args()
    asyncio.run(build(args.days, args.per_type, args.out))
