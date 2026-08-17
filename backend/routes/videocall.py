"""Zweig Messenger — Video calling via LiveKit (channel-based)."""

import json
import os
import uuid
import asyncio
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from helpers import get_current_user, get_display_name, now_iso
from database import get_db
from ws_manager import manager

router = APIRouter(prefix="/api", tags=["videocall"])

LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "devsecret1234567890devsecret")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
# Public URL for clients (mobile/web) — never expose the Docker-internal hostname.
# NB: os.getenv's default only applies when the var is UNSET; docker-compose passes
# LIVEKIT_PUBLIC_URL=${LIVEKIT_PUBLIC_URL} which sends an empty string when it's not
# in .env, so we must handle "" explicitly or clients get an empty host
# ("https:///rtc/validate"). Fall back to the server's own domain.
LIVEKIT_PUBLIC_URL = os.getenv("LIVEKIT_PUBLIC_URL", "").strip()
if not LIVEKIT_PUBLIC_URL:
    _lk_domain = os.getenv("SERVER_DOMAIN", "").strip()
    LIVEKIT_PUBLIC_URL = (
        f"wss://{_lk_domain}/livekit/" if _lk_domain else "wss://localhost/livekit/"
    )
CALL_RING_TIMEOUT_SEC = int(os.getenv("CALL_RING_TIMEOUT_SEC", "45"))
CALL_ACTIVE_STALE_SEC = int(os.getenv("CALL_ACTIVE_STALE_SEC", "180"))
CALL_CLEANUP_INTERVAL_SEC = int(os.getenv("CALL_CLEANUP_INTERVAL_SEC", "30"))
CALL_RETENTION_DAYS = int(os.getenv("CALL_RETENTION_DAYS", "5"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


CALL_CLEANUP_ON_STARTUP = _env_bool("CALL_CLEANUP_ON_STARTUP", True)
CALL_PUSH_ALWAYS = _env_bool("CALL_PUSH_ALWAYS", True)
def _create_livekit_token(room_name: str, username: str, display_name: str) -> str:
    from livekit.api import AccessToken, VideoGrants

    token = AccessToken(
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )
    token.with_identity(username)
    token.with_name(display_name)
    token.with_grants(VideoGrants(room_join=True, room=room_name))
    return token.to_jwt()


async def _get_members(db, channel_id: str) -> list[str]:
    cursor = await db.execute(
        "SELECT username FROM channel_members WHERE channel_id = ?", (channel_id,)
    )
    return [r["username"] for r in await cursor.fetchall()]


async def _get_channel_by_slug(db, slug: str, username: str):
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE c.slug = ? AND cm.username = ?""",
        (slug, username),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")
    return dict(row)


async def _get_channel_by_slug_for_member(db, slug: str, username: str):
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE (c.slug = ? OR c.id = ?) AND cm.username = ?""",
        (slug, slug, username),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")
    return dict(row)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


async def _mark_call_expired_if_needed(db, call_row) -> bool:
    if not call_row or call_row["status"] != "ringing":
        return False
    exp = _parse_iso(call_row["expires_at"])
    if not exp or _now_utc() <= exp:
        return False
    await db.execute(
        "UPDATE calls SET status = 'missed', ended_at = ?, end_reason = ? WHERE id = ?",
        (now_iso(), "timeout", call_row["id"]),
    )
    return True


async def cleanup_calls_state() -> dict[str, int]:
    """
    Self-healing cleanup for addressed calls:
    - ringing (expired) -> missed
    - active (stale) -> ended
    - remove old finished calls by retention window
    """
    db = await get_db()
    now_iso_s = now_iso()

    cur = await db.execute(
        """UPDATE calls
           SET status = 'missed', ended_at = ?, end_reason = 'timeout'
           WHERE status = 'ringing' AND expires_at != '' AND expires_at < ?""",
        (now_iso_s, now_iso_s),
    )
    expired_ringing = cur.rowcount or 0

    active_stale_before = (_now_utc() - timedelta(seconds=CALL_ACTIVE_STALE_SEC)).isoformat()
    cur = await db.execute(
        """UPDATE calls
           SET status = 'ended', ended_at = ?, end_reason = 'stale_active_timeout'
           WHERE status = 'active'
             AND COALESCE(answered_at, created_at) != ''
             AND COALESCE(answered_at, created_at) < ?""",
        (now_iso_s, active_stale_before),
    )
    stale_active = cur.rowcount or 0

    retention_cutoff = (_now_utc() - timedelta(days=CALL_RETENTION_DAYS)).isoformat()
    cur = await db.execute(
        """DELETE FROM calls
           WHERE status IN ('ended', 'missed', 'rejected', 'cancelled')
             AND COALESCE(ended_at, created_at) != ''
             AND COALESCE(ended_at, created_at) < ?""",
        (retention_cutoff,),
    )
    deleted_old = cur.rowcount or 0

    await db.commit()
    return {
        "expired_ringing": expired_ringing,
        "stale_active": stale_active,
        "deleted_old": deleted_old,
    }


async def run_calls_cleanup_once() -> dict[str, int]:
    try:
        stats = await cleanup_calls_state()
        if any(stats.values()):
            print(f"[calls.cleanup] {stats}")
        return stats
    except Exception as e:
        print(f"[calls.cleanup] failed: {e}")
        return {"expired_ringing": 0, "stale_active": 0, "deleted_old": 0}


async def run_calls_cleanup_loop():
    while True:
        await asyncio.sleep(CALL_CLEANUP_INTERVAL_SEC)
        await run_calls_cleanup_once()


@router.post("/calls/start")
async def start_addressed_call(data: dict, username: str = Depends(get_current_user)):
    """Start addressed (1:1) call with audio-first defaults."""
    channel_slug = data.get("channel_slug", "") or data.get("conversation_id", "")
    callee_username = (data.get("callee_username") or "").strip().lower()
    mode = (data.get("mode") or "audio").strip().lower()
    if mode not in ("audio", "video"):
        mode = "audio"
    if not channel_slug or not callee_username:
        raise HTTPException(status_code=400, detail="channel_slug and callee_username are required")
    if callee_username == username:
        raise HTTPException(status_code=400, detail="Cannot call yourself")

    db = await get_db()
    ch = await _get_channel_by_slug_for_member(db, channel_slug, username)
    members = await _get_members(db, ch["id"])
    if callee_username not in members:
        raise HTTPException(status_code=400, detail="Callee is not a channel member")

    # Self-heal stale call states before checking busy.
    await run_calls_cleanup_once()

    # Ensure target user is not already ringing/active on another call.
    c = await db.execute(
        "SELECT id FROM calls WHERE callee_username = ? AND status IN ('ringing', 'active') LIMIT 1",
        (callee_username,),
    )
    busy = await c.fetchone()
    if busy:
        raise HTTPException(status_code=409, detail="Callee is busy")

    call_id = str(uuid.uuid4())
    room_name = f"call-{ch['id']}-{call_id[:8]}"
    now = _now_utc()
    expires_at = (now + timedelta(seconds=CALL_RING_TIMEOUT_SEC)).isoformat()

    await db.execute(
        """INSERT INTO calls
           (id, channel_id, channel_slug, room_name, caller_username, callee_username, status, mode,
            video_enabled, speaker_enabled, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'ringing', ?, 0, 0, ?, ?)""",
        (call_id, ch["id"], ch["slug"], room_name, username, callee_username, mode, expires_at, now.isoformat()),
    )
    await db.execute(
        "INSERT INTO call_log (channel_id, started_by, started_at, status) VALUES (?, ?, ?, 'active')",
        (ch["id"], username, now.isoformat()),
    )
    await db.commit()

    caller_name = await get_display_name(username)
    callee_name = await get_display_name(callee_username)
    callee_token = _create_livekit_token(room_name, callee_username, callee_name)
    caller_token = _create_livekit_token(room_name, username, caller_name)

    payload = {
        "event": "call_invite",
        "call_id": call_id,
        "channel_id": ch["id"],
        "channel_slug": ch["slug"],
        "room_name": room_name,
        "caller_username": username,
        "caller_name": caller_name,
        "callee_username": callee_username,
        "mode": mode,
        "expires_at": expires_at,
        "livekit_token": callee_token,
        "livekit_url": LIVEKIT_PUBLIC_URL,
    }
    # A callee living on another server is reached through their server, not
    # over our WebSocket, and our push channels know nothing about them. The
    # room stays here: the token above is signed with our LiveKit secret.
    from routes.federation import remote_peer_of, push_call_event
    callee_domain = await remote_peer_of(db, callee_username)
    if callee_domain:
        await db.execute(
            "UPDATE calls SET remote_domain = ? WHERE id = ?", (callee_domain, call_id)
        )
        await db.commit()
        cur = await db.execute("SELECT remote_username FROM users WHERE username = ?", (callee_username,))
        row = await cur.fetchone()
        delivered = await push_call_event(callee_domain, "/api/federation/call/invite", {
            "call_id": call_id,
            "caller": username,
            "caller_name": caller_name,
            "callee": row["remote_username"] if row else "",
            "room_name": room_name,
            "mode": mode,
            "expires_at": expires_at,
            "livekit_token": callee_token,
            "livekit_url": LIVEKIT_PUBLIC_URL,
        })
        if not delivered:
            await db.execute(
                "UPDATE calls SET status = 'ended', ended_at = ?, end_reason = 'peer_unreachable' WHERE id = ?",
                (now_iso(), call_id),
            )
            await db.commit()
            raise HTTPException(status_code=502, detail=f"Сервер {callee_domain} недоступен")
        await manager.send_to_user(username, {"event": "call_ringing", "call_id": call_id, "channel_slug": ch["slug"]})
        return {
            "ok": True,
            "call_id": call_id,
            "status": "ringing",
            "mode": mode,
            "expires_at": expires_at,
            "livekit_token": caller_token,
            "livekit_url": LIVEKIT_PUBLIC_URL,
            "room_name": room_name,
        }

    await manager.send_to_user(callee_username, payload)
    await manager.send_to_user(username, {"event": "call_ringing", "call_id": call_id, "channel_slug": ch["slug"]})

    should_push = CALL_PUSH_ALWAYS or (not manager.is_online(callee_username))
    if should_push:
        from fcm_sender import send_call_notification
        await send_call_notification(
            recipient=callee_username,
            caller_name=caller_name,
            channel_slug=ch["slug"],
            room_name=room_name,
            livekit_token=callee_token,
            livekit_url=LIVEKIT_PUBLIC_URL,
            call_id=call_id,
            mode=mode,
            expires_at=expires_at,
        )

    # Web push wake-up path for background PWA clients.
    from routes.push import send_push_to_user
    await send_push_to_user(callee_username, {
        "type": "call_invite",
        "title": "Входящий звонок",
        "body": f"{caller_name} звонит вам",
        "conv_id": ch["slug"],
        "channel_slug": ch["slug"],
        "call_id": call_id,
        "mode": mode,
        "expires_at": expires_at,
        "badge": 0,
    })

    return {
        "ok": True,
        "call_id": call_id,
        "status": "ringing",
        "mode": mode,
        "expires_at": expires_at,
        "livekit_token": caller_token,
        "livekit_url": LIVEKIT_PUBLIC_URL,
        "room_name": room_name,
    }


@router.post("/calls/answer")
async def answer_addressed_call(data: dict, username: str = Depends(get_current_user)):
    call_id = (data.get("call_id") or "").strip()
    if not call_id:
        raise HTTPException(status_code=400, detail="call_id is required")
    db = await get_db()
    c = await db.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
    call = await c.fetchone()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    await _mark_call_expired_if_needed(db, call)
    c2 = await db.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
    call = await c2.fetchone()
    if call["status"] == "missed":
        await db.commit()
        raise HTTPException(status_code=410, detail="Call expired")
    if call["callee_username"] != username:
        raise HTTPException(status_code=403, detail="Only callee can answer this call")
    if call["status"] != "ringing":
        raise HTTPException(status_code=409, detail=f"Call status is {call['status']}")

    answered_at = now_iso()
    await db.execute(
        "UPDATE calls SET status = 'active', answered_at = ? WHERE id = ?",
        (answered_at, call_id),
    )
    await db.commit()

    # For a federated call the room is on the other server, so the token we
    # hand back is the one it minted for us — we cannot sign one for its room.
    if call["remote_domain"]:
        token = call["remote_livekit_token"]
        livekit_url = call["remote_livekit_url"]
        await _relay_call_state(db, call, "answered")
    else:
        display_name = await get_display_name(username)
        token = _create_livekit_token(call["room_name"], username, display_name)
        livekit_url = LIVEKIT_PUBLIC_URL

    answer_event = {
        "event": "call_answered",
        "call_id": call_id,
        "channel_slug": call["channel_slug"],
        "answered_by": username,
    }
    await manager.send_to_user(call["caller_username"], answer_event)
    await manager.send_to_user(call["callee_username"], answer_event)

    return {
        "ok": True,
        "call_id": call_id,
        "status": "active",
        "room_name": call["room_name"],
        "mode": call["mode"],
        "livekit_token": token,
        "livekit_url": livekit_url,
    }


@router.post("/calls/reject")
async def reject_addressed_call(data: dict, username: str = Depends(get_current_user)):
    call_id = (data.get("call_id") or "").strip()
    if not call_id:
        raise HTTPException(status_code=400, detail="call_id is required")
    db = await get_db()
    c = await db.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
    call = await c.fetchone()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if call["callee_username"] != username:
        raise HTTPException(status_code=403, detail="Only callee can reject this call")

    if call["status"] == "ringing":
        ended = now_iso()
        await db.execute(
            "UPDATE calls SET status = 'rejected', ended_at = ?, end_reason = ? WHERE id = ?",
            (ended, "rejected", call_id),
        )
        await db.execute(
            "UPDATE call_log SET status = 'rejected', ended_at = ?, duration_sec = 0 WHERE channel_id = ? AND status = 'active'",
            (ended, call["channel_id"]),
        )
        await db.commit()
        await _relay_call_state(db, call, "rejected", "rejected")
        reject_event = {"event": "call_ended", "call_id": call_id, "channel_slug": call["channel_slug"], "reason": "rejected"}
        await manager.send_to_user(call["caller_username"], reject_event)
        await manager.send_to_user(call["callee_username"], reject_event)
    return {"ok": True}


@router.post("/calls/end")
async def end_addressed_call(data: dict, username: str = Depends(get_current_user)):
    call_id = (data.get("call_id") or "").strip()
    if not call_id:
        raise HTTPException(status_code=400, detail="call_id is required")
    db = await get_db()
    c = await db.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
    call = await c.fetchone()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if username not in (call["caller_username"], call["callee_username"]):
        raise HTTPException(status_code=403, detail="Not a call participant")

    if call["status"] in ("ringing", "active"):
        ended = now_iso()
        await db.execute(
            "UPDATE calls SET status = 'ended', ended_at = ?, end_reason = ? WHERE id = ?",
            (ended, "ended_by_user", call_id),
        )
        await db.execute(
            """UPDATE call_log SET status = 'ended', ended_at = ?,
               duration_sec = CAST((julianday(?) - julianday(started_at)) * 86400 AS INTEGER)
               WHERE channel_id = ? AND status = 'active'""",
            (ended, ended, call["channel_id"]),
        )
        await db.commit()
        await _relay_call_state(db, call, "ended", "ended")
        end_event = {"event": "call_ended", "call_id": call_id, "channel_slug": call["channel_slug"], "reason": "ended"}
        await manager.send_to_user(call["caller_username"], end_event)
        await manager.send_to_user(call["callee_username"], end_event)
    return {"ok": True}


@router.post("/calls/update-media")
async def update_call_media(data: dict, username: str = Depends(get_current_user)):
    call_id = (data.get("call_id") or "").strip()
    if not call_id:
        raise HTTPException(status_code=400, detail="call_id is required")
    db = await get_db()
    c = await db.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
    call = await c.fetchone()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if username not in (call["caller_username"], call["callee_username"]):
        raise HTTPException(status_code=403, detail="Not a call participant")
    if call["status"] != "active":
        raise HTTPException(status_code=409, detail="Call is not active")

    video_enabled = int(bool(data.get("video_enabled", False)))
    speaker_enabled = int(bool(data.get("speaker_enabled", False)))
    mode = "video" if video_enabled else "audio"
    await db.execute(
        "UPDATE calls SET video_enabled = ?, speaker_enabled = ?, mode = ? WHERE id = ?",
        (video_enabled, speaker_enabled, mode, call_id),
    )
    await db.commit()

    event = {
        "event": "call_media_updated",
        "call_id": call_id,
        "video_enabled": bool(video_enabled),
        "speaker_enabled": bool(speaker_enabled),
        "mode": mode,
        "updated_by": username,
    }
    await manager.send_to_user(call["caller_username"], event)
    await manager.send_to_user(call["callee_username"], event)
    return {"ok": True, "mode": mode}


@router.get("/calls/{call_id}")
async def get_call_state(call_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    c = await db.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
    call = await c.fetchone()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if username not in (call["caller_username"], call["callee_username"]):
        raise HTTPException(status_code=403, detail="Not a call participant")

    expired = await _mark_call_expired_if_needed(db, call)
    if expired:
        await db.commit()
        c = await db.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
        call = await c.fetchone()

    payload = dict(call)
    payload["video_enabled"] = bool(payload.get("video_enabled", 0))
    payload["speaker_enabled"] = bool(payload.get("speaker_enabled", 0))
    return payload


@router.post("/videocall/token")
async def get_token(data: dict, username: str = Depends(get_current_user)):
    """Generate a LiveKit room token for the user."""
    channel_slug = data.get("channel_slug", "") or data.get("conversation_id", "")
    if not channel_slug:
        return {"error": "channel_slug required"}

    db = await get_db()
    # Try by slug first, then by id (backward compatibility)
    cursor = await db.execute(
        """SELECT c.id FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id AND cm.username = ?
           WHERE c.slug = ? OR c.id = ?""",
        (username, channel_slug, channel_slug),
    )
    ch = await cursor.fetchone()
    if not ch:
        return {"error": "Channel not found"}

    room_name = f"call-{ch['id']}"
    display_name = await get_display_name(username)
    token = _create_livekit_token(room_name, username, display_name)
    return {"token": token, "url": LIVEKIT_PUBLIC_URL, "room_name": room_name}


@router.post("/videocall/start")
async def start_call(data: dict, username: str = Depends(get_current_user)):
    """Start a video call in a channel."""
    channel_slug = data.get("channel_slug", "") or data.get("conversation_id", "")
    if not channel_slug:
        return {"error": "channel_slug required"}

    db = await get_db()
    # Resolve channel
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id AND cm.username = ?
           WHERE c.slug = ? OR c.id = ?""",
        (username, channel_slug, channel_slug),
    )
    ch_row = await cursor.fetchone()
    if not ch_row:
        return {"error": "Channel not found"}
    ch = dict(ch_row)

    room_name = f"call-{ch['id']}"
    display_name = await get_display_name(username)
    token = _create_livekit_token(room_name, username, display_name)
    now = now_iso()

    # Register active call
    await db.execute(
        "INSERT OR REPLACE INTO active_calls (channel_id, room_name, started_by, started_at) VALUES (?, ?, ?, ?)",
        (ch["id"], room_name, username, now),
    )
    await db.execute(
        "INSERT INTO call_log (channel_id, started_by, started_at, status) VALUES (?, ?, ?, 'active')",
        (ch["id"], username, now),
    )

    # System message about call
    call_msg = {
        "id": str(uuid.uuid4()),
        "channel_id": ch["id"],
        "sender": "system",
        "sender_name": "System",
        "type": "call",
        "text": f"{display_name} started a video call",
        "call": {
            "room_name": room_name,
            "status": "active",
            "started_by": username,
            "started_at": now,
        },
        "timestamp": now,
        "read_by": [username],
    }

    call_data = json.dumps(call_msg["call"], ensure_ascii=False)
    await db.execute(
        "INSERT INTO messages (id, channel_id, sender, sender_name, type, text, file_data, call_data, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (call_msg["id"], ch["id"], "system", "System", "call", call_msg["text"], None, call_data, now),
    )
    await db.execute(
        "INSERT INTO message_reads (message_id, username) VALUES (?, ?)",
        (call_msg["id"], username),
    )

    await db.execute(
        "UPDATE channels SET last_msg_text = ?, last_msg_sender = ?, last_msg_sender_name = ?, last_msg_timestamp = ? WHERE id = ?",
        (call_msg["text"], "system", "System", now, ch["id"]),
    )
    await db.commit()

    members = await _get_members(db, ch["id"])

    # Notify all members with their personal tokens
    from fcm_sender import send_call_notification
    for p in members:
        p_display = await get_display_name(p)
        p_token = _create_livekit_token(room_name, p, p_display)
        await manager.send_to_user(p, {
            "event": "call_started",
            "channel_id": ch["id"],
            "channel_slug": ch["slug"],
            "room_name": room_name,
            "started_by": username,
            "started_by_name": display_name,
            "livekit_token": p_token,
            "livekit_url": LIVEKIT_PUBLIC_URL,
        })
        # Send push to offline members (calls are critical)
        if p != username and not manager.is_online(p):
            await send_call_notification(
                recipient=p,
                caller_name=display_name,
                channel_slug=ch["slug"],
                room_name=room_name,
                livekit_token=p_token,
                livekit_url=LIVEKIT_PUBLIC_URL,
            )
        # Web Push for PWA (always send for calls, even if online — important!)
        if p != username:
            from routes.push import send_push_to_user
            await send_push_to_user(p, {
                "type": "call",
                "title": "Входящий звонок",
                "body": f"{display_name} вызывает вас",
                "conv_id": ch["slug"],
                "badge": 0,
            })

    await manager.send_to_channel(members, {"event": "new_message", "message": call_msg})

    return {"token": token, "url": LIVEKIT_PUBLIC_URL, "room_name": room_name}


@router.post("/videocall/invite")
async def invite_to_call(data: dict, username: str = Depends(get_current_user)):
    """Invite specific users to an active call."""
    channel_slug = data.get("channel_slug", "")
    usernames = data.get("usernames", [])
    if not channel_slug or not usernames:
        return {"error": "channel_slug and usernames required"}

    db = await get_db()
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id AND cm.username = ?
           WHERE c.slug = ? OR c.id = ?""",
        (username, channel_slug, channel_slug),
    )
    ch_row = await cursor.fetchone()
    if not ch_row:
        return {"error": "Channel not found"}
    ch = dict(ch_row)

    # Check active call exists
    cursor = await db.execute("SELECT * FROM active_calls WHERE channel_id = ?", (ch["id"],))
    call_row = await cursor.fetchone()
    if not call_row:
        return {"error": "No active call in this channel"}

    room_name = call_row["room_name"]
    display_name = await get_display_name(username)

    from routes.federation import remote_peer_of, push_call_event

    invited = 0
    unreachable = []
    for u in usernames:
        u_display = await get_display_name(u)
        # The room is ours either way, so we mint the token here — including
        # for participants on other servers, whose own server cannot sign one.
        u_token = _create_livekit_token(room_name, u, u_display)
        payload = {
            "event": "call_started",
            "channel_id": ch["id"],
            "channel_slug": ch["slug"],
            "room_name": room_name,
            "started_by": username,
            "started_by_name": display_name,
            "livekit_token": u_token,
            "livekit_url": LIVEKIT_PUBLIC_URL,
        }

        domain = await remote_peer_of(db, u)
        if domain:
            cur = await db.execute("SELECT remote_username FROM users WHERE username = ?", (u,))
            row = await cur.fetchone()
            ok = await push_call_event(domain, "/api/federation/call/conference", {
                **payload,
                "callee": row["remote_username"] if row else "",
                "inviter": username,
                "inviter_name": display_name,
            })
            if ok:
                invited += 1
            else:
                unreachable.append(u)
            continue

        await manager.send_to_user(u, payload)
        invited += 1

    return {"ok": True, "invited": invited, "unreachable": unreachable}


@router.post("/videocall/reject")
async def reject_call(data: dict, username: str = Depends(get_current_user)):
    """Reject/decline an incoming call. For 1:1 channels, ends the call entirely."""
    channel_slug = data.get("channel_slug", "") or data.get("conversation_id", "")
    if not channel_slug:
        return {"error": "channel_slug required"}

    db = await get_db()
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id AND cm.username = ?
           WHERE c.slug = ? OR c.id = ?""",
        (username, channel_slug, channel_slug),
    )
    ch_row = await cursor.fetchone()
    if not ch_row:
        return {"error": "Channel not found"}
    ch = dict(ch_row)

    # Check if call is still active
    cursor2 = await db.execute("SELECT * FROM active_calls WHERE channel_id = ?", (ch["id"],))
    call_row = await cursor2.fetchone()
    if not call_row:
        return {"ok": True, "ended": False}

    room_name = call_row["room_name"]
    members = await _get_members(db, ch["id"])

    # For direct (1:1) channels, rejecting ends the call for everyone
    if ch["type"] == "direct" or len(members) <= 2:
        await db.execute("DELETE FROM active_calls WHERE channel_id = ?", (ch["id"],))

        # Update call message status to "rejected"
        cursor3 = await db.execute(
            """SELECT id, call_data FROM messages
               WHERE channel_id = ? AND type = 'call' AND call_data IS NOT NULL
               ORDER BY timestamp DESC LIMIT 1""",
            (ch["id"],),
        )
        msg_row = await cursor3.fetchone()
        if msg_row and msg_row["call_data"]:
            call = json.loads(msg_row["call_data"])
            if call.get("status") == "active":
                call["status"] = "rejected"
                call["duration"] = 0
                await db.execute(
                    "UPDATE messages SET call_data = ?, text = ? WHERE id = ?",
                    (json.dumps(call, ensure_ascii=False), "Missed call", msg_row["id"]),
                )

        rejected_at = now_iso()
        await db.execute(
            "UPDATE call_log SET status = 'rejected', ended_at = ?, duration_sec = 0 WHERE channel_id = ? AND status = 'active'",
            (rejected_at, ch["id"]),
        )
        await db.commit()

        await manager.send_to_channel(members, {
            "event": "call_ended",
            "channel_id": ch["id"],
            "channel_slug": ch["slug"],
            "room_name": room_name,
            "reason": "rejected",
        })
        return {"ok": True, "ended": True}

    return {"ok": True, "ended": False}


@router.post("/videocall/answer")
async def answer_call(data: dict, username: str = Depends(get_current_user)):
    """Notify other devices/tabs that a call was answered."""
    channel_slug = data.get("channel_slug", "") or data.get("conversation_id", "")
    if not channel_slug:
        return {"error": "channel_slug required"}

    db = await get_db()
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id AND cm.username = ?
           WHERE c.slug = ? OR c.id = ?""",
        (username, channel_slug, channel_slug),
    )
    ch_row = await cursor.fetchone()
    if not ch_row:
        return {"error": "Channel not found"}
    ch = dict(ch_row)

    members = await _get_members(db, ch["id"])
    display_name = await get_display_name(username)

    # Broadcast call_answered to all members — other devices/tabs dismiss the incoming call UI
    await manager.send_to_channel(members, {
        "event": "call_answered",
        "channel_id": ch["id"],
        "channel_slug": ch["slug"],
        "answered_by": username,
        "answered_by_name": display_name,
    })
    return {"ok": True}


@router.post("/videocall/end")
async def end_call(data: dict, username: str = Depends(get_current_user)):
    """End a video call."""
    channel_slug = data.get("channel_slug", "") or data.get("conversation_id", "")
    room_name = data.get("room_name", "")

    if not channel_slug:
        return {"error": "channel_slug required"}

    db = await get_db()
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id AND cm.username = ?
           WHERE c.slug = ? OR c.id = ?""",
        (username, channel_slug, channel_slug),
    )
    ch_row = await cursor.fetchone()
    if not ch_row:
        return {"error": "Channel not found"}
    ch = dict(ch_row)

    # Remove from active calls
    await db.execute("DELETE FROM active_calls WHERE channel_id = ?", (ch["id"],))

    # Find active call message and mark ended
    cursor2 = await db.execute(
        """SELECT id, call_data, timestamp FROM messages
           WHERE channel_id = ? AND type = 'call' AND call_data IS NOT NULL
           ORDER BY timestamp DESC LIMIT 1""",
        (ch["id"],),
    )
    msg_row = await cursor2.fetchone()
    if msg_row and msg_row["call_data"]:
        call = json.loads(msg_row["call_data"])
        if call.get("status") == "active":
            call["status"] = "ended"
            try:
                start_dt = datetime.fromisoformat(call.get("started_at", msg_row["timestamp"]))
                duration = int((datetime.now(timezone.utc) - start_dt).total_seconds())
                call["duration"] = duration
            except Exception:
                call["duration"] = 0

            new_text = f"Video call ended ({call['duration']}s)"
            await db.execute(
                "UPDATE messages SET call_data = ?, text = ? WHERE id = ?",
                (json.dumps(call, ensure_ascii=False), new_text, msg_row["id"]),
            )

    ended_at = now_iso()
    await db.execute(
        """UPDATE call_log SET status = 'ended', ended_at = ?,
           duration_sec = CAST((julianday(?) - julianday(started_at)) * 86400 AS INTEGER)
           WHERE channel_id = ? AND status = 'active'""",
        (ended_at, ended_at, ch["id"]),
    )

    await db.commit()

    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {
        "event": "call_ended",
        "channel_id": ch["id"],
        "channel_slug": ch["slug"],
        "room_name": room_name,
    })
    return {"ok": True}


@router.get("/videocall/active")
async def get_any_active_call(username: str = Depends(get_current_user)):
    """Return the first active call in any channel where the current user is a member.
    Used by the mobile app on WS reconnect to restore the incoming call UI."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT ac.*, c.slug as channel_slug, c.name as channel_name
           FROM active_calls ac
           JOIN channels c ON c.id = ac.channel_id
           JOIN channel_members cm ON cm.channel_id = ac.channel_id AND cm.username = ?
           WHERE ac.started_by != ?
             AND ac.started_at > datetime('now', '-30 minutes')
           ORDER BY ac.started_at DESC LIMIT 1""",
        (username, username),
    )
    row = await cursor.fetchone()
    if not row:
        return {"active": False}

    display_name = await get_display_name(username)
    token = _create_livekit_token(row["room_name"], username, display_name)

    # Fetch caller display name
    cursor2 = await db.execute(
        "SELECT display_name FROM users WHERE username = ?", (row["started_by"],)
    )
    caller_row = await cursor2.fetchone()
    caller_name = caller_row["display_name"] if caller_row else row["started_by"]

    return {
        "active": True,
        "channel_slug": row["channel_slug"],
        "channel_name": row["channel_name"],
        "room_name": row["room_name"],
        "started_by": row["started_by"],
        "started_by_name": caller_name,
        "livekit_token": token,
        "livekit_url": LIVEKIT_PUBLIC_URL,
    }


@router.get("/channels/{slug}/call")
async def get_active_call(slug: str, username: str = Depends(get_current_user)):
    """Check if there's an active call in a channel."""
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)

    cursor = await db.execute(
        "SELECT * FROM active_calls WHERE channel_id = ?", (ch["id"],)
    )
    row = await cursor.fetchone()
    if not row:
        return {"active": False}

    display_name = await get_display_name(username)
    token = _create_livekit_token(row["room_name"], username, display_name)
    return {
        "active": True,
        "room_name": row["room_name"],
        "started_by": row["started_by"],
        "started_at": row["started_at"],
        "livekit_token": token,
        "livekit_url": LIVEKIT_PUBLIC_URL,
    }


