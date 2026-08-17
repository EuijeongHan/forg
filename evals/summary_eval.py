"""요약 품질 평가 하네스 v1 — 정형(typed) 경로.

측정하는 것 (이슈 #4의 '답변' 층 중 지금 정답 데이터가 존재하는 부분):
  - 숫자 충실도: 요약 속 금액이 정형 API 값에 근거하는가
    (app/verification/checks.verify_summary — Stage 7 결정론 검증을 그대로 사용)
  - 투자의견 금칙 위반율 (법적 하드 룰)
  - 제공자 분포(openai/claude/gemini 폴백 체인 중 누가 응답했나)와 지연

계층 구조 (ADR-010 cascade + 운영 원칙 "판정은 생성보다 상위 티어"):
  - 티어 0: 결정론 체크 (무료, 전수) — 숫자 교차검증·금칙어
  - 티어 1: LLM judge — 정형 데이터를 참조로 주고 요약의 사실 왜곡을 판정.
    생성 체인이 gpt-4o-mini / claude-sonnet-4-5 / gemini-1.5-flash 이므로
    judge 기본값은 그보다 상위 티어인 gpt-5 ($1.25/$10 per 1M —
    상위 티어 중 최저가, 2026-08 확인. GPT-5.5는 $5/$30이라 제외).
    주의: 주 생성 모델과 같은 OpenAI 계열이라 자기선호 편향 가능성 있음 —
    judge 보정(교차 제공자 대조)은 v2 과제로 명시해 둔다.

측정하지 않는 것 (v1 한계, 문서화):
  - 비정형(크롤링) 경로 — 정답 데이터가 없어 judge조차 참조 없이 판정해야 함. v2.
  - 검색/툴콜링 층 — Stage 8 구현 후 Hit@k로 추가한다.

사용:
  python3 evals/summary_eval.py --golden evals/golden/typed_golden.jsonl --limit 30
결과는 evals/results/<날짜>-summary-eval.{jsonl,md} 로 저장하고,
수치는 이슈 #8(Eval Log)에 append한다.
"""
import argparse
import asyncio
import json
import pathlib
import statistics
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

KST = ZoneInfo("Asia/Seoul")


# 생성 체인(mini/sonnet-4-5/flash)보다 상위 티어이면서 저가 — 운영 원칙(판정>생성) + 비용
JUDGE_MODEL = "gpt-5"

JUDGE_PROMPT = """당신은 공시 요약의 사실 검증관입니다. 아래 [정형 데이터]가 사실의 전부입니다.

[정형 데이터]
{typed}

[검증 대상 요약]
{summary}

요약을 정형 데이터와 대조해 JSON으로만 답하세요:
{{"faithful": true/false, "errors": ["사실과 다르거나 근거 없는 주장 목록 (없으면 빈 배열)"], "opinion": true/false}}

판정 기준:
- 숫자·날짜·비율이 정형 데이터와 다르면 error
- 정형 데이터에 없는 사실 주장(추측·해석 제외한 단정)은 error
- 일반적 배경 설명이나 신중한 해석 문장은 error 아님
- 매수/매도/호재/악재/목표가 등 투자 판단 표현이 있으면 opinion=true"""


async def judge_with_llm(summary: str, typed_data: dict, model: str = JUDGE_MODEL):
    """상위 티어 모델로 요약 충실도 판정. 실패 시 None(가용 불가)을 돌려준다."""
    from openai import OpenAI

    def _call():
        client = OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": JUDGE_PROMPT.format(
                typed=json.dumps(typed_data, ensure_ascii=False),
                summary=summary,
            )}],
        )
        return resp.choices[0].message.content

    try:
        raw = await asyncio.to_thread(_call)
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start:end + 1])
    except Exception as e:
        print(f"judge 호출 실패: {type(e).__name__}: {e}")
        return None


