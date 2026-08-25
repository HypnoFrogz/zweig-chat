"""Zweig Messenger — server-to-server federation (peer linking).

Handshake, in short:

  1. Admin on A adds domain B.
     A stores {domain: B, status: pending_out, request_token: T} and calls
     POST https://B/api/federation/request {from_domain: A, request_token: T}

  2. B does NOT trust the body. It calls back
     GET https://A/api/federation/verify?token=T&for=B
     Only the real A can confirm it issued T for B — that is what proves domain
     ownership. On success B stores {domain: A, status: pending_in} and pings
     its admins over WebSocket.

  3. B's admin approves: B generates shared_secret S, stores it, and calls
     POST https://A/api/federation/approve {from_domain: B, request_token: T,
     shared_secret: S}. A matches T against its pending_out row and activates.

  4. B's admin declines: B calls POST https://A/api/federation/decline {…, T}.

After activation both sides hold the same S and authenticate later S2S calls
with it (see require_peer). Everything travels over HTTPS — S is a bearer
secret and must never go over plain HTTP.
"""

import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from helpers import get_current_user, require_admin, now_iso
from database import get_db
from ws_manager import manager

router = APIRouter(prefix="/api", tags=["federation"])

# Our own public domain, e.g. "chat.example.com". Federation is disabled
# unless this is configured — we cannot prove who we are without it.
SERVER_DOMAIN = os.getenv("SERVER_DOMAIN", "").strip().lower().rstrip("/")

# Allow plain HTTP between peers only for local development.
FEDERATION_ALLOW_HTTP = os.getenv("FEDERATION_ALLOW_HTTP", "").lower() in ("1", "true", "yes")

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

# Development only. With FEDERATION_ALLOW_HTTP the two peers are usually
# processes on the same machine, so a port — and a single-label host like
# "localhost" — has to be accepted. Never reachable in production, where
# FEDERATION_ALLOW_HTTP is off and a real public domain is required.
_DEV_HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:\d{2,5})?$"
)
_TIMEOUT = 10.0


def _host_re() -> re.Pattern:
    return _DEV_HOST_RE if FEDERATION_ALLOW_HTTP else _DOMAIN_RE


def _normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].strip().rstrip(".")
    return d


def _validate_domain(d: str) -> None:
    if not d or not _host_re().fullmatch(d):
        raise HTTPException(status_code=400, detail="Некорректный домен сервера")
    if SERVER_DOMAIN and d == SERVER_DOMAIN:
        raise HTTPException(status_code=400, detail="Нельзя подключить сервер к самому себе")


def _peer_url(domain: str, path: str) -> str:
    scheme = "http" if FEDERATION_ALLOW_HTTP else "https"
    return f"{scheme}://{domain}{path}"


def _require_configured() -> None:
    if not SERVER_DOMAIN:
        raise HTTPException(
            status_code=503,
            detail="Федерация не настроена: задайте SERVER_DOMAIN в .env",
        )


