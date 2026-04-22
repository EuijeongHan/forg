from config import DART_API_KEY, IMPORTANT_REPORT_TYPES
import httpx
import re
from html.parser import HTMLParser

DART_BASE_URL = "https://opendart.fss.or.kr/api"

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


async def fetch_recent_disclosures() -> list[dict]:
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    url = f"{DART_BASE_URL}/list.json"
    all_disclosures = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            params = {
                "crtfc_key": DART_API_KEY,
                "bgn_de": today,
                "page_no": page,
                "page_count": 100,
            }
            try:
                response = await client.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data.get("status") != "000":
                    break
                items = data.get("list", [])
                if not items:
                    break
                all_disclosures.extend(items)
                total_page = int(data.get("total_page", 1))
                if page >= total_page:
                    break
                page += 1
            except Exception as e:
                print(f"DART API 호출 실패: {e}")
                break

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
    from datetime import datetime
    from database import AsyncSessionLocal
    from models import Disclosure
    from sqlalchemy import select

    today = datetime.now().strftime("%Y%m%d")
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


async def fetch_disclosure_detail(receipt_no: str) -> str:
    try:
        main_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(main_url, timeout=10)
            dcm_nos = re.findall(r"node1\['dcmNo'\]\s*=\s*\"(\d+)\"", r.text)
            if not dcm_nos:
                dcm_nos = re.findall(r"dcmNo[=:\"']+(\d+)", r.text)
            if not dcm_nos:
                return ""
            dcm_no = dcm_nos[0]

        viewer_url = f"https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt_no}&dcmNo={dcm_no}&eleId=0&offset=0&length=0&dtd=dart4.xsd"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(viewer_url, timeout=15)
            parser = TextExtractor()
            parser.feed(r.text)
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
