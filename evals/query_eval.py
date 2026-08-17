"""질의 축(Stage 8) 평가 — 검색 Hit율 + 인용 정확도.

골든 질의는 typed 골든셋의 실제 공시(rcept_no가 정답)에서 만든다.
에이전트가 실제 DART를 검색하므로 이것은 end-to-end 평가다.

지표:
  - retrieval_hit: 기대 공시(rcept_no)가 도구 검색 결과(last_tool_trace)에 포함됐나
  - citation: 최종 답변 텍스트에 기대 rcept_no가 인용됐나
  - tool_calls / latency

사용: python3 evals/query_eval.py [--tag rN]
"""
import argparse
import asyncio
import json
import pathlib
import statistics
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
KST = ZoneInfo("Asia/Seoul")

# (기업명, 질의 템플릿) — {corp}는 치환. 기대 rcept_no는 골든셋에서 찾는다.
QUERY_SPECS = [
    ("씨젠", "씨젠이 최근 한 달 사이에 자기주식 처분 결정한 공시 찾아줘", "tsstkDpDecsn"),
    ("한국카본", "한국카본 최근 합병 결정 공시 있어?", "cmpMgDecsn"),
    ("금호석유화학", "금호석유화학이 최근에 합병 공시 낸 거 찾아줘", "cmpMgDecsn"),
    ("덴티스", "덴티스 최근 전환사채 발행 공시 검색해줘", "cvbdIsDecsn"),
    ("브이티", "브이티 자기주식 취득 결정 최근 거 알려줘", "tsstkAqDecsn"),
    ("모아데이타", "모아데이타 최근 유상증자 공시 찾아줘", "piicDecsn"),
    ("SK네트웍스", "SK네트웍스 최근 한 달 자기주식 처분 공시", "tsstkDpDecsn"),
    ("이렘", "이렘이 최근 발행한 전환사채 공시 보여줘", "cvbdIsDecsn"),
]


def build_golden_queries():
    items = [json.loads(l) for l in open(ROOT / "evals/golden/typed_golden.jsonl", encoding="utf-8")]
    queries = []
    for corp, q, api in QUERY_SPECS:
        match = [i for i in items if i["corp_name"] == corp and i["api"] == api]
        if match:
            queries.append({"question": q, "corp": corp,
                            "expected_rcept": sorted(i["rcept_no"] for i in match)})
    return queries


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    from services import query_service as qs

    queries = build_golden_queries()
    print(f"골든 질의 {len(queries)}건 평가 시작...")
    results = []
    for i, g in enumerate(queries):
        chat_id = f"eval-{i}"
        qs.reset_session(chat_id)
        t0 = time.monotonic()
        answer = await qs.answer_query(chat_id, g["question"])
        latency_ms = int((time.monotonic() - t0) * 1000)

        retrieved = set()
        for t in qs.last_tool_trace:
            if t["tool"] == "search_disclosures" and isinstance(t["result"], list):
                retrieved |= {r.get("rcept_no") for r in t["result"]}
        hit = any(r in retrieved for r in g["expected_rcept"])
        cited = any(r in answer for r in g["expected_rcept"])
        results.append({
            **g, "answer": answer, "retrieved_n": len(retrieved),
            "tool_calls": len(qs.last_tool_trace),
            "retrieval_hit": hit, "citation": cited, "latency_ms": latency_ms,
        })
        print(f"  [{'HIT' if hit else 'MISS'}][{'CITE' if cited else '----'}] {g['question'][:40]}")

    n = len(results)
    metrics = {
        "n": n,
        "model": qs.QUERY_MODEL,
        "retrieval_hit_rate": round(sum(r["retrieval_hit"] for r in results) / n, 3),
        "citation_rate": round(sum(r["citation"] for r in results) / n, 3),
        "avg_tool_calls": round(sum(r["tool_calls"] for r in results) / n, 2),
        "latency_ms": {
            "avg": int(statistics.mean(r["latency_ms"] for r in results)),
            "p50": int(statistics.median(r["latency_ms"] for r in results)),
            "max": max(r["latency_ms"] for r in results),
        },
    }

    date = datetime.now(KST).strftime("%Y-%m-%d")
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, cwd=ROOT).stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"
    suffix = f"-{args.tag}" if args.tag else ""
    out = ROOT / "evals/results"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{date}-query-eval{suffix}.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    lines = [
        f"## 질의 평가 (Stage 8) — {date}",
        "",
        f"- 에이전트: {metrics['model']} native tool loop · N={n} · 코드: {commit}",
        f"- **검색 Hit율: {metrics['retrieval_hit_rate']}** (기대 공시가 도구 결과에 포함)",
        f"- **인용 정확도: {metrics['citation_rate']}** (답변에 기대 접수번호 인용)",
        f"- 평균 도구 호출: {metrics['avg_tool_calls']}회 · 지연 p50 {metrics['latency_ms']['p50']}ms",
        "",
        "### 실패 항목",
    ]
    fails = [r for r in results if not (r["retrieval_hit"] and r["citation"])]
    if not fails:
        lines.append("- 없음")
    for r in fails:
        lines.append(f"- [{'HIT' if r['retrieval_hit'] else 'MISS'}/"
                     f"{'CITE' if r['citation'] else 'NO-CITE'}] {r['question']}")
    (out / f"{date}-query-eval{suffix}.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\n리포트: {out / f'{date}-query-eval{suffix}.md'}")


if __name__ == "__main__":
    asyncio.run(main())
