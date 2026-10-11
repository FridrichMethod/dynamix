# Frozen implementation contracts (schema version 1)

Frozen by the coordinator on **2026-10-10** before parallel implementation. This document refines [07-data-contracts.md](07-data-contracts.md) into exact names. Workers implement against it; any change is proposed to the coordinator, committed once on `impl/integration`, and merged into dependent branches. `scripts/lib/schema.py` is the executable form of this document; if they disagree, report it rather than picking one silently.

## Measured inputs behind these decisions (2026-10-10 probes)

- Miraheze `allimages`: 4,469 files, 2,102,094,760 bytes (API sizes). Fandom: 4,924 entries (569 `video/youtube`), 2,070,864,118 bytes.
- Fandom's CDN serves **transformed WEBP** for `/revision/latest` URLs. Appending `format=original` returns the file Fandom's CDN stores instead of that WEBP; whether it is the upload is judged against the API values, not assumed. For sampled files the Fandom `format=original` bytes were **byte-identical to Miraheze** (SHA-1 equal to the Miraheze API value) while the **Fandom API `size`/`sha1` differed** by 18–530 bytes. Exact-byte dedup therefore collapses the two wikis naturally; the Fandom API mismatch is recorded as `source_reported` evidence, not as a second version.
- Original-game song pages (Miraheze `Category:Songs`, excluding `Dynamix Universe` categories): ~510 template covers (`Imgsuffix`), ~460 MB. Core artwork roles together ≈ 780 MB. Chart-list announcement graphics ≈ 271 MB; animated character GIFs (≤250 px) ≈ 634 MB; probable Universe square `_cover` files ≈ 151 MB.

## Storage and publication policy

| Tier | Kinds | Stored in Git | Original served by Pages |
| --- | --- | --- | --- |
| `site` | `song_art`, `pack_cover`, `character`, `background`, `part`, `promotional`, `process` | yes | yes |
| `archive` | `reference`, `gameplay_screenshot`, `store_screenshot`, any PDF | yes | **no** — site shows the preview; the viewer links the original at `archive_url` (GitHub raw URL on `main`) |

- `build_catalog.py` sets `site_original` from this table. `prepare_pages.py` omits originals whose catalog record has `site_original: false` and fails if the remaining payload exceeds **950,000,000 bytes**. If the `site` tier alone exceeds the budget, that is a reported blocker, never fixed by recompression or dropping records.
- Excluded (recorded candidates, never downloaded by default): animated GIF captures (`animated_capture`; the larger `-Raw.png` still is archived), Universe-only (`universe_only`), person photos, site/game UI, video placeholders, fanart, upscales, rejected submissions. `fetch_wikis.py --include-animated` exists so the decision is reversible.
- No Git LFS, no release attachments, no paid storage. No single file may exceed 100 MiB; a push must stay under 2 GB.

### Tier revision 1.1 (2026-10-10, after metadata-only wiki discovery)

Measured site-tier estimate before this revision: wikis 944,412,040 B (2,971 unique files) plus artists/official/archives 132 MB, before previews and catalog — over the 950,000,000 B budget. Two non-artwork blocks moved to the archive tier; nothing is dropped or recompressed:

- **Kind `reference`** now explicitly covers informational graphics and documents: official chart/difficulty-list, comeback/new-chart and update-note announcement graphics (wiki `*_Chart_List*`, `*_Comeback*`, `*_New_Charts*` and equivalents, ~789 files / ~256.5 MB), press PDFs and similar documents. **Kind `promotional`** keeps key art, banners, event/anniversary/collaboration visuals and teasers that are not chart-level listings.
- **`store_screenshot`** moves to the archive tier (app-store renditions are not artwork masters).
- Re-measure after acquisition; if the site tier still exceeds the budget, it is a reported blocker resolved by an explicit, documented tier decision, not by dropping or recompressing originals.

### Coordinator classification decisions (2026-10-10, wave 2 review)

