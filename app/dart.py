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
