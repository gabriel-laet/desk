# hardware — LAN metrics adapter

Role: **`hardware`**. Id: **`hardware`**. Core never opens the endpoint.

```
~/.local/lib/desk/hardware
~/.local/lib/desk/hardware.manifest.json
```

One unauthenticated JSON document on the LAN. Pin the URL; nothing is
fetched until `adapters.hardware.enabled` is true.

```json
{
  "adapters": {
    "hardware": {
      "enabled": true,
      "url": "http://metrics.local/api/hardware",
      "poll_seconds": 5,
      "timeout_seconds": 1.5,
      "stale_seconds": 30,
      "host_map": { "endpoint-id": "linux" },
      "extra_hosts": ["nas"],
      "show": ["cpu_temp_c", "cpu_pct", "mem_pct", "gpu_pct", "gpu_temp_c"]
    }
  }
}
```

`host_map` renames an endpoint id to a desk host id. Unlisted ids stay
as they are. `extra_hosts` are shown even though they are not desk
hosts. Everyone else in the payload is left out once `desk_hosts` or
`extra_hosts` is set. Null metrics are omitted, not drawn as zero.

`info` / `status` print the cached sample and do not open a socket.
`refresh` does one fetch with `timeout_seconds`. On timeout, refusal,
or bad JSON the previous sample is kept. The cache file is
`$DESK_HARDWARE_STATE` when that points at a file or directory,
otherwise `$DESK_STATE_DIR/hardware.json` or
`$XDG_STATE_HOME/desk/hardware.json`.

`desk status` only reads that cache, then starts `refresh` in the
background when the sample is older than `poll_seconds`. `desk watch`
does not call this adapter.

An optional `hosts.<id>.ai` object is passed through generically.
Tool ids are not special-cased. Null numbers stay null; they are not
stored or drawn as zero. The hardware token (only on the bar when
`ui.tray.bar_slots` is true) adds the focused host's highest
`tools[].limit_used_pct`, skipping tools whose `scope` is `account`.
The click panel lists each remaining tool's name, plan, highest
`limits[]` entry (label, percent, reset time), and today's tokens and
cost when those numbers exist. `llama` is one line (model, tok/s,
slots) and only when `running` is true.

A top-level `ai_accounts` object is one row per shared account, not a
per-machine sum. Each entry uses the same fields as a tool, plus
`scope: "account"` and `hosts`. The adapter copies that into
`accounts` on its JSON and on the hardware slot. Missing or malformed
`ai_accounts` is an empty list. The click panel prints a Shared
accounts section above the per-host rows: name, plan, the highest
limit percent, that limit's reset time in this machine's local time,
today's sessions and tokens when they are not null, and the host
list. The same account is not repeated under each host and is not
added into the per-host limit chip. The bar mark is unchanged.

```bash
desk hardware status --json
```
