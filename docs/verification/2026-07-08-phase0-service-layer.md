# 검증 기록 — Phase 0 서비스 계층 분리

- **날짜**: 2026-07-07 ~ 07-08
- **대상**: PR #20 (`af57ac2`) — bot.py 비즈니스 로직을 `app/services/`로 추출
- **이슈**: #2
- **위험 가설**: 리팩터 과정에서 세션/쿼리 로직 훼손, 임포트 체인 파손

## 1) 오프라인 서비스 계층 테스트 — 14/14 PASS
- 환경: Python 3.12 venv, SQLAlchemy 2.0.35 + aiosqlite, 임시 SQLite
- 스크립트: `tests/test_services_smoke.py` (당시 스크래치 버전을 리포로 이식)
- 케이스: 유저 생성·멱등성 / 키워드 set·split·clear / toggle_sync 전파(today→mytoday) /
  sync ON 시 set 동기화 / 워치리스트 추가(2건)·중복 skip / get_corp_codes /
  find_by_name(ilike) / remove(성공·미존재 False) / list
- 결과: **14/14 PASS** — INSERT/UPDATE/DELETE/SELECT 실제 실행 확인

## 2) 런타임 부팅 (Docker Compose + Postgres 16)
- `main → bot → services/* → models → database` 전 임포트 체인 정상
- `DB 초기화 완료` (create_all → Postgres 스키마 생성 성공)
- 판정: 리팩터에 임포트/구조 버그 없음 (있었다면 DB init 전에 크래시)

## 3) 텔레그램 커맨드 E2E (유효 토큰, Docker Compose)
- `텔레그램 봇 시작 완료` → `DART 폴링 시작` → 폴링 사이클 정상 반복
- 실사용 검증(DB 상태로 확정):
  - `/start` → `users`에 유저 row 생성
  - `/add` 검색·선택·등록 → `watchlist` 5건 (삼성전자·삼성바이오로직스·SK하이닉스·SKAI·SK)
  - `/list` `/today` `/mytoday` `/settings` 실행 — **Traceback 0**
- 오검출 해명: 반복되던 `SELECT users JOIN watchlist` 대량 쿼리는 충돌이 아니라
  폴링 파이프라인(tasks.py)의 공시별 대상자 조회(정상 동작)였음

## 재현
```bash
pip install -r requirements.txt -r requirements-dev.txt
python tests/test_services_smoke.py
# E2E: .env 준비 후 docker compose up → 봇에서 커맨드 순서대로 실행
```
