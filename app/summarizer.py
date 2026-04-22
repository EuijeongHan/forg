import os
from config import ANTHROPIC_API_KEY

SYSTEM_PROMPT = """당신은 금융 공시 내용을 요약하는 전문가입니다.
다음 규칙을 반드시 지켜주세요:

1. 팩트만 3줄로 요약합니다
2. 숫자, 날짜, 금액은 원문 그대로 표기합니다
3. 투자 의견, 전망, 판단은 절대 포함하지 않습니다
4. 원문이 없으면 공시 유형만으로 간략히 설명합니다
5. 요약 마지막에 항상 아래 문구를 추가합니다:
   ⚠️ 본 요약은 AI가 생성한 참고용 정보입니다. 투자 판단 전 반드시 원문을 확인하세요."""

def build_prompt(corp_name, report_nm, content):
    nl = chr(10)
    if content:
        return nl.join(["기업명: " + corp_name, "공시 유형: " + report_nm, "공시 내용:", content[:3000], "", "위 공시를 3줄로 요약해주세요."])
    else:
        return nl.join(["기업명: " + corp_name, "공시 유형: " + report_nm, "", "원문 없이 공시 유형만으로 어떤 공시인지 2줄로 간략히 설명해주세요."])

async def summarize_with_openai(prompt):
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=500,
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
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
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
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
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
