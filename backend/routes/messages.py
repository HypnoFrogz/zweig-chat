"""ChaosHelper Messenger — Messages (send, edit, delete, search, WebSocket)."""

import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from helpers import get_current_user, get_display_name, decode_token_from_string, now_iso
from database import get_db
from ws_manager import manager

router = APIRouter(prefix="/api", tags=["messages"])


# ── Helpers ──────────────────────────────────────────────────────

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


async def _get_members(db, channel_id: str) -> list[str]:
    cursor = await db.execute(
        "SELECT username FROM channel_members WHERE channel_id = ?", (channel_id,)
    )
    return [r["username"] for r in await cursor.fetchall()]


async def _build_message(db, msg_row) -> dict:
    m = dict(msg_row)
    cursor = await db.execute(
        "SELECT username FROM message_reads WHERE message_id = ?", (m["id"],)
    )
    m["read_by"] = [r["username"] for r in await cursor.fetchall()]

    # Sender avatar + live display name.
    # sender_name stored on the row is a frozen snapshot from send time; resolve
    # the current display_name so the UI shows the nickname (not the login) and
    # reflects later nickname changes.
    sender_cursor = await db.execute(
        "SELECT display_name, avatar_path FROM users WHERE username = ?", (m["sender"],)
    )
    sender_row = await sender_cursor.fetchone()
    m["sender_avatar"] = (sender_row["avatar_path"] if sender_row else "") or ""
    if sender_row and sender_row["display_name"]:
        m["sender_name"] = sender_row["display_name"]
    if m.get("file_data"):
        m["file"] = json.loads(m["file_data"])
    if m.get("call_data"):
        m["call"] = json.loads(m["call_data"])
    m.pop("file_data", None)
    m.pop("call_data", None)

    # Reactions
    rxn_cursor = await db.execute(
        "SELECT emoji, GROUP_CONCAT(username) as users FROM message_reactions WHERE message_id = ? GROUP BY emoji",
        (m["id"],),
    )
    reactions = []
    for r in await rxn_cursor.fetchall():
        users = r["users"].split(",")
        reactions.append({"emoji": r["emoji"], "count": len(users), "users": users})
    m["reactions"] = reactions

    # Reply preview
    if m.get("reply_to"):
        rp = await db.execute(
            "SELECT id, sender_name, text FROM messages WHERE id = ?", (m["reply_to"],)
        )
        rp_row = await rp.fetchone()
        if rp_row:
            m["reply_to_preview"] = {
                "id": rp_row["id"],
                "sender_name": rp_row["sender_name"],
                "text": (rp_row["text"] or "")[:120],
            }

    # Reply count (thread)
    tc = await db.execute(
        "SELECT COUNT(*) FROM messages WHERE reply_to = ?", (m["id"],)
    )
    m["reply_count"] = (await tc.fetchone())[0]

    return m


# ── REST Endpoints ───────────────────────────────────────────────

