#!/usr/bin/env bash
# lgdualup.sh - talk to an LG DualUp over USB HID 043e:9a39 (Linux hidraw).
#
#   ./lgdualup.sh --info
#   ./lgdualup.sh input usbc|dp|dp1|dp2|hdmi1|hdmi2
#   ./lgdualup.sh input-main <name>
#   ./lgdualup.sh input-sub <name>
#   ./lgdualup.sh swap
#   ./lgdualup.sh pbp-assign <main> <sub>
#   ./lgdualup.sh pbp on|off|full|side|50-50|stacked|1|3|5
#   ./lgdualup.sh get pbp
#   ./lgdualup.sh --read 0xD7
#
# VCP 0xF4 @ 0x50 is Main only. Sub window needs 0x55/0x5A and/or the
# Main→swap→Main dance (0xF6). PBP/split is VCP 0xD7 @ 0x51.
#
# Known codes on this panel: 1 = off/full, 5 = 50-50 side by side,
# 3 = 66/33 side by side. Codes 0 and 2 stretch the picture and are
# never sent. The top/bottom (stacked) code is not hardcoded: set the
# picture from the OSD, then `lgdualup get pbp` / `--read 0xD7`, and
# put that value in pbp_splits.stacked.
#
# A get is a DDC/CI Get VCP Feature (opcode 0x01) in the same HID
# wrapper as a set. The reply is opcode 0x02.
#
# hidraw is root-only by default. One-time:
#   sudo cp linux/43-lg-dualup.rules /etc/udev/rules.d/
#   sudo udevadm control --reload-rules && sudo udevadm trigger

set -uo pipefail

SPLIT_SIDE=5
SPLIT_STACKED=""
SPLIT_REJECTED=0

lg_hidraws() {
    local node name
    for node in /dev/hidraw*; do
        [ -e "$node" ] || continue
        name=$(basename "$node")
        if grep -qiE 'HID_ID=[^:]*:0*43[eE]:0*9[aA]39' \
               "/sys/class/hidraw/$name/device/uevent" 2>/dev/null; then
            printf '%s\n' "$node"
        fi
    done
}

# XOR checksum starting at 0x6E, matching the DualUp USB wrapper.
ddc_sum() {
    local sum=110  # 0x6E
    local b
    for b in "$@"; do
        sum=$(( sum ^ b ))
    done
    printf '%s' "$sum"
}

# Write one 64-byte output report. $1 = hidraw, remaining = payload bytes.
write_report() {
    local node=$1; shift
    local -a bytes=("$@")
    local esc="" b
    local i=0
    for b in "${bytes[@]}"; do
        esc+=$(printf '\\x%02x' "$b")
        i=$((i + 1))
    done
    while [ "$i" -lt 64 ]; do
        esc+='\x00'
        i=$((i + 1))
    done
    printf '%b' "$esc" >"$node"
}

send_vcp() {
    local node=$1 addr=$2 vcp=$3 val=$4
    local hi=$(( (val >> 8) & 0xFF ))
    local lo=$(( val & 0xFF ))
    local d0=$addr d1=$((0x80 | 4)) d2=3 d3=$vcp d4=$hi d5=$lo
    local sum
    sum=$(ddc_sum "$d0" "$d1" "$d2" "$d3" "$d4" "$d5")

    write_report "$node" 8 1 85 3 7 0 3 55 \
        "$d0" "$d1" "$d2" "$d3" "$d4" "$d5" "$sum" || return 1
    sleep 0.04
    write_report "$node" 8 2 85 4 11 0 11 55 || true
}

