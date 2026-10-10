# Frozen implementation contracts (schema version 1)

Frozen by the coordinator on **2026-10-10** before parallel implementation. This document refines [07-data-contracts.md](07-data-contracts.md) into exact names. Workers implement against it; any change is proposed to the coordinator, committed once on `impl/integration`, and merged into dependent branches. `scripts/lib/schema.py` is the executable form of this document; if they disagree, report it rather than picking one silently.

## Measured inputs behind these decisions (2026-10-10 probes)

- Miraheze `allimages`: 4,469 files, 2,102,094,760 bytes (API sizes). Fandom: 4,924 entries (569 `video/youtube`), 2,070,864,118 bytes.
- Fandom's CDN serves **transformed WEBP** for `/revision/latest` URLs. Appending `format=original` returns the stored original. For sampled files the Fandom `format=original` bytes were **byte-identical to Miraheze** (SHA-1 equal to the Miraheze API value) while the **Fandom API `size`/`sha1` differed** by 18–530 bytes. Exact-byte dedup therefore collapses the two wikis naturally; the Fandom API mismatch is recorded as `source_reported` evidence, not as a second version.
- Original-game song pages (Miraheze `Category:Songs`, excluding `Dynamix Universe` categories): ~510 template covers (`Imgsuffix`), ~460 MB. Core artwork roles together ≈ 780 MB. Chart-list announcement graphics ≈ 271 MB; animated character GIFs (≤250 px) ≈ 634 MB; probable Universe square `_cover` files ≈ 151 MB.

## Storage and publication policy

| Tier | Kinds | Stored in Git | Original served by Pages |
| --- | --- | --- | --- |
| `site` | `song_art`, `pack_cover`, `character`, `background`, `part`, `promotional`, `process`, `store_screenshot` | yes | yes |
| `archive` | `reference`, `gameplay_screenshot`, any PDF | yes | **no** — site shows the preview; the viewer links the original at `archive_url` (GitHub raw URL on `main`) |

- `build_catalog.py` sets `site_original` from this table. `prepare_pages.py` omits originals whose catalog record has `site_original: false` and fails if the remaining payload exceeds **950,000,000 bytes**. If the `site` tier alone exceeds the budget, that is a reported blocker, never fixed by recompression or dropping records.
- Excluded (recorded candidates, never downloaded by default): animated GIF captures (`animated_capture`; the larger `-Raw.png` still is archived), Universe-only (`universe_only`), person photos, site/game UI, video placeholders, fanart, upscales, rejected submissions. `fetch_wikis.py --include-animated` exists so the decision is reversible.
- No Git LFS, no release attachments, no paid storage. No single file may exceed 100 MiB; a push must stay under 2 GB.

## Identifiers and paths

- Source IDs: `<family>:<slug>`; family ∈ `wikis`, `artists`, `official`, `archives`. Registered: `wikis:miraheze`, `wikis:fandom`, `wikis:atwiki-ja`, `wikis:rhythmgame-ja`, `official:apple-store`, `official:google-play`, `official:taptap`, `official:website`, `official:c4cat`, `official:facebook`, `official:x`, `archives:hkstp`, `archives:4gamer`, `archives:wayback`. Artists: `artists:<platform>:<handle>` (e.g. `artists:x:ariiol_`, `artists:artstation:sandgodoven`). New IDs are registered by the owning worker in its family manifest; slugs are lowercase `[a-z0-9-]`.
- Candidate ID: `<source_id>:<stable upstream key>` (wiki: the canonical `File:` title with spaces as `_`; platforms: post/asset ID plus page index).
- Asset ID: `<source_id>:<sha256[:16]>`; stable across title edits and refreshes.
- Asset path: `assets/public/<family>/<source-slug>/<slug(original-name-stem)>--<sha256[:12]>.<ext>` where `<ext>` comes from the **sniffed** format (`png`, `jpg`, `webp`, `gif`, `pdf`). The upstream filename is kept verbatim in `original_filename`. Slugs are NFKC, ASCII-transliteration not required: allowed characters `[A-Za-z0-9._-]` plus a hash suffix; empty slugs become `file`. Paths are checked for case-fold collisions.
- Song ID: `song:<slug(exact title)>` with `-2`, `-3` suffixes on collision, assigned deterministically by sorted exact title.

