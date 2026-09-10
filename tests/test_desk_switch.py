#!/usr/bin/env python3
"""Unit tests for desk-switch host mapping and CLI plumbing (no HID required)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
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
        self.assertIn("kettle", proc.stdout)
        self.assertIn("weather", proc.stdout)
        self.assertIn("smarthome", proc.stdout)

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
        env["DESK_SWITCH_WEATHER_URL"] = ""
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
        self.assertEqual(data["adapters"]["display"]["backend"], "lgdualup")
        self.assertIn("keyboard", data["adapters"])
        self.assertIn("smarthome", data["adapters"])
        self.assertIn("discovered", data["adapters"])
        self.assertEqual(data["adapters"]["smarthome"]["backend"], "alexa")
        self.assertNotIn("lights", data["bar_strip"])
        self.assertIn("bar_strip", data)
        self.assertIn("focus", data["bar_strip"])
        self.assertIn("slots", data)
        self.assertIsInstance(data["slots"], list)
        self.assertIn("kettle", data["adapters"])
        self.assertIn("weather", data["adapters"])
        self.assertEqual(data["ui"]["tray"]["density"], "strip")

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
                                "inputs": {"mac": "hdmi1", "linux": "dp"},
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
        self.assertEqual(cfg["hosts"]["mac"]["dualup_input"], "hdmi1")
        self.assertEqual(cfg["hosts"]["linux"]["dualup_input"], "dp")
        self.assertEqual(cfg["mxswitch"], "/opt/mxswitch")
        self.assertTrue(cfg["_mouse_enabled"])
        self.assertTrue(cfg["_dualup_enabled"])

    def test_display_alias_and_backend_and_density(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "adapters": {
                            "mouse": {"backend": "dummy-mouse"},
                            "keyboard": {"backend": "hhkb"},
                            "display": {
                                "enabled": True,
                                "backend": "lgdualup",
                                "inputs": {"mac": "hdmi1", "linux": "dp"},
                            },
                        },
                        "ui": {"tray": {"density": "chips"}},
                    }
                )
            )
            with mock.patch.object(ds, "CONFIG_CANDIDATES", (cfg_path,)):
                cfg = ds.load_config()
        self.assertEqual(cfg["_mouse_backend"], "dummy-mouse")
        self.assertEqual(cfg["_display_backend"], "lgdualup")
        self.assertEqual(cfg["_keyboard_backend"], "hhkb")
        self.assertEqual(cfg["hosts"]["linux"]["dualup_input"], "dp")
        self.assertEqual(ds.tray_density(cfg), "chips")

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
        self.assertIn('["layout"]', src)
        self.assertIn("⌘⌥⇧F", src)
        self.assertIn("⌘⌥⇧P", src)
        self.assertIn("⌘⌥U", src)
        self.assertIn("desk-switch", src)
        self.assertIn("bar_label", src)
        self.assertIn("bar_strip", src)
        self.assertIn("stripTitle", src)
        self.assertIn("density", src)
        self.assertIn("slots", src)
        self.assertIn("MenuBarStatusItemRenderer", src)
        self.assertIn("NSImage", src)
        self.assertIn("SlotGlyphMap", src)
        self.assertIn("SlotHUD", src)
        self.assertIn("TraySettingsPanel", src)
        self.assertIn("DeskUIConfig", src)
        self.assertIn("DeskUIConfigStore", src)
        self.assertIn("Configure tray", src)
        self.assertIn("applyOptimisticLight", src)
        self.assertIn("placeholderLights", src)
        self.assertIn("onMove", src)
        self.assertIn(".config/desk-switch", src)
        self.assertIn("show_altitude", src)
        self.assertIn("show_faces", src)
        self.assertNotIn('slot.id == "kettle"', src)
        self.assertNotIn('slot.id == "weather"', src)
        self.assertNotIn("Fellow", src)
        self.assertIn("hhkb_usb", src)
        self.assertIn("hhkb_transport", src)
        self.assertIn("USB on this host", src)
        self.assertNotIn("lib/desk-switch/mxswitch", src)
        self.assertNotIn("lib/desk-switch/lgdualup", src)


class BarWidgetSourceTests(unittest.TestCase):
    def test_omarchy_uses_shared_bar_label(self) -> None:
        bar = (ROOT / "linux" / "omarchy" / "BarWidget.qml").read_text()
        panel = (ROOT / "linux" / "omarchy" / "Panel.qml").read_text()
        manifest = json.loads((ROOT / "manifest.json").read_text())
        self.assertEqual(manifest["entryPoints"]["barWidget"], "linux/omarchy/BarWidget.qml")
        self.assertIn("bar_label", bar)
        self.assertIn("bar_strip", bar)
        self.assertIn("stripTitle", bar)
        self.assertIn("slots", bar)
        self.assertIn("slotsTitle", bar)
        self.assertIn("slots", panel)
        self.assertIn("uiConfig", bar)
        self.assertIn("uiConfig", panel)
        self.assertIn("TODO: Omarchy drag-reorder", bar)
        self.assertIn("faceSlotLabel", panel)
        self.assertIn("slotActions", panel)
        self.assertIn("hhkb_usb", bar)
        self.assertIn("status --json", bar)
        self.assertIn("USB on this host", panel)
        self.assertIn("BT only", panel)
        self.assertIn("StatusChip", panel)
        self.assertIn("⌘⌥⇧F", panel)
        self.assertIn("⌘⌥⇧P", panel)
        self.assertIn("layout", panel)


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


class DualupAdapterTests(unittest.TestCase):
    def _cfg(self, lg: Path, layout: Path, **extra: object) -> dict:
        cfg: dict = {
            "lgdualup": str(lg),
            "_dualup_enabled": True,
            "_dualup_layout": True,
            "_dualup_layout_helper": str(layout),
            "_dualup_layout_retries": 3,
            "_dualup_layout_retry_delay_s": 0,
            "_dualup_layout_settle_s": 0,
            "pbp_mode": "50-50",
            "hosts": {
                "mac": {"channel": 1, "dualup_input": "hdmi1"},
                "linux": {"channel": 2, "dualup_input": "dp"},
            },
        }
        cfg.update(extra)
        return cfg

    def _helpers(self, tmp: str) -> tuple[Path, Path, Path]:
        root = Path(tmp)
        lg = root / "lgdualup"
        lg.write_text("#!/bin/sh\necho usb:\"$@\"\n")
        lg.chmod(0o755)
        layout = root / "dualup-layout"
        layout.write_text("#!/bin/sh\necho layout:\"$@\"\n")
        layout.chmod(0o755)
        log = root / "calls.log"
        lg.write_text("#!/bin/sh\necho usb:\"$@\" | tee -a \"%s\"\n" % log)
        lg.chmod(0o755)
        layout.write_text("#!/bin/sh\necho layout:\"$@\" | tee -a \"%s\"\n" % log)
        layout.chmod(0o755)
        return lg, layout, log

    def test_layout_verb(self) -> None:
        self.assertEqual(ds.layout_verb("full"), "full")
        self.assertEqual(ds.layout_verb("off"), "full")
        self.assertEqual(ds.layout_verb("50-50"), "pbp")
        self.assertEqual(ds.layout_verb("on"), "pbp")

    def test_pbp_input_order_is_dp_then_hdmi1(self) -> None:
        cfg = {
            "hosts": {
                "mac": {"dualup_input": "hdmi1"},
                "linux": {"dualup_input": "dp"},
            }
        }
        self.assertEqual(ds.dualup_pbp_inputs(cfg), [("linux", "dp"), ("mac", "hdmi1")])

    def test_pbp_input_defaults_when_empty(self) -> None:
        self.assertEqual(
            ds.dualup_pbp_inputs({"hosts": {"mac": {}, "linux": {}}}),
            [("linux", "dp"), ("mac", "hdmi1")],
        )

    def test_pbp_invokes_usb_inputs_then_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lg, layout, _log = self._helpers(tmp)
            cfg = self._cfg(lg, layout)
            calls: list[list[str]] = []

            def fake_run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="ok:" + " ".join(cmd[1:]), stderr="")

            buf = io.StringIO()
            with mock.patch.object(ds, "run", side_effect=fake_run), mock.patch(
                "sys.stdout", buf
            ), mock.patch.object(ds, "lgdualup_path", return_value=lg), mock.patch.object(
                ds, "dualup_layout_path", return_value=layout
            ), mock.patch.object(ds.time, "sleep"):
                rc = ds.cmd_pbp(cfg, None)
            self.assertEqual(rc, 0)
            self.assertEqual(calls[0][1:], ["pbp", "50-50"])
            self.assertEqual(calls[1][1:], ["pbp-assign", "hdmi1", "dp"])
            self.assertEqual(calls[2], [str(layout), "pbp"])
            self.assertIn("ok:pbp 50-50", buf.getvalue())
            self.assertIn("ok:pbp", buf.getvalue())

    def test_full_invokes_usb_then_layout_without_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lg, layout, _log = self._helpers(tmp)
            cfg = self._cfg(lg, layout)
            calls: list[list[str]] = []

            def fake_run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

            with mock.patch.object(ds, "run", side_effect=fake_run), mock.patch(
                "sys.stdout", io.StringIO()
            ), mock.patch.object(ds, "lgdualup_path", return_value=lg), mock.patch.object(
                ds, "dualup_layout_path", return_value=layout
            ):
                rc = ds.cmd_full(cfg)
            self.assertEqual(rc, 0)
            self.assertEqual(calls[0][1:], ["pbp", "full"])
            self.assertEqual(calls[1], [str(layout), "full"])
            self.assertEqual(len(calls), 2)

    def test_full_settles_before_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lg, layout, _log = self._helpers(tmp)
            cfg = self._cfg(lg, layout, _dualup_layout_settle_s=0.4)
            order: list[str] = []

            def fake_run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
                if cmd[0] == str(lg):
                    order.append("usb")
                else:
                    order.append("layout")
                return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

            def fake_sleep(seconds: float) -> None:
                order.append(f"sleep:{seconds}")

            with mock.patch.object(ds, "run", side_effect=fake_run), mock.patch(
                "sys.stdout", io.StringIO()
            ), mock.patch.object(ds, "lgdualup_path", return_value=lg), mock.patch.object(
                ds, "dualup_layout_path", return_value=layout
            ), mock.patch.object(ds.time, "sleep", side_effect=fake_sleep):
                rc = ds.cmd_full(cfg)
            self.assertEqual(rc, 0)
            self.assertEqual(order, ["usb", "sleep:0.4", "layout"])

    def test_pbp_settles_after_assign_before_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lg, layout, _log = self._helpers(tmp)
            cfg = self._cfg(lg, layout, _dualup_layout_settle_s=0.4)
            order: list[str] = []

            def fake_run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
                if cmd[0] == str(lg) and cmd[1:2] == ["pbp-assign"]:
                    order.append("assign")
                elif cmd[0] == str(lg):
                    order.append("usb")
                else:
                    order.append("layout")
                return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

            def fake_sleep(seconds: float) -> None:
                order.append(f"sleep:{seconds}")

            with mock.patch.object(ds, "run", side_effect=fake_run), mock.patch(
                "sys.stdout", io.StringIO()
            ), mock.patch.object(ds, "lgdualup_path", return_value=lg), mock.patch.object(
                ds, "dualup_layout_path", return_value=layout
            ), mock.patch.object(ds.time, "sleep", side_effect=fake_sleep):
                rc = ds.cmd_pbp(cfg, "50-50")
            self.assertEqual(rc, 0)
            self.assertEqual(order, ["usb", "assign", "sleep:0.4", "layout"])

    def test_layout_retries_when_edid_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lg, layout, _log = self._helpers(tmp)
            cfg = self._cfg(lg, layout, _dualup_display_id="ABCD")
            results = [
                subprocess.CompletedProcess(["lg"], 0, "usb", ""),
                subprocess.CompletedProcess(["lay"], 2, "", "not yet"),
                subprocess.CompletedProcess(["lay"], 0, "applied", ""),
            ]

            def fake_run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
                return results.pop(0)

            sleeps: list[float] = []
            with mock.patch.object(ds, "run", side_effect=fake_run), mock.patch(
                "sys.stdout", io.StringIO()
            ), mock.patch("sys.stderr", io.StringIO()), mock.patch.object(
                ds, "lgdualup_path", return_value=lg
            ), mock.patch.object(ds, "dualup_layout_path", return_value=layout), mock.patch.object(
                ds.time, "sleep", side_effect=sleeps.append
            ):
                rc = ds.cmd_full(cfg)
            self.assertEqual(rc, 0)
            self.assertEqual(sleeps, [0])

    def test_layout_helper_gets_display_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lg, layout, _log = self._helpers(tmp)
            cfg = self._cfg(lg, layout, _dualup_display_id="9134432D-0196-4653-9712-EFCAF1980612")
            calls: list[list[str]] = []

            def fake_run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch.object(ds, "run", side_effect=fake_run), mock.patch(
                "sys.stdout", io.StringIO()
            ), mock.patch.object(ds, "lgdualup_path", return_value=lg), mock.patch.object(
                ds, "dualup_layout_path", return_value=layout
            ):
                ds.cmd_full(cfg)
            self.assertEqual(
                calls[1],
                [str(layout), "full", "--id", "9134432D-0196-4653-9712-EFCAF1980612"],
            )

    def test_layout_disabled_skips_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lg, layout, _log = self._helpers(tmp)
            cfg = self._cfg(lg, layout, _dualup_layout=False)
            calls: list[list[str]] = []

            def fake_run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch.object(ds, "run", side_effect=fake_run), mock.patch(
                "sys.stdout", io.StringIO()
            ), mock.patch.object(ds, "lgdualup_path", return_value=lg):
                rc = ds.cmd_full(cfg)
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1:], ["pbp", "full"])

    def test_pbp_without_lgdualup_does_not_call_layout(self) -> None:
        layout = mock.Mock()
        cfg = {"lgdualup": "lgdualup-missing", "_dualup_enabled": True, "pbp_mode": "50-50"}
        with mock.patch.object(ds, "lgdualup_path", return_value=None), mock.patch.object(
            ds, "apply_dualup_layout", layout
        ), mock.patch("sys.stdout", io.StringIO()):
            rc = ds.cmd_pbp(cfg, "50-50")
        self.assertEqual(rc, 0)
        layout.assert_not_called()

    def test_example_config_is_hdmi1_and_dp(self) -> None:
        example = json.loads((ROOT / "config.example.json").read_text())
        inputs = example["adapters"]["dualup"]["inputs"]
        self.assertEqual(inputs["mac"], "hdmi1")
        self.assertEqual(inputs["linux"], "dp")
        self.assertNotEqual(inputs["mac"], "usbc")
        self.assertEqual(example["adapters"]["smarthome"]["backend"], "alexa")
        self.assertEqual(example["adapters"]["smarthome"]["device"], "Escritório")
        self.assertEqual(example["adapters"]["kettle"]["backend"], "kettle")
        self.assertEqual(example["adapters"]["kettle"]["host"], "192.168.3.36")
        self.assertEqual(example["adapters"]["weather"]["backend"], "weather")
        self.assertFalse(example["ui"]["tray"]["lights"])
        ids = [item["id"] for item in example["ui"]["tray"]["slots"]]
        self.assertEqual(ids, ["weather", "kettle", "dualup", "lights"])
        self.assertTrue(example["ui"]["hud"]["show_altitude"])
        self.assertTrue(example["ui"]["hud"]["show_faces"])
        self.assertEqual(example["ui"]["hud"]["density"], "regular")

    def test_display_id_from_adapter_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "adapters": {
                            "dualup": {
                                "display_id": "9134432D-0196-4653-9712-EFCAF1980612",
                                "inputs": {"mac": "hdmi1", "linux": "dp"},
                            }
                        }
                    }
                )
            )
            with mock.patch.object(ds, "CONFIG_CANDIDATES", (cfg_path,)):
                cfg = ds.load_config()
        self.assertEqual(cfg["_dualup_display_id"], "9134432D-0196-4653-9712-EFCAF1980612")
        self.assertEqual(cfg["hosts"]["mac"]["dualup_input"], "hdmi1")
        self.assertEqual(cfg["hosts"]["linux"]["dualup_input"], "dp")

    def test_lgdualup_sources_accept_desk_switch_modes(self) -> None:
        c_src = (ROOT / "adapters" / "lgdualup" / "macos" / "lgdualup.c").read_text()
        sh_src = (ROOT / "adapters" / "lgdualup" / "linux" / "lgdualup.sh").read_text()
        for src in (c_src, sh_src):
            self.assertIn("full", src)
            self.assertIn("50-50", src)
            self.assertIn("pbp-assign", src)
            self.assertIn("input-sub", src)
            self.assertIn("0x55", src)
            self.assertIn("0xF6", src)


class DualupLayoutScriptTests(unittest.TestCase):
    MAC = ROOT / "adapters" / "lgdualup" / "macos" / "dualup-layout"
    LNX = ROOT / "adapters" / "lgdualup" / "linux" / "dualup-layout"
    DID = "9134432D-0196-4653-9712-EFCAF1980612"

    def _run_script(
        self, script: Path, args: list[str], env_bin: Path, extra_env: dict | None = None
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PATH"] = f"{env_bin}:{env.get('PATH', '')}"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def _macos_list(self, res: str, rotation: int, modes: list[str]) -> str:
        mode_block = "\n".join(modes)
        return (
            f"Persistent screen id: {self.DID}\n"
            "Type: 28 inch external screen\n"
            f"Resolution: {res}\n"
            "Hertz: 60\n"
            "Color Depth: 8\n"
            "Scaling: off\n"
            "Origin: (0,0) - main display\n"
            f"Rotation: {rotation}\n"
            "Enabled: true\n"
            f"Resolutions for persistent screen id: {self.DID}\n"
            f"{mode_block}\n"
        )

    def _write_displayplacer(
        self,
        bin_dir: Path,
        list_before: str,
        list_after: str | None = None,
        *,
        reject_verbose: bool = False,
        apply_rc: int = 0,
    ) -> None:
        before = bin_dir / "list-before.txt"
        after = bin_dir / "list-after.txt"
        before.write_text(list_before)
        after.write_text(list_after if list_after is not None else list_before)
        placer = bin_dir / "displayplacer"
        placer.write_text(
            "#!/bin/sh\n"
            f"BEFORE={shlex.quote(str(before))}\n"
            f"AFTER={shlex.quote(str(after))}\n"
            f"STATE={shlex.quote(str(bin_dir / 'applied'))}\n"
            f"REJECT_VERBOSE={shlex.quote('yes' if reject_verbose else 'no')}\n"
            f"APPLY_RC={apply_rc}\n"
            'if [ "$1" = list ]; then\n'
            '  if [ -f "$STATE" ]; then cat "$AFTER"; else cat "$BEFORE"; fi\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$REJECT_VERBOSE" = yes ]; then\n'
            '  case "$1" in\n'
            "    *hz:*|*color_depth:*) echo 'verbose rejected'; exit 1 ;;\n"
            "  esac\n"
            "fi\n"
            'if [ "$APPLY_RC" -eq 0 ]; then touch "$STATE"; fi\n'
            'echo "applied:$*"\n'
            'exit "$APPLY_RC"\n'
        )
        placer.chmod(0o755)

    def _assert_verbose_apply(self, stdout: str, res: str) -> None:
        self.assertIn(f"res:{res}", stdout)
        self.assertIn("hz:60", stdout)
        self.assertIn("color_depth:8", stdout)
        self.assertIn("enabled:true", stdout)
        self.assertIn("scaling:off", stdout)
        self.assertIn("origin:(0,0)", stdout)
        self.assertIn("degree:270", stdout)
        self.assertIn(
            f"id:{self.DID} res:{res} hz:60 color_depth:8 enabled:true "
            f"scaling:off origin:(0,0) degree:270",
            stdout,
        )

    def test_macos_full_is_2880x2560_at_270(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(
                bin_dir,
                self._macos_list("2880x2560", 270, ["  mode 0: res:2880x2560 hz:60"]),
            )
            proc = self._run_script(self.MAC, ["full", "--id", self.DID], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_verbose_apply(proc.stdout, "2880x2560")
        self.assertNotIn("fallback", proc.stdout)

    def test_macos_pbp_prefers_2880x1280_at_270(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(
                bin_dir,
                self._macos_list(
                    "2880x1280",
                    270,
                    [
                        "  mode 0: res:2880x1280 hz:60",
                        "  mode 1: res:2560x1440 hz:60",
                        "  mode 2: res:1920x1080 hz:60",
                    ],
                ),
            )
            proc = self._run_script(self.MAC, ["pbp", "--id", self.DID], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_verbose_apply(proc.stdout, "2880x1280")
        self.assertNotIn("degree:0", proc.stdout)
        self.assertNotIn("1080x1920", proc.stdout)

    def test_macos_full_accepts_degree0_edid_2560x2880(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(
                bin_dir,
                self._macos_list("2560x2880", 0, ["  mode 0: res:2560x2880 hz:60"]),
                self._macos_list("2880x2560", 270, ["  mode 0: res:2880x2560 hz:60"]),
            )
            proc = self._run_script(self.MAC, ["full", "--id", self.DID], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_verbose_apply(proc.stdout, "2880x2560")

    def test_macos_pbp_accepts_degree0_edid_1280x2880(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(
                bin_dir,
                self._macos_list("1280x2880", 0, ["  mode 0: res:1280x2880 hz:60"]),
                self._macos_list("2880x1280", 270, ["  mode 0: res:2880x1280 hz:60"]),
            )
            proc = self._run_script(self.MAC, ["pbp", "--id", self.DID], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_verbose_apply(proc.stdout, "2880x1280")
        self.assertNotIn("res:1280x2880", proc.stdout)

    def test_macos_res_flag_accepts_axis_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(
                bin_dir,
                self._macos_list("2560x2880", 0, ["  mode 0: res:2560x2880 hz:60"]),
                self._macos_list("2880x2560", 270, ["  mode 0: res:2880x2560 hz:60"]),
            )
            proc = self._run_script(
                self.MAC,
                ["full", "--id", self.DID, "--res", "2560x2880"],
                bin_dir,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_verbose_apply(proc.stdout, "2880x2560")

    def test_macos_pbp_does_not_use_generic_landscape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(
                bin_dir,
                self._macos_list(
                    "1920x1080",
                    0,
                    ["  mode 0: res:1920x1080 hz:60", "  mode 1: res:2560x1440 hz:60"],
                ),
            )
            proc = self._run_script(self.MAC, ["pbp", "--id", self.DID], bin_dir)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("1920x1080", proc.stdout)
        self.assertNotIn("2560x1440", proc.stdout)

    def test_macos_pbp_exits_2_when_edid_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(
                bin_dir,
                self._macos_list("2880x2560", 270, ["  mode 0: res:2880x2560 hz:60"]),
            )
            proc = self._run_script(self.MAC, ["pbp", "--id", self.DID], bin_dir)
        self.assertEqual(proc.returncode, 2)

    def test_macos_exits_2_when_apply_does_not_rotate(self) -> None:
        stuck = self._macos_list("2560x2880", 0, ["  mode 0: res:2560x2880 hz:60"])
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(bin_dir, stuck, stuck)
            proc = self._run_script(self.MAC, ["full", "--id", self.DID], bin_dir)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("wanted 2880x2560 @ 270", proc.stderr)
        self.assertIn("2560x2880 @ 0", proc.stderr)

    def test_macos_falls_back_to_short_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_displayplacer(
                bin_dir,
                self._macos_list("2560x2880", 0, ["  mode 0: res:2560x2880 hz:60"]),
                self._macos_list("2880x2560", 270, ["  mode 0: res:2880x2560 hz:60"]),
                reject_verbose=True,
            )
            proc = self._run_script(self.MAC, ["full", "--id", self.DID], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("fallback", proc.stdout)
        self.assertIn(f"id:{self.DID} res:2880x2560 degree:270", proc.stdout)

    def _write_hyprctl(self, bin_dir: Path, monitors_json: str) -> None:
        hypr = bin_dir / "hyprctl"
        hypr.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = -j ] || [ \"$2\" = -j ]; then\n"
            f"cat <<'EOF'\n{monitors_json}\nEOF\n"
            "exit 0\n"
            "fi\n"
            "echo \"keyword:$*\"\n"
        )
        hypr.chmod(0o755)

    def test_linux_pbp_uses_1280x2880_transform_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_hyprctl(
                bin_dir,
                '[{"name":"DP-2","description":"LG Electronics LG SDQHD",'
                '"width":1280,"height":2880,"x":0,"y":0,"scale":1.0,'
                '"refreshRate":59.96,"availableModes":['
                '"1280x2880@59.96Hz","2880x1280@59.96Hz","2560x2880@59.96Hz"]}]',
            )
            proc = self._run_script(self.LNX, ["pbp", "--id", "DP-2"], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("DP-2,1280x2880@59.96,0x0,1,transform,3", proc.stdout)
        self.assertNotIn("2880x1280", proc.stdout)
        self.assertNotIn("transform,0", proc.stdout)

    def test_linux_full_uses_2560x2880_transform_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_hyprctl(
                bin_dir,
                '[{"name":"DP-2","description":"LG Electronics LG SDQHD",'
                '"width":2560,"height":2880,"x":0,"y":0,"scale":1.0,'
                '"refreshRate":59.96,"availableModes":['
                '"2560x2880@59.96Hz","2880x2560@59.96Hz","1280x2880@59.96Hz"]}]',
            )
            proc = self._run_script(self.LNX, ["full", "--id", "DP-2"], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("DP-2,2560x2880@59.96,0x0,1,transform,3", proc.stdout)
        self.assertNotIn("2880x2560", proc.stdout)

    def test_linux_pbp_ignores_2560x1440_and_exits_2_without_half_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_hyprctl(
                bin_dir,
                '[{"name":"DP-2","description":"LG Electronics LG SDQHD",'
                '"width":2560,"height":1440,"x":0,"y":0,"scale":1.0,'
                '"refreshRate":60.0,"availableModes":["2560x1440@60.00Hz"]}]',
            )
            proc = self._run_script(self.LNX, ["pbp", "--id", "DP-2"], bin_dir)
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        self.assertNotIn("2560x1440", proc.stdout)


class MacosDualupLayoutUnitTests(unittest.TestCase):
    DID = "9134432D-0196-4653-9712-EFCAF1980612"

    @classmethod
    def setUpClass(cls) -> None:
        path = ROOT / "adapters" / "lgdualup" / "macos" / "dualup-layout"
        loader = importlib.machinery.SourceFileLoader("dualup_layout_macos", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        cls.ml = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.ml)

    def test_pick_res_keeps_edid_axis_swap(self) -> None:
        text = (
            f"Persistent screen id: {self.DID}\n"
            "Resolution: 2560x2880\n"
            "  mode 0: res:2560x2880 hz:60\n"
        )
        self.assertEqual(self.ml.pick_res("full", text, ""), "2880x2560")

    def test_verbose_profile_uses_defaults(self) -> None:
        profile = self.ml.verbose_profile(self.DID, "2880x2560", 270, {})
        self.assertEqual(
            profile,
            f"id:{self.DID} res:2880x2560 hz:60 color_depth:8 enabled:true "
            "scaling:off origin:(0,0) degree:270",
        )

    def test_layout_matches_requires_res_and_rotation(self) -> None:
        ok = (
            f"Persistent screen id: {self.DID}\n"
            "Resolution: 2880x2560\n"
            "Rotation: 270\n"
        )
        stuck = (
            f"Persistent screen id: {self.DID}\n"
            "Resolution: 2560x2880\n"
            "Rotation: 0\n"
        )
        self.assertTrue(self.ml.layout_matches(ok, self.DID, "2880x2560", 270))
        self.assertFalse(self.ml.layout_matches(stuck, self.DID, "2880x2560", 270))
        self.assertFalse(self.ml.layout_matches(ok, self.DID, "2880x2560", 0))

    def test_mode_line_extras_from_swapped_edid(self) -> None:
        text = "  mode 0: res:2560x2880 hz:60 color_depth:8 scaling:off\n"
        extras = self.ml.mode_line_extras(text, "2880x2560")
        self.assertEqual(extras["hz"], "60")
        self.assertEqual(extras["color_depth"], "8")
        self.assertEqual(extras["scaling"], "off")


class HhkbTransportTests(unittest.TestCase):
    def test_linux_bluetooth_hid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hid = Path(tmp) / "hid"
            usb = Path(tmp) / "usb"
            hid.mkdir()
            usb.mkdir()
            node = hid / "0005:000004FE:00000016.0001"
            node.mkdir()
            (node / "uevent").write_text("HID_NAME=HHKB-Studio\nHID_ID=0005:000004FE:00000016\n")
            probe = ds.parse_linux_hhkb_sysfs(hid, usb, 0x04FE, 0x0016)
        self.assertTrue(probe["present"])
        self.assertTrue(probe["bluetooth"])
        self.assertFalse(probe["usb"])
        self.assertEqual(probe["transport"], "bluetooth")

    def test_linux_usb_and_bluetooth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hid = Path(tmp) / "hid"
            usb = Path(tmp) / "usb"
            hid.mkdir()
            usb.mkdir()
            bt = hid / "0005:000004FE:00000016.0001"
            bt.mkdir()
            (bt / "uevent").write_text("HID_NAME=HHKB-Studio\n")
            wired = hid / "0003:000004FE:00000016.0002"
            wired.mkdir()
            (wired / "uevent").write_text("HID_NAME=HHKB-Studio\n")
            dev = usb / "1-3"
            dev.mkdir()
            (dev / "idVendor").write_text("04fe\n")
            (dev / "idProduct").write_text("0016\n")
            probe = ds.parse_linux_hhkb_sysfs(hid, usb, 0x04FE, 0x0016)
        self.assertTrue(probe["usb"])
        self.assertTrue(probe["bluetooth"])
        self.assertEqual(probe["transport"], "both")

    def test_linux_name_match_without_vid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hid = Path(tmp) / "hid"
            hid.mkdir()
            node = hid / "0005:00000000:00000000.0008"
            node.mkdir()
            (node / "uevent").write_text("HID_NAME=HHKB-Studio1\n")
            probe = ds.parse_linux_hhkb_sysfs(hid, None, 0x04FE, 0x0016)
        self.assertTrue(probe["present"])
        self.assertTrue(probe["bluetooth"])

    def test_ioreg_bluetooth_name_without_vid(self) -> None:
        hid = """
