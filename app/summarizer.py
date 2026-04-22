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