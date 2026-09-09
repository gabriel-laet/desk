# desk-switch

Hop a Logitech MX Master (and, if you want, an [LG DualUp](https://www.lg.com/us/monitors/lg-28mq780-b))
between a Mac Studio and an Omarchy/Linux desk. Optional: follow the
[HHKB Studio](https://happyhackingkb.com/) when it leaves this host.

One command: **`desk-switch`**. Mouse hop, host map, and DualUp are adapters
the CLI plugs in. `mxswitch` / `lgdualup` are private helpers (in this repo),
not tools you need to learn.

Works on **macOS** and **Linux**. Each machine only ever pushes the mouse
*away*. Install the watcher on every computer you leave from.

**In this repo:** `desk-switch` CLI, `mxswitch` (HID++ Easy-Switch),
`lgdualup` (DualUp USB HID — `macos/lgdualup.c`, `linux/lgdualup.sh`, udev
rule), Omarchy bar plugin, macOS menu bar app, LaunchAgent / systemd units.

The **dualup** adapter does three things on `full` / `pbp`: USB HID toggle
(`lgdualup`), PBP input assignment (Mac=`hdmi1`, Linux=`dp`), and OS
resolution/rotation (`dualup-layout` via displayplacer on macOS, hyprctl on
Linux). Optional `adapters.dualup.peer` SSHes layout-only to the other host.

## How it works

Logitech Enhanced Easy-Switch only links an MX keyboard to an MX mouse. An
HHKB is invisible to Logi Options+. Two paths:

```
Fn+Ctrl+2 on the HHKB
        │
        ▼
 HHKB disconnects from this host
        │
        ▼
 desk-switch watch   (~2s debounce)
        │
        ▼
 mouse adapter (mxswitch) → other Easy-Switch channel
        │
        ▼
 DualUp is left alone

desk-switch to linux          (or the Omarchy / macOS panel)
        │
        ├─ mouse adapter  → Linux channel
        └─ dualup adapter → DualUp input (if configured + USB is on this host)

desk-switch pbp                 (or DualUp PBP in the menu bar / Omarchy panel)
        │
        ├─ lgdualup pbp 50-50
        ├─ lgdualup pbp-assign hdmi1 dp   (Main + Sub; 0xF4 alone cannot set Sub)
        └─ dualup-layout pbp      (Mac 2880x1280@270 / Linux 1280x2880 t0)

desk-switch full
        │
        ├─ lgdualup pbp full
        └─ dualup-layout full     (Mac 2880x2560@270 / Linux 2560x2880 t3)
```

Adapters (more can be added later without renaming the model):

| Adapter | Role | Backend |
|---|---|---|
| **mouse** | HID++ Easy-Switch hop | `~/.local/lib/desk-switch/mxswitch` |
| **hosts** | Mac vs Linux, channels, HHKB follow target | config |
| **dualup** | DualUp USB input + PBP/full + OS layout | `lgdualup` + `dualup-layout` |

`status --json` exposes `adapters.mouse` / `adapters.hosts` / `adapters.dualup`
and still has the Omarchy fields (`target_hint`, `lgdualup`, `hhkb`,
`mouse_channel`).

## Requirements

- HHKB Studio (USB or Bluetooth; VID `04FE` / PID `0016`)
- MX Master 3 / 3S / 4 on matching Easy-Switch channels
- Python 3.9+
- macOS: Xcode Command Line Tools (`clang` for helpers; `swiftc` for the menu bar)
- Linux: `hidraw` + the udev rules below
- DualUp hardware is optional. The USB helper **and** the OS layout helper
  are in-tree (`make install`). Control is USB HID `043e:9a39` — plug that
  cable into the machine that should flip the monitor. macOS layout needs
  `displayplacer`; Linux layout needs Hyprland `hyprctl`.

Pairing that works:

| Device | Channel / slot | Host |
|---|---|---|
| HHKB | Fn+Ctrl+1 (or USB) | Mac |
| HHKB | Fn+Ctrl+2 | Linux |
| MX Master | Easy-Switch 1 | Mac |
| MX Master | Easy-Switch 2 | Linux |

If the HHKB USB cable stays in the Mac *and* you hop the keyboard to Bluetooth
on Linux, the Mac may still enumerate a USB keyboard collection. The watcher
then never fires. Bluetooth on both hosts; treat USB as charging, or unplug
when you hop.

## Install

```bash
git clone https://github.com/gabriel-laet/desk-switch.git
cd desk-switch
```

Keep the clone at `~/.local/share/desk-switch` if you want `git pull && make
install` updates. Put `~/.local/bin` on `PATH`.

`make install` writes:

```
~/.local/bin/desk-switch                 # the CLI
~/.local/bin/hhkb-mx-follow              # same program (legacy name)
~/.local/lib/desk-switch/mxswitch        # mouse adapter
~/.local/lib/desk-switch/lgdualup        # dualup USB helper (in-tree)
~/.local/lib/desk-switch/dualup-layout   # dualup OS layout (displayplacer / hyprctl)
~/.local/bin/mxswitch                    # compat shim → lib/
~/.local/bin/lgdualup                    # compat shim → lib/
~/.config/desk-switch/config.json        # first install only
```

Old configs under `~/.config/hhkb-mx-follow/` still load. Prefer
`~/.config/desk-switch/config.json`.

### macOS

```bash
make install
desk-switch status
```

Grant **Input Monitoring** to `~/.local/lib/desk-switch/mxswitch`
(System Settings → Privacy & Security). The PATH shim is a shell script; TCC
is on the real binary. `mxswitch --setup` opens that pane.

On this Mac, `adapters.hosts.follow_channel` is the *other* machine’s
Easy-Switch slot (2 if the Mac is channel 1).

HHKB follow at login (existing unit name — do not rename if already loaded):

```bash
cp macos/local.hhkb-mx-follow.plist.example \
   ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
# edit both /Users/YOU paths, then:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
```

Log: `~/Library/Logs/hhkb-mx-follow.log`.

Menu bar (optional, same actions as the Omarchy panel): see
[macOS menu bar](#macos-menu-bar).

### Linux

```bash
make install
desk-switch status
```

Mouse hidraw:

```bash
sudo cp linux/42-logitech-hidpp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input "$USER"   # then log out/in once
```

DualUp USB on *this* box (skip if the DualUp cable is in the Mac):

```bash
sudo cp linux/43-lg-dualup.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Set `adapters.hosts.this_host` to `linux` and `follow_channel` to `1` (the
Mac). `make install` does that on a first-time Linux config.

Watcher (existing unit name — `ExecStart` is still `hhkb-mx-follow watch`):

```bash
mkdir -p ~/.config/systemd/user
cp linux/hhkb-mx-follow.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hhkb-mx-follow.service
journalctl --user -u hhkb-mx-follow -f
```

`WantedBy=graphical-session.target` — starts with Hyprland/Omarchy.

## Omarchy plugin

The bar widget lives at the git root (`manifest.json`, id
`glaet.desk-switch`), same layout as
[omarchy-hey-plugin](https://github.com/basecamp/omarchy-hey-plugin). It only
calls `desk-switch`. It does **not** run `make install`.

On the Linux box:

```bash
make install                          # CLI + adapters first
omarchy plugin add https://github.com/gabriel-laet/desk-switch.git --enable
```

That clones into `~/.config/omarchy/plugins/glaet.desk-switch/` and places a
widget on the **right** section. Title is `MAC` / `LNX` / `?` (from
`desk-switch status --json` → `target_hint`). Click for:

| Button | Command |
|---|---|
| Refresh status | `desk-switch status --json` |
| Switch to Mac | `desk-switch to mac` |
| Switch to Linux | `desk-switch to linux` |
| DualUp Full | `desk-switch full` — hidden unless the dualup adapter is present |
| DualUp PBP | `desk-switch pbp` — uses `pbp_mode` from config; same visibility |

Polls about every 15s. Looks up `desk-switch` via `bash -lc` with
`~/.local/bin` on `PATH` (falls back to `hhkb-mx-follow`).

Already cloned this repo on the machine? Enable the checkout instead of
re-adding, then `omarchy plugin validate .`.

Without Omarchy:

```bash
make validate-plugin
```

### Omarchy menu

Merge the keys in [`extensions/omarchy-menu.jsonc`](extensions/omarchy-menu.jsonc)
into `~/.config/omarchy/extensions/omarchy-menu.jsonc`. Do not replace the
file. Rows land under **Trigger → Desk switch**: Status, Switch to Mac /
Linux, DualUp Full / PBP (DualUp rows hide when the helper is missing).

## macOS menu bar

Native `MenuBarExtra`. Same job as the Omarchy panel: title `MAC` / `LNX` /
`?`, click for refresh / to mac / to linux / DualUp full+PBP. Calls
`desk-switch` only (PATH, then `~/.local/bin`). macOS 13+. Ad-hoc signed,
not App Store.

```bash
make install                 # CLI first
make menubar                 # build/DeskSwitchBar.app  (needs swiftc)
make install-menubar         # ~/Applications/DeskSwitchBar.app
open -a DeskSwitchBar
```

`xcode-select --install` if `swiftc` is missing. Refresh every ~15s and
again when the panel opens.

Login item:

```bash
cp macos/local.desk-switch-bar.plist.example \
   ~/Library/LaunchAgents/local.desk-switch-bar.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.desk-switch-bar.plist
```

## CLI

```bash
desk-switch status              # HHKB / mouse / adapters (text)
desk-switch status --json
desk-switch status --hint       # MAC / LNX / ?  (same as: desk-switch hint)
desk-switch to mac              # mouse + DualUp input if configured
desk-switch to linux
desk-switch to linux --mouse-only
desk-switch switch mac          # same as `to mac`
desk-switch switch 2            # mouse only, Easy-Switch 1|2|3
desk-switch pbp                 # USB PBP + hdmi1/dp inputs + OS layout
desk-switch pbp 50-50           # or 50 / 50/50 / on  (lgdualup also accepts full/off)
desk-switch full                # USB full + OS layout (Mac 2880x2560@270 / Linux 2560x2880 t3)
desk-switch watch               # HHKB leave → mouse only
desk-switch watch --dry-run
desk-switch --version
```

`watch` never touches DualUp. Use `to mac` / `to linux` (or a panel) for
mouse + monitor together.

`pbp` / `full` no-op with a message if the dualup adapter is missing. After
a successful USB toggle, `pbp` assigns the cabling pair (Linux DisplayPort
`dp` first, then Mac Studio `hdmi1`) and both commands apply OS layout.
PBP Main/Sub assignment uses `lgdualup pbp-assign` (sub VCPs 0x55/0x5A plus
a Main→swap→Main dance). Plain `input dp` cannot change the sub window
(it stays HDMI2). After assign, layout retries ~8s while EDID catches up.
PBP layout is OS-specific (same on-screen half, different EDID naming):

| Host | PBP | Full |
|---|---|---|
| macOS (displayplacer) | `2880x1280 @ 270°` → on-screen 1280×2880 | `2880x2560 @ 270°` |
| Linux / Omarchy (`hyprctl`, typically `DP-2`) | `1280x2880` transform **0** | `2560x2880` transform **3** |

Not `2560x1440` transform 0 — that was the old Linux helper. If the half
mode is not in EDID yet, the helper exits 2 and `desk-switch` retries.
Set `adapters.dualup.peer` to SSH layout-only to the other machine.

Compat: `hhkb-mx-follow` is the same CLI. `mxswitch` / `lgdualup` on PATH
exec the private helpers.

## Config

Edit [`config.example.json`](config.example.json) →
`~/.config/desk-switch/config.json`.

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
      "display_id": "9134432D-0196-4653-9712-EFCAF1980612",
      "inputs": { "mac": "hdmi1", "linux": "dp" }
    }
  }
}
```

| Key | Meaning |
|---|---|
| `adapters.hosts.this_host` | Machine you are on (`mac` / `linux`) |
| `adapters.hosts.follow_channel` | Easy-Switch slot `watch` pushes the mouse to |
| `adapters.hosts.*.channel` | Easy-Switch slot for `to mac` / `to linux` |
| `adapters.dualup.inputs.*` | DualUp input for that host. PBP uses Mac=`hdmi1`, Linux=`dp` when empty. `to mac\|linux` without `switch_pbp` leaves input alone if empty |
| `adapters.dualup.switch_pbp` | If true, `to mac\|linux` also runs the PBP sequence (USB + inputs + layout) |
| `adapters.dualup.pbp_mode` | Mode for `desk-switch pbp` and for `to` when `switch_pbp` is true |
| `adapters.dualup.display_id` | macOS displayplacer UUID or Hyprland connector. Empty = detect DualUp |
| `adapters.dualup.layout` | Apply OS resolution/rotation after USB (default true) |
| `adapters.dualup.peer` | Optional SSH host; runs `dualup-layout` there (no USB) |
| `poll_interval_s` / `absent_polls_required` | Watcher debounce (defaults 0.5s × 4 ≈ 2s) |

Inputs the helper accepts: `usbc` / `usb-c` / `dp3`, `dp` / `dp1`, `dp2`,
`hdmi1`, `hdmi2`, `auto`. List devices: `lgdualup --list` (or `--info`).

PBP on `to mac|linux` stays off unless `switch_pbp` is true or a host entry
has `"pbp": "…"`. `desk-switch full` / `pbp` always run the dualup adapter
when the USB helper exists.

Desk cabling: Mac Studio = **HDMI1**, Omarchy/Linux = **DisplayPort (`dp`)**.
Do not set Mac to `usbc`.

The panel is physically tilted. macOS reports the tilt as displayplacer
`degree:270`; Hyprland reports the matching half as **`1280x2880` transform 0**
(confirmed on Omarchy `DP-2` @ 59.96 Hz) and full as **`2560x2880` transform 3**.
Not the old Linux `2560x1440` t0. If EDID has not published the half mode
yet, the helper exits 2 and `desk-switch` retries.
macOS needs
[displayplacer](https://github.com/jakehilborn/displayplacer)
(`brew install jakehilborn/jakehilborn/displayplacer`). Linux uses `hyprctl`.

Legacy keys (`mxswitch`, `lgdualup`, `this_host`, `hosts`, `target_channel`)
still load. New installs write the adapters shape.

## Troubleshooting

**`desk-switch status` first.** Check `target_hint`, `adapters.mouse.available`,
`adapters.dualup.available`, and whether DualUp USB was seen (`dualup_info`).

**Watcher never fires (HHKB USB ghost).** Mac still sees the HHKB keyboard
collection (usage page 1 / usage 6) over USB. Unplug the cable or charge-only;
use Bluetooth on both hosts. Probe: macOS `hidutil list`; Linux
`/sys/bus/hid/devices` for `04FE:0016`. A probe error is treated as *present*
so a flaky `hidutil` cannot steal the mouse. Sleep/wake clock jumps disarm
until the HHKB is seen again.

**Mouse does not hop (macOS Input Monitoring).** Grant it to
`~/.local/lib/desk-switch/mxswitch`, not the shim. Click the mouse once if
`--info` fails. After a reinstall the binary path changed — re-grant TCC.

**Mouse hidraw denied (Linux).** udev rule `42-logitech-hidpp.rules`, user in
`input`, then a new login.

**DualUp no-op / not found.** The USB “LG Monitor Controls” cable (`043e:9a39`)
must be in the **host running the command**. Typical desk: cable in the Mac,
so `to linux` from the Mac flips the input; Linux cannot see the device.
`make install` installs the helper; empty `adapters.dualup.inputs` means
`to mac|linux` leaves the input alone. Linux also needs
`43-lg-dualup.rules`. After PBP the host must also get the OS layout —
Mac `2880x1280@270`, Linux `1280x2880` transform 0 (not `2560x1440` t0).
If Linux PBP is still landscape 2560×1440, `make install` again. Set
`adapters.dualup.display_id` (`DP-2` on Omarchy) if auto-detect misses
the DualUp. Optional `adapters.dualup.peer` SSHes layout-only to the
other machine.

**Panel / menu bar shows `?` or “desk-switch not found”.** CLI not installed,
or GUI `PATH` lacks `~/.local/bin`. The menu bar also looks in
`~/.local/bin` directly. `make install` then `open -a DeskSwitchBar`.

**Omarchy widget missing.** `make install` on Linux, then
`omarchy plugin add … --enable`. The plugin checkout is not a substitute for
the CLI.

## Credits

Mouse channel switching is [mxswitch](https://github.com/marcocosta97/mxswitch)
(MIT), vendored in `macos/mxswitch.c` and `linux/mxswitch.py`. The follow
idea — poll for the keyboard, then `ChangeHost` — is the same pattern as
[logi_mx_auto_switch](https://github.com/omar16100/logi_mx_auto_switch) and
[CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch), which only
speak Logitech-to-Logitech.

## License

MIT. See [LICENSE](LICENSE).
