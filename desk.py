#!/usr/bin/env python3
"""desk — one CLI + adapters + shared tray/HUD slots.

Core orchestrates. Shells only paint. Hardware plugs in as adapters:

    mouse      — host hop (`mouse.host_switch`; reference: mxswitch)
    keyboard   — presence / follow (`keyboard.presence`; reference: hhkb)
    hosts      — configured host ids, channels, follow target
                 (`mac` / `linux` stay the defaults; any other id is allowed)
    display    — input + PBP/full + OS layout (reference: lgdualup)
                 `adapters.dualup` is a legacy alias of `display`
    smarthome  — list devices + light on/off (reference: alexa)
    kettle     — Fellow-style LAN kettle (reference: kettle)
    weather    — Open-Meteo ambient ° + altitude (reference: weather)
    wallpaper  — per-output image (reference: wallpaper). Off unless configured.

Reference adapters live under `adapters/` and install to
`~/.local/lib/desk/` (`$DESK_LIB`). Older libdir and config paths still
load for one release. Third parties drop a binary +
`*.manifest.json` (`api_version: 1`) in that libdir, or `desk-<id>`
on PATH. `mxswitch` / `lgdualup` on PATH stay shims.

    desk status
    desk status --json
    desk to mac
    desk to linux
    desk to <host>         # any id configured under adapters.hosts
    desk switch 2          # mouse only, Easy-Switch channel
    desk switch mac        # same as: to mac
    desk pbp <mode>
    desk full
    desk layout <name>     # named monitors: full host, or PBP top/bottom
    desk layout            # re-apply full or PBP from the live display
    desk smarthome list     # smart-home devices (adapter)
    desk smarthome status
    desk smarthome on|off   # desk light via the bound adapter
    desk kettle status      # Fellow-style LAN kettle (adapter)
    desk kettle heat 93
    desk kettle off
    desk weather status     # Open-Meteo ambient (adapter)
    desk wallpaper apply    # per-output wallpaper (no-op unless configured)
    desk watch              # keyboard arrives → desk to this host
    desk watch --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

SYSTEM = platform.system()
HERE = Path(__file__).resolve().parent
VERSION = "1.10.0"
ADAPTER_API_VERSION = 1
LAYOUT_FULL_MODES = ("full", "off", "none", "solo")
PBP_INPUT_DEFAULTS = {"linux": "dp", "mac": "hdmi1"}  # Studio HDMI1, Omarchy DisplayPort
PBP_INPUT_ORDER = ("linux", "mac")  # secondary first, primary last
LAYOUT_DEFAULT_RETRIES = 16  # EDID after PBP+inputs can lag several seconds
LAYOUT_DEFAULT_DELAY_S = 0.5
LAYOUT_DEFAULT_SETTLE_S = 0.5
WALLPAPER_POLL_S = 2.0
WALLPAPER_DEBOUNCE_S = 2.0
HHKB_VID_DEFAULT = 0x04FE
HHKB_PID_DEFAULT = 0x0016
MOUSE_CACHE_TTL_S = 24 * 3600  # last-known channel until contradicted (wake lag / other host)
PEER_CACHE_TTL_S = 20.0
# Relay never waits on a password prompt. ConnectTimeout matches the
# brief the display-input hop can spend on a dead peer.
SSH_RELAY_ARGS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=3"]
RELAY_TIMEOUT_S = 12.0
# Adapters report "device absent" with a non-zero exit and text such as
# "not found" or a line "No <device> found". Other failures stay on this host.
_ABSENT_RE = re.compile(r"not found|^\s*no\b.+\bfound\b", re.I | re.M)
HOST_ALIASES = {
    "mac": "mac",
    "macos": "mac",
    "darwin": "mac",
    "studio": "mac",
    "linux": "linux",
    "lnx": "linux",
    "omarchy": "linux",
}
# Keys on adapters.hosts that are desk settings, not host entries.
HOST_MAP_META = {
    "this_host",
    "follow_channel",
    "follow_hhkb_usb",
    "follow_keyboard_arrival",
    "arrival_moves_display",
    "push_on_leave",
    "peer",
    "watch",
    "order",
    "target_channel",
}
LEGACY_HOST_IDS = ("mac", "linux")
HOST_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
HINT_FOR_HOST = {"mac": "MAC", "linux": "LNX"}
CHANNEL_RE = re.compile(r"currently on\s+(\d+)", re.I)

CONFIG_CANDIDATES = (
    Path.home() / ".config" / "desk" / "config.json",
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
    "wallpaper": ("wallpaper.apply",),
}
ROLE_REFERENCE_ID = {
    "mouse": "mxswitch",
    "display": "lgdualup",
    "keyboard": "hhkb",
    "smarthome": "alexa",
    "kettle": "kettle",
    "weather": "weather",
    "wallpaper": "wallpaper",
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
    "wallpaper": {
        "api_version": ADAPTER_API_VERSION,
        "id": "wallpaper",
        "name": "Per-output wallpaper",
        "capabilities": ["wallpaper.apply"],
    },
}


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}", flush=True)


def default_this_host() -> str:
    return "mac" if SYSTEM == "Darwin" else "linux"


def normalize_host(name: str) -> str:
    """Canonical host id.

    `mac` and `linux` keep their aliases (`macos`, `omarchy`, …). Any other
    id is kept, lowercased. Empty names and names with spaces are rejected.
    Whether the id is configured is checked later, against the host map.
    """
    key = str(name).strip().lower()
    if not key:
        raise SystemExit(f"unknown host: {name}")
    if key in HOST_ALIASES:
        return HOST_ALIASES[key]
    if HOST_ID_RE.fullmatch(key):
        return key
    raise SystemExit(f"unknown host: {name}")


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
        "follow_keyboard_arrival": True,
        "arrival_moves_display": True,
        "push_on_leave": None,
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
    if cfg["this_host"] not in cfg["hosts"]:
        known = ", ".join(cfg["hosts"]) or "none"
        raise SystemExit(
            f"this_host {cfg['this_host']} is not configured in adapters.hosts ({known})"
        )
    if cfg.get("push_on_leave") is None:
        cfg["push_on_leave"] = default_push_on_leave(cfg)
    else:
        cfg["push_on_leave"] = bool(cfg["push_on_leave"])
    if isinstance(user.get("adapters"), dict):
        cfg["adapters"] = user["adapters"]
    cfg["_layouts"] = normalize_layouts(user)
    return cfg


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _user_set_follow_channel(user: dict) -> bool:
    if "target_channel" in user:
        return True
    hosts_ad = _as_dict(_as_dict(user.get("adapters")).get("hosts"))
    return "follow_channel" in hosts_ad


def _cfg_bool(user: dict, hosts_ad: dict, key: str) -> bool | None:
    """Bool from the top-level file or `adapters.hosts`. Unset is None."""
    if key in user:
        return bool(user[key])
    if key in hosts_ad:
        return bool(hosts_ad[key])
    return None


def configured_host_count(cfg: dict) -> int:
    hosts = cfg.get("hosts") if isinstance(cfg.get("hosts"), dict) else {}
    return sum(1 for spec in hosts.values() if isinstance(spec, dict))


def default_push_on_leave(cfg: dict) -> bool:
    """Leave still pushes the mouse on a one- or two-host file.

    Three or more hosts cannot tell where the keyboard went, so the
    default is off and the host that sees the arrival pulls instead.
    """
    return configured_host_count(cfg) < 3


def display_adapter_cfg(container: dict) -> dict:
    """`adapters.display` overlays legacy `adapters.dualup` (same role)."""
    adapters = _as_dict(container.get("adapters"))
    return {**_as_dict(adapters.get("dualup")), **_as_dict(adapters.get("display"))}


def tray_density(cfg: dict) -> str:
    raw = str(cfg.get("_tray_density") or "strip").strip().lower()
    return "chips" if raw == "chips" else "strip"


def hud_density(cfg: dict) -> str:
    raw = str(cfg.get("_hud_density") or "regular").strip().lower()
    return "compact" if raw == "compact" else "regular"


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


# Quiet default extra: weather → kettle → DualUp. Lights stay off until opted in.
DEFAULT_TRAY_SLOT_IDS = ("weather", "kettle", "dualup")
KNOWN_TRAY_SLOT_IDS = ("weather", "kettle", "dualup", "lights")
KNOWN_SLOT_KINDS = ("face", "chip", "toggle", "mode", "host")
# Shells paint by `kind`. Ids only seed the v1 registry when an adapter
# omits it — a new espresso slot can set `"kind": "face"` and the HUD
# learns the flavor without a Swift/QML special case.
SLOT_KIND_BY_ID = {
    "weather": "chip",
    "kettle": "face",
    "dualup": "mode",
    "lights": "toggle",
    "host": "host",
}
ALTITUDE_DETAIL_RE = re.compile(r"^\d+m$")


def resolve_slot_kind(slot_id: str, raw_kind: object = None) -> str:
    kind = str(raw_kind or "").strip().lower()
    if kind in KNOWN_SLOT_KINDS:
        return kind
    return SLOT_KIND_BY_ID.get(str(slot_id or "").strip(), "chip")


def default_tray_slot_prefs(*, lights: bool = False) -> list[dict]:
    return [
        {"id": "weather", "enabled": True, "kind": "chip", "show_altitude": True},
        {"id": "kettle", "enabled": True, "kind": "face"},
        {"id": "dualup", "enabled": True, "kind": "mode"},
        {"id": "lights", "enabled": bool(lights), "kind": "toggle"},
    ]


def parse_slot_pref(raw: object) -> dict | None:
    """Accept a pin string (`\"kettle\"`) or `{id, enabled, kind?, …}` object."""
    if isinstance(raw, str):
        slot_id = raw.strip()
        if not slot_id:
            return None
        return {"id": slot_id, "enabled": True, "kind": resolve_slot_kind(slot_id)}
    if not isinstance(raw, dict):
        return None
    slot_id = str(raw.get("id") or "").strip()
    if not slot_id:
        return None
    pref: dict = {
        "id": slot_id,
        "enabled": _as_bool(raw.get("enabled"), True),
        "kind": resolve_slot_kind(slot_id, raw.get("kind")),
    }
    if "show_altitude" in raw:
        pref["show_altitude"] = _as_bool(raw.get("show_altitude"), True)
    return pref


def parse_tray_slot_list(raw: object) -> tuple[list[dict] | None, bool, bool]:
    """Return (prefs, exclusive, from_user).

    A string pin list is exclusive (only those ids). An object list is
    order + visibility; unknown available slots append after it.
    Missing / empty → defaults at compose time.
    """
    if not isinstance(raw, list) or not raw:
        return None, False, False
    prefs: list[dict] = []
    exclusive = True
    for item in raw:
        if isinstance(item, dict):
            exclusive = False
        pref = parse_slot_pref(item)
        if pref:
            prefs.append(pref)
    if not prefs:
        return None, False, False
    return prefs, exclusive, True


def parse_ui_section(ui: object) -> dict:
    """Normalize `ui` from config.json. Shared by core, Mac, and Omarchy."""
    raw = _as_dict(ui)
    tray = _as_dict(raw.get("tray"))
    hud = _as_dict(raw.get("hud"))
    slots, exclusive, from_user = parse_tray_slot_list(tray.get("slots"))
    lights = _as_bool(tray.get("lights"), False)
    if slots and not exclusive:
        for pref in slots:
            if pref["id"] == "lights" and pref["enabled"]:
                lights = True
    density = str(tray.get("density") or "strip").strip().lower()
    hud_raw = str(hud.get("density") or "regular").strip().lower()
    show_altitude = _as_bool(hud.get("show_altitude"), True)
    if slots:
        for pref in slots:
            if pref.get("kind") == "chip" and "show_altitude" in pref:
                show_altitude = bool(pref["show_altitude"])
                break
    return {
        "tray_density": "chips" if density == "chips" else "strip",
        "tray_lights": lights,
        "slots": slots,
        "slots_exclusive": exclusive,
        "slots_from_user": from_user,
        "hud_show_altitude": show_altitude,
        "hud_show_faces": _as_bool(hud.get("show_faces"), True),
        "hud_density": "compact" if hud_raw == "compact" else "regular",
    }


def apply_ui_section(cfg: dict, ui: object) -> dict:
    parsed = parse_ui_section(ui)
    cfg["_tray_density"] = parsed["tray_density"]
    cfg["_tray_lights"] = parsed["tray_lights"]
    cfg["_hud_show_altitude"] = parsed["hud_show_altitude"]
    cfg["_hud_show_faces"] = parsed["hud_show_faces"]
    cfg["_hud_density"] = parsed["hud_density"]
    if parsed["slots_from_user"] and parsed["slots"]:
        cfg["_ui_slots"] = parsed["slots"]
        cfg["_ui_slots_exclusive"] = parsed["slots_exclusive"]
        cfg["_tray_slots"] = [item["id"] for item in parsed["slots"] if item["enabled"]]
    return cfg


def public_slot_prefs(cfg: dict) -> list[dict]:
    """Catalog the settings UI / status.ui.tray.slots persist and paint from."""
    lights = bool(cfg.get("_tray_lights"))
    catalog = default_tray_slot_prefs(lights=lights)
    prefs = cfg.get("_ui_slots")
    exclusive = bool(cfg.get("_ui_slots_exclusive"))
    if not isinstance(prefs, list) or not prefs:
        wanted = cfg.get("_tray_slots")
        if isinstance(wanted, list) and wanted:
            prefs = [{"id": str(item), "enabled": True} for item in wanted if str(item).strip()]
            exclusive = True
        else:
            return catalog
    out = [_public_slot_pref(item, cfg) for item in prefs]
    seen = {item["id"] for item in out}
    if exclusive:
        for item in catalog:
            if item["id"] not in seen:
                hidden = _public_slot_pref(item, cfg)
                hidden["enabled"] = False
                out.append(hidden)
        return out
    for item in catalog:
        if item["id"] not in seen:
            out.append(_public_slot_pref(item, cfg))
    return out


def _public_slot_pref(item: dict, cfg: dict) -> dict:
    slot_id = str(item.get("id") or "")
    kind = resolve_slot_kind(slot_id, item.get("kind"))
    out: dict = {
        "id": slot_id,
        "enabled": bool(item.get("enabled", True)),
        "kind": kind,
    }
    if kind == "chip":
        if "show_altitude" in item:
            out["show_altitude"] = _as_bool(item.get("show_altitude"), True)
        else:
            out["show_altitude"] = _as_bool(cfg.get("_hud_show_altitude"), True)
    return out


def format_public_ui(cfg: dict) -> dict:
    slots = public_slot_prefs(cfg)
    lights = bool(cfg.get("_tray_lights")) or any(
        item["id"] == "lights" and item["enabled"] for item in slots
    )
    chip_alt = next(
        (item["show_altitude"] for item in slots if item.get("kind") == "chip" and "show_altitude" in item),
        _as_bool(cfg.get("_hud_show_altitude"), True),
    )
    return {
        "tray": {
            "density": tray_density(cfg),
            "lights": lights,
            "slots": slots,
        },
        "hud": {
            # Mirror of the first chip slot's show_altitude. Prefer the
            # per-slot key; this stays so older shells keep working.
            "show_altitude": chip_alt,
            "show_faces": _as_bool(cfg.get("_hud_show_faces"), True),
            "density": hud_density(cfg),
        },
    }


def preferred_config_path(existing: str | None = None) -> Path:
    if existing and existing not in ("(defaults)",):
        return Path(existing)
    return Path(CONFIG_CANDIDATES[0])


def write_ui_config(ui: object, path: Path | None = None) -> Path:
    """Merge `ui` into the shared desk config. Leaves adapters alone."""
    dest = Path(path) if path else preferred_config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if dest.is_file():
        try:
            loaded = json.loads(dest.read_text())
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            data = loaded
    parsed = parse_ui_section(ui)
    shadow = {
        "_tray_density": parsed["tray_density"],
        "_tray_lights": parsed["tray_lights"],
        "_hud_show_altitude": parsed["hud_show_altitude"],
        "_hud_show_faces": parsed["hud_show_faces"],
        "_hud_density": parsed["hud_density"],
    }
    if parsed["slots_from_user"] and parsed["slots"]:
        shadow["_ui_slots"] = parsed["slots"]
        shadow["_ui_slots_exclusive"] = parsed["slots_exclusive"]
        shadow["_tray_slots"] = [item["id"] for item in parsed["slots"] if item["enabled"]]
    data["ui"] = format_public_ui(shadow)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(dest)
    return dest


def apply_slot_prefs(available: list[dict], prefs: list[dict] | None, *, exclusive: bool) -> list[dict]:
    """Order + visibility. Exclusive string pins hide anything not listed."""
    by_id: dict[str, dict] = {}
    for slot in available:
        slot_id = str(slot.get("id") or "")
        if slot_id and slot_id not in by_id:
            by_id[slot_id] = slot
    if not prefs:
        return list(available)
    out: list[dict] = []
    used: set[str] = set()
    for pref in prefs:
        slot_id = str(pref.get("id") or "")
        if not slot_id:
            continue
        used.add(slot_id)
        if not pref.get("enabled", True):
            continue
        if slot_id in by_id:
            slot = dict(by_id[slot_id])
            if pref.get("kind"):
                slot["kind"] = resolve_slot_kind(slot_id, pref.get("kind"))
            out.append(slot)
    if exclusive:
        return out
    for slot in available:
        slot_id = str(slot.get("id") or "")
        if slot_id in used or slot_id == "lights":
            continue
        out.append(slot)
    return out


def strip_altitude_from_detail(detail: str) -> str:
    parts = [part.strip() for part in str(detail).split("·")]
    kept = [part for part in parts if part and not ALTITUDE_DETAIL_RE.match(part)]
    return " · ".join(kept)


def _slot_show_altitude(pref: dict | None, cfg: dict) -> bool:
    if pref and "show_altitude" in pref:
        return _as_bool(pref.get("show_altitude"), True)
    return _as_bool(cfg.get("_hud_show_altitude"), True)


def apply_hud_prefs(slots: list[dict], cfg: dict | None = None) -> list[dict]:
    cfg = cfg or {}
    prefs = cfg.get("_ui_slots") if isinstance(cfg.get("_ui_slots"), list) else []
    by_id = {str(item.get("id") or ""): item for item in prefs if isinstance(item, dict)}
    show_faces = _as_bool(cfg.get("_hud_show_faces"), True)
    out: list[dict] = []
    for slot in slots:
        item = dict(slot)
        slot_id = str(item.get("id") or "")
        pref = by_id.get(slot_id)
        item["kind"] = resolve_slot_kind(slot_id, (pref or {}).get("kind") or item.get("kind"))
        if item["kind"] == "chip" and not _slot_show_altitude(pref, cfg):
            detail = strip_altitude_from_detail(str(item.get("detail") or ""))
            if detail:
                item["detail"] = detail
            else:
                item.pop("detail", None)
        if not show_faces:
            item.pop("face", None)
        out.append(item)
    return out


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
    wallpaper = _as_dict(adapters.get("wallpaper"))
    dual = display_adapter_cfg(user)
    ui = _as_dict(user.get("ui"))

    cfg["_mouse_enabled"] = bool(mouse.get("enabled", True))
    if mouse.get("backend"):
        cfg["_mouse_backend"] = str(mouse["backend"])
    if mouse.get("path"):
        cfg["mxswitch"] = str(mouse["path"])
        cfg["_mouse_path"] = str(mouse["path"])
    if "relay_hosts" in mouse:
        cfg["_mouse_relay_hosts"] = _coerce_relay_hosts(mouse.get("relay_hosts"))
    mouse_remote = str(mouse.get("relay_path") or "").strip()
    if mouse_remote:
        cfg["_mouse_relay_path"] = mouse_remote

    if hosts_ad.get("this_host"):
        cfg["this_host"] = hosts_ad["this_host"]
    if hosts_ad.get("follow_channel") is not None:
        cfg["target_channel"] = hosts_ad["follow_channel"]
    if hosts_ad.get("peer"):
        cfg["_peer"] = str(hosts_ad["peer"])
    follow_usb = _cfg_bool(user, hosts_ad, "follow_hhkb_usb")
    cfg["follow_hhkb_usb"] = True if follow_usb is None else follow_usb
    follow_arrival = _cfg_bool(user, hosts_ad, "follow_keyboard_arrival")
    cfg["follow_keyboard_arrival"] = True if follow_arrival is None else follow_arrival
    moves_display = _cfg_bool(user, hosts_ad, "arrival_moves_display")
    cfg["arrival_moves_display"] = True if moves_display is None else moves_display
    # None until the host map is resolved: 3+ hosts default this off.
    cfg["push_on_leave"] = _cfg_bool(user, hosts_ad, "push_on_leave")

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

    # Off unless the user opts in. Missing section does nothing.
    wallpaper_outputs = wallpaper.get("outputs") if isinstance(wallpaper.get("outputs"), dict) else {}
    cfg["_wallpaper_config"] = {
        "enabled": bool(wallpaper.get("enabled", False)),
        "landscape": str(wallpaper.get("landscape") or ""),
        "portrait": str(wallpaper.get("portrait") or ""),
        "exact_dir": str(wallpaper.get("exact_dir") or ""),
        "exact_prefix": str(wallpaper.get("exact_prefix") or ""),
        "primary_output": str(wallpaper.get("primary_output") or ""),
        "outputs": wallpaper_outputs,
    }
    cfg["_wallpaper_enabled"] = cfg["_wallpaper_config"]["enabled"]
    if wallpaper.get("backend"):
        cfg["_wallpaper_backend"] = str(wallpaper["backend"])
    if wallpaper.get("path"):
        cfg["_wallpaper_path"] = str(wallpaper["path"])

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
    cfg["_display_relay_hosts"] = _coerce_relay_hosts(dual.get("relay_hosts"))
    relay_path = str(dual.get("relay_path") or "").strip()
    if relay_path:
        cfg["_display_relay_path"] = relay_path
    cfg["_dualup_layout_retries"] = as_int(dual.get("layout_retries"), LAYOUT_DEFAULT_RETRIES)
    raw_delay = dual.get("layout_retry_delay_s")
    cfg["_dualup_layout_retry_delay_s"] = float(
        LAYOUT_DEFAULT_DELAY_S if raw_delay is None else raw_delay
    )
    raw_settle = dual.get("layout_settle_s")
    cfg["_dualup_layout_settle_s"] = float(
        LAYOUT_DEFAULT_SETTLE_S if raw_settle is None else raw_settle
    )
    apply_ui_section(cfg, ui)
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
    """One host record. `channel` and `mouse_channel` are the same slot."""
    if isinstance(raw, bool):
        return {}
    if isinstance(raw, int):
        return {"channel": raw, "mouse_channel": raw}
    if isinstance(raw, str) and str(raw).strip().lstrip("-").isdigit():
        channel = int(str(raw).strip())
        return {"channel": channel, "mouse_channel": channel}
    if not isinstance(raw, dict):
        return {}
    out = dict(raw)
    if out.get("channel") not in (None, ""):
        out["channel"] = as_int(out["channel"], 0)
    elif out.get("mouse_channel") not in (None, ""):
        out["channel"] = as_int(out["mouse_channel"], 0)
    if "channel" in out:
        out["mouse_channel"] = out["channel"]
    if out.get("keyboard_slot") not in (None, ""):
        out["keyboard_slot"] = as_int(out["keyboard_slot"], 0)
    for key in ("input", "upstream", "ssh", "pre_switch", "peer", "hint", "dualup_input"):
        if out.get(key) is not None:
            out[key] = str(out[key]).strip()
    if not str(out.get("dualup_input") or "").strip() and str(out.get("input") or "").strip():
        out["dualup_input"] = str(out["input"]).strip()
    return out


def _ordered_host_ids(ids: list[str], order_raw: object) -> list[str]:
    """Apply optional `order`. Ids not listed keep their relative order at the end."""
    if order_raw is None:
        return list(ids)
    if isinstance(order_raw, str):
        requested = [part.strip() for part in order_raw.split(",")]
    elif isinstance(order_raw, list):
        requested = [str(part).strip() for part in order_raw]
    else:
        return list(ids)
    known = set(ids)
    out: list[str] = []
    seen: set[str] = set()
    for name in requested:
        if not name:
            continue
        try:
            key = normalize_host(name)
        except SystemExit:
            continue
        if key not in known or key in seen:
            continue
        seen.add(key)
        out.append(key)
    for key in ids:
        if key not in seen:
            out.append(key)
    return out


def _reject_duplicate_channels(hosts: dict) -> None:
    seen: dict[int, str] = {}
    for name, spec in hosts.items():
        channel = _as_dict(spec).get("channel")
        if not channel:
            continue
        channel_i = int(channel)
        prev = seen.get(channel_i)
        if prev is not None:
            raise SystemExit(f"hosts {prev} and {name} both use mouse channel {channel_i}")
        seen[channel_i] = name


def resolve_hosts(cfg: dict, user_hosts: dict | None = None) -> dict:
    """Merge the configured host map.

    Host ids are whatever `adapters.hosts` names. `mac` and `linux` stay the
    defaults, with their aliases, when the file is still that pair (or empty).
    A map that names any other id does not invent `linux` beside it.

    `channel` and `mouse_channel` are one slot. `to <id>` reads it. `watch`
    on keyboard arrival uses this host's slot. Leave uses `target_channel`
    only when `push_on_leave` is on.
    If a legacy two-host file never named the other host, that host inherits
    `target_channel`.
    """
    this_host = normalize_host(cfg.get("this_host") or default_this_host())
    if user_hosts is None:
        supplied = cfg.get("hosts") if isinstance(cfg.get("hosts"), dict) else {}
    else:
        supplied = user_hosts if isinstance(user_hosts, dict) else {}

    order_raw = supplied.get("order")
    named: set[str] = set()
    entries: dict[str, dict] = {}
    order_keys: list[str] = []
    for name, raw in supplied.items():
        if str(name) in HOST_MAP_META:
            continue
        try:
            key = normalize_host(str(name))
        except SystemExit:
            continue
        entry = _host_entry(raw)
        if key not in entries:
            order_keys.append(key)
        entries[key] = {**entries.get(key, {}), **entry}
        if "channel" in entry:
            named.add(key)

    legacy = set(order_keys).issubset(set(LEGACY_HOST_IDS))
    if legacy:
        merged = {
            "mac": {"channel": 1, "mouse_channel": 1},
            "linux": {"channel": 2, "mouse_channel": 2},
        }
        for key in order_keys:
            merged[key] = {**merged.get(key, {}), **entries[key]}
        if this_host in LEGACY_HOST_IDS:
            other = "mac" if this_host == "linux" else "linux"
            if other not in named and cfg.get("target_channel") is not None:
                channel = as_int(cfg["target_channel"], merged[other]["channel"])
                merged[other] = {**merged[other], "channel": channel, "mouse_channel": channel}
        merged = {key: merged[key] for key in _ordered_host_ids(list(merged), order_raw)}
    else:
        ordered = _ordered_host_ids(order_keys, order_raw)
        merged = {key: entries[key] for key in ordered}
    _reject_duplicate_channels(merged)
    return merged


def host_spec(cfg: dict, host: str) -> tuple[str, dict]:
    """Return `(id, entry)` for a configured host, or exit."""
    host_id = normalize_host(host)
    hosts = cfg.get("hosts") if isinstance(cfg.get("hosts"), dict) else {}
    spec = hosts.get(host_id)
    if not isinstance(spec, dict):
        known = ", ".join(name for name, item in hosts.items() if isinstance(item, dict)) or "none"
        raise SystemExit(f"unknown host: {host_id} (configured: {known})")
    return host_id, spec


def host_channel(cfg: dict, host: str) -> int:
    host_id, spec = host_spec(cfg, host)
    channel = spec.get("channel")
    if not channel:
        channel = spec.get("mouse_channel")
    if not channel:
        raise SystemExit(f"no Easy-Switch channel configured for host {host_id}")
    return int(channel)


def host_hint(cfg: dict, host: str | None) -> str:
    """Strip token. `mac` / `linux` stay `MAC` / `LNX`. Others use `hint` or the id."""
    if not host:
        return "?"
    try:
        host_id = normalize_host(str(host))
    except SystemExit:
        host_id = str(host).strip()
    if host_id in HINT_FOR_HOST:
        return HINT_FOR_HOST[host_id]
    spec = _as_dict((cfg.get("hosts") or {}).get(host_id))
    custom = str(spec.get("hint") or "").strip()
    return custom or host_id


def public_host_list(cfg: dict) -> list[dict]:
    """Ordered host records for status. Additive; the host map itself stays a map."""
    out: list[dict] = []
    for name, raw in (cfg.get("hosts") or {}).items():
        if not isinstance(raw, dict):
            continue
        spec = raw
        item: dict = {
            "id": name,
            "channel": spec.get("channel"),
            "hint": host_hint(cfg, name),
        }
        if spec.get("mouse_channel") not in (None, ""):
            item["mouse_channel"] = spec.get("mouse_channel")
        if spec.get("keyboard_slot") not in (None, ""):
            item["keyboard_slot"] = spec.get("keyboard_slot")
        display_input = str(spec.get("dualup_input") or spec.get("input") or "").strip()
        if display_input:
            item["input"] = display_input
        for key in ("upstream", "ssh", "pre_switch", "peer"):
            text = str(spec.get(key) or "").strip()
            if text:
                item[key] = text
        out.append(item)
    return out


def format_host_summary(state: dict) -> str:
    """One status line: this host, follow channel, then every host's mouse channel."""
    hosts = state.get("hosts") if isinstance(state.get("hosts"), dict) else {}
    this = state.get("this_host")
    follow = state.get("target_channel")
    if follow is None:
        follow = hosts.get("follow_channel")
    parts = [f"this_host={this}", f"follow_channel={follow}"]
    names = [name for name in LEGACY_HOST_IDS if isinstance(hosts.get(name), dict)]
    names.extend(
        name
        for name, spec in hosts.items()
        if name not in HOST_MAP_META and name not in LEGACY_HOST_IDS and isinstance(spec, dict)
    )
    for name in names:
        parts.append(f"{name}={_as_dict(hosts.get(name)).get('channel', '?')}")
    return "  ".join(parts)


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
    preferred = root / "desk"
    legacy = root / "desk-switch"
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


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
            return host_hint(cfg, host), source
    if hhkb_present:
        return host_hint(cfg, str(cfg.get("this_host") or "")), "hhkb"
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


