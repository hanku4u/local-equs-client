# M2 smoke test

End-to-end manual verification that the server-integration milestone works:
fresh install → check updates → download → chart. **Internal-only test —
do not release the saved views built here**, since canonical names land in
M3 and any saved selection will need to be rebuilt.

This is the **M2 exit criterion**.

## Prerequisites

- A backend running with seeded data:
  ```bash
  # in the server repo
  ./scripts/seed-dev --with-parquet
  ```
- Server URL reachable from the dev machine.
- Latest `main` of the client checked out and installed:
  ```bash
  python3.12 -m venv .venv
  .venv/bin/pip install -e ".[dev]"
  ```

## Setup

1. Wipe the client's app dir so the run is genuinely fresh:
   ```bash
   export LOCAL_EQUS_APP_DIR="$PWD/.equs-fresh"
   rm -rf "$LOCAL_EQUS_APP_DIR"
   ```
2. Launch the client:
   ```bash
   .venv/bin/python -m local_equs_client
   ```

## Checklist

### First-run wizard

- [ ] **First-run wizard** appears with the server URL prompt and a Skip button.
- [ ] Enter the dev server URL → Save. Wizard closes; the app continues.
- [ ] `cat "$LOCAL_EQUS_APP_DIR/config.toml"` shows `data_dir = …` and
      `server_url = "https://…"` lines.
- [ ] `sqlite3 "$LOCAL_EQUS_APP_DIR/state.db" "SELECT value FROM app_state WHERE key='client_id'"`
      returns a UUID — the client identity from C2.2.

### Settings + Local Library

- [ ] **File → Settings…** shows the data dir + server URL fields. Edit the
      server URL, save; reopen — the new value sticks.
- [ ] **View → Local Library…** opens the table. With nothing downloaded yet
      it's empty; the footer says `Used: 0 B`.

### Manifest fetch + diff

- [ ] **View → Updates…** opens the panel. Within ~1 s the summary shows
      `N files to download · 0 archived locally` against the dev manifest.
- [ ] Tools group as parent rows; expanding shows individual files with size
      columns.
- [ ] `sqlite3 … "SELECT etag FROM cached_manifest"` returns the ETag the
      server sent.
- [ ] **Refresh** in the panel re-fetches; if the manifest hasn't changed the
      server should answer 304 (verify in server logs / DevTools-equivalent).

### Selective download

- [ ] Check 2-3 individual files across two tools, hit **Download selected**.
- [ ] Status column moves to `downloading…` then `done` per file. The C0.5
      thread pool runs them in parallel.
- [ ] Cancel in the middle of a longer download — status shows the cancelled
      message, the corresponding `*.partial` file remains under
      `$LOCAL_EQUS_APP_DIR/data/`, and pressing Download again resumes from
      the partial (server should see a `Range: bytes=N-` request).
- [ ] Refresh the panel — the just-downloaded files no longer appear in
      "Available updates".

### Sensor catalog from server

- [ ] **View → Rescan local data**. Status bar reports the indexed count.
- [ ] In the Sensor Picker, sensor names match what the server's
      `/v1/sensors/{tool_id}.json` returns (server-side names take precedence
      over parquet column names — verify with one tool that has different
      naming on each side, e.g. `chamber_pressure_torr` in parquet vs
      `chamber_pressure` in the server payload).
- [ ] Disconnect the network briefly, run Rescan again. The picker still
      shows sensors (from the cache); a stale-cache warning is in
      `$LOCAL_EQUS_APP_DIR/logs/local-equs-client.log`.

### Chart end-to-end

- [ ] Pick 2-3 sensors across the downloaded tools.
- [ ] Charts populate within ~200 ms each. Linked pan/zoom works
      (regression check from M1).
- [ ] No "Tool error" overlays — every selected tool has data locally now.

### Archived locally

- [ ] In the server, simulate dropping a file from the manifest (e.g. delete
      the row from the dev manifest). Refresh the Updates panel.
- [ ] The file appears under "Archived locally (not in manifest)" in muted
      grey. Right-clicking in the Local Library panel still lets the user
      delete it manually — eviction policy is M5.

## Bail criteria

If any of the following happens, M2 is **not** done:

- The first-run wizard never opens for a fresh install.
- Manifest fetch ignores the cached ETag (server logs show no
  `If-None-Match`).
- A cancelled download leaves a corrupt file under `data/` instead of a
  `.partial`.
- A checksum mismatch is silently ignored — the chart picks up wrong data.
- Picker hangs the UI when the server is slow to answer (refresh should be
  off the UI thread or short-circuited via cache).

## What's deliberately not in M2

- Canonical sensor names + cross-tool selection. M3.
- Mapping editor, saved sets CRUD, view modes, virtualized chart grid. M3-M4.
- Telemetry, crash handler, settings rolled out fully. M5.
- Auto-updater + signed installer. M6.
