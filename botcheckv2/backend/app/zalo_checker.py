import re
import httpx
import json
import logging

log = logging.getLogger("api")

async def check_zalo_phone(phone: str, cookie: str, imei: str, zpw_sek: str = ""):
    """
    Check if a phone number is registered on Zalo using Zalo Web API.
    Returns: {"live": bool, "name": str, "avatar": str, "error": str}
    """
    phone = phone.strip()
    if not phone.startswith("84") and phone.startswith("0"):
        phone = "84" + phone[1:]
        
    if not cookie or not imei:
        return {"live": False, "error": "Chưa cấu hình Zalo Cookie hoặc IMEI"}
        
    # Extract zpw_sek from cookie if not provided
    if not zpw_sek:
        match = re.search(r'zpw_sek=([^;]+)', cookie)
        if match:
            zpw_sek = match.group(1)
            
    if not zpw_sek:
        # Sometimes zpw_sek is not required or can be skipped, but typically it is.
        pass

    url = "https://friends-wpa.chat.zalo.me/api/friend/profile/get"
    params = {
        "phone": phone,
        "avatar_size": 120,
        "imei": imei
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": cookie,
        "Origin": "https://chat.zalo.me",
        "Referer": "https://chat.zalo.me/",
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                return {"live": False, "error": f"HTTP {resp.status_code}"}
                
            data = resp.json()
            error_code = data.get("error_code", -1)
            
            if error_code == 0:
                profile = data.get("data", {})
                if profile and profile.get("userId"):
                    return {
                        "live": True, 
                        "name": profile.get("displayName", ""), 
                        "avatar": profile.get("avatar", ""),
                        "userId": profile.get("userId", "")
                    }
                return {"live": False, "error": "Không tìm thấy thông tin"}
            elif error_code == 216:
                return {"live": False, "error": "Số điện thoại chưa đăng ký Zalo"}
            elif error_code == -216:
                return {"live": False, "error": "Chặn tìm kiếm qua số điện thoại"}
            elif error_code == 11:
                return {"live": False, "error": "Cookie hết hạn hoặc không hợp lệ"}
            else:
                return {"live": False, "error": f"Lỗi Zalo: {error_code} - {data.get('error_message', '')}"}
    except Exception as e:
        log.error(f"Error checking zalo {phone}: {e}")
        return {"live": False, "error": f"Lỗi kết nối: {str(e)}"}
