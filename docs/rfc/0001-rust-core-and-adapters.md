# RFC 0001 — Rust core, adapter contract, thinner trays

**Status:** draft (review first, no implementation in this PR)
**Desk (reference, not a core assumption):** Mac Studio ↔ Omarchy/Linux,
MX Master + optional LG DualUp + HHKB follow

One CLI. Thin native shells. Adapters you can swap. Bars that stop shouting.

This is the plan for getting there without breaking `desk-switch to linux`
on a Tuesday morning.

### Design principle — pluggability

**Core is orchestration and contract. Anything hardware-shaped plugs in.**

MX Master, LG DualUp, and HHKB are **reference adapters**: in-tree examples
that make *this* desk work and show third parties the shape. They are not
permanent in-core special cases. Core must never *assume* Logitech, LG, or
HHKB — no vendor VID/PID, no DualUp geometry types, no `if lgdualup` /
`if mxswitch` / `04FE:0016` in the orchestrator.

The north star: Gabriel’s defaults and hardware can be unplugged and
replaced. Someone else’s mouse, keyboard, or PBP/KVM drops in beside the
references. Core still hops, watches, and paints the same strip.

```
core     = config · status · watch · to/full/pbp · adapter registry
adapters = mouse / keyboard.presence / display (input · pbp · full · layout)
shells   = paint `bar_strip` + panel; never speak HID or hyprctl
```

---

## 1. Goals

- **One core in Rust** for config, status, watch, and orchestration. Same
  brain on Mac and Omarchy. Shells stay native and thin. Core never names
  a vendor.
- **A real adapter contract** so mouse / keyboard / display are
  capabilities, not `if dualup` special cases. Reference adapters
  (`mxswitch`, `lgdualup`, `hhkb`) ship in-tree under `adapters/<id>/`.
  Third parties drop a binary **and a manifest** in
  `~/.local/lib/desk-switch/` (or on `PATH`) and show up.
- **Clear binaries.** You learn `desk-switch`. Helpers stay private.
  `mxswitch` / `lgdualup` on `PATH` remain shims.
- **Trays that are visual and quiet.** Always-visible strip: focus + maybe
  a display mark. Core **composes** `bar_strip` from adapter status
  fields; shells only paint. Chips and cabling live in the click panel.
  A settings surface (later) rearranges hosts, channels, inputs, and
  devices — same model on both OS. Settings are **local JSON first**.
- **Keep today's CLI and JSON.** `to` / `full` / `pbp` / `watch` /
  `status --json` keep working. Config stays JSON. Add keys, don't rename.
  `bar_label` stays the dense compat string; `bar_strip` is additive.
- **Reference adapters can leave later.** `lgdualup` is first-class
  in-tree until the ABI is boring (prefer after settings / phase 3);
  extract is not a phase-1 story.

## 2. Non-goals

- Rewriting HID++ or DualUp USB in Rust on day one. Reference helpers
  stay out-of-process (TCC / hidraw / extract story).
- Baking Logitech / LG / HHKB into core types or discovery defaults that
  cannot be overridden by a drop-in adapter.
- App Store / notarized Mac app. Ad-hoc sign stays fine.
- Windows, or a third host name, until someone actually has one.
- D-Bus, gRPC, or protobuf as the *product* ABI. JSON is the contract.
- A settings cloud, or peer-sync in v1. Local JSON on each box first.
- `desk-switchd` in phase 1. The dense title was the pain; a socket is
  optional later if spawn lag shows up.
- Renaming LaunchAgents / systemd units (`local.hhkb-mx-follow`,
  `hhkb-mx-follow.service`). Those names are already loaded on this desk.
- Making a display adapter required. Mouse-only desks keep working.
- Pixel-perfect identical chrome on Swift vs QML. Shared *model*, native
  *widgets*.
- Starting phase 1 implementation in this PR. **Hold phase 1** until the
  RFC is accepted. Markdown only.

---

## 3. Current architecture (this repo, today)

Python `desk-switch` (v1.4.0) is the product. Adapters are config keys plus
hardcoded helper names — not a plugin ABI. Shells only ever call
`desk-switch`. Hardware knowledge lives in the core script and in
OS-named source paths (`macos/`, `linux/`), which is the thing this RFC
undoes.

```
desk-switch.py
  ├── mouse   → ~/.local/lib/desk-switch/mxswitch
  │              macos/mxswitch.c  |  linux/mxswitch.py
  ├── hosts   → config (this_host, channels, follow_channel, HHKB USB)
  ├── dualup  → lgdualup + dualup-layout
  │              macos/lgdualup.c  |  linux/lgdualup.sh
  │              macos/dualup-layout | linux/dualup-layout
  ├── HHKB    → in-process probe (ioreg/hidutil · sysfs 04FE:0016)
  ├── watch   → poll 0.5s × 4 ≈ 2s; leave → mouse away; USB rise → to here
  └── peer    → SSH `desk-switch status --json --local` (optional)

shells (15s poll, same JSON keys)
  macos/DeskSwitchBar/DeskSwitchBar.swift   MenuBarExtra
  BarWidget.qml + Panel.qml                 Omarchy plugin glaet.desk-switch
  extensions/omarchy-menu.jsonc             Trigger → Desk switch
```

`make install` writes:

