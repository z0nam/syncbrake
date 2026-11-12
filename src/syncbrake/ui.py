"""Dashboard Window UI (local HTTP + Chrome app window) & Native Menu Bar / System Tray."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .drivers import get_all_driver_statuses, pause_drivers, resume_drivers
from .inspector import act_on_app, inspect_top_drainers, trim_recommended_apps
from .probe import probe_network, probe_power, probe_tailnet
from .scheduler import cancel_schedule, get_schedule_status, schedule_window_or_until

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>⚡ SyncBrake Dashboard</title>
<style>
  :root {
    --bg: #0f1117; --card: #181b24; --border: #262b38;
    --text: #eef1f8; --muted: #8b93a7; --accent: #3b82f6;
    --green: #10b981; --amber: #f59e0b; --red: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Pretendard", sans-serif;
    background: var(--bg); color: var(--text); padding: 20px;
    line-height: 1.45;
  }
  header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid var(--border);
  }
  .title { font-size: 20px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
  .badge {
    font-size: 12px; padding: 4px 10px; border-radius: 999px; font-weight: 600;
    background: #1e293b; color: #93c5fd; border: 1px solid #334155;
  }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 16px; margin-bottom: 16px; }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px;
  }
  .card h2 { font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 12px; }
  .metric-big { font-size: 28px; font-weight: 700; margin-bottom: 6px; }
  .sub { font-size: 13px; color: var(--muted); }
  .btn-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  button {
    background: #252b3b; color: var(--text); border: 1px solid #363f54;
    padding: 7px 12px; border-radius: 8px; font-size: 13px; font-weight: 600;
    cursor: pointer; transition: 0.15s;
  }
  button:hover { background: #323a4f; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.danger { background: rgba(239,68,68,0.15); border-color: var(--red); color: #fca5a5; }
  button.success { background: rgba(16,185,129,0.15); border-color: var(--green); color: #6ee7b7; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; }
  .pill { padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; }
  .pill.PAUSED, .pill.STOPPED { background: rgba(245,158,11,0.18); color: #fcd34d; }
  .pill.SYNCING { background: rgba(16,185,129,0.18); color: #6ee7b7; }
</style>
</head>
<body>
<header>
  <div class="title">⚡ SyncBrake <span class="badge" id="power-badge">Loading...</span></div>
  <div>
    <button onclick="refreshState()">↻ 새로고침</button>
  </div>
</header>

<div class="grid">
  <div class="card">
    <h2>🔋 배터리 & 전원 상태</h2>
    <div class="metric-big" id="bat-main">--%</div>
    <div class="sub" id="bat-sub">Checking...</div>
    <div class="sub" id="net-sub" style="margin-top:6px;"></div>
  </div>

  <div class="card">
    <h2>🎯 빠른 제어 & 스케줄 (Tier-0 Google Drive + Resilio)</h2>
    <div class="sub" id="sched-status">예약 없음</div>
    <div class="btn-row">
      <button class="danger" onclick="apiCall('/api/pause')">⏸ 지금 모두 일시정지</button>
      <button class="success" onclick="apiCall('/api/resume')">▶ 지금 모두 재개</button>
    </div>
    <div class="btn-row">
      <button class="primary" onclick="apiCall('/api/schedule?until=1h')">⏰ 1시간 일시정지</button>
      <button class="primary" onclick="apiCall('/api/schedule?until=2h')">⏰ 2시간 일시정지</button>
      <button class="primary" onclick="apiCall('/api/schedule?until=4h')">⏰ 4시간 일시정지</button>
    </div>
    <div class="btn-row" style="align-items:center;">
      <input id="sched-from" placeholder="시작 (예: 16:00, 비우면 즉시)" style="background:#11141c;color:#fff;border:1px solid #363f54;padding:6px 8px;border-radius:6px;width:170px;font-size:12px;">
      <span>~</span>
      <input id="sched-until" placeholder="재개 (예: 24:00 또는 3h)" value="2h" style="background:#11141c;color:#fff;border:1px solid #363f54;padding:6px 8px;border-radius:6px;width:155px;font-size:12px;">
      <button onclick="customSchedule()">예약 적용</button>
      <button onclick="apiCall('/api/cancel_schedule')">예약 취소</button>
    </div>
    <div class="btn-row">
      <button onclick="apiCall('/api/preset?name=trip')">🧳 출장(Trip) 프리셋</button>
      <button onclick="apiCall('/api/preset?name=hotspot')">📡 핫스팟 프리셋</button>
      <button onclick="apiCall('/api/preset?name=work')">🏢 평상시(Work) 복구</button>
    </div>
  </div>
</div>

<div class="card" style="margin-bottom:16px;">
  <h2>☁️ 동기화 엔진 상태 (Tier-0 Google Drive & P2P)</h2>
  <table>
    <thead><tr><th>이름</th><th>상태</th><th>직접 CPU</th><th>유발 CPU (Spotlight 등)</th><th>추정 전력</th><th>상세</th><th>제어</th></tr></thead>
    <tbody id="drivers-tbody"></tbody>
  </table>
</div>

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <h2>🔥 배터리 상위 소모 앱 (화이트리스트 자동 보호)</h2>
    <button class="danger" onclick="apiCall('/api/trim')">🧹 추천 비필수 앱 일괄 정리 (Trim)</button>
  </div>
  <table>
    <thead><tr><th>앱</th><th>점수</th><th>CPU%</th><th>MEM%</th><th>사유 / Sleep 방해</th><th>액션</th></tr></thead>
    <tbody id="top-tbody"></tbody>
  </table>
</div>

<script>
async function refreshState() {
  const res = await fetch('/api/state');
  const d = await res.json();
  const p = d.power;
  document.getElementById('power-badge').textContent = p.power_mode;
  const wStr = (p.net_watts >= 0 ? '+' : '') + p.net_watts + 'W';
  const hrs = p.estimated_hours_left ? ` | 약 ${p.estimated_hours_left}시간 남음` : '';
  document.getElementById('bat-main').textContent = `${p.soc_pct}% (${wStr}${hrs})`;
  document.getElementById('bat-sub').textContent = `${p.power_mode_reason} | 완충용량 ${p.capacity_wh}Wh (건강도 ${p.health_pct}%)`;
  document.getElementById('net-sub').textContent = `네트워크: ${d.network.online ? '온라인' : '오프라인'} ${d.network.is_hotspot ? '📡 [핫스팟 연결됨]' : ''} ${d.network.tailscale_active ? '| Tailscale 온라인 피어 ' + d.network.tailscale_peers_online + '대' : ''}`;

  const s = d.schedule;
  document.getElementById('sched-status').textContent = s.active
    ? `⏰ 활성 스케줄: ${s.pause_from_str} 일시정지 → ${s.resume_after_str} 이후 온라인 시 자동 재개`
    : '현재 등록된 자동 예약 스케줄 없음';

  const dtbody = document.getElementById('drivers-tbody');
  dtbody.innerHTML = d.drivers.map(drv => `
    <tr>
      <td><b>${drv.name}</b></td>
      <td><span class="pill ${drv.state}">${drv.state}</span></td>
      <td>${drv.cpu_pct}%</td>
      <td>${drv.induced_cpu_pct}%</td>
      <td>~${drv.estimated_watts}W</td>
      <td class="sub">${drv.details}</td>
      <td>
        ${drv.state === 'SYNCING'
          ? `<button class="danger" onclick="apiCall('/api/pause?driver=${drv.id}')">⏸ 정지</button>`
          : `<button class="success" onclick="apiCall('/api/resume?driver=${drv.id}')">▶ 재개</button>`}
      </td>
    </tr>
  `).join('');

  const ttbody = document.getElementById('top-tbody');
  ttbody.innerHTML = d.top.map(app => `
    <tr>
      <td><b>${app.name}</b> ${app.protected ? '🔒' : ''}</td>
      <td>${app.drain_score}</td>
      <td>${app.cpu_pct}%</td>
      <td>${app.mem_pct}%</td>
      <td class="sub">${app.reason}</td>
      <td>
        ${app.protected
          ? '<span class="sub">보호됨</span>'
          : `<button onclick="apiCall('/api/act?app=${encodeURIComponent(app.name)}')">${app.recommended_action === 'pause' ? '⏸ Pause' : '✖ Quit'}</button>`}
      </td>
    </tr>
  `).join('');
}

async function apiCall(url) {
  await fetch(url, {method: 'POST'});
  await refreshState();
}

async function customSchedule() {
  const f = document.getElementById('sched-from').value.trim();
  const u = document.getElementById('sched-until').value.trim() || '2h';
  const q = f ? `from=${encodeURIComponent(f)}&until=${encodeURIComponent(u)}` : `until=${encodeURIComponent(u)}`;
  await apiCall('/api/schedule?' + q);
}

refreshState();
setInterval(refreshState, 5000);
</script>
</body>
</html>
"""