@router.get("/channels/{slug}/messages")
async def get_messages(
    slug: str,
    username: str = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(None),
):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)

    if before:
        bc = await db.execute("SELECT timestamp FROM messages WHERE id = ?", (before,))
        before_row = await bc.fetchone()
        if before_row:
            cursor = await db.execute(
                """SELECT * FROM messages
                   WHERE channel_id = ? AND timestamp < ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (ch["id"], before_row["timestamp"], limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM messages WHERE channel_id = ? ORDER BY timestamp DESC LIMIT ?",
                (ch["id"], limit),
            )
    else:
        cursor = await db.execute(
            "SELECT * FROM messages WHERE channel_id = ? ORDER BY timestamp DESC LIMIT ?",
            (ch["id"], limit),
        )

    rows = list(reversed(await cursor.fetchall()))

    total_cursor = await db.execute(
        "SELECT COUNT(*) FROM messages WHERE channel_id = ?", (ch["id"],)
    )
    total = (await total_cursor.fetchone())[0]

    messages = [await _build_message(db, r) for r in rows]
    return {"messages": messages, "has_more": total > len(messages) + (1 if before else 0)}


@router.post("/channels/{slug}/messages")
async def send_message(slug: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)
    display_name = await get_display_name(username)

    text = data.get("text", "").strip()
    msg_type = data.get("type", "text")
    reply_to = data.get("reply_to")
    if not text and msg_type == "text":
        raise HTTPException(status_code=400, detail="Empty message")

    now = now_iso()

    # Get sender avatar
    av_cursor = await db.execute("SELECT avatar_path FROM users WHERE username = ?", (username,))
    av_row = await av_cursor.fetchone()
    sender_avatar = (av_row["avatar_path"] if av_row else "") or ""

    msg = {
        "id": str(uuid.uuid4()),
        "channel_id": ch["id"],
        "sender": username,
        "sender_name": display_name,
        "sender_avatar": sender_avatar,
        "type": msg_type,
        "text": text,
        "reply_to": reply_to,
        "timestamp": now,
        "read_by": [username],
        "reactions": [],
        "reply_count": 0,
    }

    file_data = None
    if msg_type == "file" and "file" in data:
        msg["file"] = data["file"]
        file_data = json.dumps(data["file"], ensure_ascii=False)

    # Fetch reply preview if replying
    if reply_to:
        rp = await db.execute("SELECT id, sender_name, text FROM messages WHERE id = ?", (reply_to,))
        rp_row = await rp.fetchone()
        if rp_row:
            msg["reply_to_preview"] = {
                "id": rp_row["id"],
                "sender_name": rp_row["sender_name"],
                "text": (rp_row["text"] or "")[:120],
            }

    await db.execute(
        "INSERT INTO messages (id, channel_id, sender, sender_name, type, text, file_data, call_data, reply_to, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg["id"], ch["id"], username, display_name, msg_type, text, file_data, None, reply_to, now),
    )
    await db.execute(
        "INSERT INTO message_reads (message_id, username) VALUES (?, ?)",
        (msg["id"], username),
    )

    lm_text = text or (msg.get("file", {}).get("name", "File"))
    await db.execute(
        "UPDATE channels SET last_msg_text = ?, last_msg_sender = ?, last_msg_sender_name = ?, last_msg_timestamp = ? WHERE id = ?",
        (lm_text, username, display_name, now, ch["id"]),
    )
    await db.commit()

    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {"event": "new_message", "channel_slug": ch["slug"], "message": msg})

    # Push notifications to offline members (FCM for native apps + Web Push for PWA)
    from fcm_sender import send_message_notification
    from routes.push import send_push_to_user
    for member in members:
        if member != username and not manager.is_online(member):
            await send_message_notification(
                recipient=member,
                sender_name=display_name,
                text=text,
                channel_slug=ch["slug"],
                channel_name=ch.get("name") or display_name,
            )
            await send_push_to_user(member, {
                "type": "message",
                "title": ch.get("name") or display_name,
                "body": (text or "")[:100] or "Новое сообщение",
                "conv_id": ch["slug"],
                "badge": 1,
            })

    return msg


@router.put("/channels/{slug}/messages/{msg_id}")
async def edit_message(slug: str, msg_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE id = ? AND channel_id = ?", (msg_id, ch["id"])
    )
    msg_row = await cursor.fetchone()
    if not msg_row:
        raise HTTPException(status_code=404, detail="Message not found")

    if msg_row["sender"] != username:
        raise HTTPException(status_code=403, detail="Can only edit your own messages")

    new_text = data.get("text", "").strip()
    if not new_text:
        raise HTTPException(status_code=400, detail="Empty message")

    now = now_iso()
    await db.execute(
        "UPDATE messages SET text = ?, edited_at = ? WHERE id = ?",
        (new_text, now, msg_id),
    )
    await db.commit()

    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {
        "event": "message_edited",
        "channel_id": ch["id"],
        "message_id": msg_id,
        "text": new_text,
        "edited_at": now,
    })
    return {"ok": True}


@router.delete("/channels/{slug}/messages/{msg_id}")
async def delete_message(slug: str, msg_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE id = ? AND channel_id = ?", (msg_id, ch["id"])
    )
    msg_row = await cursor.fetchone()
    if not msg_row:
        raise HTTPException(status_code=404, detail="Message not found")

    # Can delete own messages, or admin can delete any
    if msg_row["sender"] != username:
        from helpers import get_current_user_info
        user_info = await get_current_user_info(username)
        if user_info.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Can only delete your own messages")

    await db.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    await db.commit()

    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {
        "event": "message_deleted",
        "channel_id": ch["id"],
        "message_id": msg_id,
    })
    return {"ok": True}


@router.post("/channels/{slug}/read")
async def mark_read(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)

    cursor = await db.execute(
        """SELECT m.id FROM messages m
           WHERE m.channel_id = ? AND m.sender != ?
           AND NOT EXISTS (
               SELECT 1 FROM message_reads mr WHERE mr.message_id = m.id AND mr.username = ?
           )
           ORDER BY m.timestamp""",
        (ch["id"], username, username),
    )
    unread_ids = [r["id"] for r in await cursor.fetchall()]

    if unread_ids:
        for msg_id in unread_ids:
            await db.execute(
                "INSERT OR IGNORE INTO message_reads (message_id, username) VALUES (?, ?)",
                (msg_id, username),
            )
        await db.commit()

        members = await _get_members(db, ch["id"])
        await manager.send_to_channel(
            members,
            {"event": "messages_read", "channel_id": ch["id"], "username": username, "up_to_message_id": unread_ids[-1]},
            exclude=username,
        )
    return {"ok": True}


@router.get("/channels/{slug}/unread")
async def get_unread_count(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)
    count = await _count_unread(db, ch["id"], username)
    return {"unread_count": count}


async def _count_unread(db, channel_id: str, username: str) -> int:
    cursor = await db.execute(
        """SELECT COUNT(*) FROM messages m
           WHERE m.channel_id = ? AND m.sender != ?
           AND NOT EXISTS (
               SELECT 1 FROM message_reads mr WHERE mr.message_id = m.id AND mr.username = ?
           )""",
        (channel_id, username, username),
    )
    return (await cursor.fetchone())[0]


@router.get("/messages/search")
async def search_messages(
    q: str = Query(..., min_length=1),
    username: str = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
):
    """Search messages across all user's channels."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT m.*, c.slug as channel_slug, c.name as channel_name FROM messages m
           JOIN channels c ON c.id = m.channel_id
           JOIN channel_members cm ON cm.channel_id = c.id AND cm.username = ?
           WHERE LOWER(m.text) LIKE LOWER(?)
           ORDER BY m.timestamp DESC LIMIT ?""",
        (username, f"%{q}%", limit),
    )
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        m = dict(r)
        m["channel_slug"] = m.pop("channel_slug", "")
        m["channel_name"] = m.pop("channel_name", "")
        if m.get("file_data"):
            m["file"] = json.loads(m["file_data"])
        m.pop("file_data", None)
        m.pop("call_data", None)
        results.append(m)
    return results


@router.get("/unread")
async def get_all_unread(username: str = Depends(get_current_user)):
    """Get unread counts for all channels."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT channel_id FROM channel_members WHERE username = ?", (username,)
    )
    channel_ids = [r["channel_id"] for r in await cursor.fetchall()]

    total = 0
    by_channel = {}
    for ch_id in channel_ids:
        unread = await _count_unread(db, ch_id, username)
        if unread > 0:
            by_channel[ch_id] = unread
            total += unread

    return {"total_unread": total, "by_channel": by_channel}