def wallpaper_source_path() -> Path | None:
    return python_adapter_source_path("wallpaper")


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
    """Preferred adapter libdir. `$DESK_LIB` / `$DESK_SWITCH_LIB` win when set."""
    env = os.environ.get("DESK_LIB") or os.environ.get("DESK_SWITCH_LIB")
    if env:
        return Path(env).expanduser()
    preferred = Path.home() / ".local" / "lib" / "desk"
    legacy = Path.home() / ".local" / "lib" / "desk-switch"
    if preferred.is_dir() or not legacy.is_dir():
        return preferred
    return legacy


def libexec_dirs() -> list[Path]:
    """Search preferred libdir first, then the legacy desk-switch path."""
    primary = libexec_dir()
    seen = [primary]
    if os.environ.get("DESK_LIB") or os.environ.get("DESK_SWITCH_LIB"):
        return seen
    for extra in (
        Path.home() / ".local" / "lib" / "desk",
        Path.home() / ".local" / "lib" / "desk-switch",
    ):
        if extra not in seen:
            seen.append(extra)
    return seen


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


def _libexec_candidates(backend: str) -> list[Path]:
    out: list[Path] = []
    for lib in libexec_dirs():
        out.extend((lib / backend, lib / backend / backend))
    return out


def resolve_backend_id(backend: str) -> Path | None:
    """Resolve an adapter id: libdir/<id>, libdir/<id>/<id>, PATH desk-<id>, PATH desk-switch-<id>, PATH <id>."""
    for candidate in _libexec_candidates(backend):
        if _is_exe(candidate):
            return candidate
    for prefix in (f"desk-{backend}", f"desk-switch-{backend}"):
        prefixed = which_cmd(prefix)
        if prefixed:
            return prefixed
    return which_cmd(backend)


