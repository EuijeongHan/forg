"""거래소 소관 공시 원문 추출 검증 (2026-08-26 사용자 신고).

증상: 단일판매·공급계약체결 알림 요약이 전부 '정보 없음'.
원인 둘 — 둘 다 원문이 비어서 LLM에 제목만 전달된 탓이었다.
  1) 거래소 공시(접수번호 …8xxxxx)는 main.do에 node1['dcmNo'] 블록이 없다.
     문서번호·dtd는 viewDoc(...) 인자에 있고, dtd가 dart4.xsd가 아니라 HTML이다.
  2) 거래소 뷰어는 MS949로 내려온다. utf-8로 강제하면 한글이 전부 깨진다.
네트워크 없이 실제 응답 형태를 재현해 검증한다.
"""
import os
import pathlib
import sys

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
os.environ.setdefault("DART_API_KEY", "dummy")
sys.path.insert(0, APP)

import dart  # noqa: E402

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


class FakeResponse:
    def __init__(self, content: bytes, charset: str | None):
        self.content = content
        self.headers = {"content-type": f"text/html; charset={charset}" if charset else "text/html"}


# 실측 형태(2026-08-26 한전산업 20260826800502): dtd=HTML, node1 블록 없음
KRX_MAIN = '''<script>
  function foo(){ viewDoc("20260826800502", "11552351", "0", "0", "0", "HTML", ""); }
</script>'''
# 주요사항보고서(카카오 20260821000047)는 node1 블록과 viewDoc 양쪽을 갖는다
DART_MAIN = '''<script>
  node1['rcpNo'] = "20260821000047";
  node1['dcmNo'] = "11544633";
  viewDoc("20260821000047", "11544633", "1", "829", "2521", "dart4.xsd", "");
</script>'''

KRX_BODY_KR = (
    "<html><body><p>단일판매ㆍ공급계약 체결</p>"
    "<p>계약금액(원) 27,593,490,000 최근매출액(원) 394,548,141,840 매출액대비(%) 6.99</p>"
    "<p>계약상대 한국수력원자력</p>"
    "<p>계약기간 시작일 2026-09-01 종료일 2029-08-31</p></body></html>"
)

# ── viewDoc 파라미터 추출 ────────────────────────────────────────────
m = dart._VIEW_DOC_RE.search(KRX_MAIN)
check("거래소 공시에서 viewDoc 인식", m is not None)
check("문서번호 추출", m.group(2), "11552351")
check("dtd는 HTML (하드코딩 dart4.xsd면 빈손)", m.group(6), "HTML")

m2 = dart._VIEW_DOC_RE.search(DART_MAIN)
check("주요사항보고서도 같은 패턴으로 인식", m2 is not None)
check("주요사항보고서 문서번호", m2.group(2), "11544633")
check("주요사항보고서 dtd", m2.group(6), "dart4.xsd")
check("eleId·offset·length도 원문 값 사용", (m2.group(3), m2.group(4), m2.group(5)),
      ("1", "829", "2521"))

# 구 형식(viewDoc 없음)도 폴백으로 살린다
check("node1 폴백 패턴 유지", bool(__import__("re").findall(
    r"node1\['dcmNo'\]\s*=\s*\"(\d+)\"", DART_MAIN)))

# ── 인코딩 ───────────────────────────────────────────────────────────
krx = FakeResponse(KRX_BODY_KR.encode("cp949"), "MS949")
text = dart._decode(krx)
check("MS949 응답이 한글로 디코드됨", "한국수력원자력" in text)
check("계약금액 보존", "27,593,490,000" in text)

utf = FakeResponse("<p>주요사항보고서 회사분할 결정</p>".encode("utf-8"), "utf-8")
check("utf-8 응답도 그대로", "회사분할 결정" in dart._decode(utf))

# charset 미표기 + cp949 본문 → 폴백으로 살아난다
noc = FakeResponse(KRX_BODY_KR.encode("cp949"), None)
check("charset 없어도 cp949 폴백", "계약상대" in dart._decode(noc))

# 깨진 바이트가 섞여도 예외 없이 문자열을 돌려준다
broken = FakeResponse(b"\xff\xfe\x00abc", "utf-8")
check("디코드 불가 바이트도 예외 없음", isinstance(dart._decode(broken), str))

# ── 본문 텍스트 추출 ─────────────────────────────────────────────────
parser = dart.TextExtractor()
parser.feed(dart._decode(krx))
body = " ".join(parser.text)
for want in ["27,593,490,000", "6.99", "한국수력원자력", "2026-09-01", "2029-08-31"]:
    check(f"본문에 {want} 포함", want in body)



# ── 원문을 못 읽었으면 요약하지 않는다 (2026-11-02 이용 기록) ─────────
# 이전에는 LLM에게 '유형만 보고 한 문장 설명'을 시켰고, 사용자는 이런 걸 받았다:
#   "…공시는 특정 계약에 따른 매출 규모, 기간, 거래 상대방 관계 등을 포함하는
#    중요 계약 내용을 보고하는 공시입니다"
# 그 공시에 대한 사실이 0개인데 요약처럼 보이고, 실패 문구가 없어 캐시까지 됐다.
# 10-13 피아이이·10-16 제이티에서 실제로 그랬으며 두 건 다 재조회는 정상이었다.
import asyncio as _asyncio  # noqa: E402
import summarizer as _sm  # noqa: E402


def _empty_summary():
    called = []

    async def should_not_run(prompt):
        called.append(prompt)
        return "제이티의 공급계약 공시는 중요 계약 내용을 보고하는 공시입니다."

    orig = _sm._generate_with_fallback
    _sm._generate_with_fallback = should_not_run
    try:
        out = _asyncio.run(_sm.summarize_disclosure("제이티", "단일판매ㆍ공급계약체결", ""))
    finally:
        _sm._generate_with_fallback = orig
    return out, called


out, called = _empty_summary()
check("빈 원문이면 LLM을 부르지 않음", called == [])
check("못 읽었다고 밝힌다", "읽지 못했습니다" in out)
check("유형 설명을 지어내지 않음", "보고하는 공시입니다" not in out)
check("원문으로 안내", "DART" in out)

# 이 문구는 캐시되지 않아야 다음 클릭에서 재조회된다
import services.disclosure_service as _ds  # noqa: E402
import inspect as _inspect  # noqa: E402
_src = _inspect.getsource(_ds.summarize_by_receipt)
check("'읽지 못했습니다'는 캐시 제외 조건에 포함", "읽지 못했습니다" in _src)

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
