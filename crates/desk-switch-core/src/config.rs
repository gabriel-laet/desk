use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use serde_json::Value;

use crate::error::{Error, Result};
use crate::hosts::{normalize_host, resolve_hosts, HostSpec};
use crate::json_util::{as_bool, as_dict, as_dict_ref, as_float, as_int, opt_str};
use crate::paths::{config_candidates, default_this_host};

pub const HHKB_VID_DEFAULT: i64 = 0x04FE;
pub const HHKB_PID_DEFAULT: i64 = 0x0016;
pub const LAYOUT_DEFAULT_RETRIES: i64 = 16;
pub const LAYOUT_DEFAULT_DELAY_S: f64 = 0.5;
pub const LAYOUT_DEFAULT_SETTLE_S: f64 = 0.5;
pub const MOUSE_CACHE_TTL_S: f64 = 24.0 * 3600.0;
pub const PEER_CACHE_TTL_S: f64 = 20.0;
pub const ADAPTER_API_VERSION: i64 = 1;

#[derive(Clone, Debug)]
pub struct Config {
    pub target_channel: i64,
    pub this_host: String,
    pub hhkb_vendor_id: i64,
    pub hhkb_product_id: i64,
    pub poll_interval_s: f64,
    pub absent_polls_required: i64,
    pub sleep_gap_s: f64,
    pub switch_retries: i64,
    pub switch_retry_delay_s: f64,
    pub mxswitch: String,
    pub lgdualup: String,
    pub switch_monitor: bool,
    pub switch_pbp: bool,
    pub pbp_mode: String,
    pub follow_hhkb_usb: bool,
    pub hosts: BTreeMap<String, HostSpec>,
    pub config_path: String,
    pub adapters_user: Value,
    pub mouse_enabled: bool,
    pub mouse_backend: Option<String>,
    pub mouse_path: Option<String>,
    pub keyboard_enabled: bool,
    pub keyboard_backend: Option<String>,
    pub keyboard_path: Option<String>,
    pub dualup_enabled: bool,
    pub dualup_layout: bool,
    pub display_backend: Option<String>,
    pub display_path: Option<String>,
    pub dualup_layout_helper: Option<String>,
    pub dualup_display_id: Option<String>,
    pub dualup_peer: Option<String>,
    pub dualup_layout_retries: i64,
    pub dualup_layout_retry_delay_s: f64,
    pub dualup_layout_settle_s: f64,
    pub peer: Option<String>,
    pub tray_density: Option<String>,
    /// Raw user object (minus adapters overlay bookkeeping).
    pub user: Value,
}

impl Config {
    pub fn tray_density(&self) -> &'static str {
        match self
            .tray_density
            .as_deref()
            .unwrap_or("strip")
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "chips" => "chips",
            _ => "strip",
        }
    }
}

pub fn load_config() -> Result<Config> {
    load_config_from(None)
}

pub fn load_config_from(candidates: Option<&[PathBuf]>) -> Result<Config> {
    let this_host = default_this_host().to_string();
    let mut cfg = default_config(&this_host);
    let paths = match candidates {
        Some(list) => list.to_vec(),
        None => config_candidates(),
    };
    let mut user = Value::Object(serde_json::Map::new());
    let mut found = false;
    for path in &paths {
        if path.is_file() {
            let text = fs::read_to_string(path)
                .map_err(|e| Error::exit(format!("cannot read config {}: {e}", path.display())))?;
            user = serde_json::from_str(&text)
                .map_err(|e| Error::exit(format!("config is not JSON: {}: {e}", path.display())))?;
            if !user.is_object() {
                return Err(Error::exit(format!(
                    "config is not an object: {}",
                    path.display()
                )));
            }
            overlay_flat(&mut cfg, &user);
            cfg.config_path = path.display().to_string();
            found = true;
            break;
        }
    }
    if !found {
        cfg.config_path = "(defaults)".into();
    }
    apply_adapter_config(&mut cfg, &user);
    cfg.this_host = normalize_host(if cfg.this_host.is_empty() {
        default_this_host()
    } else {
        &cfg.this_host
    })?;
    if !user_set_follow_channel(&user) {
        cfg.target_channel = if cfg.this_host == "linux" { 1 } else { 2 };
    }
    cfg.target_channel = if cfg.target_channel == 0 {
        if cfg.this_host == "linux" {
            1
        } else {
            2
        }
    } else {
        cfg.target_channel
    };
    let mut supplied = BTreeMap::new();
    if let Some(Value::Object(hosts)) = user.get("hosts") {
        for (k, v) in hosts {
            supplied.insert(k.clone(), v.clone());
        }
    }
    for (k, v) in adapter_host_overrides(&user) {
        supplied.insert(k, v);
    }
    cfg.hosts = resolve_hosts(&cfg.this_host, Some(cfg.target_channel), &supplied);
    if user.get("adapters").map(|v| v.is_object()).unwrap_or(false) {
        cfg.adapters_user = user.get("adapters").cloned().unwrap_or(Value::Null);
    }
    cfg.user = user;
    Ok(cfg)
}

