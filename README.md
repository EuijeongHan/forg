# forG

> 관심기업의 **중요한 DART 공시를 놓치지 않게** 해주는 한국 상장사 공시 알림 서비스.
> DART 공시를 실시간 폴링 → 중요 공시 선별 → 정형 데이터로 핵심 숫자를 정확히 요약 →
> 텔레그램으로 알림. (`forG` = for Geonsoo — 기관투자자인 동생을 위해 시작한 프로젝트.)

**투자자문·종목추천 서비스가 아닙니다.** 공시 사실의 전달·요약이며, 판단 전 DART 원문 확인이 필요합니다.

---

## 무엇을 해결하나

기관 실무자는 40개 이상 기업을 모니터링하는데, DART 알림이 너무 많아 정작 **주주가치에
큰 영향을 주는 공시(분할·유상증자·CB·감자 등)가 묻힙니다.** forG는 중요 공시만 선별해
핵심 숫자·일정과 함께, 원문 링크를 붙여 전달합니다.

## 핵심 설계 원칙

1. **정형 API 우선** — 유상증자·전환사채 등은 DART 정형(typed) API의 숫자 필드를 그대로
   쓴다. LLM이 원문에서 숫자를 추출할 때 생기는 단위 오류('4,605주'→'4,605조')를 원천 차단.
2. **숫자·날짜 계산은 Python이, LLM은 서술만** — 희석률·D-day·변화 비교는 코드가 계산한다.
3. **놓치지 않는다** — 발송 성공 시에만 기록(실패는 재시도), 자정 경계 2일 창 조회,
   파이프라인 침묵 실패 시 운영자 자가 경보.
4. **투자 의견 금지** — 요약에 매수/매도/목표가를 생성하지 않는다(법적 제약 겸 품질 기준).

## 아키텍처

```
DART list.json ─(60초 폴링)→ 저장 ─→ 중요공시 선별 ─→ 워치리스트 사용자 매칭
                                                          │
                              정형 API 우선 / 없으면 원문 크롤링 ─→ 요약
                                                          │
                                    SeenDisclosure(사용자별 중복방지) ─→ 텔레그램 알림
```

