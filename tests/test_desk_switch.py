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


if __name__ == "__main__":
    unittest.main()
