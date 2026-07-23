"""ChaosHelper Messenger — shared utilities."""

import os
import re
import json
import hashlib
import secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext

SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-secret-change-me")
REFRESH_SECRET_KEY = os.getenv("REFRESH_SECRET_KEY", SECRET_KEY)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "90"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Russian → Latin transliteration map ──────────────────────────
_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}


def slugify(text: str, max_len: int = 50) -> str:
    """Transliterate Russian text and produce a URL-friendly slug."""
    text = text.lower().strip()
    result = ""
    for ch in text:
        result += _TRANSLIT.get(ch, ch)
    result = re.sub(r'[^a-z0-9]+', '-', result).strip('-')
    return result[:max_len] if result else ""


# ── Auth helpers ─────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "exp": expire, "type": "access"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(username: str, session_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": username,
        "sid": session_id,
        "jti": secrets.token_urlsafe(16),
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, REFRESH_SECRET_KEY, algorithm=ALGORITHM)


def create_token(username: str) -> str:
    """Backward-compatible alias used by legacy callers."""
    return create_access_token(username)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """FastAPI dependency — returns username from Bearer token."""
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        token_type = payload.get("type")
        # Keep compatibility with old access tokens without explicit type.
        if token_type not in (None, "access"):
            raise HTTPException(status_code=401, detail="Invalid token type")
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def decode_token_from_string(token_str: str) -> str | None:
    """Decode JWT from raw string (for WebSocket auth)."""
    try:
        payload = jwt.decode(token_str, SECRET_KEY, algorithms=[ALGORITHM])
        token_type = payload.get("type")
        if token_type not in (None, "access"):
            return None
        return payload.get("sub")
    except JWTError:
        return None


def decode_refresh_token(token_str: str) -> dict:
    """Decode refresh token payload and validate token type."""
    try:
        payload = jwt.decode(token_str, REFRESH_SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token type")
        if not payload.get("sub") or not payload.get("sid"):
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


async def get_current_user_info(username: str) -> dict:
    """Get full user row from DB, raise if blocked."""
    from database import get_db
    db = await get_db()
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="User not found")
    user = dict(row)
    if user.get("blocked"):
        raise HTTPException(status_code=403, detail="Account is blocked")
    return user


async def require_admin(username: str):
    """Raise 403 if user is not admin."""
    user = await get_current_user_info(username)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_display_name(username: str) -> str:
    """Get display name from SQLite."""
    from database import get_db
    db = await get_db()
    cursor = await db.execute("SELECT display_name FROM users WHERE username = ?", (username,))
    row = await cursor.fetchone()
    return row["display_name"] if row else username


# ── File system helpers ──────────────────────────────────────────

def user_root(username: str) -> Path:
    p = UPLOAD_DIR / username
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_path(username: str, rel: str) -> Path:
    base = user_root(username)
    target = (base / rel).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    return target


def get_meta_path(username: str) -> Path:
    return UPLOAD_DIR / f".meta_{username}.json"


def load_meta(username: str) -> dict:
    mp = get_meta_path(username)
    if mp.exists():
        with open(mp, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_meta(username: str, meta: dict):
    mp = get_meta_path(username)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def set_item_date(username: str, rel_path: str):
    meta = load_meta(username)
    meta[rel_path] = datetime.now(timezone.utc).isoformat()
    save_meta(username, meta)


def now_iso() -> str:
    """UTC now in ISO format."""
    return datetime.now(timezone.utc).isoformat()