**스택**: Python 3.11 · FastAPI · APScheduler · SQLAlchemy(async)+asyncpg · PostgreSQL ·
python-telegram-bot · OpenAI/Anthropic/Gemini(요약 폴백 체인). 배포는 Railway(Docker),
운영 상태는 [상태](#상태) 참조.

모듈 구조와 DART 규약은 [CLAUDE.md](../CLAUDE.md)(개괄)·[SKILL.md](../SKILL.md)(구현 세부) 참조.

## 봇 명령

| 명령 | 설명 |
|---|---|
| `/start` | 시작·안내 |
| `/add 기업명` · `/remove 기업명` · `/list` | 관심기업 등록·삭제·목록 |
| `/today` | 관심기업의 오늘 중요 공시 |
| `/today 유상증자` | 위 결과를 검색어로 좁힘 (이번 조회에만 적용, 저장 안 됨) |
| `/market` · `/market 감사보고서` | 전체 시장의 오늘 중요 공시 |
| `/help` | 전체 사용법 |
| `/settings` | 설정 |
| `/deletedata` | 내 데이터 전체 삭제 (확인 후 즉시 삭제) |

조회 범위(관심기업/전체)와 검색어는 명령에서 바로 드러난다. 저장된 필터가 결과를
조용히 바꾸지 않는다 — `/keyword`·`/mykeyword`는 이 이유로 폐기됐고, 입력하면 새
사용법을 안내한다. `/mytoday`는 `/today`의 별칭으로 당분간 유지된다.

## 알림 등급 (2026-08-16 실측 기반, `app/alert_tiers.py`)

원칙: **워치리스트 기업의 공시는 버리지 않는다 — 등급만 나눈다.**

| 등급 | 대상 | 범위 | 시점 |
|---|---|---|---|
| 🚨 긴급 | 상장폐지 결정·정리매매, 회생절차, 부도, 횡령·배임 | **시장 전체** (워치리스트 무관, 기본 ON) | 즉시 · 사이클 최우선 · LLM 한도 우회 |
| 📌 시장 공지 | 상장폐지 예고·심사, 실질적 거래정지 발생 | 시장 전체 | 즉시 |
| ⚠️ 중요 | 증자·CB·합병·분할·감자·자기주식 + **5%룰·내부자매매·잠정실적·손익구조·배당·투자판단** | 워치리스트 기업 | 즉시 |
| 📄 참고 | 그 외 전부 (정기보고서·IR·주총 등) | 워치리스트 기업 | 매일 18:30 KST 다이제스트 |

- 실측(10영업일, 상장사 6,440건): 긴급 일 0.6건 · 시장 공지 일 0.8건.
- 굵은 유형들은 2026-08-16 커버리지 결함 수정으로 추가 — 이전 필터는 상장사
  공시의 87%를 버렸고 그 안에 실적·5%룰·내부자 매매가 있었다.
- '거래정지 **해제**'·액면분할 등 기술적 정지는 시장 등급에서 제외되지만,
  워치리스트 기업이면 여전히 전달된다(제외는 삭제가 아니라 강등).

## 로컬 실행

```bash
cd forg-git
cp .env.example .env   # 키 채우기 (아래 참조)
docker compose up -d   # db + app
docker compose logs app -f
```
필요한 환경변수·상세는 [docs/ops/docker-and-server-guide.md](docs/ops/docker-and-server-guide.md).

## 테스트

```bash
pip install -r requirements.txt -r requirements-dev.txt
python tests/test_services_smoke.py   # 등 (tests/README.md 참조)
```
PR·main push마다 GitHub Actions가 전체 행동 테스트를 실행한다.

## 문서

| 위치 | 내용 |
|---|---|
| [forg-git/IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 이벤트 인텔리전스 확장 계획 (Stage 0~8) |
| [forg-git/DART_API_INTEGRATION_GUIDE.md](DART_API_INTEGRATION_GUIDE.md) | OpenDART API 연동·확장 가이드 |
| [docs/ops/](docs/ops/) | 도커·서버 운영 설명서 |
| [docs/legal/](docs/legal/) | 이용약관·개인정보처리방침·고지문·법률 쟁점 메모 |
| [docs/research/](docs/research/) | 사용자 조사 설문(풀/라이트/인터뷰) |
| [docs/planning/2026-07-27-product-rebuild-plan.md](docs/planning/2026-07-27-product-rebuild-plan.md) | 제품 개편안 (명령 체계·카드·신뢰성 재설계) |
| [docs/planning/](docs/planning/) | 비판적 검토 기획안 |
| [docs/verification/](docs/verification/) | 변경별 검증 실행 기록 |
| GitHub Issues #26 | 통합 로드맵 (운영 기준) |

## 상태

**프로덕션 운영 중 (Railway Hobby, 2026-08-18 복구).** 2026-05-23 Railway 무료
트라이얼 만료로 중단됐던 배포를 유료 전환 후 재가동했다. 현재 배포 코드는 등급
알림(#49)까지 포함한 최신 main이며, 마이그레이션 0001~0003이 프로덕션 PostgreSQL에
적용 확인됐다. 로컬 `docker compose up`은 이제 개발용이다 — **같은 텔레그램 토큰으로
Railway와 동시에 켜면 명령 수신이 씹히고 알림이 중복되므로**, 로컬 기동 시 별도
개발 토큰을 쓰거나 Railway와 겹치지 않게 한다.

운영 주의사항:

- **키를 로테이션하면 Railway Variables에도 반영해야 한다.** 2026-07 봇 토큰
  재발급이 로컬 `.env`에만 반영돼, 복구 첫 배포에서 텔레그램 인증이 거부됐던
  전례가 있다(변수 교체 후 정상화).
- `DATABASE_URL`은 Railway PostgreSQL 값이어야 한다. 로컬 `.env`의 `db:5432`는
  docker compose 내부 호스트명이라 그대로 쓰면 안 된다.
- 신규 환경변수 `LLM_DAILY_CALL_LIMIT`(기본 500)·`ENABLE_EVENT_CARDS`(기본 false)는
  선택이다. 미설정이면 기본값으로 동작한다.
- 배포 후 기동 로그에서 `DB 마이그레이션 적용 완료`·`텔레그램 봇 시작 완료`를 확인한다.

로드맵은 이슈 [#26](https://github.com/EuijeongHan/forg/issues/26) 기준으로 운영 안정화(Stage 0)
완료, 이벤트 카드·정정 비교 계층을 플래그 뒤에서 선구축 중이다. 제품 방향 재정의는
[제품 개편안](docs/planning/2026-07-27-product-rebuild-plan.md) 참조.

## 면책

⚠️ 본 서비스의 요약·분류는 AI 참고용이며 오류·지연이 있을 수 있습니다. 공식 정보는
[dart.fss.or.kr](https://dart.fss.or.kr) 원문을 확인하십시오. 투자 판단과 그 결과의
책임은 이용자에게 있습니다. 자세한 내용은 [이용약관](docs/legal/terms-of-service.md).
