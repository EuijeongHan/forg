# forG 확장 구현 계획서

> 목표: 현재의 "DART 중요 공시 요약 Telegram 봇"을 "관심기업의 중요한 변화와 후속 일정을 알려주는 한국 상장사 이벤트 인텔리전스 서비스"로 확장한다.
>
> 이 문서는 구현 순서, 파일별 변경점, 예제 코드, 테스트, 배포와 롤백 방법을 한곳에 모은 실행 설명서다. 아래 코드는 설계 예시이며, 각 단계의 실제 DART/KIND 응답을 먼저 확인한 다음 적용한다.

---

## 0. 가장 먼저 읽을 내용

### 0.1 지금 서비스가 하는 일

현재 forG는 다음 순서로 움직인다.

1. 60초마다 OpenDART `list.json`을 조회한다.
2. 오늘 제출된 공시를 DB에 저장한다.
3. `IMPORTANT_REPORT_TYPES`에 해당하는 공시만 고른다.
4. 그 기업을 관심기업으로 등록한 사용자를 찾는다.
5. 정형 API가 있으면 정형 데이터를 사용하고, 없으면 DART 본문을 크롤링한다.
6. 요약을 만들고 Telegram으로 발송한다.
7. `SeenDisclosure`로 중복 발송을 막는다.

### 0.2 확장 후 사용자가 얻는 것

단순히 "공시가 올라왔다"를 알려주는 데서 끝내지 않는다.

- 무엇이 발생했는지 알려준다.
- 숫자와 일정을 유형별 카드로 보여준다.
- 이전 공시와 무엇이 달라졌는지 보여준다.
- 앞으로 확인할 납입일, 전환청구일 등을 알려준다.
- DART에 없는 KIND 고유 공시도 보완한다.
- 사용자가 원하는 조건의 알림만 받게 한다.
- 나중에는 기업별 타임라인과 출처 기반 질문 기능을 제공한다.

### 0.3 절대 한 번에 다 만들지 않는다

구현 순서는 아래와 같다.

1. 운영 안정화
2. 정형 이벤트 카드
3. 정정·변화 비교
4. 이벤트 타임라인과 일정
5. KIND 누락 측정과 보완 수집
6. 개인화 알림과 리캡
7. 검증과 사용자 피드백
8. REST API와 웹
9. 출처 기반 질의

앞 단계가 안정적으로 운영된 뒤에 다음 단계로 이동한다.

---

## 1. 목표 아키텍처

```text
OpenDART collector ─┐
                    ├─> 원본 공시 저장 ─> 이벤트 정규화 ─> 변화 비교 ─> 알림 정책
KIND collector ─────┘                            │               │
                                                 │               ├─> Telegram 즉시 알림
                                                 │               └─> 일간/주간 리캡
                                                 │
                                                 ├─> 후속 일정
                                                 └─> 기업별 이벤트 타임라인

나중 단계:
원본 공시 + 정형 이벤트 + 출처 위치 ─> 검색/질의 API ─> Web UI
```

핵심 원칙은 세 가지다.

1. **원본과 해석을 분리한다.** DART/KIND에서 받은 원본은 그대로 저장한다.
2. **정형 계산을 LLM보다 먼저 한다.** 숫자, 비율, 일정, 변화는 Python이 계산한다.
3. **알림 발송은 마지막 단계다.** 수집이나 요약에 실패해도 원본 공시는 DB에 남아야 한다.

---

## 2. Phase 0 — 운영 기반 안정화

새 기능보다 먼저 해야 한다. 이 단계가 끝나지 않으면 사용자가 늘었을 때 중복 알림, 누락, HTML 오류가 발생할 수 있다.

### 2.1 사용자별 중복 발송 키 수정

현재 `SeenDisclosure.receipt_no`에는 단독 `unique=True`가 있다. 그러나 실제 중복 기준은 `(receipt_no, chat_id)`다. 같은 공시를 여러 사용자에게 보내야 하므로 복합 유니크 제약이 필요하다.

#### `app/models.py` 변경 예시

```python
from sqlalchemy import UniqueConstraint


class SeenDisclosure(Base):
    __tablename__ = "seen_disclosures"
    __table_args__ = (
        UniqueConstraint(
            "receipt_no",
            "chat_id",
            name="uq_seen_disclosure_receipt_chat",
        ),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    receipt_no = Column(String, nullable=False, index=True)
    chat_id = Column(String, ForeignKey("users.chat_id"), nullable=False)
    corp_name = Column(String, nullable=False)
    report_nm = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)
```

#### 중요한 주의점

`create_all()`은 기존 DB의 `unique=True`를 제거하지 않는다. 반드시 Alembic migration이 필요하다. 프로덕션 DB에 직접 SQL을 붙여 넣지 않는다.

Migration이 해야 할 일:

1. 기존 `receipt_no` 단독 unique constraint 이름을 DB에서 확인한다.
2. 단독 unique constraint를 제거한다.
3. `(receipt_no, chat_id)` 복합 unique constraint를 추가한다.
4. `receipt_no` 조회용 일반 index를 추가한다.

검증 SQL 예시:

```sql
SELECT receipt_no, chat_id, COUNT(*)
FROM seen_disclosures
GROUP BY receipt_no, chat_id
HAVING COUNT(*) > 1;
```

결과가 0행이어야 migration을 안전하게 적용할 수 있다.

### 2.2 Telegram HTML 생성 통일

현재 자동 알림은 `notifier.escape_html()`을 사용하지만 `/today` 버튼으로 보는 요약은 문자열을 직접 HTML에 넣는다. 모든 HTML 메시지는 한 함수에서 만들어야 한다.

#### `app/notifier.py`에 추가할 함수 예시

```python
def build_disclosure_message(
    corp_name: str,
    report_nm: str,
    receipt_no: str,
    summary: str,
) -> str:
    dart_url = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
        + receipt_no
    )
    return (
        "🏢 <b>" + escape_html(corp_name) + "</b>\n"
        "📋 " + escape_html(report_nm) + "\n\n"
        "📝 <b>요약</b>\n"
        + escape_html(summary)
        + "\n\n"
        + '<a href="' + dart_url + '">원문 보기</a>'
    )
```

