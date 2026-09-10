use std::io::{Read, Write};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use wait_timeout::ChildExt;

use crate::error::{Error, Result};

#[derive(Clone, Debug)]
pub struct CommandOutput {
    pub status: i32,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Debug, thiserror::Error)]
pub enum RunError {
    #[error("{0}")]
    Start(String),
    #[error("timed out")]
    Timeout,
}

impl RunError {
    pub fn start(err: impl std::fmt::Display) -> Self {
        Self::Start(err.to_string())
    }
}

pub trait Runner: Send + Sync {
    fn run(
        &self,
        argv: &[String],
        timeout: Duration,
    ) -> std::result::Result<CommandOutput, RunError>;
}

pub trait Sleeper: Send + Sync {
    fn sleep(&self, seconds: f64);
}

pub trait Clock: Send + Sync {
    fn now(&self) -> f64;
}

pub trait Io: Send + Sync {
    fn stdout(&self, text: &str);
    fn stderr(&self, text: &str);
}

#[derive(Default)]
pub struct StdRunner;

impl Runner for StdRunner {
    fn run(
        &self,
        argv: &[String],
        timeout: Duration,
    ) -> std::result::Result<CommandOutput, RunError> {
        if argv.is_empty() {
            return Err(RunError::start("empty command"));
        }
        let mut cmd = Command::new(&argv[0]);
        cmd.args(&argv[1..])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = cmd.spawn().map_err(RunError::start)?;
        match child.wait_timeout(timeout).map_err(RunError::start)? {
            Some(status) => {
                let mut stdout = String::new();
                let mut stderr = String::new();
                if let Some(mut pipe) = child.stdout.take() {
                    let _ = pipe.read_to_string(&mut stdout);
                }
                if let Some(mut pipe) = child.stderr.take() {
                    let _ = pipe.read_to_string(&mut stderr);
                }
                Ok(CommandOutput {
                    status: status.code().unwrap_or(1),
                    stdout,
                    stderr,
                })
            }
            None => {
                let _ = child.kill();
                let _ = child.wait();
                Err(RunError::Timeout)
            }
        }
    }
}

#[derive(Default)]
pub struct StdSleeper;

impl Sleeper for StdSleeper {
    fn sleep(&self, seconds: f64) {
        if seconds > 0.0 {
            std::thread::sleep(Duration::from_secs_f64(seconds));
        }
    }
}

#[derive(Default)]
pub struct StdClock;

impl Clock for StdClock {
    fn now(&self) -> f64 {
        unix_now()
    }
}

pub fn unix_now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub struct StdIo;

impl Io for StdIo {
    fn stdout(&self, text: &str) {
        let mut out = std::io::stdout();
        let _ = writeln!(out, "{text}");
        let _ = out.flush();
    }

    fn stderr(&self, text: &str) {
        let mut err = std::io::stderr();
        let _ = writeln!(err, "{text}");
        let _ = err.flush();
    }
}

#[derive(Default)]
pub struct CaptureIo {
    pub out: Mutex<String>,
    pub err: Mutex<String>,
}

impl Io for CaptureIo {
    fn stdout(&self, text: &str) {
        let mut out = self.out.lock().expect("stdout lock");
        out.push_str(text);
        out.push('\n');
    }

    fn stderr(&self, text: &str) {
        let mut err = self.err.lock().expect("stderr lock");
        err.push_str(text);
        err.push('\n');
    }
}

impl CaptureIo {
    pub fn stdout_text(&self) -> String {
        self.out.lock().expect("stdout lock").clone()
    }

    pub fn stderr_text(&self) -> String {
        self.err.lock().expect("stderr lock").clone()
    }
}

pub struct Runtime {
    pub runner: Arc<dyn Runner>,
    pub sleeper: Arc<dyn Sleeper>,
    pub clock: Arc<dyn Clock>,
    pub io: Arc<dyn Io>,
}

impl Default for Runtime {
    fn default() -> Self {
        Self {
            runner: Arc::new(StdRunner),
            sleeper: Arc::new(StdSleeper),
            clock: Arc::new(StdClock),
            io: Arc::new(StdIo),
        }
    }
}

impl Runtime {
    pub fn capturing() -> (Self, Arc<CaptureIo>) {
        let io = Arc::new(CaptureIo::default());
        let rt = Self {
            runner: Arc::new(StdRunner),
            sleeper: Arc::new(StdSleeper),
            clock: Arc::new(StdClock),
            io: io.clone(),
        };
        (rt, io)
    }

    pub fn print(&self, text: &str) {
        self.io.stdout(text);
    }

    pub fn eprint(&self, text: &str) {
        self.io.stderr(text);
    }

    pub fn log(&self, msg: &str) {
        let ts = chrono_like_stamp();
        self.print(&format!("{ts} {msg}"));
    }

    pub fn run(
        &self,
        argv: &[impl AsRef<str>],
        timeout_s: f64,
    ) -> std::result::Result<CommandOutput, RunError> {
        let argv: Vec<String> = argv.iter().map(|s| s.as_ref().to_string()).collect();
        self.runner.run(&argv, Duration::from_secs_f64(timeout_s))
    }

    pub fn sleep(&self, seconds: f64) {
        self.sleeper.sleep(seconds);
    }

    pub fn now(&self) -> f64 {
        self.clock.now()
    }

    pub fn print_helper(&self, proc: &CommandOutput) {
        let out = proc.stdout.trim_end();
        let err = proc.stderr.trim_end();
        if !out.is_empty() {
            self.print(out);
        }
        if !err.is_empty() {
            if proc.status != 0 {
                self.eprint(err);
            } else {
                self.print(err);
            }
        }
    }
}

fn chrono_like_stamp() -> String {
    let now = std::time::SystemTime::now();
    let secs = now
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Local offset is not worth a chrono dep; UTC stamp is fine for watch logs.
    let days = secs / 86400;
    let rem = secs % 86400;
    let (y, m, d) = civil_from_days(days as i64);
    let hh = rem / 3600;
    let mm = (rem % 3600) / 60;
    let ss = rem % 60;
    format!("{y:04}-{m:02}-{d:02} {hh:02}:{mm:02}:{ss:02}")
}

fn civil_from_days(z: i64) -> (i32, u32, u32) {
    // Howard Hinnant civil_from_days (UTC).
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as i32, m as u32, d as u32)
}

/// Invoke an adapter binary. `.py` files (and shebang scripts without +x in odd
/// installs) go through `python3` so source-tree HHKB still works.
pub fn adapter_argv(path: &Path, args: &[&str]) -> Vec<String> {
    let mut argv = Vec::new();
    let ext = path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .eq_ignore_ascii_case("py");
    if ext {
        argv.push(python3_bin());
    }
    argv.push(path.to_string_lossy().into_owned());
    argv.extend(args.iter().map(|s| (*s).to_string()));
    argv
}

fn python3_bin() -> String {
    if crate::paths::which_cmd("python3").is_some() {
        "python3".into()
    } else {
        "python".into()
    }
}

pub fn run_or_start_error(
    rt: &Runtime,
    argv: &[String],
    timeout_s: f64,
    start_prefix: &str,
) -> Result<CommandOutput> {
    match rt.runner.run(argv, Duration::from_secs_f64(timeout_s)) {
        Ok(out) => Ok(out),
        Err(RunError::Timeout) => Err(Error::exit(format!("{start_prefix}: timed out"))),
        Err(RunError::Start(msg)) => Err(Error::exit(format!("{start_prefix}: {msg}"))),
    }
}
