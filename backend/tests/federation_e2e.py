"""End-to-end federation test: two servers, handshake, invite, messages.

Needs two backends running locally. FEDERATION_ALLOW_HTTP lets them talk over
plain HTTP and accept a host:port as a domain — both are development-only and
refused when it is off.

    mkdir -p /tmp/fedtest/a/up /tmp/fedtest/b/up

    SERVER_DOMAIN=localhost:8001 FEDERATION_ALLOW_HTTP=1 \
      DATA_DIR=/tmp/fedtest/a UPLOAD_DIR=/tmp/fedtest/a/up \
      SECRET_KEY=devA ADMIN_PASSWORD=admin \
      uvicorn main:app --host 127.0.0.1 --port 8001 &

    SERVER_DOMAIN=localhost:8002 FEDERATION_ALLOW_HTTP=1 \
      DATA_DIR=/tmp/fedtest/b UPLOAD_DIR=/tmp/fedtest/b/up \
      SECRET_KEY=devB ADMIN_PASSWORD=admin \
      uvicorn main:app --host 127.0.0.1 --port 8002 &

    python tests/federation_e2e.py

Start from empty DATA_DIRs: the run mints an invite with max_uses=1 and asserts
it cannot be redeemed twice, so a second run against the same data fails.
Exits non-zero if any check fails.
"""
import httpx, sys, time

A = "http://localhost:8001"
B = "http://localhost:8002"
DOM_A, DOM_B = "localhost:8001", "localhost:8002"

ok = 0
fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def login(base):
    r = httpx.post(f"{base}/api/login", json={"username": "admin", "password": "admin"}, timeout=20)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


print("== waiting for both servers ==")
for base in (A, B):
    for _ in range(60):
        try:
            httpx.get(f"{base}/api/federation/info", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        print(f"server {base} never came up")
        sys.exit(1)

ha, hb = login(A), login(B)
print("== identity ==")
check("A knows its domain", httpx.get(f"{A}/api/federation/info").json()["domain"] == DOM_A)
check("B knows its domain", httpx.get(f"{B}/api/federation/info").json()["domain"] == DOM_B)

print("== stage 1: handshake ==")
r = httpx.post(f"{A}/api/admin/federation/servers", json={"domain": DOM_B}, headers=ha, timeout=30)
check("A requests link with B", r.status_code == 200, r.text[:200])
r = httpx.post(f"{B}/api/admin/federation/servers/{DOM_A}/approve", headers=hb, timeout=30)
check("B approves", r.status_code == 200, r.text[:200])
sa = httpx.get(f"{A}/api/admin/federation/servers", headers=ha).json()["servers"]
sb = httpx.get(f"{B}/api/admin/federation/servers", headers=hb).json()["servers"]
check("A sees B active", any(s["domain"] == DOM_B and s["status"] == "active" for s in sa), sa)
check("B sees A active", any(s["domain"] == DOM_A and s["status"] == "active" for s in sb), sb)
check("secret never leaves the server", all("shared_secret" not in s for s in sa + sb))

print("== peer auth (require_peer) ==")
r = httpx.post(f"{A}/api/federation/message", json={}, timeout=10)
check("unauthenticated S2S call rejected", r.status_code == 401, r.status_code)
r = httpx.post(f"{A}/api/federation/message", json={},
               headers={"Authorization": f"Peer {DOM_B}:wrongsecret"}, timeout=10)
check("wrong secret rejected", r.status_code == 401, r.status_code)

print("== stage 3: invite ==")
r = httpx.post(f"{A}/api/invites", json={"ttl_days": 1, "max_uses": 1, "note": "test"}, headers=ha, timeout=20)
check("A mints an invite", r.status_code == 200, r.text[:200])
inv = r.json()
link = inv["url"]
print(f"  link: {link}")
check("invite starts active", inv["status"] == "active", inv)

r = httpx.post(f"{B}/api/invites/redeem", json={"link": "http://" + DOM_A + "/i/deadbeefdeadbeefdeadbeef"}, headers=hb, timeout=20)
check("unknown token rejected", r.status_code == 404, r.status_code)

r = httpx.post(f"{B}/api/invites/redeem", json={"link": link}, headers=hb, timeout=30)
check("B redeems the invite", r.status_code == 200, r.text[:300])
red = r.json() if r.status_code == 200 else {}
check("B got a channel", bool(red.get("channel_id")), red)
check("remote user is qualified", red.get("user", {}).get("username") == f"admin@{DOM_A}", red)

r = httpx.get(f"{A}/api/invites", headers=ha).json()
check("invite is now used up", r[0]["used_count"] == 1 and r[0]["status"] == "used_up", r[0])
r = httpx.post(f"{B}/api/invites/redeem", json={"link": link}, headers=hb, timeout=30)
check("used-up invite cannot be redeemed again", r.status_code == 404, r.status_code)

print("== directory must not expose stubs ==")
ua = [u["username"] for u in httpx.get(f"{A}/api/users", headers=ha).json()]
check("A's directory hides the remote stub", f"admin@{DOM_B}" not in ua, ua)

print("== stage 4: messages ==")


def slug_for(base, headers, channel_id):
    for c in httpx.get(f"{base}/api/channels", headers=headers, timeout=20).json():
        if c["id"] == channel_id:
            return c["slug"]
    return None


slug_b = slug_for(B, hb, red.get("channel_id"))
check("B can see the new conversation", slug_b is not None)

r = httpx.post(f"{B}/api/channels/{slug_b}/messages", json={"text": "привет с сервера B"}, headers=hb, timeout=30)
check("B sends a message", r.status_code == 200, r.text[:200])

time.sleep(3)
chans_a = httpx.get(f"{A}/api/channels", headers=ha, timeout=20).json()
dm_a = [c for c in chans_a if c["type"] == "direct"]
check("A has the conversation", len(dm_a) == 1, chans_a)
msgs_a = httpx.get(f"{A}/api/channels/{dm_a[0]['slug']}/messages", headers=ha, timeout=20).json()["messages"]
texts_a = [m["text"] for m in msgs_a]
check("A received B's message", "привет с сервера B" in texts_a, texts_a)
if msgs_a:
    check("sender is the qualified remote id", msgs_a[0]["sender"] == f"admin@{DOM_B}", msgs_a[0]["sender"])

r = httpx.post(f"{A}/api/channels/{dm_a[0]['slug']}/messages", json={"text": "ответ с сервера A"}, headers=ha, timeout=30)
check("A replies", r.status_code == 200, r.text[:200])
time.sleep(3)
msgs_b = httpx.get(f"{B}/api/channels/{slug_b}/messages", headers=hb, timeout=20).json()["messages"]
texts_b = [m["text"] for m in msgs_b]
check("B received A's reply", "ответ с сервера A" in texts_b, texts_b)
check("B sees both messages", len(texts_b) == 2, texts_b)

print("== unsolicited message must be refused ==")
# A conversation only exists because an invite was redeemed. Forge a message
# from a user who never got one.
r = httpx.post(f"{A}/api/federation/message", json={
    "id": "forged-1", "to": "admin", "from": "stranger", "text": "should not arrive",
}, headers={"Authorization": f"Peer {DOM_B}:x"}, timeout=10)
check("forged message with bad secret rejected", r.status_code == 401, r.status_code)

print(f"\n{'=' * 40}\nPASSED {ok}   FAILED {fail}")
sys.exit(1 if fail else 0)
