"""Web Push Notifications — subscription management and push sending."""

import asyncio
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends
from helpers import get_current_user, DATA_DIR

router = APIRouter(prefix="/api/push", tags=["push"])

SUBSCRIPTIONS_FILE = DATA_DIR / "push_subscriptions.json"

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
# Raw URL-safe base64 EC private key (32 bytes, no PEM wrapper)
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_EMAIL = os.getenv("VAPID_EMAIL", "mailto:admin@example.com")


def _load_subscriptions() -> dict:
    if SUBSCRIPTIONS_FILE.exists():
        with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_subscriptions(data: dict):
    SUBSCRIPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── REST Endpoints ─────────────────────────────────────────────────────────────

@router.get("/vapid-public-key")
async def get_vapid_public_key():
    return {"publicKey": VAPID_PUBLIC_KEY}


@router.post("/subscribe")
async def subscribe(data: dict, username: str = Depends(get_current_user)):
    """Save a push subscription for the current user."""
    endpoint = data.get("endpoint")
    keys = data.get("keys")
    if not endpoint or not keys:
        return {"error": "Invalid subscription"}

    subs = _load_subscriptions()
    user_subs = subs.get(username, [])

    # Avoid duplicates — replace if same endpoint exists
    user_subs = [s for s in user_subs if s.get("endpoint") != endpoint]
    user_subs.append({"endpoint": endpoint, "keys": keys})
    subs[username] = user_subs
    _save_subscriptions(subs)
    return {"ok": True}


@router.delete("/subscribe")
async def unsubscribe(data: dict, username: str = Depends(get_current_user)):
    """Remove a push subscription (called on logout)."""
    endpoint = data.get("endpoint", "")
    subs = _load_subscriptions()
    if username in subs:
        if endpoint:
            subs[username] = [s for s in subs[username] if s.get("endpoint") != endpoint]
        else:
            subs[username] = []
        _save_subscriptions(subs)
    return {"ok": True}


# ── Push sender (called from messaging / videocall routes) ─────────────────────

async def send_push_to_user(username: str, payload: dict):
    """Send a Web Push notification to all subscriptions of a user."""
    private_key = VAPID_PRIVATE_KEY
    if not private_key or not VAPID_PUBLIC_KEY:
        return  # VAPID not configured

    subs = _load_subscriptions()
    user_subs = subs.get(username, [])
    if not user_subs:
        return

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return

    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    dead_endpoints = []

    def _send_one(sub):
        endpoint_short = sub["endpoint"][:60]
        try:
            webpush(
                subscription_info=sub,
                data=payload_bytes,
                vapid_private_key=private_key,
                vapid_claims={"sub": VAPID_EMAIL},
                content_encoding="aes128gcm",
            )
            print(f"[push] ✓ sent to {endpoint_short}...")
            return None
        except WebPushException as ex:
            status = ex.response.status_code if ex.response is not None else "no-response"
            body = ex.response.text[:200] if ex.response is not None else str(ex)
            print(f"[push] ✗ WebPushException {status} → {endpoint_short}...: {body}")
            if ex.response is not None and ex.response.status_code in (404, 410):
                return sub["endpoint"]
            return None
        except Exception as ex:
            print(f"[push] ✗ Exception → {endpoint_short}...: {ex}")
            return None

    for sub in user_subs:
        dead = await asyncio.to_thread(_send_one, sub)
        if dead:
            dead_endpoints.append(dead)

    # Clean up dead subscriptions
    if dead_endpoints:
        subs[username] = [s for s in user_subs if s["endpoint"] not in dead_endpoints]
        _save_subscriptions(subs)
