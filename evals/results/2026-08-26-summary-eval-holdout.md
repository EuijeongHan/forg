## 요약 평가 (typed 경로) — 2026-08-26

- 골든셋: /evals/golden/typed_golden_holdout.jsonl · N=28 · 코드: unknown
- **[티어0 결정론] 숫자 충실도 pass율: 0.964** (pass 27 / warning 1 / fail 0)
- [티어0] 투자의견 금칙 위반: **0건**
- **[티어1 judge=gpt-5] 충실 판정율: 0.929** (판정 28건, 의견 플래그 0건) — 생성 체인보다 상위 티어로 판정(운영 원칙)
- 제공자 분포: {'openai': 28}
- 지연(ms): avg 1783 · p50 1611 · max 3863

| API 유형 | n | pass |
|---|---|---|
| cmpDvDecsn | 1 | 1 |
| cmpMgDecsn | 6 | 6 |
| cvbdIsDecsn | 6 | 5 |
| piicDecsn | 6 | 6 |
| tsstkAqDecsn | 5 | 5 |
| tsstkDpDecsn | 4 | 4 |

### 문제 항목 (티어0 warning/fail 또는 judge 불충실)
- [pass] SK이노베이션 · 주요사항보고서(회사합병결정) · 미근거 금액 [] · judge 지적 ["주주총회 소집 여부를 '예'로 기재했으나, 정형 데이터에는 '아니오'로 기재되어 있음"]
- [warning] 서진시스템 · 주요사항보고서(전환사채권발행결정) · 미근거 금액 [Decimal('30000000000000')] · judge 지적 ['"자금목적은 30,000,000,000,000원입니다." → 사실과 다름. 정형 데이터상 운영자금은 30,000,000,000원임(fdpp_op).']