fn default_config(this_host: &str) -> Config {
    Config {
        target_channel: if this_host == "linux" { 1 } else { 2 },
        this_host: this_host.to_string(),
        hhkb_vendor_id: HHKB_VID_DEFAULT,
        hhkb_product_id: HHKB_PID_DEFAULT,
        poll_interval_s: 0.5,
        absent_polls_required: 4,
        sleep_gap_s: 3.0,
        switch_retries: 8,
        switch_retry_delay_s: 0.6,
        mxswitch: "mxswitch".into(),
        lgdualup: "lgdualup".into(),
        switch_monitor: true,
        switch_pbp: false,
        pbp_mode: "50-50".into(),
        follow_hhkb_usb: true,
        hosts: BTreeMap::new(),
        config_path: "(defaults)".into(),
        adapters_user: Value::Null,
        mouse_enabled: true,
        mouse_backend: None,
        mouse_path: None,
        keyboard_enabled: true,
        keyboard_backend: None,
        keyboard_path: None,
        dualup_enabled: true,
        dualup_layout: true,
        display_backend: None,
        display_path: None,
        dualup_layout_helper: None,
        dualup_display_id: None,
        dualup_peer: None,
        dualup_layout_retries: LAYOUT_DEFAULT_RETRIES,
        dualup_layout_retry_delay_s: LAYOUT_DEFAULT_DELAY_S,
        dualup_layout_settle_s: LAYOUT_DEFAULT_SETTLE_S,
        peer: None,
        tray_density: None,
        user: Value::Object(serde_json::Map::new()),
    }
}

fn overlay_flat(cfg: &mut Config, user: &Value) {
    let obj = match user {
        Value::Object(m) => m,
        _ => return,
    };
    if let Some(v) = obj.get("target_channel") {
        cfg.target_channel = as_int(v, cfg.target_channel);
    }
    if let Some(v) = obj.get("this_host").and_then(Value::as_str) {
        cfg.this_host = v.to_string();
    }
    if let Some(v) = obj.get("hhkb_vendor_id") {
        cfg.hhkb_vendor_id = as_int(v, HHKB_VID_DEFAULT);
    }
    if let Some(v) = obj.get("hhkb_product_id") {
        cfg.hhkb_product_id = as_int(v, HHKB_PID_DEFAULT);
    }
    if let Some(v) = obj.get("poll_interval_s") {
        cfg.poll_interval_s = as_float(v, cfg.poll_interval_s);
    }
    if let Some(v) = obj.get("absent_polls_required") {
        cfg.absent_polls_required = as_int(v, cfg.absent_polls_required);
    }
    if let Some(v) = obj.get("sleep_gap_s") {
        cfg.sleep_gap_s = as_float(v, cfg.sleep_gap_s);
    }
    if let Some(v) = obj.get("switch_retries") {
        cfg.switch_retries = as_int(v, cfg.switch_retries);
    }
    if let Some(v) = obj.get("switch_retry_delay_s") {
        cfg.switch_retry_delay_s = as_float(v, cfg.switch_retry_delay_s);
    }
    if let Some(v) = obj.get("mxswitch").and_then(Value::as_str) {
        cfg.mxswitch = v.to_string();
    }
    if let Some(v) = obj.get("lgdualup").and_then(Value::as_str) {
        cfg.lgdualup = v.to_string();
    }
    if let Some(v) = obj.get("switch_monitor") {
        cfg.switch_monitor = as_bool(v, cfg.switch_monitor);
    }
    if let Some(v) = obj.get("switch_pbp") {
        cfg.switch_pbp = as_bool(v, cfg.switch_pbp);
    }
    if let Some(v) = obj.get("pbp_mode").and_then(Value::as_str) {
        cfg.pbp_mode = v.to_string();
    }
    if let Some(v) = obj.get("follow_hhkb_usb") {
        cfg.follow_hhkb_usb = as_bool(v, cfg.follow_hhkb_usb);
    }
}

fn user_set_follow_channel(user: &Value) -> bool {
    if user.get("target_channel").is_some() {
        return true;
    }
    let adapters = as_dict_ref(user.get("adapters"));
    let hosts = as_dict_ref(adapters.get("hosts"));
    hosts.contains_key("follow_channel")
}

/// `adapters.display` overlays legacy `adapters.dualup` (same role).
pub fn display_adapter_cfg(container: &Value) -> serde_json::Map<String, Value> {
    let adapters = as_dict_ref(container.get("adapters"));
    let mut out = as_dict_ref(adapters.get("dualup"));
    for (k, v) in as_dict_ref(adapters.get("display")) {
        out.insert(k, v);
    }
    out
}

