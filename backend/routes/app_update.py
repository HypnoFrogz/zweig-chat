"""Уведомление «Вышла новая версия Zweig» всем пользователям этого сервера.

Кнопка в админке мессенджера (Администрирование → Обновления). Сервер сам
рассылает push на телефоны своих пользователей по токенам, которые у него уже
есть, — центральный сервер для этого не нужен, и до организаций со своим
сервером ничего не доходит без решения их администратора.

Телефоны: push с видимым уведомлением (Android — блок notification, iOS —
alert), поэтому его покажут и версии приложения, которые ещё не знают тип
`app_update`. Нажатие ведёт в стор.
Веб и компьютер: событие по WebSocket всем, кто сейчас в сети, — страница
показывает плашку «Обновить». Интерфейс у них и так берётся с сервера, так
что достаточно перезагрузки.
"""

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException

from database import get_db
from helpers import get_current_user, require_admin
from ws_manager import manager

router = APIRouter(prefix="/api/admin", tags=["admin"])

APP_STORE_URL = "https://apps.apple.com/ru/app/zweig-chat/id6796614898"
PLAY_URL = "https://play.google.com/store/apps/details?id=ru.nebenan.app"
DEFAULT_TITLE = "Вышла новая версия Zweig"
DEFAULT_BODY = "Обновите приложение, чтобы получить новые возможности и исправления."
COOLDOWN_SEC = 10 * 60

_last_sent = 0.0


async def _device_tokens(platforms: set[str]) -> list[tuple[str, str]]:
    """Токены телефонов с живым входом, без повторов: (token, platform)."""
    db = await get_db()
    cur = await db.execute(
        """SELECT DISTINCT d.fcm_token, d.platform FROM user_devices d
           JOIN user_sessions s ON s.id = d.session_id
           WHERE s.revoked_at IS NULL"""
    )
    return [(r["fcm_token"], r["platform"]) for r in await cur.fetchall() if r["platform"] in platforms]


def _send_push(tokens: list[tuple[str, str]], title: str, body: str) -> dict:
    """Отправить push. Возвращает счётчики по платформам и мёртвые токены."""
    import fcm_sender
    out = {"android": 0, "ios": 0, "failed": 0, "dead": [], "enabled": True}
    if not getattr(fcm_sender, "_firebase_app", None):
        out["enabled"] = False
        return out
    from firebase_admin import messaging

    for token, platform in tokens:
        url = APP_STORE_URL if platform == "ios" else PLAY_URL
        msg = messaging.Message(
            token=token,
            data={"type": "app_update", "url": url, "title": title, "text": body},
            # Видимое уведомление: его показывает сама система, даже если
            # установленная версия приложения не знает тип app_update.
            notification=messaging.Notification(title=title, body=body),
            android=messaging.AndroidConfig(priority="normal"),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(alert=messaging.ApsAlert(title=title, body=body), sound="default"),
                ),
            ),
        )
        try:
            messaging.send(msg)
            out["ios" if platform == "ios" else "android"] += 1
        except messaging.UnregisteredError:
            out["dead"].append(token)
            out["failed"] += 1
        except Exception as e:  # одна плохая запись не должна сорвать рассылку
            print(f"[app-update] push failed {token[:20]}…: {e}")
            out["failed"] += 1
    return out


@router.get("/app-update/stats")
async def app_update_stats(username: str = Depends(get_current_user)):
    await require_admin(username)
    tokens = await _device_tokens({"android", "ios"})
    online = len(getattr(manager, "active_connections", {}) or {})
    return {
        "android": sum(1 for _, p in tokens if p == "android"),
        "ios": sum(1 for _, p in tokens if p == "ios"),
        "online": online,
        "cooldown": max(0, int(COOLDOWN_SEC - (time.time() - _last_sent))),
    }


@router.post("/app-update/notify")
async def notify_app_update(data: dict, admin: str = Depends(get_current_user)):
    await require_admin(admin)
    global _last_sent
    wait = COOLDOWN_SEC - (time.time() - _last_sent)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"Уведомление уже отправлено. Повторить можно через {int(wait // 60) + 1} мин.")

    platforms = {p for p in (data.get("platforms") or ["android", "ios", "web"]) if p in ("android", "ios", "web")}
    if not platforms:
        raise HTTPException(status_code=400, detail="Выберите хотя бы одну платформу")
    title = (data.get("title") or DEFAULT_TITLE).strip()[:80]
    body = (data.get("body") or DEFAULT_BODY).strip()[:240]
    version = (data.get("version") or "").strip()[:20]

    _last_sent = time.time()
    result = {"android": 0, "ios": 0, "failed": 0, "web": 0}

    phone_platforms = platforms & {"android", "ios"}
    if phone_platforms:
        tokens = await _device_tokens(phone_platforms)
        sent = await asyncio.to_thread(_send_push, tokens, title, body)
        result.update(android=sent["android"], ios=sent["ios"], failed=sent["failed"])
        if not sent["enabled"]:
            result["push_disabled"] = True
        if sent["dead"]:
            db = await get_db()
            await db.executemany("DELETE FROM user_devices WHERE fcm_token = ?", [(t,) for t in sent["dead"]])
            await db.commit()

    if "web" in platforms:
        users = list((getattr(manager, "active_connections", {}) or {}).keys())
        await manager.broadcast_all({"event": "app_update", "title": title, "text": body, "version": version})
        result["web"] = len(users)

    print(f"[app-update] by {admin}: {result}")
    return result