@router.get("/presence")
async def get_presence(username: str = Depends(get_current_user)):
    return manager.get_online_users()


# ── WebSocket ────────────────────────────────────────────────────

@router.websocket("/ws/messaging")
async def messaging_websocket(websocket: WebSocket, token: str = Query(...)):
    username = decode_token_from_string(token)
    if not username:
        await websocket.close(code=4003, reason="Unauthorized")
        return

    # Check if user is blocked
    db = await get_db()
    c = await db.execute("SELECT blocked FROM users WHERE username = ?", (username,))
    user_row = await c.fetchone()
    if not user_row or user_row["blocked"]:
        await websocket.close(code=4003, reason="Account blocked")
        return

    await manager.connect(username, websocket)
    display_name = await get_display_name(username)

    now = now_iso()
    today = now[:10]  # YYYY-MM-DD

    # Update last_seen
    await db.execute("UPDATE users SET last_seen = ? WHERE username = ?", (now, username))
    # Log daily activity (one row per user per day)
    await db.execute(
        "INSERT OR IGNORE INTO user_activity_log (username, date) VALUES (?, ?)",
        (username, today)
    )
    # Track real-time online status for Grafana
    await db.execute(
        "INSERT INTO online_sessions (username, online, updated_at) VALUES (?, 1, ?) ON CONFLICT(username) DO UPDATE SET online = 1, updated_at = excluded.updated_at",
        (username, now)
    )
    await db.commit()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event = data.get("event")

            if event == "ping":
                await manager.send_to_user(username, {"event": "pong"})
                # Heartbeat: keep online_sessions fresh
                ping_now = now_iso()
                ping_db = await get_db()
                await ping_db.execute(
                    "INSERT INTO online_sessions (username, online, updated_at) VALUES (?, 1, ?) ON CONFLICT(username) DO UPDATE SET online = 1, updated_at = excluded.updated_at",
                    (username, ping_now)
                )
                await ping_db.commit()

            elif event == "send_message":
                await _ws_send_message(data, username, display_name)

            elif event == "typing":
                await _ws_typing(data, username, display_name)

            elif event == "mark_read":
                channel_id = data.get("channel_id")
                if channel_id:
                    await _ws_mark_read(channel_id, username)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        went_offline = await manager.disconnect(username, websocket)
        if went_offline:
            db2 = await get_db()
            offline_now = now_iso()
            await db2.execute("UPDATE users SET last_seen = ? WHERE username = ?", (offline_now, username))
            await db2.execute(
                "UPDATE online_sessions SET online = 0, updated_at = ? WHERE username = ?",
                (offline_now, username)
            )
            await db2.commit()
            await manager.broadcast_presence(username, online=False)


