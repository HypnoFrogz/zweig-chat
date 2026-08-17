"""Zweig Messenger — Firebase Cloud Messaging sender."""

import os
import time
from datetime import datetime, timezone, timedelta
import firebase_admin
from firebase_admin import credentials, messaging
from database import get_db
import apns_sender

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


async def get_user_voip_tokens(username: str) -> list[str]:
    """PushKit tokens for the user's iOS devices (used for call invites)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT voip_token FROM user_voip_devices WHERE username = ?",
        (username,),
    )
    return [r["voip_token"] for r in await cursor.fetchall()]


async def _remove_voip_token(token: str):
    db = await get_db()
    await db.execute("DELETE FROM user_voip_devices WHERE voip_token = ?", (token,))
    await db.commit()


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
                # Data-only, like the call push: a top-level `notification` would
                # be auto-displayed by Android *in addition to* the one the app's
                # own handler shows, so the user got the same message twice.
                # iOS still needs a real alert (see the apns block) because it
                # has no equivalent background-display path.
                data={
                    "type": "message",
                    "channel_slug": channel_slug,
                    "channel_name": channel_name,
                    "sender_name": sender_name,
                    # The body travels in data now that there's no notification
                    # block for the client to read it from.
                    "text": preview,
                },
                android=messaging.AndroidConfig(
                    priority="high",
                    # No AndroidNotification: the app displays exactly one.
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            alert=messaging.ApsAlert(
                                title=sender_name,
                                body=preview,
                            ),
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


async def _ring_ios_devices(
    tokens: list[str],
    *,
    call_id: str | None,
    caller_name: str,
    channel_slug: str,
    room_name: str,
    livekit_token: str,
    livekit_url: str,
    mode: str,
    expires_at: str | None,
    ttl_seconds: int,
) -> bool:
    """Ring iOS devices through CallKit. True if at least one push landed.

    The payload keys are the ones flutter_callkit_incoming parses natively on
    the device, so the call reaches CallKit without waking the Dart side.
    """
    payload = {
        "id": call_id or "",
        "nameCaller": caller_name,
        "appName": "Zweig",
        "handle": caller_name,
        # 0 = audio, 1 = video: picks the handset vs camera answer button.
        "type": 1 if mode == "video" else 0,
        "duration": ttl_seconds * 1000,
        "extra": {
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
        "ios": {
            "handleType": "generic",
            "supportsVideo": mode == "video",
            "maximumCallGroups": 1,
            "maximumCallsPerCallGroup": 1,
            # LiveKit owns the audio session once the call is answered.
            "configureAudioSession": True,
        },
    }

    delivered = False
    for token in tokens:
        result = await apns_sender.send_voip_push(token, payload, ttl_seconds)
        if result == "ok":
            delivered = True
        elif result == "stale":
            await _remove_voip_token(token)
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
    devices = await get_user_tokens(recipient)
    voip_devices = await get_user_voip_tokens(recipient)
    if not devices and not voip_devices:
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

    # iOS rings over PushKit, which Firebase cannot deliver — those go straight
    # to APNs. Only when that path is unavailable do iOS devices fall back to
    # the FCM push below, which merely wakes the app to post a notification.
    voip_rang = False
    if voip_devices and apns_sender.is_configured():
        voip_rang = await _ring_ios_devices(
            voip_devices,
            call_id=call_id,
            caller_name=caller_name,
            channel_slug=channel_slug,
            room_name=room_name,
            livekit_token=livekit_token,
            livekit_url=livekit_url,
            mode=mode,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
        )

    if not _firebase_app:
        return

    for device in devices:
        # Skip a device we already rang through CallKit, or it gets a second,
        # redundant wake-up for the same call.
        if voip_rang and device["platform"] == "ios":
            continue
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
                    #
                    # Priority 5, not 10: this is a background (content-available)
                    # push with no alert, and APNs rejects priority 10 for those.
                    # Being a background push it is also throttled by iOS and never
                    # reaches a force-quit app — which is exactly why iOS calls go
                    # through PushKit above. This remains only as the fallback for
                    # deployments with no APNs key configured.
                    headers={"apns-priority": "5", "apns-expiration": apns_expiration},
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