`send_alert()`와 `bot.view_disclosure_callback()`이 모두 이 함수를 사용하게 바꾼다.

### 2.3 폴링 작업 중첩 방지

한 번의 `process_disclosures()`가 60초 이상 걸리면 다음 실행과 겹칠 수 있다. 우선 APScheduler 설정으로 한 프로세스 안의 중첩을 막는다.

#### `app/main.py` 변경 예시

```python
scheduler.add_job(
    process_disclosures,
    "interval",
    seconds=POLLING_INTERVAL,
    id="dart_polling",
    next_run_time=datetime.now(timezone.utc),
    max_instances=1,
    coalesce=True,
    misfire_grace_time=30,
)
```

- `max_instances=1`: 같은 작업을 동시에 두 개 실행하지 않는다.
- `coalesce=True`: 밀린 실행을 한 번으로 합친다.
- `misfire_grace_time=30`: 너무 늦어진 실행을 무조건 뒤늦게 실행하지 않는다.

다중 Railway 인스턴스를 사용할 때는 이것만으로 부족하다. 그때는 PostgreSQL advisory lock 또는 별도 worker가 필요하다. 초기에는 Railway 인스턴스를 1개로 유지한다.

### 2.4 성공한 발송만 DB에 기록

현재 `send_alert()`는 성공 여부를 반환하지 않는다. 실패했는데도 `SeenDisclosure`를 기록하면 영원히 재발송되지 않을 수 있다.

#### `app/notifier.py` 변경 예시

```python
async def send_alert(...) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ...
            await bot.send_message(...)
            return True
        except TelegramError as exc:
            ...
    return False
```

#### `app/tasks.py` 변경 예시

```python
sent = await send_alert(
    chat_id=user.chat_id,
    corp_name=corp_name,
    report_nm=report_nm,
    receipt_no=receipt_no,
    summary=summary,
)

if sent:
    session.add(SeenDisclosure(...))
```

### 2.5 비동기 호출 정리

`summarize_with_claude()`와 `summarize_with_gemini()`는 async 함수 안에서 동기 SDK를 호출한다. 신규 의존성이나 버전 변경 없이 먼저 `asyncio.to_thread()`로 event loop 차단을 피할 수 있다.

```python
import asyncio


async def summarize_with_claude(prompt: str) -> str | None:
    try:
        message = await asyncio.to_thread(
            client.messages.create,
            model="검증된_현재_모델명",
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as exc:
        print("Claude 요약 실패:", exc)
        return None
```

모델명과 SDK 지원 여부는 현재 설치 버전으로 실제 호출해 확인한 뒤 반영한다. 의존성 업그레이드는 사용자 확인 없이 하지 않는다.

### 2.6 Phase 0 완료 조건

- 동일 공시가 사용자 A와 B에게 각각 1번씩 발송된다.
- 같은 사용자에게는 동일 공시가 2번 발송되지 않는다.
- `<`, `>`, `&`가 포함된 기업명과 요약도 Telegram에서 깨지지 않는다.
- 폴링이 60초를 넘어도 두 작업이 겹치지 않는다.
- Telegram 발송 실패 시 `SeenDisclosure`가 생성되지 않는다.
- 모든 외부 HTTP 호출이 async `httpx` 또는 명시적인 thread offload를 사용한다.

---

## 3. Phase 1 — 공시 유형별 이벤트 카드

### 3.1 왜 필요한가

지금 `format_typed_disclosure()`는 화면 문자열을 바로 만든다. 앞으로 변화 비교, 일정, 검색을 하려면 먼저 공시를 공통 구조의 `event`로 바꿔야 한다.

화면 문자열만 저장하면 "이번 전환가액과 이전 전환가액을 비교"하기 어렵다. 숫자와 날짜를 JSON 필드로 저장해야 한다.

### 3.2 새 파일 구조

```text
app/
├─ events/
│  ├─ __init__.py
│  ├─ types.py          # 이벤트 타입 이름
│  ├─ normalizer.py     # DART 정형 데이터 -> 공통 이벤트
│  ├─ metrics.py        # 희석률, D-day 등 계산
│  ├─ comparator.py     # 이전 이벤트와 변화 비교
│  └─ renderer.py       # Telegram 카드 문자열 생성
└─ services/
   └─ event_service.py  # 저장, 조회, 연결
```

새 외부 라이브러리는 필요 없다.

### 3.3 DB 모델

`Disclosure`는 "제출된 문서"이고 `DisclosureEvent`는 "문서가 뜻하는 사건"이다. 둘을 분리한다.

```python
from sqlalchemy import JSON, Float, Integer, UniqueConstraint


class DisclosureEvent(Base):
    __tablename__ = "disclosure_events"
    __table_args__ = (
        UniqueConstraint(
            "disclosure_id",
            name="uq_disclosure_event_disclosure",
        ),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    disclosure_id = Column(
        String,
        ForeignKey("disclosures.id"),
        nullable=False,
        index=True,
    )
    corp_code = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    occurred_on = Column(String, nullable=True)

    # 원본 정형 데이터. 나중에 포매터가 바뀌어도 다시 계산할 수 있다.
    raw_typed_data = Column(JSON, nullable=False, default=dict)

    # 공시 유형을 초월한 공통 필드와 유형별 필드다.
    normalized_data = Column(JSON, nullable=False, default=dict)

    # 계산된 희석률, D-day 같은 값이다.
    metrics = Column(JSON, nullable=False, default=dict)

    normalization_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )
```

`JSON`을 사용하는 이유는 공시 유형마다 필드가 다르기 때문이다. 자주 검색하는 `corp_code`, `event_type`, `occurred_on`만 일반 컬럼으로 둔다.

### 3.4 이벤트 타입

#### `app/events/types.py`

