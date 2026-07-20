# forG OpenDART API 연동 가이드

> 목적: OpenDART API 목록을 손으로 복사하지 않고, 공식 문서와 실제 응답을 확인해 forG에 안전하게 추가하는 방법을 정의한다.
>
> 기준 목록 작성일: **2026-06-16**
>
> 주의: 아래 API 목록은 사용자가 OpenDART 화면에서 복사한 기준선이다. 목록에 있다는 사실과 forG에서 실제 호출·검증·구현이 끝났다는 사실은 다르다. 각 API는 공식 문서 확인과 실제 응답 테스트를 거쳐야 `verified` 또는 `implemented`로 표시한다.

---

## 1. 가장 중요한 결론

OpenDART API는 웹페이지를 긁는 "크롤링"과 다르다. 정해진 URL에 요청 인자를 넣어 HTTP GET 요청을 보내고 JSON, XML 또는 ZIP 파일을 받는다.

forG는 전체 API를 매분 호출하지 않는다. 다음 순서로 필요한 API만 선택한다.

```text
1. list.json으로 새 공시 검색
2. 공시 제목과 접수번호 확인
3. 공시 유형 판정
4. 해당 유형의 정형 API 하나만 호출
5. 결과 목록에서 같은 rcept_no 항목 선택
6. 정형 카드 생성
7. 정형 데이터가 없을 때만 DART viewer 본문 크롤링 + LLM 요약
```

예시:

```text
A사 전환사채 발행결정
→ cvbdIsDecsn.json만 호출

B사 소송 등의 제기
→ lwstLg.json만 호출

C사 감사보고서
→ 실시간 주요사항 정형 API가 없으면 현재 원문 경로 사용
```

이 방식을 사용하는 이유:

- API 요청 수를 줄인다.
- 요청 제한 초과를 예방한다.
- 정형 숫자를 LLM이 다시 만들지 않게 한다.
- 같은 기업·같은 날짜의 다른 공시를 잘못 가져오는 것을 막는다.

---

## 2. 공식 출처

API를 추가할 때는 아래 공식 출처를 우선한다.

- OpenDART 개발가이드: <https://opendart.fss.or.kr/guide/main.do>
- OpenDART API 기본 URL: `https://opendart.fss.or.kr/api`
- DART 공시 원문: <https://dart.fss.or.kr/>

검색 결과, 블로그, 기억만으로 엔드포인트나 필드 이름을 확정하지 않는다.

공식 개발가이드에서 확인할 항목:

- API 이름
- 정확한 요청 URL
- HTTP method
- 필수 요청 인자
- 선택 요청 인자
- 제공 시작 연도
- 응답 형식
- 응답 필드 이름과 설명
- 금액·수량·비율 단위
- 날짜 형식
- 상태 코드

---

## 3. 2026-06-16 OpenDART API 기준 목록

### 3.1 공시정보

| API명 | URL | 현재 forG 사용 | 비고 |
|---|---|---:|---|
| 공시검색 | `/api/list.json` | 사용 | 매 60초 폴링의 시작점 |
| 기업개황 | `/api/company.json` | 미사용 | 기업 상세 정보 확장 후보 |
| 공시서류원본파일 | `/api/document.xml` | 실시간 본문에는 미사용 | 정상 응답은 ZIP binary. 최근 공시에서 014가 발생할 수 있어 현재 viewer 크롤링 사용 |
| 고유번호 | `/api/corpCode.xml` | 사용 | ZIP 안의 XML로 기업코드 캐시 생성 |

### 3.2 발행공시

| API명 | URL | 현재 forG 사용 |
|---|---|---:|
| 지분증권 | `/api/estkRs.json` | 미사용 |
| 채무증권 | `/api/bdRs.json` | 미사용 |
| 증권예탁증권 | `/api/stkdpRs.json` | 미사용 |
| 합병 | `/api/mgRs.json` | 미사용 |
| 주식포괄적교환·이전 | `/api/extrRs.json` | 미사용 |
| 분할 | `/api/dvRs.json` | 미사용 |

발행공시는 증권신고서 주요정보다. 이름이 비슷해도 주요사항보고서 API와 동일하지 않다.

```text
mgRs.json       = 증권신고서 주요정보의 합병
cmpMgDecsn.json = 주요사항보고서의 회사합병 결정
```

