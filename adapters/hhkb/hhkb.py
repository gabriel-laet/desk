#!/usr/bin/env python3
"""HHKB keyboard.presence reference adapter.

Core follow policy (watch edges, follow_channel) stays in desk.
VID/PID, USB-ghost matching, and ioreg/sysfs/hidutil probes live here.

    hhkb info [--vid 0x04FE] [--pid 0x0016]   # JSON presence
    hhkb probe                                 # same as info

When installed: ~/.local/lib/desk/hhkb
desk prefers this binary when discovered; otherwise it imports this
module from the source tree as a temporary in-process fallback.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

SYSTEM = platform.system()
HHKB_VID_DEFAULT = 0x04FE
HHKB_PID_DEFAULT = 0x0016
HHKB_NAME_RE = re.compile(r"HHKB", re.I)
HID_BUS_USB = 0x0003
HID_BUS_BLUETOOTH = 0x0005
HID_DEVICES_DIR = Path("/sys/bus/hid/devices")
USB_DEVICES_DIR = Path("/sys/bus/usb/devices")


def run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def empty_hhkb_probe(*, unknown: bool = False) -> dict:
    return {
        "present": unknown,  # watch-safe: a failed probe is not "gone"
        "usb": False,
        "bluetooth": False,
        "transport": "unknown" if unknown else "absent",
        "unknown": unknown,
        "names": [],
    }


def hhkb_transport_of(usb: bool, bluetooth: bool, present: bool, unknown: bool = False) -> str:
    if usb and bluetooth:
        return "both"
    if usb:
        return "usb"
    if bluetooth:
        return "bluetooth"
    if unknown or (present and not usb and not bluetooth):
        return "unknown"
    return "absent"


def _finish_hhkb_probe(usb: bool, bluetooth: bool, present: bool, unknown: bool, names: list) -> dict:
    if present:
        unknown = False
    return {
        "present": present or unknown,
        "usb": usb,
        "bluetooth": bluetooth,
        "transport": hhkb_transport_of(usb, bluetooth, present, unknown),
        "unknown": unknown and not present,
        "names": names,
    }


def merge_hhkb_probes(*probes: dict) -> dict:
    usb = any(p.get("usb") for p in probes)
    bluetooth = any(p.get("bluetooth") for p in probes)
    present = any(p.get("present") and not p.get("unknown") for p in probes)
    unknown = bool(probes) and all(p.get("unknown") for p in probes) and not present
    names: list[str] = []
    for probe in probes:
        names.extend(probe.get("names") or [])
    return _finish_hhkb_probe(usb, bluetooth, present, unknown, names)


def _hid_id_parts(name: str) -> tuple[int | None, int | None, int | None]:
    """Parse 0003:000004FE:00000016.XXXX → (bus, vid, pid)."""
    head = str(name).split(".", 1)[0]
    parts = head.split(":")
    if len(parts) < 3:
        return None, None, None
    try:
        return int(parts[0], 16), int(parts[1], 16), int(parts[2], 16)
    except ValueError:
        return None, None, None


def parse_linux_hhkb_sysfs(hid_dir: Path, usb_dir: Path | None, vid: int, pid: int) -> dict:
    """USB vs Bluetooth from hid bus type (0003=USB, 0005=BT) plus /sys USB tree."""
    if not hid_dir.is_dir():
        return empty_hhkb_probe(unknown=True)
    usb = False
    bluetooth = False
    present = False
    names: list[str] = []
    needle = f"{vid:04X}:{pid:04X}"
    try:
        entries = list(hid_dir.iterdir())
    except OSError:
        return empty_hhkb_probe(unknown=True)
    for entry in entries:
        bus, hid_vid, hid_pid = _hid_id_parts(entry.name)
        hid_name = ""
        uevent = entry / "uevent"
        try:
            text = uevent.read_text() if uevent.is_file() else ""
        except OSError:
            text = ""
        for line in text.splitlines():
            if line.startswith("HID_NAME="):
                hid_name = line.split("=", 1)[1]
        id_hit = needle in entry.name.upper() or (hid_vid == vid and hid_pid == pid)
        name_hit = bool(hid_name and HHKB_NAME_RE.search(hid_name))
        if not (id_hit or name_hit):
            continue
        present = True
        if hid_name:
            names.append(hid_name)
        if bus == HID_BUS_USB:
            usb = True
        elif bus == HID_BUS_BLUETOOTH:
            bluetooth = True
    if usb_dir is not None and usb_dir.is_dir():
        try:
            for entry in usb_dir.iterdir():
                try:
                    got_vid = (entry / "idVendor").read_text().strip()
                    got_pid = (entry / "idProduct").read_text().strip()
                except OSError:
                    continue
                try:
                    if int(got_vid, 16) == vid and int(got_pid, 16) == pid:
                        usb = True
                        present = True
                except ValueError:
                    continue
        except OSError:
            pass
    return _finish_hhkb_probe(usb, bluetooth, present, False, names)


def hhkb_probe_linux(vid: int, pid: int) -> dict:
    return parse_linux_hhkb_sysfs(HID_DEVICES_DIR, USB_DEVICES_DIR, vid, pid)


def _ioreg_int(block: str, key: str) -> int | None:
    match = re.search(rf'"{re.escape(key)}"\s*=\s*(0x[0-9a-fA-F]+|\d+)', block)
    return int(match.group(1), 0) if match else None


def _ioreg_str(block: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*=\s*"([^"]*)"', block)
    return match.group(1) if match else ""


def _ioreg_bool(block: str, key: str) -> bool | None:
    match = re.search(rf'"{re.escape(key)}"\s*=\s*(Yes|No|true|false)', block, re.I)
    if not match:
        return None
    return match.group(1).lower() in ("yes", "true")


def split_ioreg_nodes(text: str) -> list[str]:
    return [part for part in re.split(r"\n\s*\+-o\s+", "\n" + (text or "")) if part.strip()]


def parse_ioreg_hhkb(hid_text: str, usb_text: str, bt_text: str, vid: int, pid: int) -> dict:
    """Mac USB cable vs Bluetooth from ioreg (IOUSB + IOHIDDevice + IOBluetoothDevice)."""
    usb = False
    bluetooth = False
    present = False
    names: list[str] = []
    for block in split_ioreg_nodes(usb_text):
        product = _ioreg_str(block, "USB Product Name") or _ioreg_str(block, "kUSBProductString")
        id_hit = _ioreg_int(block, "idVendor") == vid and _ioreg_int(block, "idProduct") == pid
        name_hit = bool(product and HHKB_NAME_RE.search(product))
        if not (id_hit or name_hit):
            continue
        usb = True
        present = True
        if product:
            names.append(product)
    for block in split_ioreg_nodes(hid_text):
        product = _ioreg_str(block, "Product") or _ioreg_str(block, "ProductName")
        hid_vid = _ioreg_int(block, "VendorID")
        hid_pid = _ioreg_int(block, "ProductID")
        id_hit = hid_vid == vid and hid_pid in (None, pid)
        name_hit = bool(product and HHKB_NAME_RE.search(product))
        if not (id_hit or name_hit):
            continue
        present = True
        if product:
            names.append(product)
        transport = (_ioreg_str(block, "Transport") or "").lower()
        if "usb" in transport:
            usb = True
        elif "bluetooth" in transport or "ble" in transport:
            bluetooth = True
    for block in split_ioreg_nodes(bt_text):
        name = _ioreg_str(block, "Name") or _ioreg_str(block, "DeviceName")
        if not (name and HHKB_NAME_RE.search(name)):
            continue
        connected = _ioreg_bool(block, "DeviceConnected")
        if connected is None:
            connected = _ioreg_bool(block, "Connected")
        if connected is False:
            continue
        bluetooth = True
        present = True
        names.append(name)
    return _finish_hhkb_probe(usb, bluetooth, present, False, names)


def parse_hidutil_hhkb(text: str, vid: int, pid: int) -> dict:
    """Any HHKB HID row — Product name or VID/PID, not only usage page 1 / usage 6."""
    usb = False
    bluetooth = False
    present = False
    names: list[str] = []
    in_services = True
    vid_tok = f"0x{vid:x}"
    pid_tok = f"0x{pid:x}"
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("Services:"):
            in_services = True
            continue
        if line.startswith("Devices:"):
            in_services = False
            continue
        if not in_services:
            continue
        low = line.lower()
        name_hit = bool(HHKB_NAME_RE.search(line))
        col_hit = False
        cols = line.split()
        if cols and cols[0].startswith("0x"):
            try:
                col_hit = int(cols[0], 0) == vid and len(cols) > 1 and int(cols[1], 0) == pid
            except ValueError:
                col_hit = False
        id_hit = col_hit or (vid_tok in low and pid_tok in low)
        if not (name_hit or id_hit):
            continue
        present = True
        if name_hit:
            names.append(line)
        if "bluetooth" in low:
            bluetooth = True
        elif "usb" in low:
            usb = True
    return _finish_hhkb_probe(usb, bluetooth, present, False, names)


def _ioreg(args: list[str]) -> str:
    try:
        proc = run(["ioreg", *args], timeout=4.0)
        return proc.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def hhkb_probe_macos(vid: int, pid: int) -> dict:
    """Prefer ioreg (USB tree + HID Transport + BT name). hidutil VID/PID misses BT HHKB-Studio1."""
    parsed = parse_ioreg_hhkb(
        _ioreg(["-r", "-c", "IOHIDDevice", "-l", "-w", "0"]),
        _ioreg(["-p", "IOUSB", "-l", "-w", "0"]),
        _ioreg(["-r", "-c", "IOBluetoothDevice", "-l", "-w", "0"]),
        vid,
        pid,
    )
    hidutil_unknown = False
    hidutil_text = ""
    try:
        proc = run(
            ["hidutil", "list", "--matching", json.dumps({"VendorID": vid, "ProductID": pid})],
            timeout=4.0,
        )
        if proc.returncode != 0:
            hidutil_unknown = True
        hidutil_text = proc.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        hidutil_unknown = True
    hidutil = parse_hidutil_hhkb(hidutil_text, vid, pid)
    merged = merge_hhkb_probes(parsed, hidutil)
    if hidutil_unknown and not merged["present"]:
        return empty_hhkb_probe(unknown=True)
    return merged


def hhkb_probe(vid: int, pid: int, system: str | None = None) -> dict:
    os_name = system or SYSTEM
    if os_name == "Darwin":
        return hhkb_probe_macos(vid, pid)
    if os_name == "Linux":
        return hhkb_probe_linux(vid, pid)
    raise SystemExit(f"unsupported OS: {os_name}")


def probe_from_argv(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", nargs="?", default="info", help="info | probe")
    parser.add_argument("--vid", default=hex(HHKB_VID_DEFAULT))
    parser.add_argument("--pid", default=hex(HHKB_PID_DEFAULT))
    args = parser.parse_args(argv)
    if args.verb not in ("info", "probe"):
        raise SystemExit(f"unknown verb: {args.verb}")
    vid = int(str(args.vid), 0)
    pid = int(str(args.pid), 0)
    return hhkb_probe(vid, pid)


def main(argv: list[str] | None = None) -> int:
    probe = probe_from_argv(argv)
    print(json.dumps(probe, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