## Family manifest `data/sources/<family>.json`

```json
{
  "schema_version": 1,
  "family": "wikis",
  "fetched_at": "2026-10-10T12:00:00Z",
  "sources": [Source],
  "assets": [Asset],
  "failures": [Failure]
}
```

JSON is written UTF-8, `ensure_ascii=False`, `indent=2`, trailing newline, arrays sorted by `id` (assets/failures) so reruns are deterministic. `fetched_at` is the latest successful acquisition time, not "now" during `--verify`.

### Source

| Field | Type | Notes |
| --- | --- | --- |
| `id`, `name`, `family`, `url`, `platform` | string | `platform` e.g. `mediawiki`, `x`, `bilibili`, `artstation`, `app-store`, `web` |
| `status` | enum | `available`, `partially_available`, `temporarily_unavailable`, `manual_access`, `metadata_only`, `excluded` |
| `checked_at` | ISO-8601 UTC | last access attempt |
| `notes` | string | human summary incl. access limitations |
| optional | | `api_url`, `owner_evidence`, `crawl_scope`, `rate_policy`, `discovery_complete` (bool), `candidate_count`, `asset_count`, `failure_count`, `evidence_level` (`live_api`, `page_read`, `search_index`, `blocked`, `not_established`) |

### Asset (one record per source × file)

Required: `id`, `source_id`, `candidate_id`, `title`, `kind`, `game` (`"dynamix"`), `page_url`, `download_url`, `resolved_url`, `path`, `original_filename`, `bytes`, `sha256`, `sha1`, `format` (Pillow format name or `PDF`), `mime` (from sniffed format), `width`, `height` (null for PDF), `fetched_at`, `illustrators` (string[], verbatim; `[]` = unknown), `composers` (string[]), `song_titles` (string[], exact), `song_ids` (string[]), `collections` (string[]: pack/event names), `mapping_status`, `mapping_evidence` (list), `quality` (object), `rights` (object), `source_reported` (object), `download_observed` (object), `comparisons` (object).

Optional: `credit_evidence` ([{`role`, `name`, `url`, `field`, `quote`}]), `variant_group`, `variant_role`, `upstream_status` (`current`, `removed`, `superseded`, `unknown`), `superseded_by` (asset ID; only with `superseded`), `game_associations` (e.g. `["dynamix", "arcaea"]`), `notes`, `upstream_revision`, `transformed` (bool; true only when the stored bytes are known to be a platform transform).

Enumerations:

- `kind`: `song_art`, `pack_cover`, `character`, `background`, `part`, `promotional`, `store_screenshot`, `gameplay_screenshot`, `process`, `reference`.
- `mapping_status`: `confirmed_primary`, `confirmed_wiki`, `inferred`, `unmapped`, `conflict`.
- `quality.source_variant`: `site_original`, `preview`, `unknown`; `quality.origin_status`: `artist_post`, `official`, `wiki_copy`, `repost`, `scan`, `unknown`; `quality.master_status`: `confirmed`, `unverified`; `quality.notes`: string.
- `rights`: `{"holder": str|null, "creator": str|null, "url": str|null, "license": str|null, "restrictions": str|null}`. Default holder text for game art: `"C4Cat Entertainment Limited and/or the credited artist"`; `license` stays null unless a license is actually established.
- `source_reported`: `{"url", "mime", "bytes", "width", "height", "sha1", "timestamp", "revision"}` — values the source advertised (API/page), null when not advertised.
- `download_observed`: `{"content_type", "sniffed_format", "bytes", "width", "height", "sha1", "sha256"}` — measured from the saved bytes.
- `comparisons`: `{"sha1_match", "bytes_match", "dimensions_match", "format_match"}` each `true`, `false` or `null` (not comparable). Never a single undifferentiated "verified" flag.

