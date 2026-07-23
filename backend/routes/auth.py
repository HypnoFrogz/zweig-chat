"""ChaosHelper Messenger — Authentication & user profile."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from helpers import (
    get_current_user, verify_password, hash_password, create_access_token,
    create_refresh_token, decode_refresh_token, hash_token,
    REFRESH_TOKEN_EXPIRE_DAYS, get_current_user_info, UPLOAD_DIR, now_iso,
)
from database import get_db
from ws_manager import manager

router = APIRouter(prefix="/api", tags=["auth"])

AVATAR_DIR = UPLOAD_DIR / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)


def _expires_at_for_refresh() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()


async def _create_refresh_session(db, username: str, refresh_token: str, session_id: str | None = None) -> str:
    session_id = session_id or str(uuid.uuid4())
    now = now_iso()
    await db.execute(
        """INSERT INTO user_sessions
           (id, username, refresh_hash, created_at, expires_at, last_used_at, revoked_at, revoked_reason)
           VALUES (?, ?, ?, ?, ?, ?, NULL, '')""",
        (session_id, username, hash_token(refresh_token), now, _expires_at_for_refresh(), now),
    )
    return session_id


async def _revoke_session(db, session_id: str, reason: str = "revoked"):
    await db.execute(
        "UPDATE user_sessions SET revoked_at = ?, revoked_reason = ? WHERE id = ? AND revoked_at IS NULL",
        (now_iso(), reason, session_id),
    )


async def _revoke_all_user_sessions(db, username: str, reason: str = "revoked"):
    await db.execute(
        "UPDATE user_sessions SET revoked_at = ?, revoked_reason = ? WHERE username = ? AND revoked_at IS NULL",
        (now_iso(), reason, username),
    )


@router.post("/login")
async def login(data: dict, request: Request):
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    db = await get_db()
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = await cursor.fetchone()
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user["blocked"]:
        raise HTTPException(status_code=403, detail="Account is blocked")

    now = now_iso()
    ip = request.client.host if request.client else ""

    # Update last_seen
    await db.execute("UPDATE users SET last_seen = ? WHERE username = ?", (now, username))
    # Log login event
    await db.execute(
        "INSERT INTO login_log (username, timestamp, ip) VALUES (?, ?, ?)",
        (username, now, ip)
    )
    await db.commit()

    session_id = str(uuid.uuid4())
    access_token = create_access_token(username)
    refresh_token = create_refresh_token(username, session_id)
    await _create_refresh_session(db, username, refresh_token, session_id)
    await db.commit()

    return {
        # `token` is kept for backward compatibility with existing clients.
        "token": access_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "username": username,
        "display_name": user["display_name"],
        "role": user["role"],
        "avatar_path": user["avatar_path"] or "",
    }


@router.post("/auth/refresh")
async def refresh_session(data: dict):
    refresh_token = (data.get("refresh_token") or "").strip()
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    payload = decode_refresh_token(refresh_token)
    username = payload["sub"]
    session_id = payload["sid"]

    db = await get_db()
    cursor = await db.execute(
        """SELECT id, username, refresh_hash, expires_at, revoked_at
           FROM user_sessions WHERE id = ?""",
        (session_id,),
    )
    session = await cursor.fetchone()
    if not session or session["username"] != username:
        raise HTTPException(status_code=401, detail="Refresh session not found")
    if session["revoked_at"]:
        raise HTTPException(status_code=401, detail="Refresh session revoked")
    if session["refresh_hash"] != hash_token(refresh_token):
        raise HTTPException(status_code=401, detail="Refresh token mismatch")
    if session["expires_at"] and session["expires_at"] < now_iso():
        await _revoke_session(db, session_id, "expired")
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # Validate user state before issuing new tokens.
    await get_current_user_info(username)

    # Rotate refresh token: revoke old session, issue new one.
    await _revoke_session(db, session_id, "rotated")
    new_session_id = str(uuid.uuid4())
    access_token = create_access_token(username)
    new_refresh_token = create_refresh_token(username, new_session_id)
    await _create_refresh_session(db, username, new_refresh_token, new_session_id)
    await db.commit()

    return {
        "token": access_token,
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


@router.post("/auth/logout")
async def logout_session(data: dict, username: str = Depends(get_current_user)):
    refresh_token = (data.get("refresh_token") or "").strip()
    db = await get_db()

    if refresh_token:
        payload = decode_refresh_token(refresh_token)
        if payload.get("sub") != username:
            raise HTTPException(status_code=403, detail="Cannot revoke another user's session")
        await _revoke_session(db, payload["sid"], "logout")
    else:
        await _revoke_all_user_sessions(db, username, "logout_all")

    await db.commit()
    return {"ok": True}


@router.get("/me")
async def get_me(username: str = Depends(get_current_user)):
    user = await get_current_user_info(username)
    return _user_public(user)


@router.put("/me")
async def update_me(data: dict, username: str = Depends(get_current_user)):
    await get_current_user_info(username)
    db = await get_db()

    updates = []
    params = []

    if "display_name" in data:
        updates.append("display_name = ?")
        params.append(data["display_name"].strip()[:100])
    if "nickname" in data:
        updates.append("nickname = ?")
        params.append(data["nickname"].strip()[:50])
    if "status_text" in data:
        updates.append("status_text = ?")
        params.append(data["status_text"].strip()[:200])
    if "password" in data and data["password"]:
        updates.append("password = ?")
        params.append(hash_password(data["password"]))

    if updates:
        params.append(username)
        await db.execute(f"UPDATE users SET {', '.join(updates)} WHERE username = ?", params)
        if "password" in data and data["password"]:
            # Password changed -> invalidate all long-lived refresh sessions.
            await _revoke_all_user_sessions(db, username, "password_changed")
        await db.commit()

    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = await cursor.fetchone()
    user_data = _user_public(dict(user))

    # Broadcast profile update to all connected users
    await manager.broadcast_all({
        "event": "user_updated",
        "user": user_data,
    })

    return user_data


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    username: str = Depends(get_current_user),
):
    await get_current_user_info(username)

    ext = Path(file.filename).suffix.lower() if file.filename else ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        raise HTTPException(status_code=400, detail="Invalid image format")

    filename = f"{username}{ext}"
    filepath = AVATAR_DIR / filename

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")
    with open(filepath, "wb") as f:
        f.write(content)

    avatar_path = f"/uploads/avatars/{filename}"
    db = await get_db()
    await db.execute("UPDATE users SET avatar_path = ? WHERE username = ?", (avatar_path, username))
    await db.commit()

    # Broadcast avatar update to all connected users
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = await cursor.fetchone()
    await manager.broadcast_all({
        "event": "user_updated",
        "user": _user_public(dict(user)),
    })

    return {"avatar_path": avatar_path}


@router.get("/users/{target_user}/avatar")
async def get_avatar(target_user: str):
    db = await get_db()
    cursor = await db.execute("SELECT avatar_path FROM users WHERE username = ?", (target_user,))
    row = await cursor.fetchone()
    avatar = row["avatar_path"] if row and row["avatar_path"] else ""
    return {"avatar_path": avatar}


@router.get("/users")
async def list_users(username: str = Depends(get_current_user)):
    """List all non-blocked users."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT username, display_name, nickname, avatar_path, status_text, role FROM users WHERE blocked = 0 ORDER BY username"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


