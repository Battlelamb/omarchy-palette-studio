# Palette Studio backend — v0.3.0

## Exact CLI

From the plugin checkout (source/data lookup does not otherwise depend on cwd):

```sh
/usr/bin/python3 runtime/0.3.0/backend.py catalog
/usr/bin/python3 runtime/0.3.0/backend.py preferences
/usr/bin/python3 runtime/0.3.0/backend.py preferences --apply --payload '{"options":{"style":"pastel","colorCount":8,"favoritesOnly":false}}'
/usr/bin/python3 runtime/0.3.0/backend.py action --id PALETTE-ID-FROM-CATALOG --verb favorite --apply
/usr/bin/python3 runtime/0.3.0/backend.py action --id PALETTE-ID-FROM-CATALOG --verb copy-color --apply --payload '{"index":0}'
/usr/bin/python3 runtime/0.3.0/backend.py action --id PALETTE-ID-FROM-CATALOG --verb export --apply
/usr/bin/python3 runtime/0.3.0/backend.py action --id restore:last --verb restore --apply
/usr/bin/python3 runtime/0.3.0/backend.py preferences --apply --payload '{"reset":true}'
```

Replace the placeholder with an exact current catalog ID, including its `json` / `css` / `gpl` suffix. **Only `copy-color` accepts an action payload**: exactly `{"index":N}`, where N is a JSON integer (not boolean), zero-based and inside the freshly extracted palette. Other verbs reject any payload. Arbitrary text, output paths, shell commands and postprocessing fields are not supported.

Commands are exactly `catalog`, `preferences`, `action`; unknown/abbreviated options fail. Catalog has no options. Preference reads take no options; writes need `--payload` plus `--apply` and reject `--id`/`--verb`. Actions need explicit `--id`, `--verb`, `--apply`; no confirmation means no action. Payloads must be JSON objects, ≤16384 UTF-8 bytes, without duplicate keys, nonfinite constants, unknown fields/options, invalid types or excessive nesting. Output is one JSON object, capped at1 MiB; usable reads/catalog and confirmed writes exit0, failures exit nonzero without tracebacks.

## Preference schema and catalogue

| Key | Type | Default | Allowed values |
| --- | --- | --- | --- |
| `style` | enum | `vibrant` | `vibrant`, `muted`, `pastel`, `monochrome`, `high-contrast` |
| `colorCount` | integer | 6 | 2–16 inclusive; bool/float/string refused |
| `favoritesOnly` | boolean | false | JSON boolean only |

Preference responses contain `{ok,preferences,settingsSchema,message}`. Document: `{version:1,favorites:[],recent:[],collections:{},options:{...}}`. Patches accept only `options`, `favorites`, `collections`; recent/version are backend-owned. Options merge; collections replace the named map. The sole-key `{"reset":true}` resets preferences only, not exports/recovery receipts. Limits are512 favorites,32 recents,64 named collections,512 inert IDs/collection; each ID is bounded/control-free ≤256 characters. Unknown/stale saved IDs never become action authority.

The independent sibling `preferences.py` supplies bounded validated regular-file reads, a private locked atomic store and explicit corruption refusal. Location: absolute `XDG_CONFIG_HOME` or `$HOME/.config`, followed by `omarchy/lego/palette-studio/preferences.json`. Successful copy/export updates recent only after actual helper/export acceptance. A subsequent preference-storage error adds a warning while preserving the truthful external result. Restore does not read preferences and remains usable if they are corrupt.

Catalog emits three format-specific rows per decoded image, preserving baseline fields and adding `favorite`, `category` (style), `swatches` and structured `contrast`. Backend favorites filtering precedes row emission. Primary verb is `export`; secondary fixed verbs are `favorite`, `copy-color`, conditional `restore`. Action capability hints include those secondary verbs without forcing a catalog reload/decode.

Row IDs are `palette:<32-lowercase-hex>:<json|css|gpl>`. For default Vibrant /6, the identity stays the existing literal path/device/inode/size/mtime/ctime source token. Nondefault options hash that source token plus style/count, separating different output variants and refusing stale-settings selections. A single operation freezes its preference options for membership and extraction.

New preference writes are preflighted under the store lock against an aggregate 128 KiB compact UTF-8 document budget before replacement. Individual list/text maxima do not imply their Cartesian maximum is supported. Legacy valid stores remain bounded to 16 MiB for reads and explicit Reset/recovery; a rejected write leaves the existing file unchanged. Replies use the common UTF-8 encoder below 1 MiB (including the newline, at most 1 MiB); the UI action parser uses the same strict byte ceiling. Oversized optional preference snapshots on successful commands are omitted with a warning, preserving `ok:true`; actual catalogue overflow still fails closed, without truncating saved data. A Recent-save failure likewise warns without reversing confirmed helper/export acceptance.

## Actual extraction and five styles

Uses the already-installed `/usr/bin/magick`; no dependency download, matugen/Aether invocation or user Wallbash template. The same independent fixed local wallpaper roots are scanned: Pictures/Wallpapers and Omarchy backgrounds with one collection level, installed theme backgrounds, staged current-theme backgrounds. Other plugins' saved folders/preferences are not imported.