같은 formatter나 요청 인자를 그대로 공유하지 않는다.

### 3.3 주요사항보고서

| API명 | URL | 현재 typed mapping | 우선순위 |
|---|---|---:|---:|
| 유상증자 결정 | `/api/piicDecsn.json` | 있음 | 구현 유지·검증 |
| 무상증자 결정 | `/api/fricDecsn.json` | 있음 | formatter 보강 |
| 유무상증자 결정 | `/api/pifricDecsn.json` | 없음 | 높음 |
| 감자 결정 | `/api/crDecsn.json` | 있음 | 구현 유지·검증 |
| 전환사채(CB) 발행결정 | `/api/cvbdIsDecsn.json` | 있음 | 최우선 |
| 신주인수권부사채(BW) 발행결정 | `/api/bdwtIsDecsn.json` | 있음 | 높음 |
| 교환사채 발행결정 | `/api/exbdIsDecsn.json` | 있음 | 높음 |
| 상각형 조건부자본증권 발행결정 | `/api/wdCocobdIsDecsn.json` | 없음 | 낮음 |
| 자기주식 취득 결정 | `/api/tsstkAqDecsn.json` | 있음 | 높음 |
| 자기주식 처분 결정 | `/api/tsstkDpDecsn.json` | 있음 | 높음 |
| 자기주식취득 신탁계약 체결 | `/api/tsstkAqTrctrCnsDecsn.json` | 없음 | 높음 |
| 자기주식취득 신탁계약 해지 | `/api/tsstkAqTrctrCcDecsn.json` | 없음 | 높음 |
| 영업양수 결정 | `/api/bsnInhDecsn.json` | 없음 | 중간 |
| 영업양도 결정 | `/api/bsnTrfDecsn.json` | 없음 | 중간 |
| 유형자산 양수 결정 | `/api/tgastInhDecsn.json` | 없음 | 중간 |
| 유형자산 양도 결정 | `/api/tgastTrfDecsn.json` | 없음 | 중간 |
| 타법인 주식 양수결정 | `/api/otcprStkInvscrInhDecsn.json` | 없음 | 중간 |
| 타법인 주식 양도결정 | `/api/otcprStkInvscrTrfDecsn.json` | 없음 | 중간 |
| 주권관련 사채권 양수 결정 | `/api/stkrtbdInhDecsn.json` | 없음 | 낮음 |
| 주권관련 사채권 양도 결정 | `/api/stkrtbdTrfDecsn.json` | 없음 | 낮음 |
| 회사합병 결정 | `/api/cmpMgDecsn.json` | 있음 | 높음 |
| 회사분할 결정 | `/api/cmpDvDecsn.json` | 있음 | 높음 |
| 회사분할합병 결정 | `/api/cmpDvmgDecsn.json` | 없음 | 높음 |
| 주식교환·이전 결정 | `/api/stkExtrDecsn.json` | 없음 | 높음 |
| 자산양수도(기타), 풋백옵션 | `/api/astInhtrfEtcPtbkOpt.json` | 없음 | 높음 |
| 부도발생 | `/api/dfOcr.json` | 없음 | 최우선 |
| 영업정지 | `/api/bsnSp.json` | 없음 | 최우선 |
| 회생절차 개시신청 | `/api/ctrcvsBgrq.json` | 없음 | 최우선 |
| 해산사유 발생 | `/api/dsRsOcr.json` | 없음 | 높음 |
| 채권은행 관리절차 개시 | `/api/bnkMngtPcbg.json` | 없음 | 높음 |
| 채권은행 관리절차 중단 | `/api/bnkMngtPcsp.json` | 없음 | 높음 |
| 소송 등의 제기 | `/api/lwstLg.json` | 없음 | 최우선 |
| 해외 증권시장 상장 결정 | `/api/ovLstDecsn.json` | 없음 | 낮음 |
| 해외 증권시장 상장폐지 결정 | `/api/ovDlstDecsn.json` | 없음 | 높음 |
| 해외 증권시장 상장 | `/api/ovLst.json` | 없음 | 낮음 |
| 해외 증권시장 상장폐지 | `/api/ovDlst.json` | 없음 | 높음 |

