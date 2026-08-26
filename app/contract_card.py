"""단일판매·공급계약체결 결정론적 파싱.

이 유형은 정형 API가 없어 원문을 LLM이 읽는다. 그런데 원문은 번호가 붙은 표를
평문으로 펼친 것이라, 라벨 경계만 잡으면 값을 코드로 뽑을 수 있다.

그렇게 하는 이유(2026-08-26 평가 실측): gpt-4o-mini와 gpt-5-mini가 **둘 다**
'회사와의 관계'를 바로 뒤 항목('최근 3년간 동종계약 이행여부')의 값으로 잘못
읽었다. 모델 능력이 아니라 표가 평문이 되며 생긴 구조적 모호성이라, 더 비싼
모델로는 해결되지 않는다. 숫자·라벨은 코드가 잡고 LLM은 서술만 — 정형 카드와
같은 원칙이다.

업종별로 번호와 라벨 구성이 다르므로(건설·용역·물품) 번호가 아니라 **라벨**을
앵커로 쓰고, 다음 라벨이 나올 때까지를 값으로 본다.
"""
import re

# 값의 끝을 판정하기 위한 알려진 라벨 전체. 길이 내림차순으로 매칭해
# '계약금액'이 '계약금액 총액(원)'을 먼저 잘라먹지 않게 한다.
_LABELS = [
    "판매ㆍ공급계약 내용", "판매ㆍ공급계약 구분", "체결계약명", "계약내역",
    "조건부 계약여부", "확정 계약금액", "조건부 계약금액", "계약금액 총액(원)",
    "계약금액", "최근 매출액(원)", "최근매출액(원)", "매출액 대비(%)", "매출액대비(%)",
    "대규모법인여부", "계약상대방", "계약상대", "주요사업",
    "회사와 최근 3년간 동종계약 이행여부", "회사와의 관계",
    "판매ㆍ공급지역", "계약기간", "시작일", "종료일",
    "주요 계약조건", "계약금ㆍ선급금 유무", "대금지급 조건 등",
    "판매ㆍ공급방식", "자체생산", "외주생산", "기타",
    "계약(수주)일자", "공시유보 관련내용", "공시유보", "유보기한", "유보사유",
    "기타 투자판단에 참고할 사항", "기타 투자판단과 관련한 중요사항",
]
_LABEL_ALT = "|".join(re.escape(x) for x in sorted(_LABELS, key=len, reverse=True))


# 기재정정 공시는 본문 앞에 '정정사항' 표(정정항목/정정전/정정후)가 붙는다.
# 그 표를 본문으로 오인하면 정정 '전' 값과 표 문구가 카드에 섞인다 — 첫 구현이
# 정확히 그랬다(계약상대방 '방', 계약기간 '… ~ 변경 4. 정정사항', 정정 전 금액).
# 본문은 항상 '1. 판매ㆍ공급계약'으로 시작하므로 그 지점부터 읽는다.
_BODY_ANCHOR = re.compile(r"1\s*\.\s*판매ㆍ공급계약")
_CORRECTION_REASON = re.compile(r"정정사유\s*(.*?)(?=\s*\d+\s*\.\s*정정사항|\s*정정사항|$)", re.S)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# '기타 투자판단…' 섹션은 첫 사용자가 네 번째로 요청한 '투자자 주요참고'다.
# 대부분 정형 문구(재무제표 기준·VAT 별도)지만, 회당 단가·환율·분할계약 금액·
# 상대방 비공개 사유·조회공시 답변 갈음처럼 표에 없는 사실이 여기에만 있다.
# 정정공시 표에도 같은 라벨이 나오므로 **마지막** 것을 본문으로 본다.
_NOTE_ANCHOR = re.compile(r"기타 투자판단(?:에 참고할 사항|과 관련한 중요사항)")


