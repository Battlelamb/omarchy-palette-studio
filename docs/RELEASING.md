# Release gates

1. Require the exact candidate's package/offline backend/controller/native tests, native wide/compact geometry and pixels, upgrade/rollback proof, connected read-only independent review and committed tested-blob equivalence. Keep failed evidence private and immutable.
2. Keep the full license and pinned donor/dataset notices. No copyrighted wallpaper/game artwork, private desktop captures, credentials or raw local process/provider logs in exports. Previews are optional, not invented product evidence.
3. Build a deterministic current-commit source tarball with SHA256SUMS; verify unpacked files, native manifest validation and independence from all other Lego plugins. No source history or ignored evidence is bundled.
4. Maintainer approval is required for public writes, tags/releases and marketplace submissions. Clean current source while preserving published Git history; never rewrite history or retarget published tags. Approval and local checks are not publication evidence.
5. After publication, verify exact remote commit, CI, tag target, release assets/bytes and deployed marketplace commit before claiming success. Hosted CI and marketplace approval are not implied by local checks. Never retarget a published tag. Runtime releases advance their versioned path; retain rollback and do not restart the lock-hosting shell to mask stale QML.