`현재 typed mapping 있음`은 `app/dart.py`의 `TYPED_APIS`에 엔드포인트가 등록됐다는 뜻이다. 카드 필드가 완전하고 정확하게 구현됐다는 뜻은 아니다.

### 3.4 지분공시

| API명 | URL | 현재 forG 사용 | 확장 가치 |
|---|---|---:|---:|
| 대량보유 상황보고 | `/api/majorstock.json` | 미사용 | 매우 높음 |
| 임원·주요주주 소유보고 | `/api/elestock.json` | 미사용 | 높음 |

지분공시는 최대주주·경영권·내부자 거래 타임라인에 활용할 수 있다. 주요사항보고서처럼 `report_nm → endpoint` 한 건 처리만 생각하지 말고, 보유자와 변동 내역을 별도 이벤트 모델로 정규화한다.

### 3.5 정기보고서 재무정보

| API명 | URL | 현재 forG 사용 | 확장 가치 |
|---|---|---:|---:|
| 단일회사 주요계정 | `/api/fnlttSinglAcnt.json` | 미사용 | 높음 |
| 다중회사 주요계정 | `/api/fnlttMultiAcnt.json` | 미사용 | 중간 |
| 단일회사 전체 재무제표 | `/api/fnlttSinglAcntAll.json` | 미사용 | 높음 |
| 단일회사 주요 재무지표 | `/api/fnlttSinglIndx.json` | 미사용 | 높음 |
| 다중회사 주요 재무지표 | `/api/fnlttCmpnyIndx.json` | 미사용 | 중간 |
| 재무제표 원본파일(XBRL) | `/api/fnlttXbrl.xml` | 미사용 | 장기 후보 |

재무정보 API는 공시 알림의 즉시 요약보다 다음 기능에 적합하다.

- 계약금액/최근 매출액 비율 검증
- 자금조달 규모/자산총액 비교
- 실적 변화 비교
- 기업별 재무 타임라인
- 웹 대시보드

### 3.6 정기보고서 주요정보

| API명 | URL | 현재 forG 사용 | 확장 가치 |
|---|---|---:|---:|
| 증자(감자) 현황 | `/api/irdsSttus.json` | 미사용 | 높음 |
| 배당에 관한 사항 | `/api/alotMatter.json` | 미사용 | 높음 |
| 자기주식 취득 및 처분 현황 | `/api/tesstkAcqsDspsSttus.json` | 미사용 | 높음 |
| 최대주주 현황 | `/api/hyslrSttus.json` | 미사용 | 높음 |
| 최대주주 변동현황 | `/api/hyslrChgSttus.json` | 미사용 | 매우 높음 |
| 소액주주 현황 | `/api/mrhlSttus.json` | 미사용 | 중간 |
| 임원 현황 | `/api/exctvSttus.json` | 미사용 | 중간 |
| 직원 현황 | `/api/empSttus.json` | 미사용 | 낮음 |
| 회계감사인 명칭 및 감사의견 | `/api/accnutAdtorNmNdAdtOpinion.json` | 미사용 | 매우 높음 |
| 감사용역체결현황 | `/api/adtServcCnclsSttus.json` | 미사용 | 중간 |
| 타법인 출자현황 | `/api/otrCprInvstmntSttus.json` | 미사용 | 중간 |
| 주식의 총수 현황 | `/api/stockTotqySttus.json` | 미사용 | 매우 높음 |

`stockTotqySttus`는 유상증자·CB의 희석률 계산에 필요한 분모 후보다. 다만 최신 공시 이후의 현재 발행주식 수와 정기보고서 기준 주식 수에는 시차가 있을 수 있다. 어떤 기준일의 수치를 사용했는지 반드시 표시한다.

---

## 4. API 종류별 호출 방식

모든 API를 하나의 요청 함수로 똑같이 처리하면 안 된다.

### 4.1 공시검색 `list.json`

대표 요청 인자:

```text
crtfc_key
corp_code       선택
bgn_de          시작일 YYYYMMDD
end_de          종료일 YYYYMMDD
last_reprt_at   최종보고서 검색 여부
pblntf_ty       공시유형
pblntf_detail_ty 공시상세유형
corp_cls        법인구분
sort
sort_mth
page_no
page_count
```

forG에서는 오늘 공시 전체를 페이지네이션해서 가져오는 시작점이다.

### 4.2 주요사항보고서

