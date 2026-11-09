# wallpaper — per-output wallpaper adapter

Role: **`wallpaper`**. Id: **`wallpaper`**. Capability: **`wallpaper.apply`**.

```
~/.local/lib/desk/wallpaper
~/.local/lib/desk/wallpaper.manifest.json
```

Off until `adapters.wallpaper.enabled` is true. Core does not name a
monitor. This adapter asks the host which outputs are connected, decides
portrait vs landscape from each output's logical size, and sets an image.
Source files are never modified.

```bash
desk wallpaper apply
desk wallpaper status --json
```

`geometry` prints the logical sizes and does not change the desktop.
`apply` / `status` with `enabled: false` do nothing.

## Aspect

Portrait when logical height is greater than logical width. Otherwise
landscape, including squares.

macOS reads `NSScreen` frames (already oriented) via
`osascript -l JavaScript`. If that fails, it falls back to
`system_profiler SPDisplaysDataType -json` and uses the reported
resolution as-is.

Linux / Hyprland reads `hyprctl monitors -j`. `width` / `height` are the
mode pixels. Transforms **1, 3, 5, and 7** swap the axes (90° / 270°).
Transforms 0, 2, 4, and 6 do not. A `1920×1080` mode with transform 3 is
portrait. A mode stored as `1280×2880` with transform 3 swaps to
landscape `2880×1280`.

Per-output keys match the connector (`DP-1`) or the display description.
An override may set only `landscape` or only `portrait`; the other
orientation falls back to the global path. `~` is expanded.

Exact files are checked first. `exact_dir` holds
`<exact_prefix>-<W>x<H>.png` (for example `desk-2560x1440.png`,
`desk-1920x1080.png`, `desk-2560x2880.png`). W×H is the mode size
**after** rotation and **not** divided by scale. If that png is missing,
a single `*-<W>x<H>.*` in the same directory is used. More than one
wildcard match is ignored and the portrait/landscape paths are used.

## Fit

Cover / fill: scale uniformly and crop. Never stretch.

| Setter | How fill is requested |
|---|---|
| macOS | System Events sets `desktop N`'s picture (NSScreen name, else index). NSWorkspace then sets the image with `NSWorkspaceDesktopImageScalingKey` = `NSImageScaleProportionallyUpOrDown` (3) and `NSWorkspaceDesktopImageAllowClippingKey` = true. Value 2 is center and is not used. |
| swaybg | `-m fill` |
| hyprpaper | default cover (`preload`, then `wallpaper NAME,path`) |
| Omarchy shell | `PreserveAspectCrop` |

Those setters crop at display time, so the source path is passed through.
If a setter cannot crop, a derived file is written under
`~/.cache/desk/wallpaper/` (`$XDG_CACHE_HOME/desk/wallpaper`, or
`DESK_WALLPAPER_CACHE`). The source is only read.

## Which Linux setter

First match:

1. **hyprpaper is running** — per output via `hyprctl hyprpaper`. Cover.
2. **swaybg is running** — stop `swaybg` processes (they cannot reload) and start one `swaybg -o NAME -i PATH -m fill` per output. This does not stop hyprpaper or the Omarchy shell.
3. **`omarchy-shell background setForScreen`** — per output, when a read-only probe (`background assignments`) shows the method is actually there (for example the Backdrop plugin). Stock Omarchy does not implement this.
4. **`omarchy-theme-bg-set --monitor`** — per output, when that flag exists in the installed script. The stock script takes one file and has no `--monitor`.
5. **Stock Omarchy** (`omarchy-theme-bg-set`, else `omarchy-shell background set`) — **one image**. The shell paints every output from a single current background. The image is chosen from `primary_output` when that output is connected (connector or display name), otherwise from the output with the largest logical area (name breaks ties). The shell crops.
6. **`swaybg` is installed and nothing else owns the background** — same per-output `-m fill` launch as (2).
7. Otherwise apply fails with a logged error. `desk` commands that triggered it still succeed.

A running hyprpaper or swaybg is preferred over Omarchy's single-image
path. On Omarchy Quattro the shell usually owns the background and
neither daemon is running, so (5) applies. Stop hyprpaper/swaybg if the
shell is what you actually see and those daemons are also running.

`desk watch` polls this adapter's `geometry` every few seconds (faster
while a change is settling) and re-applies when the set of
`(output, logical width×height)` stays different for about 2 seconds.
The Hyprland event socket is not required. Failures are logged and do
not stop the watcher.

## Config

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
        "DP-2": {
          "portrait": "~/Pictures/wallpaper-portrait.jpg"
        }
      }
    }
  }
}
```

`primary_output` is only used when the setter can take one image.
Leave `enabled` false (the default) and nothing is changed.

## Host setup

`make install` copies the adapter into `~/.local/lib/desk/`.

- **macOS** — no extra package. `osascript` is enough.
- **Linux / Hyprland** — one of `hyprpaper`, `swaybg`, or Omarchy's background tools (`omarchy-theme-bg-set` / `omarchy-shell`). `hyprctl` is required to list outputs.
- **`desk watch`** — already the login watcher. It re-applies on hotplug, mode, and transform changes when wallpaper is enabled.

`DESK_WALLPAPER_OUTPUTS` and `DESK_WALLPAPER_PROBES` replace live
enumeration and setter detection (tests and debugging).
