# 검증 기록 — 사용자별 중복발송 스키마 수정 + LLM 재호출 제거

- **날짜**: 2026-07-21
- **대상**: PR #23 (`765132a`) — I-1 복합 unique + alembic 도입, I-2 요약 재생성 제거
- **이슈**: #22
- **결함 요약**:
  - I-1: `SeenDisclosure.receipt_no` 단독 unique → 두 번째 사용자 삽입 시 UniqueViolation
    → 폴링 커밋 전체 롤백 → **매 60초 전원 재발송 무한 반복** (테스터 2인부터 발현)
  - I-2: 요약 생성(정형 API+LLM)이 발송여부 체크보다 먼저 → 발송 완료된 공시도
    **매 폴링마다 자정까지 LLM 재호출** (공시 1건당 일 수백 회 낭비)

## 1) SQLite 행동 테스트 — 10/10 PASS
- 스크립트: `tests/test_seen_dedup.py` (process_disclosures 직접 구동, 외부의존 스텁)
- 케이스·결과:

| # | 케이스 | 기대 | 결과 |
|---|---|---|---|
| 1 | 2인 워치·사이클1 | 요약 1회, 알림 2건, Seen 2행(같은 receipt) | PASS |
| 2 | 사이클2(전원 발송됨) | 요약·정형API 재호출 0, 알림 0 | PASS |
| 3 | 신규 유저 후 사이클3 | 신규에게만 알림, 요약 1회만 추가 | PASS |
| 4 | (receipt,chat) 중복 삽입 | IntegrityError 거부 | PASS |

## 2) 실제 PostgreSQL 마이그레이션 검증 (수동 절차)
- 대상: docker compose 볼륨에 남아 있던 **구 스키마 + 실데이터 DB**
  (`seen_disclosures_receipt_no_key` 단독 unique, seen 1행 — 프로덕션과 동일 조건)
- 절차: `docker compose up -d db` → venv에서 `alembic upgrade head`
  (`DATABASE_URL=postgresql+asyncpg://…@localhost:5432/forg`)
- 결과:

| 확인 항목 | 결과 |
|---|---|
| `seen_disclosures_receipt_no_key` 제거 | ✅ |
| `uq_seen_disclosure_receipt_chat` (receipt_no, chat_id) 생성 | ✅ |
| `ix_seen_disclosures_receipt_no` 인덱스 생성 | ✅ |
| `alembic_version` = `0001_seen_dedup` 스탬프 | ✅ |
| 기존 데이터 보존 (1행 유지) | ✅ |
| 멱등성: upgrade 재실행 3회 무오류 | ✅ |
| 동일 receipt·다른 유저 INSERT 허용 (2행) | ✅ |
| 동일 (receipt, chat) INSERT → `uq_…` 위반으로 거부 | ✅ |

- 후처리: 테스트 행/유저 삭제, db 컨테이너 정지
- 참고: 마이그레이션은 PostgreSQL 전용 DO 블록. SQLite에서 `init_db` 실행 시
  "DB 마이그레이션 적용 실패 … near 'DO'" 로그 후 기동 계속 — **의도된 폴백**
  (적용 실패가 기동을 막지 않는 설계, 프로덕션 악화 없음)

## 재현
```bash
python tests/test_seen_dedup.py                       # 행동 테스트
# PG 마이그레이션: db 기동 후
DATABASE_URL=postgresql+asyncpg://forg:forg1234@localhost:5432/forg \
  python -c "from pathlib import Path; from alembic import command; from alembic.config import Config; \
h=Path('app'); c=Config(str(h/'alembic.ini')); c.set_main_option('script_location', str(h/'migrations')); \
command.upgrade(c,'head')"
```
