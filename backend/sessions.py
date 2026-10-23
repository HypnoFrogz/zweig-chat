"""Устройства и сеансы входа.

Каждый вход — строка в user_sessions. Здесь:
  * что за устройство вошло (по данным клиента или User-Agent);
  * мгновенный отзыв: номер сеанса (sid) лежит в токене доступа, а
    отозванные sid держатся в памяти, так что каждый запрос проверяется без
    обращения к базе;
  * при отзыве закрываются WebSocket этого сеанса и удаляются его пуш-токены.

Сервер работает одним процессом uvicorn, поэтому памяти процесса достаточно.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

from helpers import now_iso

# sid → время отзыва. Хранится, пока могут жить выданные этому сеансу токены
# доступа (с запасом); старые записи убирает prune().
REVOKED: dict[str, float] = {}
# sid → открытые WebSocket этого сеанса.
SOCKETS: dict[str, set] = {}

_KEEP_SEC = 2 * 24 * 3600


def is_revoked(sid: str | None) -> bool:
    return bool(sid) and sid in REVOKED


def _mark(sids):
    t = datetime.now(timezone.utc).timestamp()
    for s in sids:
        REVOKED[s] = t


def prune():
    lim = datetime.now(timezone.utc).timestamp() - _KEEP_SEC
    for s in [s for s, t in REVOKED.items() if t < lim]:
        REVOKED.pop(s, None)


async def init_schema(db):
    for table in ("user_devices", "user_voip_devices"):
        cur = await db.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in await cur.fetchall()]
        if "session_id" not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN session_id TEXT")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(username, revoked_at)")
    # Автовыход: через сколько дней без активности вход завершается сам.
    await db.execute(
        """CREATE TABLE IF NOT EXISTS user_session_prefs (
            username       TEXT PRIMARY KEY REFERENCES users(username) ON DELETE CASCADE,
            inactive_days  INTEGER NOT NULL DEFAULT 0
        )"""
    )
    await db.commit()
    # Отозванные недавно сеансы — в память, чтобы их токены не ожили после
    # перезапуска сервера. 'rotated' — старая схема обновления токенов, не отзыв.
    since = (datetime.now(timezone.utc) - timedelta(seconds=_KEEP_SEC)).isoformat()
    cur = await db.execute(
        "SELECT id FROM user_sessions WHERE revoked_at IS NOT NULL AND revoked_at > ? AND revoked_reason != 'rotated'",
        (since,),
    )
    _mark([r[0] for r in await cur.fetchall()])


# ── Что за устройство ─────────────────────────────────────────────

def client_ip(request_or_ws) -> str:
    h = request_or_ws.headers
    ip = (h.get("x-real-ip") or "").strip()
    if not ip:
        ip = (h.get("x-forwarded-for") or "").split(",")[0].strip()
    if not ip and request_or_ws.client:
        ip = request_or_ws.client.host or ""
    return ip[:64]


def _clean(v, n=60) -> str:
    return re.sub(r"[\x00-\x1f<>]", "", str(v or "")).strip()[:n]


def parse_user_agent(ua: str) -> dict:
    ua = ua or ""
    d: dict = {}
    if "Electron/" in ua:
        d["kind"] = "desktop"
        d["app"] = "Zweig Desktop"
    elif ua.startswith("Dart/") or "okhttp" in ua.lower() or "CFNetwork" in ua:
        d["kind"] = "mobile"
    elif ua:
        d["kind"] = "web"
    if "Windows" in ua:
        d["os"] = "Windows"
    elif "iPhone" in ua or "iPad" in ua:
        d["os"] = "iOS"
    elif "Android" in ua:
        d["os"] = "Android"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        d["os"] = "macOS"
    elif "CrOS" in ua:
        d["os"] = "ChromeOS"
    elif "Linux" in ua:
        d["os"] = "Linux"
    if d.get("kind") == "web":
        for name, pat in (("Edge", r"Edg/"), ("Opera", r"OPR/"), ("Yandex", r"YaBrowser/"),
                          ("Firefox", r"Firefox/"), ("Chrome", r"Chrome/"), ("Safari", r"Version/[\d.]+.*Safari/")):
            if re.search(pat, ua):
                d["browser"] = name
                break
    return d


def build_device(ua: str, client: dict | None, ip: str) -> dict:
    """client — то, что прислало приложение: {platform, os, model, app, name}."""
    d = parse_user_agent(ua)
    c = client if isinstance(client, dict) else {}
    plat = _clean(c.get("platform"), 20).lower()
    if plat in ("android", "ios"):
        d["kind"] = "mobile"
        d.setdefault("os", "iOS" if plat == "ios" else "Android")
    elif plat in ("web", "desktop"):
        d["kind"] = plat
    for k in ("os", "model", "app", "name"):
        if c.get(k):
            d[k] = _clean(c[k])
    if ip:
        d["ip"] = ip
    return d


def merge_device(old_json: str, new: dict) -> str:
    try:
        old = json.loads(old_json) if old_json else {}
    except ValueError:
        old = {}
    if not isinstance(old, dict):
        old = {}
    # Данные от приложения точнее User-Agent: не затираем их догадками.
    for k, v in new.items():
        if v and (k in ("ip",) or not old.get(k) or k in new.get("_explicit", ())):
            old[k] = v
    old.pop("_explicit", None)
    return json.dumps(old, ensure_ascii=False)


def title_for(d: dict) -> str:
    if d.get("name"):
        return d["name"]
    kind = d.get("kind")
    if kind == "mobile":
        # Без модели и ОС название подставит клиент («Мобильное приложение»).
        parts = [x for x in (d.get("model") or ("Zweig" if d.get("os") else ""), d.get("os")) if x]
        return " · ".join(parts)
    if kind == "desktop":
        return f"Zweig Desktop · {d['os']}" if d.get("os") else "Zweig Desktop"
    if kind == "web":
        b = d.get("browser") or "Браузер"
        return f"{b} · {d['os']}" if d.get("os") else b
    return ""


async def note_activity(db, sid: str | None, ua: str = "", client: dict | None = None, ip: str = ""):
    """Обновить устройство и время активности сеанса (вход, обновление токена, WebSocket)."""
    if not sid:
        return
    cur = await db.execute("SELECT device_info FROM user_sessions WHERE id = ?", (sid,))
    row = await cur.fetchone()
    if not row:
        return
    new = build_device(ua, client, ip)
    if client:
        new["_explicit"] = tuple(k for k in ("os", "model", "app", "name", "kind") if new.get(k))
    await db.execute(
        "UPDATE user_sessions SET device_info = ?, last_used_at = ? WHERE id = ?",
        (merge_device(row["device_info"], new), now_iso(), sid),
    )


_LAST_BEAT: dict[str, float] = {}


async def heartbeat(db, sid: str | None):
    """Отметить активность сеанса не чаще раза в пять минут."""
    if not sid:
        return
    t = datetime.now(timezone.utc).timestamp()
    if t - _LAST_BEAT.get(sid, 0) < 300:
        return
    _LAST_BEAT[sid] = t
    await db.execute("UPDATE user_sessions SET last_used_at = ? WHERE id = ?", (now_iso(), sid))


def describe(row, current_sid: str | None = None) -> dict:
    try:
        d = json.loads(row["device_info"]) if row["device_info"] else {}
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    return {
        "id": row["id"],
        "title": title_for(d),
        "kind": d.get("kind") or "unknown",
        "os": d.get("os") or "",
        "model": d.get("model") or "",
        "browser": d.get("browser") or "",
        "app": d.get("app") or "",
        "ip": d.get("ip") or "",
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"] or row["created_at"],
        "current": bool(current_sid) and row["id"] == current_sid,
    }


async def list_sessions(db, username: str, current_sid: str | None = None) -> list[dict]:
    cur = await db.execute(
        """SELECT id, created_at, last_used_at, device_info FROM user_sessions
           WHERE username = ? AND revoked_at IS NULL AND (expires_at = '' OR expires_at > ?)
           ORDER BY (id = ?) DESC, last_used_at DESC""",
        (username, now_iso(), current_sid or ""),
    )
    return [describe(r, current_sid) for r in await cur.fetchall()]


# ── WebSocket сеанса ─────────────────────────────────────────────

def attach(sid: str | None, ws):
    if sid:
        SOCKETS.setdefault(sid, set()).add(ws)


def detach(sid: str | None, ws):
    if sid:
        _LAST_BEAT.pop(sid, None)
    if sid and sid in SOCKETS:
        SOCKETS[sid].discard(ws)
        if not SOCKETS[sid]:
            SOCKETS.pop(sid, None)


async def _kick(sids, reason: str):
    for sid in sids:
        for ws in list(SOCKETS.pop(sid, ())):
            try:
                await ws.send_json({"event": "session_revoked", "reason": reason})
            except Exception:
                pass
            try:
                await ws.close(code=4001, reason="Session revoked")
            except Exception:
                pass


# ── Отзыв ─────────────────────────────────────────────────────────

async def revoke(db, username: str, reason: str, sids: list[str] | None = None,
                 keep: str | None = None) -> int:
    """Завершить сеансы пользователя: перечисленные `sids` или все, кроме `keep`.

    Отзыв действует сразу: токены доступа этих сеансов перестают приниматься,
    их WebSocket закрываются, пуш-токены устройств удаляются. Вызывающий
    делает commit.
    """
    prune()
    q = "SELECT id FROM user_sessions WHERE username = ? AND revoked_at IS NULL"
    args: list = [username]
    if sids is not None:
        if not sids:
            return 0
        q += f" AND id IN ({','.join('?' * len(sids))})"
        args += sids
    if keep:
        q += " AND id != ?"
        args.append(keep)
    cur = await db.execute(q, args)
    ids = [r[0] for r in await cur.fetchall()]
    if ids:
        ph = ",".join("?" * len(ids))
        await db.execute(
            f"UPDATE user_sessions SET revoked_at = ?, revoked_reason = ? WHERE id IN ({ph})",
            [now_iso(), reason] + ids,
        )
        await db.execute(f"DELETE FROM user_devices WHERE session_id IN ({ph})", ids)
        await db.execute(f"DELETE FROM user_voip_devices WHERE session_id IN ({ph})", ids)
    if sids is None and not keep:
        # Все входы: заодно и пуш-токены, записанные до появления сеансов.
        await db.execute("DELETE FROM user_devices WHERE username = ?", (username,))
        await db.execute("DELETE FROM user_voip_devices WHERE username = ?", (username,))
    _mark(ids)
    # Закрываем сокеты в фоне: спящий телефон может не ответить на закрытие,
    # а запрос «Завершить» не должен этого ждать.
    legacy = sids is None and not keep
    if ids or legacy:
        t = asyncio.create_task(_kick_all(ids, reason, username if legacy else None))
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
    return len(ids)


_TASKS: set = set()


async def _kick_all(ids, reason, legacy_user):
    try:
        if legacy_user:
            await _kick_user_legacy(legacy_user, reason, skip=set().union(*(SOCKETS.get(i, set()) for i in ids)) if ids else set())
        await _kick(ids, reason)
    except Exception as e:
        print(f"[sessions] kick: {e}")


async def _kick_user_legacy(username: str, reason: str, skip: set):
    """Сокеты без sid (старые токены) у пользователя, лишённого всех входов."""
    from ws_manager import manager
    for ws in list(manager.active_connections.get(username, [])):
        if ws in skip:
            continue
        try:
            await ws.send_json({"event": "session_revoked", "reason": reason})
            await ws.close(code=4001, reason="Session revoked")
        except Exception:
            pass


# ── Автовыход с неактивных устройств ────────────────────────────
# Пользователь выбирает срок: никогда (0), день, неделя или месяц. Вход,
# которым не пользовались дольше срока, завершается — и раз в час фоновой
# уборкой, и сразу при попытке обновить им токен.

INACTIVE_CHOICES = (0, 1, 7, 30)


async def get_inactive_days(db, username: str) -> int:
    cur = await db.execute("SELECT inactive_days FROM user_session_prefs WHERE username = ?", (username,))
    row = await cur.fetchone()
    return int(row["inactive_days"]) if row else 0


async def set_inactive_days(db, username: str, days: int):
    await db.execute(
        """INSERT INTO user_session_prefs (username, inactive_days) VALUES (?, ?)
           ON CONFLICT(username) DO UPDATE SET inactive_days = excluded.inactive_days""",
        (username, days),
    )


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def is_stale(last_used_at: str, days: int) -> bool:
    return bool(days) and bool(last_used_at) and last_used_at < _cutoff(days)


async def revoke_inactive(db, username: str | None = None) -> int:
    """Завершить входы, неактивные дольше выбранного срока. Вызывающий делает commit."""
    q = "SELECT username, inactive_days FROM user_session_prefs WHERE inactive_days > 0"
    args: list = []
    if username:
        q += " AND username = ?"
        args.append(username)
    cur = await db.execute(q, args)
    total = 0
    for row in await cur.fetchall():
        c2 = await db.execute(
            """SELECT id FROM user_sessions WHERE username = ? AND revoked_at IS NULL
               AND COALESCE(NULLIF(last_used_at, ''), created_at) < ?""",
            (row["username"], _cutoff(row["inactive_days"])),
        )
        ids = [r[0] for r in await c2.fetchall()]
        # Открытый сейчас сокет — это активность, даже если отметка не успела обновиться.
        ids = [i for i in ids if i not in SOCKETS]
        if ids:
            total += await revoke(db, row["username"], "inactive", sids=ids)
    return total


async def run_inactive_loop():
    from database import get_db
    while True:
        try:
            db = await get_db()
            n = await revoke_inactive(db)
            await db.commit()
            if n:
                print(f"[sessions] автовыход: завершено неактивных входов: {n}")
        except Exception as e:
            print(f"[sessions] inactive loop: {e}")
        await asyncio.sleep(3600)
