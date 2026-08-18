"""HTML 안전 절단 검증 (2026-08-19 리뷰 P1).

완성 HTML을 임의 인덱스로 자르면 태그·엔티티 중간이 잘리고, 텔레그램이
malformed HTML을 거부하면 Seen 미기록 → 매 폴링 재시도 → 영구 미발송이 된다.
확인할 성질:
  - 알림: 요약 '평문'을 줄여 재조립 — 태그 균형 유지, 원문 링크 항상 보존
  - 다이제스트: 줄 경계 절단 — 한 줄이 완결 HTML이므로 안전
  - 이스케이프 팽창(& → &amp;)이 있어도 예산을 넘지 않음
"""
import asyncio
import os
import pathlib
import sys

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
sys.path.insert(0, APP)

import notifier  # noqa: E402

sent: list[str] = []


class FakeBot:
    async def send_message(self, chat_id, text, **kwargs):
        sent.append(text)


notifier.get_bot = lambda: FakeBot()

failures = []


def check(label, actual, expected=True):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (기대 {expected!r})"))
    if not ok:
        failures.append(label)


def html_balanced(text: str) -> bool:
    return (
        text.count("<b>") == text.count("</b>")
        and text.count("<a ") == text.count("</a>")
        and "<" not in text.replace("<b>", "").replace("</b>", "")
                            .replace("<a ", "").replace("</a>", "").split(">")[-1]
    )


async def main():
    # ── 파트 1: 알림 — 평문 단위 절단 ───────────────────────────────
    # 이스케이프 팽창을 유발하는 특수문자를 잔뜩 섞은 초장문 요약
    huge = ("전환가 5,000원 & 한도 <10%> 조정 " * 400)

    sent.clear()
    ok = await notifier.send_alert("chat1", "가나전자&바이오", "전환사채발행결정<정정>",
                                   "20260819000001", huge, tier="important")
    check("발송 성공", ok, True)
    msg = sent[0]
    check("한도 이내", len(msg) <= notifier.MAX_MESSAGE_LENGTH, True)
    check("태그 균형 유지(HTML 무손상)", html_balanced(msg), True)
    check("원문 링크 보존", msg.endswith("원문 보기</a>"), True)
    check("절단 안내 포함", "잘렸습니다" in msg, True)
    check("엔티티 중간 절단 없음", "&am\n" not in msg and not msg.rstrip().endswith("&"), True)

    # 짧은 요약은 그대로
    sent.clear()
    await notifier.send_alert("chat1", "가나전자", "유상증자결정", "20260819000002",
                              "짧은 요약", tier="important")
    check("짧은 요약은 절단 없음", "잘렸습니다" not in sent[0], True)
    check("짧은 요약 본문 보존", "짧은 요약" in sent[0], True)

    # 긴 헤더(긴급)를 합쳐도 총길이 예산 준수
    sent.clear()
    await notifier.send_alert("chat1", "가나전자", "상장폐지결정", "20260819000003",
                              huge, tier="urgent")
    check("긴급 헤더 포함 총길이 한도 이내", len(sent[0]) <= notifier.MAX_MESSAGE_LENGTH, True)
    check("긴급 헤더 보존", "긴급" in sent[0], True)
    check("긴급도 링크 보존", sent[0].endswith("원문 보기</a>"), True)

    # 봇 조회 경로(기본 budget)도 동일 보장
    direct = notifier.build_disclosure_message("가나전자", "합병결정", "20260819000004", huge)
    check("조회 경로도 한도 이내", len(direct) <= notifier.MAX_MESSAGE_LENGTH, True)
    check("조회 경로도 태그 균형", html_balanced(direct), True)

    # ── 파트 2: 다이제스트 — 줄 경계 절단 ───────────────────────────
    lines = "\n".join(
        f'· <b>회사{i} & 특수문자</b> 아주아주긴보고서명칭{i} '
        f'<a href="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=2026{i:010d}">원문</a>'
        for i in range(120)
    )
    sent.clear()
    ok = await notifier.send_html_message("chat1", "📄 <b>헤더</b>\n\n" + lines)
    check("다이제스트 발송 성공", ok, True)
    dg = sent[0]
    check("다이제스트 한도 이내", len(dg) <= notifier.MAX_MESSAGE_LENGTH, True)
    check("다이제스트 태그 균형", html_balanced(dg), True)
    body_lines = [l for l in dg.split("\n") if l.startswith("·")]
    check("남은 줄은 전부 완결 HTML", all(l.endswith("</a>") for l in body_lines), True)
    check("생략 안내 포함", "이하 생략" in dg, True)


asyncio.run(main())

if failures:
    print(f"\n{len(failures)}건 실패: {failures}")
    sys.exit(1)
print("\n전부 통과")
