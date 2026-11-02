## 요약 평가 (typed 경로) — 2026-11-02

- 골든셋: evals/golden/typed_golden_holdout2.jsonl · N=35 · 코드: 4604159
- **[티어0 결정론] 숫자 충실도 pass율: 1.0** (pass 35 / warning 0 / fail 0)
- [티어0] 투자의견 금칙 위반: **0건**
- **[티어1 judge=gpt-5] 충실 판정율: 0.971** (판정 35건, 의견 플래그 0건) — 생성 체인보다 상위 티어로 판정(운영 원칙)
- 제공자 분포: {'none': 35}
- 지연(ms): avg 1 · p50 0 · max 61

| API 유형 | n | pass |
|---|---|---|
| bdwtIsDecsn | 1 | 1 |
| cmpMgDecsn | 6 | 6 |
| crDecsn | 2 | 2 |
| cvbdIsDecsn | 6 | 6 |
| exbdIsDecsn | 2 | 2 |
| piicDecsn | 6 | 6 |
| tsstkAqDecsn | 6 | 6 |
| tsstkDpDecsn | 6 | 6 |

### 문제 항목 (티어0 warning/fail 또는 judge 불충실)
- [pass] 광진실업 · 주요사항보고서(전환사채권발행결정) · 미근거 금액 [] · judge 지적 ["'전환청구 개시까지 D-379'가 사실과 다름: 오늘(2026-11-02)부터 전환청구 시작일(2027-11-16)까지는 D-378이 맞음"]