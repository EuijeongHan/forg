# 검증 기록 — Stage 0 잔여 (I-3~I-7)

- **날짜**: 2026-07-21
- **대상**: PR #27 (`2d48319`) — 발송 신뢰성·이스케이프 통일·KST·동기호출 오프로드
- **로드맵**: #26 Stage 0

## 변경 항목

| 항목 | 내용 | 파일 |
|---|---|---|
| I-3 | `send_alert` bool 반환, 성공 시에만 Seen 기록(실패 → 다음 폴링 재시도) + 공시 단위 커밋 | notifier.py, tasks.py |
| I-4 | `build_disclosure_message` 단일 조립점(이스케이프 내장) — 자동 알림·`/today` 상세 공용 | notifier.py, bot.py |
| I-5 | `dart.today_kst()`(Asia/Seoul) — UTC 컨테이너에서 00~09시 KST 아침 공시 지연/누락 방지, D-day도 KST | dart.py, tasks.py, summarizer.py |
| I-6 | SQLAlchemy `echo=False` | database.py |
| I-7 | Claude·Gemini 동기 SDK `asyncio.to_thread` 오프로드 | summarizer.py |

## 행동 테스트 — 14/14 PASS
- 스크립트: `tests/test_stage0_hardening.py`
- 파트 1 (SQLite+스텁, process_disclosures 구동):

| 케이스 | 기대 | 결과 |
|---|---|---|
| userB 발송 실패 사이클 | A만 Seen 기록, B 미기록·시도는 됨 | PASS ×3 |
| B 복구 후 사이클 | B 재시도·전달·기록(총 2행), 요약 1회 재생성 | PASS ×3 |
| 전원 완료 사이클 | 요약 재생성 0·발송 시도 0 (I-2 회귀 없음) | PASS ×2 |

- 파트 2 (실제 notifier 모듈): corp/report/summary의 `&<>` 이스케이프,
  원문 링크 포함, 허용 태그(`<b>`,`<a>`) 외 raw `<` 부재 — PASS ×5
- 파트 3 (실제 dart 모듈): `today_kst()` == Asia/Seoul 오늘 — PASS ×1

## 검증하지 않은 것 (한계 명시)
- I-6/I-7은 행동 테스트 미작성(외부 SDK 필요) — py_compile + 코드 리뷰 수준.
  I-7은 `asyncio.to_thread` 표준 패턴 적용으로 위험도 낮음.
- 텔레그램 실발송 재시도 경로(TelegramError 실제 발생)는 스텁으로 대체.

## 재현
```bash
pip install -r requirements.txt -r requirements-dev.txt
python tests/test_stage0_hardening.py
```
