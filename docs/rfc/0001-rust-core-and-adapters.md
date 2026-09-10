# RFC 0001 — Rust core, adapter contract, thinner trays

**Status:** draft (review first, no implementation in this PR)
**Desk:** Mac Studio ↔ Omarchy/Linux, MX Master + optional LG DualUp + HHKB follow

One CLI. Thin native shells. Adapters you can swap. Bars that stop shouting.

This is the plan for getting there without breaking `desk-switch to linux`
on a Tuesday morning.

---

## 1. Goals

- **One core in Rust** for config, status, watch, and orchestration. Same
  brain on Mac and Omarchy. Shells stay native and thin.
- **A real adapter contract** so mouse / keyboard / display / layout are
  capabilities, not `if dualup` special cases forever. Third parties can
  drop a binary in `~/.local/lib/desk-switch/` (or on `PATH`) and show up.
- **Clear binaries.** You learn `desk-switch`. Helpers stay private.
  `mxswitch` / `lgdualup` on `PATH` remain shims.
- **Trays that are visual and quiet.** Always-visible strip: focus + maybe
  a DualUp mark. Chips and cabling live in the click panel. A settings
  surface rearranges hosts, channels, inputs, and devices — same model on
  both OS.
- **Keep today's CLI and JSON.** `to` / `full` / `pbp` / `watch` /
  `status --json` keep working. Config stays JSON. Add keys, don't rename.
- **lgdualup can leave later.** First-class in-tree adapter now; extract
  when the ABI stops moving.

## 2. Non-goals

- Rewriting HID++ or DualUp USB in Rust on day one. `mxswitch` and
  `lgdualup` stay out-of-process (TCC / hidraw / extract story).
- App Store / notarized Mac app. Ad-hoc sign stays fine.
- Windows, or a third host name, until someone actually has one.
- D-Bus, gRPC, or protobuf as the *product* ABI. JSON is the contract.
- A settings cloud. Sync is optional SSH of a few keys, not a service.
- Renaming LaunchAgents / systemd units (`local.hhkb-mx-follow`,
  `hhkb-mx-follow.service`). Those names are already loaded on this desk.
- Making DualUp required. Mouse-only desks keep working.
- Pixel-perfect identical chrome on Swift vs QML. Shared *model*, native
  *widgets*.

---

## 3. Current architecture (this repo, today)

Python `desk-switch` (v1.4.0) is the product. Adapters are config keys plus
hardcoded helper names — not a plugin ABI. Shells only ever call
`desk-switch`.

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
`~/.local/lib/desk-switch/<name>` → `PATH` / `~/.local/bin`. That layout
is the right one. What's missing is a manifest, a `backend` field, and a
name for "not mxswitch / lgdualup".

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

**Status JSON** is already the bar/peer contract. `collect_adapters`
says keep the shape stable; add keys, don't rename. Bars read both the
flat fields and `adapters.*`:

| Flat (shells + tests) | Nested |
|---|---|
| `target_hint`, `bar_label`, `bar_tooltip` | `adapters.mouse` |
| `hhkb_*`, `follow_hhkb_usb` | `adapters.hhkb` |
| `mouse_channel`, `mouse_online`, `mouse_host` | `adapters.hosts` |
| `dualup_mode`, `dualup_inputs`, `dualup_usb` | `adapters.dualup` |
| `lgdualup` (bool, legacy) | `peer` |

`bar_label` is the dense title both trays paint in the bar:
`LNX  kbU  mx2  PBP`. That's the thing that got too loud.

**Config** already has the adapters shape (`mouse` / `hosts` / `dualup`)
plus flat legacy keys (`mxswitch`, `lgdualup`, `this_host`, `hosts`,
`target_channel`). `adapters.*.path` exists. `adapters.*.backend` does
not. Host names are `mac` | `linux` (aliases: `macos`, `omarchy`, `lnx`,
…).