Source bytes come from a bounded stable FD and a memfd, using a fixed magic-derived PNG/JPEG/WebP/BMP stdin coder rather than an interpreted filename. ImageMagick runs with HOME=/nonexistent, fixed system policy, one thread,128 MiB memory/map, disk cache disabled,16384 dimension limits and bounded runtime/output. It samples64×64, composites transparency on black, quantizes to the requested maximum, and validates bounded 8-bit hex output. It can return fewer distinct colors; missing/failed decoding never generates fabricated fallback colors.

Styles transform only those extracted colors in stdlib HLS space:

- `vibrant`: no transform; keeps the sampled colors.
- `muted`: saturation capped at0.35.
- `pastel`: lightness0.82, saturation capped at0.55.
- `monochrome`: saturation0.
- `high-contrast`: alternating lightness0.08/0.92, saturation capped at0.65.

Duplicates introduced by transforms are removed. This is not perceptual ranking, Material/Wallbash role semantics or a full-resolution histogram.

### Numeric contrast

For each resulting #RRGGBB value, normalized sRGB channels are linearized using the0.04045 threshold (division by12.92 below; exponent2.4 above). Relative luminance weights are0.2126/0.7152/0.0722; contrast is `(lighter + 0.05) / (darker + 0.05)`, rounded to two decimals. `contrast` contains `againstBlack`, `againstWhite`, and all unordered `pairs:[{a:<zero-based-index>,b:<index>,ratio:<number>}]`. Row detail shows first-swatch black/white ratios and minimum pair ratio when present. Values describe the resulting hex colors, not monitor calibration, alpha/text context, compliance, accessibility certification or medical suitability. The high-contrast style name makes no threshold guarantee.

### Individual color copy

`copy-color` revalidates current style/source membership, decodes only the selected image, rechecks its identity and validates the index before invoking literal `wl-copy --type text/plain;charset=utf-8` with the selected hex bytes on stdin. Exit0 without stderr confirms helper acceptance, not a later paste. No arbitrary clipboard payload or shell evaluation exists.

## Export ownership and Restore (retained hardening)

Exports go only to `$HOME/.local/share/omarchy-palette-studio/exports/<identity>.<json|css|gpl>`. JSON contains schemaVersion/colors; CSS has :root --palette-N properties; GPL is a standard GIMP palette. No arbitrary destination or app-config edits. Existing identical bytes are idempotent; different bytes/symlinks are refused rather than overwritten.

A complete fsynced temporary file and ownership receipt precede exclusive hard-link publication, followed by exact readback. The pending receipt retains the previous receipt until the new public name matches its recorded device/inode. Failed receipt writes, before-publication crashes or competing destinations do not consume earlier Restore authority. After actual publication the inode stays recoverable even if final receipt persistence fails. Recovery uses inode identity, not equal bytes, and never adopts another writer's symlink/file. Crashes may leave hidden `.export-*` files; there is no automatic pruning. Injected filesystem/crash tests are not physical power-loss certification.

Restore accepts the original owned selected ID or `restore:last`, not an unrelated selection, without requiring a live source/catalog/extractor. The receipt records ID, filename, hash, device and inode. Restore checks identity/content, then renames the owned export into a private random `.restore-<24-hex>` file in the same exports directory and rechecks it. **Bytes are retained, not deleted**, because an editor holding the inode open can write after verification. Success JSON `path` names that retained recovery file; the one-level receipt is cleared. Other exports/user files stay untouched.

A race detected after rename attempts no-overwrite hard-link recovery of the public name; the quarantine is removed only after another link preserves its inode. If a concurrent editor occupies the public name, both it and the quarantine survive and Restore fails. Held-FD writers can keep changing retained data after success; inspect hidden recovery files and close writers before deliberate manual recovery/pruning. No automatic recovery manifest, cleanup quota or total-export storage quota is claimed.

## Limits and offline verification

Scans cap1024 entries/directory (overfull directories skipped),512 roots/files,32 MiB/image. Catalog decodes at most32 images inside a five-second extraction budget, three formats each. Selected actions validate full bounded membership even outside that preview budget. Ordinary metadata reads cap64 KiB, receipt8192 bytes, subprocess stdout/stderr64 KiB each. Directory FDs and O_NOFOLLOW protect every path component; unsafe/control-bearing names, file/root/ancestor symlinks and special files are refused. The only known current-background symlink reader remains separately allowlisted where used. Qt/codec rendering is not hostile-media certification.

Subprocess argv is literal, without shell/eval/templates. Hard deadlines and process-group cleanup cover interruption/overflow and pipe-holding descendants. State under `$HOME/.local/state/omarchy-palette-studio/` uses private regular-file locking and atomic/fsynced receipts. Same-UID malicious writers, escaped process sessions and codec vulnerabilities are not magically isolated by these guards.

Use only the shipped offline runner, with `TMPDIR` naming an existing private scratch directory:

```sh
env -u PYTHONPATH /usr/bin/python3 scripts/run-tests.py --backend-only
```

The bwrap runner clears HOME/env, masks production setters and removes network/live sockets; decoder tests exercise the installed codecs, copy tests controlled clipboard helpers. Genuine feature/regression RED/GREEN logs are in `release-output/v030-tdd/`; final commands/counts/hashes and remaining integration criteria are in `release-output/v030-result.json`. No live setter, GUI/pixel test, hosted CI, installation or publication is implied. Provenance: NOTICE.backend.md and `runtime/0.3.0/data/source-ledger.json`.