대체로 다음 인자를 사용한다.

```text
crtfc_key
corp_code
bgn_de
end_de
```

응답은 목록이므로 반드시 `rcept_no`가 같은 항목만 선택한다.

```python
for item in data.get("list", []):
    if item.get("rcept_no") == receipt_no:
        return item
return {}
```

첫 번째 항목을 무조건 쓰면 같은 기업·같은 날짜의 다른 공시가 섞일 수 있다.

### 4.3 정기보고서 주요정보·재무정보

대체로 다음 인자를 사용한다.

```text
crtfc_key
corp_code
bsns_year
reprt_code
```

주요 `reprt_code`:

| 코드 | 보고서 |
|---|---|
| `11011` | 사업보고서 |
| `11012` | 반기보고서 |
| `11013` | 1분기보고서 |
| `11014` | 3분기보고서 |

정기보고서 API는 오늘 날짜와 접수번호만으로 조회하는 구조가 아니다. 사업연도와 보고서 유형을 정확히 정해야 한다.

### 4.4 ZIP binary API

다음은 JSON API가 아니다.

```text
document.xml
corpCode.xml
fnlttXbrl.xml
```

정상 응답은 ZIP binary일 수 있다. `response.json()`을 호출하면 안 된다.

```python
response = await client.get(url, params=params, timeout=30)
response.raise_for_status()
content = response.content
```

오류 응답은 XML 형태일 수 있으므로 ZIP signature와 `Content-Type`을 확인한 뒤 압축을 푼다.

---

## 5. 공통 상태 코드 처리

| status | 의미 | forG 처리 |
|---|---|---|
| `000` | 정상 | 데이터 처리 |
| `010` | 등록되지 않은 키 | 설정 오류 기록, 재시도하지 않음 |
| `011` | 사용할 수 없는 키 | 설정 오류 기록, 재시도하지 않음 |
| `012` | 접근할 수 없는 IP | 운영 설정 확인 |
| `013` | 조회된 데이터 없음 | 오류가 아닌 빈 결과 |
| `014` | 파일 없음 | 원본파일 경로라면 현재 fallback 검토 |
| `020` | 요청 제한 초과 | 호출 중단, backoff, 운영 알림 |
| `021` | 조회 회사 수 초과 | 요청 범위 축소 |
| `100` | 부적절한 필드 값 | 요청 인자 오류, 재시도하지 않음 |
| `101` | 부적절한 접근 | URL·method·인자 확인 |
| `800` | 시스템 점검 | 나중에 제한 재시도 |
| `900` | 정의되지 않은 오류 | 로그 후 제한 재시도 |

기본 규칙:

```python
status = data.get("status")

if status == "000":
    return data

if status == "013":
    return {"status": "013", "list": []}

raise DartApiError(
    status=status,
    message=data.get("message", ""),
)
```

현재 운영 코드와의 호환 때문에 즉시 예외 구조로 전부 바꾸지 않는다. 새 공통 client를 도입할 때 단계적으로 전환한다.

---

## 6. 새 API 하나를 추가하는 표준 절차

예시: `소송 등의 제기` 정형 API를 추가한다고 가정한다.

### Step 1. 공식 개발가이드 확인

확인 대상:

```text
API명: 소송 등의 제기
예상 URL: /api/lwstLg.json
```

공식 페이지에서 URL, 인자, 제공 기간, 응답 필드와 단위를 다시 확인한다.

### Step 2. 실제 공시 찾기

```python
import asyncio
import httpx

from config import DART_API_KEY


async def find_reports(keyword: str) -> list[dict]:
    params = {
        "crtfc_key": DART_API_KEY,
        "bgn_de": "20260701",
        "end_de": "20260716",
        "page_count": 100,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://opendart.fss.or.kr/api/list.json",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

    if data.get("status") == "013":
        return []
    if data.get("status") != "000":
        raise RuntimeError(
            f"DART 오류: {data.get('status')} {data.get('message')}"
        )

    return [
        item
        for item in data.get("list", [])
        if keyword in item.get("report_nm", "")
    ]


reports = asyncio.run(find_reports("소송"))
for report in reports[:5]:
    print(
        report["corp_name"],
        report["report_nm"],
        report["rcept_no"],
        report["corp_code"],
        report["rcept_dt"],
    )
```