def _body(text: str) -> str:
    """정정 헤더를 걷어낸 실제 공시 본문."""
    starts = [m.start() for m in _BODY_ANCHOR.finditer(text)]
    return text[starts[-1]:] if starts else text


def _value_after(text: str, label: str) -> str:
    """label 다음부터 '다음 라벨' 직전까지를 값으로 본다."""
    m = re.search(re.escape(label) + r"\s*(.*?)(?=\s*(?:\d+\s*\.\s*)?(?:" + _LABEL_ALT + r")|$)",
                  text, re.S)
    if not m:
        return ""
    # 표의 빈 칸은 '-'로 내려온다. 그것만 남으면 값이 없는 것이다 —
    # 다음 항목의 값을 끌어오지 않는 것이 이 파서의 존재 이유다.
    v = " ".join(m.group(1).split())
    v = re.sub(r"^[-\s]+|[-\s]+$", "", v)
    return v


def _clip(value: str, limit: int) -> str:
    """길면 자르되 잘렸다는 걸 보이게 한다 — 문장 중간에서 끊긴 값은 오해를 부른다."""
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def _value_free(text: str, label: str) -> str:
    """자유서술 칸 전용 — 번호 붙은 **다음 항목**에서만 끊는다.

    표 칸은 라벨 경계로 끊으면 되지만, 서술 칸에는 라벨과 같은 낱말이 문장 안에
    그대로 나온다. 키이스트는 대금지급 칸에 '(2) 회당 계약금액 : 805,000,000원/회'
    라고 적었는데, '계약금액'을 라벨로 보는 순간 계약 규모를 좌우하는 그 숫자
    직전에서 값이 잘렸다. 실제 항목 구분은 '7.', '8.' 같은 번호가 한다.
    """
    m = re.search(re.escape(label) + r"\s*(.*?)(?=\s*\d{1,2}\s*\.\s*(?:" + _LABEL_ALT + r")|$)",
                  text, re.S)
    if not m:
        return ""
    v = " ".join(m.group(1).split())
    return re.sub(r"^[-\s]+|[-\s]+$", "", v)


def _amount(text: str, label: str) -> str:
    """라벨 뒤 첫 숫자만. 뒤따르는 다른 항목의 숫자를 삼키지 않는다."""
    v = _value_after(text, label)
    m = re.search(r"-?[\d,]{4,}", v)
    return m.group(0) if m else ""


def extract_notes(text: str) -> str:
    """'기타 투자판단…' 섹션 원문. '※ 관련공시' 이후는 버린다."""
    if not text:
        return ""
    matches = list(_NOTE_ANCHOR.finditer(text))
    if not matches:
        return ""
    tail = re.split(r"※\s*관련공시", text[matches[-1].end():])[0]
    return " ".join(tail.split())[:1200]