- **List and chart-announcement graphics are `reference`** (amended 2026-10-10 after wave 3; supersedes the earlier single-song exception). `reference` covers (a) any announcement graphic that displays chart difficulty levels, whether for one song or several — single-song "<song> Chart List" / "COMING SOON" / "i'm back!" teasers with level marks included, on the wikis and on official X/Facebook posts alike — and (b) any graphic whose primary content is a list of songs or charts (event and comeback lineups, "Upcoming Events", "Comeback Tracks", pack/collaboration lineup cards that print several titled jackets). The jacket artwork shown inside these graphics is archived separately as `song_art`. `promotional` keeps graphics without chart-level information that concern a single song, pack or event: key art, event and anniversary posters, single collaboration key visuals, banners, logos/wordmarks.
- Reason recorded for the amendment: the wiki's single-song Chart List graphics and the official single-song teasers are the same design (verified side by side by the wiki worker), so one rule must cover both; the earlier wording contradicted itself and, applied literally, moved ~650 unique files (~186 MB) into the site tier.
- `fetch_wikis.py` exit codes: 0 ok, 1 failed/blocked downloads, 2 incomplete discovery, 3 lock held or unsafe work directory/journal, 4 archived file no longer accepted (review), 5 internal error, 130 interrupted (outputs written). Default work directory `<root>/.cache/fetch_wikis/work` (ignored by Git).
- App Store records: per-request URL tokens are not stored; source checks compare App Store items by decoded pixels, not bytes.
- `mapping_status: conflict` may appear on song art; consumers must display it, not resolve it. Known case: *Another World* (wiki artist anchor `Artists#Cosmograph`), left for a curator.

### Coordinator decisions (2026-10-11, wave 4 review)

