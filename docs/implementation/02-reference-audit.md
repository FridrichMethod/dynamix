# DEEMO reference audit

Inspected read-only on **2026-10-10**. Repository: `/home/fridrichmethod/data/repos/deemo`; remote `https://github.com/FridrichMethod/deemo.git`; clean `main` tracking `origin/main`; commit `b14bf0d5f5a05e5382c319f3fd5019a4936820b8`. No repository instruction files were found in the inspected tree. Source inspection only: this planning session did not run DEEMO fetchers, builds, or tests.

## Reuse and parity map

Paths below are relative to the reference repository and remain useful from any implementation worktree.

| Reference path | Observed behavior | Dynamix action |
| --- | --- | --- |
| `archive.html`, `src/archive.js`, `src/archive.css` | Responsive grid; text/source/type/min-long-edge filters; title/source/pixel-area sort; 60-card pagination; viewer, original download, provenance, previous/next and slideshow link | Port layout and interactions; change game metadata and source filters; test empty catalog and unknown metadata |
| `templates/slideshow.html`, `src/slideshow.css` | Generated immersive slideshow, crossfade, shuffle/autoplay, manual controls, screenshot overlay; side notes on wide screens and portrait stacking | Preserve interaction/style; use Dynamix media and accurate captions; background audio is optional and excluded unless sourced separately |
| `src/i18n.js`, `src/i18n/{common,archive,slideshow}.js` | English/Simplified Chinese; URL/localStorage language; source titles untouched | Replace project globals/storage keys consistently; reuse behavior and localization checks |
| `scripts/fetch_{wikis,artists,archives}.py` | Source adapters, raw-byte downloads, failure manifests, refresh/resume and version handling | Reuse helpers only after removing hardcoded DEEMO IDs, source hosts, categories and mappings |
| `scripts/build_catalog.py` | Manifest validation, exact-SHA-256 dedup, source aggregation; builds `catalog.json`, `catalog.js`, `index.html`; `--verify --check` detects stale output | Remove required legacy family and old mapping assumptions; use Dynamix contract |
| `scripts/build_thumbnails.py` | Long edge 480, WebP quality 75/method 6; hash-derived names; pinned encoder, color handling, check/verify/prune | Preserve original bytes and tested color logic; detect filename-prefix collisions rather than overwrite |
| `scripts/prepare_pages.py` | Git-tracked allowlist; fresh staging dir outside checkout; symlink/path rejection; 950,000,000-byte cap | Keep safeguards; explicitly exclude `docs/implementation/**` and execution/review logs from the website |
| `.github/workflows/tests.yml` | Every Python test file, generated freshness, source/legacy verification, installed-Chrome Playwright | Port relevant gates; replace legacy checks with Dynamix provenance and coverage verification |
| `.github/workflows/pages.yml` | Automatically deploys on main push after reusable tests | Change to the guarded manual-only release design; ordinary pushes must not deploy |
| `.github/workflows/check-sources.yml` | Weekly metadata discovery; bot branch and bilingual PR; isolated write token | Start with manual read-only discovery/report artifacts; keep scheduled writes inactive during hold |
| `LICENSE`, `NOTICE`, `licenses/` | Apache software, MIT bundled components, separate media attribution | Preserve only applicable notices; write Dynamix-specific rights/attribution inventory |

Useful code anchors at the pinned revision: `src/archive.js:59` (search normalization), `scripts/build_catalog.py:220` (manifest validation), `:254` (deduplication), `:485` (generated output checking), `scripts/build_thumbnails.py:32` (preview constants), `scripts/prepare_pages.py:10` (publication allowlist), and `NOTICE:5` (provenance).

The reference catalog reports 48 sources, 1,429 source asset records, 1,413 unique files, 1,411 gallery images, 16 duplicate records, 320 paired quantized variants, and zero recorded failures. These are **DEEMO observations**, not Dynamix targets. Source families contain artists 86, wikis 809, legacy 320, archives 214. Counts distinguish records from unique files and songs.

## Port hazards

- `build_catalog.py` requires all three public manifests plus DEEMO legacy/mapping data. `build_legacy_inventory.py` embeds an old DEEMO commit. Remove this coupling; do not invent an empty “historical Dynamix” collection merely to satisfy it.
- Replace `DEEMO_CATALOG`, `DEEMO_I18N`, `deemo-lang`, owner/game/repository fields, UI labels, source kinds, URL paths, application icons, social metadata, test fixtures, and internal-key heuristics intentionally. Allow “DEEMO” only in actual derivation notices and reference docs.
- Original song titles, composers, illustrators and pack names have different relationships in Dynamix. DEEMO's mapping rules are not evidence for a Dynamix association.
- Do not copy Alice/Hans dedication, game backgrounds/icons, the bundled song, game textures, artist post IDs, font files, or historical screenshots. A system font stack and attributable project icons are adequate while preserving layout.
- The retained reference font and many site media have uncertain redistribution provenance. Its two removed commercial fonts must not reappear via history or bulk copying.
- `prepare_pages.py` currently publishes everything tracked under `docs/`. Add an exclusion/explicit public-document allowlist before any artifact is prepared; this packet should remain in GitHub source but outside the gallery payload.
- A tracked-file-only staging check requires intended files to be deliberately staged/committed. Test the committed checkout, not an untracked prototype.
- DEEMO's README loop using `|| echo FAILED` can finish with exit code zero after failures. Use a failure-accumulating or fail-fast runner from CI.
- The reference automatically deploys and opens scheduled source-update PRs. Copying those workflows unchanged breaks this task's release hold and clean-final-branches requirement.

## Software and media attribution

Retain Apache-2.0 and applicable Mashiro/FridrichMethod attribution for reused software; mark adapted files and record the exact reference SHA. If retaining `src/vendor/legacy-ui.js`, preserve its html2canvas, punycode and Pace MIT notices. DEEMO song mapping license files are unnecessary if no such mapping is reused. Code license statements must not be applied to C4Cat/artist artwork.

## Baseline to capture during implementation

Serve each repository from its own loopback port. Capture archive and slideshow at 1440×900 and 375×812, in both languages, including a viewer and screenshot overlay. Record the reference commit and viewport with each image. Compare behavior and layout, not exact pixels of different artworks. Use filesystem screenshots only as local test artifacts; do not publish DEEMO screenshots as Dynamix media.

Source inspection found 13 offline test files, 256 named test methods, and 24 browser `check_*` functions. Adapt relevant cases rather than treating those counts as a coverage quota. Preserve tests for duplicates/versions, resumability, path/redirect safety, HTML escaping, deterministic ordering, line endings, color-managed thumbnails, mobile focus/scroll, offline language switching, no remote requests, and stale generated outputs.