def get_full_state() -> dict[str, Any]:
    cfg = load_config()
    return {
        "power": asdict(probe_power(cfg)),
        "network": asdict(probe_network()),
        "schedule": get_schedule_status(),
        "drivers": [asdict(d) for d in get_all_driver_statuses()],
        "top": [asdict(e) for e in inspect_top_drainers(limit=12, cfg=cfg)],
    }


def apply_preset(name: str) -> dict[str, Any]:
    cfg = load_config()
    preset = cfg.presets.get(name)
    if not preset:
        return {"ok": False, "error": f"Unknown preset: {name}"}

    action = preset.get("sync_action", "pause")
    drv_ids = preset.get("drivers", ["gdrive", "resilio"])
    drv_res = pause_drivers(drv_ids) if action == "pause" else resume_drivers(drv_ids)

    quit_res = []
    for app in preset.get("quit_apps", []):
        quit_res.append(act_on_app(app, "quit"))

    return {
        "ok": True,
        "preset": name,
        "description": preset.get("description", ""),
        "drivers": drv_res,
        "quit_apps": quit_res,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._send_json(get_full_state())
            return
        body = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/api/pause":
            drv = qs.get("driver", [None])[0]
            res = pause_drivers([drv] if drv else None)
            self._send_json({"ok": True, "results": res})
        elif parsed.path == "/api/resume":
            drv = qs.get("driver", [None])[0]
            res = resume_drivers([drv] if drv else None)
            self._send_json({"ok": True, "results": res})
        elif parsed.path == "/api/schedule":
            from_t = qs.get("from", [None])[0]
            until_t = qs.get("until", ["2h"])[0]
            res = schedule_window_or_until(from_hhmm=from_t, until_hhmm=until_t, when_online=True)
            self._send_json(res)
        elif parsed.path == "/api/cancel_schedule":
            self._send_json(cancel_schedule())
        elif parsed.path == "/api/preset":
            name = qs.get("name", ["trip"])[0]
            self._send_json(apply_preset(name))
        elif parsed.path == "/api/act":
            app = qs.get("app", [""])[0]
            self._send_json(act_on_app(app))
        elif parsed.path == "/api/trim":
            self._send_json({"ok": True, "results": trim_recommended_apps(dry_run=False)})
        else:
            self._send_json({"ok": False})


def run_window_dashboard(port: int = 18765) -> None:
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"⚡ SyncBrake Dashboard running at {url} (Press Ctrl+C to stop)")

    chrome_app = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if platform.system().lower() == "darwin" and Path(chrome_app).exists():
        subprocess.Popen([chrome_app, f"--app={url}", "--window-size=960,740"])
    else:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


