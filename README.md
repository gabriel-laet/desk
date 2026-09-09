# desk-switch

One CLI for a Mac Studio + Omarchy/Linux desk. Hop the Logitech MX Master
between Easy-Switch channels, optionally flip an [LG DualUp](https://www.lg.com/us/monitors/lg-28mq780-b)
input, and (if you want) follow the [HHKB Studio](https://happyhackingkb.com/)
when it leaves this host.

**Learn `desk-switch`.** Mouse hop, host map, and DualUp are adapters the CLI
plugs in — not peer tools you are expected to memorize.

```
desk-switch status
desk-switch to mac|linux
desk-switch switch …
desk-switch pbp …
desk-switch full
desk-switch watch
```

Works on **macOS** and **Linux**. Each machine only ever pushes the mouse
*away*. Install the watcher on every computer you leave from.

```
Fn+Ctrl+2 on the HHKB
        │
        ▼
 HHKB disconnects from this host
        │
        ▼
 watcher notices (~2s debounce)
        │
        ▼
 desk-switch → mouse adapter (mxswitch) → other machine

desk-switch to linux
        │
        ├─ mouse adapter  → Easy-Switch channel for Linux
        └─ dualup adapter → DualUp input (if enabled and present)
```

Logitech Enhanced Easy-Switch only links an MX keyboard to an MX mouse. An HHKB
is invisible to Logi Options+. The watcher notices the HHKB leave and the
mouse adapter sends a HID++ `ChangeHost` so the MX Master hops to the same
desk.

## Adapters

desk-switch is the core. You plug in adapters for pieces of the desk:

| Adapter | What it does | Backend (private helper) |
|---|---|---|
| **mouse** | HID++ Easy-Switch hop | `mxswitch` |
| **hosts** | Mac vs Linux, channels, HHKB follow target | config only |
| **dualup** | DualUp input + PBP/full | `lgdualup` |

`make install` puts helpers in a desk-switch-owned layout:

```
~/.local/bin/desk-switch                 # the product
~/.local/lib/desk-switch/mxswitch        # mouse adapter
~/.local/lib/desk-switch/lgdualup        # dualup adapter
~/.config/desk-switch/config.json
```

`mxswitch`, `lgdualup`, and `hhkb-mx-follow` on `PATH` are thin compatibility
shims so old scripts keep working. Prefer `desk-switch`.

`status --json` speaks in adapter terms (`adapters.mouse`, `adapters.hosts`,
`adapters.dualup`) and still includes the Omarchy fields (`target_hint`,
`lgdualup`, `hhkb`, `mouse_channel`).

Room for more adapters later (another monitor, …) without renaming this model.

## Requirements

- HHKB Studio (USB or Bluetooth; VID `04FE` / PID `0016`)
- MX Master 3 / 3S / 4 paired on matching Easy-Switch channels
- Python 3.9+
- macOS: Command Line Tools (`clang`) to build the mouse / DualUp helpers;
  `swiftc` only if you want the menu-bar app
- Linux: `hidraw` access (udev rules included)
- Optional DualUp USB HID (`043e:9a39`) — helper ships in this repo

Pairing that works cleanly:

| Device | Channel / slot | Host |
|---|---|---|
| HHKB | Fn+Ctrl+1 (or USB) | Mac |
| HHKB | Fn+Ctrl+2 | Linux |
| MX Master | Easy-Switch 1 | Mac |
| MX Master | Easy-Switch 2 | Linux |

If you keep the HHKB USB cable plugged into the Mac *and* switch the keyboard
to Bluetooth on the other machine, the Mac may still enumerate a USB keyboard
collection. The watcher then never fires. Use Bluetooth on both hosts and treat
USB as charging only, or unplug when you hop.

## Install

```bash
git clone https://github.com/gabriel-laet/desk-switch.git
cd desk-switch
```

Keep the clone at `~/.local/share/desk-switch` if you want `git pull && make
install` updates. `hhkb-mx-follow` remains an installed alias of `desk-switch`.

### macOS

```bash
make install
# → ~/.local/bin/desk-switch
# → ~/.local/lib/desk-switch/mxswitch
# → ~/.local/lib/desk-switch/lgdualup
# → ~/.config/desk-switch/config.json    (and a copy under hhkb-mx-follow if new)

desk-switch status
```

Grant **Input Monitoring** to `~/.local/lib/desk-switch/mxswitch` if macOS
asks (System Settings → Privacy & Security). `mxswitch --setup` (shim) opens
that pane.

Set `adapters.hosts.follow_channel` to the Easy-Switch slot of the *other*
machine (2 if this Mac is channel 1). On Linux set it to 1 (the Mac).

Optional menu bar (MAC / LNX / ? + the same actions as the Omarchy panel):

```bash
make install-menubar
# → ~/Applications/DeskSwitchBar.app
open -a DeskSwitchBar
```

Needs `swiftc` (Xcode Command Line Tools). Ad-hoc signed, not App Store.
To launch at login:

```bash
cp macos/local.desk-switch-bar.plist.example ~/Library/LaunchAgents/local.desk-switch-bar.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.desk-switch-bar.plist
```

HHKB follow at login (existing unit name — do not rename if already loaded):

```bash
cp macos/local.hhkb-mx-follow.plist.example ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
# edit the two /Users/YOU paths, then:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
```

### Linux (udev + systemd — unchanged unit paths)

```bash
make install
# installs desk-switch, adapter helpers, and PATH shims

sudo cp linux/42-logitech-hidpp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input "$USER"   # then log out/in once

# DualUp USB (if you use that adapter):
sudo cp linux/43-lg-dualup.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# edit ~/.config/desk-switch/config.json
#   adapters.hosts.this_host: linux
#   adapters.hosts.follow_channel: 1          # the Mac

mkdir -p ~/.config/systemd/user
cp linux/hhkb-mx-follow.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hhkb-mx-follow.service
```

The user unit is `WantedBy=graphical-session.target`, so it starts at login
with Hyprland/Omarchy. `ExecStart` still runs `hhkb-mx-follow watch` so an
existing enable stays valid. The program is desk-switch.

## Commands

```bash
desk-switch status              # HHKB / mouse / adapters
desk-switch status --json       # includes adapters.mouse / hosts / dualup
desk-switch status --hint       # MAC / LNX / ?  (bar + menu bar)
desk-switch to mac              # mouse → Mac channel; DualUp input if configured
desk-switch to linux
desk-switch switch mac          # same as `to mac`
desk-switch switch 2            # mouse only, Easy-Switch channel 2
desk-switch pbp 50-50           # dualup adapter; no-op if missing
desk-switch full                # DualUp full-screen; no-op if missing
desk-switch watch               # follow HHKB departures (mouse only)
desk-switch watch --dry-run
```

`watch` only pushes the mouse when the HHKB leaves. It does not touch the
DualUp. Use `to mac` / `to linux` (or the Omarchy / macOS UI) when you want
mouse + monitor together.

Logs:

- macOS LaunchAgent: `~/Library/Logs/hhkb-mx-follow.log`
- Linux systemd: `journalctl --user -u hhkb-mx-follow -f`

## Config

`~/.config/desk-switch/config.json`. Register adapters:

```json
{
  "adapters": {
    "mouse": { "enabled": true },
    "hosts": {
      "this_host": "mac",
      "follow_channel": 2,
      "mac": { "channel": 1 },
      "linux": { "channel": 2 }
    },
    "dualup": {
      "enabled": true,
      "pbp_mode": "50-50",
      "switch_pbp": false,
      "inputs": { "mac": "", "linux": "" }
    }
  }
}
```

Set `adapters.dualup.inputs` to names from `desk-switch status` / the DualUp
helper `--list` (`usbc`, `dp`, `hdmi1`, …). Leave them empty to switch the
mouse only.

PBP is left alone on `to mac|linux` unless `switch_pbp` is true or a per-host
`pbp` field is set. `desk-switch full` / `desk-switch pbp <mode>` always pass
through to the dualup adapter when it exists.

Legacy keys (`mxswitch`, `lgdualup`, `this_host`, `hosts`, `target_channel`)
still load. New installs write the adapters shape.

## Omarchy plugin

`manifest.json` lives at the **git root**, same pattern as
[omarchy-hey-plugin](https://github.com/basecamp/omarchy-hey-plugin). The bar
widget only calls `desk-switch`:

```bash
make install
omarchy plugin add https://github.com/gabriel-laet/desk-switch.git --enable
```

That clones the repo into `~/.config/omarchy/plugins/glaet.desk-switch/` and
places the bar widget on the **right** section. Title is MAC / LNX / ?. Click
it for:

- status refresh
- Switch to Mac / Switch to Linux
- DualUp Full / DualUp PBP (only listed when the dualup adapter is present)

The plugin does **not** run `make install`; the CLI must already be on `PATH`
(or in `~/.local/bin`).

Validate without Omarchy:

```bash
make validate-plugin
# or, on a machine with Omarchy:
omarchy plugin validate .
```

### Menu snippet

Copy the keys from [`extensions/omarchy-menu.jsonc`](extensions/omarchy-menu.jsonc)
into `~/.config/omarchy/extensions/omarchy-menu.jsonc` (merge, do not replace
your other rows). They land under **Trigger → Desk switch**.

## macOS menu bar

Same job as the Omarchy widget, native `MenuBarExtra`:

```bash
make menubar              # builds build/DeskSwitchBar.app (needs swiftc)
make install-menubar      # copies to ~/Applications/DeskSwitchBar.app
open -a DeskSwitchBar
```

- Title: `MAC` / `LNX` / `?` (from `desk-switch status --json` → `target_hint`)
- Click: status summary, Refresh, Switch to Mac / Linux, DualUp Full / PBP
  when the dualup adapter is available
- Refresh every ~15s and again when the panel opens
- Shells out to `desk-switch` only (PATH, then `~/.local/bin`)

Unsigned / ad-hoc local build. macOS 13+.

## How it decides the keyboard left

- **macOS:** `hidutil list` for the HHKB keyboard collection (usage page 1, usage 6).
- **Linux:** `/sys/bus/hid/devices` entry matching `04FE:0016`.

A probe error is treated as *present*, so a flaky `hidutil` cannot steal the
mouse. Sleep/wake clock jumps disarm the watcher until the HHKB is seen again.

## Troubleshooting

- `desk-switch status` — are adapters `available` or `missing`?
- Mouse hop fails on Mac: Input Monitoring on `~/.local/lib/desk-switch/mxswitch`
- DualUp no-op: helper missing, or `adapters.dualup.inputs` empty
- Watcher never fires: HHKB still enumerated over USB on this host
- Menu bar shows `?` and “desk-switch not found”: `make install` and keep
  `~/.local/bin` on your GUI `PATH`, or use the default `~/.local/bin` lookup

## Credits

Mouse channel switching is [mxswitch](https://github.com/marcocosta97/mxswitch)
(MIT), vendored in `macos/mxswitch.c` and `linux/mxswitch.py` as the mouse
adapter. The follow idea — poll for the keyboard, then `ChangeHost` — is the
same pattern as
[logi_mx_auto_switch](https://github.com/omar16100/logi_mx_auto_switch) and
[CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch), which only
speak Logitech-to-Logitech.

## License

MIT. See [LICENSE](LICENSE).
