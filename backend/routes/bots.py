"""Боты и входящие вебхуки.

Бот — особый пользователь (role = 'bot'): без входа по паролю, с токеном для
Bot API. Видит только каналы, куда его добавили, и личку, которую ему
написали. Подробно — docs/bots.md.

Bot API (заголовок `Authorization: Bearer zb_...`):
    GET    /api/bot/me
    GET    /api/bot/channels
    POST   /api/bot/channels/{slug}/messages        {text, reply_to?}
    PUT    /api/bot/channels/{slug}/messages/{id}   {text}
    DELETE /api/bot/channels/{slug}/messages/{id}
    POST   /api/bot/direct                          {username, text}
    GET    /api/bot/updates?offset=&timeout=        долгий опрос

Если у бота задан webhook_url, каждое обновление ещё и отправляется туда
POST-запросом; очередь для опроса при этом тоже пополняется.

Входящий вебхук — адрес /api/hooks/{token}: POST с {"text": "..."} (или
Slack-совместимым payload) публикует сообщение в канал от имени бота.
"""

import asyncio
import hashlib
import json
import secrets
import time
from collections import deque

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from database import get_db
from helpers import get_current_user, require_admin, hash_password, now_iso

router = APIRouter(prefix="/api", tags=["bots"])

RATE_LIMIT = 30          # сообщений
RATE_WINDOW = 60.0       # за столько секунд
QUEUE_LIMIT = 1000       # обновлений в очереди на бота
POLL_MAX_SEC = 50

_rate: dict[str, deque] = {}
_waiters: dict[str, asyncio.Event] = {}
_push_tasks: set = set()


