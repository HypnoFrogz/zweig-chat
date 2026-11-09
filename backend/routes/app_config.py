"""Версии мобильного приложения и «Что нового».

Что здесь живёт:

* **Минимальная версия** для Android и iOS. Приложение старее — показывает
  экран «Обновите приложение» и дальше не пускает. Пусто — не блокируем.
* **Рекомендуемая версия** — вышла в магазинах. Кто старее, видит плашку
  «Доступна новая версия» над чатами, а через `nag_after_days` дней —
  карточку при каждом запуске. Не блокирует.
* **«Что нового»** для конкретной версии: карточка после обновления (до трёх
  пунктов), одна подсказка у новой кнопки и метки «новое» на кнопках.
* **Новость карточкой** — объявление из adm с галочкой «карточкой»: после
  запуска (или сразу, если приложение открыто) выезжает карточка с текстом,
  по желанию — картинкой и кнопкой-ссылкой. Один раз; старше двух недель
  не показываем — устарела.
* **Отметки «уже видел»** у каждого человека — чтобы карточка и метки не
  появлялись снова на другом его телефоне.

Задаёт всё это оператор в adm.zweig.services: панель заходит на сервер
ключом деплоя и вызывает `PUT /api/admin/app-config` изнутри контейнера —
так же, как рассылки и модули. Хранится в `app_config.json` рядом с базой.

`GET /api/app/config` открыт без входа: экран «Обновите» должен работать и
у того, кто ещё не вошёл.
"""

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from database import get_db
from helpers import DATA_DIR, get_current_user, require_admin

router = APIRouter(prefix="/api", tags=["app-config"])

_PATH = DATA_DIR / "app_config.json"
_VERSION = re.compile(r"^\d{1,3}(\.\d{1,3}){0,3}$")
PLATFORMS = ("android", "ios")
# Куда можно поставить метку и подсказку — кнопки, которые приложение знает.
TARGETS = ("inbox", "compose", "folders", "chats", "files", "settings")
ICONS = ("bell", "photo", "clock", "forward", "folder", "task", "call", "lock", "star", "chat", "bolt", "shield")

STORE = {
    "android": "https://play.google.com/store/apps/details?id=ru.nebenan.app",
    "ios": "https://apps.apple.com/ru/app/zweig-chat/id6796614898",
}

_cache: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_PATH.read_text("utf-8"))
        except FileNotFoundError:
            _cache = {}
        except Exception as e:  # noqa: BLE001
            print(f"[app-config] app_config.json unreadable: {e}")
            _cache = {}
    return _cache


def _ver(v, field: str) -> str:
    v = str(v or "").strip()
    if v and not _VERSION.match(v):
        raise HTTPException(status_code=400, detail=f"{field}: версия вида 2.1.1")
    return v


def _text(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _clean_whats_new(wn) -> dict | None:
    if not wn:
        return None
    if not isinstance(wn, dict):
        raise HTTPException(status_code=400, detail="whats_new: объект")
    version = _ver(wn.get("version"), "whats_new.version")
    if not version:
        raise HTTPException(status_code=400, detail="whats_new: укажите версию")
    items = []
    for it in (wn.get("items") or [])[:3]:
        if not isinstance(it, dict):
            continue
        title = _text(it.get("title"), 60)
        if not title:
            continue
        icon = it.get("icon") if it.get("icon") in ICONS else "star"
        items.append({"icon": icon, "title": title, "subtitle": _text(it.get("subtitle"), 80)})
    tip = wn.get("tip")
    if isinstance(tip, dict) and tip.get("target") in TARGETS and _text(tip.get("title"), 60):
        tip = {"target": tip["target"], "title": _text(tip.get("title"), 60), "body": _text(tip.get("body"), 140)}
    else:
        tip = None
    badges = [b for b in dict.fromkeys(wn.get("badges") or []) if b in TARGETS]
    if not items and not tip and not badges:
        return None
    return {
        "version": version,
        "title": _text(wn.get("title"), 40) or "Что нового",
        "items": items,
        "tip": tip,
        "badges": badges,
    }


def snapshot() -> dict:
    c = _load()
    mv = c.get("min_version") or {}
    lv = c.get("latest_version") or {}
    lp = c.get("latest_published_at") or {}
    wn = c.get("whats_new")
    return {
        "min_version": {p: mv.get(p, "") for p in PLATFORMS},
        "latest_version": {p: lv.get(p, "") for p in PLATFORMS},
        "latest_published_at": {p: lp.get(p, "") for p in PLATFORMS},
        "nag_after_days": int(c.get("nag_after_days", 7)),
        "store": {p: (c.get("store") or {}).get(p) or STORE[p] for p in PLATFORMS},
        "whats_new": wn,
        "news": c.get("news"),
        "updated_at": c.get("updated_at", ""),
    }


def save(data: dict) -> dict:
    """Частичное обновление: что не передали — остаётся как было."""
    c = dict(_load())
    if "min_version" in data:
        mv = data.get("min_version") or {}
        if not isinstance(mv, dict):
            raise HTTPException(status_code=400, detail="min_version: {android, ios}")
        c["min_version"] = {p: _ver(mv.get(p), f"min_version.{p}") for p in PLATFORMS}
    if "latest_version" in data:
        lv = data.get("latest_version") or {}
        if not isinstance(lv, dict):
            raise HTTPException(status_code=400, detail="latest_version: {android, ios}")
        old = c.get("latest_version") or {}
        pub = dict(c.get("latest_published_at") or {})
        new = {p: _ver(lv.get(p), f"latest_version.{p}") for p in PLATFORMS}
        for p in PLATFORMS:
            # Отсчёт «через сколько дней настаивать» — с момента, когда
            # версию вписали, а не с каждого повторного «Применить».
            if new[p] and new[p] != old.get(p):
                pub[p] = _now()
            elif not new[p]:
                pub.pop(p, None)
        c["latest_version"] = new
        c["latest_published_at"] = pub
    if "nag_after_days" in data:
        try:
            c["nag_after_days"] = max(0, min(int(data.get("nag_after_days", 7)), 90))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="nag_after_days: число дней")
    if "store" in data:
        st = data.get("store") or {}
        c["store"] = {p: str(st.get(p) or "").strip()[:300] for p in PLATFORMS
                      if str(st.get(p) or "").startswith("https://")}
    if "whats_new" in data:
        wn = _clean_whats_new(data.get("whats_new"))
        if wn:
            old = c.get("whats_new") or {}
            same = {k: old.get(k) for k in ("version", "title", "items", "tip", "badges")} == {
                k: wn[k] for k in ("version", "title", "items", "tip", "badges")}
            # Новый id — только если содержимое поменялось: повторное
            # «Применить» из панели не должно показывать карточку снова.
            wn["id"] = old.get("id") if same and old.get("id") else f"{wn['version']}-{int(datetime.now().timestamp())}"
            wn["published_at"] = old.get("published_at") if same and old.get("published_at") else _now()
        c["whats_new"] = wn
    c["updated_at"] = _now()
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(_PATH)
    global _cache
    _cache = c
    return snapshot()


