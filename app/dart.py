from config import DART_API_KEY, IMPORTANT_REPORT_TYPES
import httpx
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

DART_BASE_URL = "https://opendart.fss.or.kr/api"
KST = ZoneInfo("Asia/Seoul")


class DartApiError(Exception):
    """DART 응답이 정상(000)도 빈 결과(013)도 아닌 경우.

    장애를 빈 결과로 위장하지 않기 위해 존재한다(2026-08-19 리뷰 P1).
    빈 리스트 반환은 '오늘 공시 없음'과 구분이 불가능해서, 키 오류(010)·
    쿼터 초과(020)·DART 점검(800)이 몇 시간씩 정상 폴링처럼 보였다 —
    last_success_at까지 갱신되고, 야간·주말엔 empty 경보 조건도 안 걸린다.
    이 예외로 폴링(tasks)은 fail_streak를 올리고, 봇 조회는 사용자에게
    오류를 알린다.
    """


def kst_date_str(days_ago: int = 0) -> str:
    """KST 기준 days_ago일 전 날짜(YYYYMMDD)."""
    return (datetime.now(KST) - timedelta(days=days_ago)).strftime("%Y%m%d")


def today_kst() -> str:
    """오늘 날짜(YYYYMMDD)를 KST 기준으로 반환.

    DART의 접수일(rcept_dt)은 KST 날짜다. 컨테이너가 UTC로 돌면
    naive datetime.now()는 00~09시 KST 사이에 전날을 가리켜
    아침 공시를 놓치므로 반드시 이 함수를 쓴다.
    """
    return kst_date_str(0)

TYPED_APIS = {
    '유상증자': 'piicDecsn',
    '무상증자': 'fricDecsn',
    '전환사채': 'cvbdIsDecsn',
    '신주인수권': 'bdwtIsDecsn',
    '교환사채': 'exbdIsDecsn',
    '감자': 'crDecsn',
    '합병': 'cmpMgDecsn',
    '분할': 'cmpDvDecsn',
    '자기주식취득': 'tsstkAqDecsn',
    '자기주식처분': 'tsstkDpDecsn',
}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ["script", "style"]:
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ["script", "style"]:
            self.skip = False

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())


def is_important(report_nm: str) -> bool:
    return any(keyword in report_nm for keyword in IMPORTANT_REPORT_TYPES)


def get_api_for_report(report_nm: str) -> str:
    for keyword, api in TYPED_APIS.items():
        if keyword in report_nm:
            return api
    return None


async def fetch_recent_disclosures(days: int = 1) -> list[dict]:
    """최근 days일(오늘 포함, KST) 공시 조회.

    폴링 파이프라인은 days=2를 쓴다 — 자정 직전(23:5x) 접수 공시가 날짜가
    넘어간 뒤 조회창 밖으로 빠져 영구 누락되는 것을 막기 위함.
    봇 조회(/my)는 기본값 1(오늘만)을 유지한다.
    """
    return await fetch_disclosures_range(kst_date_str(days - 1), today_kst())


async def fetch_disclosures_range(bgn_de: str, end_de: str) -> list[dict]:
    """지정 구간(YYYYMMDD, KST 접수일 기준)의 공시 전체 조회 (list.json, 페이지네이션).

    과거 날짜 조회(/my 20260810)와 최근 조회가 같은 경로를 쓴다.

    실패를 삼키지 않는다: 013(조회 결과 없음)만 정상적인 빈 결과이고,
    그 외 비정상 status는 DartApiError, HTTP·JSON 오류는 원 예외 그대로
    전파한다. 페이지네이션 중간 실패도 부분 결과를 돌려주지 않고 예외로
    올린다 — 다음 폴링(60초)이 전체를 재시도하며, 부분 결과는 '나머지가
    없었다'로 읽혀 누락을 만든다.
    """
    url = f"{DART_BASE_URL}/list.json"
    all_disclosures = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            params = {
                "crtfc_key": DART_API_KEY,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": page,
                "page_count": 100,
            }
            response = await client.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            if status == "013":
                break  # 조회 결과 없음 — 정상적인 빈 결과 (§4.1)
            if status != "000":
                raise DartApiError(f"DART status {status}: {data.get('message', '')}")
            items = data.get("list", [])
            if not items:
                break
            all_disclosures.extend(items)
            total_page = int(data.get("total_page", 1))
            if page >= total_page:
                break
            page += 1

    return all_disclosures


async def save_disclosures_to_db(disclosures: list[dict]):
    from database import AsyncSessionLocal
    from models import Disclosure
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        for d in disclosures:
            rcept_no = d.get("rcept_no")
            if not rcept_no:
                continue
            existing = await session.execute(
                select(Disclosure).where(Disclosure.rcept_no == rcept_no)
            )
            if existing.scalar_one_or_none():
                continue
            session.add(Disclosure(
                rcept_no=rcept_no,
                corp_code=d.get("corp_code", ""),
                corp_name=d.get("corp_name", ""),
                stock_code=d.get("stock_code", ""),
                corp_cls=d.get("corp_cls", ""),
                report_nm=d.get("report_nm", ""),
                rcept_dt=d.get("rcept_dt", ""),
                flr_nm=d.get("flr_nm", ""),
                is_important=is_important(d.get("report_nm", "")),
            ))
        await session.commit()


