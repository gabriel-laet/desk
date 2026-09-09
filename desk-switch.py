#!/usr/bin/env python3
"""Desk switch: one CLI for hopping a desk between machines.

Core command is `desk-switch`. Pieces of the desk plug in as adapters:

    mouse   — HID++ Easy-Switch hop (backend: mxswitch)
    hosts   — which machine is Mac vs Linux, channels, HHKB follow target
    dualup  — LG DualUp input + PBP/full USB + OS layout
              (backends: lgdualup, dualup-layout)

Helpers live under `~/.local/lib/desk-switch/` after `make install`.
`mxswitch` / `lgdualup` on PATH are compatibility shims, not the product.

    desk-switch status
    desk-switch status --json
    desk-switch to mac
    desk-switch to linux
    desk-switch switch 2          # mouse only, Easy-Switch channel
    desk-switch switch mac        # same as: to mac
    desk-switch pbp <mode>
    desk-switch full
    desk-switch watch
    desk-switch watch --dry-run

`hhkb-mx-follow` is the legacy command name for the same program.
"""

from __future__ import annotations

import argparse
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
VERSION = "1.3.0"
LAYOUT_FULL_MODES = ("full", "off", "none", "solo")
PBP_INPUT_DEFAULTS = {"linux": "dp", "mac": "hdmi1"}
PBP_INPUT_ORDER = ("linux", "mac")  # secondary first, primary last
HHKB_VID_DEFAULT = 0x04FE
HHKB_PID_DEFAULT = 0x0016
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


def apply_adapter_config(cfg: dict, user: dict) -> dict:
    """Map adapters.* onto the internal config. Legacy keys still win if set.

    More adapters can land here later without renaming the model.
    """
    adapters = _as_dict(user.get("adapters"))
    mouse = _as_dict(adapters.get("mouse"))
    hosts_ad = _as_dict(adapters.get("hosts"))
    dual = _as_dict(adapters.get("dualup"))

    cfg["_mouse_enabled"] = bool(mouse.get("enabled", True))
    if mouse.get("path"):
        cfg["mxswitch"] = str(mouse["path"])

    if hosts_ad.get("this_host"):
        cfg["this_host"] = hosts_ad["this_host"]
    if hosts_ad.get("follow_channel") is not None:
        cfg["target_channel"] = hosts_ad["follow_channel"]

    cfg["_dualup_enabled"] = bool(dual.get("enabled", True))
    cfg["_dualup_layout"] = bool(dual.get("layout", True))
    if dual.get("enabled") is False:
        cfg["switch_monitor"] = False
    if dual.get("path"):
        cfg["lgdualup"] = str(dual["path"])
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
    cfg["_dualup_layout_retries"] = as_int(dual.get("layout_retries"), 8)
    cfg["_dualup_layout_retry_delay_s"] = float(dual.get("layout_retry_delay_s") or 0.5)
    return cfg


def adapter_host_overrides(user: dict) -> dict:
    """Host map entries from adapters.hosts and adapters.dualup.inputs."""
    adapters = _as_dict(user.get("adapters"))
    hosts_ad = _as_dict(adapters.get("hosts"))
    dual = _as_dict(adapters.get("dualup"))
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


def target_hint(cfg: dict, mouse_channel: int | None = None, hhkb: bool | None = None) -> str:
    host = host_for_channel(cfg, mouse_channel)
    if host:
        return HINT_FOR_HOST.get(host, "?")
    if hhkb:
        return HINT_FOR_HOST.get(cfg.get("this_host"), "?")
    return "?"


