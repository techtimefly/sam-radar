from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_list(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class BusinessProfile:
    name: str
    dba: str = ""
    location: str = ""
    summary: str = ""
    capabilities: list[str] = field(default_factory=list)
    naics_primary: list[str] = field(default_factory=list)
    naics_secondary: list[str] = field(default_factory=list)
    psc: list[str] = field(default_factory=list)
    set_asides: list[str] = field(default_factory=list)
    strong_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.dba or self.name or "Configured Business"

    @property
    def naics_all(self) -> list[str]:
        seen: set[str] = set()
        codes: list[str] = []
        for code in [*self.naics_primary, *self.naics_secondary]:
            text = str(code).strip()
            if text and text not in seen:
                codes.append(text)
                seen.add(text)
        return codes


@dataclass(slots=True)
class Settings:
    sam_gov_api_key: str
    app_base_url: str = "http://127.0.0.1:8066"
    host: str = "0.0.0.0"
    port: int = 8066
    timezone: str = "America/Denver"
    profile_path: Path = Path("config/business.yaml")
    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    refresh_cron: str = "0 6 * * *"
    app_write_token: str = ""
    report_limit: int = 20
    search_days: int = 7
    enable_scheduler: bool = False
    enable_slack: bool = False
    slack_webhook_url: str = ""
    slack_channel_id: str = ""
    enable_slack_workflow: bool = False
    slack_workflow_events: list[str] = field(default_factory=lambda: ["pursue", "submitted", "due-soon", "follow-up-due"])
    enable_telegram: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def report_url_base(self) -> str:
        return self.app_base_url.rstrip("/") + "/reports"

    @property
    def latest_report_url(self) -> str:
        return self.report_url_base + "/latest.html"


def load_settings() -> Settings:
    return Settings(
        sam_gov_api_key=os.getenv("SAM_GOV_API_KEY", ""),
        app_base_url=os.getenv("APP_BASE_URL", "http://127.0.0.1:8066"),
        host=os.getenv("SAM_RADAR_HOST", "0.0.0.0"),
        port=env_int("SAM_RADAR_PORT", 8066),
        timezone=os.getenv("TIMEZONE", "America/Denver"),
        profile_path=Path(os.getenv("BUSINESS_PROFILE", "config/business.yaml")),
        data_dir=Path(os.getenv("DATA_DIR", "data")),
        reports_dir=Path(os.getenv("REPORTS_DIR", "reports")),
        refresh_cron=os.getenv("REFRESH_CRON", "0 6 * * *"),
        app_write_token=os.getenv("APP_WRITE_TOKEN", ""),
        report_limit=env_int("REPORT_LIMIT", 20),
        search_days=env_int("SEARCH_DAYS", 7),
        enable_scheduler=env_bool("ENABLE_SCHEDULER", False),
        enable_slack=env_bool("ENABLE_SLACK", False),
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
        slack_channel_id=os.getenv("SLACK_CHANNEL_ID", ""),
        enable_slack_workflow=env_bool("ENABLE_SLACK_WORKFLOW", False),
        slack_workflow_events=env_list("SLACK_WORKFLOW_EVENTS") or ["pursue", "submitted", "due-soon", "follow-up-due"],
        enable_telegram=env_bool("ENABLE_TELEGRAM", False),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )


def load_business_profile(path: Path) -> BusinessProfile:
    if not path.exists():
        raise FileNotFoundError(
            f"Business profile not found: {path}. Copy config/business.example.yaml to config/business.yaml and edit it."
        )
    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    business = data.get("business") or {}
    naics = data.get("naics") or {}
    keywords = data.get("keywords") or {}
    return BusinessProfile(
        name=str(business.get("name") or "Configured Business"),
        dba=str(business.get("dba") or ""),
        location=str(business.get("location") or ""),
        summary=str(business.get("summary") or ""),
        capabilities=[str(item) for item in (data.get("capabilities") or [])],
        naics_primary=[str(item) for item in (naics.get("primary") or [])],
        naics_secondary=[str(item) for item in (naics.get("secondary") or [])],
        psc=[str(item) for item in (data.get("psc") or [])],
        set_asides=[str(item) for item in (data.get("set_asides") or [])],
        strong_keywords=[str(item).lower() for item in (keywords.get("strong") or [])],
        exclude_keywords=[str(item).lower() for item in (keywords.get("exclude") or [])],
    )