SWIFT_TRAY_SOURCE = r"""
import Cocoa
import Foundation

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var timer: Timer?
    let sbBin: String

    override init() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        self.sbBin = "\(home)/.local/bin/sb"
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        updateStatus()
        timer = Timer.scheduledTimer(withTimeInterval: 15.0, repeats: true) { [weak self] _ in
            self?.updateStatus()
        }
    }

    func runSB(_ args: [String]) -> String {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: sbBin)
        task.arguments = args
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = pipe
        do {
            try task.run()
            task.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8) ?? ""
        } catch {
            return ""
        }
    }

    @objc func updateStatus() {
        let raw = runSB(["status", "--json"])
        guard let data = raw.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let power = obj["power"] as? [String: Any],
              let drivers = obj["drivers"] as? [[String: Any]] else {
            statusItem.button?.title = "⚡SB"
            return
        }

        let soc = power["soc_pct"] as? Int ?? 100
        let watts = power["net_watts"] as? Double ?? 0.0
        let pmode = power["power_mode"] as? String ?? ""
        var gdState = "STOPPED"
        for d in drivers {
            if (d["id"] as? String) == "gdrive" {
                gdState = d["state"] as? String ?? "STOPPED"
            }
        }
        let icon = (gdState == "PAUSED") ? "⏸⚡" : "⚡"
        let wStr = String(format: "%+.0fW", watts)
        statusItem.button?.title = "\(icon)\(soc)%"

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "SyncBrake — \(pmode) (\(soc)% \(wStr))", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Google Drive: \(gdState)", action: nil, keyEquivalent: ""))

        if let sched = obj["schedule"] as? [String: Any],
           let active = sched["active"] as? Bool, active {
            let resumeStr = sched["resume_after_str"] as? String ?? ""
            menu.addItem(NSMenuItem(title: "⏰ 예약됨: \(resumeStr) 이후 자동 재개", action: nil, keyEquivalent: ""))
            let cancelItem = NSMenuItem(title: "✖ 예약 취소 (Cancel Schedule)", action: #selector(cancelSched), keyEquivalent: "")
            cancelItem.target = self
            menu.addItem(cancelItem)
        }
        menu.addItem(NSMenuItem.separator())

        menu.addItem(NSMenuItem(title: "⏸ 지금 모두 일시정지 (Pause All)", action: #selector(pauseAll), keyEquivalent: "p"))
        menu.addItem(NSMenuItem(title: "▶ 지금 모두 재개 (Resume All)", action: #selector(resumeAll), keyEquivalent: "r"))

        // Submenu for timed pause & custom schedule
        let schedMenu = NSMenu()
        let s1h = NSMenuItem(title: "1시간 뒤 자동 재개 (1h)", action: #selector(sched1h), keyEquivalent: "")
        let s2h = NSMenuItem(title: "2시간 뒤 자동 재개 (2h)", action: #selector(sched2h), keyEquivalent: "")
        let s4h = NSMenuItem(title: "4시간 뒤 자동 재개 (4h)", action: #selector(sched4h), keyEquivalent: "")
        let sCustom = NSMenuItem(title: "시간/구간 직접 입력... (예: 3h, 09:00, 14:00~18:00)", action: #selector(schedCustom), keyEquivalent: "")
        for it in [s1h, s2h, s4h, sCustom] {
            it.target = self
            schedMenu.addItem(it)
        }
        let schedRoot = NSMenuItem(title: "⏰ 예약 일시정지 (Schedule Pause)", action: nil, keyEquivalent: "")
        schedRoot.submenu = schedMenu
        menu.addItem(schedRoot)
        menu.addItem(NSMenuItem.separator())

        menu.addItem(NSMenuItem(title: "🧳 프리셋: 출장 모드 (Trip)", action: #selector(presetTrip), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "📡 프리셋: 핫스팟 모드 (Hotspot)", action: #selector(presetHotspot), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "🏢 프리셋: 평상시 복구 (Work)", action: #selector(presetWork), keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())

        menu.addItem(NSMenuItem(title: "🧹 비필수 배터리 소모 앱 정리 (Trim)", action: #selector(trimApps), keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "✖ 메뉴바 종료 (Quit Tray)", action: #selector(quitApp), keyEquivalent: "q"))

        for item in menu.items {
            if item.target == nil && item.action != nil {
                item.target = self
            }
        }
        statusItem.menu = menu
    }

    @objc func pauseAll() { _ = runSB(["pause"]); updateStatus() }
    @objc func resumeAll() { _ = runSB(["resume", "--cancel-schedule"]); updateStatus() }
    @objc func cancelSched() { _ = runSB(["resume", "--cancel-schedule"]); updateStatus() }
    @objc func sched1h() { _ = runSB(["pause", "--until", "1h", "--when-online"]); updateStatus() }
    @objc func sched2h() { _ = runSB(["pause", "--until", "2h", "--when-online"]); updateStatus() }
    @objc func sched4h() { _ = runSB(["pause", "--until", "4h", "--when-online"]); updateStatus() }
    @objc func schedCustom() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "⏰ 동기화 일시정지 & 자동 재개 예약"
        alert.informativeText = "재개 시각/시간(예: 2h, 30m, 09:00, 24:00) 또는 구간(예: 14:00~18:00)을 입력하세요.\n지정 시각 이후 온라인 상태일 때 자동 재개됩니다."
        alert.addButton(withTitle: "예약 적용")
        alert.addButton(withTitle: "취소")
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        input.stringValue = "2h"
        alert.accessoryView = input
        if alert.runModal() == .alertFirstButtonReturn {
            let val = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            if val.contains("~") || val.contains("-") {
                let sep: Character = val.contains("~") ? "~" : "-"
                let parts = val.split(separator: sep).map { String($0).trimmingCharacters(in: .whitespaces) }
                if parts.count == 2 {
                    _ = runSB(["pause", "--from", parts[0], "--until", parts[1], "--when-online"])
                }
            } else if !val.isEmpty {
                _ = runSB(["pause", "--until", val, "--when-online"])
            }
            updateStatus()
        }
    }
    @objc func presetTrip() { _ = runSB(["preset", "trip"]); updateStatus() }
    @objc func presetHotspot() { _ = runSB(["preset", "hotspot"]); updateStatus() }
    @objc func presetWork() { _ = runSB(["preset", "work"]); updateStatus() }
    @objc func trimApps() { _ = runSB(["trim"]); updateStatus() }
    @objc func quitApp() { NSApplication.shared.terminate(nil) }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
"""


