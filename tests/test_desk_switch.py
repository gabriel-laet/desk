#!/usr/bin/env python3
"""Unit tests for desk-switch host mapping and CLI plumbing (no HID required)."""

from __future__ import annotations

import importlib.util
import io
import json


import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("desk_switch", ROOT / "desk-switch.py")
ds = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ds)


class HostMappingTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual(ds.normalize_host("macos"), "mac")
        self.assertEqual(ds.normalize_host("LNX"), "linux")
        self.assertEqual(ds.normalize_host("omarchy"), "linux")
        with self.assertRaises(SystemExit):
            ds.normalize_host("windows")

    def test_legacy_target_channel_overrides_other_host(self) -> None:
        cfg = ds.resolve_hosts(
            {"this_host": "linux", "target_channel": 1},
            {},
        )
        self.assertEqual(cfg["mac"]["channel"], 1)
        self.assertEqual(cfg["linux"]["channel"], 2)

    def test_explicit_hosts_keep_channels(self) -> None:
        cfg = ds.resolve_hosts(
            {
                "this_host": "mac",
                "target_channel": 2,
                "hosts": {"mac": {"channel": 1}, "linux": {"channel": 3}},
            }
        )
        self.assertEqual(cfg["linux"]["channel"], 3)  # user host channel wins
        self.assertEqual(cfg["mac"]["channel"], 1)

    def test_int_host_entry(self) -> None:
        cfg = ds.resolve_hosts({"this_host": "mac", "hosts": {"linux": 3}, "target_channel": 3})
        self.assertEqual(cfg["linux"]["channel"], 3)

    def test_hint_from_mouse_channel(self) -> None:
        cfg = {
            "this_host": "linux",
            "hosts": {"mac": {"channel": 1}, "linux": {"channel": 2}},
        }
        self.assertEqual(ds.target_hint(cfg, 1, False), "MAC")
        self.assertEqual(ds.target_hint(cfg, 2, False), "LNX")
        self.assertEqual(ds.target_hint(cfg, None, True), "LNX")
        self.assertEqual(ds.target_hint(cfg, None, False), "?")

    def test_default_linux_hosts_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_home = Path(tmp)
            with mock.patch.object(ds, "SYSTEM", "Linux"), mock.patch.object(
                ds, "CONFIG_CANDIDATES", (env_home / "none.json",)
            ), mock.patch.object(ds, "default_this_host", return_value="linux"):
                cfg = ds.load_config()
        self.assertEqual(cfg["this_host"], "linux")
        self.assertEqual(cfg["target_channel"], 1)
        self.assertEqual(cfg["hosts"]["mac"]["channel"], 1)
        self.assertEqual(cfg["hosts"]["linux"]["channel"], 2)

    def test_parse_mouse_channel(self) -> None:
        self.assertEqual(ds.parse_mouse_channel("channels   : 3, currently on 2"), 2)
        self.assertIsNone(ds.parse_mouse_channel("no mouse"))


class CliTests(unittest.TestCase):
    def _run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "desk-switch.py"), *args],
            capture_output=True,
            text=True,
            env=env or os.environ.copy(),
        )

    def test_help(self) -> None:
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("to mac", proc.stdout)
        self.assertIn("watch", proc.stdout)

    def test_legacy_wrapper_help(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "hhkb-mx-follow.py"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("watch", proc.stdout)

    def test_pbp_without_lgdualup_is_noop(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"lgdualup": "lgdualup-missing"}))
            env["HOME"] = str(home)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "pbp", "50-50"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("lgdualup not on PATH", proc.stdout)

    def test_full_without_lgdualup_is_noop(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env["HOME"] = str(home)
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"lgdualup": "lgdualup-missing"}))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "full"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("lgdualup not on PATH", proc.stdout)

    def test_status_json_and_hint(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env["HOME"] = str(home)
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(
                json.dumps({
                    "this_host": "linux",
                    "target_channel": 1,
                    "mxswitch": "/no/such/mxswitch",
                    "lgdualup": "lgdualup-missing",
                })
            )
            hint = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "status", "--hint"],
                capture_output=True,
                text=True,
                env=env,
            )
            js = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "status", "--json"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(hint.returncode, 0)
        self.assertIn(hint.stdout.strip(), {"MAC", "LNX", "?"})
        self.assertEqual(js.returncode, 0)
        data = json.loads(js.stdout)
        self.assertIn("target_hint", data)
        self.assertIn("lgdualup", data)
        self.assertFalse(data["lgdualup"])
        self.assertIn("adapters", data)
        self.assertFalse(data["adapters"]["mouse"]["available"])
        self.assertFalse(data["adapters"]["dualup"]["available"])
        self.assertEqual(data["adapters"]["hosts"]["this_host"], "linux")
        self.assertEqual(data["adapters"]["mouse"]["backend"], "mxswitch")
        self.assertEqual(data["adapters"]["dualup"]["backend"], "lgdualup")

    def test_switch_rejects_bad_channel(self) -> None:
        proc = self._run("switch", "9")
        self.assertNotEqual(proc.returncode, 0)

    def test_to_unknown_host(self) -> None:
        proc = self._run("to", "sparc")
        self.assertNotEqual(proc.returncode, 0)