```python
EVENT_CONVERTIBLE_BOND = "convertible_bond"
EVENT_EXCHANGEABLE_BOND = "exchangeable_bond"
EVENT_BOND_WITH_WARRANT = "bond_with_warrant"
EVENT_PAID_INCREASE = "paid_in_capital_increase"
EVENT_FREE_INCREASE = "free_capital_increase"
EVENT_CAPITAL_REDUCTION = "capital_reduction"
EVENT_MERGER = "merger"
EVENT_SPLIT = "split"
EVENT_TREASURY_ACQUISITION = "treasury_acquisition"
EVENT_TREASURY_DISPOSAL = "treasury_disposal"
EVENT_AUDIT_REPORT = "audit_report"
EVENT_OTHER = "other"
```

### 3.5 안전한 값 정규화

숫자 계산 전에 쉼표, 공백, `-`를 처리한다. 원문 표시값은 별도로 보존한다.

#### `app/events/normalizer.py`

```python
from decimal import Decimal, InvalidOperation


EMPTY_VALUES = {None, "", "-"}


def clean_text(value) -> str | None:
    if value in EMPTY_VALUES:
        return None
    text = str(value).strip()
    return text or None


def parse_decimal(value) -> Decimal | None:
    text = clean_text(value)
    if text is None:
        return None

    cleaned = (
        text.replace(",", "")
        .replace("원", "")
        .replace("주", "")
        .replace("%", "")
        .strip()
    )
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def decimal_to_json(value: Decimal | None) -> str | None:
    # JSON float는 금융 숫자에서 오차가 날 수 있으므로 문자열로 저장한다.
    return str(value) if value is not None else None
```

중요: `억원`, `만원`처럼 이미 축약된 단위는 임의로 원 단위로 바꾸지 않는다. API 필드의 공식 단위를 확인한 유형만 계산한다.

### 3.6 전환사채 normalizer 예시

실제 필드명은 OpenDART 실측 결과와 대조한 뒤 확정한다.

```python
def normalize_convertible_bond(data: dict) -> dict:
    amount = parse_decimal(data.get("bd_fta"))
    conversion_price = parse_decimal(data.get("cv_prc"))

    return {
        "event_type": "convertible_bond",
        "normalized_data": {
            "amount": decimal_to_json(amount),
            "bond_type": clean_text(data.get("bd_knd")),
            "coupon_rate": decimal_to_json(
                parse_decimal(data.get("bd_intr_ex"))
            ),
            "maturity_rate": decimal_to_json(
                parse_decimal(data.get("bd_intr_sf"))
            ),
            "maturity_date": clean_text(data.get("bd_mtd")),
            "conversion_price": decimal_to_json(conversion_price),
            "conversion_start_date": clean_text(data.get("cvrqpd_bgd")),
            "operating_funds": decimal_to_json(
                parse_decimal(data.get("fdpp_op"))
            ),
            "debt_repayment_funds": decimal_to_json(
                parse_decimal(data.get("fdpp_dtrp"))
            ),
            "reset_terms": clean_text(
                data.get("act_mktprcfl_cvprc_lwtrsprc_bs")
            ),
        },
    }
```

### 3.7 유형 선택 함수

```python
def normalize_typed_disclosure(report_nm: str, data: dict) -> dict:
    if "전환사채" in report_nm:
        return normalize_convertible_bond(data)
    if "유상증자" in report_nm:
        return normalize_paid_increase(data)
    if "감자" in report_nm:
        return normalize_capital_reduction(data)
    if "합병" in report_nm:
        return normalize_merger(data)

    return {
        "event_type": "other",
        "normalized_data": {},
    }
```

`TYPED_APIS`에 등록된 모든 유형을 한 번에 구현하지 않는다. 추천 순서는 다음과 같다.

1. 전환사채
2. 유상증자
3. 자기주식 취득·처분
4. 감자
5. 합병·분할
6. 신주인수권부사채·교환사채
7. 무상증자

### 3.8 계산 지표

처음부터 희석률을 무리하게 계산하지 않는다. 필요한 분모 데이터가 있어야 한다.

```python
def calculate_dilution_rate(
    new_share_count: Decimal | None,
    existing_share_count: Decimal | None,
) -> Decimal | None:
    if new_share_count is None:
        return None
    if existing_share_count is None or existing_share_count <= 0:
        return None

    total_after = existing_share_count + new_share_count
    return (new_share_count / total_after * Decimal("100")).quantize(
        Decimal("0.01")
    )
```

분모가 없으면 `0`을 표시하지 말고 "계산 불가"로 처리한다. `0`은 실제로 희석이 없다는 뜻이기 때문이다.

### 3.9 renderer

renderer는 저장된 JSON을 사람이 읽는 문자열로만 바꾼다. 계산하지 않는다.

```python
def add_line(lines: list[str], label: str, value, suffix: str = ""):
    if value not in (None, "", "-"):
        lines.append(f"• {label}: {value}{suffix}")


def render_convertible_bond(event: dict) -> str:
    data = event["normalized_data"]
    metrics = event.get("metrics", {})
    lines = ["[전환사채 발행결정]"]

    add_line(lines, "발행금액", data.get("amount"), "원")
    add_line(lines, "전환가액", data.get("conversion_price"), "원")
    add_line(lines, "표면이자율", data.get("coupon_rate"), "%")
    add_line(lines, "만기이자율", data.get("maturity_rate"), "%")
    add_line(lines, "전환청구 가능일", data.get("conversion_start_date"))
    add_line(lines, "잠재 희석률", metrics.get("dilution_rate"), "%")

    return "\n".join(lines)
```

### 3.10 Phase 1 테스트

표시 문자열 전체를 한 번에 비교하는 snapshot 테스트와 필드별 테스트를 같이 둔다.

```python
def test_parse_decimal():
    assert parse_decimal("4,605주") == Decimal("4605")
    assert parse_decimal("-") is None
    assert parse_decimal("") is None


def test_cb_normalizer_keeps_units_out_of_number():
    result = normalize_convertible_bond({
        "bd_fta": "15,000,000,000",
        "cv_prc": "3,250",
    })
    data = result["normalized_data"]
    assert data["amount"] == "15000000000"
    assert data["conversion_price"] == "3250"


def test_dilution_returns_none_without_denominator():
    assert calculate_dilution_rate(Decimal("100"), None) is None
```

