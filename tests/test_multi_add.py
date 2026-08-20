"""/add 복수 검색어 검증 (2026-08-20).

애널리스트가 데스크톱에서 커버리지 종목을 한 줄로 붙여넣는 흐름:
    /add 삼성전자, LG전자, SK하이닉스
확인할 성질:
  - 쉼표·줄바꿈 분리, 순서 유지, 중복 제거
  - 공백·대소문자 무시 매칭('SK 하이닉스' → 'SK하이닉스')
  - 이름이 정확히 일치하면 미리 선택 (등록은 여전히 확인 버튼을 눌러야 발생)
  - 못 찾은 검색어를 반드시 드러낸다(조용히 버리면 등록된 줄 안다)
  - callback_data가 텔레그램 64바이트 한도를 넘지 않는다
"""
import asyncio
import os
import pathlib
import sys

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DART_API_KEY", "dummy")
sys.path.insert(0, APP)

from services import corp_service  # noqa: E402
import bot  # noqa: E402

# (corp_code, corp_name, stock_code) — stock_code가 있어야 상장사로 취급된다
corp_service._corp_cache = [
    ("00126380", "삼성전자", "005930"),
    ("00126381", "삼성전자우", "005935"),
    ("00401731", "SK하이닉스", "000660"),
    ("00356361", "LG전자", "066570"),
    ("00164742", "현대자동차", "005380"),
    ("99999901", "삼성스팩1호", "900001"),      # EXCLUDE_KEYWORDS로 제외돼야 함
    ("99999902", "비상장회사", ""),              # stock_code 없음 → 제외
    ("99999903", "한국전자금융서비스홀딩스", "123456"),  # 긴 이름 (callback_data 한도)
]

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.markups = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        self.markups.append(kw.get("reply_markup"))


class FakeUpdate:
    def __init__(self, chat_id="u1"):
        self.effective_chat = type("C", (), {"id": chat_id, "first_name": ""})()
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, args):
        self.args = args
        self.user_data = {}


class FakeQuery:
    def __init__(self, data, chat_id="u1"):
        self.data = data
        self.from_user = type("U", (), {"id": chat_id, "first_name": ""})()
        self.answers = []
        self.markup = None

    async def answer(self, text=None, **kw):
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup = reply_markup


def labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


