# desk

One CLI. Adapters do the hardware. Shells only paint.

**desk** is a small orchestrator for a desk of one or more hosts: hop a Logitech MX
Master (and, if you want, an [LG DualUp](https://www.lg.com/us/monitors/lg-28mq780-b))
to a configured host, optionally follow an
[HHKB Studio](https://happyhackingkb.com/), and hang extra tray slots
(smart-home light, Fellow-style kettle, weather) off the same contract.
`mac` and `linux` are the defaults. Any other id in `adapters.hosts` is a host too.

```
core     = config · status · watch · to/full/pbp/layout · adapter registry · slots
adapters = mouse / keyboard / display / smarthome / kettle / weather / wallpaper
shells   = macOS DeskSwitchBar + Omarchy tray — they paint status and call `desk`
```

Taught command: **`desk`**. LaunchAgent / systemd *unit filenames* can
stay (`local.hhkb-mx-follow`, `hhkb-mx-follow.service`); they invoke
`desk`.

Works on **macOS** and **Linux**. Install the watcher on every computer.
The host where the keyboard arrives runs `desk to` itself.

**Docs:** [RFC 0001](docs/rfc/0001-rust-core-and-adapters.md) ·
[RFC 0002](docs/rfc/0002-desk-product-kettle-shared-tray.md) ·
[RFC 0003](docs/rfc/0003-three-host-desk.md) ·
[HUD / tray UI config](docs/ui-config.md)

## Adapters

| id | role | one line |
|---|---|---|
| **mxswitch** | mouse | MX Master Easy-Switch hop (`mouse.host_switch`) |
| **lgdualup** | display | DualUp input / PBP / full + OS layout |
| **hhkb** | keyboard | HHKB presence so `watch` can follow the keyboard |
| **alexa** | smarthome | list devices + desk light on/off (wraps alexacli) |
| **kettle** | kettle | Fellow-style kettle on the LAN (heat / off / status) |
| **weather** | weather | Open-Meteo ambient ° + altitude |
| **wallpaper** | wallpaper | Per-output image from aspect or exact resolution. Off unless enabled |

Pin with `adapters.<role>.backend` / `path`, or drop a binary +
`*.manifest.json` in `~/.local/lib/desk/` (`$DESK_LIB`). Third parties
can also ship `desk-<id>` on PATH. See [Plug another adapter](#plug-another-adapter).

`adapters.dualup` is a legacy alias of `adapters.display`.

## How it works

The keyboard picks the host. Software cannot move it: each machine is a
Bluetooth slot, or the USB cable. `desk watch` on that machine sees the
keyboard arrive and runs the full `desk to <this host>`.

```
keyboard arrives on this host     (Bluetooth or USB, after the leave debounce)
        │
        ▼
desk to <this host>
        ├─ mouse → this host's channel
        │     local adapter, else SSH relay_hosts when the device is absent
        └─ display input → this host's input, when arrival_moves_display
              (default on; off leaves every monitor where it is)

keyboard leaves this host
        │
        ├─ one or two hosts: mouse → follow_channel     (push_on_leave, default on)
        └─ three or more:    nothing                    (the next host pulls)

desk to <host>                (or the Omarchy / macOS panel)
        │
        ├─ mouse adapter  → that host's channel
        │     local, else SSH relay on "not found"
        └─ display input  → local adapter, else SSH relay_hosts on "not found"

desk pbp                      (or DualUp PBP in the menu bar / Omarchy panel)
        │
        ├─ lgdualup pbp 50-50
        ├─ lgdualup pbp-assign hdmi1 dp
        ├─ settle (layout_settle_s, default 0.5s)
        └─ dualup-layout pbp      (Mac 2880x1280@270 / Linux 1280x2880 t3)

desk full
        │
        ├─ lgdualup pbp full
        ├─ settle
        └─ dualup-layout full     (Mac 2880x2560@270 / Linux 2560x2880 t3)
```

`status --json` exposes `adapters.*`, additive `slots`, and the shared
bar fields (`target_hint`, `bar_label`, `bar_strip`, HHKB / mouse /
DualUp probes). New shells paint `slots`. Lights stay out of the strip
unless `ui.tray.lights` is true. Slot order and HUD prefs live in `ui`
— see [docs/ui-config.md](docs/ui-config.md).

A desk often shows two hosts at once: one on the main monitor and another
on a second, or both in PBP. `desk layout <name>` applies a named
arrangement. `status --json` reports `active_layout` and the per-monitor
assignment; the display slot label is that layout name.

Keyboard arrival still builds a plan (`mouse`, `displays`, `layout`).
With no active layout, `layout` stays empty and the blanket input switch
runs only when `displays` is `move`. With one active, arrival moves the
mouse and leaves the monitors alone when this host is already shown.
Otherwise, and only when `arrival_moves_display` is on, it switches the
monitors that layout marks `follow` (default: the primary monitor, and
only while that monitor is full). A PBP monitor stays split unless
`follow` is set. `desk to <host>` typed at the command line is unchanged.

A rising USB sample is the legacy `follow_hhkb_usb` edge: it also runs
`desk to` this host when the keyboard was already connected over Bluetooth.
`follow_keyboard_arrival` (default on) is the edge for every transport.
The first sample after start or sleep is a snapshot, and a probe error
counts as present, so neither one pulls the desk by itself. A drop shorter
than the debounce does not count as a new arrival, and a repeat sample
does not run again while this host is still the last pull.

**Lock screen / greeter:** keep the keyboard USB cable in the machine you are
unlocking. Selecting USB makes `watch` pull mouse + display input to whichever
host has the cable.

## Requirements

- Python 3.9+
- MX Master 3 / 3S / 4 on matching Easy-Switch channels
- HHKB Studio is optional (VID `04FE` / PID `0016`)
- DualUp is optional. USB HID `043e:9a39` — plug that cable into the
  machine that should flip the monitor. macOS layout needs `displayplacer`;
  Linux layout needs Hyprland `hyprctl`.
- macOS: Xcode Command Line Tools (`clang` for helpers; `swiftc` for the menu bar)
- Linux: `hidraw` + the udev rules below

Pairing that works:

| Device | Channel / slot | Host |
|---|---|---|
| HHKB | Fn+Ctrl+1 (or USB) | Mac |
| HHKB | Fn+Ctrl+2 | Linux |
| HHKB | Fn+Ctrl+3 | third host (`tower`) |
| MX Master | Easy-Switch 1 | Mac |
| MX Master | Easy-Switch 2 | Linux |
| MX Master | Easy-Switch 3 | third host (`tower`) |

If the HHKB USB cable stays in the Mac *and* you hop the keyboard to
Bluetooth on Linux, the Mac may still enumerate a USB collection. The
watcher then never fires. Bluetooth on both hosts; treat USB as charging,
or unplug when you hop.

## Install

```bash
git clone https://github.com/gabriel-laet/desk.git
cd desk
```

Put `~/.local/bin` on `PATH`. `git pull && make install` from the clone.

`make install` writes:

```
~/.local/bin/desk                        # the CLI
~/.local/lib/desk/mxswitch               # mouse reference (TCC path — do not move)
~/.local/lib/desk/lgdualup               # display USB helper
~/.local/lib/desk/dualup-layout          # display OS layout (displayplacer / hyprctl)
~/.local/lib/desk/hhkb                   # keyboard.presence reference
~/.local/lib/desk/alexa                  # smarthome reference (wraps alexacli)
~/.local/lib/desk/kettle                 # Fellow-style LAN kettle
~/.local/lib/desk/weather                # Open-Meteo ambient ° + altitude
~/.local/lib/desk/wallpaper              # per-output wallpaper (off unless enabled)
~/.local/lib/desk/*.manifest.json        # api_version: 1
~/.local/bin/mxswitch                    # compat shim → lib/
~/.local/bin/lgdualup                    # compat shim → lib/
~/.local/bin/kettle                      # compat shim → lib/ (prefer `desk kettle`)
~/.config/desk/config.json               # first install only
```

Config read order: `~/.config/desk/`, then `~/.config/desk-switch/`, then
`~/.config/hhkb-mx-follow/`. Adapter libdir read order: `$DESK_LIB`,
`$DESK_SWITCH_LIB`, `~/.local/lib/desk/`, then `~/.local/lib/desk-switch/`.

### macOS

```bash
make install
desk status
```

Grant **Input Monitoring** to `~/.local/lib/desk/mxswitch`
(System Settings → Privacy & Security). The PATH shim is a shell script;
TCC is on the real binary. `mxswitch --setup` opens that pane.

`adapters.hosts.follow_channel` is the *other* machine’s Easy-Switch slot
(2 if this Mac is channel 1).

HHKB follow at login (existing unit name — do not rename if already loaded):

```bash
cp macos/local.hhkb-mx-follow.plist.example \
   ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
# edit both /Users/YOU paths, then:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
```

The example plist calls `desk watch`. Log:
`~/Library/Logs/hhkb-mx-follow.log`.

That agent is the GUI session. Input Monitoring applies to adapters it
runs, not to an SSH shell. `desk watch` listens on a user socket; a peer's
mouse relay runs `desk session-run` over SSH, and the agent runs the
adapter. After `make install`, reload the agent so the listener is the
new process:

```bash
launchctl kickstart -k gui/$(id -u)/local.hhkb-mx-follow
```

Menu bar (optional): see [macOS menu bar](#macos-menu-bar).

### Linux

```bash
make install
desk status
```

Mouse hidraw:

```bash
sudo cp adapters/mxswitch/linux/42-logitech-hidpp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input "$USER"   # then log out/in once
```

DualUp USB on *this* box (skip if the DualUp cable is in the Mac):

```bash
sudo cp adapters/lgdualup/linux/43-lg-dualup.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Set `adapters.hosts.this_host` to `linux` and `follow_channel` to `1`
(the Mac). `make install` does that on a first-time Linux config.

Watcher (existing unit name):

```bash
mkdir -p ~/.config/systemd/user
cp linux/hhkb-mx-follow.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hhkb-mx-follow.service
journalctl --user -u hhkb-mx-follow -f
```

`WantedBy=graphical-session.target` — starts with Hyprland/Omarchy.
The unit runs `desk watch`.

## Omarchy plugin

The plugin manifest stays at the git root (`manifest.json`, id
`glaet.desk-switch`). The widget is `linux/omarchy/BarWidget.qml`. It
calls `desk` only. It does **not** run `make install`.

On the Linux box:

```bash
make install                          # CLI + adapters first
omarchy plugin add https://github.com/gabriel-laet/desk.git --enable
```

That clones into `~/.config/omarchy/plugins/glaet.desk-switch/` and places
a widget on the **right** section. The strip stays short. Quiet density
paints the focus token and, when a named layout is active, that name
(`TWR  work`, from `bar_strip.title`). `FULL` or `PBP` stands in when
no named layout is set. Enabled slot labels follow the same token.
`ui.tray.density: "chips"` paints `bar_label`, which leads with the
same host and layout, then the keyboard and mouse marks.

Buttons come from `desk status --json`. One switch button per
`host_list` entry, so a third id such as `tower` shows up on its own.
The focus target is marked (`host_list[].current`); when focus is
unknown the mark is this host. Saved layouts are `layouts[]`. The
active one is marked. Applying one runs `desk layout <name>`.

| Button | Command |
|---|---|
| Refresh status | `desk status --json` |
| Switch to `<host>` | `desk to <id>` — one button per configured host |
| `<layout>` | `desk layout <name>` — one button per saved layout. Hidden when `layouts` is empty |
| DualUp Full  ⌘⌥⇧F | `desk full` — hidden unless DualUp is present or mode is known |
| DualUp PBP  ⌘⌥⇧P | `desk pbp` — uses `pbp_mode` from config |
| Auto layout  ⌘⌥U | `desk layout` — re-applies full or PBP from the live display |

Polls about every 15s. Looks up `desk` via `bash -lc` with `~/.local/bin`
on `PATH`.

Already cloned this repo on the machine? Enable the checkout instead of
re-adding, then `omarchy plugin validate .`. Without Omarchy:
`make validate-plugin`.

### Omarchy menu

Merge the keys in [`extensions/omarchy-menu.jsonc`](extensions/omarchy-menu.jsonc)
into `~/.config/omarchy/extensions/omarchy-menu.jsonc`. Do not replace the
file. Rows land under **Trigger → desk**.

## macOS menu bar

Native `MenuBarExtra`. Same job as the Omarchy panel. The extra stays
short: quiet density draws the focus token and the active layout name
(`TWR  work`) in front of the slot glyphs. Chips density paints
`bar_label` (host, layout, keyboard, mouse). Click for a modular HUD:
one **Switch to** button per `host_list` entry (the current target is
filled; a third id such as `tower` is just another button), then one
button per saved layout (the active name is filled; `desk layout <name>`),
then each enabled slot once by `kind`. Full / PBP / auto layout stay on
the display slot. **Configure tray** drag-reorders slots and writes `ui`
in `~/.config/desk/config.json`. Calls `desk` only (PATH, then
`~/.local/bin`). macOS 13+. Ad-hoc signed, not App Store.

```bash
make install                 # CLI first
make menubar                 # build/DeskSwitchBar.app  (needs swiftc)
make install-menubar         # ~/Applications/DeskSwitchBar.app
open -a DeskSwitchBar
```

Refresh every ~15s and again when the panel opens. DualUp rows show
Karabiner-style shortcuts: **⌘⌥⇧F** full, **⌘⌥⇧P** PBP, **⌘⌥U** auto
layout. Those keys should run `desk full` / `pbp` / `layout`.

Login item:

```bash
cp macos/local.desk-switch-bar.plist.example \
   ~/Library/LaunchAgents/local.desk-switch-bar.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.desk-switch-bar.plist
```

## CLI

```bash
desk status              # HHKB / mouse / adapters (text)
desk status --json
desk status --json --local  # skip SSH peer peek
desk status --hint       # MAC / LNX / host hint / ?  (same as: desk hint)
desk to mac              # mouse + display input if configured (relayed if needed)
desk to linux
desk to tower            # any id configured in adapters.hosts
desk to linux --mouse-only
desk switch mac          # same as `to mac`
desk switch 2            # mouse only, Easy-Switch 1|2|3
desk pbp                 # USB PBP + hdmi1/dp inputs + OS layout
desk pbp 50-50           # or 50 / 50/50 / on  (lgdualup also accepts full/off)
desk full                # USB full + OS layout
desk layout <name>       # apply layouts.<name> (full host, or PBP top/bottom)
desk layout              # re-apply full or PBP from the live display geometry
desk watch               # HHKB leave → mouse away; USB appear → this host's channel
desk watch --dry-run
desk smarthome list      # devices (and entities when that API works)
desk smarthome status    # light-oriented snapshot
desk smarthome on        # desk light on
desk smarthome off       # desk light off
desk kettle status --json
desk kettle heat 93
desk kettle off
desk weather status --json
desk wallpaper apply     # no-op unless adapters.wallpaper.enabled
desk wallpaper status --json
desk --version
```

`watch` never touches the display on keyboard leave. USB appearance runs
`desk to <this_host>` (mouse + display input). Use `to <host>` (or a panel)
for mouse + monitor together.

`pbp` / `full` no-op with a message if the dualup adapter is missing.
After a successful USB toggle, `pbp` assigns the cabling pair (Linux
DisplayPort `dp` first, then Mac `hdmi1`) and both commands apply OS
layout. After the USB toggle (and PBP assign), both wait
`layout_settle_s` (default 0.5s) so EDID can settle. Layout retries ~8s
while EDID catches up.

| Host | PBP | Full |
|---|---|---|
| macOS (displayplacer) | `2880x1280 @ 270°` | `2880x2560 @ 270°` |
| Linux / Hyprland (`hyprctl`) | `1280x2880` transform **3** | `2560x2880` transform **3** |

Not `2880x1280` t3 (stretched) and not `1280x2880` t0 (wrong orientation).
Set `adapters.dualup.peer` to SSH layout-only to the other machine.

`mxswitch` / `lgdualup` / `kettle` on PATH exec the private helpers.

## Config

Edit [`config.example.json`](config.example.json) →
`~/.config/desk/config.json`.

```json
{
  "adapters": {
    "mouse": { "enabled": true, "backend": "mxswitch" },
    "keyboard": { "enabled": true, "backend": "hhkb" },
    "hosts": {
      "this_host": "mac",
      "follow_channel": 2,
      "mac": { "channel": 1 },
      "linux": { "channel": 2 },
      "follow_hhkb_usb": true
    },
    "display": { "backend": "lgdualup" },
    "dualup": {
      "enabled": true,
      "pbp_mode": "50-50",
      "switch_pbp": false,
      "inputs": { "mac": "hdmi1", "linux": "dp" }
    },
    "smarthome": {
      "enabled": true,
      "backend": "alexa",
      "device": "DeviceName"
    },
    "kettle": {
      "enabled": true,
      "backend": "kettle",
      "host": "YOUR_HOST"
    },
    "weather": {
      "enabled": true,
      "backend": "weather",
      "latitude": 0,
      "longitude": 0,
      "timezone": "UTC",
      "label": "Home"
    },
    "wallpaper": {
      "enabled": false,
      "landscape": "~/Pictures/wallpaper-landscape.jpg",
      "portrait": "~/Pictures/wallpaper-portrait.jpg",
      "exact_dir": "~/Pictures/wallpapers",
      "exact_prefix": "desk",
      "primary_output": "",
      "outputs": {}
    }
  },
  "ui": {
    "tray": {
      "density": "strip",
      "slots": [
        { "id": "weather", "enabled": true, "kind": "chip", "show_altitude": true },
        { "id": "kettle", "enabled": true, "kind": "face" },
        { "id": "dualup", "enabled": true, "kind": "mode" }
      ]
    },
    "hud": { "show_faces": true, "density": "regular" }
  }
}
```

| Key | Meaning |
|---|---|
| `adapters.mouse.backend` / `path` | Mouse adapter id or executable |
| `adapters.keyboard.backend` / `path` | Keyboard.presence adapter (`hhkb` is the reference) |
| `adapters.display` | Same role as `adapters.dualup` (legacy alias) |
| `adapters.smarthome` | Smart-home role. Optional `device` is the speaker that hears light phrases |
| `adapters.kettle` | Fellow-style LAN kettle. Set `host` (DHCP moves it). See [`adapters/kettle/`](adapters/kettle/) |
| `adapters.weather` | Open-Meteo ambient. Set `latitude` / `longitude` / `timezone` / `label`. See [`adapters/weather/`](adapters/weather/) |
| `adapters.wallpaper` | Optional per-output images. Off unless `enabled` is true. See [Wallpaper](#wallpaper) |
| `ui.tray.density` | `strip` (default, quiet) or `chips` (dense `bar_label` in the bar) |
| `ui.tray.lights` | If true, `bar_strip` may include a lights on/off mark |
| `ui.tray.slots` | Order + show/hide + flavor. Default **weather → kettle → dualup**. [docs/ui-config.md](docs/ui-config.md) |
| `adapters.hosts.this_host` | This machine's id. Any id in the host map. Default is `mac` on macOS and `linux` on Linux |
| `adapters.hosts.follow_channel` | Easy-Switch slot `watch` pushes the mouse to when the keyboard leaves, if `push_on_leave` is on |
| `adapters.hosts.follow_keyboard_arrival` | If true (default), `watch` runs `desk to <this_host>` when the keyboard arrives on any transport, after the same debounce as a leave |
| `adapters.hosts.arrival_moves_display` | If true (default), that arrival also switches display inputs. False leaves every monitor where it is (mixed layouts, PBP) and still moves the mouse. `desk to` from the command line is unchanged |
| `adapters.hosts.push_on_leave` | If true, a confirmed leave pushes the mouse to `follow_channel`. Default true for one or two hosts, false for three or more |
| `adapters.hosts.<id>.channel` | Mouse channel for that host. `mouse_channel` is the same field. `desk to <id>` uses it |
| `adapters.hosts.<id>.keyboard_slot` | Opaque keyboard slot stored for the keyboard adapter |
| `adapters.hosts.<id>.input` | Display input for `desk to <id>`. `adapters.display.inputs.<id>` is the same value |
| `adapters.hosts.<id>.hint` | Short focus token. `mac` stays `MAC` and `linux` stays `LNX`; other ids use `hint` or the id |
| `adapters.hosts.order` | Optional order for status. Default is key order. A file that only names `mac` and `linux` still loads those two |
| `adapters.dualup.inputs.*` | Display input for that host (`adapters.display.inputs` is the same map). PBP uses Mac=`hdmi1`, Linux=`dp` when empty. `desk to` with neither this nor `hosts.<id>.dualup_input` / `hosts.<id>.input` says so and leaves the input unchanged |
| `adapters.hosts.<id>.ssh` | Optional SSH target for that host (`user@192.0.2.10`). Relay and `pre_switch` use it |
| `adapters.hosts.<id>.pre_switch` | Optional shell command run on that host before `desk to` changes its display input |
| `adapters.display.relay_hosts` | Ordered host ids. When the local display adapter reports the device absent, try these peers and stop at the first success. `adapters.dualup.relay_hosts` is the same list. Each monitor can set its own |
| `layouts` | Named arrangements. `desk layout <name>` applies one and stores it as `active_layout` |
| `adapters.displays` | Map of monitor id → display instance. Absent: the single `adapters.display` object is monitor id `display` |
| `pbp_splits` | On a DualUp instance. `side` defaults to 5 (written). `stacked` defaults to keep: null does not write `0xD7`. A number other than 0 or 2 is written. 0 and 2 are never sent |
| `adapters.display.relay_path` | Optional remote executable. Default is `$HOME/.local/lib/desk/<adapter id>` |
| `adapters.mouse.relay_hosts` | Ordered host ids for the mouse. When the local mouse adapter reports the device absent, try these and stop at the first success. When omitted, the display `relay_hosts` list is used |
| `adapters.mouse.relay_path` | Optional remote mouse executable. Default is `$HOME/.local/lib/desk/<mouse adapter id>` |
| `adapters.dualup.switch_pbp` | If true, `to mac\|linux` also runs the PBP sequence |
| `adapters.dualup.pbp_mode` | Mode for `desk pbp` and for `to` when `switch_pbp` is true |
| `adapters.dualup.display_id` | macOS displayplacer UUID or Hyprland connector. Empty = detect DualUp |
| `adapters.dualup.layout` | Apply OS resolution/rotation after USB (default true) |
| `adapters.dualup.layout_settle_s` | Seconds to wait after USB before OS layout. Default 0.5 |
| `adapters.dualup.peer` | Optional SSH host; runs `dualup-layout` there (no USB) |
| `adapters.hosts.follow_hhkb_usb` | Legacy USB-only edge. If true (default), a rising USB sample runs `desk to <this_host>` even when the keyboard was already here over Bluetooth. Set `follow_keyboard_arrival` false to keep only this edge |
| `poll_interval_s` / `absent_polls_required` | Watcher debounce (defaults 0.5s × 4 ≈ 2s) |

### More than two hosts

Host ids come from `adapters.hosts`. `mac` and `linux` stay valid, including
aliases (`macos`, `omarchy`). A third id is just another entry: its channel,
display input, `ssh`, and `pre_switch` are what `desk to <id>` and the
relays use. `this_host` on that machine is the same id.

With three or more hosts the keyboard is the only driver. The machine
that sees it arrive (Bluetooth slot or USB) runs `desk to` **this** id:
mouse channel, and — when `arrival_moves_display` is on — display input,
`pre_switch`, and the relay when the device is not on this host. Turn
`arrival_moves_display` off when two hosts should stay on screen.
Leaving does not push the mouse at `follow_channel`,
because this host cannot know which of the others took the keyboard.
`push_on_leave` stays available and defaults off once the map has three
or more ids. A file that only names `mac` and `linux` still pushes on
leave, and still treats a USB appearance as `desk to` this host.

A file that only names `mac` and `linux` (or only sets `target_channel`)
keeps today's channels. Naming any other id does not invent a `linux` entry
beside it. Two hosts on the same mouse channel is a config error.
Tray slots stay weather, kettle, and the display mode; shells paint those.
Status lists every host (`host_list`, with `current` on the focus target)
and every saved layout (`layouts`, with `active` on the one in use).
The menu bar and the Omarchy panel build their switch and layout buttons
from those lists.

```json
{
  "adapters": {
    "hosts": {
      "this_host": "tower",
      "follow_channel": 1,
      "order": ["mac", "linux", "tower"],
      "mac": { "channel": 1, "input": "hdmi1", "ssh": "user@192.0.2.10" },
      "linux": { "channel": 2, "input": "dp", "ssh": "user@192.0.2.20" },
      "tower": {
        "channel": 3,
        "keyboard_slot": 3,
        "input": "hdmi2",
        "hint": "TWR",
        "ssh": "user@192.0.2.30",
        "pre_switch": "wake-outputs"
      }
    },
    "display": {
      "relay_hosts": ["mac", "linux", "tower"],
      "inputs": { "mac": "hdmi1", "linux": "dp", "tower": "hdmi2" }
    }
  }
}
```

`192.0.2.0/24` is documentation space. `mouse_channel` is accepted in place
of `channel`. A per-host `peer` string is the SSH target when `ssh` is empty.

### Display input relay

The display adapter can only run on the host that currently holds the
monitor's control device (for the reference adapter, the USB upstream).
`desk to <host>` still switches the mouse locally, then:

1. If that host has `pre_switch`, run it before the input change. This
   machine runs the command in a shell. Any other host runs it over SSH
   so its outputs can wake and the monitor has a signal. A failed hook
   is printed and `desk to` exits non-zero.
2. Run the display adapter locally (`input <name>`).
3. If that exits non-zero and the output says the device is absent
   (`not found`, or `No … found`), try each `relay_hosts` id in order.
   Stop at the first success. Other adapter errors stay on this host.

The peer command is:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=3 <ssh target> \
  "$HOME/.local/lib/desk/<adapter id> input <name>"
```

`<adapter id>` is whichever display adapter is bound, so this is not
specific to one monitor. On success desk prints `display input <name> via <host>`.

```json
{
  "adapters": {
    "hosts": {
      "this_host": "mac",
      "mac": { "channel": 1, "ssh": "user@192.0.2.10" },
      "linux": {
        "channel": 2,
        "ssh": "user@192.0.2.20",
        "pre_switch": "wake-outputs"
      }
    },
    "display": {
      "backend": "lgdualup",
      "relay_hosts": ["linux", "mac"],
      "inputs": { "mac": "hdmi1", "linux": "dp" }
    }
  }
}
```

`192.0.2.0/24` is documentation space. Use your own SSH targets. Omit
`relay_hosts` when the control device stays on the machine that runs
`desk to`. A target with no input configured prints that and does not
guess one.

### Mouse relay

The mouse adapter can send a host change only while the mouse is connected
to the machine that runs it. `desk to` (and the leave-push, when that is
on) runs the local adapter first. If this process is not `desk watch`, and
a watcher is listening, the local hop asks that watcher so the adapter
runs in its session. If the adapter exits non-zero and the output says
the device is absent (`not found`, or `No … found`), desk tries each relay
host over SSH and stops at the first success.

Candidates stay in `relay_hosts` order, with two changes that do not drop
anyone: a host that recently reported the mouse online is tried first
(newest report wins; `desk status` records the local adapter and a
reachable peer), and a peer that just failed to connect sinks for about
two minutes. Mouse SSH uses a 2s connect timeout and one attempt, so an
offline peer does not stall the hop.

```bash
ssh -o BatchMode=yes -o ConnectTimeout=2 -o ConnectionAttempts=1 <ssh target> \
  'export PATH="$HOME/.local/bin:$PATH"; desk session-run -- $HOME/.local/lib/desk/<mouse adapter id> <channel>'
```

`desk session-run` asks the peer's watcher to run the adapter. No watcher
(or an older `desk` without the subcommand): the adapter runs directly.
On success it prints `mouse channel <n> via <host>`. `adapters.mouse.relay_hosts`
is the configured list. When that key is omitted, `adapters.display.relay_hosts`
is used, so one list can cover both devices. An explicit empty mouse list
does not fall back. Other adapter errors stay on this host.

An SSH shell that prints `Input Monitoring` did not see the device from
the GUI session. That is not "mouse not on this host". The log names the
session. A watcher answer that still cannot see the device is a real miss
(`not found on <host>`), and the adapter's own line still says to wake
the mouse and check Input Monitoring.

Inputs the helper accepts: `usbc` / `usb-c` / `dp3`, `dp` / `dp1`, `dp2`,
`hdmi1`, `hdmi2`, `auto`. List devices: `lgdualup --list` (or `--info`).

Typical cabling: Mac = **HDMI1**, Linux = **DisplayPort (`dp`)**. Do not
set Mac to `usbc`.

macOS needs [displayplacer](https://github.com/jakehilborn/displayplacer)
(`brew install jakehilborn/jakehilborn/displayplacer`). Linux uses `hyprctl`.

Legacy keys (`mxswitch`, `lgdualup`, `this_host`, `hosts`, `target_channel`)
still load. New installs write the adapters shape.

## Plug another adapter

Core discovers adapters from `$DESK_LIB` (default `~/.local/lib/desk/`,
with a read-fallback to `~/.local/lib/desk-switch/`) and `desk-<id>` on
PATH. A drop-in is an executable plus a manifest (`api_version: 1`):

```json
{
  "api_version": 1,
  "id": "unifying",
  "name": "Unifying",
  "capabilities": ["mouse.host_switch"]
}
```

Layout (first that works):

```
~/.local/lib/desk/unifying
~/.local/lib/desk/unifying.manifest.json
# or: ~/.local/lib/desk/unifying/unifying
#     ~/.local/lib/desk/unifying/manifest.json
# or: desk-unifying on PATH
```

Pin if more than one mouse adapter is present:

```json
{
  "adapters": {
    "mouse": { "enabled": true, "backend": "unifying" }
  }
}
```

`desk to linux` then calls `unifying 2`. `--info` should print
`currently on N` so status can cache the channel. A worked example lives
at [`examples/dummy-mouse/`](examples/dummy-mouse/).

Display adapters list `display.input` / `display.pbp` / `display.full` /
`layout.apply`. Keyboard adapters list `keyboard.presence`. Smart-home
adapters list `smarthome.list` / `smarthome.status` / `light.on` /
`light.off`. Kettle adapters list `appliance.status` / `appliance.heat` /
`appliance.off`. Weather adapters list `weather.status`. Wallpaper
adapters list `wallpaper.apply`.

## Named layouts

`layouts` names an arrangement of monitors. A monitor id is a key of
`adapters.displays`, or `display` when the file still has one
`adapters.display` / `adapters.dualup` object. Core does not know VCP
codes. It asks the display adapter to select an input, and, for PBP,
to set a split name, then the sub input, then the main input. The
DualUp manual calls these Main Screen (Up) and Sub Screen (Down), so
top is main and bottom is sub. Sub is still sent first.

```json
{
  "layouts": {
    "split": {
      "monitors": {
        "display": {
          "mode": "pbp",
          "top": "mac",
          "bottom": "linux",
          "split": "stacked"
        },
        "side": { "mode": "full", "host": "tower", "follow": false }
      }
    },
    "focus": {
      "monitors": {
        "display": { "mode": "full", "host": "mac" }
      }
    }
  },
  "adapters": {
    "displays": {
      "display": {
        "backend": "lgdualup",
        "inputs": { "mac": "hdmi1", "linux": "dp", "tower": "hdmi2" },
        "relay_hosts": ["mac", "linux", "tower"],
        "pbp_splits": { "side": 5 }
      },
      "side": {
        "backend": "ddc",
        "inputs": { "mac": "0x11", "linux": "0x12", "tower": "0x0f" }
      }
    }
  }
}
```

`desk layout split` walks each monitor. Only a host that currently holds
that monitor's control link can send, so each command is tried locally
and then on `relay_hosts`, and tried once more after the settle delay:
the link moves when the input changes. PBP order is the split, then the
bottom host as the sub input, then the top host as the main input, with
`layout_settle_s` (default 0.5s) after each. When the monitor is set,
OS layout runs on **every** host shown there — locally on this machine,
over SSH (`ssh` or `peer`) on the others — via that host's
`dualup-layout` (or the instance's `layout_helper` / `peer_layout`).

`follow` defaults to the primary monitor, and only in `full` mode. Set
it on a PBP monitor only when arrival should leave that split.

The applied name is stored in `~/.local/state/desk/state.json`
(`$XDG_STATE_HOME/desk`, or `$DESK_STATE_DIR`). `desk status --json`
includes `active_layout` and `monitors`. The display slot's label is
the layout name while one is active.

### PBP split: keep the OSD picture

On this DualUp, VCP `0xD7` at `0x51` is not a reliable split direction.
`1` is full (PBP off). `5` is 50-50 side by side. `3` is 66/33 side by
side. With top/bottom 1:1 selected in the monitor menu, a read returns
`0`. That `0` means PBP is on, not which way the picture is split, and
**sending `0` (or `2`) stretches the panel**. Those two values are never
written.

`stacked` therefore defaults to **keep**. `split: "keep"`, or
`pbp_splits.stacked: null`, is the same: do not write `0xD7`.

- If the monitor already reads as PBP (the value is not `1`), desk only
  sets the sub input, the main input, and the OS layout.
- If it reads `1` (full), desk logs `PBP is off; enable PBP 1:1 in the
  monitor OSD once`, still sets the inputs, and still does not write
  `0xD7`.

Set the 1:1 picture from the OSD once. After that, `desk layout` leaves
it alone. Side-by-side is different: `split: "side"` still writes `5`.

To see the raw reading, on the machine that holds the USB upstream:

```bash
lgdualup get pbp
lgdualup --read 0xD7
```

Both send a DDC/CI get (opcode `0x01`) and parse the reply (opcode
`0x02`). A line `pbp 0xD7 @ 0x51 = 0 (0x00)` is PBP on. Do not put `0`
in `pbp_splits`. Only a value you have checked as a real direction
(not `0` or `2`) belongs there:

```json
"pbp_splits": { "stacked": null, "side": 5 }
```

`desk layout` forwards that map as `--splits` and does not interpret
it. Side-by-side halves are 1280x2880. Stacked and keep halves are
2560x1440 with no rotation: `dualup-layout pbp --split stacked` (or
`--split keep`) uses Hyprland `2560x1440@59.95` transform 0, and
displayplacer `res:2560x1440` `degree:0`.

## Alexa / smart-home

The `alexa` adapter is a thin wrapper around [`alexacli`](https://github.com/buddyh/alexa-cli).
Core never talks to Amazon itself. Detail: [`adapters/alexa/`](adapters/alexa/).

1. Install `alexacli` (`brew install buddyh/tap/alexacli` on a Mac).
2. Authenticate once: `alexacli auth`. Credentials live in
   `~/.alexa-cli/config.json`.
3. `make install` so `~/.local/lib/desk/alexa` + its manifest land.
4. Confirm device names: `desk smarthome list`.
5. Pin `adapters.smarthome.device` to the speaker that should hear the
   light phrases.

Spoken text must be only the light phrase (`acender a luz` /
`apagar a luz` by default). Address the speaker with `-d DeviceName`,
not by stuffing the room into the sentence. The adapter refuses an
utterance that names the configured device.

Light state prefers a readable entity power flag when `alexacli sh list`
returns one. Otherwise the adapter writes an optimistic last-commanded
`on` / `off`. Bars omit the lights slot unless `ui.tray.lights` is on.

## Kettle + weather

Fellow-style LAN HTTP lives in [`adapters/kettle/`](adapters/kettle/).
Open-Meteo ambient ° + altitude lives in
[`adapters/weather/`](adapters/weather/). Weather is **not** a kettle
side-feed — it keeps working when the kettle host is down.

```bash
desk kettle status --json
desk kettle heat 93
desk kettle off
desk weather status --json
desk status --json   # adapters.kettle + adapters.weather + slots[]
```

Set `adapters.kettle.host` to `YOUR_HOST`. The Stagg CLI has **no auth**
on port 80 — keep it on the LAN.

`slots` is additive on `status --json`. Default extra order is weather →
kettle → DualUp; `ui.tray.slots` reorders and hides.

Prefer `desk kettle …`. Optional `kettle` on PATH is a shim to
`~/.local/lib/desk/kettle`.

## Wallpaper

Optional. Nothing is set unless `adapters.wallpaper.enabled` is true.
The reference adapter does not know a particular monitor: it lists the
host's outputs and picks an image per output.

Resolution for the pick is the **mode size after rotation**, not the
scaled layout size. A 2560×1440 panel at scale 2 still looks up
`2560x1440`. Hyprland transforms 1, 3, 5, and 7 swap width and height
first (`hyprctl` `width`/`height` are the mode). macOS uses
`NSScreen` pixels (points × backing scale), which are already oriented.

1. **Exact file** in `exact_dir`, when that directory is set.
   `{exact_prefix}-<W>x<H>.png` wins (`desk-2560x1440.png`,
   `desk-1920x1080.png`, `desk-2560x2880.png`). If that file is absent,
   a single `*-<W>x<H>.*` in the same directory is used. Several matches
   are ignored.
2. **Portrait** when the rotated height is greater than the width,
   otherwise **landscape** (a square uses landscape). A per-output entry
   under `outputs` (connector or display name) replaces the global path
   for that orientation only.

Fit is cover/fill: crop, never stretch. Source files are not modified.
A setter that cannot crop writes a derived file under
`~/.cache/desk/wallpaper/`.

```bash
desk wallpaper apply
desk wallpaper status --json
```

`desk full`, `desk pbp`, `desk layout` (a named arrangement, or the live
full/PBP reapply), and `desk to` (including USB arrival inside `desk watch`)
apply afterwards. Failures are logged and do not fail the command. `desk watch` polls logical geometry every few
seconds and applies again when the set of output plus rotated pixel
size stays changed for about 2 seconds.

macOS sets each display with System Events (`desktop N`'s picture,
matched to the NSScreen name, else index) and then
`NSWorkspace.setDesktopImageURL` with
`NSWorkspaceDesktopImageScalingKey` =
`NSImageScaleProportionallyUpOrDown` (**3**) and
`NSWorkspaceDesktopImageAllowClippingKey` = true. Scaling **2** is
center and is not used.

Linux / Hyprland, first setter that applies:

1. **hyprpaper running** — per output (`preload`, then `wallpaper NAME,path`). Cover.
2. **swaybg running** — stop `swaybg`, start one `swaybg -o NAME -i PATH -m fill` per output.
3. **`omarchy-shell background setForScreen`** or **`omarchy-theme-bg-set --monitor`** — per output, only when that interface is present.
4. **Stock Omarchy** (`omarchy-theme-bg-set` / `background set`) — one image. The stock setter has no per-output path. The image is the one for `primary_output` when that output is connected, otherwise the largest output. The shell crops.
5. **`swaybg` installed and nothing else owns the background** — same per-output `-m fill` as (2).

A running hyprpaper or swaybg is preferred over Omarchy's single image.
On a stock Omarchy shell neither daemon is running, so (4) is what
runs. Detail: [`adapters/wallpaper/`](adapters/wallpaper/).

```json
{
  "adapters": {
    "wallpaper": {
      "enabled": true,
      "landscape": "~/Pictures/wallpaper-landscape.jpg",
      "portrait": "~/Pictures/wallpaper-portrait.jpg",
      "exact_dir": "~/Pictures/wallpapers",
      "exact_prefix": "desk",
      "primary_output": "DP-1",
      "outputs": {
        "DP-2": { "portrait": "~/Pictures/wallpaper-portrait.jpg" }
      }
    }
  }
}
```

Host setup is `make install`, the config above, and on Linux one of
`hyprpaper`, `swaybg`, or Omarchy's background tools. `hyprctl` lists
outputs. macOS only needs `osascript`. `desk watch` (the existing login
watcher) re-applies on hotplug, mode, and rotation.

## Troubleshooting

**`desk status` first.** Check `target_hint` / `bar_label`,
`hhkb_transport` (`usb` / `bluetooth` / `both` / `absent`), `hhkb_usb`,
`mouse_channel` (live or cached), `adapters.mouse.available`,
`adapters.dualup.available`, and whether DualUp USB was seen (`dualup_info`).

**Watcher log repeats `clock gap` while the keyboard stays put.** A real
sleep or wake still disarms until the keyboard is seen again. The gap is
measured on the wait between polls, not on how long the probe or a relay
took, so a slow iteration does not disarm by itself.

**Watcher never fires (keyboard USB ghost).** This host still sees the
keyboard over USB, so a Bluetooth hop does not look like a leave. With
`follow_keyboard_arrival` (default) the host that actually sees the
keyboard runs `desk to` itself. `follow_hhkb_usb` still treats a rising
USB sample as that same pull. To hop on Bluetooth only, unplug or use a
charge-only cable. A probe error is treated as *present* so a flaky read
cannot steal the mouse or look like a new arrival.

**Bar shows `?` on Mac while Linux is focused.** The MX Master is on the
Linux Easy-Switch channel, so Mac `mxswitch --info` fails (mouse asleep /
other host). Status remembers the last live channel and, if
`adapters.dualup.peer` (or `adapters.hosts.peer`) is set, peeks the peer
over SSH (`status --json --local` skips that).

**Mouse does not hop (macOS Input Monitoring or SSH session).** Grant
Input Monitoring to `~/.local/lib/desk/mxswitch`, not the shim. The grant
belongs to the GUI session: `desk watch` under the launch agent can see
the mouse, and an SSH shell often cannot, even when the same binary is
allowed. Peers ask that agent (`desk session-run`). Reload it after
install (`launchctl kickstart -k gui/$(id -u)/local.hhkb-mx-follow`).
If the agent itself still cannot see the mouse, click the mouse once to
wake it, and run `mxswitch --setup` if the binary path changed — re-grant
TCC after a path change.

**Mouse relay says `outside the GUI session`.** The peer's watcher is not
running, or it is still the process from before this `desk` was installed.
On macOS reload the launch agent above. The hop keeps trying the other
hosts. `not found on <host>` from a watcher means that host really does
not have the radio.

**Mouse hidraw denied (Linux).** udev rule `42-logitech-hidpp.rules`, user
in `input`, then a new login.

**Display input not found.** The control device is only on the host that
holds the monitor's USB upstream. `desk to` runs the display adapter
locally, then each `adapters.display.relay_hosts` entry over SSH, and
prints which host performed it. A target with no display input configured
says so and leaves the input unchanged. After PBP / full the host must
also get the OS layout. Optional `adapters.dualup.peer` SSHes layout-only
to the other machine. `pre_switch` on the target host runs first when you
need that machine's outputs awake before the input changes.

**Mouse not found on this host.** The mouse radio is on whichever machine
it is paired to right now. `desk to` runs the mouse adapter locally (in
the watcher session when one is listening), then each
`adapters.mouse.relay_hosts` entry (or the display `relay_hosts` list
when the mouse list is omitted) over SSH, and prints which host performed
it. The host that last reported the mouse online is tried first. A peer
that does not answer within the 2s connect timeout is skipped and tried
later. The host where the keyboard arrives is often not the host that
still has the mouse, so that relay is what completes the hop.

**Panel / menu bar shows `?` or “desk not found”.** CLI not installed, or
GUI `PATH` lacks `~/.local/bin`. The menu bar also looks in `~/.local/bin`
directly (`desk`). `make install` then
`open -a DeskSwitchBar`.

**Omarchy widget missing.** `make install` on Linux, then
`omarchy plugin add … --enable`. The plugin checkout is not a substitute
for the CLI.

## Credits

Mouse channel switching is [mxswitch](https://github.com/marcocosta97/mxswitch)
(MIT), vendored in `adapters/mxswitch/`. The follow idea — poll for the
keyboard, then `ChangeHost` — is the same pattern as
[logi_mx_auto_switch](https://github.com/omar16100/logi_mx_auto_switch) and
[CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch).

## License

MIT. See [LICENSE](LICENSE).
