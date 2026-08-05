from __future__ import annotations

import json
import urllib.request


def send_slack_webhook(webhook_url: str, text: str) -> None:
    if not webhook_url:
        return
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=12) as response:
        if response.status >= 300:
            raise RuntimeError(f"Slack webhook failed with status {response.status}")