API 키 값은 출력하지 않는다.

### Step 3. 정형 API 최소 호출

```python
async def fetch_lawsuits(
    corp_code: str,
    bgn_de: str,
    end_de: str,
) -> list[dict]:
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": end_de,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://opendart.fss.or.kr/api/lwstLg.json",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

    status = data.get("status")
    if status == "013":
        return []
    if status != "000":
        raise RuntimeError(
            f"DART 오류: {status} {data.get('message')}"
        )

    return data.get("list", [])
```

### Step 4. 접수번호 일치 항목 선택

```python
def find_matching_receipt(
    items: list[dict],
    receipt_no: str,
) -> dict | None:
    for item in items:
        if item.get("rcept_no") == receipt_no:
            return item
    return None
```

일치 항목이 없으면 첫 번째 항목을 사용하지 않는다. 비정형 원문 경로로 폴백하고 원인을 로그로 남긴다.

### Step 5. 실제 필드 확인

```python
if matched:
    print(sorted(matched.keys()))
```

필드명을 기억이나 번역으로 만들어내지 않는다. 실제 응답 키와 공식 설명을 함께 확인한다.

### Step 6. sanitized fixture 저장

추천 경로:

```text
tests/fixtures/dart/lwstLg_example.json
```

fixture에서 제거할 것:

- API 키
- DB URL
- Telegram token
- 사용자 chat_id
- 테스트와 무관한 개인정보

실제 기업·접수번호는 공개 공시 정보이므로 테스트 목적상 사용할 수 있지만, fixture의 출처와 확인일을 기록한다.

### Step 7. registry 등록

```python
{
    "name": "소송 등의 제기",
    "endpoint": "lwstLg",
    "report_keywords": ["소송등의제기", "소송 등의 제기"],
    "category": "major_event",
    "verified_on": "YYYY-MM-DD",
}
```

### Step 8. normalizer 작성

```python
def normalize_lawsuit(data: dict) -> dict:
    # 아래 키는 실제 응답을 확인한 뒤 작성한다.
    return {
        "event_type": "lawsuit",
        "normalized_data": {
            "case_name": clean_text(data.get("ACTUAL_FIELD")),
            "claim_amount": clean_text(data.get("ACTUAL_FIELD")),
            "counterparty": clean_text(data.get("ACTUAL_FIELD")),
        },
    }
```

`ACTUAL_FIELD`는 설명용 자리표시자다. 실제 코드에 그대로 넣지 않는다.

### Step 9. renderer 작성

```python
def render_lawsuit(event: dict) -> str:
    data = event["normalized_data"]
    lines = ["[소송 등의 제기]"]

    if data.get("case_name"):
        lines.append("• 사건명: " + data["case_name"])
    if data.get("claim_amount"):
        lines.append("• 청구금액: " + data["claim_amount"])
    if data.get("counterparty"):
        lines.append("• 상대방: " + data["counterparty"])

    return "\n".join(lines)
```

### Step 10. 회귀 테스트

최소 테스트:

- status `000`
- status `013`
- 잘못된 status
- 같은 날짜 복수 공시 중 rcept_no 일치
- rcept_no 불일치
- 값이 `-`인 필드
- 금액에 쉼표가 있는 필드
- 카드 HTML 이스케이프
- 정형 데이터 없을 때 비정형 fallback

---

## 7. API registry 개선안

현재 `TYPED_APIS`는 단순한 `키워드 → endpoint` 사전이다.

```python
TYPED_APIS = {
    "유상증자": "piicDecsn",
    "전환사채": "cvbdIsDecsn",
}
```

API가 늘어나면 다음 정보도 필요하다.

- 공식 API 이름
- category
- 여러 제목 키워드
- 구체적인 유형의 우선순위
- 공식 문서 확인일
- fixture 경로
- normalizer 구현 여부
- renderer 구현 여부

추천 구조:

```python
DART_API_SPECS = [
    {
        "name": "회사분할합병 결정",
        "endpoint": "cmpDvmgDecsn",
        "report_keywords": ["분할합병"],
        "category": "major_event",
        "verified_on": "2026-06-16",
    },
    {
        "name": "회사분할 결정",
        "endpoint": "cmpDvDecsn",
        "report_keywords": ["회사분할", "분할결정"],
        "category": "major_event",
        "verified_on": "2026-06-16",
    },
    {
        "name": "회사합병 결정",
        "endpoint": "cmpMgDecsn",
        "report_keywords": ["회사합병", "합병결정"],
        "category": "major_event",
        "verified_on": "2026-06-16",
    },
]
```

