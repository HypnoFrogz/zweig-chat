"""Журнал использования для дашбордов Grafana.

Кто, когда и с какого устройства пользуется мессенджером. Пишется сюда,
читается Grafana напрямую из SQLite (только чтение, см. deploy/grafana/).

- `activity_hourly` — одна строка на человека, час и тип клиента: из неё
  строятся DAU/WAU/MAU, тепловая карта по часам и дням недели и устройства.
- `session_log` — каждое подключение по вебсокету: начало, конец, длительность.

Срок хранения — ANALYTICS_RETENTION_DAYS (по умолчанию 365 дней): статистика
по сотрудникам — персональные данные, хранить её вечно незачем.
"""

import os
from datetime import datetime, timedelta, timezone

from database import get_db

RETENTION_DAYS = int(os.getenv("ANALYTICS_RETENTION_DAYS", "365"))

# Последний записанный час по (пользователь, клиент): чтобы не ходить в базу
# на каждое событие вебсокета, а только раз в час.
_last_hour: dict[tuple[str, str], str] = {}


def classify_client(client: str | None, user_agent: str | None, platform: str | None = None) -> str:
    """ios / android / mobile / desktop / web.

    Мобильное приложение подключается с client=mobile; новые сборки могут
    прислать и platform=ios|android. Десктоп — тот же веб внутри Electron,
    его видно по User-Agent.
    """
    ua = (user_agent or "").lower()
    p = (platform or "").strip().lower()
    if p in ("ios", "android", "macos", "windows", "linux", "web"):
        return {"macos": "desktop", "windows": "desktop", "linux": "desktop"}.get(p, p)
    if (client or "").lower() == "mobile":
        if "iphone" in ua or "ipad" in ua or "ios" in ua:
            return "ios"
        if "android" in ua:
            return "android"
        return "mobile"
    if "electron" in ua:
        return "desktop"
    return "web"


def _hour(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:00")


async def touch(username: str, client: str) -> None:
    """Отметить, что человек был в сети в этот час с этого клиента."""
    now = datetime.now(timezone.utc)
    hour = _hour(now)
    key = (username, client)
    if _last_hour.get(key) == hour:
        return
    _last_hour[key] = hour
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO activity_hourly (username, hour, client) VALUES (?, ?, ?)",
        (username, hour, client),
    )
    await db.commit()


async def session_start(username: str, client: str, app_version: str = "") -> int:
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = await db.execute(
        "INSERT INTO session_log (username, client, app_version, started_at) VALUES (?, ?, ?, ?)",
        (username, client, (app_version or "")[:32], now),
    )
    await db.commit()
    await touch(username, client)
    return cur.lastrowid


async def session_end(session_id: int | None) -> None:
    if not session_id:
        return
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE session_log SET ended_at = ?,
               duration_sec = CAST((julianday(?) - julianday(started_at)) * 86400 AS INTEGER)
           WHERE id = ? AND ended_at IS NULL""",
        (now, now, session_id),
    )
    await db.commit()


async def on_startup() -> None:
    """Закрыть сеансы, оборванные перезапуском сервера, и убрать старое."""
    db = await get_db()
    # Сколько длился оборванный сеанс, уже не узнать; считаем его нулевым,
    # чтобы не приписать человеку часы, которых не было.
    await db.execute(
        "UPDATE session_log SET ended_at = started_at, duration_sec = 0 WHERE ended_at IS NULL"
    )
    await prune(db)
    await db.commit()


async def prune(db=None) -> None:
    db = db or await get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    await db.execute("DELETE FROM activity_hourly WHERE hour < ?", (_hour(cutoff),))
    await db.execute("DELETE FROM session_log WHERE started_at < ?", (cutoff.isoformat(),))
    await db.execute("DELETE FROM login_log WHERE timestamp < ?", (cutoff.isoformat(),))