Hosts are hardcoded to two. DualUp full/PBP geometry is DualUp-specific
(tilted 28MQ780, Mac vs Linux EDID names). The dualup adapter is
LG-shaped even though the *role* (display input + layout) is generic.

---

## 4. Proposed architecture

```
                    ┌─────────────────────────────────────┐
                    │  desk-switch-core   (Rust lib)       │
                    │  config · status · watch · orchestrate│
                    │  adapter registry                    │
                    └───────────────┬─────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
     desk-switch (CLI)      desk-switchd (opt)     adapter binaries
     same argv as today     watch + event socket   mxswitch / lgdualup /
                            phase 3                dualup-layout / yours
              │                     │
              └──────────┬──────────┘
                         │  JSON status / events
              ┌──────────┴──────────┐
              ▼                     ▼
        DeskSwitchBar            Omarchy BarWidget / Panel
        (Swift, thin)            (QML, thin)
```

### 4.1 Rust crates

Phase 2 adds a cargo workspace. Don't rearrange the tree in phase 1.

| Crate | Role |
|---|---|
| `desk-switch-core` | Config load (adapters + legacy), host map, HHKB probe, status compose, watch loop, `to` / `full` / `pbp` / `layout`, adapter client |
| `desk-switch` | CLI binary. Argv = today's parser. stdout JSON = today's object |
| `desk-switch-ipc` | Phase 3. Unix socket + NDJSON of the *same* status object. Optional |

Keep HID helpers out of the Rust bins. A new `desk-switch` path must not
invalidate the Input Monitoring grant on `~/.local/lib/desk-switch/mxswitch`.

Suggested on-disk layout once crates exist (move files when it pays, not
in this PR):

```
crates/desk-switch-core/
crates/desk-switch/              # bin
adapters/mxswitch/               # C + Python, as now
adapters/lgdualup/               # C + sh + dualup-layout + udev
shells/macos/DeskSwitchBar/
shells/omarchy/                  # QML + manifest.json
```

Until then, current paths stay.

### 4.2 CLI binary

`desk-switch` remains the only command you teach. Responsibilities:

- Parse argv (`status`, `hint`, `to`, `switch`, `full`, `pbp`, `layout`,
  `watch`, `--version`).
- Load `~/.config/desk-switch/config.json` (legacy path still works).
- Dispatch into core. Print human text or `--json`.
- Never talk IOHID/hidraw itself.

Later (phase 3, optional): `desk-switch config get|set` for the settings
panel. Merge-write; unknown keys survive.

`hhkb-mx-follow` stays an argv-compatible alias (copy or symlink).

### 4.3 Mac shell

`DeskSwitchBar` stays Swift `MenuBarExtra`. It already refuses to call
`mxswitch` / `lgdualup` directly — keep that.

Changes (phase 1 + 3), not a new app:

- **Strip title:** focus glyph + optional DualUp mark. Stop putting
  `bar_label`'s `kbU mx2~` in the menu bar.
- **Click panel:** today's chips + actions (refresh / to mac / to linux /
  full / pbp / layout). Detail lives here.
- **Settings:** native form that edits the shared JSON (hosts, channels,
  DualUp inputs, display_id, adapter enable, tray density). Phase 3.
- Still locates `~/.local/bin/desk-switch` (LaunchAgents have a thin PATH).

### 4.4 Omarchy shell

Same job as the Mac bar. `BarWidget.qml` is the strip; `Panel.qml` is the
detail. Plugin id stays `glaet.desk-switch`. Still `bash -lc` with
`~/.local/bin` on `PATH`.

Omarchy menu (`extensions/omarchy-menu.jsonc`) keeps calling `desk-switch`
verbs. DualUp rows already hide when the helper is missing — later they
can key off `adapters.dualup.available` from status if we grow a
`when`-friendly helper.

No HID in QML. No Hyprland calls from QML.

### 4.5 IPC — JSON status / events

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
   humans. Phase 1–2 change nothing here except *add* keys.
