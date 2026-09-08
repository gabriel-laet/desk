# desk-switch

Desk switcher for a Mac Studio + Omarchy/Linux desk: hop a Logitech MX Master
between Easy-Switch channels and, when you want it, flip an [LG DualUp](https://www.lg.com/us/monitors/lg-28mq780-b)
input. The original HHKB follow behaviour is still here — when the
[HHKB Studio](https://happyhackingkb.com/) leaves this host, the mouse is
pushed to the other machine.

Logitech Enhanced Easy-Switch only links an MX keyboard to an MX mouse. An HHKB
is invisible to Logi Options+. This repo watches the HHKB leave the current
machine and sends the mouse a HID++ `ChangeHost` command so it hops to the same
desk. A unified `desk-switch` CLI (legacy name: `hhkb-mx-follow`) can also
drive the DualUp through an optional `lgdualup` helper, plus an Omarchy bar
widget.

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
 mxswitch 2   →  MX Master joins the other machine

desk-switch to linux
        │
        ├─ mxswitch <linux channel>
        └─ lgdualup input <name>   (if lgdualup is on PATH)
```

## Requirements

- HHKB Studio (USB or Bluetooth; VID `04FE` / PID `0016`)
- MX Master 3 / 3S / 4 paired on matching Easy-Switch channels
- Python 3.9+
- macOS: Command Line Tools (`clang`) to build `mxswitch`
- Linux: `hidraw` access (udev rule included)
- Optional: `lgdualup` on `PATH` for DualUp USB HID monitor control
  (device `043e:9a39`). That helper is **not** in this repo — call it by
  PATH, do not invent DDC codes here.

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
# → ~/.local/bin/mxswitch
# → ~/.local/bin/desk-switch
# → ~/.local/bin/hhkb-mx-follow          (same program)
# → ~/.config/desk-switch/config.json    (and a copy under hhkb-mx-follow if new)

mxswitch --info          # click the mouse if this fails
desk-switch status

# run at login (existing unit name — do not rename if already loaded)
cp macos/local.hhkb-mx-follow.plist.example ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
# edit the two /Users/YOU paths, then:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
```

Grant **Input Monitoring** to `~/.local/bin/mxswitch` if macOS asks
(System Settings → Privacy & Security). `mxswitch --setup` opens that pane.

Set `target_channel` to the Easy-Switch slot of the *other* machine (2 if this
Mac is channel 1). On Linux set it to 1 (the Mac).

### Linux (udev + systemd — unchanged paths)

```bash
make install
# installs mxswitch, desk-switch, and the hhkb-mx-follow alias

sudo cp linux/42-logitech-hidpp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input "$USER"   # then log out/in once

# edit ~/.config/desk-switch/config.json  (or ~/.config/hhkb-mx-follow/config.json)
#   this_host: linux
#   target_channel: 1          # the Mac
#   mxswitch: ~/.local/bin/mxswitch

mkdir -p ~/.config/systemd/user
cp linux/hhkb-mx-follow.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hhkb-mx-follow.service
```

The user unit is `WantedBy=graphical-session.target`, so it starts at login
with Hyprland/Omarchy. `ExecStart` still runs `hhkb-mx-follow watch` so an
existing enable stays valid. Binaries live in `~/.local/bin`.

## Commands

```bash
desk-switch status              # HHKB present? MX channel? DualUp if lgdualup exists
desk-switch status --json
desk-switch status --hint       # MAC / LNX / ?  (bar widget)
desk-switch to mac              # mouse → Mac channel; DualUp input if configured
desk-switch to linux
desk-switch switch mac          # same as `to mac`
desk-switch switch 2            # mouse only, Easy-Switch channel 2
desk-switch pbp 50-50           # lgdualup pbp <mode>; no-op if missing
desk-switch full                # lgdualup pbp full; no-op if missing
desk-switch watch               # follow HHKB departures (mouse only)
desk-switch watch --dry-run

hhkb-mx-follow status           # legacy name — same CLI
hhkb-mx-follow watch
hhkb-mx-follow switch 2
mxswitch --info
mxswitch 2
```

`watch` is unchanged: it only pushes the mouse when the HHKB leaves. It does
not touch the DualUp. Use `to mac` / `to linux` (or the Omarchy widget) when
you want mouse + monitor together.

Logs:

- macOS LaunchAgent: `~/Library/Logs/hhkb-mx-follow.log`
- Linux systemd: `journalctl --user -u hhkb-mx-follow -f`

## DualUp (`lgdualup`)

`lgdualup` is an optional binary Gabriel already keeps at `~/.local/bin/lgdualup`
on the Mac (not vendored here):

```bash
lgdualup --info
lgdualup --list
lgdualup input <name>
lgdualup pbp <mode>
```

desk-switch only invokes those subcommands. Put the binary on `PATH` (or set
`lgdualup` in config to an absolute path). Set `hosts.mac.dualup_input` and
`hosts.linux.dualup_input` to names from `lgdualup --list`. Leave them empty to
switch the mouse only.

PBP is left alone unless you set `switch_pbp: true` or a per-host `pbp` field.
`desk-switch full` / `desk-switch pbp <mode>` always pass through to
`lgdualup` when it exists, and print a clear no-op message otherwise.

## Omarchy plugin

`manifest.json` lives at the **git root**, same pattern as
[omarchy-hey-plugin](https://github.com/basecamp/omarchy-hey-plugin), so this
works:

```bash
# still install the CLI on the Linux box
make install

omarchy plugin add https://github.com/gabriel-laet/desk-switch.git --enable
```

That clones the repo into `~/.config/omarchy/plugins/glaet.desk-switch/` and
places the bar widget on the **right** section. Click it for:

- status refresh
- Switch to Mac / Switch to Linux
- DualUp Full / DualUp PBP (only listed when `lgdualup` is on PATH)

The widget shells out to `desk-switch` via `bash -lc` (falls back to
`hhkb-mx-follow`). The plugin does **not** run `make install`; the CLI must
already be on `PATH`.

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

## How it decides the keyboard left

- **macOS:** `hidutil list` for the HHKB keyboard collection (usage page 1, usage 6).
- **Linux:** `/sys/bus/hid/devices` entry matching `04FE:0016`.

A probe error is treated as *present*, so a flaky `hidutil` cannot steal the
mouse. Sleep/wake clock jumps disarm the watcher until the HHKB is seen again.

## Credits

Mouse channel switching is [mxswitch](https://github.com/marcocosta97/mxswitch)
(MIT), vendored in `macos/mxswitch.c` and `linux/mxswitch.sh`. The follow idea
— poll for the keyboard, then `ChangeHost` — is the same pattern as
[logi_mx_auto_switch](https://github.com/omar16100/logi_mx_auto_switch) and
[CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch), which only
speak Logitech-to-Logitech.

## License

MIT. See [LICENSE](LICENSE).
