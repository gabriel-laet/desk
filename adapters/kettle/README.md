# kettle — Fellow-style appliance adapter

Role: **`kettle`**. Id: **`kettle`**. Core never `GET`s the kettle.

Fellow Stagg LAN HTTP (`GET /cli?cmd=…`) lives here. DeskSwitchBar / the
Omarchy tray paint the kettle **slot** (`kind: face` — circular gauge +
scoped Heat/Off). There is no standalone Kettle.app in this product.

```
~/.local/lib/desk/kettle
~/.local/lib/desk/kettle.manifest.json
```

## LAN warning

The Stagg CLI is **unauthenticated HTTP on port 80**. Anyone on the LAN
can heat or turn it off. Do not expose that port. Core does not speak
Fellow HTTP.

Set `adapters.kettle.host` to `YOUR_HOST` (DHCP moves it; save a host).

## Host resolution

1. `--host` / `adapters.kettle.host`
2. `KETTLE_HOST` / `FELLOW_HOST` / `STAGG_HOST`
3. `~/.config/kettle/host` then `~/.config/fellow/host`
4. Control verbs (`heat` / `on` / `off`) require a configured host
5. `info` / `status` without a configured host **does not probe**
   (so `status --json` stays fast when you have no kettle)

```bash
desk kettle status --json
desk kettle heat 93
desk kettle off
desk kettle host YOUR_HOST
```

Optional pin:

```json
{
  "adapters": {
    "kettle": {
      "enabled": true,
      "backend": "kettle",
      "host": "YOUR_HOST"
    }
  }
}
```

Weather is **not** this adapter. Ambient ° + altitude live in
`adapters/weather/`. A down kettle must not hide weather.