2. **Unix socket NDJSON** — phase 3, optional daemon. Same payload:

   ```json
   {"event":"status","ts":1710000000.0,"payload":{ "...collect_status..." }}
   ```

   Socket: `$XDG_RUNTIME_DIR/desk-switch.sock` (Linux) or
   `~/Library/Caches/desk-switch/desk-switch.sock` (macOS). Shells that
   cannot connect keep the 15s spawn. Watch and completed `to`/`full`/`pbp`
   push a fresh snapshot so the strip updates without polling.

No second schema. If a field is in `status --json`, it is in the event
payload. Add keys; don't rename.

`desk-switchd` is **not** required to hop a mouse. `watch` can stay a
foreground/service process that does not serve sockets until phase 3
needs it. Prefer "CLI is enough" until the 15s spawn is actually the
pain.

---

## 5. Adapter model

An adapter is an **executable** plus an optional **manifest**. Core never
`dlopen`s you. Capabilities are verbs, not linkable traits — Rust traits
live *inside* core as the client API; the wire is argv + JSON.

### 5.1 Capabilities

| Capability | What it means | Today's backend |
|---|---|---|
| `mouse.host_switch` | Read/set Easy-Switch (or equivalent) slot | `mxswitch` `--info` / `1\|2\|3` |
| `keyboard.presence` | Is the follow keyboard here? USB vs BT | in-core HHKB probe |
| `keyboard.follow` | Leave → push mouse away; USB rise → `to` here | `hosts` + `watch` |
| `display.input` | Select monitor input | `lgdualup input` |
| `display.pbp` | Split / assign Main+Sub | `lgdualup pbp` + `pbp-assign` |
| `display.full` | One input, full panel | `lgdualup pbp full` |
| `layout.apply` | OS resolution / rotation for a named profile | `dualup-layout full\|pbp` |

