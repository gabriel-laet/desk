#!/usr/bin/env python3
"""Per-output wallpaper adapter.

Out of process. Core calls `apply` / `geometry` / `info` and does not
name a monitor.

    wallpaper apply | status | info | geometry

An optional exact_dir is checked first: `<prefix>-<W>x<H>.png`, or a
single `*-<W>x<H>.*`, where W×H is the mode size after rotation (not
divided by scale). Otherwise portrait when the logical height is
greater than the width, else landscape (squares included). Linux
transforms 1, 3, 5, and 7 swap width and height. macOS frames are
already oriented; fill uses NSImageScaleProportionallyUpOrDown (3)
with clipping. Value 2 is center and is not used.

Fit is cover/fill (crop, never stretch). Source images are never
written. A derived cover file is written under ~/.cache/desk/wallpaper/
only when the setter cannot crop.

Off unless the config says enabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ADAPTER_ID = "wallpaper"
ADAPTER_API_VERSION = 1
CAPABILITIES = ["wallpaper.apply"]
# wl_output transforms that rotate by 90° or 270° (including flipped).
SWAP_TRANSFORMS = {1, 3, 5, 7}
# NSImageScaleProportionallyUpOrDown. 2 is NSImageScaleNone (center).
MACOS_FILL_SCALING = 3
PER_OUTPUT_BACKENDS = {
    "hyprpaper",
    "swaybg",
    "omarchy-shell-per-output",
    "omarchy-monitor-flag",
}
NATIVE_FILL_BACKENDS = PER_OUTPUT_BACKENDS | {"omarchy-single"}
SYSTEM = platform.system()


def manifest() -> dict:
    return {
        "api_version": ADAPTER_API_VERSION,
        "id": ADAPTER_ID,
        "name": "Per-output wallpaper",
        "capabilities": list(CAPABILITIES),
    }


def expand_path(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve())


def cache_dir() -> Path:
    override = os.environ.get("DESK_WALLPAPER_CACHE")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return root / "desk" / "wallpaper"


def logical_size(width: object, height: object, transform: object = 0) -> tuple[int, int]:
    """Rotated mode pixels. Scale is not applied (2560x1440@2 stays 2560x1440)."""
    w = int(round(float(width)))
    h = int(round(float(height)))
    try:
        turn = int(transform)
    except (TypeError, ValueError):
        turn = 0
    if turn in SWAP_TRANSFORMS:
        return h, w
    return w, h


def orientation_for(width: int, height: int) -> str:
    """Portrait only when height is strictly greater. Square is landscape."""
    if int(height) > int(width):
        return "portrait"
    return "landscape"


def normalize_config(raw: object) -> dict:
    """Accept a wallpaper object or a full desk config. Enabled defaults off."""
    data = raw if isinstance(raw, dict) else {}
    adapters = data.get("adapters")
    if isinstance(adapters, dict) and "wallpaper" in adapters:
        data = adapters.get("wallpaper") if isinstance(adapters.get("wallpaper"), dict) else {}
    elif "adapters" in data and "enabled" not in data and "landscape" not in data:
        data = {}
    outputs = data.get("outputs") if isinstance(data.get("outputs"), dict) else {}
    cleaned: dict[str, dict] = {}
    for key, spec in outputs.items():
        if isinstance(spec, dict):
            cleaned[str(key)] = spec
    return {
        "enabled": bool(data.get("enabled", False)),
        "landscape": str(data.get("landscape") or ""),
        "portrait": str(data.get("portrait") or ""),
        "primary_output": str(data.get("primary_output") or ""),
        "exact_dir": str(data.get("exact_dir") or ""),
        "exact_prefix": str(data.get("exact_prefix") or "").strip(),
        "outputs": cleaned,
    }


def load_config_file() -> dict:
    candidates = (
        Path.home() / ".config" / "desk" / "config.json",
        Path.home() / ".config" / "desk-switch" / "config.json",
        Path.home() / ".config" / "hhkb-mx-follow" / "config.json",
    )
    for path in candidates:
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(raw, dict):
            return normalize_config(raw)
    return normalize_config({})


def config_from_args(blob: str | None) -> dict:
    if blob is None or blob == "":
        return load_config_file()
    try:
        raw = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"wallpaper config json: {exc}") from exc
    return normalize_config(raw)


def normalize_output(raw: object, *, index: int = 0) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("disabled") in (True, "true", 1, "1"):
        return None
    mirror = str(raw.get("mirrorOf") if raw.get("mirrorOf") is not None else raw.get("mirror") or "").strip()
    if mirror and mirror.lower() not in ("none", "null"):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    try:
        width = int(round(float(raw.get("width") or 0)))
        height = int(round(float(raw.get("height") or 0)))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    try:
        transform = int(raw.get("transform", 0) or 0)
    except (TypeError, ValueError):
        transform = 0
    # Rotated mode pixels. `scale` is intentionally ignored so exact files
    # match the panel (2560x1440), not the scaled layout size (1280x720).
    logical_w, logical_h = logical_size(width, height, transform)
    description = str(raw.get("description") or raw.get("display_name") or "").strip()
    if not description:
        description = " ".join(
            part for part in (str(raw.get("make") or "").strip(), str(raw.get("model") or "").strip()) if part
        )
    try:
        parsed_index = int(raw.get("index") or 0)
    except (TypeError, ValueError):
        parsed_index = 0
    desktop_index = parsed_index or index or 0
    return {
        "name": name,
        "description": description,
        "index": desktop_index,
        "width": width,
        "height": height,
        "transform": transform,
        "logical_width": logical_w,
        "logical_height": logical_h,
        "orientation": orientation_for(logical_w, logical_h),
        "area": logical_w * logical_h,
    }


def outputs_from_monitors(data: object) -> list[dict]:
    rows = data
    if isinstance(data, dict):
        rows = data.get("monitors") or data.get("outputs") or data.get("screens") or []
    if not isinstance(rows, list):
        return []
    found: list[dict] = []
    for position, item in enumerate(rows, start=1):
        output = normalize_output(item, index=position)
        if output:
            found.append(output)
    return found


def _resolution_pair(text: str) -> tuple[int, int]:
    cleaned = text.replace("×", "x").replace(",", " ")
    digits: list[int] = []
    token = ""
    for char in cleaned:
        if char.isdigit():
            token += char
            continue
        if token:
            digits.append(int(token))
            token = ""
            if len(digits) == 2:
                break
    if token and len(digits) < 2:
        digits.append(int(token))
    if len(digits) >= 2 and digits[0] > 0 and digits[1] > 0:
        return digits[0], digits[1]
    return 0, 0


def outputs_from_system_profiler(payload: object) -> list[dict]:
    """system_profiler resolutions are already the oriented size."""
    if not isinstance(payload, dict):
        return []
    gpus = payload.get("SPDisplaysDataType") or []
    raws: list[dict] = []
    if not isinstance(gpus, list):
        return []
    for gpu in gpus:
        if not isinstance(gpu, dict):
            continue
        drivers = gpu.get("spdisplays_ndrvs") or []
        if not isinstance(drivers, list):
            continue
        for item in drivers:
            if not isinstance(item, dict):
                continue
            name = str(item.get("_name") or "").strip()
            res = str(item.get("_spdisplays_resolution") or item.get("spdisplays_resolution") or "")
            width, height = _resolution_pair(res)
            if not name or width <= 0:
                continue
            raws.append(
                {
                    "name": name,
                    "description": name,
                    "width": width,
                    "height": height,
                    "transform": 0,
                }
            )
    return outputs_from_monitors(raws)


def geometry_signature(outputs: list[dict]) -> str:
    parts = []
    for item in outputs:
        name = str(item.get("name") or "")
        width = int(item.get("logical_width") or 0)
        height = int(item.get("logical_height") or 0)
        parts.append((name, width, height))
    parts.sort()
    return "|".join(f"{name}:{width}x{height}" for name, width, height in parts)


def match_score(output: dict, wanted: str) -> int:
    """Rank a config key against an output. 0 is no match."""
    needle = str(wanted or "").strip()
    if not needle:
        return 0
    name = str(output.get("name") or "")
    description = str(output.get("description") or "")
    if needle == name:
        return 4
    if needle.lower() == name.lower():
        return 3
    if description and needle.lower() == description.lower():
        return 2
    if len(needle) >= 3 and description and needle.lower() in description.lower():
        return 1
    return 0


def find_override(output: dict, outputs_cfg: dict) -> dict:
    best: dict = {}
    best_score = 0
    best_len = -1
    for key, spec in outputs_cfg.items():
        if not isinstance(spec, dict):
            continue
        score = match_score(output, str(key))
        if score > best_score or (score == best_score and score > 0 and len(str(key)) > best_len):
            best = spec
            best_score = score
            best_len = len(str(key))
    return best


def _exact_prefix(prefix: object) -> str:
    text = str(prefix or "").strip()
    if not text or "/" in text or "\\" in text or text in (".", ".."):
        return ""
    return text


def exact_resolution_files(directory: Path, width: int, height: int) -> list[Path]:
    """Files named `*-<W>x<H>.<ext>` in one directory. Not recursive."""
    marker = f"-{int(width)}x{int(height)}"
    found: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    for path in entries:
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.stem.endswith(marker):
            found.append(path)
    found.sort(key=lambda item: item.name)
    return found


def find_exact_image(output: dict, config: dict) -> str:
    """Prefer `<prefix>-<W>x<H>.png`, else the only `*-<W>x<H>.*`."""
    raw_dir = str(config.get("exact_dir") or "").strip()
    if not raw_dir:
        return ""
    root = Path(expand_path(raw_dir))
    if not root.is_dir():
        return ""
    width = int(output.get("logical_width") or 0)
    height = int(output.get("logical_height") or 0)
    if width <= 0 or height <= 0:
        return ""
    prefix = _exact_prefix(config.get("exact_prefix"))
    if prefix:
        preferred = root / f"{prefix}-{width}x{height}.png"
        if preferred.is_file():
            return str(preferred.resolve())
    matches = exact_resolution_files(root, width, height)
    if len(matches) == 1:
        return str(matches[0].resolve())
    return ""


def choose_image(output: dict, config: dict) -> tuple[str, str]:
    """Exact resolution first, then portrait/landscape (override, then global)."""
    exact = find_exact_image(output, config)
    if exact:
        return exact, "exact"
    override = find_override(output, config.get("outputs") or {})
    orient = str(output.get("orientation") or "landscape")
    specific = str(override.get(orient) or "").strip()
    fallback = str(config.get(orient) or "").strip()
    return expand_path(specific or fallback), orient


def pick_reference_output(outputs: list[dict], primary: str) -> tuple[dict | None, str]:
    """Output whose aspect supplies the single shared image."""
    if not outputs:
        return None, ""
    wanted = str(primary or "").strip()
    if wanted:
        ranked = [(match_score(item, wanted), item) for item in outputs]
        ranked = [item for item in ranked if item[0] > 0]
        if ranked:
            ranked.sort(key=lambda item: (-item[0], item[1]["name"]))
            return ranked[0][1], "primary_output"
    ranked_outputs = sorted(outputs, key=lambda item: (-int(item["area"]), str(item["name"])))
    return ranked_outputs[0], "largest output"


def plan_apply(outputs: list[dict], config: dict, backend: str) -> dict:
    """Choose an image per output. Reads exact_dir; never writes images."""
    per_output = backend in PER_OUTPUT_BACKENDS
    reason = ""
    chosen = list(outputs) if per_output else []
    if backend == "omarchy-single":
        reference, how = pick_reference_output(outputs, str(config.get("primary_output") or ""))
        chosen = [reference] if reference else []
        if reference:
            reason = (
                f"single image from {how} {reference['name']} "
                f"({reference['orientation']} {reference['logical_width']}x{reference['logical_height']})"
            )
    assignments = []
    for output in chosen:
        image, match = choose_image(output, config)
        assignments.append(
            {
                "name": output["name"],
                "description": output.get("description") or "",
                "index": output.get("index") or 0,
                "logical_width": output["logical_width"],
                "logical_height": output["logical_height"],
                "orientation": output["orientation"],
                "match": match,
                "image": image,
                "fit": "fill",
            }
        )
    return {
        "backend": backend,
        "per_output": per_output,
        "reason": reason,
        "assignments": assignments,
    }


def cover_box(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[int, int, int, int]:
    """Center crop in source pixels that covers dst without stretching."""
    src_w = int(src_w)
    src_h = int(src_h)
    dst_w = max(1, int(dst_w))
    dst_h = max(1, int(dst_h))
    scale = max(dst_w / src_w, dst_h / src_h)
    crop_w = min(src_w, max(1, int(round(dst_w / scale))))
    crop_h = min(src_h, max(1, int(round(dst_h / scale))))
    crop_x = max(0, int(round((src_w - crop_w) / 2)))
    crop_y = max(0, int(round((src_h - crop_h) / 2)))
    if crop_x + crop_w > src_w:
        crop_w = src_w - crop_x
    if crop_y + crop_h > src_h:
        crop_h = src_h - crop_y
    return crop_x, crop_y, crop_w, crop_h


def derived_path(cache: Path, source: Path, dst_w: int, dst_h: int) -> Path:
    digest = hashlib.sha256(f"{source.resolve()}|{dst_w}x{dst_h}".encode()).hexdigest()[:16]
    return cache / f"{digest}-{int(dst_w)}x{int(dst_h)}{source.suffix.lower() or '.img'}"


def _crop_with_pillow(source: Path, dest: Path, dst_w: int, dst_h: int) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    with Image.open(source) as image:
        src_w, src_h = image.size
        crop_x, crop_y, crop_w, crop_h = cover_box(src_w, src_h, dst_w, dst_h)
        cropped = image.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
        if cropped.size != (int(dst_w), int(dst_h)):
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            cropped = cropped.resize((int(dst_w), int(dst_h)), resample)
        dest.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(dest)
    return True


def _crop_with_magick(source: Path, dest: Path, dst_w: int, dst_h: int) -> bool:
    binary = shutil.which("magick") or shutil.which("convert")
    if not binary:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    geometry = f"{int(dst_w)}x{int(dst_h)}"
    # `^` scales to cover; extent center-crops. Aspect is preserved.
    cmd = [binary, str(source), "-resize", f"{geometry}^", "-gravity", "center", "-extent", geometry, str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and dest.is_file()


def materialize_image(
    source: str,
    dst_w: int,
    dst_h: int,
    cache: Path,
    *,
    native_fill: bool,
    cropper=None,
) -> tuple[str, str]:
    """Return (path to set, warning). Never writes the source."""
    src = Path(source)
    if native_fill:
        return str(src), ""
    dest = derived_path(cache, src, dst_w, dst_h)
    if dest.resolve() == src.resolve():
        raise RuntimeError("refusing to write a derived wallpaper over the source")
    if cropper is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cropper(str(src), str(dest), int(dst_w), int(dst_h))
        return str(dest), ""
    if _crop_with_pillow(src, dest, dst_w, dst_h) or _crop_with_magick(src, dest, dst_w, dst_h):
        return str(dest), ""
    return str(src), "no crop tool (Pillow or ImageMagick); passing the source through"


def choose_backend(probes: dict) -> tuple[str, str]:
    """Pick a setter. Per-output daemons win; stock Omarchy is one image."""
    if probes.get("hyprpaper_running"):
        return (
            "hyprpaper",
            "hyprpaper is running; per-output hyprctl hyprpaper (cover)",
        )
    if probes.get("swaybg_running"):
        return (
            "swaybg",
            "swaybg is running; replacing it with one swaybg -o NAME -m fill per output",
        )
    if probes.get("omarchy_set_for_screen"):
        return (
            "omarchy-shell-per-output",
            "omarchy-shell background setForScreen is available",
        )
    if probes.get("omarchy_monitor_flag"):
        return (
            "omarchy-monitor-flag",
            "omarchy-theme-bg-set supports --monitor",
        )
    if probes.get("omarchy_shell_running") or probes.get("omarchy_bg_set"):
        return (
            "omarchy-single",
            "Omarchy sets one background image. Stock omarchy-theme-bg-set "
            "has no per-output flag and this shell has no setForScreen. "
            "Using primary_output when that output is connected, otherwise the largest output.",
        )
    if probes.get("swaybg_available"):
        return (
            "swaybg",
            "no wallpaper owner is running; starting swaybg -o NAME -m fill per output",
        )
    return ("none", "no wallpaper setter found (hyprpaper, swaybg, or omarchy background)")


def argv_for(backend: str, name: str, image: str, probes: dict | None = None) -> list[list[str]]:
    probes = probes or {}
    if backend == "hyprpaper":
        return [
            ["hyprctl", "hyprpaper", "preload", image],
            ["hyprctl", "hyprpaper", "wallpaper", f"{name},{image}"],
        ]
    if backend == "swaybg":
        return [["swaybg", "-o", name, "-i", image, "-m", "fill"]]
    if backend == "omarchy-shell-per-output":
        return [["omarchy-shell", "background", "setForScreen", name, image]]
    if backend == "omarchy-monitor-flag":
        return [["omarchy-theme-bg-set", "--monitor", name, image]]
    if backend == "omarchy-single":
        if probes.get("omarchy_bg_set"):
            return [["omarchy-theme-bg-set", image]]
        return [["omarchy-shell", "background", "set", image]]
    return []


def command_supports_monitor_flag(path: Path) -> bool:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    return "--monitor" in text


def reply_is_available(stdout: str, stderr: str, returncode: int) -> bool:
    if returncode != 0:
        return False
    text = f"{stdout or ''}\n{stderr or ''}".strip().lower()
    if not text:
        return False
    for marker in ("skip", "no such", "unknown", "not available", "not found", "not running"):
        if marker in text:
            return False
    return True


def process_names() -> set[str]:
    names: set[str] = set()
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                names.add((entry / "comm").read_text().strip())
            except OSError:
                continue
        return names
    pgrep = shutil.which("pgrep")
    if not pgrep:
        return names
    for name in ("hyprpaper", "swaybg"):
        try:
            proc = subprocess.run([pgrep, "-x", name], capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            names.add(name)
    return names


def _run(cmd: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def detect_probes() -> dict:
    override = os.environ.get("DESK_WALLPAPER_PROBES")
    if override:
        try:
            data = json.loads(override)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"DESK_WALLPAPER_PROBES: {exc}") from exc
        if not isinstance(data, dict):
            raise SystemExit("DESK_WALLPAPER_PROBES must be an object")
        return data
    names = process_names()
    bg_set = shutil.which("omarchy-theme-bg-set")
    shell = shutil.which("omarchy-shell")
    shell_running = False
    set_for_screen = False
    if shell:
        try:
            ping = _run([shell, "shell", "ping"], timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            ping = None
        if ping is not None:
            shell_running = reply_is_available(ping.stdout or "", ping.stderr or "", ping.returncode)
        if shell_running:
            try:
                listed = _run([shell, "background", "assignments"], timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                listed = None
            if listed is not None:
                set_for_screen = reply_is_available(listed.stdout or "", listed.stderr or "", listed.returncode)
    return {
        "hyprpaper_running": "hyprpaper" in names,
        "hyprpaper_available": "hyprpaper" in names or shutil.which("hyprpaper") is not None,
        "swaybg_running": "swaybg" in names,
        "swaybg_available": "swaybg" in names or shutil.which("swaybg") is not None,
        "omarchy_bg_set": bool(bg_set),
        "omarchy_monitor_flag": bool(bg_set) and command_supports_monitor_flag(Path(bg_set)),
        "omarchy_shell_running": shell_running,
        "omarchy_set_for_screen": set_for_screen,
    }


def enumerate_outputs() -> tuple[list[dict], str | None]:
    override = os.environ.get("DESK_WALLPAPER_OUTPUTS")
    if override:
        try:
            data = json.loads(override)
        except json.JSONDecodeError as exc:
            return [], f"DESK_WALLPAPER_OUTPUTS: {exc}"
        return outputs_from_monitors(data), None
    if SYSTEM == "Darwin":
        return _enumerate_macos()
    return _enumerate_linux()


def _enumerate_linux() -> tuple[list[dict], str | None]:
    hypr = shutil.which("hyprctl")
    if not hypr:
        return [], "hyprctl not found"
    try:
        proc = _run([hypr, "-j", "monitors"], timeout=5.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if proc.returncode != 0:
        try:
            proc = _run([hypr, "monitors", "-j"], timeout=5.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [], str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "hyprctl monitors failed").strip()
        return [], detail
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], f"hyprctl json: {exc}"
    return outputs_from_monitors(data), None


ENUM_JS = r"""
function run() {
  ObjC.import("AppKit");
  const screens = $.NSScreen.screens;
  const out = [];
  for (let i = 0; i < screens.count; i++) {
    const screen = screens.objectAtIndex(i);
    const frame = screen.frame;
    let scale = 1;
    try { scale = screen.backingScaleFactor; } catch (e) { scale = 1; }
    let name = "Display " + (i + 1);
    try {
      const localized = screen.localizedName;
      name = ObjC.unwrap(localized) || name;
    } catch (e) {}
    out.push({
      index: i + 1,
      name: name,
      description: name,
      // Physical pixels (points × backing scale), not the scaled point size.
      width: frame.size.width * scale,
      height: frame.size.height * scale,
      transform: 0
    });
  }
  return JSON.stringify(out);
}
"""

APPLY_JS = r"""
function run() {
  ObjC.import("AppKit");
  ObjC.import("Foundation");
  ObjC.import("stdlib");
  const raw = $.getenv("DESK_WALLPAPER_JOB");
  const job = JSON.parse(raw);
  const screens = $.NSScreen.screens;
  const se = Application("System Events");
  const results = [];
  const items = job.assignments || [];
  for (let n = 0; n < items.length; n++) {
    const item = items[n];
    let idx = -1;
    for (let i = 0; i < screens.count; i++) {
      const screen = screens.objectAtIndex(i);
      let name = "";
      try { name = ObjC.unwrap(screen.localizedName) || ""; } catch (e) { name = ""; }
      if (item.name && name === item.name) {
        idx = i;
        break;
      }
    }
    if (idx < 0 && item.index >= 1 && item.index <= screens.count) {
      idx = item.index - 1;
    }
    const row = {name: item.name || "", picture: false, fill: false, ok: false, error: ""};
    if (idx < 0) {
      row.error = "display not found";
      results.push(row);
      continue;
    }
    try {
      se.desktops[idx].picture = item.path;
      row.picture = true;
    } catch (e) {
      try {
        se.desktops[idx].picture = Path(item.path);
        row.picture = true;
      } catch (e2) {
        row.error = String(e2);
      }
    }
    try {
      const url = $.NSURL.fileURLWithPath($(item.path));
      const screen = screens.objectAtIndex(idx);
      const opts = $.NSMutableDictionary.alloc.init;
      // NSImageScaleProportionallyUpOrDown (3) + clipping = fill/cover.
      // 2 is NSImageScaleNone (center) and must not be used here.
      opts.setObjectForKey($.NSNumber.numberWithInt(3), $.NSWorkspaceDesktopImageScalingKey);
      opts.setObjectForKey($.NSNumber.numberWithBool(true), $.NSWorkspaceDesktopImageAllowClippingKey);
      const err = Ref();
      const filled = $.NSWorkspace.sharedWorkspace.setDesktopImageURLForScreenOptionsError(url, screen, opts, err);
      row.fill = filled ? true : false;
      if (!row.fill && !row.error) {
        try { row.error = String(ObjC.unwrap(err)); } catch (e3) { row.error = "fill failed"; }
      }
    } catch (e) {
      if (!row.error) row.error = String(e);
    }
    row.ok = row.fill || row.picture;
    if (row.picture && !row.fill && !row.error) {
      row.error = "picture set but fill scaling failed";
    }
    results.push(row);
  }
  return JSON.stringify(results);
}
"""


def _osascript(script: str, *, job: dict | None = None, timeout: float = 8.0) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if job is not None:
        env["DESK_WALLPAPER_JOB"] = json.dumps(job)
    return subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _enumerate_macos() -> tuple[list[dict], str | None]:
    if not shutil.which("osascript"):
        return [], "osascript not found"
    try:
        proc = _osascript(ENUM_JS, timeout=6.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        proc = None
        enum_error = str(exc)
    else:
        enum_error = ""
        if proc.returncode == 0:
            try:
                outputs = outputs_from_monitors(json.loads(proc.stdout or "[]"))
            except json.JSONDecodeError:
                outputs = []
            if outputs:
                return outputs, None
        enum_error = (proc.stderr or proc.stdout or "osascript failed").strip()
    profiler = shutil.which("system_profiler")
    if profiler:
        try:
            listed = _run([profiler, "SPDisplaysDataType", "-json"], timeout=15.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [], enum_error or str(exc)
        if listed.returncode == 0:
            try:
                outputs = outputs_from_system_profiler(json.loads(listed.stdout or "{}"))
            except json.JSONDecodeError:
                outputs = []
            if outputs:
                return outputs, None
    return [], enum_error or "no displays"


def _stop_swaybg() -> None:
    root = Path("/proc")
    if root.is_dir():
        for entry in root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                comm = (entry / "comm").read_text().strip()
            except OSError:
                continue
            if comm != "swaybg":
                continue
            try:
                os.kill(int(entry.name), signal.SIGTERM)
            except OSError:
                continue
        return
    pkill = shutil.which("pkill")
    if pkill:
        try:
            subprocess.run([pkill, "-x", "swaybg"], capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return


def _spawn(cmd: list[str]) -> int:
    proc = subprocess.Popen(  # noqa: S603 — argv list, no shell
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return int(proc.pid)


def _macos_apply(assignments: list[dict]) -> dict[str, dict]:
    if not shutil.which("osascript"):
        return {item["name"]: {"ok": False, "error": "osascript not found"} for item in assignments}
    job = {
        "assignments": [
            {"name": item["name"], "index": item.get("index") or 0, "path": item["image"]}
            for item in assignments
        ]
    }
    try:
        proc = _osascript(APPLY_JS, job=job, timeout=20.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {item["name"]: {"ok": False, "error": str(exc)} for item in assignments}
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        detail = (proc.stderr or proc.stdout or "osascript failed").strip()
        return {item["name"]: {"ok": False, "error": detail} for item in assignments}
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "osascript json").strip()
        return {item["name"]: {"ok": False, "error": detail} for item in assignments}
    mapped: dict[str, dict] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("name"):
                mapped[str(row["name"])] = row
    return mapped


def execute(
    plan: dict,
    probes: dict,
    *,
    runner=None,
    spawner=None,
    stopper=None,
    cache: Path | None = None,
    cropper=None,
) -> dict:
    """Apply a plan. Source files are not opened for write."""
    run_cmd = runner or _run
    spawn = spawner or _spawn
    stop = stopper or _stop_swaybg
    store = cache or cache_dir()
    backend = str(plan.get("backend") or "none")
    native_fill = backend in NATIVE_FILL_BACKENDS or SYSTEM == "Darwin"
    applied: list[dict] = []
    if backend == "swaybg":
        stop("swaybg")
    macos_rows: dict[str, dict] = {}
    prepared: list[dict] = []
    for item in plan.get("assignments") or []:
        record = dict(item)
        image = str(item.get("image") or "")
        path = Path(image) if image else None
        if path is None or not path.is_file():
            record["ok"] = False
            record["error"] = f"image not found: {image or '(none)'}"
            applied.append(record)
            continue
        if "," in image and backend == "hyprpaper":
            record["ok"] = False
            record["error"] = "hyprpaper paths cannot contain a comma"
            applied.append(record)
            continue
        try:
            ready, warning = materialize_image(
                image,
                int(item.get("logical_width") or 0),
                int(item.get("logical_height") or 0),
                store,
                native_fill=native_fill and backend != "none",
                cropper=cropper,
            )
        except (OSError, RuntimeError) as exc:
            record["ok"] = False
            record["error"] = str(exc)
            applied.append(record)
            continue
        record["image"] = ready
        if warning:
            record["warning"] = warning
        prepared.append(record)
    if SYSTEM == "Darwin" and prepared and not os.environ.get("DESK_WALLPAPER_PROBES"):
        macos_rows = _macos_apply(prepared)
    if backend == "swaybg":
        for record in prepared:
            cmd = ["swaybg", "-o", record["name"], "-i", record["image"], "-m", "fill"]
            try:
                pid = spawn(cmd)
            except (OSError, subprocess.TimeoutExpired) as exc:
                record["ok"] = False
                record["error"] = str(exc)
            else:
                record["ok"] = True
                record["pid"] = pid
            applied.append(record)
    elif SYSTEM == "Darwin" and macos_rows and not probes:
        for record in prepared:
            row = macos_rows.get(record["name"]) or {}
            record["ok"] = bool(row.get("ok"))
            record["fill"] = bool(row.get("fill"))
            if row.get("error") and not record["ok"]:
                record["error"] = row.get("error")
            elif row.get("error"):
                record["warning"] = row.get("error")
            applied.append(record)
    else:
        for record in prepared:
            commands = argv_for(backend, record["name"], record["image"], probes)
            if not commands:
                record["ok"] = False
                record["error"] = plan.get("reason") or "no wallpaper setter"
                applied.append(record)
                continue
            error = ""
            ok = True
            for index, cmd in enumerate(commands):
                try:
                    proc = run_cmd(cmd, timeout=20.0)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    ok = False
                    error = str(exc)
                    break
                if proc.returncode != 0:
                    # preload can fail when the image is already loaded; wallpaper still runs
                    if backend == "hyprpaper" and index == 0 and len(commands) > 1:
                        continue
                    ok = False
                    error = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
                    break
            record["ok"] = ok
            if error:
                record["error"] = error
            applied.append(record)
    failures = [item for item in applied if not item.get("ok")]
    result = {
        "ok": not failures and backend != "none",
        "enabled": True,
        "backend": backend,
        "per_output": bool(plan.get("per_output")),
        "reason": plan.get("reason") or "",
        "fit": "fill",
        "applied": applied,
    }
    if backend == "none":
        result["ok"] = False
        result["error"] = plan.get("reason") or "no wallpaper setter"
    elif failures:
        result["error"] = "; ".join(
            f"{item.get('name')}: {item.get('error') or 'failed'}" for item in failures
        )
    result["summary"] = _summary(result)
    return result


def _summary(result: dict) -> str:
    if result.get("enabled") is False:
        return "disabled"
    backend = str(result.get("backend") or "wallpaper")
    applied = [item for item in result.get("applied") or [] if isinstance(item, dict) and item.get("ok")]
    if result.get("error") and not applied:
        return str(result["error"])
    if not applied:
        return f"{backend} no outputs"
    if result.get("per_output") is False:
        item = applied[0]
        return f"{backend} {item.get('name')} {item.get('orientation')}"
    noun = "output" if len(applied) == 1 else "outputs"
    return f"{backend} {len(applied)} {noun}"


def public_outputs(outputs: list[dict]) -> list[dict]:
    rows = []
    for item in outputs:
        rows.append(
            {
                "name": item["name"],
                "description": item.get("description") or "",
                "index": item.get("index") or 0,
                "width": item["width"],
                "height": item["height"],
                "transform": item["transform"],
                "logical_width": item["logical_width"],
                "logical_height": item["logical_height"],
                "orientation": item["orientation"],
            }
        )
    return rows


def geometry_payload() -> dict:
    outputs, error = enumerate_outputs()
    if error and not outputs:
        return {"ok": False, "error": error, "outputs": [], "signature": ""}
    return {
        "ok": True,
        "outputs": public_outputs(outputs),
        "signature": geometry_signature(outputs),
    }


def info_payload(config: dict) -> dict:
    if not config.get("enabled"):
        return {"ok": True, "enabled": False, "id": ADAPTER_ID, "applied": [], "summary": "disabled"}
    outputs, error = enumerate_outputs()
    probes: dict = {}
    backend = "none"
    reason = error or ""
    if not error:
        try:
            probes = detect_probes()
            backend, reason = choose_backend(probes)
        except (OSError, SystemExit) as exc:
            error = str(exc)
    plan = plan_apply(outputs, config, backend if not error else "none")
    body = {
        "ok": not error,
        "enabled": True,
        "id": ADAPTER_ID,
        "backend": backend,
        "per_output": backend in PER_OUTPUT_BACKENDS,
        "reason": reason,
        "fit": "fill",
        "signature": geometry_signature(outputs),
        "outputs": plan["assignments"] or public_outputs(outputs),
        "summary": reason or backend,
    }
    if error:
        body["error"] = error
    return body


def apply_payload(config: dict, *, cropper=None, runner=None, spawner=None, stopper=None) -> dict:
    if not config.get("enabled"):
        return {"ok": True, "enabled": False, "applied": [], "summary": "disabled"}
    outputs, error = enumerate_outputs()
    if error and not outputs:
        body = {
            "ok": False,
            "enabled": True,
            "error": error,
            "applied": [],
            "summary": error,
        }
        return body
    try:
        probes = detect_probes()
    except SystemExit as exc:
        return {"ok": False, "enabled": True, "error": str(exc), "applied": [], "summary": str(exc)}
    backend, reason = choose_backend(probes)
    if SYSTEM == "Darwin" and not os.environ.get("DESK_WALLPAPER_PROBES"):
        backend = "macos"
        reason = "per-display osascript (System Events picture, then NSWorkspace fill)"
    plan = plan_apply(outputs, config, backend if backend != "macos" else "hyprpaper")
    if backend == "macos":
        plan["backend"] = "macos"
        plan["per_output"] = True
        plan["reason"] = reason
    else:
        plan["reason"] = reason
    if backend == "none":
        return {
            "ok": False,
            "enabled": True,
            "backend": backend,
            "error": reason,
            "reason": reason,
            "applied": [],
            "summary": reason,
        }
    if not plan["assignments"]:
        return {
            "ok": True,
            "enabled": True,
            "backend": backend,
            "per_output": plan["per_output"],
            "reason": reason,
            "applied": [],
            "summary": f"{backend} no outputs",
            "fit": "fill",
        }
    result = execute(
        plan,
        probes if backend != "macos" else {},
        runner=runner,
        spawner=spawner,
        stopper=stopper,
        cropper=cropper,
    )
    result["reason"] = reason
    result["summary"] = _summary(result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", nargs="?", default="apply", help="apply | status | info | geometry")
    parser.add_argument("--config-json", default=None, help="wallpaper object JSON from desk")
    parser.add_argument("--json", action="store_true", help="accepted; output is always JSON")
    parser.add_argument("--desk-switch-manifest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.desk_switch_manifest or args.verb == "--desk-switch-manifest":
        print(json.dumps(manifest(), indent=2))
        return 0
    verb = str(args.verb or "apply").strip().lower()
    if verb not in ("apply", "status", "info", "geometry", "probe"):
        print(json.dumps({"ok": False, "error": f"unknown verb: {args.verb}"}), file=sys.stderr)
        return 2
    if verb == "geometry":
        payload = geometry_payload()
    else:
        config = config_from_args(args.config_json)
        if verb in ("status", "info", "probe"):
            payload = info_payload(config)
        else:
            payload = apply_payload(config)
    print(json.dumps(payload, indent=2))
    if payload.get("enabled") is False:
        return 0
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
