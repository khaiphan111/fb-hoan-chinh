"""FB check worker - process RIENG biet khoi bot chinh.

Ly do tach: khi Facebook chan IP egress hoac request treo, check_uid co the
ngam het timeout (15s) hang loat -> lam nghen event loop cua bot, bot ban hang
khong phan hoi. Worker chay tren 127.0.0.1:8001, bot goi qua HTTP voi timeout;
worker chet/treo thi bot fail-closed ve status "error" (bo qua, cho lan sau).

Chay bang: bash ~/workspace/fb-hoan-chinh/fb_check_worker.sh
Giam sat: cron fb-check-worker-keepalive (moi phut)
"""
import asyncio
from fastapi import FastAPI
from pydantic import BaseModel

from .fb import check_uid_direct

app = FastAPI(title="fb-check-worker")


class CheckReq(BaseModel):
    uid: str


class BatchReq(BaseModel):
    uids: list


@app.get("/health")
async def health():
    return {"ok": True, "service": "fb-check-worker"}


@app.post("/check")
async def check_one(req: CheckReq):
    """Check 1 UID, tra ve dict ket qua nhu fb.check_uid."""
    return await check_uid_direct(req.uid)


@app.post("/check_batch")
async def check_batch(req: BatchReq):
    """Check nhieu UID (toi da 50/lan), dong thoi toi da 10."""
    uids = [str(u) for u in (req.uids or [])][:50]
    sem = asyncio.Semaphore(10)

    async def _one(uid):
        async with sem:
            try:
                return await check_uid_direct(uid)
            except Exception as e:
                return {"uid": uid, "alive": False, "status": "error",
                        "name": "", "ok": False, "via": "worker_exception",
                        "error": str(e)[:200]}

    results = await asyncio.gather(*[_one(u) for u in uids])
    return {"results": results}
