from datetime import datetime
from zoneinfo import ZoneInfo

import config

_KST = ZoneInfo("Asia/Seoul")

# LLM 일일 비용 가드 — 단일 프로세스 전제(§6-4). KST 날짜가 바뀌면 리셋.
_llm_calls_today = 0
_llm_count_date = None
_budget_op_alerted = False


def _budget_allows() -> bool:
    """일일 LLM 호출 예산 확인. 허용 시 카운트 후 True, 초과 시 False."""
    global _llm_calls_today, _llm_count_date, _budget_op_alerted
    today = datetime.now(_KST).strftime("%Y%m%d")
    if today != _llm_count_date:
        _llm_count_date = today
        _llm_calls_today = 0
        _budget_op_alerted = False
    if _llm_calls_today >= config.LLM_DAILY_CALL_LIMIT:
        return False
    _llm_calls_today += 1
    return True


# ── 프로바이더 가용성 감시 (2026-08-18 프로덕션 3중 폴백 전멸 재발 방지) ──
# OpenAI는 일반 API 키로 잔액 조회 API를 제공하지 않는다. 따라서 잔액·키 상태의
# 유일한 검증은 실호출이다: 실패를 분류해 잔액/인증류는 운영자에게 1일 1회 경보하고,
# 마지막 상태를 provider_status로 남겨 /health에 노출한다. 폴백 체인이 실패를
# 조용히 가리는 것("조용한 강등")이 이번 사고의 본질이었다.
provider_status: dict = {}          # {"openai": {"ok": bool, "error": str|None, "at": iso}}
_provider_alert_date: dict = {}     # provider -> KST 날짜 (1일 1회 경보)

# 분류 주의(2026-08-19 교훈): Gemini 429 본문("exceeded your current quota,
# please check your plan and billing details")에는 'quota'와 'billing'이 둘 다
# 들어 있다 — 이 단어들을 billing 마커로 쓰면 할당량 초과가 "잔액 소진"으로
# 오표기된다. Gemini Developer API는 선불 잔액이 아니라 프로젝트 티어별
# 할당량(RPM/RPD/TPM/TPD) 방식이라 429와 충전 여부는 직접 연결되지 않는다.
_BILLING_MARKERS = ("insufficient_quota", "credit")
_AUTH_MARKERS = ("401", "unauthorized", "invalid_api_key", "authentication",
                 "api key", "rejected by the server")
_QUOTA_MARKERS = ("quota", "rate limit", "rate_limit", "resource_exhausted", "429")


def _classify_llm_error(err: str) -> str:
    """billing/auth/quota는 사람이 확인할 오류(경보 대상), 나머지는 일시 오류(폴백이 처리).

    billing을 quota보다 먼저 본다 — OpenAI의 잔액 소진(insufficient_quota)도
    429로 오기 때문에, 전용 마커(insufficient_quota·credit)로 먼저 걸러낸다.
    """
    low = err.lower()
    if any(m in low for m in _BILLING_MARKERS):
        return "billing"
    if any(m in low for m in _AUTH_MARKERS):
        return "auth"
    if any(m in low for m in _QUOTA_MARKERS):
        return "quota"
    return "other"


def _record_provider(name: str, ok: bool, error: str | None = None):
    provider_status[name] = {
        "ok": ok,
        "error": (error or "")[:300] or None,
        "at": datetime.now(_KST).isoformat(timespec="seconds"),
    }


def _muted_providers() -> set[str]:
    """경보 음소거 대상 (환경변수 LLM_ALERT_MUTE, 쉼표 구분. 예: claude).

    잔액을 채우지 않기로 결정한 최후순위 프로바이더가 매일 경보를 울리면
    경보 자체가 소음이 되어 진짜 경보를 묻는다. 음소거해도 로그·/health·
    '전원 실패' 경보(tasks.run_llm_canary)에는 그대로 잡힌다.
    """
    import os
    raw = os.getenv("LLM_ALERT_MUTE", "")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


