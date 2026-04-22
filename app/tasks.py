import uuid
from sqlalchemy import select
from database import AsyncSessionLocal
from models import SeenDisclosure, User, Watchlist
from dart import fetch_recent_disclosures, is_important, fetch_disclosure_detail
from summarizer import summarize_disclosure
from notifier import send_alert

async def process_disclosures():
    """DART 공시 폴링 → 필터링 → 요약 → 알림 발송"""
    print("공시 폴링 시작...")

    disclosures = await fetch_recent_disclosures()
    if not disclosures:
        print("새로운 공시 없음")
        return

    async with AsyncSessionLocal() as session:
        for disclosure in disclosures:
            receipt_no = disclosure.get("rcept_no")
            corp_name = disclosure.get("corp_name", "")
            report_nm = disclosure.get("report_nm", "")
            corp_code = disclosure.get("corp_code", "")

            # 1. 중요 공시 유형 필터링
            if not is_important(report_nm):
                continue

            # 2. 해당 기업 watchlist 유저 조회
            result = await session.execute(
                select(User).join(Watchlist).where(
                    Watchlist.corp_code == corp_code,
                    User.is_active == True,
                )
            )
            target_users = result.scalars().all()

            if not target_users:
                continue

            # 3. 공시 원문 조회 + Claude 요약 (공시당 1번만)
            content = await fetch_disclosure_detail(receipt_no)
            summary = await summarize_disclosure(corp_name, report_nm, content)

            # 4. 유저별 처리
            for user in target_users:
                # 유저별 중복 확인
                result = await session.execute(
                    select(SeenDisclosure).where(
                        SeenDisclosure.receipt_no == receipt_no,
                        SeenDisclosure.chat_id == user.chat_id,
                    )
                )
                if result.scalar_one_or_none():
                    continue

                # 알림 발송
                await send_alert(
                    chat_id=user.chat_id,
                    corp_name=corp_name,
                    report_nm=report_nm,
                    receipt_no=receipt_no,
                    summary=summary,
                )

                # seen 저장
                seen = SeenDisclosure(
                    id=str(uuid.uuid4()),
                    receipt_no=receipt_no,
                    chat_id=user.chat_id,
                    corp_name=corp_name,
                    report_nm=report_nm,
                    summary=summary,
                )
                session.add(seen)

        await session.commit()

    print("공시 폴링 완료")
