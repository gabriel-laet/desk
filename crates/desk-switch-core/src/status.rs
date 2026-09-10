use std::path::PathBuf;

use serde_json::{json, Map, Value};

use crate::adapters::{bind_role, discovered_adapters};
use crate::cache::{load_peer_cache, save_mouse_cache, save_peer_cache};
use crate::config::Config;
use crate::display::{detect_display_mode, dualup_inputs_map, dualup_layout_path, lgdualup_path};
use crate::hosts::{hint_for_host, host_for_channel, known_hints};
use crate::keyboard::{keyboard_probe, Presence};
use crate::mouse::mouse_snapshot;
use crate::process::{adapter_argv, Runtime};
use crate::VERSION;

pub fn resolve_target_hint(
    cfg: &Config,
    mouse_channel: Option<i64>,
    hhkb_present: Option<bool>,
    peer_channel: Option<i64>,
    mouse_source: Option<&str>,
) -> (String, String) {
    let pairs = [
        (mouse_channel, mouse_source.unwrap_or("mouse")),
        (peer_channel, "peer_mouse"),
    ];
    for (channel, source) in pairs {
        if let Some(host) = host_for_channel(&cfg.hosts, channel) {
            return (hint_for_host(&host).to_string(), source.to_string());
        }
    }
    if hhkb_present.unwrap_or(false) {
        return (hint_for_host(&cfg.this_host).to_string(), "hhkb".into());
    }
    ("?".into(), "unknown".into())
}

pub fn target_hint(cfg: &Config, mouse_channel: Option<i64>, hhkb: Option<bool>) -> String {
    resolve_target_hint(cfg, mouse_channel, hhkb, None, None).0
}

pub fn format_bar_strip(state: &Value) -> Value {
    let mut hint = state
        .get("target_hint")
        .and_then(|v| v.as_str())
        .unwrap_or("?")
        .to_string();
    if !known_hints().contains(&hint.as_str()) && hint != "?" {
        hint = "?".into();
    }
    let mut strip = Map::new();
    strip.insert("focus".into(), Value::String(hint));
    let mode = state
        .get("dualup_mode")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_ascii_lowercase();
    if mode == "full" || mode == "pbp" {
        strip.insert("display".into(), Value::String(mode));
    }
    Value::Object(strip)
}

pub fn format_strip_title(strip: &Value) -> String {
    let mut focus = strip
        .get("focus")
        .and_then(|v| v.as_str())
        .unwrap_or("?")
        .to_string();
    if !known_hints().contains(&focus.as_str()) {
        focus.clear();
    }
    let display = strip
        .get("display")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let mark = if display == "pbp" {
        "PBP"
    } else if display == "full" {
        "FULL"
    } else {
        ""
    };
    let parts: Vec<&str> = [focus.as_str(), mark]
        .into_iter()
        .filter(|p| !p.is_empty())
        .collect();
    if parts.is_empty() {
        "desk".into()
    } else {
        parts.join("  ")
    }
}

pub fn format_bar_label(state: &Value) -> String {
    let mut hint = state
        .get("target_hint")
        .and_then(|v| v.as_str())
        .unwrap_or("?")
        .to_string();
    if !known_hints().contains(&hint.as_str()) && hint != "?" {
        hint = "?".into();
    }
    let kb = if state
        .get("hhkb_usb")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        "kbU"
    } else if state
        .get("hhkb_bluetooth")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        "kbB"
    } else if state
        .get("hhkb_present")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        "kb"
    } else {
        "kb-"
    };
    let mx = match state.get("mouse_channel").and_then(|v| v.as_i64()) {
        None => "mx-".to_string(),
        Some(ch)
            if state
                .get("mouse_online")
                .and_then(|v| v.as_bool())
                .unwrap_or(false) =>
        {
            format!("mx{ch}")
        }
        Some(ch) => format!("mx{ch}~"),
    };
    let mode = state
        .get("dualup_mode")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_ascii_lowercase();
    let mut parts: Vec<String> = Vec::new();
    if known_hints().contains(&hint.as_str()) {
        parts.push(hint);
    }
    parts.push(kb.into());
    parts.push(mx);
    if mode == "pbp" {
        parts.push("PBP".into());
    } else if mode == "full" {
        parts.push("FULL".into());
    }
    if parts.is_empty() {
        "desk".into()
    } else {
        parts.join("  ")
    }
}