외부 테스트 라이브러리를 추가하려면 사용자 확인이 필요하다. 현재 환경에 `pytest`가 없다면 우선 표준 라이브러리 `unittest`를 사용하거나 의존성 추가 승인을 받는다.

### 3.11 Phase 1 완료 조건

- 최소 전환사채와 유상증자가 공통 이벤트 JSON으로 저장된다.
- 같은 원본을 여러 번 처리해도 이벤트는 하나만 생긴다.
- 카드의 숫자는 LLM이 아니라 저장된 정형 값에서 나온다.
- 값이 없으면 `0`으로 꾸미지 않고 해당 줄을 생략하거나 계산 불가로 표시한다.
- 실제 접수번호 fixture로 카드가 만들어지는 테스트가 있다.

---

## 4. Phase 2 — 정정공시와 과거 공시 변화 비교

이 단계가 forG의 가장 강한 차별점이다.

### 4.1 먼저 구분할 두 종류

1. **정정 비교**: 같은 사건의 원본과 정정본 비교
2. **과거 비교**: 이번 CB와 과거 CB처럼 서로 다른 사건 비교

둘을 같은 로직으로 섞지 않는다.

### 4.2 공시 연결 정보 저장

```python
class DisclosureRelation(Base):
    __tablename__ = "disclosure_relations"
    __table_args__ = (
        UniqueConstraint(
            "from_disclosure_id",
            "to_disclosure_id",
            "relation_type",
            name="uq_disclosure_relation",
        ),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    from_disclosure_id = Column(
        String,
        ForeignKey("disclosures.id"),
        nullable=False,
    )
    to_disclosure_id = Column(
        String,
        ForeignKey("disclosures.id"),
        nullable=False,
    )
    relation_type = Column(String, nullable=False)
    # correction_of, follow_up_of, same_event_type_previous
    confidence = Column(String, nullable=False, default="rule")
    created_at = Column(DateTime(timezone=True), default=now_utc)
```

### 4.3 처음에는 규칙 기반으로 연결

정정공시 제목에서 `[정정]`, `정정`을 감지한다. 하지만 제목만으로 원본을 확정하지 않는다.

후보 조건:

- 같은 `corp_code`
- 정정 표시를 제거한 보고서명이 같거나 매우 유사함
- 정정본보다 먼저 제출됨
- 가까운 날짜
- 가능하면 DART 원문에 적힌 관련 접수번호 사용

정확한 관련 접수번호를 원문에서 얻을 수 없다면 자동 연결의 confidence를 낮게 두고 알림에는 "추정 연결"이라고 표시한다.

### 4.4 JSON 필드 비교

#### `app/events/comparator.py`

```python
LABELS = {
    "amount": "발행금액",
    "conversion_price": "전환가액",
    "conversion_start_date": "전환청구 가능일",
    "payment_date": "납입일",
    "allotment_method": "배정방법",
}


def compare_normalized_data(
    before: dict,
    after: dict,
) -> list[dict]:
    changes = []
    all_keys = sorted(set(before) | set(after))

    for key in all_keys:
        old_value = before.get(key)
        new_value = after.get(key)
        if old_value == new_value:
            continue

        changes.append({
            "field": key,
            "label": LABELS.get(key, key),
            "before": old_value,
            "after": new_value,
        })

    return changes
```

### 4.5 변화 메시지

```python
def render_changes(changes: list[dict]) -> str:
    if not changes:
        return "핵심 정형 항목의 변경이 없습니다."

    lines = ["[정정된 핵심 항목]"]
    for item in changes:
        before = item["before"] if item["before"] is not None else "없음"
        after = item["after"] if item["after"] is not None else "없음"
        lines.append(f"• {item['label']}: {before} → {after}")
    return "\n".join(lines)
```

### 4.6 중요 변화와 단순 변화를 구분

초기 규칙 예시:

- 금액, 수량, 비율 변경: 중요
- 납입일, 전환일, 합병기일 변경: 중요
- 대상자·상대방 변경: 중요
- 띄어쓰기, 연락처, 담당자 변경: 낮음
- 기존 값과 새 값이 동일: 표시하지 않음

좋음/나쁨을 판정하지 않는다. 오직 "변화의 크기와 사용자 확인 필요성"만 분류한다.

### 4.7 Phase 2 완료 조건

- 정정본이 원본과 연결된다.
- 변경된 필드만 Telegram에 표시된다.
- 원본 연결이 불확실하면 확정 표현을 쓰지 않는다.
- 동일한 정정본을 여러 번 처리해도 관계와 알림이 중복되지 않는다.
- 정정 전후 fixture 테스트가 있다.

---

## 5. Phase 3 — 이벤트 타임라인과 후속 일정

### 5.1 일정 모델

```python
class EventSchedule(Base):
    __tablename__ = "event_schedules"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "schedule_type",
            "scheduled_on",
            name="uq_event_schedule",
        ),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    event_id = Column(
        String,
        ForeignKey("disclosure_events.id"),
        nullable=False,
        index=True,
    )
    corp_code = Column(String, nullable=False, index=True)
    schedule_type = Column(String, nullable=False)
    scheduled_on = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="scheduled")
    # scheduled, changed, completed, cancelled
    source_field = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)
```

일정 예시:

- CB 납입일
- 전환청구 개시일
- 만기일
- 유상증자 납입일
- 신주 상장 예정일
- 합병 주주총회일
- 합병기일
- 자기주식 취득 시작·종료일

### 5.2 일정 생성 규칙

```python
def schedules_from_event(event: dict) -> list[dict]:
    data = event["normalized_data"]
    event_type = event["event_type"]
    result = []

    if event_type == "convertible_bond":
        if data.get("conversion_start_date"):
            result.append({
                "schedule_type": "conversion_start",
                "scheduled_on": data["conversion_start_date"],
                "source_field": "conversion_start_date",
            })
        if data.get("maturity_date"):
            result.append({
                "schedule_type": "maturity",
                "scheduled_on": data["maturity_date"],
                "source_field": "maturity_date",
            })

    return result
```

