use serde_json::{Map, Value};

pub fn as_dict(value: &Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map.clone(),
        _ => Map::new(),
    }
}

pub fn as_dict_ref(value: Option<&Value>) -> Map<String, Value> {
    match value {
        Some(Value::Object(map)) => map.clone(),
        _ => Map::new(),
    }
}

pub fn as_int(value: &Value, default: i64) -> i64 {
    match value {
        Value::Null => default,
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_u64().map(|u| u as i64))
            .or_else(|| n.as_f64().map(|f| f as i64))
            .unwrap_or(default),
        Value::String(s) => parse_int_auto(s).unwrap_or(default),
        Value::Bool(true) => 1,
        Value::Bool(false) => 0,
        _ => default,
    }
}

pub fn parse_int_auto(s: &str) -> Option<i64> {
    let s = s.trim();
    if let Some(hex) = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")) {
        i64::from_str_radix(hex, 16).ok()
    } else if s.starts_with('0') && s.len() > 1 && s.bytes().all(|b| b.is_ascii_digit()) {
        // Python int(s, 0) treats leading 0 as decimal in Py3 (not octal).
        s.parse().ok()
    } else {
        s.parse().ok()
    }
}

pub fn as_float(value: &Value, default: f64) -> f64 {
    match value {
        Value::Null => default,
        Value::Number(n) => n.as_f64().unwrap_or(default),
        Value::String(s) => s.parse().unwrap_or(default),
        _ => default,
    }
}

pub fn as_bool(value: &Value, default: bool) -> bool {
    match value {
        Value::Null => default,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_i64().unwrap_or(0) != 0,
        Value::String(s) => {
            let s = s.trim().to_ascii_lowercase();
            match s.as_str() {
                "1" | "true" | "yes" | "on" => true,
                "0" | "false" | "no" | "off" => false,
                _ => default,
            }
        }
        _ => default,
    }
}

pub fn opt_str(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
        Some(Value::Number(n)) => Some(n.to_string()),
        _ => None,
    }
}