# DDC/CI get (opcode 0x01). Prints the current value, or exits non-zero.
# Reply opcode is 0x02. The HID report is read on the same fd that wrote.
read_vcp() {
    local node=$1 addr=$2 vcp=$3
    if ! command -v python3 >/dev/null 2>&1; then
        echo "lgdualup get needs python3 to read the HID reply" >&2
        return 1
    fi
    python3 - "$node" "$addr" "$vcp" <<'PY'
import os, select, sys, time

node, addr_s, vcp_s = sys.argv[1:]
addr = int(addr_s, 0) & 0xFF
vcp = int(vcp_s, 0) & 0xFF

def checksum(msg):
    total = 0x6E
    for byte in msg:
        total ^= byte
    return total & 0xFF

def frame(second, payload):
    pkt = bytearray(64)
    pkt[0] = 0x08
    pkt[2] = 0x55
    pkt[7] = 0x37
    if second:
        pkt[1] = 0x02
        pkt[3] = 0x04
        pkt[4] = 0x0B
        pkt[6] = 0x0B
        return bytes(pkt)
    body = bytes([addr, 0x80 | len(payload), *payload])
    body += bytes([checksum(body)])
    pkt[1] = 0x01
    pkt[3] = 0x03
    pkt[4] = len(body)
    pkt[6] = 0x03
    pkt[8:8 + len(body)] = body
    return bytes(pkt)

def parse(data, want):
    def at(buf):
        if len(buf) < 14:
            return None
        span = (buf[5] & 0x7F) + 2
        if 5 + span > len(buf) or buf[8] != want:
            return None
        total = 0x6E ^ 0x50
        for i in range(span):
            total ^= buf[5 + i]
        if total & 0xFF != 0:
            return None
        return (buf[12] << 8) | buf[13]

    hit = at(data)
    if hit is not None:
        return hit
    if len(data) > 1:
        hit = at(data[1:])
        if hit is not None:
            return hit
    for i in range(0, len(data) - 8):
        if data[i] == 0x02 and data[i + 2] == want and data[i + 1] in (0x00, 0x01):
            return (data[i + 6] << 8) | data[i + 7]
    return None

fd = os.open(node, os.O_RDWR)
try:
    for _attempt in range(4):
        os.write(fd, frame(False, bytes([0x01, vcp])))
        time.sleep(0.04)
        os.write(fd, frame(True, b""))
        deadline = time.time() + 0.4
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            data = os.read(fd, 64)
            value = parse(data, vcp)
            if value is not None:
                print(value)
                sys.exit(0)
    sys.exit(3)
finally:
    os.close(fd)
PY
}

apply_splits() {
    local blob=$1 pair key value
    local IFS=','
    # shellcheck disable=SC2086
    for pair in $blob; do
        key=${pair%%=*}
        value=${pair#*=}
        key=$(printf '%s' "$key" | tr '[:upper:]' '[:lower:]' | tr -d ' ')
        [ -n "$key" ] || continue
        case "$value" in
            ''|*[!0-9xXa-fA-F]*) continue ;;
        esac
        value=$((value))
        if [ "$value" -eq 0 ] || [ "$value" -eq 2 ]; then
            echo "pbp code ${value} is not sent (it stretches this panel)" >&2
            SPLIT_REJECTED=1
            continue
        fi
        case "$key" in
            stacked) SPLIT_STACKED=$value ;;
            side) SPLIT_SIDE=$value ;;
        esac
    done
}

load_config_splits() {
    command -v python3 >/dev/null 2>&1 || return 0
    local blob
    blob=$(python3 - <<'PY'
import json, os
from pathlib import Path

home = Path.home()
xdg = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
paths = [
    xdg / "desk" / "config.json",
    home / ".config" / "desk" / "config.json",
    home / ".config" / "desk-switch" / "config.json",
    home / ".config" / "hhkb-mx-follow" / "config.json",
]
found = {}

def walk(node):
    if not isinstance(node, dict):
        return
    splits = node.get("pbp_splits")
    if isinstance(splits, dict):
        for key in ("stacked", "side"):
            if key in splits and splits[key] not in (None, ""):
                try:
                    found[key] = int(str(splits[key]), 0)
                except (TypeError, ValueError):
                    pass
    for value in node.values():
        if isinstance(value, dict):
            walk(value)

for path in paths:
    if not path.is_file():
        continue
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        continue
    if isinstance(raw, dict):
        walk(raw)
    break

parts = []
for key in ("stacked", "side"):
    if key not in found:
        continue
    if found[key] in (0, 2):
        continue
    parts.append(f"{key}={found[key]}")
if parts:
    print(",".join(parts))
PY
    ) || return 0
    if [ -n "$blob" ]; then
        apply_splits "$blob"
    fi
}