pub fn apply_adapter_config(cfg: &mut Config, user: &Value) {
    let adapters = as_dict_ref(user.get("adapters"));
    let mouse = as_dict_ref(adapters.get("mouse"));
    let hosts_ad = as_dict_ref(adapters.get("hosts"));
    let keyboard = as_dict_ref(adapters.get("keyboard"));
    let dual = display_adapter_cfg(user);
    let ui = as_dict_ref(user.get("ui"));
    let tray = as_dict_ref(ui.get("tray"));

    cfg.mouse_enabled = as_bool(mouse.get("enabled").unwrap_or(&Value::Bool(true)), true);
    if let Some(backend) = opt_str(mouse.get("backend")) {
        cfg.mouse_backend = Some(backend);
    }
    if let Some(path) = opt_str(mouse.get("path")) {
        cfg.mxswitch = path.clone();
        cfg.mouse_path = Some(path);
    }

    if let Some(this_host) = opt_str(hosts_ad.get("this_host")) {
        cfg.this_host = this_host;
    }
    if hosts_ad.get("follow_channel").is_some() {
        cfg.target_channel = as_int(hosts_ad.get("follow_channel").unwrap(), cfg.target_channel);
    }
    if let Some(peer) = opt_str(hosts_ad.get("peer")) {
        cfg.peer = Some(peer);
    }
    let follow_usb = user
        .get("follow_hhkb_usb")
        .or_else(|| hosts_ad.get("follow_hhkb_usb"));
    cfg.follow_hhkb_usb = match follow_usb {
        None => true,
        Some(v) => as_bool(v, true),
    };

    cfg.keyboard_enabled = as_bool(keyboard.get("enabled").unwrap_or(&Value::Bool(true)), true);
    if let Some(backend) = opt_str(keyboard.get("backend")) {
        cfg.keyboard_backend = Some(backend);
    }
    if let Some(path) = opt_str(keyboard.get("path")) {
        cfg.keyboard_path = Some(path);
    }

    cfg.dualup_enabled = as_bool(dual.get("enabled").unwrap_or(&Value::Bool(true)), true);
    cfg.dualup_layout = as_bool(dual.get("layout").unwrap_or(&Value::Bool(true)), true);
    if dual.get("enabled") == Some(&Value::Bool(false)) {
        cfg.switch_monitor = false;
    }
    if let Some(backend) = opt_str(dual.get("backend")) {
        cfg.display_backend = Some(backend);
    }
    if let Some(path) = opt_str(dual.get("path")) {
        cfg.lgdualup = path.clone();
        cfg.display_path = Some(path);
    }
    if let Some(mode) = opt_str(dual.get("pbp_mode")) {
        cfg.pbp_mode = mode;
    }
    if let Some(v) = dual.get("switch_pbp") {
        cfg.switch_pbp = as_bool(v, cfg.switch_pbp);
    }
    if let Some(helper) = opt_str(dual.get("layout_helper")) {
        cfg.dualup_layout_helper = Some(helper);
    }
    if let Some(id) = opt_str(dual.get("display_id")) {
        cfg.dualup_display_id = Some(id);
    }
    if let Some(peer) = opt_str(dual.get("peer")) {
        cfg.dualup_peer = Some(peer);
    }
    cfg.dualup_layout_retries = match dual.get("layout_retries") {
        None => LAYOUT_DEFAULT_RETRIES,
        Some(v) => as_int(v, LAYOUT_DEFAULT_RETRIES),
    };
    cfg.dualup_layout_retry_delay_s = match dual.get("layout_retry_delay_s") {
        None => LAYOUT_DEFAULT_DELAY_S,
        Some(v) => as_float(v, LAYOUT_DEFAULT_DELAY_S),
    };
    cfg.dualup_layout_settle_s = match dual.get("layout_settle_s") {
        None => LAYOUT_DEFAULT_SETTLE_S,
        Some(v) => as_float(v, LAYOUT_DEFAULT_SETTLE_S),
    };
    if let Some(density) = opt_str(tray.get("density")) {
        cfg.tray_density = Some(density);
    }
}

pub fn adapter_host_overrides(user: &Value) -> BTreeMap<String, Value> {
    let adapters = as_dict_ref(user.get("adapters"));
    let hosts_ad = as_dict_ref(adapters.get("hosts"));
    let dual = display_adapter_cfg(user);
    let mut out: BTreeMap<String, Value> = BTreeMap::new();
    for (name, raw) in hosts_ad {
        if name == "this_host" || name == "follow_channel" {
            continue;
        }
        out.insert(name, raw);
    }
    for (name, inp) in as_dict(dual.get("inputs").unwrap_or(&Value::Null)) {
        let mut entry = match out.get(&name) {
            Some(existing) => HostSpec::from_value(existing),
            None => HostSpec::default(),
        };
        entry.dualup_input = Some(match inp {
            Value::String(s) => s,
            other => other.to_string(),
        });
        out.insert(name, entry.to_value());
    }
    out
}

/// Display-role config from the loaded file (adapters.display over dualup).
pub fn display_cfg_from_loaded(cfg: &Config) -> serde_json::Map<String, Value> {
    display_adapter_cfg(&cfg.user)
}
