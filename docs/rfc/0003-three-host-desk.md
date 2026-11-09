# RFC 0003 — N-host desk, relay, and a second display

**Status:** draft. Docs only. No adapter code, no config migration,
no tray change in this PR.
**Extends:** [RFC 0001](0001-rust-core-and-adapters.md) (adapter
contract; core never assumes a vendor) and
[RFC 0002](0002-desk-product-kettle-shared-tray.md) (product name
`desk`, shared slots). Taught CLI is **`desk`**.
**Desk (reference, not a core assumption):** three hosts, ids
`mac`, `ser9`, and `tower`. Reference devices stay what they are
in RFC 0001: a mouse adapter, a keyboard-presence adapter, and a
display adapter. Those names are not core types.

The desk grew from two hosts to N. Two of the hosts are Linux and
unlock their disks with LUKS, which needs a keyboard before `desk`
is running. The orchestrator has to hop a mouse and a monitor from
whichever machine currently owns the hardware, and the shells have
to show where things are when that hop does not happen.

### Design principle — pluggability (unchanged)

**Core is orchestration and contract. Anything hardware-shaped plugs in.**

RFC 0001 deferred a third host until one existed. This is that desk.
Core still must not assume Logitech, LG, or HHKB: no vendor id, no
DualUp geometry, no `if lgdualup` / `if mxswitch`, no ChangeHost
feature index in the orchestrator. The reference mouse adapter is
the component that knows how to send a host change, and only from
the machine the mouse is on. The reference display adapter is the
component that knows the monitor's control HID. A DDC adapter is
the component that knows `ddcutil` and `m1ddc`. Core asks for
`mouse.host_switch` and `display.input`.

```
core     = config · status · watch · to/full/pbp · relay
           · adapter registry · compose bar_strip + slots + health
adapters = mouse / keyboard.presence / display (one or more)
           · smarthome / kettle / weather
shells   = paint where devices are, hop buttons, health
           never speak HID, DDC, hyprctl, or SSH
```

---

## 1. Goals

- **N hosts in one map.** Reference ids: `mac`, `ser9`, `tower`.
  Each entry has a mouse channel, a keyboard slot, the primary
  display input, an optional SSH peer, and which display upstream
  (if any) that host's cable holds. Two-host files keep loading.
- **Ownership.** Only the host that currently has the mouse may
  switch it. Only the host that currently holds a display's
  control path may switch that display's input. `desk to <host>`
  from any machine does the local part, then asks a peer to do
  the rest.
- **Arrival watch.** The host where the keyboard shows up runs
  `desk to` itself (relayed). Leaving a host does not push the
  mouse at a single `follow_channel`. The USB-appear rule stays.
- **Displays are a list.** The reference HID adapter stays. A DDC
  adapter is the fallback for input switching. OS layout stays
  local and does not need USB. The second, smaller monitor is its
  own list entry.
- **Shells stay, on a shorter job.** Where each device is, one
  click to hop, and a warning when the desk is broken. Weather,
  kettle, and lights are opt-in and off by default.
- **Cabling and boot unlock are written down.** Cable moves are
  a checklist for this desk. TPM unlock is ops, outside core.

## 2. Non-goals

- Any code, config rewrite, or tray change **in this PR**.
  Markdown only.
- Teaching core a vendor, a VID/PID, VCP codes, `ddcutil`,
  `m1ddc`, `displayplacer`, or `hyprctl`. Those belong to adapters.
- Windows. RFC 0001 still holds there.
- **Deskflow or any software KVM.** Wayland capture is not mature
  enough to be the hop. A software KVM is not there at LUKS or at
  the login screen, and it repeats the hardware switch this desk
  already has (monitor input + mouse channel). Clipboard sync is
  the piece worth a later look. It is not this RFC.
- Treating an optional single-host dock as a desk citizen. A dock
  may sit on the monitor's downstream ports, on its own power
  supply, for peripherals. Core does not configure it. The dock
  is optional. The webcam does not need one (`mac` has a free
  port; see §9).
- Running `desk` inside the initramfs. Unlock policy is ops (§11).
  Relay runs between booted hosts.
- Peer-sync of the whole config file. Each machine keeps its own
  JSON. `this_host` differs. Shared fields are edited to match,
  as in RFC 0001.