# ── Хранение ─────────────────────────────────────────────────────

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS bots (
        username     TEXT PRIMARY KEY,
        owner        TEXT NOT NULL DEFAULT '',
        description  TEXT NOT NULL DEFAULT '',
        token_hash   TEXT NOT NULL,
        webhook_url  TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS bot_updates (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        bot         TEXT NOT NULL,
        payload     TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_bot_updates ON bot_updates(bot, id)",
    """CREATE TABLE IF NOT EXISTS channel_webhooks (
        id          TEXT PRIMARY KEY,
        token_hash  TEXT NOT NULL UNIQUE,
        channel_id  TEXT NOT NULL,
        bot         TEXT NOT NULL,
        name        TEXT NOT NULL DEFAULT '',
        created_by  TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS bot_audit (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        bot         TEXT NOT NULL,
        action      TEXT NOT NULL,
        detail      TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_bot_audit ON bot_audit(bot, id)",
]


async def init_schema(db) -> None:
    for ddl in SCHEMA:
        await db.execute(ddl)
    await db.commit()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


async def _audit(db, bot: str, action: str, detail: str = "") -> None:
    await db.execute(
        "INSERT INTO bot_audit (bot, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (bot, action, detail[:500], now_iso()),
    )


def _check_rate(bot: str) -> None:
    now = time.monotonic()
    q = _rate.setdefault(bot, deque())
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail=f"Не больше {RATE_LIMIT} сообщений в минуту")
    q.append(now)


async def is_bot(db, username: str) -> bool:
    cur = await db.execute("SELECT role FROM users WHERE username = ?", (username,))
    row = await cur.fetchone()
    return bool(row and row["role"] == "bot")


# ── Авторизация бота ─────────────────────────────────────────────

async def get_bot(authorization: str = Header("")) -> str:
    token = authorization.removeprefix("Bearer ").removeprefix("Bot ").strip()
    if not token.startswith("zb_"):
        raise HTTPException(status_code=401, detail="Нужен токен бота: Authorization: Bearer zb_…")
    db = await get_db()
    cur = await db.execute(
        "SELECT b.username, u.blocked FROM bots b JOIN users u ON u.username = b.username WHERE b.token_hash = ?",
        (_hash(token),),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Неизвестный токен бота")
    if row["blocked"]:
        raise HTTPException(status_code=403, detail="Бот отключён")
    return row["username"]


# ── Обновления для ботов ─────────────────────────────────────────

async def on_new_message(db, channel: dict, msg: dict, members: list[str]) -> None:
    """Положить сообщение в очередь каждому боту-участнику канала.

    Вызывается после рассылки сообщения. Сообщения самого бота ему не
    приходят. Никогда не бросает: бот не должен ломать переписку людей.
    """
    try:
        if not members:
            return
        q = ",".join("?" * len(members))
        cur = await db.execute(
            f"SELECT b.username, b.webhook_url FROM bots b JOIN users u ON u.username = b.username "
            f"WHERE u.blocked = 0 AND b.username IN ({q})",
            members,
        )
        bots = [dict(r) for r in await cur.fetchall()]
        if not bots:
            return
        update_msg = {
            "id": msg.get("id"),
            "channel": {"slug": channel.get("slug"), "name": channel.get("name") or "", "type": channel.get("type")},
            "sender": msg.get("sender"),
            "sender_name": msg.get("sender_name"),
            "type": msg.get("type"),
            "text": msg.get("text") or "",
            "file": msg.get("file"),
            "reply_to": msg.get("reply_to"),
            "timestamp": msg.get("timestamp"),
        }
        for b in bots:
            if b["username"] == msg.get("sender"):
                continue
            cur = await db.execute(
                "INSERT INTO bot_updates (bot, payload, created_at) VALUES (?, ?, ?)",
                (b["username"], json.dumps({"type": "message", "message": update_msg}, ensure_ascii=False), now_iso()),
            )
            update_id = cur.lastrowid
            await db.execute(
                "DELETE FROM bot_updates WHERE bot = ? AND id <= ?",
                (b["username"], update_id - QUEUE_LIMIT),
            )
            ev = _waiters.get(b["username"])
            if ev:
                ev.set()
            if b["webhook_url"]:
                task = asyncio.create_task(_push_webhook(
                    b["webhook_url"], {"update_id": update_id, "type": "message", "message": update_msg}))
                _push_tasks.add(task)
                task.add_done_callback(_push_tasks.discard)
        await db.commit()
    except Exception as e:
        print(f"[bots] on_new_message: {e}")


async def _push_webhook(url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload)
    except Exception as e:
        print(f"[bots] webhook {url}: {e}")


# ── Bot API ──────────────────────────────────────────────────────

@router.get("/bot/me")
async def bot_me(bot: str = Depends(get_bot)):
    db = await get_db()
    cur = await db.execute(
        "SELECT u.username, u.display_name, b.description, b.webhook_url FROM users u JOIN bots b ON b.username = u.username WHERE u.username = ?",
        (bot,),
    )
    return dict(await cur.fetchone())


@router.get("/bot/channels")
async def bot_channels(bot: str = Depends(get_bot)):
    db = await get_db()
    cur = await db.execute(
        """SELECT c.slug, c.name, c.type FROM channels c
           JOIN channel_members cm ON cm.channel_id = c.id WHERE cm.username = ? ORDER BY c.name""",
        (bot,),
    )
    return [dict(r) for r in await cur.fetchall()]


@router.post("/bot/channels/{slug}/messages")
async def bot_send(slug: str, data: dict, bot: str = Depends(get_bot)):
    from routes.messages import _get_channel_by_slug, deliver_message
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, bot)
    text = str(data.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    _check_rate(bot)
    msg = await deliver_message(db, ch, bot, {"text": text[:8000], "type": "text", "reply_to": data.get("reply_to")})
    await _audit(db, bot, "send", f"{slug}:{msg['id']}")
    await db.commit()
    return msg


@router.put("/bot/channels/{slug}/messages/{msg_id}")
async def bot_edit(slug: str, msg_id: str, data: dict, bot: str = Depends(get_bot)):
    from routes.messages import edit_message
    _check_rate(bot)
    res = await edit_message(slug, msg_id, data, username=bot)
    db = await get_db()
    await _audit(db, bot, "edit", f"{slug}:{msg_id}")
    await db.commit()
    return res


@router.delete("/bot/channels/{slug}/messages/{msg_id}")
async def bot_delete(slug: str, msg_id: str, bot: str = Depends(get_bot)):
    from routes.messages import delete_message
    db = await get_db()
    cur = await db.execute("SELECT sender FROM messages WHERE id = ?", (msg_id,))
    row = await cur.fetchone()
    if not row or row["sender"] != bot:
        raise HTTPException(status_code=403, detail="Бот удаляет только свои сообщения")
    res = await delete_message(slug, msg_id, scope="all", username=bot)
    await _audit(db, bot, "delete", f"{slug}:{msg_id}")
    await db.commit()
    return res


@router.post("/bot/direct")
async def bot_direct(data: dict, bot: str = Depends(get_bot)):
    """Написать человеку в личку — только тому, кто уже писал боту."""
    from routes.messages import deliver_message
    from routes.channels import get_or_create_direct
    target = str(data.get("username") or "").strip().lower()
    text = str(data.get("text") or "").strip()
    if not target or not text:
        raise HTTPException(status_code=400, detail="Нужны username и text")
    db = await get_db()
    cur = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members a ON a.channel_id = c.id AND a.username = ?
           JOIN channel_members b ON b.channel_id = c.id AND b.username = ?
           WHERE c.type = 'direct'""",
        (bot, target),
    )
    row = await cur.fetchone()
    if not row:
        # Бот не начинает переписку первым: так его не превратить в рассылку
        # по всей компании. Человек пишет боту — и тогда бот может отвечать.
        raise HTTPException(status_code=403, detail="Человек ещё не писал этому боту")
    _check_rate(bot)
    msg = await deliver_message(db, dict(row), bot, {"text": text[:8000], "type": "text"})
    await _audit(db, bot, "direct", f"{target}:{msg['id']}")
    await db.commit()
    return msg


@router.get("/bot/updates")
async def bot_updates(offset: int = 0, timeout: int = 25, limit: int = 100, bot: str = Depends(get_bot)):
    """Долгий опрос. offset — последний обработанный update_id: всё до него
    включительно удаляется, приходит то, что новее."""
    db = await get_db()
    if offset:
        await db.execute("DELETE FROM bot_updates WHERE bot = ? AND id <= ?", (bot, offset))
        await db.commit()
    timeout = max(0, min(int(timeout), POLL_MAX_SEC))
    limit = max(1, min(int(limit), 100))
    deadline = time.monotonic() + timeout
    while True:
        cur = await db.execute(
            "SELECT id, payload FROM bot_updates WHERE bot = ? AND id > ? ORDER BY id LIMIT ?",
            (bot, offset, limit),
        )
        rows = await cur.fetchall()
        if rows or time.monotonic() >= deadline:
            return [{"update_id": r["id"], **json.loads(r["payload"])} for r in rows]
        ev = _waiters.setdefault(bot, asyncio.Event())
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), timeout=max(0.1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            pass


# ── Управление ботами (админ) ────────────────────────────────────

def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Адрес вебхука должен начинаться с http:// или https://")
    return url[:500]


@router.get("/admin/bots")
async def admin_list_bots(username: str = Depends(get_current_user)):
    await require_admin(username)
    db = await get_db()
    cur = await db.execute(
        """SELECT b.username, u.display_name, b.description, b.webhook_url, b.owner, b.created_at, u.blocked,
                  (SELECT COUNT(*) FROM channel_members cm JOIN channels c ON c.id = cm.channel_id
                    WHERE cm.username = b.username AND c.type != 'direct') AS channels,
                  (SELECT MAX(created_at) FROM bot_audit a WHERE a.bot = b.username) AS last_action
           FROM bots b JOIN users u ON u.username = b.username ORDER BY b.created_at"""
    )
    return [dict(r) for r in await cur.fetchall()]


@router.post("/admin/bots")
async def admin_create_bot(data: dict, username: str = Depends(get_current_user)):
    await require_admin(username)
    from routes.admin import _normalize_username, _validate_username
    name = _normalize_username(data.get("username"))
    err = _validate_username(name)
    if err:
        raise HTTPException(status_code=400, detail=err)
    display = str(data.get("display_name") or "").strip() or name
    db = await get_db()
    cur = await db.execute("SELECT 1 FROM users WHERE username = ?", (name,))
    if await cur.fetchone():
        raise HTTPException(status_code=409, detail="Логин уже занят")
    token = _new_token("zb_")
    now = now_iso()
    # Пароль — случайный и никому не известный: бот входит только токеном,
    # а /login ботов не пускает вовсе.
    await db.execute(
        "INSERT INTO users (username, password, display_name, role, created_at) VALUES (?, ?, ?, 'bot', ?)",
        (name, hash_password(secrets.token_urlsafe(32)), display[:64], now),
    )
    await db.execute(
        "INSERT INTO bots (username, owner, description, token_hash, webhook_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, username, str(data.get("description") or "")[:500], _hash(token), _validate_url(data.get("webhook_url")), now),
    )
    await _audit(db, name, "created", f"by {username}")
    await db.commit()
    return {"ok": True, "username": name, "token": token}


@router.put("/admin/bots/{name}")
async def admin_update_bot(name: str, data: dict, username: str = Depends(get_current_user)):
    await require_admin(username)
    db = await get_db()
    cur = await db.execute("SELECT 1 FROM bots WHERE username = ?", (name,))
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="Бот не найден")
    if "display_name" in data:
        await db.execute("UPDATE users SET display_name = ? WHERE username = ?",
                         (str(data["display_name"] or name).strip()[:64], name))
    if "description" in data:
        await db.execute("UPDATE bots SET description = ? WHERE username = ?", (str(data["description"] or "")[:500], name))
    if "webhook_url" in data:
        await db.execute("UPDATE bots SET webhook_url = ? WHERE username = ?", (_validate_url(data["webhook_url"]), name))
    if "blocked" in data:
        await db.execute("UPDATE users SET blocked = ? WHERE username = ?", (1 if data["blocked"] else 0, name))
    await _audit(db, name, "updated", f"by {username}")
    await db.commit()
    return {"ok": True}