async def _get_peer(db, domain: str):
    cur = await db.execute("SELECT * FROM federated_servers WHERE domain = ?", (domain,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def _notify_admins(event: str, payload: dict) -> None:
    """Push a federation event to every admin that is currently connected."""
    db = await get_db()
    cur = await db.execute("SELECT username FROM users WHERE role = 'admin' AND blocked = 0")
    for row in await cur.fetchall():
        await manager.send_to_user(row["username"], {"event": event, **payload})


def _public(row: dict) -> dict:
    """Strip secrets before returning a peer to the admin UI."""
    return {
        "domain": row["domain"],
        "status": row["status"],
        "direction": row["direction"],
        "requested_by": row["requested_by"],
        "decided_by": row["decided_by"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ── Peer authentication ──────────────────────────────────────────────────────

async def require_peer(authorization: str = Header(default="")) -> str:
    """FastAPI dependency — authenticate a call coming from a federated server.

    Peers authenticate with the shared_secret agreed during the handshake:

        Authorization: Peer <their-domain>:<shared_secret>

    Both sides store the same secret for a given link, so the same row serves
    to verify incoming calls and to sign outgoing ones. Returns the caller's
    domain; raises 401 for anything unrecognised.

    Note this is deliberately NOT used by the handshake endpoints above: those
    run before a secret exists and prove domain ownership by calling back.
    """
    _require_configured()
    scheme, _, value = (authorization or "").strip().partition(" ")
    if scheme.lower() != "peer" or ":" not in value:
        raise HTTPException(status_code=401, detail="Требуется аутентификация сервера")

    # rpartition, not partition: the domain may itself contain a colon when it
    # carries a port (dev setups), while the secret is token_urlsafe and never
    # does. Splitting on the first colon would cut "localhost:8002" in half.
    domain, _, secret = value.rpartition(":")
    domain = _normalize_domain(domain)
    if not domain or not secret:
        raise HTTPException(status_code=401, detail="Требуется аутентификация сервера")

    db = await get_db()
    peer = await _get_peer(db, domain)
    if not peer or peer["status"] != "active" or not peer["shared_secret"]:
        raise HTTPException(status_code=401, detail="Сервер не в активной федерации")
    # compare_digest keeps the check constant-time — a plain != would leak the
    # secret one byte at a time to anyone able to measure the response.
    if not secrets.compare_digest(peer["shared_secret"], secret):
        raise HTTPException(status_code=401, detail="Неверный секрет сервера")
    return domain


def remote_id(domain: str, username: str) -> str:
    """Local primary key for a user that lives on `domain`."""
    return f"{username.strip().lower()}@{_normalize_domain(domain)}"


async def ensure_remote_user(
    domain: str, username: str, display_name: str = "", avatar_url: str = ""
) -> str:
    """Create or refresh the stub row for a user living on a peer server.

    channel_members.username is a foreign key into users(username) and
    foreign_keys is ON, so a remote participant cannot be added to a
    conversation without a row here. The stub carries no usable password: the
    stored value is a marker, and login rejects any row with home_server set.

    Returns the local qualified username.
    """
    domain = _normalize_domain(domain)
    bare = (username or "").strip().lower()
    if not domain or not bare:
        raise HTTPException(status_code=400, detail="Некорректный удалённый пользователь")

    uid = remote_id(domain, bare)
    display = (display_name or "").strip() or bare
    db = await get_db()
    cur = await db.execute("SELECT 1 FROM users WHERE username = ?", (uid,))
    if await cur.fetchone():
        await db.execute(
            "UPDATE users SET display_name = ?, avatar_path = ? WHERE username = ?",
            (display, avatar_url or "", uid),
        )
    else:
        await db.execute(
            "INSERT INTO users (username, password, display_name, avatar_path, role, "
            "created_at, home_server, remote_username) "
            "VALUES (?, ?, ?, ?, 'user', ?, ?, ?)",
            (uid, "!federated", display, avatar_url or "", now_iso(), domain, bare),
        )
    await db.commit()
    return uid


async def peer_post(domain: str, path: str, payload: dict) -> httpx.Response:
    """POST to a federated peer, signed with the secret we share with it."""
    _require_configured()
    db = await get_db()
    peer = await _get_peer(db, domain)
    if not peer or peer["status"] != "active" or not peer["shared_secret"]:
        raise HTTPException(status_code=409, detail=f"Сервер {domain} не в активной федерации")

    headers = {"Authorization": f"Peer {SERVER_DOMAIN}:{peer['shared_secret']}"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        return await client.post(_peer_url(domain, path), json=payload, headers=headers)


# ── Server-to-server endpoints (no user auth) ────────────────────────────────

@router.get("/federation/info")
async def federation_info():
    """Public identity of this server."""
    return {"domain": SERVER_DOMAIN, "software": "zweig", "federation": bool(SERVER_DOMAIN)}


@router.get("/federation/verify")
async def federation_verify(token: str = "", for_domain: str = Query("", alias="for")):
    """Confirm that we issued `token` for the asking domain.

    Called by the peer during the handshake — this is what proves the request
    they received really originated from this domain. The token is bound to a
    specific peer, so a token issued for one server cannot be replayed at
    another.
    """
    _require_configured()
    asking = _normalize_domain(for_domain)
    if not token or not asking:
        return {"valid": False, "domain": SERVER_DOMAIN}

    db = await get_db()
    peer = await _get_peer(db, asking)
    valid = bool(
        peer
        and peer["status"] == "pending_out"
        and peer["request_token"]
        and secrets.compare_digest(peer["request_token"], token)
    )
    return {"valid": valid, "domain": SERVER_DOMAIN}


@router.post("/federation/request")
async def federation_request(data: dict):
    """Incoming link request from another server."""
    _require_configured()
    from_domain = _normalize_domain(data.get("from_domain", ""))
    token = (data.get("request_token") or "").strip()
    _validate_domain(from_domain)
    if not token:
        raise HTTPException(status_code=400, detail="request_token обязателен")

    # Verify the caller really is `from_domain` by asking that domain whether it
    # issued this token for us. A spoofed request fails here.
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _peer_url(from_domain, "/api/federation/verify"),
                params={"token": token, "for": SERVER_DOMAIN},
            )
        ok = resp.status_code == 200 and resp.json().get("valid") is True
    except Exception:
        raise HTTPException(status_code=502, detail="Не удалось связаться с запрашивающим сервером")

    if not ok:
        raise HTTPException(status_code=403, detail="Не удалось подтвердить владение доменом")

    db = await get_db()
    existing = await _get_peer(db, from_domain)
    if existing and existing["status"] == "active":
        return {"status": "active"}

    now = now_iso()
    await db.execute(
        """INSERT INTO federated_servers
               (domain, status, direction, request_token, created_at, updated_at)
           VALUES (?, 'pending_in', 'incoming', ?, ?, ?)
           ON CONFLICT(domain) DO UPDATE SET
               status='pending_in', direction='incoming', request_token=excluded.request_token,
               last_error='', updated_at=excluded.updated_at""",
        (from_domain, token, now, now),
    )
    await db.commit()

    await _notify_admins("federation_request", {"domain": from_domain, "created_at": now})
    return {"status": "pending"}


@router.post("/federation/approve")
async def federation_approve(data: dict):
    """The peer's admin approved our outgoing request."""
    _require_configured()
    from_domain = _normalize_domain(data.get("from_domain", ""))
    token = (data.get("request_token") or "").strip()
    secret = (data.get("shared_secret") or "").strip()
    if not from_domain or not token or not secret:
        raise HTTPException(status_code=400, detail="Некорректные данные подтверждения")

    db = await get_db()
    peer = await _get_peer(db, from_domain)
    # Only a peer we actually asked, with the exact token we generated, may
    # activate the link — otherwise anyone could force themselves in.
    if not peer or peer["status"] != "pending_out" or not secrets.compare_digest(peer["request_token"], token):
        raise HTTPException(status_code=403, detail="Нет ожидающей заявки для этого сервера")

    now = now_iso()
    await db.execute(
        "UPDATE federated_servers SET status='active', shared_secret=?, last_error='', updated_at=? WHERE domain=?",
        (secret, now, from_domain),
    )
    await db.commit()
    await _notify_admins("federation_linked", {"domain": from_domain})
    return {"status": "active"}


@router.post("/federation/decline")
async def federation_decline(data: dict):
    """The peer's admin declined our outgoing request."""
    _require_configured()
    from_domain = _normalize_domain(data.get("from_domain", ""))
    token = (data.get("request_token") or "").strip()

    db = await get_db()
    peer = await _get_peer(db, from_domain)
    if not peer or peer["status"] != "pending_out" or not secrets.compare_digest(peer["request_token"], token or ""):
        raise HTTPException(status_code=403, detail="Нет ожидающей заявки для этого сервера")

    await db.execute(
        "UPDATE federated_servers SET status='declined', request_token='', updated_at=? WHERE domain=?",
        (now_iso(), from_domain),
    )
    await db.commit()
    await _notify_admins("federation_declined", {"domain": from_domain})
    return {"status": "declined"}


# ── Admin endpoints ──────────────────────────────────────────────────────────

@router.get("/admin/federation/servers")
async def list_servers(username: str = Depends(get_current_user)):
    await require_admin(username)
    db = await get_db()
    cur = await db.execute("SELECT * FROM federated_servers ORDER BY updated_at DESC")
    return {
        "server_domain": SERVER_DOMAIN,
        "servers": [_public(dict(r)) for r in await cur.fetchall()],
    }


@router.post("/admin/federation/servers")
async def add_server(data: dict, username: str = Depends(get_current_user)):
    """Ask another server to link with us."""
    await require_admin(username)
    _require_configured()

    domain = _normalize_domain(data.get("domain", ""))
    _validate_domain(domain)

    db = await get_db()
    peer = await _get_peer(db, domain)
    if peer and peer["status"] == "active":
        raise HTTPException(status_code=409, detail="Сервер уже подключён")
    if peer and peer["status"] == "pending_in":
        raise HTTPException(
            status_code=409,
            detail="Этот сервер уже прислал заявку вам — подтвердите её во входящих",
        )

    token = secrets.token_urlsafe(32)
    now = now_iso()
    await db.execute(
        """INSERT INTO federated_servers
               (domain, status, direction, request_token, requested_by, created_at, updated_at)
           VALUES (?, 'pending_out', 'outgoing', ?, ?, ?, ?)
           ON CONFLICT(domain) DO UPDATE SET
               status='pending_out', direction='outgoing', request_token=excluded.request_token,
               requested_by=excluded.requested_by, last_error='', updated_at=excluded.updated_at""",
        (domain, token, username, now, now),
    )
    await db.commit()

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _peer_url(domain, "/api/federation/request"),
                json={"from_domain": SERVER_DOMAIN, "request_token": token},
            )
    except Exception as e:
        await db.execute(
            "UPDATE federated_servers SET last_error=?, updated_at=? WHERE domain=?",
            (f"Сервер недоступен: {e}", now_iso(), domain),
        )
        await db.commit()
        raise HTTPException(status_code=502, detail="Сервер недоступен. Проверьте домен")

    if resp.status_code != 200:
        detail = "Сервер отклонил запрос"
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        await db.execute(
            "UPDATE federated_servers SET last_error=?, updated_at=? WHERE domain=?",
            (detail, now_iso(), domain),
        )
        await db.commit()
        raise HTTPException(status_code=502, detail=detail)

    return {"domain": domain, "status": "pending_out"}


@router.post("/admin/federation/servers/{domain}/approve")
async def approve_server(domain: str, username: str = Depends(get_current_user)):
    """Accept an incoming link request and hand the peer a shared secret."""
    await require_admin(username)
    _require_configured()
    domain = _normalize_domain(domain)

    db = await get_db()
    peer = await _get_peer(db, domain)
    if not peer or peer["status"] != "pending_in":
        raise HTTPException(status_code=404, detail="Нет входящей заявки от этого сервера")

    secret = secrets.token_urlsafe(48)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _peer_url(domain, "/api/federation/approve"),
                json={
                    "from_domain": SERVER_DOMAIN,
                    "request_token": peer["request_token"],
                    "shared_secret": secret,
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
    except Exception as e:
        await db.execute(
            "UPDATE federated_servers SET last_error=?, updated_at=? WHERE domain=?",
            (f"Не удалось подтвердить: {e}", now_iso(), domain),
        )
        await db.commit()
        raise HTTPException(status_code=502, detail="Не удалось связаться с сервером. Заявка осталась во входящих")

    now = now_iso()
    await db.execute(
        """UPDATE federated_servers
              SET status='active', shared_secret=?, decided_by=?, last_error='', updated_at=?
            WHERE domain=?""",
        (secret, username, now, domain),
    )
    await db.commit()
    return {"domain": domain, "status": "active"}


@router.post("/admin/federation/servers/{domain}/decline")
async def decline_server(domain: str, username: str = Depends(get_current_user)):
    await require_admin(username)
    domain = _normalize_domain(domain)

    db = await get_db()
    peer = await _get_peer(db, domain)
    if not peer or peer["status"] != "pending_in":
        raise HTTPException(status_code=404, detail="Нет входящей заявки от этого сервера")

    # Best-effort: tell them, but decline locally regardless.
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await client.post(
                _peer_url(domain, "/api/federation/decline"),
                json={"from_domain": SERVER_DOMAIN, "request_token": peer["request_token"]},
            )
    except Exception:
        pass

    await db.execute(
        "UPDATE federated_servers SET status='declined', decided_by=?, request_token='', updated_at=? WHERE domain=?",
        (username, now_iso(), domain),
    )
    await db.commit()
    return {"domain": domain, "status": "declined"}


@router.delete("/admin/federation/servers/{domain}")
async def remove_server(domain: str, username: str = Depends(get_current_user)):
    """Drop a link (or a finished request) entirely."""
    await require_admin(username)
    domain = _normalize_domain(domain)

    db = await get_db()
    peer = await _get_peer(db, domain)
    if not peer:
        raise HTTPException(status_code=404, detail="Сервер не найден")

    await db.execute("DELETE FROM federated_servers WHERE domain = ?", (domain,))
    await db.commit()
    return {"ok": True}


# ── Invite links ─────────────────────────────────────────────────────────────
#
# There is no cross-server user directory: a server never hands a peer the list
# of its people. Instead the owner of a profile mints a token, passes the link
# on out of band (mail, another messenger, paper), and only whoever holds that
# token can open a conversation with them.

INVITE_DEFAULT_TTL_DAYS = 14
INVITE_MAX_TTL_DAYS = 365
INVITE_MAX_USES = 100
INVITE_MAX_ACTIVE = 50

# Best-effort throttle on redemption attempts, keyed by calling peer. It lives
# in the process, so it resets on restart and is per-worker — enough to make
# guessing tokens impractical, not a substitute for the token's own entropy.
_REDEEM_WINDOW_SEC = 300.0
_REDEEM_MAX_ATTEMPTS = 20
_redeem_attempts: dict[str, list[float]] = {}

_INVITE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _invite_url(token: str) -> str:
    scheme = "http" if FEDERATION_ALLOW_HTTP else "https"
    return f"{scheme}://{SERVER_DOMAIN}/i/{token}"


def _absolute_url(path: str) -> str:
    """Make a local avatar path absolute so a peer can actually load it."""
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    scheme = "http" if FEDERATION_ALLOW_HTTP else "https"
    return f"{scheme}://{SERVER_DOMAIN}{path if path.startswith('/') else '/' + path}"


def _invite_status(row: dict) -> str:
    if row["revoked"]:
        return "revoked"
    if row["used_count"] >= row["max_uses"]:
        return "used_up"
    expires = row["expires_at"]
    if expires:
        try:
            if datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
                return "expired"
        except ValueError:
            return "expired"
    return "active"


def _public_invite(row: dict) -> dict:
    return {
        "token": row["token"],
        "url": _invite_url(row["token"]),
        "status": _invite_status(row),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "max_uses": row["max_uses"],
        "used_count": row["used_count"],
        "note": row["note"],
    }


def _parse_invite_link(raw: str) -> tuple[str, str]:
    """Pull (domain, token) out of an invite link the user pasted."""
    s = re.sub(r"^https?://", "", (raw or "").strip())
    s = s.split("?")[0].split("#")[0]
    if "/i/" not in s:
        raise HTTPException(status_code=400, detail="Это не похоже на ссылку-приглашение")
    host, _, token = s.partition("/i/")
    domain = _normalize_domain(host)
    token = token.strip("/").strip()
    if not domain or not _host_re().fullmatch(domain):
        raise HTTPException(status_code=400, detail="Некорректный домен в ссылке")
    if not _INVITE_TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=400, detail="Некорректный код приглашения")
    if SERVER_DOMAIN and domain == SERVER_DOMAIN:
        raise HTTPException(status_code=400, detail="Это ссылка вашего же сервера")
    return domain, token


async def _require_local_user(db, username: str) -> dict:
    cur = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = await cur.fetchone()
    if not row or row["home_server"] or row["blocked"]:
        raise HTTPException(status_code=403, detail="Недоступно для этой учётной записи")
    return dict(row)


def _check_redeem_rate(domain: str) -> None:
    now = time.monotonic()
    bucket = [t for t in _redeem_attempts.get(domain, []) if now - t < _REDEEM_WINDOW_SEC]
    if len(bucket) >= _REDEEM_MAX_ATTEMPTS:
        _redeem_attempts[domain] = bucket
        raise HTTPException(status_code=429, detail="Слишком много попыток, попробуйте позже")
    bucket.append(now)
    _redeem_attempts[domain] = bucket


@router.post("/invites")
async def create_invite(data: dict, username: str = Depends(get_current_user)):
    """Mint an invite link for the calling user."""
    _require_configured()
    db = await get_db()
    await _require_local_user(db, username)

    try:
        ttl_days = int(data.get("ttl_days") or INVITE_DEFAULT_TTL_DAYS)
        max_uses = int(data.get("max_uses") or 1)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректные параметры приглашения")
    if not 1 <= ttl_days <= INVITE_MAX_TTL_DAYS:
        raise HTTPException(status_code=400, detail=f"Срок жизни — от 1 до {INVITE_MAX_TTL_DAYS} дней")
    if not 1 <= max_uses <= INVITE_MAX_USES:
        raise HTTPException(status_code=400, detail=f"Число погашений — от 1 до {INVITE_MAX_USES}")

    cur = await db.execute(
        "SELECT * FROM federation_invites WHERE owner = ? AND revoked = 0", (username,)
    )
    active = [r for r in await cur.fetchall() if _invite_status(dict(r)) == "active"]
    if len(active) >= INVITE_MAX_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail=f"Слишком много активных приглашений (максимум {INVITE_MAX_ACTIVE}), отзовите ненужные",
        )

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    row = {
        "token": token,
        "owner": username,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=ttl_days)).isoformat(),
        "max_uses": max_uses,
        "used_count": 0,
        "revoked": 0,
        "note": (data.get("note") or "").strip()[:200],
    }
    await db.execute(
        "INSERT INTO federation_invites (token, owner, created_at, expires_at, max_uses, used_count, revoked, note) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (row["token"], row["owner"], row["created_at"], row["expires_at"],
         row["max_uses"], 0, 0, row["note"]),
    )
    await db.commit()
    return _public_invite(row)


