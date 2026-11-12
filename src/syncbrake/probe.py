"""Cross-platform power, battery, 80%-sailing mode, hotspot, and Tailscale fleet probe."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import SyncBrakeConfig, load_config


@dataclass
class PowerState:
    os_name: str
    cpu_model: str = ""
    has_battery: bool = True
    soc_pct: int = 100
    external_connected: bool = True
    is_charging: bool = False
    power_mode: str = "AC_CHARGING"
    power_mode_reason: str = ""
    net_watts: float = 0.0
    instant_watts: float = 0.0
    adapter_watts: int = 0
    adapter_voltage_mv: int = 0
    adapter_current_ma: int = 0
    adapter_desc: str = ""
    capacity_wh: float = 0.0
    design_capacity_wh: float = 0.0
    health_pct: float = 100.0
    remaining_wh: float = 0.0
    estimated_hours_left: float | None = None
    cycle_count: int = 0
    low_power_mode: bool = False
    sleep_preventers: list[str] = field(default_factory=list)


@dataclass
class NetworkState:
    online: bool = False
    is_hotspot: bool = False
    hotspot_reason: str = ""
    local_ips: list[str] = field(default_factory=list)
    tailscale_active: bool = False
    tailscale_self_ip: str = ""
    tailscale_peers_online: int = 0


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.stdout or ""
    except Exception:
        return ""


def _signed_64(val: int) -> int:
    if val >= (1 << 63):
        return val - (1 << 64)
    return val


def probe_power(cfg: SyncBrakeConfig | None = None) -> PowerState:
    if cfg is None:
        cfg = load_config()
    sys_name = platform.system().lower()
    if sys_name == "darwin":
        return _probe_power_macos(cfg)
    elif sys_name == "linux":
        return _probe_power_linux(cfg)
    elif sys_name == "windows":
        return _probe_power_windows(cfg)
    return PowerState(os_name=sys_name)


def _probe_power_macos(cfg: SyncBrakeConfig) -> PowerState:
    state = PowerState(os_name="macos")
    state.cpu_model = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()

    ioreg_out = _run(["ioreg", "-rn", "AppleSmartBattery"])
    if not ioreg_out:
        state.has_battery = False
        return state

    def _find_int(key: str) -> int | None:
        m = re.search(rf'^\s*"{key}"\s*=\s*(\d+)', ioreg_out, re.MULTILINE)
        return int(m.group(1)) if m else None

    def _find_bool(key: str) -> bool | None:
        m = re.search(rf'^\s*"{key}"\s*=\s*(Yes|No)', ioreg_out, re.MULTILINE)
        return (m.group(1) == "Yes") if m else None

    ui_soc = re.search(r'"UISoc"\s*=\s*(\d+)', ioreg_out)
    max_cap = _find_int("AppleRawMaxCapacity") or _find_int("MaxCapacity") or 0
    cur_cap = _find_int("CurrentCapacity") or 0
    des_cap = _find_int("DesignCapacity") or max_cap
    voltage_mv = _find_int("Voltage") or 11400
    amp_raw = _find_int("Amperage") or 0
    inst_amp_raw = _find_int("InstantAmperage") or amp_raw
    cycles = _find_int("CycleCount") or 0

    amp_ma = _signed_64(amp_raw)
    inst_amp_ma = _signed_64(inst_amp_raw)

    if ui_soc:
        state.soc_pct = int(ui_soc.group(1))
    elif max_cap > 0:
        state.soc_pct = round((cur_cap / max_cap) * 100)

    state.external_connected = bool(_find_bool("ExternalConnected"))
    state.is_charging = bool(_find_bool("IsCharging"))
    state.cycle_count = cycles

    v = voltage_mv / 1000.0
    state.net_watts = round((amp_ma / 1000.0) * v, 1)
    state.instant_watts = round((inst_amp_ma / 1000.0) * v, 1)
    state.capacity_wh = round((max_cap / 1000.0) * 11.4, 1)
    state.design_capacity_wh = round((des_cap / 1000.0) * 11.4, 1)
    if state.design_capacity_wh > 0:
        state.health_pct = round((state.capacity_wh / state.design_capacity_wh) * 100, 1)
    state.remaining_wh = round(state.capacity_wh * (state.soc_pct / 100.0), 1)

    discharge_w = abs(state.net_watts) if state.net_watts < -0.5 else 0.0
    if discharge_w > 0.5:
        state.estimated_hours_left = round(state.remaining_wh / discharge_w, 2)

    adapter_match = re.search(r'"AdapterDetails"\s*=\s*\{([^\}]+)\}', ioreg_out)
    if adapter_match:
        ad_str = adapter_match.group(1)
        w_m = re.search(r'"Watts"\s*=\s*(\d+)', ad_str)
        v_m = re.search(r'"AdapterVoltage"\s*=\s*(\d+)', ad_str)
        c_m = re.search(r'"Current"\s*=\s*(\d+)', ad_str)
        d_m = re.search(r'"Description"\s*=\s*"([^"]+)"', ad_str)
        if w_m:
            state.adapter_watts = int(w_m.group(1))
        if v_m:
            state.adapter_voltage_mv = int(v_m.group(1))
        if c_m:
            state.adapter_current_ma = int(c_m.group(1))
        if d_m:
            state.adapter_desc = d_m.group(1)

    pmset_out = _run(["pmset", "-g"])
    if re.search(r"lowpowermode\s+1", pmset_out):
        state.low_power_mode = True
    prevent_m = re.search(r"sleep\s+\d+\s+\(sleep prevented by ([^\)]+)\)", pmset_out)
    if prevent_m:
        state.sleep_preventers = [x.strip() for x in prevent_m.group(1).split(",")]

    # Smart Power Classification (80% Charge Limit Sailing vs Power Bank vs AC vs Battery)
    if cfg.force_powerbank_mode:
        state.power_mode = "POWERBANK_FORCED"
        state.power_mode_reason = "Forced power bank mode via SyncBrake config"
    elif not state.external_connected:
        state.power_mode = "BATTERY"
        state.power_mode_reason = f"Running on internal battery ({state.net_watts:+.1f}W)"
    elif state.is_charging:
        state.power_mode = "AC_CHARGING"
        state.power_mode_reason = f"Charging on AC ({state.adapter_watts}W adapter, {state.net_watts:+.1f}W net)"
    elif state.soc_pct >= cfg.charge_limit_pct:
        state.power_mode = "AC_SAILING_LIMIT"
        state.power_mode_reason = (
            f"AC attached at {state.soc_pct}% (>= {cfg.charge_limit_pct}% battery protection limit; "
            f"intentional SMC sailing mode)"
        )
    elif state.adapter_watts > 0 and state.adapter_watts < cfg.min_adapter_watts:
        state.power_mode = "POWERBANK_OR_WEAK_AC"
        state.power_mode_reason = (
            f"Weak adapter/power bank ({state.adapter_watts}W < {cfg.min_adapter_watts}W threshold) "
            f"at {state.soc_pct}% SOC"
        )
    elif state.net_watts < -2.0:
        state.power_mode = "POWERBANK_OR_WEAK_AC"
        state.power_mode_reason = (
            f"AC attached below {cfg.charge_limit_pct}% SOC but net discharging ({state.net_watts:+.1f}W)"
        )
    else:
        state.power_mode = "AC_FULL"
        state.power_mode_reason = "AC attached and battery maintained"

    return state


def _probe_power_linux(cfg: SyncBrakeConfig) -> PowerState:
    state = PowerState(os_name="linux")
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if "model name" in line:
                state.cpu_model = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass

    ps_base = Path("/sys/class/power_supply")
    bats = sorted(ps_base.glob("BAT*")) if ps_base.exists() else []
    if not bats:
        state.has_battery = False
        return state

    bat = bats[0]

    def _read_str(p: Path) -> str:
        try:
            return p.read_text().strip()
        except Exception:
            return ""

    def _read_int(p: Path) -> int:
        s = _read_str(p)
        return int(s) if s.isdigit() else 0

    state.soc_pct = _read_int(bat / "capacity")
    status_str = _read_str(bat / "status").lower()
    state.is_charging = status_str == "charging"
    state.external_connected = status_str != "discharging"
    threshold = _read_int(bat / "charge_control_end_threshold") or cfg.charge_limit_pct

    power_uw = _read_int(bat / "power_now")
    if power_uw == 0:
        v_uv = _read_int(bat / "voltage_now")
        c_ua = _read_int(bat / "current_now")
        power_uw = int((v_uv / 1e6) * (c_ua / 1e6) * 1e6)
    watts = round(power_uw / 1e6, 1)
    state.net_watts = -watts if status_str == "discharging" else watts
    state.instant_watts = state.net_watts

    if cfg.force_powerbank_mode:
        state.power_mode = "POWERBANK_FORCED"
        state.power_mode_reason = "Forced power bank mode"
    elif status_str == "discharging":
        state.power_mode = "BATTERY"
        state.power_mode_reason = f"Discharging on battery ({state.net_watts:+.1f}W)"
    elif status_str == "not charging" and state.soc_pct >= threshold:
        state.power_mode = "AC_SAILING_LIMIT"
        state.power_mode_reason = f"At/above charge threshold ({state.soc_pct}% >= {threshold}%)"
    else:
        state.power_mode = "AC_CHARGING"
        state.power_mode_reason = f"AC connected ({status_str})"

    return state


def _probe_power_windows(cfg: SyncBrakeConfig) -> PowerState:
    state = PowerState(os_name="windows")
    out = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance -ClassName Win32_Battery | Select-Object EstimatedChargeRemaining,BatteryStatus | ConvertTo-Json",
        ]
    )
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, list):
                data = data[0]
            state.soc_pct = int(data.get("EstimatedChargeRemaining", 100))
            bstatus = int(data.get("BatteryStatus", 2))
            # 1 = Discharging, 2 = AC
            state.external_connected = bstatus != 1
            state.is_charging = bstatus in (2, 6, 7, 8, 9)
            state.power_mode = "AC_CHARGING" if state.external_connected else "BATTERY"
        except Exception:
            pass
    return state


def probe_network() -> NetworkState:
    net = NetworkState()
    sys_name = platform.system().lower()

    # Check local IPs for iPhone hotspot (172.20.10.x)
    if sys_name == "darwin":
        ifconfig_out = _run(["ifconfig"])
        ips = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", ifconfig_out)
        net.local_ips = [ip for ip in ips if ip != "127.0.0.1"]
    elif sys_name == "linux":
        ip_out = _run(["ip", "-4", "-o", "addr", "show"])
        ips = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", ip_out)
        net.local_ips = [ip for ip in ips if ip != "127.0.0.1"]
        metered = _run(["nmcli", "-t", "-f", "GENERAL.METERED", "dev", "show"])
        if "yes" in metered.lower():
            net.is_hotspot = True
            net.hotspot_reason = "NetworkManager metered connection"

    for ip in net.local_ips:
        if ip.startswith("172.20.10."):
            net.is_hotspot = True
            net.hotspot_reason = f"iPhone Personal Hotspot subnet ({ip})"

    # Fast online check
    curl_bin = shutil.which("curl")
    if curl_bin:
        res = subprocess.run(
            [curl_bin, "-sI", "--max-time", "2.5", "https://www.google.com"],
            capture_output=True,
        )
        net.online = res.returncode == 0

    # Check Tailscale status
    ts_info = probe_tailnet()
    if ts_info.get("running"):
        net.tailscale_active = True
        net.tailscale_self_ip = ts_info.get("self_ip", "")
        net.tailscale_peers_online = len([p for p in ts_info.get("peers", []) if p.get("online")])

    return net


def probe_tailnet() -> dict[str, Any]:
    ts_bin = shutil.which("tailscale") or "/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale"
    if not Path(ts_bin).exists():
        return {"running": False, "peers": [], "active_connections": []}

    raw = _run([ts_bin, "status", "--json"], timeout=3.0)
    if not raw:
        return {"running": False, "peers": [], "active_connections": []}

    try:
        data = json.loads(raw)
    except Exception:
        return {"running": False, "peers": [], "active_connections": []}

    backend_state = data.get("BackendState", "")
    self_node = data.get("Self", {})
    self_ips = self_node.get("TailscaleIPs", [])
    peers_raw = data.get("Peer", {})

    peers = []
    for _, p in peers_raw.items():
        peers.append(
            {
                "hostname": p.get("HostName", ""),
                "dns_name": (p.get("DNSName", "") or "").rstrip("."),
                "os": p.get("OS", ""),
                "ips": p.get("TailscaleIPs", []),
                "online": bool(p.get("Online", False)),
                "active": bool(p.get("Active", False)),
                "relay": p.get("Relay", ""),
            }
        )

    peers.sort(key=lambda x: (not x["online"], not x["active"], x["hostname"]))

    return {
        "running": backend_state == "Running",
        "backend_state": backend_state,
        "self_hostname": self_node.get("HostName", ""),
        "self_os": self_node.get("OS", ""),
        "self_ip": self_ips[0] if self_ips else "",
        "peers": peers,
    }


def probe_all() -> dict[str, Any]:
    cfg = load_config()
    power = probe_power(cfg)
    net = probe_network()
    return {
        "power": asdict(power),
        "network": asdict(net),
    }