async def _ws_send_message(data: dict, username: str, display_name: str):
    channel_id = data.get("channel_id")
    text = data.get("text", "").strip()
    msg_type = data.get("type", "text")

    if not channel_id or (not text and msg_type == "text"):
        return

    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?",
        (channel_id, username),
    )
    if not await cursor.fetchone():
        return

    now = now_iso()
    reply_to = data.get("reply_to")
    file_data = None

    # Get sender avatar
    av_cursor = await db.execute("SELECT avatar_path FROM users WHERE username = ?", (username,))
    av_row = await av_cursor.fetchone()
    sender_avatar = (av_row["avatar_path"] if av_row else "") or ""

    msg = {
        "id": str(uuid.uuid4()),
        "channel_id": channel_id,
        "sender": username,
        "sender_name": display_name,
        "sender_avatar": sender_avatar,
        "type": msg_type,
        "text": text,
        "reply_to": reply_to,
        "timestamp": now,
        "read_by": [username],
        "reactions": [],
        "reply_count": 0,
    }

    if msg_type == "file" and "file" in data:
        msg["file"] = data["file"]
        file_data = json.dumps(data["file"], ensure_ascii=False)

    # Fetch reply preview if replying
    if reply_to:
        rp = await db.execute("SELECT id, sender_name, text FROM messages WHERE id = ?", (reply_to,))
        rp_row = await rp.fetchone()
        if rp_row:
            msg["reply_to_preview"] = {
                "id": rp_row["id"],
                "sender_name": rp_row["sender_name"],
                "text": (rp_row["text"] or "")[:120],
            }

    await db.execute(
        "INSERT INTO messages (id, channel_id, sender, sender_name, type, text, file_data, call_data, reply_to, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg["id"], channel_id, username, display_name, msg_type, text, file_data, None, reply_to, now),
    )
    await db.execute(
        "INSERT INTO message_reads (message_id, username) VALUES (?, ?)",
        (msg["id"], username),
    )

    lm_text = text or (msg.get("file", {}).get("name", "File"))
    await db.execute(
        "UPDATE channels SET last_msg_text = ?, last_msg_sender = ?, last_msg_sender_name = ?, last_msg_timestamp = ? WHERE id = ?",
        (lm_text, username, display_name, now, channel_id),
    )
    await db.commit()

    members = await _get_members(db, channel_id)

    # Get channel slug for WS event
    ch_cursor = await db.execute("SELECT slug, name FROM channels WHERE id = ?", (channel_id,))
    ch_info = await ch_cursor.fetchone()
    ch_slug = ch_info["slug"] if ch_info else channel_id
    ch_name = (ch_info["name"] if ch_info else "") or display_name

    await manager.send_to_channel(members, {"event": "new_message", "channel_slug": ch_slug, "message": msg})

    # Push notifications to offline members (FCM for native apps + Web Push for PWA)
    from fcm_sender import send_message_notification
    from routes.push import send_push_to_user
    for member in members:
        if member != username and not manager.is_online(member):
            await send_message_notification(
                recipient=member,
                sender_name=display_name,
                text=text,
                channel_slug=ch_slug,
                channel_name=ch_name,
            )
            await send_push_to_user(member, {
                "type": "message",
                "title": ch_name,
                "body": (text or "")[:100] or "Новое сообщение",
                "conv_id": ch_slug,
                "badge": 1,
            })