날짜 형식은 저장 전에 `YYYY-MM-DD`로 정규화한다. 파싱 실패 시 원문 문자열은 이벤트 JSON에 보존하되 일정 테이블에는 넣지 않는다.

### 5.3 Telegram 명령

```text
/timeline 삼성전자
/upcoming
/upcoming 30
```

- `/timeline`: 최근 이벤트를 날짜순으로 보여준다.
- `/upcoming`: 관심기업의 향후 14일 일정을 보여준다.
- `/upcoming 30`: 향후 30일을 보여준다.

Telegram은 메시지가 길어지므로 최대 20개만 표시하고 나머지는 웹 단계에서 제공한다.

### 5.4 Phase 3 완료 조건

- 정형 이벤트에서 날짜가 일정 테이블로 생성된다.
- 정정으로 날짜가 바뀌면 기존 일정은 `changed`, 새 일정은 `scheduled`가 된다.
- 오늘 기준 D-day가 항상 새로 계산된다. 저장된 D-day 숫자를 재사용하지 않는다.
- `/upcoming`이 관심기업 일정만 보여준다.

---

## 6. Phase 4 — KIND 보완 수집

### 6.1 중요한 전제

KIND 화면 주소나 HTML 구조를 추측해서 구현하지 않는다. 먼저 같은 날짜의 DART와 KIND 목록을 실제로 내려받아 차이를 측정한다.

### 6.2 첫 작업은 수집기가 아니라 비교 리포트

비교 스크립트의 출력 예시:

```text
날짜: 2026-07-16
DART 상장사 공시: 1,234건
KIND 공시: 1,401건
공통: 1,180건
DART만: 54건
KIND만: 221건

KIND만 존재하는 주요 유형:
공정공시 42건
조회공시답변 11건
자율공시 36건
투자판단 관련 주요경영사항 58건
기타 74건
```

최소 5영업일을 비교한 뒤 KIND 수집 범위를 결정한다.

### 6.3 공통 수집 결과 구조

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CollectedDisclosure:
    source: str
    source_key: str
    corp_name: str
    stock_code: str | None
    report_nm: str
    disclosed_at: str
    source_url: str
    raw_data: dict
```

DART와 KIND 모두 이 구조로 반환하게 만든다.

### 6.4 Disclosure 모델 확장

```python
class Disclosure(Base):
    __tablename__ = "disclosures"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_key",
            name="uq_disclosure_source_key",
        ),
    )

    # 기존 id는 유지
    source = Column(String, nullable=False, default="dart", index=True)
    source_key = Column(String, nullable=False)
    source_url = Column(String, nullable=True)
    raw_data = Column(JSON, nullable=False, default=dict)
```

기존 `rcept_no`는 DART 문서에만 존재하므로 장기적으로 nullable하게 만들거나 DART 전용 필드로 유지한다. 바로 제거하거나 이름을 바꾸지 않는다.

기존 데이터 migration:

```text
source = "dart"
source_key = 기존 rcept_no
source_url = DART 원문 URL
```

### 6.5 KIND adapter 뼈대

정확한 URL, 파라미터, 응답 형식은 브라우저 개발자도구와 실제 응답으로 확인한 뒤 채운다.

```python
import httpx


class KindCollector:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def fetch_for_date(
        self,
        target_date: str,
    ) -> list[CollectedDisclosure]:
        # 1. 실제 KIND 요청 URL과 파라미터를 확인한다.
        # 2. response.raise_for_status()를 호출한다.
        # 3. HTML 또는 JSON의 실제 구조를 파싱한다.
        # 4. 각 항목을 CollectedDisclosure로 반환한다.
        raise NotImplementedError(
            "KIND 실제 응답 형식을 확인한 뒤 구현하세요."
        )
```

이 `NotImplementedError`를 없애기 전에 실제 응답 fixture를 저장하고 parser 단위 테스트를 먼저 만든다. API 키, 쿠키, 개인정보는 fixture에 넣지 않는다.

### 6.6 DART와 KIND 중복 통합

완전히 같은 문서가 양쪽에 있을 수 있다. 처음에는 자동 병합하지 말고 `DisclosureRelation(relation_type="same_publication")`으로 연결한다.

일치 후보 조건:

- 같은 종목코드
- 같은 날짜
- 정정 접두어를 제거한 제목이 같음
- 원문에 동일 접수번호가 존재함

확실한 키가 없는데 제목만 비슷한 경우에는 서로 다른 문서로 저장한다. 중복 저장이 잘못된 병합보다 안전하다.

### 6.7 수집 예절과 장애 대응

- `httpx.AsyncClient`만 사용한다.
- 짧은 간격으로 무한 재시도하지 않는다.
- timeout을 설정한다.
- 429 또는 5xx는 지수 backoff한다.
- HTML 구조가 바뀌면 빈 목록을 정상 결과처럼 저장하지 않는다.
- parser 성공 건수와 전일 대비 급감 여부를 기록한다.
- KIND 장애가 DART 폴링을 막지 않도록 작업을 분리한다.

### 6.8 Phase 4 완료 조건

- 최소 5영업일의 DART/KIND 누락 비교 리포트가 있다.
- KIND parser는 저장된 실제 응답 fixture로 테스트된다.
- KIND가 실패해도 DART 알림은 정상 동작한다.
- DART와 KIND 중복이 사용자에게 두 번 발송되지 않는다.
- KIND 고유 공시에는 KIND 원문 링크가 표시된다.

---

## 7. Phase 5 — 개인화 알림과 리캡

### 7.1 문자열 컬럼 대신 규칙 테이블

키워드를 `User.today_keywords` 한 칸에 쉼표로 저장하는 방식은 복잡한 조건을 표현하기 어렵다.

```python
class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(String, primary_key=True, default=gen_uuid)
    chat_id = Column(
        String,
        ForeignKey("users.chat_id"),
        nullable=False,
        index=True,
    )
    name = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    delivery_mode = Column(String, nullable=False, default="instant")
    # instant, daily_digest, both
    conditions = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_utc)