# ── Federated calls ──────────────────────────────────────────────────────────
#
# Called from routes.federation, which owns the peer authentication. These
# functions hold the call-state half so that videocall.py stays the only place
# that writes to the calls table.


async def register_incoming_federated_call(
    db, *, call_id: str, channel_id: str, room_name: str, caller_uid: str,
    caller_name: str, callee: str, mode: str, expires_at: str, domain: str,
    livekit_url: str, livekit_token: str,
) -> dict:
    """Record a call started on a peer's server and ring our local user.

    The room belongs to `domain`, so we keep the token and URL they minted:
    we cannot produce our own, the secret that signs them is theirs.
    """
    await run_calls_cleanup_once()
    c = await db.execute(
        "SELECT id FROM calls WHERE callee_username = ? AND status IN ('ringing', 'active') LIMIT 1",
        (callee,),
    )
    if await c.fetchone():
        raise HTTPException(status_code=409, detail="Callee is busy")

    c = await db.execute("SELECT slug FROM channels WHERE id = ?", (channel_id,))
    row = await c.fetchone()
    channel_slug = row["slug"] if row else ""
    mode = mode if mode in ("audio", "video") else "audio"

    await db.execute(
        """INSERT OR REPLACE INTO calls
           (id, channel_id, channel_slug, room_name, caller_username, callee_username, status, mode,
            video_enabled, speaker_enabled, expires_at, created_at,
            remote_domain, remote_livekit_url, remote_livekit_token)
           VALUES (?, ?, ?, ?, ?, ?, 'ringing', ?, 0, 0, ?, ?, ?, ?, ?)""",
        (call_id, channel_id, channel_slug, room_name, caller_uid, callee, mode,
         expires_at, now_iso(), domain, livekit_url, livekit_token),
    )
    await db.commit()

    payload = {
        "event": "call_invite",
        "call_id": call_id,
        "channel_id": channel_id,
        "channel_slug": channel_slug,
        "room_name": room_name,
        "caller_username": caller_uid,
        "caller_name": caller_name,
        "callee_username": callee,
        "mode": mode,
        "expires_at": expires_at,
        "livekit_token": livekit_token,
        "livekit_url": livekit_url,
        "remote_domain": domain,
    }
    await manager.send_to_user(callee, payload)

    if CALL_PUSH_ALWAYS or not manager.is_online(callee):
        from fcm_sender import send_call_notification
        await send_call_notification(
            recipient=callee, caller_name=caller_name, channel_slug=channel_slug,
            room_name=room_name, livekit_token=livekit_token, livekit_url=livekit_url,
            call_id=call_id, mode=mode, expires_at=expires_at,
        )
    from routes.push import send_push_to_user
    await send_push_to_user(callee, {
        "type": "call_invite",
        "title": "Входящий звонок",
        "body": f"{caller_name} звонит вам",
        "conv_id": channel_slug,
        "channel_slug": channel_slug,
        "call_id": call_id,
        "mode": mode,
        "expires_at": expires_at,
        "badge": 0,
    })
    return {"ok": True}