@router.post("/admin/bots/{name}/token")
async def admin_reissue_token(name: str, username: str = Depends(get_current_user)):
    await require_admin(username)
    db = await get_db()
    token = _new_token("zb_")
    cur = await db.execute("UPDATE bots SET token_hash = ? WHERE username = ?", (_hash(token), name))
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Бот не найден")
    await _audit(db, name, "token_reissued", f"by {username}")
    await db.commit()
    return {"ok": True, "token": token}


@router.delete("/admin/bots/{name}")
async def admin_delete_bot(name: str, username: str = Depends(get_current_user)):
    """Отключить бота: токен перестаёт действовать, вебхуки удаляются.
    Его сообщения в переписке остаются."""
    await require_admin(username)
    db = await get_db()
    cur = await db.execute("DELETE FROM bots WHERE username = ?", (name,))
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Бот не найден")
    await db.execute("UPDATE users SET blocked = 1 WHERE username = ?", (name,))
    await db.execute("DELETE FROM channel_webhooks WHERE bot = ?", (name,))
    await db.execute("DELETE FROM bot_updates WHERE bot = ?", (name,))
    await _audit(db, name, "deleted", f"by {username}")
    await db.commit()
    return {"ok": True}


@router.post("/admin/bots/{name}/channels")
async def admin_bot_join(name: str, data: dict, username: str = Depends(get_current_user)):
    """Добавить бота в канал (админ — в любой, не только в свой)."""
    await require_admin(username)
    db = await get_db()
    if not await is_bot(db, name):
        raise HTTPException(status_code=404, detail="Бот не найден")
    slug = str(data.get("channel_slug") or "").strip()
    cur = await db.execute("SELECT id, type FROM channels WHERE slug = ?", (slug,))
    ch = await cur.fetchone()
    if not ch or ch["type"] == "direct":
        raise HTTPException(status_code=404, detail="Канал не найден")
    await db.execute(
        "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
        (ch["id"], name, now_iso()),
    )
    await _audit(db, name, "joined", slug)
    await db.commit()
    return {"ok": True}


