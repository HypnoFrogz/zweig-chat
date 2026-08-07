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

import os
import re
import secrets

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
_TIMEOUT = 10.0


def _normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].strip().rstrip(".")
    return d


def _validate_domain(d: str) -> None:
    if not d or not _DOMAIN_RE.fullmatch(d):
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

    domain, _, secret = value.partition(":")
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
