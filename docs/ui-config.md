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
        { "id": "weather", "enabled": true, "kind": "chip", "show_altitude": true },
        { "id": "kettle", "enabled": true, "kind": "face" },
        { "id": "dualup", "enabled": true, "kind": "mode" },
        { "id": "lights", "enabled": false, "kind": "toggle" }
      ]
    },
    "hud": {
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
| `ui.tray.slots` | Order + visibility + flavor. Objects `{id, enabled, kind?, …}` or a string pin list (`["kettle"]`) |
| `ui.tray.slots[].kind` | Widget flavor the shell renders. Adapters publish this on `slots[]`; the catalog copies it |
| `ui.tray.slots[].show_altitude` | Chip-slot pref. If false, core strips `780m`-style detail. **Altitude is adapter-owned** — not a global HUD switch |
| `ui.hud.show_faces` | If false, face-kind widgets skip the circular gauge |
| `ui.hud.density` | `regular` (default) or `compact` |
| `ui.hud.show_altitude` | Deprecated mirror of the first chip slot’s `show_altitude`. Older configs still work |

## Widget kinds (flavors)

Each live `slots[]` item carries `kind`. Shells keep a small v1 registry
for snapshots that omit it. A new adapter sets `kind` and the HUD
paints it without an `if kettle` / `if weather` branch.

| Kind | Role | Typical slot | HUD module |
|---|---|---|---|
| `host` | Focus strip | *(shell-owned, not a tray slot)* | Desk → MAC/LNX · Switch Mac/Linux |
| `face` | Appliance gauge | `kettle` | Circular progress + scoped Heat/Off |
| `chip` | Ambient | `weather` | ° + optional per-slot altitude / mood |
| `mode` | Display | `dualup` | FULL/PBP + scoped Full / PBP / layout |
| `toggle` | Light | `lights` | ON/OFF/? + scoped On/Off (HUD flips immediately) |

macOS DeskSwitchBar is the reference HUD: host at the top, then enabled
slots in `ui.tray.slots` order, each once, actions under that widget,
Refresh / Configure tray / Quit at the bottom. The menu-bar extra stays
a compact composite of the same enabled slots.

Omarchy stub-reads `kind` on the same JSON. A modular QML HUD is a
later pass — do not fork the schema.

## Slot rules

- No `ui.tray.slots` → today’s quiet set: **weather → kettle → dualup**.
  Missing adapters are omitted. Lights stay out.
- Lights opted in (`ui.tray.lights` or the `lights` pref) always emit a
  slot (`ON` / `OFF` / `?`). Core prefers adapter entity power when
  readable; otherwise last commanded. The Mac HUD flips the slot
  immediately on On/Off, then the next `status --json` poll confirms.
- Object list → that order. `enabled: false` hides a slot. A new
  adapter id that is not listed still appends (except `lights`).
- String list → exclusive pin (RFC 0002). Only those ids, that order.
- `status --json` always echoes a normalized `ui` (object slots with
  `kind`) so shells do not need to open the file.
- Per-slot prefs travel on the catalog object (`show_altitude` on
  `kind: chip`). Core applies them in `collect_slots` before either
  shell paints.

## Who writes it

| Writer | What |
|---|---|
| DeskSwitchBar **Configure tray** | Drag-reorder, show/hide, HUD toggles, chip altitude. Merges `ui` only |
| Hand-edit / `config.example.json` | Same keys |
| Omarchy | Reader only for now (TODO settings UI) |

Core (`desk-switch.py`) is the composer: `collect_slots` honors order,
visibility, kind, per-slot altitude, and faces before either shell paints.
