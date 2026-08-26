"""ChaosHelper Messenger — Channel management (CRUD, members, slug routing)."""

import uuid
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from helpers import get_current_user, get_display_name, slugify, now_iso
from database import get_db
from ws_manager import manager

router = APIRouter(prefix="/api", tags=["channels"])

UPLOAD_DIR = Path(os.getenv("DATA_DIR", "/app/data")) / "uploads" / "channel_avatars"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────

async def _get_members(db, channel_id: str) -> list[str]:
    cursor = await db.execute(
        "SELECT username FROM channel_members WHERE channel_id = ?", (channel_id,)
    )
    return [r["username"] for r in await cursor.fetchall()]


async def _get_member_details(db, channel_id: str) -> list[dict]:
    cursor = await db.execute(
        """SELECT cm.username, cm.role, cm.joined_at,
                  u.display_name, u.nickname, u.avatar_path, u.status_text, u.home_server
           FROM channel_members cm
           JOIN users u ON u.username = cm.username
           WHERE cm.channel_id = ?
           ORDER BY cm.role DESC, u.display_name""",
        (channel_id,),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def cleared_at(db, channel_id: str, username: str) -> str:
    """When this member deleted the conversation for themselves ('' = never).

    Everything at or before this moment is invisible to them, in the list, in
    the history and in the unread counter.
    """
    cursor = await db.execute(
        "SELECT cleared_at FROM channel_members WHERE channel_id = ? AND username = ?",
        (channel_id, username),
    )
    row = await cursor.fetchone()
    return (row["cleared_at"] if row else "") or ""


async def visible_from(db, channel_id: str, username: str) -> str:
    """Момент, раньше которого переписка этому участнику не показывается.

    Границ две, и обе односторонние: он сам удалил у себя чат (`cleared_at`)
    либо его добавили без доступа к истории (`history_from`). Берётся поздняя —
    ни одна из них не должна отменять другую.
    """
    cursor = await db.execute(
        "SELECT cleared_at, history_from FROM channel_members WHERE channel_id = ? AND username = ?",
        (channel_id, username),
    )
    row = await cursor.fetchone()
    if not row:
        return ""
    return max((row["cleared_at"] or ""), (row["history_from"] or ""))


MUTE_FOREVER = "9999-12-31T00:00:00+00:00"


async def muted_until(db, channel_id: str, username: str) -> str:
    """До какого момента беседа молчит для этого человека ('' — не молчит)."""
    cursor = await db.execute(
        "SELECT muted_until FROM channel_members WHERE channel_id = ? AND username = ?",
        (channel_id, username),
    )
    row = await cursor.fetchone()
    return (row["muted_until"] if row else "") or ""


async def is_muted(db, channel_id: str, username: str) -> bool:
    """Молчит ли беседа прямо сейчас.

    Отдельная функция, потому что спрашивают её из трёх разных мест перед
    каждым уведомлением: заглушённая беседа не должна звенеть ни своим
    сообщением, ни пришедшим с соседнего сервера, ни пропущенным звонком.
    """
    until = await muted_until(db, channel_id, username)
    if not until:
        return False
    return until > now_iso()


async def _count_unread(db, channel_id: str, username: str) -> int:
    since = await visible_from(db, channel_id, username)
    cursor = await db.execute(
        """SELECT COUNT(*) FROM messages m
           WHERE m.channel_id = ? AND m.sender != ?
           AND m.timestamp > ?
           AND NOT EXISTS (
               SELECT 1 FROM message_reads mr
               WHERE mr.message_id = m.id AND mr.username = ?
           )""",
        (channel_id, username, since, username),
    )
    row = await cursor.fetchone()
    return row[0]


async def _build_channel(db, row, username: str) -> dict:
    c = dict(row)
    c["members"] = await _get_members(db, c["id"])
    # Include member details so clients can resolve display names (nicknames)
    # for direct-chat titles in the channel list — without them the UI falls
    # back to showing the raw login.
    c["member_details"] = await _get_member_details(db, c["id"])
    c["unread_count"] = await _count_unread(db, c["id"], username)
    c["muted_until"] = await muted_until(db, c["id"], username)
    if c.get("last_msg_text") is not None:
        c["last_message"] = {
            "text": c["last_msg_text"],
            "sender": c["last_msg_sender"],
            "sender_name": c["last_msg_sender_name"],
            "timestamp": c["last_msg_timestamp"],
        }
    else:
        c["last_message"] = None
    for k in ("last_msg_text", "last_msg_sender", "last_msg_sender_name", "last_msg_timestamp"):
        c.pop(k, None)
    return c


async def _ensure_unique_slug(db, slug: str, exclude_id: str | None = None) -> str:
    """Ensure slug is unique, appending suffix if needed."""
    base_slug = slug
    counter = 1
    while True:
        if exclude_id:
            cursor = await db.execute(
                "SELECT 1 FROM channels WHERE slug = ? AND id != ?", (slug, exclude_id)
            )
        else:
            cursor = await db.execute("SELECT 1 FROM channels WHERE slug = ?", (slug,))
        if not await cursor.fetchone():
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


async def find_direct(db, user_a: str, user_b: str) -> str | None:
    """Id of the existing direct channel between two users, or None.

    Federation relies on the distinction between this and
    get_or_create_direct: an incoming remote message is only accepted into a
    conversation that already exists, because that conversation is what a
    redeemed invite created. Creating one on delivery would let anyone on a
    linked server message anyone here.
    """
    cursor = await db.execute(
        """SELECT c.id FROM channels c
           JOIN channel_members cm1 ON c.id = cm1.channel_id AND cm1.username = ?
           JOIN channel_members cm2 ON c.id = cm2.channel_id AND cm2.username = ?
           WHERE c.type = 'direct'""",
        (user_a, user_b),
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


async def get_or_create_direct(db, user_a: str, user_b: str) -> tuple[str, bool]:
    """Find the direct channel between two users, creating it if missing.

    Returns (channel_id, created). Shared with federation: redeeming an invite
    opens exactly this kind of conversation, the only difference being that one
    member is a stub row for someone on another server.
    """
    existing = await find_direct(db, user_a, user_b)
    if existing:
        return existing, False

    now = now_iso()
    ch_id = str(uuid.uuid4())
    slug = await _ensure_unique_slug(db, f"dm-{ch_id[:8]}")
    await db.execute(
        "INSERT INTO channels (id, name, slug, type, created_by, created_at) VALUES (?, '', ?, 'direct', ?, ?)",
        (ch_id, slug, user_a, now),
    )
    for member in (user_a, user_b):
        await db.execute(
            "INSERT INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
            (ch_id, member, now),
        )
    await db.commit()
    return ch_id, True


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/channels")
async def list_channels(username: str = Depends(get_current_user)):
    """List channels the user is a member of.

    A conversation the user deleted for themselves stays hidden until something
    new arrives in it: `cleared_at` marks the moment, and the chat comes back
    only when a message is newer than that.
    """
    db = await get_db()
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE cm.username = ?
             AND (cm.cleared_at = ''
                  OR (c.last_msg_timestamp IS NOT NULL
                      AND c.last_msg_timestamp > cm.cleared_at))
           ORDER BY COALESCE(c.last_msg_timestamp, c.created_at) DESC""",
        (username,),
    )
    rows = await cursor.fetchall()
    return [await _build_channel(db, r, username) for r in rows]


@router.get("/channels/public")
async def list_public_channels(username: str = Depends(get_current_user)):
    """List all public channels (for discovery)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM channels WHERE type = 'public' ORDER BY name"
    )
    rows = await cursor.fetchall()
    result = []
    for r in rows:
        ch = dict(r)
        # Check if user is member
        c2 = await db.execute(
            "SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?",
            (ch["id"], username),
        )
        ch["is_member"] = bool(await c2.fetchone())
        # Count members
        c3 = await db.execute(
            "SELECT COUNT(*) FROM channel_members WHERE channel_id = ?", (ch["id"],)
        )
        ch["member_count"] = (await c3.fetchone())[0]
        result.append(ch)
    return result


@router.post("/channels")
async def create_channel(data: dict, username: str = Depends(get_current_user)):
    """Create a new channel (public/private) or direct message."""
    db = await get_db()
    ch_type = data.get("type", "public")
    now = now_iso()

    if ch_type == "direct":
        participant = data.get("participant", "").strip()
        if not participant or participant == username:
            raise HTTPException(status_code=400, detail="Invalid participant")

        ch_id, created = await get_or_create_direct(db, username, participant)
        c3 = await db.execute("SELECT * FROM channels WHERE id = ?", (ch_id,))
        ch = await _build_channel(db, await c3.fetchone(), username)
        if created:
            # Notify both users (recipient + creator's other tabs)
            await manager.send_to_user(participant, {"event": "channel_created", "channel": ch})
            await manager.send_to_user(username, {"event": "channel_created", "channel": ch})
        return ch

    else:
        # Public or private channel
        name = data.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Channel name required")

        raw_slug = data.get("slug", "").strip()
        slug = slugify(raw_slug) if raw_slug else slugify(name)
        if not slug:
            raise HTTPException(status_code=400, detail="Invalid channel name for slug generation")

        slug = await _ensure_unique_slug(db, slug)
        description = data.get("description", "").strip()[:500]

        ch_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO channels (id, name, slug, type, description, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ch_id, name, slug, ch_type, description, username, now),
        )
        # Creator is the owner
        await db.execute(
            "INSERT INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'owner', ?)",
            (ch_id, username, now),
        )

        # Add invited members
        for p in data.get("members", []):
            if p == username:
                continue
            # Same rule as adding later: someone from another server may join
            # only if this user already has a conversation with them.
            cur_m = await db.execute("SELECT home_server FROM users WHERE username = ?", (p,))
            row_m = await cur_m.fetchone()
            if not row_m:
                continue
            if row_m["home_server"] and not await find_direct(db, username, p):
                raise HTTPException(
                    status_code=403,
                    detail="Сначала откройте личный диалог с этим человеком",
                )
            await db.execute(
                "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
                (ch_id, p, now),
            )

        await db.commit()

        c3 = await db.execute("SELECT * FROM channels WHERE id = ?", (ch_id,))
        ch = await _build_channel(db, await c3.fetchone(), username)
        # A channel born with members from other servers has to be announced to
        # them straight away, or their first message would have nowhere to land.
        from routes.federation import sync_channel_to_peers
        await sync_channel_to_peers(db, ch_id)
        # Notify all members
        for m in ch["members"]:
            if m != username:
                await manager.send_to_user(m, {"event": "channel_created", "channel": ch})
        return ch


