//! Port of `tests/test_desk_switch.py` core semantics (host map, discovery,
//! watch USB edge, status keys, PBP sequence, layout exit-2).

use std::collections::VecDeque;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};

use crate::adapters::{bind_role, resolve_backend_id, scan_libdir_manifests};
use crate::cache::{load_mouse_cache, save_mouse_cache};
use crate::commands::{cmd_full, cmd_pbp};
use crate::config::{load_config_from, Config};
use crate::display::{
    detect_display_mode_linux, detect_display_mode_macos, dualup_pbp_inputs, layout_verb,
    lgdualup_path,
};
use crate::hosts::{normalize_host, resolve_hosts, HostSpec};
use crate::keyboard::Presence;
use crate::mouse::parse_mouse_channel;
use crate::process::{CaptureIo, CommandOutput, RunError, Runner, Runtime, Sleeper};

static ENV_LOCK: Mutex<()> = Mutex::new(());
use crate::status::{
    collect_status_with_probe, format_bar_label, format_bar_strip, format_strip_title,
    resolve_target_hint, target_hint,
};
use crate::watch::{cmd_watch_with, watch_usb_rising_edge};

fn write_exe(path: &Path, body: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(path, body).unwrap();
    let mut perm = fs::metadata(path).unwrap().permissions();
    perm.set_mode(0o755);
    fs::set_permissions(path, perm).unwrap();
}

fn temp_dir() -> PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static N: AtomicU64 = AtomicU64::new(0);
    let base = std::env::temp_dir().join(format!(
        "desk-switch-rs-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = fs::remove_dir_all(&base);
    fs::create_dir_all(&base).unwrap();
    base
}

struct ScriptedRunner {
    calls: Mutex<Vec<Vec<String>>>,
    script: Mutex<VecDeque<CommandOutput>>,
}

impl ScriptedRunner {
    fn new(outputs: Vec<CommandOutput>) -> Self {
        Self {
            calls: Mutex::new(Vec::new()),
            script: Mutex::new(outputs.into()),
        }
    }
}

impl Runner for ScriptedRunner {
    fn run(&self, argv: &[String], _timeout: Duration) -> Result<CommandOutput, RunError> {
        self.calls.lock().unwrap().push(argv.to_vec());
        let mut script = self.script.lock().unwrap();
        if let Some(out) = script.pop_front() {
            Ok(out)
        } else {
            Ok(CommandOutput {
                status: 0,
                stdout: format!("ok:{}", argv.get(1..).unwrap_or(&[]).join(" ")),
                stderr: String::new(),
            })
        }
    }
}

struct RecordSleep {
    sleeps: Mutex<Vec<f64>>,
}

impl Sleeper for RecordSleep {
    fn sleep(&self, seconds: f64) {
        self.sleeps.lock().unwrap().push(seconds);
    }
}

fn ok_out(stdout: &str) -> CommandOutput {
    CommandOutput {
        status: 0,
        stdout: stdout.into(),
        stderr: String::new(),
    }
}

fn rt_with(
    runner: ScriptedRunner,
    sleeper: RecordSleep,
) -> (
    Runtime,
    std::sync::Arc<CaptureIo>,
    std::sync::Arc<ScriptedRunner>,
    std::sync::Arc<RecordSleep>,
) {
    let io = std::sync::Arc::new(CaptureIo::default());
    let runner = std::sync::Arc::new(runner);
    let sleeper = std::sync::Arc::new(sleeper);
    let rt = Runtime {
        runner: runner.clone(),
        sleeper: sleeper.clone(),
        clock: std::sync::Arc::new(crate::process::StdClock),
        io: io.clone(),
    };
    (rt, io, runner, sleeper)
}

fn base_cfg(lg: &Path, layout: &Path) -> Config {
    let dir = temp_dir();
    let cfg_path = dir.join("cfg.json");
    fs::write(
        &cfg_path,
        serde_json::to_string(&json!({
            "this_host": "mac",
            "pbp_mode": "50-50",
            "lgdualup": lg.to_string_lossy(),
            "adapters": {
                "mouse": {"enabled": false},
                "keyboard": {"enabled": false},
                "dualup": {
                    "enabled": true,
                    "layout": true,
                    "layout_helper": layout.to_string_lossy(),
                    "layout_retries": 3,
                    "layout_retry_delay_s": 0,
                    "layout_settle_s": 0,
                    "inputs": {"mac": "hdmi1", "linux": "dp"}
                }
            }
        }))
        .unwrap(),
    )
    .unwrap();
    load_config_from(Some(&[cfg_path])).unwrap()
}

#[test]
fn host_aliases() {
    assert_eq!(normalize_host("macos").unwrap(), "mac");
    assert_eq!(normalize_host("LNX").unwrap(), "linux");
    assert_eq!(normalize_host("omarchy").unwrap(), "linux");
    assert!(normalize_host("windows").is_err());
}

#[test]
fn legacy_target_channel_overrides_other_host() {
    let supplied = std::collections::BTreeMap::new();
    let cfg_this = "linux";
    let hosts = resolve_hosts(cfg_this, Some(1), &supplied);
    assert_eq!(hosts["mac"].channel, Some(1));
    assert_eq!(hosts["linux"].channel, Some(2));
    let _ = supplied;
}

#[test]
fn explicit_hosts_keep_channels() {
    let mut supplied = std::collections::BTreeMap::new();
    supplied.insert("mac".into(), json!({"channel": 1}));
    supplied.insert("linux".into(), json!({"channel": 3}));
    let hosts = resolve_hosts("mac", Some(2), &supplied);
    assert_eq!(hosts["linux"].channel, Some(3));
    assert_eq!(hosts["mac"].channel, Some(1));
}

#[test]
fn int_host_entry() {
    let mut supplied = std::collections::BTreeMap::new();
    supplied.insert("linux".into(), json!(3));
    let hosts = resolve_hosts("mac", Some(3), &supplied);
    assert_eq!(hosts["linux"].channel, Some(3));
}

#[test]
fn hint_from_mouse_channel() {
    let mut cfg = load_isolated("{}");
    cfg.this_host = "linux".into();
    cfg.hosts.insert(
        "mac".into(),
        HostSpec {
            channel: Some(1),
            ..HostSpec::default()
        },
    );
    cfg.hosts.insert(
        "linux".into(),
        HostSpec {
            channel: Some(2),
            ..HostSpec::default()
        },
    );
    assert_eq!(target_hint(&cfg, Some(1), Some(false)), "MAC");
    assert_eq!(target_hint(&cfg, Some(2), Some(false)), "LNX");
    assert_eq!(target_hint(&cfg, None, Some(true)), "LNX");
    assert_eq!(target_hint(&cfg, None, Some(false)), "?");
}

fn load_isolated(json_text: &str) -> Config {
    let dir = temp_dir();
    let path = dir.join("cfg.json");
    fs::write(&path, json_text).unwrap();
    load_config_from(Some(&[path])).unwrap()
}

#[test]
fn parse_mouse_channel_line() {
    assert_eq!(
        parse_mouse_channel("channels   : 3, currently on 2"),
        Some(2)
    );
    assert_eq!(parse_mouse_channel("no mouse"), None);
}

#[test]
fn adapters_overlay_hosts_and_inputs() {
    let cfg = load_isolated(
        r#"{
            "adapters": {
                "mouse": {"enabled": true, "path": "/opt/mxswitch"},
                "hosts": {
                    "this_host": "linux",
                    "follow_channel": 3,
                    "mac": {"channel": 1},
                    "linux": {"channel": 3}
                },
                "dualup": {
                    "enabled": true,
                    "inputs": {"mac": "hdmi1", "linux": "dp"}
                }
            }
        }"#,
    );
    assert_eq!(cfg.this_host, "linux");
    assert_eq!(cfg.target_channel, 3);
    assert_eq!(cfg.hosts["linux"].channel, Some(3));
    assert_eq!(cfg.hosts["mac"].dualup_input.as_deref(), Some("hdmi1"));
    assert_eq!(cfg.hosts["linux"].dualup_input.as_deref(), Some("dp"));
    assert_eq!(cfg.mxswitch, "/opt/mxswitch");
    assert!(cfg.mouse_enabled);
    assert!(cfg.dualup_enabled);
}