Core orchestration (`to`, `full`, `pbp`, `watch`) asks the registry for
capabilities, not for "the DualUp code path". A KVM that only hops USB
implements `mouse.host_switch`. A monitor without PBP implements
`display.input` + maybe `layout.apply`. Missing capability = skip with a
one-line message (today's `pbp` no-op when `lgdualup` is missing).

`hosts` stays a **config adapter** (no binary): `this_host`, per-host
`channel`, `follow_channel`, `follow_hhkb_usb`, optional `peer`. It is
not discovered on disk.

### 5.2 Manifest (`api_version: 1`)

```json
{
  "api_version": 1,
  "id": "lgdualup",
  "name": "LG DualUp",
  "capabilities": ["display.input", "display.pbp", "display.full", "layout.apply"]
}
```

How to get it (first that works):

1. `<libdir>/<id>.manifest.json` or `<libdir>/<id>/manifest.json`
2. `<binary> --desk-switch-manifest` (stdout JSON)
3. **Built-in table** for the three names we already ship (`mxswitch`,
   `lgdualup`, `dualup-layout`) so phase 1 does not have to patch C/sh

Core ignores adapters with `api_version` major > 1. Unknown capability
strings are ignored (forward compatible). Helper `--version` is
informational only.

In-tree helpers keep their current argv. The built-in table maps
capabilities → those verbs. New adapters should:

```
--desk-switch-manifest
info                 # prefer JSON; text is ok if you stay private
<verbs for each capability you listed>
```

`layout.apply` exit codes stay meaningful: `0` ok/skipped, `2` retry
(EDID), `1` hard fail. Core already retries on 2.

### 5.3 Binary naming and layout

Install root stays `~/.local/lib/desk-switch/` (`$DESK_SWITCH_LIB`).

| Kind | Path |
|---|---|
| In-tree helper | `~/.local/lib/desk-switch/<id>` (`mxswitch`, `lgdualup`, `dualup-layout`) |
| Nested drop-in | `~/.local/lib/desk-switch/<id>/<id>` |
| Third-party on PATH | `desk-switch-<id>` |
| Compat shim | `~/.local/bin/<id>` → libdir (existing `desk-switch-adapter-shim`) |

Do **not** require `desk-switch-mxswitch`. The historical names stay.
Third parties use the `desk-switch-<id>` prefix so they don't squat
`mxswitch` on `PATH`.

Override in config:

```json
{
  "adapters": {
    "mouse": {
      "enabled": true,
      "backend": "unifying",
      "path": "~/.local/lib/desk-switch/unifying"
    }
  }
}
```

`backend` is new. `path` already works. If `path` is set, discovery
stops there.

### 5.4 Discovery order

For each *role* (`mouse`, `dualup`, later `keyboard`):

1. `adapters.<role>.enabled == false` → skip
2. `adapters.<role>.path` if executable
3. `adapters.<role>.backend` (default: `mxswitch` / `lgdualup`)
   1. `$DESK_SWITCH_LIB/<backend>`
   2. `$DESK_SWITCH_LIB/<backend>/<backend>`
   3. `PATH`: `desk-switch-<backend>`
   4. `PATH`: `<backend>` (compat)
4. Role default if `backend` omitted

Scan-the-directory for *extra* adapters (a second mouse, a projector) is
phase 3+. Phase 1 only needs "I can point `backend` at something that
isn't mxswitch". Extra manifests under libdir can be listed in
`status --json` as `adapters.discovered` without being invoked.

### 5.5 Versioning

| Thing | Policy |
|---|---|
| Manifest `api_version` | Major 1 now. Bump only when verbs/exit codes break |
| `status --json` | Add keys. Don't rename. `lgdualup` bool stays |
| CLI argv | Frozen until 2.0. New commands are additive (`config`) |
| Config JSON | Additive. Legacy flat keys still win if set (today's rule) |
| Product `version` | Today's `1.4.0` string in status; Rust bump follows semver |

A third-party adapter and this repo can release on different clocks.
Core + manifest major is the handshake.

### 5.6 How a third party ships

```bash
install -m 755 unifying ~/.local/lib/desk-switch/unifying
# or: install -m 755 unifying ~/.local/bin/desk-switch-unifying

# ~/.config/desk-switch/config.json
{
  "adapters": {
    "mouse": { "enabled": true, "backend": "unifying" }
  }
}

desk-switch status --json   # adapters.mouse.backend == "unifying"
desk-switch to linux        # core calls unifying 2
```

Ship a `unifying.manifest.json` next to the binary (or implement
`--desk-switch-manifest`). Document your `info` / switch verbs. You do
not need a crate dep on desk-switch.

---

## 6. In-tree vs extractable

| Piece | Now | Later |
|---|---|---|
| CLI + watch + HHKB probe + host map | in-tree (Python → Rust) | stays |
| `mxswitch` | in-tree (vendored MIT) | stays; Logitech-specific but small and required for *this* desk |
| Omarchy plugin + DeskSwitchBar | in-tree | stays (thin shells) |
| `lgdualup` + `dualup-layout` + `43-lg-dualup.rules` | in-tree, **treat as `adapters/lgdualup`** | extract when manifest + `layout.apply` exit codes have sat through a phase |

**Phase 1–3:** physically group DualUp files under `adapters/lgdualup/`
when we touch them (or leave paths and just *call* it that). Still
installed by `make install`.

**Phase 4:** other repo (e.g. `lgdualup`). `make install` here either
bundles a release tarball or tells you to install the adapter. A
mouse-only clone of desk-switch must still hop the MX Master.

Do not extract on the first Rust PR. DualUp is full of desk-specific
geometry (2880×2560 @ 270, `pbp-assign` dance, displayplacer verify).
That knowledge should sit behind `layout.apply` + config
(`display_id`, `inputs`, `layouts`) before it crosses a repo boundary.

`keyboard.presence` stays in-core until a second keyboard exists. HHKB
USB-ghost rules are too easy to get wrong in a drive-by adapter.

---

## 7. Tray UX

### 7.1 Strip vs detail

Today both bars use `bar_label` as the always-visible title. That's four
tokens of telemetry on a status bar that already has a clock.

| Surface | Shows | Does not show |
|---|---|---|
| **Strip** (always visible) | Focus (MAC / LNX / ?) as a two-host mark; DualUp split vs full if the adapter is present | HHKB USB/BT, mouse channel, `~` cached, peer, cabling |
| **Panel** (click) | Today's chips: HHKB / MX / DualUp / PEER; tooltip-level summary; actions | Raw `status --json` dump |
| **Settings** (gear / menu) | Host order, Easy-Switch slots, DualUp inputs, display_id, enable adapters, density escape hatch | HID reports |

Visual, not more monospace. Prefer a filled Mac/Linux mark and a
split/full DualUp glyph over `kbU  mx2~  PBP`. Use the platform's
symbols (SF Symbols on Mac, the Omarchy / Nerd Font set on the bar).
Don't invent a third icon font.

`bar_label` **stays the dense string** in JSON so old widgets and tests
don't break. New shells compose the strip from structured fields
(`target_hint`, `dualup_mode`, `adapters.dualup.available`). Add
`bar_strip` as a hint object if we want the core to pick glyphs:

```json
"bar_strip": { "focus": "LNX", "display": "pbp" }
```

Escape hatch: `ui.tray.density: "chips"` paints today's `bar_label` in
the strip. Default becomes `"strip"`.

### 7.2 Settings — rearrange monitors and devices

One form, two skins (Swift / QML):

- **Hosts:** this machine, the other machine, Easy-Switch channel per
  host, `follow_channel`, `follow_hhkb_usb`.
- **Display:** DualUp (or whatever `display.*` adapter) inputs per host,
  `pbp_mode`, `switch_pbp`, `display_id`, layout on/off.
- **Devices:** enable/disable mouse and display adapters; show discovered
  backends.
- **Monitors:** list what `layout` / the OS can see (displayplacer UUID,
  Hyprland connector). Pick which one the layout helper targets. Drag
  order is "which host is left/right in PBP" = `pbp-assign` Main/Sub
  order, not a full tiling WM.

Writes go through core (`desk-switch config set` or a `--json` merge on
stdin) so Mac and Omarchy cannot drift field names.

### 7.3 Sync across Mac / Omarchy

Same files, same keys. That *is* the sync story for a two-box desk:

- `config.json` shape is identical except `adapters.hosts.this_host`
  and `follow_channel` (each box pushes the mouse *away*).
- `status --json` keys are identical. Peer peek already fills the Mac
  `?` when the MX is asleep on Linux.

Optional later: `desk-switch config sync` over the existing `peer` SSH
host. Copy only `adapters.hosts.{mac,linux}.channel` and
`adapters.dualup.inputs` (and maybe `pbp_mode`). Never overwrite
`this_host`, `follow_channel`, or `display_id` (those are per-machine).

Do not build a settings replica protocol. If SSH peer is unset, you
edit each box.

---

## 8. Config surface

Keep reading, in order:

1. `~/.config/desk-switch/config.json`
2. `~/.config/hhkb-mx-follow/config.json`
3. repo-local `config.json` (dev)

Keep the adapters object. New installs still write that shape.

**Additive keys (proposed):**

```json
{
  "adapters": {
    "mouse": { "enabled": true, "backend": "mxswitch", "path": null },
    "hosts": { "this_host": "mac", "follow_channel": 2, "mac": { "channel": 1 }, "linux": { "channel": 2 } },
    "dualup": { "enabled": true, "backend": "lgdualup" }
  },
  "ui": {
    "tray": { "density": "strip" }
  }
}
```

Rules we already have and keep:

- Legacy flat keys still win if set (`apply_adapter_config`).
- Unknown keys are preserved on rewrite (phase 3 `config set` must
  merge).
- `adapters.dualup.inputs` empty ⇒ `to mac|linux` leaves input alone;
  `pbp` still uses Mac=`hdmi1`, Linux=`dp`.
- `layout_settle_s`, `layout_retries`, `peer`, `display_id` stay.

No TOML migration. No YAML. The file is JSON because the bars and the
peer already speak JSON.

---

## 9. Migration / CLI compatibility

These keep working through Rust and the shell rewrite. Tests in
`tests/test_desk_switch.py` are the contract; port them, don't weaken
them.

| Command | Promise |
|---|---|
| `desk-switch to mac\|linux [--mouse-only]` | Mouse hop + optional DualUp input |
| `desk-switch switch mac\|linux\|1\|2\|3` | Host or raw channel |
| `desk-switch full` / `pbp [mode]` / `layout` | DualUp sequence; no-op if adapter missing |
| `desk-switch watch [--dry-run]` | HHKB leave / USB appear; **never** DualUp on leave |
| `desk-switch status [--json] [--local] [--hint]` | Same keys; `--local` skips SSH |
| `desk-switch hint` | `MAC` / `LNX` / `?` |
| `desk-switch --version` | Semver string |
| `hhkb-mx-follow …` | Same program |

PATH shims for `mxswitch` / `lgdualup` stay. Plugin still falls back to
`hhkb-mx-follow`.

**Status JSON:** add `bar_strip`, `adapters.mouse.backend`,
`adapters.discovered`, etc. Do not rename `bar_label`, `target_hint`,
`hhkb_transport`, `mouse_channel`, `dualup_mode`, `lgdualup`.

**Units:** `ExecStart` may become `/Users/YOU/.local/bin/desk-switch watch`
when someone edits the plist; default examples can mention both names.
Do not force a reload rename.

Phase 2 cutover: install the Rust binary at the same path. Keep the
Python file in-tree as `desk-switch.py` until the CLI tests pass on both
hosts; then it's reference or gone. No `desk-switch-rs` on PATH.

---

## 10. Phased delivery

No calendar. Each phase is a PR series with an exit check.

### Phase 1 — Adapter contract + quieter trays (Python is fine)

- Document + implement discovery: `backend`, libdir / `desk-switch-<id>`,
  built-in manifest table for the three helpers.
- Shells: strip uses `target_hint` + DualUp mark; chips stay in the panel.
  `ui.tray.density` escape hatch.
- `status --json` gains `bar_strip` (additive). `bar_label` unchanged.
- Settings can wait for phase 3; a README note is enough.

**Exit:** drop a dummy `backend` on disk, set it in config, `to linux`
invokes it. Both bars look quieter and still switch / full / pbp. Old
`bar_label` consumers keep working. No Rust required.

### Phase 2 — Rust core, same CLI

- `desk-switch-core` + `desk-switch` bin. Helpers stay C / Python / sh.
- Port `tests/test_desk_switch.py` semantics (host map, watch USB edge,
  status keys, pbp sequence, layout exit 2). Golden `status --json`.
- `make install` puts the Rust binary in `~/.local/bin/desk-switch`.

**Exit:** on this Mac and the Omarchy box, `status --json`, `to`,
`full`, `pbp`, `layout`, `watch --dry-run` match Python behavior closely
enough that the existing bars don't notice. Input Monitoring still
points at `mxswitch`, not the new CLI.

### Phase 3 — Shell rewrite

- Swift + QML consume `bar_strip` / events; spawn fallback remains.
- Optional `desk-switchd` socket. Optional `desk-switch config set`.
- Settings panel: rearrange hosts / channels / inputs / display_id.
- Glyphs, not chip soup, in the strip.

**Exit:** no adapter logic in shells. Settings written on Mac show up in
the Linux file shape (and vice versa). Strip + panel + settings all
work with DualUp present *and* missing. Plugin `make validate-plugin`
still passes.

### Phase 4 — Optional extract `lgdualup`

- Repo split only after phase 2 (preferably 3) so the manifest and
  `layout.apply` retries/verify are boring.
- desk-switch without that repo still hops the mouse.

**Exit:** `adapters.dualup.enabled: false` or missing helper = mouse-only
desk. DualUp users install the adapter (bundled or separate) and
`full` / `pbp` work as now.

---

## 11. Risks

- **macOS Input Monitoring.** Grant is on the `mxswitch` *binary path*.
  Reinstall, Rust rewrite, or moving the helper to
  `adapters/mxswitch/mxswitch` means re-grant. Keep the installed path
  stable (`~/.local/lib/desk-switch/mxswitch`). Don't embed HID++ in the
  CLI. `--setup` stays on the helper.
- **Linux hidraw.** `42-logitech-hidpp.rules` + `input` group; DualUp
  needs `43-lg-dualup.rules`. An extracted adapter must ship its udev
  file or hops silently no-op.
- **DualUp cable is on one host.** Typical desk: USB `043e:9a39` in the
  Mac. Linux `full` cannot flip HID. `peer` is layout-only SSH. Core
  must keep that split — don't assume both boxes see the monitor.
- **displayplacer / hyprctl EDID lag.** Helper exit 2 + retries.
  Mac must verify rotation, not trust displayplacer's 0. A generic
  `layout.apply` adapter has to preserve that, or full stays
  `2560x2880` @ 0°.
- **HHKB USB ghost.** Cable in the Mac + BT on Linux ⇒ Mac still
  enumerates a USB collection; watcher never sees "leave".
  `follow_hhkb_usb` treats USB *appearance* as `to here`. Probe errors
  count as *present* so a flaky `hidutil` cannot steal the mouse.
  Sleep/wake clock gap disarms. Don't "simplify" this in Rust.
- **Peer SSH on every status.** 15s bar poll × 2s SSH = jank. `--local`
  and the 20s peer cache stay. A daemon should peek less, not more.
- **GUI PATH.** Menu bar and Omarchy already special-case
  `~/.local/bin`. Don't drop that.
- **Capability leak.** DualUp geometry, VCP dance, and tilted EDID
  names must not become core types named `DualUpMode`. Profiles
  (`full` / `pbp`) + adapter-owned details.
- **Watch in a new language.** Port the disarm / USB-edge / retry loop
  with the tests first. A missed clock-gap = surprise mouse hop after
  lid open.

---

## 12. Open questions

Please argue these in the PR; they gate phase 1–2 shape.

1. **HHKB probe in-core or `keyboard.presence` adapter?** In-core is
   proposed (ghost / ioreg rules). Worth an adapter anyway for a
   non-HHKB follow keyboard?
2. **`desk-switchd` at all?** Socket events vs keep spawning
   `status --json` every 15s. Is the spawn actually annoying, or is
   the dense *title* the only problem?
3. **Who composes the strip — core or shells?** `bar_strip` as
   `{focus, display}` vs shells reading `target_hint` + `dualup_mode`
   only. Core-composed keeps Mac/Omarchy identical; shells-composed
   looks more native.
4. **Settings peer-sync?** Local edit only, or `config sync` of
   channels/inputs over existing `peer` SSH?
5. **Manifest file vs `--desk-switch-manifest` vs both?** Both is
   proposed. Fine to ship file-only for phase 1 and skip argv on C
   helpers.
6. **Scan libdir for extra adapters in phase 1, or config-only?**
   Config-only is less magic. Directory scan is nicer for "I dropped
   a file in".
7. **Layout as its own helper forever, or a capability of the display
   adapter?** Today `lgdualup` + `dualup-layout` are two binaries.
   One adapter with two capabilities is cleaner for extract. Two
   binaries match install/TCC/udev reality.
8. **Hard cutover to Rust, or `desk-switch.py` left as reference?**
   Proposed: same path, Python stays until both machines pass tests,
   then delete.
9. **Extract lgdualup after phase 2 or after settings exist?** After
   the ABI is boring (post-2, ideally post-3).
10. **`bar_label` default:** keep generating the dense string forever
    (compat), or eventually make it match the strip and tell old
    widgets to upgrade?

---

## 13. What this PR is

Markdown only. No adapter code, no tray restyle, no Cargo.toml.

Next PR, if this holds: phase 1 discovery + strip, still Python.
