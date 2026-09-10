use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::config::{Config, ADAPTER_API_VERSION};
use crate::paths::{hhkb_source_path, is_exe, libexec_dir, which_cmd};

pub const ROLE_CAPABILITIES: &[(&str, &[&str])] = &[
    ("mouse", &["mouse.host_switch"]),
    (
        "display",
        &[
            "display.input",
            "display.pbp",
            "display.full",
            "layout.apply",
        ],
    ),
    ("keyboard", &["keyboard.presence"]),
];

pub fn role_reference_id(role: &str) -> Option<&'static str> {
    match role {
        "mouse" => Some("mxswitch"),
        "display" => Some("lgdualup"),
        "keyboard" => Some("hhkb"),
        _ => None,
    }
}

pub fn builtin_manifest(adapter_id: &str) -> Option<Value> {
    let (id, name, caps): (&str, &str, &[&str]) = match adapter_id {
        "mxswitch" => ("mxswitch", "MX Master Easy-Switch", &["mouse.host_switch"]),
        "lgdualup" => (
            "lgdualup",
            "LG DualUp",
            &[
                "display.input",
                "display.pbp",
                "display.full",
                "layout.apply",
            ],
        ),
        "dualup-layout" => ("dualup-layout", "DualUp OS layout", &["layout.apply"]),
        "hhkb" => ("hhkb", "HHKB presence", &["keyboard.presence"]),
        _ => return None,
    };
    Some(serde_json::json!({
        "api_version": ADAPTER_API_VERSION,
        "id": id,
        "name": name,
        "capabilities": caps,
    }))
}

#[derive(Clone, Debug)]
pub struct Binding {
    pub id: Option<String>,
    pub path: Option<PathBuf>,
    pub source: String,
    pub manifest: Option<Value>,
    pub capabilities: Vec<String>,
    pub enabled: bool,
    pub candidates: Vec<String>,
}

impl Binding {
    fn new(
        adapter_id: Option<String>,
        path: Option<PathBuf>,
        source: &str,
        enabled: bool,
        candidates: Vec<String>,
    ) -> Self {
        let manifest = adapter_id
            .as_deref()
            .map(|id| manifest_for(id, path.as_deref()));
        let capabilities = manifest
            .as_ref()
            .and_then(|m| m.get("capabilities"))
            .and_then(|c| c.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        Self {
            id: adapter_id,
            path,
            source: source.to_string(),
            manifest,
            capabilities,
            enabled,
            candidates,
        }
    }
}

pub fn resolve_backend_id(backend: &str) -> Option<PathBuf> {
    let lib = libexec_dir();
    for candidate in [lib.join(backend), lib.join(backend).join(backend)] {
        if is_exe(&candidate) {
            return Some(candidate);
        }
    }
    if let Some(prefixed) = which_cmd(&format!("desk-switch-{backend}")) {
        return Some(prefixed);
    }
    which_cmd(backend)
}

pub fn which_adapter(backend: &str, configured: Option<&str>) -> Option<PathBuf> {
    if let Some(configured) = configured {
        let expanded = crate::paths::expand_user(configured);
        if is_exe(&expanded) {
            return Some(expanded);
        }
    }
    let lib = libexec_dir();
    for candidate in [lib.join(backend), lib.join(backend).join(backend)] {
        if is_exe(&candidate) {
            return Some(candidate);
        }
    }
    if let Some(configured) = configured {
        if let Some(found) = which_cmd(configured) {
            return Some(found);
        }
        if Path::new(configured).file_name().and_then(|n| n.to_str()) != Some(backend) {
            return None;
        }
    }
    if let Some(prefixed) = which_cmd(&format!("desk-switch-{backend}")) {
        return Some(prefixed);
    }
    which_cmd(backend)
}

pub fn manifest_api_ok(raw: &Value) -> bool {
    let Some(obj) = raw.as_object() else {
        return false;
    };
    if obj
        .get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .is_empty()
    {
        return false;
    }
    let major = match obj.get("api_version") {
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0),
        Some(Value::String(s)) => s
            .split('.')
            .next()
            .and_then(|p| p.parse().ok())
            .unwrap_or(0),
        _ => 0,
    };
    major == ADAPTER_API_VERSION
}

pub fn load_manifest_file(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path).ok()?;
    let raw: Value = serde_json::from_str(&text).ok()?;
    if raw.is_object() && manifest_api_ok(&raw) {
        Some(raw)
    } else {
        None
    }
}

