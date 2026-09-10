# weather — Open-Meteo ambient adapter

Role: **`weather`**. Id: **`weather`**. First-class adapter, not a kettle
side-feed and not a kettle capability.

```
~/.local/lib/desk-switch/weather
~/.local/lib/desk-switch/weather.manifest.json
```

Fetches São Paulo (default) ambient temperature, WMO weather code, and
Open-Meteo elevation. Independent of the Fellow host — works when the
kettle is offline.

```bash
desk-switch weather status --json
```

Optional pin:

```json
{
  "adapters": {
    "weather": {
      "enabled": true,
      "backend": "weather",
      "latitude": -23.5505,
      "longitude": -46.6333,
      "timezone": "America/Sao_Paulo",
      "label": "São Paulo"
    }
  }
}
```

The tray slot is `kind: chip` (ambient ° + optional mood / altitude).
Altitude is adapter-owned — hide it with the weather slot’s
`show_altitude` pref in [`docs/ui-config.md`](../../docs/ui-config.md),
not a global HUD flag.

`DESK_SWITCH_WEATHER_URL` overrides the forecast URL (tests). Cache:
`$XDG_CACHE_HOME/desk-switch/weather.json` (10 minutes).