# ── Входящие вебхуки ─────────────────────────────────────────────

@router.get("/admin/webhooks")
async def admin_list_webhooks(username: str = Depends(get_current_user)):
    await require_admin(username)
    db = await get_db()
    cur = await db.execute(
        """SELECT w.id, w.name, w.bot, w.created_by, w.created_at, c.slug AS channel_slug, c.name AS channel_name
           FROM channel_webhooks w JOIN channels c ON c.id = w.channel_id ORDER BY w.created_at"""
    )
    return [dict(r) for r in await cur.fetchall()]


@router.post("/admin/webhooks")
async def admin_create_webhook(data: dict, request: Request, username: str = Depends(get_current_user)):
    """Адрес, на который внешний сервис (CI, мониторинг) шлёт POST с текстом,
    и тот появляется в канале от имени бота. Бот — указанный или общий
    «webhook», который создаётся сам при первом вебхуке."""
    await require_admin(username)
    db = await get_db()
    slug = str(data.get("channel_slug") or "").strip()
    cur = await db.execute("SELECT id, type FROM channels WHERE slug = ?", (slug,))
    ch = await cur.fetchone()
    if not ch or ch["type"] == "direct":
        raise HTTPException(status_code=404, detail="Канал не найден")
    bot = str(data.get("bot") or "").strip().lower() or "webhook"
    if not await is_bot(db, bot):
        if bot != "webhook":
            raise HTTPException(status_code=404, detail="Бот не найден")
        cur = await db.execute("SELECT 1 FROM users WHERE username = 'webhook'")
        if await cur.fetchone():
            raise HTTPException(status_code=409, detail="Логин webhook занят обычным пользователем — укажите бота")
        now = now_iso()
        await db.execute(
            "INSERT INTO users (username, password, display_name, role, created_at) VALUES ('webhook', ?, 'Вебхук', 'bot', ?)",
            (hash_password(secrets.token_urlsafe(32)), now),
        )
        await db.execute(
            "INSERT INTO bots (username, owner, description, token_hash, created_at) VALUES ('webhook', ?, ?, ?, ?)",
            (username, "Сообщения от внешних сервисов через входящие вебхуки", _hash(_new_token("zb_")), now),
        )
    await db.execute(
        "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
        (ch["id"], bot, now_iso()),
    )
    token = _new_token("zh_")
    hook_id = secrets.token_hex(8)
    await db.execute(
        "INSERT INTO channel_webhooks (id, token_hash, channel_id, bot, name, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (hook_id, _hash(token), ch["id"], bot, str(data.get("name") or "")[:100], username, now_iso()),
    )
    await _audit(db, bot, "webhook_created", f"{slug} by {username}")
    await db.commit()
    return {"ok": True, "id": hook_id, "bot": bot, "path": f"/api/hooks/{token}", "token": token}