@router.get("/presence")
async def get_presence(username: str = Depends(get_current_user)):
    """Online/offline status for all users."""
    return manager.get_online_users()


@router.get("/stats")
async def get_stats(username: str = Depends(get_current_user)):
    """Stats for Grafana dashboards."""
    from ws_manager import manager as ws_manager
    from database import get_db
    db = await get_db()

    online_count = len(ws_manager.active_connections)

    cursor = await db.execute(
        "SELECT COUNT(DISTINCT username) as cnt FROM user_activity_log WHERE date = date('now')"
    )
    row = await cursor.fetchone()
    dau = row["cnt"] if row else 0

    cursor2 = await db.execute(
        "SELECT COUNT(*) as cnt FROM call_log WHERE started_at >= date('now') AND status != 'rejected'"
    )
    row2 = await cursor2.fetchone()
    calls_today = row2["cnt"] if row2 else 0

    cursor3 = await db.execute("SELECT COUNT(*) as cnt FROM users")
    row3 = await cursor3.fetchone()
    total_users = row3["cnt"] if row3 else 0

    return {
        "online_users": online_count,
        "dau": dau,
        "calls_today": calls_today,
        "total_users": total_users,
    }


def _user_public(user: dict) -> dict:
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "nickname": user.get("nickname", ""),
        "avatar_path": user.get("avatar_path", ""),
        "status_text": user.get("status_text", ""),
        "role": user.get("role", "user"),
        "created_at": user.get("created_at", ""),
    }