def _https(v) -> str:
    v = str(v or "").strip()[:500]
    return v if v.startswith("https://") else ""


def set_news(title: str, body: str, data: dict) -> dict:
    """Новость карточкой (зовёт рассылка объявлений, см. app_update.py)."""
    news = {
        "id": f"news-{int(datetime.now().timestamp())}",
        "title": _text(title, 80),
        "body": str(body or "").strip()[:600],
        "image": _https(data.get("image_url")),
        "button_text": _text(data.get("button_text"), 30),
        "button_url": _https(data.get("button_url")),
        "published_at": _now(),
    }
    if not news["button_url"]:
        news["button_text"] = ""
    c = dict(_load())
    c["news"] = news
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(_PATH)
    global _cache
    _cache = c
    return news


@router.get("/app/config")
async def get_app_config():
    return snapshot()


@router.put("/admin/app-config")
async def put_app_config(data: dict, username: str = Depends(get_current_user)):
    await require_admin(username)
    return save(data)


# ── «Уже видел» ───────────────────────────────────────────────

_seen_ready = False


async def _ensure_seen():
    global _seen_ready
    if _seen_ready:
        return
    db = await get_db()
    await db.execute(
        """CREATE TABLE IF NOT EXISTS user_seen (
            username TEXT NOT NULL,
            key      TEXT NOT NULL,
            seen_at  TEXT NOT NULL,
            PRIMARY KEY (username, key)
        )"""
    )
    await db.commit()
    _seen_ready = True


@router.get("/app/seen")
async def get_seen(username: str = Depends(get_current_user)):
    """Что человек уже видел, и новичок ли он для текущего «Что нового».

    Новичок — пришёл после того, как «Что нового» опубликовали: для него
    новое всё приложение, карточка и метки ему не нужны.
    """
    await _ensure_seen()
    db = await get_db()
    cur = await db.execute("SELECT key FROM user_seen WHERE username = ?", (username,))
    keys = [r["key"] for r in await cur.fetchall()]
    fresh = False
    wn = _load().get("whats_new")
    if wn and wn.get("published_at"):
        cur = await db.execute("SELECT created_at FROM users WHERE username = ?", (username,))
        row = await cur.fetchone()
        created = (row["created_at"] if row else "") or ""
        fresh = bool(created) and created > wn["published_at"]
    cur = await db.execute("SELECT created_at FROM users WHERE username = ?", (username,))
    row = await cur.fetchone()
    return {"keys": keys, "fresh": fresh, "created_at": (row["created_at"] if row else "") or ""}


@router.post("/app/seen")
async def mark_seen(data: dict, username: str = Depends(get_current_user)):
    keys = [str(k)[:120] for k in (data.get("keys") or []) if str(k).strip()][:50]
    if not keys:
        return {"ok": True}
    await _ensure_seen()
    db = await get_db()
    now = _now()
    await db.executemany(
        "INSERT OR IGNORE INTO user_seen (username, key, seen_at) VALUES (?, ?, ?)",
        [(username, k, now) for k in keys],
    )
    await db.commit()
    return {"ok": True}
