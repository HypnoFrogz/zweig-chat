"""Модули сервера: что включено у этой установки (пока — Zweig Tracker).

Включает и выключает модуль оператор в adm.zweig.services: панель заходит на
сервер ключом деплоя и через `docker exec` вызывает `PUT /api/admin/features`
от имени администратора — так же, как рассылки. Состояние живёт в файле
`features.json` рядом с базой и переживает перезапуск.

Модуль может быть включён навсегда или до даты. Дату сервер проверяет сам:
в полночь после неё модуль выключается, даже если связи с панелью нет.

Выключение ничего не удаляет: проекты и задачи остаются в базе и
возвращаются, как только модуль снова включат.

Чистый self-host без панели: `FEATURES` в `.env` задаёт начальное состояние
(`FEATURES=tracker` — включён, `FEATURES=-tracker` или `none` — выключен).
Без файла и без переменной трекер включён — так он работал до появления модулей.
"""
import asyncio
import json
import os
from datetime import date

from helpers import DATA_DIR

MODULES = ("tracker",)
_PATH = DATA_DIR / "features.json"
_state: dict[str, dict] | None = None
_last_effective: dict | None = None


def _from_env() -> dict[str, dict]:
    raw = os.getenv("FEATURES", "").strip().lower()
    st = {m: {"enabled": True, "until": None} for m in MODULES}
    if not raw:
        return st
    if raw in ("none", "off", "-"):
        return {m: {"enabled": False, "until": None} for m in MODULES}
    items = [x.strip() for x in raw.split(",") if x.strip()]
    listed = {x.lstrip("-+") for x in items}
    for m in MODULES:
        if m in listed:
            st[m]["enabled"] = not any(x == "-" + m for x in items)
        elif not any(x.startswith("-") for x in items):
            # Перечислены только включённые — остальные выключены.
            st[m]["enabled"] = False
    return st


def _load() -> dict[str, dict]:
    global _state
    if _state is None:
        st = _from_env()
        try:
            saved = json.loads(_PATH.read_text("utf-8"))
            for m in MODULES:
                if isinstance(saved.get(m), dict):
                    st[m] = {"enabled": bool(saved[m].get("enabled")), "until": saved[m].get("until") or None,
                             "updated_at": saved[m].get("updated_at", "")}
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"[features] features.json unreadable, using defaults: {e}")
        _state = st
    return _state


def _valid_until(v) -> str | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        raise ValueError("Дата в формате ГГГГ-ММ-ДД")


def enabled(name: str) -> bool:
    s = _load().get(name)
    if not s or not s.get("enabled"):
        return False
    until = s.get("until")
    return not (until and date.today().isoformat() > until)


def snapshot() -> dict:
    """Для клиентов: включён ли модуль сейчас и до какого числа."""
    out = {}
    for m in MODULES:
        s = _load()[m]
        out[m] = {"enabled": enabled(m), "until": s.get("until"), "configured": bool(s.get("enabled"))}
    return out


def save(changes: dict) -> dict:
    from helpers import now_iso
    st = _load()
    for m, v in (changes or {}).items():
        if m not in MODULES or not isinstance(v, dict):
            continue
        st[m] = {"enabled": bool(v.get("enabled")), "until": _valid_until(v.get("until")) if v.get("enabled") else None,
                 "updated_at": now_iso()}
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(_PATH)
    return snapshot()


async def broadcast():
    """Сообщить открытым клиентам: модуль появился или пропал — без перезагрузки."""
    global _last_effective
    from ws_manager import manager
    snap = snapshot()
    _last_effective = {m: snap[m]["enabled"] for m in MODULES}
    await manager.broadcast_all({"event": "features_changed", "features": snap})


async def _warn_admins_expiring():
    """За 3 дня и в последний день — администраторам сервера в «Важное»."""
    from database import get_db
    import notifications
    today = date.today()
    for m in MODULES:
        s = _load()[m]
        if not s.get("enabled") or not s.get("until"):
            continue
        left = (date.fromisoformat(s["until"]) - today).days
        if left not in (3, 0):
            continue
        db = await get_db()
        cur = await db.execute("SELECT username FROM users WHERE role = 'admin' AND COALESCE(blocked, 0) = 0 AND COALESCE(home_server, '') = ''")
        when = date.fromisoformat(s["until"]).strftime("%d.%m.%Y")
        for r in await cur.fetchall():
            await notifications.notify(db, r["username"], "module_expiring", title="Zweig Tracker",
                                       body=f"Трекер работает до {when}" + (" — сегодня последний день" if left == 0 else ""),
                                       dedupe=f"module:{m}:{s['until']}:{left}")


async def run_loop():
    """Раз в час: не истёк ли срок модуля (тогда — событие клиентам) и не пора ли предупредить."""
    global _last_effective
    _last_effective = {m: enabled(m) for m in MODULES}
    while True:
        await asyncio.sleep(3600)
        try:
            now = {m: enabled(m) for m in MODULES}
            if now != _last_effective:
                await broadcast()
            await _warn_admins_expiring()
        except Exception as e:  # noqa: BLE001
            print(f"[features] loop error: {e}")
