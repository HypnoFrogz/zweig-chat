"""Zweig Messenger — Device token registration for push notifications."""

import uuid
from fastapi import APIRouter, Depends
from helpers import get_current_user, now_iso
from database import get_db

router = APIRouter(prefix="/api", tags=["devices"])


@router.post("/devices/register")
async def register_device(data: dict, username: str = Depends(get_current_user)):
    fcm_token = data.get("fcm_token", "").strip()
    voip_token = data.get("voip_token", "").strip()
    platform = data.get("platform", "android").strip()
    if not fcm_token and not voip_token:
        return {"error": "fcm_token or voip_token required"}
    if platform not in ("android", "ios"):
        platform = "android"

    db = await get_db()
    now = now_iso()

    # iOS reports its PushKit token in a separate callback from the FCM one, so
    # it arrives in its own request and is stored on its own.
    if voip_token:
        cursor = await db.execute(
            "SELECT id FROM user_voip_devices WHERE voip_token = ?", (voip_token,)
        )
        if await cursor.fetchone():
            await db.execute(
                "UPDATE user_voip_devices SET username = ?, updated_at = ? WHERE voip_token = ?",
                (username, now, voip_token),
            )
        else:
            await db.execute(
                "INSERT INTO user_voip_devices (id, username, voip_token, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), username, voip_token, now, now),
            )
        if not fcm_token:
            await db.commit()
            return {"ok": True}

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
    voip_token = data.get("voip_token", "").strip()
    if not fcm_token and not voip_token:
        return {"error": "fcm_token or voip_token required"}

    db = await get_db()
    if fcm_token:
        await db.execute(
            "DELETE FROM user_devices WHERE fcm_token = ? AND username = ?",
            (fcm_token, username),
        )
    # Signing out must stop CallKit from ringing this device too.
    if voip_token:
        await db.execute(
            "DELETE FROM user_voip_devices WHERE voip_token = ? AND username = ?",
            (voip_token, username),
        )
    await db.commit()
    return {"ok": True}
