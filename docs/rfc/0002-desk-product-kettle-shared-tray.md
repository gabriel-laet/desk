# RFC 0002 — desk product, kettle adapter, shared tray / HUD

**Status:** draft (review first, **no implementation in this PR**)
**Extends:** [RFC 0001](0001-rust-core-and-adapters.md) — same pluggability
north star; this RFC adds the product name, the kettle fold, and a
generic slot tray / HUD.
**Desk (reference, not a core assumption):** Mac Studio ↔ Omarchy/Linux,
MX Master + optional LG DualUp + HHKB follow + Escritório Alexa light +
Fellow Stagg EKG Pro on LAN (e.g. `192.168.3.36`)

One product: **desk**. Core orchestrates. Hardware and services plug in
under `adapters/`. One Mac extra and one Omarchy tray paint the same
slots. They do not know kettle or Alexa.

This PR is markdown only. It does **not** rename the GitHub repo, does
**not** delete [gabriel-laet/kettle](https://github.com/gabriel-laet/kettle),
and does **not** move or implement adapters.

### Design principle — pluggability (unchanged)

**Core is orchestration and contract. Anything hardware-shaped plugs in.**

RFC 0001 already said MX / DualUp / HHKB are reference adapters, not
in-core special cases. Alexa already shipped that way (`adapters/alexa`,
role `smarthome`, `desk-switch smarthome …`). Kettle is the same shape:
Fellow LAN HTTP lives under `adapters/kettle/`. Weather is a tray
side-feed, not a device.

```
core     = config · status · watch · to/full/pbp · adapter registry
           · compose `bar_strip` + additive `slots`
adapters = mouse / keyboard.presence / display / smarthome / appliance
shells   = paint slots (composite extra + generic HUD); never speak
           HID, hyprctl, kettle HTTP, or alexacli
```

---

## 1. Goals

- **Product name is `desk`.** Eventual GitHub repo:
  `gabriel-laet/desk-switch` → `gabriel-laet/desk`. Document that
  intent here. **Do not rename the repo in this PR**, and do not
  rename it until the fold is green (see §10).
- **Fold kettle into this repo** as a first-class adapter
  (`adapters/kettle/`). Source of truth today is
  [gabriel-laet/kettle](https://github.com/gabriel-laet/kettle)
  (Rust `kettle` CLI + Mac menu bar + Omarchy plugin). After the fold
  works, the GitHub rename, and Gabriel confirms the Mac install:
  **hard-delete** that repo (remove it; do not archive). **Not in
  this PR.**
- **Alexa stays a first-class device adapter.** Already in-tree:
  `adapters/alexa/`, role `smarthome`, verbs
  `desk-switch smarthome list|status|on|off`. Core still does not
  call Amazon or `alexacli`.
- **One shared tray + one generic HUD** on each OS. Mac:
  one `MenuBarExtra`. Omarchy: one bar plugin. Both paint the same
  `slots` array. Neither hard-codes mug, flame, Echo, or DualUp
  geometry.
- **Keep today's CLI and JSON.** `desk-switch` argv stays (RFC 0001
  freeze). `status --json` **adds** `slots`; it does not rename
  `bar_label`, `bar_strip`, or `adapters.*`.
- **Single install** eventually replaces standalone `Kettle.app` and
  the current desk-switch extras where they overlap. One extra, one
  HUD, one `make install`.

## 2. Non-goals

- Any application code, adapter port, file move, or delete **in this
  PR**. Markdown only.
- Renaming `gabriel-laet/desk-switch` on GitHub in this PR.
- Hard-deleting `gabriel-laet/kettle` in this PR. That delete is
  phase E only, after migrate + rename + a confirmed Mac install.
- Merging kettle PRs **into kettle** as a prerequisite. Pull usable
  bits *into desk* (see §10.4), including open app-icon work on
  kettle PR [#15](https://github.com/gabriel-laet/kettle/pull/15)
  branch `cursor/macos-kettle-app-icon-35ac`.
- Teaching shells kettle HTTP (`GET /cli?cmd=…`), Open-Meteo, or
  `alexacli`. Those stay in adapters / side-feeds.
- Treating weather as a device adapter. It is a tray side-feed
  (today: São Paulo ambient + altitude on the kettle extra).
- Putting Alexa lights in the default strip. `ui.tray.lights` already
  exists; default stays off (RFC 0001 + current core).
- Renaming the CLI to `desk` in this series. `desk-switch` stays the
  taught command (and `hhkb-mx-follow` the alias). A `desk` alias is
  an open later question, not a migrate blocker.
- Unifying Mac vs Omarchy chrome. Shared *model*; native *widgets*
  (RFC 0001).
- Porting kettle's C++ `fellow` / GTK `fellow-tray` as first-class
  desk shells. The Rust CLI + unified extras are the target. `fellow`
  as a temporary shim inside the adapter is allowed if it unblocks
  the fold; it is not the product.
- App Store / notarized Mac app. Ad-hoc sign stays fine.

---

## 3. Locked product decisions

Gabriel locked these. Argue only if they break the 0001 north star.

| Decision | Locked as |
|---|---|
| Product / eventual GitHub name | **`desk`** (`gabriel-laet/desk`) |
| When to rename the repo | **After** the kettle fold is green — not this PR |
| Kettle | Fold into **`adapters/kettle/`**. After migrate + rename `desk-switch` → `desk` + confirmed Mac install: **hard-delete** `gabriel-laet/kettle` (do not archive) |
| Alexa | First-class **device** adapter (already `adapters/alexa`) |
| Weather | Tray **side-feed**, not a device / not `adapters/weather` |
| Tray / HUD | One Mac extra + one Omarchy tray/plugin + one Watch-style HUD. **No** kettle-specific or Alexa-specific chrome |
| This PR | Docs only. No code, no deletes, no GitHub rename |

Personal GitHub (`gabriel-laet`) only.

---

## 4. Current architecture (today, two repos)

### 4.1 This repo (`desk-switch`, after RFC 0001 phase 1 + Alexa)

Python `desk-switch` (v1.5.0) is the product CLI. Reference adapters
already live under `adapters/<id>/`. Shells only call `desk-switch`.

```
desk-switch.py
  ├── mouse      → adapters/mxswitch     (mouse.host_switch)
  ├── keyboard   → adapters/hhkb         (keyboard.presence)
  ├── hosts      → config
  ├── display    → adapters/lgdualup     (legacy alias: adapters.dualup)
  ├── smarthome  → adapters/alexa        (list / status / light on/off)
  ├── watch      → HHKB leave / USB rise
  └── peer       → SSH `desk-switch status --json --local`

shells
  macos/DeskSwitchBar/DeskSwitchBar.swift   MenuBarExtra (Text title today)
  BarWidget.qml + Panel.qml                 Omarchy plugin glaet.desk-switch
```

`status --json` is already the bar/peer contract. Core composes
`bar_strip` (`focus` + optional `display`; optional `lights` if
`ui.tray.lights`). `bar_label` stays the dense compat string
(`LNX  kbU  mx2  PBP`). Nested hardware state lives under
`adapters.*`. **There is no `slots` array yet.**

Alexa is present and correct as an adapter:

| Piece | Today |
|---|---|
| Source | `adapters/alexa/` + `alexa.manifest.json` |
| Role | `smarthome` |
| Capabilities | `smarthome.list`, `smarthome.status`, `light.on`, `light.off` |
| CLI | `desk-switch smarthome list\|status\|on\|off` |
| Auth | out-of-process `alexacli` (`~/.alexa-cli/config.json`, domain `amazon.com`) |
| Desk light | spoken text **only** `acender a luz` / `apagar a luz`, Echo `-d Escritório` |

Core copies adapter JSON into `adapters.smarthome`. Bars stay quiet.

### 4.2 Kettle repo (to fold; do not delete here)

[gabriel-laet/kettle](https://github.com/gabriel-laet/kettle) is a
second product today: unofficial LAN CLI for the Fellow Stagg EKG Pro
plus its own Mac extra and Omarchy plugin.

```
kettle (other repo)
  crates/kettle-core + kettle-cli     Rust `kettle` binary
  macos/Fellow/                       Kettle.app MenuBarExtra + Watch HUD
  apps/omarchy/glaet.fellow           QML chip + neon HUD
  linux/                              Waybar + GTK fellow-tray (fallback)
```

Wire: unauthenticated `GET http://<ip>/cli?cmd=<command>` on the LAN.
Default / documented host on this desk: **`192.168.3.36`**. Discover
exists (`kettle discover --save`) because DHCP moves the kettle.
Commands that matter for the fold: `status`, `heat <temp>`, `off`,
`on`, `bar`, `host`, `discover`.

Kettle's bar JSON (Waybar chip + extras the Mac extra already decodes)
is **not** `desk-switch status --json`. Typical extras:

```json
{
  "text": "65° · 780m",
  "tooltip": "holding  65°C  set 96°C",
  "class": "holding",
  "tempr": 65,
  "temprT": 96,
  "mode": "…",
  "mode_title": "Holding",
  "altitude_m": 780,
  "weather_c": 22,
  "weather_mood": "Rain",
  "host": "192.168.3.36"
}
```

`kettle --weather bar` is how weather got onto that extra. After the
fold, that bundling is a **compat shim**, not the architecture.
Weather stays a side-feed. The kettle adapter reports kettle state.

### 4.3 The MenuBarExtra lesson (kettle PR #14)

Kettle PR [#14](https://github.com/gabriel-laet/kettle/pull/14) fixed
SwiftUI `MenuBarExtra` flattening nested `Image` + `Text` to **one**
SF Symbol (the mug). Weather never appeared until both pairs were
drawn into a **single composite `NSImage`**.

The shared Mac tray in desk **must** learn that. A SwiftUI
`HStack { Image; Text; Image; Text }` label will drop slots. Paint
the extra as one status-item image (or equivalent), and rebuild the
item when the slot set changes.

This RFC does not port that Swift. It records the constraint so the
later shell PR does not rediscover it.

---

## 5. Ownership map

If a file is hardware- or vendor-shaped, it lives under
`adapters/<id>/`. Shells paint. Core composes.

| Piece | Layer | Today | Target | Unchanged? |
|---|---|---|---|---|
| Config, `watch`, `to` / `full` / `pbp`, host map, adapter registry | **core** | `desk-switch.py` (Rust later, RFC 0001 phase 2) | core. Orchestration + contract only. Composes `bar_strip` **and** `slots`. | ownership yes |
| `status --json` | **core** | flat keys + `adapters.*` + `bar_strip` | **add** `slots` (§7). Do not rename existing keys. | additive |
| Weather (ambient °, mood, altitude as tray chrome) | **side-feed** | bundled inside kettle `--weather bar` / Mac `WeatherService` | core (or a tiny non-device helper) publishes a `weather` **slot**. Not `adapters/weather`. Not a kettle capability. | new home |
| MX Master | **adapter** `mxswitch` | `adapters/mxswitch/` | same | yes |
| LG DualUp + layout | **adapter** `lgdualup` | `adapters/lgdualup/` | same. May publish a `dualup` / `display` slot (`label: "PBP"`). | yes |
| HHKB presence | **adapter** `hhkb` | `adapters/hhkb/` | same | yes |
| Hosts / channels | **config** | `adapters.hosts` | same | yes |
| Alexa / Escritório light | **adapter** `alexa` | `adapters/alexa/` | same. Role `smarthome`. No default tray slot. | yes |
| Fellow kettle (temp / mode / heat / off / discover / host) | **adapter** `kettle` | **other repo** `gabriel-laet/kettle` | **`adapters/kettle/`** | **fold** |
| Mac extra + Watch HUD | **shell** | `macos/DeskSwitchBar/` **and** kettle `Kettle.app` | **one** Mac extra + one generic HUD under `shells/macos/` (paths can stay until a later move). Composite `NSImage` extra. | unify |
| Omarchy tray / plugin | **shell** | `glaet.desk-switch` **and** kettle `glaet.fellow` | **one** plugin / tray. Same `slots`. Native Tron/hacker chrome is fine. | unify |

North star reminder: Logitech / LG / HHKB / Amazon / Fellow **source**
lives under `adapters/<id>/`. Third parties drop another adapter
beside them. Shells never gain an `if kettle` / `if alexa`.

### 5.1 Proposed tree (source; install paths can stay)

```
adapters/
  mxswitch/          # mouse reference          (unchanged)
  lgdualup/          # display reference        (unchanged)
  hhkb/              # keyboard.presence        (unchanged)
  alexa/             # smarthome reference      (unchanged)
  kettle/            # appliance reference      (fold from gabriel-laet/kettle)
crates/              # RFC 0001 phase 2 — not this PR
  desk-switch-core/
  desk-switch/
shells/
  macos/             # one extra + generic HUD (composite NSImage)
  omarchy/           # one plugin + generic HUD
```

**This PR does not create `adapters/kettle/` or `shells/`.** Markdown
only. Hold the fold for the migrate series.

---

## 6. Shared tray + generic HUD

### 6.1 One extra, not two products

Today you can run DeskSwitchBar **and** Kettle.app (and on Omarchy,
`glaet.desk-switch` **and** `glaet.fellow`). That is the thing this
RFC ends.

| Surface | Shows | Does not show / do |
|---|---|---|
| **Strip / extra** (always visible) | Composite of selected `slots` (glyphs + short labels). Default set is quiet-ish: weather side-feed, kettle if bound, display mode if known. Focus can stay a slot or stay on `bar_strip`. | Nested SwiftUI `Image+Text` pairs; vendor HTTP; Echo phrases; chip-soup `bar_label` (unless `ui.tray.density: "chips"`) |
| **HUD** (click the extra) | Generic Watch-style face driven by the same `slots` (+ optional per-slot `detail` / actions). Heat / Off / Full / PBP / light are **actions on a slot**, not hardcoded buttons named in the shell. | Kettle-only dial types, Alexa-only rows, DualUp geometry |
| **Settings** (later, RFC 0001 phase 3) | Host, kettle host/discover, Echo device, tray which-slots | HID reports, raw `alexacli` |

`bar_label` and `bar_strip` stay in JSON. New shells prefer `slots`.
Old widgets keep working.

Escape hatches (additive, names are a guess — discard freely):

- `ui.tray.density: "strip" | "chips"` — already exists (`chips` =
  paint `bar_label`).
- `ui.tray.slots: ["weather", "kettle", "dualup"]` — optional pin of
  which slot ids paint in the extra. Default = core's quiet set.
- `ui.tray.lights` — already exists; Alexa stays out unless true.

### 6.2 Shells do not know kettle or Alexa

A shell may map **semantic** `glyph` tokens to SF Symbols / Nerd
Font (RFC 0001 still-open: core emits semantics). That map is a
glyph table (`mug` → `cup.and.saucer`, `flame` → `flame`,
`cloud.rain` → `cloud.rain`), not `if slot.id == "kettle"`.

If a new adapter publishes `{ "id": "espresso", "glyph": "mug",
"label": "90°" }`, the extra and HUD work without a shell change.

Actions the HUD can run go through `desk-switch` verbs the slot
names (`desk-switch kettle heat 93`, `desk-switch smarthome off`,
`desk-switch pbp`). The shell passes argv from the slot / a tiny
generic action list. It does not assemble Fellow HTTP or Alexa
utterances.

### 6.3 Mac extra: composite `NSImage`

Required lesson from kettle PR #14:

1. Do **not** use a `MenuBarExtra` label that is nested
   `Image` + `Text` + `Image` + `Text`. AppKit flattens that to one
   symbol.
2. Draw the visible slots into **one** status-item `NSImage`
   (glyph + label clusters left → right).
3. Identity-rebuild the extra when the slot *set* changes (weather
   arriving after the first poll was a real bug on kettle).
4. Linux / QML is allowed to lay out multiple icons; it still
   consumes the same `slots` JSON.

### 6.4 Omarchy

Same job. Plugin id can stay `glaet.desk-switch` through the fold
(Omarchy already has that checkout). `glaet.fellow` goes away when
the unified plugin paints the kettle slot. Do not require a plugin
rename in the first port PR.

---

## 7. Status / slot contract

**The contract is still one JSON object** (`desk-switch status --json`).
Add keys. Don't rename.

### 7.1 What exists today (keep)

| Key | Role |
|---|---|
| `bar_label` | Dense compat string for old widgets / `ui.tray.density: "chips"` |
| `bar_strip` | Quiet object: `{ "focus": "LNX", "display": "pbp" }` (+ optional `lights`) |
| `adapters.mouse` / `.keyboard` / `.hosts` / `.display` / `.dualup` / `.smarthome` / `.discovered` | Nested hardware / discovery |
| Flat `target_hint`, `hhkb_*`, `mouse_*`, `dualup_*`, `lgdualup` | Compat; tests pin these |

Kettle's Waybar `{text, tooltip, class, tempr, …}` is **not** this
object. After the fold, `kettle bar` may keep emitting that shape as
an adapter-private compat command. Desk shells do not parse it.

### 7.2 Additive `slots` (proposed)

Core composes `slots` from adapter snapshots + the weather
side-feed. Shells only paint.

```json
{
  "slots": [
    {
      "id": "weather",
      "glyph": "cloud.rain",
      "label": "22°",
      "detail": "780m"
    },
    {
      "id": "kettle",
      "glyph": "mug",
      "label": "65°",
      "hot": true,
      "detail": "holding → 96°"
    },
    {
      "id": "dualup",
      "glyph": "display.split",
      "label": "PBP"
    }
  ],
  "bar_strip": { "focus": "LNX", "display": "pbp" },
  "bar_label": "LNX  kbU  mx2  PBP",
  "adapters": {
    "mouse": { "backend": "mxswitch", "available": true },
    "keyboard": { "backend": "hhkb", "transport": "usb" },
    "display": { "backend": "lgdualup", "mode": "pbp" },
    "dualup": { "backend": "lgdualup", "mode": "pbp" },
    "smarthome": { "backend": "alexa", "available": true },
    "kettle": {
      "backend": "kettle",
      "available": true,
      "host": "192.168.3.36",
      "temp_c": 65,
      "target_c": 96,
      "mode": "holding",
      "hot": true
    },
    "discovered": []
  }
}
```

Field sketch (discard / tighten when the first port PR lands):

| Field | Who sets it | Meaning |
|---|---|---|
| `id` | core (stable token) | `weather`, `kettle`, `dualup`, later maybe `focus`, `lights` |
| `glyph` | core, from adapter/side-feed **semantics** | Token, not a codepoint. Shells map to SF Symbols / Nerd Font. Examples: `mug`, `flame`, `cloud.rain`, `display.split`, `display.full` |
| `label` | core | Short extra text (`65°`, `PBP`, `22°`) |
| `detail` | core, optional | HUD / tooltip (`780m`, `holding → 96°`) |
| `hot` | kettle adapter → core, optional | Heating / holding-hot. Extra may swap `mug` → `flame` from this flag **or** from `glyph` already being `flame`. Prefer one source; don't make the shell infer Fellow modes. |
| `actions` | optional, later | Generic `{ "label", "argv" }` list for the HUD. Not required for v1 of the fold if the HUD can call documented verbs from `id`. |

Rules:

- **Add `slots`. Do not replace `bar_strip`.** Quiet focus+display
  stays. Slots are how weather + kettle + display share one extra.
- Missing adapter ⇒ omit that slot. A mouse-only desk still works.
- Alexa / lights: omit from `slots` unless `ui.tray.lights`.
- Weather: omit if the side-feed failed; kettle slot must still
  render (kettle PR #14's "mug-only when weather nil" is correct).
- `adapters.kettle` (when present) holds the rich snapshot for
  `jq` / tests / settings. Shells should not *need* it if `slots`
  is enough to paint.
- Glyph tokens stay semantic. Do not put `cup.and.saucer` in core
  JSON just because AppKit uses that name.

### 7.3 Guess vs fact (invite discard)

**Fact:** `status --json` already exists and is the ABI. Kettle
already emits a *different* bar JSON. Alexa already copies into
`adapters.smarthome`. MenuBarExtra flattens nested Image+Text.

**Guess (ok to throw away):**

- Role name **`appliance`** for kettle (parallel to `smarthome`).
  Alternatives: no new role (kettle only publishes a slot), or
  `heat`. The important part is: core does not import Fellow HTTP.
- Exact `adapters.kettle` keys (`temp_c` / `target_c` / `mode` /
  `hot` / `host`). Refine against kettle-core's snapshot when
  porting; do not invent a second temperature model.
- `ui.tray.slots` pin list.
- Whether `focus` becomes a slot or stays only on `bar_strip`.
- Whether HUD `actions` need to be in v1 JSON or can be a
  shell-side table of `desk-switch` verbs keyed by `id` (that
  second option is a smell — it teaches the shell ids).

---

## 8. Adapter model extensions

RFC 0001 roles stay. One additive role:

| Role | Capabilities (typical) | Reference id | Config |
|---|---|---|---|
| `mouse` | `mouse.host_switch` | `mxswitch` | `adapters.mouse` |
| `keyboard` | `keyboard.presence` | `hhkb` | `adapters.keyboard` |
| `display` | `display.input` / `pbp` / `full`, `layout.apply` | `lgdualup` | `adapters.display` (legacy `dualup`) |
| `smarthome` | `smarthome.list` / `smarthome.status` / `light.on` / `light.off` | `alexa` | `adapters.smarthome` |
| **`appliance`** | **`appliance.status` / `appliance.heat` / `appliance.off`** | **`kettle`** | **`adapters.kettle`** (or `adapters.appliance` + `backend: kettle` — pick one in the port PR; don't ship both) |
| `hosts` | config only | — | `adapters.hosts` |

Kettle capability sketch (map to today's CLI, keep helper argv
private):

| Capability | Kettle CLI today | Desk-facing (proposed) |
|---|---|---|
| `appliance.status` | `kettle status` / `kettle bar` | adapter `info` / `status` JSON |
| `appliance.heat` | `kettle heat <temp>` / `on` | `desk-switch kettle heat 93` (exact verb: lock in the port PR) |
| `appliance.off` | `kettle off` | `desk-switch kettle off` |
| discover / host | `kettle discover --save`, `kettle host` | adapter-owned; config `adapters.kettle.host` |

Manifest (`api_version: 1`), same file-drop rules as RFC 0001:

```json
{
  "api_version": 1,
  "id": "kettle",
  "name": "Fellow Stagg EKG Pro",
  "capabilities": ["appliance.status", "appliance.heat", "appliance.off"]
}
```

Core never names `192.168.3.36`, never `GET /cli`, never ships
Fellow mode enums as core types. LAN security warning (no auth on
port 80) travels with the **adapter**, not the orchestrator.

**Weather is not a kettle capability.** If the first port still
calls `kettle --weather bar` to avoid rewriting Open-Meteo, that is
a temporary bridge. Status composition should still emit a
`weather` slot with `id: "weather"`, not nest weather only under
`adapters.kettle`.

---

## 9. Install and names

`make install` today writes `~/.local/bin/desk-switch` and
`~/.local/lib/desk-switch/<id>`. Keep that. Add
`~/.local/lib/desk-switch/kettle` when the adapter exists.

Single-install target (later PR, not here):

| Before | After (intent) |
|---|---|
| `Kettle.app` (kettle repo) | gone; desk extra shows the kettle slot |
| `DeskSwitchBar.app` | same app (or renamed in a *later* PR) paints `slots` |
| `glaet.fellow` | gone once `glaet.desk-switch` paints the kettle slot |
| `glaet.desk-switch` | stays |
| `fellow` / `kettle` on PATH | adapter binary under libdir; optional `kettle` shim if humans still type it |

Do not rename LaunchAgents / plugin ids in the first port
(`local.desk-switch-bar`, `glaet.desk-switch`). RFC 0001 already
refused drive-by unit renames.

GitHub repo rename `desk-switch` → `desk` is **step 5**, after
green. Clone URLs, Omarchy `plugin add`, and README badges update
then — not in this PR.

---

## 10. Migrate plan (document only)

No calendar. Each step is a later PR series. **This RFC is step 1.**

### Phase A — this PR (docs)

- [x] Ownership map: kettle → `adapters/kettle/`; Alexa stays;
      shared tray/HUD shells; weather is a side-feed.
- [x] Slot contract sketch on top of existing `status --json`.
- [x] Product name `desk` recorded; GitHub rename deferred.
- [ ] **No code.** No `adapters/kettle/`. No deletes. No rename.

**Exit:** RFC accepted or revised. Implementation stays held.

### Phase B — port kettle into the adapter (still this git repo)

- [ ] Copy / port the Rust CLI (or a thin wrapper) into
      `adapters/kettle/`. Manifest + libdir install.
- [ ] `status --json` grows `adapters.kettle` and additive `slots`.
      Golden tests: existing keys unchanged; new keys present when
      the adapter is bound.
- [ ] Mac + Omarchy extras consume **unified** `slots`. Composite
      `NSImage` on Mac (PR #14 lesson). Generic HUD from the same
      array.
- [ ] Weather slot from the side-feed, not from shell-local
      Open-Meteo as the long-term design.
- [ ] Do **not** merge kettle PRs into `gabriel-laet/kettle` for
      this. Read kettle `main` (and the icon branch below) and
      bring files here.

**Exit:** `desk-switch status --json` shows a kettle slot on a LAN
with the Stagg; heat/off work via desk-switch; bars do not import
kettle-core.

### Phase C — single install

- [ ] `make install` / `make install-menubar` replaces standalone
      `Kettle.app` and the old dual extras where applicable.
- [ ] Omarchy: one plugin. Document uninstall of `glaet.fellow`.
- [ ] PATH: `kettle` may shim to the adapter for a release; humans
      learn `desk-switch`.

**Exit:** one extra on the Mac, one Omarchy widget, both showing
weather + mug + DualUp from `slots`. Old `bar_label` consumers
still work.

### Phase D — pull usable kettle assets (into desk)

- [ ] Icons, HUD chrome notes, tests that encode the MenuBarExtra
      flattening bug, discover/host behavior, LAN security warning.
- [ ] **App icon:** pull usable work from kettle PR
      [#15](https://github.com/gabriel-laet/kettle/pull/15)
      (`cursor/macos-kettle-app-icon-35ac`) into desk. Do not wait
      on merging #15 into kettle. Do not copy Fellow trademarks;
      that PR's mark is an original mug, which is the right idea.
- [ ] Leave C++ `fellow` / GTK tray behind unless something still
      needs them as a bridge.

**Exit:** desk has the assets it will actually ship. Kettle repo is
no longer the place you edit the extra.

### Phase E — after green: rename desk, then hard-delete kettle

Order is locked. Do not skip ahead.

1. Migrate is done (B–D green): kettle adapter + shared tray/HUD
   work; Gabriel has confirmed the Mac install.
2. Rename GitHub `gabriel-laet/desk-switch` → `gabriel-laet/desk`.
3. **Hard-delete** `gabriel-laet/kettle` — remove the GitHub repo.
   Do **not** archive it.

- [ ] B–D green on this Mac and the Omarchy box; Gabriel confirms
      the Mac install.
- [ ] Rename GitHub `gabriel-laet/desk-switch` → `gabriel-laet/desk`.
- [ ] Hard-delete `https://github.com/gabriel-laet/kettle` (remove
      the repo; not archive).
- [ ] Update clone URLs, Omarchy plugin add, README. CLI name can
      stay `desk-switch`.

**Exit:** one repo (`desk`), kettle GitHub repo gone. This RFC does
not perform E and does not delete anything.

---

## 11. Compatibility

| Thing | Promise through the fold |
|---|---|
| `desk-switch to\|full\|pbp\|watch\|status` | Unchanged argv (RFC 0001) |
| `desk-switch smarthome …` | Unchanged |
| `status --json` keys listed in §7.1 | Stay. `slots` is additive |
| `adapters.dualup` | Still an alias of `display` |
| `hhkb-mx-follow` | Still the same program |
| Plugin id `glaet.desk-switch` | Stay through the first port |
| LaunchAgent / systemd names | Stay |
| `kettle` / `fellow` CLI | Adapter-private; optional shim |
| GitHub name `desk-switch` | Stay until phase E |

---

## 12. Risks

- **Two extras during the fold.** Shipping slots in desk while
  `Kettle.app` still runs = two mugs. Phase C must be explicit
  about which extra to keep. Don't leave both enabled by default.
- **MenuBarExtra flattening.** If the shared Mac extra uses nested
  SwiftUI images, weather (or DualUp) disappears again. Composite
  `NSImage` is not optional.
- **Weather-as-device creep.** Easy to hang `weather_c` only on
  `adapters.kettle` because that is how `kettle --weather bar`
  works today. Resist. Slot `id: "weather"` or we cannot add a
  second extra later without lying.
- **Kettle LAN has no auth.** Anyone on the LAN can heat/off.
  Document on the adapter. Do not expose port 80. Core should not
  grow a Fellow HTTP client "to make status faster".
- **DHCP.** `192.168.3.36` is an example, not a core constant.
  Discover + `~/.config/kettle/host` (or
  `~/.config/desk-switch/` equivalent) stay adapter-owned.
- **Alexa utterances.** Already locked in `adapters/alexa`. Shells
  must not invent `acender a luz`. HUD actions call
  `desk-switch smarthome on|off`.
- **Omarchy plugin add URL.** Repo rename (phase E) breaks
  documented `omarchy plugin add https://github.com/gabriel-laet/desk-switch.git`
  until GitHub redirects settle. Do E only after install docs are
  ready to change.
- **RFC 0001 phase 2 (Rust core) overlap.** An open Rust-core PR
  must not bake Fellow types into `desk-switch-core`. Kettle stays
  an out-of-process adapter even if core is Rust.
- **Icon / trademark.** Pull the original mug mark from kettle
  PR #15. Do not copy Fellow product art into desk.

---

## 13. Resolved for this RFC

1. **Product name is `desk`.** Repo rename is phase E, not this PR.
2. **Kettle folds here** as `adapters/kettle/`. After migrate +
   rename + confirmed Mac install, **hard-delete**
   `gabriel-laet/kettle` (do not archive). Not this PR.
3. **Alexa is already the smarthome reference.** This RFC does not
   relocate it.
4. **One tray + one generic HUD.** Shells paint `slots`. They do
   not know Fellow or Alexa.
5. **Weather is a side-feed slot**, not a device adapter.
6. **`slots` is additive** on `status --json`. `bar_strip` /
   `bar_label` / `adapters.*` stay.
7. **Mac extra composites one `NSImage`** (kettle PR #14).
8. **Do not merge work into kettle** to prepare the fold. Pull
   into desk, including PR #15 icon work.
9. **This PR is markdown only.**

### Still open (do not block accepting the RFC)

- Exact appliance role / config key (`adapters.kettle` vs
  `adapters.appliance.backend`).
- Exact `desk-switch kettle …` argv vs reusing a generic
  `desk-switch appliance …`.
- Whether `focus` is a slot.
- HUD `actions` in JSON vs a later pass.
- `desk` as a CLI alias after the GitHub rename.
- App bundle display name (`Desk` vs `DeskSwitchBar`) — cosmetic,
  after the extra actually paints slots.

---

## 14. What this PR is

Markdown only.

- Adds this RFC.
- Points RFC 0001 and the README at it.

No adapter code, no file moves, no tray restyle, no Cargo.toml, no
GitHub rename, no kettle delete (hard-delete is phase E only).

**Hold the fold** until this RFC is accepted.

Next PR, if this holds: phase B — `adapters/kettle/` + additive
`slots` in `status --json`, still no repo rename.
