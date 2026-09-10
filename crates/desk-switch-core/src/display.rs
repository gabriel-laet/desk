use std::path::PathBuf;

use regex::Regex;
use serde_json::Value;

use crate::adapters::{bind_role, which_adapter};
use crate::cache::{load_dualup_mode_cache, save_dualup_mode_cache};
use crate::config::{display_cfg_from_loaded, Config};
use crate::error::Result;
use crate::json_util::{as_dict, as_int, opt_str};
use crate::paths::{bundled_layout_helper, which_cmd};
use crate::process::{adapter_argv, Runtime};

pub const LAYOUT_FULL_MODES: &[&str] = &["full", "off", "none", "solo"];
pub const PBP_INPUT_DEFAULTS: &[(&str, &str)] = &[("linux", "dp"), ("mac", "hdmi1")];
pub const PBP_INPUT_ORDER: &[&str] = &["linux", "mac"];

pub fn lgdualup_path(cfg: &Config) -> Option<PathBuf> {
    if !cfg.dualup_enabled {
        return None;
    }
    bind_role(cfg, "display").path
}

pub fn layout_verb(mode: &str) -> &'static str {
    let key = mode.trim().to_ascii_lowercase();
    if LAYOUT_FULL_MODES.contains(&key.as_str()) {
        "full"
    } else {
        "pbp"
    }
}

pub fn call_display(cfg: &Config, rt: &Runtime, args: &[&str], missing: &str) -> i32 {
    let Some(path) = lgdualup_path(cfg) else {
        rt.print(missing);
        return 0;
    };
    let timeout = if args.first() == Some(&"pbp-assign") {
        15.0
    } else {
        8.0
    };
    let argv = adapter_argv(&path, args);
    let proc = match rt.run(&argv, timeout) {
        Ok(p) => p,
        Err(exc) => {
            rt.eprint(&format!("lgdualup failed to start: {exc}"));
            return 1;
        }
    };
    rt.print_helper(&proc);
    proc.status
}

pub fn dualup_layout_path(cfg: &Config) -> Option<PathBuf> {
    let adapters = display_cfg_from_loaded(cfg);
    if !cfg.dualup_layout {
        return None;
    }
    let configured = cfg
        .dualup_layout_helper
        .clone()
        .or_else(|| opt_str(adapters.get("layout_helper")));
    if let Some(path) = which_adapter("dualup-layout", configured.as_deref()) {
        return Some(path);
    }
    bundled_layout_helper()
}

pub fn apply_display_layout(cfg: &Config, rt: &Runtime, mode: &str) -> i32 {
    let Some(path) = dualup_layout_path(cfg) else {
        if !cfg.dualup_layout {
            return 0;
        }
        rt.print("dualup layout helper not found — run `make install` (OS layout skipped)");
        return 0;
    };
    let adapters = display_cfg_from_loaded(cfg);
    let display_id = cfg
        .dualup_display_id
        .clone()
        .or_else(|| opt_str(adapters.get("display_id")))
        .unwrap_or_default()
        .trim()
        .to_string();
    let retries = cfg.dualup_layout_retries.max(1);
    let delay = cfg.dualup_layout_retry_delay_s;
    let verb = layout_verb(mode);
    let mut argv = adapter_argv(&path, &[verb]);
    if !display_id.is_empty() {
        argv.push("--id".into());
        argv.push(display_id);
    }
    let layouts = as_dict(adapters.get("layouts").unwrap_or(&Value::Null));
    let user = as_dict(layouts.get(verb).unwrap_or(&Value::Null));
    if let Some(res) = opt_str(user.get("res")) {
        argv.push("--res".into());
        argv.push(res);
    }
    if user.get("degree").is_some() {
        argv.push("--degree".into());
        argv.push(
            user.get("degree")
                .map(|v| v.to_string())
                .unwrap_or_default(),
        );
    }
    let mut last_rc = 1;
    let attempts = retries.max(1);
    for attempt in 1..=attempts {
        let proc = match rt.run(&argv, 12.0) {
            Ok(p) => p,
            Err(exc) => {
                rt.eprint(&format!("dualup-layout failed to start: {exc}"));
                return 1;
            }
        };
        rt.print_helper(&proc);
        last_rc = proc.status;
        if last_rc == 0 {
            return 0;
        }
        if last_rc != 2 || attempt >= attempts {
            break;
        }
        rt.sleep(delay);
    }
    last_rc
}