### Failure

`id` (`<candidate_id>:<phase>`), `source_id`, `candidate_id`, `phase` (`discover`, `download`, `decode`, `verify`), `url`, `attempted_at`, `attempts` (int), `error_class` (`http_4xx`, `http_5xx`, `timeout`, `blocked`, `challenge`, `decode_error`, `size_limit`, `host_not_allowed`, `other`), `http_status` (int|null), `retriable` (bool), `manual` (bool), `message` (credential-free).

## Discovery snapshot `data/discovery/<source-slug>.json`

```json
{
  "schema_version": 1,
  "source_id": "wikis:miraheze",
  "started_at": "...", "finished_at": "...",
  "scope": {"api": "...", "lists": ["allimages", "Category:Songs"], "notes": "..."},
  "continuation": {"allimages": null},
  "discovery_complete": true,
  "candidates": [Candidate],
  "errors": [ {"phase": "...", "url": "...", "message": "..."} ]
}
```

`discovery_complete` is true only when every list was paged until the API returned no continuation. A partial run keeps the last continuation token and `false`.

### Candidate

`id`, `source_id`, `upstream_key`, `page_url`, `file_url` (nullable), `title`, `role` (a `kind` value or `null` when unclassified), `game` (`dynamix`, `dynamix_universe`, `mixed`, `unknown`, `not_dynamix`), `game_evidence` (list of strings/URLs), `source_reported` (object as above), `disposition`, `reason` (string; required unless `downloaded`), `asset_id` (when `downloaded` or `duplicate_bytes`), optional `song_titles`, `illustrators`, `composers`, `collections`, `notes`.

- `disposition`: `downloaded`, `duplicate_bytes` (identical SHA-256 already archived — `asset_id` names that asset), `excluded`, `failed`, `blocked`, `pending`.
- Exclusion `reason` vocabulary (prefix before an optional `: detail`): `universe_only`, `not_dynamix`, `animated_capture`, `person_photo`, `site_ui`, `game_ui`, `video_placeholder`, `external_release_art`, `fanart`, `upscaled`, `rejected_submission`, `redirect_or_alias`, `out_of_scope_other`.
- Every candidate of a complete discovery has exactly one disposition. Totals per source reconcile: `downloaded + duplicate_bytes + excluded + failed + blocked + pending = candidates`.

## Song index `data/sources/song-index.json` (coordinator-owned)

The wiki worker proposes `data/discovery/song-index.wikis.json`; the coordinator reconciles into `data/sources/song-index.json`.

```json
{"schema_version": 1, "generated_from": ["data/discovery/miraheze.json", "..."], "as_of": "2026-10-10",
 "songs": [{"id": "song:cosmic-pulse", "title": "Cosmic Pulse", "aliases": [], "composers": ["Yunosuke"],
   "illustrators": ["ariiol"], "collections": ["Event"], "in_original_game": true,
   "inclusion_evidence": ["https://dynamix.miraheze.org/wiki/Cosmic_Pulse (Template:Song, Category:Event songs)"],
   "universe_also": false, "removed": false, "evidence": [{"source_id": "wikis:miraheze", "url": "...", "revision": 12345}],
   "artwork_asset_ids": []}],
 "disagreements": [{"song_id": "...", "field": "...", "values": {"wikis:miraheze": "...", "wikis:fandom": "..."}}]}
```

`in_original_game` requires positive evidence (original `Template:Song`, an original-game category, or the Japanese original-game list). A page only in Universe categories/`Template:SongUV` is `false` and does not enter the denominator.

## Catalog `data/catalog.json` and `data/catalog.js` (coordinator-generated)

`catalog.js` is exactly `window.DYNAMIX_CATALOG = <catalog.json content>;\n` with `</` escaped as `<\/` and U+2028/U+2029 escaped.

