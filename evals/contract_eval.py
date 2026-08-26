"""비정형(원문 크롤링) 요약 평가 — 공급계약 유형.

정형 카드는 숫자가 API 필드라 검증이 쉽지만, 공급계약은 정형 API가 없어
원문을 LLM이 읽는다. 즉 여기가 환각·누락이 실제로 발생하는 지점인데
평가가 없었다(2026-08-26 확인).

캐스케이드는 summary_eval과 같다:
  티어0(결정론, 무료): 필수 항목 커버리지 · 원문 대조 숫자 근거 · 투자의견 금칙
  티어1(judge, 상위 티어 모델): 충실성·누락 판정
--model로 생성 모델을 바꿔 같은 셋에서 비교한다.
"""
import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

JUDGE_MODEL = "gpt-5"

REQUIRED = {
    "계약금액": ("계약금액", "계약 금액"),
    "매출대비비율": ("매출액 대비", "매출대비", "최근 매출액"),
    "계약기간": ("계약기간", "계약 기간"),
    "거래상대방": ("거래상대방", "계약상대", "계약 상대"),
}

JUDGE_PROMPT = """당신은 공시 요약을 검증하는 심사자입니다. 아래 '공시 원문'만을 근거로
'요약'을 평가하세요. 원문에 없는 내용을 요약이 말하면 불충실입니다.

판정 기준:
1. faithful: 요약의 모든 사실·숫자가 원문으로 뒷받침되는가 (추정·창작 금지)
2. missing: 원문에 있는데 요약이 빠뜨린 핵심 항목 (계약금액, 최근 매출액 대비 비율,
   계약기간, 거래상대방) 목록. 원문에도 없으면 누락이 아니다.
3. opinion: 매수/매도/호재/악재/목표가 등 투자 판단 표현이 있는가

JSON만 출력:
{"faithful": true/false, "errors": ["..."], "missing": ["..."], "opinion": true/false}

=== 공시 원문 ===
%(content)s

=== 요약 ===
%(summary)s
"""


async def generate(model: str, corp_name: str, report_nm: str, content: str) -> str:
    """summarizer의 실제 프롬프트를 쓰되 생성 모델만 교체해 비교 가능하게 한다."""
    from openai import AsyncOpenAI
    import summarizer

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = summarizer.build_prompt(corp_name, report_nm, content)
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": summarizer.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    # 일부 모델은 max_tokens 대신 max_completion_tokens만 받는다. 고정된 SDK
    # (1.30.0)는 후자를 인자로 노출하지 않으므로 extra_body로 넘긴다.
    # 추론 모델은 예산을 사고에 먼저 쓴다 — 2000에서는 finish_reason=length로
    # 출력이 0자였다(2026-08-26 실측). 넉넉히 줘야 공정한 비교가 된다.
    try:
        r = await client.chat.completions.create(max_tokens=600, **kwargs)
    except Exception as e:
        if "max_tokens" not in str(e):
            raise
        r = await client.chat.completions.create(
            extra_body={"max_completion_tokens": 8000}, **kwargs)
    return r.choices[0].message.content or ""


async def judge(content: str, summary: str) -> dict | None:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        r = await client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": JUDGE_PROMPT % {
                "content": content[:4000], "summary": summary}}],
        )
        text = r.choices[0].message.content or ""
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:
        print(f"judge 실패: {type(e).__name__}: {e}")
        return None


def tier0(summary: str, content: str) -> dict:
    from verification.checks import check_investment_opinion, cross_check_amounts

    covered = {
        key: any(alias in summary for alias in aliases)
        for key, aliases in REQUIRED.items()
    }
    cross = cross_check_amounts(summary, {"content": content})
    return {
        "covered": covered,
        "coverage_rate": round(sum(covered.values()) / len(covered), 3),
        "ungrounded_amounts": [str(x) for x in cross["unverified_large"]],
        "opinion_violations": check_investment_opinion(summary),
        "empty": not summary.strip(),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="/evals/golden/contract_golden.jsonl")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--limit", type=int, default=18)
    ap.add_argument("--out-dir", default="/evals/results")
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args()

    items = [json.loads(l) for l in open(args.golden, encoding="utf-8") if l.strip()][:args.limit]
    print(f"평가 대상 {len(items)}건 · 생성 모델 {args.model}")

    rows = []
    for i, it in enumerate(items, 1):
        summary = await generate(args.model, it["corp_name"], it["report_nm"], it["content"])
        t0 = tier0(summary, it["content"])
        j = None if args.no_judge else await judge(it["content"], summary)
        rows.append({**{k: it[k] for k in ("rcept_no", "corp_name", "report_nm")},
                     "model": args.model, "summary": summary, "tier0": t0, "judge": j})
        mark = "".join("O" if v else "." for v in t0["covered"].values())
        print(f"  {i:2}/{len(items)} {it['corp_name'][:10]:10} 커버 {mark} "
              f"근거없는금액 {len(t0['ungrounded_amounts'])} "
              f"judge {'-' if not j else ('충실' if j.get('faithful') else '불충실')}")

    n = len(rows)
    judged = [r for r in rows if r["judge"]]
    agg = {
        "n": n,
        "model": args.model,
        "coverage_rate": round(sum(r["tier0"]["coverage_rate"] for r in rows) / n, 3),
        "field_coverage": {
            k: round(sum(1 for r in rows if r["tier0"]["covered"][k]) / n, 3)
            for k in REQUIRED
        },
        "ungrounded_amount_items": sum(1 for r in rows if r["tier0"]["ungrounded_amounts"]),
        "opinion_violations": sum(1 for r in rows if r["tier0"]["opinion_violations"]),
        "empty": sum(1 for r in rows if r["tier0"]["empty"]),
        "judge": {
            "model": JUDGE_MODEL,
            "judged": len(judged),
            "faithful_rate": round(sum(1 for r in judged if r["judge"].get("faithful")) / len(judged), 3) if judged else None,
            "items_with_missing": sum(1 for r in judged if r["judge"].get("missing")),
            "opinion_flags": sum(1 for r in judged if r["judge"].get("opinion")),
        },
    }
    print("\n" + json.dumps(agg, ensure_ascii=False, indent=2))

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = args.model.replace(".", "").replace("/", "-")
    stamp = datetime.now().strftime("%Y-%m-%d")
    with open(out / f"{stamp}-contract-eval-{tag}.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / f"{stamp}-contract-eval-{tag}.md", "w", encoding="utf-8") as f:
        f.write(f"# 공급계약 비정형 요약 평가 ({args.model})\n\n```json\n"
                + json.dumps(agg, ensure_ascii=False, indent=2) + "\n```\n")
    print(f"\n저장: {out}/{stamp}-contract-eval-{tag}.*")


if __name__ == "__main__":
    asyncio.run(main())
