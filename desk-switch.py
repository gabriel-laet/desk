#!/usr/bin/env python3
"""desk — orchestrate a desk between machines. Taught command: desk-switch.

Core is orchestration + contract. Hardware plugs in as adapters:

    mouse      — host hop (`mouse.host_switch`; reference: mxswitch)
    keyboard   — presence / follow (`keyboard.presence`; reference: hhkb)
    hosts      — which machine is Mac vs Linux, channels, follow target
    display    — input + PBP/full + OS layout (reference: lgdualup)
                 `adapters.dualup` is a legacy alias of `display`
    smarthome  — list devices + light on/off (reference: alexa)
    kettle     — Fellow Stagg LAN appliance (reference: kettle)
    weather    — Open-Meteo ambient ° + altitude (reference: weather)

Reference adapters live under `adapters/` in this repo and install to
`~/.local/lib/desk-switch/` (`$DESK_SWITCH_LIB`). Third parties drop a
binary + `*.manifest.json` (`api_version: 1`) in that libdir, or
`desk-switch-<id>` on PATH. `mxswitch` / `lgdualup` on PATH stay shims.

    desk-switch status
    desk-switch status --json
    desk-switch to mac
    desk-switch to linux
    desk-switch switch 2          # mouse only, Easy-Switch channel
    desk-switch switch mac        # same as: to mac
    desk-switch pbp <mode>
    desk-switch full
    desk-switch smarthome list     # Echo / smart-home devices (adapter)
    desk-switch smarthome status
    desk-switch smarthome on|off   # desk light via the bound adapter
    desk-switch kettle status      # Fellow Stagg LAN (adapter)
    desk-switch kettle heat 93
    desk-switch kettle off
    desk-switch weather status     # Open-Meteo ambient (adapter)
    desk-switch watch              # HHKB leave → mouse away; USB appear → desk here
    desk-switch watch --dry-run

`hhkb-mx-follow` is the legacy command name for the same program.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SYSTEM = platform.system()
HERE = Path(__file__).resolve().parent
VERSION = "1.7.0"
ADAPTER_API_VERSION = 1
LAYOUT_FULL_MODES = ("full", "off", "none", "solo")
PBP_INPUT_DEFAULTS = {"linux": "dp", "mac": "hdmi1"}  # Studio HDMI1, Omarchy DisplayPort
PBP_INPUT_ORDER = ("linux", "mac")  # secondary first, primary last
LAYOUT_DEFAULT_RETRIES = 16  # EDID after PBP+inputs can lag several seconds
LAYOUT_DEFAULT_DELAY_S = 0.5
LAYOUT_DEFAULT_SETTLE_S = 0.5
HHKB_VID_DEFAULT = 0x04FE
HHKB_PID_DEFAULT = 0x0016
MOUSE_CACHE_TTL_S = 24 * 3600  # last-known channel until contradicted (wake lag / other host)
PEER_CACHE_TTL_S = 20.0
HOST_ALIASES = {
    "mac": "mac",
    "macos": "mac",
    "darwin": "mac",
    "studio": "mac",
    "linux": "linux",
    "lnx": "linux",
    "omarchy": "linux",
    "ser9": "linux",
}
HINT_FOR_HOST = {"mac": "MAC", "linux": "LNX"}
CHANNEL_RE = re.compile(r"currently on\s+(\d+)", re.I)

CONFIG_CANDIDATES = (
    Path.home() / ".config" / "desk-switch" / "config.json",
    Path.home() / ".config" / "hhkb-mx-follow" / "config.json",
    HERE / "config.json",
)

ROLE_CAPABILITIES = {
    "mouse": ("mouse.host_switch",),
    "display": ("display.input", "display.pbp", "display.full", "layout.apply"),
    "keyboard": ("keyboard.presence",),
    "smarthome": ("smarthome.list", "smarthome.status", "light.on", "light.off"),
    "kettle": ("appliance.status", "appliance.heat", "appliance.off"),
    "weather": ("weather.status",),
}
ROLE_REFERENCE_ID = {
    "mouse": "mxswitch",
    "display": "lgdualup",
    "keyboard": "hhkb",
    "smarthome": "alexa",
    "kettle": "kettle",
    "weather": "weather",
}
BUILTIN_MANIFESTS = {
    "mxswitch": {
        "api_version": ADAPTER_API_VERSION,
        "id": "mxswitch",
        "name": "MX Master Easy-Switch",
        "capabilities": ["mouse.host_switch"],
    },
    "lgdualup": {
        "api_version": ADAPTER_API_VERSION,
        "id": "lgdualup",
        "name": "LG DualUp",
        "capabilities": ["display.input", "display.pbp", "display.full", "layout.apply"],
    },
    "dualup-layout": {
        "api_version": ADAPTER_API_VERSION,
        "id": "dualup-layout",
        "name": "DualUp OS layout",
        "capabilities": ["layout.apply"],
    },
    "hhkb": {
        "api_version": ADAPTER_API_VERSION,
        "id": "hhkb",
        "name": "HHKB presence",
        "capabilities": ["keyboard.presence"],
    },
    "alexa": {
        "api_version": ADAPTER_API_VERSION,
        "id": "alexa",
        "name": "Alexa smart home",
        "capabilities": ["smarthome.list", "smarthome.status", "light.on", "light.off"],
    },
    "kettle": {
        "api_version": ADAPTER_API_VERSION,
        "id": "kettle",
        "name": "Fellow Stagg EKG Pro",
        "capabilities": ["appliance.status", "appliance.heat", "appliance.off"],
    },
    "weather": {
        "api_version": ADAPTER_API_VERSION,
        "id": "weather",
        "name": "Open-Meteo ambient",
        "capabilities": ["weather.status"],
    },
}


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}", flush=True)


def default_this_host() -> str:
    return "mac" if SYSTEM == "Darwin" else "linux"


def normalize_host(name: str) -> str:
    key = str(name).strip().lower()
    if key not in HOST_ALIASES:
        raise SystemExit(f"unknown host: {name} (try mac or linux)")
    return HOST_ALIASES[key]


def as_int(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def load_config() -> dict:
    this_host = default_this_host()
    cfg: dict = {
        "target_channel": 1 if this_host == "linux" else 2,
        "this_host": this_host,
        "hhkb_vendor_id": HHKB_VID_DEFAULT,
        "hhkb_product_id": HHKB_PID_DEFAULT,
        "poll_interval_s": 0.5,
        "absent_polls_required": 4,
        "sleep_gap_s": 3.0,
        "switch_retries": 8,
        "switch_retry_delay_s": 0.6,
        "mxswitch": "mxswitch",
        "lgdualup": "lgdualup",
        "switch_monitor": True,
        "switch_pbp": False,
        "pbp_mode": "50-50",
        "follow_hhkb_usb": True,
        "hosts": {
            "mac": {"channel": 1},
            "linux": {"channel": 2},
        },
    }
    user: dict = {}
    for path in CONFIG_CANDIDATES:
        if path.is_file():
            with path.open() as fh:
                user = json.load(fh)
            if not isinstance(user, dict):
                raise SystemExit(f"config is not an object: {path}")
            cfg.update({k: v for k, v in user.items() if k != "adapters"})
            cfg["_config_path"] = str(path)
            break
    else:
        cfg["_config_path"] = "(defaults)"

    apply_adapter_config(cfg, user)

    cfg["hhkb_vendor_id"] = as_int(cfg["hhkb_vendor_id"], HHKB_VID_DEFAULT)
    cfg["hhkb_product_id"] = as_int(cfg["hhkb_product_id"], HHKB_PID_DEFAULT)
    cfg["this_host"] = normalize_host(cfg.get("this_host") or default_this_host())
    if not _user_set_follow_channel(user):
        cfg["target_channel"] = 1 if cfg["this_host"] == "linux" else 2
    cfg["target_channel"] = as_int(cfg["target_channel"], 1 if cfg["this_host"] == "linux" else 2)
    user_hosts = user.get("hosts") if isinstance(user.get("hosts"), dict) else {}
    merged_hosts = {**user_hosts, **adapter_host_overrides(user)}
    cfg["hosts"] = resolve_hosts(cfg, merged_hosts)
    if isinstance(user.get("adapters"), dict):
        cfg["adapters"] = user["adapters"]
    return cfg


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _user_set_follow_channel(user: dict) -> bool:
    if "target_channel" in user:
        return True
    hosts_ad = _as_dict(_as_dict(user.get("adapters")).get("hosts"))
    return "follow_channel" in hosts_ad


def display_adapter_cfg(container: dict) -> dict:
    """`adapters.display` overlays legacy `adapters.dualup` (same role)."""
    adapters = _as_dict(container.get("adapters"))
    return {**_as_dict(adapters.get("dualup")), **_as_dict(adapters.get("display"))}


def tray_density(cfg: dict) -> str:
    raw = str(cfg.get("_tray_density") or "strip").strip().lower()
    return "chips" if raw == "chips" else "strip"


def apply_adapter_config(cfg: dict, user: dict) -> dict:
    """Map adapters.* onto the internal config. Legacy keys still win if set.

    More adapters can land here later without renaming the model.
    """
    adapters = _as_dict(user.get("adapters"))
    mouse = _as_dict(adapters.get("mouse"))
    hosts_ad = _as_dict(adapters.get("hosts"))
    keyboard = _as_dict(adapters.get("keyboard"))
    smarthome = _as_dict(adapters.get("smarthome"))
    kettle = _as_dict(adapters.get("kettle"))
    weather = _as_dict(adapters.get("weather"))
    dual = display_adapter_cfg(user)
    ui = _as_dict(user.get("ui"))
    tray = _as_dict(ui.get("tray"))

    cfg["_mouse_enabled"] = bool(mouse.get("enabled", True))
    if mouse.get("backend"):
        cfg["_mouse_backend"] = str(mouse["backend"])
    if mouse.get("path"):
        cfg["mxswitch"] = str(mouse["path"])
        cfg["_mouse_path"] = str(mouse["path"])

    if hosts_ad.get("this_host"):
        cfg["this_host"] = hosts_ad["this_host"]
    if hosts_ad.get("follow_channel") is not None:
        cfg["target_channel"] = hosts_ad["follow_channel"]
    if hosts_ad.get("peer"):
        cfg["_peer"] = str(hosts_ad["peer"])
    follow_usb = user.get("follow_hhkb_usb")
    if follow_usb is None:
        follow_usb = hosts_ad.get("follow_hhkb_usb")
    cfg["follow_hhkb_usb"] = True if follow_usb is None else bool(follow_usb)

    cfg["_keyboard_enabled"] = bool(keyboard.get("enabled", True))
    if keyboard.get("backend"):
        cfg["_keyboard_backend"] = str(keyboard["backend"])
    if keyboard.get("path"):
        cfg["_keyboard_path"] = str(keyboard["path"])

    cfg["_smarthome_enabled"] = bool(smarthome.get("enabled", True))
    if smarthome.get("backend"):
        cfg["_smarthome_backend"] = str(smarthome["backend"])
    if smarthome.get("path"):
        cfg["_smarthome_path"] = str(smarthome["path"])
    extras: dict[str, str] = {}
    for key in ("device", "light_on", "light_off", "cli"):
        if smarthome.get(key):
            extras[key] = str(smarthome[key])
    cfg["_smarthome_extras"] = extras

    cfg["_kettle_enabled"] = bool(kettle.get("enabled", True))
    if kettle.get("backend"):
        cfg["_kettle_backend"] = str(kettle["backend"])
    if kettle.get("path"):
        cfg["_kettle_path"] = str(kettle["path"])
    kettle_extras: dict[str, str] = {}
    for key in ("host", "timeout"):
        if kettle.get(key) is not None and str(kettle.get(key)):
            kettle_extras[key] = str(kettle[key])
    cfg["_kettle_extras"] = kettle_extras

    cfg["_weather_enabled"] = bool(weather.get("enabled", True))
    if weather.get("backend"):
        cfg["_weather_backend"] = str(weather["backend"])
    if weather.get("path"):
        cfg["_weather_path"] = str(weather["path"])
    weather_extras: dict[str, str] = {}
    for key, flag in (
        ("latitude", "--latitude"),
        ("longitude", "--longitude"),
        ("timezone", "--timezone"),
        ("label", "--label"),
        ("altitude_m", "--altitude-m"),
        ("url", "--url"),
        ("timeout", "--timeout"),
    ):
        if weather.get(key) is not None and str(weather.get(key)):
            weather_extras[flag] = str(weather[key])
    cfg["_weather_extras"] = weather_extras

    cfg["_dualup_enabled"] = bool(dual.get("enabled", True))
    cfg["_dualup_layout"] = bool(dual.get("layout", True))
    if dual.get("enabled") is False:
        cfg["switch_monitor"] = False
    if dual.get("backend"):
        cfg["_display_backend"] = str(dual["backend"])
    if dual.get("path"):
        cfg["lgdualup"] = str(dual["path"])
        cfg["_display_path"] = str(dual["path"])
    if dual.get("pbp_mode"):
        cfg["pbp_mode"] = str(dual["pbp_mode"])
    if "switch_pbp" in dual:
        cfg["switch_pbp"] = bool(dual["switch_pbp"])
    if dual.get("layout_helper"):
        cfg["_dualup_layout_helper"] = str(dual["layout_helper"])
    if dual.get("display_id"):
        cfg["_dualup_display_id"] = str(dual["display_id"])
    if dual.get("peer"):
        cfg["_dualup_peer"] = str(dual["peer"])
    cfg["_dualup_layout_retries"] = as_int(dual.get("layout_retries"), LAYOUT_DEFAULT_RETRIES)
    raw_delay = dual.get("layout_retry_delay_s")
    cfg["_dualup_layout_retry_delay_s"] = float(
        LAYOUT_DEFAULT_DELAY_S if raw_delay is None else raw_delay
    )
    raw_settle = dual.get("layout_settle_s")
    cfg["_dualup_layout_settle_s"] = float(
        LAYOUT_DEFAULT_SETTLE_S if raw_settle is None else raw_settle
    )
    if tray.get("density"):
        cfg["_tray_density"] = str(tray["density"])
    cfg["_tray_lights"] = bool(tray.get("lights"))
    if isinstance(tray.get("slots"), list):
        cfg["_tray_slots"] = [str(item) for item in tray["slots"] if str(item).strip()]
    return cfg


def adapter_host_overrides(user: dict) -> dict:
    """Host map entries from adapters.hosts and display/dualup inputs."""
    adapters = _as_dict(user.get("adapters"))
    hosts_ad = _as_dict(adapters.get("hosts"))
    dual = display_adapter_cfg(user)
    out: dict = {}
    for name, raw in hosts_ad.items():
        if name in ("this_host", "follow_channel"):
            continue
        out[name] = raw
    for name, inp in _as_dict(dual.get("inputs")).items():
        entry = _host_entry(out.get(name, {}))
        entry["dualup_input"] = inp
        out[name] = entry
    return out


def _host_entry(raw: object) -> dict:
    if isinstance(raw, int):
        return {"channel": raw}
    if isinstance(raw, dict):
        out = dict(raw)
        if "channel" in out:
            out["channel"] = as_int(out["channel"], 0)
        return out
    return {}


def resolve_hosts(cfg: dict, user_hosts: dict | None = None) -> dict:
    """Merge configured host map with the documented Mac=1 / Linux=2 pairing.

    `to mac` / `to linux` read hosts.*.channel. `watch` still uses
    target_channel. If the user never named the other host, inherit
    target_channel so a legacy one-field config keeps working.
    """
    this_host = normalize_host(cfg.get("this_host") or default_this_host())
    other = "mac" if this_host == "linux" else "linux"
    merged = {
        "mac": {"channel": 1},
        "linux": {"channel": 2},
    }
    named: set[str] = set()
    if user_hosts is None:
        supplied = cfg.get("hosts") if isinstance(cfg.get("hosts"), dict) else {}
    else:
        supplied = user_hosts if isinstance(user_hosts, dict) else {}
    for name, raw in supplied.items():
        try:
            key = normalize_host(name)
        except SystemExit:
            continue
        entry = _host_entry(raw)
        merged[key] = {**merged.get(key, {}), **entry}
        if "channel" in entry:
            named.add(key)
    if other not in named and cfg.get("target_channel") is not None:
        merged[other] = {
            **merged[other],
            "channel": as_int(cfg["target_channel"], merged[other]["channel"]),
        }
    return merged


def host_channel(cfg: dict, host: str) -> int:
    host = normalize_host(host)
    channel = (cfg.get("hosts") or {}).get(host, {}).get("channel")
    if not channel:
        raise SystemExit(f"no Easy-Switch channel configured for host {host}")
    return int(channel)


def host_for_channel(cfg: dict, channel: int | None) -> str | None:
    if channel is None:
        return None
    for name, spec in (cfg.get("hosts") or {}).items():
        if as_int(spec.get("channel"), 0) == int(channel):
            return name
    return None


def cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "desk-switch"


def mouse_cache_path() -> Path:
    return cache_dir() / "mouse-channel.json"


def load_mouse_cache(now: float | None = None) -> tuple[int | None, float | None]:
    """Last live/switched Easy-Switch channel. None if missing or expired."""
    path = mouse_cache_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(raw, dict):
        return None, None
    channel = raw.get("channel")
    ts = raw.get("ts")
    try:
        channel_i = int(channel)
        ts_f = float(ts)
    except (TypeError, ValueError):
        return None, None
    if channel_i not in (1, 2, 3):
        return None, None
    age = (time.time() if now is None else now) - ts_f
    if age < 0 or age > MOUSE_CACHE_TTL_S:
        return None, None
    return channel_i, ts_f


def save_mouse_cache(channel: int, now: float | None = None) -> None:
    if channel not in (1, 2, 3):
        return
    path = mouse_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"channel": int(channel), "ts": time.time() if now is None else now})
            + "\n"
        )
    except OSError:
        return


def target_hint(cfg: dict, mouse_channel: int | None = None, hhkb: bool | None = None) -> str:
    """Desk focus: mouse Easy-Switch channel wins; local HHKB is a weak fallback."""
    return resolve_target_hint(cfg, mouse_channel=mouse_channel, hhkb_present=hhkb)[0]


def resolve_target_hint(
    cfg: dict,
    *,
    mouse_channel: int | None = None,
    hhkb_present: bool | None = None,
    peer_channel: int | None = None,
    mouse_source: str | None = None,
) -> tuple[str, str]:
    """Return (MAC|LNX|?, source). Mouse channel is desk focus, not local HHKB."""
    for channel, source in (
        (mouse_channel, mouse_source or "mouse"),
        (peer_channel, "peer_mouse"),
    ):
        host = host_for_channel(cfg, channel)
        if host:
            return HINT_FOR_HOST.get(host, "?"), source
    if hhkb_present:
        return HINT_FOR_HOST.get(cfg.get("this_host"), "?"), "hhkb"
    return "?", "unknown"


def run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


_HHKB_MOD = None


def hhkb_source_path() -> Path | None:
    bundled = HERE / "adapters" / "hhkb" / "hhkb.py"
    if bundled.is_file():
        return bundled
    installed = libexec_dir() / "hhkb"
    if installed.is_file():
        return installed
    return None


def alexa_source_path() -> Path | None:
    bundled = HERE / "adapters" / "alexa" / "alexa.py"
    if bundled.is_file():
        return bundled
    installed = libexec_dir() / "alexa"
    if installed.is_file():
        return installed
    return None


def python_adapter_source_path(adapter_id: str) -> Path | None:
    bundled = HERE / "adapters" / adapter_id / f"{adapter_id}.py"
    if bundled.is_file():
        return bundled
    installed = libexec_dir() / adapter_id
    if installed.is_file():
        return installed
    return None


def kettle_source_path() -> Path | None:
    return python_adapter_source_path("kettle")


def weather_source_path() -> Path | None:
    return python_adapter_source_path("weather")


def load_hhkb_module():
    """Import the HHKB reference adapter (source tree or installed libdir)."""
    global _HHKB_MOD
    if _HHKB_MOD is not None:
        return _HHKB_MOD
    path = hhkb_source_path()
    if path is None:
        raise SystemExit("hhkb adapter module not found — run make install")
    spec = importlib.util.spec_from_file_location("desk_switch_hhkb", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load hhkb adapter: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _HHKB_MOD = mod
    return mod


def empty_hhkb_probe(*, unknown: bool = False) -> dict:
    return load_hhkb_module().empty_hhkb_probe(unknown=unknown)


def hhkb_transport_of(usb: bool, bluetooth: bool, present: bool, unknown: bool = False) -> str:
    return load_hhkb_module().hhkb_transport_of(usb, bluetooth, present, unknown)


def merge_hhkb_probes(*probes: dict) -> dict:
    return load_hhkb_module().merge_hhkb_probes(*probes)


def parse_linux_hhkb_sysfs(hid_dir: Path, usb_dir: Path | None, vid: int, pid: int) -> dict:
    return load_hhkb_module().parse_linux_hhkb_sysfs(hid_dir, usb_dir, vid, pid)


def hhkb_probe_linux(vid: int, pid: int) -> dict:
    return load_hhkb_module().hhkb_probe_linux(vid, pid)


def split_ioreg_nodes(text: str) -> list[str]:
    return load_hhkb_module().split_ioreg_nodes(text)


def parse_ioreg_hhkb(hid_text: str, usb_text: str, bt_text: str, vid: int, pid: int) -> dict:
    return load_hhkb_module().parse_ioreg_hhkb(hid_text, usb_text, bt_text, vid, pid)


def parse_hidutil_hhkb(text: str, vid: int, pid: int) -> dict:
    return load_hhkb_module().parse_hidutil_hhkb(text, vid, pid)


def hhkb_probe_macos(vid: int, pid: int) -> dict:
    return load_hhkb_module().hhkb_probe_macos(vid, pid)


def hhkb_present_macos(vid: int, pid: int) -> bool:
    return bool(hhkb_probe_macos(vid, pid).get("present"))


def hhkb_present_linux(vid: int, pid: int) -> bool:
    return bool(hhkb_probe_linux(vid, pid).get("present"))


def _hhkb_probe_exec(path: Path, vid: int, pid: int) -> dict | None:
    try:
        proc = run([str(path), "info", "--vid", hex(vid), "--pid", hex(pid)], timeout=4.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "present" not in data:
        return None
    return data


def hhkb_probe(cfg: dict) -> dict:
    """keyboard.presence: bound adapter binary, else in-process HHKB module."""
    if not cfg.get("_keyboard_enabled", True):
        return empty_hhkb_probe()
    vid = cfg["hhkb_vendor_id"]
    pid = cfg["hhkb_product_id"]
    bound = bind_role(cfg, "keyboard")
    path = bound.get("path")
    if path:
        probe = _hhkb_probe_exec(Path(path), vid, pid)
        if probe is not None:
            return probe
    return load_hhkb_module().hhkb_probe(vid, pid, SYSTEM)


def hhkb_present(cfg: dict) -> bool:
    return bool(hhkb_probe(cfg).get("present"))


def libexec_dir() -> Path:
    env = os.environ.get("DESK_SWITCH_LIB")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "lib" / "desk-switch"


def which_cmd(name: str) -> Path | None:
    expanded = Path(name).expanduser()
    if expanded.is_file() and os.access(expanded, os.X_OK):
        return expanded
    found = shutil.which(name)
    if found:
        return Path(found)
    local = Path.home() / ".local" / "bin" / Path(name).name
    if local.is_file() and os.access(local, os.X_OK):
        return local
    return None


def _is_exe(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_backend_id(backend: str) -> Path | None:
    """Resolve an adapter id: libdir/<id>, libdir/<id>/<id>, PATH desk-switch-<id>, PATH <id>."""
    lib = libexec_dir()
    for candidate in (lib / backend, lib / backend / backend):
        if _is_exe(candidate):
            return candidate
    prefixed = which_cmd(f"desk-switch-{backend}")
    if prefixed:
        return prefixed
    return which_cmd(backend)


def which_adapter(backend: str, configured: str | None = None) -> Path | None:
    """Resolve an adapter helper: explicit path, then libexec, then PATH."""
    if configured:
        expanded = Path(str(configured)).expanduser()
        if _is_exe(expanded):
            return expanded
    lib = libexec_dir()
    for candidate in (lib / backend, lib / backend / backend):
        if _is_exe(candidate):
            return candidate
    if configured:
        found = which_cmd(str(configured))
        if found:
            return found
        if Path(str(configured)).name != backend:
            return None
    prefixed = which_cmd(f"desk-switch-{backend}")
    if prefixed:
        return prefixed
    return which_cmd(backend)


def manifest_api_ok(raw: dict) -> bool:
    try:
        major = int(str(raw.get("api_version", 0)).split(".", 1)[0])
    except (TypeError, ValueError):
        return False
    return major == ADAPTER_API_VERSION and bool(raw.get("id"))


def load_manifest_file(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict) or not manifest_api_ok(raw):
        return None
    return raw


def builtin_manifest(adapter_id: str) -> dict | None:
    raw = BUILTIN_MANIFESTS.get(adapter_id)
    return dict(raw) if raw else None


def manifest_for(adapter_id: str, binary: Path | None = None) -> dict:
    lib = libexec_dir()
    candidates = [
        lib / f"{adapter_id}.manifest.json",
        lib / adapter_id / "manifest.json",
    ]
    if binary is not None:
        candidates.extend(
            [
                binary.parent / f"{adapter_id}.manifest.json",
                binary.parent / "manifest.json",
                binary.with_name(f"{adapter_id}.manifest.json"),
            ]
        )
    for path in candidates:
        loaded = load_manifest_file(path)
        if loaded and str(loaded.get("id")) == adapter_id:
            return loaded
    return builtin_manifest(adapter_id) or {
        "api_version": ADAPTER_API_VERSION,
        "id": adapter_id,
        "name": adapter_id,
        "capabilities": [],
    }


def _binary_for_manifest(lib: Path, adapter_id: str, manifest_path: Path) -> Path | None:
    if manifest_path.name == "manifest.json":
        nested = manifest_path.parent / adapter_id
        if _is_exe(nested):
            return nested
    flat = lib / adapter_id
    if _is_exe(flat):
        return flat
    nested = lib / adapter_id / adapter_id
    if _is_exe(nested):
        return nested
    return resolve_backend_id(adapter_id)


def scan_libdir_manifests(lib: Path | None = None) -> list[dict]:
    """File-drop manifests: <libdir>/<id>.manifest.json or <libdir>/<id>/manifest.json."""
    root = lib or libexec_dir()
    found: dict[str, dict] = {}
    if not root.is_dir():
        return []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        manifest_path: Path | None = None
        if entry.is_file() and entry.name.endswith(".manifest.json"):
            manifest_path = entry
        elif entry.is_dir() and (entry / "manifest.json").is_file():
            manifest_path = entry / "manifest.json"
        if manifest_path is None:
            continue
        raw = load_manifest_file(manifest_path)
        if raw is None:
            continue
        adapter_id = str(raw["id"])
        binary = _binary_for_manifest(root, adapter_id, manifest_path)
        found[adapter_id] = {
            "id": adapter_id,
            "name": raw.get("name") or adapter_id,
            "capabilities": list(raw.get("capabilities") or []),
            "path": str(binary) if binary else None,
            "manifest": raw,
            "source": "scan",
        }
    return list(found.values())


def role_matches_caps(role: str, capabilities: list) -> bool:
    wanted = ROLE_CAPABILITIES.get(role) or ()
    have = {str(c) for c in capabilities}
    return any(cap in have for cap in wanted)


def _role_enabled(cfg: dict, role: str) -> bool:
    if role == "mouse":
        return bool(cfg.get("_mouse_enabled", True))
    if role == "display":
        return bool(cfg.get("_dualup_enabled", True))
    if role == "keyboard":
        return bool(cfg.get("_keyboard_enabled", True))
    if role == "smarthome":
        return bool(cfg.get("_smarthome_enabled", True))
    if role == "kettle":
        return bool(cfg.get("_kettle_enabled", True))
    if role == "weather":
        return bool(cfg.get("_weather_enabled", True))
    return True


def _role_pin(cfg: dict, role: str) -> tuple[str | None, str | None]:
    """Return (backend, path) pins from config. path wins over scan."""
    if role == "mouse":
        return cfg.get("_mouse_backend"), cfg.get("_mouse_path") or None
    if role == "display":
        return cfg.get("_display_backend"), cfg.get("_display_path") or None
    if role == "keyboard":
        return cfg.get("_keyboard_backend"), cfg.get("_keyboard_path") or None
    if role == "smarthome":
        return cfg.get("_smarthome_backend"), cfg.get("_smarthome_path") or None
    if role == "kettle":
        return cfg.get("_kettle_backend"), cfg.get("_kettle_path") or None
    if role == "weather":
        return cfg.get("_weather_backend"), cfg.get("_weather_path") or None
    return None, None


def _binding(adapter_id: str | None, path: Path | None, source: str, **extra: object) -> dict:
    manifest = manifest_for(adapter_id, path) if adapter_id else None
    out = {
        "id": adapter_id,
        "path": path,
        "source": source,
        "manifest": manifest,
        "capabilities": list((manifest or {}).get("capabilities") or []),
    }
    out.update(extra)
    return out


def bind_role(cfg: dict, role: str) -> dict:
    """Bind one role. Pins override scan; reference id wins ties; no silent guess."""
    cache = cfg.setdefault("_bindings", {})
    if role in cache:
        return cache[role]
    if not _role_enabled(cfg, role):
        cache[role] = _binding(None, None, "disabled", enabled=False)
        return cache[role]
    backend, path_pin = _role_pin(cfg, role)
    if path_pin:
        expanded = Path(str(path_pin)).expanduser()
        if _is_exe(expanded):
            adapter_id = backend or ROLE_REFERENCE_ID.get(role) or expanded.name
            cache[role] = _binding(adapter_id, expanded, "path", enabled=True)
            return cache[role]
        cache[role] = _binding(backend, None, "path", enabled=True)
        return cache[role]
    if backend:
        resolved = resolve_backend_id(str(backend))
        cache[role] = _binding(str(backend), resolved, "backend", enabled=True)
        return cache[role]
    scanned = [item for item in scan_libdir_manifests() if role_matches_caps(role, item.get("capabilities") or [])]
    reference = ROLE_REFERENCE_ID.get(role)
    if len(scanned) == 1:
        item = scanned[0]
        path = Path(item["path"]) if item.get("path") else resolve_backend_id(item["id"])
        cache[role] = _binding(item["id"], path, "scan", enabled=True)
        return cache[role]
    if len(scanned) > 1:
        preferred = next((item for item in scanned if item["id"] == reference), None)
        if preferred:
            path = Path(preferred["path"]) if preferred.get("path") else resolve_backend_id(preferred["id"])
            cache[role] = _binding(
                preferred["id"],
                path,
                "scan",
                enabled=True,
                candidates=[item["id"] for item in scanned],
            )
            return cache[role]
        cache[role] = _binding(
            None,
            None,
            "ambiguous",
            enabled=True,
            candidates=[item["id"] for item in scanned],
        )
        return cache[role]
    if reference:
        if role == "mouse":
            resolved = which_adapter("mxswitch", str(cfg.get("mxswitch") or "mxswitch"))
            cache[role] = _binding("mxswitch", resolved, "reference", enabled=True)
            return cache[role]
        if role == "display":
            resolved = which_adapter("lgdualup", str(cfg.get("lgdualup") or "lgdualup"))
            cache[role] = _binding("lgdualup", resolved, "reference", enabled=True)
            return cache[role]
        if role == "keyboard":
            resolved = resolve_backend_id(reference) or hhkb_source_path()
            cache[role] = _binding("hhkb", resolved, "incore" if resolved == hhkb_source_path() else "reference", enabled=True)
            return cache[role]
        if role == "smarthome":
            resolved = resolve_backend_id(reference) or alexa_source_path()
            cache[role] = _binding("alexa", resolved, "reference" if resolved else "missing", enabled=True)
            return cache[role]
        if role == "kettle":
            resolved = resolve_backend_id(reference) or kettle_source_path()
            cache[role] = _binding("kettle", resolved, "reference" if resolved else "missing", enabled=True)
            return cache[role]
        if role == "weather":
            resolved = resolve_backend_id(reference) or weather_source_path()
            cache[role] = _binding("weather", resolved, "reference" if resolved else "missing", enabled=True)
            return cache[role]
    cache[role] = _binding(reference, None, "missing", enabled=True)
    return cache[role]


def discovered_adapters(cfg: dict | None = None) -> list[dict]:
    found = {item["id"]: item for item in scan_libdir_manifests()}
    for adapter_id, raw in BUILTIN_MANIFESTS.items():
        if adapter_id in found:
            continue
        path = resolve_backend_id(adapter_id)
        if path is None and adapter_id == "hhkb":
            path = hhkb_source_path()
        if path is None and adapter_id == "alexa":
            path = alexa_source_path()
        if path is None and adapter_id == "kettle":
            path = kettle_source_path()
        if path is None and adapter_id == "weather":
            path = weather_source_path()
        if path is None:
            continue
        found[adapter_id] = {
            "id": adapter_id,
            "name": raw.get("name") or adapter_id,
            "capabilities": list(raw.get("capabilities") or []),
            "path": str(path),
            "manifest": dict(raw),
            "source": "builtin",
        }
    items = list(found.values())
    items.sort(key=lambda item: str(item.get("id") or ""))
    if cfg is not None:
        cfg["_discovered"] = items
    return items


def mxswitch_path(cfg: dict) -> Path:
    bound = bind_role(cfg, "mouse")
    path = bound.get("path")
    if path is None:
        raise SystemExit(
            f"mouse adapter not found: {bound.get('id') or cfg.get('_mouse_backend') or cfg.get('mxswitch')}"
        )
    return Path(path)


def lgdualup_path(cfg: dict) -> Path | None:
    if not cfg.get("_dualup_enabled", True):
        return None
    bound = bind_role(cfg, "display")
    path = bound.get("path")
    return Path(path) if path else None


def smarthome_adapter_path(cfg: dict) -> Path | None:
    if not cfg.get("_smarthome_enabled", True):
        return None
    bound = bind_role(cfg, "smarthome")
    path = bound.get("path")
    return Path(path) if path else None


def smarthome_adapter_cmd(cfg: dict, *verbs: str) -> list[str] | None:
    """Argv for the bound smarthome adapter. Core does not name a vendor CLI."""
    path = smarthome_adapter_path(cfg)
    if path is None:
        return None
    cmd = [str(path), *[str(v) for v in verbs]]
    extras = cfg.get("_smarthome_extras") if isinstance(cfg.get("_smarthome_extras"), dict) else {}
    if extras.get("device"):
        cmd.extend(["--device", str(extras["device"])])
    if extras.get("light_on"):
        cmd.extend(["--on-phrase", str(extras["light_on"])])
    if extras.get("light_off"):
        cmd.extend(["--off-phrase", str(extras["light_off"])])
    if extras.get("cli"):
        cmd.extend(["--cli", str(extras["cli"])])
    return cmd


def invoke_smarthome(cfg: dict, verb: str, *extra: str, timeout: float = 20.0) -> dict | None:
    cmd = smarthome_adapter_cmd(cfg, verb, *extra)
    if cmd is None:
        return None
    return invoke_adapter_cmd(cmd, timeout=timeout)


def role_adapter_path(cfg: dict, role: str, enabled_key: str) -> Path | None:
    if not cfg.get(enabled_key, True):
        return None
    bound = bind_role(cfg, role)
    path = bound.get("path")
    return Path(path) if path else None


def kettle_adapter_path(cfg: dict) -> Path | None:
    return role_adapter_path(cfg, "kettle", "_kettle_enabled")


def weather_adapter_path(cfg: dict) -> Path | None:
    return role_adapter_path(cfg, "weather", "_weather_enabled")


def invoke_adapter_cmd(cmd: list[str], timeout: float = 8.0) -> dict | None:
    if not cmd:
        return None
    try:
        proc = run(cmd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    text = (proc.stdout or "").strip()
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        data = {"raw": text}
    if not isinstance(data, dict):
        data = {"data": data}
    if proc.returncode != 0 and "ok" not in data:
        data["ok"] = False
        data.setdefault("error", (proc.stderr or "").strip() or f"exit {proc.returncode}")
    data["_returncode"] = proc.returncode
    return data


def kettle_adapter_cmd(cfg: dict, *verbs: str) -> list[str] | None:
    path = kettle_adapter_path(cfg)
    if path is None:
        return None
    cmd = [str(path), *[str(v) for v in verbs]]
    extras = cfg.get("_kettle_extras") if isinstance(cfg.get("_kettle_extras"), dict) else {}
    if extras.get("host"):
        cmd.extend(["--host", str(extras["host"])])
    if extras.get("timeout"):
        cmd.extend(["--timeout", str(extras["timeout"])])
    return cmd


def weather_adapter_cmd(cfg: dict, *verbs: str) -> list[str] | None:
    path = weather_adapter_path(cfg)
    if path is None:
        return None
    cmd = [str(path), *[str(v) for v in verbs]]
    extras = cfg.get("_weather_extras") if isinstance(cfg.get("_weather_extras"), dict) else {}
    for flag, value in extras.items():
        cmd.extend([str(flag), str(value)])
    return cmd


def invoke_kettle(cfg: dict, verb: str, *extra: str, timeout: float = 8.0) -> dict | None:
    cmd = kettle_adapter_cmd(cfg, verb, *extra)
    if cmd is None:
        return None
    return invoke_adapter_cmd(cmd, timeout=timeout)


def invoke_weather(cfg: dict, verb: str, *extra: str, timeout: float = 4.0) -> dict | None:
    cmd = weather_adapter_cmd(cfg, verb, *extra)
    if cmd is None:
        return None
    return invoke_adapter_cmd(cmd, timeout=timeout)


def switch_mouse(cfg: dict, channel: int | None = None) -> int:
    if not cfg.get("_mouse_enabled", True):
        print("mouse adapter disabled")
        return 0
    dest = int(cfg["target_channel"] if channel is None else channel)
    cmd = [str(mxswitch_path(cfg)), str(dest)]
    try:
        proc = run(cmd, timeout=8.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"mouse adapter failed to start: {exc}")
        return 1
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        log(out)
    if err:
        log(err)
    if proc.returncode == 0:
        save_mouse_cache(dest)
    return proc.returncode


def parse_mouse_channel(info: str) -> int | None:
    match = CHANNEL_RE.search(info or "")
    if not match:
        return None
    return int(match.group(1))


def mouse_info(cfg: dict) -> tuple[str, int | None]:
    try:
        proc = run([str(mxswitch_path(cfg)), "--info"], timeout=8.0)
        text = (proc.stdout or proc.stderr or "").rstrip()
        return text, parse_mouse_channel(text)
    except SystemExit as exc:
        return str(exc), None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc), None


def mouse_snapshot(cfg: dict) -> dict:
    """Live Easy-Switch channel, or last-known if the mouse is asleep / on the other host."""
    info, live = mouse_info(cfg)
    cached, cached_ts = load_mouse_cache()
    if live is not None:
        save_mouse_cache(live)
        channel, source, online, stale = live, "mouse", True, False
    elif cached is not None:
        channel, source, online, stale = cached, "mouse_cached", False, True
    else:
        channel, source, online, stale = None, None, False, False
    return {
        "info": info,
        "channel": channel,
        "channel_live": live,
        "online": online,
        "stale": stale,
        "source": source,
        "host": host_for_channel(cfg, channel),
        "cached_ts": cached_ts,
    }


def call_lgdualup(cfg: dict, args: list[str], *, missing: str) -> int:
    path = lgdualup_path(cfg)
    if path is None:
        print(missing)
        return 0
    timeout = 15.0 if args and args[0] == "pbp-assign" else 8.0
    try:
        proc = run([str(path), *args], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"lgdualup failed to start: {exc}", file=sys.stderr)
        return 1
    out = (proc.stdout or "").rstrip()
    err = (proc.stderr or "").rstrip()
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr if proc.returncode else sys.stdout)
    return proc.returncode


def layout_verb(mode: str) -> str:
    """Map an lgdualup pbp mode string to a dualup-layout profile."""
    key = (mode or "").strip().lower()
    if key in LAYOUT_FULL_MODES:
        return "full"
    return "pbp"


def dualup_layout_path(cfg: dict) -> Path | None:
    adapters = display_adapter_cfg(cfg)
    if cfg.get("_dualup_layout", adapters.get("layout", True)) is False:
        return None
    configured = cfg.get("_dualup_layout_helper") or adapters.get("layout_helper")
    path = which_adapter("dualup-layout", str(configured) if configured else None)
    if path:
        return path
    sub = "macos" if SYSTEM == "Darwin" else "linux"
    bundled = HERE / "adapters" / "lgdualup" / sub / "dualup-layout"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return bundled
    return None


def _print_helper(proc: subprocess.CompletedProcess) -> None:
    out = (proc.stdout or "").rstrip()
    err = (proc.stderr or "").rstrip()
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr if proc.returncode else sys.stdout)


def apply_dualup_layout(cfg: dict, mode: str) -> int:
    """Apply host resolution/rotation; retry while EDID still shows the old mode."""
    path = dualup_layout_path(cfg)
    if path is None:
        if cfg.get("_dualup_layout") is False:
            return 0
        print("dualup layout helper not found — run `make install` (OS layout skipped)")
        return 0
    adapters = display_adapter_cfg(cfg)
    display_id = str(
        cfg.get("_dualup_display_id") or adapters.get("display_id") or ""
    ).strip()
    retries = as_int(
        cfg.get("_dualup_layout_retries", adapters.get("layout_retries")),
        LAYOUT_DEFAULT_RETRIES,
    )
    raw_delay = cfg.get("_dualup_layout_retry_delay_s")
    if raw_delay is None:
        raw_delay = adapters.get("layout_retry_delay_s", LAYOUT_DEFAULT_DELAY_S)
    delay = float(raw_delay)
    verb = layout_verb(mode)
    cmd = [str(path), verb]
    if display_id:
        cmd.extend(["--id", display_id])
    user = _as_dict(_as_dict(adapters.get("layouts")).get(verb))
    if user.get("res"):
        cmd.extend(["--res", str(user["res"])])
    if user.get("degree") is not None:
        cmd.extend(["--degree", str(user["degree"])])
    last_rc = 1
    for attempt in range(1, max(retries, 1) + 1):
        try:
            proc = run(cmd, timeout=12.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"dualup-layout failed to start: {exc}", file=sys.stderr)
            return 1
        _print_helper(proc)
        last_rc = proc.returncode
        if last_rc == 0:
            return 0
        if last_rc != 2 or attempt >= retries:
            break
        time.sleep(delay)
    return last_rc


def apply_peer_layout(cfg: dict, mode: str) -> int:
    """Best-effort SSH of layout-only to the other machine (no USB toggle)."""
    adapters = display_adapter_cfg(cfg)
    peer = str(cfg.get("_dualup_peer") or adapters.get("peer") or "").strip()
    if not peer:
        return 0
    verb = layout_verb(mode)
    remote = str(adapters.get("peer_layout") or "~/.local/lib/desk-switch/dualup-layout")
    print(f"DualUp peer layout → {peer} {verb}")
    try:
        proc = run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", peer, f"{remote} {verb}"],
            timeout=15.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"dualup peer layout skipped: {exc}", file=sys.stderr)
        return 0
    _print_helper(proc)
    if proc.returncode != 0:
        print(f"dualup peer layout failed (rc={proc.returncode})", file=sys.stderr)
    return 0


def dualup_pbp_inputs(cfg: dict) -> list[tuple[str, str]]:
    """PBP input pair in apply order: Linux/dp (secondary), then Mac/hdmi1 (primary)."""
    hosts = cfg.get("hosts") or {}
    out: list[tuple[str, str]] = []
    for host in PBP_INPUT_ORDER:
        spec = _as_dict(hosts.get(host))
        name = str(spec.get("dualup_input") or "").strip() or PBP_INPUT_DEFAULTS[host]
        out.append((host, name))
    return out


def dualup_assign_pbp_inputs(cfg: dict) -> int:
    """Assign PBP Main/Sub. 0xF4 alone cannot set the sub window (stays HDMI2)."""
    by_host = dict(dualup_pbp_inputs(cfg))
    main = by_host.get("mac") or PBP_INPUT_DEFAULTS["mac"]
    sub = by_host.get("linux") or PBP_INPUT_DEFAULTS["linux"]
    print(f"DualUp PBP assign → main={main} (mac)  sub={sub} (linux)")
    return call_lgdualup(
        cfg,
        ["pbp-assign", main, sub],
        missing="lgdualup not on PATH — run `make install`",
    )


def dualup_set_mode(cfg: dict, mode: str, *, missing: str) -> int:
    """USB PBP/full, then (for PBP) input pair, settle, then tilted OS layout."""
    chosen = mode.strip()
    verb = layout_verb(chosen)
    print(f"DualUp {verb} → {chosen}")
    if verb in ("full", "pbp"):
        save_dualup_mode_cache(verb)
    rc = call_lgdualup(cfg, ["pbp", chosen], missing=missing)
    if lgdualup_path(cfg) is None:
        return rc
    if rc != 0:
        return rc
    if verb == "pbp":
        rc = rc or dualup_assign_pbp_inputs(cfg)
    adapters = display_adapter_cfg(cfg)
    raw_settle = cfg.get("_dualup_layout_settle_s")
    if raw_settle is None:
        raw_settle = adapters.get("layout_settle_s", LAYOUT_DEFAULT_SETTLE_S)
    settle = float(raw_settle)
    if settle > 0:
        time.sleep(settle)
    layout_rc = apply_dualup_layout(cfg, chosen)
    apply_peer_layout(cfg, chosen)
    return rc or layout_rc


def switch_monitor(cfg: dict, host: str, *, mouse_only: bool) -> int:
    if mouse_only or not cfg.get("switch_monitor", True):
        return 0
    path = lgdualup_path(cfg)
    if path is None:
        print("lgdualup not on PATH — run `make install` (DualUp helper ships in this repo)")
        return 0
    spec = (cfg.get("hosts") or {}).get(normalize_host(host), {})
    pbp = spec.get("pbp")
    if pbp or cfg.get("switch_pbp"):
        mode = str(pbp or cfg.get("pbp_mode") or "").strip()
        if mode:
            return dualup_set_mode(
                cfg,
                mode,
                missing="lgdualup not on PATH — run `make install`",
            )
    name = str(spec.get("dualup_input") or "").strip()
    if name:
        print(f"DualUp input → {name}")
        return call_lgdualup(
            cfg,
            ["input", name],
            missing="lgdualup not on PATH — run `make install`",
        )
    print(f"no dualup_input configured for {host} — leaving DualUp input alone")
    return 0


def cmd_to(cfg: dict, host: str, *, mouse_only: bool = False) -> int:
    dest_host = normalize_host(host)
    channel = host_channel(cfg, dest_host)
    print(f"desk → {dest_host} (MX channel {channel})")
    rc = switch_mouse(cfg, channel)
    mon_rc = switch_monitor(cfg, dest_host, mouse_only=mouse_only)
    return rc or mon_rc


def cmd_switch(cfg: dict, target: str | None) -> int:
    if target is None:
        return switch_mouse(cfg, None)
    if str(target).isdigit():
        channel = int(target)
        if channel not in (1, 2, 3):
            raise SystemExit("channel must be 1, 2 or 3")
        return switch_mouse(cfg, channel)
    return cmd_to(cfg, target)


def cmd_pbp(cfg: dict, mode: str | None) -> int:
    chosen = (mode or str(cfg.get("pbp_mode") or "")).strip()
    if not chosen:
        print("usage: desk-switch pbp <mode>   (mode is whatever lgdualup pbp accepts)")
        return 2
    return dualup_set_mode(
        cfg,
        chosen,
        missing="lgdualup not on PATH — DualUp PBP needs `make install`",
    )


def cmd_full(cfg: dict) -> int:
    return dualup_set_mode(
        cfg,
        "full",
        missing="lgdualup not on PATH — DualUp full needs `make install`",
    )


def cmd_layout(cfg: dict) -> int:
    """Re-apply full or PBP from the live OS geometry (USB + layout)."""
    mode = detect_dualup_mode(cfg)
    if mode == "full":
        return cmd_full(cfg)
    return cmd_pbp(cfg, None)


DUALUP_FULL_RES = {(2880, 2560), (2560, 2880)}
DUALUP_PBP_RES = {(2880, 1280), (1280, 2880)}


def dualup_mode_cache_path() -> Path:
    return cache_dir() / "dualup-mode.json"


def load_dualup_mode_cache() -> str | None:
    try:
        raw = json.loads(dualup_mode_cache_path().read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    mode = str(raw.get("mode") or "").strip().lower()
    return mode if mode in ("full", "pbp") else None


def save_dualup_mode_cache(mode: str) -> None:
    key = layout_verb(mode)
    if key not in ("full", "pbp"):
        return
    path = dualup_mode_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mode": key, "ts": time.time()}) + "\n")
    except OSError:
        return


def parse_dualup_mode_from_res(width: int, height: int) -> str | None:
    pair = (int(width), int(height))
    if pair in DUALUP_FULL_RES:
        return "full"
    if pair in DUALUP_PBP_RES:
        return "pbp"
    return None


def detect_dualup_mode_linux(monitors: list | None = None) -> str:
    data = monitors
    if data is None:
        hypr = shutil.which("hyprctl")
        if not hypr:
            return "unknown"
        try:
            proc = run([hypr, "-j", "monitors"], timeout=5.0)
            data = json.loads(proc.stdout or "[]")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError, ValueError):
            return "unknown"
    if not isinstance(data, list):
        return "unknown"
    markers = ("sdqhd", "dualup", "28mq780", "lg electronics")
    for mon in data:
        if not isinstance(mon, dict):
            continue
        desc = f"{mon.get('description', '')} {mon.get('name', '')}".lower()
        try:
            width = int(mon.get("width") or 0)
            height = int(mon.get("height") or 0)
        except (TypeError, ValueError):
            continue
        mode = parse_dualup_mode_from_res(width, height)
        looks = any(marker in desc for marker in markers) or mode is not None
        if looks and mode:
            return mode
    return "unknown"


def detect_dualup_mode_macos(placer_list: str | None = None) -> str:
    text = placer_list
    if text is None:
        placer = shutil.which("displayplacer")
        if not placer:
            return "unknown"
        try:
            proc = run([placer, "list"], timeout=8.0)
            text = proc.stdout or ""
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
    skip = False
    for line in (text or "").splitlines():
        if line.startswith("Persistent screen id:"):
            skip = False
            continue
        if re.search(r"built[ -]?in", line, re.I):
            skip = True
            continue
        if skip:
            continue
        match = re.search(r"(?:Resolution:|res:)\s*(\d{3,5})x(\d{3,5})", line)
        if not match:
            continue
        mode = parse_dualup_mode_from_res(int(match.group(1)), int(match.group(2)))
        if mode:
            return mode
    return "unknown"


def detect_dualup_mode(cfg: dict | None = None) -> str:
    if SYSTEM == "Linux":
        mode = detect_dualup_mode_linux()
    elif SYSTEM == "Darwin":
        mode = detect_dualup_mode_macos()
    else:
        mode = "unknown"
    if mode in ("full", "pbp"):
        save_dualup_mode_cache(mode)
        return mode
    return load_dualup_mode_cache() or "unknown"


def dualup_inputs_map(cfg: dict) -> dict:
    hosts = cfg.get("hosts") or {}
    out = {}
    for host, default in PBP_INPUT_DEFAULTS.items():
        spec = _as_dict(hosts.get(host))
        out[host] = str(spec.get("dualup_input") or "").strip() or default
    return out


def peer_ssh_target(cfg: dict) -> str:
    adapters = _as_dict(cfg.get("adapters"))
    hosts_ad = _as_dict(adapters.get("hosts"))
    dual = display_adapter_cfg(cfg)
    return str(
        cfg.get("_peer")
        or hosts_ad.get("peer")
        or cfg.get("_dualup_peer")
        or dual.get("peer")
        or ""
    ).strip()


def peer_cache_path() -> Path:
    return cache_dir() / "peer-status.json"


def load_peer_cache(peer: str) -> dict | None:
    try:
        raw = json.loads(peer_cache_path().read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict) or raw.get("peer") != peer:
        return None
    try:
        ts = float(raw.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    if time.time() - ts > PEER_CACHE_TTL_S:
        return None
    state = raw.get("state")
    return state if isinstance(state, dict) else None


def save_peer_cache(peer: str, state: dict) -> None:
    path = peer_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"peer": peer, "ts": time.time(), "state": state}) + "\n")
    except OSError:
        return


def slim_peer_status(data: dict, peer: str) -> dict:
    return {
        "reachable": True,
        "peer": peer,
        "this_host": data.get("this_host"),
        "hhkb_present": data.get("hhkb_present"),
        "hhkb_usb": data.get("hhkb_usb"),
        "hhkb_bluetooth": data.get("hhkb_bluetooth"),
        "hhkb_transport": data.get("hhkb_transport"),
        "mouse_channel": data.get("mouse_channel"),
        "mouse_online": data.get("mouse_online"),
        "target_hint": data.get("target_hint"),
        "dualup_mode": data.get("dualup_mode"),
    }


def peek_peer_status(cfg: dict) -> dict | None:
    """Best-effort SSH status --json --local. Never blocks the local probe for long."""
    peer = peer_ssh_target(cfg)
    if not peer:
        return None
    cached = load_peer_cache(peer)
    if cached is not None:
        return cached
    remote = (
        'export PATH="$HOME/.local/bin:$PATH"; '
        "if command -v desk-switch >/dev/null; then desk-switch status --json --local; "
        "elif command -v hhkb-mx-follow >/dev/null; then hhkb-mx-follow status --json --local; "
        "else echo '{}'; fi"
    )
    try:
        proc = run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", peer, f"bash -lc {remote!r}"],
            timeout=6.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        missed = {"reachable": False, "peer": peer}
        save_peer_cache(peer, missed)
        return missed
    if proc.returncode != 0:
        missed = {"reachable": False, "peer": peer}
        save_peer_cache(peer, missed)
        return missed
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        missed = {"reachable": False, "peer": peer}
        save_peer_cache(peer, missed)
        return missed
    if not isinstance(data, dict) or not data:
        missed = {"reachable": False, "peer": peer}
        save_peer_cache(peer, missed)
        return missed
    slim = slim_peer_status(data, peer)
    save_peer_cache(peer, slim)
    return slim


def format_bar_strip(state: dict) -> dict:
    """Quiet strip model: focus + optional display mark. Shells only paint."""
    hint = str(state.get("target_hint") or "?")
    if hint not in HINT_FOR_HOST.values() and hint != "?":
        hint = "?"
    strip: dict[str, str] = {"focus": hint}
    mode = str(state.get("dualup_mode") or "unknown").lower()
    if mode in ("full", "pbp"):
        strip["display"] = mode
    # Lights stay out of the default strip. Opt in with ui.tray.lights.
    if state.get("_tray_lights"):
        lights = ((state.get("adapters") or {}).get("smarthome") or {}).get("lights") or []
        mark = None
        if isinstance(lights, list):
            for item in lights:
                if isinstance(item, dict) and str(item.get("state") or "") in ("on", "off"):
                    mark = str(item["state"])
                    break
        if mark:
            strip["lights"] = mark
    return strip


ADAPTER_SNAPSHOT_RESERVED = {
    "enabled",
    "available",
    "backend",
    "path",
    "source",
    "capabilities",
    "candidates",
}


def merge_adapter_snapshot(target: dict | None, snapshot: dict | None) -> dict | None:
    """Copy adapter JSON into adapters.<role>. Keep binding keys from core."""
    if not isinstance(target, dict) or not isinstance(snapshot, dict):
        return target
    for key, value in snapshot.items():
        if str(key).startswith("_") or key in ADAPTER_SNAPSHOT_RESERVED:
            continue
        target[key] = value
    return target


def normalize_slot(raw: object) -> dict | None:
    """Keep a small public slot shape. Extra keys (hot, face, progress, actions) pass through."""
    if not isinstance(raw, dict):
        return None
    slot_id = str(raw.get("id") or "").strip()
    glyph = str(raw.get("glyph") or "").strip()
    label = str(raw.get("label") or "").strip()
    if not slot_id or not (glyph or label):
        return None
    slot: dict = {"id": slot_id, "glyph": glyph or "dot", "label": label or slot_id}
    if raw.get("detail"):
        slot["detail"] = str(raw["detail"])
    if "hot" in raw:
        slot["hot"] = bool(raw["hot"])
    if "face" in raw:
        slot["face"] = bool(raw["face"])
    if raw.get("progress") is not None:
        try:
            slot["progress"] = max(0.0, min(1.0, float(raw["progress"])))
        except (TypeError, ValueError):
            pass
    actions = raw.get("actions")
    if isinstance(actions, list):
        cleaned = []
        for item in actions:
            if not isinstance(item, dict):
                continue
            argv = item.get("argv")
            if not isinstance(argv, list) or not argv:
                continue
            cleaned.append(
                {
                    "label": str(item.get("label") or argv[0]),
                    "argv": [str(part) for part in argv],
                }
            )
        if cleaned:
            slot["actions"] = cleaned
    return slot


def display_slot(state: dict) -> dict | None:
    mode = str(state.get("dualup_mode") or "unknown").lower()
    if mode == "pbp":
        glyph, label = "display.split", "PBP"
    elif mode == "full":
        glyph, label = "display.full", "FULL"
    else:
        return None
    return {
        "id": "dualup",
        "glyph": glyph,
        "label": label,
        "actions": [
            {"label": "Full", "argv": ["full"]},
            {"label": "PBP", "argv": ["pbp"]},
        ],
    }


def collect_slots(state: dict, cfg: dict | None = None) -> list[dict]:
    """Compose additive tray/HUD slots. Shells only paint. Missing adapter ⇒ omit."""
    adapters = state.get("adapters") or {}
    ordered: list[dict] = []
    for role in ("weather", "kettle"):
        snap = adapters.get(role) if isinstance(adapters.get(role), dict) else {}
        if snap.get("enabled") is False:
            continue
        slot = normalize_slot(snap.get("slot"))
        if slot:
            ordered.append(slot)
    display = normalize_slot(display_slot(state))
    if display:
        ordered.append(display)
    if (cfg or {}).get("_tray_lights") or state.get("_tray_lights"):
        home = adapters.get("smarthome") if isinstance(adapters.get("smarthome"), dict) else {}
        lights = home.get("lights") if isinstance(home.get("lights"), list) else []
        mark = None
        if lights and isinstance(lights[0], dict):
            mark = str(lights[0].get("state") or "")
        if mark in ("on", "off"):
            light = normalize_slot(
                {
                    "id": "lights",
                    "glyph": "light.on" if mark == "on" else "light.off",
                    "label": mark.upper(),
                    "actions": [
                        {"label": "On", "argv": ["smarthome", "on"]},
                        {"label": "Off", "argv": ["smarthome", "off"]},
                    ],
                }
            )
            if light:
                ordered.append(light)
    wanted = (cfg or {}).get("_tray_slots")
    if isinstance(wanted, list) and wanted:
        allow = {str(item) for item in wanted}
        ordered = [slot for slot in ordered if slot["id"] in allow]
    return ordered


def format_strip_title(strip: dict) -> str:
    """Painted default strip: MAC/LNX plus PBP/FULL when a display mode is known."""
    focus = str(strip.get("focus") or "?")
    if focus not in HINT_FOR_HOST.values():
        focus = ""
    display = str(strip.get("display") or "").lower()
    mark = "PBP" if display == "pbp" else ("FULL" if display == "full" else "")
    parts = [part for part in (focus, mark) if part]
    return "  ".join(parts) if parts else "desk"


def format_bar_label(state: dict) -> str:
    """Compact shared glyph for Omarchy + DeskSwitchBar: LNX  kbU  mx2  PBP."""
    hint = str(state.get("target_hint") or "?")
    if hint not in HINT_FOR_HOST.values() and hint != "?":
        hint = "?"
    if state.get("hhkb_usb"):
        kb = "kbU"
    elif state.get("hhkb_bluetooth"):
        kb = "kbB"
    elif state.get("hhkb_present"):
        kb = "kb"
    else:
        kb = "kb-"
    channel = state.get("mouse_channel")
    if channel is None:
        mx = "mx-"
    elif state.get("mouse_online"):
        mx = f"mx{channel}"
    else:
        mx = f"mx{channel}~"
    mode = str(state.get("dualup_mode") or "unknown").lower()
    parts: list[str] = []
    if hint in HINT_FOR_HOST.values():
        parts.append(hint)
    parts.extend([kb, mx])
    if mode == "pbp":
        parts.append("PBP")
    elif mode == "full":
        parts.append("FULL")
    return "  ".join(parts) if parts else "desk"


def format_bar_tooltip(state: dict) -> str:
    hhkb = "USB on this host" if state.get("hhkb_usb") else (
        "BT only" if state.get("hhkb_bluetooth") else (
            "present" if state.get("hhkb_present") else "absent"
        )
    )
    host = state.get("mouse_host") or "?"
    channel = state.get("mouse_channel")
    mouse = f"ch {channel} → {host}" if channel is not None else "ch ?"
    if state.get("mouse_online"):
        mouse += " online"
    elif channel is not None:
        mouse += " cached"
    else:
        mouse += " missing"
    dual = str(state.get("dualup_mode") or "unknown")
    inputs = state.get("dualup_inputs") or {}
    return (
        f"focus {state.get('target_hint', '?')} · HHKB {hhkb} · "
        f"mouse {mouse} · DualUp {dual} mac={inputs.get('mac', 'hdmi1')} linux={inputs.get('linux', 'dp')}"
    )


def collect_adapters(cfg: dict, *, mouse_channel: int | None, mouse_path: Path | None, dual_path: Path | None) -> dict:
    """User-facing adapter snapshot. Keep this shape stable; add keys, don't rename."""
    hosts = cfg.get("hosts") or {}
    layout_path = dualup_layout_path(cfg)
    mouse_bound = bind_role(cfg, "mouse")
    display_bound = bind_role(cfg, "display")
    keyboard_bound = bind_role(cfg, "keyboard")
    display = {
        "enabled": bool(cfg.get("_dualup_enabled", True)),
        "available": dual_path is not None,
        "backend": display_bound.get("id") or "lgdualup",
        "path": str(dual_path) if dual_path else None,
        "layout": bool(cfg.get("_dualup_layout", True)),
        "layout_helper": str(layout_path) if layout_path else None,
        "display_id": str(cfg.get("_dualup_display_id") or "") or None,
        "mode": None,
        "inputs": dualup_inputs_map(cfg),
        "source": display_bound.get("source"),
    }
    if display_bound.get("candidates"):
        display["candidates"] = display_bound["candidates"]
    keyboard = {
        "enabled": bool(cfg.get("_keyboard_enabled", True)),
        "available": keyboard_bound.get("path") is not None or keyboard_bound.get("source") == "incore",
        "backend": keyboard_bound.get("id") or "hhkb",
        "path": str(keyboard_bound["path"]) if keyboard_bound.get("path") else None,
        "source": keyboard_bound.get("source"),
    }
    smarthome_bound = bind_role(cfg, "smarthome")
    smarthome_path = smarthome_bound.get("path")
    smarthome = {
        "enabled": bool(cfg.get("_smarthome_enabled", True)),
        "available": smarthome_path is not None,
        "backend": smarthome_bound.get("id") or "alexa",
        "path": str(smarthome_path) if smarthome_path else None,
        "source": smarthome_bound.get("source"),
        "capabilities": list(smarthome_bound.get("capabilities") or []),
    }
    if smarthome_bound.get("candidates"):
        smarthome["candidates"] = smarthome_bound["candidates"]
    kettle_bound = bind_role(cfg, "kettle")
    kettle_path = kettle_bound.get("path")
    kettle = {
        "enabled": bool(cfg.get("_kettle_enabled", True)),
        "available": kettle_path is not None,
        "backend": kettle_bound.get("id") or "kettle",
        "path": str(kettle_path) if kettle_path else None,
        "source": kettle_bound.get("source"),
        "capabilities": list(kettle_bound.get("capabilities") or []),
    }
    if kettle_bound.get("candidates"):
        kettle["candidates"] = kettle_bound["candidates"]
    extras = cfg.get("_kettle_extras") if isinstance(cfg.get("_kettle_extras"), dict) else {}
    if extras.get("host"):
        kettle["host"] = extras["host"]
    weather_bound = bind_role(cfg, "weather")
    weather_path = weather_bound.get("path")
    weather = {
        "enabled": bool(cfg.get("_weather_enabled", True)),
        "available": weather_path is not None,
        "backend": weather_bound.get("id") or "weather",
        "path": str(weather_path) if weather_path else None,
        "source": weather_bound.get("source"),
        "capabilities": list(weather_bound.get("capabilities") or []),
    }
    if weather_bound.get("candidates"):
        weather["candidates"] = weather_bound["candidates"]
    discovered = []
    for item in discovered_adapters(cfg):
        discovered.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "capabilities": item.get("capabilities") or [],
                "path": item.get("path"),
                "source": item.get("source"),
            }
        )
    return {
        "mouse": {
            "enabled": bool(cfg.get("_mouse_enabled", True)),
            "available": mouse_path is not None,
            "backend": mouse_bound.get("id") or "mxswitch",
            "path": str(mouse_path) if mouse_path else None,
            "channel": mouse_channel,
            "source": mouse_bound.get("source"),
        },
        "keyboard": keyboard,
        "hosts": {
            "this_host": cfg["this_host"],
            "follow_channel": cfg["target_channel"],
            "mac": hosts.get("mac") or {},
            "linux": hosts.get("linux") or {},
        },
        "display": dict(display),
        "dualup": dict(display),
        "smarthome": smarthome,
        "kettle": kettle,
        "weather": weather,
        "discovered": discovered,
    }


