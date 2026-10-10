# Proposed data and command contracts

Freeze these contracts in milestone 1 before parallel implementation. They adapt DEEMO's manifest/catalog shape while removing its legacy-only assumptions. These are **proposed interfaces**, not claims that scripts already exist. Keep the schema small; require evidence fields when they support an actual acquisition or display decision.

## File layout

```text
assets/public/{wikis,artists,official,archives}/<source-id>/...
assets/thumbs/                         derived previews only
assets/site/{icons,images}/            attributable presentation assets
data/sources/{wikis,artists,official,archives}.json
data/discovery/<source-id>.json        stable metadata snapshots
data/sources/song-index.json           original-Dynamix denominator
data/sources/artist-index.json         role-specific identities and aliases
data/sources/source-registry.json      source state and crawl boundary
data/coverage.json                     generated coverage and gaps
data/catalog.{json,js}                 generated, offline browser payload
data/thumbs.json                       generated preview manifest
scripts/{fetch_wikis,fetch_artists,fetch_official,fetch_archives}.py
scripts/{build_catalog,build_thumbnails,build_coverage,check_sources,prepare_pages}.py
scripts/lib/                          shared parsing/download/schema helpers
templates/slideshow.html
src/{archive.js,archive.css,slideshow.css,i18n.js,i18n/...}
tests/fixtures/                       tiny synthetic or documented sample data
docs/sources-*.md, docs/sources-*.zh-CN.md
docs/implementation/                  this packet; excluded from site staging
```

Reuse names and contracts from DEEMO when appropriate; do not create a required `assets/legacy/` family. Empty source families are valid, but each has an explicit status. One worker owns each family manifest. The coordinator owns merged indexes and all generated outputs.

## Source registry and acquisition manifests

Manifest envelope: `schema_version`, `fetched_at`, `sources[]`, `assets[]`, `failures[]`. Preserve DEEMO's `assets` name rather than introducing parallel `records` collections. New discovery snapshots may have `candidates[]` and pagination state separately.

| Entity | Required fields and meaning |
| --- | --- |
| Source | `id`, `name`, `family`, `url`, `platform`, `status`, `checked_at`, `notes`; optional API URL, owner evidence, query/crawl scope and rate policy |
| Discovery snapshot | source ID; start/finish timestamps; queries/category/page scope; response/revision identifiers; full continuation state; `discovery_complete`; candidates; errors |
| Candidate | stable upstream ID/page/file identity, source ID, page URL, discovered file URL if any, role, game association and evidence, source-reported metadata, disposition and reason |
| Downloaded asset | stable asset ID, source ID, candidate ID, title, kind, page URL, download/resolved URLs, relative path, actual bytes/SHA-256/format/MIME/width/height, retrieval timestamp, quality and attribution fields |
| Failure | source/candidate ID, phase, URL, attempted timestamp, bounded attempt count, error class, retriable/manual flag; no downloaded-success claim |
| Song index record | stable project song ID, exact title, aliases, composer credits, pack/event/version memberships, evidence URLs/revision IDs, original-game inclusion status, linked artwork IDs |

Suggested source `status`: `available`, `partially_available`, `temporarily_unavailable`, `manual_access`, `metadata_only`, `excluded`. Candidate terminal disposition: `downloaded`, `duplicate_bytes`, `excluded`, `failed`, `blocked`; `pending` remains visible until resolved or explicitly carried as an acquisition limitation. Never overload these with upstream lifecycle status (`current`, `removed`, `superseded`, `unknown`).

Preserve raw source values separately from normalized display fields. Song and artist names stay verbatim in both UI languages; normalization may support search but must not mutate credited names. Use an evidence-backed alias table. A title match or visually similar image alone cannot establish authorship or original-game membership.

## Artwork metadata

- `kind`: `song_art`, `pack_cover`, `character`, `background`, `part`, `promotional`, `store_screenshot`, `gameplay_screenshot`, `process`, `reference`. Fanart and detected upscales are excluded from the core archive and recorded as discovery exclusions unless the user later changes scope.
- `game`: `dynamix` for accepted game assets; `game_associations[]` can preserve other documented uses. Candidates may be `dynamix_universe`, `mixed`, or `unknown`, but those values do not automatically enter the gallery.
- `artist`/`illustrator`, `composer`, `song_titles[]`, `collections[]`: freeze exact plural/singular representation before workers start. Recommended: role-tagged credit arrays internally plus DEEMO-compatible display fields at catalog output. Null means unknown; never infer illustrator from the generic word “Artist” in a song table.
- `mapping_status`: `confirmed_primary`, `confirmed_wiki`, `inferred`, `unmapped`, `conflict`; `mapping_evidence[]` stores URLs and narrowly quoted/template-field evidence. Inferred/conflicting assignments need explicit review.
- `quality`: separate `source_variant` (`site_original`, `preview`, `unknown`), `origin_status` (`artist_post`, `official`, `wiki_copy`, `repost`, `scan`, `unknown`), `master_status` (`confirmed`, `unverified`), and notes. A source's “Original” label may describe composition, not resolution.
- `rights`: rights holder/creator when known; exact rights/source URL; license text/identifier only when established; restrictions; otherwise unknown. Keep rights metadata independent of the repository's software license.
- `variant_group`, `variant_role`, `superseded_by`, `upstream_status`: related works remain distinguishable. Preserve changed upstream images as separate bytes and records.
- `source_reported`: original URL, original MIME/size/dimensions/SHA-1/timestamp/revision; `download_observed`: response MIME, sniffed format, actual dimensions/length/SHA-1/SHA-256. Record each comparison as true/false/unknown, not one undifferentiated “verified” flag.