pub fn apply_peer_layout(cfg: &Config, rt: &Runtime, mode: &str) -> i32 {
    let adapters = display_cfg_from_loaded(cfg);
    let peer = cfg
        .dualup_peer
        .clone()
        .or_else(|| opt_str(adapters.get("peer")))
        .unwrap_or_default()
        .trim()
        .to_string();
    if peer.is_empty() {
        return 0;
    }
    let verb = layout_verb(mode);
    let remote = opt_str(adapters.get("peer_layout"))
        .unwrap_or_else(|| "~/.local/lib/desk-switch/dualup-layout".into());
    rt.print(&format!("DualUp peer layout → {peer} {verb}"));
    let argv = vec![
        "ssh".into(),
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        "ConnectTimeout=5".into(),
        peer.clone(),
        format!("{remote} {verb}"),
    ];
    match rt.run(&argv, 15.0) {
        Ok(proc) => {
            rt.print_helper(&proc);
            if proc.status != 0 {
                rt.eprint(&format!("dualup peer layout failed (rc={})", proc.status));
            }
            0
        }
        Err(exc) => {
            rt.eprint(&format!("dualup peer layout skipped: {exc}"));
            0
        }
    }
}

pub fn dualup_pbp_inputs(cfg: &Config) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for host in PBP_INPUT_ORDER {
        let spec = cfg.hosts.get(*host);
        let name = spec
            .and_then(|s| s.dualup_input.clone())
            .unwrap_or_default()
            .trim()
            .to_string();
        let name = if name.is_empty() {
            PBP_INPUT_DEFAULTS
                .iter()
                .find(|(h, _)| h == host)
                .map(|(_, d)| (*d).to_string())
                .unwrap_or_else(|| "hdmi1".into())
        } else {
            name
        };
        out.push(((*host).to_string(), name));
    }
    out
}

pub fn dualup_assign_pbp_inputs(cfg: &Config, rt: &Runtime) -> i32 {
    let by_host: std::collections::BTreeMap<_, _> = dualup_pbp_inputs(cfg).into_iter().collect();
    let main = by_host
        .get("mac")
        .cloned()
        .unwrap_or_else(|| "hdmi1".into());
    let sub = by_host.get("linux").cloned().unwrap_or_else(|| "dp".into());
    rt.print(&format!(
        "DualUp PBP assign → main={main} (mac)  sub={sub} (linux)"
    ));
    call_display(
        cfg,
        rt,
        &["pbp-assign", &main, &sub],
        "lgdualup not on PATH — run `make install`",
    )
}

fn settle_s(cfg: &Config) -> f64 {
    cfg.dualup_layout_settle_s
}

/// USB PBP/full, then (for PBP) input pair, settle, then OS layout.
pub fn display_set_mode(cfg: &Config, rt: &Runtime, mode: &str, missing: &str) -> i32 {
    let chosen = mode.trim();
    let verb = layout_verb(chosen);
    rt.print(&format!("DualUp {verb} → {chosen}"));
    if verb == "full" || verb == "pbp" {
        save_dualup_mode_cache(verb);
    }
    let mut rc = call_display(cfg, rt, &["pbp", chosen], missing);
    if lgdualup_path(cfg).is_none() {
        return rc;
    }
    if rc != 0 {
        return rc;
    }
    if verb == "pbp" {
        let assign_rc = dualup_assign_pbp_inputs(cfg, rt);
        if rc == 0 {
            rc = assign_rc;
        }
    }
    let settle = settle_s(cfg);
    if settle > 0.0 {
        rt.sleep(settle);
    }
    let layout_rc = apply_display_layout(cfg, rt, chosen);
    apply_peer_layout(cfg, rt, chosen);
    if rc != 0 {
        rc
    } else {
        layout_rc
    }
}

pub fn switch_monitor(cfg: &Config, rt: &Runtime, host: &str, mouse_only: bool) -> Result<i32> {
    if mouse_only || !cfg.switch_monitor {
        return Ok(0);
    }
    if lgdualup_path(cfg).is_none() {
        rt.print("lgdualup not on PATH — run `make install` (DualUp helper ships in this repo)");
        return Ok(0);
    }
    let dest = crate::hosts::normalize_host(host)?;
    let spec = cfg.hosts.get(&dest);
    let pbp = spec.and_then(|s| s.pbp.clone());
    if pbp.is_some() || cfg.switch_pbp {
        let mode = pbp
            .or_else(|| Some(cfg.pbp_mode.clone()))
            .unwrap_or_default()
            .trim()
            .to_string();
        if !mode.is_empty() {
            return Ok(display_set_mode(
                cfg,
                rt,
                &mode,
                "lgdualup not on PATH — run `make install`",
            ));
        }
    }
    let name = spec
        .and_then(|s| s.dualup_input.clone())
        .unwrap_or_default()
        .trim()
        .to_string();
    if !name.is_empty() {
        rt.print(&format!("DualUp input → {name}"));
        return Ok(call_display(
            cfg,
            rt,
            &["input", &name],
            "lgdualup not on PATH — run `make install`",
        ));
    }
    rt.print(&format!(
        "no dualup_input configured for {dest} — leaving DualUp input alone"
    ));
    Ok(0)
}