```

조건 예시:

```json
{
  "event_types": ["convertible_bond", "paid_in_capital_increase"],
  "minimum_dilution_rate": "5.0",
  "after_hours_only": false,
  "correction_only": false,
  "important_change_only": true
}
```

### 7.2 규칙 판정 함수

```python
from decimal import Decimal


def event_matches_rule(event: dict, rule: dict) -> bool:
    conditions = rule.get("conditions", {})

    event_types = conditions.get("event_types", [])
    if event_types and event.get("event_type") not in event_types:
        return False

    minimum = conditions.get("minimum_dilution_rate")
    if minimum is not None:
        actual = event.get("metrics", {}).get("dilution_rate")
        if actual is None:
            return False
        if Decimal(actual) < Decimal(minimum):
            return False

    if conditions.get("correction_only"):
        if not event.get("is_correction"):
            return False

    return True
```

### 7.3 리캡

리캡은 매 공시마다 LLM을 다시 호출하지 않는다. 이미 저장된 카드와 변화 데이터를 모아 Python으로 만든다.

```text
[7월 16일 관심기업 리캡]

신규 중요 이벤트 7건
정정 2건
향후 14일 일정 3건

1. A사 — 전환사채 150억원 발행
2. B사 — 최대주주 변경
3. C사 — 계약금액 정정
```

APScheduler job을 별도로 등록한다.

```python
scheduler.add_job(
    send_daily_digests,
    "cron",
    hour=18,
    minute=30,
    timezone="Asia/Seoul",
    id="daily_digest",
    max_instances=1,
    coalesce=True,
)
```

### 7.4 Phase 5 완료 조건

- 사용자가 즉시 알림과 리캡을 선택할 수 있다.
- 규칙이 없는 기존 사용자는 현재 동작을 유지한다.
- 계산할 수 없는 희석률을 0으로 취급하지 않는다.
- 리캡 생성 때문에 LLM 호출 수가 공시 수만큼 증가하지 않는다.

---

## 8. Phase 6 — 요약 검증과 사용자 오류 신고

### 8.1 검증 순서

```text
1. 입력 본문이 충분한가?
2. 정형 필드와 카드가 일치하는가?
3. 숫자·날짜·단위가 원본에 존재하는가?
4. 투자 의견 금칙 표현이 있는가?
5. 필요한 경우에만 LLM claim judge를 호출한다.
```

정형 카드는 동일 데이터로 만든 것이므로 카드 전체를 다시 LLM으로 평가하지 않는다. 검증 대상은 필드 매핑과 AI가 추가한 코멘트다.

### 8.2 검증 결과 모델

```python
class SummaryVerification(Base):
    __tablename__ = "summary_verifications"

    id = Column(String, primary_key=True, default=gen_uuid)
    disclosure_id = Column(
        String,
        ForeignKey("disclosures.id"),
        nullable=False,
        index=True,
    )
    summary = Column(Text, nullable=False)
    verifier_version = Column(String, nullable=False)
    verdict = Column(String, nullable=False)
    # pass, warning, fail, unavailable
    checks = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_utc)
```

### 8.3 안전한 발송 정책

- `pass`: 정상 발송
- `warning`: 사실 카드만 발송하고 AI 코멘트 제거
- `fail`: 원문 링크와 "자동 요약 검증 실패"만 발송
- `unavailable`: 검증 완료라고 표현하지 않음

검증 실패 후 자동 재생성은 초기 버전에 넣지 않는다. 재생성이 새 오류를 만들 수 있기 때문이다.

### 8.4 Telegram 피드백

버튼 예시:

```text
[👍 정확해요] [⚠️ 오류 신고]
```

```python
InlineKeyboardMarkup([[
    InlineKeyboardButton(
        "👍 정확해요",
        callback_data=f"feedback:ok:{receipt_no}",
    ),
    InlineKeyboardButton(
        "⚠️ 오류 신고",
        callback_data=f"feedback:error:{receipt_no}",
    ),
]])
```

피드백 모델:

```python
class SummaryFeedback(Base):
    __tablename__ = "summary_feedback"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "disclosure_id",
            name="uq_summary_feedback_user_disclosure",
        ),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    chat_id = Column(String, nullable=False)
    disclosure_id = Column(String, nullable=False)
    rating = Column(String, nullable=False)
    # ok, error
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)
```

### 8.5 Golden set

처음 목표는 30~50건이다.

포함할 사례:

- 전환사채
- 유상증자
- 자기주식
- 합병·분할
- 감사보고서
- 정정공시
- 표가 긴 공시
- 본문 추출 실패 공시
- 숫자 단위가 혼동되기 쉬운 공시

각 사례에 다음 라벨을 붙인다.

```json
{
  "receipt_no": "실제 접수번호",
  "claims": [
    {
      "text": "발행금액은 150억원이다",
      "label": "supported",
      "evidence": "원문 또는 정형 필드"
    }
  ],
  "has_number_error": false,
  "has_unit_error": false,
  "has_entity_error": false,
  "has_investment_opinion": false
}
```

민감한 사용자 정보와 API 키는 golden set에 넣지 않는다.

### 8.6 Phase 6 완료 조건

- 검증기의 precision, recall을 golden set에서 측정한다.
- 검증 결과와 실제 발송 정책이 분리되어 있다.
- 사용자의 오류 신고가 중복 저장되지 않는다.
- 오류 신고 데이터가 곧바로 모델 학습에 사용되지 않는다. 사람이 확인한 뒤 golden set 후보가 된다.

---

## 9. Phase 7 — REST API와 웹 대시보드

현재 `services/`는 Telegram에서 비즈니스 로직을 분리하기 시작했기 때문에 방향이 좋다. 웹을 만들기 전에 FastAPI route가 같은 service를 호출하도록 한다.

### 9.1 추천 route

```text
GET  /api/v1/companies/{corp_code}/timeline
GET  /api/v1/companies/{corp_code}/events
GET  /api/v1/events/{event_id}
GET  /api/v1/schedules/upcoming
GET  /api/v1/watchlist
POST /api/v1/watchlist
DELETE /api/v1/watchlist/{corp_code}
GET  /api/v1/alert-rules
POST /api/v1/alert-rules
PATCH /api/v1/alert-rules/{rule_id}
GET  /api/v1/digests/daily
```

### 9.2 router 예시

```python
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("/{event_id}")
async def get_event(event_id: str):
    event = await event_service.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event