@router.get("/invites")
async def list_invites(username: str = Depends(get_current_user)):
    """The calling user's own invites."""
    _require_configured()
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM federation_invites WHERE owner = ? ORDER BY created_at DESC", (username,)
    )
    return [_public_invite(dict(r)) for r in await cur.fetchall()]


@router.delete("/invites/{token}")
async def revoke_invite(token: str, username: str = Depends(get_current_user)):
    """Отозвать своё приглашение, а уже недействующее — убрать из списка.

    Два шага, а не один, потому что защищает именно первый: отозванная ссылка
    перестаёт работать, даже если её уже кому-то отправили. Строку после этого
    держать незачем — случайно созданная ссылка иначе остаётся в списке
    навсегда, — поэтому повторное удаление стирает её насовсем. Просроченные и
    исчерпанные удаляются сразу: отзывать в них нечего.
    """
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM federation_invites WHERE token = ? AND owner = ?", (token, username)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Приглашение не найдено")

    if _invite_status(dict(row)) == "active":
        await db.execute("UPDATE federation_invites SET revoked = 1 WHERE token = ?", (token,))
        await db.commit()
        return {"ok": True, "deleted": False}

    await db.execute("DELETE FROM federation_invites WHERE token = ?", (token,))
    await db.commit()
    return {"ok": True, "deleted": True}


