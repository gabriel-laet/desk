use crate::config::Config;
use crate::error::{Error, Result};
use crate::keyboard::{keyboard_probe, Presence};
use crate::mouse::switch_mouse;
use crate::process::Runtime;

/// Detect Fn+Ctrl+0: USB appearance. Startup snapshot does not fire.
pub fn watch_usb_rising_edge(
    last_usb: Option<bool>,
    usb_now: bool,
    follow_usb: bool,
) -> (bool, bool) {
    match last_usb {
        None => (usb_now, false),
        Some(last) => {
            let fire = follow_usb && usb_now && !last;
            (usb_now, fire)
        }
    }
}

pub type ProbeFn = Box<dyn FnMut() -> Result<Presence>>;
pub type ToFn = Box<dyn FnMut(&str) -> Result<i32>>;

pub fn cmd_watch(cfg: &Config, rt: &Runtime, dry_run: bool, to_fn: ToFn) -> Result<i32> {
    cmd_watch_with(
        cfg,
        rt,
        dry_run,
        Box::new({
            let cfg = cfg.clone();
            let rt = Runtime {
                runner: rt.runner.clone(),
                sleeper: rt.sleeper.clone(),
                clock: rt.clock.clone(),
                io: rt.io.clone(),
            };
            move || Ok(keyboard_probe(&cfg, &rt))
        }),
        to_fn,
        None,
    )
}

pub fn cmd_watch_with(
    cfg: &Config,
    rt: &Runtime,
    dry_run: bool,
    mut probe_fn: ProbeFn,
    mut to_fn: ToFn,
    max_iters: Option<u32>,
) -> Result<i32> {
    let interval = cfg.poll_interval_s;
    let need = cfg.absent_polls_required;
    let sleep_gap = cfg.sleep_gap_s;
    let retries = cfg.switch_retries;
    let retry_delay = cfg.switch_retry_delay_s;
    let dest = cfg.target_channel;
    let follow_usb = cfg.follow_hhkb_usb;

    rt.log(&format!(
        "watching HHKB → MX channel {dest} (dry_run={dry_run}, every {interval}s, absent×{need}, follow_hhkb_usb={follow_usb})"
    ));
    let mut armed = false;
    let mut absent = 0i64;
    let mut last = rt.now();
    let mut last_usb: Option<bool> = None;
    let mut iter = 0u32;

    loop {
        if let Some(max) = max_iters {
            if iter >= max {
                return Ok(0);
            }
            iter += 1;
        }
        let now = rt.now();
        if now - last > sleep_gap.max(interval * 4.0) {
            rt.log("clock gap (sleep/wake) — disarm until HHKB is seen again");
            armed = false;
            absent = 0;
            last_usb = None;
        }
        last = now;

        let probe = match probe_fn() {
            Ok(p) => p,
            Err(Error::Interrupted) => return Err(Error::Interrupted),
            Err(exc) => {
                rt.log(&format!(
                    "presence probe error ({exc}); treating as present"
                ));
                Presence::empty(true)
            }
        };

        let present = probe.present;
        let usb_now = probe.usb;
        let (next_usb, follow_here) = watch_usb_rising_edge(last_usb, usb_now, follow_usb);
        last_usb = Some(next_usb);
        if follow_here {
            let here = cfg.this_host.as_str();
            if dry_run {
                rt.log(&format!(
                    "dry-run: HHKB USB appeared — would desk-switch to {here}"
                ));
            } else {
                rt.log(&format!("HHKB USB appeared — desk → {here}"));
                let rc = to_fn(here)?;
                if rc != 0 {
                    rt.log(&format!("USB follow to {here} failed (rc={rc})"));
                }
            }
            armed = true;
            absent = 0;
        }

        if usb_now {
            if !armed {
                rt.log("HHKB USB present — armed");
            }
            armed = true;
            absent = 0;
        } else if present {
            if !armed {
                rt.log("HHKB present — armed");
            }
            armed = true;
            absent = 0;
        } else if armed {
            absent += 1;
            if absent == 1 || absent == need {
                rt.log(&format!("HHKB absent ({absent}/{need})"));
            }
            if absent >= need {
                if dry_run {
                    rt.log(&format!("dry-run: would switch mouse to channel {dest}"));
                } else {
                    rt.log(&format!("HHKB left — switching mouse to channel {dest}"));
                    let mut rc = 1;
                    for attempt in 1..=retries {
                        rc = switch_mouse(cfg, rt, Some(dest));
                        if rc == 0 {
                            rt.log(&format!("switch sent (attempt {attempt})"));
                            break;
                        }
                        rt.log(&format!("switch attempt {attempt} failed (rc={rc})"));
                        rt.sleep(retry_delay);
                    }
                    if rc != 0 {
                        rt.log("giving up this departure; will re-arm when HHKB returns");
                    }
                }
                armed = false;
                absent = 0;
            }
        }
        rt.sleep(interval);
    }
}