```

인증이 생기기 전에는 이 API를 인터넷에 공개하지 않는다. `chat_id`를 URL이나 클라이언트 입력값만 믿고 사용하면 다른 사용자의 데이터를 볼 수 있다.

### 9.3 웹 화면 우선순위

1. 로그인
2. 관심기업 관리
3. 기업별 이벤트 타임라인
4. 정정 전후 비교
5. 향후 일정
6. 알림 규칙
7. 저장·메모
8. 질의

Next.js 도입은 이 단계에서 별도 프로젝트로 진행한다. 현재 Python requirements에 JavaScript 의존성을 섞지 않는다.

---

## 10. Phase 8 — 출처 기반 공시 질의

### 10.1 처음부터 범용 에이전트를 만들지 않는다

초기 질문은 미리 정의한 tool로 답한다.

```text
get_company_events(corp_code, event_type, date_from, date_to)
get_upcoming_schedules(corp_code, days)
get_latest_disclosures(corp_code, limit)
compare_disclosures(before_id, after_id)
get_event_source(event_id)
```

### 10.2 tool 결과 예시

```python
async def get_company_events(
    corp_code: str,
    event_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    # SQLAlchemy select로 DisclosureEvent를 조회한다.
    # 반환값에는 반드시 source_url과 disclosure_id를 포함한다.
    ...
```

LLM은 이 결과를 자연어로 설명하되 숫자를 새로 계산하지 않는다. 계산은 tool이 한다.

### 10.3 답변 형식

```text
A사는 최근 1년 동안 전환사채 발행결정을 3건 공시했습니다.

• 2026-07-16: 150억원 — [원문]
• 2026-03-04: 80억원 — [원문]
• 2025-11-21: 50억원 — [원문]

합계는 정형 데이터에서 확인된 280억원입니다.
```

모든 핵심 주장에는 최소한 공시 링크가 있어야 한다. 원문 위치까지 안정적으로 저장할 수 있게 되면 문서 내 근거 위치도 제공한다.

### 10.4 평가 지표

- tool 선택 정확도
- 필수 파라미터 정확도
- 조회 결과 recall
- 숫자 정확도
- 출처 연결 정확도
- 답변 faithfulness
- 투자 의견 포함률

LangChain이나 LangGraph는 기본 Python 함수 호출로 상태 관리가 어려워졌을 때 검토한다. 프레임워크를 사용하는 것 자체는 목표가 아니다.

---

## 11. 작업 단위별 파일 변경 목록

### Phase 0

```text
app/models.py
app/notifier.py
app/bot.py
app/tasks.py
app/main.py
app/summarizer.py
alembic.ini 및 migrations/  # 실제 migration 체계 도입 시
```

### Phase 1~3

```text
app/models.py
app/events/__init__.py
app/events/types.py
app/events/normalizer.py
app/events/metrics.py
app/events/comparator.py
app/events/renderer.py
app/services/event_service.py
app/services/schedule_service.py
app/tasks.py
app/bot.py
tests/events/
```

### Phase 4

```text
app/collectors/__init__.py
app/collectors/base.py
app/collectors/dart_collector.py
app/collectors/kind_collector.py
app/services/ingestion_service.py
app/models.py
scripts/compare_dart_kind.py
tests/fixtures/kind/
tests/collectors/
```

### Phase 5~6

```text
app/models.py
app/services/alert_rule_service.py
app/services/digest_service.py
app/services/verification_service.py
app/services/feedback_service.py
app/notifier.py
app/bot.py
app/main.py
evals/golden_set.jsonl
evals/run_eval.py
```

### Phase 7~8

```text
app/api/__init__.py
app/api/events.py
app/api/watchlist.py
app/api/schedules.py
app/api/alert_rules.py
app/api/query.py
app/services/query_service.py
```

---

## 12. 각 기능을 구현할 때 반복할 체크리스트

### 구현 전

- [ ] `AGENTS.md`, `CLAUDE.md`, `SKILL.md`를 읽었다.
- [ ] 수정할 함수의 현재 코드를 읽었다.
- [ ] DART/KIND 응답 형식을 실제로 확인했다.
- [ ] 정형 API 우선 원칙을 지킨다.
- [ ] DB 변경이면 migration과 롤백을 설계했다.
- [ ] 의존성 변경이 필요하면 사용자 승인을 받았다.

### 구현 중

- [ ] 모든 외부 HTTP는 async `httpx`다.
- [ ] DB는 `async with AsyncSessionLocal()`을 사용한다.
- [ ] 저장 후 필요한 위치에서 명시적으로 commit한다.
- [ ] 사용자 문자열은 Telegram HTML 이스케이프를 한다.
- [ ] API 키나 실제 `.env` 값을 로그에 출력하지 않는다.
- [ ] `status != "000"`을 정상 성공으로 처리하지 않는다.
- [ ] DART `status == "013"`은 빈 결과로 처리한다.
- [ ] 정형 데이터가 없을 때만 비정형 경로를 사용한다.

### 테스트

- [ ] 정상 응답
- [ ] 빈 응답
- [ ] `status 013`
- [ ] timeout
- [ ] 잘못된 HTML/JSON
- [ ] 동일 공시 재처리
- [ ] 여러 사용자에게 같은 공시 발송
- [ ] Telegram 발송 실패
- [ ] 정정 전후 값 동일
- [ ] 정정 전후 값 변경
- [ ] 숫자에 쉼표와 단위가 포함된 경우
- [ ] 날짜 파싱 실패

### 배포

- [ ] DB backup 또는 Railway 복구 방법을 확인했다.
- [ ] migration을 앱 코드보다 먼저/나중 중 어느 순서로 실행할지 정했다.
- [ ] 구버전 앱과 신버전 스키마가 잠시 공존 가능한지 확인했다.
- [ ] health endpoint를 확인했다.
- [ ] 한 개의 테스트 사용자에게 먼저 발송했다.
- [ ] 폴링 3회 이상을 관찰했다.
- [ ] 중복·누락·에러 로그를 확인했다.

---

## 13. 안전한 배포 순서

각 Phase는 아래 방식으로 배포한다.

1. 테스트 데이터를 사용해 로컬 단위 테스트를 실행한다.
2. 실제 DART 접수번호 하나로 최소 재현 테스트를 한다.
3. DB migration이 있으면 현재 데이터 중 충돌 여부를 조회한다.
4. Railway DB 백업 또는 복구 수단을 확인한다.
5. 기존 코드와 호환되는 additive migration을 먼저 적용한다.
6. 앱을 배포한다.
7. `/health`를 확인한다.
8. 테스트 사용자의 Telegram 알림을 확인한다.
9. 최소 3회의 폴링을 관찰한다.
10. 문제가 있으면 앱 코드를 이전 버전으로 돌린다.
11. 이미 사용된 새 컬럼은 즉시 삭제하지 않는다. 별도 migration으로 정리한다.

### Feature flag 예시

새 기능을 환경변수로 끌 수 있게 하면 롤백이 빠르다.

```python
ENABLE_EVENT_CARDS = (
    os.getenv("ENABLE_EVENT_CARDS", "false").lower() == "true"
)
ENABLE_CHANGE_ALERTS = (
    os.getenv("ENABLE_CHANGE_ALERTS", "false").lower() == "true"
)
ENABLE_KIND_COLLECTOR = (
    os.getenv("ENABLE_KIND_COLLECTOR", "false").lower() == "true"
)
```

기본값은 새 기능이 꺼진 `false`로 둔다. Railway에서 테스트 사용자에게만 켜는 별도 플래그가 있으면 더 안전하다.

---

## 14. 관측해야 할 숫자

기능을 많이 만드는 것보다 아래 숫자가 좋아지는지가 중요하다.

### 수집

- DART 폴링 성공률
- DART 응답 건수
- KIND 응답 건수
- 전일 대비 급감 여부
- 원문 추출 성공률
- 정형 API 적중률

### 요약·검증

- 공시 유형별 요약 성공률
- provider별 성공률과 지연시간
- 숫자 오류율
- 단위 오류율
- 검증 불가율
- 사용자 오류 신고율

### 알림

- 사용자별 발송 성공률
- 중복 발송 건수
- 발송 지연시간
- 즉시 알림 대비 리캡 선택률
- 공시 상세 버튼 클릭률

### 제품

- 주간 활성 사용자
- 관심기업 등록 수 중앙값
- 사용자당 실제로 읽은 공시 수
- 7일·30일 재방문율
- 가장 많이 사용하는 이벤트 유형

로그에 `chat_id`를 그대로 남길 필요는 없다. 운영 분석에는 익명화된 사용자 식별자를 사용한다.

---

## 15. 당장 시작할 첫 4개 작업

### 작업 1 — 중복 발송 스키마 수정

목표:

- `(receipt_no, chat_id)` 복합 unique
- 성공한 발송만 Seen 기록

완료 후 확인:

- 테스트 사용자 2명에게 같은 공시가 각각 도착한다.

### 작업 2 — 이벤트 normalizer 기반 만들기

목표:

- `DisclosureEvent` 모델
- `events/normalizer.py`
- 전환사채·유상증자 두 유형

완료 후 확인:

- 실제 정형 응답이 JSON 이벤트로 저장된다.
- 카드가 JSON에서 생성된다.

### 작업 3 — 정정 변화 비교

목표:

- 원본·정정본 연결
- 변경 필드 표시

완료 후 확인:

- 납입일이나 금액이 바뀐 실제 정정공시가 `이전 → 이후`로 표시된다.

### 작업 4 — DART/KIND 5영업일 비교

목표:

- 실제 누락 유형과 건수 파악
- KIND collector 범위 결정

완료 후 확인:

- "왜 KIND가 필요한가"를 추측이 아니라 숫자로 설명할 수 있다.

---

## 16. 지금 하지 않을 것

- LangChain을 사용했다는 이유만으로 도입하지 않는다.
- 복잡한 상태 전이가 없는데 LangGraph를 도입하지 않는다.
- 라벨 데이터 없이 파인튜닝하지 않는다.
- 이미지 입력 문제가 확인되지 않았는데 VLM을 붙이지 않는다.
- 검색 baseline 없이 임베딩을 파인튜닝하지 않는다.
- 정형 API가 있는데 LLM이 숫자를 다시 쓰게 하지 않는다.
- 검증 실패 요약을 자동으로 여러 번 재생성하지 않는다.
- KIND 엔드포인트와 HTML 구조를 추측해서 구현하지 않는다.
- 모든 기능을 한 배포에 넣지 않는다.

---

## 17. 최종 제품 한 문장

> forG는 관심기업의 DART·KIND 공시를 실시간으로 감지하고, 정형 데이터로 핵심 숫자를 정확하게 보여주며, 이전 공시와 달라진 점과 앞으로의 일정을 추적해 주는 한국 상장사 이벤트 인텔리전스 서비스다.

이 문장이 아닌 기능은 우선순위를 낮춘다. 예를 들어 뉴스 추천, 종목 추천, 주가 예측은 현재 핵심 목표가 아니다.

---

## 18. 공식 참고자료

- OpenDART 개발가이드: <https://opendart.fss.or.kr/guide/main.do>
- DART 전자공시시스템: <https://dart.fss.or.kr/>
- KIND 한국거래소 기업공시: <https://kind.krx.co.kr/>
- KRX 공시제도 운영체제: <https://regulation.krx.co.kr/contents/RGL/02/02010103/RGL02010103.jsp>

공식 문서와 실제 응답이 다르게 보이면 실제 응답을 최소 재현 스크립트로 확인하되, 우연히 관찰된 한 건을 전체 규칙으로 일반화하지 않는다.
