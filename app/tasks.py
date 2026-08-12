import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
import config
from config import TELEGRAM_CHAT_ID
from database import AsyncSessionLocal
from models import SeenDisclosure, User, Watchlist
from dart import fetch_recent_disclosures, is_important, fetch_disclosure_detail, fetch_typed_disclosure, fetch_rcept_times, is_after_hours
from summarizer import summarize_disclosure, summarize_typed_disclosure
from notifier import send_alert, send_system_message

_KST = ZoneInfo("Asia/Seoul")

# 자가 경보 상태 — 단일 프로세스 전제(CLAUDE.md §6-4의 disclosure_cache와 동일 제약).
# 침묵 사망 방지: 폴링이 조용히 실패/빈손 반복되면 운영자 채팅으로 1회 경보를 보낸다.
FAIL_ALERT_THRESHOLD = 5     # 연속 실패 N회에 경보
EMPTY_ALERT_THRESHOLD = 10   # 평일 장중 공시 0건 N사이클 지속에 경보
_fail_streak = 0
_fail_alerted = False
_empty_streak = 0
_empty_alerted = False

# 마지막 폴링 결과 — /health로 노출해 "조용한 정상"과 "조용한 고장"을 구분한다.
# empty(공시가 없음)와 error(수집 실패)가 로그상 똑같아 보이면 침묵 사망을 놓친다.
#   success: 대상 공시를 수집·처리 완료 / empty: 수집됐으나 신규 공시 0건
#   partial: 처리 중 일부 공시가 실패 / error: 수집 자체가 실패
poll_status: dict = {
    "last_result": None,          # success | empty | partial | error
    "last_run_at": None,          # 폴링 시도 시각(성공·실패 무관)
    "last_success_at": None,      # 마지막으로 수집에 성공한 시각
    "last_fetch_count": 0,        # 마지막 수집 건수(필터 전)
    "last_alert_count": 0,        # 마지막 사이클에서 발송한 알림 수
    "last_error": None,
    "fail_streak": 0,
    "empty_streak": 0,
}


def _now_kst_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


def _is_business_hours_kst(now: datetime | None = None) -> bool:
    """평일 08~19시 KST — 이 시간대에 공시 0건이 지속되면 연동 이상 신호다."""
    now = now or datetime.now(_KST)
    return now.weekday() < 5 and 8 <= now.hour < 19


async def _notify_operator(text: str):
    if TELEGRAM_CHAT_ID:
        await send_system_message(TELEGRAM_CHAT_ID, text)


async def process_disclosures():
    """폴링 엔트리포인트 — 예외를 삼켜 스케줄을 지키되, 연속 실패는 운영자에게 경보"""
    global _fail_streak, _fail_alerted
    poll_status["last_run_at"] = _now_kst_iso()
    try:
        await _run_pipeline()
    except Exception as e:
        _fail_streak += 1
        poll_status["last_result"] = "error"
        poll_status["last_error"] = f"{type(e).__name__}: {e}"
        poll_status["fail_streak"] = _fail_streak
        print(f"공시 폴링 실패 (연속 {_fail_streak}회): {type(e).__name__}: {e}")
        if _fail_streak >= FAIL_ALERT_THRESHOLD and not _fail_alerted:
            _fail_alerted = True
            await _notify_operator(
                f"⚠️ forG 자가 경보: 공시 폴링 연속 {_fail_streak}회 실패\n"
                f"{type(e).__name__}: {e}\n로그 확인이 필요합니다."
            )
        return
    _fail_streak = 0
    _fail_alerted = False
    poll_status["fail_streak"] = 0
    poll_status["last_error"] = None
    poll_status["last_success_at"] = _now_kst_iso()


async def _store_typed_snapshot(session, receipt_no: str, typed_data: dict):
    """정형 응답 원본을 Disclosure에 1회 저장 (P1-2).

    매일 흘러가는 정형 수치를 보존해 정정 전후 비교(Stage 3)·검증 레이어(Stage 7)·
    golden set의 재료로 쓴다. 이미 저장된 행은 덮어쓰지 않는다(원본 보존).
    """
    from models import Disclosure
    result = await session.execute(
        select(Disclosure).where(Disclosure.rcept_no == receipt_no)
    )
    row = result.scalar_one_or_none()
    if row is not None and row.raw_typed_data is None:
        row.raw_typed_data = typed_data
    return row