For raster images, actual dimensions are decoded from saved bytes. For reference PDFs, set `width` and `height` to null, detect `format=PDF` and `mime=application/pdf`, validate the PDF signature/parser result, and use `gallery=false`; optional page dimensions need their own units. Keep API dimensions as separate evidence. Fandom can serve WEBP through a `.png` URL: do not rename content based only on the remote suffix or claim API SHA-1 matches without checking. Preserve a transformation/mismatch note; test resumability across these cases.

## Integrity and deterministic output

1. Download to a unique temporary file with bounded size/time/retries; decode/check it; atomically move it into the owned source directory. A interrupted file never becomes a successful record.
2. Reject path traversal, absolute file destinations, symlinks and out-of-root writes. Slug user-controlled names and append a stable identity/hash; preserve the original filename in metadata. Check case-fold collisions for Windows/macOS.
3. Allow only explicitly reviewed HTTP(S) source/CDN hosts and public destinations; revalidate redirects. Do not let wiki URLs fetch local/private services. Strip credentials/cookies from logs and manifests.
4. Honor `Retry-After`, cache metadata, begin with one concurrent request per host and at least one-second spacing as a project default, not a claimed provider limit. Cap transient retries. A 401/403/challenge becomes a reported block instead of a retry storm.
5. Follow every returned MediaWiki continuation field; classify file semantics and game identity using templates, usage and categories. Track incomplete enumeration explicitly. File namespace counts include icons, screenshots, unrelated games and media.
6. SHA-256 equality permits exact-byte dedup while retaining every provenance record. Different bytes remain separate archive variants. Pixel hashes/pHash can propose relationships but never automatically delete or replace files.
7. Deterministic ordering uses stable IDs and explicit tie-breakers, independent of fetch worker completion order. Build timestamps derive from input snapshots; offline rebuilds do not inject current time.
8. Keep archived bytes when upstream removes or supersedes a file. Resume verifies local files rather than trusting existence. Metadata-only source checks never alter the displayed corpus.
9. Preview generation never alters originals. Use the reference's small WebP display copies, record encoder version and full original hash, reject ambiguous short-hash filenames, and restrict pruning to manifest-owned derived files.
10. Encode data for HTML/JS/Markdown safely; block script terminators and unsafe links in generated payloads; render arbitrary source strings as text. No source-provided HTML is trusted as UI.

## Catalog and coverage

Keep `schema_version`, owner/game/repository fields, source snapshots, `sources[]`, deduplicated `assets[]`, `failures[]`, and `summary`. Each asset has `provenance[]`, local `url`, `gallery` eligibility and optional `thumb`. Stable IDs survive title edits and source refreshes. The slideshow uses a declared artwork eligibility set; screenshots/PDFs/references do not silently appear as song jackets.

Coverage uses an explicit, dated, reconciled original-Dynamix song index. Publish separate metrics:

- source inventories visited, complete/incomplete enumerations, and registered blocked/excluded sources;
- candidates by mutually exclusive disposition, with totals reconciling per source;
- asset records, exact unique files, gallery artworks, alternate variants and source duplicates;
- distinct songs with any qualifying artwork / all verified in-scope songs;
- distinct songs with a confirmed artist/official original, and with decoded long edge ≥1920, reported separately;
- unknown illustrator/mapping, missing-song art, preview-only art, source mismatches and unresolved scope;
- bytes by source family, full tracked corpus size and staged website size.

`≥1920` is only a measurable display-size bucket. It is not a promise of authenticity, full composition, or an artist master. A multi-song illustration may cover several explicitly associated songs; multiple variants never multiply the song denominator. Report provenance disagreements without inventing a universal current song count.

## Required CLI behavior

Freeze exact flags during bootstrap and document them in the implemented README. Target compatibility:

```text
fetch_*.py --metadata-only            enumerate/discover; no asset downloads
fetch_*.py --resume --workers N       bounded acquisition, family manifest ownership
fetch_*.py --verify                   offline bytes/provenance validation
build_thumbnails.py [--check|--verify|--prune]
build_catalog.py --verify [--check]
build_coverage.py [--check]
check_sources.py                     compare explicit saved snapshots, no remote writes
prepare_pages.py --output PATH       new external staging directory; tracked allowlist
```

Not every adapter needs the same discovery mechanism, but status/error semantics must agree. `--verify`, `--check`, and all normal tests run offline. A nonzero exit indicates verification failure or incomplete required acquisition; write diagnostic manifests before exiting where safe. Blocked optional sources remain documented limitations rather than silently successful fetches.
