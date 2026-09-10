use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_desk-switch"))
}

fn run_isolated(args: &[&str]) -> std::process::Output {
    let home = std::env::temp_dir().join(format!(
        "desk-switch-cli-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(home.join(".config/desk-switch")).unwrap();
    fs::create_dir_all(home.join("lib")).unwrap();
    fs::write(
        home.join(".config/desk-switch/config.json"),
        r#"{"this_host":"linux","target_channel":1,"mxswitch":"/no/such/mxswitch","lgdualup":"lgdualup-missing"}"#,
    )
    .unwrap();
    Command::new(bin())
        .args(args)
        .env("HOME", &home)
        .env("XDG_CACHE_HOME", home.join("cache"))
        .env("DESK_SWITCH_LIB", home.join("lib"))
        .env("PATH", "/usr/bin:/bin")
        .output()
        .expect("run desk-switch")
}

#[test]
fn help_lists_to_mac_and_watch() {
    let out = Command::new(bin()).arg("--help").output().expect("help");
    assert!(out.status.success());
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("to mac"), "{text}");
    assert!(text.contains("watch"), "{text}");
}

#[test]
fn version_is_semver() {
    let out = Command::new(bin())
        .arg("--version")
        .output()
        .expect("version");
    assert!(out.status.success());
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("desk-switch 1."), "{text}");
}

#[test]
fn status_json_has_contract_keys() {
    let out = run_isolated(&["status", "--json", "--local"]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let data: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    for key in [
        "target_hint",
        "bar_label",
        "bar_strip",
        "hhkb_transport",
        "adapters",
        "lgdualup",
        "dualup_mode",
        "follow_hhkb_usb",
    ] {
        assert!(data.get(key).is_some(), "missing {key} in {data}");
    }
    assert!(data["bar_strip"].get("focus").is_some());
    assert!(data["adapters"].get("discovered").is_some());
    assert!(data["adapters"].get("keyboard").is_some());
    assert_eq!(data["adapters"]["mouse"]["backend"], "mxswitch");
    assert_eq!(data["adapters"]["display"]["backend"], "lgdualup");
    assert_eq!(data["ui"]["tray"]["density"], "strip");
}

#[test]
fn pbp_without_helper_is_noop() {
    let out = run_isolated(&["pbp", "50-50"]);
    assert!(out.status.success());
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("lgdualup not on PATH"), "{text}");
}

#[test]
fn switch_rejects_bad_channel() {
    let out = Command::new(bin()).args(["switch", "9"]).output().unwrap();
    assert!(!out.status.success());
}

#[test]
fn to_unknown_host() {
    let out = Command::new(bin()).args(["to", "sparc"]).output().unwrap();
    assert!(!out.status.success());
}