- Deleting the macOS extra or the Omarchy widget in the migration
  PRs. Removal is a later checkpoint (§8), and only if the
  narrowed shells are not the daily UI.

---

## 3. Current architecture (two hosts)

Python `desk` is the product. The host set is closed: `normalize_host`
accepts `mac` and `linux` (plus aliases such as `macos` and
`omarchy`). `desk to mac` and `desk to linux` read
`hosts.<id>.channel`. `watch` does not.

```
adapters.hosts
  this_host            mac | linux
  follow_channel       one Easy-Switch slot (legacy target_channel)
  follow_hhkb_usb      USB rising edge → desk to this host
  peer                 one SSH destination (status peek)
  mac / linux          { channel }

watch (poll 0.5s × 4 ≈ 2s)
  keyboard absent long enough → mouse.host_switch(follow_channel)
  keyboard USB appears        → desk to this_host
  leave does not touch the display

desk to <host>         runs on the machine you typed it on
  mouse switch         fails unless that machine has the mouse
  display input        fails unless that machine has the control HID
  layout.apply         local (displayplacer / hyprctl); no USB
```

`status` can SSH one peer (`desk status --json --local` on the far
side, `ConnectTimeout=2`, command budget about 6s, short cache).
That peek fills a hint when the mouse is asleep here. It does not
hop.

Shells paint a two-host desk. The Mac extra and the Omarchy panel
each have "to mac" and "to linux", plus full / PBP for the
reference display. The default extra also paints weather and kettle
slots ([RFC 0002](0002-desk-product-kettle-shared-tray.md)). Lights
stay off unless `ui.tray.lights` is set.

The in-repo example still maps `linux` → DisplayPort and `mac` →
HDMI1. On the reference desk the DisplayPort machine is `ser9`.
The tower is already on an HDMI cable and is not in the map. The
monitor's USB-B upstream is still in `mac`.

A two-host file must keep working through every phase below.
`follow_channel`, `channel`, `adapters.dualup`, and a single
`peer` string stay legal.

---

## 4. Host map

Hosts stay a **map** keyed by id. Today's files are already a map
(`mac`, `linux`), so legacy keys load with no translation. A list
of `{id, ...}` would break that. Button order, when the shell
needs one, is an optional `order` array. Default order is the
order of the keys.

Display **inputs for the primary panel** live on the host, next to
the channel, as the one-monitor case. A second panel carries its
own `inputs` map (§7, §10) and does not read the host-level
`input`.

```json
{
  "adapters": {
    "hosts": {
      "this_host": "mac",
      "watch": "arrive",
      "order": ["mac", "ser9", "tower"],
      "follow_hhkb_usb": true,
      "mac": {
        "mouse_channel": 1,
        "keyboard_slot": 1,
        "input": "hdmi1",
        "upstream": null
      },
      "ser9": {
        "mouse_channel": 2,
        "keyboard_slot": 2,
        "input": "dp",
        "upstream": "usb-b",
        "peer": "ser9"
      },
      "tower": {
        "mouse_channel": 3,
        "keyboard_slot": 3,
        "input": "hdmi2",
        "upstream": "usb-c",
        "peer": "tower"
      }
    }
  }
}
```

