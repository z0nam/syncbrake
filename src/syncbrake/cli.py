"""SyncBrake CLI (`syncbrake` / `sb`) — AI Agent & Human friendly command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from .config import load_config, save_config
from .drivers import get_all_driver_statuses, pause_drivers, resume_drivers
from .inspector import inspect_top_drainers, trim_recommended_apps
from .probe import probe_network, probe_power, probe_tailnet
from .scheduler import cancel_schedule, get_schedule_status, schedule_window_or_until
from .ui import apply_preset, get_full_state, launch_macos_menubar_tray, run_window_dashboard


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_status(args: argparse.Namespace) -> int:
    state = get_full_state()
    if args.json:
        _print_json(state)
        return 0

    p = state["power"]
    n = state["network"]
    s = state["schedule"]
    print(f"⚡ SyncBrake Status  [{p['power_mode']}]")
    print("─" * 68)
    hrs_str = f" | ~{p['estimated_hours_left']}h left" if p.get("estimated_hours_left") else ""
    print(
        f"🔋 Battery : {p['soc_pct']}% ({p['net_watts']:+.1f}W net{hrs_str}) "
        f"| Capacity: {p['capacity_wh']}Wh ({p['health_pct']}% health, {p['cycle_count']} cycles)"
    )
    print(f"🔌 Power   : {p['power_mode_reason']}")
    net_flags = []
    if n["is_hotspot"]:
        net_flags.append(f"📡 HOTSPOT ({n['hotspot_reason']})")
    if n["tailscale_active"]:
        net_flags.append(f"🔗 Tailscale ({n['tailscale_peers_online']} peers online)")
    flag_str = " | " + ", ".join(net_flags) if net_flags else ""
    print(f"🌐 Network : {'Online' if n['online'] else 'Offline'}{flag_str}")

    if s.get("active"):
        print(
            f"⏰ Schedule: PAUSE [{s['pause_from_str']}] -> RESUME after [{s['resume_after_str']}] "
            f"({'when online' if s.get('when_online') else 'unconditional'})"
        )
    else:
        print("⏰ Schedule: None")

    print("\n☁️  Sync Engines (Tier-0 Google Drive & P2P)")
    for d in state["drivers"]:
        badge = "⏸ PAUSED " if d["state"] in ("PAUSED", "STOPPED") else "🟢 SYNCING"
        print(
            f"  • {d['name']:<24} {badge} | Direct CPU: {d['cpu_pct']:>5.1f}% "
            f"| Induced: {d['induced_cpu_pct']:>4.1f}% (~{d['estimated_watts']}W) | {d['details']}"
        )

    print("\n🔥 Top Battery Drainers (Top 5)")
    for e in state["top"][:5]:
        lock = "🔒" if e["protected"] else "  "
        print(
            f"  {lock} {e['name']:<22} Score: {e['drain_score']:>5.1f} "
            f"| CPU: {e['cpu_pct']:>5.1f}% | MEM: {e['mem_pct']:>4.1f}% "
            f"| Action: {e['recommended_action']:<5} ({e['reason']})"
        )
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    if args.from_time or args.until_time:
        until_t = args.until_time or "24:00"
        sched = schedule_window_or_until(
            from_hhmm=args.from_time,
            until_hhmm=until_t,
            when_online=args.when_online,
        )
        res: dict[str, Any] = {"ok": True, "scheduled": sched}
        if not args.from_time:
            res["paused_now"] = pause_drivers(args.drivers or None)
        if args.json:
            _print_json(res)
        else:
            if not args.from_time:
                for r in res.get("paused_now", []):
                    print(f"⏸  {r.get('driver')}: {r.get('message')}")
            print(
                f"⏰ Scheduled pause window: [{sched.get('pause_from_str')}] -> "
                f"auto-resume after [{sched.get('resume_after_str')}] (when online={sched.get('when_online')})"
            )
        return 0

    results = pause_drivers(args.drivers or None)
    if args.json:
        _print_json({"ok": True, "results": results})
    else:
        for r in results:
            print(f"⏸  [{r.get('driver')}] {r.get('message')}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    if args.cancel_schedule:
        cancel_schedule()
    results = resume_drivers(args.drivers or None)
    if args.json:
        _print_json({"ok": True, "results": results})
    else:
        for r in results:
            print(f"▶  [{r.get('driver')}] {r.get('message')}")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    entries = inspect_top_drainers(limit=args.limit)
    if args.json:
        _print_json([asdict(e) for e in entries])
        return 0

    print(f"{'APP':<24} {'SCORE':>6} {'CPU%':>6} {'MEM%':>6} {'ACTION':<8} {'REASON'}")
    print("─" * 76)
    for e in entries:
        mark = "🔒 " if e.protected else "   "
        print(
            f"{mark}{e.name:<21} {e.drain_score:>6.1f} {e.cpu_pct:>5.1f}% "
            f"{e.mem_pct:>5.1f}% {e.recommended_action:<8} {e.reason}"
        )
    return 0


def cmd_trim(args: argparse.Namespace) -> int:
    results = trim_recommended_apps(dry_run=args.dry_run)
    if args.json:
        _print_json({"ok": True, "dry_run": args.dry_run, "results": results})
        return 0

    if not results:
        print("✨ 정리할 비필수 배터리 소모 앱이 없습니다.")
        return 0
    prefix = "[DRY-RUN] " if args.dry_run else ""
    for r in results:
        print(f"🧹 {prefix}{r.get('app')}: {r.get('action')} (score={r.get('drain_score')})")
    return 0


def cmd_preset(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.name == "list":
        if args.json:
            _print_json(cfg.presets)
        else:
            for k, v in cfg.presets.items():
                print(f"• {k:<10}: {v.get('description')}")
        return 0

    res = apply_preset(args.name)
    if args.json:
        _print_json(res)
    else:
        if not res.get("ok"):
            print(f"❌ {res.get('error')}")
            return 1
        print(f"🎯 Applied preset [{args.name}]: {res.get('description')}")
        for d in res.get("drivers", []):
            print(f"  • {d.get('driver')}: {d.get('message')}")
    return 0


def cmd_fleet(args: argparse.Namespace) -> int:
    info = probe_tailnet()
    if args.json:
        _print_json(info)
        return 0

    if not info.get("running"):
        print("🔗 Tailscale이 현재 실행 중이지 않거나 연결되어 있지 않습니다.")
        return 0

    print(f"🔗 Tailscale Fleet (Self: {info.get('self_hostname')} [{info.get('self_os')}] {info.get('self_ip')})")
    print("─" * 72)
    print(f"{'HOSTNAME':<22} {'OS':<10} {'STATUS':<12} {'IP':<18}")
    for p in info.get("peers", []):
        st = "🟢 ACTIVE" if p["active"] else ("🟢 ONLINE" if p["online"] else "⚪ OFFLINE")
        ip = p["ips"][0] if p["ips"] else ""
        print(f"{p['hostname']:<22} {p['os']:<10} {st:<12} {ip:<18}")
    return 0


def cmd_powerbank(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.mode == "on":
        cfg.force_powerbank_mode = True
    elif args.mode == "off":
        cfg.force_powerbank_mode = False
    save_config(cfg)
    if args.json:
        _print_json({"ok": True, "force_powerbank_mode": cfg.force_powerbank_mode})
    else:
        print(f"🔋 Power Bank forced mode: {'ON' if cfg.force_powerbank_mode else 'OFF (Auto-detect)'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sb",
        description="⚡ SyncBrake (sb) — Smart battery & hotspot saver (Google Drive Tier-0, Resilio, Tailscale Fleet)",
    )
    parser.add_argument("--json", action="store_true", help="Output structured JSON for AI agents")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="Show battery wattage, 80% sailing mode, sync engines, and top drainers")
    p_status.add_argument("--json", action="store_true")

    p_pause = sub.add_parser("pause", help="Pause sync engines now or schedule a pause window with auto-resume")
    p_pause.add_argument("drivers", nargs="*", help="Specific drivers to pause (e.g. gdrive resilio)")
    p_pause.add_argument("--from", dest="from_time", help="Start time for pause window (e.g. 16:00)")
    p_pause.add_argument("--until", dest="until_time", help="Resume time (e.g. 24:00 or 2h)")
    p_pause.add_argument("--when-online", action="store_true", default=True, help="Wait until online to resume")
    p_pause.add_argument("--json", action="store_true")

    p_resume = sub.add_parser("resume", help="Resume sync engines immediately")
    p_resume.add_argument("drivers", nargs="*", help="Specific drivers to resume (e.g. gdrive resilio)")
    p_resume.add_argument("--cancel-schedule", action="store_true", help="Also cancel active scheduled pause/resume")
    p_resume.add_argument("--json", action="store_true")

    p_top = sub.add_parser("top", help="List top battery-draining apps and sleep blockers")
    p_top.add_argument("-n", "--limit", type=int, default=12)
    p_top.add_argument("--json", action="store_true")

    p_trim = sub.add_parser("trim", help="Pause/quit non-whitelisted battery drainers")
    p_trim.add_argument("--dry-run", action="store_true", help="Preview apps that would be paused/quit")
    p_trim.add_argument("--json", action="store_true")

    p_preset = sub.add_parser("preset", help="Apply situation preset (work, trip, hotspot, survival, list)")
    p_preset.add_argument("name", choices=["work", "trip", "hotspot", "survival", "list"])
    p_preset.add_argument("--json", action="store_true")

    p_fleet = sub.add_parser("fleet", help="Inspect Tailscale tailnet peers across Windows/macOS/Linux")
    p_fleet.add_argument("--json", action="store_true")

    p_pb = sub.add_parser("powerbank", help="Toggle manual Power Bank mode override (on/off/status)")
    p_pb.add_argument("mode", nargs="?", default="status", choices=["on", "off", "status"])
    p_pb.add_argument("--json", action="store_true")

    p_win = sub.add_parser("window", help="Open standalone SyncBrake Dashboard window")
    p_win.add_argument("--port", type=int, default=18765)

    p_tray = sub.add_parser("tray", help="Launch native Menu Bar / System Tray icon")
    p_tray.add_argument("--json", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cmd = args.command or "status"

    if cmd == "status":
        return cmd_status(args)
    elif cmd == "pause":
        return cmd_pause(args)
    elif cmd == "resume":
        return cmd_resume(args)
    elif cmd == "top":
        return cmd_top(args)
    elif cmd == "trim":
        return cmd_trim(args)
    elif cmd == "preset":
        return cmd_preset(args)
    elif cmd == "fleet":
        return cmd_fleet(args)
    elif cmd == "powerbank":
        return cmd_powerbank(args)
    elif cmd == "window":
        run_window_dashboard(port=getattr(args, "port", 18765))
        return 0
    elif cmd == "tray":
        res = launch_macos_menubar_tray()
        if getattr(args, "json", False):
            _print_json(res)
        else:
            print(f"⚡ {res.get('message', res.get('error'))}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