가장 긴 키워드를 우선 선택한다.

```python
def get_api_spec(report_nm: str) -> dict | None:
    matches = []

    for spec in DART_API_SPECS:
        for keyword in spec["report_keywords"]:
            if keyword in report_nm:
                matches.append((len(keyword), spec))

    if not matches:
        return None

    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]
```

이렇게 해야 `분할합병`이 단순 `분할` API에 먼저 잡히는 문제를 예방할 수 있다.

---

## 8. 별도 API 카탈로그 파일 제안

코드 외에도 사람이 읽을 수 있는 카탈로그를 둔다.

추천 경로:

```text
docs/dart_api_catalog.yaml
```

예시:

```yaml
catalog_version: 1
baseline_copied_on: "2026-06-16"
official_source: "https://opendart.fss.or.kr/guide/main.do"

apis:
  - name: "전환사채 발행결정"
    category: "major_event"
    endpoint: "cvbdIsDecsn.json"
    docs_verified: true
    live_response_verified: true
    implemented: true
    fixture: "tests/fixtures/dart/cvbdIsDecsn.json"
    last_verified_on: "YYYY-MM-DD"

  - name: "소송 등의 제기"
    category: "major_event"
    endpoint: "lwstLg.json"
    docs_verified: false
    live_response_verified: false
    implemented: false
    fixture: null
    last_verified_on: null
```

상태의 뜻:

| 상태 | 의미 |
|---|---|
| `docs_verified` | 공식 개발가이드에서 URL·인자·필드 확인 |
| `live_response_verified` | 실제 기업·기간으로 status 000 응답 확인 |
| `implemented` | registry, normalizer, renderer, 테스트 연결 완료 |
| `fixture` | 민감정보 제거된 회귀 테스트 응답 경로 |
| `last_verified_on` | 마지막 확인일 |

API 목록에 존재한다는 이유만으로 세 값을 모두 `true`로 만들지 않는다.

---

## 9. 공통 DART client 제안

중복된 status 처리를 줄이기 위한 장기 구조다. 바로 전면 교체하지 않고 새 API부터 사용한 뒤 기존 코드를 단계적으로 이전한다.

```python
import httpx

from config import DART_API_KEY


DART_BASE_URL = "https://opendart.fss.or.kr/api"


class DartApiError(RuntimeError):
    def __init__(self, status: str | None, message: str):
        self.status = status
        self.message = message
        super().__init__(f"DART API 오류: {status} {message}")


async def fetch_dart_json(
    endpoint: str,
    params: dict,
) -> dict:
    request_params = {
        "crtfc_key": DART_API_KEY,
        **params,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{DART_BASE_URL}/{endpoint}.json",
            params=request_params,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

    status = data.get("status")
    if status == "000":
        return data
    if status == "013":
        return {
            "status": "013",
            "message": data.get("message", ""),
            "list": [],
        }

    raise DartApiError(
        status=status,
        message=data.get("message", ""),
    )
```

정형 공시 조회:

```python
async def fetch_major_event(
    endpoint: str,
    corp_code: str,
    receipt_no: str,
    receipt_date: str,
) -> dict:
    data = await fetch_dart_json(
        endpoint,
        {
            "corp_code": corp_code,
            "bgn_de": receipt_date,
            "end_de": receipt_date,
        },
    )

    for item in data.get("list", []):
        if item.get("rcept_no") == receipt_no:
            return item

    return {}
```

주의:

- `DART_API_KEY`가 없는 경우 앱 시작 시 명확한 설정 오류를 내도록 별도 검증한다.
- 요청 URL 전체를 로그에 출력하면 query string에 API 키가 노출될 수 있다.
- 오류 로그에는 endpoint, status, corp_code, receipt_no만 남기고 인증키는 남기지 않는다.

---

## 10. 정형 API 추가 우선순위

### 1차: 현재 중요한데 typed mapping이 없는 유형

