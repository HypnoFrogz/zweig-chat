"""Уведомление «Вышла новая версия Zweig» всем пользователям этого сервера.

Кнопка в админке мессенджера (Администрирование → Обновления). Сервер сам
рассылает push на телефоны своих пользователей по токенам, которые у него уже
есть, — центральный сервер для этого не нужен, и до организаций со своим
сервером ничего не доходит без решения их администратора.

Телефоны: push в формате обычного сообщения (см. _send_push), поэтому его
показывают и уже установленные версии приложения. В новых версиях нажатие
ведёт в стор.
Веб и компьютер: событие по WebSocket всем, кто сейчас в сети, — страница
показывает плашку «Обновить». Интерфейс у них и так берётся с сервера, так
что достаточно перезагрузки.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

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
# Служебный «адрес беседы» для уведомления об обновлении (см. _send_push).
UPDATE_SLUG = "__zweig_app_update__"

_last_sent = 0.0


async def _device_tokens(platforms: set[str]) -> list[tuple[str, str]]:
    """Токены телефонов, без повторов: (token, platform).

    Берём устройства с живым входом и те, у кого вход к токену ещё не
    привязан. Таких большинство у старых версий приложения: они получили
    токен до появления «Устройств» и с тех пор его не перерегистрировали, —
    ровно им и нужно напомнить об обновлении. Совсем старые записи (больше
    двух месяцев без обновления) не трогаем, а мёртвые токены Firebase
    вернёт с ошибкой, и они удалятся после рассылки.
    """
    db = await get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    cur = await db.execute(
        """SELECT DISTINCT d.fcm_token, d.platform FROM user_devices d
           LEFT JOIN user_sessions s ON s.id = d.session_id
           WHERE (d.session_id IS NOT NULL AND s.id IS NOT NULL AND s.revoked_at IS NULL)
              OR (d.session_id IS NULL AND COALESCE(NULLIF(d.updated_at, ''), d.created_at) > ?)""",
        (cutoff,),
    )
    return [(r["fcm_token"], r["platform"]) for r in await cur.fetchall() if r["platform"] in platforms]


async def _people_with_phones() -> int:
    db = await get_db()
    cur = await db.execute("SELECT COUNT(DISTINCT username) AS n FROM user_devices")
    return (await cur.fetchone())["n"]


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
        # Формат обычного сообщения из чата: именно его показывают все уже
        # установленные версии приложения — и в фоне, и когда приложение
        # открыто. Отдельный тип `app_update` старые версии молча пропускали,
        # а системное уведомление Android при открытом приложении не выводит.
        # Нажатие в старых версиях просто открывает приложение (беседы
        # UPDATE_SLUG нет), новые по этому slug ведут в стор.
        msg = messaging.Message(
            token=token,
            data={
                "type": "message",
                "kind": "app_update",
                "url": url,
                "channel_slug": UPDATE_SLUG,
                "channel_name": "Zweig",
                "sender_name": title,
                "text": body,
                "is_group": "0",
            },
            android=messaging.AndroidConfig(priority="high"),
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
        "people": await _people_with_phones(),
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
