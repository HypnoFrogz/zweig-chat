"""Direct APNs client for PushKit VoIP pushes (iOS incoming calls).

Firebase cannot deliver VoIP pushes — they must go to APNs itself, on a
separate topic (`<bundle-id>.voip`) and to a separate device token issued by
PushKit. Everything else (message notifications) still goes through FCM.

Auth is token-based (a .p8 key from the Apple Developer portal), not a VoIP
certificate: one key covers every app on the team and does not expire yearly.

Configuration (all via environment):
    APNS_KEY_PATH   path to the .p8 file           (default /app/apns-key.p8)
    APNS_KEY_ID     the key's 10-character Key ID
    APNS_TEAM_ID    Apple Developer Team ID
    APNS_BUNDLE_ID  bundle id of the iOS app that will receive the pushes
    APNS_SANDBOX    "1" to target the sandbox gateway (development builds)

With no key configured every send is a no-op, so a deployment that does not
use iOS calls keeps working unchanged.
"""

import os
import time
import json
import asyncio

import httpx
from jose import jwt

_PROD_HOST = "https://api.push.apple.com"
_SANDBOX_HOST = "https://api.sandbox.push.apple.com"

# Apple rejects a token older than 1 hour and throttles refreshes more frequent
# than every 20 minutes, so sit comfortably between the two.
_TOKEN_TTL_SEC = 45 * 60

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_cached_token: tuple[str, float] | None = None


def _key_pem() -> str | None:
    path = os.getenv("APNS_KEY_PATH", "/app/apns-key.p8")
    try:
        with open(path, "r") as fh:
            return fh.read()
    except OSError:
        return None


def is_configured() -> bool:
    return bool(
        os.getenv("APNS_KEY_ID")
        and os.getenv("APNS_TEAM_ID")
        and os.getenv("APNS_BUNDLE_ID")
        and _key_pem()
    )


def _auth_token() -> str | None:
    """Signed JWT for APNs, cached until it approaches Apple's 1-hour limit."""
    global _cached_token

    now = time.time()
    if _cached_token and now < _cached_token[1]:
        return _cached_token[0]

    pem = _key_pem()
    key_id = os.getenv("APNS_KEY_ID")
    team_id = os.getenv("APNS_TEAM_ID")
    if not (pem and key_id and team_id):
        return None

    try:
        token = jwt.encode(
            {"iss": team_id, "iat": int(now)},
            pem,
            algorithm="ES256",
            headers={"kid": key_id},
        )
    except Exception as e:
        print(f"[apns] ✗ could not sign auth token: {e}")
        return None

    _cached_token = (token, now + _TOKEN_TTL_SEC)
    return token


async def _get_client() -> httpx.AsyncClient:
    """One shared HTTP/2 client — APNs requires HTTP/2 and rewards reuse."""
    global _client
    async with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(http2=True, timeout=10.0)
        return _client


async def close() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def send_voip_push(token: str, payload: dict, ttl_seconds: int) -> str:
    """Ring one device, on whichever APNs environment its token belongs to.

    A device token carries no hint of its environment, and the two gateways
    reject each other's tokens. Builds installed from Xcode register against
    the sandbox while App Store and TestFlight builds register against
    production, so a server pinned to one environment silently fails to ring
    the other — the kind of breakage that only shows up in production, since
    testing is done with the very builds that do work.

    So: try the likely gateway, and treat "bad token" as a hint to try the
    other one rather than as proof the token is dead. Only a token both
    gateways reject is really gone.

    Returns "ok" when APNs accepted the push, "stale" when the token is dead
    and should be deleted, or "error" for anything transient.
    """
    auth = _auth_token()
    if not auth:
        return "error"

    # APNS_SANDBOX only reorders the attempts — it no longer excludes a
    # gateway, so leaving it set can cost a retry but cannot break calls.
    hosts = (
        [_SANDBOX_HOST, _PROD_HOST]
        if os.getenv("APNS_SANDBOX") == "1"
        else [_PROD_HOST, _SANDBOX_HOST]
    )

    for host in hosts:
        result = await _post_to_gateway(host, token, payload, ttl_seconds, auth)
        if result == "ok":
            return "ok"
        # Transient failures say nothing about the environment; retrying the
        # other gateway would only double the damage of an APNs outage.
        if result == "error":
            return "error"

    print(f"[apns] ✗ token rejected by both gateways: {token[:16]}...")
    return "stale"


async def _post_to_gateway(
    host: str, token: str, payload: dict, ttl_seconds: int, auth: str
) -> str:
    bundle_id = os.getenv("APNS_BUNDLE_ID", "")

    headers = {
        "authorization": f"bearer {auth}",
        # VoIP pushes go to a dedicated topic, not the plain bundle id.
        "apns-topic": f"{bundle_id}.voip",
        "apns-push-type": "voip",
        # Valid for VoIP: this push must wake the device immediately.
        "apns-priority": "10",
        # Drop the invite rather than ring long after the call stopped.
        "apns-expiration": str(int(time.time()) + max(1, ttl_seconds)),
    }

    try:
        client = await _get_client()
        response = await client.post(
            f"{host}/3/device/{token}",
            headers=headers,
            content=json.dumps(payload),
        )
    except Exception as e:
        print(f"[apns] ✗ send failed for {token[:16]}...: {e}")
        return "error"

    env = "sandbox" if host == _SANDBOX_HOST else "production"

    if response.status_code == 200:
        print(f"[apns] ✓ voip push sent to {token[:16]}... ({env})")
        return "ok"

    try:
        reason = response.json().get("reason", "")
    except Exception:
        reason = response.text[:200]

    # "Wrong gateway" and "dead token" are the same response, so this only
    # means the token is unusable *here* — the caller decides after trying
    # both environments.
    if response.status_code == 410 or reason in ("BadDeviceToken", "Unregistered"):
        return "stale"

    print(f"[apns] ✗ {response.status_code} from {env} for {token[:16]}...: {reason}")
    return "error"