def which_adapter(backend: str, configured: str | None = None) -> Path | None:
    """Resolve an adapter helper: explicit path, then libexec, then PATH."""
    if configured:
        expanded = Path(str(configured)).expanduser()
        if _is_exe(expanded):
            return expanded
    for candidate in _libexec_candidates(backend):
        if _is_exe(candidate):
            return candidate
    if configured:
        found = which_cmd(str(configured))
        if found:
            return found
        if Path(str(configured)).name != backend:
            return None
    for prefix in (f"desk-{backend}", f"desk-switch-{backend}"):
        prefixed = which_cmd(prefix)
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
    candidates: list[Path] = []
    for lib in libexec_dirs():
        candidates.extend(
            [
                lib / f"{adapter_id}.manifest.json",
                lib / adapter_id / "manifest.json",
            ]
        )
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
    found: dict[str, dict] = {}
    roots = [lib] if lib is not None else libexec_dirs()
    for root in roots:
        if root is None or not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
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
            if adapter_id in found:
                continue
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
    if role == "wallpaper":
        return bool(cfg.get("_wallpaper_enabled", False))
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
    if role == "wallpaper":
        return cfg.get("_wallpaper_backend"), cfg.get("_wallpaper_path") or None
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
        if role == "wallpaper":
            resolved = resolve_backend_id(reference) or wallpaper_source_path()
            cache[role] = _binding("wallpaper", resolved, "reference" if resolved else "missing", enabled=True)
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
        if path is None and adapter_id == "wallpaper":
            path = wallpaper_source_path()
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


def mouse_adapter_id(cfg: dict) -> str:
    """Bound mouse-adapter id. The relay invokes that id on the peer."""
    bound = bind_role(cfg, "mouse")
    adapter_id = str(bound.get("id") or cfg.get("_mouse_backend") or "").strip()
    if adapter_id:
        return adapter_id
    path = bound.get("path")
    if path:
        return Path(path).name
    return "mxswitch"


def mouse_relay_path(cfg: dict) -> str | None:
    text = str(cfg.get("_mouse_relay_path") or "").strip()
    return text or None


def mouse_relay_host_ids(cfg: dict) -> list[str]:
    """Ordered peers for a mouse hop.

    `adapters.mouse.relay_hosts` wins, including an explicit empty list.
    Otherwise the display `relay_hosts` list is reused.
    """
    if "_mouse_relay_hosts" in cfg:
        return _coerce_relay_hosts(cfg.get("_mouse_relay_hosts"))
    return display_relay_host_ids(cfg)


def wallpaper_adapter_path(cfg: dict) -> Path | None:
    if not cfg.get("_wallpaper_enabled", False):
        return None
    return role_adapter_path(cfg, "wallpaper", "_wallpaper_enabled")


def wallpaper_installed_path(cfg: dict) -> Path | None:
    """Binary for status, including when the role is left disabled."""
    if cfg.get("_wallpaper_enabled", False):
        return wallpaper_adapter_path(cfg)
    pinned = cfg.get("_wallpaper_path")
    if pinned:
        expanded = Path(str(pinned)).expanduser()
        if _is_exe(expanded):
            return expanded
    backend = str(cfg.get("_wallpaper_backend") or "wallpaper")
    return resolve_backend_id(backend) or wallpaper_source_path()


def wallpaper_adapter_cmd(cfg: dict, *verbs: str) -> list[str] | None:
    path = wallpaper_adapter_path(cfg)
    if path is None:
        return None
    cmd = [str(path), *[str(verb) for verb in verbs]]
    raw = cfg.get("_wallpaper_config")
    if isinstance(raw, dict):
        cmd.extend(["--config-json", json.dumps(raw)])
    return cmd


def invoke_wallpaper(cfg: dict, verb: str, *extra: str, timeout: float = 8.0) -> dict | None:
    cmd = wallpaper_adapter_cmd(cfg, verb, *extra)
    if cmd is None:
        return None
    return invoke_adapter_cmd(cmd, timeout=timeout)


def wallpaper_signature_from_outputs(outputs: object) -> str:
    parts: list[tuple[str, int, int]] = []
    if not isinstance(outputs, list):
        return ""
    for item in outputs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        width = item.get("logical_width")
        if width is None:
            width = item.get("width") or 0
        height = item.get("logical_height")
        if height is None:
            height = item.get("height") or 0
        try:
            parts.append((name, int(width), int(height)))
        except (TypeError, ValueError):
            continue
    parts.sort()
    return "|".join(f"{name}:{width}x{height}" for name, width, height in parts)


def wallpaper_geometry_signature(cfg: dict) -> tuple[str | None, str | None]:
    data = invoke_wallpaper(cfg, "geometry", timeout=5.0)
    if data is None:
        return None, "wallpaper adapter not found"
    if data.get("ok") is False:
        return None, str(data.get("error") or "geometry failed")
    if data.get("signature"):
        return str(data["signature"]), None
    return wallpaper_signature_from_outputs(data.get("outputs") or []), None


