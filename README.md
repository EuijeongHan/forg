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
python-telegram-bot · OpenAI/Anthropic/Gemini(요약 폴백 체인). 배포는 Railway(Docker).

모듈 구조와 DART 규약은 [CLAUDE.md](../CLAUDE.md)(개괄)·[SKILL.md](../SKILL.md)(구현 세부) 참조.

## 봇 명령

| 명령 | 설명 |
|---|---|
| `/start` | 시작·안내 | `/add 기업명` | 관심기업 등록 | `/remove 기업명` | 삭제 |
| `/list` | 등록 목록 | `/today` | 오늘 중요 공시 | `/mytoday` | 내 기업 오늘 공시 |
| `/keyword` `/mykeyword` | 키워드 필터 | `/settings` | 설정 |

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
| [docs/planning/](docs/planning/) | 비판적 검토 기획안 |
| [docs/verification/](docs/verification/) | 변경별 검증 실행 기록 |
| GitHub Issues #26 | 통합 로드맵 (운영 기준) |

## 상태

프로덕션 상시 운영(Railway). 로드맵은 이슈 [#26](https://github.com/EuijeongHan/forg/issues/26) 기준으로
운영 안정화(Stage 0) 완료, 이벤트 카드·정정 비교 계층을 플래그 뒤에서 선구축 중.

## 면책

⚠️ 본 서비스의 요약·분류는 AI 참고용이며 오류·지연이 있을 수 있습니다. 공식 정보는
[dart.fss.or.kr](https://dart.fss.or.kr) 원문을 확인하십시오. 투자 판단과 그 결과의
책임은 이용자에게 있습니다. 자세한 내용은 [이용약관](docs/legal/terms-of-service.md).
