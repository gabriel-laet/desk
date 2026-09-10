# kettle — Fellow Stagg appliance adapter

Role: **`kettle`**. Id: **`kettle`**. Core never `GET`s the kettle.

Fellow Stagg LAN HTTP (`GET /cli?cmd=…`) lives here. The old
`gabriel-laet/kettle` repo was hard-deleted; this adapter is the source
of truth. DeskSwitchBar / the Omarchy tray paint the kettle **slot** —
there is no standalone Kettle.app in this product.

```
~/.local/lib/desk-switch/kettle
~/.local/lib/desk-switch/kettle.manifest.json
```

## LAN warning

The Stagg CLI is **unauthenticated HTTP on port 80**. Anyone on the LAN
can heat or turn it off. Do not expose that port. Core does not speak
Fellow HTTP.

Typical desk host: `192.168.3.36` (DHCP moves it; save a host).

## Host resolution

1. `--host` / `adapters.kettle.host`
2. `KETTLE_HOST` / `FELLOW_HOST` / `STAGG_HOST`
3. `~/.config/kettle/host` then `~/.config/fellow/host`
4. Default `192.168.3.36` for control verbs (`heat` / `on` / `off`)
5. `info` / `status` without a configured host **does not probe** the
   default (so `status --json` stays fast when you have no kettle)

```bash
desk-switch kettle status --json
desk-switch kettle heat 93
desk-switch kettle off
desk-switch kettle host 192.168.3.36
```

Optional pin:

```json
{
  "adapters": {
    "kettle": {
      "enabled": true,
      "backend": "kettle",
      "host": "192.168.3.36"
    }
  }
}
```

Weather is **not** this adapter. Ambient ° + altitude live in
`adapters/weather/`. A down kettle must not hide weather.
