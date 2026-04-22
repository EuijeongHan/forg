from config import DART_API_KEY, IMPORTANT_REPORT_TYPES
import httpx

DART_BASE_URL = "https://opendart.fss.or.kr/api"

async def fetch_recent_disclosures() -> list[dict]:
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    url = f"{DART_BASE_URL}/list.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "bgn_de": today,
        "page_no": 1,
        "page_count": 100,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "000":
                msg = data.get("message")
                print(f"DART API 오류: {msg}")
                return []
            return data.get("list", [])
        except Exception as e:
            print(f"DART API 호출 실패: {e}")
            return []

def is_important(report_nm: str) -> bool:
    return any(keyword in report_nm for keyword in IMPORTANT_REPORT_TYPES)

async def fetch_disclosure_detail(receipt_no: str) -> str:
    url = f"{DART_BASE_URL}/document.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "rcept_no": receipt_no,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("content", "")
        except Exception as e:
            print(f"공시 원문 조회 실패: {e}")
            return ""


# 공시 유형별 정형 데이터 API
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

def get_api_for_report(report_nm: str) -> str:
    for keyword, api in TYPED_APIS.items():
        if keyword in report_nm:
            return api
    return None

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
            # rcept_no 매칭
            for item in data.get("list", []):
                if item.get("rcept_no") == rcept_no:
                    return item
            return {}
        except Exception as e:
            print(f"정형 데이터 조회 실패: {e}")
            return {}
