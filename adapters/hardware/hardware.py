#!/usr/bin/env python3
"""LAN hardware-metrics adapter.

Out of process. Core never names a host, a URL, or a vendor. It passes
`adapters.hardware` and reads this JSON.

    hardware info | status     # last cached sample, no network
    hardware refresh           # one fetch, then the cache

The endpoint is unauthenticated HTTP on the LAN. A short timeout and
the last good payload live in the desk state dir. A down endpoint
returns that payload and does not raise.

Null metric and AI fields are omitted. They are not shown as zero.
Unknown keys, including ones added later, are ignored.

Capabilities: hardware.status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ADAPTER_ID = "hardware"
ADAPTER_API_VERSION = 1
CAPABILITIES = ["hardware.status"]
DEFAULT_SHOW = ("cpu_temp_c", "cpu_pct", "mem_pct", "gpu_pct", "gpu_temp_c")
DEFAULT_POLL_S = 5.0
DEFAULT_TIMEOUT_S = 1.5
DEFAULT_STALE_S = 30.0
COLUMN_LABELS = {
    "cpu_pct": "CPU",
    "mem_pct": "MEM",
    "cpu_temp_c": "CPU°",
    "gpu_pct": "GPU",
    "gpu_temp_c": "GPU°",
}


def manifest() -> dict:
    return {
        "api_version": ADAPTER_API_VERSION,
        "id": ADAPTER_ID,
        "name": "LAN hardware metrics",
        "capabilities": list(CAPABILITIES),
    }


def hardware_paths() -> tuple[Path, Path]:
    """Cache file and in-flight lock. Keep this in sync with desk.py."""
    override = os.environ.get("DESK_HARDWARE_STATE", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.suffix == ".json":
            return path, path.with_suffix(".refresh")
        return path / "hardware.json", path / "hardware.refresh"
    state_override = os.environ.get("DESK_STATE_DIR", "").strip()
    if state_override:
        root = Path(state_override).expanduser()
        return root / "hardware.json", root / "hardware.refresh"
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    root = base / "desk"
    return root / "hardware.json", root / "hardware.refresh"


def clamp(value: object, default: float, low: float, high: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)
    if number < low:
        return float(low)
    if number > high:
        return float(high)
    return number


def clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def parse_config(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    show_raw = data.get("show")
    show: list[str] = []
    if isinstance(show_raw, list):
        for item in show_raw:
            text = clean_text(item if isinstance(item, str) else "")
            if not text and item is not None and not isinstance(item, str):
                text = clean_text(str(item))
            if text and text not in show:
                show.append(text)
    if not show:
        show = list(DEFAULT_SHOW)
    host_map: dict[str, str] = {}
    if isinstance(data.get("host_map"), dict):
        for key, value in data["host_map"].items():
            source = clean_text(str(key))
            target = clean_text(value if isinstance(value, str) else str(value) if value is not None else "")
            if source and target:
                host_map[source] = target
    extra: list[str] = []
    if isinstance(data.get("extra_hosts"), list):
        for item in data["extra_hosts"]:
            text = clean_text(item if isinstance(item, str) else str(item) if item is not None else "")
            if text and text not in extra:
                extra.append(text)
    desk_hosts: list[str] = []
    if isinstance(data.get("desk_hosts"), list):
        for item in data["desk_hosts"]:
            text = clean_text(item if isinstance(item, str) else str(item) if item is not None else "")
            if text and text not in desk_hosts:
                desk_hosts.append(text)
    labels: dict[str, str] = {}
    if isinstance(data.get("labels"), dict):
        for key, value in data["labels"].items():
            host_id = clean_text(str(key))
            label = clean_text(value if isinstance(value, str) else "")
            if host_id and label:
                labels[host_id] = label
    url = clean_text(data.get("url") if isinstance(data.get("url"), str) else "")
    if not (url.startswith("http://") or url.startswith("https://")):
        url = ""
    return {
        "url": url,
        "poll_seconds": clamp(data.get("poll_seconds"), DEFAULT_POLL_S, 1, 300),
        "timeout_seconds": clamp(data.get("timeout_seconds"), DEFAULT_TIMEOUT_S, 0.2, 5),
        "stale_seconds": clamp(data.get("stale_seconds"), DEFAULT_STALE_S, 1, 86400),
        "host_map": host_map,
        "extra_hosts": extra,
        "show": show,
        "desk_hosts": desk_hosts,
        "labels": labels,
    }


def parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def short_reset(value: object) -> str:
    text = clean_text(value if isinstance(value, str) else "")
    if not text:
        return ""
    stamp = parse_timestamp(text)
    if stamp is None:
        return text
    return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%m-%d %H:%MZ")


def metric_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    number: float | None = None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if number is None or number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def format_number(value: float) -> str:
    rounded = round(float(value), 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.1f}".rstrip("0").rstrip(".")


def format_pct(value: float) -> str:
    return f"{format_number(value)}%"


def format_temp(value: float, *, compact: bool = False) -> str:
    if compact:
        return f"{int(round(float(value)))}°"
    return f"{format_number(value)}°"


def format_usd(value: float) -> str:
    number = float(value)
    if number == 0 or abs(number) >= 0.01:
        return f"${number:.2f}"
    text = f"{number:.4f}".rstrip("0").rstrip(".")
    return f"${text}"


def format_metric(key: str, value: float, *, compact: bool = False) -> str:
    if "temp" in key:
        return format_temp(value, compact=compact)
    if key.endswith("_pct") or key.endswith("_percent"):
        return format_pct(value)
    return format_number(value)


def column_label(key: str) -> str:
    return COLUMN_LABELS.get(key, key)


def load_cache() -> dict:
    path, _lock = hardware_paths()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_cache(record: dict) -> None:
    path, _lock = hardware_paths()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(record) + "\n")
        tmp.replace(path)
    except OSError:
        return


def clear_refresh_lock() -> None:
    _path, lock = hardware_paths()
    try:
        lock.unlink()
    except OSError:
        return


def fetch_url(url: str, timeout: float) -> str | None:
    if not url:
        return None
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(0.2, float(timeout))) as resp:
            return resp.read(1_000_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def index_hosts(raw_hosts: object, host_map: dict[str, str]) -> dict[str, dict]:
    """Map endpoint ids onto desk ids. Last write wins. Non-objects are skipped."""
    indexed: dict[str, dict] = {}
    if not isinstance(raw_hosts, dict):
        return indexed
    for key, value in raw_hosts.items():
        if not isinstance(value, dict):
            continue
        source = clean_text(str(key))
        if not source:
            continue
        mapped = clean_text(host_map.get(source, source)) or source
        indexed[mapped] = value
    return indexed


def selected_ids(indexed: dict[str, dict], desk_hosts: list[str], extra_hosts: list[str]) -> list[str]:
    if not desk_hosts and not extra_hosts:
        return list(indexed.keys())
    out: list[str] = []
    seen: set[str] = set()
    for host_id in list(desk_hosts) + list(extra_hosts):
        if host_id in seen:
            continue
        seen.add(host_id)
        out.append(host_id)
    return out


def host_marker(raw: dict | None, now: float, stale_seconds: float) -> tuple[bool, bool, str, str]:
    """Return online, stale, marker, last_seen. Explicit false is offline.

    A null metric is not a marker. Stale uses last_seen only while the
    host is not already offline.
    """
    if not isinstance(raw, dict):
        return False, False, "offline", ""
    online_flag = raw.get("online")
    last_seen = clean_text(raw.get("last_seen") if isinstance(raw.get("last_seen"), str) else "")
    seen_at = parse_timestamp(last_seen)
    stale = bool(seen_at is not None and (now - seen_at) > stale_seconds)
    if online_flag is False:
        return False, False, "offline", last_seen
    if stale:
        return True, True, "stale", last_seen
    return True, False, "", last_seen


def metric_map(raw: dict | None, show: list[str]) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key in show:
        number = metric_number(raw.get(key))
        if number is None:
            continue
        out[key] = number
    return out


def highest_limit_used(ai: object) -> float | None:
    """Highest tools[].limit_used_pct. Nulls are skipped, not treated as zero."""
    if not isinstance(ai, dict):
        return None
    tools = ai.get("tools")
    if not isinstance(tools, list):
        return None
    best: float | None = None
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        number = metric_number(tool.get("limit_used_pct"))
        if number is None:
            continue
        if best is None or number > best:
            best = number
    return best


def _highest_limit_entry(tool: dict) -> tuple[str, float | None, str]:
    limits = tool.get("limits")
    best_label = ""
    best_pct: float | None = None
    best_reset = ""
    if isinstance(limits, list):
        for item in limits:
            if not isinstance(item, dict):
                continue
            pct = metric_number(item.get("used_pct"))
            if pct is None:
                continue
            if best_pct is not None and pct <= best_pct:
                continue
            best_pct = pct
            best_label = clean_text(item.get("label") if isinstance(item.get("label"), str) else "")
            best_reset = short_reset(item.get("resets_at"))
    if best_pct is None:
        rollup = metric_number(tool.get("limit_used_pct"))
        if rollup is not None:
            return "", rollup, ""
    return best_label, best_pct, best_reset


def _today_text(tool: dict) -> str:
    parts: list[str] = []
    tokens = metric_number(tool.get("today_tokens"))
    if tokens is not None:
        parts.append(f"{format_number(tokens)} tok")
    cost = metric_number(tool.get("today_cost_usd"))
    if cost is not None:
        parts.append(format_usd(cost))
    return " · ".join(parts)


def _tool_block(tool: dict, index: int) -> dict | None:
    if not isinstance(tool, dict):
        return None
    name = clean_text(tool.get("name") if isinstance(tool.get("name"), str) else "")
    raw_id = clean_text(tool.get("id") if isinstance(tool.get("id"), str) else "")
    if not name:
        name = raw_id
    plan = clean_text(tool.get("plan") if isinstance(tool.get("plan"), str) else "")
    limit_label, limit_pct, resets = _highest_limit_entry(tool)
    limit = ""
    if limit_pct is not None:
        limit = " ".join(part for part in (limit_label, format_pct(limit_pct)) if part)
    elif limit_label:
        limit = limit_label
    today = _today_text(tool)
    if not name and not plan and not limit and not resets and not today:
        return None
    block: dict = {"id": raw_id or name or f"tool-{index}"}
    if name:
        block["name"] = name
    if plan:
        block["plan"] = plan
    if limit:
        block["limit"] = limit
    if resets:
        block["resets"] = resets
    if today:
        block["today"] = today
    return block


def llama_line(llama: object) -> str:
    if not isinstance(llama, dict) or llama.get("running") is not True:
        return ""
    parts: list[str] = []
    model = clean_text(llama.get("model") if isinstance(llama.get("model"), str) else "")
    if model:
        parts.append(model)
    rate = metric_number(llama.get("tok_s"))
    if rate is not None:
        parts.append(f"{format_number(rate)} tok/s")
    busy = metric_number(llama.get("slots_busy"))
    total = metric_number(llama.get("slots_total"))
    if busy is not None and total is not None:
        parts.append(f"{format_number(busy)}/{format_number(total)} slots")
    elif busy is not None:
        parts.append(f"{format_number(busy)} busy")
    elif total is not None:
        parts.append(f"{format_number(total)} slots")
    if not parts:
        return "llama"
    return "llama " + " · ".join(parts)


def ai_block_for_host(raw: dict | None, *, host_id: str, label: str, marker: str, focused: bool) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("ai"), dict):
        return None
    ai = raw["ai"]
    tools_out: list[dict] = []
    tools = ai.get("tools")
    if isinstance(tools, list):
        for index, tool in enumerate(tools):
            block = _tool_block(tool, index) if isinstance(tool, dict) else None
            if block:
                tools_out.append(block)
    line = llama_line(ai.get("llama"))
    if not tools_out and not line:
        return None
    title = label if not marker else f"{label}  {marker}"
    block = {
        "id": host_id,
        "label": label,
        "title": title,
        "focused": focused,
        "tools": tools_out,
    }
    if marker:
        block["marker"] = marker
    if line:
        block["llama"] = {"line": line}
    return block


def format_ai_text(blocks: list[dict]) -> str:
    lines: list[str] = []
    for host in blocks:
        lines.append(str(host.get("title") or host.get("label") or host.get("id") or ""))
        for tool in host.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            head = " · ".join(part for part in (tool.get("name"), tool.get("plan")) if part)
            if head:
                lines.append(f"  {head}")
            bits = []
            if tool.get("limit"):
                bits.append(str(tool["limit"]))
            if tool.get("resets"):
                bits.append(f"resets {tool['resets']}")
            if bits:
                lines.append("  " + " · ".join(bits))
            if tool.get("today"):
                lines.append(f"  {tool['today']}")
        llama = host.get("llama") if isinstance(host.get("llama"), dict) else {}
        if llama.get("line"):
            lines.append(f"  {llama['line']}")
    return "\n".join(lines)


def format_header(columns: list[dict]) -> str:
    labels = "  ".join(f"{str(col.get('label') or ''):>6}" for col in columns)
    return f"{'':8}  {labels}".rstrip()


def format_row_line(label: str, cells: list[str], marker: str, focused: bool) -> str:
    flag = ">" if focused else " "
    shown = "  ".join(f"{cell:>6}" for cell in cells)
    text = f"{flag} {label:<6}  {shown}".rstrip()
    if marker:
        text += f"  {marker}"
    return text


def metric_token(metrics: dict[str, float], show: list[str]) -> str:
    if "cpu_temp_c" in show and "cpu_temp_c" in metrics:
        return format_temp(metrics["cpu_temp_c"], compact=True)
    for key in show:
        if key in metrics:
            return format_metric(key, metrics[key], compact=True)
    return ""


def build_rows(indexed: dict[str, dict], config: dict, focus: str | None, now: float) -> tuple[list[dict], list[dict]]:
    show: list[str] = list(config["show"])
    columns = [{"id": key, "label": column_label(key)} for key in show]
    rows: list[dict] = []
    for host_id in selected_ids(indexed, config["desk_hosts"], config["extra_hosts"]):
        raw = indexed.get(host_id)
        online, stale, marker, last_seen = host_marker(raw, now, float(config["stale_seconds"]))
        metrics = metric_map(raw, show)
        cells = [format_metric(key, metrics[key]) if key in metrics else "—" for key in show]
        label = config["labels"].get(host_id) or host_id
        focused = bool(focus) and host_id == focus
        row = {
            "id": host_id,
            "label": label,
            "focused": focused,
            "online": online,
            "stale": stale,
            "marker": marker,
            "metrics": metrics,
            "cells": cells,
            "line": format_row_line(label, cells, marker, focused),
        }
        if last_seen:
            row["last_seen"] = last_seen
        rows.append(row)
    return columns, rows


def compact_label(row: dict | None, metrics: dict[str, float], show: list[str], ai_limit: float | None) -> str:
    parts: list[str] = []
    if row and row.get("marker") == "offline":
        parts.append("off")
    else:
        token = metric_token(metrics, show)
        if token:
            parts.append(token)
    if ai_limit is not None:
        parts.append(format_pct(ai_limit))
    return " ".join(parts)


def render(
    payload: dict | None,
    config: dict,
    focus: str | None,
    *,
    reachable: bool,
    error: str | None,
    source: str,
    now: float | None = None,
) -> dict:
    moment = time.time() if now is None else float(now)
    # No sample yet: stay quiet. Do not invent an offline row per desk host.
    if isinstance(payload, dict):
        indexed = index_hosts(payload.get("hosts"), config["host_map"])
        columns, rows = build_rows(indexed, config, focus, moment)
    else:
        indexed = {}
        columns, rows = [], []
    blocks: list[dict] = []
    by_id = {row["id"]: row for row in rows}
    for host_id in selected_ids(indexed, config["desk_hosts"], config["extra_hosts"]):
        raw = indexed.get(host_id)
        row = by_id.get(host_id)
        if row is None:
            continue
        block = ai_block_for_host(
            raw,
            host_id=host_id,
            label=str(row.get("label") or host_id),
            marker=str(row.get("marker") or ""),
            focused=bool(row.get("focused")),
        )
        if block:
            blocks.append(block)
    focus_row = by_id.get(focus) if focus else None
    focus_raw = indexed.get(focus) if focus else None
    focus_metrics = focus_row.get("metrics") if isinstance(focus_row, dict) else {}
    if not isinstance(focus_metrics, dict):
        focus_metrics = {}
    ai_limit = highest_limit_used(focus_raw.get("ai")) if isinstance(focus_raw, dict) else None
    label = compact_label(focus_row if isinstance(focus_row, dict) else None, focus_metrics, config["show"], ai_limit)
    hosts_out: dict[str, dict] = {}
    for row in rows:
        item: dict = {
            "online": bool(row.get("online")),
            "stale": bool(row.get("stale")),
            "marker": row.get("marker") or "",
        }
        if row.get("last_seen"):
            item["last_seen"] = row["last_seen"]
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        item.update(metrics)
        hosts_out[str(row["id"])] = item
    out: dict = {
        "id": ADAPTER_ID,
        "backend": ADAPTER_ID,
        "available": True,
        "ok": bool(reachable),
        "reachable": bool(reachable),
        "source": source,
        "updated_at": payload.get("updated_at") if isinstance(payload, dict) else None,
        "hosts": hosts_out,
        "focus": focus or None,
        "token": label,
    }
    if error:
        out["error"] = error
    if rows or blocks:
        slot = {
            "id": "hardware",
            "kind": "table",
            "glyph": "cpu",
            "label": label,
            "columns": columns,
            "rows": rows,
            "header": format_header(columns),
        }
        if blocks:
            slot["ai"] = blocks
            slot["ai_text"] = format_ai_text(blocks)
            out["ai"] = blocks
        out["slot"] = slot
    return out


def build_snapshot(
    payload: dict | None,
    raw_config: object,
    focus: str | None = None,
    *,
    reachable: bool = True,
    error: str | None = None,
    source: str = "live",
    now: float | None = None,
) -> dict:
    return render(
        payload if isinstance(payload, dict) else None,
        parse_config(raw_config),
        focus or None,
        reachable=reachable,
        error=error,
        source=source,
        now=now,
    )


def cached_snapshot(raw_config: object, focus: str | None = None, now: float | None = None) -> dict:
    """Render the last sample. Does not open a socket."""
    record = load_cache()
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else None
    ok = bool(record.get("ok")) and payload is not None
    error = record.get("error") if isinstance(record.get("error"), str) and not ok else None
    return build_snapshot(
        payload,
        raw_config,
        focus,
        reachable=ok,
        error=error,
        source="cache" if payload is not None else "none",
        now=now,
    )


def refresh_snapshot(raw_config: object, focus: str | None = None, now: float | None = None) -> dict:
    """One short fetch. On failure keep the previous payload."""
    config = parse_config(raw_config)
    cached = load_cache()
    previous = cached.get("payload") if isinstance(cached.get("payload"), dict) else None
    if not config["url"]:
        error = "no hardware url configured"
        save_cache({"fetched_at": time.time(), "ok": False, "error": error, "payload": previous})
        return build_snapshot(
            previous,
            config,
            focus,
            reachable=False,
            error=error,
            source="cache" if previous is not None else "none",
            now=now,
        )
    body = fetch_url(config["url"], float(config["timeout_seconds"]))
    payload = None
    error = "hardware endpoint unreachable"
    if body:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("hosts"), dict):
            payload = parsed
            error = ""
        else:
            error = "hardware json"
    if payload is None:
        save_cache({"fetched_at": time.time(), "ok": False, "error": error, "payload": previous})
        return build_snapshot(
            previous,
            config,
            focus,
            reachable=False,
            error=error,
            source="cache" if previous is not None else "none",
            now=now,
        )
    save_cache({"fetched_at": time.time(), "ok": True, "error": None, "payload": payload})
    return build_snapshot(payload, config, focus, reachable=True, error=None, source="live", now=now)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", nargs="?", default="info", help="info | status | refresh")
    parser.add_argument("--config-json", default="", help="adapters.hardware object, plus desk host ids")
    parser.add_argument("--focus", default="", help="desk host id for the compact bar token")
    parser.add_argument("--refresh", action="store_true", help="fetch once instead of reading the cache")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--desk-switch-manifest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.desk_switch_manifest or args.verb == "--desk-switch-manifest":
        print(json.dumps(manifest(), indent=2))
        return 0
    verb = str(args.verb or "info").strip().lower()
    if verb not in ("info", "status", "probe", "refresh"):
        raise SystemExit(f"unknown verb: {args.verb}")
    raw_config: object = {}
    if args.config_json:
        try:
            raw_config = json.loads(args.config_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"hardware config json: {exc}") from exc
    focus = clean_text(args.focus) or None
    do_refresh = bool(args.refresh or verb == "refresh")
    try:
        if do_refresh:
            data = refresh_snapshot(raw_config, focus)
        else:
            data = cached_snapshot(raw_config, focus)
    finally:
        if do_refresh:
            clear_refresh_lock()
    print(json.dumps(data, indent=2))
    if data.get("reachable") or data.get("hosts"):
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
