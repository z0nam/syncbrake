"""Battery Vampire Inspector (CPU + Sleep Assertions + Sync Engines) and Selective Action."""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from .config import DEFAULT_TRIM_CANDIDATES, SyncBrakeConfig, load_config
from .drivers import DRIVERS


@dataclass
class AppDrainEntry:
    name: str
    pid: int
    cpu_pct: float
    mem_pct: float
    sleep_blocker: bool
    protected: bool
    driver_id: str | None
    recommended_action: str  # "pause", "quit", "keep"
    drain_score: float
    reason: str


def _get_sleep_blockers_macos() -> set[str]:
    blockers: set[str] = set()
    try:
        res = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=3.0)
        for line in res.stdout.splitlines():
            m = re.search(r"pid\s+\d+\(([^\)]+)\):.*(NoIdleSleepAssertion|PreventUserIdleSystemSleep|PreventUserIdleDisplaySleep)", line)
            if m:
                blockers.add(m.group(1).strip())
    except Exception:
        pass
    return blockers


def inspect_top_drainers(limit: int = 12, cfg: SyncBrakeConfig | None = None) -> list[AppDrainEntry]:
    if cfg is None:
        cfg = load_config()
    protected_lower = {p.lower() for p in cfg.protected_apps}
    trim_lower = {t.lower() for t in DEFAULT_TRIM_CANDIDATES}
    sleep_blockers = _get_sleep_blockers_macos() if platform.system().lower() == "darwin" else set()

    try:
        res = subprocess.run(
            ["ps", "-Ao", "pid,pcpu,pmem,comm", "-r"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except Exception:
        return []

    grouped: dict[str, dict[str, Any]] = {}
    for line in res.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            cpu = float(parts[1])
            mem = float(parts[2])
            comm = parts[3].strip()
        except ValueError:
            continue

        # Normalize app name from path
        app_match = re.search(r"/([^/]+)\.app/", comm)
        if app_match:
            app_name = app_match.group(1)
            # Collapse Helpers into parent app name
            app_name = re.sub(r"\s+Helper.*$", "", app_name)
            app_name = re.sub(r"^Codex.*$", "ChatGPT", app_name)
        else:
            if comm.startswith("/System/") or comm.startswith("/usr/") or comm.startswith("/sbin/"):
                continue
            app_name = comm.split("/")[-1]

        if app_name not in grouped:
            grouped[app_name] = {"pid": pid, "cpu": 0.0, "mem": 0.0}
        grouped[app_name]["cpu"] += cpu
        grouped[app_name]["mem"] += mem

    entries: list[AppDrainEntry] = []
    for name, info in grouped.items():
        name_l = name.lower()
        is_protected = any(p in name_l or name_l in p for p in protected_lower)
        is_blocker = any(b.lower() in name_l or name_l in b.lower() for b in sleep_blockers)

        driver_id = None
        if "google drive" in name_l:
            driver_id = "gdrive"
        elif "resilio" in name_l:
            driver_id = "resilio"

        score = info["cpu"] * 1.0 + info["mem"] * 1.5
        reasons = []
        if driver_id == "gdrive":
            score += 25.0
            reasons.append("Tier-0 Cloud Sync")
        elif driver_id:
            score += 15.0
            reasons.append("P2P Sync")
        if is_blocker:
            score += 20.0
            reasons.append("Blocks Sleep")
        if name_l in trim_lower:
            score += 8.0
            reasons.append("Background/Idle App")

        if is_protected:
            action = "keep"
            reasons.append("Protected Whitelist")
        elif driver_id:
            action = "pause"
        elif score >= 5.0 or name_l in trim_lower:
            action = "quit"
        else:
            action = "keep"

        entries.append(
            AppDrainEntry(
                name=name,
                pid=info["pid"],
                cpu_pct=round(info["cpu"], 1),
                mem_pct=round(info["mem"], 1),
                sleep_blocker=is_blocker,
                protected=is_protected,
                driver_id=driver_id,
                recommended_action=action,
                drain_score=round(score, 1),
                reason=", ".join(reasons) if reasons else "Active process",
            )
        )

    entries.sort(key=lambda e: e.drain_score, reverse=True)
    return entries[:limit]


def act_on_app(app_name: str, action: str = "auto") -> dict[str, Any]:
    name_l = app_name.lower()
    if "google drive" in name_l or name_l == "gdrive":
        return DRIVERS["gdrive"].pause()
    if "resilio" in name_l:
        return DRIVERS["resilio"].pause()

    if platform.system().lower() == "darwin":
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to quit'],
            capture_output=True,
            timeout=4.0,
        )
        subprocess.run(["killall", app_name], capture_output=True, timeout=3.0)
        return {"ok": True, "app": app_name, "action": "quit", "message": f"Quit {app_name}"}
    else:
        subprocess.run(["pkill", "-f", app_name], capture_output=True, timeout=3.0)
        return {"ok": True, "app": app_name, "action": "quit", "message": f"Terminated {app_name}"}


def trim_recommended_apps(dry_run: bool = False) -> list[dict[str, Any]]:
    entries = inspect_top_drainers(limit=25)
    results = []
    for e in entries:
        if e.protected or e.recommended_action == "keep":
            continue
        if dry_run:
            results.append(
                {
                    "ok": True,
                    "dry_run": True,
                    "app": e.name,
                    "action": e.recommended_action,
                    "drain_score": e.drain_score,
                    "reason": e.reason,
                }
            )
        else:
            res = act_on_app(e.name, e.recommended_action)
            res["drain_score"] = e.drain_score
            results.append(res)
    return results
