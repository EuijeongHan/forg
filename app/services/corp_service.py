"""Corp code cache + name search (DART corpCode.xml). Telegram-independent.

Moved here from bot.py so both handlers and the future chat engine can resolve
company names to corp codes without importing telegram code.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import NamedTuple
import httpx
from config import DART_API_KEY

DART_BASE_URL = "https://opendart.fss.or.kr/api"
EXCLUDE_KEYWORDS = ["기업인수목적", "스팩", "SPAC"]

_corp_cache: list[tuple[str, str, str]] = []

# 복수 검색어 구분자 — 쉼표(반각·전각)와 줄바꿈. 가운뎃점(·)은 기업명 자체에
# 쓰일 수 있으므로 구분자로 쓰지 않는다.
_TERM_SEPARATORS = re.compile(r"[,，\n]")

# 검색 결과 상한. 단일 검색어는 기존대로 넉넉히(20), 복수 검색어일 때는 키보드가
# 터지지 않도록 검색어당 적게 가져오고 전체 상한을 둔다.
SINGLE_TERM_LIMIT = 20
MULTI_TERM_LIMIT = 5
MULTI_TOTAL_CAP = 30


def _norm(text: str) -> str:
    """비교용 정규화 — 공백·대소문자 무시.

    'SK 하이닉스'나 'sk하이닉스'로도 'SK하이닉스'를 찾게 한다. Stage 8 질의에서
    공백 하나 때문에 검색이 전멸했던 것과 같은 부류의 문제다.
    """
    return text.replace(" ", "").casefold()


def split_query_terms(raw: str) -> list[str]:
    """쉼표·줄바꿈으로 구분된 복수 검색어를 분리한다. 순서 유지, 중복 제거.

    애널리스트가 데스크톱에서 커버리지 종목을 한 줄에 붙여넣는 사용을 위한 것:
    '/add 삼성전자, LG전자, SK하이닉스'
    """
    terms: list[str] = []
    seen: set[str] = set()
    for part in _TERM_SEPARATORS.split(raw):
        term = part.strip()
        if not term:
            continue
        key = _norm(term)
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


class CorpSearchResult(NamedTuple):
    """복수 검색어 조회 결과.

    preselected: 검색어와 정확히 일치해 미리 선택해 둘 기업(code -> name).
      한 번에 여러 곳을 넣을 때 정확히 친 이름까지 일일이 누르게 하면
      기능의 의미가 없다. 등록은 여전히 '등록 완료' 버튼을 눌러야 일어난다.
    not_found: 결과가 0건인 검색어 — 조용히 버리면 등록된 줄 안다.
    truncated: 상한에 걸려 일부 결과를 못 담았는지.
    """
    items: list[tuple[str, str]]
    preselected: dict[str, str]
    not_found: list[str]
    truncated: bool


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


async def search_corps(corp_name: str, limit: int = SINGLE_TERM_LIMIT) -> list[tuple[str, str]]:
    """Search listed companies by name. Exact → prefix → substring, up to `limit`.

    비교는 공백·대소문자를 무시한다(_norm). 표시는 원래 이름 그대로.
    """
    await load_corp_cache()
    query = _norm(corp_name)
    if not query:
        return []
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
        norm_name = _norm(name)
        if norm_name == query:
            exact.append((code, name))
        elif norm_name.startswith(query):
            starts_with.append((code, name))
        elif query in norm_name:
            partial.append((code, name))

    starts_with.sort(key=lambda x: x[1])
    partial.sort(key=lambda x: x[1])
    return (exact + starts_with + partial)[:limit]


async def search_corps_multi(terms: list[str]) -> CorpSearchResult:
    """검색어 여러 개를 각각 조회해 하나의 선택 목록으로 합친다.

    검색어 순서를 유지하고 같은 기업은 한 번만 담는다. 검색어와 이름이 정확히
    일치하면 미리 선택해 둔다 — 그래야 '/add 삼성전자, LG전자, SK하이닉스'가
    버튼 한 번으로 끝난다.
    """
    per_term = SINGLE_TERM_LIMIT if len(terms) <= 1 else MULTI_TERM_LIMIT
    items: list[tuple[str, str]] = []
    preselected: dict[str, str] = {}
    not_found: list[str] = []
    seen_codes: set[str] = set()
    truncated = False

    for term in terms:
        rows = await search_corps(term, limit=per_term)
        if not rows:
            not_found.append(term)
            continue
        for code, name in rows:
            if code in seen_codes:
                continue
            if len(items) >= MULTI_TOTAL_CAP:
                truncated = True
                break
            seen_codes.add(code)
            items.append((code, name))
        if _norm(term):
            for code, name in rows:
                if _norm(name) == _norm(term) and code in seen_codes:
                    preselected[code] = name

    return CorpSearchResult(items, preselected, not_found, truncated)