- **"Several titled jackets" (D2) is read literally:** at least two jackets whose song titles are printed legibly on the card. First-reveal cards of a lineup series (one titled jacket plus `?` slots, no level marks) and collages of untitled jackets are `promotional`; later cards of the same series that print two or more titled jackets are `reference`. A curated rule that cites D2 names at least two titles.
- **Song and vote lists stay lists.** Clause (b) of D1 still covers graphics whose primary content is a list of tracks — "Comeback Tracks" lists, comeback polls and vote calls — even when their jackets carry no legible titles (e.g. `File:One_Extra_Day_Event.jpg`, `File:Vote_Comeback_Tracks_via_FB.jpg`, `File:Vote_Comeback_Tracks_via_Twitter.jpg`, `File:Comeback_tracks_polling_Till_26_July_2018_12nn.jpg`): they are `reference`. The untitled-collage rule above concerns pack and collaboration key visuals, not track lists.
- **Level marks trigger D1, difficulty names alone do not.** Anniversary and event posters that name new charts only by difficulty name (e.g. "GIGA", "MEGA") without level numbers or level marks stay `promotional`.
- **One file, one kind.** Every record of one SHA-256, in every source and family, carries the same `kind`; `build_catalog.py` refuses to group records that disagree (and the primary-source `--verify` checks it within its families).
- **Fallback titles stay.** Wiki files without a caption or a confirmed single song keep the `File:`-title fallback (the wiki's own page name, `_` as spaces). No archivist-invented titles are added; opaque fallback titles (for example numeric upload IDs) are a documented limitation.
- **Archived fallback renditions.** When the URL a fetcher derives answers 404 and the record is archived from the URL the page itself lists, `source_reported.url` is that listed URL and `quality.source_variant` is `unknown`, with a note.
  Owner decision (2026-10-11, review finding DX3-02): a 401 or 403 on a fetcher-derived URL that has page-listed fallback URLs is treated like the 404 (object stores answer 403 for a missing key). The listed URLs are tried for that item only, the host is not marked blocked for it, and the note names the status that was answered. A 401/403 on any other URL is still `blocked`, with no retry.
- **Other games named in captions.** Owner decision (2026-10-11): an "Artwork in/for X" caption records another game only when X names a game on the reviewed list in `scripts/wikis/config.py`. Any other name (an unlisted event, holiday or game) records no association, and the item's evidence carries a note asking a curator to review it. New collaborations need a reviewed list entry. Universe-only rules and the decisions above are unchanged.
- **"Supplied by the developer"** appears in `quality.notes` only where the page itself labels the material as supplied; otherwise the note says the page does not state who supplied the image.
- **Fandom/Miraheze byte identity** is documented as measured ("every accepted title both wikis hold except one"), never as a claim that either copy is the original upload.

### Catalog presentation rules (2026-10-10, from the trial integration build)

- **Primary record.** Among records sharing a SHA-256 the primary is the minimum of (family priority `artists` < `official` < `wikis` < `archives`; source preference within the family: `wikis:miraheze` (current wiki; its stored bytes are the bytes both English wikis serve), then `wikis:fandom` (legacy), then every other source, tied; then `quality.source_variant` `site_original` < `preview` < `unknown`; then source ID; then asset ID). The rule is deterministic and recorded in `build_catalog.py`.
- **Titles.** `title` is a human-readable name, not a file stem. Wiki song art whose candidate maps to exactly one song with `confirmed_wiki` (or stronger) evidence is titled with that song's exact title; other wiki files use the wiki's own caption when one exists, else the `File:` title without extension and with `_` as spaces. The upstream file name always stays in `original_filename`. Other families title records from the post/page text they cite.
- **Pages payload.** `data/catalog.json` and `data/catalog.js` are both published as long as the staged site stays within budget; if it does not, `catalog.json` (which the pages never load) is the first thing removed from the payload, by an explicit `prepare_pages.py` rule.

## Identifiers and paths

- Source IDs: `<family>:<slug>`; family ∈ `wikis`, `artists`, `official`, `archives`. Registered: `wikis:miraheze`, `wikis:fandom`, `wikis:atwiki-ja`, `wikis:rhythmgame-ja`, `official:apple-store`, `official:google-play`, `official:taptap`, `official:website`, `official:c4cat`, `official:facebook`, `official:x`, `archives:hkstp`, `archives:4gamer`, `archives:wayback`. Artists: `artists:<platform>:<handle>` (e.g. `artists:x:ariiol_`, `artists:artstation:sandgodoven`). New IDs are registered by the owning worker in its family manifest; slugs are lowercase `[a-z0-9-]`.
- Candidate ID: `<source_id>:<stable upstream key>` (wiki: the canonical `File:` title with spaces as `_`; platforms: post/asset ID plus page index).
- Asset ID: `<source_id>:<sha256[:16]>`; stable across title edits and refreshes.
- Asset path: `assets/public/<family>/<source-slug>/<slug(original-name-stem)>--<sha256[:12]>.<ext>` where `<ext>` comes from the **sniffed** format (`png`, `jpg`, `webp`, `gif`, `pdf`). The upstream filename is kept verbatim in `original_filename`. Slugs are NFKC, ASCII-transliteration not required: allowed characters `[A-Za-z0-9._-]` plus a hash suffix; empty slugs become `file`. Paths are checked for case-fold collisions.
- Song ID: `song:<slug(exact title)>` with `-2`, `-3` suffixes on collision, assigned deterministically by sorted exact title.
  Coordinator decision (2026-10-11, review finding DX2-09): `schema.validate_song_index` checks that assignment. Songs with an identical exact title (two wiki pages giving the same title; `wikis/songindex.py` breaks the tie by page key) take the IDs their group is assigned in any order, because the order inside the tie cannot be checked; the set of IDs does not depend on it.

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

- `disposition`: `downloaded`, `duplicate_bytes`, `excluded`, `failed`, `blocked`, `pending`.
- `downloaded`: this source has its **own** asset record at its **own** contract path. Identical bytes obtained from several sources (e.g. Miraheze and Fandom `format=original`) are stored at each source's path — Git stores a single blob — and the catalog merges the records by SHA-256 into one entry with every provenance record. `prepare_pages.py` stages only the file named by each catalog entry's `url`, so the site payload holds one copy per SHA-256.
- `duplicate_bytes`: the **same source** already archived identical bytes under another candidate (e.g. two `File:` titles with the same content); `asset_id` names that existing record and no new record or file is written.
- Rule changes accepted from the contracts review (2026-10-10): song IDs whose slug loses characters (non-ASCII or empty) get an 8-hex SHA-256 suffix of the NFC title (`song:file-568a196c`), numeric suffixes only for ASCII collisions, and `schema.validate_song_links` cross-checks asset song IDs; catalog provenance entries also carry `song_ids` and `collections`; artist platform segments are `[a-z0-9]+` and source slugs are unique across families; an older MediaWiki revision under the same candidate keeps `upstream_status: superseded` with a `superseded_by` chain to the current asset, or no successor when the newer revision was not archived.
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

`catalog.js` is exactly `window.DYNAMIX_CATALOG = ` + the catalog as compact JSON (`separators=(",", ":")`, `ensure_ascii=False`) with every `<` written as `\u003c` and U+2028/U+2029 escaped + `;\n` (the DEEMO reference encoding).

Top level: `schema_version`, `owner` (`"FridrichMethod"`), `game` (`"Dynamix"`), `repository` (`"https://github.com/FridrichMethod/dynamix"`), `snapshots` ({family: fetched_at}), `sources[]` (with `family`, `asset_count`), `assets[]`, `failures[]`, `summary`.

Catalog asset (deduplicated by `sha256`; primary record chosen by the primary-record rule in "Catalog presentation rules" above): all Asset fields of the primary record plus

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

## Slideshow rendering interface

`scripts/render_slideshow.py` (UI-owned) exposes `render_slideshow(root: Path, catalog: dict) -> str`, ported from DEEMO's `render_slideshow`/`art_layout`/`slide_*` helpers: it reads `templates/slideshow.html`, replaces the single `@python-work-area` placeholder with one `<div class="mySlides fade"><img ...></div>` per catalog asset whose `slideshow` is true (catalog order), and HTML-attribute-escapes every value. `build_catalog.py` (catalog-owned) imports it to write `index.html`; it must not duplicate slide markup.

## CLI contract

```text
scripts/fetch_wikis.py    [--metadata-only] [--resume] [--limit N] [--source miraheze|fandom|atwiki-ja|rhythmgame-ja] [--include-animated] [--verify]
scripts/fetch_artists.py  [--metadata-only] [--resume] [--verify]
scripts/fetch_official.py [--metadata-only] [--resume] [--verify]
scripts/fetch_archives.py [--metadata-only] [--resume] [--verify]
scripts/build_thumbnails.py [--check | --verify | --prune]
scripts/build_catalog.py  [--verify] [--check]
scripts/build_coverage.py [--check]
scripts/update_docs_stats.py [--check]   (fills the STATS blocks of README*/docs from data/coverage.json + data/catalog.json; exit 0 current/rewritten, 1 stale under --check, 2 bad or mismatched inputs/markers)
scripts/check_sources.py  --report PATH   (manual, read-only; compares live metadata with saved snapshots)
scripts/prepare_pages.py  --output PATH
```

- `--verify` and `--check` run offline and exit nonzero on any mismatch; `--check` compares regenerated output to committed files without writing.
- Every fetch/build script also accepts `--root PATH` (default: repository root) so tests run the real CLI against `tests/fixtures/contract/` without touching committed data.
- Import convention: scripts run as `python -I scripts/<name>.py`. On Python ≥ 3.11 `-I` implies `-P`, so the script directory is **not** on `sys.path`: every entry script inserts `str(Path(__file__).resolve().parent)` itself before `from lib import ...`; tests insert `ROOT / "scripts"`. Tests are unittest modules runnable as `python -I tests/test_<name>.py`.
- Registered extra flags: `fetch_wikis.py --only TITLE|CANDIDATE_ID` (repeatable), `--work-dir`, `--lock-file`, `--cache-dir`. Fetchers that run longer than a few minutes take an exclusive `fcntl.flock` lock, write manifests atomically and periodically, and are resumable.
- `pending` reasons may use the prefixes `awaiting_download`, `unresolved_role`, `unresolved_game` (plus `: detail`); only `excluded` uses the frozen exclusion vocabulary. `awaiting_download` means an admitted candidate with a known file URL that is not yet archived. A candidate that is not admitted is `unresolved_role` when nothing establishes what its file shows: a lead with no file to fetch (nothing recoverable, no listing or post found), or a registry lead whose file URL is known but whose role was never reviewed. Account or owner mismatches, Universe mentions and missing seed evidence are `unresolved_game` (final-review decision). Role may be established by archivist visual inspection of the file when page evidence is absent; that evidence is recorded verbatim (e.g. `visual_inspection: square jacket with song title text`) and the mapping is at most `inferred`.
- Network defaults (shared helper, not per-script): one request at a time per host, ≥ 1.0 s spacing, `Retry-After` honored up to 60 s, ≤ 3 attempts for 429/5xx/timeouts, no retry on 401/403/challenge (recorded as `blocked`), 64 MiB max response, explicit host allowlist re-checked on every redirect, private/loopback addresses rejected, User-Agent `DynamixArtworkArchive/1.0 (+https://github.com/FridrichMethod/dynamix)`.
- Downloads: unique temp file in the destination directory → decode/verify → `os.replace`; a crash never leaves a successful-looking record. `--resume` re-hashes existing files instead of trusting existence.

## Path ownership (wave 1–2)

| Branch | Owns |
| --- | --- |
| `impl/contracts` (bootstrap) | `scripts/lib/**`, `tests/test_lib_*.py`, `tests/test_fixtures_contract.py`, `tests/fixtures/contract/**`, `requirements*.txt`, `.gitattributes`, `LICENSE` |
| `impl/wikis` | `scripts/fetch_wikis.py`, `scripts/wikis/**`, `tests/test_fetch_wikis*.py`, `tests/fixtures/wikis/**`, `data/sources/wikis.json`, `data/discovery/{miraheze,fandom,atwiki-ja,rhythmgame-ja}.json`, `data/discovery/song-index.wikis.json`, `assets/public/wikis/**` |
| `impl/primary-sources` | `scripts/fetch_{artists,official,archives}.py`, `scripts/primary/**`, `tests/test_fetch_{artists,official,archives}*.py`, `tests/fixtures/{artists,official,archives}/**`, `data/sources/{artists,official,archives}.json`, their `data/discovery/*.json`, `assets/public/{artists,official,archives}/**` |
| `impl/ui` | `archive.html`, `templates/slideshow.html`, `scripts/render_slideshow.py`, `tests/test_render_slideshow.py`, `src/**`, `assets/site/**`, `site.webmanifest`, `browserconfig.xml`, `tests/test_{i18n,layout,slideshow,archive_labels}*.py`, `tests/browser_smoke.py`, `tests/fixtures/ui/**`, `tests/fixtures/browser/**`, `licenses/*-MIT.txt` for reused vendor JS |
| `impl/catalog` | `scripts/build_{catalog,thumbnails,coverage}.py`, `tests/test_{catalog,thumbnails,coverage,provenance}*.py`, `tests/fixtures/catalog/**` |
| `impl/ci` | `.github/workflows/**`, `scripts/{prepare_pages,check_sources}.py`, `tests/test_{pages,check_sources,workflows,line_endings,licensing}*.py`, `tests/run_all.py` |
| `impl/docs` (wave 2–4) | `README.md`, `README.zh-CN.md`, `NOTICE`, `.nojekyll`, `docs/sources-*.md`, `docs/coverage*.md`, `licenses/README*.md`, `scripts/update_docs_stats.py`, `tests/test_docs*.py` (generated STATS blocks are rendered by the coordinator) |
| coordinator | remaining root docs, `docs/implementation/**`, `data/sources/song-index.json`, `data/{catalog.json,catalog.js,thumbs.json,coverage.json}`, `assets/thumbs/**`, `index.html`, merges, remote |

## Tooling contract (2026-10-11, code-quality task)

Frozen by the coordinator before the review and tooling work on `quality/*` branches. `pyproject.toml`, `.pre-commit-config.yaml`, `requirements-lint.txt`, `package.json`/`package-lock.json` and `eslint.config.mjs` are its executable form; `tests/test_tooling.py` checks that they agree.

- **Versions:** ruff 0.17.0, mypy 2.4.0, coverage.py 7.16.2, pre-commit 4.6.2 with pre-commit-hooks v6.0.0 and actionlint v1.7.12, ESLint 10.12.0 with @eslint/js 10.0.1, @eslint/css 2.0.0 and globals 17.13.0. Every pre-commit `rev:` is a full commit SHA; npm packages are installed with `npm ci --ignore-scripts` from the lockfile.
- **ruff:** line length 120, target Python 3.10, the oldest version the scripts support (as at release v1.0.0); CI runs 3.14. A newer target let ruff rewrite multi-type except clauses into Python 3.14-only syntax and pyupgrade use `datetime.UTC`, so the target and mypy's `python_version` stay at that floor (final-review finding FR-DX-01). Lint stage 1 selects `E4 E7 E9 F W I B UP PLE`; stage 2 (not yet enforced) adds `C4 SIM PIE PERF PLW RUF` (without RUF001–003, which fire on Chinese text) and `S` with test exemptions. `ruff format` keeps its defaults; the mechanical formatting commit is listed in `.git-blame-ignore-revs`.
- **mypy:** stage 1 checks `scripts/lib` without strict mode (`check_untyped_defs`, `strict_equality`, warnings for redundant casts, unused ignores and unused config). Stage 2 adds `scripts/primary` and the entry scripts, stage 3 `scripts/wikis`, stage 4 the tests, then `disallow_untyped_defs` and finally `strict` for `scripts/lib`.
- **ESLint:** checks `src/**/*.js` (classic scripts, ESLint recommended rules) and `src/**/*.css` (@eslint/css recommended without `use-baseline` and `no-important`), never with `--fix`. There is no JavaScript or CSS formatter: `src/` is published as committed.
- **Coverage:** the unit tests run under `coverage run` with `patch = ["subprocess"]`, so every script they start is measured. `fail_under` is the measured baseline rounded down and may only be raised.
- **Excluded from every tool and hook:** `assets/`, `data/`, `index.html`, `LICENSE`, `licenses/*.txt`, `src/vendor/` and `tests/fixtures/`, that is every `-text` path in `.gitattributes` and every generated output. After any tooling change the build `--check`/`--verify` commands must still pass and the staged Pages payload must be unchanged except for files changed on purpose.
- **CI:** a `lint` job in `tests.yml` with read-only permissions runs `pre-commit run --all-files`; the `checks` job runs the unit tests under coverage and reports it. No new workflow, trigger or permission.

| Branch | Owns |
| --- | --- |
| `quality/integration` (coordinator) | the tool configuration above, `.editorconfig`, `.gitignore`, `.git-blame-ignore-revs`, the formatting commit, `AGENTS.md`, `docs/implementation/**`, merges, remote |
| `quality/ci` | `.github/workflows/tests.yml`, `tests/test_tooling.py`, `tests/test_workflows*.py`, `README.md`, `README.zh-CN.md` |
| `quality/fix-lib` | `scripts/lib/**`, `tests/test_lib_*.py`, `tests/test_fixtures_contract.py` |
| `quality/fix-wikis` | `scripts/fetch_wikis.py`, `scripts/wikis/**`, `tests/test_fetch_wikis*.py` |
| `quality/fix-primary` | `scripts/fetch_{artists,official,archives}.py`, `scripts/primary/**`, `tests/test_fetch_{artists,official,archives}*.py` |
| `quality/fix-rest` | the remaining `scripts/*.py`, `src/**` except `src/vendor/`, the remaining `tests/*.py`, documentation fixes from review findings |

Existing files under `tests/fixtures/` are never edited; new tests build their inputs in temporary directories.
