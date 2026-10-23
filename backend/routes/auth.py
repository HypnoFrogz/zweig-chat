"""ChaosHelper Messenger — Authentication & user profile."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from helpers import (
    get_current_user, get_current_session, verify_password, hash_password, create_access_token,
    create_refresh_token, decode_refresh_token, hash_token,
    REFRESH_TOKEN_EXPIRE_DAYS, get_current_user_info, UPLOAD_DIR, now_iso,
)
from database import get_db
from ws_manager import manager
import sessions
import twofa

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
    cur = await db.execute("SELECT username FROM user_sessions WHERE id = ?", (session_id,))
    row = await cur.fetchone()
    if row:
        await sessions.revoke(db, row["username"], reason, sids=[session_id])


async def _revoke_all_user_sessions(db, username: str, reason: str = "revoked", keep: str | None = None):
    await sessions.revoke(db, username, reason, keep=keep)


@router.post("/login")
async def login(data: dict, request: Request):
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    db = await get_db()
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = await cursor.fetchone()
    # Federated stubs live in the same table but are not accounts on this
    # server: they have no usable password. Reject them before verify_password
    # both because the stored value is not a valid hash and because the reply
    # must stay indistinguishable from "no such user".
    # Боты входят только токеном Bot API, а не паролем.
    if not user or user["home_server"] or user["role"] == "bot":
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user["blocked"]:
        raise HTTPException(status_code=403, detail="Account is blocked")

    # 2FA включена — сначала код из приложения-аутентификатора.
    if await twofa.is_enabled(db, username):
        return {
            "twofa_required": True,
            "challenge": _make_challenge(username),
            "username": username,
        }

    return await _issue_login(db, user, data, request)


def _make_challenge(username: str) -> str:
    from jose import jwt
    from helpers import SECRET_KEY, ALGORITHM
    exp = datetime.now(timezone.utc) + timedelta(minutes=5)
    return jwt.encode({"sub": username, "type": "2fa", "exp": exp, "jti": uuid.uuid4().hex}, SECRET_KEY, algorithm=ALGORITHM)


def _read_challenge(token: str) -> str:
    from jose import jwt, JWTError
    from helpers import SECRET_KEY, ALGORITHM
    try:
        p = jwt.decode(token or "", SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="2FA challenge expired")
    if p.get("type") != "2fa" or not p.get("sub"):
        raise HTTPException(status_code=401, detail="2FA challenge expired")
    return p["sub"]


@router.post("/login/2fa")
async def login_second_step(data: dict, request: Request):
    """Второй шаг входа: код из приложения или резервный код."""
    username = _read_challenge(data.get("challenge"))
    if twofa.locked(username):
        raise HTTPException(status_code=429, detail="Too many attempts")
    db = await get_db()
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = await cursor.fetchone()
    if not user or user["blocked"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    kind = await twofa.verify(db, username, data.get("code") or "")
    if not kind:
        await db.commit()
        twofa.note_fail(username)
        raise HTTPException(status_code=401, detail="Invalid code")
    twofa.clear_fails(username)
    out = await _issue_login(db, user, data, request)
    if kind == "backup":
        out["backup_left"] = (await twofa.status(db, username))["backup_left"]
    return out


async def _issue_login(db, user, data: dict, request: Request) -> dict:
    username = user["username"]
    now = now_iso()
    ip = sessions.client_ip(request)

    # Update last_seen
    await db.execute("UPDATE users SET last_seen = ? WHERE username = ?", (now, username))
    # Log login event
    await db.execute(
        "INSERT INTO login_log (username, timestamp, ip) VALUES (?, ?, ?)",
        (username, now, ip)
    )
    await db.commit()

    session_id = str(uuid.uuid4())
    access_token = create_access_token(username, session_id)
    refresh_token = create_refresh_token(username, session_id)
    await _create_refresh_session(db, username, refresh_token, session_id)
    # Что за устройство: приложение может прислать {"device": {...}}, иначе
    # определяем по User-Agent.
    await sessions.note_activity(db, session_id, request.headers.get("user-agent", ""),
                                 data.get("device"), sessions.client_ip(request))
    await sessions.replace_same_device(db, username, session_id)
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
async def refresh_session(data: dict, request: Request):
    refresh_token = (data.get("refresh_token") or "").strip()
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    payload = decode_refresh_token(refresh_token)
    username = payload["sub"]
    session_id = payload["sid"]

    db = await get_db()
    cursor = await db.execute(
        """SELECT id, username, refresh_hash, expires_at, revoked_at, last_used_at, created_at
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

    # Автовыход: вход простаивал дольше срока, выбранного пользователем.
    days = await sessions.get_inactive_days(db, username)
    if session_id not in sessions.SOCKETS and sessions.is_stale(session["last_used_at"] or session["created_at"], days):
        await sessions.revoke(db, username, "inactive", sids=[session_id])
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh session revoked")

    # Validate user state before issuing new tokens.
    await get_current_user_info(username)

    # Новый refresh-токен в том же сеансе: прежний перестаёт действовать, а
    # устройство остаётся одной строкой в списке входов.
    access_token = create_access_token(username, session_id)
    new_refresh_token = create_refresh_token(username, session_id)
    await db.execute(
        "UPDATE user_sessions SET refresh_hash = ?, expires_at = ? WHERE id = ?",
        (hash_token(new_refresh_token), _expires_at_for_refresh(), session_id),
    )
    await sessions.note_activity(db, session_id, request.headers.get("user-agent", ""),
                                 data.get("device"), sessions.client_ip(request))
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