def collect_status(cfg: dict, *, local_only: bool = False) -> dict:
    probe = hhkb_probe(cfg)
    mouse = mouse_snapshot(cfg)
    mouse_bound = bind_role(cfg, "mouse")
    mouse_path = Path(mouse_bound["path"]) if mouse_bound.get("path") else None
    dual_path = lgdualup_path(cfg)
    dual_info = ""
    dual_usb = False
    if dual_path is not None:
        try:
            proc = run([str(dual_path), "--info"], timeout=8.0)
            dual_info = (proc.stdout or proc.stderr or "").rstrip()
            dual_usb = proc.returncode == 0 and "043e:9a39" in dual_info.lower()
        except (OSError, subprocess.TimeoutExpired) as exc:
            dual_info = str(exc)
    dual_mode = detect_dualup_mode(cfg)
    inputs = dualup_inputs_map(cfg)
    peer = None if local_only else peek_peer_status(cfg)
    peer_channel = None
    peer_online = False
    if isinstance(peer, dict) and peer.get("reachable"):
        try:
            if peer.get("mouse_channel") is not None:
                peer_channel = int(peer["mouse_channel"])
            peer_online = bool(peer.get("mouse_online"))
        except (TypeError, ValueError):
            peer_channel = None

    if mouse.get("channel_live") is not None:
        channel = mouse["channel_live"]
        mouse_source = "mouse"
    elif peer_channel is not None and peer_online:
        channel = peer_channel
        mouse_source = "peer_mouse"
        save_mouse_cache(channel)
    elif mouse.get("channel") is not None:
        channel = mouse["channel"]
        mouse_source = mouse.get("source") or "mouse_cached"
    elif peer_channel is not None:
        channel = peer_channel
        mouse_source = "peer_mouse"
        save_mouse_cache(channel)
    else:
        channel = None
        mouse_source = None

    hhkb_for_hint = bool(probe.get("present")) and not probe.get("unknown")
    hint, hint_source = resolve_target_hint(
        cfg,
        mouse_channel=channel,
        hhkb_present=hhkb_for_hint,
        peer_channel=None if mouse_source == "peer_mouse" else peer_channel,
        mouse_source=mouse_source,
    )
    adapters = collect_adapters(cfg, mouse_channel=channel, mouse_path=mouse_path, dual_path=dual_path)
    adapters["mouse"].update(
        {
            "online": bool(mouse.get("online")) if mouse_source == "mouse" else False,
            "stale": mouse_source == "mouse_cached",
            "host": host_for_channel(cfg, channel),
            "source": mouse_source,
        }
    )
    adapters["hhkb"] = {
        "present": bool(probe.get("present")) and not probe.get("unknown"),
        "usb": bool(probe.get("usb")),
        "bluetooth": bool(probe.get("bluetooth")),
        "transport": probe.get("transport") or "absent",
        "backend": (adapters.get("keyboard") or {}).get("backend") or "hhkb",
    }
    if isinstance(adapters.get("keyboard"), dict):
        adapters["keyboard"].update(adapters["hhkb"])
    adapters["dualup"]["mode"] = dual_mode
    adapters["dualup"]["usb"] = dual_usb
    adapters["dualup"]["inputs"] = inputs
    if isinstance(adapters.get("display"), dict):
        adapters["display"]["mode"] = dual_mode
        adapters["display"]["usb"] = dual_usb
        adapters["display"]["inputs"] = inputs
    snapshot = invoke_smarthome(cfg, "info", timeout=4.0)
    merge_adapter_snapshot(adapters.get("smarthome"), snapshot)
    kettle_snap = invoke_kettle(cfg, "info", "--no-default-host", timeout=3.0)
    merge_adapter_snapshot(adapters.get("kettle"), kettle_snap)
    weather_snap = invoke_weather(cfg, "info", timeout=3.0)
    merge_adapter_snapshot(adapters.get("weather"), weather_snap)
    present = bool(adapters["hhkb"]["present"])
    state = {
        "os": SYSTEM,
        "version": VERSION,
        "config": cfg["_config_path"],
        "this_host": cfg["this_host"],
        "hhkb": "present" if present else "absent",
        "hhkb_present": present,
        "hhkb_usb": bool(probe.get("usb")),
        "hhkb_bluetooth": bool(probe.get("bluetooth")),
        "hhkb_transport": probe.get("transport") or "absent",
        "follow_hhkb_usb": bool(cfg.get("follow_hhkb_usb", True)),
        "target_channel": cfg["target_channel"],
        "hosts": cfg["hosts"],
        "target_hint": hint,
        "target_hint_source": hint_source,
        "mxswitch": str(mouse_path or cfg.get("mxswitch") or "mxswitch"),
        "mouse_channel": channel,
        "mouse_channel_live": mouse.get("channel_live"),
        "mouse_online": mouse_source == "mouse",
        "mouse_host": host_for_channel(cfg, channel),
        "mouse_info": mouse.get("info") or "",
        "lgdualup": bool(adapters["dualup"]["available"]),
        "lgdualup_path": str(dual_path) if dual_path else None,
        "dualup_info": dual_info,
        "dualup_mode": dual_mode,
        "dualup_usb": dual_usb,
        "dualup_inputs": inputs,
        "peer": peer,
        "adapters": adapters,
        "ui": {"tray": {"density": tray_density(cfg)}},
    }
    state["_tray_lights"] = bool(cfg.get("_tray_lights"))
    state["bar_label"] = format_bar_label(state)
    state["bar_tooltip"] = format_bar_tooltip(state)
    state["bar_strip"] = format_bar_strip(state)
    state["slots"] = collect_slots(state, cfg)
    state.pop("_tray_lights", None)
    return state


