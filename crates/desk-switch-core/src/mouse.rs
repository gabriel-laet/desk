use std::path::PathBuf;

use regex::Regex;

use crate::adapters::{bind_role, Binding};
use crate::cache::{load_mouse_cache, save_mouse_cache};
use crate::config::Config;
use crate::error::{Error, Result};
use crate::hosts::host_for_channel;
use crate::process::{adapter_argv, Runtime};

pub fn mouse_binding(cfg: &Config) -> Binding {
    bind_role(cfg, "mouse")
}

pub fn mxswitch_path(cfg: &Config) -> Result<PathBuf> {
    let bound = bind_role(cfg, "mouse");
    match bound.path {
        Some(path) => Ok(path),
        None => Err(Error::exit(format!(
            "mouse adapter not found: {}",
            bound
                .id
                .or(cfg.mouse_backend.clone())
                .unwrap_or_else(|| cfg.mxswitch.clone())
        ))),
    }
}

pub fn switch_mouse(cfg: &Config, rt: &Runtime, channel: Option<i64>) -> i32 {
    if !cfg.mouse_enabled {
        rt.print("mouse adapter disabled");
        return 0;
    }
    let dest = channel.unwrap_or(cfg.target_channel);
    let path = match mxswitch_path(cfg) {
        Ok(p) => p,
        Err(err) => {
            rt.log(&format!("mouse adapter failed to start: {err}"));
            return 1;
        }
    };
    let argv = adapter_argv(&path, &[&dest.to_string()]);
    let proc = match rt.run(&argv, 8.0) {
        Ok(p) => p,
        Err(exc) => {
            rt.log(&format!("mouse adapter failed to start: {exc}"));
            return 1;
        }
    };
    let out = proc.stdout.trim();
    let err = proc.stderr.trim();
    if !out.is_empty() {
        rt.log(out);
    }
    if !err.is_empty() {
        rt.log(err);
    }
    if proc.status == 0 {
        save_mouse_cache(dest, Some(rt.now()));
    }
    proc.status
}

pub fn parse_mouse_channel(info: &str) -> Option<i64> {
    let re = Regex::new(r"(?i)currently on\s+(\d+)").ok()?;
    re.captures(info)
        .and_then(|c| c.get(1))
        .and_then(|m| m.as_str().parse().ok())
}

pub fn mouse_info(cfg: &Config, rt: &Runtime) -> (String, Option<i64>) {
    let path = match mxswitch_path(cfg) {
        Ok(p) => p,
        Err(exc) => return (exc.to_string(), None),
    };
    let argv = adapter_argv(&path, &["--info"]);
    match rt.run(&argv, 8.0) {
        Ok(proc) => {
            let text = if !proc.stdout.is_empty() {
                proc.stdout.trim_end().to_string()
            } else {
                proc.stderr.trim_end().to_string()
            };
            (text.clone(), parse_mouse_channel(&text))
        }
        Err(exc) => (exc.to_string(), None),
    }
}

#[derive(Clone, Debug)]
pub struct MouseSnapshot {
    pub info: String,
    pub channel: Option<i64>,
    pub channel_live: Option<i64>,
    pub online: bool,
    pub stale: bool,
    pub source: Option<String>,
    pub host: Option<String>,
    pub cached_ts: Option<f64>,
}

pub fn mouse_snapshot(cfg: &Config, rt: &Runtime) -> MouseSnapshot {
    let (info, live) = mouse_info(cfg, rt);
    let (cached, cached_ts) = load_mouse_cache(Some(rt.now()));
    let (channel, source, online, stale) = if live.is_some() {
        if let Some(live_ch) = live {
            save_mouse_cache(live_ch, Some(rt.now()));
        }
        (live, Some("mouse".into()), true, false)
    } else if cached.is_some() {
        (cached, Some("mouse_cached".into()), false, true)
    } else {
        (None, None, false, false)
    };
    MouseSnapshot {
        info,
        channel,
        channel_live: live,
        online,
        stale,
        source,
        host: host_for_channel(&cfg.hosts, channel),
        cached_ts,
    }
}