pub fn dualup_inputs_map(cfg: &Config) -> serde_json::Map<String, Value> {
    let mut out = serde_json::Map::new();
    for (host, default) in PBP_INPUT_DEFAULTS {
        let name = cfg
            .hosts
            .get(*host)
            .and_then(|s| s.dualup_input.clone())
            .unwrap_or_default()
            .trim()
            .to_string();
        out.insert(
            (*host).to_string(),
            Value::String(if name.is_empty() {
                (*default).to_string()
            } else {
                name
            }),
        );
    }
    out
}

fn parse_mode_from_res(width: i64, height: i64) -> Option<&'static str> {
    match (width, height) {
        (2880, 2560) | (2560, 2880) => Some("full"),
        (2880, 1280) | (1280, 2880) => Some("pbp"),
        _ => None,
    }
}

pub fn detect_display_mode_linux(monitors: Option<&Value>) -> String {
    let owned;
    let data = if let Some(v) = monitors {
        v
    } else {
        let Some(hypr) = which_cmd("hyprctl") else {
            return "unknown".into();
        };
        let argv = vec![
            hypr.to_string_lossy().into_owned(),
            "-j".into(),
            "monitors".into(),
        ];
        let rt = Runtime::default();
        let Ok(proc) = rt.run(&argv, 5.0) else {
            return "unknown".into();
        };
        owned = match serde_json::from_str::<Value>(proc.stdout.trim()) {
            Ok(v) => v,
            Err(_) => return "unknown".into(),
        };
        &owned
    };
    let Some(arr) = data.as_array() else {
        return "unknown".into();
    };
    let markers = ["sdqhd", "dualup", "28mq780", "lg electronics"];
    for mon in arr {
        let Some(obj) = mon.as_object() else {
            continue;
        };
        let desc = format!(
            "{} {}",
            obj.get("description")
                .and_then(|v| v.as_str())
                .unwrap_or(""),
            obj.get("name").and_then(|v| v.as_str()).unwrap_or("")
        )
        .to_ascii_lowercase();
        let width = as_int(obj.get("width").unwrap_or(&Value::from(0)), 0);
        let height = as_int(obj.get("height").unwrap_or(&Value::from(0)), 0);
        let mode = parse_mode_from_res(width, height);
        let looks = markers.iter().any(|m| desc.contains(m)) || mode.is_some();
        if looks {
            if let Some(mode) = mode {
                return mode.to_string();
            }
        }
    }
    "unknown".into()
}

pub fn detect_display_mode_macos(placer_list: Option<&str>) -> String {
    let owned;
    let text = if let Some(t) = placer_list {
        t
    } else {
        let Some(placer) = which_cmd("displayplacer") else {
            return "unknown".into();
        };
        let argv = vec![placer.to_string_lossy().into_owned(), "list".into()];
        let rt = Runtime::default();
        let Ok(proc) = rt.run(&argv, 8.0) else {
            return "unknown".into();
        };
        owned = proc.stdout;
        &owned
    };
    let re = Regex::new(r"(?:Resolution:|res:)\s*(\d{3,5})x(\d{3,5})").ok();
    let built_in = Regex::new(r"(?i)built[ -]?in").ok();
    let mut skip = false;
    for line in text.lines() {
        if line.starts_with("Persistent screen id:") {
            skip = false;
            continue;
        }
        if built_in
            .as_ref()
            .map(|re| re.is_match(line))
            .unwrap_or(false)
        {
            skip = true;
            continue;
        }
        if skip {
            continue;
        }
        if let Some(re) = &re {
            if let Some(caps) = re.captures(line) {
                let w: i64 = caps
                    .get(1)
                    .and_then(|m| m.as_str().parse().ok())
                    .unwrap_or(0);
                let h: i64 = caps
                    .get(2)
                    .and_then(|m| m.as_str().parse().ok())
                    .unwrap_or(0);
                if let Some(mode) = parse_mode_from_res(w, h) {
                    return mode.to_string();
                }
            }
        }
    }
    "unknown".into()
}

pub fn detect_display_mode(cfg: Option<&Config>) -> String {
    let _ = cfg;
    let mode = if crate::paths::system() == "Linux" {
        detect_display_mode_linux(None)
    } else if crate::paths::system() == "Darwin" {
        detect_display_mode_macos(None)
    } else {
        "unknown".into()
    };
    if mode == "full" || mode == "pbp" {
        save_dualup_mode_cache(&mode);
        return mode;
    }
    load_dualup_mode_cache().unwrap_or_else(|| "unknown".into())
}