pub fn manifest_for(adapter_id: &str, binary: Option<&Path>) -> Value {
    let lib = libexec_dir();
    let mut candidates = vec![
        lib.join(format!("{adapter_id}.manifest.json")),
        lib.join(adapter_id).join("manifest.json"),
    ];
    if let Some(binary) = binary {
        if let Some(parent) = binary.parent() {
            candidates.push(parent.join(format!("{adapter_id}.manifest.json")));
            candidates.push(parent.join("manifest.json"));
        }
        if let Some(name) = binary.file_name() {
            let mut sibling = binary.to_path_buf();
            sibling.set_file_name(format!("{adapter_id}.manifest.json"));
            let _ = name;
            candidates.push(sibling);
        }
    }
    for path in candidates {
        if let Some(loaded) = load_manifest_file(&path) {
            if loaded.get("id").and_then(|v| v.as_str()) == Some(adapter_id) {
                return loaded;
            }
        }
    }
    builtin_manifest(adapter_id).unwrap_or_else(|| {
        serde_json::json!({
            "api_version": ADAPTER_API_VERSION,
            "id": adapter_id,
            "name": adapter_id,
            "capabilities": [],
        })
    })
}

fn binary_for_manifest(lib: &Path, adapter_id: &str, manifest_path: &Path) -> Option<PathBuf> {
    if manifest_path.file_name().and_then(|n| n.to_str()) == Some("manifest.json") {
        if let Some(parent) = manifest_path.parent() {
            let nested = parent.join(adapter_id);
            if is_exe(&nested) {
                return Some(nested);
            }
        }
    }
    let flat = lib.join(adapter_id);
    if is_exe(&flat) {
        return Some(flat);
    }
    let nested = lib.join(adapter_id).join(adapter_id);
    if is_exe(&nested) {
        return Some(nested);
    }
    resolve_backend_id(adapter_id)
}

#[derive(Clone, Debug)]
pub struct Discovered {
    pub id: String,
    pub name: String,
    pub capabilities: Vec<String>,
    pub path: Option<PathBuf>,
    pub manifest: Value,
    pub source: String,
}

