use crate::config::Config;
use crate::display::{detect_display_mode, display_set_mode, switch_monitor};
use crate::error::{Error, Result};
use crate::hosts::{host_channel, normalize_host};
use crate::mouse::switch_mouse;
use crate::process::Runtime;
use crate::status::cmd_status;

pub fn cmd_to(cfg: &Config, rt: &Runtime, host: &str, mouse_only: bool) -> Result<i32> {
    let dest_host = normalize_host(host)?;
    let channel = host_channel(&cfg.hosts, &dest_host)?;
    rt.print(&format!("desk → {dest_host} (MX channel {channel})"));
    let rc = switch_mouse(cfg, rt, Some(channel));
    let mon_rc = switch_monitor(cfg, rt, &dest_host, mouse_only)?;
    Ok(if rc != 0 { rc } else { mon_rc })
}

pub fn cmd_switch(cfg: &Config, rt: &Runtime, target: Option<&str>) -> Result<i32> {
    let Some(target) = target else {
        return Ok(switch_mouse(cfg, rt, None));
    };
    if target.chars().all(|c| c.is_ascii_digit()) {
        let channel: i64 = target.parse().unwrap_or(0);
        if !matches!(channel, 1 | 2 | 3) {
            return Err(Error::exit("channel must be 1, 2 or 3"));
        }
        return Ok(switch_mouse(cfg, rt, Some(channel)));
    }
    cmd_to(cfg, rt, target, false)
}

pub fn cmd_pbp(cfg: &Config, rt: &Runtime, mode: Option<&str>) -> Result<i32> {
    let chosen = mode
        .map(|s| s.to_string())
        .unwrap_or_else(|| cfg.pbp_mode.clone())
        .trim()
        .to_string();
    if chosen.is_empty() {
        rt.print("usage: desk-switch pbp <mode>   (mode is whatever lgdualup pbp accepts)");
        return Ok(2);
    }
    Ok(display_set_mode(
        cfg,
        rt,
        &chosen,
        "lgdualup not on PATH — DualUp PBP needs `make install`",
    ))
}

pub fn cmd_full(cfg: &Config, rt: &Runtime) -> Result<i32> {
    Ok(display_set_mode(
        cfg,
        rt,
        "full",
        "lgdualup not on PATH — DualUp full needs `make install`",
    ))
}

pub fn cmd_layout(cfg: &Config, rt: &Runtime) -> Result<i32> {
    let mode = detect_display_mode(Some(cfg));
    if mode == "full" {
        return cmd_full(cfg, rt);
    }
    cmd_pbp(cfg, rt, None)
}

#[derive(Clone, Debug)]
pub enum Command {
    Status { json: bool, hint: bool, local: bool },
    Hint,
    Watch { dry_run: bool },
    Switch { target: Option<String> },
    To { host: String, mouse_only: bool },
    Pbp { mode: Option<String> },
    Full,
    Layout,
}

pub fn dispatch(cfg: &Config, rt: &Runtime, cmd: Command) -> Result<i32> {
    match cmd {
        Command::Status { json, hint, local } => Ok(cmd_status(cfg, rt, json, hint, local)),
        Command::Hint => Ok(cmd_status(cfg, rt, false, true, true)),
        Command::Watch { dry_run } => {
            let to_cfg = cfg.clone();
            let to_rt = Runtime {
                runner: rt.runner.clone(),
                sleeper: rt.sleeper.clone(),
                clock: rt.clock.clone(),
                io: rt.io.clone(),
            };
            crate::watch::cmd_watch(
                cfg,
                rt,
                dry_run,
                Box::new(move |host: &str| cmd_to(&to_cfg, &to_rt, host, false)),
            )
        }
        Command::Switch { target } => cmd_switch(cfg, rt, target.as_deref()),
        Command::To { host, mouse_only } => cmd_to(cfg, rt, &host, mouse_only),
        Command::Pbp { mode } => cmd_pbp(cfg, rt, mode.as_deref()),
        Command::Full => cmd_full(cfg, rt),
        Command::Layout => cmd_layout(cfg, rt),
    }
}
