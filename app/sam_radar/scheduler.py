from __future__ import annotations

import datetime as dt
import threading
import time
from zoneinfo import ZoneInfo

from .config import Settings
from .core import refresh_report


def parse_daily_cron(expr: str) -> tuple[int, int]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("REFRESH_CRON must be a five-field cron expression like '0 6 * * *'")
    minute, hour = parts[0], parts[1]
    if not minute.isdigit() or not hour.isdigit():
        raise ValueError("Only fixed minute/hour daily cron is currently supported, e.g. '0 6 * * *'")
    minute_i = int(minute)
    hour_i = int(hour)
    if not 0 <= minute_i <= 59 or not 0 <= hour_i <= 23:
        raise ValueError("Cron hour/minute out of range")
    return hour_i, minute_i


def next_run(now: dt.datetime, expr: str) -> dt.datetime:
    hour, minute = parse_daily_cron(expr)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    return candidate


class Scheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="sam-radar-scheduler", daemon=True)

    def start(self) -> None:
        parse_daily_cron(self.settings.refresh_cron)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        tz = ZoneInfo(self.settings.timezone)
        while not self._stop.is_set():
            now = dt.datetime.now(tz)
            run_at = next_run(now, self.settings.refresh_cron)
            wait_seconds = max(1, min(300, int((run_at - now).total_seconds())))
            if self._stop.wait(wait_seconds):
                return
            if dt.datetime.now(tz) >= run_at:
                try:
                    refresh_report(self.settings, mark_seen=True, notify=True)
                except Exception as exc:  # noqa: BLE001 - scheduler keeps serving web UI
                    print(f"Scheduled refresh failed: {exc}", flush=True)
                time.sleep(60)
