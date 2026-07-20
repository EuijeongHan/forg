# forG 도커·서버 사용 설명서 (운영자용)

> 대상: forG 운영자 본인. 도커를 깊게 몰라도 이 문서만으로 로컬 실행·중지·로그 확인·문제 해결이
> 되는 것이 목표. 마지막 장은 "서버가 더 필요해지면 어떻게 하나"의 단계별 가이드.
> 작성: 2026-07-21 (기준 코드 main `301e5b3`)

---

## 1. 개념 3개만 (이것만 알면 됨)

| 용어 | 비유 | forG에서 |
|---|---|---|
| **이미지** | 프로그램 설치 CD | `Dockerfile`로 구운 "파이썬+의존성+앱코드" 묶음 |
| **컨테이너** | CD를 넣고 켠 컴퓨터 | 이미지를 실제로 실행한 것. 끄면 내부 변경은 사라짐 |
| **볼륨** | 외장하드 | 컨테이너가 꺼져도 남아야 하는 것(=DB 데이터)을 담는 곳 |

핵심: **컨테이너는 일회용, 데이터는 볼륨에.** 그래서 `db` 컨테이너를 지워도
`postgres_data` 볼륨을 안 지우면 DB 내용은 그대로다.

## 2. forG는 도커를 두 곳에서 쓴다

```
[로컬 - 내 맥북]                         [프로덕션 - Railway]
docker-compose.yml이 컨테이너 2개를 띄움     Railway가 Dockerfile로 이미지를 빌드해
┌─────────────┐  ┌──────────────┐        컨테이너 1개를 상시 실행
│ app (forG)   │→│ db (postgres) │        ┌─────────────┐  ┌────────────────┐
│ 봇+폴링+API  │  │ 볼륨:         │        │ app (forG)   │→│ Railway 관리형  │
└─────────────┘  │ postgres_data │        └─────────────┘  │ PostgreSQL     │
                 └──────────────┘                          └────────────────┘
```

**중요: 로컬 DB와 프로덕션 DB는 완전히 별개다.** 로컬에서 `/add`로 등록한 기업은
프로덕션엔 없다. 반대도 마찬가지.

### 파일별 역할
- **`Dockerfile`** — 이미지 굽는 레시피: python3.11-slim에 requirements 설치 →
  `app/` 폴더만 복사 → `uvicorn main:app`을 8080 포트로 실행. (그래서 alembic
  설정도 `app/` 안에 있다 — 밖에 두면 이미지에 안 들어감.)
- **`docker-compose.yml`** — 로컬 전용 "컨테이너 2개 동시 실행" 설정. `db`(postgres:16,
  볼륨 postgres_data), `app`(위 Dockerfile 빌드, `.env` 주입, `./app`을 컨테이너에
  실시간 마운트 → **코드 수정이 재빌드 없이 반영**됨. 단 파이썬 프로세스 재시작은 필요).
- **`.env`** — 키 모음. `forg-git/.env`에 있어야 compose가 찾는다. **절대 커밋 금지**
  (.gitignore 처리됨). 없으면 `env_file: .env` 에러로 기동 실패.

### ⚠️ 알려진 포트 불일치 (CLAUDE.md §6-1)
compose는 `8000:8000`을 열지만 앱은 컨테이너 안에서 **8080**을 듣는다. 즉 로컬에서
`curl localhost:8000/health`는 **안 된다** (지금까지 로컬 헬스체크를 안 써서 실해가 없었음).
고치고 싶으면 compose의 ports를 `"8000:8080"`으로 한 줄 수정 — 단독 PR로 할 것.

---

## 3. 명령어 치트시트 (전부 `forg-git/`에서)

```bash
# 데몬(도커 엔진) 켜기 — 맥: Docker Desktop 앱 실행 (또는)
open -a Docker

# 전체 기동 (db+app) / 백그라운드
docker compose up -d
# db만 기동 (봇 안 띄우고 DB 작업만 할 때 — 토큰 충돌 걱정 없음)
docker compose up -d db

# 상태 확인
docker ps                          # 지금 도는 컨테이너
docker compose ps                  # 이 프로젝트 것만

# 로그 (문제 생기면 무조건 이것부터)
docker compose logs app --tail 100     # 최근 100줄
docker compose logs app -f             # 실시간 (Ctrl+C로 종료)

# 정지/제거 (볼륨=DB데이터는 유지됨)
docker compose down
# DB 데이터까지 완전 초기화 (⚠️ 로컬 DB 삭제 — 신중히)
docker compose down -v

# requirements.txt 바꿨을 때만 재빌드 필요
docker compose up -d --build

# 로컬 DB에 직접 SQL
docker compose exec db psql -U forg -d forg
#   \dt              ← 테이블 목록
#   \d seen_disclosures  ← 스키마 확인
#   \q               ← 나가기

# 로컬 DB 백업/복원
docker compose exec -T db pg_dump -U forg forg > backup.sql
docker compose exec -T db psql -U forg -d forg < backup.sql
```

### 🚨 로컬에서 app을 띄우기 전에 반드시 생각할 것
로컬 app은 `.env`의 텔레그램 토큰으로 **실제 폴링을 시작**한다. 텔레그램은 토큰당
폴러를 하나만 허용하므로, **Railway 프로덕션이 같은 토큰을 쓰고 있으면 서로 충돌**해
프로덕션 봇이 오작동한다. 규칙:
- 봇 테스트가 목적이 아니면 → `docker compose up -d db` (db만)
- 봇 테스트가 목적이면 → 테스트 후 반드시 `docker compose down`
- 이상적으로는 **로컬 전용 테스트 봇 토큰**을 따로 만들어 .env에 쓰는 것 (BotFather에서 봇 하나 더 생성)