@router.post("/invites/redeem")
async def redeem_invite(data: dict, username: str = Depends(get_current_user)):
    """Redeem a link someone sent us, opening a conversation with its owner.

    Runs on the *recipient's* server: we authenticate our own user normally,
    then make one authenticated server-to-server call to the owner's server.
    """
    from routes.channels import get_or_create_direct

    _require_configured()
    db = await get_db()
    me = await _require_local_user(db, username)
    domain, token = _parse_invite_link(data.get("link") or "")

    peer = await _get_peer(db, domain)
    if not peer or peer["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail=f"Сервер {domain} не связан с нашим — попросите администратора добавить его",
        )

    try:
        resp = await peer_post(domain, "/api/federation/invite/redeem", {
            "token": token,
            "remote_username": username,
            "display_name": me["display_name"] or username,
            "avatar_url": _absolute_url(me["avatar_path"]),
        })
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail=f"Сервер {domain} недоступен")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Приглашение недействительно")
    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Слишком много попыток, попробуйте позже")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Сервер {domain} отклонил приглашение")

    owner = resp.json().get("owner") or {}
    owner_name = (owner.get("username") or "").strip().lower()
    if not owner_name:
        raise HTTPException(status_code=502, detail="Некорректный ответ сервера")

    uid = await ensure_remote_user(
        domain, owner_name, owner.get("display_name", ""), owner.get("avatar_url", "")
    )
    channel_id, _ = await get_or_create_direct(db, username, uid)
    return {"ok": True, "channel_id": channel_id, "user": {"username": uid, "domain": domain}}


@router.post("/federation/invite/redeem")
async def federation_invite_redeem(data: dict, peer: str = Depends(require_peer)):
    """A peer redeems one of our users' invite links on behalf of its user.

    Every rejection returns the same 404: distinguishing "no such token" from
    "expired" or "already used" would let a peer probe for valid tokens. The
    owner's profile is returned only on success, for the same reason.
    """
    from routes.channels import get_or_create_direct

    _check_redeem_rate(peer)
    token = (data.get("token") or "").strip()
    remote_username = (data.get("remote_username") or "").strip().lower()
    if not _INVITE_TOKEN_RE.fullmatch(token) or not remote_username:
        raise HTTPException(status_code=404, detail="Приглашение недействительно")

    db = await get_db()
    cur = await db.execute("SELECT * FROM federation_invites WHERE token = ?", (token,))
    row = await cur.fetchone()
    if not row or _invite_status(dict(row)) != "active":
        raise HTTPException(status_code=404, detail="Приглашение недействительно")

    invite = dict(row)
    cur = await db.execute("SELECT * FROM users WHERE username = ?", (invite["owner"],))
    owner_row = await cur.fetchone()
    if not owner_row or owner_row["home_server"] or owner_row["blocked"]:
        raise HTTPException(status_code=404, detail="Приглашение недействительно")
    owner = dict(owner_row)

    uid = await ensure_remote_user(
        peer, remote_username, data.get("display_name", ""), data.get("avatar_url", "")
    )
    channel_id, created = await get_or_create_direct(db, owner["username"], uid)
    await db.execute(
        "UPDATE federation_invites SET used_count = used_count + 1 WHERE token = ?", (token,)
    )
    await db.commit()

    if created:
        await manager.send_to_user(owner["username"], {
            "event": "federation_invite_redeemed",
            "channel_id": channel_id,
            "domain": peer,
            "user": {"username": uid, "display_name": data.get("display_name", "") or remote_username},
        })

    return {
        "ok": True,
        "owner": {
            "username": owner["username"],
            "display_name": owner["display_name"] or owner["username"],
            "avatar_url": _absolute_url(owner["avatar_path"]),
        },
        "channel_id": channel_id,
    }


# ── Message delivery ─────────────────────────────────────────────────────────
#
# A conversation with a remote user exists on both servers; each keeps its own
# copy of the messages. Sending pushes the message to the peer, which files it
# under its own copy of the same conversation.
#
# Delivery is queued rather than awaited inline: a peer being down must not
# fail the send for the local user, who has already got their message stored.

