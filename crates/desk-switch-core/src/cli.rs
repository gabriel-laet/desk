use crate::commands::Command;
use crate::error::{Error, Result};
use crate::VERSION;

const USAGE: &str = "\
Desk switch: one CLI for hopping a desk between machines.

    desk-switch status
    desk-switch status --json
    desk-switch to mac
    desk-switch to linux
    desk-switch switch 2
    desk-switch pbp <mode>
    desk-switch full
    desk-switch watch
    desk-switch watch --dry-run

`hhkb-mx-follow` is the legacy command name for the same program.
";

pub fn parse_args(argv: &[String]) -> Result<Command> {
    if argv.iter().any(|a| a == "--version" || a == "-V") {
        return Err(Error::Exit {
            message: format!("desk-switch {VERSION}"),
            code: 0,
        });
    }
    if argv.is_empty() || argv.iter().any(|a| a == "--help" || a == "-h") {
        let mut msg = USAGE.to_string();
        msg.push_str("\nCommands: status, hint, watch, switch, to, pbp, full, layout\n");
        if argv.is_empty() {
            return Err(Error::usage(msg));
        }
        return Err(Error::Exit {
            message: msg,
            code: 0,
        });
    }
    let cmd = argv[0].as_str();
    let rest = &argv[1..];
    match cmd {
        "status" => {
            let mut json = false;
            let mut hint = false;
            let mut local = false;
            for a in rest {
                match a.as_str() {
                    "--json" => json = true,
                    "--hint" => hint = true,
                    "--local" => local = true,
                    "--help" | "-h" => {
                        return Err(Error::Exit {
                            message: "status [--json] [--hint] [--local]".into(),
                            code: 0,
                        })
                    }
                    other => {
                        return Err(Error::usage(format!("unrecognized status flag: {other}")))
                    }
                }
            }
            Ok(Command::Status { json, hint, local })
        }
        "hint" => Ok(Command::Hint),
        "watch" => {
            let dry_run = rest.iter().any(|a| a == "--dry-run");
            Ok(Command::Watch { dry_run })
        }
        "switch" => {
            let target = rest.iter().find(|a| !a.starts_with('-')).cloned();
            Ok(Command::Switch { target })
        }
        "to" => {
            let mouse_only = rest.iter().any(|a| a == "--mouse-only");
            let host = rest
                .iter()
                .find(|a| !a.starts_with('-'))
                .cloned()
                .ok_or_else(|| Error::usage("usage: desk-switch to <host> [--mouse-only]"))?;
            Ok(Command::To { host, mouse_only })
        }
        "pbp" => {
            let mode = rest.iter().find(|a| !a.starts_with('-')).cloned();
            Ok(Command::Pbp { mode })
        }
        "full" => Ok(Command::Full),
        "layout" => Ok(Command::Layout),
        other => Err(Error::usage(format!("unknown command: {other}\n\n{USAGE}"))),
    }
}