#[test]
fn display_alias_and_backend_and_density() {
    let cfg = load_isolated(
        r#"{
            "adapters": {
                "mouse": {"backend": "dummy-mouse"},
                "keyboard": {"backend": "hhkb"},
                "display": {
                    "enabled": true,
                    "backend": "lgdualup",
                    "inputs": {"mac": "hdmi1", "linux": "dp"}
                }
            },
            "ui": {"tray": {"density": "chips"}}
        }"#,
    );
    assert_eq!(cfg.mouse_backend.as_deref(), Some("dummy-mouse"));
    assert_eq!(cfg.display_backend.as_deref(), Some("lgdualup"));
    assert_eq!(cfg.keyboard_backend.as_deref(), Some("hhkb"));
    assert_eq!(cfg.hosts["linux"].dualup_input.as_deref(), Some("dp"));
    assert_eq!(cfg.tray_density(), "chips");
}

#[test]
fn legacy_keys_still_load() {
    let cfg = load_isolated(
        r#"{
            "this_host": "mac",
            "target_channel": 2,
            "mxswitch": "/old/mxswitch",
            "lgdualup": "/old/lgdualup",
            "hosts": {
                "mac": {"channel": 1, "dualup_input": "hdmi1"},
                "linux": {"channel": 2}
            }
        }"#,
    );
    assert_eq!(cfg.this_host, "mac");
    assert_eq!(cfg.target_channel, 2);
    assert_eq!(cfg.mxswitch, "/old/mxswitch");
    assert_eq!(cfg.lgdualup, "/old/lgdualup");
    assert_eq!(cfg.hosts["mac"].dualup_input.as_deref(), Some("hdmi1"));
}