async def evaluate_items(items, summarize_fn, verify_fn, provider_of=None, judge_fn=None):
    """골든 항목들을 요약→검증하고 항목별 결과를 돌려준다.

    summarize_fn(corp_name, report_nm, typed_data) -> str (async)
    verify_fn(summary, typed_data) -> {"verdict": ..., "checks": ...}   # 티어 0
    provider_of() -> 직전 호출에 응답한 제공자 이름 (없으면 None)
    judge_fn(summary, typed_data) -> {"faithful", "errors", "opinion"} | None  # 티어 1
    """
    results = []
    for item in items:
        t0 = time.monotonic()
        summary = await summarize_fn(
            item["corp_name"], item["report_nm"], item["typed_data"]
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        verdict = verify_fn(summary, item["typed_data"])
        judge = await judge_fn(summary, item["typed_data"]) if judge_fn else None
        results.append({
            "rcept_no": item.get("rcept_no"),
            "corp_name": item["corp_name"],
            "report_nm": item["report_nm"],
            "api": item.get("api"),
            "summary": summary,
            "verdict": verdict["verdict"],
            "checks": verdict["checks"],
            "judge": judge,
            "provider": provider_of() if provider_of else None,
            "latency_ms": latency_ms,
        })
    return results


def aggregate(results):
    """항목별 결과를 지표로 집계한다."""
    n = len(results)
    verdicts = {"pass": 0, "warning": 0, "fail": 0, "unavailable": 0}
    providers = {}
    latencies = []
    opinion_violations = 0
    judged = faithful = judge_opinions = 0
    for r in results:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        if r["checks"].get("investment_opinion"):
            opinion_violations += 1
        p = r.get("provider") or "none"
        providers[p] = providers.get(p, 0) + 1
        latencies.append(r["latency_ms"])
        j = r.get("judge")
        if j is not None:
            judged += 1
            faithful += 1 if j.get("faithful") else 0
            judge_opinions += 1 if j.get("opinion") else 0
    return {
        "n": n,
        "verdicts": verdicts,
        "numeric_pass_rate": round(verdicts["pass"] / n, 3) if n else None,
        "warning_rate": round(verdicts["warning"] / n, 3) if n else None,
        "opinion_violations": opinion_violations,
        "judge": {
            "model": JUDGE_MODEL,
            "judged": judged,
            "faithful_rate": round(faithful / judged, 3) if judged else None,
            "opinion_flags": judge_opinions,
        },
        "providers": providers,
        "latency_ms": {
            "avg": int(statistics.mean(latencies)) if latencies else None,
            "p50": int(statistics.median(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
    }


def render_report(metrics, results, meta):
    """이슈 #8에 붙여넣을 마크다운 리포트."""
    by_type = {}
    for r in results:
        k = r.get("api") or "?"
        d = by_type.setdefault(k, {"n": 0, "pass": 0})
        d["n"] += 1
        d["pass"] += 1 if r["verdict"] == "pass" else 0

    lines = [
        f"## 요약 평가 (typed 경로) — {meta['date']}",
        "",
        f"- 골든셋: {meta['golden']} · N={metrics['n']} · 코드: {meta['commit']}",
        f"- **[티어0 결정론] 숫자 충실도 pass율: {metrics['numeric_pass_rate']}** "
        f"(pass {metrics['verdicts']['pass']} / warning {metrics['verdicts']['warning']}"
        f" / fail {metrics['verdicts']['fail']})",
        f"- [티어0] 투자의견 금칙 위반: **{metrics['opinion_violations']}건**",
        f"- **[티어1 judge={metrics['judge']['model']}] 충실 판정율: "
        f"{metrics['judge']['faithful_rate']}** "
        f"(판정 {metrics['judge']['judged']}건, 의견 플래그 {metrics['judge']['opinion_flags']}건)"
        f" — 생성 체인보다 상위 티어로 판정(운영 원칙)",
        f"- 제공자 분포: {metrics['providers']}",
        f"- 지연(ms): avg {metrics['latency_ms']['avg']} · p50 {metrics['latency_ms']['p50']}"
        f" · max {metrics['latency_ms']['max']}",
        "",
        "| API 유형 | n | pass |",
        "|---|---|---|",
    ]
    for k, d in sorted(by_type.items()):
        lines.append(f"| {k} | {d['n']} | {d['pass']} |")
    warn = [
        r for r in results
        if r["verdict"] in ("warning", "fail")
        or (r.get("judge") and not r["judge"].get("faithful", True))
    ]
    if warn:
        lines += ["", "### 문제 항목 (티어0 warning/fail 또는 judge 불충실)"]
        for r in warn:
            uv = r["checks"].get("cross_check", {})
            uv = uv.get("unverified_large", []) if isinstance(uv, dict) else []
            jerr = (r.get("judge") or {}).get("errors", [])
            lines.append(
                f"- [{r['verdict']}] {r['corp_name']} · {r['report_nm'][:30]}"
                f" · 미근거 금액 {uv[:3]} · judge 지적 {jerr[:2]}"
            )
    return "\n".join(lines)


async def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="evals/golden/typed_golden.jsonl")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--out-dir", default="evals/results")
    ap.add_argument("--no-judge", action="store_true", help="티어1 judge 생략(결정론만)")
    args = ap.parse_args()

    import subprocess
    import summarizer
    from verification.checks import verify_summary

    # 제공자 추적: 폴백 체인의 어느 SDK가 실제 응답했는지 기록
    last = {"name": None}

    def _track(name, fn):
        async def wrapped(prompt):
            r = await fn(prompt)
            if r:
                last["name"] = name
            return r
        return wrapped

    summarizer.summarize_with_openai = _track("openai", summarizer.summarize_with_openai)
    summarizer.summarize_with_claude = _track("claude", summarizer.summarize_with_claude)
    summarizer.summarize_with_gemini = _track("gemini", summarizer.summarize_with_gemini)

    def provider_of():
        p, last["name"] = last["name"], None
        return p

    items = [json.loads(l) for l in open(args.golden, encoding="utf-8") if l.strip()]
    items = items[: args.limit]
    print(f"골든 {len(items)}건 평가 시작...")

    judge_fn = None if args.no_judge else judge_with_llm
    results = await evaluate_items(
        items, summarizer.summarize_typed_disclosure, verify_summary,
        provider_of, judge_fn=judge_fn,
    )
    metrics = aggregate(results)

    date = datetime.now(KST).strftime("%Y-%m-%d")
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=ROOT,
        ).stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"  # 컨테이너 등 git 없는 환경
    meta = {"date": date, "golden": args.golden, "commit": commit}

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{date}-summary-eval.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    report = render_report(metrics, results, meta)
    (out / f"{date}-summary-eval.md").write_text(report, encoding="utf-8")

    print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    print(f"\n리포트: {out / f'{date}-summary-eval.md'}")


if __name__ == "__main__":
    asyncio.run(_main())
