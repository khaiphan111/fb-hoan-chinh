import os
import json
import time
from fastapi import APIRouter, Depends, Form, File, UploadFile, HTTPException
from typing import Optional

from . import db
from .api import auth

router = APIRouter(prefix="/admin/campaigns", tags=["campaigns"])

@router.get("")
def get_campaigns(_=Depends(auth)):
    camps = db.get_campaigns()
    return {"ok": True, "data": camps}

@router.post("")
async def create_campaign(
    name: str = Form(...),
    type: str = Form(...),
    scheduled_for: int = Form(0),
    config: str = Form("{}"),
    text_content: str = Form(""),
    photo: UploadFile = File(None),
    _=Depends(auth)
):
    image_url = ""
    if photo and photo.filename:
        # Save photo in static/images or data
        filename = f"camp_{int(time.time())}_{photo.filename}"
        photo_path = os.path.join(os.path.dirname(__file__), "..", "static", "images", filename)
        os.makedirs(os.path.dirname(photo_path), exist_ok=True)
        content = await photo.read()
        with open(photo_path, "wb") as f:
            f.write(content)
        image_url = filename

    camp_id = db.create_campaign(name, type, scheduled_for, config, text_content, image_url)
    return {"ok": True, "campaign_id": camp_id}

@router.delete("/{id}")
def delete_campaign(id: int, _=Depends(auth)):
    camp = db.get_campaign(id)
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete_campaign(id)
    return {"ok": True}