#[test]
fn dualup_adapter_can_be_disabled() {
    let cfg =
        load_isolated(r#"{"adapters":{"dualup":{"enabled":false}},"lgdualup":"/tmp/lgdualup"}"#);
    assert!(lgdualup_path(&cfg).is_none());
}

#[test]
fn which_adapter_prefers_libexec() {
    let _g = ENV_LOCK.lock().unwrap();
    let dir = temp_dir();
    let lib = dir.join("lib");
    fs::create_dir_all(&lib).unwrap();
    let helper = lib.join("lgdualup");
    write_exe(&helper, "#!/bin/sh\n");
    std::env::set_var("DESK_SWITCH_LIB", &lib);
    let path = crate::adapters::which_adapter("lgdualup", Some("lgdualup"));
    std::env::remove_var("DESK_SWITCH_LIB");
    assert_eq!(path, Some(helper));
}

#[test]
fn which_adapter_honors_explicit_missing_name() {
    let _g = ENV_LOCK.lock().unwrap();
    std::env::set_var("DESK_SWITCH_LIB", "/no/libexec");
    let path = crate::adapters::which_adapter("lgdualup", Some("lgdualup-missing"));
    std::env::remove_var("DESK_SWITCH_LIB");
    assert!(path.is_none());
}

#[test]
fn layout_verb_maps() {
    assert_eq!(layout_verb("full"), "full");
    assert_eq!(layout_verb("off"), "full");
    assert_eq!(layout_verb("50-50"), "pbp");
    assert_eq!(layout_verb("on"), "pbp");
}

#[test]
fn pbp_input_order_is_dp_then_hdmi1() {
    let cfg = load_isolated(r#"{"adapters":{"dualup":{"inputs":{"mac":"hdmi1","linux":"dp"}}}}"#);
    assert_eq!(
        dualup_pbp_inputs(&cfg),
        vec![
            ("linux".into(), "dp".into()),
            ("mac".into(), "hdmi1".into())
        ]
    );
}

#[test]
fn pbp_input_defaults_when_empty() {
    let cfg = load_isolated(r#"{"hosts":{"mac":{},"linux":{}}}"#);
    assert_eq!(
        dualup_pbp_inputs(&cfg),
        vec![
            ("linux".into(), "dp".into()),
            ("mac".into(), "hdmi1".into())
        ]
    );
}

fn helpers(tmp: &Path) -> (PathBuf, PathBuf) {
    let lg = tmp.join("lgdualup");
    let layout = tmp.join("dualup-layout");
    write_exe(&lg, "#!/bin/sh\necho usb:\"$@\"\n");
    write_exe(&layout, "#!/bin/sh\necho layout:\"$@\"\n");
    (lg, layout)
}

#[test]
fn pbp_invokes_usb_inputs_then_layout() {
    let tmp = temp_dir();
    let (lg, layout) = helpers(&tmp);
    let cfg = base_cfg(&lg, &layout);
    let runner = ScriptedRunner::new(vec![
        ok_out("ok:pbp 50-50"),
        ok_out("ok:pbp-assign hdmi1 dp"),
        ok_out("ok:pbp"),
    ]);
    let (rt, io, runner, _) = rt_with(
        runner,
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    let rc = cmd_pbp(&cfg, &rt, None).unwrap();
    assert_eq!(rc, 0);
    let calls = runner.calls.lock().unwrap().clone();
    assert_eq!(&calls[0][1..], ["pbp", "50-50"]);
    assert_eq!(&calls[1][1..], ["pbp-assign", "hdmi1", "dp"]);
    assert_eq!(
        calls[2],
        vec![layout.to_string_lossy().into_owned(), "pbp".into()]
    );
    let out = io.stdout_text();
    assert!(out.contains("ok:pbp 50-50"), "{out}");
}

#[test]
fn full_invokes_usb_then_layout_without_inputs() {
    let tmp = temp_dir();
    let (lg, layout) = helpers(&tmp);
    let cfg = base_cfg(&lg, &layout);
    let runner = ScriptedRunner::new(vec![ok_out("ok"), ok_out("ok")]);
    let (rt, _, runner, _) = rt_with(
        runner,
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    let rc = cmd_full(&cfg, &rt).unwrap();
    assert_eq!(rc, 0);
    let calls = runner.calls.lock().unwrap().clone();
    assert_eq!(&calls[0][1..], ["pbp", "full"]);
    assert_eq!(
        calls[1],
        vec![layout.to_string_lossy().into_owned(), "full".into()]
    );
    assert_eq!(calls.len(), 2);
}

#[test]
fn full_settles_before_layout() {
    let tmp = temp_dir();
    let (lg, layout) = helpers(&tmp);
    let mut cfg = base_cfg(&lg, &layout);
    cfg.dualup_layout_settle_s = 0.4;
    let runner = ScriptedRunner::new(vec![ok_out("ok"), ok_out("ok")]);
    let sleeper = RecordSleep {
        sleeps: Mutex::new(Vec::new()),
    };
    let (rt, _, runner, sleeper) = rt_with(runner, sleeper);
    let rc = cmd_full(&cfg, &rt).unwrap();
    assert_eq!(rc, 0);
    let calls = runner.calls.lock().unwrap().clone();
    let sleeps = sleeper.sleeps.lock().unwrap().clone();
    assert_eq!(calls[0][0], lg.to_string_lossy());
    assert_eq!(calls[1][0], layout.to_string_lossy());
    assert_eq!(sleeps, vec![0.4]);
}

#[test]
fn pbp_settles_after_assign_before_layout() {
    let tmp = temp_dir();
    let (lg, layout) = helpers(&tmp);
    let mut cfg = base_cfg(&lg, &layout);
    cfg.dualup_layout_settle_s = 0.4;
    let runner = ScriptedRunner::new(vec![ok_out("ok"), ok_out("ok"), ok_out("ok")]);
    let sleeper = RecordSleep {
        sleeps: Mutex::new(Vec::new()),
    };
    let (rt, _, runner, sleeper) = rt_with(runner, sleeper);
    let rc = cmd_pbp(&cfg, &rt, Some("50-50")).unwrap();
    assert_eq!(rc, 0);
    let calls = runner.calls.lock().unwrap().clone();
    let sleeps = sleeper.sleeps.lock().unwrap().clone();
    assert_eq!(calls[0][1], "pbp");
    assert_eq!(calls[1][1], "pbp-assign");
    assert_eq!(calls[2][0], layout.to_string_lossy());
    assert_eq!(sleeps, vec![0.4]);
}

#[test]
fn layout_retries_when_edid_not_ready() {
    let tmp = temp_dir();
    let (lg, layout) = helpers(&tmp);
    let mut cfg = base_cfg(&lg, &layout);
    cfg.dualup_display_id = Some("ABCD".into());
    let runner = ScriptedRunner::new(vec![
        ok_out("usb"),
        CommandOutput {
            status: 2,
            stdout: String::new(),
            stderr: "not yet".into(),
        },
        ok_out("applied"),
    ]);
    let sleeper = RecordSleep {
        sleeps: Mutex::new(Vec::new()),
    };
    let (rt, _, _, sleeper) = rt_with(runner, sleeper);
    let rc = cmd_full(&cfg, &rt).unwrap();
    assert_eq!(rc, 0);
    assert_eq!(*sleeper.sleeps.lock().unwrap(), vec![0.0]);
}

#[test]
fn layout_helper_gets_display_id() {
    let tmp = temp_dir();
    let (lg, layout) = helpers(&tmp);
    let mut cfg = base_cfg(&lg, &layout);
    cfg.dualup_display_id = Some("9134432D-0196-4653-9712-EFCAF1980612".into());
    let runner = ScriptedRunner::new(vec![ok_out(""), ok_out("")]);
    let (rt, _, runner, _) = rt_with(
        runner,
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    cmd_full(&cfg, &rt).unwrap();
    let calls = runner.calls.lock().unwrap().clone();
    assert_eq!(
        calls[1],
        vec![
            layout.to_string_lossy().into_owned(),
            "full".into(),
            "--id".into(),
            "9134432D-0196-4653-9712-EFCAF1980612".into()
        ]
    );
}

#[test]
fn layout_disabled_skips_helper() {
    let tmp = temp_dir();
    let (lg, layout) = helpers(&tmp);
    let mut cfg = base_cfg(&lg, &layout);
    cfg.dualup_layout = false;
    let runner = ScriptedRunner::new(vec![ok_out("")]);
    let (rt, _, runner, _) = rt_with(
        runner,
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    let rc = cmd_full(&cfg, &rt).unwrap();
    assert_eq!(rc, 0);
    let calls = runner.calls.lock().unwrap().clone();
    assert_eq!(calls.len(), 1);
    assert_eq!(&calls[0][1..], ["pbp", "full"]);
}

#[test]
fn pbp_without_lgdualup_does_not_call_layout() {
    let _g = ENV_LOCK.lock().unwrap();
    let empty = temp_dir().join("empty-lib");
    fs::create_dir_all(&empty).unwrap();
    std::env::set_var("DESK_SWITCH_LIB", &empty);
    let cfg = load_isolated(
        r#"{"lgdualup":"lgdualup-missing","adapters":{"dualup":{"enabled":true}},"pbp_mode":"50-50"}"#,
    );
    let runner = ScriptedRunner::new(vec![]);
    let (rt, io, runner, _) = rt_with(
        runner,
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    let rc = cmd_pbp(&cfg, &rt, Some("50-50")).unwrap();
    assert_eq!(rc, 0);
    assert!(runner.calls.lock().unwrap().is_empty());
    assert!(io.stdout_text().contains("lgdualup not on PATH"));
    std::env::remove_var("DESK_SWITCH_LIB");
}

#[test]
fn display_id_from_adapter_config() {
    let cfg = load_isolated(
        r#"{
            "adapters": {
                "dualup": {
                    "display_id": "9134432D-0196-4653-9712-EFCAF1980612",
                    "inputs": {"mac": "hdmi1", "linux": "dp"}
                }
            }
        }"#,
    );
    assert_eq!(
        cfg.dualup_display_id.as_deref(),
        Some("9134432D-0196-4653-9712-EFCAF1980612")
    );
    assert_eq!(cfg.hosts["mac"].dualup_input.as_deref(), Some("hdmi1"));
    assert_eq!(cfg.hosts["linux"].dualup_input.as_deref(), Some("dp"));
}

#[test]
fn hint_mouse_wins_over_missing_hhkb() {
    let cfg =
        load_isolated(r#"{"this_host":"mac","hosts":{"mac":{"channel":1},"linux":{"channel":2}}}"#);
    assert_eq!(target_hint(&cfg, Some(2), Some(false)), "LNX");
    let (hint, source) = resolve_target_hint(&cfg, Some(2), Some(false), None, None);
    assert_eq!(hint, "LNX");
    assert_eq!(source, "mouse");
}

#[test]
fn hint_peer_mouse_when_local_unknown() {
    let cfg =
        load_isolated(r#"{"this_host":"mac","hosts":{"mac":{"channel":1},"linux":{"channel":2}}}"#);
    let (hint, source) = resolve_target_hint(&cfg, None, Some(false), Some(2), None);
    assert_eq!(hint, "LNX");
    assert_eq!(source, "peer_mouse");
}

#[test]
fn mouse_cache_roundtrip() {
    let _g = ENV_LOCK.lock().unwrap();
    let dir = temp_dir();
    std::env::set_var("XDG_CACHE_HOME", &dir);
    assert_eq!(load_mouse_cache(Some(100.0)), (None, None));
    save_mouse_cache(2, Some(50.0));
    let (channel, ts) = load_mouse_cache(Some(80.0));
    assert_eq!(channel, Some(2));
    assert_eq!(ts, Some(50.0));
    let (expired, _) = load_mouse_cache(Some(50.0 + crate::config::MOUSE_CACHE_TTL_S + 1.0));
    assert!(expired.is_none());
    std::env::remove_var("XDG_CACHE_HOME");
}

#[test]
fn bar_label_chips() {
    let label = format_bar_label(&json!({
        "target_hint": "LNX",
        "hhkb_usb": true,
        "hhkb_bluetooth": false,
        "hhkb_present": true,
        "mouse_channel": 2,
        "mouse_online": false,
        "dualup_mode": "pbp"
    }));
    assert_eq!(label, "LNX  kbU  mx2~  PBP");
    let no_hint = format_bar_label(&json!({
        "target_hint": "?",
        "hhkb_present": false,
        "mouse_channel": null,
        "dualup_mode": "pbp"
    }));
    assert_eq!(no_hint, "kb-  mx-  PBP");
    assert!(!no_hint.contains('?'));
    let strip = format_bar_strip(&json!({"target_hint": "LNX", "dualup_mode": "pbp"}));
    assert_eq!(strip, json!({"focus": "LNX", "display": "pbp"}));
    assert_eq!(format_strip_title(&strip), "LNX  PBP");
    assert_eq!(format_strip_title(&json!({"focus": "MAC"})), "MAC");
}

#[test]
fn mac_status_uses_cached_channel_and_bt_hhkb() {
    let _g = ENV_LOCK.lock().unwrap();
    let dir = temp_dir();
    std::env::set_var("XDG_CACHE_HOME", &dir);
    std::env::set_var("DESK_SWITCH_LIB", dir.join("empty-lib"));
    fs::create_dir_all(dir.join("empty-lib")).unwrap();
    let cfg = load_isolated(
        r#"{
            "this_host": "mac",
            "hosts": {
                "mac": {"channel": 1, "dualup_input": "hdmi1"},
                "linux": {"channel": 2, "dualup_input": "dp"}
            },
            "follow_hhkb_usb": true,
            "target_channel": 2,
            "mxswitch": "/no/mx",
            "adapters": {"mouse": {"enabled": true}, "dualup": {"enabled": false}}
        }"#,
    );
    save_mouse_cache(2, None);
    crate::cache::save_dualup_mode_cache("pbp");
    let runner = ScriptedRunner::new(vec![CommandOutput {
        status: 1,
        stdout: "No Logitech device".into(),
        stderr: String::new(),
    }]);
    let (rt, _, _, _) = rt_with(
        runner,
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    let probe = Presence {
        present: true,
        usb: false,
        bluetooth: true,
        transport: "bluetooth".into(),
        unknown: false,
        names: vec!["HHKB-Studio1".into()],
    };
    let state = collect_status_with_probe(&cfg, &rt, true, Some(probe), None);
    assert_eq!(state["target_hint"], "LNX");
    assert_eq!(state["target_hint_source"], "mouse_cached");
    assert_eq!(state["hhkb_bluetooth"], true);
    assert_eq!(state["hhkb_usb"], false);
    assert_eq!(state["hhkb_transport"], "bluetooth");
    assert_eq!(state["mouse_channel"], 2);
    assert_eq!(state["mouse_online"], false);
    assert_eq!(state["mouse_host"], "linux");
    assert_eq!(state["bar_label"], "LNX  kbB  mx2~  PBP");
    assert_eq!(state["dualup_inputs"]["mac"], "hdmi1");
    std::env::remove_var("XDG_CACHE_HOME");
    std::env::remove_var("DESK_SWITCH_LIB");
}

#[test]
fn peer_live_mouse_fills_mac_gap() {
    let _g = ENV_LOCK.lock().unwrap();
    let dir = temp_dir();
    std::env::set_var("XDG_CACHE_HOME", &dir);
    std::env::set_var("DESK_SWITCH_LIB", dir.join("empty-lib"));
    fs::create_dir_all(dir.join("empty-lib")).unwrap();
    let cfg = load_isolated(
        r#"{
            "this_host": "mac",
            "hosts": {"mac": {"channel": 1}, "linux": {"channel": 2}},
            "target_channel": 2,
            "mxswitch": "/no/mx"
        }"#,
    );
    let peer = json!({
        "reachable": true,
        "this_host": "linux",
        "mouse_channel": 2,
        "mouse_online": true,
        "hhkb_usb": true,
        "hhkb_transport": "usb",
        "target_hint": "LNX"
    });
    let runner = ScriptedRunner::new(vec![CommandOutput {
        status: 1,
        stdout: "missing".into(),
        stderr: String::new(),
    }]);
    let (rt, _, _, _) = rt_with(
        runner,
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    let state =
        collect_status_with_probe(&cfg, &rt, false, Some(Presence::empty(false)), Some(peer));
    assert_eq!(state["target_hint"], "LNX");
    assert_eq!(state["target_hint_source"], "peer_mouse");
    assert_eq!(state["mouse_channel"], 2);
    assert_eq!(state["peer"]["hhkb_transport"], "usb");
    std::env::remove_var("XDG_CACHE_HOME");
    std::env::remove_var("DESK_SWITCH_LIB");
}

#[test]
fn rising_edge_fires_once() {
    assert_eq!(watch_usb_rising_edge(None, true, true), (true, false));
    assert_eq!(watch_usb_rising_edge(Some(false), true, true), (true, true));
    assert_eq!(watch_usb_rising_edge(Some(true), true, true), (true, false));
    assert_eq!(
        watch_usb_rising_edge(Some(true), false, true),
        (false, false)
    );
    assert_eq!(
        watch_usb_rising_edge(Some(false), true, false),
        (true, false)
    );
}

#[test]
fn follow_hhkb_usb_default_on() {
    let cfg = load_isolated("{}");
    assert!(cfg.follow_hhkb_usb);
}

#[test]
fn follow_hhkb_usb_can_disable() {
    let cfg = load_isolated(r#"{"adapters":{"hosts":{"follow_hhkb_usb":false}}}"#);
    assert!(!cfg.follow_hhkb_usb);
}

#[test]
fn usb_edge_calls_to_this_host() {
    let cfg = load_isolated(
        r#"{
            "this_host": "mac",
            "follow_hhkb_usb": true,
            "hosts": {"mac": {"channel": 1}, "linux": {"channel": 2}},
            "target_channel": 2,
            "poll_interval_s": 0.01,
            "absent_polls_required": 1,
            "sleep_gap_s": 30,
            "switch_retries": 1,
            "switch_retry_delay_s": 0
        }"#,
    );
    let probes = std::sync::Arc::new(Mutex::new(VecDeque::from([
        Presence {
            present: true,
            usb: false,
            bluetooth: true,
            transport: "bluetooth".into(),
            unknown: false,
            names: vec![],
        },
        Presence {
            present: true,
            usb: true,
            bluetooth: false,
            transport: "usb".into(),
            unknown: false,
            names: vec![],
        },
    ])));
    let calls = std::sync::Arc::new(Mutex::new(Vec::new()));
    let runner = ScriptedRunner::new(vec![]);
    let (rt, _, _, _) = rt_with(
        runner,
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    let probes_c = probes.clone();
    let calls_c = calls.clone();
    let result = cmd_watch_with(
        &cfg,
        &rt,
        false,
        Box::new(move || {
            Ok(probes_c
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or_else(|| Presence::empty(false)))
        }),
        Box::new(move |host: &str| {
            calls_c.lock().unwrap().push(host.to_string());
            Err(crate::error::Error::Interrupted)
        }),
        Some(4),
    );
    assert!(matches!(result, Err(crate::error::Error::Interrupted)));
    assert_eq!(*calls.lock().unwrap(), vec!["mac".to_string()]);
}

#[test]
fn linux_pbp_and_full_mode() {
    let pbp =
        json!([{"name":"DP-2","description":"LG Electronics LG SDQHD","width":1280,"height":2880}]);
    let full =
        json!([{"name":"DP-2","description":"LG Electronics LG SDQHD","width":2560,"height":2880}]);
    assert_eq!(detect_display_mode_linux(Some(&pbp)), "pbp");
    assert_eq!(detect_display_mode_linux(Some(&full)), "full");
}

#[test]
fn macos_pbp_from_displayplacer() {
    let text = "Persistent screen id: ABC\nType: 28 inch external screen\nResolution: 2880x1280\n";
    assert_eq!(detect_display_mode_macos(Some(text)), "pbp");
    let full = "Persistent screen id: ABC\nType: 28 inch external screen\nResolution: 2880x2560\n";
    assert_eq!(detect_display_mode_macos(Some(full)), "full");
    let swapped =
        "Persistent screen id: ABC\nType: 28 inch external screen\nResolution: 2560x2880\n";
    assert_eq!(detect_display_mode_macos(Some(swapped)), "full");
    let pbp0 = "Persistent screen id: ABC\nType: 28 inch external screen\nResolution: 1280x2880\n";
    assert_eq!(detect_display_mode_macos(Some(pbp0)), "pbp");
}

fn dummy_lib(tmp: &Path) -> PathBuf {
    let lib = tmp.join("lib");
    fs::create_dir_all(&lib).unwrap();
    let root = crate::paths::source_root().expect("source root");
    let dummy = root.join("examples/dummy-mouse/dummy-mouse");
    let dest = lib.join("dummy-mouse");
    fs::copy(&dummy, &dest).unwrap();
    let mut perm = fs::metadata(&dest).unwrap().permissions();
    perm.set_mode(0o755);
    fs::set_permissions(&dest, perm).unwrap();
    fs::write(
        lib.join("dummy-mouse.manifest.json"),
        fs::read_to_string(root.join("examples/dummy-mouse/dummy-mouse.manifest.json")).unwrap(),
    )
    .unwrap();
    lib
}

#[test]
fn scan_finds_manifest() {
    let _g = ENV_LOCK.lock().unwrap();
    let tmp = temp_dir();
    let lib = dummy_lib(&tmp);
    std::env::set_var("DESK_SWITCH_LIB", &lib);
    let found: std::collections::BTreeMap<_, _> = scan_libdir_manifests(None)
        .into_iter()
        .map(|i| (i.id.clone(), i))
        .collect();
    std::env::remove_var("DESK_SWITCH_LIB");
    assert!(found.contains_key("dummy-mouse"));
    assert!(found["dummy-mouse"]
        .capabilities
        .contains(&"mouse.host_switch".into()));
    assert!(found["dummy-mouse"].path.is_some());
}

#[test]
fn backend_pin_binds_dummy_mouse() {
    let _g = ENV_LOCK.lock().unwrap();
    let tmp = temp_dir();
    let lib = dummy_lib(&tmp);
    std::env::set_var("DESK_SWITCH_LIB", &lib);
    let cfg = load_isolated(
        r#"{"adapters":{"mouse":{"enabled":true,"backend":"dummy-mouse"}},"mxswitch":"mxswitch"}"#,
    );
    let bound = bind_role(&cfg, "mouse");
    std::env::remove_var("DESK_SWITCH_LIB");
    assert_eq!(bound.id.as_deref(), Some("dummy-mouse"));
    assert_eq!(
        bound.path.as_ref().and_then(|p| p.file_name()),
        Some(std::ffi::OsStr::new("dummy-mouse"))
    );
    assert_eq!(bound.source, "backend");
}

#[test]
fn scan_binds_lone_mouse_adapter() {
    let _g = ENV_LOCK.lock().unwrap();
    let tmp = temp_dir();
    let lib = dummy_lib(&tmp);
    std::env::set_var("DESK_SWITCH_LIB", &lib);
    let cfg =
        load_isolated(r#"{"mxswitch":"mxswitch-missing","adapters":{"mouse":{"enabled":true}}}"#);
    let bound = bind_role(&cfg, "mouse");
    std::env::remove_var("DESK_SWITCH_LIB");
    assert_eq!(bound.id.as_deref(), Some("dummy-mouse"));
    assert_eq!(bound.source, "scan");
}

#[test]
fn ambiguous_mouse_adapters_need_a_pin() {
    let _g = ENV_LOCK.lock().unwrap();
    let tmp = temp_dir();
    let lib = dummy_lib(&tmp);
    write_exe(&lib.join("other-mouse"), "#!/bin/sh\nexit 0\n");
    fs::write(
        lib.join("other-mouse.manifest.json"),
        r#"{"api_version":1,"id":"other-mouse","name":"Other","capabilities":["mouse.host_switch"]}"#,
    )
    .unwrap();
    std::env::set_var("DESK_SWITCH_LIB", &lib);
    let cfg =
        load_isolated(r#"{"mxswitch":"mxswitch-missing","adapters":{"mouse":{"enabled":true}}}"#);
    let bound = bind_role(&cfg, "mouse");
    std::env::remove_var("DESK_SWITCH_LIB");
    assert!(bound.id.is_none());
    assert_eq!(bound.source, "ambiguous");
    let mut c = bound.candidates.clone();
    c.sort();
    assert_eq!(c, vec!["dummy-mouse".to_string(), "other-mouse".into()]);
}

#[test]
fn path_pin_stops_discovery() {
    let _g = ENV_LOCK.lock().unwrap();
    let tmp = temp_dir();
    let lib = dummy_lib(&tmp);
    std::env::set_var("DESK_SWITCH_LIB", &lib);
    let missing = tmp.join("missing-mouse");
    let cfg = load_isolated(&format!(
        r#"{{"adapters":{{"mouse":{{"enabled":true,"backend":"dummy-mouse","path":"{}"}}}}}}"#,
        missing.display()
    ));
    let bound = bind_role(&cfg, "mouse");
    std::env::remove_var("DESK_SWITCH_LIB");
    assert!(bound.path.is_none());
    assert_eq!(bound.source, "path");
}

#[test]
fn nested_libdir_and_path_prefix() {
    let _g = ENV_LOCK.lock().unwrap();
    let tmp = temp_dir();
    let lib = tmp.join("lib");
    let nested = lib.join("unifying");
    fs::create_dir_all(&nested).unwrap();
    write_exe(&nested.join("unifying"), "#!/bin/sh\necho ok\n");
    fs::write(
        nested.join("manifest.json"),
        r#"{"api_version":1,"id":"unifying","name":"Unifying","capabilities":["mouse.host_switch"]}"#,
    )
    .unwrap();
    std::env::set_var("DESK_SWITCH_LIB", &lib);
    assert_eq!(
        resolve_backend_id("unifying"),
        Some(nested.join("unifying"))
    );
    let found: std::collections::HashSet<_> = scan_libdir_manifests(None)
        .into_iter()
        .map(|i| i.id)
        .collect();
    assert!(found.contains("unifying"));
    std::env::remove_var("DESK_SWITCH_LIB");

    let bindir = tmp.join("bin");
    fs::create_dir_all(&bindir).unwrap();
    write_exe(&bindir.join("desk-switch-unifying"), "#!/bin/sh\n");
    let old_path = std::env::var("PATH").unwrap_or_default();
    std::env::set_var("PATH", format!("{}:/usr/bin:/bin", bindir.display()));
    std::env::set_var("DESK_SWITCH_LIB", tmp.join("empty-lib"));
    fs::create_dir_all(tmp.join("empty-lib")).unwrap();
    assert_eq!(
        resolve_backend_id("unifying"),
        Some(bindir.join("desk-switch-unifying"))
    );
    std::env::set_var("PATH", old_path);
    std::env::remove_var("DESK_SWITCH_LIB");
}

#[test]
fn default_linux_hosts_do_not_collide() {
    let _g = ENV_LOCK.lock().unwrap();
    if crate::paths::system() != "Linux" {
        return;
    }
    let dir = temp_dir();
    std::env::set_var("HOME", &dir);
    let cfg = load_config_from(Some(&[dir.join("none.json")])).unwrap();
    std::env::remove_var("HOME");
    assert_eq!(cfg.this_host, "linux");
    assert_eq!(cfg.target_channel, 1);
    assert_eq!(cfg.hosts["mac"].channel, Some(1));
    assert_eq!(cfg.hosts["linux"].channel, Some(2));
}

#[test]
fn example_config_is_hdmi1_and_dp() {
    let root = crate::paths::source_root().unwrap();
    let example: Value =
        serde_json::from_str(&fs::read_to_string(root.join("config.example.json")).unwrap())
            .unwrap();
    let inputs = &example["adapters"]["dualup"]["inputs"];
    assert_eq!(inputs["mac"], "hdmi1");
    assert_eq!(inputs["linux"], "dp");
    assert_ne!(inputs["mac"], "usbc");
}

#[test]
fn status_json_exports_required_keys() {
    let _g = ENV_LOCK.lock().unwrap();
    let dir = temp_dir();
    std::env::set_var("HOME", &dir);
    std::env::set_var("XDG_CACHE_HOME", dir.join("cache"));
    std::env::set_var("DESK_SWITCH_LIB", dir.join("lib"));
    fs::create_dir_all(dir.join("lib")).unwrap();
    fs::create_dir_all(dir.join(".config/desk-switch")).unwrap();
    fs::write(
        dir.join(".config/desk-switch/config.json"),
        r#"{"this_host":"linux","target_channel":1,"mxswitch":"/no/such/mxswitch","lgdualup":"lgdualup-missing"}"#,
    )
    .unwrap();
    let cfg = crate::config::load_config().unwrap();
    let (rt, _, _, _) = rt_with(
        ScriptedRunner::new(vec![]),
        RecordSleep {
            sleeps: Mutex::new(Vec::new()),
        },
    );
    let state = crate::status::collect_status(&cfg, &rt, true);
    for key in [
        "hhkb_transport",
        "hhkb_usb",
        "hhkb_bluetooth",
        "bar_label",
        "bar_strip",
        "target_hint",
        "follow_hhkb_usb",
        "dualup_mode",
        "dualup_inputs",
        "adapters",
        "ui",
    ] {
        assert!(state.get(key).is_some(), "missing {key}");
    }
    assert!(state["bar_strip"].get("focus").is_some());
    assert!(state["adapters"].get("keyboard").is_some());
    assert!(state["adapters"].get("discovered").is_some());
    assert_eq!(state["adapters"]["mouse"]["backend"], "mxswitch");
    assert_eq!(state["adapters"]["dualup"]["backend"], "lgdualup");
    assert_eq!(state["adapters"]["display"]["backend"], "lgdualup");
    assert_eq!(state["ui"]["tray"]["density"], "strip");
    assert_eq!(state["dualup_inputs"]["mac"], "hdmi1");
    assert_eq!(state["dualup_inputs"]["linux"], "dp");
    std::env::remove_var("HOME");
    std::env::remove_var("XDG_CACHE_HOME");
    std::env::remove_var("DESK_SWITCH_LIB");
}