| Path | What |
|---|---|
| `~/.local/bin/desk-switch` | CLI (also copied as `hhkb-mx-follow`) |
| `~/.local/lib/desk-switch/mxswitch` | mouse helper |
| `~/.local/lib/desk-switch/lgdualup` | DualUp USB HID |
| `~/.local/lib/desk-switch/dualup-layout` | displayplacer / hyprctl |
| `~/.local/bin/mxswitch`, `lgdualup` | shims → libdir |
| `~/.config/desk-switch/config.json` | first install only |

Legacy config `~/.config/hhkb-mx-follow/config.json` still loads.

**Discovery today** (`which_adapter`): configured path →
`~/.local/lib/desk-switch/<name>` → `PATH` / `~/.local/bin`. That *install*
layout is the right one. What's missing is a manifest, a `backend` field,
a libdir scan, and a name for "not mxswitch / lgdualup / the in-process
HHKB probe".

**Helper CLIs (informal, in-tree):**

```
mxswitch --info | 1|2|3 | --setup          # macOS TCC pane
lgdualup --info | --list | input <name> | pbp <mode> | pbp-assign <main> <sub>
dualup-layout full|pbp [--id …]            # exit 2 = EDID not ready, retry
```

DualUp `full` / `pbp` is a sequence, not one ioctl: USB toggle, then
`pbp-assign hdmi1 dp` (PBP only; VCP 0xF4 cannot set Sub), settle
(`layout_settle_s`, default 0.5s), then OS layout with ~8s of exit-2
retries. Mac layout applies the verbose displayplacer profile and
*re-lists* to confirm Resolution + Rotation — a 0 from displayplacer is
not enough (full can stick at `2560x2880` @ 0°). Linux is `1280x2880` t3
/ `2560x2880` t3, not the stretched `2880x1280` t3.

Those DualUp facts belong in the **display reference adapter**, not in
core types named after LG.

**Status JSON** is already the bar/peer contract. `collect_adapters`
says keep the shape stable; add keys, don't rename. Bars read both the
flat fields and `adapters.*`:

| Flat (shells + tests) | Nested |
|---|---|
| `target_hint`, `bar_label`, `bar_tooltip` | `adapters.mouse` |
| `hhkb_*`, `follow_hhkb_usb` | `adapters.hhkb` (today) / `adapters.keyboard` (target) |
| `mouse_channel`, `mouse_online`, `mouse_host` | `adapters.hosts` |
| `dualup_mode`, `dualup_inputs`, `dualup_usb` | `adapters.dualup` (today) / `adapters.display` (target) |
| `lgdualup` (bool, legacy) | `peer` |

`bar_label` is the dense title both trays paint in the bar:
`LNX  kbU  mx2  PBP`. That's the thing that got too loud. It **stays**
in JSON for old widgets. New shells paint core-composed `bar_strip`.

**Config** already has the adapters shape (`mouse` / `hosts` / `dualup`)
plus flat legacy keys (`mxswitch`, `lgdualup`, `this_host`, `hosts`,
`target_channel`). `adapters.*.path` exists. `adapters.*.backend` does
not. Host names are `mac` | `linux` (aliases: `macos`, `omarchy`, `lnx`,
…). `adapters.dualup` remains a **legacy alias** for the `display` role
so existing files keep working.

Hosts are hardcoded to two. DualUp full/PBP geometry is DualUp-specific
(tilted 28MQ780, Mac vs Linux EDID names). Today that knowledge leaks
into core; the target is one pluggable display adapter that owns it.

---

## 4. Where source lives

This is the map. If a file is hardware-shaped, it lives under
`adapters/<id>/`. Core does not grow a Logitech / LG / HHKB corner.

| Piece | Today | Target |
|---|---|---|
| `mxswitch` (C Mac / py Linux) | `macos/mxswitch.c`, `linux/mxswitch.py` | **`adapters/mxswitch/`** — reference **mouse** adapter (`mouse.host_switch`). Own binary. Core only speaks the mouse contract. |
| `lgdualup` + `dualup-layout` | `macos/lgdualup.c`, `linux/lgdualup.sh`, `macos/` + `linux/dualup-layout`, udev rules | **`adapters/lgdualup/`** — reference **display** adapter (`display.input` / `pbp` / `full` + `layout.apply`). One adapter **id**; may still be two binaries under the hood (TCC / udev). Extractable later. |
| HHKB probe | in-process `desk-switch.py` (`04FE:0016`, USB-ghost rules) | **`adapters/hhkb/`** — reference **`keyboard.presence`** adapter. Core must not keep VID/PID / ghost rules long-term. In-core probe is a **temporary fallback** until this adapter exists, not the end state. |
| host map, `watch`, `to`, status, config | `desk-switch.py` | **core** (Python → Rust). Orchestration + contract only. |
| DeskSwitchBar / Omarchy QML | `macos/DeskSwitchBar/`, `BarWidget.qml`, `Panel.qml` | **`shells/`** — paint only. No HID, no hyprctl, no vendor names in logic. |

North star reminder: Logitech / LG / HHKB **source** lives under
`adapters/<id>/`, never assumed in core. Third parties drop another
adapter *beside* them — same libdir, same manifest, same capabilities.

### 4.1 Proposed tree

```
adapters/
  mxswitch/          # mouse reference (C + Python, as now)
  lgdualup/          # display reference: lgdualup + dualup-layout + udev
  hhkb/              # keyboard.presence reference (new; replaces in-core probe)
  alexa/             # smarthome reference (devices + light on/off via alexacli)
crates/              # or core/ — Rust workspace once phase 2 starts
  desk-switch-core/
  desk-switch/       # CLI bin
shells/
  macos/DeskSwitchBar/
  omarchy/           # QML + plugin manifest
```