parse_input() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        auto) echo 0 ;;
        hdmi1) echo $((0x90)) ;;
        hdmi2) echo $((0x91)) ;;
        dp|dp1) echo $((0xD0)) ;;
        dp2) echo $((0xD1)) ;;
        dp3|usbc|usb-c) echo $((0xD2)) ;;
        0x*|0X*) printf '%d' "$1" ;;
        *) return 1 ;;
    esac
}

# Names resolve through pbp_splits. 0 and 2 are refused even as raw numbers.
parse_pbp() {
    local key
    key=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    case "$key" in
        off|none|solo|full) echo 1 ;;
        side|on|50|50/50|50-50) echo "$SPLIT_SIDE" ;;
        66|66/33|66-33) echo 3 ;;
        stacked|top-bottom|tb)
            if [ -z "$SPLIT_STACKED" ]; then
                echo "pbp split \"stacked\" has no code. Set the picture in the OSD, run \`lgdualup get pbp\` or \`lgdualup --read 0xD7\`, and set pbp_splits.stacked to that value. Codes 0 and 2 are not sent." >&2
                return 1
            fi
            echo "$SPLIT_STACKED"
            ;;
        *)
            if [[ "$1" =~ ^[0-9]+$ ]] || [[ "$1" =~ ^0[xX][0-9a-fA-F]+$ ]]; then
                local value=$((key))
                if [ "$value" -eq 0 ] || [ "$value" -eq 2 ]; then
                    echo "pbp code ${value} is not sent (it stretches this panel)" >&2
                    return 1
                fi
                printf '%d' "$value"
            else
                echo "unknown pbp mode: $1" >&2
                return 1
            fi
            ;;
    esac
}

usage() {
    echo "usage: $0 --info | --list | input|input-main|input-sub <name> | swap | pbp-assign <main> <sub> | pbp <mode> | get pbp | --read <vcp>" >&2
}

vcp_addr() {
    # PBP lives on 0x51. Input VCPs live on 0x50.
    case "$1" in
        215|0xd7|0xD7) echo $((0x51)) ;;
        *) echo $((0x50)) ;;
    esac
}