async def _alert_provider_issue(name: str, err: str):
    """잔액/인증류 실패를 운영자에게 경보 — 폴백 뒤에 숨은 강등을 드러낸다."""
    kind = _classify_llm_error(err)
    if kind == "other":
        return
    if name.lower() in _muted_providers():
        print(f"프로바이더 경보 음소거됨: {name} ({kind})")
        return
    today = datetime.now(_KST).strftime("%Y%m%d")
    if _provider_alert_date.get(name) == today:
        return
    _provider_alert_date[name] = today
    label = {"billing": "잔액 소진", "auth": "인증 실패",
             "quota": "할당량 초과(429)"}[kind]
    hint = {
        "billing": "키/잔액을 확인해주세요 (Railway Variables 동반 갱신).",
        "auth": "키/잔액을 확인해주세요 (Railway Variables 동반 갱신).",
        # 429는 잔액이 아니라 프로젝트 티어별 할당량(RPM/RPD/TPM/TPD) 문제다.
        "quota": "키 소속 프로젝트의 할당량·결제 연결을 확인해주세요 (RPM/RPD — ai.dev/usage).",
    }[kind]
    print(f"프로바이더 경보: {name} {label}")
    if config.TELEGRAM_CHAT_ID:
        from notifier import send_system_message
        await send_system_message(
            config.TELEGRAM_CHAT_ID,
            f"⚠️ forG: {name} {label} — 폴백으로 대체 운영 중입니다.\n"
            f"{hint}\n{err[:300]}",
        )


async def check_llm_providers() -> dict:
    """3사 프로바이더에 초소형 실호출 캐너리 — 공시가 없는 날에도 잔액·키 문제를 감지.

    배포 직후 1회 + 매일 아침 스케줄 실행(main.py). 비용은 회당 1원 미만.
    """
    for name, fn in (("openai", summarize_with_openai),
                     ("gemini", summarize_with_gemini),
                     ("claude", summarize_with_claude)):
        out = await fn("상태 점검입니다. 'ok' 한 단어로만 답하세요.")
        if not out:
            # 실패 상세는 각 summarize_with_*의 except가 provider_status에 기록함
            provider_status.setdefault(name, {"ok": False, "error": "빈 응답", "at": None})
    return dict(provider_status)


async def _notify_budget_once():
    """한도 도달을 운영자에게 1회 경보 (다음 날 리셋)."""
    global _budget_op_alerted
    if _budget_op_alerted:
        return
    _budget_op_alerted = True
    print(f"LLM 일일 한도({config.LLM_DAILY_CALL_LIMIT}) 도달 — 요약 생략 폴백")
    if config.TELEGRAM_CHAT_ID:
        from notifier import send_system_message
        await send_system_message(
            config.TELEGRAM_CHAT_ID,
            f"⚠️ forG: LLM 일일 호출 한도({config.LLM_DAILY_CALL_LIMIT}) 도달 — "
            "이후 알림은 요약 없이 카드/제목만 발송됩니다. (자정 KST 리셋)",
        )

SYSTEM_PROMPT = """당신은 20년 경력의 기관 애널리스트입니다.
공시를 읽을 때 표면적인 내용이 아니라 숨겨진 의도와 리스크를 파악합니다.
반드시 아래 규칙을 따르세요:

1. 숫자와 날짜는 원문 그대로 표기합니다
2. 투자 의견은 절대 포함하지 않습니다 (법적 책임)
3. 공시 유형에 따라 다음 핵심 정보를 최우선으로 추출합니다:

[유상증자]
- 발행 금액, 발행가액, 발행 주식수
- 배정 방식 (주주배정/3자배정/일반공모)
- 3자배정일 경우 대상자 법인명 명시
- 납입일, 신주 상장 예정일
- 발행가 대비 현재가 할인율

[전환사채/CB]
- 발행 금액, 전환가액, 리픽싱 최저가
- 전환청구 가능일 (오늘부터 D+몇일인지 계산)
- 만기일, 표면이자율, 만기이자율
- 대상자 법인명

[감사보고서]
- 감사 의견 (적정/한정/부적정/의견거절)
- 계속기업 존속 불확실성 여부
- 제출 시간 (정규장 마감 후 야간 제출 여부)
- 전기 대비 감사인 변경 여부
- 강조사항 있으면 명시

[최대주주 변경]
- 변경 전/후 최대주주명
- 취득 방법 (장내/장외/경매)
- 취득 단가, 취득 주식수, 지분율 변화

[자기주식]
- 취득/처분/소각 구분
- 규모 (금액, 주식수, 지분율)
- 취득 목적, 취득 방법, 취득 기간

[단일판매·공급계약체결]
- 계약 상대방(발주처)
- 계약 금액, 최근 매출액 대비 비율
- 계약 기간(시작일·종료일)
- 정정공시면 무엇이 바뀌었는지(금액·기간 변경 등)

[합병/분할]
- 합병 비율
- 합병 기일
- 합병 상대방 법인명 및 관계
- 주요 일정 (주총, 합병기일)

4. 원문이 없으면 공시 제목만으로 위 항목 중 해당하는 것을 설명합니다
5. 마지막에 반드시 추가:
   ⚠️ 본 요약은 AI 참고용입니다. 투자 판단 전 원문을 확인하세요."""

