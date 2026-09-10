//! desk-switch-core: config, status, watch, and adapter orchestration.
//!
//! Hardware stays in out-of-process adapters (`adapters/<id>/`). Core never
//! probes Logitech / LG / HHKB VID/PID itself.

pub mod adapters;
pub mod cache;
pub mod cli;
pub mod commands;
pub mod config;
pub mod display;
pub mod error;
pub mod hosts;
pub mod json_util;
pub mod keyboard;
pub mod mouse;
pub mod paths;
pub mod process;
pub mod status;
pub mod watch;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub use commands::{dispatch, Command};
pub use config::{load_config, load_config_from, Config};
pub use error::{Error, Result};
pub use process::Runtime;

use crate::cli::parse_args;

/// Entry used by the `desk-switch` binary (and `hhkb-mx-follow` alias).
pub fn run_main(args: &[String]) -> i32 {
    match parse_args(args) {
        Err(Error::Exit { message, code }) => {
            if code == 0 {
                println!("{message}");
            } else {
                eprint_usage(&message);
            }
            code
        }
        Err(err) => {
            eprint_usage(&err.to_string());
            err.code()
        }
        Ok(cmd) => {
            let rt = Runtime::default();
            let cfg = match load_config() {
                Ok(c) => c,
                Err(err) => {
                    eprintln!("{err}");
                    return err.code();
                }
            };
            match dispatch(&cfg, &rt, cmd) {
                Ok(rc) => rc,
                Err(Error::Interrupted) => 130,
                Err(err) => {
                    if !err.to_string().is_empty() {
                        eprintln!("{err}");
                    }
                    err.code()
                }
            }
        }
    }
}

fn eprint_usage(message: &str) {
    // `--help` / empty argv: argparse prints usage on stdout for -h, stderr for missing cmd.
    // Match Python: --help is stdout (code 0, handled above). Errors go to stderr.
    eprint!("{message}");
    if !message.ends_with('\n') {
        eprintln!();
    }
}

#[cfg(test)]
mod tests_port;