def cmd_status(cfg: dict, *, as_json: bool = False, hint_only: bool = False, local_only: bool = False) -> int:
    state = collect_status(cfg, local_only=local_only or hint_only)
    if hint_only:
        print(state["target_hint"])
        return 0
    if as_json:
        print(json.dumps(state, indent=2))
        return 0
    adapters = state.get("adapters") or {}
    mouse = adapters.get("mouse") or {}
    hosts = adapters.get("hosts") or {}
    dual = adapters.get("dualup") or {}
    print(f"os            : {state['os']}")
    print(f"version       : {state['version']}")
    print(f"config        : {state['config']}")
    print(f"this_host     : {state['this_host']}")
    transport = state.get("hhkb_transport") or "absent"
    usb_note = "USB on this host" if state.get("hhkb_usb") else (
        "BT only" if state.get("hhkb_bluetooth") else transport
    )
    print(f"hhkb          : {state['hhkb']}  transport={transport}  ({usb_note})")
    print(f"target        : channel {state['target_channel']}")
    print(f"target_hint   : {state['target_hint']}  source={state.get('target_hint_source', '?')}")
    print(f"bar           : {state.get('bar_label', state['target_hint'])}")
    print("adapters")
    mouse_state = "available" if mouse.get("available") else "missing"
    if not mouse.get("enabled", True):
        mouse_state = "disabled"
    print(f"  mouse       : {mouse_state}  backend={mouse.get('backend', 'mxswitch')}  {mouse.get('path') or '-'}")
    print(
        f"  hosts       : this_host={hosts.get('this_host', state['this_host'])}  "
        f"follow_channel={hosts.get('follow_channel', state['target_channel'])}  "
        f"mac={_as_dict(hosts.get('mac')).get('channel', '?')}  "
        f"linux={_as_dict(hosts.get('linux')).get('channel', '?')}"
    )
    dual_state = "available" if dual.get("available") else "missing"
    if not dual.get("enabled", True):
        dual_state = "disabled"
    print(
        f"  dualup      : {dual_state}  backend={dual.get('backend', 'lgdualup')}  "
        f"mode={state.get('dualup_mode', 'unknown')}  "
        f"mac={state.get('dualup_inputs', {}).get('mac', 'hdmi1')}  "
        f"linux={state.get('dualup_inputs', {}).get('linux', 'dp')}  "
        f"{dual.get('path') or '-'}"
    )
    home = adapters.get("smarthome") or {}
    home_state = "available" if home.get("available") else "missing"
    if not home.get("enabled", True):
        home_state = "disabled"
    lights = home.get("lights") if isinstance(home.get("lights"), list) else []
    light_mark = "unknown"
    if lights and isinstance(lights[0], dict) and lights[0].get("state"):
        light_mark = str(lights[0]["state"])
    print(
        f"  smarthome   : {home_state}  backend={home.get('backend', 'alexa')}  "
        f"lights={light_mark}  {home.get('path') or '-'}"
    )
    kettle = adapters.get("kettle") or {}
    kettle_state = "available" if kettle.get("available") else "missing"
    if not kettle.get("enabled", True):
        kettle_state = "disabled"
    kettle_mark = "offline"
    if kettle.get("reachable"):
        kettle_mark = f"{kettle.get('temp_c', '?')}° {kettle.get('mode') or ''}".strip()
    print(
        f"  kettle      : {kettle_state}  backend={kettle.get('backend', 'kettle')}  "
        f"{kettle_mark}  host={kettle.get('host') or '-'}  {kettle.get('path') or '-'}"
    )
    weather = adapters.get("weather") or {}
    weather_state = "available" if weather.get("available") else "missing"
    if not weather.get("enabled", True):
        weather_state = "disabled"
    weather_mark = "offline"
    if weather.get("reachable") and weather.get("temp_c") is not None:
        alt = f" {weather.get('altitude_m')}m" if weather.get("altitude_m") is not None else ""
        weather_mark = f"{weather.get('temp_c')}°{alt}"
    print(
        f"  weather     : {weather_state}  backend={weather.get('backend', 'weather')}  "
        f"{weather_mark}  {weather.get('path') or '-'}"
    )
    slots = state.get("slots") or []
    if slots:
        painted = "  ".join(
            f"{item.get('glyph', '')}:{item.get('label', '')}" for item in slots if isinstance(item, dict)
        )
        print(f"slots         : {painted}")
    mouse_line = "missing"
    if state.get("mouse_channel") is not None:
        mouse_line = (
            f"channel {state['mouse_channel']} → {state.get('mouse_host') or '?'}  "
            f"{'online' if state.get('mouse_online') else 'cached'}"
        )
    print(f"mouse         : {mouse_line}")
    for line in (state["mouse_info"] or "(no output)").splitlines():
        print(f"  {line}")
    if state["lgdualup"] or state.get("dualup_info"):
        print("dualup        :")
        for line in (state["dualup_info"] or "(no output)").splitlines():
            print(f"  {line}")
    peer = state.get("peer")
    if isinstance(peer, dict):
        if peer.get("reachable"):
            print(
                f"peer          : {peer.get('this_host') or peer.get('peer')}  "
                f"hhkb={peer.get('hhkb_transport', '?')}  "
                f"mouse={peer.get('mouse_channel', '?')}  "
                f"hint={peer.get('target_hint', '?')}"
            )
        else:
            print(f"peer          : {peer.get('peer')} unreachable")
    return 0