@router.delete("/account")
async def delete_account(data: dict, username: str = Depends(get_current_user)):
    """Permanently delete the current user's account (Apple 5.1.1(v) / GDPR erasure).

    Removes the user's personal data and revokes every session. Messages the user
    authored in shared channels are ANONYMIZED (sender_name -> placeholder, sender
    replaced with an opaque token), not deleted, so team history stays intact —
    which is what a corporate deployment needs and what Apple accepts.

    Requires the account password for re-authentication.
    """
    password = data.get("password") or ""
    if not password:
        raise HTTPException(status_code=400, detail="Password confirmation required")

    db = await get_db()
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = await cursor.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Incorrect password")

    # Safeguard: the last active admin cannot delete themselves and lock out the org.
    if user["role"] == "admin":
        cur = await db.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE role = 'admin' AND blocked = 0"
        )
        row = await cur.fetchone()
        if (row["cnt"] if row else 0) <= 1:
            raise HTTPException(
                status_code=409,
                detail="You are the last administrator — assign another admin before deleting your account.",
            )

    placeholder = "Удалённый пользователь"
    anon = f"deleted:{uuid.uuid4().hex[:12]}"

    # 1) Anonymize authored content (keep messages, drop the identity link).
    await db.execute(
        "UPDATE messages SET sender = ?, sender_name = ? WHERE sender = ?",
        (anon, placeholder, username),
    )
    await db.execute(
        "UPDATE channels SET last_msg_sender = ?, last_msg_sender_name = ? WHERE last_msg_sender = ?",
        (anon, placeholder, username),
    )
    await db.execute("UPDATE channels SET created_by = ? WHERE created_by = ?", (anon, username))
    await db.execute("UPDATE call_log SET started_by = ? WHERE started_by = ?", (anon, username))

    # 2) Delete personal / identity-linked rows explicitly (do not rely on FK cascade).
    await db.execute("DELETE FROM channel_members WHERE username = ?", (username,))
    await db.execute("DELETE FROM message_reads WHERE username = ?", (username,))
    await db.execute("DELETE FROM message_reactions WHERE username = ?", (username,))
    await db.execute("DELETE FROM user_devices WHERE username = ?", (username,))
    await sessions.revoke(db, username, "account_deleted")
    await db.execute("DELETE FROM user_sessions WHERE username = ?", (username,))
    await db.execute("DELETE FROM preferences WHERE username = ?", (username,))
    await db.execute("DELETE FROM feedback WHERE username = ?", (username,))
    await db.execute("DELETE FROM online_sessions WHERE username = ?", (username,))
    await db.execute("DELETE FROM user_activity_log WHERE username = ?", (username,))
    await db.execute("DELETE FROM login_log WHERE username = ?", (username,))
    await db.execute("DELETE FROM activity_hourly WHERE username = ?", (username,))
    await db.execute("DELETE FROM session_log WHERE username = ?", (username,))
    await db.execute("DELETE FROM task_project_members WHERE username = ?", (username,))
    await db.execute(
        "DELETE FROM calls WHERE caller_username = ? OR callee_username = ?", (username, username)
    )

    # 3) Delete avatar files from disk.
    for f in AVATAR_DIR.glob(f"{username}.*"):
        try:
            f.unlink()
        except OSError:
            pass

    # 4) Finally remove the user record itself.
    await db.execute("DELETE FROM users WHERE username = ?", (username,))
    await db.commit()

    # Tell connected clients this identity is gone (they drop it from lists/UI).
    await manager.broadcast_all({"event": "user_deleted", "username": username})
    return {"ok": True}