async def _ws_typing(data: dict, username: str, display_name: str):
    channel_id = data.get("channel_id")
    is_typing = data.get("is_typing", False)
    if not channel_id:
        return

    db = await get_db()
    members = await _get_members(db, channel_id)
    if username not in members:
        return

    await manager.send_to_channel(
        members,
        {
            "event": "user_typing",
            "channel_id": channel_id,
            "username": username,
            "display_name": display_name,
            "is_typing": is_typing,
        },
        exclude=username,
    )


async def _ws_mark_read(channel_id: str, username: str):
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?",
        (channel_id, username),
    )
    if not await cursor.fetchone():
        return

    cursor2 = await db.execute(
        """SELECT m.id FROM messages m
           WHERE m.channel_id = ? AND m.sender != ?
           AND NOT EXISTS (
               SELECT 1 FROM message_reads mr WHERE mr.message_id = m.id AND mr.username = ?
           )""",
        (channel_id, username, username),
    )
    unread_ids = [r["id"] for r in await cursor2.fetchall()]
    if unread_ids:
        for msg_id in unread_ids:
            await db.execute(
                "INSERT OR IGNORE INTO message_reads (message_id, username) VALUES (?, ?)",
                (msg_id, username),
            )
        await db.commit()

        members = await _get_members(db, channel_id)
        await manager.send_to_channel(
            members,
            {"event": "messages_read", "channel_id": channel_id, "username": username, "up_to_message_id": unread_ids[-1]},
            exclude=username,
        )


# ── Reactions ───────────────────────────────────────────────────

@router.post("/channels/{slug}/messages/{msg_id}/reactions")
async def add_reaction(slug: str, msg_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)
    emoji = data.get("emoji", "").strip()
    if not emoji:
        raise HTTPException(status_code=400, detail="Emoji required")

    cursor = await db.execute(
        "SELECT 1 FROM messages WHERE id = ? AND channel_id = ?", (msg_id, ch["id"])
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Message not found")

    await db.execute(
        "INSERT OR IGNORE INTO message_reactions (message_id, username, emoji, created_at) VALUES (?, ?, ?, ?)",
        (msg_id, username, emoji, now_iso()),
    )
    await db.commit()

    rxn_cursor = await db.execute(
        "SELECT emoji, GROUP_CONCAT(username) as users FROM message_reactions WHERE message_id = ? GROUP BY emoji",
        (msg_id,),
    )
    reactions = []
    for r in await rxn_cursor.fetchall():
        users = r["users"].split(",")
        reactions.append({"emoji": r["emoji"], "count": len(users), "users": users})

    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {
        "event": "reaction_updated",
        "channel_id": ch["id"],
        "message_id": msg_id,
        "emoji": emoji,
        "username": username,
        "action": "add",
        "reactions": reactions,
    })
    return {"ok": True}