+-o IOHIDDevice  <class IOHIDDevice>
  | {
  |   "Product" = "HHKB-Studio1"
  |   "VendorID" = 0
  |   "ProductID" = 0
  |   "Transport" = "Bluetooth"
  | }
"""
        probe = ds.parse_ioreg_hhkb(hid, "", "", 0x04FE, 0x0016)
        self.assertTrue(probe["present"])
        self.assertTrue(probe["bluetooth"])
        self.assertFalse(probe["usb"])
        self.assertEqual(probe["transport"], "bluetooth")

    def test_ioreg_usb_cable(self) -> None:
        usb = """
+-o HHKB-Studio@00100000  <class IOUSBHostDevice>
  | {
  |   "idVendor" = 1278
  |   "idProduct" = 22
  |   "USB Product Name" = "HHKB-Studio"
  | }
"""
        probe = ds.parse_ioreg_hhkb("", usb, "", 0x04FE, 0x0016)
        self.assertTrue(probe["usb"])
        self.assertEqual(probe["transport"], "usb")

    def test_ioreg_bt_device_connected(self) -> None:
        bt = """
+-o IOBluetoothDevice
  | {
  |   "Name" = "HHKB-Studio1"
  |   "DeviceConnected" = Yes
  | }
"""
        probe = ds.parse_ioreg_hhkb("", "", bt, 0x04FE, 0x0016)
        self.assertTrue(probe["bluetooth"])
        self.assertTrue(probe["present"])

    def test_hidutil_product_name_without_usage_6(self) -> None:
        text = (
            "Services:\n"
            "0x0  0x0  0x0  0x1  0x2  0xabc  HHKB-Studio1 Bluetooth\n"
            "Devices:\n"
        )
        probe = ds.parse_hidutil_hhkb(text, 0x04FE, 0x0016)
        self.assertTrue(probe["present"])
        self.assertTrue(probe["bluetooth"])

    def test_hidutil_vid_pid_any_usage(self) -> None:
        text = "Services:\n0x4fe  0x16  0x0  0x1  0x1  0xabc  something\n"
        probe = ds.parse_hidutil_hhkb(text, 0x04FE, 0x0016)
        self.assertTrue(probe["present"])


class HintAndCacheTests(unittest.TestCase):
    def test_hint_mouse_wins_over_missing_hhkb(self) -> None:
        cfg = {
            "this_host": "mac",
            "hosts": {"mac": {"channel": 1}, "linux": {"channel": 2}},
        }
        self.assertEqual(ds.target_hint(cfg, 2, False), "LNX")
        hint, source = ds.resolve_target_hint(cfg, mouse_channel=2, hhkb_present=False)
        self.assertEqual(hint, "LNX")
        self.assertEqual(source, "mouse")

    def test_hint_peer_mouse_when_local_unknown(self) -> None:
        cfg = {
            "this_host": "mac",
            "hosts": {"mac": {"channel": 1}, "linux": {"channel": 2}},
        }
        hint, source = ds.resolve_target_hint(
            cfg, mouse_channel=None, hhkb_present=False, peer_channel=2
        )
        self.assertEqual(hint, "LNX")
        self.assertEqual(source, "peer_mouse")

    def test_mouse_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ds, "cache_dir", return_value=Path(tmp)):
                self.assertEqual(ds.load_mouse_cache(now=100.0), (None, None))
                ds.save_mouse_cache(2, now=50.0)
                channel, ts = ds.load_mouse_cache(now=80.0)
                self.assertEqual(channel, 2)
                self.assertEqual(ts, 50.0)
                expired, _ = ds.load_mouse_cache(now=50.0 + ds.MOUSE_CACHE_TTL_S + 1)
                self.assertIsNone(expired)

    def test_bar_label_chips(self) -> None:
        label = ds.format_bar_label(
            {
                "target_hint": "LNX",
                "hhkb_usb": True,
                "hhkb_bluetooth": False,
                "hhkb_present": True,
                "mouse_channel": 2,
                "mouse_online": False,
                "dualup_mode": "pbp",
            }
        )
        self.assertEqual(label, "LNX  kbU  mx2~  PBP")
        no_hint = ds.format_bar_label(
            {
                "target_hint": "?",
                "hhkb_present": False,
                "mouse_channel": None,
                "dualup_mode": "pbp",
            }
        )
        self.assertEqual(no_hint, "kb-  mx-  PBP")
        self.assertNotIn("?", no_hint)
        strip = ds.format_bar_strip(
            {"target_hint": "LNX", "dualup_mode": "pbp"}
        )
        self.assertEqual(strip, {"focus": "LNX", "display": "pbp"})
        self.assertEqual(ds.format_strip_title(strip), "LNX  PBP")
        self.assertEqual(ds.format_strip_title({"focus": "MAC"}), "MAC")

    def test_mac_status_uses_cached_channel_and_bt_hhkb(self) -> None:
        cfg = {
            "this_host": "mac",
            "hosts": {
                "mac": {"channel": 1, "dualup_input": "hdmi1"},
                "linux": {"channel": 2, "dualup_input": "dp"},
            },
            "hhkb_vendor_id": 0x04FE,
            "hhkb_product_id": 0x0016,
            "_config_path": "(test)",
            "_mouse_enabled": True,
            "_dualup_enabled": False,
            "mxswitch": "/no/mx",
            "follow_hhkb_usb": True,
            "target_channel": 2,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ds, "cache_dir", return_value=Path(tmp)), mock.patch.object(
                ds,
                "hhkb_probe",
                return_value={
                    "present": True,
                    "usb": False,
                    "bluetooth": True,
                    "transport": "bluetooth",
                    "unknown": False,
                    "names": ["HHKB-Studio1"],
                },
            ), mock.patch.object(ds, "mouse_info", return_value=("No Logitech device", None)), mock.patch.object(
                ds, "which_adapter", return_value=None
            ), mock.patch.object(ds, "lgdualup_path", return_value=None), mock.patch.object(
                ds, "detect_dualup_mode", return_value="pbp"
            ):
                ds.save_mouse_cache(2)
                state = ds.collect_status(cfg, local_only=True)
        self.assertEqual(state["target_hint"], "LNX")
        self.assertEqual(state["target_hint_source"], "mouse_cached")
        self.assertTrue(state["hhkb_bluetooth"])
        self.assertFalse(state["hhkb_usb"])
        self.assertEqual(state["hhkb_transport"], "bluetooth")
        self.assertEqual(state["mouse_channel"], 2)
        self.assertFalse(state["mouse_online"])
        self.assertEqual(state["mouse_host"], "linux")
        self.assertEqual(state["bar_label"], "LNX  kbB  mx2~  PBP")
        self.assertEqual(state["dualup_inputs"]["mac"], "hdmi1")

    def test_peer_live_mouse_fills_mac_gap(self) -> None:
        cfg = {
            "this_host": "mac",
            "hosts": {"mac": {"channel": 1}, "linux": {"channel": 2}},
            "hhkb_vendor_id": 0x04FE,
            "hhkb_product_id": 0x0016,
            "_config_path": "(test)",
            "target_channel": 2,
        }
        peer = {
            "reachable": True,
            "this_host": "linux",
            "mouse_channel": 2,
            "mouse_online": True,
            "hhkb_usb": True,
            "hhkb_transport": "usb",
            "target_hint": "LNX",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ds, "cache_dir", return_value=Path(tmp)), mock.patch.object(
                ds, "hhkb_probe", return_value=ds.empty_hhkb_probe()
            ), mock.patch.object(ds, "mouse_info", return_value=("missing", None)), mock.patch.object(
                ds, "which_adapter", return_value=None
            ), mock.patch.object(ds, "lgdualup_path", return_value=None), mock.patch.object(
                ds, "detect_dualup_mode", return_value="unknown"
            ), mock.patch.object(ds, "peek_peer_status", return_value=peer):
                state = ds.collect_status(cfg, local_only=False)
        self.assertEqual(state["target_hint"], "LNX")
        self.assertEqual(state["target_hint_source"], "peer_mouse")
        self.assertEqual(state["mouse_channel"], 2)
        self.assertEqual(state["peer"]["hhkb_transport"], "usb")


class WatchUsbFollowTests(unittest.TestCase):
    def test_rising_edge_fires_once(self) -> None:
        self.assertEqual(ds.watch_usb_rising_edge(None, True, True), (True, False))
        self.assertEqual(ds.watch_usb_rising_edge(False, True, True), (True, True))
        self.assertEqual(ds.watch_usb_rising_edge(True, True, True), (True, False))
        self.assertEqual(ds.watch_usb_rising_edge(True, False, True), (False, False))
        self.assertEqual(ds.watch_usb_rising_edge(False, True, False), (True, False))

    def test_follow_hhkb_usb_default_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text("{}")
            with mock.patch.object(ds, "CONFIG_CANDIDATES", (cfg_path,)):
                cfg = ds.load_config()
        self.assertTrue(cfg["follow_hhkb_usb"])

    def test_follow_hhkb_usb_can_disable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text(json.dumps({"adapters": {"hosts": {"follow_hhkb_usb": False}}}))
            with mock.patch.object(ds, "CONFIG_CANDIDATES", (cfg_path,)):
                cfg = ds.load_config()
        self.assertFalse(cfg["follow_hhkb_usb"])

    def test_usb_edge_calls_to_this_host(self) -> None:
        cfg = {
            "this_host": "mac",
            "follow_hhkb_usb": True,
            "hosts": {"mac": {"channel": 1}, "linux": {"channel": 2}},
            "target_channel": 2,
            "poll_interval_s": 0.01,
            "absent_polls_required": 1,
            "sleep_gap_s": 30,
            "switch_retries": 1,
            "switch_retry_delay_s": 0,
        }
        probes = [
            {
                "present": True,
                "usb": False,
                "bluetooth": True,
                "transport": "bluetooth",
                "unknown": False,
            },
            {
                "present": True,
                "usb": True,
                "bluetooth": False,
                "transport": "usb",
                "unknown": False,
            },
        ]
        calls: list[str] = []

        def fake_to(conf: dict, host: str, *, mouse_only: bool = False) -> int:
            calls.append(host)
            raise KeyboardInterrupt

        with mock.patch.object(ds, "hhkb_probe", side_effect=probes), mock.patch.object(
            ds, "cmd_to", side_effect=fake_to
        ), mock.patch.object(ds.time, "sleep"), mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                ds.cmd_watch(cfg, dry_run=False)
        self.assertEqual(calls, ["mac"])


class DualupModeDetectTests(unittest.TestCase):
    def test_linux_pbp_and_full(self) -> None:
        pbp = [{"name": "DP-2", "description": "LG Electronics LG SDQHD", "width": 1280, "height": 2880}]
        full = [{"name": "DP-2", "description": "LG Electronics LG SDQHD", "width": 2560, "height": 2880}]
        self.assertEqual(ds.detect_dualup_mode_linux(pbp), "pbp")
        self.assertEqual(ds.detect_dualup_mode_linux(full), "full")

    def test_macos_pbp_from_displayplacer(self) -> None:
        text = (
            "Persistent screen id: ABC\n"
            "Type: 28 inch external screen\n"
            "Resolution: 2880x1280\n"
        )
        self.assertEqual(ds.detect_dualup_mode_macos(text), "pbp")
        full = (
            "Persistent screen id: ABC\n"
            "Type: 28 inch external screen\n"
            "Resolution: 2880x2560\n"
        )
        self.assertEqual(ds.detect_dualup_mode_macos(full), "full")
        swapped = (
            "Persistent screen id: ABC\n"
            "Type: 28 inch external screen\n"
            "Resolution: 2560x2880\n"
        )
        self.assertEqual(ds.detect_dualup_mode_macos(swapped), "full")
        pbp0 = (
            "Persistent screen id: ABC\n"
            "Type: 28 inch external screen\n"
            "Resolution: 1280x2880\n"
        )
        self.assertEqual(ds.detect_dualup_mode_macos(pbp0), "pbp")

    def test_status_json_exports_transport_fields(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        env["DESK_SWITCH_WEATHER_URL"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env["HOME"] = str(home)
            env["XDG_CACHE_HOME"] = str(home / "cache")
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
            js = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "status", "--json", "--local"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(js.returncode, 0, js.stderr)
        data = json.loads(js.stdout)
        for key in (
            "hhkb_transport",
            "hhkb_usb",
            "hhkb_bluetooth",
            "bar_label",
            "bar_strip",
            "target_hint",
            "follow_hhkb_usb",
            "dualup_mode",
            "dualup_inputs",
        ):
            self.assertIn(key, data)
        self.assertIn("focus", data["bar_strip"])
        self.assertIn(data["hhkb_transport"], {"usb", "bluetooth", "both", "unknown", "absent"})
        self.assertEqual(data["dualup_inputs"]["mac"], "hdmi1")
        self.assertEqual(data["dualup_inputs"]["linux"], "dp")


class AdapterDiscoveryTests(unittest.TestCase):
    DUMMY = ROOT / "examples" / "dummy-mouse" / "dummy-mouse"
    DUMMY_MANIFEST = ROOT / "examples" / "dummy-mouse" / "dummy-mouse.manifest.json"

    def _libdir(self, tmp: str) -> Path:
        lib = Path(tmp) / "lib"
        lib.mkdir()
        helper = lib / "dummy-mouse"
        helper.write_bytes(self.DUMMY.read_bytes())
        helper.chmod(0o755)
        (lib / "dummy-mouse.manifest.json").write_text(self.DUMMY_MANIFEST.read_text())
        return lib

    def test_scan_finds_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = self._libdir(tmp)
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                found = {item["id"]: item for item in ds.scan_libdir_manifests()}
        self.assertIn("dummy-mouse", found)
        self.assertIn("mouse.host_switch", found["dummy-mouse"]["capabilities"])
        self.assertTrue(found["dummy-mouse"]["path"])

    def test_backend_pin_binds_dummy_mouse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = self._libdir(tmp)
            cfg = {
                "_mouse_enabled": True,
                "_mouse_backend": "dummy-mouse",
                "mxswitch": "mxswitch",
            }
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                bound = ds.bind_role(cfg, "mouse")
        self.assertEqual(bound["id"], "dummy-mouse")
        self.assertEqual(Path(bound["path"]).name, "dummy-mouse")
        self.assertEqual(bound["source"], "backend")

    def test_scan_binds_lone_mouse_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = self._libdir(tmp)
            cfg = {"_mouse_enabled": True, "mxswitch": "mxswitch-missing"}
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                bound = ds.bind_role(cfg, "mouse")
        self.assertEqual(bound["id"], "dummy-mouse")
        self.assertEqual(bound["source"], "scan")

    def test_ambiguous_mouse_adapters_need_a_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = self._libdir(tmp)
            other = lib / "other-mouse"
            other.write_text("#!/bin/sh\nexit 0\n")
            other.chmod(0o755)
            (lib / "other-mouse.manifest.json").write_text(
                json.dumps(
                    {
                        "api_version": 1,
                        "id": "other-mouse",
                        "name": "Other",
                        "capabilities": ["mouse.host_switch"],
                    }
                )
            )
            cfg = {"_mouse_enabled": True, "mxswitch": "mxswitch-missing"}
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                bound = ds.bind_role(cfg, "mouse")
        self.assertIsNone(bound["id"])
        self.assertEqual(bound["source"], "ambiguous")
        self.assertEqual(sorted(bound["candidates"]), ["dummy-mouse", "other-mouse"])

    def test_to_linux_invokes_dummy_mouse(self) -> None:
        env = os.environ.copy()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            lib = self._libdir(tmp)
            state = home / "dummy-state.json"
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(
                json.dumps(
                    {
                        "this_host": "mac",
                        "adapters": {
                            "mouse": {"enabled": True, "backend": "dummy-mouse"},
                            "dualup": {"enabled": False},
                        },
                    }
                )
            )
            env["HOME"] = str(home)
            env["PATH"] = "/usr/bin:/bin"
            env["DESK_SWITCH_LIB"] = str(lib)
            env["DUMMY_MOUSE_STATE"] = str(state)
            env["XDG_CACHE_HOME"] = str(home / "cache")
            env["DESK_SWITCH_WEATHER_URL"] = ""
            proc = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "to", "linux", "--mouse-only"],
                capture_output=True,
                text=True,
                env=env,
            )
            status = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "status", "--json", "--local"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("Switching to channel 2", proc.stdout)
            self.assertEqual(json.loads(state.read_text())["channel"], 2)
            self.assertEqual(status.returncode, 0, status.stderr)
            data = json.loads(status.stdout)
            self.assertEqual(data["adapters"]["mouse"]["backend"], "dummy-mouse")
            self.assertTrue(any(item["id"] == "dummy-mouse" for item in data["adapters"]["discovered"]))

    def test_path_pin_stops_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = self._libdir(tmp)
            cfg = {
                "_mouse_enabled": True,
                "_mouse_path": str(Path(tmp) / "missing-mouse"),
                "_mouse_backend": "dummy-mouse",
            }
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                bound = ds.bind_role(cfg, "mouse")
        self.assertIsNone(bound["path"])
        self.assertEqual(bound["source"], "path")

    def test_nested_libdir_and_path_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "lib"
            nested = lib / "unifying"
            nested.mkdir(parents=True)
            helper = nested / "unifying"
            helper.write_text("#!/bin/sh\necho ok\n")
            helper.chmod(0o755)
            (nested / "manifest.json").write_text(
                json.dumps(
                    {
                        "api_version": 1,
                        "id": "unifying",
                        "name": "Unifying",
                        "capabilities": ["mouse.host_switch"],
                    }
                )
            )
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                self.assertEqual(ds.resolve_backend_id("unifying"), helper)
                found = {item["id"] for item in ds.scan_libdir_manifests()}
            self.assertIn("unifying", found)
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            helper = bindir / "desk-switch-unifying"
            helper.write_text("#!/bin/sh\n")
            helper.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                ds, "libexec_dir", return_value=Path(tmp) / "empty-lib"
            ):
                (Path(tmp) / "empty-lib").mkdir()
                self.assertEqual(ds.resolve_backend_id("unifying"), helper)

    def test_reference_manifests_exist(self) -> None:
        for name in ("mxswitch", "lgdualup", "hhkb", "alexa", "kettle", "weather"):
            raw = json.loads((ROOT / "adapters" / name / "manifest.json").read_text())
            self.assertEqual(raw["api_version"], 1)
            self.assertEqual(raw["id"], name)
        alexa = json.loads((ROOT / "adapters" / "alexa" / "manifest.json").read_text())
        for cap in ("smarthome.list", "smarthome.status", "light.on", "light.off"):
            self.assertIn(cap, alexa["capabilities"])
        kettle = json.loads((ROOT / "adapters" / "kettle" / "manifest.json").read_text())
        for cap in ("appliance.status", "appliance.heat", "appliance.off"):
            self.assertIn(cap, kettle["capabilities"])
        weather = json.loads((ROOT / "adapters" / "weather" / "manifest.json").read_text())
        self.assertIn("weather.status", weather["capabilities"])

    def test_makefile_installs_from_adapters_tree(self) -> None:
        text = (ROOT / "Makefile").read_text()
        self.assertIn("adapters/mxswitch/macos/mxswitch.c", text)
        self.assertIn("adapters/lgdualup/macos/lgdualup.c", text)
        self.assertIn("adapters/hhkb/hhkb.py", text)
        self.assertIn("adapters/alexa/alexa.py", text)
        self.assertIn("adapters/kettle/kettle.py", text)
        self.assertIn("adapters/weather/weather.py", text)
        self.assertIn("$(LIBDIR)/mxswitch.manifest.json", text)
        self.assertIn("$(LIBDIR)/alexa.manifest.json", text)
        self.assertIn("$(LIBDIR)/kettle.manifest.json", text)
        self.assertIn("$(LIBDIR)/weather.manifest.json", text)
        self.assertIn("$(LIBDIR)/mxswitch", text)


class AlexaAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "alexa_adapter", ROOT / "adapters" / "alexa" / "alexa.py"
        )
        cls.alexa = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.alexa)

    def test_parse_devices_json_array(self) -> None:
        raw = json.dumps([{"name": "Sala"}, {"name": "Escritório"}])
        names = [item["name"] for item in self.alexa.parse_devices_payload(raw)]
        self.assertEqual(names, ["Sala", "Escritório"])

    def test_empty_smarthome_json_raises(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            self.alexa.parse_devices_payload("{}")
        with self.assertRaises(json.JSONDecodeError):
            self.alexa.parse_devices_payload("")

    def test_utterance_rejects_device_name(self) -> None:
        with self.assertRaises(SystemExit):
            self.alexa.utterance_for("on", "acender a luz do escritório", "Escritório")
        self.assertEqual(
            self.alexa.utterance_for("on", "acender a luz", "Escritório"),
            "acender a luz",
        )
        self.assertEqual(
            self.alexa.utterance_for("off", "apagar a luz", "Escritório"),
            "apagar a luz",
        )

    def test_on_off_argv_is_safe_phrase_plus_device_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bindir = home / "bin"
            bindir.mkdir()
            log = home / "alexacli.log"
            cli = bindir / "alexacli"
            cli.write_text(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$@" > {shlex.quote(str(log))}\n'
                "exit 0\n"
            )
            cli.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            env["HOME"] = str(home)
            env["XDG_CACHE_HOME"] = str(home / "cache")
            env["ALEXA_CLI_CONFIG"] = str(home / "missing-alexa-config.json")
            for verb, phrase in (("on", "acender a luz"), ("off", "apagar a luz")):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "adapters" / "alexa" / "alexa.py"), verb],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                data = json.loads(proc.stdout)
                self.assertEqual(data["phrase"], phrase)
                self.assertEqual(data["device"], "Escritório")
                self.assertEqual(data["argv"], ["command", phrase, "-d", "Escritório"])
                recorded = log.read_text().split()
                self.assertEqual(recorded, ["command", *phrase.split(), "-d", "Escritório"])
                self.assertNotIn("escritório", phrase.lower())
                self.assertNotIn("escritorio", self.alexa.fold_text(phrase))
                cached = json.loads((home / "cache" / "desk-switch" / "alexa-status.json").read_text())
                self.assertEqual(cached["lights"][0]["state"], verb)
                self.assertEqual(cached["lights"][0]["state_source"], "command")

    def test_list_prefers_devices_when_smarthome_json_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bindir = home / "bin"
            bindir.mkdir()
            cli = bindir / "alexacli"
            cli.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = devices ]; then\n'
                '  echo \'[{"name":"Sala"},{"name":"Escritório"}]\'\n'
                "  exit 0\n"
                "fi\n"
                "echo '{}'\n"
                "exit 0\n"
            )
            cli.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            env["HOME"] = str(home)
            env["XDG_CACHE_HOME"] = str(home / "cache")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "adapters" / "alexa" / "alexa.py"), "list"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual([item["name"] for item in data["devices"]], ["Sala", "Escritório"])
            self.assertEqual(data["list_source"], "devices")
            self.assertFalse(data["smarthome_list"]["ok"])

    def test_core_forwards_smarthome_verbs_without_amazon_utterances(self) -> None:
        src = (ROOT / "desk-switch.py").read_text()
        self.assertNotIn("acender a luz", src)
        self.assertNotIn("apagar a luz", src)
        self.assertNotIn("amazon.com", src)
        self.assertNotIn("alexacli command", src)
        parser = ds.build_parser()
        args = parser.parse_args(["smarthome", "on"])
        self.assertEqual(args.cmd, "smarthome")
        self.assertEqual(args.verb, "on")
        args = parser.parse_args(["smarthome", "list", "--json"])
        self.assertTrue(args.json)

    def test_status_json_includes_smarthome_role(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            lib = home / "lib"
            lib.mkdir()
            helper = lib / "alexa"
            helper.write_bytes((ROOT / "adapters" / "alexa" / "alexa.py").read_bytes())
            helper.chmod(0o755)
            (lib / "alexa.manifest.json").write_text(
                (ROOT / "adapters" / "alexa" / "manifest.json").read_text()
            )
            env["HOME"] = str(home)
            env["XDG_CACHE_HOME"] = str(home / "cache")
            env["DESK_SWITCH_LIB"] = str(lib)
            env["DESK_SWITCH_WEATHER_URL"] = ""
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(
                json.dumps(
                    {
                        "this_host": "mac",
                        "adapters": {
                            "smarthome": {"enabled": True, "backend": "alexa"},
                            "dualup": {"enabled": False},
                        },
                    }
                )
            )
            proc = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "status", "--json", "--local"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        home_ad = data["adapters"]["smarthome"]
        self.assertEqual(home_ad["backend"], "alexa")
        self.assertTrue(home_ad["available"])
        self.assertIn("smarthome.list", home_ad["capabilities"])
        self.assertTrue(any(item["id"] == "alexa" for item in data["adapters"]["discovered"]))
        self.assertNotIn("lights", data["bar_strip"])
        lights = home_ad.get("lights") or []
        self.assertTrue(lights)
        self.assertEqual(lights[0]["speaker"], "Escritório")
        self.assertEqual(lights[0]["on_phrase"], "acender a luz")
        self.assertNotIn("lights", [item["id"] for item in data["slots"]])

    def test_status_json_lights_slot_follows_command_cache(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            lib = home / "lib"
            lib.mkdir()
            helper = lib / "alexa"
            helper.write_bytes((ROOT / "adapters" / "alexa" / "alexa.py").read_bytes())
            helper.chmod(0o755)
            (lib / "alexa.manifest.json").write_text(
                (ROOT / "adapters" / "alexa" / "manifest.json").read_text()
            )
            bindir = home / "bin"
            bindir.mkdir()
            cli = bindir / "alexacli"
            cli.write_text("#!/bin/sh\nexit 0\n")
            cli.chmod(0o755)
            env["HOME"] = str(home)
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            env["XDG_CACHE_HOME"] = str(home / "cache")
            env["DESK_SWITCH_LIB"] = str(lib)
            env["DESK_SWITCH_WEATHER_URL"] = ""
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(
                json.dumps(
                    {
                        "this_host": "mac",
                        "adapters": {
                            "smarthome": {"enabled": True, "backend": "alexa"},
                            "dualup": {"enabled": False},
                        },
                        "ui": {"tray": {"lights": True}},
                    }
                )
            )
            before = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "status", "--json", "--local"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(before.returncode, 0, before.stderr)
            before_data = json.loads(before.stdout)
            lights_slot = next(item for item in before_data["slots"] if item["id"] == "lights")
            self.assertEqual(lights_slot["label"], "?")
            self.assertEqual(lights_slot["glyph"], "light.off")
            on = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "smarthome", "on"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(on.returncode, 0, on.stdout + on.stderr)
            after = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "status", "--json", "--local"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(after.returncode, 0, after.stderr)
            after_data = json.loads(after.stdout)
            self.assertEqual(after_data["adapters"]["smarthome"]["lights"][0]["state"], "on")
            slot = next(item for item in after_data["slots"] if item["id"] == "lights")
            self.assertEqual(slot["glyph"], "light.on")
            self.assertEqual(slot["label"], "ON")
            self.assertEqual(after_data["bar_strip"]["lights"], "on")

    def test_smarthome_cli_on_invokes_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            lib = Path(tmp) / "lib"
            lib.mkdir()
            helper = lib / "alexa"
            state = home / "invoked.json"
            helper.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"json.dump(sys.argv[1:], open({state.as_posix()!r}, 'w'))\n"
                "print(json.dumps({'ok': True, 'verb': sys.argv[1], "
                "'phrase': 'acender a luz', 'device': 'Escritório'}))\n"
            )
            helper.chmod(0o755)
            (lib / "alexa.manifest.json").write_text(
                (ROOT / "adapters" / "alexa" / "manifest.json").read_text()
            )
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(
                json.dumps(
                    {
                        "this_host": "mac",
                        "adapters": {
                            "smarthome": {"enabled": True, "backend": "alexa"},
                            "dualup": {"enabled": False},
                        },
                    }
                )
            )
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["PATH"] = "/usr/bin:/bin"
            env["DESK_SWITCH_LIB"] = str(lib)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "smarthome", "on"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("acender a luz", proc.stdout)
            self.assertEqual(json.loads(state.read_text())[0], "on")

    def test_smarthome_disabled_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(
                json.dumps({"adapters": {"smarthome": {"enabled": False}}})
            )
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["PATH"] = "/usr/bin:/bin"
            env["DESK_SWITCH_LIB"] = str(home / "empty-lib")
            (home / "empty-lib").mkdir()
            proc = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "smarthome", "list"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("smarthome adapter not found", proc.stdout)

    def test_on_writes_cache_before_failed_speak_reverts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bindir = home / "bin"
            bindir.mkdir()
            cli = bindir / "alexacli"
            cli.write_text("#!/bin/sh\nexit 1\n")
            cli.chmod(0o755)
            cache = home / "cache" / "desk-switch" / "alexa-status.json"
            cache.parent.mkdir(parents=True)
            cache.write_text(
                json.dumps(
                    {
                        "lights": [
                            {
                                "id": "desk",
                                "state": "off",
                                "state_source": "command",
                                "updated_at": 1.0,
                            }
                        ]
                    }
                )
            )
            env = os.environ.copy()
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            env["HOME"] = str(home)
            env["XDG_CACHE_HOME"] = str(home / "cache")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "adapters" / "alexa" / "alexa.py"), "on"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            cached = json.loads(cache.read_text())
            self.assertEqual(cached["lights"][0]["state"], "off")

    def test_entity_power_preferred_when_readable(self) -> None:
        self.assertEqual(self.alexa.entity_power_state({"powerState": "ON"}), "on")
        self.assertTrue(self.alexa.entity_looks_like_light({"name": "Luz mesa", "kind": "LIGHT"}))
        lights = [self.alexa.default_light("Escritório", "acender a luz", "apagar a luz", "off")]
        changed = self.alexa.apply_entity_light_state(
            lights, [{"name": "desk light", "powerState": "on"}]
        )
        self.assertTrue(changed)
        self.assertEqual(lights[0]["state"], "on")
        self.assertEqual(lights[0]["state_source"], "entity")

    def test_refresh_does_not_clobber_newer_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["XDG_CACHE_HOME"] = str(home / "cache")
            env["PATH"] = "/usr/bin:/bin"
            cache = home / "cache" / "desk-switch" / "alexa-status.json"

            def slow_devices(_cli):
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(
                    json.dumps(
                        {
                            "lights": [
                                {
                                    "id": "desk",
                                    "state": "on",
                                    "state_source": "command",
                                    "updated_at": time.time() + 10,
                                }
                            ]
                        }
                    )
                )
                return [{"name": "Escritório"}], None

            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                self.alexa, "list_echo_devices", side_effect=slow_devices
            ), mock.patch.object(
                self.alexa,
                "try_smarthome_entities",
                return_value={"ok": False, "source": None, "error": "empty", "entities": []},
            ), mock.patch.object(
                self.alexa, "cli_probe", return_value={"name": "alexacli", "available": True, "path": "/bin/true"}
            ), mock.patch.object(
                self.alexa, "auth_configured", return_value={"configured": True, "path": "", "domain": None}
            ):
                data = self.alexa.snapshot(
                    cli_name="alexacli",
                    device="Escritório",
                    on_phrase="acender a luz",
                    off_phrase="apagar a luz",
                    refresh=True,
                )
            self.assertEqual(data["lights"][0]["state"], "on")
            self.assertEqual(data["lights"][0]["state_source"], "command")

    def test_bar_strip_lights_opt_in_only(self) -> None:
        state = {
            "target_hint": "MAC",
            "dualup_mode": "unknown",
            "adapters": {"smarthome": {"lights": [{"state": "on"}]}},
        }
        self.assertNotIn("lights", ds.format_bar_strip(state))
        state["_tray_lights"] = True
        self.assertEqual(ds.format_bar_strip(state)["lights"], "on")

    def test_bind_smarthome_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "lib"
            lib.mkdir()
            helper = lib / "alexa"
            helper.write_text("#!/bin/sh\n")
            helper.chmod(0o755)
            (lib / "alexa.manifest.json").write_text(
                (ROOT / "adapters" / "alexa" / "manifest.json").read_text()
            )
            cfg = {"_smarthome_enabled": True, "_smarthome_backend": "alexa"}
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                bound = ds.bind_role(cfg, "smarthome")
        self.assertEqual(bound["id"], "alexa")
        self.assertEqual(bound["source"], "backend")
        self.assertIn("light.on", bound["capabilities"])


class KettleAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "kettle_adapter", ROOT / "adapters" / "kettle" / "kettle.py"
        )
        cls.kettle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.kettle)

    def test_parse_state_body(self) -> None:
        body = (
            "<html><form></form></html>\n"
            "mode=S_Heat\n"
            "tempr=45.2 C\n"
            "temprT=96 C\n"
            "I (35106409) Cli: command 'state' ret 0\n"
        )
        parsed = self.kettle.parse_kettle_body(body)
        self.assertEqual(parsed["field_map"]["mode"], "S_Heat")
        self.assertEqual(parsed["field_map"]["tempr"], "45.2 C")
        self.assertEqual(parsed["ret"], 0)
        snap = self.kettle.snapshot_from(parsed)
        self.assertEqual(snap["mode"], "heating")
        self.assertTrue(snap["is_active"])
        self.assertEqual(self.kettle.temperature_display(snap["current"]), "45°C")
        self.assertEqual(self.kettle.temperature_compact(snap["target"]), "96°")

    def test_parse_settings_altitude(self) -> None:
        body = "st: altitude=780 m\nst: units=C\nI (1) Cli: command 'prtsettings' ret 0\n"
        settings = self.kettle.settings_from_body(body)
        self.assertEqual(settings["altitude_meters"], 780)
        self.assertEqual(settings["fields"]["units"], "C")

    def test_commands_and_modes(self) -> None:
        self.assertEqual(self.kettle.heat_commands("93"), ["setunitsc", "setsettingd settempr 93", "heaton"])
        self.assertEqual(self.kettle.off_commands()[0], "heatoff")
        self.assertEqual(self.kettle.kettle_mode_from("S_Hold"), "holding")
        self.assertEqual(self.kettle.kettle_mode_from("S_Off"), "off")

    def test_info_without_host_does_not_probe(self) -> None:
        env = os.environ.copy()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env["HOME"] = str(home)
            env["XDG_CONFIG_HOME"] = str(home / "cfg")
            env.pop("KETTLE_HOST", None)
            env.pop("FELLOW_HOST", None)
            env.pop("STAGG_HOST", None)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "adapters" / "kettle" / "kettle.py"), "info"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertFalse(data["reachable"])
        self.assertIsNone(data["host"])
        self.assertNotIn("slot", data)

    def test_slot_from_heating_snapshot(self) -> None:
        snap = {
            "mode": "heating",
            "mode_title": "Heating",
            "is_active": True,
            "current": {"value": 65.0, "unit": "C"},
            "target": {"value": 96.0, "unit": "C"},
        }
        slot = self.kettle.slot_from_snapshot(snap, host="192.168.3.36")
        self.assertEqual(slot["id"], "kettle")
        self.assertEqual(slot["glyph"], "flame")
        self.assertEqual(slot["label"], "65°")
        self.assertTrue(slot["hot"])
        self.assertTrue(slot["face"])
        self.assertIn("heat", slot["actions"][0]["argv"])


class WeatherAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "weather_adapter", ROOT / "adapters" / "weather" / "weather.py"
        )
        cls.weather = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.weather)

    def test_parse_open_meteo(self) -> None:
        payload = {
            "elevation": 780.4,
            "current_units": {"temperature_2m": "°C"},
            "current": {"temperature_2m": 22.2, "weather_code": 61, "is_day": 1},
        }
        parsed = self.weather.parse_open_meteo(payload)
        self.assertEqual(parsed["mood_title"], "Rain")
        self.assertEqual(parsed["altitude_m"], 780)
        self.assertEqual(self.weather.weather_glyph(61, True), "cloud.rain")

    def test_disabled_url_skips_network(self) -> None:
        env = os.environ.copy()
        with tempfile.TemporaryDirectory() as tmp:
            env["HOME"] = str(tmp)
            env["DESK_SWITCH_WEATHER_URL"] = ""
            env["DESK_SWITCH_WEATHER_CACHE"] = str(Path(tmp) / "wx.json")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "adapters" / "weather" / "weather.py"), "info"],
                capture_output=True,
                text=True,
                env=env,
            )
        data = json.loads(proc.stdout)
        self.assertFalse(data["reachable"])
        self.assertNotIn("slot", data)

    def test_slot_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "wx.json"
            cache.write_text(
                json.dumps(
                    {
                        "elevation": 760,
                        "current": {"temperature_2m": 18, "weather_code": 0, "is_day": 1},
                        "cached_at": 1e18,
                    }
                )
            )
            env = os.environ.copy()
            env["HOME"] = str(tmp)
            env["DESK_SWITCH_WEATHER_CACHE"] = str(cache)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "adapters" / "weather" / "weather.py"), "info"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["reachable"])
        self.assertEqual(data["slot"]["id"], "weather")
        self.assertEqual(data["slot"]["label"], "18°")
        self.assertEqual(data["slot"]["glyph"], "sun.max")


class UIConfigTests(unittest.TestCase):
    def test_parse_defaults_when_missing(self) -> None:
        parsed = ds.parse_ui_section({})
        self.assertEqual(parsed["tray_density"], "strip")
        self.assertFalse(parsed["tray_lights"])
        self.assertIsNone(parsed["slots"])
        self.assertTrue(parsed["hud_show_altitude"])
        self.assertTrue(parsed["hud_show_faces"])
        self.assertEqual(parsed["hud_density"], "regular")
        self.assertEqual(
            [item["id"] for item in ds.default_tray_slot_prefs()],
            ["weather", "kettle", "dualup", "lights"],
        )
        self.assertFalse(ds.default_tray_slot_prefs()[-1]["enabled"])

    def test_parse_string_pin_is_exclusive(self) -> None:
        parsed = ds.parse_ui_section({"tray": {"slots": ["kettle", "weather"]}})
        self.assertTrue(parsed["slots_from_user"])
        self.assertTrue(parsed["slots_exclusive"])
        self.assertEqual(
            [(item["id"], item["enabled"]) for item in parsed["slots"]],
            [("kettle", True), ("weather", True)],
        )

    def test_parse_object_slots_and_hud(self) -> None:
        parsed = ds.parse_ui_section(
            {
                "tray": {
                    "density": "chips",
                    "slots": [
                        {"id": "dualup", "enabled": True},
                        {"id": "weather", "enabled": False},
                        {"id": "lights", "enabled": True},
                    ],
                },
                "hud": {"show_altitude": False, "show_faces": "off", "density": "compact"},
            }
        )
        self.assertEqual(parsed["tray_density"], "chips")
        self.assertTrue(parsed["tray_lights"])
        self.assertFalse(parsed["slots_exclusive"])
        self.assertEqual(parsed["slots"][0]["id"], "dualup")
        self.assertFalse(parsed["slots"][1]["enabled"])
        self.assertFalse(parsed["hud_show_altitude"])
        self.assertFalse(parsed["hud_show_faces"])
        self.assertEqual(parsed["hud_density"], "compact")

    def test_apply_slot_prefs_exclusive_and_object(self) -> None:
        available = [
            {"id": "weather", "label": "22°"},
            {"id": "kettle", "label": "65°"},
            {"id": "dualup", "label": "PBP"},
        ]
        exclusive = ds.apply_slot_prefs(
            available,
            [{"id": "kettle", "enabled": True}],
            exclusive=True,
        )
        self.assertEqual([item["id"] for item in exclusive], ["kettle"])
        ordered = ds.apply_slot_prefs(
            available,
            [
                {"id": "dualup", "enabled": True},
                {"id": "weather", "enabled": False},
                {"id": "kettle", "enabled": True},
            ],
            exclusive=False,
        )
        self.assertEqual([item["id"] for item in ordered], ["dualup", "kettle"])

    def test_apply_slot_prefs_appends_unknown_adapters(self) -> None:
        available = [
            {"id": "weather", "label": "22°"},
            {"id": "espresso", "label": "90°"},
        ]
        out = ds.apply_slot_prefs(
            available,
            [{"id": "weather", "enabled": True}],
            exclusive=False,
        )
        self.assertEqual([item["id"] for item in out], ["weather", "espresso"])

    def test_apply_hud_prefs(self) -> None:
        slots = [
            {"id": "weather", "glyph": "cloud", "label": "19°", "detail": "Fog · 12m"},
            {"id": "kettle", "glyph": "mug", "label": "40°", "face": True},
        ]
        out = ds.apply_hud_prefs(
            slots, {"_hud_show_altitude": False, "_hud_show_faces": False}
        )
        self.assertEqual(out[0]["detail"], "Fog")
        self.assertNotIn("face", out[1])

    def test_load_config_object_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "ui": {
                            "tray": {
                                "slots": [
                                    {"id": "kettle", "enabled": True},
                                    {"id": "weather", "enabled": False},
                                ]
                            },
                            "hud": {"density": "compact", "show_altitude": False},
                        }
                    }
                )
            )
            with mock.patch.object(ds, "CONFIG_CANDIDATES", (cfg_path,)):
                cfg = ds.load_config()
        self.assertEqual(cfg["_ui_slots"][0]["id"], "kettle")
        self.assertFalse(cfg["_ui_slots"][1]["enabled"])
        self.assertFalse(cfg["_ui_slots_exclusive"])
        self.assertFalse(cfg["_hud_show_altitude"])
        self.assertEqual(ds.hud_density(cfg), "compact")
        public = ds.format_public_ui(cfg)
        self.assertEqual(public["hud"]["density"], "compact")
        self.assertEqual(public["tray"]["slots"][0]["id"], "kettle")

    def test_write_ui_config_merges_without_clobbering_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "desk-switch" / "config.json"
            dest.parent.mkdir()
            dest.write_text(
                json.dumps(
                    {
                        "adapters": {"kettle": {"host": "10.0.0.8"}},
                        "ui": {"tray": {"density": "strip"}},
                    }
                )
            )
            ds.write_ui_config(
                {
                    "tray": {
                        "density": "chips",
                        "slots": [
                            {"id": "dualup", "enabled": True},
                            {"id": "weather", "enabled": True},
                            {"id": "kettle", "enabled": False},
                        ],
                    },
                    "hud": {"show_faces": False, "density": "compact"},
                },
                dest,
            )
            data = json.loads(dest.read_text())
        self.assertEqual(data["adapters"]["kettle"]["host"], "10.0.0.8")
        self.assertEqual(data["ui"]["tray"]["density"], "chips")
        self.assertEqual(data["ui"]["tray"]["slots"][0]["id"], "dualup")
        self.assertFalse(data["ui"]["hud"]["show_faces"])
        self.assertEqual(data["ui"]["hud"]["density"], "compact")

    def test_status_json_echoes_normalized_ui(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        env["DESK_SWITCH_WEATHER_URL"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env["HOME"] = str(home)
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(
                json.dumps(
                    {
                        "this_host": "linux",
                        "mxswitch": "/no/such/mxswitch",
                        "lgdualup": "lgdualup-missing",
                        "ui": {
                            "tray": {"slots": ["weather"]},
                            "hud": {"show_altitude": False},
                        },
                    }
                )
            )
            proc = subprocess.run(
                [sys.executable, str(ROOT / "desk-switch.py"), "status", "--json", "--local"],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn("slots", data["ui"]["tray"])
        self.assertEqual(data["ui"]["tray"]["slots"][0]["id"], "weather")
        self.assertTrue(data["ui"]["tray"]["slots"][0]["enabled"])
        hidden = {item["id"]: item["enabled"] for item in data["ui"]["tray"]["slots"]}
        self.assertFalse(hidden["kettle"])
        self.assertFalse(data["ui"]["hud"]["show_altitude"])


class SlotComposeTests(unittest.TestCase):
    def test_collect_slots_weather_kettle_display(self) -> None:
        state = {
            "dualup_mode": "pbp",
            "adapters": {
                "weather": {
                    "enabled": True,
                    "slot": {"id": "weather", "glyph": "cloud.rain", "label": "22°", "detail": "780m"},
                },
                "kettle": {
                    "enabled": True,
                    "slot": {
                        "id": "kettle",
                        "glyph": "mug",
                        "label": "65°",
                        "hot": False,
                        "face": True,
                        "actions": [{"label": "Heat", "argv": ["kettle", "heat", "93"]}],
                    },
                },
            },
        }
        slots = ds.collect_slots(state, {})
        self.assertEqual([item["id"] for item in slots], ["weather", "kettle", "dualup"])
        self.assertEqual(slots[0]["label"], "22°")
        self.assertEqual(slots[1]["glyph"], "mug")
        self.assertEqual(slots[2]["label"], "PBP")
        self.assertEqual(slots[1]["actions"][0]["argv"], ["kettle", "heat", "93"])
        self.assertNotIn("lights", [item["id"] for item in slots])

    def test_collect_slots_lights_on_off_unknown(self) -> None:
        state = {
            "dualup_mode": "unknown",
            "adapters": {"smarthome": {"lights": [{"state": "on"}]}},
        }
        self.assertEqual(ds.collect_slots(state, {}), [])
        on = ds.collect_slots(state, {"_tray_lights": True})
        self.assertEqual(on[0]["id"], "lights")
        self.assertEqual(on[0]["glyph"], "light.on")
        self.assertEqual(on[0]["label"], "ON")
        self.assertEqual(on[0]["actions"][0]["argv"], ["smarthome", "on"])
        state["adapters"]["smarthome"]["lights"] = [{"state": "off"}]
        off = ds.collect_slots(state, {"_tray_lights": True})
        self.assertEqual(off[0]["glyph"], "light.off")
        self.assertEqual(off[0]["label"], "OFF")
        state["adapters"]["smarthome"]["lights"] = [{"state": "unknown"}]
        unknown = ds.collect_slots(state, {"_tray_lights": True})
        self.assertEqual(unknown[0]["glyph"], "light.off")
        self.assertEqual(unknown[0]["label"], "?")
        pinned = ds.collect_slots(
            {"dualup_mode": "unknown", "adapters": {"smarthome": {"lights": []}}},
            {"_ui_slots": [{"id": "lights", "enabled": True}], "_ui_slots_exclusive": True},
        )
        self.assertEqual([item["id"] for item in pinned], ["lights"])
        self.assertEqual(pinned[0]["label"], "?")

    def test_weather_slot_survives_offline_kettle(self) -> None:
        state = {
            "dualup_mode": "unknown",
            "adapters": {
                "weather": {
                    "enabled": True,
                    "reachable": True,
                    "slot": {"id": "weather", "glyph": "cloud", "label": "19°"},
                },
                "kettle": {"enabled": True, "reachable": False, "error": "unreachable"},
            },
        }
        slots = ds.collect_slots(state, {})
        self.assertEqual([item["id"] for item in slots], ["weather"])

    def test_missing_adapters_omit_slots(self) -> None:
        self.assertEqual(ds.collect_slots({"dualup_mode": "unknown", "adapters": {}}, {}), [])

    def test_tray_slots_pin(self) -> None:
        state = {
            "dualup_mode": "full",
            "adapters": {
                "weather": {"slot": {"id": "weather", "glyph": "sun.max", "label": "20°"}},
                "kettle": {"slot": {"id": "kettle", "glyph": "mug", "label": "40°"}},
            },
        }
        slots = ds.collect_slots(state, {"_tray_slots": ["kettle"]})
        self.assertEqual([item["id"] for item in slots], ["kettle"])

    def test_tray_slots_object_order_and_hide(self) -> None:
        state = {
            "dualup_mode": "pbp",
            "adapters": {
                "weather": {"slot": {"id": "weather", "glyph": "cloud", "label": "19°", "detail": "Cloudy · 780m"}},
                "kettle": {"slot": {"id": "kettle", "glyph": "mug", "label": "40°", "face": True}},
            },
        }
        prefs = [
            {"id": "dualup", "enabled": True},
            {"id": "weather", "enabled": True},
            {"id": "kettle", "enabled": False},
        ]
        slots = ds.collect_slots(state, {"_ui_slots": prefs, "_ui_slots_exclusive": False})
        self.assertEqual([item["id"] for item in slots], ["dualup", "weather"])

    def test_hud_prefs_strip_altitude_and_faces(self) -> None:
        state = {
            "dualup_mode": "unknown",
            "adapters": {
                "weather": {
                    "slot": {
                        "id": "weather",
                        "glyph": "cloud",
                        "label": "19°",
                        "detail": "Cloudy · 780m",
                    }
                },
                "kettle": {
                    "slot": {"id": "kettle", "glyph": "mug", "label": "40°", "face": True}
                },
            },
        }
        slots = ds.collect_slots(
            state, {"_hud_show_altitude": False, "_hud_show_faces": False}
        )
        self.assertEqual([item["id"] for item in slots], ["weather", "kettle"])
        self.assertEqual(slots[0]["detail"], "Cloudy")
        self.assertNotIn("face", slots[1])

    def test_status_json_includes_slots_and_legacy_keys(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        env["DESK_SWITCH_WEATHER_URL"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env["HOME"] = str(home)
            env["DESK_SWITCH_WEATHER_CACHE"] = str(home / "wx.json")
            cfg_dir = home / ".config" / "desk-switch"
            cfg_dir.mkdir(parents=True)
            lib = home / "lib"
            lib.mkdir()
            kettle = lib / "kettle"
            kettle.write_text(
                "#!/bin/sh\n"
                "python3 - <<'PY'\n"
                "import json\n"
                "print(json.dumps({\n"
                '  "id": "kettle", "reachable": True, "host": "192.168.3.36",\n'
                '  "temp_c": 65, "target_c": 96, "mode": "holding", "hot": True,\n'
                '  "slot": {"id": "kettle", "glyph": "flame", "label": "65°", "hot": True, "face": True}\n'
                "}))\n"
                "PY\n"
            )
            kettle.chmod(0o755)
            weather = lib / "weather"
            weather.write_text(
                "#!/bin/sh\n"
                "python3 - <<'PY'\n"
                "import json\n"
                "print(json.dumps({\n"
                '  "id": "weather", "reachable": True, "temp_c": 22, "altitude_m": 780,\n'
                '  "slot": {"id": "weather", "glyph": "cloud.rain", "label": "22°", "detail": "780m"}\n'
                "}))\n"
                "PY\n"
            )
            weather.chmod(0o755)
            (lib / "kettle.manifest.json").write_text(
                (ROOT / "adapters" / "kettle" / "manifest.json").read_text()
            )
            (lib / "weather.manifest.json").write_text(
                (ROOT / "adapters" / "weather" / "manifest.json").read_text()
            )
            (cfg_dir / "config.json").write_text(
                json.dumps(
                    {
                        "this_host": "linux",
                        "mxswitch": "/no/such/mxswitch",
                        "lgdualup": "lgdualup-missing",
                        "adapters": {
                            "kettle": {"enabled": True, "backend": "kettle", "host": "192.168.3.36"},
                            "weather": {"enabled": True, "backend": "weather"},
                        },
                    }
                )
            )
            env["DESK_SWITCH_LIB"] = str(lib)
            with mock.patch.object(ds, "libexec_dir", return_value=lib):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "desk-switch.py"), "status", "--json"],
                    capture_output=True,
                    text=True,
                    env=env,
                )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn("bar_label", data)
        self.assertIn("bar_strip", data)
        self.assertIn("adapters", data)
        self.assertIn("mouse", data["adapters"])
        self.assertEqual(data["adapters"]["kettle"]["temp_c"], 65)
        self.assertEqual(data["adapters"]["weather"]["temp_c"], 22)
        ids = [item["id"] for item in data["slots"]]
        self.assertIn("weather", ids)
        self.assertIn("kettle", ids)

    def test_core_forwards_kettle_verbs(self) -> None:
        src = (ROOT / "desk-switch.py").read_text()
        self.assertNotIn("GET /cli", src)
        self.assertNotIn("192.168.3.36", src)
        self.assertNotIn("api.open-meteo.com", src)
        parser = ds.build_parser()
        args = parser.parse_args(["kettle", "heat", "93"])
        self.assertEqual(args.cmd, "kettle")
        self.assertEqual(args.verb, "heat")
        self.assertEqual(args.temp, "93")
        args = parser.parse_args(["weather", "status", "--json"])
        self.assertEqual(args.cmd, "weather")


if __name__ == "__main__":
    unittest.main()


