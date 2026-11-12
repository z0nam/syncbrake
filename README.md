# ⚡ SyncBrake (`syncbrake` / `sb`)

> **Put the brakes on background sync & battery drain — without freezing your filesystem.**  
> Smart battery & hotspot saver for **macOS** and **Windows** with App-Aware cloud sync pausing, PD power-bank & 80%-charge-limit awareness, scheduled auto-resume, and an AI-agent-ready CLI + Menu Bar / Tray UI.

---

## Why SyncBrake?

Existing battery savers and network blockers fall short when you're working on the go with a laptop, power bank, or mobile hotspot:

1. **No Finder / Explorer Freezes (`App-Aware Safe Pause`)**: Process freezers that send `SIGSTOP` (`kill -STOP`) to cloud sync daemons (`Google Drive`, `OneDrive`, `Dropbox`) freeze macOS `FileProvider` and lock up Finder/terminal access. **SyncBrake** uses native app IPC flags (like Google Drive's `--pause_syncing`) and clean lifecycle hooks so your mounted files stay 100% accessible while CPU drops to `0%`.
2. **No Retry Storms on Hotspot**: Firewall tools that block packets cause sync clients (`Resilio Sync`, `Google Drive`) to spin in `SYN_SENT` retry loops and local disk scans, burning both laptop and phone hotspot batteries. SyncBrake cleanly pauses the sync engines themselves.
3. **Power Bank vs. 80% Charge-Limit (`Sailing Mode`) Intelligence**:
   - Plugging in a PD power bank or a weak seat outlet (`8W–30W`) tricks the OS into thinking it's on wall `AC Power`, unleashing background indexing and sync while your battery net-discharges.
   - Conversely, if you use an **80% battery charge limiter** (AlDente, `bclm`, OEM charge threshold), your laptop intentionally stops charging above 80% (`5V / 8W` sailing mode). SyncBrake distinguishes intentional 80%+ sailing from actual low-wattage power bank drain.
4. **Zero-Polling Scheduled Auto-Resume**: Pause syncing for a specific window (e.g., `16:00–24:00`) and automatically resume after midnight **only when online**—powered by native OS schedulers (`launchd` on macOS, `Task Scheduler` on Windows) with zero background polling overhead.

---

## Quick CLI Preview (`syncbrake` / `sb`)

Every command supports `--json` for seamless AI agent integration (Claude Code, Codex, Antigravity, MCP).

```bash
# Check real battery wattage (W), adapter negotiation, 80% sailing state, and sync status
sb status
sb status --json

# Pause all sync services now, or schedule a pause window with conditional auto-resume
sb pause
sb pause --until 24:00 --when-online
sb pause --from 16:00 --until 24:00 --when-online

# Resume everything immediately
sb resume

# Apply situation presets (work, trip, hotspot, survival)
sb preset trip
sb preset work

# Inspect top battery-draining apps (CPU + Sleep Assertions + dGPU) & selectively pause/quit
sb top
sb top --interactive
sb trim --auto

# Launch Menu Bar / System Tray, Dashboard Window, or Terminal UI
sb tray
sb window
sb tui
```

---

## Documentation

- 📐 **[DESIGN.md](./DESIGN.md)** — Full architecture, power/sailing detection logic, driver specs, CLI/UI design, and roadmap.

## License

MIT