Install paths do **not** have to match source paths. `make install` can
keep writing `~/.local/lib/desk-switch/mxswitch` etc. The tree above is
where *this repo* puts vendor code so it is obvious what is a reference
and what is the product.

**Phase 1 may move/document toward this layout even while the core is
still Python.** Grouping files under `adapters/<id>/` is part of making
the contract real. **This PR does not move anything** — markdown only.
Hold the moves (and the rest of phase 1) for a follow-up.

Until crates exist, current paths stay. Suggested moves happen in the
phase-1 series, not here.

---

## 5. Proposed architecture

```
                    ┌─────────────────────────────────────┐
                    │  desk-switch-core   (Rust lib)       │
                    │  config · status · watch · orchestrate│
                    │  adapter registry                    │
                    │  (no Logitech / LG / HHKB types)     │
                    └───────────────┬─────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
     desk-switch (CLI)      desk-switchd (opt)     adapter binaries
     same argv as today     not in phase 1         reference or yours
                            later, if lag          mxswitch / lgdualup /
                                                   hhkb / dualup-layout
              │
              │  JSON status (`bar_strip` composed here)
              ┌──────────┴──────────┐
              ▼                     ▼
        DeskSwitchBar            Omarchy BarWidget / Panel
        (Swift, paint)           (QML, paint)
```

### 5.1 Rust crates

Phase 2 adds a cargo workspace. Don't rearrange the *Rust* tree in
phase 1. Adapter file moves (Python era) are allowed in phase 1 as
documented in §4.

| Crate | Role |
|---|---|
| `desk-switch-core` | Config load (roles + legacy), host map, status compose (`bar_strip`), watch loop, `to` / `full` / `pbp` / `layout`, adapter client. Temporary in-core HHKB probe **only** until `adapters/hhkb` ships. |
| `desk-switch` | CLI binary. Argv = today's parser. stdout JSON = today's object |
| `desk-switch-ipc` | Optional, **after** phase 1. Unix socket + NDJSON of the *same* status object. Only if 15s spawn lag is real. |

Keep HID helpers out of the Rust bins. A new `desk-switch` path must not
invalidate the Input Monitoring grant on `~/.local/lib/desk-switch/mxswitch`.

### 5.2 CLI binary

`desk-switch` remains the only command you teach. Responsibilities:

- Parse argv (`status`, `hint`, `to`, `switch`, `full`, `pbp`, `layout`,
  `watch`, `--version`).
- Load `~/.config/desk-switch/config.json` (legacy path still works).
- Dispatch into core. Print human text or `--json`.
- Never talk IOHID/hidraw itself. Never hard-code a vendor probe.

Later (phase 3, optional): `desk-switch config get|set` for the settings
panel. Merge-write; unknown keys survive. Local file only in v1.

`hhkb-mx-follow` stays an argv-compatible alias (copy or symlink).

### 5.3 Mac shell

`DeskSwitchBar` stays Swift `MenuBarExtra`. It already refuses to call
`mxswitch` / `lgdualup` directly — keep that.

Changes (phase 1 + 3), not a new app:

- **Strip title:** paint `bar_strip` (focus + optional display mark).
  Stop putting `bar_label`'s `kbU mx2~` in the menu bar.
- **Click panel:** today's chips + actions (refresh / to mac / to linux /
  full / pbp / layout). Detail lives here.
- **Settings:** native form that edits the shared **local** JSON (hosts,
  channels, display inputs, display_id, adapter enable, tray density).
  Phase 3. No peer-sync in v1.
- Still locates `~/.local/bin/desk-switch` (LaunchAgents have a thin PATH).

### 5.4 Omarchy shell

Same job as the Mac bar. `BarWidget.qml` is the strip; `Panel.qml` is the
detail. Plugin id stays `glaet.desk-switch`. Still `bash -lc` with
`~/.local/bin` on `PATH`.

Omarchy menu (`extensions/omarchy-menu.jsonc`) keeps calling `desk-switch`
verbs. Display rows already hide when the helper is missing — later they
can key off `adapters.display.available` (legacy: `adapters.dualup`)
from status if we grow a `when`-friendly helper.

No HID in QML. No Hyprland calls from QML. No vendor names in QML logic.

### 5.5 IPC — JSON status / events

**The contract is a JSON object**, not a transport.

Why JSON (and not something fancier):

- It already exists. Tests pin keys. Both shells parse it. Peer peek is
  SSH of `status --json --local`.
- You can `jq` it at 1am. Protobuf would need a toolchain the QML plugin
  does not have.
- D-Bus is Linux-only. Fine as a later *wrapper*, not the ABI Mac has to
  speak.

**Transports, in order:**

1. **CLI stdout** — forever. `desk-switch status --json`. Shells, SSH,
   humans. Phase 1–2 change nothing here except *add* keys (`bar_strip`,
   `adapters.discovered`, role/`backend` fields).
2. **Unix socket NDJSON** — **not phase 1.** Optional later if the 15s
   spawn is actually the pain (it is not the current pain; the dense
   title is). Same payload when/if it exists:

   ```json
   {"event":"status","ts":1710000000.0,"payload":{ "...collect_status..." }}
   ```

   Socket: `$XDG_RUNTIME_DIR/desk-switch.sock` (Linux) or
   `~/Library/Caches/desk-switch/desk-switch.sock` (macOS). Shells that
   cannot connect keep the 15s spawn. Watch and completed `to`/`full`/`pbp`
   would push a fresh snapshot so the strip updates without polling.

