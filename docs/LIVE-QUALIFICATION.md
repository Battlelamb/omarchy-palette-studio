# Local integration coverage

The v0.4.1 namespace/privacy patch has no new live integration qualification. Opening the panel, clipboard/paste, export/quarantine Restore and real-shell old/new/rollback remain separate gates. No live installation or configuration change is part of this cleanup.

Historical v0.2.1 observations covered native compact-detail/font/scrolling checks and a live UI upgrade with a real catalogue, allowing extraction to finish before inspection. The panel opened and closed without restarting the shell, while configuration and observed desktop state were preserved. This was UI/upgrade coverage, not a new live Copy/export/Restore pass. Earlier full-shell attempts remain failed or failed-to-run; offscreen component loading is not equivalent.

Current access uses `omarchy-shell shell summon battlelamb.palette-studio`. Existing installation, enabled-plugin, menu and binding references must be updated to that ID; stable preference, export and recovery paths are unchanged.

See [VALIDATION.md](VALIDATION.md) and [BACKEND.md](../BACKEND.md) for historical checks, bounded stores and quarantine/ownership limits. No universal rollback, security audit, long-soak or performance claim.