@router.delete("/channels/{slug}/messages/{msg_id}/reactions")
async def remove_reaction(slug: str, msg_id: str, data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)
    emoji = data.get("emoji", "").strip()
    if not emoji:
        raise HTTPException(status_code=400, detail="Emoji required")

    await db.execute(
        "DELETE FROM message_reactions WHERE message_id = ? AND username = ? AND emoji = ?",
        (msg_id, username, emoji),
    )
    await db.commit()

    rxn_cursor = await db.execute(
        "SELECT emoji, GROUP_CONCAT(username) as users FROM message_reactions WHERE message_id = ? GROUP BY emoji",
        (msg_id,),
    )
    reactions = []
    for r in await rxn_cursor.fetchall():
        users = r["users"].split(",")
        reactions.append({"emoji": r["emoji"], "count": len(users), "users": users})

    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {
        "event": "reaction_updated",
        "channel_id": ch["id"],
        "message_id": msg_id,
        "emoji": emoji,
        "username": username,
        "action": "remove",
        "reactions": reactions,
    })
    return {"ok": True}


# ── Threads ─────────────────────────────────────────────────────

@router.get("/channels/{slug}/messages/{msg_id}/thread")
async def get_thread(
    slug: str, msg_id: str,
    username: str = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)

    # Parent message
    parent_cursor = await db.execute(
        "SELECT * FROM messages WHERE id = ? AND channel_id = ?", (msg_id, ch["id"])
    )
    parent_row = await parent_cursor.fetchone()
    if not parent_row:
        raise HTTPException(status_code=404, detail="Message not found")
    parent = await _build_message(db, parent_row)

    # Replies
    cursor = await db.execute(
        "SELECT * FROM messages WHERE reply_to = ? AND channel_id = ? ORDER BY timestamp ASC LIMIT ?",
        (msg_id, ch["id"], limit),
    )
    rows = await cursor.fetchall()
    replies = [await _build_message(db, r) for r in rows]

    return {"parent": parent, "replies": replies}


# ── Pin / Unpin ──────────────────────────────────────────────

@router.get("/channels/{slug}/pinned")
async def get_pinned_messages(slug: str, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)
    cursor = await db.execute(
        "SELECT * FROM messages WHERE channel_id = ? AND is_pinned = 1 ORDER BY pinned_at ASC",
        (ch["id"],),
    )
    rows = await cursor.fetchall()
    return [await _build_message(db, r) for r in rows]


@router.post("/channels/{slug}/messages/{msg_id}/pin")
async def pin_message(slug: str, msg_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE id = ? AND channel_id = ?", (msg_id, ch["id"])
    )
    msg_row = await cursor.fetchone()
    if not msg_row:
        raise HTTPException(status_code=404, detail="Message not found")

    now = now_iso()
    await db.execute(
        "UPDATE messages SET is_pinned = 1, pinned_by = ?, pinned_at = ? WHERE id = ?",
        (username, now, msg_id),
    )
    await db.commit()

    members = await _get_members(db, ch["id"])
    display_name = await get_display_name(username)
    msg = await _build_message(db, await (await db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,))).fetchone())
    await manager.send_to_channel(members, {
        "event": "message_pinned",
        "channel_id": ch["id"],
        "message_id": msg_id,
        "pinned_by": username,
        "pinned_by_name": display_name,
        "pinned_at": now,
        "message": msg,
    })
    return {"ok": True}


@router.delete("/channels/{slug}/messages/{msg_id}/pin")
async def unpin_message(slug: str, msg_id: str, username: str = Depends(get_current_user)):
    db = await get_db()
    ch = await _get_channel_by_slug(db, slug, username)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE id = ? AND channel_id = ?", (msg_id, ch["id"])
    )
    msg_row = await cursor.fetchone()
    if not msg_row:
        raise HTTPException(status_code=404, detail="Message not found")

    await db.execute(
        "UPDATE messages SET is_pinned = 0, pinned_by = NULL, pinned_at = NULL WHERE id = ?",
        (msg_id,),
    )
    await db.commit()

    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {
        "event": "message_unpinned",
        "channel_id": ch["id"],
        "message_id": msg_id,
    })
    return {"ok": True}
