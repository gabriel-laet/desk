#!/usr/bin/env python3
"""Alexa smart-home reference adapter.

Out-of-process. Core never talks to Amazon or `alexacli`.

    alexa info | status          # JSON snapshot (cache + auth/cli; no speak)
    alexa list                   # Echo devices via `alexacli devices` (refresh)
    alexa on | off               # desk light via a fixed spoken phrase

Capabilities: smarthome.list / smarthome.status / light.on / light.off.

Today `alexacli smarthome list` (and `sh list`) often fail with empty JSON.
`alexacli devices` and `alexacli command "…" -d <Echo>` work. List prefers
devices and records a failed entity probe so it can grow later.

Desk light on the Escritório Echo: spoken text is ONLY `acender a luz` or
`apagar a luz`. The room is selected with `-d Escritório` — never put
"escritório" in the utterance (Alexa routes to the wrong device).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

ADAPTER_ID = "alexa"
ADAPTER_API_VERSION = 1
CAPABILITIES = ["smarthome.list", "smarthome.status", "light.on", "light.off"]

# Reference desk: light is reached through the Escritório Echo.
DEFAULT_DEVICE = "Escritório"
DEFAULT_ON_PHRASE = "acender a luz"
DEFAULT_OFF_PHRASE = "apagar a luz"
DEFAULT_CLI = "alexacli"
AUTH_RELATIVE = Path(".alexa-cli") / "config.json"
CACHE_NAME = "alexa-status.json"
CACHE_TTL_S = 120.0
LIGHT_ID = "desk"


def run(cmd: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def fold_text(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()


def auth_config_path() -> Path:
    override = os.environ.get("ALEXA_CLI_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / AUTH_RELATIVE


def cache_path() -> Path:
    override = os.environ.get("DESK_SWITCH_ALEXA_CACHE")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return root / "desk-switch" / CACHE_NAME


def which_cli(name: str) -> Path | None:
    found = shutil.which(name)
    if found:
        return Path(found)
    local = Path.home() / ".local" / "bin" / Path(name).name
    if local.is_file() and os.access(local, os.X_OK):
        return local
    brew = Path("/opt/homebrew/bin") / Path(name).name
    if brew.is_file() and os.access(brew, os.X_OK):
        return brew
    return None


def auth_configured(path: Path | None = None) -> dict:
    cfg_path = path or auth_config_path()
    out = {"configured": False, "path": str(cfg_path), "domain": None}
    if not cfg_path.is_file():
        return out
    try:
        raw = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return out
    if not isinstance(raw, dict):
        return out
    token = raw.get("refresh_token") or raw.get("token")
    out["configured"] = bool(token)
    domain = raw.get("amazon_domain") or raw.get("domain")
    if domain:
        out["domain"] = str(domain)
    return out


def manifest() -> dict:
    return {
        "api_version": ADAPTER_API_VERSION,
        "id": ADAPTER_ID,
        "name": "Alexa smart home",
        "capabilities": list(CAPABILITIES),
    }


def normalize_device(raw: object) -> dict | None:
    if isinstance(raw, str) and raw.strip():
        return {"name": raw.strip(), "kind": "speaker"}
    if not isinstance(raw, dict):
        return None
    name = raw.get("name") or raw.get("accountName") or raw.get("device") or raw.get("friendlyName")
    if not name:
        return None
    item = {
        "name": str(name),
        "kind": str(raw.get("kind") or raw.get("deviceType") or raw.get("type") or "speaker"),
    }
    for key in ("serial", "serialNumber", "deviceSerialNumber", "id"):
        if raw.get(key):
            item["id"] = str(raw[key])
            break
    return item


def parse_devices_payload(text: str) -> list[dict]:
    """Parse `alexacli devices --json` (`jq '.[].name'`) or a wrapped object."""
    data = json.loads(text or "")
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        nested = data.get("devices") or data.get("data") or data.get("items")
        if nested is None:
            raise json.JSONDecodeError("empty smart-home payload", text or "", 0)
        if isinstance(nested, dict):
            items = list(nested.values())
        elif isinstance(nested, list):
            items = nested
        else:
            items = []
    else:
        items = []
    out: list[dict] = []
    for raw in items:
        item = normalize_device(raw)
        if item:
            out.append(item)
    return out


def parse_devices_text(text: str) -> list[dict]:
    """Best-effort names from human `alexacli devices` output."""
    out: list[dict] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(("device", "found", "name", "---", "usage")):
            continue
        name = line.split("(", 1)[0].split("\t", 1)[0].strip(" -:")
        if not name or fold_text(name) in seen:
            continue
        seen.add(fold_text(name))
        out.append({"name": name, "kind": "speaker"})
    return out


def utterance_for(verb: str, phrase: str, device: str) -> str:
    text = (phrase or "").strip()
    if not text:
        raise SystemExit(f"empty {verb} phrase")
    folded_device = fold_text(device)
    if folded_device and folded_device in fold_text(text):
        raise SystemExit(
            f"refusing utterance that names the Echo ({device!r}): "
            "use -d for the speaker, not the room in the phrase"
        )
    return text


def default_light(device: str, on_phrase: str, off_phrase: str, state: str = "unknown") -> dict:
    return {
        "id": LIGHT_ID,
        "name": "desk",
        "speaker": device,
        "state": state,
        "on_phrase": on_phrase,
        "off_phrase": off_phrase,
        "source": "command",
    }


def load_cache() -> dict:
    path = cache_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_cache(payload: dict) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    to_store = dict(payload)
    to_store["cached_at"] = time.time()
    path.write_text(json.dumps(to_store, indent=2) + "\n")


def cache_fresh(payload: dict, ttl_s: float = CACHE_TTL_S) -> bool:
    try:
        cached_at = float(payload.get("cached_at") or 0)
    except (TypeError, ValueError):
        return False
    return bool(payload.get("devices")) and (time.time() - cached_at) < ttl_s


def cli_probe(name: str) -> dict:
    path = which_cli(name)
    return {"name": name, "available": path is not None, "path": str(path) if path else None}


def try_smarthome_entities(cli: Path) -> dict:
    """Optional entity list. Failures are recorded; they must not hide Echo devices."""
    attempts = (
        ["smarthome", "list", "--json"],
        ["sh", "list", "--json"],
        ["smarthome", "list"],
        ["sh", "list"],
    )
    last_error = "not attempted"
    for args in attempts:
        try:
            proc = run([str(cli), *args], timeout=12.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            continue
        text = (proc.stdout or "").strip()
        if not text:
            last_error = "empty output"
            continue
        try:
            entities = parse_devices_payload(text)
        except json.JSONDecodeError as exc:
            last_error = f"json: {exc}"
            continue
        return {
            "ok": True,
            "source": " ".join(args),
            "error": None,
            "entities": entities,
        }
    return {"ok": False, "source": None, "error": last_error, "entities": []}


def list_echo_devices(cli: Path) -> tuple[list[dict], str | None]:
    try:
        proc = run([str(cli), "devices", "--json"], timeout=15.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    text = (proc.stdout or "").strip()
    if text:
        try:
            return parse_devices_payload(text), None
        except json.JSONDecodeError:
            parsed = parse_devices_text(text)
            if parsed:
                return parsed, None
    if proc.returncode != 0:
        err = (proc.stderr or text or f"exit {proc.returncode}").strip()
        return [], err
    parsed = parse_devices_text(proc.stdout or "")
    return parsed, None if parsed else "no devices"


def snapshot(
    *,
    cli_name: str,
    device: str,
    on_phrase: str,
    off_phrase: str,
    refresh: bool = False,
) -> dict:
    cli = cli_probe(cli_name)
    auth = auth_configured()
    cached = load_cache()
    devices = list(cached.get("devices") or []) if isinstance(cached.get("devices"), list) else []
    lights = list(cached.get("lights") or []) if isinstance(cached.get("lights"), list) else []
    smarthome_list = cached.get("smarthome_list") if isinstance(cached.get("smarthome_list"), dict) else {
        "ok": False,
        "source": None,
        "error": "not attempted",
        "entities": [],
    }
    list_source = cached.get("list_source") or "devices"
    error = None
    if refresh:
        if not cli.get("available") or not cli.get("path"):
            error = f"{cli_name} not on PATH"
        else:
            devices, error = list_echo_devices(Path(str(cli["path"])))
            smarthome_list = try_smarthome_entities(Path(str(cli["path"])))
            list_source = "devices"
            if smarthome_list.get("ok") and smarthome_list.get("entities"):
                list_source = "devices+smarthome"
    if not lights:
        state = "unknown"
        if isinstance(cached.get("lights"), list) and cached["lights"]:
            state = str(cached["lights"][0].get("state") or "unknown")
        lights = [default_light(device, on_phrase, off_phrase, state)]
    else:
        lights = [dict(item) for item in lights]
        lights[0]["speaker"] = device
        lights[0]["on_phrase"] = on_phrase
        lights[0]["off_phrase"] = off_phrase
    out = {
        "id": ADAPTER_ID,
        "cli": cli,
        "auth": auth,
        "list_source": list_source,
        "smarthome_list": {
            "ok": bool(smarthome_list.get("ok")),
            "source": smarthome_list.get("source"),
            "error": smarthome_list.get("error"),
        },
        "devices": devices,
        "lights": lights,
    }
    if error:
        out["error"] = error
    if refresh and error is None:
        save_cache(out)
    elif cached.get("cached_at") is not None:
        out["cached_at"] = cached.get("cached_at")
        out["stale"] = not cache_fresh(cached)
    return out


def command_light(cli_name: str, verb: str, phrase: str, device: str) -> dict:
    safe = utterance_for(verb, phrase, device)
    cli = which_cli(cli_name)
    if cli is None:
        raise SystemExit(f"{cli_name} not on PATH — install alexacli and run `alexacli auth`")
    argv = [str(cli), "command", safe, "-d", device]
    try:
        proc = run(argv, timeout=25.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"alexacli command failed: {exc}") from exc
    state = "on" if verb == "on" else "off"
    result = {
        "ok": proc.returncode == 0,
        "verb": verb,
        "phrase": safe,
        "device": device,
        "argv": ["command", safe, "-d", device],
        "cli": str(cli),
        "stdout": (proc.stdout or "").rstrip(),
        "stderr": (proc.stderr or "").rstrip(),
    }
    if proc.returncode != 0:
        result["error"] = result["stderr"] or result["stdout"] or f"exit {proc.returncode}"
        return result
    cached = load_cache()
    on_phrase = safe if verb == "on" else DEFAULT_ON_PHRASE
    off_phrase = safe if verb == "off" else DEFAULT_OFF_PHRASE
    lights = [default_light(device, on_phrase, off_phrase, state)]
    cached["lights"] = lights
    cached["devices"] = cached.get("devices") or []
    save_cache(cached)
    result["lights"] = lights
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "verb",
        nargs="?",
        default="info",
        help="info | status | list | on | off",
    )
    parser.add_argument("--device", default=os.environ.get("DESK_SWITCH_SMARTHOME_DEVICE") or DEFAULT_DEVICE)
    parser.add_argument(
        "--on-phrase",
        default=os.environ.get("DESK_SWITCH_SMARTHOME_ON") or DEFAULT_ON_PHRASE,
    )
    parser.add_argument(
        "--off-phrase",
        default=os.environ.get("DESK_SWITCH_SMARTHOME_OFF") or DEFAULT_OFF_PHRASE,
    )
    parser.add_argument("--cli", default=os.environ.get("ALEXACLI") or DEFAULT_CLI)
    parser.add_argument("--json", action="store_true", help="JSON on stdout (always on for info/list)")
    parser.add_argument("--refresh", action="store_true", help="refresh device list (info/status)")
    parser.add_argument("--desk-switch-manifest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.desk_switch_manifest or args.verb == "--desk-switch-manifest":
        print(json.dumps(manifest(), indent=2))
        return 0
    verb = str(args.verb).strip().lower()
    if verb in ("info", "status", "probe"):
        data = snapshot(
            cli_name=args.cli,
            device=args.device,
            on_phrase=args.on_phrase,
            off_phrase=args.off_phrase,
            refresh=bool(args.refresh) or verb == "status",
        )
        print(json.dumps(data, indent=2))
        return 0 if not data.get("error") or data.get("devices") or not args.refresh else 1
    if verb == "list":
        data = snapshot(
            cli_name=args.cli,
            device=args.device,
            on_phrase=args.on_phrase,
            off_phrase=args.off_phrase,
            refresh=True,
        )
        print(json.dumps(data, indent=2))
        return 0 if not data.get("error") else 1
    if verb in ("on", "off"):
        phrase = args.on_phrase if verb == "on" else args.off_phrase
        data = command_light(args.cli, verb, phrase, args.device)
        print(json.dumps(data, indent=2))
        return 0 if data.get("ok") else 1
    raise SystemExit(f"unknown verb: {args.verb}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