@router.get("/channels/{slug}")
async def get_channel(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE c.slug = ? AND cm.username = ?""",
        (slug, username),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")
    ch = await _build_channel(db, row, username)
    ch["member_details"] = await _get_member_details(db, ch["id"])
    return ch


@router.put("/channels/{slug}")
async def update_channel(slug: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    # Verify membership and get channel
    cursor = await db.execute(
        """SELECT c.*, cm.role as my_role FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE c.slug = ? AND cm.username = ?""",
        (slug, username),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")

    ch = dict(row)
    my_role = ch.pop("my_role")

    # Only owner/admin can edit
    if my_role not in ("owner", "admin"):
        # Check if user is system admin
        from helpers import get_current_user_info
        user_info = await get_current_user_info(username)
        if user_info.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not enough permissions")

    updates = []
    params = []

    if "name" in data:
        updates.append("name = ?")
        params.append(data["name"].strip()[:100])
    if "description" in data:
        updates.append("description = ?")
        params.append(data["description"].strip()[:500])
    if "slug" in data and data["slug"]:
        new_slug = slugify(data["slug"].strip())
        if new_slug:
            new_slug = await _ensure_unique_slug(db, new_slug, exclude_id=ch["id"])
            updates.append("slug = ?")
            params.append(new_slug)

    if updates:
        params.append(ch["id"])
        await db.execute(f"UPDATE channels SET {', '.join(updates)} WHERE id = ?", params)

    # Add/remove members
    membership_changed = False
    if "add_members" in data:
        # По умолчанию история открыта — так было всегда, и менять это молча
        # нельзя. Закрывают её осознанно, галочкой при добавлении.
        share_history = data.get("share_history", True)
        joined_at_boundary = "" if share_history else now_iso()
        for p in data["add_members"]:
            # Someone from another server can be added, but only if this user
            # already has a conversation with them. There is no directory of
            # remote people by design, so an existing direct chat is the one
            # thing that proves they were legitimately introduced — without it
            # any username@domain string would pull a stranger into the room.
            cur_m = await db.execute("SELECT home_server FROM users WHERE username = ?", (p,))
            row_m = await cur_m.fetchone()
            if not row_m:
                continue
            if row_m["home_server"]:
                if not await find_direct(db, username, p):
                    raise HTTPException(
                        status_code=403,
                        detail="Сначала откройте личный диалог с этим человеком",
                    )
            await db.execute(
                "INSERT OR IGNORE INTO channel_members "
                "(channel_id, username, role, joined_at, history_from) VALUES (?, ?, 'member', ?, ?)",
                (ch["id"], p, now_iso(), joined_at_boundary),
            )
            membership_changed = True

    removed_users = []
    if "remove_members" in data:
        for p in data["remove_members"]:
            if p != ch["created_by"]:  # Can't remove owner
                await db.execute(
                    "DELETE FROM channel_members WHERE channel_id = ? AND username = ?",
                    (ch["id"], p),
                )
                removed_users.append(p)
                membership_changed = True

    await db.commit()

    # Every participating server keeps its own row for this channel, so a
    # membership change here has to reach all of them or their fan-out will
    # skip whoever they do not know about.
    if membership_changed:
        from routes.federation import sync_channel_to_peers
        await sync_channel_to_peers(db, ch["id"])

    # Notify kicked users
    for kicked_user in removed_users:
        kicked_display = await get_display_name(kicked_user)
        await manager.send_to_user(kicked_user, {
            "event": "member_left",
            "channel_id": ch["id"],
            "slug": slug,
            "username": kicked_user,
            "display_name": kicked_display,
        })

    # Re-fetch and notify
    c2 = await db.execute("SELECT * FROM channels WHERE id = ?", (ch["id"],))
    updated_ch = await _build_channel(db, await c2.fetchone(), username)
    members = updated_ch["members"]
    await manager.send_to_channel(members, {"event": "channel_updated", "channel": updated_ch})
    return updated_ch


@router.post("/channels/{slug}/federation/resync")
async def resync_channel(slug: str, username: str = Depends(get_current_user)):
    """Заново разослать состав канала участвующим серверам.

    Состав уходит соседям при каждом изменении членства, и повторить это иначе
    можно было только тронув членство — то есть выкинув человека и вернув
    обратно. Нужно, когда рассылка не дошла: сосед лежал, отвечал ошибкой или,
    как было до 25 августа, вовсе не получал запрос.
    """
    db = await get_db()
    cursor = await db.execute(
        """SELECT c.*, cm.role AS my_role FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE c.slug = ? AND cm.username = ?""",
        (slug, username),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")
    ch = dict(row)
    if ch.pop("my_role") not in ("owner", "admin"):
        from helpers import get_current_user_info
        user_info = await get_current_user_info(username)
        if user_info.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not enough permissions")

    from routes.federation import sync_channel_to_peers
    await sync_channel_to_peers(db, ch["id"])
    return {"ok": True}


@router.delete("/channels/{slug}")
async def delete_channel(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM channels WHERE slug = ?", (slug,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")

    ch = dict(row)

    # Only owner or system admin can delete
    c2 = await db.execute(
        "SELECT role FROM channel_members WHERE channel_id = ? AND username = ?",
        (ch["id"], username),
    )
    member = await c2.fetchone()
    is_owner = member and member["role"] == "owner"

    from helpers import get_current_user_info
    user_info = await get_current_user_info(username)
    is_admin = user_info.get("role") == "admin"

    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    members = await _get_members(db, ch["id"])
    await db.execute("DELETE FROM channels WHERE id = ?", (ch["id"],))
    await db.commit()

    await manager.send_to_channel(members, {"event": "channel_deleted", "channel_id": ch["id"], "slug": slug})
    return {"ok": True}


@router.post("/channels/{slug}/join")
async def join_channel(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM channels WHERE slug = ?", (slug,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")

    ch = dict(row)
    if ch["type"] == "direct":
        raise HTTPException(status_code=400, detail="Cannot join DM channels")
    if ch["type"] == "private":
        raise HTTPException(status_code=403, detail="Channel is private, must be invited")

    await db.execute(
        "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
        (ch["id"], username, now_iso()),
    )
    await db.commit()

    # System message
    display_name = await get_display_name(username)
    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {
        "event": "member_joined",
        "channel_id": ch["id"],
        "slug": slug,
        "username": username,
        "display_name": display_name,
    })

    c2 = await db.execute("SELECT * FROM channels WHERE id = ?", (ch["id"],))
    return await _build_channel(db, await c2.fetchone(), username)


@router.post("/channels/{slug}/mute")
async def mute_channel(slug: str, data: dict | None = None, username: str = Depends(get_current_user)):
    """Заглушить беседу для себя.

    `hours` — на сколько; пусто означает «пока не включу обратно». Тишина
    касается только уведомлений: непрочитанные считаются по-прежнему, и в
    списке беседа остаётся на своём месте. Человек не отказывается от
    переписки, он отказывается от звона.
    """
    db = await get_db()
    from routes.messages import _get_channel_by_slug

    ch = await _get_channel_by_slug(db, slug, username)

    hours = (data or {}).get("hours")
    if hours in (None, "", 0):
        until = MUTE_FOREVER
    else:
        try:
            until = (datetime.now(timezone.utc) + timedelta(hours=float(hours))).isoformat()
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="hours: число часов или пусто")

    await db.execute(
        "UPDATE channel_members SET muted_until = ? WHERE channel_id = ? AND username = ?",
        (until, ch["id"], username),
    )
    await db.commit()
    # Другим своим устройствам — чтобы список выглядел одинаково везде.
    await manager.send_to_user(username, {
        "event": "channel_muted", "channel_id": ch["id"], "slug": slug, "muted_until": until,
    })
    return {"ok": True, "muted_until": until}


@router.delete("/channels/{slug}/mute")
async def unmute_channel(slug: str, username: str = Depends(get_current_user)):
    """Вернуть звук."""
    db = await get_db()
    from routes.messages import _get_channel_by_slug

    ch = await _get_channel_by_slug(db, slug, username)
    await db.execute(
        "UPDATE channel_members SET muted_until = '' WHERE channel_id = ? AND username = ?",
        (ch["id"], username),
    )
    await db.commit()
    await manager.send_to_user(username, {
        "event": "channel_muted", "channel_id": ch["id"], "slug": slug, "muted_until": "",
    })
    return {"ok": True}


@router.post("/channels/{slug}/clear")
async def clear_channel(slug: str, username: str = Depends(get_current_user)):
    """Delete a conversation for the calling user only.

    Nothing is removed: the other side keeps the chat and the whole history.
    We only record the moment for this member, and everything up to it stops
    being shown to them. The chat reappears when a newer message arrives, with
    only what came after — this is what "delete chat" means in Telegram, and
    what people expect from the button.
    """
    db = await get_db()
    cursor = await db.execute("SELECT * FROM channels WHERE slug = ?", (slug,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")

    ch = dict(row)
    c2 = await db.execute(
        "SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?",
        (ch["id"], username),
    )
    if not await c2.fetchone():
        raise HTTPException(status_code=403, detail="Not a member of this conversation")

    await db.execute(
        "UPDATE channel_members SET cleared_at = ? WHERE channel_id = ? AND username = ?",
        (now_iso(), ch["id"], username),
    )
    await db.commit()
    return {"ok": True}


@router.post("/channels/{slug}/leave")
async def leave_channel(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM channels WHERE slug = ?", (slug,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")

    ch = dict(row)

    await db.execute(
        "DELETE FROM channel_members WHERE channel_id = ? AND username = ?",
        (ch["id"], username),
    )
    await db.commit()

    # Соседям нужно знать новый состав: у них своя копия канала, и без этого
    # ушедший остаётся в ней навсегда — вместе с рассылкой сообщений ему.
    from routes.federation import sync_channel_to_peers
    await sync_channel_to_peers(db, ch["id"])

    display_name = await get_display_name(username)
    members = await _get_members(db, ch["id"])
    event = {
        "event": "member_left",
        "channel_id": ch["id"],
        "slug": slug,
        "username": username,
        "display_name": display_name,
    }
    await manager.send_to_channel(members, event)
    # И самому ушедшему: он уже не в составе, значит рассылка по участникам его
    # не застанет, а его остальные устройства всё ещё показывают этот канал.
    await manager.send_to_user(username, event)
    return {"ok": True}


@router.get("/channels/{slug}/members")
async def get_channel_members(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    cursor = await db.execute(
        """SELECT c.id FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE c.slug = ? AND cm.username = ?""",
        (slug, username),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")

    return await _get_member_details(db, row["id"])


@router.get("/slugify")
async def preview_slug(name: str = Query(...), username: str = Depends(get_current_user)):
    """Preview what slug will be generated from a name."""
    return {"slug": slugify(name)}


@router.post("/channels/{slug}/avatar")
async def upload_channel_avatar(slug: str, file: UploadFile = File(...), username: str = Depends(get_current_user)):
    """Upload a channel avatar image."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT c.*, cm.role as my_role FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE c.slug = ? AND cm.username = ?""",
        (slug, username),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")

    ch = dict(row)
    my_role = ch.pop("my_role")

    # Only owner/admin can change avatar
    if my_role not in ("owner", "admin"):
        from helpers import get_current_user_info
        user_info = await get_current_user_info(username)
        if user_info.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not enough permissions")

    # Save file
    ext = file.filename.rsplit(".", 1)[-1] if "." in (file.filename or "") else "png"
    filename = f"{ch['id']}.{ext}"
    filepath = UPLOAD_DIR / filename
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    avatar_path = f"/uploads/channel_avatars/{filename}"
    await db.execute("UPDATE channels SET avatar_path = ? WHERE id = ?", (avatar_path, ch["id"]))
    await db.commit()

    # Notify all members
    members = await _get_members(db, ch["id"])
    c2 = await db.execute("SELECT * FROM channels WHERE id = ?", (ch["id"],))
    updated_ch = await _build_channel(db, await c2.fetchone(), username)
    await manager.send_to_channel(members, {"event": "channel_updated", "channel": updated_ch})

    return {"avatar_path": avatar_path}
