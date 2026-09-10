#!/usr/bin/env python3
"""Switch a Logitech Easy-Switch device to another channel (Linux).

HID++ 2.0 feature 0x1814 (ChangeHost) over hidraw. Unlike the bash port,
this ignores mouse/keyboard input reports and only accepts HID++ replies
(0x10 / 0x11). Bluetooth MX Masters flood hidraw with 0x02 motion frames,
which made the bash reader time out and then exit 0 anyway.

    mxswitch --info
    mxswitch 1|2|3
"""

from __future__ import annotations

import os
import select
import sys
import time
from pathlib import Path

SW_ID = 0x0A
FEAT_CHANGE_HOST = 0x1814
DEVICE_INDICES = (0xFF, 1, 2, 3)
REPORT_IDS = (0x11, 0x10)


def hid_uevent(node: Path) -> dict[str, str]:
    uevent = Path("/sys/class/hidraw") / node.name / "device" / "uevent"
    out: dict[str, str] = {}
    try:
        text = uevent.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            out[key] = val
    return out


def logitech_hidraws() -> list[Path]:
    mice: list[Path] = []
    other: list[Path] = []
    kbds: list[Path] = []
    for node in sorted(Path("/dev").glob("hidraw*")):
        ev = hid_uevent(node)
        hid_id = ev.get("HID_ID", "")
        # HID_ID=bustype:vendor:product  e.g. 0005:0000046D:0000B042
        parts = hid_id.split(":")
        if len(parts) < 2 or parts[1].lstrip("0").upper() != "46D":
            continue
        name = ev.get("HID_NAME", "").lower()
        if any(tok in name for tok in ("master", "anywhere", "mouse", "ergo")):
            mice.append(node)
        elif any(tok in name for tok in ("keys", "keyboard")):
            kbds.append(node)
        else:
            other.append(node)
    return mice + other + kbds


def drain(fd: int, budget: float = 0.05) -> None:
    end = time.time() + budget
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], max(0.0, end - time.time()))
        if not r:
            break
        try:
            os.read(fd, 64)
        except OSError:
            break


def hidpp_call(fd: int, rid: int, didx: int, fidx: int, func: int, *payload: int) -> bytes | None:
    length = 7 if rid == 0x10 else 20
    frame = [rid, didx, fidx, ((func << 4) | SW_ID), *payload]
    frame.extend([0] * (length - len(frame)))
    drain(fd)
    try:
        os.write(fd, bytes(frame))
    except OSError:
        return None

    deadline = time.time() + 0.8
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], max(0.0, deadline - time.time()))
        if not r:
            break
        try:
            reply = os.read(fd, 64)
        except OSError:
            return None
        if len(reply) < 5:
            continue
        # Skip mouse/keyboard input reports (BLE MX Master floods 0x02).
        if reply[0] not in (0x10, 0x11):
            continue
        if reply[0] != rid:
            continue
        if reply[1] != didx:
            continue
        if reply[2] in (0xFF, 0x8F):
            return None
        if reply[2] != fidx:
            continue
        if (reply[3] & 0x0F) != SW_ID:
            continue
        return reply
    return None


def find_device() -> tuple[int, int, int, int, Path] | None:
    for node in logitech_hidraws():
        try:
            fd = os.open(node, os.O_RDWR)
        except OSError:
            continue
        try:
            for didx in DEVICE_INDICES:
                for rid in REPORT_IDS:
                    reply = hidpp_call(
                        fd, rid, didx, 0, 0,
                        (FEAT_CHANGE_HOST >> 8) & 0xFF,
                        FEAT_CHANGE_HOST & 0xFF,
                    )
                    if not reply or len(reply) < 5:
                        continue
                    fidx = reply[4]
                    if fidx == 0:
                        continue
                    return fd, rid, didx, fidx, node
        except Exception:
            os.close(fd)
            continue
        os.close(fd)
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {"--info", "1", "2", "3"}:
        print("usage: mxswitch {1|2|3|--info}", file=sys.stderr)
        return 2

    found = find_device()
    if not found:
        print("No Logitech device supporting ChangeHost found.", file=sys.stderr)
        print("Click the mouse to wake it; check hidraw permissions.", file=sys.stderr)
        return 1

    fd, rid, didx, fidx, node = found
    try:
        if argv[0] == "--info":
            print(f"device     : {node}")
            print(f"transport  : index {didx}, report {rid:#x}")
            print(f"ChangeHost : feature index {fidx}")
            reply = hidpp_call(fd, rid, didx, fidx, 0)
            if reply and len(reply) >= 6:
                print(f"channels   : {reply[4]}, currently on {reply[5] + 1}")
            return 0

        target = int(argv[0]) - 1
        print(f"Switching to channel {target + 1} ...")
        reply = hidpp_call(fd, rid, didx, fidx, 1, target)
        if reply is None:
            print("ChangeHost request was not acknowledged.", file=sys.stderr)
            return 1
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
