# Coverage

**English** · [简体中文](coverage.zh-CN.md)

`data/coverage.json` reports what the archive holds and what it is missing. `scripts/build_coverage.py` generates it from the song index (`data/sources/song-index.json`), the discovery snapshots (`data/discovery/*.json`), the family manifests (`data/sources/<family>.json`) and the catalog. Every metric is a separate, defined count: records, files, images and songs are different quantities and are never used in place of one another. The report also carries these definitions in its `definitions` field.

The report is dated by its inputs (the song index's `as_of`, each manifest's `fetched_at` and each snapshot's `finished_at`), never by the clock, so a rebuild from the same inputs is byte-identical. `build_coverage.py` refuses to write when the inputs do not reconcile: per-source disposition totals, links between candidates and asset or failure records, source counts, and asset song IDs against the song index. `build_coverage.py --check` compares the committed report with a fresh build without writing.

## Current numbers

The numbers between the markers below are generated from `data/coverage.json` when the catalog is built; they are not written by hand.

<!-- STATS:BEGIN -->
<!-- STATS:END -->

## Sources

`sources` counts the registered sources by family and by `status`:

| Status | Meaning |
| --- | --- |
| `available` | The source was read and its in-scope files could be fetched. |
| `partially_available` | Part of the source could be read (for example a list that renders only partly, or an account whose full history needs a login). |
| `temporarily_unavailable` | The source could not be reached at the last check (for example a host name that did not resolve). |
| `manual_access` | A login, Referer check or bot challenge stands in the way; it is recorded and never bypassed. |
| `metadata_only` | The source provides evidence (song lists, credits, identity) but no acquirable file. |
| `excluded` | Every candidate of the source is out of scope (for example a negative control). |

`sources.enumeration` sorts the sources into `complete` (the discovery snapshot followed every list to its end), `incomplete` (a list is still open, or the source is not enumerable as a whole) and `not_enumerated` (no snapshot). An incomplete enumeration is never read as "nothing more exists". `registered_without_record` lists registered source IDs that no manifest declares. Each row of `sources.records` gives a source's status, evidence level, completeness, open continuations, discovery errors, candidates by disposition, exclusion reasons, asset records, the catalog entries whose primary record it holds, and failures by error class.

Evidence levels: `live_api` (a live API or JSON endpoint was read), `page_read` (a web page was read), `search_index` (a research lead, recorded without a request), `blocked` (the only read was refused), `not_established` (no reading succeeded).

## Candidates and dispositions

A candidate is one upstream file or item that a discovery found. Every candidate of a snapshot has exactly one disposition:

| Disposition | Meaning |
| --- | --- |
| `downloaded` | This source has its own asset record and file for it. |
| `duplicate_bytes` | The same source already archived identical bytes under another candidate; it points at that record and no file is written. |
| `excluded` | Out of scope, with a reason from a fixed vocabulary (see below). |
| `failed` | A download or check failed; the manifest's `failures` holds the phase, error class, HTTP status and attempts. |
| `blocked` | Access was refused (401, 403, login, Referer check or bot challenge) and not retried. |
| `pending` | Not decided yet: `awaiting_download`, `unresolved_role`, `unresolved_game`, or a curated hold with its reason. |

Per source, `downloaded + duplicate_bytes + excluded + failed + blocked + pending` equals the number of candidates, and the build fails otherwise. `candidates.by_exclusion_reason` uses the reasons `universe_only`, `not_dynamix`, `animated_capture`, `person_photo`, `site_ui`, `game_ui`, `video_placeholder`, `external_release_art`, `fanart`, `upscaled`, `rejected_submission`, `redirect_or_alias` and `out_of_scope_other`. `candidates.by_game` counts the candidates' game association (`dynamix`, `dynamix_universe`, `mixed`, `unknown`, `not_dynamix`).

## Files and storage tiers

| Metric | Definition |
| --- | --- |
| `source_asset_records` | Asset records across the family manifests, one per source and file. |
| `unique_files` | Catalog entries: distinct SHA-256 values. Identical bytes from several sources count once. |
| `exact_duplicate_records` | `source_asset_records` minus `unique_files`. |
| `gallery_images` | Catalog entries that are raster images and not of kind `reference`. |
| `slideshow_images` | Gallery entries of kind `song_art`, `pack_cover`, `promotional` or `character` whose long edge is at least 1000 px. |
| `by_kind`, `by_format` | Catalog entries per kind and per decoded format. |
| `by_tier` | Catalog entries whose original the website serves (`site`) or only previews (`archive`). |
| `variant_groups`, `entries_in_variant_groups`, `entries_with_variants` | Groups of related crops or versions, and the entries in them. Variants are separate files; they never multiply song counts. |
| `records_by_upstream_status` | Records that are `current`, `removed`, `superseded`, `unknown` or unset upstream. |
| `transformed_records` | Records whose stored bytes are a known platform re-encode. |
| `gallery_long_edge` | Gallery entries by decoded long edge: under 1000 px, under 1920 px, at least 1920 px. |
| `entries_with_preview` | Catalog entries with a WebP preview. |

## Songs

The denominator is the set of songs in `data/sources/song-index.json` with `in_original_game: true`. A song enters it only with positive evidence of being in the original game: an original-game `Template:Song` on an English wiki page that is not in `Category:Unreleased`, or a row of the Japanese original-game wiki's lists. Songs only in Dynamix Universe, unreleased songs and songs listed only by the corroborating wikiwiki table stay outside it (`outside_denominator`). The index is dated (`index_as_of`); it is not a claim about the game's current song count. `denominator_removed` and `denominator_universe_also` count songs that were removed from the game or also appear in Dynamix Universe.

Song art is a gallery entry of kind `song_art`. It links a song only through its provenance records that name that song, at those records' mapping status; a song is never linked by matching titles. Confirmed song art is linked by a record whose mapping status is `confirmed_primary` or `confirmed_wiki`.

| Metric | Definition |
| --- | --- |
| `with_any_artwork` | Denominator songs with confirmed song art. All per-song metrics below count confirmed song art only. |
| `inferred_only` | Songs without confirmed song art whose song-art links are all `inferred`. |
| `conflict_only` | Songs without confirmed song art with at least one `conflict` link. |
| `missing_art` | Songs without any song-art link. `with_any_artwork + inferred_only + conflict_only + missing_art` equals the denominator. |
| `with_any_linked_asset` | Songs named by any catalog entry of any kind and mapping status, screenshots included. |
| `with_confirmed_original` | Songs with song art that has a record from the artists or official family, with `origin_status` `artist_post` or `official`, `mapping_status` `confirmed_primary` naming the song, and a site-original rendition. This is not a claim that the artist's master was found. |
| `with_long_edge_ge_1920` | Songs whose confirmed song art has a decoded long edge of at least 1920 px. |
| `low_resolution_only` | Songs whose confirmed song art all have a long edge under 1920 px. `best_long_edge` buckets the songs by their largest confirmed song art (under 1000, under 1920, at least 1920 px). |
| `with_site_original`, `variant_unknown`, `preview_only` | Songs by the best rendition among their confirmed song art: a site original, only an unknown variant, or only previews or platform re-encodes. The three add up to `with_any_artwork`. |
| `unknown_illustrator` | Songs whose confirmed song art all have an empty illustrator list. |

The 1920 px bucket is a measure of display size only. A file of at least 1920 px is not thereby authentic, complete, uncropped or the artist's master, and a smaller file says nothing about whether a larger copy exists. A record's rendition is `site_original` when its `quality.source_variant` is `site_original` and it is not a known transform, `preview` when its variant is `preview` or it is a known transform, and `unknown` otherwise.

## Mapping statuses

| Status | Meaning |
| --- | --- |
| `confirmed_primary` | The artist's or an official post or page names the song; the quoted field is in `mapping_evidence`. |
| `confirmed_wiki` | A wiki song template, caption or reviewed list page names the song. |
| `inferred` | Weaker evidence (for example an image linked from a song list, or a research source rather than the fetched page); needs review. |
| `unmapped` | No song is linked. Characters, backgrounds and promotional art are often unmapped by nature. |
| `conflict` | Sources disagree about the song. |

## Gaps

`gaps` lists the IDs behind the counts so that each gap can be reviewed: `missing_art_songs`, `inferred_only_songs`, `conflict_only_songs`, `low_resolution_only_songs`, `preview_only_songs`, `variant_unknown_songs`, `unknown_illustrator_songs` and `linked_outside_denominator` (songs linked by a file but outside the denominator); `unknown_illustrator_assets` (artwork entries, not screenshots or references, with no credited illustrator); `unmapped_assets`, `conflict_assets` and `inferred_only_assets`; and the `pending_candidates`, `blocked_candidates` and `failed_candidates` of every snapshot.

## Bytes

| Metric | Definition |
| --- | --- |
| `records_by_family` | Bytes of all records per family (a file held by several records counts once per record). |
| `unique_by_primary_family`, `unique_total` | Bytes of the catalog entries, each counted once, by the family of the entry's primary record. |
| `by_tier` | Bytes of site-tier and archive-tier originals. |
| `previews` | Bytes of the WebP previews. |
| `catalog_json`, `catalog_script`, `index_html`, `coverage_json` | Sizes of the generated files. |
| `staged_site_estimate` | Site-tier originals, previews and the generated files above. The static pages, documents and icons that `prepare_pages.py` also stages are not included; `prepare_pages.py` enforces the exact budget. |
| `pages_budget`, `within_pages_budget` | The 950,000,000-byte website budget and whether the estimate fits it. |

## Metadata disagreements

Sources do not always agree, and the archive records disagreements instead of resolving them silently.

- **Song index disagreements.** When the wikis disagree about a song, the song index keeps a `disagreements` entry naming the song, the field and each source's value. Fields recorded this way include `title` (for example a title spelled differently on Miraheze and on the Japanese wiki), `composers`, `presence` (a song listed by one source but without a song page on the English wikis), `release_status` (a song on the Japanese removed-song list) and `in_original_game`. `fetch_wikis.py` proposes these entries in `data/discovery/song-index.wikis.json`; the reconciled list in `data/sources/song-index.json` is the one the coverage report counts as `mismatches.song_index_disagreements` (song and field).
- **Source-reported mismatches.** `mismatches.source_reported` lists every record where a value the source advertised (SHA-1, byte length, dimensions or MIME type) differs from the saved bytes. A value the source did not advertise is not comparable and never counts as a mismatch.
- **Record disagreements.** `mismatches.record_disagreements` lists identical files whose records from different sources disagree about the kind or the variant group.
- **Song index versus records.** `mismatches.song_index_artwork` lists songs whose `artwork_asset_ids` in the song index differ from the records that name the song.
- **Other cross-checks.** `mismatches.transformed_records` lists the known platform re-encodes; `duplicate_bytes_other_source` lists candidates whose identical bytes were archived by another source; `successor_not_primary` lists superseded records whose successor is not the primary record of its catalog entry.

A disagreement is a review item. It is resolved only with evidence, by changing the source rule or the reconciled index and rebuilding, never by deleting the record that disagrees.
