"""Zweig Messenger — Firebase Cloud Messaging sender."""

import os
import time
from datetime import datetime, timezone, timedelta
import firebase_admin
from firebase_admin import credentials, messaging
from database import get_db

_firebase_app = None


def init_firebase():
    global _firebase_app
    cred_path = os.getenv("FIREBASE_CREDENTIALS", "/app/firebase-service-account.json")
    # Use isfile (not exists): a missing bind-mount source is created as an empty
    # directory by Docker, which would otherwise crash credentials.Certificate().
    if os.path.isfile(cred_path):
        try:
            cred = credentials.Certificate(cred_path)
            _firebase_app = firebase_admin.initialize_app(cred)
            print("[fcm] Firebase initialized successfully")
        except Exception as e:
            print(f"[fcm] WARNING: Firebase init failed ({e}); push notifications disabled")
    else:
        print(f"[fcm] WARNING: Firebase credentials not found at {cred_path}; push notifications disabled")


async def get_user_tokens(username: str) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT fcm_token, platform FROM user_devices WHERE username = ?",
        (username,),
    )
    return [{"token": r["fcm_token"], "platform": r["platform"]} for r in await cursor.fetchall()]


async def send_message_notification(
    recipient: str,
    sender_name: str,
    text: str,
    channel_slug: str,
    channel_name: str,
    unread_count: int = 0,
) -> bool:
    """Push a message to the user's native apps.

    Returns True if at least one device took it, so the caller can decide
    whether a Web Push fallback is still needed — sending both is what gave
    users with the app *and* the PWA two notifications per message.
    """
    if not _firebase_app:
        return False

    devices = await get_user_tokens(recipient)
    if not devices:
        return False

    delivered = False
    preview = (text or "")[:100]

    for device in devices:
        try:
            msg = messaging.Message(
                token=device["token"],
                notification=messaging.Notification(
                    title=sender_name,
                    body=preview,
                ),
                data={
                    "type": "message",
                    "channel_slug": channel_slug,
                    "channel_name": channel_name,
                    "sender_name": sender_name,
                },
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        sound="message_received",
                        channel_id="messages",
                        tag=f"channel_{channel_slug}",
                        notification_count=unread_count,
                    ),
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            sound="message_received.wav",
                            badge=unread_count,
                            category="MESSAGE",
                        ),
                    ),
                ),
            )
            messaging.send(msg)
            delivered = True
        except messaging.UnregisteredError:
            await _remove_token(device["token"])
        except Exception as e:
            print(f"[fcm] Error sending to {recipient}: {e}")

    return delivered


async def send_call_notification(
    recipient: str,
    caller_name: str,
    channel_slug: str,
    room_name: str,
    livekit_token: str,
    livekit_url: str,
    call_id: str | None = None,
    mode: str = "audio",
    expires_at: str | None = None,
):
    if not _firebase_app:
        return

    devices = await get_user_tokens(recipient)
    if not devices:
        return

    # Time-to-live: a call invite is only useful while it's ringing. If FCM can't
    # deliver within the ring window (device asleep / app killed), it must DROP the
    # message instead of delivering a stale "incoming call" long after it ended.
    ring_default = int(os.getenv("CALL_RING_TIMEOUT_SEC", "45"))
    ttl_seconds = ring_default
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            ttl_seconds = int((exp - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            ttl_seconds = ring_default
    ttl_seconds = max(1, min(ttl_seconds, ring_default))
    apns_expiration = str(int(time.time()) + ttl_seconds)

    for device in devices:
        print(f"[fcm] Sending call push to {device['platform']} {device['token'][:30]}... (ttl={ttl_seconds}s)")
        try:
            msg = messaging.Message(
                token=device["token"],
                # Data-only on Android: no auto-display by system,
                # the app's background handler shows the single notification.
                data={
                    "type": "call_invite",
                    "call_id": call_id or "",
                    "channel_slug": channel_slug,
                    "caller_name": caller_name,
                    "room_name": room_name,
                    "livekit_token": livekit_token,
                    "livekit_url": livekit_url,
                    "mode": mode,
                    "expires_at": expires_at or "",
                },
                android=messaging.AndroidConfig(
                    priority="high",
                    # Drop the push if it can't be delivered before the call stops ringing.
                    ttl=timedelta(seconds=ttl_seconds),
                    # No AndroidNotification here — prevents the OS
                    # from showing a notification automatically.
                    # Our Dart background handler shows exactly one notification.
                ),
                apns=messaging.APNSConfig(
                    # apns-expiration: 0 would mean "expire immediately"; we use an
                    # absolute unix time so APNs also drops a stale call invite.
                    headers={"apns-priority": "10", "apns-expiration": apns_expiration},
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            category="CALL",
                            content_available=True,
                        ),
                    ),
                ),
            )
            result = messaging.send(msg)
            print(f"[fcm] ✓ call push sent: {result}")
        except messaging.UnregisteredError:
            print(f"[fcm] ✗ unregistered token: {device['token'][:30]}")
            await _remove_token(device["token"])
        except Exception as e:
            print(f"[fcm] ✗ error sending call to {recipient}: {e}")


async def _remove_token(token: str):
    db = await get_db()
    await db.execute("DELETE FROM user_devices WHERE fcm_token = ?", (token,))
    await db.commit()
