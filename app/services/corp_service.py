"""Corp code cache + name search (DART corpCode.xml). Telegram-independent.

Moved here from bot.py so both handlers and the future chat engine can resolve
company names to corp codes without importing telegram code.
"""
import io
import zipfile
import xml.etree.ElementTree as ET
import httpx
from config import DART_API_KEY

DART_BASE_URL = "https://opendart.fss.or.kr/api"
EXCLUDE_KEYWORDS = ["기업인수목적", "스팩", "SPAC"]

_corp_cache: list[tuple[str, str, str]] = []


async def load_corp_cache() -> None:
    """Load the full listed-company table once into the module-global cache."""
    global _corp_cache
    if _corp_cache:
        return
    url = f"{DART_BASE_URL}/corpCode.xml"
    params = {"crtfc_key": DART_API_KEY}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=30)
            response.raise_for_status()
            zip_file = zipfile.ZipFile(io.BytesIO(response.content))
            xml_content = zip_file.read("CORPCODE.xml")
            root = ET.fromstring(xml_content)
            _corp_cache = [
                (
                    corp.findtext("corp_code", ""),
                    corp.findtext("corp_name", ""),
                    corp.findtext("stock_code", ""),
                )
                for corp in root.findall("list")
            ]
            print(f"기업 코드 캐시 로드 완료: {len(_corp_cache)}개")
        except Exception as e:
            print(f"기업 코드 캐시 로드 실패: {e}")


async def search_corps(corp_name: str) -> list[tuple[str, str]]:
    """Search listed companies by name. Returns up to 20 (corp_code, corp_name)."""
    await load_corp_cache()
    exact: list[tuple[str, str]] = []
    starts_with: list[tuple[str, str]] = []
    partial: list[tuple[str, str]] = []
    seen_names: set[str] = set()

    for code, name, stock_code in _corp_cache:
        if not (stock_code and stock_code.strip()):
            continue
        if any(kw in name for kw in EXCLUDE_KEYWORDS):
            continue
        if name in seen_names:
            continue
        seen_names.add(name)
        if name == corp_name:
            exact.append((code, name))
        elif name.startswith(corp_name):
            starts_with.append((code, name))
        elif corp_name in name:
            partial.append((code, name))

    starts_with.sort(key=lambda x: x[1])
    partial.sort(key=lambda x: x[1])
    return (exact + starts_with + partial)[:20]