pub fn format_bar_tooltip(state: &Value) -> String {
    let hhkb = if state
        .get("hhkb_usb")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        "USB on this host"
    } else if state
        .get("hhkb_bluetooth")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        "BT only"
    } else if state
        .get("hhkb_present")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        "present"
    } else {
        "absent"
    };
    let host = state
        .get("mouse_host")
        .and_then(|v| v.as_str())
        .unwrap_or("?");
    let channel = state.get("mouse_channel").and_then(|v| v.as_i64());
    let mut mouse = if let Some(ch) = channel {
        format!("ch {ch} → {host}")
    } else {
        "ch ?".into()
    };
    if state
        .get("mouse_online")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        mouse.push_str(" online");
    } else if channel.is_some() {
        mouse.push_str(" cached");
    } else {
        mouse.push_str(" missing");
    }
    let dual = state
        .get("dualup_mode")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let inputs = state.get("dualup_inputs").cloned().unwrap_or(json!({}));
    format!(
        "focus {} · HHKB {hhkb} · mouse {mouse} · DualUp {dual} mac={} linux={}",
        state
            .get("target_hint")
            .and_then(|v| v.as_str())
            .unwrap_or("?"),
        inputs
            .get("mac")
            .and_then(|v| v.as_str())
            .unwrap_or("hdmi1"),
        inputs.get("linux").and_then(|v| v.as_str()).unwrap_or("dp"),
    )
}

pub fn collect_adapters(
    cfg: &Config,
    mouse_channel: Option<i64>,
    mouse_path: Option<&PathBuf>,
    dual_path: Option<&PathBuf>,
) -> Value {
    let layout_path = dualup_layout_path(cfg);
    let mouse_bound = bind_role(cfg, "mouse");
    let display_bound = bind_role(cfg, "display");
    let keyboard_bound = bind_role(cfg, "keyboard");
    let mut display = json!({
        "enabled": cfg.dualup_enabled,
        "available": dual_path.is_some(),
        "backend": display_bound.id.clone().unwrap_or_else(|| "lgdualup".into()),
        "path": dual_path.map(|p| p.to_string_lossy().into_owned()),
        "layout": cfg.dualup_layout,
        "layout_helper": layout_path.as_ref().map(|p| p.to_string_lossy().into_owned()),
        "display_id": cfg.dualup_display_id.clone().filter(|s| !s.is_empty()),
        "mode": Value::Null,
        "inputs": dualup_inputs_map(cfg),
        "source": display_bound.source,
    });
    if !display_bound.candidates.is_empty() {
        display["candidates"] = json!(display_bound.candidates);
    }
    let keyboard = json!({
        "enabled": cfg.keyboard_enabled,
        "available": keyboard_bound.path.is_some() || keyboard_bound.source == "incore",
        "backend": keyboard_bound.id.clone().unwrap_or_else(|| "hhkb".into()),
        "path": keyboard_bound.path.as_ref().map(|p| p.to_string_lossy().into_owned()),
        "source": keyboard_bound.source,
    });
    let discovered: Vec<Value> = discovered_adapters()
        .into_iter()
        .map(|item| {
            json!({
                "id": item.id,
                "name": item.name,
                "capabilities": item.capabilities,
                "path": item.path.map(|p| p.to_string_lossy().into_owned()),
                "source": item.source,
            })
        })
        .collect();
    json!({
        "mouse": {
            "enabled": cfg.mouse_enabled,
            "available": mouse_path.is_some(),
            "backend": mouse_bound.id.unwrap_or_else(|| "mxswitch".into()),
            "path": mouse_path.map(|p| p.to_string_lossy().into_owned()),
            "channel": mouse_channel,
            "source": mouse_bound.source,
        },
        "keyboard": keyboard,
        "hosts": {
            "this_host": cfg.this_host,
            "follow_channel": cfg.target_channel,
            "mac": cfg.hosts.get("mac").map(|h| h.to_value()).unwrap_or(json!({})),
            "linux": cfg.hosts.get("linux").map(|h| h.to_value()).unwrap_or(json!({})),
        },
        "display": display,
        "dualup": display,
        "discovered": discovered,
    })
}

