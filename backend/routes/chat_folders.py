"""ChaosHelper Messenger — Chat Folders (per-user, like Telegram)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from helpers import get_current_user
from database import get_db

router = APIRouter(prefix="/api", tags=["chat_folders"])

_ICON_READY = False


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@router.get("/chat-folders")
async def get_folders(username: str = Depends(get_current_user)):
    """Get all chat folders for the current user."""
    db = await get_db()

    # Ensure table exists
    await _ensure_table(db)

    cursor = await db.execute(
        """SELECT f.id, f.name, f.position, f.icon
           FROM chat_folders f
           WHERE f.username = ?
           ORDER BY f.position ASC""",
        (username,),
    )
    folders = []
    for row in await cursor.fetchall():
        row = dict(row)
        # Get channel slugs for this folder
        cursor2 = await db.execute(
            "SELECT channel_slug FROM chat_folder_channels WHERE folder_id = ? ORDER BY position ASC",
            (row["id"],),
        )
        slugs = [r["channel_slug"] for r in await cursor2.fetchall()]
        folders.append({
            "id": row["id"],
            "name": row["name"],
            "position": row["position"],
            "icon": row["icon"] or "",
            "channel_slugs": slugs,
        })

    return folders


@router.post("/chat-folders")
async def create_folder(data: dict, username: str = Depends(get_current_user)):
    """Create a new chat folder."""
    name = (data.get("name") or "").strip()
    if not name:
        return {"error": "name required"}

    db = await get_db()
    await _ensure_table(db)

    folder_id = str(uuid.uuid4())
    icon = str(data.get("icon") or "")[:40]
    slugs = [str(x) for x in (data.get("channel_slugs") or []) if str(x).strip()]

    # Get next position
    cursor = await db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM chat_folders WHERE username = ?",
        (username,),
    )
    row = await cursor.fetchone()
    pos = row["next_pos"]

    await db.execute(
        "INSERT INTO chat_folders (id, username, name, position, created_at, icon) VALUES (?, ?, ?, ?, ?, ?)",
        (folder_id, username, name, pos, _now_iso(), icon),
    )
    # Чаты, выбранные прямо при создании. Чат живёт только в одной папке —
    # так же, как при добавлении по одному.
    for i, slug in enumerate(slugs):
        await db.execute(
            """DELETE FROM chat_folder_channels
               WHERE channel_slug = ? AND folder_id IN
               (SELECT id FROM chat_folders WHERE username = ?)""",
            (slug, username),
        )
        await db.execute(
            "INSERT OR IGNORE INTO chat_folder_channels (folder_id, channel_slug, position) VALUES (?, ?, ?)",
            (folder_id, slug, i),
        )
    await db.commit()

    return {"id": folder_id, "name": name, "position": pos, "icon": icon, "channel_slugs": slugs}


@router.put("/chat-folders/{folder_id}")
async def update_folder(folder_id: str, data: dict, username: str = Depends(get_current_user)):
    """Update folder name or channel list."""
    db = await get_db()
    await _ensure_table(db)

    # Verify ownership
    cursor = await db.execute(
        "SELECT * FROM chat_folders WHERE id = ? AND username = ?", (folder_id, username)
    )
    if not await cursor.fetchone():
        return {"error": "Folder not found"}

    # Update name if provided
    if "name" in data:
        name = (data["name"] or "").strip()
        if name:
            await db.execute(
                "UPDATE chat_folders SET name = ? WHERE id = ?", (name, folder_id)
            )

    if "icon" in data:
        await db.execute(
            "UPDATE chat_folders SET icon = ? WHERE id = ?", (str(data["icon"] or "")[:40], folder_id)
        )

    # Update channel_slugs if provided — full replace
    if "channel_slugs" in data:
        slugs = data["channel_slugs"] or []
        await db.execute("DELETE FROM chat_folder_channels WHERE folder_id = ?", (folder_id,))
        for i, slug in enumerate(slugs):
            await db.execute(
                "INSERT INTO chat_folder_channels (folder_id, channel_slug, position) VALUES (?, ?, ?)",
                (folder_id, slug, i),
            )

    await db.commit()
    return {"ok": True}


@router.delete("/chat-folders/{folder_id}")
async def delete_folder(folder_id: str, username: str = Depends(get_current_user)):
    """Delete a chat folder."""
    db = await get_db()
    await _ensure_table(db)

    await db.execute(
        "DELETE FROM chat_folders WHERE id = ? AND username = ?", (folder_id, username)
    )
    await db.execute("DELETE FROM chat_folder_channels WHERE folder_id = ?", (folder_id,))
    await db.commit()
    return {"ok": True}


@router.post("/chat-folders/{folder_id}/channels")
async def add_channel_to_folder(folder_id: str, data: dict, username: str = Depends(get_current_user)):
    """Add a channel to a folder."""
    slug = data.get("channel_slug", "")
    if not slug:
        return {"error": "channel_slug required"}

    db = await get_db()
    await _ensure_table(db)

    # Verify ownership
    cursor = await db.execute(
        "SELECT * FROM chat_folders WHERE id = ? AND username = ?", (folder_id, username)
    )
    if not await cursor.fetchone():
        return {"error": "Folder not found"}

    # Remove from all other folders first (channel can only be in one folder)
    await db.execute(
        """DELETE FROM chat_folder_channels
           WHERE channel_slug = ? AND folder_id IN
           (SELECT id FROM chat_folders WHERE username = ?)""",
        (slug, username),
    )

    # Add to this folder
    cursor2 = await db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM chat_folder_channels WHERE folder_id = ?",
        (folder_id,),
    )
    row = await cursor2.fetchone()
    pos = row["next_pos"]

    await db.execute(
        "INSERT INTO chat_folder_channels (folder_id, channel_slug, position) VALUES (?, ?, ?)",
        (folder_id, slug, pos),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/chat-folders/{folder_id}/channels/{slug}")
async def remove_channel_from_folder(folder_id: str, slug: str, username: str = Depends(get_current_user)):
    """Remove a channel from a folder."""
    db = await get_db()
    await _ensure_table(db)

    # Verify ownership
    cursor = await db.execute(
        "SELECT * FROM chat_folders WHERE id = ? AND username = ?", (folder_id, username)
    )
    if not await cursor.fetchone():
        return {"error": "Folder not found"}

    await db.execute(
        "DELETE FROM chat_folder_channels WHERE folder_id = ? AND channel_slug = ?",
        (folder_id, slug),
    )
    await db.commit()
    return {"ok": True}


async def _ensure_table(db):
    """Create chat_folders tables if they don't exist."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS chat_folders (
            id         TEXT PRIMARY KEY,
            username   TEXT NOT NULL,
            name       TEXT NOT NULL,
            position   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT ''
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS chat_folder_channels (
            folder_id    TEXT NOT NULL,
            channel_slug TEXT NOT NULL,
            position     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (folder_id, channel_slug)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_folders_user ON chat_folders(username)"
    )
    # Значок папки — добавлен позже, старые базы получают столбец здесь.
    global _ICON_READY
    if not _ICON_READY:
        cur = await db.execute("PRAGMA table_info(chat_folders)")
        cols = {r[1] for r in await cur.fetchall()}
        if "icon" not in cols:
            await db.execute("ALTER TABLE chat_folders ADD COLUMN icon TEXT NOT NULL DEFAULT ''")
            await db.commit()
        _ICON_READY = True
