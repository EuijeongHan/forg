import httpx, asyncio, os

async def check():
    key = os.getenv("DART_API_KEY")
    print("key:", key[:10] if key else "None")
    async with httpx.AsyncClient() as client:
        r = await client.get("https://opendart.fss.or.kr/api/document.json", params={"crtfc_key": key, "rcept_no": "20260422000229"}, timeout=10)
        data = r.json()
        print("status:", data.get("status"))
        print("content:", len(data.get("content", "")))

asyncio.run(check())
