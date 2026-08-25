"""첫 실사용자 피드백 2건에 대한 수정 검증 (2026-08-21 접수).

① "아래 글 요약이 잘못됨" — 회사분할결정 카드가 raw DART 필드명(bddd·od_a_at_t·
   rs_sm_atn…)을 그대로 노출. 분기가 없는 유형이 data를 통째로 찍던 폴백 탓.
② "오늘 /market에 카카오에 대한 내용은 없던 이유가 궁금" — 그날 중요 공시 259건
   중 앞 20건만 그렸고 해당 공시는 232번째였다. 나머지에 도달할 방법이 없었다.
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

import summarizer  # noqa: E402
import bot  # noqa: E402

# 2026-08-21 카카오 '주요사항보고서(회사분할결정)' 실응답에서 발췌(§4.2 검증)
KAKAO_SPLIT = {
    "rcept_no": "20260821000047", "corp_cls": "Y",
    "corp_code": "00258801", "corp_name": "카카오",
    "bddd": "2026년 08월 21일", "od_a_at_t": "4", "od_a_at_b": "0", "adt_a_atn": "-",
    "rs_sm_atn": "예", "gmtsck_prd": "2026년 12월 17일", "popt_ctr_atn": "아니오",
    "ffdtl_tast": "5,931,750,202,958", "ffdtl_std": "2026년 06월 30일",
    "dvfcmp_cmpnm": "주식회사 카카오에이아이 (가칭)\n (KakaoAI Corp.) (가칭)",
    "dvfcmp_mbsn": "카카오톡 기반 플랫폼 사업", "dvfcmp_rlst_atn": "예",
    "atdv_excmp_cmpnm": "주식회사 카카오엑스 (가칭)\n (KakaoX Corp.) (가칭)",
    "atdv_excmp_mbsn": "투자사업", "atdv_excmp_atdv_lstmn_atn": "예",
    "abcr_crrt": "36.48537", "dvdt": "2027년 01월 01일",
    "abcr_nstkasstd": "2026년 12월 31일",
    "abcr_trspprpd_bgd": "2026년 12월 30일", "abcr_trspprpd_edd": "2027년 01월 26일",
    "abcr_nstklstprd": "2027년 01월 27일", "dvrgsprd": "2027년 01월 04일",
}

RAW_KEYS = ["bddd", "od_a_at_t", "od_a_at_b", "rs_sm_atn", "gmtsck_prd",
            "popt_ctr_atn", "ffdtl_tast", "dvfcmp_cmpnm", "abcr_crrt"]

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


class FakeQuery:
    def __init__(self, data, chat_id="u1"):
        self.data = data
        self.from_user = type("U", (), {"id": chat_id})()
        self.answers = []
        self.text = None
        self.markup = None

    async def answer(self, text=None, **kw):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        self.text = text
        self.markup = reply_markup


class FakeResult:
    def __init__(self, items, header="📋 전체 시장 오늘 공시 · 중요"):
        self.items = items
        self._header = f"{header} ({len(items)}건)"
        self.filtered_to_empty = False
        self.query = ""
        self.total_before_query = len(items)

    def header(self):
        return self._header

    def date_label(self):
        return "오늘"


def btn_texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def cb_datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def main():
    # ── 피드백 ①: 분할 카드 ──────────────────────────────────────────
    card = summarizer.format_typed_disclosure(
        "카카오", "주요사항보고서(회사분할결정)", KAKAO_SPLIT)
    leaked = [k for k in RAW_KEYS if k in card]
    check("raw DART 필드명이 카드에 없음", leaked, [])
    check("분할 카드 헤더", "[분할 결정]" in card, True)
    check("분할신설회사 표시", "카카오에이아이" in card, True)
    check("존속회사 표시", "카카오엑스" in card, True)
    check("분할비율 표시", "36.48537" in card, True)
    check("분할기일 표시", "2027년 01월 01일" in card, True)
    check("주주총회 예정일 표시", "2026년 12월 17일" in card, True)
    # 애널리스트에게 가장 실무적인 항목
    check("매매거래 정지 예정기간 표시",
          "2026년 12월 30일 ~ 2027년 01월 26일" in card, True)
    check("신주 상장 예정일 표시", "2027년 01월 27일" in card, True)
    check("값의 줄바꿈이 한 줄로 정리됨", "(가칭)\n (KakaoAI" not in card, True)
    check("숫자에 없는 단위를 붙이지 않음", "36.48537%" in card, False)

    # ── 폴백: 분기 없는 유형도 raw 키를 노출하지 않는다 ──────────────
    fb = summarizer.format_typed_disclosure(
        "테스트", "무상증자결정",
        {"bddd": "2026년 08월 21일", "ffdtl_cpt": "1,000", "zzz_unknown": "값",
         "corp_code": "00000000"})
    check("폴백도 raw 키 미노출", "bddd" in fb or "zzz_unknown" in fb, False)
    check("폴백은 검증된 필드만 한글 라벨", "이사회결의일: 2026년 08월 21일" in fb, True)
    check("모르는 키의 값도 노출 안 함", "값" in fb, False)

    fb2 = summarizer.format_typed_disclosure("테스트", "무상증자결정", {"zzz": "x"})
    check("표시할 게 없으면 원문 확인 안내", "원문을 확인" in fb2, True)
    check("빈 경우에도 raw 키 없음", "zzz" in fb2, False)

    # ── 피드백 ②: 조회 결과 페이지 넘김 ─────────────────────────────
    bot.result_pages.clear()
    many = [{"rcept_no": f"2026082100{i:04d}", "corp_name": f"기업{i}",
             "report_nm": "유상증자결정"} for i in range(259)]
    # 카카오를 실제 사고와 같은 위치(232번째)에 둔다
    many[232] = {"rcept_no": "20260821000047", "corp_name": "카카오",
                 "report_nm": "주요사항보고서(회사분할결정)"}

    u = FakeUpdate()
    await bot._send_query_result(u, FakeResult(many))
    check("1페이지는 20건", sum(1 for t in btn_texts(u.message.markups[0]) if "|" in t), 20)
    check("전체 건수 안내", "259건" in u.message.replies[0], True)
    check("표시 범위 안내", "1–20번째" in u.message.replies[0], True)
    check("다음 버튼 존재", any("다음" in t for t in btn_texts(u.message.markups[0])), True)
    check("첫 페이지엔 이전 버튼 없음",
          any("이전" in t for t in btn_texts(u.message.markups[0])), False)

    # 232번째까지 넘겨서 실제로 도달 가능한지
    q = FakeQuery("page:220")
    await bot.page_callback(type("U", (), {"callback_query": q})(), None)
    check("페이지 이동 후 카카오 도달", any("카카오" in t for t in btn_texts(q.markup)), True)
    check("이동 후 범위 안내", "221–240번째" in q.text, True)
    check("중간 페이지엔 이전·다음 모두", 
          any("이전" in t for t in btn_texts(q.markup)) and any("다음" in t for t in btn_texts(q.markup)),
          True)

    # 마지막 페이지
    q2 = FakeQuery("page:240")
    await bot.page_callback(type("U", (), {"callback_query": q2})(), None)
    check("마지막 페이지는 19건", sum(1 for t in btn_texts(q2.markup) if "|" in t), 19)
    check("마지막 페이지엔 다음 버튼 없음",
          any("다음" in t for t in btn_texts(q2.markup)), False)

    # 범위를 벗어난 offset은 마지막 페이지로 고정
    q3 = FakeQuery("page:9999")
    await bot.page_callback(type("U", (), {"callback_query": q3})(), None)
    check("과도한 offset도 안전", "241–259번째" in q3.text, True)

    # 20건 이하면 네비게이션 없음
    u2 = FakeUpdate("u2")
    await bot._send_query_result(u2, FakeResult(many[:12]))
    check("20건 이하는 페이지 버튼 없음",
          any("page:" in c for c in cb_datas(u2.message.markups[0])), False)
    check("20건 이하는 범위 안내 생략", "번째 표시" in u2.message.replies[0], False)

    # 만료 처리
    bot.result_pages.clear()
    q4 = FakeQuery("page:20")
    await bot.page_callback(type("U", (), {"callback_query": q4})(), None)
    check("만료 시 안내", "만료" in (q4.answers[0] or ""), True)
    check("만료 시 메시지 수정 안 함", q4.text, None)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
