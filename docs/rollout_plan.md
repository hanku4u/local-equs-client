# Rollout plan (C6.7)

How releases of the Local EQUS desktop client reach users. Companion to
[`m6_smoke_test.md`](m6_smoke_test.md) — the smoke test confirms a
build is shippable; this document covers what happens *after* the
build passes.

This is the **M6 exit document**: once the procedure below is signed
off and the first wave has run cleanly, M6 is complete.

---

## Release pipeline

A release is two pieces of state on the server, produced by the
build pipeline on a Windows machine:

1. A **signed installer** (`LocalEQUS-Setup-X.Y.Z.exe`) hosted at a
   URL the client can reach.
2. A **manifest entry** at `GET /v1/app-version` pointing at that URL,
   with the matching SHA-256.

The auto-updater (C6.4) polls `/v1/app-version` on the user's
`update_check_frequency_hours` cadence (default daily). When the
manifest reports a version newer than the running app, the user is
prompted to download and install (C6.5). Restart Manager handles the
file swap + relaunch.

---

## Cutting a release

### 1. Build, sign, smoke-test

On the build machine:

```cmd
git checkout main
git pull
REM bump the version in pyproject.toml; commit + tag the release
build\nuitka.cmd --clean
build\installer.cmd
set SIGNING_CERT=...
set SIGNING_PASSWORD=...
build\sign.cmd
```

Walk through `docs/m6_smoke_test.md` end-to-end on a clean Windows
machine before considering the release shippable. Every checkbox
must tick.

### 2. Publish the installer

> The server-side specifics depend on infrastructure outside this
> repo. The contract from the client's perspective is:

- The signed `LocalEQUS-Setup-X.Y.Z.exe` is reachable over HTTPS at a
  stable URL. Cache headers should allow re-download; the auto-updater
  verifies SHA-256 regardless.
- The URL is stable for the lifetime of the release. Don't move the
  file after publication or in-flight downloads will 404.

Coordinate with the backend / infra team on the actual upload path
(S3, internal artifact host, etc.).

### 3. Update `/v1/app-version`

> Server-side endpoint owned by the backend team. Mechanism for
> updating it (admin UI, manifest file in a git repo, direct DB write)
> is outside this client's scope; the contract is the JSON shape.

Set the response body to:

```json
{
  "version": "X.Y.Z",
  "url": "https://<host>/LocalEQUS-Setup-X.Y.Z.exe",
  "sha256": "<sha256 of the .exe, lower-case hex>",
  "release_notes": "Short human-readable summary."
}
```

Once this lands, every running client will see the new version on its
next poll.

### 4. Announce

Send a short message to the first-wave testers (see below) confirming
the new version is available and what to expect.

---

## Phased ramp

### Wave 1 — internal testers (first 5–10 users)

**Duration:** 1 week, or until no new defects are reported for 48 hours.

| Wave-1 user      | Role / why                                              |
|------------------|---------------------------------------------------------|
| _<fill in>_      | _<reason — e.g. "platform team lead, daily user">_      |
| _<fill in>_      | _                                                        |
| _<fill in>_      | _                                                        |
| _<fill in>_      | _                                                        |
| _<fill in>_      | _                                                        |
| _<fill in>_      | _                                                        |

> Replace the placeholder rows with actual user IDs before the first
> release. Maintain this list in this file — git history is the audit
> trail.

**Mechanism:** Wave-1 users are simply the first machines pointed at
the new `/v1/app-version`. There's no client-side targeting; the
gating is server-side (e.g., set `/v1/app-version` to the new release
only for these clients via a feature flag, or stage the update by
restricting which clients can reach the installer URL until ramp
criteria pass).

**Telemetry to watch during the wave:**

- `app_start` — every wave-1 client launches at least once on the new
  version. Track `seconds_since_last_exit` to confirm normal usage.
- `error` events — any new error types or spikes vs. the prior week.
- `query_failed` — any regression in the query pipeline.
- `update_check` / `download_*` events — confirm the auto-updater
  itself is healthy on the new version.

### Wave 2 — full ramp

Once wave-1 criteria pass (see below), flip the server gate so every
client sees the new version on its next poll.

---

## Ramp criteria

Pass to the next wave when **all** of the following hold:

1. **Zero blocking defects.** No reproducible crash with > 1 wave-1
   user affected. No regression in chart rendering, sensor mapping
   resolution, or data accuracy.
2. **Crash rate is flat or lower.** Server telemetry shows the `error`
   event volume on the new version is ≤ the prior week's average for
   the same wave-1 cohort.
3. **At least one wave-1 user has run the new version for 48 hours
   continuously** without uninstalling, downgrading, or filing a
   support ticket.
4. **No support tickets escalated to "blocker".** Minor cosmetic
   issues don't block ramp; functional regressions do.

If any criterion fails, follow the rollback procedure.

---

## Rollback procedure

The auto-updater is a one-way street from the user's perspective: a
client that has installed `X.Y.Z` cannot be downgraded by changing
`/v1/app-version` back to an older value (newer is the only direction
the updater operates in).

What rollback *does* do is stop the spread:

### Stop the bleed

1. **Revert `/v1/app-version`** to the previous release's payload.
   New polls (every client that hasn't updated yet) will see the
   previous version and stay there.
2. **Take down the broken installer.** Either remove the URL or have
   it 404. New downloads of the broken version are now impossible.

Already-updated clients stay on the bad version. That is fine in the
short term — they're a finite, identified set (Wave 1 only, if ramp
criteria caught the issue in time).

### Hotfix the broken version

1. Identify the defect.
2. Bump to `X.Y.Z+1` (patch increment).
3. Build, sign, smoke-test the fix.
4. Publish the new installer + update `/v1/app-version` to point at
   it.
5. Every wave-1 client picks it up on the next poll automatically.

The hotfix is the "downgrade." Calling out wave-1 testers by hand to
manually install a fix is acceptable for the very first M6 release;
later rollouts should rely on the auto-updater catching them.

### When a hotfix isn't possible

If the broken version corrupts user state (e.g., destructive schema
migration), the rollback story is harder. The hotfix must also
include a one-shot recovery step. Plan: every M release should
include a smoke check that user state survives the upgrade
(`%LOCALAPPDATA%\LocalEQUS\` is preserved). See the C6.2 uninstall
checkbox in [m6_smoke_test.md](m6_smoke_test.md) for the install-time
equivalent.

---

## Communication

- **Pre-wave-1:** announce on the team channel: "Releasing X.Y.Z to N
  testers tomorrow. Expect the auto-update prompt within 24 hours of
  your next launch."
- **Post-wave-1:** post the ramp decision: either "going to full ramp
  on $DATE" or "holding due to $REASON, hotfix in progress."
- **Post-full-ramp:** announce on the team channel: "X.Y.Z is rolling
  out to all users; check Help → About to see your installed version."

---

## Known gaps (carry into M7+)

The C5.16 audit ([`error_handling.md`](error_handling.md)) flagged
several follow-ups that are out of scope for M6 but worth tracking on
the rollout side:

- **No offline-mode badge.** A client that can't reach the server
  silently degrades; rollout comms should mention the impact.
- **No client-side feature flag.** Wave 1 gating is entirely
  server-side. A future release could expose a "stay on stable
  channel" Settings checkbox so wave-1 users opt in voluntarily.
- **No telemetry-based "is N% rolled out" dashboard.** Today the
  ramp criteria are checked manually. A dashboard reading
  `app_start.app_version` would make this a one-glance decision.

These don't block M6 exit; they're the natural M7 follow-ups.