1. 부도발생
2. 영업정지
3. 회생절차 개시신청
4. 소송 등의 제기
5. 유무상증자 결정
6. 회사분할합병 결정
7. 주식교환·이전 결정
8. 자산양수도·풋백옵션
9. 자기주식 신탁계약 체결·해지

### 2차: 기존 mapping의 formatter 완성

1. 전환사채
2. 유상증자
3. 자기주식 취득·처분
4. 합병·분할
5. 감자
6. BW·교환사채
7. 무상증자

`TYPED_APIS`에 endpoint가 있다고 formatter가 완성된 것은 아니다. 실제 응답 필드와 현재 카드 필드를 대조한다.

### 3차: 이벤트 분석용 보조 API

1. 주식의 총수 현황
2. 최대주주 변동현황
3. 회계감사인·감사의견
4. 대량보유 상황보고
5. 임원·주요주주 소유보고
6. 자기주식 취득·처분 현황
7. 증자·감자 현황
8. 재무정보

이 API들은 즉시 알림보다는 희석률, 지분 변화, 과거 이벤트 타임라인 계산에 사용한다.

---

## 11. 테스트 전략

### 11.1 단위 테스트

- status 000 처리
- status 013 빈 결과 처리
- status 020 예외 처리
- HTTP timeout
- 잘못된 JSON
- `list` 키가 없는 응답
- 동일 날짜 여러 공시 중 접수번호 일치
- 접수번호 불일치
- `-`, 빈 문자열, null 값
- 쉼표가 포함된 숫자
- 날짜 형식 차이

### 11.2 fixture 테스트

각 구현 API마다 최소 fixture 1개를 둔다.

```text
tests/fixtures/dart/
├─ cvbdIsDecsn_example.json
├─ piicDecsn_example.json
├─ tsstkAqDecsn_example.json
└─ lwstLg_example.json
```

가능하면 다음 fixture도 둔다.

- status 013
- 필드 일부가 `-`인 응답
- 정정공시 응답
- 같은 날짜 복수 공시 응답

### 11.3 실제 API smoke test

CI에서 매번 실제 API를 호출하지 않는다. 요청 제한과 외부 장애 때문이다.

실제 호출은 다음 경우에 수동으로 실행한다.

- 새 API 최초 추가
- 공식 문서 변경
- fixture와 다른 필드 발견
- parser 오류 급증
- DART 응답 구조 변경 의심

### 11.4 종단 테스트

```text
list.json에서 공시 발견
→ endpoint 선택
→ typed API 조회
→ rcept_no 일치
→ normalizer
→ renderer
→ Telegram escape
→ 테스트 chat_id로 발송
```

---

## 12. 운영 관측 항목

endpoint별로 다음을 측정한다.

- 호출 횟수
- status별 횟수
- 평균·최대 지연시간
- status 013 비율
- rcept_no 불일치 비율
- typed API 적중률
- 비정형 fallback 비율
- parser 오류 횟수
- Telegram 발송 성공률

이상 징후:

- 전날까지 성공하던 endpoint가 전부 013
- status 100 급증
- 응답 필드 누락 급증
- rcept_no 불일치 급증
- 특정 endpoint 지연시간 급증
- 전체 공시 수가 전일 대비 비정상적으로 급감

---

## 13. 보안 규칙

- `.env`는 커밋하지 않는다.
- API 키를 코드에 적지 않는다.
- API 키를 예제 fixture에 적지 않는다.
- query string 전체를 로그에 남기지 않는다.
- 오류 메시지에 `crtfc_key` 값을 포함하지 않는다.
- 과거 노출된 키는 잠재 유출로 간주하고 사용자가 직접 회전한다.
- 실제 API 응답을 공유할 때 인증키가 포함되지 않았는지 확인한다.

안전한 로그 예시:

```python
print(
    "DART API 실패:",
    {
        "endpoint": endpoint,
        "status": status,
        "corp_code": corp_code,
        "receipt_no": receipt_no,
    },
)
```

안전하지 않은 로그:

```python
print(response.request.url)
```

요청 URL에는 API 키가 query parameter로 포함될 수 있다.

---

## 14. 구현 체크리스트

### 공식 확인

- [ ] 공식 OpenDART 개발가이드에서 API를 찾았다.
- [ ] URL을 확인했다.
- [ ] 필수 요청 인자를 확인했다.
- [ ] 제공 시작 연도를 확인했다.
- [ ] 응답 필드와 단위를 확인했다.
- [ ] 상태 코드를 확인했다.

