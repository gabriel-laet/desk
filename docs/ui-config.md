# Shared HUD / tray UI config

One JSON object, one file, both shells.

**Path:** `~/.config/desk-switch/config.json` → `ui`  
(same file as hosts / adapters; old `~/.config/hhkb-mx-follow/config.json` still loads)

Omarchy and macOS DeskSwitchBar read this schema. DeskSwitchBar can
drag-reorder slots and write the file so you do not have to edit JSON
for the common case. Omarchy paints whatever core composes; a drag UI
there is a later TODO, not a second schema.

## Schema

```json
{
  "ui": {
    "tray": {
      "density": "strip",
      "lights": false,
      "slots": [
        { "id": "weather", "enabled": true },
        { "id": "kettle", "enabled": true },
        { "id": "dualup", "enabled": true },
        { "id": "lights", "enabled": false }
      ]
    },
    "hud": {
      "show_altitude": true,
      "show_faces": true,
      "density": "regular"
    }
  }
}
```

| Key | Meaning |
|---|---|
| `ui.tray.density` | `strip` (default, quiet extra) or `chips` (dense `bar_label`) |
| `ui.tray.lights` | Include a lights mark on `bar_strip`. Also implied when the `lights` slot is enabled |
| `ui.tray.slots` | Order + visibility. Objects `{id, enabled}` or a string pin list (`["kettle"]`) |
| `ui.hud.show_altitude` | If false, core strips `780m`-style weather detail |
| `ui.hud.show_faces` | If false, core drops `face` and shells skip the Watch chrome |
| `ui.hud.density` | `regular` (default) or `compact` |

## Slot rules

- No `ui.tray.slots` → today’s quiet set: **weather → kettle → dualup**.
  Missing adapters are omitted. Lights stay out.
- Object list → that order. `enabled: false` hides a slot. A new
  adapter id that is not listed still appends (except `lights`).
- String list → exclusive pin (RFC 0002). Only those ids, that order.
- `status --json` always echoes a normalized `ui` (object slots) so
  shells do not need to open the file.

## Who writes it

| Writer | What |
|---|---|
| DeskSwitchBar **Configure tray** | Drag-reorder, show/hide, HUD toggles. Merges `ui` only |
| Hand-edit / `config.example.json` | Same keys |
| Omarchy | Reader only for now (TODO settings UI) |

Core (`desk-switch.py`) is the composer: `collect_slots` honors order,
visibility, altitude, and faces before either shell paints.