async def fetch_today_disclosures_from_db(important_only: bool = False) -> list[dict]:
    from database import AsyncSessionLocal
    from models import Disclosure
    from sqlalchemy import select

    today = today_kst()
    async with AsyncSessionLocal() as session:
        query = select(Disclosure).where(Disclosure.rcept_dt == today)
        if important_only:
            query = query.where(Disclosure.is_important == True)
        query = query.order_by(Disclosure.created_at.desc())
        result = await session.execute(query)
        rows = result.scalars().all()
        return [
            {
                "rcept_no": r.rcept_no,
                "corp_code": r.corp_code,
                "corp_name": r.corp_name,
                "stock_code": r.stock_code,
                "corp_cls": r.corp_cls,
                "report_nm": r.report_nm,
                "rcept_dt": r.rcept_dt,
                "flr_nm": r.flr_nm,
            }
            for r in rows
        ]


# 뷰어 호출 파라미터는 main.do가 심어 둔 viewDoc(...) 호출에서 그대로 읽는다.
# 주요사항보고서는 node1['dcmNo'] 블록도 갖지만 거래소 소관 공시(단일판매·공급계약
# 체결 등, 접수번호 …8xxxxx)는 그 블록이 아예 없다. viewDoc 인자는 두 유형 모두에
# 있고 dtd까지 함께 알려준다 — dart4.xsd로 하드코딩하면 거래소 공시는 빈손이 된다.
_VIEW_DOC_RE = re.compile(
    r'viewDoc\(\s*"(\d+)"\s*,\s*"(\d+)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"'
    r'\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"'
)


def _decode(response) -> str:
    """DART 응답 디코딩. 거래소 뷰어는 MS949(=cp949)로 내려온다.

    utf-8로 강제하면 한글이 전부 깨져 숫자만 남는다 — 요약이 '정보 없음'으로
    나오던 원인 중 하나였다(2026-08-26 사용자 신고).
    """
    raw = response.content
    encodings = []
    ctype = response.headers.get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        encodings.append(m.group(1))
    encodings += ["utf-8", "cp949"]
    for enc in encodings:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


async def fetch_disclosure_detail(receipt_no: str) -> str:
    try:
        main_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(main_url, timeout=10)
            m = _VIEW_DOC_RE.search(_decode(r))
            if m:
                _, dcm_no, ele_id, offset, length, dtd = m.groups()
            else:
                # 구 경로 폴백 — viewDoc이 없는 형식이 있을 수 있다
                dcm_nos = re.findall(r"node1\['dcmNo'\]\s*=\s*\"(\d+)\"", _decode(r))
                if not dcm_nos:
                    print(f"공시 원문 문서번호 없음: {receipt_no}")
                    return ""
                dcm_no, ele_id, offset, length, dtd = dcm_nos[0], "0", "0", "0", "dart4.xsd"

        viewer_url = (
            f"https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt_no}&dcmNo={dcm_no}"
            f"&eleId={ele_id}&offset={offset}&length={length}&dtd={dtd}"
        )
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(viewer_url, timeout=15)
            parser = TextExtractor()
            parser.feed(_decode(r))
            text = " ".join(parser.text)
            return text[:5000]
    except Exception as e:
        print(f"공시 원문 조회 실패: {e}")
        return ""


async def fetch_typed_disclosure(corp_code: str, rcept_no: str, report_nm: str, rcept_dt: str) -> dict:
    api = get_api_for_report(report_nm)
    if not api:
        return {}

    url = f"{DART_BASE_URL}/{api}.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": rcept_dt,
        "end_de": rcept_dt,
    }
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, params=params, timeout=10)
            data = r.json()
            if data.get("status") != "000":
                return {}
            for item in data.get("list", []):
                if item.get("rcept_no") == rcept_no:
                    return item
            return {}
        except Exception as e:
            print(f"정형 데이터 조회 실패: {e}")
            return {}


async def fetch_rcept_times(date: str) -> dict[str, str]:
    """DART 검색 페이지에서 접수번호별 제출 시간 가져오기"""
    import re
    url = "https://dart.fss.or.kr/dsac001/search.ax"
    params = {"selectDate": date, "textCrpCik": "", "pageGrouping": "A"}
    result = {}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            r = await client.get(url, params=params, timeout=15)
            matches = re.findall(r'rcpNo=(\d{14}).*?(\d{2}:\d{2})', r.text, re.DOTALL)
            for rcept_no, time_str in matches:
                result[rcept_no] = time_str
        except Exception as e:
            print(f"접수 시간 조회 실패: {e}")
    return result


def is_after_hours(time_str: str) -> bool:
    """오후 6시 이후 제출 여부"""
    try:
        hour = int(time_str.split(":")[0])
        return hour >= 18
    except:
        return False


async def fetch_corp_disclosures(
    corp_code: str, bgn_de: str, end_de: str, max_pages: int = 10
) -> list[dict]:
    """특정 기업의 기간 내 공시 목록 (Stage 8 질의 축).

    list.json은 corp_code를 주면 수년 범위 조회가 가능하다 — 폴링(오늘 전체)과
    달리 과거 검색(pull)용. status는 §4.1 규약: "000" 정상, "013"은 결과 없음
    (에러 아님, 빈 리스트).
    """
    url = f"{DART_BASE_URL}/list.json"
    out: list[dict] = []
    page = 1

    async with httpx.AsyncClient() as client:
        while page <= max_pages:
            params = {
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": page,
                "page_count": 100,
            }
            try:
                response = await client.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                status = data.get("status")
                if status == "013":
                    break
                if status != "000":
                    print(f"기업 공시 조회 실패 (status={status}): {data.get('message', '')}")
                    break
                items = data.get("list", [])
                if not items:
                    break
                out.extend(items)
                if page >= int(data.get("total_page", 1)):
                    break
                page += 1
            except Exception as e:
                print(f"기업 공시 조회 오류: {e}")
                break

    return out