### 실측

- [ ] 실제 `corp_code`를 확보했다.
- [ ] 실제 `rcept_no`를 확보했다.
- [ ] 실제 날짜로 status 000을 확인했다.
- [ ] 응답의 실제 key 목록을 확인했다.
- [ ] 같은 접수번호 항목을 확인했다.
- [ ] 민감정보를 제거한 fixture를 만들었다.

### 코드

- [ ] API registry에 등록했다.
- [ ] 구체적인 제목 키워드가 먼저 매칭된다.
- [ ] status 013을 빈 결과로 처리한다.
- [ ] status 000 외 값을 정상으로 처리하지 않는다.
- [ ] normalizer를 작성했다.
- [ ] renderer를 작성했다.
- [ ] LLM이 정형 숫자를 다시 생성하지 않는다.
- [ ] 정형 데이터가 없을 때만 비정형 경로로 간다.

### 테스트

- [ ] fixture 단위 테스트가 있다.
- [ ] rcept_no 불일치 테스트가 있다.
- [ ] 빈 값과 `-` 테스트가 있다.
- [ ] 숫자·날짜·단위 테스트가 있다.
- [ ] Telegram HTML escape 테스트가 있다.
- [ ] 실제 접수번호로 수동 smoke test를 했다.

### 운영

- [ ] endpoint별 호출 수를 기록한다.
- [ ] API 키가 로그에 없는지 확인했다.
- [ ] 요청 제한을 고려했다.
- [ ] 실패 시 기존 DART 폴링을 막지 않는다.
- [ ] feature flag 또는 빠른 롤백 방법이 있다.

---

## 15. 하지 말아야 할 것

- 전체 OpenDART API를 매 60초마다 호출하지 않는다.
- API URL이나 응답 키를 이름만 보고 추측하지 않는다.
- 같은 날짜 결과의 첫 번째 항목을 무조건 사용하지 않는다.
- status 확인 없이 `data["list"]`에 접근하지 않는다.
- status 013을 장애로 취급하지 않는다.
- status 020에서 무한 재시도하지 않는다.
- `document.xml`을 현재 공시 본문의 기본 경로로 되돌리지 않는다.
- 정형 API가 있는 공시를 처음부터 LLM 원문 요약으로 처리하지 않는다.
- 발행공시 API와 주요사항보고서 API를 이름이 비슷하다는 이유로 합치지 않는다.
- 정기보고서 API를 날짜·접수번호 기반 주요사항 API처럼 호출하지 않는다.
- 단위를 확인하지 않고 억·만·조 단위로 변환하지 않는다.
- API 키를 출력하거나 커밋하지 않는다.

---

## 16. 이 문서의 유지 방법

새 API를 추가할 때 이 문서를 다음 순서로 갱신한다.

1. 기준 목록 표에서 현재 구현 상태를 갱신한다.
2. 공식 문서 확인일을 카탈로그에 기록한다.
3. 실제 응답 fixture 경로를 기록한다.
4. normalizer와 renderer 테스트 경로를 기록한다.
5. 발견한 DART 예외 사항을 추가한다.
6. API가 폐기·변경되면 기존 기록을 지우지 말고 변경 이력을 남긴다.

권장 확인 주기:

- 새 endpoint 구현 전
- DART 장애 또는 parser 오류 발생 시
- 3개월마다 API 카탈로그 점검
- OpenDART 공지에서 API 변경을 발표한 경우 즉시

---

## 17. 현재 바로 할 다음 작업

1. `docs/dart_api_catalog.yaml` 생성
2. 현재 `TYPED_APIS` 10개를 카탈로그로 옮김
3. 각 endpoint의 공식 문서 확인 여부 표시
4. 실제 응답 fixture 확보
5. `분할합병 → 분할 → 합병` 우선순위 매칭 수정
6. 부도·영업정지·회생·소송 typed API 추가
7. `stockTotqySttus`를 이용한 희석률 분모 기준 검토
8. endpoint별 typed 적중률과 fallback 비율 기록

이 작업들은 한 번에 프로덕션에 반영하지 않는다. API 하나씩 공식 확인, 실측, fixture, 테스트, feature flag 순서로 추가한다.