def watch_usb_rising_edge(last_usb: bool | None, usb_now: bool, follow_usb: bool) -> tuple[bool, bool]:
    """Detect Fn+Ctrl+0: USB appearance. Startup snapshot does not fire."""
    if last_usb is None:
        return bool(usb_now), False
    fire = bool(follow_usb) and bool(usb_now) and not bool(last_usb)
    return bool(usb_now), fire


def cmd_watch(cfg: dict, dry_run: bool) -> int:
    interval = float(cfg["poll_interval_s"])
    need = int(cfg["absent_polls_required"])
    sleep_gap = float(cfg["sleep_gap_s"])
    retries = int(cfg["switch_retries"])
    retry_delay = float(cfg["switch_retry_delay_s"])
    dest = int(cfg["target_channel"])
    follow_usb = bool(cfg.get("follow_hhkb_usb", True))

    log(
        f"watching HHKB → MX channel {dest} "
        f"(dry_run={dry_run}, every {interval}s, absent×{need}, "
        f"follow_hhkb_usb={follow_usb})"
    )
    armed = False
    absent = 0
    last = time.time()
    last_usb: bool | None = None

    while True:
        now = time.time()
        if now - last > max(sleep_gap, interval * 4):
            log("clock gap (sleep/wake) — disarm until HHKB is seen again")
            armed = False
            absent = 0
            last_usb = None
        last = now

        try:
            probe = hhkb_probe(cfg)
        except Exception as exc:  # noqa: BLE001 — never treat a probe crash as gone
            log(f"presence probe error ({exc}); treating as present")
            probe = empty_hhkb_probe(unknown=True)

        present = bool(probe.get("present"))
        usb_now = bool(probe.get("usb"))

        # Fn+Ctrl+0: USB rising edge pulls the whole desk to the machine with the cable.
        last_usb, follow_here = watch_usb_rising_edge(last_usb, usb_now, follow_usb)
        if follow_here:
            here = cfg["this_host"]
            if dry_run:
                log(f"dry-run: HHKB USB appeared — would desk-switch to {here}")
            else:
                log(f"HHKB USB appeared — desk → {here}")
                rc = cmd_to(cfg, here)
                if rc != 0:
                    log(f"USB follow to {here} failed (rc={rc})")
            armed = True
            absent = 0

        # USB cable is never an "absent" hop-away. BT leave still is.
        if usb_now:
            if not armed:
                log("HHKB USB present — armed")
            armed = True
            absent = 0
        elif present:
            if not armed:
                log("HHKB present — armed")
            armed = True
            absent = 0
        elif armed:
            absent += 1
            if absent == 1 or absent == need:
                log(f"HHKB absent ({absent}/{need})")
            if absent >= need:
                if dry_run:
                    log(f"dry-run: would switch mouse to channel {dest}")
                else:
                    log(f"HHKB left — switching mouse to channel {dest}")
                    rc = 1
                    for attempt in range(1, retries + 1):
                        rc = switch_mouse(cfg, dest)
                        if rc == 0:
                            log(f"switch sent (attempt {attempt})")
                            break
                        log(f"switch attempt {attempt} failed (rc={rc})")
                        time.sleep(retry_delay)
                    if rc != 0:
                        log("giving up this departure; will re-arm when HHKB returns")
                armed = False
                absent = 0
        time.sleep(interval)


