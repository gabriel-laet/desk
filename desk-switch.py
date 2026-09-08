#!/usr/bin/env python3
"""Desk switch: send the MX Master (and optionally an LG DualUp) to another host.

Mouse hop still follows the HHKB Studio leaving this machine. DualUp input/PBP
shells out to `lgdualup` (built and installed by `make install` from macos/lgdualup.c / linux/lgdualup.sh). Older installs may still
ship that binary or invent DDC codes.

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
VERSION = "1.1.0"
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
        "mxswitch": str(Path.home() / ".local" / "bin" / "mxswitch"),
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
            cfg.update(user)
            cfg["_config_path"] = str(path)
            break
    else:
        cfg["_config_path"] = "(defaults)"

    cfg["hhkb_vendor_id"] = as_int(cfg["hhkb_vendor_id"], HHKB_VID_DEFAULT)
    cfg["hhkb_product_id"] = as_int(cfg["hhkb_product_id"], HHKB_PID_DEFAULT)
    cfg["this_host"] = normalize_host(cfg.get("this_host") or default_this_host())
    if "target_channel" not in user:
        cfg["target_channel"] = 1 if cfg["this_host"] == "linux" else 2
    cfg["target_channel"] = as_int(cfg["target_channel"], 1 if cfg["this_host"] == "linux" else 2)
    user_hosts = user.get("hosts") if isinstance(user.get("hosts"), dict) else {}
    cfg["hosts"] = resolve_hosts(cfg, user_hosts)
    return cfg


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


def mxswitch_path(cfg: dict) -> Path:
    path = which_cmd(str(cfg["mxswitch"]))
    if path is None:
        raise SystemExit(f"mxswitch not found: {cfg['mxswitch']}")
    return path


def lgdualup_path(cfg: dict) -> Path | None:
    configured = str(cfg.get("lgdualup") or "lgdualup")
    return which_cmd(configured)


def switch_mouse(cfg: dict, channel: int | None = None) -> int:
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


def switch_monitor(cfg: dict, host: str, *, mouse_only: bool) -> int:
    if mouse_only or not cfg.get("switch_monitor", True):
        return 0
    path = lgdualup_path(cfg)
    if path is None:
        print("lgdualup not on PATH — run `make install` (DualUp helper ships in this repo)")
        return 0
    spec = (cfg.get("hosts") or {}).get(normalize_host(host), {})
    name = str(spec.get("dualup_input") or "").strip()
    rc = 0
    if name:
        print(f"DualUp input → {name}")
        rc = call_lgdualup(
            cfg,
            ["input", name],
            missing="lgdualup not on PATH — run `make install`",
        )
    else:
        print(f"no dualup_input configured for {host} — leaving DualUp input alone")
    pbp = spec.get("pbp")
    if pbp or cfg.get("switch_pbp"):
        mode = str(pbp or cfg.get("pbp_mode") or "").strip()
        if mode:
            print(f"DualUp PBP → {mode}")
            pbp_rc = call_lgdualup(
                cfg,
                ["pbp", mode],
                missing="lgdualup not on PATH — run `make install`",
            )
            rc = rc or pbp_rc
    return rc


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
    return call_lgdualup(
        cfg,
        ["pbp", chosen],
        missing="lgdualup not on PATH — DualUp PBP needs `make install`",
    )


def cmd_full(cfg: dict) -> int:
    return call_lgdualup(
        cfg,
        ["pbp", "full"],
        missing="lgdualup not on PATH — DualUp full needs `make install`",
    )


def collect_status(cfg: dict) -> dict:
    present = hhkb_present(cfg)
    info, channel = mouse_info(cfg)
    dual_path = lgdualup_path(cfg)
    dual_info = ""
    if dual_path is not None:
        try:
            proc = run([str(dual_path), "--info"], timeout=8.0)
            dual_info = (proc.stdout or proc.stderr or "").rstrip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            dual_info = str(exc)
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
        "mxswitch": str(cfg["mxswitch"]),
        "mouse_channel": channel,
        "mouse_info": info,
        "lgdualup": dual_path is not None,
        "lgdualup_path": str(dual_path) if dual_path else None,
        "dualup_info": dual_info,
    }


def cmd_status(cfg: dict, *, as_json: bool = False, hint_only: bool = False) -> int:
    state = collect_status(cfg)
    if hint_only:
        print(state["target_hint"])
        return 0
    if as_json:
        print(json.dumps(state, indent=2))
        return 0
    print(f"os            : {state['os']}")
    print(f"version       : {state['version']}")
    print(f"config        : {state['config']}")
    print(f"this_host     : {state['this_host']}")
    print(f"hhkb          : {state['hhkb']}")
    print(f"target        : channel {state['target_channel']}")
    print(f"target_hint   : {state['target_hint']}")
    print(f"mxswitch      : {state['mxswitch']}")
    print("mouse         :")
    for line in (state["mouse_info"] or "(no output)").splitlines():
        print(f"  {line}")
    if state["lgdualup"]:
        print(f"lgdualup      : {state['lgdualup_path']}")
        print("dualup        :")
        for line in (state["dualup_info"] or "(no output)").splitlines():
            print(f"  {line}")
    else:
        print("lgdualup      : not on PATH (run make install)")
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
    pbp = sub.add_parser("pbp", help="DualUp PBP via lgdualup (no-op if missing)")
    pbp.add_argument("mode", nargs="?", help="mode string passed to `lgdualup pbp`")
    sub.add_parser("full", help="DualUp full-screen via `lgdualup pbp full`")
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
