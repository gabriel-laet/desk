# hhkb-mx-follow

Make a Logitech MX Master follow an [HHKB Studio](https://happyhackingkb.com/) when you switch the keyboard to another computer.

Logitech Enhanced Easy-Switch only links an MX keyboard to an MX mouse. An HHKB is invisible to Logi Options+. This repo watches the HHKB leave the current machine and sends the mouse a HID++ `ChangeHost` command so it hops to the same desk.

Works on **macOS** and **Linux**. Each machine only ever pushes the mouse *away*. Install the watcher on every computer you leave from.

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
```

## Requirements

- HHKB Studio (USB or Bluetooth; VID `04FE` / PID `0016`)
- MX Master 3 / 3S / 4 paired on matching Easy-Switch channels
- Python 3.9+
- macOS: Command Line Tools (`clang`) to build `mxswitch`
- Linux: `hidraw` access (udev rule included)

Pairing that works cleanly:

| Device | Channel / slot | Host |
|---|---|---|
| HHKB | Fn+Ctrl+1 (or USB) | Mac |
| HHKB | Fn+Ctrl+2 | Linux |
| MX Master | Easy-Switch 1 | Mac |
| MX Master | Easy-Switch 2 | Linux |

If you keep the HHKB USB cable plugged into the Mac *and* switch the keyboard to Bluetooth on the other machine, the Mac may still enumerate a USB keyboard collection. The watcher then never fires. Use Bluetooth on both hosts and treat USB as charging only, or unplug when you hop.

## Install

```bash
git clone https://github.com/gabriel-laet/hhkb-mx-follow.git
cd hhkb-mx-follow
```

### macOS

```bash
make install
# → ~/.local/bin/mxswitch
# → ~/.local/bin/hhkb-mx-follow
# → ~/.config/hhkb-mx-follow/config.json   (created from the example if missing)

mxswitch --info          # click the mouse if this fails
hhkb-mx-follow status

# run at login
cp macos/local.hhkb-mx-follow.plist.example ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
# edit the two /Users/YOU paths, then:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.hhkb-mx-follow.plist
```

Grant **Input Monitoring** to `~/.local/bin/mxswitch` if macOS asks
(System Settings → Privacy & Security). `mxswitch --setup` opens that pane.

Set `target_channel` to the Easy-Switch slot of the *other* machine (2 if this Mac is channel 1).

### Linux

```bash
make install
# installs the Python mxswitch + the watcher

sudo cp linux/42-logitech-hidpp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input "$USER"   # then log out/in once

# edit ~/.config/hhkb-mx-follow/config.json
#   target_channel: 1          # the Mac
#   mxswitch: ~/.local/bin/mxswitch

mkdir -p ~/.config/systemd/user
cp linux/hhkb-mx-follow.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hhkb-mx-follow.service
```

The user unit is `WantedBy=graphical-session.target`, so it starts at login with Hyprland/Omarchy. Binaries live in `~/.local/bin`; keep the clone at `~/.local/share/hhkb-mx-follow` if you want `git pull && make install` updates.

## Commands

```bash
hhkb-mx-follow status           # is the HHKB here? which MX channel?
hhkb-mx-follow watch            # follow departures (what the service runs)
hhkb-mx-follow watch --dry-run  # log only
hhkb-mx-follow switch 2         # push the mouse now
mxswitch --info
mxswitch 2
```

Logs:

- macOS LaunchAgent: `~/Library/Logs/hhkb-mx-follow.log`
- Linux systemd: `journalctl --user -u hhkb-mx-follow -f`

## How it decides the keyboard left

- **macOS:** `hidutil list` for the HHKB keyboard collection (usage page 1, usage 6).
- **Linux:** `/sys/bus/hid/devices` entry matching `04FE:0016`.

A probe error is treated as *present*, so a flaky `hidutil` cannot steal the mouse. Sleep/wake clock jumps disarm the watcher until the HHKB is seen again.

## Credits

Mouse channel switching is [mxswitch](https://github.com/marcocosta97/mxswitch) (MIT), vendored in `macos/mxswitch.c` and `linux/mxswitch.sh`. The follow idea — poll for the keyboard, then `ChangeHost` — is the same pattern as [logi_mx_auto_switch](https://github.com/omar16100/logi_mx_auto_switch) and [CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch), which only speak Logitech-to-Logitech.

## License

MIT. See [LICENSE](LICENSE).
