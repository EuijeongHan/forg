# 검증 기록 — P0 파이프라인 가드 (CI · 자정 경계 · 자가 경보)

- **날짜**: 2026-07-21
- **대상**: 기획안(docs/planning/2026-07-21-critical-review.md) P0-1~P0-3
- **로드맵**: #26

## 변경 항목

| 항목 | 내용 | 파일 |
|---|---|---|
| P0-1 | GitHub Actions CI — PR/main push마다 테스트 4종 자동 실행 (Python 3.11) | .github/workflows/tests.yml |
| P0-2 | 자정 경계 누락 방지 — 폴링을 어제~오늘 **2일 창**으로 조회 (`fetch_recent_disclosures(days=2)`). 봇 조회 경로는 기본값 1(오늘만) 유지. 중복은 rcept_no unique + SeenDisclosure가 차단 | dart.py, tasks.py |
| P0-3 | 침묵 사망 자가 경보 — ① 폴링 연속 5회 실패 시 운영자 채팅 경보 1회(회복 시 리셋, 재발 시 재경보) ② 평일 08~19시 KST 공시 0건 10사이클 지속 시 경보 1회 | tasks.py |

## 행동 테스트 — 10/10 PASS (`tests/test_p0_pipeline_guards.py`)

| 케이스 | 기대 | 결과 |
|---|---|---|
| 폴링 조회 창 | `days=2`로 호출 | PASS |
| 연속 실패 4회 | 경보 없음 | PASS |
| 연속 실패 5회째 | 운영자 경보 1건 (수신자·문구 확인) | PASS |
| 6회째 | 중복 경보 없음 (스팸 방지) | PASS |
| 회복 후 재발 5회 | 재경보 발생 | PASS |
| 장중 0건 9사이클 | 경보 없음 | PASS |
| 10사이클째 | 경보 1건 | PASS |
| 11사이클째 | 중복 없음 | PASS |
| `kst_date_str(0/1)` | KST 오늘/어제 정확 | PASS ×2 |

기존 3종 회귀: services 14/14 · seen-dedup 10/10 · stage0 14/14 (스텁을 새 계약
`fetch_recent_disclosures(days=1)`·`send_system_message`에 맞춰 갱신 후 통과).

## 한계 (명시)
- 전일분 공시의 접수 시각은 조회하지 않음 → 어제 23:5x 감사보고서의 "야간 제출" 뱃지는
  누락될 수 있으나 **알림 자체는 발송됨** (tasks.py 주석 참조).
- 경보 상태는 프로세스 메모리 — 재시작 시 카운터 리셋(허용 가능한 손실).
- CI의 실제 동작은 이 PR부터 GitHub Actions 탭에서 확인.

## 재현
```bash
python tests/test_p0_pipeline_guards.py
```