def run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def hhkb_present_macos(vid: int, pid: int) -> bool:
    """True when this Mac has an HHKB keyboard collection (usage 0x06)."""
    try:
        proc = run(
            [
                "hidutil",
                "list",
                "--matching",
                json.dumps({"VendorID": vid, "ProductID": pid}),
            ],
            timeout=4.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True  # unknown: do not treat as gone
    if proc.returncode != 0:
        return True
    in_services = False
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if line.startswith("Services:"):
            in_services = True
            continue
        if line.startswith("Devices:"):
            in_services = False
            continue
        if not in_services or not line.startswith("0x"):
            continue
        cols = line.split()
        if len(cols) < 5:
            continue
        try:
            usage_page = int(cols[3], 0)
            usage = int(cols[4], 0)
        except ValueError:
            continue
        if usage_page == 1 and usage == 6:
            return True
    return False


def hhkb_present_linux(vid: int, pid: int) -> bool:
    hid = Path("/sys/bus/hid/devices")
    needle = f"{vid:04X}:{pid:04X}"
    if not hid.is_dir():
        return True  # unknown
    try:
        for entry in hid.iterdir():
            if needle in entry.name.upper():
                return True
    except OSError:
        return True
    return False


def hhkb_present(cfg: dict) -> bool:
    vid = cfg["hhkb_vendor_id"]
    pid = cfg["hhkb_product_id"]
    if SYSTEM == "Darwin":
        return hhkb_present_macos(vid, pid)
    if SYSTEM == "Linux":
        return hhkb_present_linux(vid, pid)
    raise SystemExit(f"unsupported OS: {SYSTEM}")


def libexec_dir() -> Path:
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


def which_adapter(backend: str, configured: str | None = None) -> Path | None:
    """Resolve an adapter helper: explicit path, then libexec, then PATH."""
    if configured:
        expanded = Path(str(configured)).expanduser()
        if expanded.is_file() and os.access(expanded, os.X_OK):
            return expanded
    private = libexec_dir() / backend
    if private.is_file() and os.access(private, os.X_OK):
        return private
    if configured:
        found = which_cmd(str(configured))
        if found:
            return found
        if Path(str(configured)).name != backend:
            return None
    return which_cmd(backend)


def mxswitch_path(cfg: dict) -> Path:
    path = which_adapter("mxswitch", str(cfg.get("mxswitch") or "mxswitch"))
    if path is None:
        raise SystemExit(f"mouse adapter (mxswitch) not found: {cfg.get('mxswitch')}")
    return path


def lgdualup_path(cfg: dict) -> Path | None:
    if not cfg.get("_dualup_enabled", True):
        return None
    return which_adapter("lgdualup", str(cfg.get("lgdualup") or "lgdualup"))


def switch_mouse(cfg: dict, channel: int | None = None) -> int:
    if not cfg.get("_mouse_enabled", True):
        print("mouse adapter disabled")
        return 0
    dest = int(cfg["target_channel"] if channel is None else channel)
    cmd = [str(mxswitch_path(cfg)), str(dest)]
    try:
        proc = run(cmd, timeout=8.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"mxswitch failed to start: {exc}")
        return 1
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        log(out)
    if err:
        log(err)
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


def call_lgdualup(cfg: dict, args: list[str], *, missing: str) -> int:
    path = lgdualup_path(cfg)
    if path is None:
        print(missing)
        return 0
    try:
        proc = run([str(path), *args], timeout=8.0)
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
    adapters = _as_dict(_as_dict(cfg.get("adapters")).get("dualup"))
    if cfg.get("_dualup_layout", adapters.get("layout", True)) is False:
        return None
    configured = cfg.get("_dualup_layout_helper") or adapters.get("layout_helper")
    path = which_adapter("dualup-layout", str(configured) if configured else None)
    if path:
        return path
    sub = "macos" if SYSTEM == "Darwin" else "linux"
    bundled = HERE / sub / "dualup-layout"
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
    adapters = _as_dict(_as_dict(cfg.get("adapters")).get("dualup"))
    display_id = str(
        cfg.get("_dualup_display_id") or adapters.get("display_id") or ""
    ).strip()
    retries = as_int(cfg.get("_dualup_layout_retries", adapters.get("layout_retries")), 8)
    raw_delay = cfg.get("_dualup_layout_retry_delay_s")
    if raw_delay is None:
        raw_delay = adapters.get("layout_retry_delay_s", 0.5)
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
    adapters = _as_dict(_as_dict(cfg.get("adapters")).get("dualup"))
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
    """After PBP enable, DualUp defaults to HDMI1+HDMI2 — reassign the desk pair."""
    rc = 0
    for host, name in dualup_pbp_inputs(cfg):
        print(f"DualUp input → {name} ({host})")
        inp_rc = call_lgdualup(
            cfg,
            ["input", name],
            missing="lgdualup not on PATH — run `make install`",
        )
        rc = rc or inp_rc
    return rc


def dualup_set_mode(cfg: dict, mode: str, *, missing: str) -> int:
    """USB PBP/full, then (for PBP) input pair, then tilted OS layout."""
    chosen = mode.strip()
    verb = layout_verb(chosen)
    print(f"DualUp {verb} → {chosen}")
    rc = call_lgdualup(cfg, ["pbp", chosen], missing=missing)
    if lgdualup_path(cfg) is None:
        return rc
    if rc != 0:
        return rc
    if verb == "pbp":
        rc = rc or dualup_assign_pbp_inputs(cfg)
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


def collect_adapters(cfg: dict, *, mouse_channel: int | None, mouse_path: Path | None, dual_path: Path | None) -> dict:
    """User-facing adapter snapshot. Keep this shape stable; add keys, don't rename."""
    hosts = cfg.get("hosts") or {}
    layout_path = dualup_layout_path(cfg)
    return {
        "mouse": {
            "enabled": bool(cfg.get("_mouse_enabled", True)),
            "available": mouse_path is not None,
            "backend": "mxswitch",
            "path": str(mouse_path) if mouse_path else None,
            "channel": mouse_channel,
        },
        "hosts": {
            "this_host": cfg["this_host"],
            "follow_channel": cfg["target_channel"],
            "mac": hosts.get("mac") or {},
            "linux": hosts.get("linux") or {},
        },
        "dualup": {
            "enabled": bool(cfg.get("_dualup_enabled", True)),
            "available": dual_path is not None,
            "backend": "lgdualup",
            "path": str(dual_path) if dual_path else None,
            "layout": bool(cfg.get("_dualup_layout", True)),
            "layout_helper": str(layout_path) if layout_path else None,
            "display_id": str(cfg.get("_dualup_display_id") or "") or None,
        },
    }


def collect_status(cfg: dict) -> dict:
    present = hhkb_present(cfg)
    info, channel = mouse_info(cfg)
    mouse_path = which_adapter("mxswitch", str(cfg.get("mxswitch") or "mxswitch"))
    dual_path = lgdualup_path(cfg)
    dual_info = ""
    if dual_path is not None:
        try:
            proc = run([str(dual_path), "--info"], timeout=8.0)
            dual_info = (proc.stdout or proc.stderr or "").rstrip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            dual_info = str(exc)
    adapters = collect_adapters(cfg, mouse_channel=channel, mouse_path=mouse_path, dual_path=dual_path)
    return {
        "os": SYSTEM,
        "version": VERSION,
        "config": cfg["_config_path"],
        "this_host": cfg["this_host"],
        "hhkb": "present" if present else "absent",
        "hhkb_present": present,
        "target_channel": cfg["target_channel"],
        "hosts": cfg["hosts"],
        "target_hint": target_hint(cfg, channel, present),
        "mxswitch": str(mouse_path or cfg.get("mxswitch") or "mxswitch"),
        "mouse_channel": channel,
        "mouse_info": info,
        "lgdualup": bool(adapters["dualup"]["available"]),
        "lgdualup_path": str(dual_path) if dual_path else None,
        "dualup_info": dual_info,
        "adapters": adapters,
    }


def cmd_status(cfg: dict, *, as_json: bool = False, hint_only: bool = False) -> int:
    state = collect_status(cfg)
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
    print(f"hhkb          : {state['hhkb']}")
    print(f"target        : channel {state['target_channel']}")
    print(f"target_hint   : {state['target_hint']}")
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
    print(f"  dualup      : {dual_state}  backend={dual.get('backend', 'lgdualup')}  {dual.get('path') or '-'}")
    print("mouse         :")
    for line in (state["mouse_info"] or "(no output)").splitlines():
        print(f"  {line}")
    if state["lgdualup"]:
        print("dualup        :")
        for line in (state["dualup_info"] or "(no output)").splitlines():
            print(f"  {line}")
    return 0


def cmd_watch(cfg: dict, dry_run: bool) -> int:
    interval = float(cfg["poll_interval_s"])
    need = int(cfg["absent_polls_required"])
    sleep_gap = float(cfg["sleep_gap_s"])
    retries = int(cfg["switch_retries"])
    retry_delay = float(cfg["switch_retry_delay_s"])
    dest = int(cfg["target_channel"])

    log(
        f"watching HHKB → MX channel {dest} "
        f"(dry_run={dry_run}, every {interval}s, absent×{need})"
    )
    armed = False
    absent = 0
    last = time.time()

    while True:
        now = time.time()
        if now - last > max(sleep_gap, interval * 4):
            log("clock gap (sleep/wake) — disarm until HHKB is seen again")
            armed = False
            absent = 0
        last = now

        try:
            present = hhkb_present(cfg)
        except Exception as exc:  # noqa: BLE001 — never treat a probe crash as gone
            log(f"presence probe error ({exc}); treating as present")
            present = True

        if present:
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
    sub.add_parser("hint", help="print MAC / LNX / ? (bar widget)")
    watch = sub.add_parser("watch", help="follow HHKB departures (mouse only)")
    watch.add_argument("--dry-run", action="store_true")
    sw = sub.add_parser("switch", help="push the mouse, or switch a whole desk")
    sw.add_argument("target", nargs="?", help="Easy-Switch 1|2|3, or host mac|linux")
    to = sub.add_parser("to", help="switch mouse + optional DualUp input to a host")
    to.add_argument("host", help="mac or linux")
    to.add_argument("--mouse-only", action="store_true", help="do not call lgdualup")
    pbp = sub.add_parser("pbp", help="DualUp PBP: USB toggle, input pair, tilted OS layout")
    pbp.add_argument("mode", nargs="?", help="mode string passed to `lgdualup pbp` (default: pbp_mode)")
    sub.add_parser("full", help="DualUp full: USB toggle + 2880x2560 @ 270°")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.cmd == "status":
        return cmd_status(cfg, as_json=args.json, hint_only=args.hint)
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
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
