"""Лента «Важное»: личные события человека — колокольчик на вебе и в приложении.

Сюда попадает то, что касается именно этого человека: его назначили на
задачу, срок задачи сегодня или уже прошёл, в его задаче написали
комментарий, задачу, которую он поставил, закрыли, его упомянули в
переписке. Обычные сообщения в чатах сюда не идут — у них свои счётчики.

Одна запись на получателя хранится на сервере, поэтому лента одинаковая на
всех устройствах, а прочитанное гаснет везде сразу (событие по WebSocket).
"""
import asyncio
import re
import uuid
from datetime import date, datetime, timezone

from database import get_db
from helpers import now_iso
from ws_manager import manager

import features

# Вкладка «Уведомления»: то, что касается человека вне трекера.
KINDS_NOTICE = ("mention", "missed_call", "announce")

KINDS_TASK = ("task_assigned", "task_comment", "task_done", "task_due", "task_overdue",
              "task_review", "task_returned", "task_approved")


def _row(r) -> dict:
    d = dict(r)
    d["read"] = bool(d.get("read_at"))
    return d


async def unread_count(db, username: str) -> int:
    q = "SELECT COUNT(*) FROM notifications WHERE username = ? AND read_at = ''"
    params: list = [username]
    if not features.enabled("tracker"):
        q += f" AND kind NOT IN ({','.join('?' * len(KINDS_TASK))})"
        params += list(KINDS_TASK)
    cur = await db.execute(q, params)
    return (await cur.fetchone())[0]


