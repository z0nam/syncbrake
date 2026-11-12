"""Configuration management for SyncBrake (~/.config/syncbrake/config.json)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PROTECTED_APPS = [
    "Slack",
    "Google Chrome",
    "Claude",
    "Ghostty",
    "Terminal",
    "iTerm2",
    "WezTerm",
    "Windows Terminal",
    "Finder",
    "Explorer",
    "agy",
    "SyncBrakeTray",
]

DEFAULT_TRIM_CANDIDATES = [
    "ChatGPT",
    "Parsec",
    "Synergy",
    "synergy-service",
    "monday",
    "Keynote",
    "Keynote Creator Studio",
    "Skim",
    "iPhone Mirroring",
    "Visual Studio Code",
    "Code",
    "CodexBar",
    "App Store",
    "TextMate",
    "TextEdit",
]

DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "work": {
        "description": "Normal AC work mode — resume all cloud sync and restore normal power profile.",
        "sync_action": "resume",
        "drivers": ["gdrive", "resilio", "onedrive", "dropbox", "syncthing"],
        "low_power_mode": False,
        "quit_apps": [],
    },
    "trip": {
        "description": "Business trip / on-the-go mode — pause Google Drive & Resilio Sync, quit remote/heavy idle apps, enable Low Power Mode.",
        "sync_action": "pause",
        "drivers": ["gdrive", "resilio", "onedrive", "dropbox", "syncthing"],
        "low_power_mode": True,
        "quit_apps": ["Parsec", "monday", "iPhone Mirroring", "ChatGPT"],
    },
    "hotspot": {
        "description": "Mobile hotspot saver — pause all cloud & P2P sync (Google Drive, Resilio, OneDrive, Dropbox) to save phone battery & data.",
        "sync_action": "pause",
        "drivers": ["gdrive", "resilio", "onedrive", "dropbox", "syncthing"],
        "low_power_mode": False,
        "quit_apps": ["Parsec"],
    },
    "survival": {
        "description": "Ultra battery survival (<20%) — pause all sync, enable Low Power Mode, and quit all non-whitelisted background/GUI drainers.",
        "sync_action": "pause",
        "drivers": ["gdrive", "resilio", "onedrive", "dropbox", "syncthing", "ollama"],
        "low_power_mode": True,
        "quit_apps": DEFAULT_TRIM_CANDIDATES,
    },
}


@dataclass
class SyncBrakeConfig:
    charge_limit_pct: int = 80
    min_adapter_watts: int = 45
    force_powerbank_mode: bool = False
    protected_apps: list[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED_APPS))
    presets: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_PRESETS))


def get_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "syncbrake" / "config.json"


def load_config() -> SyncBrakeConfig:
    path = get_config_path()
    if not path.exists():
        return SyncBrakeConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cfg = SyncBrakeConfig()
        if "charge_limit_pct" in raw:
            cfg.charge_limit_pct = int(raw["charge_limit_pct"])
        if "min_adapter_watts" in raw:
            cfg.min_adapter_watts = int(raw["min_adapter_watts"])
        if "force_powerbank_mode" in raw:
            cfg.force_powerbank_mode = bool(raw["force_powerbank_mode"])
        if "protected_apps" in raw and isinstance(raw["protected_apps"], list):
            cfg.protected_apps = [str(x) for x in raw["protected_apps"]]
        if "presets" in raw and isinstance(raw["presets"], dict):
            cfg.presets.update(raw["presets"])
        return cfg
    except Exception:
        return SyncBrakeConfig()


def save_config(cfg: SyncBrakeConfig) -> Path:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
