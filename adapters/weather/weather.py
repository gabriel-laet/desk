#!/usr/bin/env python3
"""Open-Meteo ambient weather adapter.

Out-of-process. First-class adapter — not a kettle side-feed.

    weather info | status          # JSON snapshot + tray slot

Default location is São Paulo (America/Sao_Paulo). Elevation comes from
the Open-Meteo forecast payload (or `adapters.weather.altitude_m`).
Works with the kettle host down.

Capabilities: weather.status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ADAPTER_ID = "weather"
ADAPTER_API_VERSION = 1
CAPABILITIES = ["weather.status"]
DEFAULT_LATITUDE = -23.5505
DEFAULT_LONGITUDE = -46.6333
DEFAULT_TIMEZONE = "America/Sao_Paulo"
DEFAULT_LABEL = "São Paulo"
CACHE_NAME = "weather.json"
CACHE_TTL_S = 600.0
DEFAULT_TIMEOUT_S = 1.5


def manifest() -> dict:
    return {
        "api_version": ADAPTER_API_VERSION,
        "id": ADAPTER_ID,
        "name": "Open-Meteo ambient",
        "capabilities": list(CAPABILITIES),
    }


def cache_path() -> Path:
    override = os.environ.get("DESK_SWITCH_WEATHER_CACHE")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return root / "desk-switch" / CACHE_NAME


def weather_mood_title(wmo_code: int, is_day: bool) -> str:
    if wmo_code in (0, 1):
        return "Clear" if is_day else "Clear night"
    if wmo_code in (2, 3):
        return "Cloudy"
    if wmo_code in (45, 48):
        return "Fog"
    if wmo_code in (95, 96, 99):
        return "Storm"
    if wmo_code >= 50:
        return "Rain"
    return "Cloudy"


def weather_glyph(wmo_code: int, is_day: bool) -> str:
    mood = weather_mood_title(wmo_code, is_day)
    if mood == "Clear":
        return "sun.max"
    if mood == "Clear night":
        return "moon.stars"
    if mood == "Fog":
        return "cloud.fog"
    if mood == "Storm":
        return "cloud.bolt.rain"
    if mood == "Rain":
        return "cloud.rain"
    return "cloud"


def open_meteo_url(latitude: float, longitude: float, timezone: str) -> str:
    query = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code,is_day",
            "timezone": timezone,
        }
    )
    return f"https://api.open-meteo.com/v1/forecast?{query}"


def parse_open_meteo(payload: dict | str) -> dict | None:
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(payload, dict):
        data = payload
    else:
        return None
    current = data.get("current")
    if not isinstance(current, dict):
        return None
    try:
        temp = float(current["temperature_2m"])
        code = int(round(float(current["weather_code"])))
    except (KeyError, TypeError, ValueError):
        return None
    is_day = True
    if current.get("is_day") is not None:
        try:
            is_day = abs(float(current["is_day"]) - 1.0) < 1e-9
        except (TypeError, ValueError):
            is_day = True
    altitude = None
    if data.get("elevation") is not None:
        try:
            altitude = int(round(float(data["elevation"])))
        except (TypeError, ValueError):
            altitude = None
    return {
        "temperature_c": temp,
        "weather_code": code,
        "is_day": is_day,
        "mood_title": weather_mood_title(code, is_day),
        "altitude_m": altitude,
    }


def load_cache() -> dict:
    path = cache_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def cache_fresh(payload: dict, ttl_s: float = CACHE_TTL_S) -> bool:
    try:
        cached_at = float(payload.get("cached_at") or 0)
    except (TypeError, ValueError):
        return False
    return bool(payload.get("current") or payload.get("temperature_c") is not None) and (
        time.time() - cached_at
    ) < ttl_s


def save_cache(payload: dict) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    to_store = dict(payload)
    to_store["cached_at"] = time.time()
    path.write_text(json.dumps(to_store, indent=2) + "\n")


def fetch_url(url: str, timeout: float) -> str | None:
    if not url:
        return None
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(0.2, float(timeout))) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def slot_from_weather(parsed: dict, *, altitude_m: int | None) -> dict:
    temp = int(round(float(parsed["temperature_c"])))
    detail_parts = [str(parsed.get("mood_title") or "")]
    if altitude_m is not None:
        detail_parts.append(f"{altitude_m}m")
    return {
        "id": "weather",
        "glyph": weather_glyph(int(parsed["weather_code"]), bool(parsed.get("is_day", True))),
        "label": f"{temp}°",
        "detail": " · ".join(part for part in detail_parts if part),
    }


def snapshot(
    *,
    latitude: float,
    longitude: float,
    timezone: str,
    label: str,
    altitude_override: int | None,
    timeout: float,
    url_override: str | None = None,
) -> dict:
    out: dict = {
        "id": ADAPTER_ID,
        "backend": ADAPTER_ID,
        "available": True,
        "reachable": False,
        "label": label,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "temp_c": None,
        "mood": None,
        "altitude_m": altitude_override,
        "weather_code": None,
        "is_day": None,
    }
    cached = load_cache()
    parsed = None
    source = None
    if cache_fresh(cached):
        parsed = parse_open_meteo(cached) or (
            {
                "temperature_c": cached.get("temperature_c"),
                "weather_code": cached.get("weather_code"),
                "is_day": cached.get("is_day"),
                "mood_title": cached.get("mood_title") or cached.get("mood"),
                "altitude_m": cached.get("altitude_m"),
            }
            if cached.get("temperature_c") is not None
            else None
        )
        if parsed and parsed.get("temperature_c") is not None:
            source = "cache"
    if parsed is None:
        url = url_override
        if url is None:
            url = os.environ.get("DESK_SWITCH_WEATHER_URL")
        if url == "":
            out["error"] = "weather fetch disabled"
            return out
        if not url:
            url = open_meteo_url(latitude, longitude, timezone)
        body = fetch_url(url, timeout)
        if not body:
            out["error"] = "open-meteo unreachable"
            return out
        try:
            raw = json.loads(body)
        except json.JSONDecodeError:
            out["error"] = "open-meteo json"
            return out
        parsed = parse_open_meteo(raw)
        if parsed is None:
            out["error"] = "open-meteo missing current"
            return out
        raw["cached_at"] = time.time()
        save_cache(raw)
        source = "open-meteo"
    altitude = altitude_override
    if altitude is None:
        altitude = parsed.get("altitude_m")
        if altitude is None and cached.get("altitude_m") is not None:
            try:
                altitude = int(cached["altitude_m"])
            except (TypeError, ValueError):
                altitude = None
    out.update(
        {
            "reachable": True,
            "temp_c": int(round(float(parsed["temperature_c"]))),
            "mood": parsed.get("mood_title"),
            "altitude_m": altitude,
            "weather_code": parsed.get("weather_code"),
            "is_day": parsed.get("is_day"),
            "source": source,
            "slot": slot_from_weather(parsed, altitude_m=altitude),
        }
    )
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", nargs="?", default="info", help="info | status")
    parser.add_argument("--latitude", type=float, default=float(os.environ.get("DESK_SWITCH_WEATHER_LAT") or DEFAULT_LATITUDE))
    parser.add_argument("--longitude", type=float, default=float(os.environ.get("DESK_SWITCH_WEATHER_LON") or DEFAULT_LONGITUDE))
    parser.add_argument("--timezone", default=os.environ.get("DESK_SWITCH_WEATHER_TZ") or DEFAULT_TIMEZONE)
    parser.add_argument("--label", default=os.environ.get("DESK_SWITCH_WEATHER_LABEL") or DEFAULT_LABEL)
    parser.add_argument("--altitude-m", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--url", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--desk-switch-manifest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.desk_switch_manifest or args.verb == "--desk-switch-manifest":
        print(json.dumps(manifest(), indent=2))
        return 0
    verb = str(args.verb).strip().lower()
    if verb not in ("info", "status", "probe"):
        raise SystemExit(f"unknown verb: {args.verb}")
    altitude = args.altitude_m
    if altitude is None and os.environ.get("DESK_SWITCH_WEATHER_ALT"):
        try:
            altitude = int(os.environ["DESK_SWITCH_WEATHER_ALT"])
        except ValueError:
            altitude = None
    data = snapshot(
        latitude=args.latitude,
        longitude=args.longitude,
        timezone=args.timezone,
        label=args.label,
        altitude_override=altitude,
        timeout=args.timeout,
        url_override=args.url,
    )
    print(json.dumps(data, indent=2))
    return 0 if data.get("reachable") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