No second schema. If a field is in `status --json`, it is in the event
payload. Add keys; don't rename.

`desk-switchd` is **not** required to hop a mouse and is **out of
phase 1**. `watch` stays a foreground/service process. Prefer "CLI is
enough" until spawn lag shows up.

---

## 6. Adapter model

An adapter is an **executable** plus a **manifest**. Core never
`dlopen`s you. Capabilities are verbs, not linkable traits — Rust traits
live *inside* core as the client API; the wire is argv + JSON.

**Roles** (what core asks for) are not **ids** (what you drop on disk):

| Role | Capabilities (typical) | Reference adapter id | Legacy config key |
|---|---|---|---|
| `mouse` | `mouse.host_switch` | `mxswitch` | `adapters.mouse` |
| `keyboard` | `keyboard.presence` (and follow policy in core) | `hhkb` | in-process today |
| `display` | `display.input` / `pbp` / `full`, `layout.apply` | `lgdualup` | `adapters.dualup` |
| `smarthome` | `smarthome.list` / `smarthome.status` / `light.on` / `light.off` | `alexa` | `adapters.smarthome` |
| `hosts` | config only — no binary | — | `adapters.hosts` |

Swap DualUp for another PBP/KVM by dropping a different display adapter
and pointing `adapters.display.backend` at it (or letting libdir scan
find it). Core does not change.

### 6.1 Capabilities

| Capability | What it means | Reference backend |
|---|---|---|
| `mouse.host_switch` | Read/set Easy-Switch (or equivalent) slot | `mxswitch` `--info` / `1\|2\|3` |
| `keyboard.presence` | Is the follow keyboard here? USB vs BT | `hhkb` adapter (in-core probe only until that ships) |
| `keyboard.follow` | Leave → push mouse away; USB rise → `to` here | `hosts` + `watch` (core policy; presence from the adapter) |
| `display.input` | Select monitor input | `lgdualup input` |
| `display.pbp` | Split / assign Main+Sub | `lgdualup pbp` + `pbp-assign` |
| `display.full` | One input, full panel | `lgdualup pbp full` |
| `layout.apply` | OS resolution / rotation for a named profile | owned by the **display** adapter (`dualup-layout` binary today) |
| `smarthome.list` | List speakers / entities the adapter can address | `alexa` `list` (`alexacli devices` today; entity list when it works) |
| `smarthome.status` | Light-oriented snapshot (last command or entity state) | `alexa` `info` / `status` |
| `light.on` / `light.off` | Turn a configured light on or off | `alexa` `on` / `off` (fixed spoken phrases + Echo `-d`) |

