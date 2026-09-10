use std::collections::{BTreeMap, HashSet};

use serde_json::Value;

use crate::error::{Error, Result};
use crate::json_util::{as_dict, as_int};

pub const HOST_ALIASES: &[(&str, &str)] = &[
    ("mac", "mac"),
    ("macos", "mac"),
    ("darwin", "mac"),
    ("studio", "mac"),
    ("linux", "linux"),
    ("lnx", "linux"),
    ("omarchy", "linux"),
    ("ser9", "linux"),
];

pub fn hint_for_host(host: &str) -> &'static str {
    match host {
        "mac" => "MAC",
        "linux" => "LNX",
        _ => "?",
    }
}

pub fn known_hints() -> &'static [&'static str] {
    &["MAC", "LNX"]
}

pub fn normalize_host(name: &str) -> Result<String> {
    let key = name.trim().to_ascii_lowercase();
    for (alias, canon) in HOST_ALIASES {
        if *alias == key {
            return Ok((*canon).to_string());
        }
    }
    Err(Error::exit(format!(
        "unknown host: {name} (try mac or linux)"
    )))
}

#[derive(Clone, Debug, Default)]
pub struct HostSpec {
    pub channel: Option<i64>,
    pub dualup_input: Option<String>,
    pub pbp: Option<String>,
    pub extra: BTreeMap<String, Value>,
}

impl HostSpec {
    pub fn from_value(raw: &Value) -> Self {
        match raw {
            Value::Number(n) => Self {
                channel: n.as_i64().or_else(|| n.as_u64().map(|u| u as i64)),
                ..Self::default()
            },
            Value::Object(map) => {
                let mut spec = Self::default();
                if let Some(ch) = map.get("channel") {
                    spec.channel = Some(as_int(ch, 0));
                }
                if let Some(Value::String(inp)) = map.get("dualup_input") {
                    spec.dualup_input = Some(inp.clone());
                }
                if let Some(pbp) = map.get("pbp") {
                    spec.pbp = Some(match pbp {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    });
                }
                for (k, v) in map {
                    if k != "channel" && k != "dualup_input" && k != "pbp" {
                        spec.extra.insert(k.clone(), v.clone());
                    }
                }
                spec
            }
            _ => Self::default(),
        }
    }

    pub fn to_value(&self) -> Value {
        let mut map = serde_json::Map::new();
        if let Some(ch) = self.channel {
            map.insert("channel".into(), Value::from(ch));
        }
        if let Some(inp) = &self.dualup_input {
            map.insert("dualup_input".into(), Value::String(inp.clone()));
        }
        if let Some(pbp) = &self.pbp {
            map.insert("pbp".into(), Value::String(pbp.clone()));
        }
        for (k, v) in &self.extra {
            map.insert(k.clone(), v.clone());
        }
        Value::Object(map)
    }
}

pub fn host_entry(raw: &Value) -> HostSpec {
    HostSpec::from_value(raw)
}

/// Merge configured host map with the documented Mac=1 / Linux=2 pairing.
pub fn resolve_hosts(
    this_host: &str,
    target_channel: Option<i64>,
    supplied: &BTreeMap<String, Value>,
) -> BTreeMap<String, HostSpec> {
    let this_host = normalize_host(this_host).unwrap_or_else(|_| this_host.to_string());
    let other = if this_host == "linux" { "mac" } else { "linux" };
    let mut merged: BTreeMap<String, HostSpec> = BTreeMap::new();
    merged.insert(
        "mac".into(),
        HostSpec {
            channel: Some(1),
            ..HostSpec::default()
        },
    );
    merged.insert(
        "linux".into(),
        HostSpec {
            channel: Some(2),
            ..HostSpec::default()
        },
    );
    let mut named: HashSet<String> = HashSet::new();
    for (name, raw) in supplied {
        let Ok(key) = normalize_host(name) else {
            continue;
        };
        let entry = host_entry(raw);
        let had_channel = entry.channel.is_some();
        let existing = merged.remove(&key).unwrap_or_default();
        let combined = merge_spec(&existing, &entry);
        if had_channel {
            named.insert(key.clone());
        }
        merged.insert(key, combined);
    }
    if !named.contains(other) {
        if let Some(tc) = target_channel {
            if let Some(spec) = merged.get_mut(other) {
                spec.channel = Some(tc);
            }
        }
    }
    merged
}

fn merge_spec(base: &HostSpec, overlay: &HostSpec) -> HostSpec {
    let mut out = base.clone();
    if overlay.channel.is_some() {
        out.channel = overlay.channel;
    }
    if overlay.dualup_input.is_some() {
        out.dualup_input = overlay.dualup_input.clone();
    }
    if overlay.pbp.is_some() {
        out.pbp = overlay.pbp.clone();
    }
    for (k, v) in &overlay.extra {
        out.extra.insert(k.clone(), v.clone());
    }
    out
}

pub fn host_channel(hosts: &BTreeMap<String, HostSpec>, host: &str) -> Result<i64> {
    let host = normalize_host(host)?;
    let channel = hosts.get(&host).and_then(|s| s.channel).unwrap_or(0);
    if channel == 0 {
        return Err(Error::exit(format!(
            "no Easy-Switch channel configured for host {host}"
        )));
    }
    Ok(channel)
}

pub fn host_for_channel(
    hosts: &BTreeMap<String, HostSpec>,
    channel: Option<i64>,
) -> Option<String> {
    let channel = channel?;
    for (name, spec) in hosts {
        if spec.channel == Some(channel) {
            return Some(name.clone());
        }
    }
    None
}

pub fn hosts_from_user_map(raw: &Value) -> BTreeMap<String, Value> {
    as_dict(raw)
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect()
}