main() {
    local cli_blob=""
    local -a kept=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --splits)
                [ $# -ge 2 ] || { usage; exit 2; }
                cli_blob="${cli_blob:+$cli_blob,}$2"
                shift 2
                ;;
            --splits=*)
                cli_blob="${cli_blob:+$cli_blob,}${1#--splits=}"
                shift
                ;;
            *)
                kept+=("$1")
                shift
                ;;
        esac
    done
    if [ "${#kept[@]}" -gt 0 ]; then
        set -- "${kept[@]}"
    else
        set --
    fi

    # Config, then the environment, then the command line. A later
    # source replaces the same key. Codes 0 and 2 are refused.
    load_config_splits
    if [ -n "${LGDUALUP_PBP_SPLITS:-}" ]; then
        apply_splits "$LGDUALUP_PBP_SPLITS"
    fi
    if [ -n "$cli_blob" ]; then
        apply_splits "$cli_blob"
    fi
    if [ "$SPLIT_REJECTED" -ne 0 ]; then
        exit 2
    fi

    local cmd=${1:-}
    case "$cmd" in
        --list)
            local n
            n=$(lg_hidraws)
            if [ -z "$n" ]; then
                echo "No LG Monitor Controls hidraw (043e:9a39) found." >&2
                exit 1
            fi
            printf '%s\n' "$n"
            exit 0
            ;;
        --info)
            local node
            node=$(lg_hidraws | head -n1)
            if [ -z "$node" ]; then
                echo "No LG Monitor Controls hidraw (043e:9a39) found." >&2
                echo "Plug DualUp USB into this machine, or use ddcutil over DP/HDMI." >&2
                exit 1
            fi
            echo "device : LG Monitor Controls"
            echo "usb    : 043e:9a39"
            echo "hidraw : $node"
            exit 0
            ;;
        get|--read)
            if [ "$cmd" = get ] && [ "${2:-}" != pbp ] && ! [[ "${2:-}" =~ ^0[xX][0-9a-fA-F]+$ ]]; then
                usage
                exit 2
            fi
            if [ "$cmd" = --read ] && ! [[ "${2:-}" =~ ^(0[xX][0-9a-fA-F]+|[0-9]+)$ ]]; then
                usage
                exit 2
            fi
            ;;
        pbp)
            parse_pbp "${2:-}" >/dev/null || exit 2
            ;;
        input|input-main|input-sub|swap|pbp-assign) ;;
        *) usage; exit 2 ;;
    esac

    local node
    node=$(lg_hidraws | head -n1)
    if [ -z "$node" ]; then
        echo "No LG Monitor Controls hidraw (043e:9a39) found." >&2
        exit 1
    fi
    if [ ! -w "$node" ] && [ "$cmd" != get ] && [ "$cmd" != --read ]; then
        echo "Cannot write $node — install linux/43-lg-dualup.rules" >&2
        exit 1
    fi

    case "$cmd" in
        input|input-main)
            local val
            val=$(parse_input "${2:-}") || {
                echo "unknown input: ${2:-}" >&2
                exit 2
            }
            send_vcp "$node" $((0x50)) $((0xF4)) "$val" || exit 1
            printf 'input-main -> %s (0x%02x)\n' "$2" "$val"
            ;;
        input-sub)
            local val
            val=$(parse_input "${2:-}") || {
                echo "unknown input: ${2:-}" >&2
                exit 2
            }
            send_vcp "$node" $((0x50)) $((0x55)) "$val" || exit 1
            sleep 0.08
            send_vcp "$node" $((0x50)) $((0x5A)) "$val" || true
            printf 'input-sub -> %s (0x%02x) via 0x55/0x5A\n' "$2" "$val"
            ;;
        swap)
            send_vcp "$node" $((0x50)) $((0xF6)) 1 || exit 1
            sleep 0.04
            send_vcp "$node" $((0x51)) $((0xF6)) 1 || true
            printf 'pbp swap -> main/sub (0xF6=1)\n'
            ;;
        pbp-assign)
            local main_v sub_v
            main_v=$(parse_input "${2:-}") || {
                echo "unknown input: ${2:-}" >&2
                exit 2
            }
            sub_v=$(parse_input "${3:-}") || {
                echo "unknown input: ${3:-}" >&2
                exit 2
            }
            send_vcp "$node" $((0x50)) $((0x55)) "$sub_v" || true
            sleep 0.08
            send_vcp "$node" $((0x50)) $((0x5A)) "$sub_v" || true
            sleep 0.15
            send_vcp "$node" $((0x50)) $((0xF4)) "$sub_v" || exit 1
            sleep 0.2
            send_vcp "$node" $((0x50)) $((0xF6)) 1 || true
            send_vcp "$node" $((0x51)) $((0xF6)) 1 || true
            sleep 0.2
            send_vcp "$node" $((0x50)) $((0xF4)) "$main_v" || exit 1
            printf 'pbp-assign main=%s (0x%02x) sub=%s (0x%02x)\n' "$2" "$main_v" "$3" "$sub_v"
            ;;
        pbp)
            local val
            val=$(parse_pbp "${2:-}") || {
                echo "unknown pbp mode: ${2:-}" >&2
                exit 2
            }
            send_vcp "$node" $((0x51)) $((0xD7)) "$val" || exit 1
            printf 'pbp -> %s (0x%02x)\n' "$2" "$val"
            ;;
        get|--read)
            local which addr value label
            which=${2:-}
            if [ "$cmd" = get ] && [ "$which" = pbp ]; then
                which=$((0xD7))
                label=pbp
            else
                which=$((which))
                label=vcp
            fi
            addr=$(vcp_addr "$which")
            if ! value=$(read_vcp "$node" "$addr" "$which"); then
                printf 'no VCP reply for 0x%02x @ 0x%02x (DDC get 0x01, reply 0x02)\n' "$which" "$addr" >&2
                exit 1
            fi
            printf '%s 0x%02x @ 0x%02x = %d (0x%02x)\n' "$label" "$which" "$addr" "$value" "$value"
            if [ "$which" -eq $((0xD7)) ] && [ "$value" -ne 1 ] && [ "$value" -ne 3 ] && [ "$value" -ne 5 ]; then
                echo "If this is the stacked picture, set pbp_splits.stacked to ${value}. Codes 0 and 2 are not sent." >&2
            fi
            ;;
    esac
}

main "$@"
