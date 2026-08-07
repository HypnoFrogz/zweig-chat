"""ChaosHelper Messenger — Channel management (CRUD, members, slug routing)."""

import uuid
import os
import shutil
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
                  u.display_name, u.nickname, u.avatar_path, u.status_text
           FROM channel_members cm
           JOIN users u ON u.username = cm.username
           WHERE cm.channel_id = ?
           ORDER BY cm.role DESC, u.display_name""",
        (channel_id,),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def _count_unread(db, channel_id: str, username: str) -> int:
    cursor = await db.execute(
        """SELECT COUNT(*) FROM messages m
           WHERE m.channel_id = ? AND m.sender != ?
           AND NOT EXISTS (
               SELECT 1 FROM message_reads mr
               WHERE mr.message_id = m.id AND mr.username = ?
           )""",
        (channel_id, username, username),
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


async def get_or_create_direct(db, user_a: str, user_b: str) -> tuple[str, bool]:
    """Find the direct channel between two users, creating it if missing.

    Returns (channel_id, created). Shared with federation: redeeming an invite
    opens exactly this kind of conversation, the only difference being that one
    member is a stub row for someone on another server.
    """
    cursor = await db.execute(
        """SELECT c.id FROM channels c
           JOIN channel_members cm1 ON c.id = cm1.channel_id AND cm1.username = ?
           JOIN channel_members cm2 ON c.id = cm2.channel_id AND cm2.username = ?
           WHERE c.type = 'direct'""",
        (user_a, user_b),
    )
    existing = await cursor.fetchone()
    if existing:
        return existing["id"], False

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
    """List channels the user is a member of."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT c.* FROM channels c
           JOIN channel_members cm ON c.id = cm.channel_id
           WHERE cm.username = ?
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
            if p != username:
                await db.execute(
                    "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
                    (ch_id, p, now),
                )

        await db.commit()

        c3 = await db.execute("SELECT * FROM channels WHERE id = ?", (ch_id,))
        ch = await _build_channel(db, await c3.fetchone(), username)
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
    if "add_members" in data:
        for p in data["add_members"]:
            await db.execute(
                "INSERT OR IGNORE INTO channel_members (channel_id, username, role, joined_at) VALUES (?, ?, 'member', ?)",
                (ch["id"], p, now_iso()),
            )

    removed_users = []
    if "remove_members" in data:
        for p in data["remove_members"]:
            if p != ch["created_by"]:  # Can't remove owner
                await db.execute(
                    "DELETE FROM channel_members WHERE channel_id = ? AND username = ?",
                    (ch["id"], p),
                )
                removed_users.append(p)

    await db.commit()

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

    display_name = await get_display_name(username)
    members = await _get_members(db, ch["id"])
    await manager.send_to_channel(members, {
        "event": "member_left",
        "channel_id": ch["id"],
        "slug": slug,
        "username": username,
        "display_name": display_name,
    })
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
