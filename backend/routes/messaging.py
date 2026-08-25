"""Messaging — personal and group chats with WebSocket real-time delivery."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from helpers import get_current_user, load_json_data, save_json_data, load_users, DATA_DIR, decode_token_from_string
from ws_manager import manager
from routes.push import send_push_to_user

router = APIRouter(prefix="/api", tags=["messaging"])

MESSAGES_DIR = DATA_DIR / "messages"
MESSAGES_DIR.mkdir(parents=True, exist_ok=True)


def _load_conversations() -> list:
    return load_json_data("conversations.json", [])


def _save_conversations(convs: list):
    save_json_data("conversations.json", convs)


def _load_messages(conversation_id: str) -> list:
    fp = MESSAGES_DIR / f"{conversation_id}.json"
    if fp.exists():
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_messages(conversation_id: str, messages: list):
    fp = MESSAGES_DIR / f"{conversation_id}.json"
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


def _get_display_name(username: str) -> str:
    users = load_users()
    user = users.get(username, {})
    return user.get("display_name", username)


def _find_direct_conversation(convs: list, user1: str, user2: str):
    for c in convs:
        if c["type"] == "direct" and set(c["participants"]) == {user1, user2}:
            return c
    return None


def _count_unread(messages: list, username: str) -> int:
    count = 0
    for m in messages:
        if m["sender"] != username and username not in m.get("read_by", []):
            count += 1
    return count


# === REST Endpoints ===

@router.get("/messaging/conversations")
async def list_conversations(username: str = Depends(get_current_user)):
    convs = _load_conversations()
    result = []
    for c in convs:
        if username not in c["participants"]:
            continue
        msgs = _load_messages(c["id"])
        unread = _count_unread(msgs, username)
        conv_data = {**c, "unread_count": unread}
        result.append(conv_data)
    result.sort(
        key=lambda x: (x.get("last_message") or {}).get("timestamp", "") or x.get("created_at", ""),
        reverse=True,
    )
    return result


@router.post("/messaging/conversations")
async def create_conversation(data: dict, username: str = Depends(get_current_user)):
    convs = _load_conversations()
    conv_type = data.get("type", "direct")

    if conv_type == "direct":
        participant = data.get("participant", "")
        if not participant or participant == username:
            return {"error": "Invalid participant"}
        existing = _find_direct_conversation(convs, username, participant)
        if existing:
            return existing
        conv = {
            "id": str(uuid.uuid4()),
            "type": "direct",
            "participants": [username, participant],
            "created_by": username,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_message": None,
        }
    else:
        name = data.get("name", "").strip()
        participants = data.get("participants", [])
        if not name:
            return {"error": "Group name required"}
        if username not in participants:
            participants.append(username)
        conv = {
            "id": str(uuid.uuid4()),
            "type": "group",
            "name": name,
            "participants": participants,
            "created_by": username,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_message": None,
        }
        # Send system message
        sys_msg = _create_system_message(conv["id"], username, f"{_get_display_name(username)} created the group")
        _save_messages(conv["id"], [sys_msg])

    convs.append(conv)
    _save_conversations(convs)

    # Notify participants
    for p in conv["participants"]:
        if p != username:
            await manager.send_to_user(p, {"event": "conversation_updated", "conversation": {**conv, "unread_count": 0}})

    return {**conv, "unread_count": 0}


@router.get("/messaging/conversations/{conv_id}")
async def get_conversation(conv_id: str, username: str = Depends(get_current_user)):
    convs = _load_conversations()
    for c in convs:
        if c["id"] == conv_id and username in c["participants"]:
            msgs = _load_messages(c["id"])
            unread = _count_unread(msgs, username)
            return {**c, "unread_count": unread}
    return {"error": "Not found"}


@router.put("/messaging/conversations/{conv_id}")
async def update_conversation(conv_id: str, data: dict, username: str = Depends(get_current_user)):
    convs = _load_conversations()
    for c in convs:
        if c["id"] == conv_id and username in c["participants"] and c["type"] == "group":
            if "name" in data:
                c["name"] = data["name"].strip()
            if "add_participants" in data:
                for p in data["add_participants"]:
                    if p not in c["participants"]:
                        c["participants"].append(p)
                        sys_msg = _create_system_message(conv_id, username, f"{_get_display_name(p)} added to the group")
                        async with manager.get_lock(conv_id):
                            msgs = _load_messages(conv_id)
                            msgs.append(sys_msg)
                            _save_messages(conv_id, msgs)
                        await manager.send_to_conversation(c["participants"], {"event": "new_message", "message": sys_msg})
            if "remove_participants" in data:
                for p in data["remove_participants"]:
                    if p in c["participants"] and p != c["created_by"]:
                        c["participants"].remove(p)
            _save_conversations(convs)
            await manager.send_to_conversation(c["participants"], {"event": "conversation_updated", "conversation": c})
            return c
    return {"error": "Not found or not a group"}


@router.get("/messaging/conversations/{conv_id}/messages")
async def get_messages(
    conv_id: str,
    username: str = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(None),
):
    convs = _load_conversations()
    conv = None
    for c in convs:
        if c["id"] == conv_id and username in c["participants"]:
            conv = c
            break
    if not conv:
        return {"error": "Not found"}

    msgs = _load_messages(conv_id)

    if before:
        idx = next((i for i, m in enumerate(msgs) if m["id"] == before), None)
        if idx is not None:
            msgs = msgs[:idx]

    result = msgs[-limit:]
    return {"messages": result, "has_more": len(msgs) > limit}


@router.post("/messaging/conversations/{conv_id}/read")
async def mark_read(conv_id: str, username: str = Depends(get_current_user)):
    convs = _load_conversations()
    conv = None
    for c in convs:
        if c["id"] == conv_id and username in c["participants"]:
            conv = c
            break
    if not conv:
        return {"error": "Not found"}

    async with manager.get_lock(conv_id):
        msgs = _load_messages(conv_id)
        changed = False
        last_read_id = None
        for m in msgs:
            if username not in m.get("read_by", []):
                m.setdefault("read_by", []).append(username)
                changed = True
                last_read_id = m["id"]
        if changed:
            _save_messages(conv_id, msgs)
            if last_read_id:
                await manager.send_to_conversation(
                    conv["participants"],
                    {"event": "messages_read", "conversation_id": conv_id, "username": username, "up_to_message_id": last_read_id},
                    exclude=username,
                )
    return {"ok": True}


@router.get("/messaging/unread")
async def get_unread(username: str = Depends(get_current_user)):
    convs = _load_conversations()
    total = 0
    by_conversation = {}
    for c in convs:
        if username not in c["participants"]:
            continue
        msgs = _load_messages(c["id"])
        unread = _count_unread(msgs, username)
        if unread > 0:
            by_conversation[c["id"]] = unread
            total += unread
    return {"total_unread": total, "by_conversation": by_conversation}


@router.get("/messaging/presence")
async def get_presence(username: str = Depends(get_current_user)):
    return manager.get_online_users()


@router.post("/messaging/conversations/{conv_id}/messages")
async def send_message_rest(conv_id: str, data: dict, username: str = Depends(get_current_user)):
    """REST fallback for sending messages (used when WebSocket is unavailable)."""
    convs = _load_conversations()
    conv = None
    for c in convs:
        if c["id"] == conv_id and username in c["participants"]:
            conv = c
            break
    if not conv:
        return {"error": "Conversation not found"}

    display_name = _get_display_name(username)
    text = data.get("text", "").strip()
    msg_type = data.get("type", "text")

    if not text and msg_type == "text":
        return {"error": "Empty message"}

    msg = {
        "id": str(uuid.uuid4()),
        "conversation_id": conv_id,
        "sender": username,
        "sender_name": display_name,
        "type": msg_type,
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "read_by": [username],
    }

    if msg_type == "file" and "file" in data:
        msg["file"] = data["file"]

    async with manager.get_lock(conv_id):
        msgs = _load_messages(conv_id)
        msgs.append(msg)
        _save_messages(conv_id, msgs)

    conv["last_message"] = {
        "text": text or (msg.get("file", {}).get("name", "File")),
        "sender": username,
        "sender_name": display_name,
        "timestamp": msg["timestamp"],
    }
    _save_conversations(convs)

    # Notify via WS if anyone is connected
    await manager.send_to_conversation(conv["participants"], {"event": "new_message", "message": msg})

    # Push to offline participants
    await _push_new_message(conv, msg, username)

    return msg


# === WebSocket ===

def _client_kind(websocket: WebSocket, declared: str) -> str:
    """Мобильное приложение это или браузер.

    Новые сборки говорят прямо (`?client=mobile`), у прежних спрашивать нечего
    — но они ходят через dart:io, и его user-agent ни с одним браузером не
    спутать. Ошибка в пользу браузера безопасна: человек получит лишний пуш,
    а не останется без него.
    """
    declared = (declared or "").strip().lower()
    if declared in ("mobile", "android", "ios"):
        return "mobile"
    if declared in ("web", "desktop"):
        return "web"
    ua = (websocket.headers.get("user-agent") or "").lower()
    return "mobile" if ua.startswith("dart/") or "flutter" in ua else "web"


@router.websocket("/ws/messaging")
async def messaging_websocket(
    websocket: WebSocket, token: str = Query(...), client: str = Query(default="")
):
    username = decode_token_from_string(token)
    if not username:
        await websocket.close(code=4003, reason="Unauthorized")
        return

    await manager.connect(username, websocket, kind=_client_kind(websocket, client))
    display_name = _get_display_name(username)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event = data.get("event")

            if event == "ping":
                await manager.send_to_user(username, {"event": "pong"})

            elif event == "send_message":
                await _handle_send_message(data, username, display_name)

            elif event == "typing":
                await _handle_typing(data, username, display_name)

            elif event == "mark_read":
                conv_id = data.get("conversation_id")
                if conv_id:
                    convs = _load_conversations()
                    conv = next((c for c in convs if c["id"] == conv_id and username in c["participants"]), None)
                    if conv:
                        up_to = data.get("up_to_message_id")
                        async with manager.get_lock(conv_id):
                            msgs = _load_messages(conv_id)
                            changed = False
                            for m in msgs:
                                if username not in m.get("read_by", []):
                                    m.setdefault("read_by", []).append(username)
                                    changed = True
                                if m["id"] == up_to:
                                    break
                            if changed:
                                _save_messages(conv_id, msgs)
                        if up_to:
                            await manager.send_to_conversation(
                                conv["participants"],
                                {"event": "messages_read", "conversation_id": conv_id, "username": username, "up_to_message_id": up_to},
                                exclude=username,
                            )

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.disconnect(username, None)
        await manager.broadcast_presence(username, online=False)


async def _handle_send_message(data: dict, username: str, display_name: str):
    conv_id = data.get("conversation_id")
    text = data.get("text", "").strip()
    msg_type = data.get("type", "text")

    if not conv_id or (not text and msg_type == "text"):
        return

    convs = _load_conversations()
    conv = None
    for c in convs:
        if c["id"] == conv_id and username in c["participants"]:
            conv = c
            break
    if not conv:
        return

    msg = {
        "id": str(uuid.uuid4()),
        "conversation_id": conv_id,
        "sender": username,
        "sender_name": display_name,
        "type": msg_type,
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "read_by": [username],
    }

    if msg_type == "file" and "file" in data:
        msg["file"] = data["file"]

    async with manager.get_lock(conv_id):
        msgs = _load_messages(conv_id)
        msgs.append(msg)
        _save_messages(conv_id, msgs)

    # Update last_message in conversation
    conv["last_message"] = {
        "text": text or (msg.get("file", {}).get("name", "File")),
        "sender": username,
        "sender_name": display_name,
        "timestamp": msg["timestamp"],
    }
    _save_conversations(convs)

    # Send to all participants
    await manager.send_to_conversation(
        conv["participants"],
        {"event": "new_message", "message": msg},
    )

    # Push to offline participants
    await _push_new_message(conv, msg, username)


async def _handle_typing(data: dict, username: str, display_name: str):
    conv_id = data.get("conversation_id")
    is_typing = data.get("is_typing", False)
    if not conv_id:
        return

    convs = _load_conversations()
    conv = next((c for c in convs if c["id"] == conv_id and username in c["participants"]), None)
    if not conv:
        return

    await manager.send_to_conversation(
        conv["participants"],
        {
            "event": "user_typing",
            "conversation_id": conv_id,
            "username": username,
            "display_name": display_name,
            "is_typing": is_typing,
        },
        exclude=username,
    )


async def _push_new_message(conv: dict, msg: dict, sender: str):
    """Send Web Push to conversation members who are currently offline."""
    online_users = manager.get_online_users()
    conv_name = conv.get("name") or msg.get("sender_name", sender)
    body = msg.get("text", "")
    if not body and msg.get("file"):
        body = msg["file"].get("name", "Файл")

    for member in conv["participants"]:
        if member == sender:
            continue
        if online_users.get(member, {}).get("online"):
            continue  # already delivered via WebSocket
        # Count total unread for badge
        convs = _load_conversations()
        total_unread = 0
        for c in convs:
            if member not in c["participants"]:
                continue
            msgs = _load_messages(c["id"])
            total_unread += _count_unread(msgs, member)

        push_payload = {
            "type": "message",
            "title": conv_name,
            "body": body[:100] if body else "Новое сообщение",
            "conv_id": conv["id"],
            "badge": total_unread,
        }
        await send_push_to_user(member, push_payload)


def _create_system_message(conversation_id: str, triggered_by: str, text: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "sender": "system",
        "sender_name": "System",
        "type": "system",
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "read_by": [],
    }
