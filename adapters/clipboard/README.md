# clipboard

Role: **clipboard**. Id: **clipboard**. Core never calls `pbpaste` or `wl-copy`.

```
~/.local/lib/desk/clipboard
~/.local/lib/desk/clipboard.manifest.json
```

Reads and writes the local clipboard as JSON. Text is `text/plain` UTF-8.
An `image/png` payload is accepted only when the caller passes `--images`.
Sensitive items are reported with `sensitive: true` and an empty body.

macOS uses `pbpaste` / `pbcopy`. A pasteboard that advertises
`org.nspasteboard.ConcealedType` or `org.nspasteboard.TransientType` is skipped.
Wayland uses `wl-paste` / `wl-copy` from `wl-clipboard`. If
`WAYLAND_DISPLAY` is unset, the helper uses the first `wayland-*` socket
in `XDG_RUNTIME_DIR` (or `/run/user/<uid>`). A mime
`x-kde-passwordManagerHint` of `secret` is skipped.

`desk watch` is what polls and pushes. `desk clip push` and `desk clip pull`
are the manual path. Peers receive `desk clip apply` on stdin. The bytes
are not written to the log.
