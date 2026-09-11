# alexa — smart-home reference adapter

Role: **`smarthome`**. Id: **`alexa`**. Core never calls Amazon or `alexacli`.

```
~/.local/lib/desk/alexa
~/.local/lib/desk/alexa.manifest.json
```

## Auth (Mac first)

This adapter shells out to [`alexacli`](https://github.com/buddyh/alexa-cli).

- config: `~/.alexa-cli/config.json`
- domain: whatever `alexacli auth` stored

```bash
brew install buddyh/tap/alexacli   # if needed
alexacli auth                      # once; opens a browser
alexacli devices                   # confirm speaker names
```

`ALEXA_CLI_CONFIG` overrides the config path (tests). `ALEXACLI` / `--cli`
overrides the binary.

## What works today

| Verb | Capability | Backing command |
|---|---|---|
| `list` | `smarthome.list` | `alexacli devices --json` |
| `info` / `status` | `smarthome.status` | cache + auth/cli probe (`status` refreshes) |
| `on` | `light.on` | `alexacli command "<on phrase>" -d DeviceName` |
| `off` | `light.off` | `alexacli command "<off phrase>" -d DeviceName` |

`alexacli smarthome list` / `sh list` is attempted and recorded. It often
fails with an empty JSON parse and **does not expose light power**.
`alexacli devices` and `alexacli command` work. Until the entity list
returns a `powerState`, `lights[].state` is last commanded:

- `on` / `off` write that state to the cache *before* the speak so a
  concurrent `desk status --json` poll is honest.
- A failed speak reverts the cache.
- When `sh list` later returns a light with `powerState`,
  `state_source` becomes `entity` and `lights_readable` is true.

Do not expect an Alexa-app / physical-switch toggle to show on the tray
until that entity read works.

## Desk light

Spoken text must be **only** the on/off phrase (defaults: `acender a luz`
/ `apagar a luz`). The speaker is selected with `-d DeviceName`. Putting
the device name in the utterance addresses the wrong device. The adapter
refuses a phrase that names the configured Echo.

```bash
desk smarthome on
desk smarthome off
desk smarthome list
desk smarthome status --json
```

Optional pin:

```json
{
  "adapters": {
    "smarthome": {
      "enabled": true,
      "backend": "alexa",
      "device": "DeviceName"
    }
  }
}
```

Do not set `light_on` / `light_off` to a room-qualified sentence.
