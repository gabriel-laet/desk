#!/usr/bin/env python3
"""Follow an HHKB Studio host-switch by pushing the MX Master to another channel.

Each machine only ever pushes the mouse *away*. Install on every computer you
leave from, and point target_channel at the machine you switch *to*.

    hhkb-mx-follow status
    hhkb-mx-follow watch
    hhkb-mx-follow watch --dry-run
    hhkb-mx-follow switch [1|2|3]
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

SYSTEM = platform.system()
HERE = Path(__file__).resolve().parent
CONFIG_CANDIDATES = (
    Path.home() / ".config" / "hhkb-mx-follow" / "config.json",
    HERE / "config.json",
)
HHKB_VID_DEFAULT = 0x04FE
HHKB_PID_DEFAULT = 0x0016


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    print(line, flush=True)


def load_config() -> dict:
    cfg: dict = {
        "target_channel": 2,
        "hhkb_vendor_id": HHKB_VID_DEFAULT,
        "hhkb_product_id": HHKB_PID_DEFAULT,
        "poll_interval_s": 0.5,
        "absent_polls_required": 4,
        "sleep_gap_s": 3.0,
        "switch_retries": 8,
        "switch_retry_delay_s": 0.6,
        "mxswitch": str(Path.home() / ".local" / "bin" / "mxswitch"),
    }
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
    cfg["hhkb_vendor_id"] = int(cfg["hhkb_vendor_id"], 0) if isinstance(
        cfg["hhkb_vendor_id"], str
    ) else int(cfg["hhkb_vendor_id"])
    cfg["hhkb_product_id"] = int(cfg["hhkb_product_id"], 0) if isinstance(
        cfg["hhkb_product_id"], str
    ) else int(cfg["hhkb_product_id"])
    return cfg


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
        # Keyboard application collection — this is the typing interface.
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


def mxswitch_path(cfg: dict) -> Path:
    path = Path(cfg["mxswitch"]).expanduser()
    if not path.is_file():
        raise SystemExit(f"mxswitch not found: {path}")
    return path


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


def cmd_status(cfg: dict) -> int:
    present = hhkb_present(cfg)
    print(f"os            : {SYSTEM}")
    print(f"config        : {cfg['_config_path']}")
    print(f"hhkb          : {'present' if present else 'absent'}")
    print(f"target        : channel {cfg['target_channel']}")
    print(f"mxswitch      : {cfg['mxswitch']}")
    try:
        proc = run([str(mxswitch_path(cfg)), "--info"], timeout=8.0)
        info = (proc.stdout or proc.stderr or "").rstrip()
        print("mouse         :")
        for line in info.splitlines() or ["(no output)"]:
            print(f"  {line}")
    except SystemExit as exc:
        print(f"mouse         : {exc}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"mouse         : {exc}")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show HHKB / mouse state")
    watch = sub.add_parser("watch", help="follow HHKB departures")
    watch.add_argument("--dry-run", action="store_true")
    sw = sub.add_parser("switch", help="push the mouse now")
    sw.add_argument("channel", nargs="?", type=int)
    args = parser.parse_args()

    cfg = load_config()
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "watch":
        return cmd_watch(cfg, args.dry_run)
    if args.cmd == "switch":
        return switch_mouse(cfg, args.channel)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
