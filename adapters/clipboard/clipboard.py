#!/usr/bin/env python3
"""Local clipboard read/write. Text first. Images are optional.

Out of process. Core never names pbcopy, wl-copy, or a pasteboard type.
The helper prints JSON and never logs the bytes it moved.

    clipboard read [--max-bytes N] [--images]
    clipboard write [--max-bytes N]

`write` reads a JSON object on stdin: kind, mime, text, and optional b64.
Sensitive items are reported and not returned. Over-size items are refused.

Capabilities: clipboard.read, clipboard.write.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ADAPTER_ID = "clipboard"
ADAPTER_API_VERSION = 1
CAPABILITIES = ["clipboard.read", "clipboard.write"]
TEXT_MIME = "text/plain"
PNG_MIME = "image/png"
SECRET_MIME = "x-kde-passwordManagerHint"


def manifest() -> dict:
    return {
        "api_version": ADAPTER_API_VERSION,
        "id": ADAPTER_ID,
        "name": "Local clipboard",
        "capabilities": list(CAPABILITIES),
    }


def discover_session_env() -> dict[str, str]:
    """Fill in the Wayland session when this process was started from SSH or a unit.

    `XDG_RUNTIME_DIR` defaults to `/run/user/<uid>`. `WAYLAND_DISPLAY` is the
    first `wayland-*` socket there. A Hyprland signature directory is picked
    up the same way when it is unset.
    """
    found: dict[str, str] = {}
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not os.environ.get("XDG_RUNTIME_DIR"):
        found["XDG_RUNTIME_DIR"] = runtime
    if not os.environ.get("WAYLAND_DISPLAY"):
        display = _first_wayland_display(runtime)
        if display:
            found["WAYLAND_DISPLAY"] = display
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        signature = _first_hypr_signature(runtime)
        if signature:
            found["HYPRLAND_INSTANCE_SIGNATURE"] = signature
    return found


def _first_wayland_display(runtime: str) -> str:
    try:
        sockets = sorted(
            path.name
            for path in Path(runtime).glob("wayland-*")
            if path.is_socket() and not path.name.endswith(".lock")
        )
    except OSError:
        return ""
    return sockets[0] if sockets else ""


def _first_hypr_signature(runtime: str) -> str:
    root = Path(runtime) / "hypr"
    try:
        names = sorted(path.name for path in root.iterdir() if path.is_dir())
    except OSError:
        return ""
    return names[0] if names else ""


def ensure_session_env() -> dict[str, str]:
    found = discover_session_env()
    for key, value in found.items():
        os.environ.setdefault(key, value)
    return found


def _command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(ensure_session_env())
    return env


def run_cmd(argv: list[str], input_text: str | None = None, timeout: float = 3.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_command_env(),
    )


def _backend() -> str:
    if sys.platform == "darwin":
        return "macos"
    ensure_session_env()
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DESK_CLIPBOARD_BACKEND") == "wayland":
        return "wayland"
    return "none"


def _too_big(size: int, max_bytes: int) -> dict:
    return {"ok": False, "error": "too-large", "bytes": size, "sensitive": False}


def _sensitive(size: int = 0) -> dict:
    return {"ok": True, "sensitive": True, "bytes": size, "kind": "text", "mime": TEXT_MIME, "text": ""}


def _empty() -> dict:
    return {"ok": True, "sensitive": False, "bytes": 0, "kind": "text", "mime": TEXT_MIME, "text": ""}


def macos_types() -> str:
    try:
        proc = run_cmd(["osascript", "-e", "clipboard info"], timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return f"{proc.stdout or ''}{proc.stderr or ''}"


def macos_is_sensitive(info: str) -> bool:
    lowered = info.lower()
    return "concealedtype" in lowered or "transienttype" in lowered


def wayland_types() -> list[str]:
    try:
        proc = run_cmd(["wl-paste", "--list-types"], timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def wayland_is_secret(types: list[str]) -> bool:
    if SECRET_MIME not in types:
        return False
    try:
        proc = run_cmd(["wl-paste", "--no-newline", "--type", SECRET_MIME], timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (proc.stdout or "").strip() == "secret"


def read_local(max_bytes: int, images: bool) -> dict:
    backend = _backend()
    if backend == "macos":
        info = macos_types()
        if macos_is_sensitive(info):
            return _sensitive()
        try:
            proc = run_cmd(["pbpaste"], timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "error": "unavailable", "sensitive": False, "bytes": 0}
        text = proc.stdout or ""
        size = len(text.encode("utf-8"))
        if size > max_bytes:
            return _too_big(size, max_bytes)
        if text:
            return {
                "ok": True,
                "sensitive": False,
                "kind": "text",
                "mime": TEXT_MIME,
                "text": text,
                "bytes": size,
            }
        return _empty()
    if backend == "wayland":
        types = wayland_types()
        if wayland_is_secret(types):
            return _sensitive()
        text = ""
        for mime in ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "TEXT"):
            if types and mime not in types and mime.split(";", 1)[0] not in types:
                continue
            try:
                proc = run_cmd(["wl-paste", "--no-newline", "--type", mime], timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if proc.returncode == 0 and proc.stdout:
                text = proc.stdout
                break
        if text:
            size = len(text.encode("utf-8"))
            if size > max_bytes:
                return _too_big(size, max_bytes)
            return {
                "ok": True,
                "sensitive": False,
                "kind": "text",
                "mime": TEXT_MIME,
                "text": text,
                "bytes": size,
            }
        if images and PNG_MIME in types:
            try:
                raw = subprocess.run(
                    ["wl-paste", "--no-newline", "--type", PNG_MIME],
                    capture_output=True,
                    timeout=2.0,
                    check=False,
                    env=_command_env(),
                )
            except (OSError, subprocess.TimeoutExpired):
                raw = None
            if raw is not None and raw.returncode == 0 and raw.stdout:
                if len(raw.stdout) > max_bytes:
                    return _too_big(len(raw.stdout), max_bytes)
                return {
                    "ok": True,
                    "sensitive": False,
                    "kind": "image",
                    "mime": PNG_MIME,
                    "b64": base64.b64encode(raw.stdout).decode("ascii"),
                    "bytes": len(raw.stdout),
                    "text": "",
                }
        return _empty()
    return {"ok": False, "error": "no-backend", "sensitive": False, "bytes": 0}


def _wayland_readback_ok(mime: str, expect_text: str, raw: bytes) -> bool:
    try:
        proc = subprocess.run(
            ["wl-paste", "--no-newline", "--type", mime],
            capture_output=True,
            timeout=2.0,
            check=False,
            env=_command_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    if mime == TEXT_MIME:
        return proc.stdout.decode("utf-8", "replace") == expect_text
    return proc.stdout == raw


def _wayland_copy(data: bytes, mime: str, size: int, expect_text: str) -> dict:
    """Hand the bytes to wl-copy without waiting for it to finish serving.

    wl-copy stays up to own the selection and would hold an SSH session
    open. Detach it, then treat the write as ok only when wl-paste reads
    the same bytes back.
    """
    try:
        proc = subprocess.Popen(
            ["wl-copy", "--type", mime],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_command_env(),
        )
    except OSError:
        return {"ok": False, "error": "unavailable", "bytes": size}
    try:
        if proc.stdin is not None:
            proc.stdin.write(data)
            proc.stdin.close()
    except (OSError, BrokenPipeError):
        pass
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass
    if _wayland_readback_ok(mime, expect_text, data):
        kind = "image" if mime == PNG_MIME else "text"
        return {"ok": True, "bytes": size, "kind": kind}
    return {"ok": False, "error": "write-failed", "bytes": size}


def write_local(payload: dict, max_bytes: int) -> dict:
    kind = str(payload.get("kind") or "text")
    if payload.get("sensitive"):
        return {"ok": False, "error": "sensitive", "bytes": 0}
    backend = _backend()
    if kind == "image":
        raw_b64 = str(payload.get("b64") or "")
        try:
            raw = base64.b64decode(raw_b64.encode("ascii"), validate=False)
        except (ValueError, UnicodeError):
            return {"ok": False, "error": "bad-image", "bytes": 0}
        if len(raw) > max_bytes:
            return _too_big(len(raw), max_bytes)
        if backend != "wayland":
            return {"ok": False, "error": "images-unsupported", "bytes": len(raw)}
        return _wayland_copy(raw, PNG_MIME, len(raw), "")
    text = payload.get("text")
    if not isinstance(text, str):
        return {"ok": False, "error": "no-text", "bytes": 0}
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        return _too_big(size, max_bytes)
    if backend == "macos":
        try:
            proc = run_cmd(["pbcopy"], input_text=text, timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "error": "unavailable", "bytes": size}
        if proc.returncode != 0:
            return {"ok": False, "error": "write-failed", "bytes": size}
        return {"ok": True, "bytes": size, "kind": "text"}
    if backend == "wayland":
        return _wayland_copy(text.encode("utf-8"), TEXT_MIME, size, text)
    return {"ok": False, "error": "no-backend", "bytes": size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", nargs="?", default="read", help="read | write")
    parser.add_argument("--max-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--images", action="store_true")
    parser.add_argument("--desk-switch-manifest", action="store_true")
    args = parser.parse_args(argv)
    if args.desk_switch_manifest or args.verb == "--desk-switch-manifest":
        print(json.dumps(manifest(), indent=2))
        return 0
    verb = str(args.verb or "read").strip().lower()
    if verb == "read":
        print(json.dumps(read_local(max(1, args.max_bytes), bool(args.images))))
        return 0
    if verb == "write":
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            print(json.dumps({"ok": False, "error": "bad-json", "bytes": 0}))
            return 1
        if not isinstance(payload, dict):
            print(json.dumps({"ok": False, "error": "bad-json", "bytes": 0}))
            return 1
        result = write_local(payload, max(1, args.max_bytes))
        print(json.dumps(result))
        return 0 if result.get("ok") else 1
    print(json.dumps({"ok": False, "error": "unknown-verb", "bytes": 0}))
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
