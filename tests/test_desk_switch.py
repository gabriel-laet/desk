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
        self.assertIn("bar_label", src)
        self.assertIn("hhkb_usb", src)
        self.assertIn("hhkb_transport", src)
        self.assertIn("USB on this host", src)
        self.assertNotIn("lib/desk-switch/mxswitch", src)
        self.assertNotIn("lib/desk-switch/lgdualup", src)


class BarWidgetSourceTests(unittest.TestCase):
    def test_omarchy_uses_shared_bar_label(self) -> None:
        bar = (ROOT / "BarWidget.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()
        self.assertIn("bar_label", bar)
        self.assertIn("hhkb_usb", bar)
        self.assertIn("status --json", bar)
        self.assertIn("USB on this host", panel)
        self.assertIn("BT only", panel)
        self.assertIn("StatusChip", panel)


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
        c_src = (ROOT / "macos" / "lgdualup.c").read_text()
        sh_src = (ROOT / "linux" / "lgdualup.sh").read_text()
        for src in (c_src, sh_src):
            self.assertIn("full", src)
            self.assertIn("50-50", src)
            self.assertIn("pbp-assign", src)
            self.assertIn("input-sub", src)
            self.assertIn("0x55", src)
            self.assertIn("0xF6", src)


class DualupLayoutScriptTests(unittest.TestCase):
    MAC = ROOT / "macos" / "dualup-layout"
    LNX = ROOT / "linux" / "dualup-layout"

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

    def test_macos_full_is_2880x2560_at_270(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            placer = bin_dir / "displayplacer"
            placer.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = list ]; then\n"
                "cat <<'EOF'\n"
                "Persistent screen id: 9134432D-0196-4653-9712-EFCAF1980612\n"
                "Type: 28 inch external screen\n"
                "Resolution: 2880x2560\n"
                "  mode 0: res:2880x2560 hz:60\n"
                "EOF\n"
                "exit 0\n"
                "fi\n"
                "echo \"applied:$*\"\n"
            )
            placer.chmod(0o755)
            proc = self._run_script(self.MAC, ["full", "--id", "9134432D-0196-4653-9712-EFCAF1980612"], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("res:2880x2560 degree:270", proc.stdout)

    def test_macos_pbp_prefers_2880x1280_at_270(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            placer = bin_dir / "displayplacer"
            placer.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = list ]; then\n"
                "cat <<'EOF'\n"
                "Persistent screen id: 9134432D-0196-4653-9712-EFCAF1980612\n"
                "Type: 28 inch external screen\n"
                "  mode 0: res:2880x1280 hz:60\n"
                "  mode 1: res:2560x1440 hz:60\n"
                "  mode 2: res:1920x1080 hz:60\n"
                "EOF\n"
                "exit 0\n"
                "fi\n"
                "echo \"applied:$*\"\n"
            )
            placer.chmod(0o755)
            proc = self._run_script(self.MAC, ["pbp", "--id", "9134432D-0196-4653-9712-EFCAF1980612"], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("res:2880x1280 degree:270", proc.stdout)
        self.assertNotIn("degree:0", proc.stdout)
        self.assertNotIn("1080x1920", proc.stdout)

    def test_macos_pbp_falls_back_to_best_landscape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            placer = bin_dir / "displayplacer"
            placer.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = list ]; then\n"
                "cat <<'EOF'\n"
                "Persistent screen id: 9134432D-0196-4653-9712-EFCAF1980612\n"
                "Type: 28 inch external screen\n"
                "  mode 0: res:1600x900 hz:60\n"
                "  mode 1: res:1280x720 hz:60\n"
                "EOF\n"
                "exit 0\n"
                "fi\n"
                "echo \"applied:$*\"\n"
            )
            placer.chmod(0o755)
            proc = self._run_script(self.MAC, ["pbp", "--id", "9134432D-0196-4653-9712-EFCAF1980612"], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("res:1600x900 degree:270", proc.stdout)

    def test_macos_pbp_falls_back_to_1920x1080_at_270(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            placer = bin_dir / "displayplacer"
            placer.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = list ]; then\n"
                "cat <<'EOF'\n"
                "Persistent screen id: 9134432D-0196-4653-9712-EFCAF1980612\n"
                "Type: 28 inch external screen\n"
                "Resolution: 1920x1080\n"
                "  mode 0: res:1920x1080 hz:60\n"
                "EOF\n"
                "exit 0\n"
                "fi\n"
                "echo \"applied:$*\"\n"
            )
            placer.chmod(0o755)
            proc = self._run_script(self.MAC, ["pbp", "--id", "9134432D-0196-4653-9712-EFCAF1980612"], bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("res:1920x1080 degree:270", proc.stdout)

    def test_macos_pbp_exits_2_when_edid_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            placer = bin_dir / "displayplacer"
            placer.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = list ]; then\n"
                "cat <<'EOF'\n"
                "Persistent screen id: 9134432D-0196-4653-9712-EFCAF1980612\n"
                "Type: 28 inch external screen\n"
                "Resolution: 2880x2560\n"
                "  mode 0: res:2880x2560 hz:60\n"
                "EOF\n"
                "exit 0\n"
                "fi\n"
                "echo unexpected\n"
            )
            placer.chmod(0o755)
            proc = self._run_script(self.MAC, ["pbp", "--id", "9134432D-0196-4653-9712-EFCAF1980612"], bin_dir)
        self.assertEqual(proc.returncode, 2)

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

    def test_status_json_exports_transport_fields(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
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
            "target_hint",
            "follow_hhkb_usb",
            "dualup_mode",
            "dualup_inputs",
        ):
            self.assertIn(key, data)
        self.assertIn(data["hhkb_transport"], {"usb", "bluetooth", "both", "unknown", "absent"})
        self.assertEqual(data["dualup_inputs"]["mac"], "hdmi1")
        self.assertEqual(data["dualup_inputs"]["linux"], "dp")


if __name__ == "__main__":
    unittest.main()

