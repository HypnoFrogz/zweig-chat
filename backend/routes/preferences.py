"""Zweig Messenger — User preferences (theme, language, contact names)."""

from fastapi import APIRouter, Depends
from helpers import get_current_user
from database import get_db

router = APIRouter(prefix="/api", tags=["preferences"])


@router.get("/preferences")
async def get_preferences(username: str = Depends(get_current_user)):
    db = await get_db()
    cursor = await db.execute("SELECT theme, language FROM preferences WHERE username = ?", (username,))
    row = await cursor.fetchone()
    return {
        "theme": row["theme"] if row else "dark",
        "language": row["language"] if row else "ru",
    }


@router.put("/preferences")
async def update_preferences(data: dict, username: str = Depends(get_current_user)):
    theme = data.get("theme", "dark")
    if theme not in ("dark", "light"):
        theme = "dark"
    language = data.get("language", "ru")
    if language not in ("ru", "en"):
        language = "ru"

    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO preferences (username, theme, language) VALUES (?, ?, ?)",
        (username, theme, language),
    )
    await db.commit()
    return {"theme": theme, "language": language}


@router.get("/contact-names")
async def get_contact_names(username: str = Depends(get_current_user)):
    """Свои имена собеседников — только те, что поставил спрашивающий.

    Имя видно ровно одному человеку: тому, кто его придумал. Настоящее имя
    собеседника от этого не меняется, и он о переименовании не узнаёт.
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT target, name FROM contact_names WHERE owner = ?", (username,)
    )
    return {r["target"]: r["name"] for r in await cursor.fetchall()}


@router.put("/contact-names/{target}")
async def set_contact_name(target: str, data: dict, username: str = Depends(get_current_user)):
    """Назначить или снять своё имя для собеседника (пустое — снять)."""
    name = (data.get("name") or "").strip()[:80]
    db = await get_db()
    if not name:
        await db.execute(
            "DELETE FROM contact_names WHERE owner = ? AND target = ?",
            (username, target),
        )
    else:
        await db.execute(
            "INSERT OR REPLACE INTO contact_names (owner, target, name) VALUES (?, ?, ?)",
            (username, target, name),
        )
    await db.commit()
    return {"target": target, "name": name}