pub fn scan_libdir_manifests(lib: Option<&Path>) -> Vec<Discovered> {
    let root = lib.map(Path::to_path_buf).unwrap_or_else(libexec_dir);
    let mut found: BTreeMap<String, Discovered> = BTreeMap::new();
    let entries = match fs::read_dir(&root) {
        Ok(rd) => rd,
        Err(_) => return Vec::new(),
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let manifest_path = if path.is_file() {
            path.file_name()
                .and_then(|n| n.to_str())
                .filter(|n| n.ends_with(".manifest.json"))
                .map(|_| path.clone())
        } else if path.is_dir() && path.join("manifest.json").is_file() {
            Some(path.join("manifest.json"))
        } else {
            None
        };
        let Some(manifest_path) = manifest_path else {
            continue;
        };
        let Some(raw) = load_manifest_file(&manifest_path) else {
            continue;
        };
        let adapter_id = raw
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if adapter_id.is_empty() {
            continue;
        }
        let binary = binary_for_manifest(&root, &adapter_id, &manifest_path);
        let name = raw
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(&adapter_id)
            .to_string();
        let capabilities = raw
            .get("capabilities")
            .and_then(|c| c.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        found.insert(
            adapter_id.clone(),
            Discovered {
                id: adapter_id,
                name,
                capabilities,
                path: binary,
                manifest: raw,
                source: "scan".into(),
            },
        );
    }
    found.into_values().collect()
}

pub fn role_matches_caps(role: &str, capabilities: &[String]) -> bool {
    let wanted = ROLE_CAPABILITIES
        .iter()
        .find(|(r, _)| *r == role)
        .map(|(_, caps)| *caps)
        .unwrap_or(&[]);
    wanted
        .iter()
        .any(|cap| capabilities.iter().any(|c| c == cap))
}

fn role_enabled(cfg: &Config, role: &str) -> bool {
    match role {
        "mouse" => cfg.mouse_enabled,
        "display" => cfg.dualup_enabled,
        "keyboard" => cfg.keyboard_enabled,
        _ => true,
    }
}

fn role_pin(cfg: &Config, role: &str) -> (Option<String>, Option<String>) {
    match role {
        "mouse" => (cfg.mouse_backend.clone(), cfg.mouse_path.clone()),
        "display" => (cfg.display_backend.clone(), cfg.display_path.clone()),
        "keyboard" => (cfg.keyboard_backend.clone(), cfg.keyboard_path.clone()),
        _ => (None, None),
    }
}

pub fn bind_role(cfg: &Config, role: &str) -> Binding {
    if !role_enabled(cfg, role) {
        return Binding::new(None, None, "disabled", false, Vec::new());
    }
    let (backend, path_pin) = role_pin(cfg, role);
    if let Some(path_pin) = path_pin {
        let expanded = crate::paths::expand_user(&path_pin);
        if is_exe(&expanded) {
            let adapter_id = backend
                .or_else(|| role_reference_id(role).map(|s| s.to_string()))
                .unwrap_or_else(|| {
                    expanded
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("adapter")
                        .to_string()
                });
            return Binding::new(Some(adapter_id), Some(expanded), "path", true, Vec::new());
        }
        return Binding::new(backend, None, "path", true, Vec::new());
    }
    if let Some(backend) = backend {
        let resolved = resolve_backend_id(&backend);
        return Binding::new(Some(backend), resolved, "backend", true, Vec::new());
    }
    let scanned: Vec<Discovered> = scan_libdir_manifests(None)
        .into_iter()
        .filter(|item| role_matches_caps(role, &item.capabilities))
        .collect();
    let reference = role_reference_id(role);
    if scanned.len() == 1 {
        let item = &scanned[0];
        let path = item.path.clone().or_else(|| resolve_backend_id(&item.id));
        return Binding::new(Some(item.id.clone()), path, "scan", true, Vec::new());
    }
    if scanned.len() > 1 {
        if let Some(preferred) = scanned
            .iter()
            .find(|item| Some(item.id.as_str()) == reference)
        {
            let path = preferred
                .path
                .clone()
                .or_else(|| resolve_backend_id(&preferred.id));
            let candidates = scanned.iter().map(|i| i.id.clone()).collect();
            return Binding::new(Some(preferred.id.clone()), path, "scan", true, candidates);
        }
        let candidates = scanned.iter().map(|i| i.id.clone()).collect();
        return Binding::new(None, None, "ambiguous", true, candidates);
    }
    if let Some(reference) = reference {
        match role {
            "mouse" => {
                let resolved = which_adapter("mxswitch", Some(&cfg.mxswitch));
                return Binding::new(
                    Some("mxswitch".into()),
                    resolved,
                    "reference",
                    true,
                    Vec::new(),
                );
            }
            "display" => {
                let resolved = which_adapter("lgdualup", Some(&cfg.lgdualup));
                return Binding::new(
                    Some("lgdualup".into()),
                    resolved,
                    "reference",
                    true,
                    Vec::new(),
                );
            }
            "keyboard" => {
                let src = hhkb_source_path();
                let resolved = resolve_backend_id(reference).or_else(|| src.clone());
                let source = if resolved == src && src.is_some() {
                    "incore"
                } else {
                    "reference"
                };
                return Binding::new(Some("hhkb".into()), resolved, source, true, Vec::new());
            }
            _ => {}
        }
    }
    Binding::new(
        reference.map(|s| s.to_string()),
        None,
        "missing",
        true,
        Vec::new(),
    )
}

pub fn discovered_adapters() -> Vec<Discovered> {
    let mut found: BTreeMap<String, Discovered> = scan_libdir_manifests(None)
        .into_iter()
        .map(|item| (item.id.clone(), item))
        .collect();
    for adapter_id in ["mxswitch", "lgdualup", "dualup-layout", "hhkb"] {
        if found.contains_key(adapter_id) {
            continue;
        }
        let mut path = resolve_backend_id(adapter_id);
        if path.is_none() && adapter_id == "hhkb" {
            path = hhkb_source_path();
        }
        let Some(path) = path else {
            continue;
        };
        let raw = builtin_manifest(adapter_id).unwrap_or_else(|| serde_json::json!({}));
        let name = raw
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(adapter_id)
            .to_string();
        let capabilities = raw
            .get("capabilities")
            .and_then(|c| c.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        found.insert(
            adapter_id.to_string(),
            Discovered {
                id: adapter_id.to_string(),
                name,
                capabilities,
                path: Some(path),
                manifest: raw,
                source: "builtin".into(),
            },
        );
    }
    let mut items: Vec<Discovered> = found.into_values().collect();
    items.sort_by(|a, b| a.id.cmp(&b.id));
    items
}