def apply_wallpaper(cfg: dict) -> int:
    """Run the adapter. Disabled is a no-op. Failures are logged, not raised."""
    if not cfg.get("_wallpaper_enabled"):
        return 0
    try:
        data = invoke_wallpaper(cfg, "apply", timeout=25.0)
    except Exception as exc:  # noqa: BLE001 — wallpaper must not break desk
        log(f"wallpaper apply failed: {exc}")
        return 1
    if data is None:
        log("wallpaper adapter not found — skipping")
        return 0
    ok = data.get("ok") is not False and data.get("_returncode") in (0, None)
    if data.get("enabled") is False:
        return 0
    if not ok:
        log(str(data.get("error") or data.get("summary") or "wallpaper apply failed"))
        try:
            return int(data.get("_returncode") or 1)
        except (TypeError, ValueError):
            return 1
    log(f"wallpaper apply: {data.get('summary') or 'ok'}")
    return 0


def note_wallpaper(cfg: dict) -> None:
    """Best-effort apply after a layout change. Never changes the caller's result."""
    if not cfg.get("_wallpaper_enabled"):
        return
    try:
        apply_wallpaper(cfg)
    except Exception as exc:  # noqa: BLE001
        log(f"wallpaper apply failed: {exc}")


def wallpaper_poll_due(state: dict, now: float, *, poll_s: float = WALLPAPER_POLL_S) -> bool:
    if state.get("pending") is not None and not state.get("last_error"):
        return True
    try:
        last = float(state.get("last_poll") or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    return (now - last) >= poll_s


def wallpaper_watch_step(
    state: dict,
    signature: str,
    now: float,
    *,
    debounce_s: float = WALLPAPER_DEBOUNCE_S,
) -> bool:
    """Return True once a new geometry signature has been stable for debounce_s.

    The first signature is only a baseline.
    """
    state["last_poll"] = now
    state["last_error"] = None
    baseline = state.get("baseline")
    if baseline is None:
        state["baseline"] = signature
        state["pending"] = None
        return False
    if signature == baseline:
        state["pending"] = None
        state["pending_since"] = None
        return False
    if state.get("pending") != signature:
        state["pending"] = signature
        state["pending_since"] = now
        return False
    try:
        since = float(state.get("pending_since"))
    except (TypeError, ValueError):
        since = now
        state["pending_since"] = now
    if now - since >= debounce_s:
        state["baseline"] = signature
        state["pending"] = None
        state["pending_since"] = None
        return True
    return False


def wallpaper_watch_poll(cfg: dict, state: dict, now: float) -> None:
    if not cfg.get("_wallpaper_enabled"):
        return
    if not wallpaper_poll_due(state, now):
        return
    try:
        signature, error = wallpaper_geometry_signature(cfg)
    except Exception as exc:  # noqa: BLE001
        signature, error = None, str(exc)
    if error or signature is None:
        message = error or "geometry failed"
        if message != state.get("last_error"):
            log(f"wallpaper geometry: {message}")
        state["last_error"] = message
        state["last_poll"] = now
        return
    if wallpaper_watch_step(state, signature, now):
        log("output geometry changed — applying wallpaper")
        apply_wallpaper(cfg)


def switch_mouse(cfg: dict, channel: int | None = None) -> int:
    """Switch the mouse to `channel`, relaying when this host does not have it.

    The local adapter runs first. A non-zero exit that reports the device
    absent is tried on each relay host, stopping at the first success.
    The line printed names the host that performed it.
    """
    if not cfg.get("_mouse_enabled", True):
        print("mouse adapter disabled")
        return 0
    dest = int(cfg["target_channel"] if channel is None else channel)
    relays = mouse_relay_host_ids(cfg)
    explicit = "_mouse_relay_hosts" in cfg
    peers = [host for host in relays if host != _this_host_id(cfg)]
    try:
        path = mxswitch_path(cfg)
    except SystemExit:
        path = None
    # A missing binary is an install error, unless this config asked the
    # mouse role to relay. Falling back to the display list only applies
    # once a local adapter has reported the device absent.
    if path is None and not (explicit and peers):
        mxswitch_path(cfg)
        return 1
    local = [str(path), str(dest)] if path is not None else None
    rc = relay_adapter(
        cfg,
        local,
        adapter_id=mouse_adapter_id(cfg),
        args=[str(dest)],
        label=f"mouse channel {dest}",
        relay_host_ids=relays,
        remote_path=mouse_relay_path(cfg),
    )
    if rc == 0:
        save_mouse_cache(dest)
    return rc


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
    remote = str(adapters.get("peer_layout") or "~/.local/lib/desk/dualup-layout")
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


def _coerce_relay_hosts(raw: object) -> list[str]:
    """Ordered host ids. Aliases collapse; unknown ids are kept as written."""
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, list):
        items = [str(part).strip() for part in raw]
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for name in items:
        if not name:
            continue
        try:
            name = normalize_host(name)
        except SystemExit:
            pass
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _this_host_id(cfg: dict) -> str:
    raw = str(cfg.get("this_host") or "").strip()
    if not raw:
        return ""
    try:
        return normalize_host(raw)
    except SystemExit:
        return raw


def _host_id(host: str) -> str:
    try:
        return normalize_host(host)
    except SystemExit:
        return str(host).strip()


def host_text(cfg: dict, host: str, key: str) -> str:
    """String field on a host entry (`ssh`, `pre_switch`, …). Missing is empty.

    `ssh` falls back to a per-host `peer` string so either name is the relay target.
    """
    spec = _as_dict((cfg.get("hosts") or {}).get(_host_id(host)))
    value = spec.get(key)
    if key == "ssh" and not (isinstance(value, str) and value.strip()):
        value = spec.get("peer")
    if isinstance(value, str):
        return value.strip()
    return ""


def configured_display_input(cfg: dict, host: str) -> str:
    """Input token for `desk to <host>`. Empty means leave the input alone.

    `adapters.display.inputs` / `adapters.dualup.inputs` land on
    `dualup_input`. A host-level `input` is the same field.
    """
    spec = _as_dict((cfg.get("hosts") or {}).get(_host_id(host)))
    for field in ("dualup_input", "input"):
        name = str(spec.get(field) or "").strip()
        if name:
            return name
    return ""


def display_relay_host_ids(cfg: dict) -> list[str]:
    if "_display_relay_hosts" in cfg:
        raw = cfg.get("_display_relay_hosts")
    else:
        raw = display_adapter_cfg(cfg).get("relay_hosts")
    return _coerce_relay_hosts(raw)


def display_relay_path(cfg: dict) -> str | None:
    raw = cfg.get("_display_relay_path")
    if not raw:
        raw = display_adapter_cfg(cfg).get("relay_path")
    text = str(raw or "").strip()
    return text or None


def display_adapter_id(cfg: dict) -> str:
    """Bound display-adapter id. The relay invokes that id on the peer."""
    bound = bind_role(cfg, "display")
    adapter_id = str(bound.get("id") or cfg.get("_display_backend") or "").strip()
    if adapter_id:
        return adapter_id
    path = bound.get("path")
    if path:
        return Path(path).name
    return ""


def adapter_reports_absent(returncode: int, stdout: str = "", stderr: str = "") -> bool:
    """True when an adapter says its device is not on the machine that ran it."""
    if returncode == 0:
        return False
    return _ABSENT_RE.search(f"{stdout or ''}\n{stderr or ''}") is not None


def _shell_token(token: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./~$:-]+", token):
        return token
    return shlex.quote(token)


def remote_adapter_invocation(
    adapter_id: str,
    args: list[str],
    remote_path: str | None = None,
) -> str:
    """Remote shell command: adapter path plus the same arguments.

    Default path is the install location, with `$HOME` left for the peer
    shell to expand. `relay_path` overrides it.
    """
    if remote_path:
        binary = remote_path.strip()
    else:
        ident = adapter_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", ident):
            ident = shlex.quote(ident)
        binary = f"$HOME/.local/lib/desk/{ident}"
    return " ".join([_shell_token(binary), *(shlex.quote(str(arg)) for arg in args)])


def ssh_argv(target: str, remote_command: str) -> list[str]:
    return ["ssh", *SSH_RELAY_ARGS, target, remote_command]


def run_pre_switch(cfg: dict, host: str) -> int:
    """Run the target host's `pre_switch` command before an input change.

    The local host runs it in a shell. Any other host runs it over SSH
    using that host's `ssh` target.
    """
    command = host_text(cfg, host, "pre_switch")
    if not command:
        return 0
    host_id = _host_id(host)
    print(f"pre_switch {host_id}: {command}")
    if host_id == _this_host_id(cfg):
        argv = ["sh", "-c", command]
    else:
        target = host_text(cfg, host_id, "ssh")
        if not target:
            print(f"pre_switch {host_id}: no ssh target configured")
            return 1
        argv = ssh_argv(target, command)
    try:
        proc = run(argv, timeout=RELAY_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"pre_switch {host_id} failed: {exc}")
        return 1
    _print_helper(proc)
    if proc.returncode != 0:
        print(f"pre_switch {host_id} failed (rc={proc.returncode})")
    return int(proc.returncode or 0)


def relay_adapter(
    cfg: dict,
    local_argv: list[str] | None,
    *,
    adapter_id: str,
    args: list[str],
    label: str,
    relay_host_ids: list[str] | None = None,
    remote_path: str | None = None,
) -> int:
    """Run an adapter here, then on each relay host until one succeeds.

    A peer is used only when this host has no adapter binary, or the local
    run reports the device absent. The first success stops the search.
    Prints which host performed it.
    """
    this_id = _this_host_id(cfg) or "this host"
    hosts = list(relay_host_ids or [])
    tried: list[str] = [this_id]

    def performed(who: str) -> int:
        print(f"{label} via {who}")
        return 0

    if local_argv:
        try:
            proc = run(list(local_argv), timeout=8.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"{label}: local adapter failed to start: {exc}")
            return 1
        _print_helper(proc)
        if proc.returncode == 0:
            return performed(this_id)
        if not adapter_reports_absent(proc.returncode, proc.stdout or "", proc.stderr or ""):
            return int(proc.returncode)
        print(f"{label}: not found on {this_id}")
    else:
        print(f"{label}: adapter not available on {this_id}")

    can_relay = bool(adapter_id or remote_path)
    remote = remote_adapter_invocation(adapter_id, args, remote_path) if can_relay else ""
    for host_id in hosts:
        if host_id == this_id:
            continue
        tried.append(host_id)
        target = host_text(cfg, host_id, "ssh")
        if not target:
            print(f"{label}: relay {host_id} has no ssh target")
            continue
        if not can_relay:
            print(f"{label}: relay {host_id} has no adapter id")
            continue
        print(f"{label}: trying {host_id}")
        try:
            proc = run(ssh_argv(target, remote), timeout=RELAY_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"{label}: relay {host_id} failed: {exc}")
            continue
        _print_helper(proc)
        if proc.returncode == 0:
            return performed(host_id)
        if adapter_reports_absent(proc.returncode, proc.stdout or "", proc.stderr or ""):
            print(f"{label}: not found on {host_id}")
            continue
        print(f"{label}: relay {host_id} failed (rc={proc.returncode})")
    print(f"{label}: no host could run it (tried {', '.join(tried)})")
    return 1


def switch_monitor(cfg: dict, host: str, *, mouse_only: bool) -> int:
    if mouse_only or not cfg.get("switch_monitor", True):
        return 0
    host_id = _host_id(host)
    spec = _as_dict((cfg.get("hosts") or {}).get(host_id))
    pbp = spec.get("pbp")
    if pbp or cfg.get("switch_pbp"):
        mode = str(pbp or cfg.get("pbp_mode") or "").strip()
        if mode:
            return dualup_set_mode(
                cfg,
                mode,
                missing="lgdualup not on PATH — run `make install`",
            )
    name = configured_display_input(cfg, host_id)
    if not name:
        print(
            f"no display input configured for {host_id} "
            f"(set adapters.display.inputs.{host_id} or hosts.{host_id}.dualup_input) "
            f"— leaving the display input unchanged"
        )
        return 0
    path = lgdualup_path(cfg)
    relays = display_relay_host_ids(cfg)
    others = [item for item in relays if item != _this_host_id(cfg)]
    if path is None and not others:
        print("lgdualup not on PATH — run `make install` (DualUp helper ships in this repo)")
        return 0
    pre_rc = run_pre_switch(cfg, host_id)
    adapter_id = display_adapter_id(cfg)
    if not adapter_id and path is not None:
        adapter_id = path.name
    local = [str(path), "input", name] if path is not None else None
    rc = relay_adapter(
        cfg,
        local,
        adapter_id=adapter_id,
        args=["input", name],
        label=f"display input {name}",
        relay_host_ids=relays,
        remote_path=display_relay_path(cfg),
    )
    return pre_rc or rc