Core orchestration (`to`, `full`, `pbp`, `watch`) asks the registry for
capabilities, not for "the DualUp code path". A KVM that only hops USB
implements `mouse.host_switch`. A monitor without PBP implements
`display.input` + maybe `layout.apply`. Missing capability = skip with a
one-line message (today's `pbp` no-op when `lgdualup` is missing).

`layout.apply` is a **capability of the display adapter**, not a
separate core-known helper forever. One adapter **id** (e.g. `lgdualup`)
may still be two installed binaries (`lgdualup` + `dualup-layout`)
because TCC, hidraw, and udev do not want one fat process. The contract
is one pluggable display adapter. Core talks to the id, not to "the
layout script" as a first-class product.

`hosts` stays a **config adapter** (no binary): `this_host`, per-host
`channel`, `follow_channel`, `follow_hhkb_usb`, optional `peer`. It is
not discovered on disk.

### 6.2 Manifest (`api_version: 1`)

Manifests **matter in phase 1**. The product story is "drop a binary + a
manifest in libdir, core discovers it." A built-in table for today's
three helpers is a bridge, not the design.

```json
{
  "api_version": 1,
  "id": "lgdualup",
  "name": "LG DualUp",
  "capabilities": ["display.input", "display.pbp", "display.full", "layout.apply"]
}
```

How to get it (first that works):

1. **File drop (phase 1):** `<libdir>/<id>.manifest.json` or
   `<libdir>/<id>/manifest.json`
2. **Optional later:** `<binary> --desk-switch-manifest` (stdout JSON)
3. **Temporary built-in table** for the names we already ship
   (`mxswitch`, `lgdualup`, `dualup-layout`) so phase 1 does not have to
   patch C/sh on day one. HHKB has no binary yet — the in-core probe is
   the fallback until `adapters/hhkb` + its manifest exist.

Core ignores adapters with `api_version` major > 1. Unknown capability
strings are ignored (forward compatible). Helper `--version` is
informational only.

In-tree helpers keep their current argv. The built-in table maps
capabilities → those verbs. New adapters should:

```
--desk-switch-manifest          # optional; file drop is enough in phase 1
info                            # prefer JSON; text is ok if you stay private
<verbs for each capability you listed>
```

`layout.apply` exit codes stay meaningful: `0` ok/skipped, `2` retry
(EDID), `1` hard fail. Core already retries on 2. The *display* adapter
owns when to return 2; core does not special-case DualUp EDID names.

### 6.3 Binary naming and layout

Install root stays `~/.local/lib/desk-switch/` (`$DESK_SWITCH_LIB`).

| Kind | Path |
|---|---|
| In-tree / reference helper | `~/.local/lib/desk-switch/<id>` (`mxswitch`, `lgdualup`, `dualup-layout`, later `hhkb`) |
| Nested drop-in | `~/.local/lib/desk-switch/<id>/<id>` plus `<id>/manifest.json` |
| Third-party on PATH | `desk-switch-<id>` |
| Compat shim | `~/.local/bin/<id>` → libdir (existing `desk-switch-adapter-shim`) |

Do **not** require `desk-switch-mxswitch`. The historical names stay.
Third parties use the `desk-switch-<id>` prefix so they don't squat
`mxswitch` on `PATH`.

Override in config (pin wins over scan):

```json
{
  "adapters": {
    "mouse": {
      "enabled": true,
      "backend": "unifying",
      "path": "~/.local/lib/desk-switch/unifying"
    },
    "display": {
      "enabled": true,
      "backend": "lgdualup"
    },
    "keyboard": {
      "enabled": true,
      "backend": "hhkb"
    }
  }
}
```

`backend` is new. `path` already works. If `path` is set, discovery
stops there. `adapters.dualup` is read as an alias of `adapters.display`.

### 6.4 Discovery order

Discovery **is part of the product in phase 1.** Config pins and
overrides; it is not config-only forever.

Scan `$DESK_SWITCH_LIB` for manifests in phase 1. A third-party-shaped
adapter (binary + `*.manifest.json`) that lists a role's capabilities
must be findable without a core patch.

For each *role* (`mouse`, `display` / legacy `dualup`, `keyboard`, `smarthome`):

1. `adapters.<role>.enabled == false` → skip
2. `adapters.<role>.path` if executable → use it (still read its
   manifest if present)
3. `adapters.<role>.backend` if set → resolve that id:
   1. `$DESK_SWITCH_LIB/<backend>`
   2. `$DESK_SWITCH_LIB/<backend>/<backend>`
   3. `PATH`: `desk-switch-<backend>`
   4. `PATH`: `<backend>` (compat)
4. **Libdir scan:** manifests under `$DESK_SWITCH_LIB` whose
   capabilities match the role. If exactly one unused match, use it.
   If several, prefer the reference id for that role when present
   (`mxswitch` / `lgdualup` / `hhkb`); otherwise require a pin
   (`status` should list the candidates).
5. Role reference default if nothing matched (and the reference is
   installed)
6. Temporary in-core fallback: `keyboard.presence` only, until
   `adapters/hhkb` exists

Every manifest found by the scan is listed in `status --json` as
`adapters.discovered`, including ones not bound to a role.

### 6.5 Versioning

| Thing | Policy |
|---|---|
| Manifest `api_version` | Major 1 now. Bump only when verbs/exit codes break |
| `status --json` | Add keys. Don't rename. `lgdualup` bool stays |
| CLI argv | Frozen until 2.0. New commands are additive (`config`) |
| Config JSON | Additive. Legacy flat keys still win if set (today's rule). `adapters.dualup` aliases `adapters.display`. |
| Product `version` | Today's `1.4.0` string in status; Rust bump follows semver |

A third-party adapter and this repo can release on different clocks.
Core + manifest major is the handshake.

### 6.6 How a third party ships (phase 1 story)

```bash
install -m 755 unifying ~/.local/lib/desk-switch/unifying
install -m 644 unifying.manifest.json ~/.local/lib/desk-switch/unifying.manifest.json
# or: ~/.local/lib/desk-switch/unifying/unifying + unifying/manifest.json

# optional pin — scan should also find it
# ~/.config/desk-switch/config.json
{
  "adapters": {
    "mouse": { "enabled": true, "backend": "unifying" }
  }
}

desk-switch status --json   # adapters.mouse.backend == "unifying"
                            # adapters.discovered includes unifying
desk-switch to linux        # core calls unifying 2
```

Phase 1 exit depends on this path working for a **third-party-shaped**
drop-in (not only `backend: mxswitch`). You do not need a crate dep on
desk-switch. `--desk-switch-manifest` is nice-to-have later; the file
is enough.

---

## 7. In-tree vs extractable

| Piece | Now | Later |
|---|---|---|
| CLI + watch + host map | in-tree (Python → Rust) | stays (core) |
| HHKB probe | in-process Python | **`adapters/hhkb`** reference; in-core probe dies when that adapter exists |
| `mxswitch` | in-tree (vendored MIT) | stays in-tree as the mouse **reference**; Logitech-specific code never moves *into* core |
| Omarchy plugin + DeskSwitchBar | in-tree | stays (thin shells under `shells/`) |
| `lgdualup` + `dualup-layout` + `43-lg-dualup.rules` | in-tree, **treat as `adapters/lgdualup`** | extract when manifest + `layout.apply` exit codes have sat through settings (prefer **post phase 3**) |

**Phase 1–3:** physically group vendor files under `adapters/<id>/`
when we touch them (phase 1 may start those moves; **not this PR**).
Still installed by `make install`.

**Phase 4:** other repo (e.g. `lgdualup`). `make install` here either
bundles a release tarball or tells you to install the adapter. A
mouse-only clone of desk-switch must still hop *a* mouse adapter — the
reference happens to be MX Master.

Do not extract on the first Rust PR. DualUp is full of desk-specific
geometry (2880×2560 @ 270, `pbp-assign` dance, displayplacer verify).
That knowledge should sit behind `layout.apply` + adapter-owned config
(`display_id`, `inputs`, `layouts`) before it crosses a repo boundary.

Do not leave HHKB in core "until a second keyboard exists." The second
keyboard *is the point of the contract*. Ship the reference adapter;
core keeps follow *policy* (`watch` edges, `follow_channel`) and asks
`keyboard.presence`.

---

## 8. Tray UX

### 8.1 Strip vs detail

Today both bars use `bar_label` as the always-visible title. That's four
tokens of telemetry on a status bar that already has a clock.

| Surface | Shows | Does not show |
|---|---|---|
| **Strip** (always visible) | Focus (MAC / LNX / ?) as a two-host mark; display split vs full if a `display.*` adapter reported a mode | Keyboard USB/BT, mouse channel, `~` cached, peer, cabling |
| **Panel** (click) | Today's chips: keyboard / mouse / display / PEER; tooltip-level summary; actions | Raw `status --json` dump |
| **Settings** (gear / menu) | Host order, Easy-Switch slots, display inputs, display_id, enable adapters, density escape hatch | HID reports |

Visual, not more monospace. Prefer a filled Mac/Linux mark and a
split/full display glyph over `kbU  mx2~  PBP`. Use the platform's
symbols (SF Symbols on Mac, the Omarchy / Nerd Font set on the bar).
Don't invent a third icon font.

**Core composes `bar_strip` from adapter status fields.** Shells only
paint. Any mouse / keyboard / display adapter feeds the same object —
not a DualUp-shaped field list that QML special-cases.

```json
"bar_strip": {
  "focus": "LNX",
  "display": "pbp"
}
```

Slots appear when that role has an adapter and something worth showing.
Default painted strip stays quiet (focus + optional display). Extra
slots (`mouse`, `keyboard`) may be present for a density escape hatch
or the panel; they are not an invitation to put chip soup back in the
bar.

`bar_label` **stays the dense string** in JSON so old widgets and tests
don't break. Additive `bar_strip` is the new API. Do not eventually
"fix" `bar_label` to match the strip.

Escape hatch: `ui.tray.density: "chips"` paints today's `bar_label` in
the strip. Default becomes `"strip"`.

### 8.2 Settings — rearrange monitors and devices

One form, two skins (Swift / QML), **phase 3**. Writes **local JSON**
on that box. No sync protocol in v1.

- **Hosts:** this machine, the other machine, Easy-Switch channel per
  host, `follow_channel`, `follow_hhkb_usb`.
- **Display:** whichever `display.*` adapter is bound — inputs per host,
  `pbp_mode`, `switch_pbp`, `display_id`, layout on/off. UI copy can say
  "DualUp" when `backend` is `lgdualup`; the fields are generic.
- **Devices:** enable/disable mouse, keyboard, and display adapters;
  show `adapters.discovered`.
- **Monitors:** list what `layout` / the OS can see (displayplacer UUID,
  Hyprland connector). Pick which one the display adapter targets. Drag
  order is "which host is left/right in PBP" = Main/Sub order, not a
  full tiling WM.

Writes go through core (`desk-switch config set` or a `--json` merge on
stdin) so Mac and Omarchy cannot drift field names.

### 8.3 Sync across Mac / Omarchy

**v1: edit each box.** Same file shape, same keys. That is enough.

- `config.json` shape is identical except `adapters.hosts.this_host`
  and `follow_channel` (each box pushes the mouse *away*).
- `status --json` keys are identical. Peer peek already fills the Mac
  `?` when the MX is asleep on Linux.

Peer-sync of channels/inputs over SSH is **later**, not a v1 promise.
If it happens, copy only shared slots (channels, display inputs, maybe
`pbp_mode`). Never overwrite `this_host`, `follow_channel`, or
`display_id`. Do not build a settings replica protocol.

---

## 9. Config surface

Keep reading, in order:

1. `~/.config/desk-switch/config.json`
2. `~/.config/hhkb-mx-follow/config.json`
3. repo-local `config.json` (dev)

Keep the adapters object. New installs still write that shape, with
`display` / `keyboard` added and `dualup` still accepted.

**Additive keys (proposed):**

```json
{
  "adapters": {
    "mouse": { "enabled": true, "backend": "mxswitch", "path": null },
    "keyboard": { "enabled": true, "backend": "hhkb", "path": null },
    "hosts": { "this_host": "mac", "follow_channel": 2, "mac": { "channel": 1 }, "linux": { "channel": 2 } },
    "display": { "enabled": true, "backend": "lgdualup" },
    "dualup": { "enabled": true, "backend": "lgdualup" }
  },
  "ui": {
    "tray": { "density": "strip" }
  }
}
```

`adapters.dualup` is a legacy alias of `adapters.display` (same object
if both set; flat legacy keys still win). Reference `backend` values
are defaults, not core types — a pin or a libdir manifest overrides
them.

Rules we already have and keep:

- Legacy flat keys still win if set (`apply_adapter_config`).
- Unknown keys are preserved on rewrite (phase 3 `config set` must
  merge).
- `adapters.display.inputs` / `adapters.dualup.inputs` empty ⇒
  `to mac|linux` leaves input alone; `pbp` still uses whatever the
  *display adapter* documents as its default pair (reference DualUp:
  Mac=`hdmi1`, Linux=`dp`).
- `layout_settle_s`, `layout_retries`, `peer`, `display_id` stay.

No TOML migration. No YAML. The file is JSON because the bars and the
peer already speak JSON. Settings edit this file locally.

---

## 10. Migration / CLI compatibility

These keep working through Rust and the shell rewrite. Tests in
`tests/test_desk_switch.py` are the contract; port them, don't weaken
them.

| Command | Promise |
|---|---|
| `desk-switch to mac\|linux [--mouse-only]` | Mouse hop + optional display input |
| `desk-switch switch mac\|linux\|1\|2\|3` | Host or raw channel |
| `desk-switch full` / `pbp [mode]` / `layout` | Display sequence; no-op if that adapter is missing |
| `desk-switch watch [--dry-run]` | Keyboard leave / USB appear; **never** display hop on leave |
| `desk-switch status [--json] [--local] [--hint]` | Same keys; `--local` skips SSH |
| `desk-switch hint` | `MAC` / `LNX` / `?` |
| `desk-switch --version` | Semver string |
| `hhkb-mx-follow …` | Same program |

PATH shims for `mxswitch` / `lgdualup` stay. Plugin still falls back to
`hhkb-mx-follow`.

**Status JSON:** add `bar_strip`, `adapters.mouse.backend`,
`adapters.display`, `adapters.keyboard`, `adapters.discovered`, etc.
Do not rename `bar_label`, `target_hint`, `hhkb_transport`,
`mouse_channel`, `dualup_mode`, `lgdualup`.

**Units:** `ExecStart` may become `/Users/YOU/.local/bin/desk-switch watch`
when someone edits the plist; default examples can mention both names.
Do not force a reload rename.

**Rust cutover:** install the Rust binary at the **same path**. Keep
`desk-switch.py` in-tree until the CLI tests pass on **both** hosts;
then delete it. No `desk-switch-rs` on PATH. No long-term Python
reference copy.

---

## 11. Phased delivery

No calendar. Each phase is a PR series with an exit check.
**This RFC PR holds phase 1** — do not start implementation here.

### Phase 1 — Adapter contract + quieter trays (Python is fine)

Hold until this RFC is accepted. Then:

- **Ownership moves (source only):** start grouping reference adapters
  under `adapters/mxswitch`, `adapters/lgdualup`, and (when written)
  `adapters/hhkb`. Document the tree in the README. Install paths can
  stay. Core remains Python.
- **Manifest + libdir scan:** file drop in
  `~/.local/lib/desk-switch/` (`<id>.manifest.json` or
  `<id>/manifest.json`). Built-in table only as a bridge for current
  helpers. `--desk-switch-manifest` can wait.
- **Discovery is a feature:** scan libdir; config pins/overrides.
- **HHKB:** land `adapters/hhkb` as the `keyboard.presence` reference
  if it fits this series; otherwise keep the in-core probe as an
  explicit temporary fallback and do not treat that as the end state.
- **Strip:** core composes `bar_strip` from adapter status fields.
  Shells paint it. `ui.tray.density` escape hatch.
- **`status --json`:** additive `bar_strip`. `bar_label` unchanged.
- No `desk-switchd`. No settings panel. No peer-sync.
- Settings can wait for phase 3; a README note is enough.

**Exit:**

1. Drop a **third-party-shaped** adapter into libdir **with a
   manifest**, enable and/or let discovery find it, and core uses it
   for that role — without DualUp / MX / HHKB assumed in core logic.
2. Both bars look quieter and still switch / full / pbp. Old
   `bar_label` consumers keep working.
3. Source tree has started to match §4 (or the leftover paths are
   listed as explicit debt).
4. No Rust required. No daemon.

### Phase 2 — Rust core, same CLI

- `desk-switch-core` + `desk-switch` bin. Helpers stay C / Python / sh
  (and the HHKB adapter, once it exists).
- Port `tests/test_desk_switch.py` semantics (host map, watch USB edge,
  status keys, pbp sequence, layout exit 2). Golden `status --json`.
- `make install` puts the Rust binary in `~/.local/bin/desk-switch`.
- In-core HHKB fallback, if still present, is ported *or* deleted in
  favor of `adapters/hhkb` — do not invent a Rust-native HHKB special
  case as the design.

**Exit:** on this Mac and the Omarchy box, `status --json`, `to`,
`full`, `pbp`, `layout`, `watch --dry-run` match Python behavior closely
enough that the existing bars don't notice. Input Monitoring still
points at `mxswitch`, not the new CLI. Python deleted after both hosts
pass.

### Phase 3 — Shell rewrite + local settings

- Swift + QML consume `bar_strip`; spawn fallback remains.
- `desk-switch config set` edits **local** JSON.
- Settings panel: rearrange hosts / channels / inputs / display_id.
- Glyphs, not chip soup, in the strip.
- Optional Unix socket / `desk-switchd` **only if** 15s spawn lag
  showed up. Not a default deliverable.

**Exit:** no adapter logic in shells. Settings written on Mac are the
same file shape on Linux (you still copy or edit the other box). Strip
+ panel + settings work with a display adapter present *and* missing,
and with a non-`lgdualup` display adapter bound. Plugin
`make validate-plugin` still passes.

### Phase 4 — Optional extract `lgdualup`

- Repo split only after the ABI is boring — **prefer post phase 3 /
  settings**, not immediately after Rust cutover.
- desk-switch without that repo still hops via whichever mouse adapter
  is installed (reference: `mxswitch`).

**Exit:** `adapters.display.enabled: false` (or legacy
`adapters.dualup.enabled: false`) or missing helper = mouse-only desk.
Display users install the adapter (bundled or separate) and `full` /
`pbp` work as now.

---

## 12. Risks

- **macOS Input Monitoring.** Grant is on the `mxswitch` *binary path*.
  Reinstall, Rust rewrite, or moving the helper to
  `adapters/mxswitch/mxswitch` means re-grant if the *installed* path
  changes. Keep `~/.local/lib/desk-switch/mxswitch`. Don't embed HID++
  in the CLI. `--setup` stays on the helper.
- **Linux hidraw.** `42-logitech-hidpp.rules` + `input` group; DualUp
  needs `43-lg-dualup.rules`. Those files travel with the **adapter**,
  not with core. An extracted adapter must ship its udev file or hops
  silently no-op.
- **Display USB is on one host.** Typical desk: USB `043e:9a39` in the
  Mac. Linux `full` cannot flip HID. `peer` is layout-only SSH. Core
  must keep "input hop only if this host sees the display adapter" —
  don't assume both boxes see the monitor, and don't name that USB id
  in core.
- **displayplacer / hyprctl EDID lag.** Helper exit 2 + retries.
  Mac must verify rotation, not trust displayplacer's 0. That verify
  lives in the display adapter's `layout.apply`, or full stays
  `2560x2880` @ 0°.
- **HHKB USB ghost.** Cable in the Mac + BT on Linux ⇒ Mac still
  enumerates a USB collection; watcher never sees "leave".
  `follow_hhkb_usb` treats USB *appearance* as `to here`. Probe errors
  count as *present* so a flaky `hidutil` cannot steal the mouse.
  Sleep/wake clock gap disarms. Those rules move **with `adapters/hhkb`**;
  don't "simplify" them in core or in a drive-by rewrite.
- **Peer SSH on every status.** 15s bar poll × 2s SSH = jank. `--local`
  and the 20s peer cache stay. A later daemon should peek less, not more.
- **GUI PATH.** Menu bar and Omarchy already special-case
  `~/.local/bin`. Don't drop that.
- **Capability leak.** DualUp geometry, VCP dance, and tilted EDID
  names must not become core types named `DualUpMode`. Profiles
  (`full` / `pbp`) + adapter-owned details. Same for Easy-Switch and
  HHKB VID/PID.
- **Watch in a new language.** Port the disarm / USB-edge / retry loop
  with the tests first. A missed clock-gap = surprise mouse hop after
  lid open. Presence bits come from the keyboard adapter; the loop
  stays in core.
- **Libdir scan surprises.** Two mouse manifests and no pin. Phase 1
  must list them in `adapters.discovered` and refuse to guess when the
  reference id is absent — don't silently bind the wrong binary.

---

## 13. Resolved for v1

These were the §12 open questions. **Decided** for v1 — argue only if
the north star (pluggable reference adapters; core never assumes LG /
Logitech / HHKB) is at risk.

1. **HHKB → `keyboard.presence` adapter.** Ship an in-tree HHKB adapter
   (`adapters/hhkb`) as the reference. In-core probe is a temporary
   fallback until that adapter exists, not the end state. Ghost /
   `04FE:0016` rules live in the adapter.
2. **No `desk-switchd` in phase 1.** Socket optional later if spawn lag
   shows up. The dense title was the pain.
3. **Core composes `bar_strip`** from adapter status fields. Shells
   only paint. Any mouse / keyboard / display adapter feeds the same
   strip.
4. **Settings = local JSON first.** Peer-sync later, if ever.
5. **Manifests matter in phase 1.** File drop in libdir is the story
   (`<id>.manifest.json` or `<id>/manifest.json`). Optional
   `--desk-switch-manifest` later. Built-in table is a bridge for
   current C/sh helpers only.
6. **Scan libdir in phase 1.** Discovery is part of the product. Config
   pins and overrides; not config-only forever.
7. **Display adapter owns `layout.apply`.** One adapter **id** (e.g.
   `lgdualup`) may still be two binaries (HID + layout) for TCC/udev,
   but the contract is one pluggable display adapter. Swap DualUp for
   another PBP/KVM without core changes.
8. **Rust cutover = same path.** Python until both hosts pass tests,
   then delete. No forever-`desk-switch.py` reference.
9. **Extract DualUp after the ABI is boring.** Prefer post phase 3 /
   settings. Not after phase 2 by default.
10. **`bar_label` stays dense** for compat. Strip is additive
    `bar_strip`. Do not redefine `bar_label` later.

### Still open (do not block v1)

- Exact `bar_strip` glyph tokens vs semantic values (`"pbp"` vs a
  codepoint). Core should emit **semantics**; shells pick SF Symbols /
  Nerd Font.
- HHKB adapter argv / presence JSON shape — lock when `adapters/hhkb`
  is written. Flat `hhkb_*` status keys stay for compat.
- Whether two adapters may bind the same role at once. v1: one bound
  role, extras listed in `adapters.discovered`.
- When (not whether) C helpers grow `--desk-switch-manifest`. File drop
  is enough to ship phase 1.

---

## 14. What this PR is

Markdown only. No adapter code, no file moves, no tray restyle, no
Cargo.toml.

**Hold phase 1 implementation** until this RFC is accepted.

Next PR, if this holds: phase 1 discovery + manifest scan + strip +
start of the `adapters/` tree, still Python.