async def apply_federated_call_state(db, call: dict, state: str, reason: str = "") -> dict:
    """Apply answer / reject / end that happened on the peer's side."""
    call_id = call["id"]
    if state == "answered":
        if call["status"] == "ringing":
            await db.execute(
                "UPDATE calls SET status = 'active', answered_at = ? WHERE id = ?",
                (now_iso(), call_id),
            )
            await db.commit()
        event = {
            "event": "call_answered",
            "call_id": call_id,
            "channel_slug": call["channel_slug"],
            "answered_by": call["callee_username"],
        }
    else:
        if call["status"] in ("ringing", "active"):
            ended = now_iso()
            await db.execute(
                "UPDATE calls SET status = ?, ended_at = ?, end_reason = ? WHERE id = ?",
                ("rejected" if state == "rejected" else "ended", ended, reason or state, call_id),
            )
            await db.execute(
                """UPDATE call_log SET status = ?, ended_at = ?,
                   duration_sec = CAST((julianday(?) - julianday(started_at)) * 86400 AS INTEGER)
                   WHERE channel_id = ? AND status = 'active'""",
                (state, ended, ended, call["channel_id"]),
            )
            await db.commit()
        event = {
            "event": "call_ended",
            "call_id": call_id,
            "channel_slug": call["channel_slug"],
            "reason": reason or state,
        }

    # Only one of these is local; sending to a stub is a harmless no-op.
    await manager.send_to_user(call["caller_username"], event)
    await manager.send_to_user(call["callee_username"], event)
    return {"ok": True}


async def _relay_call_state(db, call, state: str, reason: str = "") -> None:
    """Tell the other server about a state change, if the call crosses servers."""
    domain = call["remote_domain"] if "remote_domain" in call.keys() else ""
    if not domain:
        return
    from routes.federation import push_call_event
    await push_call_event(domain, "/api/federation/call/state", {
        "call_id": call["id"], "state": state, "reason": reason,
    })