async def main():
    # ── 파트 1: 검색어 분리 ──────────────────────────────────────────
    sp = corp_service.split_query_terms
    check("쉼표 분리", sp("삼성전자, LG전자, SK하이닉스"), ["삼성전자", "LG전자", "SK하이닉스"])
    check("쉼표 뒤 공백 없어도 분리", sp("삼성전자,LG전자"), ["삼성전자", "LG전자"])
    check("줄바꿈 분리(엑셀 붙여넣기)", sp("삼성전자\nLG전자"), ["삼성전자", "LG전자"])
    check("전각 쉼표 분리", sp("삼성전자，LG전자"), ["삼성전자", "LG전자"])
    check("중복 검색어 제거", sp("삼성전자, 삼성전자"), ["삼성전자"])
    check("빈 조각 무시", sp("삼성전자, , ,LG전자,"), ["삼성전자", "LG전자"])
    check("단일 검색어는 그대로", sp("삼성전자"), ["삼성전자"])
    check("공백뿐이면 빈 목록", sp("  ,  "), [])
    check("기업명 내 공백은 보존", sp("SK 하이닉스"), ["SK 하이닉스"])

    # ── 파트 2: 단일 검색(정규화) ────────────────────────────────────
    rows = await corp_service.search_corps("삼성전자")
    check("정확 일치가 맨 앞", rows[0][1], "삼성전자")
    check("접두 일치도 포함", "삼성전자우" in [n for _, n in rows], True)
    check("스팩 제외", all("스팩" not in n for _, n in rows), True)

    rows = await corp_service.search_corps("SK 하이닉스")
    check("공백 무시 매칭", [n for _, n in rows], ["SK하이닉스"])
    rows = await corp_service.search_corps("sk하이닉스")
    check("대소문자 무시 매칭", [n for _, n in rows], ["SK하이닉스"])
    check("비상장 제외", await corp_service.search_corps("비상장회사"), [])
    check("빈 검색어는 빈 결과", await corp_service.search_corps("  "), [])
    check("limit 적용", len(await corp_service.search_corps("삼성", limit=1)), 1)

    # ── 파트 3: 복수 검색 집계 ───────────────────────────────────────
    r = await corp_service.search_corps_multi(["삼성전자", "LG전자", "SK하이닉스"])
    names = [n for _, n in r.items]
    check("입력 순서 유지", names[:1] + [n for n in names if n in ("LG전자", "SK하이닉스")],
          ["삼성전자", "LG전자", "SK하이닉스"])
    check("정확 일치 3곳 자동 선택", sorted(r.preselected.values()),
          sorted(["삼성전자", "LG전자", "SK하이닉스"]))
    check("못 찾은 검색어 없음", r.not_found, [])
    check("잘림 없음", r.truncated, False)

    r = await corp_service.search_corps_multi(["삼성전자", "없는회사이름"])
    check("못 찾은 검색어 수집", r.not_found, ["없는회사이름"])
    check("찾은 것은 그대로 표시", "삼성전자" in [n for _, n in r.items], True)

    r = await corp_service.search_corps_multi(["삼성전자", "삼성전자우"])
    codes = [c for c, _ in r.items]
    check("중복 기업은 한 번만", len(codes), len(set(codes)))
    check("각 검색어의 정확 일치 모두 선택", sorted(r.preselected.values()),
          ["삼성전자", "삼성전자우"])

    r = await corp_service.search_corps_multi(["삼성"])
    check("모호한 검색어는 자동 선택 안 함", r.preselected, {})

    # ── 파트 4: 봇 핸들러 ────────────────────────────────────────────
    bot.pending_selections.clear()
    u = FakeUpdate()
    ctx = FakeContext([])
    await bot.add(u, ctx)
    check("인자 없으면 복수 입력 예시 안내", "삼성전자, LG전자" in u.message.replies[0], True)

    u = FakeUpdate()
    ctx = FakeContext(["삼성전자,", "LG전자,", "SK하이닉스"])
    await bot.add(u, ctx)
    check("검색 중 안내에 개수 표기", "3곳 검색 중" in u.message.replies[0], True)
    check("결과 안내에 검색어 수", "검색어 3개" in u.message.replies[1], True)
    check("미리 선택 안내", "미리 선택" in u.message.replies[1], True)
    sel = bot.pending_selections["u1"]
    check("3곳이 미리 선택된 상태", sorted(sel.values()),
          sorted(["삼성전자", "LG전자", "SK하이닉스"]))
    marks = labels(u.message.markups[1])
    check("선택된 항목에 체크 표시", sum(1 for m in marks if m.startswith("✅")), 3)
    check("등록 완료 버튼 존재", "📥 등록 완료" in marks, True)

    u = FakeUpdate()
    ctx = FakeContext(["삼성전자,", "없는회사이름"])
    await bot.add(u, ctx)
    check("못 찾은 검색어를 사용자에게 노출", "못 찾음: 없는회사이름" in u.message.replies[1], True)

    u = FakeUpdate()
    ctx = FakeContext(["없는회사이름"])
    await bot.add(u, ctx)
    check("전부 못 찾으면 안내 후 종료", "찾을 수 없습니다" in u.message.replies[1], True)
    check("전멸 시 키보드 없음", u.message.markups[1], None)

    # ── 파트 5: 토글 콜백 ────────────────────────────────────────────
    bot.pending_selections.clear()
    ctx = FakeContext([])
    ctx.user_data["search_results"] = {"00126380": "삼성전자", "00356361": "LG전자"}
    q = FakeQuery("toggle:00126380")
    await bot.toggle_callback(type("U", (), {"callback_query": q})(), ctx)
    check("토글로 선택 추가", bot.pending_selections["u1"], {"00126380": "삼성전자"})
    await bot.toggle_callback(type("U", (), {"callback_query": q})(), ctx)
    check("다시 누르면 해제", bot.pending_selections["u1"], {})

    q2 = FakeQuery("toggle:00126380")
    empty_ctx = FakeContext([])
    await bot.toggle_callback(type("U", (), {"callback_query": q2})(), empty_ctx)
    check("목록 만료 시 안내", "만료" in (q2.answers[0] or ""), True)
    check("만료 시 키보드 재구성 안 함", q2.markup, None)

    # ── 파트 6: callback_data 한도·긴 목록 ───────────────────────────
    long_rows = await corp_service.search_corps("한국전자금융서비스홀딩스")
    kb = bot.build_add_keyboard(long_rows, {})
    over = [b.callback_data for row in kb.inline_keyboard for b in row
            if len(b.callback_data.encode()) > 64]
    check("긴 기업명도 callback_data 64바이트 이내", over, [])

    many = [(f"{i:08d}", f"기업{i}") for i in range(15)]
    kb = bot.build_add_keyboard(many, {})
    first_row = kb.inline_keyboard[0][0].text
    check("목록이 길면 상단에도 등록 완료", first_row, "📥 등록 완료")
    check("하단 등록 완료 유지", kb.inline_keyboard[-1][0].text, "📥 등록 완료")
    kb_short = bot.build_add_keyboard(many[:3], {})
    check("짧은 목록은 상단 버튼 없음", kb_short.inline_keyboard[0][0].text, "기업0")


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