def build_prompt(corp_name, report_nm, content):
    nl = chr(10)
    if content:
        return nl.join(["기업명: " + corp_name, "공시 유형: " + report_nm, "공시 원문:", content[:4000], "", "위 공시의 핵심 정보를 공시 유형에 맞게 추출해주세요."])
    else:
        return nl.join(["기업명: " + corp_name, "공시 유형: " + report_nm, "", "원문 없이 공시 유형과 제목만으로 어떤 공시인지 핵심만 설명해주세요."])

async def summarize_with_openai(prompt):
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=__import__("os").getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=600,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        _record_provider("openai", True)
        return response.choices[0].message.content
    except Exception as e:
        print("OpenAI 요약 실패:", e)
        _record_provider("openai", False, str(e))
        await _alert_provider_issue("openai", str(e))
        return None

async def summarize_with_claude(prompt):
    try:
        import asyncio
        import anthropic  # 지연 임포트 — 다른 provider와 동일 패턴, 테스트 용이성
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        # anthropic 0.34.2 동기 클라이언트 — 이벤트 루프 블로킹 방지를 위해 스레드로 오프로드
        message = await asyncio.to_thread(
            client.messages.create,
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        _record_provider("claude", True)
        return message.content[0].text
    except Exception as e:
        print("Claude 요약 실패:", e)
        _record_provider("claude", False, str(e))
        await _alert_provider_issue("claude", str(e))
        return None

async def summarize_with_gemini(prompt):
    try:
        import asyncio
        import google.generativeai as genai
        genai.configure(api_key=__import__("os").getenv("GEMINI_API_KEY"))
        # gemini-1.5-flash는 퇴역함(2026-08-18 프로덕션 404 실측). 3차 폴백의 역할은
        # 가용성이므로 고정 버전 대신 현행 flash를 추적하는 별칭을 쓴다.
        model = genai.GenerativeModel("gemini-flash-latest")
        # 동기 SDK — 스레드로 오프로드
        response = await asyncio.to_thread(model.generate_content, SYSTEM_PROMPT + chr(10) + prompt)
        _record_provider("gemini", True)
        return response.text
    except Exception as e:
        # 429의 어떤 할당량(quotaMetric: RPM/RPD/TPM/TPD)이 걸렸는지와 retryDelay는
        # str(e)가 아니라 details(QuotaFailure.violations)에 있다 — 버리면 원인
        # 구분이 불가능해 경보 문구까지 틀린다(2026-08-19 교훈).
        detail = f"{type(e).__name__}: {e}"
        try:
            extra = getattr(e, "details", None)
            if extra:
                detail += f" | details: {extra}"
        except Exception:
            pass
        detail = detail[:500]
        print("Gemini 요약 실패:", detail)
        _record_provider("gemini", False, detail)
        await _alert_provider_issue("gemini", detail)
        return None

async def _generate_with_fallback(prompt) -> str | None:
    """폴백 체인: OpenAI → Gemini → Claude. 첫 성공 결과를 반환한다.

    Claude가 최후순위인 이유: 단가가 가장 높아($3/$15) 상시 폴백이 아니라
    나머지 둘이 모두 죽었을 때의 최후 보루로 쓴다(2026-08-18 결정).
    """
    for label, fn in (("OpenAI", summarize_with_openai),
                      ("Gemini", summarize_with_gemini),
                      ("Claude", summarize_with_claude)):
        result = await fn(prompt)
        if result:
            print(f"{label} 요약 성공")
            return result
    return None


async def summarize_disclosure(
    corp_name: str, report_nm: str, content: str, bypass_budget: bool = False
) -> str:
    # bypass_budget: 긴급 등급(상장폐지·회생 등) 전용. 중대 사건이 하필 한도
    # 소진 시점에 터졌을 때 요약이 빠지는 상황을 막는다. 호출은 카운트한다.
    if not _budget_allows() and not bypass_budget:
        await _notify_budget_once()
        return "오늘 자동 요약 한도에 도달했습니다. DART 원문을 확인해주세요."

    prompt = build_prompt(corp_name, report_nm, content)

    result = await _generate_with_fallback(prompt)
    if result:
        return result

    return "요약 생성에 실패했습니다. DART에서 직접 확인해주세요."

def _one_line(value) -> str:
    """DART 원문 값의 줄바꿈·연속 공백을 한 줄로 정리한다(카드 가독성)."""
    return " ".join(str(value).split())


# 여러 정형 API가 공유하는 공통 필드. 뜻은 2026-08-21 카카오 회사분할결정
# 실응답의 값으로 확인했다(§4.2). 여기 없는 키는 카드에 절대 노출하지 않는다.
_COMMON_FIELD_LABELS = {
    'bddd': '이사회결의일',
    'gmtsck_prd': '주주총회 예정일',
    'ffdtl_tast': '자산총계',
    'ffdtl_tdbt': '부채총계',
    'ffdtl_teqt': '자본총계',
    'ffdtl_cpt': '자본금',
    'ffdtl_std': '재무 기준일',
    'popt_ctr_atn': '풋옵션 등 계약 체결 여부',
}


def format_typed_disclosure(corp_name: str, report_nm: str, data: dict, today=None) -> str:
    """정형 데이터를 카드 뷰 형식으로 포맷팅.

    필드명은 실제 DART 응답으로 검증한다(§10 — 추측 금지). 2026-08-18 평가에서
    합병(mgptncmp_cmpnm/mgsc_mgdt)·유상증자(ic_mthn)·자기주식 처분(dppln_*)이
    잘못된/누락된 키로 조회돼 카드가 비었고, LLM이 그 공백을 '미제공'이라고
    서술하는 결함이 확인됐다. today는 테스트에서 고정할 수 있게 주입 가능.
    """
    from datetime import datetime
    from dart import KST
    today = today or datetime.now(KST).date()  # D-day는 KST 기준

    lines = []

    if '전환사채' in report_nm or '교환사채' in report_nm:
        lines.append("[전환사채 발행결정]")
        if data.get('bd_fta'): lines.append(f"• 발행금액: {data['bd_fta']}원")
        if data.get('bd_knd'): lines.append(f"• 종류: {data['bd_knd']}")
        if data.get('bd_intr_ex'): lines.append(f"• 표면이자율: {data['bd_intr_ex']}%")
        if data.get('bd_intr_sf'): lines.append(f"• 만기이자율: {data['bd_intr_sf']}%")
        if data.get('bd_mtd'): lines.append(f"• 만기일: {data['bd_mtd']}")
        if data.get('cv_prc'): lines.append(f"• 전환가액: {data['cv_prc']}원")
        if data.get('cvrqpd_bgd'):
            bgd = data['cvrqpd_bgd']
            lines.append(f"• 전환청구 가능일: {bgd}")
            # D-day는 코드가 결정론적으로 계산한다(LLM 파생 금지).
            # 미래는 D-n(남은 일수), 당일은 D-Day, 지난 날짜는 카운트다운 생략.
            try:
                d = datetime.strptime(bgd.replace('년 ', '-').replace('월 ', '-').replace('일', '').strip(), '%Y-%m-%d').date()
                diff = (d - today).days
                if diff > 0:
                    lines.append(f"• ⏰ 전환청구 개시까지 D-{diff}")
                elif diff == 0:
                    lines.append("• ⏰ 전환청구 개시일 (D-Day)")
            except Exception:
                pass
        if data.get('fdpp_op') and data['fdpp_op'] != '-': lines.append(f"• 자금목적(운영): {data['fdpp_op']}원")
        if data.get('fdpp_dtrp') and data['fdpp_dtrp'] != '-': lines.append(f"• 자금목적(채무상환): {data['fdpp_dtrp']}원")
        if data.get('act_mktprcfl_cvprc_lwtrsprc_bs'): lines.append(f"• 리픽싱: {data['act_mktprcfl_cvprc_lwtrsprc_bs']}")

    elif '유상증자' in report_nm:
        lines.append("[유상증자 결정]")
        # 증자방식 실제 키는 ic_mthn (구 iscls는 폴백으로 유지)
        mthn = data.get('ic_mthn') or data.get('iscls')
        if mthn: lines.append(f"• 증자방식: {mthn}")
        if data.get('nstk_ostk_cnt'): lines.append(f"• 신주 발행수(보통주): {data['nstk_ostk_cnt']}주")
        if data.get('fv_ps'): lines.append(f"• 액면가: {data['fv_ps']}원")
        if data.get('nstk_ispr'): lines.append(f"• 발행가액: {data['nstk_ispr']}원")
        if data.get('bfic_tisstk_ostk'): lines.append(f"• 증자 전 발행주식수: {data['bfic_tisstk_ostk']}주")
        # 자금 목적은 항목별 '배정액'이다 — 전 항목 표기 (총 발행금액으로 단정 금지)
        for key, label in (('fdpp_fclt', '시설'), ('fdpp_bsninh', '영업양수'),
                           ('fdpp_op', '운영'), ('fdpp_dtrp', '채무상환'),
                           ('fdpp_ocsa', '타법인증권취득'), ('fdpp_etc', '기타')):
            if data.get(key) and data[key] != '-':
                lines.append(f"• 자금목적({label}): {data[key]}원")
        if data.get('nstk_sdtpd_bgd'): lines.append(f"• 신주배정기준일: {data['nstk_sdtpd_bgd']}")
        if data.get('pymd'): lines.append(f"• 납입일: {data['pymd']}")

    elif '감자' in report_nm:
        lines.append("[감자 결정]")
        if data.get('cr_rt'): lines.append(f"• 감자비율: {data['cr_rt']}%")
        if data.get('cr_mth'): lines.append(f"• 감자방법: {data['cr_mth']}")
        if data.get('cr_rs'): lines.append(f"• 감자사유: {data['cr_rs']}")
        if data.get('cr_dt'): lines.append(f"• 감자기일: {data['cr_dt']}")

    elif '합병' in report_nm:
        lines.append("[합병 결정]")
        # 실제 키: mgptncmp_cmpnm(상대법인)·mgsc_mgdt(합병기일)·rs_sm_atn(주총 소집 여부)
        # — 구 키(mrgcmp_nm/mgdt)만 조회해 카드가 비던 결함 수정 (2026-08-18 평가)
        target = data.get('mgptncmp_cmpnm') or data.get('mrgcmp_nm')
        if target: lines.append(f"• 합병상대: {target}")
        # 관계는 mgptncmp_rl_cmpn이다. r2 평가에서 nmgcmp_rlst_atn(다른 의미의
        # 필드, '해당사항없음')을 관계로 오표기한 버그를 judge가 잡아 수정.
        if data.get('mgptncmp_rl_cmpn') and data['mgptncmp_rl_cmpn'] != '-':
            lines.append(f"• 상대법인과의 관계: {data['mgptncmp_rl_cmpn']}")
        if data.get('mg_rt'):
            lines.append(f"• 합병비율: {' '.join(str(data['mg_rt']).split())}")  # 원문 개행 정리
        mgdt = data.get('mgsc_mgdt') or data.get('mgdt')
        if mgdt: lines.append(f"• 합병기일: {mgdt}")
        # rs_sm_atn을 '주주총회 소집 여부'로 쓰던 것은 오표기였다(2026-08-26 홀드아웃
        # 평가). 같은 셋 28건에서 SK이노베이션은 rs_sm_atn='예'인데 주총이 없고
        # (소규모합병), SK아이이테크놀로지는 '아니오'인데 주총이 있다 — 이 필드는
        # 주총과 무관하다. 뜻을 단정할 수 없으므로 표시하지 않고, 주총은 날짜 필드로
        # 명확히 드러낸다.
        if data.get('mg_stn') and data['mg_stn'] not in ('-', '해당사항없음'):
            lines.append(f"• 합병 형태: {data['mg_stn']}")
        gm = data.get('mgsc_gmtsck_prd')
        if gm and gm != '-':
            lines.append(f"• 주주총회 예정일: {gm}")
        if data.get('mgr_nstk_ismt_atn'): lines.append(f"• 신주발행: {data['mgr_nstk_ismt_atn']}")

    elif '분할' in report_nm:
        lines.append("[분할 결정]")
        # 필드명은 2026-08-21 카카오 '주요사항보고서(회사분할결정)' 실응답으로 검증했다
        # (§4.2 — 추측 금지). 이 유형에 분기가 없어 raw 키(bddd·od_a_at_t…)가 그대로
        # 사용자에게 나가던 것을 실사용자 피드백으로 확인하고 추가한 것이다.
        if data.get('dvfcmp_cmpnm'):
            lines.append(f"• 분할신설회사: {_one_line(data['dvfcmp_cmpnm'])}")
        if data.get('dvfcmp_mbsn') and data['dvfcmp_mbsn'] != '-':
            lines.append(f"• 신설회사 주요사업: {_one_line(data['dvfcmp_mbsn'])}")
        if data.get('dvfcmp_rlst_atn') and data['dvfcmp_rlst_atn'] != '-':
            lines.append(f"• 신설회사 재상장 예정: {data['dvfcmp_rlst_atn']}")
        if data.get('atdv_excmp_cmpnm'):
            lines.append(f"• 분할존속회사: {_one_line(data['atdv_excmp_cmpnm'])}")
        if data.get('atdv_excmp_mbsn') and data['atdv_excmp_mbsn'] != '-':
            lines.append(f"• 존속회사 주요사업: {_one_line(data['atdv_excmp_mbsn'])}")
        if data.get('atdv_excmp_atdv_lstmn_atn') and data['atdv_excmp_atdv_lstmn_atn'] != '-':
            lines.append(f"• 존속회사 상장 유지: {data['atdv_excmp_atdv_lstmn_atn']}")
        # 값은 원문 그대로 둔다 — 단위(%)를 임의로 붙이지 않는다
        if data.get('abcr_crrt') and data['abcr_crrt'] != '-':
            lines.append(f"• 분할비율: {data['abcr_crrt']}")
        if data.get('dvdt') and data['dvdt'] != '-':
            lines.append(f"• 분할기일: {data['dvdt']}")
        if data.get('gmtsck_prd') and data['gmtsck_prd'] != '-':
            lines.append(f"• 주주총회 예정일: {data['gmtsck_prd']}")
        if data.get('abcr_nstkasstd') and data['abcr_nstkasstd'] != '-':
            lines.append(f"• 신주배정 기준일: {data['abcr_nstkasstd']}")
        # 매매거래 정지 예정기간 — 커버리지 담당자에게 가장 실무적인 항목
        bgd = data.get('abcr_trspprpd_bgd')
        edd = data.get('abcr_trspprpd_edd')
        if bgd and bgd != '-':
            lines.append(f"• 매매거래 정지 예정: {bgd} ~ {edd if edd and edd != '-' else '미정'}")
        if data.get('abcr_nstklstprd') and data['abcr_nstklstprd'] != '-':
            lines.append(f"• 신주 상장 예정일: {data['abcr_nstklstprd']}")
        if data.get('dvrgsprd') and data['dvrgsprd'] != '-':
            lines.append(f"• 분할등기 예정일: {data['dvrgsprd']}")
        if data.get('bddd') and data['bddd'] != '-':
            lines.append(f"• 이사회결의일: {data['bddd']}")

    elif '자기주식' in report_nm:
        if '취득' in report_nm:
            lines.append("[자기주식 취득 결정]")
            if data.get('aqpln_prc_ostk'): lines.append(f"• 보통주 취득금액: {data['aqpln_prc_ostk']}원")
            if data.get('aqpln_stk_ostk'): lines.append(f"• 보통주 취득수량: {data['aqpln_stk_ostk']}주")
            if data.get('aq_pp'): lines.append(f"• 취득목적: {data['aq_pp']}")
            if data.get('aq_mth'): lines.append(f"• 취득방법: {data['aq_mth']}")
            if data.get('aqpln_bgd'): lines.append(f"• 취득기간: {data['aqpln_bgd']} ~ {data.get('aqpln_edd', '')}")
        else:
            lines.append("[자기주식 처분 결정]")
            # 처분(dppln_*) 필드가 통째로 빠져 카드가 비던 결함 수정 (2026-08-18 평가,
            # 씨젠 사례 — 수치가 있는데 요약이 '미제공'이라고 서술)
            if data.get('dppln_stk_ostk'): lines.append(f"• 처분예정 주식수(보통주): {data['dppln_stk_ostk']}주")
            if data.get('dpstk_prc_ostk'): lines.append(f"• 처분단가(보통주): {data['dpstk_prc_ostk']}원")
            if data.get('dppln_prc_ostk'): lines.append(f"• 처분예정금액(보통주): {data['dppln_prc_ostk']}원")
            if data.get('dp_pp'): lines.append(f"• 처분목적: {data['dp_pp']}")
            if data.get('dpprpd_bgd'): lines.append(f"• 처분기간: {data['dpprpd_bgd']} ~ {data.get('dpprpd_edd', '')}")

    else:
        # 분기가 없는 유형. 예전에는 data를 그대로 찍어 raw 필드명(bddd·od_a_at_t·
        # rs_sm_atn…)이 사용자에게 나갔다 — 실사용자가 "요약이 잘못됨"으로 신고한
        # 결함이다. 이제 뜻이 검증된 공통 필드만 한글 라벨로 보여주고, 모르는 키는
        # 아예 표시하지 않는다. 값을 못 보여주는 것이 뜻 모를 코드를 보여주는 것보다 낫다.
        shown = 0
        for key, label in _COMMON_FIELD_LABELS.items():
            v = data.get(key)
            if v and str(v) != '-':
                lines.append(f"• {label}: {_one_line(v)}")
                shown += 1
        if not shown:
            lines.append("• 정형 항목을 표시할 수 없는 유형입니다 — 원문을 확인해주세요.")

    return chr(10).join(lines)


async def summarize_typed_disclosure(
    corp_name: str, report_nm: str, data: dict, bypass_budget: bool = False
) -> str:
    """정형 데이터 기반 요약 - 카드 뷰 우선, AI 보완"""
    card = format_typed_disclosure(corp_name, report_nm, data)

    if not card:
        return "요약 생성에 실패했습니다. DART에서 직접 확인해주세요."

    # 예산 초과 시 카드(정형 수치)만 발송 — 숫자는 API 필드라 LLM 불필요
    # bypass_budget은 긴급 등급 전용(summarize_disclosure와 동일 이유)
    if not _budget_allows() and not bypass_budget:
        await _notify_budget_once()
        return card

    # AI로 추가 인사이트 보완
    # 규칙은 2026-08-18 평가에서 judge가 잡은 실제 위반 유형을 하나씩 막는다:
    # 새 수치 파생(주당단가·일수 계산), 배정액을 총액으로 단정, '미제공' 서술,
    # 데이터에 없는 사실(주총 결과·지분율 변화) 단정.
    prompt = chr(10).join([
        f"기업명: {corp_name}",
        f"공시 유형: {report_nm}",
        f"공시 핵심 데이터:",
        card,
        "",
        "위 데이터에서 투자자가 주목할 핵심 포인트 1-2줄만 추가하세요. 규칙:",
        "1. 카드에 적힌 수치·날짜만 언급한다. 새 수치를 계산하지 않는다 (주당 단가, 남은 일수, 합계, 비율 환산 금지).",
        "2. 자금목적 배정액을 '총 발행금액'이나 '조달 총액'으로 단정하지 않는다.",
        "3. 카드에 없는 항목은 언급 자체를 하지 않는다 ('미제공', '정보 없음' 같은 서술 금지).",
        "4. 카드에 없는 사실(주주총회 결과, 지분율 변화 등)을 단정하지 않는다.",
        "5. 매수/매도/호재/악재 등 투자 판단 표현을 쓰지 않는다.",
    ])
    
    # 카드 수치는 이미 확보됐으므로 코멘트 생성은 실패해도 무방하지만,
    # 알림 경로와 동일한 폴백 체인을 태워 주력 프로바이더 장애 시에도 유지한다.
    ai_comment = await _generate_with_fallback(prompt)

    if not ai_comment:
        return card

    # 카드 수치는 정형 API에서 왔으니 늘 옳다. 위험한 건 그것을 '다시 말하는'
    # 코멘트다 — 홀드아웃 평가에서 300억(30,000,000,000)을 30조로 옮겨 적은
    # 1000배 오기가 나왔다. 정형 응답에 근거가 없는 대형 금액이 코멘트에 있으면
    # 코멘트를 버린다. 카드만 보내는 편이 틀린 숫자를 덧붙이는 것보다 낫다.
    from verification.checks import cross_check_amounts
    ungrounded = cross_check_amounts(ai_comment, data)["unverified_large"]
    if ungrounded:
        print(f"💡 코멘트 폐기 — 정형 근거 없는 금액: {[str(x) for x in ungrounded[:3]]}")
        return card
    return card + chr(10) + chr(10) + "💡 " + ai_comment.strip()