def _after_display_change(cfg: dict) -> None:
    note_wallpaper(cfg)


def cmd_to(cfg: dict, host: str, *, mouse_only: bool = False) -> int:
    dest_host, _spec = host_spec(cfg, host)
    channel = host_channel(cfg, dest_host)
    print(f"desk → {dest_host} (MX channel {channel})")
    rc = switch_mouse(cfg, channel)
    mon_rc = switch_monitor(cfg, dest_host, mouse_only=mouse_only)
    _after_display_change(cfg)
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
        print("usage: desk pbp <mode>   (mode is whatever lgdualup pbp accepts)")
        return 2
    rc = dualup_set_mode(
        cfg,
        chosen,
        missing="lgdualup not on PATH — DualUp PBP needs `make install`",
    )
    _after_display_change(cfg)
    return rc


def cmd_full(cfg: dict) -> int:
    rc = dualup_set_mode(
        cfg,
        "full",
        missing="lgdualup not on PATH — DualUp full needs `make install`",
    )
    _after_display_change(cfg)
    return rc


def cmd_layout(cfg: dict, name: str | None = None) -> int:
    """Apply a named layout, or re-apply full/PBP from the live display."""
    if name:
        rc = apply_named_layout(cfg, name)
        # An unknown name does not change a monitor. A real apply, even a
        # partial one, asks wallpaper the same way full, pbp, and to do.
        if rc != 2:
            _after_display_change(cfg)
        return rc
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
    """Display inputs. `mac` / `linux` keep their defaults so old keys stay.

    Any other host is included only when an input is configured.
    """
    hosts = cfg.get("hosts") or {}
    out = {}
    for host, default in PBP_INPUT_DEFAULTS.items():
        spec = _as_dict(hosts.get(host))
        configured = str(spec.get("dualup_input") or spec.get("input") or "").strip()
        out[host] = configured or default
    for host, spec in hosts.items():
        if host in out:
            continue
        name = str(_as_dict(spec).get("dualup_input") or _as_dict(spec).get("input") or "").strip()
        if name:
            out[host] = name
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
        "if command -v desk >/dev/null; then desk status --json --local; "
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
    """Quiet strip model: focus + optional display mark. Shells only paint.

    `MAC` / `LNX` stay those tokens. Any other focused host keeps its hint
    (or its id) so a third host is visible. Shells that only know the two
    tokens still treat anything else as unknown.
    """
    hint = str(state.get("target_hint") or "?").strip() or "?"
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
    slot: dict = {
        "id": slot_id,
        "glyph": glyph or "dot",
        "label": label or slot_id,
        "kind": resolve_slot_kind(slot_id, raw.get("kind")),
    }
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
    active = str(state.get("active_layout") or "").strip()
    monitors = state.get("monitors") if isinstance(state.get("monitors"), list) else []
    split = mode == "pbp" or any(
        isinstance(item, dict) and item.get("mode") == "pbp" for item in monitors
    )
    if active:
        glyph = "display.split" if split else "display.full"
        label = active
    elif mode == "pbp":
        glyph, label = "display.split", "PBP"
    elif mode == "full":
        glyph, label = "display.full", "FULL"
    else:
        return None
    return {
        "id": "dualup",
        "kind": "mode",
        "glyph": glyph,
        "label": label,
        "actions": [
            {"label": "Full", "argv": ["full"]},
            {"label": "PBP", "argv": ["pbp"]},
            {"label": "Auto layout", "argv": ["layout"]},
        ],
    }


def _slot_prefs_from_cfg(cfg: dict | None) -> tuple[list[dict] | None, bool]:
    cfg = cfg or {}
    prefs = cfg.get("_ui_slots")
    if isinstance(prefs, list) and prefs:
        return prefs, bool(cfg.get("_ui_slots_exclusive"))
    wanted = cfg.get("_tray_slots")
    if isinstance(wanted, list) and wanted:
        return (
            [{"id": str(item), "enabled": True} for item in wanted if str(item).strip()],
            True,
        )
    return None, False


def lights_slot(mark: str) -> dict:
    """Tray/HUD slot for the desk light. Always paint when the user opted in.

    `on` / `off` come from the adapter (entity power when readable, else last
    commanded). Anything else is unknown — still show the slot so HUD actions
    exist and the bar can flip after an optimistic command.
    """
    state = str(mark or "unknown").strip().lower()
    if state == "on":
        glyph, label = "light.on", "ON"
    elif state == "off":
        glyph, label = "light.off", "OFF"
    else:
        glyph, label = "light.off", "?"
    return {
        "id": "lights",
        "kind": "toggle",
        "glyph": glyph,
        "label": label,
        "hot": state == "on",
        "actions": [
            {"label": "On", "argv": ["smarthome", "on"]},
            {"label": "Off", "argv": ["smarthome", "off"]},
        ],
    }


def collect_slots(state: dict, cfg: dict | None = None) -> list[dict]:
    """Compose additive tray/HUD slots. Shells only paint. Missing adapter ⇒ omit.

    Default order is weather → kettle → dualup. `ui.tray.slots` reorders and
    hides. Each slot carries `kind` (face / chip / toggle / mode). Lights
    stay out unless `ui.tray.lights` or the lights pref is on.
    """
    cfg = cfg or {}
    adapters = state.get("adapters") or {}
    ordered: list[dict] = []
    for role in DEFAULT_TRAY_SLOT_IDS:
        if role == "dualup":
            continue
        snap = adapters.get(role) if isinstance(adapters.get(role), dict) else {}
        if snap.get("enabled") is False:
            continue
        slot = normalize_slot(snap.get("slot"))
        if slot:
            ordered.append(slot)
    display = normalize_slot(display_slot(state))
    if display:
        ordered.append(display)
    prefs, exclusive = _slot_prefs_from_cfg(cfg)
    lights_opt_in = bool(cfg.get("_tray_lights") or state.get("_tray_lights"))
    if prefs:
        lights_opt_in = lights_opt_in or any(
            item.get("id") == "lights" and item.get("enabled", True) for item in prefs
        )
    if lights_opt_in:
        home = adapters.get("smarthome") if isinstance(adapters.get("smarthome"), dict) else {}
        lights = home.get("lights") if isinstance(home.get("lights"), list) else []
        mark = "unknown"
        if lights and isinstance(lights[0], dict):
            mark = str(lights[0].get("state") or "unknown")
        light = normalize_slot(lights_slot(mark))
        if light:
            ordered.append(light)
    if prefs:
        ordered = apply_slot_prefs(ordered, prefs, exclusive=exclusive)
    return apply_hud_prefs(ordered, cfg)


def format_strip_title(strip: dict) -> str:
    """Painted default strip: focus token plus PBP/FULL when a display mode is known."""
    focus = str(strip.get("focus") or "?").strip()
    if focus == "?":
        focus = ""
    display = str(strip.get("display") or "").lower()
    mark = "PBP" if display == "pbp" else ("FULL" if display == "full" else "")
    parts = [part for part in (focus, mark) if part]
    return "  ".join(parts) if parts else "desk"