Top level: `schema_version`, `owner` (`"FridrichMethod"`), `game` (`"Dynamix"`), `repository` (`"https://github.com/FridrichMethod/dynamix"`), `snapshots` ({family: fetched_at}), `sources[]` (with `family`, `asset_count`), `assets[]`, `failures[]`, `summary`.

Catalog asset (deduplicated by `sha256`; primary record chosen by family priority `artists` < `official` < `wikis` < `archives`, then source ID, then asset ID): all Asset fields of the primary record plus

- `family`, `source_name`, `url` (URL-encoded relative path), `thumb` (relative preview path or null), `gallery` (bool), `slideshow` (bool), `site_original` (bool), `archive_url` (`https://github.com/FridrichMethod/dynamix/raw/main/<url>`), `provenance[]` (every source record with this SHA-256, each carrying `source_id`, `source_name`, `family`, `page_url`, `download_url`, `resolved_url`, `quality`, `rights`, `mapping_status`, `mapping_evidence`, `credit_evidence`, `illustrators`, `composers`, `song_titles`, `source_reported`, `comparisons`, `upstream_status`, `superseded_by`, `notes`), `variants` (sorted IDs of other catalog assets sharing `variant_group`), and merged `illustrators`/`composers`/`song_titles`/`song_ids`/`collections` (ordered union across provenance).
- `gallery`: raster image whose `kind` is not `reference`. `slideshow`: `gallery` and `kind` ∈ {`song_art`, `pack_cover`, `promotional`, `character`} and long edge ≥ 1000 px.
- `summary`: `source_count`, `source_asset_records`, `unique_files`, `gallery_images`, `slideshow_images`, `exact_duplicate_records`, `by_family` (records), `by_kind` (unique files), `failure_count`, `downloaded_bytes` (unique), `site_original_bytes`, `archive_only_bytes`.
- Ordering: assets by (`kind` order as listed above, normalized title, `id`). No wall-clock timestamps; snapshots come from manifests.

## Previews `data/thumbs.json` and `assets/thumbs/` (coordinator-generated)

Port DEEMO: WebP, long edge 480 (never enlarged), quality 75, method 6, Pillow 12.3.0 / libwebp 1.6.0 recorded; ICC → sRGB handling as in the reference; filename `assets/thumbs/<sha256[:16]>.webp` with a collision check that fails instead of overwriting; `--check`, `--verify`, `--prune` (prunes only files listed as derived). GIFs use the first frame.

## Coverage `data/coverage.json` (coordinator-generated)

Separate metrics as in 07-data-contracts §Catalog and coverage, from the song index, discovery snapshots, manifests and catalog: sources by status and enumeration completeness; candidates by disposition and exclusion reason per source (reconciling); asset records / unique files / gallery / variants / duplicates; songs in denominator, with any artwork, with confirmed artist/official original, with decoded long edge ≥ 1920; unknown illustrator, unmapped, conflicts, missing-art songs (listed by ID), preview-only, source mismatches; bytes by family, by tier, staged-site estimate.

## UI contract

- Globals: `window.DYNAMIX_CATALOG`, `window.DYNAMIX_I18N`; language storage key `dynamix-lang`; URL `?lang=en|zh-CN` (DEEMO semantics). Page title/brand "Dynamix Artwork Archive" / "Dynamix 曲绘档案".
- Archive grid uses `thumb` (fallback to `url` only when `site_original`); viewer shows `url` when `site_original`, else the preview plus an "original in repository" link to `archive_url`. Credits display `illustrators` as **Illustrator** and `composers` as **Composer**; empty = "Unknown". Every upstream string is rendered as text (`textContent`), never HTML.
- Filters: text, source family/source, kind, minimum long edge; sort: title, source, pixel area. Pagination 60 per page. Deep links `archive.html#<asset id>` and slideshow `index.html#<asset id>`; keep DEEMO URL semantics.
- No remote fonts, analytics, audio, or network requests during browsing; works from `file://`, HTTP root and an HTTP `/dynamix/` subpath.

## CLI contract