@router.get("/me")
async def get_me(username: str = Depends(get_current_user)):
    user = await get_current_user_info(username)
    return _user_public(user)


@router.put("/me")
async def update_me(data: dict, sess: tuple = Depends(get_current_session)):
    username, current_sid = sess
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
            # Пароль сменён — завершаем все остальные входы, этот оставляем.
            await _revoke_all_user_sessions(db, username, "password_changed", keep=current_sid)
        await db.commit()

    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = await cursor.fetchone()
    user_data = _user_public(dict(user))

    # Broadcast profile update to all connected users
    await manager.broadcast_all({
        "event": "user_updated",
        "user": user_data,
    })
    # И соседям по федерации: у них наша карточка живёт своей копией и сама
    # обновиться не может.
    from routes.federation import push_profile_to_peers
    await push_profile_to_peers(db, username)

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
    from routes.federation import push_profile_to_peers
    await push_profile_to_peers(db, username)

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
    """List all non-blocked local users.

    Federated stubs (home_server != '') are deliberately excluded: this is the
    local directory used for DMs, mentions and member pickers, and remote
    people are reachable only through an invite link they were given.
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT username, display_name, nickname, avatar_path, status_text, role "
        "FROM users WHERE blocked = 0 AND home_server = '' ORDER BY username"
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


# ── Устройства и сеансы ─────────────────────────────────────────

@router.get("/me/sessions")
async def my_sessions(sess: tuple = Depends(get_current_session)):
    username, sid = sess
    db = await get_db()
    return {"sessions": await sessions.list_sessions(db, username, sid), "current": sid}


@router.delete("/me/sessions/{session_id}")
async def end_my_session(session_id: str, sess: tuple = Depends(get_current_session)):
    username, sid = sess
    db = await get_db()
    n = await sessions.revoke(db, username, "ended_by_user", sids=[session_id])
    await db.commit()
    if not n:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "current": session_id == sid}


@router.post("/me/sessions/revoke-stale")
async def end_stale_sessions(data: dict | None = None, sess: tuple = Depends(get_current_session)):
    """Завершить входы, которыми давно не пользовались (по умолчанию — 7 дней)."""
    username, sid = sess
    days = int((data or {}).get("days") or 7)
    days = max(1, min(days, 365))
    db = await get_db()
    ids = await sessions.stale_session_ids(db, username, days, sid)
    n = await sessions.revoke(db, username, "stale", sids=ids) if ids else 0
    await db.commit()
    return {"ok": True, "ended": n}


@router.post("/me/sessions/revoke-others")
async def end_other_sessions(sess: tuple = Depends(get_current_session)):
    username, sid = sess
    if not sid:
        # Старый токен без номера сеанса: «остальные» не определить.
        raise HTTPException(status_code=409, detail="Sign in again to manage sessions")
    db = await get_db()
    n = await sessions.revoke(db, username, "ended_by_user", keep=sid)
    await db.commit()
    return {"ok": True, "ended": n}


# ── Двухфакторная аутентификация (по желанию) ───────────────────

async def _check_password(db, username: str, password: str):
    cur = await db.execute("SELECT password FROM users WHERE username = ?", (username,))
    row = await cur.fetchone()
    # 400, а не 401: клиенты считают 401 концом сеанса и выходят из аккаунта.
    if not row or not password or not verify_password(password, row["password"]):
        raise HTTPException(status_code=400, detail="Incorrect password")


@router.get("/me/2fa")
async def twofa_status(username: str = Depends(get_current_user)):
    db = await get_db()
    return await twofa.status(db, username)


@router.post("/me/2fa/setup")
async def twofa_setup(data: dict, request: Request, username: str = Depends(get_current_user)):
    """Шаг 1: пароль → секрет и QR-код. 2FA ещё не включена."""
    db = await get_db()
    await _check_password(db, username, data.get("password") or "")
    if await twofa.is_enabled(db, username):
        raise HTTPException(status_code=409, detail="2FA already enabled")
    secret = await twofa.start_setup(db, username)
    await db.commit()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0]
    url = twofa.otpauth_url(username, secret, host)
    return {"secret": secret, "otpauth_url": url, "qr_svg": twofa.qr_svg(url)}


@router.post("/me/2fa/enable")
async def twofa_enable(data: dict, sess: tuple = Depends(get_current_session)):
    """Шаг 2: первый код из приложения → 2FA включена, выдаём резервные коды.
    Остальные входы завершаются: их открывали без второго фактора."""
    username, sid = sess
    db = await get_db()
    codes = await twofa.confirm_setup(db, username, data.get("code") or "")
    if codes is None:
        raise HTTPException(status_code=400, detail="Invalid code")
    await sessions.revoke(db, username, "2fa_enabled", keep=sid)
    await db.commit()
    return {"enabled": True, "backup_codes": codes}


@router.post("/me/2fa/disable")
async def twofa_disable(data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    await _check_password(db, username, data.get("password") or "")
    if twofa.locked(username):
        raise HTTPException(status_code=429, detail="Too many attempts")
    if not await twofa.verify(db, username, data.get("code") or ""):
        await db.commit()
        twofa.note_fail(username)
        raise HTTPException(status_code=400, detail="Invalid code")
    await twofa.disable(db, username)
    await db.commit()
    return {"enabled": False}


@router.post("/me/2fa/backup-codes")
async def twofa_new_backup(data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    if twofa.locked(username):
        raise HTTPException(status_code=429, detail="Too many attempts")
    if not await twofa.verify(db, username, data.get("code") or "", allow_backup=False):
        await db.commit()
        twofa.note_fail(username)
        raise HTTPException(status_code=400, detail="Invalid code")
    codes = await twofa.regenerate_backup(db, username)
    await db.commit()
    return {"backup_codes": codes}



@router.post("/me/sessions/revoke-all")
async def end_all_sessions(username: str = Depends(get_current_user)):
    """Выйти на всех устройствах, включая это."""
    db = await get_db()
    n = await sessions.revoke(db, username, "ended_by_user")
    await db.commit()
    return {"ok": True, "ended": n}


@router.get("/me/sessions/settings")
async def session_settings(username: str = Depends(get_current_user)):
    db = await get_db()
    return {"inactive_days": await sessions.get_inactive_days(db, username),
            "choices": list(sessions.INACTIVE_CHOICES)}


@router.put("/me/sessions/settings")
async def update_session_settings(data: dict, sess: tuple = Depends(get_current_session)):
    username, sid = sess
    try:
        days = int(data.get("inactive_days", 0))
    except (TypeError, ValueError):
        days = -1
    if days not in sessions.INACTIVE_CHOICES:
        raise HTTPException(status_code=400, detail="inactive_days must be one of 0, 1, 7, 30")
    db = await get_db()
    await sessions.set_inactive_days(db, username, days)
    # Текущий вход только что был активен — освежаем отметку, чтобы не выкинуть себя.
    if sid:
        await db.execute("UPDATE user_sessions SET last_used_at = ? WHERE id = ?", (now_iso(), sid))
    ended = await sessions.revoke_inactive(db, username) if days else 0
    await db.commit()
    return {"inactive_days": days, "ended": ended}
