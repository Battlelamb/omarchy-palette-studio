# Palette Studio validation

## v0.4.1 scope

This patch changes the public plugin identity and versioned runtime URL, cleans current documentation, and adds namespace/version/portable-path assertions to the existing component tests. Backend, preference-store, validation and recovery behavior are unchanged. Historical results below are not fresh v0.4.1 qualification.

Focused offline checks passed: package identity/layout validation, **59 Node component tests** (zero failures/skips), **11 required-file negative controls**, and **nine namespace/version/author/source-path/stale-runtime negative controls**. The namespace assertion first failed on the unchanged baseline. Runtime hashes were unchanged by the checks. Full Python/native suites, hosted CI and real-shell qualification are separate gates, not implied by this result.

For the complete offline gate, set `TMPDIR` to an existing private scratch directory and run `env -u PYTHONPATH bash test.sh`. It uses isolated image/clipboard fixtures, not live clipboard or desktop providers. See [CI scope](CI.md) for the narrower hosted checks; hosted status must be checked on the exact commit.

## Historical v0.4.0 offline results

The shipped gate recorded **73 Python passes, 1 explicit skip (74 discovered)** and **58 JavaScript component passes**. The **23 native subprocess/settings cases** and **five native UI modes** also passed, including wide, compact, tall-font and light-theme fixtures, Save/reopen/Reset readback and control containment. These overlapping checks are not an aggregate score or real-shell action evidence.

Historical runtime inventory SHA-256: `2538a7aa10070cb1668820b8255ed0e7cf5db9ac8b5e24303b74726469c2ae83` (sorted compact JSON of relative runtime paths to SHA-256 values). This identifies v0.4.0 only. Browse/Configure images remain the illustrative v0.4.0 component renders documented in [PREVIEW.md](PREVIEW.md).

Earlier incomplete baseline/candidate runs remain failed records; the later offline pass does not qualify those runs or full-shell behavior. Native timeout cleanup refuses to claim success when descendant ownership cannot be proved, and aborts later cases. Existing anchor-transition warnings remain.

## Earlier observations and limits

The v0.3.0 offline gate recorded **68 Python passes out of 69 discovered**, with the shared Glyph-only CLI case explicitly skipped, and **51 Node passes**. Offscreen native fixtures and scoped reviews were not live Copy/export/Restore qualification. Historical test installation covered opening, populating and closing the panel without restarting the shell.

The v0.2.1 compact-detail fix had native geometry/scrolling checks and a live UI upgrade observation, including waiting for the palette catalogue to finish. Historical v0.1.0 export/quarantine Restore checks do not qualify newer versions. Previous whole-shell resource attempts remained **failed/failed-to-run**: admission prevented a full-shell trial, and a separate device-free compositor attempt failed before shell startup. Component-only old/new/rollback loading does not close that gate.

**Still unqualified for this patch:** real-shell old/new/rollback, live clipboard/paste/export/Restore, GPU presentation, lock races, RTL/IME/accessibility, hotplug, long-soak/resource behavior and physical power-loss recovery. Restore retains owned export bytes in quarantine; bounded stores and ownership/conflict checks in [BACKEND.md](../BACKEND.md) remain authoritative. No performance improvement, full security audit, stable release or marketplace approval is claimed. No installation, shell restart or live configuration change is part of this cleanup.
