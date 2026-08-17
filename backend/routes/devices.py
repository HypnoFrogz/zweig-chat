"""Zweig Messenger — Device token registration for push notifications."""

import uuid
from fastapi import APIRouter, Depends
from helpers import get_current_user, now_iso
from database import get_db

router = APIRouter(prefix="/api", tags=["devices"])


@router.post("/devices/register")
async def register_device(data: dict, username: str = Depends(get_current_user)):
    fcm_token = data.get("fcm_token", "").strip()
    platform = data.get("platform", "android").strip()
    if not fcm_token:
        return {"error": "fcm_token required"}
    if platform not in ("android", "ios"):
        platform = "android"

    db = await get_db()
    now = now_iso()

    cursor = await db.execute(
        "SELECT id, username FROM user_devices WHERE fcm_token = ?", (fcm_token,)
    )
    existing = await cursor.fetchone()

    if existing:
        await db.execute(
            "UPDATE user_devices SET username = ?, platform = ?, updated_at = ? WHERE fcm_token = ?",
            (username, platform, now, fcm_token),
        )
    else:
        await db.execute(
            "INSERT INTO user_devices (id, username, fcm_token, platform, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), username, fcm_token, platform, now, now),
        )

    await db.commit()
    return {"ok": True}


@router.post("/devices/unregister")
async def unregister_device(data: dict, username: str = Depends(get_current_user)):
    fcm_token = data.get("fcm_token", "").strip()
    if not fcm_token:
        return {"error": "fcm_token required"}

    db = await get_db()
    await db.execute(
        "DELETE FROM user_devices WHERE fcm_token = ? AND username = ?",
        (fcm_token, username),
    )
    await db.commit()
    return {"ok": True}
