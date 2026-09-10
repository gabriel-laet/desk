use std::env;
use std::path::{Path, PathBuf};

use crate::VERSION;

/// Product version printed by `--version` and `status --json`.
pub fn version() -> &'static str {
    VERSION
}

pub fn system() -> &'static str {
    if cfg!(target_os = "macos") {
        "Darwin"
    } else if cfg!(target_os = "linux") {
        "Linux"
    } else {
        std::env::consts::OS
    }
}

pub fn default_this_host() -> &'static str {
    if system() == "Darwin" {
        "mac"
    } else {
        "linux"
    }
}

pub fn home_dir() -> PathBuf {
    if let Ok(home) = env::var("HOME") {
        if !home.is_empty() {
            return PathBuf::from(home);
        }
    }
    PathBuf::from("/")
}

pub fn expand_user(path: &str) -> PathBuf {
    let home = home_dir();
    if path == "~" {
        return home;
    }
    if let Some(rest) = path.strip_prefix("~/") {
        return home.join(rest);
    }
    PathBuf::from(path)
}

pub fn cache_dir() -> PathBuf {
    if let Ok(xdg) = env::var("XDG_CACHE_HOME") {
        if !xdg.is_empty() {
            return PathBuf::from(xdg).join("desk-switch");
        }
    }
    home_dir().join(".cache").join("desk-switch")
}

pub fn libexec_dir() -> PathBuf {
    if let Ok(env_lib) = env::var("DESK_SWITCH_LIB") {
        if !env_lib.is_empty() {
            return expand_user(&env_lib);
        }
    }
    home_dir().join(".local").join("lib").join("desk-switch")
}

/// Repo root when developing or running tests (`adapters/` lives here).
pub fn source_root() -> Option<PathBuf> {
    if let Ok(src) = env::var("DESK_SWITCH_SRC") {
        let path = PathBuf::from(src);
        if path.join("adapters").is_dir() {
            return Some(path);
        }
    }
    let compiled = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if let Some(ws) = compiled.parent().and_then(|p| p.parent()) {
        if ws.join("adapters").join("hhkb").join("hhkb.py").is_file() {
            return Some(ws.to_path_buf());
        }
    }
    if let Ok(mut cur) = env::current_dir() {
        for _ in 0..10 {
            if cur.join("adapters").join("hhkb").join("hhkb.py").is_file() {
                return Some(cur);
            }
            if !cur.pop() {
                break;
            }
        }
    }
    None
}

pub fn config_candidates() -> Vec<PathBuf> {
    let mut out = vec![
        home_dir()
            .join(".config")
            .join("desk-switch")
            .join("config.json"),
        home_dir()
            .join(".config")
            .join("hhkb-mx-follow")
            .join("config.json"),
    ];
    if let Some(root) = source_root() {
        out.push(root.join("config.json"));
    }
    out
}

pub fn is_exe(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = path.metadata() {
            return meta.permissions().mode() & 0o111 != 0;
        }
        false
    }
    #[cfg(not(unix))]
    {
        true
    }
}

pub fn which_cmd(name: &str) -> Option<PathBuf> {
    let expanded = expand_user(name);
    if is_exe(&expanded) {
        return Some(expanded);
    }
    if let Some(found) = search_path(name) {
        return Some(found);
    }
    let local = home_dir()
        .join(".local")
        .join("bin")
        .join(Path::new(name).file_name().unwrap_or_else(|| name.as_ref()));
    if is_exe(&local) {
        return Some(local);
    }
    None
}

fn search_path(name: &str) -> Option<PathBuf> {
    let path_var = env::var("PATH").unwrap_or_default();
    for dir in env::split_paths(&path_var) {
        let candidate = dir.join(name);
        if is_exe(&candidate) {
            return Some(candidate);
        }
    }
    None
}

pub fn hhkb_source_path() -> Option<PathBuf> {
    if let Some(root) = source_root() {
        let bundled = root.join("adapters").join("hhkb").join("hhkb.py");
        if bundled.is_file() {
            return Some(bundled);
        }
    }
    let installed = libexec_dir().join("hhkb");
    if installed.is_file() {
        return Some(installed);
    }
    None
}

pub fn bundled_layout_helper() -> Option<PathBuf> {
    let root = source_root()?;
    let sub = if system() == "Darwin" {
        "macos"
    } else {
        "linux"
    };
    let bundled = root
        .join("adapters")
        .join("lgdualup")
        .join(sub)
        .join("dualup-layout");
    if is_exe(&bundled) {
        Some(bundled)
    } else {
        None
    }
}
