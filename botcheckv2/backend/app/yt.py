import re
import json
import random
import os

from . import db

def get_yt_api():
    from googleapiclient.discovery import build
    api_key = db.get_setting("yt_api_key", "")
    
    if not api_key:
        raise Exception("Chưa cấu hình YouTube API Key")
    return build('youtube', 'v3', developerKey=api_key)

def parse_yt_username(link: str) -> str:
    link = (link or "").strip()
    if not link: return ""
    match = re.search(r'@([a-zA-Z0-9_.-]+)', link)
    if match: return match.group(1)
    if "youtube.com/channel/" in link:
        return link.split("youtube.com/channel/")[1].split("/")[0]
    if "youtube.com/c/" in link:
        return link.split("youtube.com/c/")[1].split("/")[0]
    if "youtube.com/user/" in link:
        return link.split("youtube.com/user/")[1].split("/")[0]
    return link.split("/")[-1]

import asyncio

async def fetch_yt_info(username: str) -> dict:
    youtube = get_yt_api()
    result = {
        "username": username,
        "subscribers": 0,
        "videos": 0,
        "avatar": "",
    }
    
    try:
        from googleapiclient.errors import HttpError
        if username.startswith("UC"):
            request = youtube.channels().list(part='snippet,statistics', id=username)
        else:
            request = youtube.channels().list(part='snippet,statistics', forHandle=username)
            
        response = await asyncio.to_thread(request.execute)
        
        if not response.get('items'):
            # Fallback if forHandle doesn't work, search by query
            search_request = youtube.search().list(part='snippet', q=username, type='channel', maxResults=1)
            search_response = await asyncio.to_thread(search_request.execute)
            if search_response.get('items'):
                channel_id = search_response['items'][0]['id']['channelId']
                request = youtube.channels().list(part='snippet,statistics', id=channel_id)
                response = await asyncio.to_thread(request.execute)
                
        if response.get('items'):
            item = response['items'][0]
            snippet = item['snippet']
            stats = item['statistics']
            
            result["username"] = snippet.get("title", username)
            result["subscribers"] = int(stats.get("subscriberCount", 0))
            result["videos"] = int(stats.get("videoCount", 0))
            result["avatar"] = snippet.get("thumbnails", {}).get("high", {}).get("url", "")
        else:
            raise Exception("Không tìm thấy kênh")
            
    except HttpError as e:
        raise Exception(f"Lỗi YouTube API: {e.reason}")
    except Exception as e:
        raise Exception(f"Không thể lấy thông tin kênh: {str(e)}")
        
    return result

def parse_yt_video_id(link: str) -> str:
    link = (link or "").strip()
    if not link: return ""
    match = re.search(r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})', link)
    if match: return match.group(1)
    return link

async def fetch_yt_video_info(url: str) -> dict:
    video_id = parse_yt_video_id(url)
    if not video_id:
        raise Exception("Không tìm thấy Video ID")
        
    youtube = get_yt_api()
    result = {
        "id": video_id,
        "views": 0,
        "likes": 0,
        "comments": 0,
        "desc": f"YouTube Video: {video_id}",
        "cover": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        "username": "",
    }
    
    try:
        from googleapiclient.errors import HttpError
        request = youtube.videos().list(part='snippet,statistics', id=video_id)
        response = await asyncio.to_thread(request.execute)
        
        if response.get('items'):
            item = response['items'][0]
            snippet = item['snippet']
            stats = item['statistics']
            
            result["desc"] = snippet.get("title", result["desc"])
            result["username"] = snippet.get("channelTitle", "")
            result["views"] = int(stats.get("viewCount", 0))
            result["likes"] = int(stats.get("likeCount", 0))
            result["comments"] = int(stats.get("commentCount", 0))
        else:
            raise Exception("Không tìm thấy video")
            
    except HttpError as e:
        raise Exception(f"Lỗi YouTube API: {e.reason}")
    except Exception as e:
        raise Exception(f"Không thể lấy dữ liệu video YouTube: {str(e)}")
        
    return result

def build_yt_caption(info: dict) -> str:
    return (
        f"╭─── <b>[ KẾT QUẢ YOUTUBE ]</b> ───╮\n"
        f"│\n"
        f"├ 👤 <b>Kênh:</b> <code>{info['username']}</code>\n"
        f"├ 👥 <b>Đăng ký:</b> {info['subscribers']:,}\n"
        f"├ 🎥 <b>Video:</b> {info['videos']:,}\n"
        f"│\n"
        f"╰──────────────────────────╯\n\n"
        f"<i>💡 Gõ /trackyt {info['username']} để tự động theo dõi kênh này.</i>"
    )

def build_yt_video_caption(info: dict) -> str:
    return (
        f"╭─── <b>[ KẾT QUẢ VIDEO YOUTUBE ]</b> ───╮\n"
        f"│\n"
        f"├ 👤 <b>Kênh:</b> {info['username']}\n"
        f"├ 👁️ <b>Lượt xem:</b> {info['views']:,}\n"
        f"├ 👍 <b>Lượt thích:</b> {info['likes']:,}\n"
        f"│\n"
        f"╰───────────────────────────────╯\n\n"
        f"<i>💡 Gõ /trackvyt {info['id']} để tự động theo dõi video này.</i>"
    )
