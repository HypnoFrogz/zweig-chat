"""Двухфакторная аутентификация (TOTP, RFC 6238) — по желанию пользователя.

Включает каждый сам в профиле. Кто не включил — входит логином и паролем,
как раньше. Секрет хранится зашифрованным ключом сервера (Fernet от
SECRET_KEY), резервные коды — только хэшами.
"""

import base64
import hashlib
import hmac
import io
import json
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from helpers import SECRET_KEY, now_iso

ISSUER = "Zweig"
STEP = 30
DIGITS = 6
BACKUP_COUNT = 10

_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("2fa:" + SECRET_KEY).encode()).digest()))


async def init_schema(db):
    await db.execute(
        """CREATE TABLE IF NOT EXISTS user_2fa (
            username     TEXT PRIMARY KEY REFERENCES users(username) ON DELETE CASCADE,
            secret_enc   TEXT NOT NULL DEFAULT '',
            pending_enc  TEXT NOT NULL DEFAULT '',
            enabled_at   TEXT,
            backup_json  TEXT NOT NULL DEFAULT '[]',
            last_step    INTEGER NOT NULL DEFAULT 0
        )"""
    )
    await db.commit()


# ── TOTP ──────────────────────────────────────────────────────────

def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def totp_now(secret_b32: str, t: float | None = None) -> str:
    return _hotp(secret_b32, int((t or time.time()) // STEP))


def match_step(secret_b32: str, code: str, window: int = 1) -> int | None:
    """Шаг времени, которому соответствует код (±window), или None."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = int(time.time() // STEP)
    for d in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_b32, now + d), code):
            return now + d
    return None


def otpauth_url(username: str, secret_b32: str, server: str = "") -> str:
    label = quote(f"{ISSUER}:{username}" + (f"@{server}" if server else ""))
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}"


def qr_svg(text: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def enc(s: str) -> str:
    return _fernet.encrypt(s.encode()).decode()


def dec(s: str) -> str:
    try:
        return _fernet.decrypt(s.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


# ── Резервные коды ────────────────────────────────────────────────

def _norm_backup(code: str) -> str:
    return "".join(ch for ch in str(code).lower() if ch.isalnum())


def _hash_backup(code: str) -> str:
    return hashlib.sha256(("2fa-backup:" + _norm_backup(code)).encode()).hexdigest()


def new_backup_codes() -> tuple[list[str], str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = []
    for _ in range(BACKUP_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes, json.dumps([_hash_backup(c) for c in codes])


# ── Состояние пользователя ────────────────────────────────────────

async def get_row(db, username: str):
    cur = await db.execute("SELECT * FROM user_2fa WHERE username = ?", (username,))
    return await cur.fetchone()


async def is_enabled(db, username: str) -> bool:
    row = await get_row(db, username)
    return bool(row and row["enabled_at"] and row["secret_enc"])


async def status(db, username: str) -> dict:
    row = await get_row(db, username)
    enabled = bool(row and row["enabled_at"] and row["secret_enc"])
    left = len(json.loads(row["backup_json"] or "[]")) if enabled else 0
    return {"enabled": enabled, "enabled_at": row["enabled_at"] if enabled else None, "backup_left": left}


async def start_setup(db, username: str) -> str:
    secret = new_secret()
    await db.execute(
        """INSERT INTO user_2fa (username, pending_enc) VALUES (?, ?)
           ON CONFLICT(username) DO UPDATE SET pending_enc = excluded.pending_enc""",
        (username, enc(secret)),
    )
    return secret


async def confirm_setup(db, username: str, code: str) -> list[str] | None:
    row = await get_row(db, username)
    secret = dec(row["pending_enc"]) if row and row["pending_enc"] else ""
    if not secret:
        return None
    step = match_step(secret, code)
    if step is None:
        return None
    codes, hashes = new_backup_codes()
    await db.execute(
        """UPDATE user_2fa SET secret_enc = ?, pending_enc = '', enabled_at = ?, backup_json = ?, last_step = ?
           WHERE username = ?""",
        (enc(secret), now_iso(), hashes, step, username),
    )
    return codes


async def verify(db, username: str, code: str, allow_backup: bool = True) -> str | None:
    """Проверить код входа. Возвращает 'totp' / 'backup' или None.

    Код TOTP нельзя использовать дважды (last_step), резервный код сгорает."""
    row = await get_row(db, username)
    if not row or not row["enabled_at"]:
        return None
    secret = dec(row["secret_enc"])
    if secret:
        step = match_step(secret, code)
        if step is not None and step > (row["last_step"] or 0):
            await db.execute("UPDATE user_2fa SET last_step = ? WHERE username = ?", (step, username))
            return "totp"
    if allow_backup:
        hashes = json.loads(row["backup_json"] or "[]")
        h = _hash_backup(code)
        if _norm_backup(code) and h in hashes:
            hashes.remove(h)
            await db.execute("UPDATE user_2fa SET backup_json = ? WHERE username = ?", (json.dumps(hashes), username))
            return "backup"
    return None


async def regenerate_backup(db, username: str) -> list[str]:
    codes, hashes = new_backup_codes()
    await db.execute("UPDATE user_2fa SET backup_json = ? WHERE username = ?", (hashes, username))
    return codes


async def disable(db, username: str):
    await db.execute("DELETE FROM user_2fa WHERE username = ?", (username,))


# ── Ограничение попыток ──────────────────────────────────────────
# В памяти процесса (сервер — один процесс uvicorn): 5 неверных кодов
# за 15 минут на пользователя — дальше пауза до конца окна.

_FAILS: dict[str, list[float]] = {}
_WINDOW = 15 * 60
_LIMIT = 5


def locked(username: str) -> bool:
    t = time.time()
    lst = [x for x in _FAILS.get(username, []) if t - x < _WINDOW]
    _FAILS[username] = lst
    return len(lst) >= _LIMIT


def note_fail(username: str):
    _FAILS.setdefault(username, []).append(time.time())


def clear_fails(username: str):
    _FAILS.pop(username, None)