def format_bar_label(state: dict) -> str:
    """Compact shared glyph for Omarchy + DeskSwitchBar: LNX  kbU  mx2  PBP."""
    hint = str(state.get("target_hint") or "?").strip() or "?"
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
    if hint != "?":
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
    input_bits = [
        f"mac={inputs.get('mac', 'hdmi1')}",
        f"linux={inputs.get('linux', 'dp')}",
    ]
    input_bits.extend(
        f"{name}={value}" for name, value in inputs.items() if name not in ("mac", "linux")
    )
    return (
        f"focus {state.get('target_hint', '?')} · HHKB {hhkb} · "
        f"mouse {mouse} · DualUp {dual} {' '.join(input_bits)}"
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
    wallpaper_path = wallpaper_installed_path(cfg)
    if cfg.get("_wallpaper_enabled", False):
        wallpaper_bound = bind_role(cfg, "wallpaper")
        wallpaper_backend = wallpaper_bound.get("id") or "wallpaper"
        wallpaper_source = wallpaper_bound.get("source")
        wallpaper_caps = list(wallpaper_bound.get("capabilities") or ["wallpaper.apply"])
        if wallpaper_bound.get("path"):
            wallpaper_path = Path(wallpaper_bound["path"])
    else:
        wallpaper_backend = str(cfg.get("_wallpaper_backend") or "wallpaper")
        wallpaper_source = "disabled"
        manifest = manifest_for(wallpaper_backend, wallpaper_path)
        wallpaper_caps = list((manifest or {}).get("capabilities") or ["wallpaper.apply"])
    wallpaper = {
        "enabled": bool(cfg.get("_wallpaper_enabled", False)),
        "available": wallpaper_path is not None,
        "backend": wallpaper_backend,
        "path": str(wallpaper_path) if wallpaper_path else None,
        "source": wallpaper_source,
        "capabilities": wallpaper_caps,
    }
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
            **{name: spec for name, spec in hosts.items() if isinstance(spec, dict)},
        },
        "display": dict(display),
        "dualup": dict(display),
        "smarthome": smarthome,
        "kettle": kettle,
        "weather": weather,
        "wallpaper": wallpaper,
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
    active_layout = active_layout_name(cfg)
    known_layout = active_layout if active_layout in layouts_config(cfg) else None
    assignment = monitor_assignment(cfg, known_layout)
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
    if cfg.get("_wallpaper_enabled"):
        wallpaper_snap = invoke_wallpaper(cfg, "info", timeout=4.0)
        merge_adapter_snapshot(adapters.get("wallpaper"), wallpaper_snap)
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
        "follow_keyboard_arrival": bool(cfg.get("follow_keyboard_arrival", True)),
        "arrival_moves_display": arrival_moves_display(cfg),
        "push_on_leave": (
            default_push_on_leave(cfg)
            if cfg.get("push_on_leave") is None
            else bool(cfg["push_on_leave"])
        ),
        "target_channel": cfg["target_channel"],
        "hosts": cfg["hosts"],
        "host_list": public_host_list(cfg),
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
        "active_layout": active_layout,
        "monitors": assignment,
        "peer": peer,
        "adapters": adapters,
        "ui": format_public_ui(cfg),
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
    print(
        f"follow        : arrival={str(bool(state.get('follow_keyboard_arrival'))).lower()}  "
        f"usb={str(bool(state.get('follow_hhkb_usb'))).lower()}  "
        f"push_on_leave={str(bool(state.get('push_on_leave'))).lower()}  "
        f"arrival_moves_display={str(bool(state.get('arrival_moves_display', True))).lower()}"
    )
    if state.get("active_layout"):
        print(f"layout        : {state['active_layout']}")
        for mon in state.get("monitors") or []:
            if not isinstance(mon, dict):
                continue
            if mon.get("mode") == "pbp":
                detail = f"pbp top={mon.get('top') or '-'} bottom={mon.get('bottom') or '-'}"
                if mon.get("split"):
                    detail += f" split={mon['split']}"
            else:
                detail = f"full host={mon.get('host') or '-'}"
            follow = "follow" if mon.get("follow") else "stay"
            print(f"  {mon.get('id')}: {detail}  {follow}")
    print(f"target_hint   : {state['target_hint']}  source={state.get('target_hint_source', '?')}")
    print(f"bar           : {state.get('bar_label', state['target_hint'])}")
    print("adapters")
    mouse_state = "available" if mouse.get("available") else "missing"
    if not mouse.get("enabled", True):
        mouse_state = "disabled"
    print(f"  mouse       : {mouse_state}  backend={mouse.get('backend', 'mxswitch')}  {mouse.get('path') or '-'}")
    print(f"  hosts       : {format_host_summary(state)}")
    dual_state = "available" if dual.get("available") else "missing"
    if not dual.get("enabled", True):
        dual_state = "disabled"
    inputs = state.get("dualup_inputs") or {}
    input_bits = [
        f"mac={inputs.get('mac', 'hdmi1')}",
        f"linux={inputs.get('linux', 'dp')}",
    ]
    input_bits.extend(
        f"{name}={value}" for name, value in inputs.items() if name not in ("mac", "linux")
    )
    print(
        f"  dualup      : {dual_state}  backend={dual.get('backend', 'lgdualup')}  "
        f"mode={state.get('dualup_mode', 'unknown')}  "
        f"{'  '.join(input_bits)}  "
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
    paper = adapters.get("wallpaper") or {}
    paper_state = "available" if paper.get("available") else "missing"
    if not paper.get("enabled", False):
        paper_state = "disabled"
    paper_mark = str(paper.get("summary") or paper.get("backend_choice") or "").strip()
    if paper.get("error"):
        paper_mark = str(paper.get("error"))
    print(
        f"  wallpaper   : {paper_state}  backend={paper.get('backend', 'wallpaper')}  "
        f"{paper_mark + '  ' if paper_mark else ''}{paper.get('path') or '-'}"
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
    """Detect USB appearance. Startup snapshot does not fire."""
    if last_usb is None:
        return bool(usb_now), False
    fire = bool(follow_usb) and bool(usb_now) and not bool(last_usb)
    return bool(usb_now), fire


def state_dir() -> Path:
    """Persistent desk state. `DESK_STATE_DIR` overrides the XDG location."""
    override = os.environ.get("DESK_STATE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return root / "desk"


def state_path() -> Path:
    return state_dir() / "state.json"


def load_desk_state() -> dict:
    try:
        raw = json.loads(state_path().read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_desk_state(state: dict) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n")
    except OSError:
        return


def save_active_layout(name: str) -> None:
    state = load_desk_state()
    state["active_layout"] = name
    save_desk_state(state)


def active_layout_name(cfg: dict) -> str | None:
    """Last layout `desk layout` applied, else the config fallback.

    `_active_layout` on the in-memory cfg (including an explicit empty
    value) wins, so a caller can pin the answer without reading state.
    """
    if "_active_layout" in cfg:
        text = str(cfg.get("_active_layout") or "").strip()
        return text or None
    text = str(load_desk_state().get("active_layout") or "").strip()
    if text:
        return text
    configured = str(cfg.get("active_layout") or "").strip()
    return configured or None


def _parse_pbp_splits(raw: object) -> dict:
    """Opaque split-name → code map. The display adapter interprets it."""
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        name = str(key).strip().lower()
        if not name or value in (None, ""):
            continue
        try:
            out[name] = int(str(value), 0)
        except (TypeError, ValueError):
            continue
    return out


def _splits_blob(splits: dict) -> str:
    parts = []
    for key in sorted(splits):
        value = splits[key]
        if value in (None, ""):
            continue
        try:
            parts.append(f"{key}={int(value)}")
        except (TypeError, ValueError):
            continue
    return ",".join(parts)


def normalize_monitor_layout(raw: object, *, layout: str, monitor: str) -> dict:
    spec = _as_dict(raw)
    mode = str(spec.get("mode") or "full").strip().lower()
    if mode not in ("full", "pbp"):
        raise SystemExit(f"layout {layout} monitor {monitor}: mode must be full or pbp")
    out: dict = {"mode": mode}
    if mode == "full":
        host = str(spec.get("host") or "").strip()
        if host:
            out["host"] = normalize_host(host)
    else:
        for key in ("top", "bottom"):
            value = str(spec.get(key) or "").strip()
            if value:
                out[key] = normalize_host(value)
        split = str(spec.get("split") or "").strip()
        if split:
            out["split"] = split
    if "follow" in spec:
        out["follow"] = _as_bool(spec.get("follow"), False)
    return out


def normalize_layouts(user: dict) -> dict:
    """Named arrangements. Top-level `layouts`, or `adapters.layouts` when
    each entry has `monitors`. Geometry overrides under a display adapter
    stay where they are.
    """
    raw = user.get("layouts") if isinstance(user.get("layouts"), dict) else None
    if raw is None:
        candidate = _as_dict(_as_dict(user.get("adapters")).get("layouts"))
        if candidate and any(isinstance(item, dict) and "monitors" in item for item in candidate.values()):
            raw = candidate
        else:
            raw = {}
    out: dict = {}
    for name, body in raw.items():
        key = str(name).strip()
        if not key or not isinstance(body, dict):
            continue
        monitors_raw = body.get("monitors")
        if not isinstance(monitors_raw, dict):
            raise SystemExit(f"layout {key} needs a monitors object")
        monitors: dict = {}
        for mid, spec in monitors_raw.items():
            monitor_id = str(mid).strip()
            if not monitor_id:
                continue
            monitors[monitor_id] = normalize_monitor_layout(spec, layout=key, monitor=monitor_id)
        out[key] = {"monitors": monitors}
    return out


def layouts_config(cfg: dict) -> dict:
    stored = cfg.get("_layouts")
    if isinstance(stored, dict):
        return stored
    if isinstance(cfg.get("layouts"), dict):
        return normalize_layouts({"layouts": cfg["layouts"]})
    adapters = _as_dict(cfg.get("adapters"))
    candidate = adapters.get("layouts")
    if isinstance(candidate, dict):
        return normalize_layouts({"adapters": {"layouts": candidate}})
    return {}


def _normalize_instance(monitor_id: str, raw: dict, *, legacy: bool) -> dict:
    spec = _as_dict(raw)
    backend = str(spec.get("backend") or "").strip()
    if not backend and legacy:
        backend = "lgdualup"
    inputs: dict[str, str] = {}
    for key, value in _as_dict(spec.get("inputs")).items():
        text = str(value or "").strip()
        if text:
            inputs[str(key).strip()] = text
    relay = _coerce_relay_hosts(spec.get("relay_hosts")) if "relay_hosts" in spec else None
    caps = [str(item) for item in spec["capabilities"]] if isinstance(spec.get("capabilities"), list) else []
    settle = spec.get("layout_settle_s")
    return {
        "id": monitor_id,
        "backend": backend,
        "inputs": inputs,
        "relay_hosts": relay,
        "relay_path": str(spec.get("relay_path") or "").strip(),
        "path": str(spec.get("path") or "").strip(),
        "enabled": bool(spec.get("enabled", True)),
        "layout": bool(spec.get("layout", True)),
        "display_id": str(spec.get("display_id") or "").strip(),
        "pbp_splits": _parse_pbp_splits(spec.get("pbp_splits")),
        "bus": str(spec.get("bus") or "").strip(),
        "serial": str(spec.get("serial") or "").strip(),
        "primary": bool(spec.get("primary", False)),
        "legacy": legacy,
        "peer_layout": str(spec.get("peer_layout") or "").strip(),
        "layout_helper": str(spec.get("layout_helper") or "").strip(),
        "layout_settle_s": settle,
        "capabilities": caps,
    }


def display_instances(cfg: dict) -> list[dict]:
    """Monitors this desk can switch.

    `adapters.displays` is a map of id → instance. A legacy single
    `adapters.display` / `adapters.dualup` object is one monitor, id
    `display`, when that map is absent.
    """
    adapters = _as_dict(cfg.get("adapters"))
    raw_map = adapters.get("displays")
    instances: list[dict] = []
    if isinstance(raw_map, dict) and raw_map:
        for key, raw in raw_map.items():
            if not isinstance(raw, dict):
                continue
            instances.append(_normalize_instance(str(key).strip(), raw, legacy=False))
        if instances:
            if not any(item.get("primary") for item in instances):
                instances[0]["primary"] = True
            return instances
    legacy = display_adapter_cfg(cfg)
    inst = _normalize_instance("display", legacy, legacy=True)
    inst["primary"] = True
    if inst.get("relay_hosts") is None and "_display_relay_hosts" in cfg:
        inst["relay_hosts"] = list(cfg.get("_display_relay_hosts") or [])
    return [inst]


def primary_monitor_id(cfg: dict) -> str:
    for inst in display_instances(cfg):
        if inst.get("primary"):
            return str(inst["id"])
    return "display"


def instance_capabilities(instance: dict) -> set[str]:
    caps = {str(item) for item in (instance.get("capabilities") or [])}
    backend = str(instance.get("backend") or "").strip()
    if backend:
        manifest = manifest_for(backend)
        caps.update(str(item) for item in (manifest.get("capabilities") or []))
    if not caps and (backend == "lgdualup" or instance.get("legacy")):
        caps.update(ROLE_CAPABILITIES["display"])
    if not caps:
        caps.add("display.input")
    return caps


def instance_relay_hosts(cfg: dict, instance: dict) -> list[str]:
    raw = instance.get("relay_hosts")
    if raw is None:
        return display_relay_host_ids(cfg)
    return list(raw)


def instance_adapter_path(cfg: dict, instance: dict) -> Path | None:
    if instance.get("enabled") is False:
        return None
    backend = str(instance.get("backend") or "").strip()
    configured = str(instance.get("path") or "").strip() or None
    legacy_lg = bool(instance.get("legacy")) or (
        instance.get("id") == "display" and backend in ("", "lgdualup")
    )
    if legacy_lg:
        if configured:
            found = which_adapter(backend or "lgdualup", configured)
            if found:
                return found
        return lgdualup_path(cfg)
    if not backend and not configured:
        return None
    return which_adapter(backend or (configured or ""), configured)


def monitor_input_code(cfg: dict, instance: dict, host: str) -> str:
    """Input token for `host` on this monitor. Empty means it is not wired."""
    host_id = _host_id(host)
    for key, value in (instance.get("inputs") or {}).items():
        try:
            key_id = normalize_host(str(key))
        except SystemExit:
            key_id = str(key).strip()
        if key_id == host_id:
            text = str(value or "").strip()
            if text:
                return text
    if instance.get("legacy") or instance.get("primary") or instance.get("id") == "display":
        return configured_display_input(cfg, host_id)
    return ""


def hosts_on_monitor(spec: dict) -> list[str]:
    if spec.get("mode") == "pbp":
        found: list[str] = []
        for key in ("top", "bottom"):
            host = str(spec.get(key) or "").strip()
            if host and host not in found:
                found.append(host)
        return found
    host = str(spec.get("host") or "").strip()
    return [host] if host else []


def monitor_follow_enabled(spec: dict, *, primary: bool) -> bool:
    """Whether keyboard arrival may retarget this monitor.

    Explicit `follow` wins. Otherwise only the primary monitor follows,
    and only while it is in full mode. A PBP monitor stays split unless
    the layout sets `follow`.
    """
    if "follow" in spec:
        return bool(spec["follow"])
    return bool(primary) and spec.get("mode") == "full"


def layout_shows_host(layout: dict, host: str) -> bool:
    host_id = _host_id(host)
    for spec in (layout.get("monitors") or {}).values():
        if isinstance(spec, dict) and host_id in hosts_on_monitor(spec):
            return True
    return False


def layout_settle_seconds(cfg: dict, instance: dict | None = None) -> float:
    if instance is not None and instance.get("layout_settle_s") not in (None, ""):
        try:
            return float(instance["layout_settle_s"])
        except (TypeError, ValueError):
            pass
    raw = cfg.get("_dualup_layout_settle_s")
    if raw is None:
        raw = LAYOUT_DEFAULT_SETTLE_S
    try:
        return float(raw)
    except (TypeError, ValueError):
        return LAYOUT_DEFAULT_SETTLE_S


def layout_retry_settings(cfg: dict) -> tuple[int, float]:
    retries = as_int(cfg.get("_dualup_layout_retries"), LAYOUT_DEFAULT_RETRIES)
    raw_delay = cfg.get("_dualup_layout_retry_delay_s")
    if raw_delay is None:
        raw_delay = LAYOUT_DEFAULT_DELAY_S
    try:
        delay = float(raw_delay)
    except (TypeError, ValueError):
        delay = LAYOUT_DEFAULT_DELAY_S
    return max(retries, 1), delay


def steps_for_monitor(cfg: dict, instance: dict, spec: dict) -> list[dict]:
    """USB and OS-layout steps for one monitor. No I/O.

    PBP order is split, then the sub input (top), then the main input
    (bottom), with a settle after each. Full mode leaves split mode
    first when the adapter can, then selects the one input. OS layout
    runs after the monitor is set, once per host shown on it.
    """
    mid = str(instance.get("id") or "")
    caps = instance_capabilities(instance)
    mode = str(spec.get("mode") or "full")
    steps: list[dict] = []

    def adapter(args: list[str], label: str) -> None:
        steps.append({"monitor": mid, "op": "adapter", "args": list(args), "label": label})

    def settle() -> None:
        steps.append({"monitor": mid, "op": "settle"})

    def maybe_pre(host: str) -> None:
        if host_text(cfg, host, "pre_switch"):
            steps.append({"monitor": mid, "op": "pre_switch", "host": host})

    def os_layout(host: str, layout_mode: str, split: str) -> None:
        if instance.get("layout") is False or "layout.apply" not in caps:
            return
        steps.append(
            {
                "monitor": mid,
                "op": "os-layout",
                "host": host,
                "mode": layout_mode,
                "split": split,
            }
        )

    if mode == "pbp":
        if "display.pbp" not in caps:
            backend = instance.get("backend") or "display"
            steps.append(
                {
                    "monitor": mid,
                    "op": "error",
                    "message": f"{mid} cannot set PBP ({backend})",
                }
            )
            return steps
        split = str(spec.get("split") or "side").strip() or "side"
        panes: list[tuple[str, str, str, str]] = []
        for role, key, verb in (("sub", "top", "input-sub"), ("main", "bottom", "input-main")):
            host = str(spec.get(key) or "").strip()
            if not host:
                steps.append(
                    {"monitor": mid, "op": "error", "message": f"{mid} PBP has no {role} host"}
                )
                return steps
            code = monitor_input_code(cfg, instance, host)
            if not code:
                steps.append(
                    {
                        "monitor": mid,
                        "op": "error",
                        "message": f"no input configured for {host} on {mid}",
                    }
                )
                return steps
            panes.append((role, host, verb, code))
        args = ["pbp", split]
        blob = _splits_blob(instance.get("pbp_splits") or {})
        if blob:
            args.extend(["--splits", blob])
        adapter(args, f"{mid} split {split}")
        settle()
        for _role, host, verb, code in panes:
            maybe_pre(host)
            adapter([verb, code], f"{mid} {verb} {code}")
            settle()
        for host in hosts_on_monitor(spec):
            os_layout(host, "pbp", split)
        return steps

    host = str(spec.get("host") or "").strip()
    if not host:
        steps.append({"monitor": mid, "op": "error", "message": f"{mid} full mode has no host"})
        return steps
    code = monitor_input_code(cfg, instance, host)
    if not code:
        steps.append(
            {"monitor": mid, "op": "error", "message": f"no input configured for {host} on {mid}"}
        )
        return steps
    if "display.full" in caps:
        adapter(["pbp", "full"], f"{mid} full")
        settle()
    maybe_pre(host)
    adapter(["input", code], f"{mid} input {code}")
    settle()
    os_layout(host, "full", "")
    return steps


def layout_apply_steps(cfg: dict, name: str) -> list[dict]:
    """Ordered steps that apply one named layout. No I/O."""
    layout = layouts_config(cfg).get(name)
    if not layout:
        return [{"monitor": "", "op": "error", "message": f"unknown layout: {name}"}]
    by_id = {inst["id"]: inst for inst in display_instances(cfg)}
    steps: list[dict] = []
    for mid, spec in (layout.get("monitors") or {}).items():
        inst = by_id.get(mid)
        if inst is None:
            steps.append({"monitor": mid, "op": "error", "message": f"unknown monitor {mid}"})
            continue
        if inst.get("enabled") is False:
            continue
        steps.extend(steps_for_monitor(cfg, inst, spec))
    return steps


def _run_retry(argv: list[str], *, retries: int, delay: float, label: str, timeout: float = 12.0) -> int:
    last = 1
    for attempt in range(1, retries + 1):
        try:
            proc = run(list(argv), timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"{label} failed to start: {exc}", file=sys.stderr)
            return 1
        _print_helper(proc)
        last = int(proc.returncode or 0)
        if last == 0:
            return 0
        if last != 2 or attempt >= retries:
            break
        time.sleep(delay)
    return last


def instance_layout_binary(cfg: dict, instance: dict) -> Path | None:
    if instance.get("layout") is False:
        return None
    helper = str(instance.get("layout_helper") or "").strip()
    if helper:
        path = Path(helper).expanduser()
        if _is_exe(path):
            return path
        found = which_cmd(helper)
        if found:
            return found
    if "layout.apply" not in instance_capabilities(instance):
        return None
    backend = str(instance.get("backend") or "")
    if backend in ("", "lgdualup") or instance.get("legacy"):
        return dualup_layout_path(cfg)
    return None


def remote_layout_command(instance: dict, mode: str, split: str) -> str | None:
    if instance.get("layout") is False or "layout.apply" not in instance_capabilities(instance):
        return None
    helper = str(instance.get("peer_layout") or "~/.local/lib/desk/dualup-layout").strip()
    parts = [helper, mode]
    if mode == "pbp" and split:
        parts.extend(["--split", split])
    display_id = str(instance.get("display_id") or "").strip()
    if display_id:
        parts.extend(["--id", display_id])
    return " ".join(_shell_token(part) for part in parts)


def apply_os_layout_host(cfg: dict, instance: dict, host: str, mode: str, split: str) -> int:
    """Resolution and rotation on one host that the monitor is showing."""
    host_id = _host_id(host)
    retries, delay = layout_retry_settings(cfg)
    if host_id and host_id == _this_host_id(cfg):
        path = instance_layout_binary(cfg, instance)
        if path is None:
            return 0
        cmd = [str(path), mode]
        if mode == "pbp" and split:
            cmd.extend(["--split", split])
        display_id = str(instance.get("display_id") or cfg.get("_dualup_display_id") or "").strip()
        if display_id:
            cmd.extend(["--id", display_id])
        print(f"os layout {host_id} → {' '.join(cmd[1:])}")
        return _run_retry(cmd, retries=retries, delay=delay, label=f"os layout {host_id}")
    remote = remote_layout_command(instance, mode, split)
    if not remote:
        return 0
    target = host_text(cfg, host_id, "ssh")
    if not target:
        print(f"os layout {host_id}: no ssh target configured")
        return 1
    print(f"os layout {host_id} → {target}")
    return _run_retry(
        ssh_argv(target, remote),
        retries=retries,
        delay=delay,
        label=f"os layout {host_id}",
        timeout=15.0,
    )


def relay_monitor_command(cfg: dict, instance: dict, args: list[str], *, label: str) -> int:
    """Run one display command on whichever host currently holds the link.

    The upstream cable follows the active input, so the host that answered
    the previous command may not hold the device for this one. A failed
    sweep is tried once more after the settle delay.
    """
    if instance.get("enabled") is False:
        return 0
    path = instance_adapter_path(cfg, instance)
    backend = str(instance.get("backend") or (path.name if path else "")).strip()
    relays = instance_relay_hosts(cfg, instance)
    others = [item for item in relays if item != _this_host_id(cfg)]
    if path is None and not others:
        print(f"{label}: adapter not available")
        return 1
    local = [str(path), *args] if path is not None else None
    remote_path = str(instance.get("relay_path") or "").strip() or None
    if instance.get("legacy") and not remote_path:
        remote_path = display_relay_path(cfg)

    def once() -> int:
        return relay_adapter(
            cfg,
            local,
            adapter_id=backend,
            args=list(args),
            label=label,
            relay_host_ids=relays,
            remote_path=remote_path,
        )

    rc = once()
    if rc == 0:
        return 0
    settle = layout_settle_seconds(cfg, instance)
    if settle > 0:
        time.sleep(settle)
    return once()


def execute_layout_steps(cfg: dict, steps: list[dict]) -> int:
    by_id = {inst["id"]: inst for inst in display_instances(cfg)}
    for step in steps:
        op = str(step.get("op") or "")
        mid = str(step.get("monitor") or "")
        if op == "error":
            print(step.get("message") or "layout error")
            return 1
        inst = by_id.get(mid)
        if inst is None:
            print(f"layout: unknown monitor {mid}")
            return 1
        if op == "settle":
            delay = layout_settle_seconds(cfg, inst)
            if delay > 0:
                time.sleep(delay)
            continue
        if op == "pre_switch":
            rc = run_pre_switch(cfg, str(step.get("host") or ""))
            if rc:
                return rc
            continue
        if op == "adapter":
            rc = relay_monitor_command(
                cfg,
                inst,
                [str(arg) for arg in (step.get("args") or [])],
                label=str(step.get("label") or mid),
            )
            if rc:
                return rc
            continue
        if op == "os-layout":
            rc = apply_os_layout_host(
                cfg,
                inst,
                str(step.get("host") or ""),
                str(step.get("mode") or "full"),
                str(step.get("split") or ""),
            )
            if rc:
                return rc
            continue
        print(f"layout: unknown step {op}")
        return 1
    return 0


def apply_named_layout(cfg: dict, name: str) -> int:
    key = str(name or "").strip()
    layouts = layouts_config(cfg)
    if key not in layouts:
        known = ", ".join(layouts) or "none"
        print(f"unknown layout: {key} ({known})")
        return 2
    print(f"layout {key}")
    rc = execute_layout_steps(cfg, layout_apply_steps(cfg, key))
    if rc == 0:
        save_active_layout(key)
    return rc


def arrival_monitor_plan(cfg: dict, host: str, layout_name: str) -> list[dict]:
    """What arrival may change on each monitor. No I/O.

    A host that is already shown somewhere is left alone (mouse still
    moves). Otherwise only monitors marked follow — by default the
    primary monitor, and only in full mode — switch to that host.
    """
    layout = layouts_config(cfg).get(layout_name) or {}
    monitors = layout.get("monitors") or {}
    primary = primary_monitor_id(cfg)
    visible = layout_shows_host(layout, host) if layout else False
    move = arrival_moves_display(cfg)
    actions: list[dict] = []
    for mid, spec in monitors.items():
        if not isinstance(spec, dict):
            continue
        if not move:
            actions.append({"id": mid, "action": "leave", "reason": "arrival-leaves-displays"})
            continue
        if visible:
            actions.append({"id": mid, "action": "leave", "reason": "already-visible"})
            continue
        if not monitor_follow_enabled(spec, primary=(mid == primary)):
            reason = "pbp" if spec.get("mode") == "pbp" else "not-follow"
            actions.append({"id": mid, "action": "leave", "reason": reason})
            continue
        actions.append(
            {
                "id": mid,
                "action": "switch",
                "host": _host_id(host),
                "mode": "full",
            }
        )
    return actions


def monitor_assignment(cfg: dict, name: str | None) -> list[dict]:
    if not name:
        return []
    layout = layouts_config(cfg).get(name)
    if not layout:
        return []
    primary = primary_monitor_id(cfg)
    out: list[dict] = []
    for mid, spec in (layout.get("monitors") or {}).items():
        if not isinstance(spec, dict):
            continue
        item: dict = {
            "id": mid,
            "mode": spec.get("mode"),
            "hosts": hosts_on_monitor(spec),
            "follow": monitor_follow_enabled(spec, primary=(mid == primary)),
        }
        if spec.get("mode") == "pbp":
            item["top"] = spec.get("top") or ""
            item["bottom"] = spec.get("bottom") or ""
            if spec.get("split"):
                item["split"] = spec["split"]
        else:
            item["host"] = spec.get("host") or ""
        out.append(item)
    return out


def arrival_moves_display(cfg: dict) -> bool:
    """Whether keyboard arrival may change display inputs. Default on."""
    raw = cfg.get("arrival_moves_display", True)
    if raw is None:
        return True
    return bool(raw)


def arrival_plan(cfg: dict) -> dict:
    """What a keyboard arrival on this host is allowed to change.

    The mouse always follows. With no active layout, displays move only
    when `arrival_moves_display` is on (the blanket input switch).

    An active layout fills `layout` and `monitors`. A host already shown
    on any monitor is left on the displays. Otherwise only monitors the
    layout marks `follow` (default: the primary monitor, and only in
    full mode) switch to this host. A PBP monitor stays split unless
    `follow` is set. The blanket input switch does not also run.
    """
    host = str(cfg.get("this_host") or "")
    plan = {
        "host": host,
        "mouse": True,
        "displays": "move" if arrival_moves_display(cfg) else "leave",
        "layout": None,
    }
    name = active_layout_name(cfg)
    if not name or name not in layouts_config(cfg):
        return plan
    plan["layout"] = name
    actions = arrival_monitor_plan(cfg, host, name)
    plan["monitors"] = actions
    if not any(item.get("action") == "switch" for item in actions):
        plan["displays"] = "leave"
    return plan


def apply_named_arrival_layout(cfg: dict, name: str) -> int:
    """Move only the follow monitors. Do not replay the whole layout.

    An unknown name, a host that is already visible, or
    `arrival_moves_display` off leaves every display where it is.
    """
    if name not in layouts_config(cfg):
        print(f"arrival layout {name} is not configured — leaving displays unchanged")
        return 0
    host = str(cfg.get("this_host") or "")
    actions = arrival_monitor_plan(cfg, host, name)
    switches = [item for item in actions if item.get("action") == "switch"]
    if not switches:
        reason = actions[0].get("reason") if actions else ""
        if reason == "already-visible":
            print(
                f"arrival layout {name}: {host} is already on a monitor "
                "— leaving displays unchanged"
            )
        else:
            print(f"arrival layout {name}: leaving displays unchanged")
        return 0
    by_id = {inst["id"]: inst for inst in display_instances(cfg)}
    steps: list[dict] = []
    for item in switches:
        inst = by_id.get(item["id"])
        if inst is None:
            print(f"arrival layout {name}: unknown monitor {item['id']}")
            return 1
        steps.extend(steps_for_monitor(cfg, inst, {"mode": "full", "host": item["host"]}))
    return execute_layout_steps(cfg, steps)


def apply_arrival(cfg: dict, plan: dict, *, dry_run: bool, reason: str) -> int:
    """Run one arrival plan. Mouse first, then either a layout or displays."""
    host = str(plan.get("host") or cfg.get("this_host") or "")
    channel = host_channel(cfg, host)
    layout = plan.get("layout") or None
    move_displays = plan.get("displays") == "move" and not layout
    if dry_run:
        if layout:
            detail = f"layout {layout}"
        elif move_displays:
            detail = "mouse + displays"
        else:
            detail = "mouse only"
        log(f"dry-run: {reason} — would desk to {host} (channel {channel}, {detail})")
        return 0
    log(f"{reason} — desk → {host} (channel {channel})")
    if layout:
        print(f"desk → {host} (MX channel {channel})")
        rc = switch_mouse(cfg, channel) if plan.get("mouse", True) else 0
        layout_rc = apply_named_arrival_layout(cfg, str(layout))
        _after_display_change(cfg)
        return rc or layout_rc
    return cmd_to(cfg, host, mouse_only=not move_displays)


def _watch_pull_here(cfg: dict, dry_run: bool, *, reason: str) -> None:
    """Run the arrival plan once for a keyboard edge."""
    rc = apply_arrival(cfg, arrival_plan(cfg), dry_run=dry_run, reason=reason)
    if rc != 0 and not dry_run:
        log(f"follow to {cfg.get('this_host')} failed (rc={rc})")


def _watch_push_leave(cfg: dict, dry_run: bool, dest: int, retries: int, retry_delay: float) -> None:
    if dry_run:
        log(f"dry-run: would switch mouse to channel {dest}")
        return
    log(f"HHKB left — switching mouse to channel {dest}")
    rc = 1
    for attempt in range(1, retries + 1):
        rc = switch_mouse(cfg, dest)
        if rc == 0:
            log(f"switch sent (attempt {attempt})")
            return
        log(f"switch attempt {attempt} failed (rc={rc})")
        time.sleep(retry_delay)
    log("giving up this departure; will re-arm when HHKB returns")


def cmd_watch(cfg: dict, dry_run: bool) -> int:
    """Follow the keyboard. Arrival pulls the whole desk to this host.

    Bluetooth and USB use the same debounced rising edge
    (`absent_polls_required`, sleep-gap snapshot, probe errors count as
    present). `follow_hhkb_usb` still adds the legacy one-sample USB edge
    so a cable plug pulls even when the keyboard was already here over
    Bluetooth. Leave pushes the mouse to `follow_channel` only when
    `push_on_leave` is set.
    """
    interval = float(cfg["poll_interval_s"])
    need = max(int(cfg["absent_polls_required"]), 1)
    sleep_gap = float(cfg["sleep_gap_s"])
    retries = int(cfg["switch_retries"])
    retry_delay = float(cfg["switch_retry_delay_s"])
    dest = int(cfg["target_channel"])
    follow_usb = bool(cfg.get("follow_hhkb_usb", True))
    follow_arrival = bool(cfg.get("follow_keyboard_arrival", True))
    if cfg.get("push_on_leave") is None:
        push_leave = default_push_on_leave(cfg)
    else:
        push_leave = bool(cfg["push_on_leave"])
    here = str(cfg["this_host"])

    log(
        f"watching keyboard → desk to {here} "
        f"(dry_run={dry_run}, every {interval}s, absent×{need}, "
        f"follow_keyboard_arrival={follow_arrival}, follow_hhkb_usb={follow_usb}, "
        f"arrival_moves_display={arrival_moves_display(cfg)}, "
        f"push_on_leave={push_leave}, follow_channel={dest})"
    )
    armed = False
    absent = 0
    last = time.time()
    last_usb: bool | None = None
    # First sample after start or a sleep gap is a snapshot, not an edge.
    baseline = True
    present_streak = 0
    # True once this host is the last pull target and the keyboard has not
    # had a confirmed leave since. A blip shorter than `need` polls must
    # not run `desk to` again.
    focused_here = False
    arrival_latched = False
    wallpaper_state = {
        "baseline": None,
        "pending": None,
        "pending_since": None,
        "last_poll": 0.0,
        "last_error": None,
    }

    while True:
        now = time.time()
        if now - last > max(sleep_gap, interval * 4):
            log("clock gap (sleep/wake) — disarm until HHKB is seen again")
            armed = False
            absent = 0
            last_usb = None
            present_streak = 0
            arrival_latched = False
            baseline = True
        last = now

        try:
            probe = hhkb_probe(cfg)
        except StopIteration:
            raise
        except Exception as exc:  # noqa: BLE001 — never treat a probe crash as gone
            log(f"presence probe error ({exc}); treating as present")
            probe = empty_hhkb_probe(unknown=True)

        unknown = bool(probe.get("unknown"))
        # A failed probe counts as present so it cannot look like a leave
        # or, below, like a fresh arrival.
        usb_now = bool(probe.get("usb")) and not unknown
        present = bool(probe.get("present")) and not unknown

        if baseline:
            baseline = False
            last_usb = usb_now
            if present or unknown:
                armed = True
                present_streak = 1 if present else 0
                focused_here = True
                arrival_latched = True
                log("HHKB USB present — armed" if usb_now else "HHKB present — armed")
            else:
                present_streak = 0
                focused_here = False
                arrival_latched = False
            time.sleep(interval)
            continue

        last_usb, usb_rise = watch_usb_rising_edge(last_usb, usb_now, follow_usb)

        if present:
            present_streak += 1
            absent = 0
            if not armed:
                log("HHKB USB present — armed" if usb_now else "HHKB present — armed")
            armed = True
        elif unknown:
            absent = 0
            if not armed:
                log("HHKB present — armed")
            armed = True
        elif armed:
            present_streak = 0
            arrival_latched = False
            absent += 1
            if absent == 1 or absent == need:
                log(f"HHKB absent ({absent}/{need})")
        else:
            present_streak = 0
            arrival_latched = False

        arrival_edge = (
            follow_arrival
            and present
            and present_streak >= need
            and not arrival_latched
        )
        if usb_rise or (arrival_edge and not focused_here):
            reason = "HHKB USB appeared" if usb_rise else "keyboard arrived"
            _watch_pull_here(cfg, dry_run, reason=reason)
            focused_here = True
            arrival_latched = True
            armed = True
            absent = 0
        elif arrival_edge and focused_here:
            log(f"keyboard arrived — {here} is already the last action; not switching again")
            arrival_latched = True
        elif not present and not unknown and armed and absent >= need:
            if push_leave:
                _watch_push_leave(cfg, dry_run, dest, retries, retry_delay)
            else:
                log("HHKB left — not pushing the mouse (the host that sees the keyboard pulls)")
            focused_here = False
            arrival_latched = False
            armed = False
            absent = 0
        try:
            wallpaper_watch_poll(cfg, wallpaper_state, now)
        except Exception as exc:  # noqa: BLE001 — watcher stays up
            log(f"wallpaper watch failed: {exc}")
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


def cmd_wallpaper(cfg: dict, verb: str | None, *, as_json: bool = False) -> int:
    action = str(verb or "apply").strip().lower()
    if action in ("info", "probe"):
        action = "status"
    if action not in ("apply", "status"):
        print(f"unknown wallpaper verb: {verb} (try apply, status)", file=sys.stderr)
        return 2
    if not cfg.get("_wallpaper_enabled"):
        payload = {"ok": True, "enabled": False, "applied": [], "summary": "disabled"}
        print(json.dumps(payload, indent=2))
        return 0
    if wallpaper_adapter_path(cfg) is None:
        print("wallpaper adapter not found (install adapters/wallpaper or pin adapters.wallpaper.path)")
        return 0
    data = invoke_wallpaper(cfg, "info" if action == "status" else "apply", timeout=25.0)
    if data is None:
        print("wallpaper adapter not found (install adapters/wallpaper or pin adapters.wallpaper.path)")
        return 0
    printable = {key: value for key, value in data.items() if not str(key).startswith("_")}
    print(json.dumps(printable, indent=2))
    if printable.get("enabled") is False:
        return 0
    rc = data.get("_returncode")
    try:
        return int(rc) if rc is not None else (0 if data.get("ok", True) else 1)
    except (TypeError, ValueError):
        return 1


def _lower_host_arg(value: str) -> str:
    """Argparse type so `LNX` and `Tower` match configured ids."""
    return str(value).strip().lower()


def configured_host_choices(cfg: dict | None) -> list[str] | None:
    """Argparse choices: configured ids, plus mac/linux aliases that resolve to one."""
    if not isinstance(cfg, dict):
        return None
    hosts = cfg.get("hosts")
    if not isinstance(hosts, dict) or not hosts:
        return None
    ids = [name for name, spec in hosts.items() if isinstance(spec, dict)]
    if not ids:
        return None
    present = set(ids)
    aliases = [
        alias
        for alias, canon in HOST_ALIASES.items()
        if canon in present and alias not in present
    ]
    return ids + aliases


def build_parser(cfg: dict | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desk",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"desk {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status", help="show HHKB / mouse / DualUp state")
    status.add_argument("--json", action="store_true", help="machine-readable status")
    status.add_argument("--hint", action="store_true", help="print the focus token only")
    status.add_argument("--local", action="store_true", help="skip SSH peer peek")
    sub.add_parser("hint", help="print the focus token (MAC, LNX, or that host's hint)")
    watch = sub.add_parser("watch", help="keyboard arrival runs desk to this host")
    watch.add_argument("--dry-run", action="store_true")
    sw = sub.add_parser("switch", help="push the mouse, or switch a whole desk")
    sw.add_argument("target", nargs="?", help="Easy-Switch 1|2|3, or a configured host id")
    to = sub.add_parser("to", help="switch mouse + optional display input to a host")
    to.add_argument(
        "host",
        type=_lower_host_arg,
        choices=configured_host_choices(cfg),
        help="configured host id",
    )
    to.add_argument("--mouse-only", action="store_true", help="do not change the display input")
    pbp = sub.add_parser("pbp", help="DualUp PBP: USB toggle, input pair, tilted OS layout")
    pbp.add_argument("mode", nargs="?", help="mode string passed to `lgdualup pbp` (default: pbp_mode)")
    sub.add_parser("full", help="DualUp full: USB toggle + 2880x2560 @ 270°")
    lay = sub.add_parser(
        "layout",
        help="apply a named layout, or re-apply full/PBP from the live display",
    )
    lay.add_argument("name", nargs="?", help="name from config layouts")
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
    wallpaper = sub.add_parser("wallpaper", help="per-output wallpaper (off unless configured)")
    wallpaper.add_argument("verb", nargs="?", default="apply", help="apply | status")
    wallpaper.add_argument("--json", action="store_true", help="print adapter JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    parser = build_parser(cfg)
    args = parser.parse_args(argv)
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
        return cmd_layout(cfg, args.name)
    if args.cmd == "smarthome":
        return cmd_smarthome(cfg, args.verb, as_json=args.json)
    if args.cmd == "kettle":
        return cmd_kettle(cfg, args.verb, args.temp, as_json=args.json)
    if args.cmd == "weather":
        return cmd_weather(cfg, args.verb, as_json=args.json)
    if args.cmd == "wallpaper":
        return cmd_wallpaper(cfg, args.verb, as_json=args.json)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