def format_smarthome_human(verb: str, data: dict) -> str:
    if verb in ("on", "off"):
        device = data.get("device") or "?"
        phrase = data.get("phrase") or verb
        if data.get("ok"):
            return f"light {verb} → {device}: {phrase}"
        return f"light {verb} failed: {data.get('error') or 'adapter error'}"
    devices = data.get("devices") if isinstance(data.get("devices"), list) else []
    lights = data.get("lights") if isinstance(data.get("lights"), list) else []
    lines = ["devices:"]
    if devices:
        for item in devices:
            if isinstance(item, dict):
                lines.append(f"  {item.get('name') or item.get('id') or '?'}")
            else:
                lines.append(f"  {item}")
    else:
        lines.append("  (none — authenticate the smarthome adapter CLI, then list again)")
    lines.append("lights:")
    if lights:
        for item in lights:
            if not isinstance(item, dict):
                lines.append(f"  {item}")
                continue
            lines.append(
                f"  {item.get('name') or item.get('id') or 'desk'}  "
                f"{item.get('state') or 'unknown'}  via {item.get('speaker') or '?'}"
            )
    else:
        lines.append("  (none)")
    if data.get("error"):
        lines.append(f"note: {data['error']}")
    source = data.get("list_source")
    if source:
        lines.append(f"list_source: {source}")
    return "\n".join(lines)


