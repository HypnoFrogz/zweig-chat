"""Zweig Messenger — WebSocket connection manager.

Supports multiple connections per user (multiple tabs/devices).
Routes messages to channels or individual users.
"""

import json
from datetime import datetime, timezone
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # username → list[WebSocket]  (multi-tab support)
        self.active_connections: dict[str, list[WebSocket]] = {}
        # username → last_seen ISO string
        self.last_seen: dict[str, str] = {}
        # WebSocket → вид клиента ('mobile' | 'web'). Нужен уведомлениям: пуш
        # на телефон имеет смысл, пока приложение не открыто, а открытая
        # вкладка в браузере об этом ничего не говорит.
        self.conn_kind: dict[WebSocket, str] = {}

    async def connect(self, username: str, websocket: WebSocket, kind: str = "web"):
        await websocket.accept()
        if username not in self.active_connections:
            self.active_connections[username] = []
        self.active_connections[username].append(websocket)
        self.conn_kind[websocket] = kind if kind in ("mobile", "web") else "web"
        # Broadcast only if this is the first connection (was offline)
        if len(self.active_connections[username]) == 1:
            await self.broadcast_presence(username, online=True)

    async def disconnect(self, username: str, websocket):
        if websocket is not None:
            self.conn_kind.pop(websocket, None)
        else:
            for ws in self.active_connections.get(username, []):
                self.conn_kind.pop(ws, None)
        if username in self.active_connections:
            if websocket is not None:
                try:
                    self.active_connections[username].remove(websocket)
                except ValueError:
                    pass
            else:
                self.active_connections[username].clear()
            if not self.active_connections[username]:
                del self.active_connections[username]
                self.last_seen[username] = datetime.now(timezone.utc).isoformat()
                return True  # user went offline
        return False  # still has other connections

    def is_online(self, username: str) -> bool:
        return bool(self.active_connections.get(username))

    def has_mobile(self, username: str) -> bool:
        """Открыто ли у человека мобильное приложение прямо сейчас.

        Уведомление на телефон не нужно, только когда приложение открыто и
        сообщение и так видно. Открытая вкладка в браузере — не повод молчать:
        телефон при этом лежит в кармане, и человек ждёт от него сигнала.
        """
        return any(
            self.conn_kind.get(ws) == "mobile"
            for ws in self.active_connections.get(username, [])
        )

    async def broadcast_presence(self, username: str, online: bool):
        data = {
            "event": "presence",
            "username": username,
            "online": online,
            "last_seen": self.last_seen.get(username, ""),
        }
        for user in list(self.active_connections.keys()):
            if user != username:
                await self.send_to_user(user, data)

    async def send_to_user(self, username: str, data: dict):
        """Send to ALL connections of a user (all tabs)."""
        sockets = self.active_connections.get(username, [])
        for ws in list(sockets):
            await self._safe_send(ws, data)

    async def send_to_channel(
        self, members: list[str], data: dict, exclude: str | None = None
    ):
        """Broadcast to all members of a channel."""
        for user in members:
            if user != exclude:
                await self.send_to_user(user, data)

    async def broadcast_all(self, data: dict, exclude: str | None = None):
        """Send to every connected user."""
        for user in list(self.active_connections.keys()):
            if user != exclude:
                await self.send_to_user(user, data)

    async def _safe_send(self, ws: WebSocket, data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            # Drop broken sockets immediately, so online status doesn't stay "stuck".
            offline_users: list[str] = []
            for username, sockets in list(self.active_connections.items()):
                if ws in sockets:
                    try:
                        sockets.remove(ws)
                    except ValueError:
                        pass
                    if not sockets:
                        del self.active_connections[username]
                        self.last_seen[username] = datetime.now(timezone.utc).isoformat()
                        offline_users.append(username)
            for username in offline_users:
                await self.broadcast_presence(username, online=False)

    def get_online_users(self) -> dict[str, dict]:
        result = {}
        all_known = set(self.active_connections.keys()) | set(self.last_seen.keys())
        for username in all_known:
            online = self.is_online(username)
            result[username] = {
                "online": online,
                "last_seen": self.last_seen.get(username, ""),
            }
        return result


manager = ConnectionManager()
