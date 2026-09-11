# desk

One CLI. Adapters do the hardware. Shells only paint.

**desk** is a small orchestrator for a two-host desk: hop a Logitech MX
Master (and, if you want, an [LG DualUp](https://www.lg.com/us/monitors/lg-28mq780-b))
between macOS and Linux, optionally follow an
[HHKB Studio](https://happyhackingkb.com/), and hang extra tray slots
(smart-home light, Fellow-style kettle, weather) off the same contract.

```
core     = config · status · watch · to/full/pbp · adapter registry · slots
adapters = mouse / keyboard / display / smarthome / kettle / weather
shells   = macOS DeskSwitchBar + Omarchy tray — they paint `slots`, nothing else
```

Taught command: **`desk`**. LaunchAgent / systemd *unit filenames* can
stay (`local.hhkb-mx-follow`, `hhkb-mx-follow.service`); they invoke
`desk`.

Works on **macOS** and **Linux**. Each machine only ever pushes the mouse
*away*. Install the watcher on every computer you leave from.

**Docs:** [RFC 0001](docs/rfc/0001-rust-core-and-adapters.md) ·
[RFC 0002](docs/rfc/0002-desk-product-kettle-shared-tray.md) ·
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

Pin with `adapters.<role>.backend` / `path`, or drop a binary +
`*.manifest.json` in `~/.local/lib/desk/` (`$DESK_LIB`). Third parties
can also ship `desk-<id>` on PATH. See [Plug another adapter](#plug-another-adapter).

`adapters.dualup` is a legacy alias of `adapters.display`.

## How it works

Logitech Enhanced Easy-Switch only links an MX keyboard to an MX mouse. An
HHKB is invisible to Logi Options+. Two paths:

```
Fn+Ctrl+2 on the HHKB          Fn+Ctrl+0 (HHKB → USB)
        │                              │
        ▼                              ▼
 HHKB disconnects (BT hop)      USB appears on the host
        │                       that has the cable
        ▼                              │
 desk watch (~2s)                      ▼
        │                       watch: rising hhkb_usb
        ▼                              │
 mouse → other Easy-Switch             ▼
 DualUp is left alone           desk to <this_host>
                                (mouse + DualUp input)

desk to linux                 (or the Omarchy / macOS panel)
        │
        ├─ mouse adapter  → Linux channel
        └─ dualup adapter → DualUp input (if configured + USB is on this host)

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

**Lock screen / greeter:** keep the HHKB USB cable in the machine you are
unlocking. `Fn+Ctrl+0` selects USB — `watch` then pulls mouse + DualUp
input to whichever host has the cable.

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
| MX Master | Easy-Switch 1 | Mac |
| MX Master | Easy-Switch 2 | Linux |

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
a widget on the **right** section. The strip title is quiet `bar_strip`
(e.g. `LNX  PBP`) from `desk status --json`. Dense `bar_label` stays in
JSON and in the click panel chips. `ui.tray.density: "chips"` paints
`bar_label` in the strip.

| Button | Command |
|---|---|
| Refresh status | `desk status --json` |
| Switch to Mac | `desk to mac` |
| Switch to Linux | `desk to linux` |
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

Native `MenuBarExtra`. Same job as the Omarchy panel: strip is `slots`
(composite `NSImage`) or quiet `bar_strip`. Click for a modular HUD:
host strip first, then each enabled slot once by `kind`. **Configure
tray** drag-reorders slots and writes `ui` in `~/.config/desk/config.json`.
Calls `desk` only (PATH, then `~/.local/bin`).
macOS 13+. Ad-hoc signed, not App Store.

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
desk status --hint       # MAC / LNX / ?  (same as: desk hint)
desk to mac              # mouse + DualUp input if configured
desk to linux
desk to linux --mouse-only
desk switch mac          # same as `to mac`
desk switch 2            # mouse only, Easy-Switch 1|2|3
desk pbp                 # USB PBP + hdmi1/dp inputs + OS layout
desk pbp 50-50           # or 50 / 50/50 / on  (lgdualup also accepts full/off)
desk full                # USB full + OS layout
desk layout              # re-apply full or PBP from the live DualUp geometry
desk watch               # HHKB leave → mouse away; USB appear → to this host
desk watch --dry-run
desk smarthome list      # devices (and entities when that API works)
desk smarthome status    # light-oriented snapshot
desk smarthome on        # desk light on
desk smarthome off       # desk light off
desk kettle status --json
desk kettle heat 93
desk kettle off
desk weather status --json
desk --version
```

`watch` never touches DualUp. Use `to mac` / `to linux` (or a panel) for
mouse + monitor together.

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
| `ui.tray.density` | `strip` (default, quiet) or `chips` (dense `bar_label` in the bar) |
| `ui.tray.lights` | If true, `bar_strip` may include a lights on/off mark |
| `ui.tray.slots` | Order + show/hide + flavor. Default **weather → kettle → dualup**. [docs/ui-config.md](docs/ui-config.md) |
| `adapters.hosts.this_host` | Machine you are on (`mac` / `linux`) |
| `adapters.hosts.follow_channel` | Easy-Switch slot `watch` pushes the mouse to |
| `adapters.hosts.*.channel` | Easy-Switch slot for `to mac` / `to linux` |
| `adapters.dualup.inputs.*` | DualUp input for that host. PBP uses Mac=`hdmi1`, Linux=`dp` when empty |
| `adapters.dualup.switch_pbp` | If true, `to mac\|linux` also runs the PBP sequence |
| `adapters.dualup.pbp_mode` | Mode for `desk pbp` and for `to` when `switch_pbp` is true |
| `adapters.dualup.display_id` | macOS displayplacer UUID or Hyprland connector. Empty = detect DualUp |
| `adapters.dualup.layout` | Apply OS resolution/rotation after USB (default true) |
| `adapters.dualup.layout_settle_s` | Seconds to wait after USB before OS layout. Default 0.5 |
| `adapters.dualup.peer` | Optional SSH host; runs `dualup-layout` there (no USB) |
| `adapters.hosts.follow_hhkb_usb` | If true (default), `watch` treats HHKB USB appearance as `to <this_host>` |
| `poll_interval_s` / `absent_polls_required` | Watcher debounce (defaults 0.5s × 4 ≈ 2s) |

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
`appliance.off`. Weather adapters list `weather.status`.

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

## Troubleshooting

**`desk status` first.** Check `target_hint` / `bar_label`,
`hhkb_transport` (`usb` / `bluetooth` / `both` / `absent`), `hhkb_usb`,
`mouse_channel` (live or cached), `adapters.mouse.available`,
`adapters.dualup.available`, and whether DualUp USB was seen (`dualup_info`).

**Watcher never fires (HHKB USB ghost).** Mac still sees the HHKB over USB.
That is now a *feature* when `follow_hhkb_usb` is on: USB appearance pulls
the desk here instead of hopping the mouse away. To hop with BT only, unplug
or use charge-only USB. A probe error is treated as *present* so a flaky
`hidutil` cannot steal the mouse.

**Bar shows `?` on Mac while Linux is focused.** The MX Master is on the
Linux Easy-Switch channel, so Mac `mxswitch --info` fails (mouse asleep /
other host). Status remembers the last live channel and, if
`adapters.dualup.peer` (or `adapters.hosts.peer`) is set, peeks the peer
over SSH (`status --json --local` skips that).

**Mouse does not hop (macOS Input Monitoring).** Grant it to
`~/.local/lib/desk/mxswitch`, not the shim. Click the mouse once if
`--info` fails. After a reinstall the binary path changed — re-grant TCC.

**Mouse hidraw denied (Linux).** udev rule `42-logitech-hidpp.rules`, user
in `input`, then a new login.

**DualUp no-op / not found.** The USB “LG Monitor Controls” cable
(`043e:9a39`) must be in the **host running the command**. Empty
`adapters.dualup.inputs` means `to mac|linux` leaves the input alone.
After PBP / full the host must also get the OS layout. Optional
`adapters.dualup.peer` SSHes layout-only to the other machine.

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
