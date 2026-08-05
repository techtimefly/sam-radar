from __future__ import annotations

import json
import urllib.parse
import urllib.request


def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "false"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError("Telegram send failed")