def launch_macos_menubar_tray() -> dict[str, Any]:
    cache_dir = Path.home() / ".cache" / "syncbrake"
    app_bundle = cache_dir / "SyncBrakeTray.app"
    contents_dir = app_bundle / "Contents"
    macos_dir = contents_dir / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    swift_src = cache_dir / "SyncBrakeTray.swift"
    bin_path = macos_dir / "SyncBrakeTray"
    plist_path = contents_dir / "Info.plist"

    plist_xml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>SyncBrakeTray</string>
    <key>CFBundleIdentifier</key>
    <string>com.z0nam.syncbrake.tray</string>
    <key>CFBundleName</key>
    <string>SyncBrakeTray</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
"""
    plist_path.write_text(plist_xml, encoding="utf-8")

    if not bin_path.exists() or swift_src.read_text(encoding="utf-8", errors="ignore") != SWIFT_TRAY_SOURCE:
        swift_src.write_text(SWIFT_TRAY_SOURCE, encoding="utf-8")
        res = subprocess.run(
            ["/usr/bin/swiftc", "-O", str(swift_src), "-o", str(bin_path)],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            return {"ok": False, "error": res.stderr}

    subprocess.run(["killall", "SyncBrakeTray"], capture_output=True)
    subprocess.run(["/usr/bin/open", "-a", str(app_bundle)], capture_output=True)
    return {"ok": True, "app_bundle": str(app_bundle), "message": "Launched native macOS Menu Bar app (SyncBrakeTray.app)"}