async def notify(db, username: str, kind: str, *, actor: str = "", title: str = "", body: str = "",
                 project_slug: str = "", task_id: str = "", task_key: str = "",
                 channel_slug: str = "", message_id: str = "", dedupe: str = "") -> dict | None:
    """Положить событие в ленту человека и сразу показать его открытым клиентам.

    Себе о своих же действиях не пишем. Пустой получатель и чужие заглушки
    федерации (`name@server`) пропускаем — у них нет нашей ленты.
    """
    if not username or username == actor or "@" in username:
        return None
    if kind in KINDS_TASK and not features.enabled("tracker"):
        return None
    nid = str(uuid.uuid4())
    now = now_iso()
    try:
        await db.execute(
            """INSERT INTO notifications (id, username, kind, actor, title, body, project_slug, task_id,
                   task_key, channel_slug, message_id, dedupe, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (nid, username, kind, actor, title[:300], body[:500], project_slug, task_id, task_key,
             channel_slug, message_id, dedupe, now),
        )
    except Exception:
        # Тот же dedupe уже есть — напоминание о сроке на эту дату уже было.
        return None
    await db.commit()
    cur = await db.execute(
        """SELECT n.*, COALESCE(u.display_name, n.actor) AS actor_name, COALESCE(u.avatar_path, '') AS actor_avatar
           FROM notifications n LEFT JOIN users u ON u.username = n.actor WHERE n.id = ?""", (nid,)
    )
    item = _row(await cur.fetchone())
    unread = await unread_count(db, username)
    await manager.send_to_user(username, {"event": "notification", "item": item, "unread": unread})
    if kind in PUSH_KINDS:
        asyncio.create_task(_push(username, item, unread))
    return item


# Упоминания в пуш не дублируем: о самом сообщении телефон уже узнал обычным
# пушем чата. Остальное — события, о которых иначе не узнать, не открыв веб.
PUSH_KINDS = KINDS_TASK + ("module_expiring",)


async def _push(username: str, item: dict, unread: int):
    try:
        from fcm_sender import send_inbox_notification, INBOX_TITLES
        delivered = False
        if not manager.has_mobile(username):
            delivered = await send_inbox_notification(username, item, unread)
        if not delivered and not manager.is_online(username):
            from routes.push import send_push_to_user
            await send_push_to_user(username, {
                "type": "inbox", "title": INBOX_TITLES.get(item["kind"], "Важное"),
                "body": item.get("title") or item.get("body") or "", "task_id": item.get("task_id", ""),
                "project_slug": item.get("project_slug", ""), "badge": unread,
            })
    except Exception as e:  # noqa: BLE001 — пуш не должен ломать событие
        print(f"[notifications] push failed: {e}")


async def list_for(db, username: str, kind: str = "all", limit: int = 50, before: str = "") -> list[dict]:
    q = """SELECT n.*, COALESCE(u.display_name, n.actor) AS actor_name, COALESCE(u.avatar_path, '') AS actor_avatar
           FROM notifications n LEFT JOIN users u ON u.username = n.actor
           WHERE n.username = ?"""
    params: list = [username]
    if kind == "tasks":
        q += f" AND n.kind IN ({','.join('?' * len(KINDS_TASK))})"
        params += list(KINDS_TASK)
    elif kind in ("notices", "mentions"):
        # «Уведомления»: упоминания и пропущенные звонки. `mentions` — прежнее
        # имя вкладки, его ещё шлют старые сборки.
        q += f" AND n.kind IN ({','.join('?' * len(KINDS_NOTICE))})"
        params += list(KINDS_NOTICE)
    if not features.enabled("tracker"):
        q += f" AND n.kind NOT IN ({','.join('?' * len(KINDS_TASK))})"
        params += list(KINDS_TASK)
    if before:
        q += " AND n.created_at < ?"
        params.append(before)
    q += " ORDER BY n.created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit or 50), 200)))
    cur = await db.execute(q, params)
    return [_row(r) for r in await cur.fetchall()]


async def mark_read(db, username: str, ids: list[str] | None = None, all_: bool = False,
                    task_id: str = "", channel_slug: str = "") -> int:
    now = now_iso()
    if all_:
        cur = await db.execute(
            "UPDATE notifications SET read_at = ? WHERE username = ? AND read_at = ''", (now, username))
    elif task_id:
        cur = await db.execute(
            "UPDATE notifications SET read_at = ? WHERE username = ? AND read_at = '' AND task_id = ?",
            (now, username, task_id))
    elif channel_slug:
        cur = await db.execute(
            "UPDATE notifications SET read_at = ? WHERE username = ? AND read_at = '' AND channel_slug = ? AND kind IN ('mention', 'missed_call')",
            (now, username, channel_slug))
    else:
        ids = [i for i in (ids or []) if isinstance(i, str)][:200]
        if not ids:
            return 0
        cur = await db.execute(
            f"UPDATE notifications SET read_at = ? WHERE username = ? AND read_at = '' AND id IN ({','.join('?' * len(ids))})",
            [now, username, *ids])
    await db.commit()
    changed = cur.rowcount or 0
    if changed:
        await manager.send_to_user(username, {
            "event": "notifications_read", "unread": await unread_count(db, username),
            "all": all_, "ids": ids or [], "task_id": task_id, "channel_slug": channel_slug,
        })
    return changed


# ── Упоминания в переписке ──────────────────────────────────────

_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_][A-Za-z0-9_.\-]{1,63})")


async def notify_mentions(db, channel: dict, sender: str, text: str, message_id: str):
    """@логин или @никнейм в сообщении — событие тому, кого упомянули.

    Только участникам этого канала: упомянуть человека, который переписку не
    видит, значит показать ему кусок чужого чата.
    """
    if not text or "@" not in text:
        return
    tags = {m.group(1).lower() for m in _MENTION_RE.finditer(text)}
    if not tags:
        return
    cur = await db.execute(
        """SELECT u.username, LOWER(COALESCE(u.nickname, '')) AS nick FROM users u
           JOIN channel_members cm ON cm.username = u.username
           WHERE cm.channel_id = ? AND COALESCE(u.home_server, '') = ''""",
        (channel["id"],),
    )
    targets = [r["username"] for r in await cur.fetchall()
               if r["username"].lower() in tags or (r["nick"] and r["nick"].lstrip("@") in tags)]
    if not targets:
        return
    where = channel.get("name") or ""
    if channel.get("type") == "direct":
        where = ""
    for u in targets:
        await notify(db, u, "mention", actor=sender, title=where, body=text[:300],
                     channel_slug=channel.get("slug", ""), message_id=message_id)


# ── Сроки задач ─────────────────────────────────────────────────

async def scan_due_tasks():
    """Раз в час: «срок сегодня» и «срок прошёл» исполнителю открытой задачи.

    dedupe держит одно напоминание на задачу и дату — сколько бы раз ни
    проходил обход, человек получит его один раз в день.
    """
    if not features.enabled("tracker"):
        return
    db = await get_db()
    today = date.today().isoformat()
    cur = await db.execute(
        """SELECT t.id, t.title, t.task_key, t.assignee, t.due_date, p.slug AS project_slug, p.name AS project_name
           FROM tasks t JOIN task_projects p ON p.id = t.project_id
           WHERE t.assignee IS NOT NULL AND t.assignee != '' AND t.due_date IS NOT NULL AND t.due_date != ''
             AND substr(t.due_date, 1, 10) <= ?
             AND t.status NOT IN (SELECT key FROM task_statuses s WHERE s.project_id = t.project_id AND s.kind = 'done')""",
        (today,),
    )
    for r in await cur.fetchall():
        due = (r["due_date"] or "")[:10]
        kind = "task_due" if due == today else "task_overdue"
        await notify(db, r["assignee"], kind, title=r["title"], body=r["project_name"],
                     project_slug=r["project_slug"], task_id=r["id"], task_key=r["task_key"] or "",
                     dedupe=f"{kind}:{r['id']}:{today}")


async def run_due_loop():
    await asyncio.sleep(20)
    while True:
        try:
            await scan_due_tasks()
        except Exception as e:  # noqa: BLE001 — обход не должен ронять сервер
            print(f"[notifications] due scan failed: {e}")
        await asyncio.sleep(3600)
