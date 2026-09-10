use std::env;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    let rc = desk_switch_core::run_main(&args);
    ExitCode::from(rc.clamp(0, 255) as u8)
}