@router.delete("/admin/webhooks/{hook_id}")
async def admin_delete_webhook(hook_id: str, username: str = Depends(get_current_user)):
    await require_admin(username)
    db = await get_db()
    cur = await db.execute("DELETE FROM channel_webhooks WHERE id = ?", (hook_id,))
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Вебхук не найден")
    await db.commit()
    return {"ok": True}


@router.post("/hooks/{token}")
async def incoming_webhook(token: str, request: Request):
    from routes.messages import deliver_message
    db = await get_db()
    cur = await db.execute(
        """SELECT w.bot AS hook_bot, c.* FROM channel_webhooks w JOIN channels c ON c.id = w.channel_id
           WHERE w.token_hash = ?""",
        (_hash(token),),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown webhook")
    bot = row["hook_bot"]
    cur = await db.execute("SELECT blocked FROM users WHERE username = ?", (bot,))
    u = await cur.fetchone()
    if not u or u["blocked"]:
        raise HTTPException(status_code=403, detail="Бот вебхука отключён")

    text = ""
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    try:
        if "json" in ctype:
            body = json.loads(raw or b"{}")
            text = str(body.get("text") or body.get("message") or body.get("content") or "")
        elif "form" in ctype:
            form = await request.form()
            if form.get("payload"):  # Slack-совместимый формат
                text = str(json.loads(form["payload"]).get("text") or "")
            else:
                text = str(form.get("text") or "")
        else:
            text = raw.decode("utf-8", "replace")
    except Exception:
        raise HTTPException(status_code=400, detail="Не разобрать тело запроса: нужен JSON {\"text\": \"...\"}")
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой текст")
    _check_rate(f"hook:{bot}")
    ch = {k: row[k] for k in row.keys() if k != "hook_bot"}
    msg = await deliver_message(db, ch, bot, {"text": text[:8000], "type": "text"})
    await _audit(db, bot, "webhook", f"{ch['slug']}:{msg['id']}")
    await db.commit()
    return {"ok": True, "id": msg["id"]}
