#!/usr/bin/env bash
# lgdualup.sh - talk to an LG DualUp over USB HID 043e:9a39 (Linux hidraw).
#
#   ./lgdualup.sh --info
#   ./lgdualup.sh input usbc|dp|dp1|dp2|hdmi1|hdmi2
#   ./lgdualup.sh pbp on|off|full|50-50|1|2|3|5
#
# hidraw is root-only by default. One-time:
#   sudo cp linux/43-lg-dualup.rules /etc/udev/rules.d/
#   sudo udevadm control --reload-rules && sudo udevadm trigger

set -uo pipefail

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

parse_pbp() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        off|none|solo|full) echo 1 ;;
        on|50|50/50|50-50) echo 5 ;;
        66|66/33) echo 3 ;;
        *)
            if [[ "$1" =~ ^[0-9]+$ ]] || [[ "$1" =~ ^0[xX] ]]; then
                printf '%d' "$1"
            else
                return 1
            fi
            ;;
    esac
}

usage() {
    echo "usage: $0 --info | --list | input <name> | pbp <mode>" >&2
}

main() {
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
        input|pbp) ;;
        *) usage; exit 2 ;;
    esac

    local node
    node=$(lg_hidraws | head -n1)
    if [ -z "$node" ]; then
        echo "No LG Monitor Controls hidraw (043e:9a39) found." >&2
        exit 1
    fi
    if [ ! -w "$node" ]; then
        echo "Cannot write $node — install linux/43-lg-dualup.rules" >&2
        exit 1
    fi

    case "$cmd" in
        input)
            local val
            val=$(parse_input "${2:-}") || {
                echo "unknown input: ${2:-}" >&2
                exit 2
            }
            send_vcp "$node" $((0x50)) $((0xF4)) "$val" || exit 1
            printf 'input -> %s (0x%02x)\n' "$2" "$val"
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
    esac
}

main "$@"