_OUTBOX_TICK_SEC = 15.0
_OUTBOX_MAX_ATTEMPTS = 12
_OUTBOX_BATCH = 25


def _backoff_seconds(attempts: int) -> float:
    """1m, 2m, 4m … capped at an hour."""
    return min(60.0 * (2 ** max(0, attempts - 1)), 3600.0)


async def sync_channel_to_peers(db, channel_id: str) -> None:
    """Tell every participating server who is in this channel.

    Called after membership changed. Each peer gets the full member list rather
    than a diff: the list is small, and a diff would need ordering guarantees
    the outbox does not give. Never raises — a peer being unreachable must not
    fail the local change; the next membership edit re-sends the whole picture.
    """
    if not SERVER_DOMAIN:
        return
    try:
        cur = await db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
        ch = await cur.fetchone()
        if not ch or ch["type"] == "direct":
            return
        # A mirror is not ours to describe: the owner is the one who tells
        # everybody the composition, or two servers would fight over it.
        if (ch["home_server"] or ""):
            return

        cur = await db.execute(
            "SELECT u.username, u.home_server, u.remote_username, u.display_name, u.avatar_path "
            "FROM channel_members cm JOIN users u ON u.username = cm.username "
            "WHERE cm.channel_id = ?",
            (channel_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        domains = {r["home_server"] for r in rows if r["home_server"]}
        if not domains:
            return

        for domain in domains:
            # Each peer is addressed in its own names: their own people by bare
            # username, everyone else qualified by the domain they live on.
            members = []
            for r in rows:
                if r["home_server"] == domain:
                    continue
                members.append({
                    "username": r["remote_username"] or r["username"],
                    "home_server": r["home_server"] or SERVER_DOMAIN,
                    "display_name": r["display_name"] or "",
                    "avatar_url": _absolute_url(r["avatar_path"] or ""),
                })
            for r in rows:
                if r["home_server"] != domain:
                    continue
                payload = {
                    "channel_id": channel_id,
                    "for_user": r["remote_username"] or r["username"],
                    "name": ch["name"] or "",
                    "description": ch["description"] or "",
                    "created_by": ch["created_by"] or "",
                    "members": members,
                }
                # Путь именно с /api: _peer_url ничего не подставляет, а без
                # префикса запрос уходит в nginx на статику — тот отвечает
                # страницей, и зеркало канала не создаётся никогда.
                try:
                    resp = await peer_post(domain, "/api/federation/channel/sync", payload)
                    if resp.status_code != 200:
                        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    await _rearm_channel_outbox(db, domain, channel_id)
                except Exception as e:
                    # Молча терять состав канала нельзя: у соседа не появится ни
                    # канала, ни возможности принять в него сообщение. Кладём в
                    # ту же очередь, что и сообщения, и повторяем с отступом.
                    print(f"[federation] channel sync to {domain} failed: {e}")
                    await _queue_channel_sync(db, domain, payload)
    except Exception as e:
        print(f"[federation] sync_channel_to_peers failed: {e}")


async def push_profile_to_peers(db, username: str) -> None:
    """Разослать соседям новое имя, ник и аватар нашего пользователя.

    Без этого удалённая карточка застывает на том, чем была в момент, когда
    сосед впервые о человеке услышал: имя приходит попутно с сообщениями, и
    смена имени доезжает только со следующим из них — а до тех пор собеседник
    подписан по-старому у всех, кто на другом сервере.

    Соседи — те, с кем есть хоть один общий разговор. Каталога пользователей
    федерация не держит, и рассылать профиль дальше этого круга незачем.
    Никогда не поднимает исключений: правка своего профиля не должна падать
    из-за недоступного соседа.
    """
    if not SERVER_DOMAIN:
        return
    try:
        cur = await db.execute(
            "SELECT display_name, nickname, avatar_path, home_server FROM users WHERE username = ?",
            (username,),
        )
        me = await cur.fetchone()
        if not me or me["home_server"]:
            return  # чужой профиль не нам рассылать

        cur = await db.execute(
            "SELECT DISTINCT u.home_server FROM channel_members mine "
            "JOIN channel_members other ON other.channel_id = mine.channel_id "
            "  AND other.username != mine.username "
            "JOIN users u ON u.username = other.username "
            "WHERE mine.username = ? AND u.home_server != ''",
            (username,),
        )
        domains = [r["home_server"] for r in await cur.fetchall()]
        if not domains:
            return

        payload = {
            "username": username,
            "display_name": me["display_name"] or username,
            "nickname": me["nickname"] or "",
            "avatar_url": _absolute_url(me["avatar_path"] or ""),
        }
        for domain in domains:
            try:
                resp = await peer_post(domain, "/api/federation/profile", payload)
                if resp.status_code != 200:
                    print(f"[federation] profile push to {domain}: HTTP {resp.status_code}")
            except Exception as e:
                print(f"[federation] profile push to {domain} failed: {e}")
    except Exception as e:
        print(f"[federation] push_profile_to_peers failed: {e}")


async def _rearm_channel_outbox(db, domain: str, channel_id: str) -> None:
    """Вернуть в очередь сообщения, отвергнутые до появления зеркала канала.

    Сосед отвечает 403 на сообщение в канал, о котором не знает, и доставка
    считает такой отказ окончательным — справедливо, пока причина не устранена.
    Синхронизация состава её и устраняет, так что отвергнутое имеет смысл
    отправить ещё раз: иначе переписка, случившаяся до починки, пропадает.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            "UPDATE federation_outbox SET attempts = 0, next_attempt = ?, last_error = '' "
            "WHERE delivered_at = '' AND next_attempt = '' AND domain = ? AND channel_id = ?",
            (now, domain, channel_id),
        )
        await db.commit()
        asyncio.create_task(_flush_soon())
    except Exception as e:
        print(f"[federation] re-arming outbox for {domain} failed: {e}")


async def _queue_channel_sync(db, domain: str, payload: dict) -> None:
    """Отложить состав канала на повтор через общий outbox.

    Ключ строки — канал, сосед и адресат, а не момент времени: свежая картина
    состава заменяет прежнюю, накапливать историю попыток незачем.
    """
    now = datetime.now(timezone.utc).isoformat()
    key = f"sync:{payload['channel_id']}:{domain}:{payload['for_user']}"
    try:
        await db.execute("DELETE FROM federation_outbox WHERE id = ?", (key,))
        await db.execute(
            "INSERT INTO federation_outbox "
            "(id, domain, path, channel_id, payload, attempts, next_attempt, created_at) "
            "VALUES (?,?,?,?,?,0,?,?)",
            (key, domain, "/api/federation/channel/sync", payload["channel_id"],
             json.dumps(payload, ensure_ascii=False), now, now),
        )
        await db.commit()
    except Exception as e:
        print(f"[federation] queueing channel sync to {domain} failed: {e}")


async def queue_message_for_peers(db, channel_id: str, msg: dict, sender: str) -> None:
    """Queue `msg` for every remote member of this channel.

    Called after the message is already committed locally. Never raises: a
    federation problem must not turn into a failed send.
    """
    if not SERVER_DOMAIN:
        return
    try:
        cur = await db.execute(
            "SELECT u.username, u.home_server, u.remote_username FROM channel_members cm "
            "JOIN users u ON u.username = cm.username "
            "WHERE cm.channel_id = ? AND u.home_server != ''",
            (channel_id,),
        )
        remotes = [dict(r) for r in await cur.fetchall()]
        if not remotes:
            return

        cur = await db.execute(
            "SELECT display_name, avatar_path FROM users WHERE username = ?", (sender,)
        )
        me = await cur.fetchone()
        now = datetime.now(timezone.utc)

        for r in remotes:
            payload = {
                "id": msg["id"],
                "to": r["remote_username"],
                "from": sender,
                "display_name": (me["display_name"] if me else "") or sender,
                "avatar_url": _absolute_url(me["avatar_path"] if me else ""),
                "type": msg.get("type", "text"),
                "text": msg.get("text", ""),
                "timestamp": msg.get("timestamp") or now.isoformat(),
                # Which conversation this belongs to. A direct chat can be
                # inferred from the pair of people, a group cannot — there the
                # id is the only thing that says where the message goes. Older
                # peers ignore the field and keep inferring.
                "channel_id": channel_id,
            }
            await db.execute(
                    "INSERT OR IGNORE INTO federation_outbox "
                "(id, domain, channel_id, payload, attempts, next_attempt, created_at) "
                "VALUES (?,?,?,?,0,?,?)",
                (
                    # Keyed by recipient, not just domain: a group can hold two
                    # people on the same peer, and one row per domain would let
                    # INSERT OR IGNORE silently drop the second delivery.
                    f"{msg['id']}:{r['home_server']}:{r['remote_username'] or r['username']}",
                    r["home_server"],
                    channel_id,
                    json.dumps(payload, ensure_ascii=False),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        await db.commit()
        # Kick delivery now rather than waiting for the next tick; anything
        # that fails here stays queued and is retried by run_outbox_loop.
        asyncio.create_task(_flush_soon())
    except Exception as e:
        print(f"[federation] queueing failed for channel {channel_id}: {e}")


async def _deliver_one(db, row: dict) -> None:
    """Attempt one outbox row, updating it with the outcome."""
    attempts = row["attempts"] + 1
    now = datetime.now(timezone.utc)
    try:
        # Очередь возит не только сообщения: строка знает свой путь. Пустой —
        # это ряд, созданный до появления колонки, и он всегда про сообщение.
        path = row.get("path") or "/api/federation/message"
        resp = await peer_post(row["domain"], path, json.loads(row["payload"]))
        # 4xx other than 429 means the peer will never accept it — retrying
        # would just burn attempts, so stop and keep the error for diagnosis.
        if resp.status_code == 200:
            await db.execute(
                "UPDATE federation_outbox SET delivered_at = ?, attempts = ?, last_error = '' WHERE id = ?",
                (now.isoformat(), attempts, row["id"]),
            )
            await db.commit()
            return
        permanent = 400 <= resp.status_code < 500 and resp.status_code != 429
        error = f"HTTP {resp.status_code}"
    except Exception as e:
        permanent = False
        error = str(e)[:200]

    if permanent or attempts >= _OUTBOX_MAX_ATTEMPTS:
        # Give up: leave it undelivered with the reason recorded, and stop
        # scheduling it by pushing next_attempt far out.
        await db.execute(
            "UPDATE federation_outbox SET attempts = ?, last_error = ?, next_attempt = '' WHERE id = ?",
            (attempts, error, row["id"]),
        )
    else:
        nxt = now + timedelta(seconds=_backoff_seconds(attempts))
        await db.execute(
            "UPDATE federation_outbox SET attempts = ?, last_error = ?, next_attempt = ? WHERE id = ?",
            (attempts, error, nxt.isoformat(), row["id"]),
        )
    await db.commit()


async def _flush_due() -> None:
    """Deliver every queued message whose next attempt is due."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM federation_outbox WHERE delivered_at = '' AND next_attempt != '' "
        "AND next_attempt <= ? ORDER BY next_attempt LIMIT ?",
        (datetime.now(timezone.utc).isoformat(), _OUTBOX_BATCH),
    )
    for row in [dict(r) for r in await cur.fetchall()]:
        await _deliver_one(db, row)


async def _flush_soon() -> None:
    """Best-effort immediate flush, so a normal message is not held for a tick."""
    try:
        await _flush_due()
    except Exception as e:
        print(f"[federation] immediate flush failed: {e}")


async def run_outbox_loop() -> None:
    """Background task — retry queued messages until they land or expire."""
    while True:
        try:
            await asyncio.sleep(_OUTBOX_TICK_SEC)
            if not SERVER_DOMAIN:
                continue
            await _flush_due()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[federation] outbox loop error: {e}")


@router.get("/federation/contacts")
async def federation_contacts(username: str = Depends(get_current_user)):
    """People from other servers this user may add to a channel.

    Exactly those they already have a direct conversation with. There is no
    directory of remote users — `/users` hides the stubs on purpose — so the
    conversations someone has already accepted are the only honest source for
    a picker, and they match what the server will allow on the way in.
    """
    db = await get_db()
    cur = await db.execute(
        """SELECT u.username, u.display_name, u.avatar_path, u.home_server
           FROM channel_members me
           JOIN channels c ON c.id = me.channel_id AND c.type = 'direct'
           JOIN channel_members other ON other.channel_id = c.id AND other.username != me.username
           JOIN users u ON u.username = other.username
           WHERE me.username = ? AND u.home_server != '' AND u.blocked = 0
           ORDER BY u.display_name""",
        (username,),
    )
    return [dict(r) for r in await cur.fetchall()]


@router.post("/federation/channel/sync")
async def federation_channel_sync(data: dict, peer: str = Depends(require_peer)):
    """A peer tells us one of our users belongs to a channel of theirs.

    Group federation is a mesh, not a hub: every participating server keeps its
    own row for the channel under the *same id*, and each of them fans a local
    message out to all the other members' servers. That only works if everyone
    agrees on the id and on who is in the room, which is what this call
    establishes.

    Direct chats do not come through here — they are opened by redeeming an
    invite, and the pair of people is enough to identify them.
    """
    import uuid as _uuid
    from routes.channels import _ensure_unique_slug

    channel_id = (data.get("channel_id") or "").strip()
    for_user = (data.get("for_user") or "").strip().lower()
    members = data.get("members") or []
    if not channel_id or not for_user:
        raise HTTPException(status_code=400, detail="channel_id и for_user обязательны")

    db = await get_db()
    cur = await db.execute("SELECT * FROM users WHERE username = ?", (for_user,))
    local = await cur.fetchone()
    if not local or local["home_server"] or local["blocked"]:
        raise HTTPException(status_code=404, detail="Получатель не найден")

    # The channel is theirs. We never let a peer claim one that lives here or
    # that another domain already owns — the id would collide with a real
    # conversation and messages would land in it.
    cur = await db.execute("SELECT home_server FROM channels WHERE id = ?", (channel_id,))
    existing = await cur.fetchone()
    if existing and (existing["home_server"] or "") != peer:
        raise HTTPException(status_code=409, detail="Канал уже существует и принадлежит другому серверу")

    now = now_iso()
    name = (data.get("name") or "").strip()[:100]
    if not existing:
        slug = await _ensure_unique_slug(db, f"fed-{channel_id[:8]}")
        await db.execute(
            "INSERT INTO channels (id, name, slug, type, description, created_by, created_at, home_server) "
            "VALUES (?,?,?,'private',?,?,?,?)",
            (channel_id, name, slug, (data.get("description") or "").strip()[:500],
             remote_id(peer, (data.get("created_by") or "").strip().lower() or "unknown"), now, peer),
        )
    else:
        await db.execute("UPDATE channels SET name = ? WHERE id = ?", (name, channel_id))

    # Everyone in the room gets a row here: our own user as themselves, people
    # on the owner's server and on any third server as stubs. Without the third
    # parties our fan-out would silently skip them.
    await db.execute(
        "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?,?,'member',?)",
        (channel_id, for_user, now),
    )
    for m in members:
        bare = (m.get("username") or "").strip().lower()
        home = _normalize_domain(m.get("home_server") or peer)
        if not bare:
            continue
        if home == SERVER_DOMAIN:
            # Someone of ours, named from the peer's point of view.
            uid = bare
            cur = await db.execute("SELECT 1 FROM users WHERE username = ? AND home_server = ''", (uid,))
            if not await cur.fetchone():
                continue
        else:
            uid = await ensure_remote_user(home, bare, m.get("display_name", ""), m.get("avatar_url", ""))
        await db.execute(
            "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?,?,'member',?)",
            (channel_id, uid, now),
        )
    await db.commit()

    # Same shape the local paths use — clients key off "event" and expect the
    # whole channel, not an id they would have to go and fetch.
    from routes.channels import _build_channel
    cur = await db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
    row = await cur.fetchone()
    if row:
        ch = await _build_channel(db, row, for_user)
        await manager.send_to_user(for_user, {"event": "channel_created", "channel": ch})
    return {"ok": True, "channel_id": channel_id}


@router.post("/federation/profile")
async def federation_profile(data: dict, peer: str = Depends(require_peer)):
    """Сосед сообщает, что у его пользователя сменились имя, ник или аватар.

    Обновляем только тех, кого уже знаем: заводить человека по такому поводу
    значило бы принимать чужой каталог пользователей, которого федерация
    намеренно не имеет.
    """
    bare = (data.get("username") or "").strip().lower()
    if not bare:
        raise HTTPException(status_code=400, detail="username обязателен")

    db = await get_db()
    uid = remote_id(peer, bare)
    cur = await db.execute("SELECT 1 FROM users WHERE username = ?", (uid,))
    if not await cur.fetchone():
        return {"ok": True, "unknown": True}

    await ensure_remote_user(peer, bare, data.get("display_name", ""), data.get("avatar_url", ""))
    await db.execute(
        "UPDATE users SET nickname = ? WHERE username = ?",
        ((data.get("nickname") or "").strip()[:50], uid),
    )
    await db.commit()

    # Открытым клиентам — тем же событием, что и правку своего профиля: списки
    # и шапки чатов слушают именно его.
    from routes.auth import _user_public
    cur = await db.execute("SELECT * FROM users WHERE username = ?", (uid,))
    row = await cur.fetchone()
    if row:
        await manager.broadcast_all({"event": "user_updated", "user": _user_public(dict(row))})
    return {"ok": True}


@router.post("/federation/message")
async def federation_message(data: dict, peer: str = Depends(require_peer)):
    """Accept a message from a federated peer.

    The conversation must already exist here: it is created when an invite is
    redeemed, and that redemption is the only thing that authorises someone on
    another server to write to a local user. Delivering into a conversation we
    would have to invent would turn federation into open messaging.
    """
    from routes.channels import find_direct
    from routes.messages import _get_members

    # Blocking is not part of every build (the open-source packaging ships
    # without routes/moderation). Absent the module, nobody has blocked anyone.
    try:
        from routes.moderation import get_blockers_of
    except ImportError:
        async def get_blockers_of(_db, _username):
            return []

    msg_id = (data.get("id") or "").strip()
    to_user = (data.get("to") or "").strip().lower()
    from_user = (data.get("from") or "").strip().lower()
    text = data.get("text") or ""
    if not msg_id or not to_user or not from_user:
        raise HTTPException(status_code=400, detail="Некорректное сообщение")

    db = await get_db()
    cur = await db.execute("SELECT * FROM users WHERE username = ?", (to_user,))
    recipient = await cur.fetchone()
    if not recipient or recipient["home_server"] or recipient["blocked"]:
        raise HTTPException(status_code=404, detail="Получатель не найден")

    uid = await ensure_remote_user(
        peer, from_user, data.get("display_name", ""), data.get("avatar_url", "")
    )
    # A group names its channel; a direct chat is still inferred from the pair,
    # both because older peers send nothing else and because there is exactly
    # one such conversation. The claim of membership is never taken on trust:
    # the sender must already be in the channel here, which only happens after
    # a sync we accepted.
    claimed = (data.get("channel_id") or "").strip()
    if claimed:
        channel_id = await _shared_channel(db, claimed, to_user, uid)
        if not channel_id:
            raise HTTPException(status_code=403, detail="Канал недоступен — участие не подтверждено")
    else:
        channel_id = await find_direct(db, to_user, uid)
        if not channel_id:
            raise HTTPException(status_code=403, detail="Диалог не открыт — нужно приглашение")

    # A block is enforced silently: we accept the message so the sender's
    # server stops retrying, but nothing is stored or shown.
    if to_user in await get_blockers_of(db, uid):
        return {"ok": True, "dropped": True}

    now = data.get("timestamp") or now_iso()
    display = (data.get("display_name") or "").strip() or from_user
    msg_type = data.get("type") or "text"

    # INSERT OR IGNORE makes redelivery harmless — the sender owns the id, and
    # retries after a timeout are expected.
    cur = await db.execute(
        "INSERT OR IGNORE INTO messages (id, channel_id, sender, sender_name, type, text, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        (msg_id, channel_id, uid, display, msg_type, text, now),
    )
    if cur.rowcount == 0:
        return {"ok": True, "duplicate": True}

    await db.execute(
        "UPDATE channels SET last_msg_text = ?, last_msg_sender = ?, last_msg_sender_name = ?, "
        "last_msg_timestamp = ? WHERE id = ?",
        (text, uid, display, now, channel_id),
    )
    await db.commit()

    cur = await db.execute("SELECT slug, name FROM channels WHERE id = ?", (channel_id,))
    ch = await cur.fetchone()
    slug = ch["slug"] if ch else channel_id

    msg = {
        "id": msg_id,
        "channel_id": channel_id,
        "sender": uid,
        "sender_name": display,
        "sender_avatar": data.get("avatar_url", ""),
        "type": msg_type,
        "text": text,
        "timestamp": now,
        "read_by": [],
        "reactions": [],
        "reply_count": 0,
        "remote_domain": peer,
    }
    members = await _get_members(db, channel_id)
    await manager.send_to_channel(members, {"event": "new_message", "channel_slug": slug, "message": msg})

    if not manager.is_online(to_user):
        from routes.push import send_push_to_user
        await send_push_to_user(to_user, {
            "type": "message",
            "title": display,
            "body": (text or "")[:100] or "Новое сообщение",
            "conv_id": slug,
            "badge": 1,
        })

    return {"ok": True}


# ── Calls across servers ─────────────────────────────────────────────────────
#
# A LiveKit room lives on exactly one server — the one that started the call —
# because the join token is signed with that server's LiveKit secret and no
# peer can forge it. So the room's server mints a token for every participant,
# local or remote, and the remote participant's browser dials that server's
# LiveKit directly. The peer only relays signalling.
#
# Authorisation is the same as for messages: there must already be a
# conversation between the two people, which only a redeemed invite creates.


async def _shared_channel(db, channel_id: str, local_user: str, remote_uid: str) -> str | None:
    """The channel both of them actually belong to, or None.

    Membership is checked for the sender as well as the recipient. Without that
    a peer could name any channel id it happened to learn and post into a
    conversation its user was never part of — the id travels in the message and
    is not a secret.
    """
    cur = await db.execute(
        "SELECT COUNT(*) FROM channel_members WHERE channel_id = ? AND username IN (?, ?)",
        (channel_id, local_user, remote_uid),
    )
    row = await cur.fetchone()
    return channel_id if row and row[0] == 2 else None


async def remote_peer_of(db, username: str) -> str | None:
    """Domain this user lives on, or None if they are local to us."""
    cur = await db.execute("SELECT home_server FROM users WHERE username = ?", (username,))
    row = await cur.fetchone()
    return (row["home_server"] or None) if row else None


async def push_call_event(domain: str, path: str, payload: dict) -> bool:
    """Relay one call signalling event to a peer. Never raises."""
    try:
        resp = await peer_post(domain, path, payload)
        if resp.status_code != 200:
            print(f"[federation] call event {path} to {domain}: HTTP {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[federation] call event {path} to {domain} failed: {e}")
        return False


async def _authorised_channel(
    db, local_user: str, remote_uid: str, channel_id: str = ""
) -> str | None:
    """The conversation that authorises these two to reach each other.

    A direct chat authorises a one-to-one call. For a group call the room is
    the named channel, and what has to be true is that both of them are in it —
    people meet in a group without ever having opened a private conversation,
    so requiring one would make federated group calls impossible.
    """
    from routes.channels import find_direct
    if channel_id:
        shared = await _shared_channel(db, channel_id, local_user, remote_uid)
        if shared:
            return shared
    return await find_direct(db, local_user, remote_uid)


@router.post("/federation/call/invite")
async def federation_call_invite(data: dict, peer: str = Depends(require_peer)):
    """A peer's user is calling one of ours. We only relay — the room is theirs."""
    from routes.videocall import register_incoming_federated_call

    call_id = (data.get("call_id") or "").strip()
    callee = (data.get("callee") or "").strip().lower()
    caller = (data.get("caller") or "").strip().lower()
    room_name = (data.get("room_name") or "").strip()
    if not all((call_id, callee, caller, room_name)):
        raise HTTPException(status_code=400, detail="Некорректное приглашение в звонок")

    db = await get_db()
    cur = await db.execute("SELECT * FROM users WHERE username = ?", (callee,))
    local = await cur.fetchone()
    if not local or local["home_server"] or local["blocked"]:
        raise HTTPException(status_code=404, detail="Получатель не найден")

    uid = await ensure_remote_user(peer, caller, data.get("caller_name", ""), data.get("avatar_url", ""))
    channel_id = await _authorised_channel(db, callee, uid)
    if not channel_id:
        raise HTTPException(status_code=403, detail="Диалог не открыт — нужно приглашение")

    return await register_incoming_federated_call(
        db, call_id=call_id, channel_id=channel_id, room_name=room_name,
        caller_uid=uid, caller_name=data.get("caller_name", "") or caller, callee=callee,
        mode=data.get("mode", "audio"), expires_at=data.get("expires_at", ""),
        domain=peer, livekit_url=data.get("livekit_url", ""),
        livekit_token=data.get("livekit_token", ""),
    )


@router.post("/federation/call/state")
async def federation_call_state(data: dict, peer: str = Depends(require_peer)):
    """Relay answer / reject / end for a call we share with this peer."""
    from routes.videocall import apply_federated_call_state

    call_id = (data.get("call_id") or "").strip()
    state = (data.get("state") or "").strip()
    if not call_id or state not in ("answered", "rejected", "ended"):
        raise HTTPException(status_code=400, detail="Некорректное состояние звонка")

    db = await get_db()
    cur = await db.execute("SELECT * FROM calls WHERE id = ? AND remote_domain = ?", (call_id, peer))
    call = await cur.fetchone()
    if not call:
        raise HTTPException(status_code=404, detail="Звонок не найден")
    return await apply_federated_call_state(db, dict(call), state, data.get("reason", ""))


@router.post("/federation/call/conference")
async def federation_call_conference(data: dict, peer: str = Depends(require_peer)):
    """A peer adds one of our users to a call already running on their server.

    No local call row: joining a conference is stateless for us, we just hand
    the invitation to the user. The conversation check still applies, so only
    someone they already accepted can pull them in.
    """
    callee = (data.get("callee") or "").strip().lower()
    inviter = (data.get("inviter") or "").strip().lower()
    room_name = (data.get("room_name") or "").strip()
    if not all((callee, inviter, room_name)):
        raise HTTPException(status_code=400, detail="Некорректное приглашение в конференцию")

    db = await get_db()
    cur = await db.execute("SELECT * FROM users WHERE username = ?", (callee,))
    local = await cur.fetchone()
    if not local or local["home_server"] or local["blocked"]:
        raise HTTPException(status_code=404, detail="Получатель не найден")

    uid = await ensure_remote_user(peer, inviter, data.get("inviter_name", ""))
    if not await _authorised_channel(db, callee, uid, (data.get("channel_id") or "").strip()):
        raise HTTPException(status_code=403, detail="Нет общего канала или диалога")

    await manager.send_to_user(callee, {
        "event": "call_started",
        "channel_id": data.get("channel_id", ""),
        "channel_slug": data.get("channel_slug", ""),
        "room_name": room_name,
        "started_by": uid,
        "started_by_name": data.get("inviter_name", "") or inviter,
        "livekit_token": data.get("livekit_token", ""),
        "livekit_url": data.get("livekit_url", ""),
        "remote_domain": peer,
    })
    return {"ok": True}
