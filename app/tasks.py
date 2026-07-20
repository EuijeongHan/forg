import uuid
from sqlalchemy import select
from database import AsyncSessionLocal
from models import SeenDisclosure, User, Watchlist
from dart import fetch_recent_disclosures, is_important, fetch_disclosure_detail, fetch_typed_disclosure, fetch_rcept_times, is_after_hours
from summarizer import summarize_disclosure, summarize_typed_disclosure
from notifier import send_alert

async def process_disclosures():
    """DART 공시 폴링 → DB 저장 → 필터링 → 요약 → 알림 발송"""
    from dart import save_disclosures_to_db
    from datetime import datetime
    print("공시 폴링 시작...")

    disclosures = await fetch_recent_disclosures()
    if not disclosures:
        print("새로운 공시 없음")
        return

    await save_disclosures_to_db(disclosures)

    today = datetime.now().strftime("%Y%m%d")
    rcept_times = await fetch_rcept_times(today)

    async with AsyncSessionLocal() as session:
        for disclosure in disclosures:
            receipt_no = disclosure.get("rcept_no")
            corp_name = disclosure.get("corp_name", "")
            report_nm = disclosure.get("report_nm", "")
            corp_code = disclosure.get("corp_code", "")
            rcept_dt = disclosure.get("rcept_dt", "")

            if not is_important(report_nm):
                continue

            result = await session.execute(
                select(User).join(Watchlist).where(
                    Watchlist.corp_code == corp_code,
                    User.is_active == True,
                )
            )
            target_users = result.scalars().all()

            if not target_users:
                continue

            # 미발송 사용자를 먼저 확정한다. 전원 발송 완료된 공시는 요약 생성
            # (정형 API + LLM 호출) 자체를 건너뛴다 — 이 체크가 요약 뒤에 있으면
            # 이미 알림이 끝난 공시도 매 폴링(60초)마다 LLM을 재호출하게 된다.
            result = await session.execute(
                select(SeenDisclosure.chat_id).where(
                    SeenDisclosure.receipt_no == receipt_no,
                    SeenDisclosure.chat_id.in_([u.chat_id for u in target_users]),
                )
            )
            seen_chat_ids = set(result.scalars().all())
            unseen_users = [u for u in target_users if u.chat_id not in seen_chat_ids]
            if not unseen_users:
                continue

            # 제출 시간 확인
            rcept_time = rcept_times.get(receipt_no, "")
            after_hours = is_after_hours(rcept_time) if rcept_time else False

            # 감사보고서 야간 제출 경고
            is_audit = "감사보고서" in report_nm
            time_warning = ""
            if is_audit and after_hours:
                time_warning = f"\n\n⚠️ 야간 제출 감지 ({rcept_time}) - 주의 필요"

            # 정형 데이터 우선, 없으면 원문 크롤링
            typed_data = await fetch_typed_disclosure(corp_code, receipt_no, report_nm, rcept_dt)
            if typed_data:
                summary = await summarize_typed_disclosure(corp_name, report_nm, typed_data)
            else:
                content = await fetch_disclosure_detail(receipt_no)
                summary = await summarize_disclosure(corp_name, report_nm, content)

            summary = summary + time_warning

            for user in unseen_users:
                await send_alert(
                    chat_id=user.chat_id,
                    corp_name=corp_name,
                    report_nm=report_nm,
                    receipt_no=receipt_no,
                    summary=summary,
                )

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
