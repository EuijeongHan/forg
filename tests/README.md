# tests/ — 행동 검증 테스트

프로덕션 파이프라인의 핵심 동작을 외부 서비스 없이 검증하는 스크립트 모음.
DB는 임시 SQLite(aiosqlite), 외부 의존(DART·LLM·텔레그램)은 스텁으로 대체한다.
각 실행 결과는 `docs/verification/`에 날짜·SHA와 함께 기록한다.

## 실행 방법

```bash
# 의존성: 프로덕션 requirements + 테스트 전용(requirements-dev.txt)
pip install -r requirements.txt -r requirements-dev.txt

python tests/test_services_smoke.py      # 서비스 계층 DB 로직 (user/watchlist)
python tests/test_seen_dedup.py          # I-1 복합 unique + I-2 요약 재생성 방지
python tests/test_stage0_hardening.py    # I-3 발송실패 재시도 + I-4 이스케이프 + I-5 KST
```

각 스크립트는 독립 실행형이다: PASS/FAIL 목록과 `SUMMARY: n/m passed`를 출력하고,
전부 통과하면 exit 0, 하나라도 실패하면 exit 1을 반환한다.

## 다루지 않는 것

- **PostgreSQL alembic 마이그레이션**: docker compose db가 필요해 스크립트화하지 않았다.
  수동 절차와 실행 기록은 `docs/verification/2026-07-21-seen-dedup-migration.md` 참조.
- **텔레그램 실제 발송 E2E**: 유효 토큰 + 실제 유저 상호작용 필요.
  절차는 `docs/verification/2026-07-08-phase0-service-layer.md` 참조.

## 규약

- 새 파이프라인 수정 PR은 해당 동작의 테스트를 여기 추가하거나 갱신한다.
- 실행 결과는 `docs/verification/<날짜>-<주제>.md`로 기록한다 (커밋 SHA 포함).
- 평가 하네스 지표(툴콜링·검색·답변)는 별도 규약을 따른다: 이슈 #8 + `eval/reports/`.
