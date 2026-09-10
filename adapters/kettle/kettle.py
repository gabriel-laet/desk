#!/usr/bin/env python3
"""Fellow Stagg EKG Pro appliance adapter.

Out-of-process. Core never speaks Fellow HTTP.

    kettle info | status          # JSON snapshot (no default-host probe)
    kettle heat [temp]            # setunitsc + settempr + heaton
    kettle on                     # heaton
    kettle off                    # heatoff + warmoff + ss S_Off
    kettle host [ip]              # print or save ~/.config/kettle/host

Wire: unauthenticated GET http://<ip>/cli?cmd=<command> on the LAN.
Default / documented host on this desk: 192.168.3.36.

Capabilities: appliance.status / appliance.heat / appliance.off.

Weather is a separate adapter (`adapters/weather`). This binary does not
call Open-Meteo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ADAPTER_ID = "kettle"
ADAPTER_API_VERSION = 1
CAPABILITIES = ["appliance.status", "appliance.heat", "appliance.off"]
DEFAULT_HOST = "192.168.3.36"
DEFAULT_HEAT_C = "93"
DEFAULT_TIMEOUT_S = 2.0
CONTROL_TIMEOUT_S = 8.0


def manifest() -> dict:
    return {
        "api_version": ADAPTER_API_VERSION,
        "id": ADAPTER_ID,
        "name": "Fellow Stagg EKG Pro",
        "capabilities": list(CAPABILITIES),
    }


def config_root() -> Path | None:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg)
    home = os.environ.get("HOME", "").strip()
    if home:
        return Path(home) / ".config"
    return None


def kettle_host_path() -> Path | None:
    root = config_root()
    return (root / "kettle" / "host") if root else None


def fellow_host_path() -> Path | None:
    root = config_root()
    return (root / "fellow" / "host") if root else None


def _read_first_line(path: Path) -> str | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    line = (text.splitlines() or [""])[0].strip()
    return line or None


def load_saved_host() -> str | None:
    path = kettle_host_path()
    if path:
        host = _read_first_line(path)
        if host:
            return host
    fellow = fellow_host_path()
    return _read_first_line(fellow) if fellow else None


def save_host(host: str) -> Path:
    host = host.strip()
    if not host:
        raise SystemExit("empty host")
    path = kettle_host_path()
    if path is None:
        raise SystemExit("no HOME/XDG_CONFIG_HOME to save host")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(host + "\n")
    fellow = fellow_host_path()
    if fellow is not None:
        try:
            fellow.parent.mkdir(parents=True, exist_ok=True)
            fellow.write_text(host + "\n")
        except OSError:
            pass
    return path


def env_host() -> str | None:
    for name in ("KETTLE_HOST", "FELLOW_HOST", "STAGG_HOST"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def resolve_host_spec(explicit: str = "", *, use_default: bool = False) -> str | None:
    trimmed = (explicit or "").strip()
    if trimmed:
        return trimmed
    found = env_host()
    if found:
        return found
    saved = load_saved_host()
    if saved:
        return saved
    if use_default:
        return DEFAULT_HOST
    return None


def parse_target(host_or_url: str) -> dict:
    raw = (host_or_url or "").strip()
    if not raw:
        raise ValueError("empty host")
    if raw.startswith("https://"):
        raise ValueError("https is not supported (kettle CLI is unsecured HTTP on the LAN)")
    if raw.startswith("http://"):
        raw = raw[len("http://") :]
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if not raw:
        raise ValueError("missing host")
    if raw.count(":") == 1:
        host, port_s = raw.rsplit(":", 1)
        if not host:
            raise ValueError("invalid host:port")
        try:
            port = int(port_s)
        except ValueError as exc:
            raise ValueError("invalid host:port") from exc
        if port <= 0:
            raise ValueError("invalid host:port")
        return {"host": host, "port": port}
    return {"host": raw, "port": 80}


def format_target(target: dict) -> str:
    if int(target.get("port") or 80) == 80:
        return str(target["host"])
    return f"{target['host']}:{target['port']}"


def looks_like_html(line: str) -> bool:
    return "<" in line or ">" in line


def looks_like_esp_log(line: str) -> bool:
    return len(line) >= 4 and line[0] in "IWED" and line[1] == " " and line[2] == "("


def is_field_key(key: str) -> bool:
    return bool(key) and all(ch.isalnum() or ch in "_ " for ch in key)


def parse_return_code(line: str) -> int | None:
    if "command " not in line:
        return None
    pos = line.rfind(" ret ")
    if pos < 0:
        return None
    try:
        return int(line[pos + 5 :].strip())
    except ValueError:
        return None


def parse_kettle_body(body: str) -> dict:
    fields: list[tuple[str, str]] = []
    field_map: dict[str, str] = {}
    ret = None
    stripped_lines: list[str] = []
    for raw_line in (body or "").splitlines():
        line = raw_line.rstrip("\r")
        trimmed = line.strip()
        if not trimmed or looks_like_html(trimmed):
            continue
        stripped_lines.append(trimmed)
        if looks_like_esp_log(trimmed):
            code = parse_return_code(trimmed)
            if code is not None:
                ret = code
            continue
        if "=" in trimmed:
            key, value = trimmed.split("=", 1)
            key = key.strip()
            value = value.strip()
            if is_field_key(key):
                field_map[key] = value
                fields.append((key, value))
    return {
        "fields": fields,
        "field_map": field_map,
        "ret": ret,
        "stripped": "\n".join(stripped_lines) + ("\n" if stripped_lines else ""),
    }


def kettle_mode_from(raw: str) -> str:
    if raw in ("S_Off", "Off"):
        return "off"
    if raw in ("S_Heat", "Heat"):
        return "heating"
    if raw in ("S_HeatOff", "HeatOff"):
        return "heat_off"
    if raw in ("S_Hold", "Hold", "S_Warm", "Warm"):
        return "holding"
    return "unknown"


def kettle_mode_title(mode: str, raw: str = "") -> str:
    titles = {
        "off": "Off",
        "heat_off": "Off",
        "heating": "Heating",
        "holding": "Holding",
        "unknown": "Unknown",
    }
    if mode != "unknown":
        return titles[mode]
    if raw.startswith("S_") and raw[2:]:
        return raw[2:]
    return raw or "Unknown"


def kettle_mode_is_active(mode: str) -> bool:
    return mode in ("heating", "holding")


def parse_temperature(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    index = 1 if text[:1] in "+-" else 0
    saw_digit = False
    saw_dot = False
    while index < len(text):
        ch = text[index]
        if ch.isdigit():
            saw_digit = True
            index += 1
            continue
        if ch == "." and not saw_dot:
            saw_dot = True
            index += 1
            continue
        break
    if not saw_digit:
        return None
    try:
        value = float(text[:index])
    except ValueError:
        return None
    rest = text[index:].strip()
    while rest:
        if rest.startswith("°") or rest.startswith("*"):
            rest = rest[1:].strip()
            continue
        if rest.encode("utf-8").startswith(b"\xc2\xb0"):
            rest = rest[1:].strip()
            continue
        break
    return {"value": value, "unit": rest}


def temperature_symbol(temp: dict) -> str:
    unit = str(temp.get("unit") or "").upper()
    if unit in ("C", "°C"):
        return "C"
    if unit in ("F", "°F"):
        return "F"
    return "C" if not temp.get("unit") else str(temp["unit"])


def temperature_display(temp: dict) -> str:
    return f"{int(round(float(temp['value'])))}°{temperature_symbol(temp)}"


def temperature_compact(temp: dict) -> str:
    return f"{int(round(float(temp['value'])))}°"


def parse_altitude_meters(raw: str) -> int | None:
    token = (raw or "").strip().split()[0] if (raw or "").strip() else ""
    if not token:
        return None
    try:
        if "." not in token:
            return int(token)
        return int(round(float(token)))
    except ValueError:
        return None


def parse_settings_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in (body or "").splitlines():
        trimmed = raw_line.rstrip("\r").strip()
        if len(trimmed) < 3 or not trimmed[:3].lower() == "st:":
            continue
        after = trimmed[3:].strip()
        if "=" not in after:
            continue
        key, value = after.split("=", 1)
        key = key.strip()
        if key:
            fields[key] = value.strip()
    return fields


def settings_from_body(body: str) -> dict:
    fields = parse_settings_fields(body)
    return {
        "altitude_meters": parse_altitude_meters(fields["altitude"]) if "altitude" in fields else None,
        "fields": fields,
    }


def snapshot_from(parsed: dict) -> dict:
    field_map = parsed.get("field_map") or {}
    mode_raw = str(field_map.get("mode") or "")
    mode = kettle_mode_from(mode_raw)
    current = parse_temperature(field_map["tempr"]) if field_map.get("tempr") else None
    target = parse_temperature(field_map["temprT"]) if field_map.get("temprT") else None
    return {
        "mode": mode,
        "mode_raw": mode_raw,
        "mode_title": kettle_mode_title(mode, mode_raw),
        "is_active": kettle_mode_is_active(mode),
        "current": current,
        "target": target,
        "return_code": parsed.get("ret"),
    }


def heat_commands(celsius: str) -> list[str]:
    return ["setunitsc", f"setsettingd settempr {celsius}", "heaton"]


def off_commands() -> list[str]:
    return ["heatoff", "warmoff", "ss S_Off"]


def parse_temp(token: str) -> str:
    text = str(token).strip()
    if not text or not all(ch.isdigit() or ch in ".+-" for ch in text):
        raise SystemExit("temperature must be a number")
    try:
        float(text)
    except ValueError as exc:
        raise SystemExit("temperature must be a number") from exc
    return text


def http_cli(host: str, cmd: str, timeout: float) -> dict:
    target = parse_target(host)
    path = "/cli?cmd=" + urllib.parse.quote(cmd, safe="")
    url = f"http://{target['host']}:{target['port']}{path}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(0.2, float(timeout))) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {"ok": False, "status": int(exc.code), "body": body, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"ok": False, "status": 0, "body": "", "error": str(exc.reason if isinstance(exc, urllib.error.URLError) else exc)}
    if status < 200 or status >= 300:
        return {"ok": False, "status": status, "body": body, "error": f"HTTP {status}"}
    parsed = parse_kettle_body(body)
    if parsed.get("ret") not in (None, 0):
        return {
            "ok": False,
            "status": status,
            "body": body,
            "parsed": parsed,
            "error": f"kettle command {cmd!r} returned {parsed['ret']}",
        }
    return {"ok": True, "status": status, "body": body, "parsed": parsed, "error": None}


def send_command(host: str, cmd: str, timeout: float) -> dict:
    return http_cli(host, cmd, timeout)


def send_sequence(host: str, cmds: list[str], timeout: float, *, require_all: bool = False) -> dict:
    successes: list[dict] = []
    first_error = None
    for cmd in cmds:
        result = send_command(host, cmd, timeout)
        if result.get("ok"):
            successes.append(result)
            continue
        if first_error is None:
            first_error = result
            if require_all:
                return result
    if successes:
        return successes[-1]
    return first_error or {"ok": False, "error": "no commands succeeded"}


def slot_from_snapshot(snap: dict, *, host: str) -> dict | None:
    current = snap.get("current")
    if not current:
        return None
    target = snap.get("target")
    hot = bool(snap.get("is_active"))
    mode = str(snap.get("mode") or "unknown")
    label = temperature_compact(current)
    if mode == "heating" and target:
        detail = f"heating → {temperature_compact(target)}"
    elif mode == "holding" and target:
        detail = f"holding → {temperature_compact(target)}"
    elif target:
        detail = f"{snap.get('mode_title') or mode} → {temperature_compact(target)}"
    else:
        detail = str(snap.get("mode_title") or mode)
    progress = 0.0
    if mode == "holding":
        progress = 1.0
    elif mode == "heating" and target:
        try:
            progress = max(0.0, min(1.0, float(current["value"]) / float(target["value"])))
        except (TypeError, ValueError, ZeroDivisionError):
            progress = 0.35
    return {
        "id": "kettle",
        "glyph": "flame" if hot else "mug",
        "label": label,
        "detail": detail,
        "hot": hot,
        "face": True,
        "progress": progress,
        "actions": [
            {"label": f"Heat {DEFAULT_HEAT_C}", "argv": ["kettle", "heat", DEFAULT_HEAT_C]},
            {"label": "Off", "argv": ["kettle", "off"]},
        ],
    }


def snapshot_payload(host: str | None, timeout: float, *, probed: bool) -> dict:
    out: dict = {
        "id": ADAPTER_ID,
        "backend": ADAPTER_ID,
        "available": True,
        "reachable": False,
        "host": host,
        "temp_c": None,
        "target_c": None,
        "mode": None,
        "mode_raw": None,
        "mode_title": None,
        "hot": False,
    }
    if not host or not probed:
        if not host:
            out["error"] = "no kettle host configured"
        return out
    state = send_command(host, "state", timeout)
    if not state.get("ok"):
        out["error"] = state.get("error") or "unreachable"
        return out
    snap = snapshot_from(state["parsed"])
    current = snap.get("current")
    target = snap.get("target")
    settings = {}
    settings_res = send_command(host, "prtsettings", timeout)
    if settings_res.get("ok"):
        settings = settings_from_body(settings_res["parsed"].get("stripped") or "")
    out.update(
        {
            "reachable": True,
            "temp_c": int(round(float(current["value"]))) if current else None,
            "target_c": int(round(float(target["value"]))) if target else None,
            "mode": snap.get("mode"),
            "mode_raw": snap.get("mode_raw"),
            "mode_title": snap.get("mode_title"),
            "hot": bool(snap.get("is_active")),
            "altitude_m": settings.get("altitude_meters"),
        }
    )
    slot = slot_from_snapshot(snap, host=host)
    if slot:
        out["slot"] = slot
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", nargs="?", default="info", help="info | status | heat | on | off | host")
    parser.add_argument("temp", nargs="?", help="celsius for heat")
    parser.add_argument("--host", default="", help="kettle IP or http://host[:port]")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-default-host", action="store_true", help="do not fall back to 192.168.3.36")
    parser.add_argument("--desk-switch-manifest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.desk_switch_manifest or args.verb == "--desk-switch-manifest":
        print(json.dumps(manifest(), indent=2))
        return 0
    verb = str(args.verb).strip().lower()
    timeout = float(args.timeout)
    if verb in ("info", "status", "probe"):
        use_default = verb != "info" and not args.no_default_host
        host = resolve_host_spec(args.host, use_default=use_default)
        data = snapshot_payload(host, timeout, probed=bool(host))
        print(json.dumps(data, indent=2))
        return 0 if data.get("reachable") or not host else 1
    if verb == "host":
        if args.temp:
            path = save_host(args.temp)
            print(json.dumps({"ok": True, "host": args.temp.strip(), "path": str(path)}, indent=2))
            return 0
        host = resolve_host_spec(args.host, use_default=True)
        print(json.dumps({"ok": True, "host": host, "path": str(kettle_host_path()) if kettle_host_path() else None}, indent=2))
        return 0
    host = resolve_host_spec(args.host, use_default=True)
    if not host:
        print(json.dumps({"ok": False, "error": "no kettle host"}, indent=2))
        return 1
    if verb == "on":
        result = send_command(host, "heaton", max(timeout, CONTROL_TIMEOUT_S))
        print(json.dumps({"ok": bool(result.get("ok")), "host": host, "error": result.get("error")}, indent=2))
        return 0 if result.get("ok") else 1
    if verb == "off":
        result = send_sequence(host, off_commands(), max(timeout, CONTROL_TIMEOUT_S))
        print(json.dumps({"ok": bool(result.get("ok")), "host": host, "error": result.get("error")}, indent=2))
        return 0 if result.get("ok") else 1
    if verb == "heat":
        temp = parse_temp(args.temp or DEFAULT_HEAT_C)
        result = send_sequence(host, heat_commands(temp), max(timeout, CONTROL_TIMEOUT_S))
        print(json.dumps({"ok": bool(result.get("ok")), "host": host, "temp": temp, "error": result.get("error")}, indent=2))
        return 0 if result.get("ok") else 1
    raise SystemExit(f"unknown verb: {args.verb}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