def cmd_smarthome(cfg: dict, verb: str | None, *, as_json: bool = False) -> int:
    action = str(verb or "status").strip().lower()
    if action in ("info", "probe"):
        action = "status"
    if action not in ("list", "status", "on", "off"):
        print(f"unknown smarthome verb: {verb} (try list, status, on, off)", file=sys.stderr)
        return 2
    if smarthome_adapter_path(cfg) is None:
        print("smarthome adapter not found (install adapters/alexa or pin adapters.smarthome)")
        return 0
    extra = ("--refresh",) if action == "status" else ()
    data = invoke_smarthome(cfg, action, *extra, timeout=25.0)
    if data is None:
        print("smarthome adapter not found (install adapters/alexa or pin adapters.smarthome)")
        return 0
    if as_json:
        printable = {k: v for k, v in data.items() if not str(k).startswith("_")}
        print(json.dumps(printable, indent=2))
    else:
        print(format_smarthome_human(action, data))
    rc = data.get("_returncode")
    try:
        return int(rc) if rc is not None else (0 if data.get("ok", True) or data.get("devices") else 1)
    except (TypeError, ValueError):
        return 1


def cmd_kettle(cfg: dict, verb: str | None, temp: str | None = None, *, as_json: bool = False) -> int:
    action = str(verb or "status").strip().lower()
    if action in ("info", "probe"):
        action = "status"
    if action not in ("status", "heat", "on", "off", "host"):
        print(f"unknown kettle verb: {verb} (try status, heat, on, off, host)", file=sys.stderr)
        return 2
    if kettle_adapter_path(cfg) is None:
        print("kettle adapter not found (install adapters/kettle or pin adapters.kettle)")
        return 0
    extra: list[str] = []
    if action == "status":
        data = invoke_kettle(cfg, "status", *extra, timeout=8.0)
    elif action == "heat":
        args = ["heat"]
        if temp:
            args.append(str(temp))
        data = invoke_kettle(cfg, *args, timeout=12.0)
    elif action == "host":
        args = ["host"]
        if temp:
            args.append(str(temp))
        data = invoke_kettle(cfg, *args, timeout=4.0)
    else:
        data = invoke_kettle(cfg, action, timeout=12.0)
    if data is None:
        print("kettle adapter not found (install adapters/kettle or pin adapters.kettle)")
        return 0
    printable = {k: v for k, v in data.items() if not str(k).startswith("_")}
    if as_json or action in ("status", "host"):
        print(json.dumps(printable, indent=2))
    else:
        if data.get("ok"):
            print(f"kettle {action} ok" + (f" {temp}" if temp else ""))
        else:
            print(data.get("error") or f"kettle {action} failed")
    rc = data.get("_returncode")
    try:
        return int(rc) if rc is not None else (0 if data.get("ok", True) or data.get("reachable") else 1)
    except (TypeError, ValueError):
        return 1