fn slim_peer_status(data: &Value, peer: &str) -> Value {
    json!({
        "reachable": true,
        "peer": peer,
        "this_host": data.get("this_host"),
        "hhkb_present": data.get("hhkb_present"),
        "hhkb_usb": data.get("hhkb_usb"),
        "hhkb_bluetooth": data.get("hhkb_bluetooth"),
        "hhkb_transport": data.get("hhkb_transport"),
        "mouse_channel": data.get("mouse_channel"),
        "mouse_online": data.get("mouse_online"),
        "target_hint": data.get("target_hint"),
        "dualup_mode": data.get("dualup_mode"),
    })
}

pub fn peer_ssh_target(cfg: &Config) -> String {
    let adapters = cfg.adapters_user.clone();
    let hosts_ad = adapters.get("hosts").cloned().unwrap_or(json!({}));
    let dual = crate::config::display_adapter_cfg(&cfg.user);
    cfg.peer
        .clone()
        .or_else(|| {
            hosts_ad
                .get("peer")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
        .or_else(|| cfg.dualup_peer.clone())
        .or_else(|| {
            dual.get("peer")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_default()
        .trim()
        .to_string()
}

pub fn peek_peer_status(cfg: &Config, rt: &Runtime) -> Option<Value> {
    let peer = peer_ssh_target(cfg);
    if peer.is_empty() {
        return None;
    }
    if let Some(cached) = load_peer_cache(&peer) {
        return Some(cached);
    }
    let remote = concat!(
        r#"export PATH="$HOME/.local/bin:$PATH"; "#,
        "if command -v desk-switch >/dev/null; then desk-switch status --json --local; ",
        "elif command -v hhkb-mx-follow >/dev/null; then hhkb-mx-follow status --json --local; ",
        "else echo '{}'; fi"
    );
    // Python: bash -lc {remote!r}  →  bash -lc '<quoted remote>'
    let quoted = python_repr(remote);
    let argv = vec![
        "ssh".into(),
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        "ConnectTimeout=2".into(),
        peer.clone(),
        format!("bash -lc {quoted}"),
    ];
    let missed = json!({"reachable": false, "peer": peer});
    let proc = match rt.run(&argv, 6.0) {
        Ok(p) => p,
        Err(_) => {
            save_peer_cache(&peer, &missed);
            return Some(missed);
        }
    };
    if proc.status != 0 {
        save_peer_cache(&peer, &missed);
        return Some(missed);
    }
    let data: Value = match serde_json::from_str(proc.stdout.trim()) {
        Ok(v) => v,
        Err(_) => {
            save_peer_cache(&peer, &missed);
            return Some(missed);
        }
    };
    if !data.is_object() || data.as_object().map(|o| o.is_empty()).unwrap_or(true) {
        save_peer_cache(&peer, &missed);
        return Some(missed);
    }
    let slim = slim_peer_status(&data, &peer);
    save_peer_cache(&peer, &slim);
    Some(slim)
}

fn python_repr(s: &str) -> String {
    // Match Python !r for a string without quotes/backslashes (our remote is clean).
    format!("'{s}'")
}

pub fn collect_status(cfg: &Config, rt: &Runtime, local_only: bool) -> Value {
    collect_status_with_probe(cfg, rt, local_only, None, None)
}

pub fn collect_status_with_probe(
    cfg: &Config,
    rt: &Runtime,
    local_only: bool,
    probe_override: Option<Presence>,
    peer_override: Option<Value>,
) -> Value {
    let probe = probe_override.unwrap_or_else(|| keyboard_probe(cfg, rt));
    let mouse = mouse_snapshot(cfg, rt);
    let mouse_bound = bind_role(cfg, "mouse");
    let mouse_path = mouse_bound.path.clone();
    let dual_path = lgdualup_path(cfg);
    let mut dual_info = String::new();
    let mut dual_usb = false;
    if let Some(path) = dual_path.as_ref() {
        let argv = adapter_argv(path, &["--info"]);
        match rt.run(&argv, 8.0) {
            Ok(proc) => {
                dual_info = if !proc.stdout.is_empty() {
                    proc.stdout.trim_end().to_string()
                } else {
                    proc.stderr.trim_end().to_string()
                };
                dual_usb = proc.status == 0 && dual_info.to_ascii_lowercase().contains("043e:9a39");
            }
            Err(exc) => dual_info = exc.to_string(),
        }
    }
    let dual_mode = detect_display_mode(Some(cfg));
    let inputs = dualup_inputs_map(cfg);
    let peer = if local_only {
        None
    } else {
        peer_override.or_else(|| peek_peer_status(cfg, rt))
    };
    let mut peer_channel = None;
    let mut peer_online = false;
    if let Some(Value::Object(p)) = &peer {
        if p.get("reachable")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            peer_channel = p.get("mouse_channel").and_then(|v| v.as_i64());
            peer_online = p
                .get("mouse_online")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
        }
    }

    let (channel, mouse_source) = if mouse.channel_live.is_some() {
        (mouse.channel_live, Some("mouse".to_string()))
    } else if peer_channel.is_some() && peer_online {
        if let Some(ch) = peer_channel {
            save_mouse_cache(ch, Some(rt.now()));
        }
        (peer_channel, Some("peer_mouse".into()))
    } else if mouse.channel.is_some() {
        (
            mouse.channel,
            Some(
                mouse
                    .source
                    .clone()
                    .unwrap_or_else(|| "mouse_cached".into()),
            ),
        )
    } else if peer_channel.is_some() {
        if let Some(ch) = peer_channel {
            save_mouse_cache(ch, Some(rt.now()));
        }
        (peer_channel, Some("peer_mouse".into()))
    } else {
        (None, None)
    };

    let hhkb_for_hint = probe.present && !probe.unknown;
    let peer_for_hint = if mouse_source.as_deref() == Some("peer_mouse") {
        None
    } else {
        peer_channel
    };
    let (hint, hint_source) = resolve_target_hint(
        cfg,
        channel,
        Some(hhkb_for_hint),
        peer_for_hint,
        mouse_source.as_deref(),
    );
    let mut adapters = collect_adapters(cfg, channel, mouse_path.as_ref(), dual_path.as_ref());
    adapters["mouse"]["online"] = json!(mouse.online && mouse_source.as_deref() == Some("mouse"));
    adapters["mouse"]["stale"] = json!(mouse_source.as_deref() == Some("mouse_cached"));
    adapters["mouse"]["host"] = json!(host_for_channel(&cfg.hosts, channel));
    adapters["mouse"]["source"] = json!(mouse_source);
    let hhkb = json!({
        "present": probe.present && !probe.unknown,
        "usb": probe.usb,
        "bluetooth": probe.bluetooth,
        "transport": if probe.transport.is_empty() { "absent" } else { &probe.transport },
        "backend": adapters.get("keyboard").and_then(|k| k.get("backend")).cloned().unwrap_or(json!("hhkb")),
    });
    adapters["hhkb"] = hhkb.clone();
    if let Some(kb) = adapters.get_mut("keyboard").and_then(|v| v.as_object_mut()) {
        for (k, v) in hhkb.as_object().unwrap() {
            kb.insert(k.clone(), v.clone());
        }
    }
    adapters["dualup"]["mode"] = json!(dual_mode);
    adapters["dualup"]["usb"] = json!(dual_usb);
    adapters["dualup"]["inputs"] = Value::Object(inputs.clone());
    if let Some(display) = adapters.get_mut("display").and_then(|v| v.as_object_mut()) {
        display.insert("mode".into(), json!(dual_mode));
        display.insert("usb".into(), json!(dual_usb));
        display.insert("inputs".into(), Value::Object(inputs.clone()));
    }
    let present = adapters["hhkb"]["present"].as_bool().unwrap_or(false);
    let mut state = json!({
        "os": crate::paths::system(),
        "version": VERSION,
        "config": cfg.config_path,
        "this_host": cfg.this_host,
        "hhkb": if present { "present" } else { "absent" },
        "hhkb_present": present,
        "hhkb_usb": probe.usb,
        "hhkb_bluetooth": probe.bluetooth,
        "hhkb_transport": if probe.transport.is_empty() { "absent" } else { &probe.transport },
        "follow_hhkb_usb": cfg.follow_hhkb_usb,
        "target_channel": cfg.target_channel,
        "hosts": {
            "mac": cfg.hosts.get("mac").map(|h| h.to_value()).unwrap_or(json!({})),
            "linux": cfg.hosts.get("linux").map(|h| h.to_value()).unwrap_or(json!({})),
        },
        "target_hint": hint,
        "target_hint_source": hint_source,
        "mxswitch": mouse_path.as_ref().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|| cfg.mxswitch.clone()),
        "mouse_channel": channel,
        "mouse_channel_live": mouse.channel_live,
        "mouse_online": mouse_source.as_deref() == Some("mouse"),
        "mouse_host": host_for_channel(&cfg.hosts, channel),
        "mouse_info": mouse.info,
        "lgdualup": adapters["dualup"]["available"],
        "lgdualup_path": dual_path.as_ref().map(|p| p.to_string_lossy().into_owned()),
        "dualup_info": dual_info,
        "dualup_mode": dual_mode,
        "dualup_usb": dual_usb,
        "dualup_inputs": inputs,
        "peer": peer,
        "adapters": adapters,
        "ui": {"tray": {"density": cfg.tray_density()}},
    });
    state["bar_label"] = json!(format_bar_label(&state));
    state["bar_tooltip"] = json!(format_bar_tooltip(&state));
    state["bar_strip"] = format_bar_strip(&state);
    state
}

pub fn cmd_status(
    cfg: &Config,
    rt: &Runtime,
    as_json: bool,
    hint_only: bool,
    local_only: bool,
) -> i32 {
    let state = collect_status(cfg, rt, local_only || hint_only);
    if hint_only {
        rt.print(
            state
                .get("target_hint")
                .and_then(|v| v.as_str())
                .unwrap_or("?"),
        );
        return 0;
    }
    if as_json {
        rt.print(&serde_json::to_string_pretty(&state).unwrap_or_else(|_| "{}".into()));
        return 0;
    }
    let adapters = state.get("adapters").cloned().unwrap_or(json!({}));
    let mouse = adapters.get("mouse").cloned().unwrap_or(json!({}));
    let hosts = adapters.get("hosts").cloned().unwrap_or(json!({}));
    let dual = adapters.get("dualup").cloned().unwrap_or(json!({}));
    rt.print(&format!(
        "os            : {}",
        state["os"].as_str().unwrap_or("")
    ));
    rt.print(&format!(
        "version       : {}",
        state["version"].as_str().unwrap_or("")
    ));
    rt.print(&format!(
        "config        : {}",
        state["config"].as_str().unwrap_or("")
    ));
    rt.print(&format!(
        "this_host     : {}",
        state["this_host"].as_str().unwrap_or("")
    ));
    let transport = state
        .get("hhkb_transport")
        .and_then(|v| v.as_str())
        .unwrap_or("absent");
    let usb_note = if state["hhkb_usb"].as_bool().unwrap_or(false) {
        "USB on this host"
    } else if state["hhkb_bluetooth"].as_bool().unwrap_or(false) {
        "BT only"
    } else {
        transport
    };
    rt.print(&format!(
        "hhkb          : {}  transport={transport}  ({usb_note})",
        state["hhkb"].as_str().unwrap_or("")
    ));
    rt.print(&format!(
        "target        : channel {}",
        state["target_channel"]
    ));
    rt.print(&format!(
        "target_hint   : {}  source={}",
        state["target_hint"].as_str().unwrap_or("?"),
        state["target_hint_source"].as_str().unwrap_or("?")
    ));
    rt.print(&format!(
        "bar           : {}",
        state
            .get("bar_label")
            .and_then(|v| v.as_str())
            .unwrap_or("?")
    ));
    rt.print("adapters");
    let mut mouse_state = if mouse["available"].as_bool().unwrap_or(false) {
        "available"
    } else {
        "missing"
    };
    if !mouse
        .get("enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(true)
    {
        mouse_state = "disabled";
    }
    rt.print(&format!(
        "  mouse       : {mouse_state}  backend={}  {}",
        mouse
            .get("backend")
            .and_then(|v| v.as_str())
            .unwrap_or("mxswitch"),
        mouse.get("path").and_then(|v| v.as_str()).unwrap_or("-")
    ));
    rt.print(&format!(
        "  hosts       : this_host={}  follow_channel={}  mac={}  linux={}",
        hosts
            .get("this_host")
            .and_then(|v| v.as_str())
            .unwrap_or(state["this_host"].as_str().unwrap_or("")),
        hosts
            .get("follow_channel")
            .and_then(|v| v.as_i64())
            .unwrap_or(cfg.target_channel),
        hosts
            .get("mac")
            .and_then(|v| v.get("channel"))
            .cloned()
            .unwrap_or(json!("?")),
        hosts
            .get("linux")
            .and_then(|v| v.get("channel"))
            .cloned()
            .unwrap_or(json!("?")),
    ));
    let mut dual_state = if dual["available"].as_bool().unwrap_or(false) {
        "available"
    } else {
        "missing"
    };
    if !dual
        .get("enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(true)
    {
        dual_state = "disabled";
    }
    rt.print(&format!(
        "  dualup      : {dual_state}  backend={}  mode={}  mac={}  linux={}  {}",
        dual.get("backend")
            .and_then(|v| v.as_str())
            .unwrap_or("lgdualup"),
        state
            .get("dualup_mode")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown"),
        state
            .get("dualup_inputs")
            .and_then(|v| v.get("mac"))
            .and_then(|v| v.as_str())
            .unwrap_or("hdmi1"),
        state
            .get("dualup_inputs")
            .and_then(|v| v.get("linux"))
            .and_then(|v| v.as_str())
            .unwrap_or("dp"),
        dual.get("path").and_then(|v| v.as_str()).unwrap_or("-"),
    ));
    let mouse_line = if state
        .get("mouse_channel")
        .and_then(|v| v.as_i64())
        .is_some()
    {
        format!(
            "channel {} → {}  {}",
            state["mouse_channel"],
            state
                .get("mouse_host")
                .and_then(|v| v.as_str())
                .unwrap_or("?"),
            if state["mouse_online"].as_bool().unwrap_or(false) {
                "online"
            } else {
                "cached"
            }
        )
    } else {
        "missing".into()
    };
    rt.print(&format!("mouse         : {mouse_line}"));
    let info = state
        .get("mouse_info")
        .and_then(|v| v.as_str())
        .unwrap_or("(no output)");
    for line in info.lines() {
        rt.print(&format!("  {line}"));
    }
    if state["lgdualup"].as_bool().unwrap_or(false)
        || !state
            .get("dualup_info")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .is_empty()
    {
        rt.print("dualup        :");
        let info = state
            .get("dualup_info")
            .and_then(|v| v.as_str())
            .unwrap_or("(no output)");
        for line in info.lines() {
            rt.print(&format!("  {line}"));
        }
    }
    if let Some(peer) = state.get("peer").filter(|v| v.is_object()) {
        if peer
            .get("reachable")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            rt.print(&format!(
                "peer          : {}  hhkb={}  mouse={}  hint={}",
                peer.get("this_host")
                    .and_then(|v| v.as_str())
                    .or_else(|| peer.get("peer").and_then(|v| v.as_str()))
                    .unwrap_or(""),
                peer.get("hhkb_transport")
                    .and_then(|v| v.as_str())
                    .unwrap_or("?"),
                peer.get("mouse_channel").cloned().unwrap_or(json!("?")),
                peer.get("target_hint")
                    .and_then(|v| v.as_str())
                    .unwrap_or("?"),
            ));
        } else {
            rt.print(&format!(
                "peer          : {} unreachable",
                peer.get("peer").and_then(|v| v.as_str()).unwrap_or("")
            ));
        }
    }
    0
}