| Field | Meaning |
|---|---|
| `mouse_channel` | Opaque slot the **mouse** adapter accepts. Legacy key: `channel`. |
| `keyboard_slot` | Opaque slot the **keyboard** adapter accepts. Reference pairing is 1 / 2 / 3. Core does not know how the keyboard selects a slot. |
| `input` | Primary display input for `desk to <this host>`. Empty means leave that input alone. Legacy: `adapters.dualup.inputs.<host>` / `adapters.display.inputs.<host>`. |
| `upstream` | Which control-USB upstream this host's cable holds, or `null`. Opaque token (`usb-b`, `usb-c`). A hint for who to try first. The live probe still decides ownership. |
| `peer` | SSH destination for relay and status. A config alias is enough. No address belongs in the repo or in this RFC. |
| `hint` | Optional short strip token. `mac` → `MAC`, `linux` → `LNX` stay. Other ids use `hint` or the id itself. |
| `watch` | `arrive` (this RFC's policy) or omit on a legacy two-host file to keep leave → `follow_channel`. |
| `follow_channel` | Still loaded. Used only by the legacy leave-push watcher. |
| `follow_hhkb_usb` | Still loaded. USB appearance is still `desk to` this host. |

`peer` on the host entry replaces the single `adapters.hosts.peer`
string once any host has its own `peer`. Until then, that string
remains the other machine of a two-host file (today's status peek).

Host ids are config strings. `mac` and `linux` stay valid, including
today's aliases. `ser9` and `tower` are reference ids, not
constants in core, and `linux` is not silently rewritten to either
of them. A map that contains `ser9` or `tower` is the N-host map;
core does not also invent a `linux` entry beside them.

`this_host` is still per machine. The other fields are the same
text on each box. v1 still has no config sync.

Channels in one map are unique. Two hosts on mouse channel 2 is a
config error at load, not a surprise hop.

`status --json` **adds** a host list and does not rename
`target_hint`, `mouse_channel`, `bar_label`, or `bar_strip`.
`target_hint` for `mac` / `linux` stays `MAC` / `LNX`. For any
other focused host it is `hint` or the id. Old widgets that only
understand the two tokens keep working on a two-host file.

---

## 5. Ownership and relay

Two facts from the hardware, enforced by whoever owns the device:

1. **Mouse.** The reference mouse adapter can send a host change
   only while the mouse is connected to *this* computer. Core's
   rule, with no vendor in it: call `mouse.host_switch` only on
   the host whose mouse adapter currently reports the radio.
2. **Display input.** Call `display.input` only on the host whose
   display adapter currently reports that instance's control path
   (control HID for the reference adapter, a DDC bus for the DDC
   adapter). Holding `upstream: "usb-b"` in config is the cable
   plan, not proof the HID is enumerated right now.

OS layout (`layout.apply`) is neither of those. `displayplacer` /
`hyprctl` run on the host whose desktop must change. They do not
need the monitor's USB. That split already exists; relay must not
collapse it.

### 5.1 `desk to <host>`

From whichever machine you typed it on:

1. Resolve the id in the host map.
2. **Local.** If this host owns the mouse, switch it to the
   target's `mouse_channel`. For each display instance that has
   an input for the target, if this host owns that instance's
   control path, switch it. If this host *is* the target, apply
   layout here for instances that implement `layout.apply`.
3. **Relay.** Anything still wanted goes to peers. Prefer the
   peer that config or the last fresh status says owns that
   device. If the owner is unknown, ask the other peers.
4. A peer runs the relay command and returns. It does not relay
   again.

`desk to <host> --mouse-only` stays mouse-only. `desk switch <n>`
stays a raw channel on the local mouse adapter and does not relay.

A display with no input configured for the target is a skip, exit
success (the side panel on `desk to tower`, §10). A skip is not
a health failure.

### 5.2 `desk relay`

Internal. Humans keep typing `desk to`. SSH (and tests) call
`desk relay`. Same JSON body later if a small LAN call replaces
SSH; the first implementation is SSH, which status peek already
uses. Batch mode, so a relay never waits on a password prompt.

Request (stdin JSON):

```json
{
  "op": "to",
  "host": "ser9",
  "id": "c4c0e1e2-0c0a-4c0e-8c0e-000000000003",
  "ttl": 1
}
```

`ttl` is 1 on the way out. The receiver refuses to relay onward.
A missing `ttl`, or `ttl` already consumed, is a local-only run.

Response:

```json
{
  "id": "c4c0e1e2-0c0a-4c0e-8c0e-000000000003",
  "this_host": "mac",
  "results": [
    {"device": "mouse", "result": "not_owner"},
    {"device": "display", "id": "main", "result": "already"},
    {"device": "display", "id": "side", "result": "skipped"}
  ]
}
```

| `result` | Meaning |
|---|---|
| `switched` | This host owned the device and changed it. |
| `already` | This host owned it and it was already on the target. No second write. |
| `not_owner` | This host does not hold the device. Sender tries another peer. |
| `skipped` | No input (or no action) configured for this target. Success. |
| `failed` | This host owned it and the adapter returned an error. |

`desk to` exits 0 when every wanted device ended `switched`,
`already`, or `skipped`. It exits non-zero when an owner reported
`failed`, or when a device that had an action never found an
owner. The line it prints is the same sentence the shell health
mark is for ("mouse owner unreachable", "display `main` has no
owner").

### 5.3 Timeouts and idempotency

| Limit | Value |
|---|---|
| SSH connect | 2s (same as today's peer peek) |
| Remote `desk relay` | 8s |
| Fan-out | Parallel. One dead peer must not add its timeout onto a hop this host can finish locally. |
| Duplicate `id` | 5s. The same id returns the stored result and does not send another host change. |
| Status cache | Not a hop cache. A 20s-old peek may choose who to ask first. It must not skip the write. |

Idempotency is the owner's read: mouse channel already the target,
display input already the target. The second `desk to ser9` is a
success with `already`. Watch arriving on a host that is already
focused must not bounce the mouse or re-run PBP.

Layout runs on the target host when the input actually changed, and
when someone runs `desk layout`. An `already` hop does not re-apply
layout. EDID retries stay inside the display adapter, as in RFC 0001.

### 5.4 Picture

```
desk to ser9                         (typed on mac)
    │
    ├─ mouse: mac does not own the radio
    │         relay → the host that does
    ├─ main display: mac holds no control USB
    │         relay → whoever enumerates it
    │         (config tries ser9 / usb-b first)
    ├─ side display: ser9 has an input configured
    │         DDC from whichever linked host sees the bus
    └─ layout: on ser9, once ser9 is the machine running it
              (displayplacer / hyprctl; no USB)

keyboard arrives on tower
    │
    ▼
desk to tower                        (tower's watcher)
    ├─ relay mouse → tower's mouse_channel
    ├─ relay main input → hdmi2
    └─ side display has no tower input → skipped
```

---

## 6. Watch

Approved policy (`watch: "arrive"`):

| Edge | Action |
|---|---|
| Keyboard **arrives** here (presence rising edge) | `desk to <this_host>` (relayed if this host does not own the device) |
| Keyboard **USB appears** here | same — `desk to <this_host>` |
| Keyboard **leaves** here | nothing. Do not push the mouse to `follow_channel`. |

The USB rule stays the one RFC 0001 shipped: a rising edge, the
startup sample does not fire, a clock gap (sleep/wake) disarms
until the keyboard is seen again, and a probe error counts as
present so a flaky read cannot look like a leave or a fresh
arrival. Bluetooth arrival uses that same edge, not a level. A
USB collection that stays enumerated must not fire on every poll.
Ghost rules stay in the keyboard adapter.

Why arrival, not leave: with three hosts, "the keyboard left" does
not say which host it went to. The host that sees it arrive does.
That host often does **not** own the mouse yet, so `desk to self`
has to relay the host-change back to the machine the mouse is
still on. Leave-push and arrival must not both be armed, or the
mouse hops twice. Idempotency makes a leftover double a no-op;
the config should still arm only one policy.

**Legacy two-host files.** A map that is still just `mac` and
`linux`, with `follow_channel`, and without `watch: "arrive"`,
keeps today's watcher: leave pushes the mouse to `follow_channel`,
USB appearance runs `desk to` this host, leave still does not
touch the display. Adding `ser9` / `tower`, or setting
`watch: "arrive"`, selects the new policy. `follow_channel` still
parses either way. It is not read when the policy is `arrive`.

`desk watch` records a heartbeat (`~/.cache/desk/watch-heartbeat`,
beside the other cache files). `status` reports the watcher down
when the stamp is older than about 15s. Peer status includes that
bit so another host's panel can show it (§8). The heartbeat is
not a hop.

Watch and relay do not run at the LUKS prompt. Getting a keyboard
there is §11.

---

## 7. Displays

RFC 0001 binds one adapter per role. **Display becomes a list of
instances.** Mouse and keyboard stay one binding each. Each
instance has one primary backend and an optional fallback backend.
Core loops the list. It does not special-case a backend id.

```json
{
  "adapters": {
    "displays": [
      {
        "id": "main",
        "backend": "lgdualup",
        "fallback": "ddc",
        "inputs_from": "hosts"
      },
      {
        "id": "side",
        "backend": "ddc",
        "inputs": {
          "mac": "",
          "ser9": ""
        }
      }
    ]
  }
}
```

`inputs_from: "hosts"` means the primary panel uses each host's
`input` field (§4). The side instance has its own map (§10). An
empty or missing name is `skipped`.

Legacy `adapters.display` and `adapters.dualup` (one object) load
as a single instance, id `main`, backend unchanged. Flat
`dualup_*` status keys and `adapters.dualup` stay, filled from
`main`, so old widgets and tests keep their keys. `adapters.displays`
is additive.

| Piece | Where it lives |
|---|---|
| Reference control HID, PBP, full | `adapters/lgdualup/` — `display.input` / `display.pbp` / `display.full` |
| DDC input fallback | new `adapters/ddc/` — `display.input` only. Linux: `ddcutil`. macOS: `m1ddc`. |
| OS layout | the display instance that has geometry to apply (reference: `dualup-layout`). Local process. No USB. |
| VCP, I2C, udev for the DDC bus | the DDC adapter, the way hidraw rules travel with the mouse adapter |

Switch path for one instance: ask the primary backend, on a host
where it reports the control path. If it does not, ask `fallback`
the same way (local, then relay). If both can see a path, the
primary wins, so one hop does not write the input twice.
`desk full` / `desk pbp` go to the instance that advertises those
capabilities (reference: `main`). The DDC adapter does not
implement them and does not grow a DualUp geometry table.

The DDC adapter maps a configured input name to whatever the
display answers. VCP `0x60` (input select) is the code to verify
on hardware (§14). It is not a core constant. Until that check
passes, a DDC instance with empty input names skips.

---

## 8. Shells — keep, and narrow them

**Decision: keep** the macOS menu bar (`DeskSwitchBar`) and the
Omarchy widget (`BarWidget.qml` / `Panel.qml`). Narrow them to
three jobs. This amends the RFC 0002 default, where the extra's
purpose is the weather + kettle + display composite. The slot
contract stays. Membership of the default surface changes.

### 8.1 The three jobs

1. **Where things are.** Per host: keyboard here or not (and USB
   vs Bluetooth, as the keyboard adapter already reports), mouse
   here or not (channel → host), and each display instance's
   current input. Unknown stays unknown when no owner can read it.
2. **One click to hop.** One control per host, running
   `desk to <id>`, for N hosts. Plus full and PBP when `main`
   advertises them. Reference copy may say DualUp when that
   backend is bound; the shell still only runs `desk full` /
   `desk pbp` (RFC 0001). Shortcuts stay: **⌘⌥⇧F** full,
   **⌘⌥⇧P** PBP, **⌘⌥U** layout.
3. **Why it did not switch.** One health mark, plain language in
   the panel, when any of these is true: the watcher heartbeat
   is stale on this host or a peer, a peer did not answer SSH,
   or an enabled adapter (including a display instance) is
   missing. The mark is the glance. The panel names the host
   and the code.

```json
"health": {
  "ok": false,
  "issues": [
    {"code": "watch_down", "host": "tower"},
    {"code": "peer_unreachable", "host": "ser9"},
    {"code": "adapter_missing", "role": "display", "id": "side"}
  ]
}
```

`health` is additive on `status --json`. Shells paint it. They
do not invent a second probe.

A side panel that correctly stays put on `desk to tower` is not
an issue. The location rows show that its input did not move.

### 8.2 What gets cut from the default surface

Off unless the operator opts in. Not part of the core purpose:

| Cut from the default | Stays available as |
|---|---|
| Weather chip, altitude, Open-Meteo | `ui.tray.slots` id `weather`, `enabled: false` |
| Kettle face, heat, off | `ui.tray.slots` id `kettle`, `enabled: false` |
| Lights on/off | already off unless `ui.tray.lights` (unchanged) |
| Hardcoded "Switch to Mac" / "Switch to Linux" | one button per host id from config. A legacy file still shows `mac` and `linux`. |

`adapters/weather`, `adapters/kettle`, and `adapters/alexa` stay
in the tree. CLI verbs stay (`desk weather`, `desk kettle`,
`desk smarthome`). The configure-tray UI can still reorder slots;
the stock list ships with the ambient ones disabled. Default
painted strip is focus + optional main display mode + the health
mark when `health.ok` is false. `docs/ui-config.md` changes in
the shell PR, not in this one.

### 8.3 Decision rule

Ship the narrowed shells with the N-host migration and use them
for the three jobs above. **If, after that migration, they are
not how those jobs get done, remove them.** Keep the `desk` CLI
and the keyboard shortcuts (and the Omarchy menu rows, which
grow one row per host in the same series). Do not delete the
extra in the PR that first draws N buttons. Do not add usage
tracking to make the call; the person at the desk can see
whether they open the panel.

---

## 9. Reference cabling

This section is the reference desk's cable plan. It is not a core
model. Host ids are `mac`, `ser9`, `tower`. Ports are the
monitor's own.

The monitor is the reference display with a built-in KVM: video
inputs, USB upstreams paired to those inputs, and a downstream
USB hub that follows the active input. Keyboard and mouse stay
on Bluetooth for the daily hop (slots / channels 1, 2, 3). USB
on the keyboard is the unlock fallback, not the path `watch`
uses for a normal Bluetooth arrival.

### 9.1 Current (port by port)

| # | What | From | To |
|---|---|---|---|
| 1 | Video | `ser9` DisplayPort | monitor DP |
| 2 | Video | `mac` HDMI | monitor HDMI |
| 3 | Video | `tower` HDMI | monitor HDMI |
| 4 | USB upstream | `mac` USB | monitor USB-B upstream |
| 5 | Downstream hub | monitor hub | USB webcam, used only by `mac` |

Checked from `mac`, which is the machine that currently holds
USB-B: that downstream hub enumerates the webcam and the
monitor's own control HID, and nothing else. That inventory
does not answer whether an inactive upstream also enumerates
the HID (§14). It is what the hub shows for the host that has
it today.

`mac` can drive the reference control HID because USB-B is
plugged in there. `ser9` and `tower` have video only.

### 9.2 Target checklist

Video for `ser9` stays on DP. Do this in one sitting **after**
relay can switch an input from the host that holds the control
path (§12). Moving USB-B first, with no relay, strands input
switching on a machine that no longer has the HID.

1. **MOVE** the USB-B upstream cable from `mac` to `ser9`.
   USB-A on the host, USB-B on the monitor.
2. **ADD** a data cable from `tower` to the monitor's USB-C
   upstream. Prefer USB-A on the host to USB-C on the monitor,
   so the cable carries data only. That USB-C input also speaks
   DisplayPort Alt Mode and power delivery; a full USB-C cable
   can become a second video link or a power contract beside
   the HDMI cable that is already the tower's picture.
3. **VERIFY** `mac` is on HDMI1 and `tower` is on HDMI2. If they
   are swapped, change `input` in config. Leave the HDMI cables
   where they are.
4. **OSD.** Pair upstreams with inputs: DP → USB-B, HDMI2 →
   USB-C, HDMI1 → no upstream.
5. **ADD** the keyboard's USB cable to a downstream USB-A port
   on the monitor. Unlock fallback (§11). It follows the active
   input.
6. **MOVE** the webcam off the monitor's downstream hub onto a
   USB port on `mac` itself. `mac` is about to lose USB-B, and
   then it never sees that hub. A webcam left on the hub stops
   being `mac`'s camera the moment the upstream cable moves, and
   it would follow whichever input is active after that.
7. **Optional.** A single-host dock on the monitor downstream,
   self-powered, peripherals only. Not required for the webcam.
   `mac` has the ports. Leave the dock out unless some other
   peripheral needs it.

### 9.3 Where a device belongs

A device that only one host uses plugs into **that host**. The
monitor hub follows the active input, so it is the right place
for a device that should follow focus. The keyboard's USB
fallback is that kind of device. The webcam is not: only `mac`
uses it, so it plugs into `mac`.

The monitor's control HID stays with the monitor. It is not a
cable we move in step 6. After the checklist, the downstream hub
is the keyboard fallback plus that control HID, and the optional
dock only if step 7 was actually done.

### 9.4 Target pairing

| Host | Keyboard slot | Mouse channel | Primary input | Holds upstream |
|---|---|---|---|---|
| `mac` | 1 | 1 | HDMI1 | none |
| `ser9` | 2 | 2 | DP | USB-B |
| `tower` | 3 | 3 | HDMI2 | USB-C |

Bluetooth for the keyboard and the mouse on those slots and
channels. Names of the reference adapters stay in the adapters.
Core stores the integers and the input tokens.

---

## 10. Second, smaller display

A second, smaller monitor is already on the desk. It is wired to
`ser9` and `mac` only. `tower` has no cable to it.

**Plan.** Keep those two inputs. Do not run a cable from `tower`
unless `tower` has a free output **and** the display has a free
input. Until both are true, tower stays unwired.

The panel gets its own display instance, backend `ddc` (§7).
No control-HID adapter is assumed for it.

| Command | Side instance |
|---|---|
| `desk to mac` | Switch to the mac input, once that name is known. |
| `desk to ser9` | Switch to the ser9 input, once that name is known. |
| `desk to tower` | Leave it alone. |

"Leave it alone" is the default because tower has no input on
this panel, not because tower is special in core. Set
`inputs.tower` and a later cable will make `desk to tower`
switch it. Clear the name again to restore the skip. Per-host
configuration is the whole mechanism.

Input names are **not** filled in by this RFC. The first probe
did not see the panel in `ddcutil` or `m1ddc` (§14). Empty
strings stay in the example so the wiring is documented and
`desk to` skips the instance instead of sending a guess.

OS layout for this panel is local and needs no USB, the same
rule as the main panel. This RFC does not invent a resolution
or a rotation for it. No `layout.apply` until someone has
geometry worth applying, on the host that owns the desktop.

---

## 11. Boot unlock (ops, out of core)

`ser9` and `tower` use LUKS. The unlock prompt needs a keyboard.
`desk watch` is not running there, SSH relay is not running
there, and Bluetooth from the login-time keyboard adapter is
not up yet. Core does not grow an initramfs mode.

**Preferred: TPM2 auto-unlock.** Enroll with
`systemd-cryptenroll --tpm2-device=auto` on the LUKS volume, and
let `sd-encrypt` unlock in the initramfs. **Keep the passphrase
slot.** The enroll adds a slot. Do not wipe the passphrase slot
to do it.

Physical-access trade-off, stated so it is a choice and not a
surprise: anyone who can power on that machine gets the volume
without the passphrase, for whatever PCR set the enrollment
bound. That is the cost of a boot that does not wait for a
keyboard. A firmware or bootloader change that breaks the
prediction falls back to the passphrase, which is why the slot
stays. The passphrase is also what you still have if the
machine should not unlock just because it was turned on.

**Fallback: keyboard USB in the monitor's downstream** (§9 step
5). The KVM presents that port to the active upstream only.
Leave the input on the host you are about to boot (or switch it
while a booted host can still run `desk to`). The keyboard is
then a USB keyboard at the LUKS prompt of that host. It does
not follow a host that is not the selected input.

Neither path is a `desk` verb. Do not add a helper script in
this repo that enrolls TPM or rewrites crypttab.

---

## 12. Migration

Small PRs. Each one leaves `desk to mac` / `desk to linux` and
the legacy watcher working on a two-host file. **This RFC is
step 0.** Cable moves are last, and they are not a code PR.

### Phase 1 — host map loads N ids

- Accept `ser9`, `tower`, and any other id. Keep the `mac` /
  `linux` alias table for those two names only.
- Read `mouse_channel` and legacy `channel`. Read `keyboard_slot`,
  `input`, `upstream`, per-host `peer`.
- `desk to <id>` is still local-only.
- Status lists the map. Old JSON keys stay.

**Exit:** a three-host file loads; a two-host file behaves as now.

### Phase 2 — ownership on status

- Additive: this host owns the mouse or not; each display
  instance owns its control path here or not.
- The probe is the adapter's `available` / ownership report.
  Core does not open hidraw or I2C itself.

**Exit:** `status --json` on each host shows who can switch what,
without switching.

### Phase 3 — relay

- `desk relay` and `desk to` as in §5. Timeouts, `ttl`, request
  id, idempotent `already`.
- Tests with a fake peer. No real addresses in fixtures.
- Legacy single `peer` string still does today's status peek.

**Exit:** from a host that does not own the mouse, `desk to` the
owner's target still hops, and a second run is `already`.

### Phase 4 — arrival watch

- `watch: "arrive"` and any map that is not the legacy pair.
- Heartbeat file. USB edge unchanged. Legacy leave-push unchanged
  when the policy is not `arrive`.

**Exit:** keyboard arrival on host A relays `desk to A`. Unplug
on A does not push a channel.

### Phase 5 — display list + DDC adapter

- `adapters.displays[]`. Legacy object becomes `main`.
- `adapters/ddc` shells out to `ddcutil` / `m1ddc`. Manifest
  lists `display.input` only.
- Side instance ships with empty input names (skip) until §14
  is answered.
- `desk full` / `desk pbp` still target the instance that
  implements them.

**Exit:** mouse-only and DualUp-only desks unchanged. A desk
with `fallback: "ddc"` switches `main` when the HID is absent
and DDC answers.

### Phase 6 — shells

- Three jobs in §8. Ambient slots default off.
- Update `docs/ui-config.md` in this PR.
- Omarchy menu: one row per host.

**Exit:** the panel answers "where is it", "send me there", and
"why didn't it". Weather and kettle stay on the CLI.

### Phase 7 — cables, then the shell checkpoint

- Physical checklist §9, only after phase 3 is in use. Move the
  webcam in the same sitting as USB-B.
- OSD pairing before trusting upstream ownership hints.
- After the narrowed shells have been the daily UI: if they are
  not actually used for the three jobs, a follow-up removes them
  (§8.3). CLI and shortcuts stay.

**Exit:** reference cabling matches §9.4. Boot unlock, if used,
is the ops in §11 and not a commit in this repo.

---

## 13. Risks

- **Moving USB-B before relay exists.** `mac` is the only host
  that can switch the reference HID today. Pull that cable
  first and `desk to` on `mac` cannot change input. Phase 3
  before §9.
- **Webcam left on the hub.** Same sitting as the USB-B move.
  After `mac` loses the upstream, the hub is invisible to `mac`,
  so the camera would follow the active input instead of staying
  on `mac`.
- **USB-C that is not data-only.** Alt Mode or power delivery on
  the tower's new cable can fight the HDMI picture. USB-A to
  USB-C is the requirement in §9 step 2.
- **Arrival is slower than leave-push.** The destination must see
  the keyboard, then reach the mouse's owner over SSH. If that
  peer is down, the mouse stays. That is what `peer_unreachable`
  is for. Do not "fix" it by also pushing on leave.
- **Both watch policies armed.** Double hop. One policy per
  config.
- **USB ghost vs arrival.** A host that keeps a stale USB
  collection must not treat it as a new edge. Adapter-owned
  ghost rules; core only sees the edge the adapter reports.
- **Three watchers at login.** The first sample is a snapshot,
  not an arrival, on every host. Same rule as today's USB path.
- **Inactive upstream HID.** If the control HID only enumerates
  on the selected input, the host paired with USB-B cannot
  switch away once HDMI1 (no upstream) is showing. DDC from a
  GPU that still has the link is the fallback, and it is
  unverified (§14).
- **DDC permissions.** An I2C udev rule belongs to `adapters/ddc`,
  or the fallback silently no-ops. Same shape as the mouse
  adapter's hidraw rule.
- **Side panel surprise.** `desk to tower` not moving the small
  monitor is correct. Show it in the location rows so it does
  not look like a missed hop.
- **TPM enrollment.** Auto-unlock trades the passphrase at boot
  for physical possession of the machine. Wiping the passphrase
  slot removes the recovery path. Ops text only; core stays out.
- **Shell scope creep.** Putting weather back on the default
  strip recreates the extra this RFC narrowed. Opt-in stays
  opt-in. If the three jobs are unused, remove the shells
  instead of restuffing them.
- **`linux` aliased onto one of two Linux hosts.** A half-migrated
  file would hop the wrong machine. Loader must not invent that
  alias (§4).

---

## 14. Open questions

Hardware checks. They do not block accepting the RFC. They do
block filling input names and trusting DDC in phase 5.

1. **Inactive upstream.** Does the monitor's control HID
   enumerate on an upstream that is not the selected input? The
   hub check in §9.1 was from `mac`, the host that currently
   holds USB-B. It does not answer this.
2. **VCP `0x60` on the main monitor.** Does DDC input select
   work at all, and does it work over `mac`'s HDMI link (the
   input with no USB upstream)?
3. **DDC on each Linux GPU.** `ser9` and `tower`, separately.
   A dead I2C controller on one of them means that host cannot
   be the DDC fallback.
4. **Side panel.** Model, which physical inputs it has, and
   whether it answers DDC. A first probe with `ddcutil` and
   `m1ddc` did not list it. It may have been off, or powered
   and showing the other host's input. Re-check with the panel
   on and each of its two cables active before writing
   `inputs.mac` / `inputs.ser9`.

---

## 15. What this PR is

Markdown only. No adapter, no `desk relay` subcommand, no watcher
change, no shell change, no cable pulled by a script.

Next PR, if this holds: phase 1, the host map, still local-only,
legacy two-host files untouched in behavior.
