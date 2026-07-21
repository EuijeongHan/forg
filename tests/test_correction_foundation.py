"""Stage 3 기반(정정 비교 계층) 검증 — 접두어·감지·원본 매칭·연결 멱등·diff·렌더."""
import asyncio
import logging
import os
import pathlib
import sys
import tempfile

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app")
DB = pathlib.Path(tempfile.mkdtemp(prefix="forg-test-")) / "corr.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, APP)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


def part1_units():
    from services.correction_service import strip_title_prefixes, is_correction
    from events.comparator import compare_normalized_data, render_changes

    check("접두어 제거: [기재정정]", strip_title_prefixes("[기재정정]유상증자결정") == "유상증자결정")
    check("접두어 제거: 다중", strip_title_prefixes("[기재정정] [첨부추가] 전환사채발행결정") == "전환사채발행결정")
    check("접두어 없음 그대로", strip_title_prefixes("유상증자결정") == "유상증자결정")
    check("정정 감지: [기재정정]", is_correction("[기재정정]유상증자결정") is True)
    check("정정 감지: 접두어 없는 '정정' 본문은 미감지", is_correction("정정명령 관련 안내") is False)
    check("정정 감지: 일반 공시 False", is_correction("[첨부추가]유상증자결정") is False)

    before = {"amount": "100", "payment_date": "20260801", "bond_type": "사모"}
    after = {"amount": "150", "payment_date": "20260801", "bond_type": None, "issue_price": "5000"}
    changes = compare_normalized_data(before, after)
    fields = {c["field"]: c for c in changes}
    check("diff: 변경 필드만", set(fields) == {"amount", "bond_type", "issue_price"})
    check("diff: 값 방향", fields["amount"]["before"] == "100" and fields["amount"]["after"] == "150")
    check("diff: 중요도 분류", fields["amount"]["significance"] == "major"
          and fields["bond_type"]["significance"] == "minor")

    txt = render_changes(changes)
    check("render: 라벨·화살표", "발행금액: 100 → 150" in txt and "❗" in txt)
    check("render: None → '없음'", "없음" in txt)
    check("render: 변경 없음 문구", render_changes([]) == "핵심 정형 항목의 변경이 없습니다.")


async def part2_linking():
    from sqlalchemy import select, func
    from database import AsyncSessionLocal, engine, Base
    import models  # noqa: F401
    from models import Disclosure, DisclosureRelation
    from services import correction_service

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as s:
        rows = [
            # 원본 (7/10, CB 100억)
            Disclosure(id="d-orig", rcept_no="20260710000100", corp_code="C1", corp_name="A사",
                       report_nm="전환사채권발행결정", rcept_dt="20260710",
                       raw_typed_data={"bd_fta": "10,000,000,000", "cv_prc": "3,000"}),
            # 미끼: 다른 기업 동일 제목
            Disclosure(id="d-other", rcept_no="20260710000200", corp_code="C2", corp_name="B사",
                       report_nm="전환사채권발행결정", rcept_dt="20260710"),
            # 미끼: 같은 기업 다른 제목
            Disclosure(id="d-diff", rcept_no="20260711000100", corp_code="C1", corp_name="A사",
                       report_nm="유상증자결정", rcept_dt="20260711"),
            # 정정본 (7/21, 금액 150억으로 정정)
            Disclosure(id="d-corr", rcept_no="20260721000300", corp_code="C1", corp_name="A사",
                       report_nm="[기재정정]전환사채권발행결정", rcept_dt="20260721",
                       raw_typed_data={"bd_fta": "15,000,000,000", "cv_prc": "3,000"}),
        ]
        for r in rows:
            s.add(r)
        await s.commit()

    async with AsyncSessionLocal() as s:
        r = await s.execute(select(Disclosure).where(Disclosure.id == "d-corr"))
        corr = r.scalar_one()
        out = await correction_service.link_correction(s, corr)
        await s.commit()

    check("원본 매칭: 미끼 배제하고 d-orig", out is not None and out["original"].id == "d-orig")
    check("confidence=rule (추정 연결)", out["confidence"] == "rule")
    ch = {c["field"]: c for c in (out["changes"] or [])}
    check("정정 diff: 발행금액 100억→150억만 검출",
          set(ch) == {"amount"} and ch["amount"]["before"] == "10000000000"
          and ch["amount"]["after"] == "15000000000")

    # 멱등: 재실행에도 관계 1행
    async with AsyncSessionLocal() as s:
        r = await s.execute(select(Disclosure).where(Disclosure.id == "d-corr"))
        await correction_service.link_correction(s, r.scalar_one())
        await s.commit()
        n = (await s.execute(select(func.count()).select_from(DisclosureRelation))).scalar_one()
    check("연결 멱등: 관계 1행 유지", n == 1)

    # 원본 없는 정정본 → None (확정 표현 금지 경로)
    async with AsyncSessionLocal() as s:
        s.add(Disclosure(id="d-orphan", rcept_no="20260721000900", corp_code="C9", corp_name="X사",
                         report_nm="[기재정정]감자결정", rcept_dt="20260721"))
        await s.commit()
        r = await s.execute(select(Disclosure).where(Disclosure.id == "d-orphan"))
        out2 = await correction_service.link_correction(s, r.scalar_one())
    check("원본 미발견 시 None", out2 is None)

    await engine.dispose()


async def main():
    part1_units()
    await part2_linking()
    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== SUMMARY: {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


asyncio.run(main())
