# M6 smoke test

End-to-end manual verification that the **packaged** Windows build
(Nuitka → Inno Setup installer → Authenticode-signed → auto-updater) is
shippable. Each section below corresponds to one M6 client issue;
running the whole checklist in order is the **M6 exit criterion** for
the client.

## Prerequisites

- A Windows 10 or 11 machine — clean if possible (no prior install of
  the client).
- A working dev backend with `/v1/telemetry`, `/v1/app-version`, and
  the manifest endpoints reachable from the test machine.
- The signing certificate available either as a `.pfx` file or in the
  Windows certificate store (per `build/README.md`).
- Inno Setup 6 and a recent Windows SDK installed on the build machine.
- A way to query server-side telemetry (database, dashboard, etc.) so
  you can confirm events landed.

---

## C6.1 — Nuitka build

```cmd
pip install -e ".[build]"
build\nuitka.cmd --clean
```

- [ ] Builds without errors (takes a few minutes).
- [ ] `dist\LocalEQUS\LocalEQUS.exe` exists.
- [ ] Double-click `dist\LocalEQUS\LocalEQUS.exe`. No console window
      appears alongside the GUI.
- [ ] App launches and shows the FirstRunWizard (or the main window
      if `server_url` is already configured).
- [ ] No traceback in the console / log file.

## C6.2 — Inno Setup installer

```cmd
build\installer.cmd
```

- [ ] Produces `dist\LocalEQUS-Setup-<version>.exe`.
- [ ] Double-click the setup .exe. Wizard appears with no UAC prompt
      (per-user install).
- [ ] Default install path shown is
      `%LOCALAPPDATA%\Programs\LocalEQUS`.
- [ ] Click through to finish. The "Launch LocalEQUS" checkbox is
      ticked by default; clicking Finish launches the app.
- [ ] Start Menu now shows a "Local EQUS Client" entry.
- [ ] Add/Remove Programs lists "Local EQUS Client" with the right
      version.
- [ ] Re-run setup with `/SILENT` from `cmd`: installer runs
      headlessly, no wizard, no launch.
- [ ] Uninstall via Add/Remove Programs. `%LOCALAPPDATA%\Programs\
      LocalEQUS\` is removed; `%LOCALAPPDATA%\LocalEQUS\` (state.db,
      config.toml, telemetry queue) is **untouched**.

## C6.3 — Code signing

After building both .exes, sign them with the configured cert:

```cmd
set SIGNING_CERT=C:\secure\codesign.pfx
set SIGNING_PASSWORD=...
build\sign.cmd
```

(Or `set SIGNING_THUMBPRINT=...` for cert-store mode.)

- [ ] `signtool verify /pa /v dist\LocalEQUS\LocalEQUS.exe` reports
      `Successfully verified`.
- [ ] `signtool verify /pa /v dist\LocalEQUS-Setup-<version>.exe`
      reports `Successfully verified`.
- [ ] The timestamping URL appears in the signature details (right-click
      the .exe → Properties → Digital Signatures).
- [ ] Downloading the signed setup .exe via a web browser and running
      it does **not** trigger SmartScreen's "Windows protected your PC"
      blue dialog (OV certs need to build reputation; expect a less
      aggressive warning at worst).

## C6.4 + C6.5 — Auto-updater across 0.1.0 → 0.1.1

Set up two consecutive versions and confirm the in-place update works.

### Build 0.1.0

1. Make sure `pyproject.toml` reads `version = "0.1.0"`.
2. `build\nuitka.cmd --clean && build\installer.cmd && build\sign.cmd`
3. Install on the test machine.

### Build 0.1.1

1. Bump `pyproject.toml` to `version = "0.1.1"`.
2. `build\nuitka.cmd --clean && build\installer.cmd && build\sign.cmd`
3. Compute the SHA-256 of `dist\LocalEQUS-Setup-0.1.1.exe`. Publish
   the .exe to a URL the test machine can reach.
4. Configure the dev backend's `/v1/app-version` to return:
   ```json
   {
     "version": "0.1.1",
     "url": "https://<your-server>/LocalEQUS-Setup-0.1.1.exe",
     "sha256": "<computed-sha>",
     "release_notes": "C6.5 smoke test"
   }
   ```

### Run the update

1. Launch the installed 0.1.0.
2. Wait ~30 seconds (`QTimer.singleShot(30_000, ...)` fires the first
   check after the UI settles).
   - [ ] `Update available — Local EQUS 0.1.1 is available.` prompt
         appears.
3. Click Yes.
   - [ ] Download progress appears in the log; no UI block.
   - [ ] `%LOCALAPPDATA%\LocalEQUS\updates\LocalEQUS-Setup-0.1.1.exe`
         exists after download completes.
4. `Update ready to install` prompt appears. Click Yes.
   - [ ] Running app closes within a few seconds.
   - [ ] Installer runs silently in the background (no wizard).
   - [ ] The new version (0.1.1) launches on its own via Restart
         Manager.
   - [ ] `app_version()` in the running app reflects `0.1.1` (check
         via Help → About or by looking at the most recent `app_start`
         telemetry event server-side).

## C6.6 — Crash reporting wiring

The packaged build inherits the `crash_handler` module from C5.15. This
step proves an `error` event actually leaves the test machine.

1. Open a `cmd` prompt, navigate to the install dir
   (`%LOCALAPPDATA%\Programs\LocalEQUS\`).
2. Run:
   ```cmd
   LocalEQUS.exe --induce-crash
   ```
3. Expected behavior:
   - The app exits within a second or two with a non-zero exit code.
   - The traceback is written to the rotating log file at
     `%LOCALAPPDATA%\LocalEQUS\logs\local_equs_client.log`.
   - The crash handler attempts a synchronous `telemetry.flush()`
     before chaining to the previous excepthook, so the `error` event
     should hit the server immediately.
4. Verify server-side, within ~2 minutes:
   - [ ] An `error` event arrived with `error_type="RuntimeError"`,
         `thread="main"`, and a traceback mentioning
         `induce-crash flag set`.
   - [ ] The app's most recent `app_start` event (just before the
         crash) also arrived — confirms the queue flushed both
         events together.

If the synchronous flush couldn't reach the server (network blip), the
event stays in the local queue and is sent on the next launch:

5. Re-launch `LocalEQUS.exe` (no flag this time).
6. Wait up to 60 seconds for the periodic flush, or check ~5 seconds
   after launch (single-shot flush from C5.11 hardening).
   - [ ] The previously-queued `error` event now appears server-side
         if it didn't make it through during the crash.

## M6 exit criterion (C6.7)

If every checkbox above is ticked, the M6 milestone is complete: a
freshly-installed package launches cleanly, updates itself, and reports
crashes back to the server.