def parse_contract(text: str) -> dict:
    """공급계약 원문에서 값들을 뽑는다. 못 찾은 항목은 키가 없다."""
    if not text:
        return {}
    full, text = text, _body(text)
    out: dict[str, str] = {}

    if "정정" in full[:200]:
        reason = " ".join(_CORRECTION_REASON.search(full).group(1).split())[:80] \
            if _CORRECTION_REASON.search(full) else ""
        if reason:
            out["정정사유"] = reason

    name = _value_after(text, "체결계약명") or _value_after(text, "판매ㆍ공급계약 내용")
    if name:
        out["계약명"] = _clip(name, 120)

    amount = (_amount(text, "계약금액 총액(원)") or _amount(text, "확정 계약금액")
              or _amount(text, "계약금액"))
    if amount:
        out["계약금액"] = amount
    # '최근 매출액(원)'은 계약상대방 항목에도 또 나온다 — 앞의 것(자사)만 쓴다.
    head = text.split("계약상대")[0] if "계약상대" in text else text
    revenue = _amount(head, "최근 매출액(원)") or _amount(head, "최근매출액(원)")
    if revenue:
        out["최근매출액"] = revenue
    ratio = _value_after(text, "매출액 대비(%)") or _value_after(text, "매출액대비(%)")
    m = re.search(r"[\d.]+", ratio)
    if m:
        out["매출액대비"] = m.group(0)

    # 양식에 따라 라벨이 갈린다: 건설은 '계약상대', 일반은 '계약상대방'. 상호배타라
    # 폴백으로 둘 다 시도하면 '계약상대방'을 '계약상대'로 잘라 값이 '방'이 된다.
    partner = _value_after(text, "계약상대방" if "계약상대방" in text else "계약상대")
    if partner:
        out["계약상대방"] = _clip(partner, 80)
    relation = _value_after(text, "회사와의 관계")
    if relation:
        out["관계"] = relation[:40]

    for key, label in (("시작일", "시작일"), ("종료일", "종료일")):
        m = _DATE.search(_value_after(text, label))
        if m:
            out[key] = m.group(0)

    region = _value_after(text, "판매ㆍ공급지역")
    if region:
        out["공급지역"] = region[:40]
    m = _DATE.search(_value_after(text, "계약(수주)일자"))
    if m:
        out["수주일자"] = m.group(0)

    # 이 칸엔 대금지급 조건만 오지 않는다 — 키이스트는 회당 단가(805,000,000원/회)와
    # 회차(28부작)를 여기 적었다. 계약 규모를 좌우하는 값이라 넉넉히 남긴다.
    terms = _value_free(text, "대금지급 조건 등")
    if terms:
        out["대금지급"] = _clip(terms, 300)
    # 상대방이 비어 있을 때 왜 비었는지가 곧 정보다 — 유보 사유는 공시에 있다.
    hold = _value_after(text, "유보사유")
    if hold:
        out["유보사유"] = _clip(hold, 80)
    return out


def format_contract_card(corp_name: str, report_nm: str, text: str) -> str:
    """파싱 결과를 카드로. 필수 항목을 못 뽑으면 빈 문자열(→ LLM 경로로 폴백)."""
    d = parse_contract(text)
    if not d.get("계약금액") or not (d.get("계약상대방") or d.get("계약명")):
        return ""

    lines = ["[단일판매·공급계약체결]"]
    if "[기재정정]" in report_nm:
        lines[0] = "[단일판매·공급계약체결 — 기재정정]"
    if d.get("정정사유"):
        lines.append(f"• 정정사유: {d['정정사유']}")
    if d.get("계약명"):
        lines.append(f"• 계약명: {d['계약명']}")
    lines.append(f"• 계약금액: {d['계약금액']}원")
    if d.get("최근매출액"):
        lines.append(f"• 최근 매출액: {d['최근매출액']}원")
    if d.get("매출액대비"):
        lines.append(f"• 매출액 대비: {d['매출액대비']}%")
    if d.get("계약상대방"):
        lines.append(f"• 계약상대방: {d['계약상대방']}")
    elif d.get("유보사유"):
        # 침묵보다 낫다: 상대방이 없는 게 아니라 공시가 유보한 것이다.
        lines.append(f"• 계약상대방: 공시유보 ({d['유보사유']})")
    # 값이 '-'뿐이면 아예 표시하지 않는다. 옆 항목 값을 관계로 잘못 적는 것이
    # 두 모델 모두에서 나온 오류였다.
    if d.get("관계"):
        lines.append(f"• 회사와의 관계: {d['관계']}")
    if d.get("시작일") or d.get("종료일"):
        lines.append(f"• 계약기간: {d.get('시작일', '미상')} ~ {d.get('종료일', '미상')}")
    if d.get("공급지역"):
        lines.append(f"• 공급지역: {d['공급지역']}")
    if d.get("수주일자"):
        lines.append(f"• 계약(수주)일자: {d['수주일자']}")
    if d.get("대금지급"):
        lines.append(f"• 대금지급: {d['대금지급']}")
    return "\n".join(lines)