def cmd_weather(cfg: dict, verb: str | None, *, as_json: bool = False) -> int:
    action = str(verb or "status").strip().lower()
    if action in ("info", "probe"):
        action = "status"
    if action != "status":
        print(f"unknown weather verb: {verb} (try status)", file=sys.stderr)
        return 2
    if weather_adapter_path(cfg) is None:
        print("weather adapter not found (install adapters/weather or pin adapters.weather)")
        return 0
    data = invoke_weather(cfg, "info", timeout=6.0)
    if data is None:
        print("weather adapter not found (install adapters/weather or pin adapters.weather)")
        return 0
    printable = {k: v for k, v in data.items() if not str(k).startswith("_")}
    print(json.dumps(printable, indent=2))
    rc = data.get("_returncode")
    try:
        return int(rc) if rc is not None else (0 if data.get("reachable") else 1)
    except (TypeError, ValueError):
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"desk-switch {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status", help="show HHKB / mouse / DualUp state")
    status.add_argument("--json", action="store_true", help="machine-readable status")
    status.add_argument("--hint", action="store_true", help="print MAC / LNX / ? only")
    status.add_argument("--local", action="store_true", help="skip SSH peer peek")
    sub.add_parser("hint", help="print MAC / LNX / ? (bar widget)")
    watch = sub.add_parser("watch", help="follow HHKB leave (mouse away) and USB appear (desk here)")
    watch.add_argument("--dry-run", action="store_true")
    sw = sub.add_parser("switch", help="push the mouse, or switch a whole desk")
    sw.add_argument("target", nargs="?", help="Easy-Switch 1|2|3, or host mac|linux")
    to = sub.add_parser("to", help="switch mouse + optional DualUp input to a host")
    to.add_argument("host", help="mac or linux")
    to.add_argument("--mouse-only", action="store_true", help="do not call lgdualup")
    pbp = sub.add_parser("pbp", help="DualUp PBP: USB toggle, input pair, tilted OS layout")
    pbp.add_argument("mode", nargs="?", help="mode string passed to `lgdualup pbp` (default: pbp_mode)")
    sub.add_parser("full", help="DualUp full: USB toggle + 2880x2560 @ 270°")
    sub.add_parser("layout", help="re-apply DualUp full or PBP from the live display")
    smarthome = sub.add_parser("smarthome", help="list devices / light status / on / off (bound adapter)")
    smarthome.add_argument(
        "verb",
        nargs="?",
        default="status",
        help="list | status | on | off",
    )
    smarthome.add_argument("--json", action="store_true", help="print adapter JSON")
    kettle = sub.add_parser("kettle", help="Fellow Stagg status / heat / off (bound adapter)")
    kettle.add_argument(
        "verb",
        nargs="?",
        default="status",
        help="status | heat | on | off | host",
    )
    kettle.add_argument("temp", nargs="?", help="celsius for heat, or host IP")
    kettle.add_argument("--json", action="store_true", help="print adapter JSON")
    weather = sub.add_parser("weather", help="Open-Meteo ambient status (bound adapter)")
    weather.add_argument(
        "verb",
        nargs="?",
        default="status",
        help="status | info",
    )
    weather.add_argument("--json", action="store_true", help="print adapter JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.cmd == "status":
        return cmd_status(cfg, as_json=args.json, hint_only=args.hint, local_only=args.local)
    if args.cmd == "hint":
        return cmd_status(cfg, hint_only=True)
    if args.cmd == "watch":
        return cmd_watch(cfg, args.dry_run)
    if args.cmd == "switch":
        return cmd_switch(cfg, args.target)
    if args.cmd == "to":
        return cmd_to(cfg, args.host, mouse_only=args.mouse_only)
    if args.cmd == "pbp":
        return cmd_pbp(cfg, args.mode)
    if args.cmd == "full":
        return cmd_full(cfg)
    if args.cmd == "layout":
        return cmd_layout(cfg)
    if args.cmd == "smarthome":
        return cmd_smarthome(cfg, args.verb, as_json=args.json)
    if args.cmd == "kettle":
        return cmd_kettle(cfg, args.verb, args.temp, as_json=args.json)
    if args.cmd == "weather":
        return cmd_weather(cfg, args.verb, as_json=args.json)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
