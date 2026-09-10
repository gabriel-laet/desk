use std::path::Path;

use serde_json::Value;

use crate::adapters::bind_role;
use crate::config::Config;
use crate::paths::hhkb_source_path;
use crate::process::{adapter_argv, Runtime};

#[derive(Clone, Debug)]
pub struct Presence {
    pub present: bool,
    pub usb: bool,
    pub bluetooth: bool,
    pub transport: String,
    pub unknown: bool,
    pub names: Vec<String>,
}

impl Presence {
    pub fn empty(unknown: bool) -> Self {
        Self {
            present: unknown,
            usb: false,
            bluetooth: false,
            transport: if unknown { "unknown" } else { "absent" }.into(),
            unknown,
            names: Vec::new(),
        }
    }

    pub fn from_json(data: &Value) -> Option<Self> {
        let obj = data.as_object()?;
        if !obj.contains_key("present") {
            return None;
        }
        Some(Self {
            present: obj
                .get("present")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            usb: obj.get("usb").and_then(|v| v.as_bool()).unwrap_or(false),
            bluetooth: obj
                .get("bluetooth")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            transport: obj
                .get("transport")
                .and_then(|v| v.as_str())
                .unwrap_or("absent")
                .to_string(),
            unknown: obj
                .get("unknown")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            names: obj
                .get("names")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default(),
        })
    }
}

fn probe_exec(rt: &Runtime, path: &Path, vid: i64, pid: i64) -> Option<Presence> {
    let vid_s = format!("0x{vid:X}");
    let pid_s = format!("0x{pid:X}");
    let argv = adapter_argv(path, &["info", "--vid", &vid_s, "--pid", &pid_s]);
    let proc = rt.run(&argv, 4.0).ok()?;
    if proc.status != 0 {
        return None;
    }
    let data: Value = serde_json::from_str(proc.stdout.trim()).ok()?;
    Presence::from_json(&data)
}

/// keyboard.presence: bound adapter binary (or source-tree hhkb.py). No VID/PID
/// matching in core — that stays in `adapters/hhkb`.
pub fn keyboard_probe(cfg: &Config, rt: &Runtime) -> Presence {
    if !cfg.keyboard_enabled {
        return Presence::empty(false);
    }
    let vid = cfg.hhkb_vendor_id;
    let pid = cfg.hhkb_product_id;
    let bound = bind_role(cfg, "keyboard");
    if let Some(path) = bound.path.as_ref() {
        if let Some(probe) = probe_exec(rt, path, vid, pid) {
            return probe;
        }
    }
    if let Some(path) = hhkb_source_path() {
        if let Some(probe) = probe_exec(rt, &path, vid, pid) {
            return probe;
        }
    }
    // Probe errors count as present so a flaky helper cannot steal the mouse.
    Presence::empty(true)
}

pub fn keyboard_present(cfg: &Config, rt: &Runtime) -> bool {
    keyboard_probe(cfg, rt).present
}
