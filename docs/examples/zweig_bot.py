#!/usr/bin/env python3
"""Минимальный бот Zweig: отвечает на «/ping» и повторяет остальное.

    pip install httpx
    ZWEIG_URL=https://chat.example.ru ZWEIG_BOT_TOKEN=zb_... python3 zweig_bot.py

Бот получает сообщения долгим опросом (/api/bot/updates) — ни публичного
адреса, ни открытых портов ему не нужно.
"""
import os
import time

import httpx

URL = os.environ["ZWEIG_URL"].rstrip("/") + "/api"
TOKEN = os.environ["ZWEIG_BOT_TOKEN"]
api = httpx.Client(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60)


def reply(channel_slug: str, text: str, reply_to: str | None = None) -> None:
    r = api.post(f"{URL}/bot/channels/{channel_slug}/messages", json={"text": text, "reply_to": reply_to})
    if r.status_code == 429:  # больше 30 сообщений в минуту
        time.sleep(5)
    r.raise_for_status()


def handle(message: dict) -> None:
    text = message["text"].strip()
    slug = message["channel"]["slug"]
    if text == "/ping":
        reply(slug, "pong 🏓", reply_to=message["id"])
    elif message["channel"]["type"] == "direct":
        reply(slug, f"{message['sender_name']}, вы написали: {text}")


def main() -> None:
    me = api.get(f"{URL}/bot/me").json()
    print(f"Бот @{me['username']} запущен")
    offset = 0
    while True:
        try:
            updates = api.get(f"{URL}/bot/updates", params={"offset": offset, "timeout": 25}).json()
        except httpx.HTTPError as e:
            print("нет связи:", e)
            time.sleep(5)
            continue
        for upd in updates:
            offset = upd["update_id"]
            if upd["type"] == "message":
                handle(upd["message"])


if __name__ == "__main__":
    main()