class AdapterConfigTests(unittest.TestCase):
    def test_adapters_overlay_hosts_and_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "adapters": {
                            "mouse": {"enabled": True, "path": "/opt/mxswitch"},
                            "hosts": {
                                "this_host": "linux",
                                "follow_channel": 3,
                                "mac": {"channel": 1},
                                "linux": {"channel": 3},
                            },
                            "dualup": {
                                "enabled": True,
                                "inputs": {"mac": "usbc", "linux": "dp"},
                            },
                        }
                    }
                )
            )
            with mock.patch.object(ds, "SYSTEM", "Linux"), mock.patch.object(
                ds, "CONFIG_CANDIDATES", (cfg_path,)
            ), mock.patch.object(ds, "default_this_host", return_value="linux"):
                cfg = ds.load_config()
        self.assertEqual(cfg["this_host"], "linux")
        self.assertEqual(cfg["target_channel"], 3)
        self.assertEqual(cfg["hosts"]["linux"]["channel"], 3)
        self.assertEqual(cfg["hosts"]["mac"]["dualup_input"], "usbc")
        self.assertEqual(cfg["hosts"]["linux"]["dualup_input"], "dp")
        self.assertEqual(cfg["mxswitch"], "/opt/mxswitch")
        self.assertTrue(cfg["_mouse_enabled"])
        self.assertTrue(cfg["_dualup_enabled"])

    def test_legacy_keys_still_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "this_host": "mac",
                        "target_channel": 2,
                        "mxswitch": "/old/mxswitch",
                        "lgdualup": "/old/lgdualup",
                        "hosts": {
                            "mac": {"channel": 1, "dualup_input": "hdmi1"},
                            "linux": {"channel": 2},
                        },
                    }
                )
            )
            with mock.patch.object(ds, "SYSTEM", "Darwin"), mock.patch.object(
                ds, "CONFIG_CANDIDATES", (cfg_path,)
            ), mock.patch.object(ds, "default_this_host", return_value="mac"):
                cfg = ds.load_config()
        self.assertEqual(cfg["this_host"], "mac")
        self.assertEqual(cfg["target_channel"], 2)
        self.assertEqual(cfg["mxswitch"], "/old/mxswitch")
        self.assertEqual(cfg["lgdualup"], "/old/lgdualup")
        self.assertEqual(cfg["hosts"]["mac"]["dualup_input"], "hdmi1")

    def test_dualup_adapter_can_be_disabled(self) -> None:
        self.assertIsNone(ds.lgdualup_path({"_dualup_enabled": False, "lgdualup": "/tmp/lgdualup"}))

    def test_which_adapter_prefers_libexec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "lib"
            lib.mkdir()
            helper = lib / "lgdualup"
            helper.write_text("#!/bin/sh\n")
            helper.chmod(0o755)
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                path = ds.which_adapter("lgdualup", "lgdualup")
        self.assertEqual(path, helper)

    def test_which_adapter_honors_explicit_missing_name(self) -> None:
        with mock.patch.object(ds, "libexec_dir", return_value=Path("/no/libexec")):
            self.assertIsNone(ds.which_adapter("lgdualup", "lgdualup-missing"))


class MenubarSourceTests(unittest.TestCase):
    def test_menubar_only_invokes_desk_switch(self) -> None:
        src = (ROOT / "macos" / "DeskSwitchBar" / "DeskSwitchBar.swift").read_text()
        self.assertIn('["status", "--json"]', src)
        self.assertIn('["to", "mac"]', src)
        self.assertIn('["to", "linux"]', src)
        self.assertIn('["full"]', src)
        self.assertIn('["pbp"]', src)
        self.assertIn("desk-switch", src)
        self.assertNotIn("lib/desk-switch/mxswitch", src)
        self.assertNotIn("lib/desk-switch/lgdualup", src)


class LgdualupCallTests(unittest.TestCase):
    def test_calls_configured_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "lgdualup"
            helper.write_text("#!/bin/sh\necho called:\"$@\"\n")
            helper.chmod(0o755)
            cfg = {"lgdualup": str(helper)}
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                rc = ds.call_lgdualup(cfg, ["pbp", "full"], missing="missing")
            self.assertEqual(rc, 0)
            self.assertIn("called:pbp full", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
