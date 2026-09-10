use std::fs;
use std::path::PathBuf;

use serde_json::Value;

use crate::config::{MOUSE_CACHE_TTL_S, PEER_CACHE_TTL_S};
use crate::paths::cache_dir;
use crate::process::Runtime;

pub fn mouse_cache_path() -> PathBuf {
    cache_dir().join("mouse-channel.json")
}

pub fn dualup_mode_cache_path() -> PathBuf {
    cache_dir().join("dualup-mode.json")
}

pub fn peer_cache_path() -> PathBuf {
    cache_dir().join("peer-status.json")
}

pub fn load_mouse_cache(now: Option<f64>) -> (Option<i64>, Option<f64>) {
    let text = match fs::read_to_string(mouse_cache_path()) {
        Ok(t) => t,
        Err(_) => return (None, None),
    };
    let raw: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return (None, None),
    };
    let Some(obj) = raw.as_object() else {
        return (None, None);
    };
    let channel = match obj.get("channel").and_then(|v| v.as_i64()) {
        Some(c) => c,
        None => return (None, None),
    };
    let ts = match obj.get("ts").and_then(|v| v.as_f64()) {
        Some(t) => t,
        None => return (None, None),
    };
    if !matches!(channel, 1 | 2 | 3) {
        return (None, None);
    }
    let now = now.unwrap_or_else(crate::process::unix_now);
    let age = now - ts;
    if age < 0.0 || age > MOUSE_CACHE_TTL_S {
        return (None, None);
    }
    (Some(channel), Some(ts))
}

pub fn save_mouse_cache(channel: i64, now: Option<f64>) {
    if !matches!(channel, 1 | 2 | 3) {
        return;
    }
    let path = mouse_cache_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let ts = now.unwrap_or_else(crate::process::unix_now);
    let body = format!("{{\"channel\":{channel},\"ts\":{ts}}}\n");
    let _ = fs::write(path, body);
}

pub fn load_dualup_mode_cache() -> Option<String> {
    let text = fs::read_to_string(dualup_mode_cache_path()).ok()?;
    let raw: Value = serde_json::from_str(&text).ok()?;
    let mode = raw.get("mode")?.as_str()?.trim().to_ascii_lowercase();
    if mode == "full" || mode == "pbp" {
        Some(mode)
    } else {
        None
    }
}

pub fn save_dualup_mode_cache(mode: &str) {
    let key = crate::display::layout_verb(mode);
    if key != "full" && key != "pbp" {
        return;
    }
    let path = dualup_mode_cache_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let ts = crate::process::unix_now();
    let body = format!("{{\"mode\":\"{key}\",\"ts\":{ts}}}\n");
    let _ = fs::write(path, body);
}

pub fn load_peer_cache(peer: &str) -> Option<Value> {
    let text = fs::read_to_string(peer_cache_path()).ok()?;
    let raw: Value = serde_json::from_str(&text).ok()?;
    let obj = raw.as_object()?;
    if obj.get("peer").and_then(|v| v.as_str()) != Some(peer) {
        return None;
    }
    let ts = obj.get("ts").and_then(|v| v.as_f64()).unwrap_or(0.0);
    if crate::process::unix_now() - ts > PEER_CACHE_TTL_S {
        return None;
    }
    obj.get("state").cloned().filter(|s| s.is_object())
}

pub fn save_peer_cache(peer: &str, state: &Value) {
    let path = peer_cache_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let payload = serde_json::json!({
        "peer": peer,
        "ts": crate::process::unix_now(),
        "state": state,
    });
    if let Ok(text) = serde_json::to_string(&payload) {
        let _ = fs::write(path, format!("{text}\n"));
    }
}

pub fn remember_mouse(rt: &Runtime, channel: i64) {
    let _ = rt;
    save_mouse_cache(channel, None);
}