async def _run_pipeline():
    """DART 공시 폴링 → DB 저장 → 필터링 → 요약 → 알림 발송"""
    from dart import save_disclosures_to_db, today_kst
    global _empty_streak, _empty_alerted
    print("공시 폴링 시작...")

    # 자정 경계 누락 방지: 어제~오늘 2일 창으로 조회.
    # 중복은 save_disclosures_to_db(rcept_no unique)와 SeenDisclosure가 막는다.
    disclosures = await fetch_recent_disclosures(days=2)
    poll_status["last_fetch_count"] = len(disclosures or [])
    if not disclosures:
        if _is_business_hours_kst():
            _empty_streak += 1
            if _empty_streak >= EMPTY_ALERT_THRESHOLD and not _empty_alerted:
                _empty_alerted = True
                await _notify_operator(
                    f"⚠️ forG 자가 경보: 평일 장중 공시 0건이 {_empty_streak}사이클 지속 — "
                    "DART 연동(키·네트워크·응답 형식) 점검이 필요합니다."
                )
        poll_status["last_result"] = "empty"
        poll_status["empty_streak"] = _empty_streak
        poll_status["last_alert_count"] = 0
        print("새로운 공시 없음")
        return
    _empty_streak = 0
    _empty_alerted = False
    poll_status["empty_streak"] = 0

    await save_disclosures_to_db(disclosures)

    # 접수 시각은 오늘자만 조회 — 전일분 야간경고 뱃지는 놓칠 수 있으나 알림 자체는 발송됨
    rcept_times = await fetch_rcept_times(today_kst())

    alert_count = 0
    failed_receipts: list[str] = []

    async with AsyncSessionLocal() as session:
        for disclosure in disclosures:
            receipt_no = disclosure.get("rcept_no")
            corp_name = disclosure.get("corp_name", "")
            report_nm = disclosure.get("report_nm", "")
            corp_code = disclosure.get("corp_code", "")
            rcept_dt = disclosure.get("rcept_dt", "")

            if not is_important(report_nm):
                continue

            # 공시 한 건의 실패가 사이클 전체를 중단시키지 않게 격리한다.
            # 격리가 없으면 특정 공시(응답 형식 이상 등) 하나 때문에 뒤에 오는
            # 모든 공시가 영구히 발송되지 않는다 — 누락 0을 목표로 하는 서비스에서
            # 가장 조용하고 위험한 실패다.
            try:
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
                    disclosure_row = await _store_typed_snapshot(session, receipt_no, typed_data)
                    if config.ENABLE_EVENT_CARDS and disclosure_row is not None:
                        from services import event_service
                        await event_service.record_typed_event(session, disclosure_row, report_nm)
                    summary = await summarize_typed_disclosure(corp_name, report_nm, typed_data)
                else:
                    content = await fetch_disclosure_detail(receipt_no)
                    summary = await summarize_disclosure(corp_name, report_nm, content)

                summary = summary + time_warning

                for user in unseen_users:
                    sent = await send_alert(
                        chat_id=user.chat_id,
                        corp_name=corp_name,
                        report_nm=report_nm,
                        receipt_no=receipt_no,
                        summary=summary,
                    )
                    if not sent:
                        # 발송 실패 시 기록하지 않는다 → 다음 폴링에서 재시도
                        continue

                    seen = SeenDisclosure(
                        id=str(uuid.uuid4()),
                        receipt_no=receipt_no,
                        chat_id=user.chat_id,
                        corp_name=corp_name,
                        report_nm=report_nm,
                        summary=summary,
                    )
                    session.add(seen)
                    alert_count += 1

                # 공시 단위 커밋: 중간 크래시 시에도 이미 발송된 기록이 보존되어
                # 다음 폴링에서 중복 발송되지 않는다
                await session.commit()
            except Exception as e:
                # 이 공시만 건너뛴다. 세션을 되돌려 다음 공시가 오염된 트랜잭션을
                # 물려받지 않게 한다. 다음 폴링에서 자연히 재시도된다.
                await session.rollback()
                failed_receipts.append(receipt_no or "?")
                print(f"공시 처리 실패 (rcept_no={receipt_no}): {type(e).__name__}: {e}")

    poll_status["last_alert_count"] = alert_count
    if failed_receipts:
        poll_status["last_result"] = "partial"
        poll_status["last_error"] = f"{len(failed_receipts)}건 처리 실패: {failed_receipts[:5]}"
        print(f"공시 폴링 완료 (일부 실패 {len(failed_receipts)}건, 발송 {alert_count}건)")
    else:
        poll_status["last_result"] = "success"
        print(f"공시 폴링 완료 (발송 {alert_count}건)")
