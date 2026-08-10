import asyncio
import httpx

async def check():
    uid = '100089260699193'
    url = f"https://mbasic.facebook.com/{uid}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        r = await client.get(url, headers=headers)
        print("Status code:", r.status_code)
        if r.status_code in [301, 302]:
            print("Location:", r.headers.get("location"))
        print("Text:", r.text)
        
        die_indicators = ["không tìm thấy trang", "account disabled", "bị vô hiệu hóa", "checkpoint", "not found", "không khả dụng"]
        html = r.text.lower()
        if r.status_code == 200:
            for ind in die_indicators:
                if ind in html:
                    print(f"Found die indicator: {ind}")

asyncio.run(check())
