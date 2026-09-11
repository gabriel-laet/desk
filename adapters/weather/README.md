# weather — Open-Meteo ambient adapter

Role: **`weather`**. Id: **`weather`**. First-class adapter, not a kettle
side-feed and not a kettle capability.

```
~/.local/lib/desk/weather
~/.local/lib/desk/weather.manifest.json
```

Fetches ambient temperature, WMO weather code, and Open-Meteo elevation
for the coordinates you pin. Independent of the kettle host — works when
the kettle is offline.

```bash
desk weather status --json
```

Optional pin:

```json
{
  "adapters": {
    "weather": {
      "enabled": true,
      "backend": "weather",
      "latitude": 0,
      "longitude": 0,
      "timezone": "UTC",
      "label": "Home"
    }
  }
}
```

The tray slot is `kind: chip` (ambient ° + optional mood / altitude).
Altitude is adapter-owned — hide it with the weather slot’s
`show_altitude` pref in [`docs/ui-config.md`](../../docs/ui-config.md),
not a global HUD flag.

`DESK_WEATHER_URL` / `DESK_SWITCH_WEATHER_URL` overrides the forecast URL
(tests). Cache: `$XDG_CACHE_HOME/desk/weather.json` (10 minutes), with a
read-fallback to the legacy `desk-switch` cache dir.