```text
scripts/fetch_wikis.py    [--metadata-only] [--resume] [--limit N] [--source miraheze|fandom|atwiki-ja|rhythmgame-ja] [--include-animated] [--verify]
scripts/fetch_artists.py  [--metadata-only] [--resume] [--verify]
scripts/fetch_official.py [--metadata-only] [--resume] [--verify]
scripts/fetch_archives.py [--metadata-only] [--resume] [--verify]
scripts/build_thumbnails.py [--check | --verify | --prune]
scripts/build_catalog.py  [--verify] [--check]
scripts/build_coverage.py [--check]
scripts/check_sources.py  --report PATH   (manual, read-only; compares live metadata with saved snapshots)
scripts/prepare_pages.py  --output PATH
```

- `--verify` and `--check` run offline and exit nonzero on any mismatch; `--check` compares regenerated output to committed files without writing.
- Every fetch/build script also accepts `--root PATH` (default: repository root) so tests run the real CLI against `tests/fixtures/contract/` without touching committed data.
- Import convention: scripts run as `python -I scripts/<name>.py` and import shared code as `from lib import ...` (`sys.path[0]` is `scripts/`); tests insert `ROOT / "scripts"` into `sys.path`. Tests are unittest modules runnable as `python -I tests/test_<name>.py`.
- Network defaults (shared helper, not per-script): one request at a time per host, ≥ 1.0 s spacing, `Retry-After` honored up to 60 s, ≤ 3 attempts for 429/5xx/timeouts, no retry on 401/403/challenge (recorded as `blocked`), 64 MiB max response, explicit host allowlist re-checked on every redirect, private/loopback addresses rejected, User-Agent `DynamixArtworkArchive/1.0 (+https://github.com/FridrichMethod/dynamix)`.
- Downloads: unique temp file in the destination directory → decode/verify → `os.replace`; a crash never leaves a successful-looking record. `--resume` re-hashes existing files instead of trusting existence.

## Path ownership (wave 1–2)

| Branch | Owns |
| --- | --- |
| `impl/contracts` (bootstrap) | `scripts/lib/**`, `tests/test_lib_*.py`, `tests/test_fixtures_contract.py`, `tests/fixtures/contract/**`, `requirements*.txt`, `.gitattributes`, `LICENSE` |
| `impl/wikis` | `scripts/fetch_wikis.py`, `scripts/wikis/**`, `tests/test_fetch_wikis*.py`, `tests/fixtures/wikis/**`, `data/sources/wikis.json`, `data/discovery/{miraheze,fandom,atwiki-ja,rhythmgame-ja}.json`, `data/discovery/song-index.wikis.json`, `assets/public/wikis/**` |
| `impl/primary-sources` | `scripts/fetch_{artists,official,archives}.py`, `scripts/primary/**`, `tests/test_fetch_{artists,official,archives}*.py`, `tests/fixtures/{artists,official,archives}/**`, `data/sources/{artists,official,archives}.json`, their `data/discovery/*.json`, `assets/public/{artists,official,archives}/**` |
| `impl/ui` | `archive.html`, `templates/slideshow.html`, `src/**`, `assets/site/**`, `site.webmanifest`, `browserconfig.xml`, `tests/test_{i18n,layout,slideshow,archive_labels}*.py`, `tests/browser_smoke.py`, `tests/fixtures/ui/**`, `tests/fixtures/browser/**`, `licenses/*-MIT.txt` for reused vendor JS |
| `impl/catalog` | `scripts/build_{catalog,thumbnails,coverage}.py`, `tests/test_{catalog,thumbnails,coverage,provenance}*.py`, `tests/fixtures/catalog/**` |
| `impl/ci` | `.github/workflows/**`, `scripts/{prepare_pages,check_sources}.py`, `tests/test_{pages,check_sources,workflows,line_endings,licensing}*.py`, `tests/run_all.py` |
| coordinator | root docs, `NOTICE`, `docs/implementation/**`, `data/sources/song-index.json`, `data/{catalog.json,catalog.js,thumbs.json,coverage.json}`, `assets/thumbs/**`, `index.html`, merges, remote |