---

## 4. 실제로 겪었던 문제와 해결 (트러블슈팅)

| 증상 | 원인 | 해결 |
|---|---|---|
| `Bind for 0.0.0.0:5432 failed: port is already allocated` | 다른 곳에서 띄운 옛 forg 스택(예: `~/forg`)이 5432 점유 | `docker ps`로 확인 → 그 디렉토리에서 `docker compose down` (또는 `docker compose -p forg down`) |
| `env_file: .env not found` | `.env`가 `forg-git/`에 없음 | §2의 .env 절 참조해 생성 |
| `InvalidToken: Unauthorized` + 앱 종료 | 텔레그램 토큰 만료/오타 | BotFather에서 토큰 재확인 → `.env` 수정 → `docker compose up -d --force-recreate app` |
| `.env` 바꿨는데 반영 안 됨 | 실행 중 컨테이너는 기동 시점 env를 유지 | `docker compose up -d --force-recreate app` |
| 코드 바꿨는데 동작 그대로 | 마운트는 실시간이지만 파이썬 프로세스는 재시작 필요 | `docker compose restart app` |
| 옛날 데이터가 계속 보임 | `down`은 볼륨을 지우지 않음 (의도된 동작) | 초기화가 목적일 때만 `down -v` |
| 클론이 두 개라 헷갈림 | `~/forg`(옛것)과 `Desktop/foRG/forg-git`(현행) | **작업·실행은 항상 `forg-git`에서.** `~/forg`는 정리 권장 |
| 기동 로그에 `DB 마이그레이션 적용 실패 … near "DO"` (SQLite 테스트에서) | 마이그레이션이 PostgreSQL 전용 — 의도된 폴백 | 무시. PG에선 `DB 마이그레이션 적용 완료`가 떠야 정상 |

---

## 5. 프로덕션(Railway)은 뭐가 다른가

- Railway가 GitHub `main`을 보고(자동 배포 설정 시) → Dockerfile 빌드 → 컨테이너 교체.
  즉 **PR을 main에 머지하는 것이 곧 배포**다.
- DB는 Railway 관리형 PostgreSQL — `DATABASE_URL`을 Railway 환경변수로 주입.
  `.env` 파일은 로컬 전용이고, 프로덕션 키는 Railway 대시보드 → Variables에서 관리.
- 배포 직후 확인 루틴: Railway 로그에서
  `DB 초기화 완료` → `DB 마이그레이션 적용 완료` → `텔레그램 봇 시작 완료` → `DART 폴링 시작` 4줄.
- 권장 환경변수: `TZ=Asia/Seoul` (코드가 KST를 명시해서 필수는 아니지만 로그 시각 가독성).

## 6. "서버가 더 필요하면?" — 단계별 확장 경로

> 결론부터: **지금은 이미 서버가 있다(Railway).** 사용자 수십 명까지는 아무것도 바꿀 필요 없다.

### 단계 1 — 지금 구조 그대로 (사용자 ~50명 추정)
- 병목은 서버가 아니라 LLM 비용·텔레그램 발송 속도. Railway 요금제에서 메모리/CPU만
  올리면 됨(대시보드에서 클릭). 코드 변경 없음.

### 단계 2 — 인스턴스를 늘리고 싶어질 때 (주의!)
- **현재 코드는 다중 인스턴스 금지.** 두 가지가 깨진다:
  ① 폴링이 인스턴스마다 돌아 중복 알림 (PostgreSQL advisory lock 필요 — 계획서 §2.3),
  ② `disclosure_cache`/`pending_selections`가 프로세스 메모리라 인스턴스 간 공유 안 됨.
- 그 전에 할 일: 폴링을 웹서버와 분리(별도 worker), 상태를 DB/Redis로. 이건 로드맵
  후반(SaaS 전환) 작업이며 **사용자 요청 없이 미리 하지 않는다.**

### 단계 3 — GPU가 필요해질 때 (Track B 파인튜닝 재개 시)
- 학습은 **일회성 클라우드 GPU 대여**가 정석: RunPod/Vast.ai/Lambda 등에서 시간당 대여
  (소형 모델 LoRA 기준 수 달러~수십 달러 수준, 가격 변동 큼). Colab도 실험용으론 가능.
- 학습된 모델의 **서빙**을 GPU 서버로 상시 운영하는 건 비용이 커서, 트래픽이 실증되기
  전엔 비추천. 로드맵 #26의 보류 조건과 같은 얘기.

### 단계 4 — Railway를 떠나 직접 서버(VPS)로 가고 싶을 때
- 갈 수 있는 조건이 이미 준비돼 있다: `docker compose up -d`가 그대로 VPS에서 돈다.
- 하지만 그 순간부터 **직접 책임져야 하는 것**: DB 백업·복구, OS 보안 업데이트, 장애 시
  재기동, 도메인·HTTPS(웹 단계 진입 시), 모니터링. Railway 비용이 아깝지 않은 이유가 이것.
- 이전을 정당화하는 신호: Railway 월 비용이 VPS+운영시간 가치를 명확히 초과할 때,
  또는 데이터 위치/규제 요건이 생길 때. 그 전엔 Railway 유지 권장.

### 한 줄 요약
> 로컬은 `docker compose`, 프로덕션은 Railway, 배포는 main 머지.
> 서버 고민은 "다중 인스턴스가 필요해지는 날"까지 미루는 게 맞고, 그날이 오면
> 이 문서의 단계 2부터 다시 읽으면 된다.
