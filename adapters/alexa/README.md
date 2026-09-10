# alexa — smart-home reference adapter

Role: **`smarthome`**. Id: **`alexa`**. Core never calls Amazon or `alexacli`.

```
~/.local/lib/desk-switch/alexa
~/.local/lib/desk-switch/alexa.manifest.json
```

## Auth (Mac first)

This adapter shells out to [`alexacli`](https://github.com/buddyh/alexa-cli).
Gabriel’s Mac already has it authenticated:

- config: `~/.alexa-cli/config.json`
- domain: `amazon.com`
- Echo devices: **Sala**, **Escritório**

```bash
brew install buddyh/tap/alexacli   # if needed
alexacli auth                      # once; opens a browser
alexacli devices                   # Sala, Escritório
```

`ALEXA_CLI_CONFIG` overrides the config path (tests). `ALEXACLI` / `--cli`
overrides the binary.

## What works today

| Verb | Capability | Backing command |
|---|---|---|
| `list` | `smarthome.list` | `alexacli devices --json` |
| `info` / `status` | `smarthome.status` | cache + auth/cli probe (`status` refreshes) |
| `on` | `light.on` | `alexacli command "acender a luz" -d Escritório` |
| `off` | `light.off` | `alexacli command "apagar a luz" -d Escritório` |

`alexacli smarthome list` / `sh list` is attempted and recorded. On this
desk it currently fails with an empty JSON parse and **does not expose
light power**. `alexacli ask` can query in English prose but is too slow
and language-fragile for the 15s tray poll. Until the entity list
returns a `powerState`, `lights[].state` is last commanded:

- `on` / `off` write that state to the cache *before* the speak so a
  concurrent `desk-switch status --json` (DeskSwitchBar poll / HUD
  refresh) is honest.
- A failed speak reverts the cache.
- A device-list refresh will not clobber a newer command that landed
  while `alexacli devices` was in flight.
- When `sh list` later returns a light with `powerState`,
  `state_source` becomes `entity` and `lights_readable` is true.

Do not expect an Alexa-app / physical-switch toggle to show on the tray
until that entity read works.

## Escritório desk light

Spoken text must be **only** `acender a luz` or `apagar a luz`. The Echo
is selected with `-d Escritório`. Putting “escritório” in the utterance
addresses the wrong device. The adapter refuses a phrase that names the
Echo.

```bash
desk-switch smarthome on
desk-switch smarthome off
desk-switch smarthome list
desk-switch smarthome status --json
```

Optional pin:

```json
{
  "adapters": {
    "smarthome": {
      "enabled": true,
      "backend": "alexa",
      "device": "Escritório"
    }
  }
}
```

Do not set `light_on` / `light_off` to a room-qualified sentence.
