"""Ручки ленты «Важное» и модулей сервера."""
from fastapi import APIRouter, Depends, HTTPException, Query

import features
import notifications
from database import get_db
from helpers import get_current_user, require_admin

router = APIRouter(prefix="/api", tags=["inbox"])


@router.get("/notifications")
async def list_notifications(kind: str = Query("all"), limit: int = Query(50), before: str = Query(""),
                             username: str = Depends(get_current_user)):
    db = await get_db()
    if kind not in ("all", "tasks", "notices", "mentions"):
        kind = "all"
    items = await notifications.list_for(db, username, kind, limit, before)
    return {"items": items, "unread": await notifications.unread_count(db, username)}


@router.get("/notifications/unread")
async def notifications_unread(username: str = Depends(get_current_user)):
    db = await get_db()
    return {"unread": await notifications.unread_count(db, username)}


@router.post("/notifications/read")
async def notifications_read(data: dict, username: str = Depends(get_current_user)):
    db = await get_db()
    n = await notifications.mark_read(
        db, username, ids=data.get("ids"), all_=bool(data.get("all")),
        task_id=str(data.get("task_id") or ""), channel_slug=str(data.get("channel_slug") or ""),
    )
    return {"ok": True, "changed": n, "unread": await notifications.unread_count(db, username)}


@router.get("/features")
async def get_features(username: str = Depends(get_current_user)):
    return {"features": features.snapshot()}


@router.put("/admin/features")
async def put_features(data: dict, username: str = Depends(get_current_user)):
    """Включить/выключить модуль. Вызывает панель оператора через docker exec,
    но годится и администратору сервера руками: {"tracker": {"enabled": true, "until": "2026-12-31"}}."""
    await require_admin(username)
    try:
        snap = features.save(data.get("features", data))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await features.broadcast()
    return {"features": snap}


async def require_tracker():
    """Подключается ко всем ручкам трекера: модуль выключен — ответ 403 с понятной причиной."""
    if not features.enabled("tracker"):
        raise HTTPException(status_code=403, detail="module_disabled")
