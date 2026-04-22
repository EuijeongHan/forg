import anthropic
from config import ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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
        return response.choices[0].message.content
    except Exception as e:
        print("OpenAI 요약 실패:", e)
        return None

async def summarize_with_claude(prompt):
    try:
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as e:
        print("Claude 요약 실패:", e)
        return None

async def summarize_with_gemini(prompt):
    try:
        import google.generativeai as genai
        genai.configure(api_key=__import__("os").getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(SYSTEM_PROMPT + chr(10) + prompt)
        return response.text
    except Exception as e:
        print("Gemini 요약 실패:", e)
        return None

async def summarize_disclosure(corp_name: str, report_nm: str, content: str) -> str:
    prompt = build_prompt(corp_name, report_nm, content)

    result = await summarize_with_openai(prompt)
    if result:
        print("OpenAI 요약 성공")
        return result

    result = await summarize_with_claude(prompt)
    if result:
        print("Claude 요약 성공")
        return result

    result = await summarize_with_gemini(prompt)
    if result:
        print("Gemini 요약 성공")
        return result

    return "요약 생성에 실패했습니다. DART에서 직접 확인해주세요."

def format_typed_disclosure(corp_name: str, report_nm: str, data: dict) -> str:
    """정형 데이터를 카드 뷰 형식으로 포맷팅"""
    from datetime import datetime, date
    today = date.today()

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
            # D-Day 계산
            try:
                d = datetime.strptime(bgd.replace('년 ', '-').replace('월 ', '-').replace('일', '').strip(), '%Y-%m-%d').date()
                diff = (d - today).days
                lines.append(f"• ⏰ 전환청구까지 D+{diff}일")
            except:
                pass
        if data.get('fdpp_op') and data['fdpp_op'] != '-': lines.append(f"• 자금목적(운영): {data['fdpp_op']}원")
        if data.get('fdpp_dtrp') and data['fdpp_dtrp'] != '-': lines.append(f"• 자금목적(채무상환): {data['fdpp_dtrp']}원")
        if data.get('act_mktprcfl_cvprc_lwtrsprc_bs'): lines.append(f"• 리픽싱: {data['act_mktprcfl_cvprc_lwtrsprc_bs']}")

    elif '유상증자' in report_nm:
        lines.append("[유상증자 결정]")
        if data.get('iscls'): lines.append(f"• 증자방식: {data['iscls']}")
        if data.get('nstk_ostk_cnt'): lines.append(f"• 신주 발행수: {data['nstk_ostk_cnt']}주")
        if data.get('nstk_ispr'): lines.append(f"• 발행가액: {data['nstk_ispr']}원")
        if data.get('fdpp_op') and data['fdpp_op'] != '-': lines.append(f"• 자금목적(운영): {data['fdpp_op']}원")
        if data.get('allot_mthn'): lines.append(f"• 배정방법: {data['allot_mthn']}")
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
        if data.get('mrgcmp_nm'): lines.append(f"• 합병대상: {data['mrgcmp_nm']}")
        if data.get('mg_rt'): lines.append(f"• 합병비율: {data['mg_rt']}")
        if data.get('mgdt'): lines.append(f"• 합병기일: {data['mgdt']}")
        if data.get('mgr_nstk_ismt_atn'): lines.append(f"• 신주발행: {data['mgr_nstk_ismt_atn']}")

    elif '자기주식' in report_nm:
        if '취득' in report_nm:
            lines.append("[자기주식 취득 결정]")
        else:
            lines.append("[자기주식 처분 결정]")
        if data.get('aqpln_prc_ostk'): lines.append(f"• 보통주 취득금액: {data['aqpln_prc_ostk']}원")
        if data.get('aqpln_stk_ostk'): lines.append(f"• 보통주 취득수량: {data['aqpln_stk_ostk']}주")
        if data.get('aq_pp'): lines.append(f"• 취득목적: {data['aq_pp']}")
        if data.get('aq_mth'): lines.append(f"• 취득방법: {data['aq_mth']}")
        if data.get('aqpln_bgd'): lines.append(f"• 취득기간: {data['aqpln_bgd']} ~ {data.get('aqpln_edd', '')}")

    else:
        # 기타 유형은 데이터 그대로
        for k, v in data.items():
            if v and v != '-' and k not in ['rcept_no', 'corp_cls', 'corp_code', 'corp_name']:
                lines.append(f"• {k}: {v}")

    return chr(10).join(lines)


async def summarize_typed_disclosure(corp_name: str, report_nm: str, data: dict) -> str:
    """정형 데이터 기반 요약 - 카드 뷰 우선, AI 보완"""
    card = format_typed_disclosure(corp_name, report_nm, data)
    
    if not card:
        return "요약 생성에 실패했습니다. DART에서 직접 확인해주세요."
    
    # AI로 추가 인사이트 보완
    prompt = chr(10).join([
        f"기업명: {corp_name}",
        f"공시 유형: {report_nm}",
        f"공시 핵심 데이터:",
        card,
        "",
        "위 데이터를 바탕으로 투자자가 주목해야 할 핵심 포인트 1-2줄만 추가해주세요. 숫자 중심으로.",
    ])
    
    ai_comment = await summarize_with_openai(prompt)
    
    if ai_comment:
        return card + chr(10) + chr(10) + "💡 " + ai_comment.strip()
    return card
