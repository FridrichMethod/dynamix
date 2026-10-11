# Dynamix Artwork Archive

**English** · [简体中文](README.zh-CN.md)

A personal archive of artwork from the original **Dynamix** rhythm game by C4Cat Entertainment Limited, maintained by FridrichMethod (Zhaoyang Li). Repository: <https://github.com/FridrichMethod/dynamix>.

Only the original game is in scope. Artwork used only by the sequel, Dynamix Universe, is excluded; artwork that both games use stays in, and the other game is recorded as an association.

Every archived file keeps the exact bytes its source served, together with a provenance record: where the file came from, what the source said about it, what was actually downloaded, who is credited, and how it was linked to a song. Sources that could not be read, files that could not be fetched and candidates that were excluded are recorded as well, so gaps stay visible instead of disappearing.

The interface and build tooling are adapted from the DEEMO artwork archive ([FridrichMethod/deemo](https://github.com/FridrichMethod/deemo) at commit `b14bf0d5f5a05e5382c319f3fd5019a4936820b8`), which is itself derived from Mashiro's [mashirozx/deemo](https://github.com/mashirozx/deemo); no artwork, audio, fonts or data of that DEEMO archive were copied (adapted code only, see [NOTICE](NOTICE)).

**Status:** the website is not deployed. GitHub Pages deployment is held until a later, explicit release decision; see [Release hold](#release-hold).

## Browsing

There are two static pages. Both read the local catalog `data/catalog.js`, which `scripts/build_catalog.py` generates from the source manifests.

- `index.html` is the slideshow, rendered from [templates/slideshow.html](templates/slideshow.html). It shows song artwork, pack covers, promotional art and character art whose long edge is at least 1000 px, with previous/next navigation, shuffle autoplay, a screenshot button, and links to each file and to the archive.
- [archive.html](archive.html) is the searchable archive. Search song titles, illustrators, composers and packs; filter by source type, source, kind and minimum long edge; sort by title, source or pixel area. The grid loads results in pages of 60, with a button to load more. Clicking a file opens a viewer with the file, its file information, every provenance record and its other versions.

Deep links use the catalog asset ID: `archive.html#<asset id>` opens a file in the archive viewer and `index.html#<asset id>` opens that slide.

Open the pages straight from the folder (`file://`), or serve the checkout on this machine only:

```sh
python3 -I -m http.server 8765 --bind 127.0.0.1
```

and open <http://127.0.0.1:8765/archive.html>. The pages also work from a subdirectory such as `/dynamix/`. While browsing they load only local files: no remote fonts, no analytics, no audio and no other network requests. They carry `noindex,nofollow`, which asks search engines not to index them; it is not access control.

Both pages start in English. The language button in each page (labelled "中文" in English and "English" in Chinese) switches between English and Simplified Chinese without any network request. The choice is remembered in the browser (`localStorage`, key `dynamix-lang`) and carried in the URL as `?lang=zh-CN` (for example `archive.html?lang=zh-CN`), so it also survives `file://` browsing. Only interface text is translated: song titles, credited names and other strings quoted from sources are shown verbatim in both languages, and the English source and maintainer notes in the manifests are shown as written.

## What the archive contains

### Kinds and storage tiers

Each file has one kind. The kind decides whether the website serves the original file (`site` tier) or only a preview of it (`archive` tier):

| Tier | Kinds | In Git | Original served by the website |
| --- | --- | --- | --- |
| `site` | `song_art`, `pack_cover`, `character`, `background`, `part`, `promotional`, `process` | yes | yes |
| `archive` | `reference`, `gameplay_screenshot`, `store_screenshot`, and every PDF | yes | no: the viewer shows a preview (PDFs have none) and links the original in the GitHub repository |

`reference` covers informational graphics and documents. By the classification rule of 2026-10-10, any announcement graphic that shows chart difficulty levels is `reference`, whether it announces one song or several and whether it comes from a wiki or from an official X or Facebook post (a single song's chart list, "coming soon" or comeback teaser with level marks included), and so is any graphic whose main content is a list of songs or charts: event and comeback lineups, "upcoming events" and "comeback tracks" graphics, and pack or collaboration lineup cards that print several titled jackets. The jacket artwork shown inside such a graphic is archived separately, as the song's own `song_art` file. Update notes, press PDFs and similar documents are `reference` as well. `promotional` keeps graphics without chart-level information that concern a single song, pack or event: key art, event and anniversary posters, single collaboration key visuals, banners, logos and wordmarks. Store screenshots are app-store renditions, not artwork masters.

Archive-tier originals are stored in this repository like every other file; the website leaves them out only to stay within its size budget. Their catalog entries carry an `archive_url`, the GitHub raw URL of the file on `main` (`https://github.com/FridrichMethod/dynamix/raw/main/...`), and the viewer labels them "Original kept in the repository". Such a link works once the commit that holds the file is on `main` of the GitHub repository.

### What is excluded

Candidates that are not archived are still recorded in the discovery snapshots with a reason, among them:

- artwork used only by Dynamix Universe (`universe_only`) and files that are not Dynamix material (`not_dynamix`);
- animated GIF captures (`animated_capture`), which stay recorded with their metadata but are not downloaded by default; the larger still rendition, such as a character's `-Raw.png`, is a separate candidate and is archived when its own evidence accepts it, and `fetch_wikis.py --include-animated` downloads the animated files as well, so the exclusion can be reversed;
- photos of people (`person_photo`), wiki interface images (`site_ui`) and game interface elements such as icons, buttons and grade badges (`game_ui`);
- embedded video placeholders (`video_placeholder`), cover art of separate music releases (`external_release_art`), fanart (`fanart`), upscales (`upscaled`) and rejected submissions (`rejected_submission`);
- redirects and aliases of another file (`redirect_or_alias`) and other out-of-scope files (`out_of_scope_other`).

Paid or private material is never obtained, and logins, bot challenges and access controls are never bypassed: such sources are recorded as blocked or as needing manual access.

"Not archived" describes the current tree and catalog. Git history is never rewritten, so the bytes of records that a reviewed decision later withdrew or reclassified stay reachable in earlier commits, although no current record, catalog entry or page presents them: a wiki photo that turned out to be an upscale (now `upscaled`), wiki eyecatch images that are the cover art of a separate release (now `external_release_art`), App Store JPEG re-encodes that Apple's PNG renditions of the same uploads replaced, artists' personal illustrations whose records were withdrawn while they await review (now `pending`), and an official X interface strip (now `site_ui`). The commit messages that withdrew them name the commits that still hold the bytes; the withdrawal mechanisms are described in [docs/sources-wikis.md](docs/sources-wikis.md#duplicates-revisions-and-the-two-english-wikis) and [docs/sources-artists.md](docs/sources-artists.md).

### Files are kept as published

Nothing is upscaled, cropped, recompressed, colour-corrected, background-removed or watermark-removed. A file's extension comes from its decoded format, not from the URL. Different crops, revisions and versions of the same artwork are separate files; superseded and removed uploads stay archived and are marked as such. The only derived images are the WebP previews in `assets/thumbs/` (long edge 480 px), which are display copies listed in `data/thumbs.json`; the viewer, the download link and the repository link always point to the original.

A "site original" means the full-size file a source serves. It is not automatically the artist's master: wiki uploads, store renditions and platform re-encodes are labelled as what they are.

### Wiki copies: Miraheze and Fandom

The current English wiki on Miraheze imported its files from the legacy Fandom wiki under the same titles. Both are archived as separate sources with their own records. Fandom files are requested with `format=original`, because its CDN otherwise serves a transformed WebP. In the acquisition recorded in `data/sources/wikis.json`, those bytes were identical to the Miraheze file of the same title for every accepted title that both wikis hold except one, including the files for which Fandom's API records another SHA-1 and byte length for the upload; that difference is recorded, its cause is not established, and neither copy is described as the upload Fandom records. For the remaining title Fandom served other bytes than the upload both wikis record, so that Fandom record is marked as a platform transform (`transformed: true`) and kept as a separate file. Byte-identical files from several sources become one catalog entry that lists every source record, and the website stages a single copy of them. The details, and the generated number of catalog entries that two or more wiki sources hold, are in [docs/sources-wikis.md](docs/sources-wikis.md).

## Provenance model

`data/catalog.json` holds one entry per distinct SHA-256. Its `provenance` list keeps every source record of those bytes, and each record carries:

- **Where:** source, source page, requested and resolved download URLs, original filename, fetch time and upstream revision when known.
- **`source_reported`** (what the source advertised: URL, MIME type, byte length, dimensions, SHA-1, timestamp, revision) beside **`download_observed`** (what the saved bytes are: content type, sniffed format, byte length, decoded dimensions, SHA-1, SHA-256). **`comparisons`** states for SHA-1, byte length, dimensions and format separately whether they match (`true`), differ (`false`) or cannot be compared (`null`); there is no single "verified" flag. `transformed: true` marks bytes known to be a platform re-encode.
- **Quality labels:** `source_variant` (`site_original`, `preview`, `unknown`), `origin_status` (`artist_post`, `official`, `wiki_copy`, `repost`, `scan`, `unknown`) and `master_status` (`confirmed` or `unverified`).
- **Credits:** `illustrators` and `composers` are separate lists, copied verbatim; an empty list means unknown and is shown as "Unknown". `credit_evidence` quotes the field that states each name. A composer is never taken for the illustrator.
- **Mapping to songs:** `song_titles`, `song_ids` and `collections` with a `mapping_status` of `confirmed_primary` (the artist's or an official post names the song), `confirmed_wiki` (a wiki template, caption or list page names it), `inferred` (weaker evidence, needs review), `unmapped` or `conflict` (sources disagree). `mapping_evidence` holds the URLs and quoted fields.
- **Rights:** holder, creator, rights URL, license and restrictions. The holder defaults to "C4Cat Entertainment Limited and/or the credited artist"; `license` stays empty unless a license is actually established.
- **Upstream state:** `current`, `removed`, `superseded` (with `superseded_by` naming the newer upload when it was archived) or `unknown`; related crops share a `variant_group`.

**Primary record.** An entry's own fields (title, kind, file, quality labels) come from one of its records, chosen by a fixed rule: the family first (`artists`, then `official`, then `wikis`, then `archives`); within a family the source, where the current Miraheze wiki ranks before the legacy Fandom wiki because Miraheze stores the bytes that both English wikis serve, and other sources rank by ID; then the rendition (`site_original`, then `preview`, then `unknown`), the source ID and the asset ID. An artist's or official copy of a file therefore leads the entry even when a wiki holds the same bytes, and the other records stay in `provenance`.

**Titles.** A title is a readable name, never a bare file name. Wiki song art whose file maps to exactly one song with `confirmed_wiki` or stronger evidence carries that song's exact title; other wiki files use the wiki's own caption when there is one, otherwise the `File:` title without its extension and with underscores shown as spaces. Records of the other families are titled from the text of the post or page they cite. The upstream file name always stays in `original_filename`.

Song IDs come from `data/sources/song-index.json`, the dated song index built from the wikis. It also lists songs that are not part of the original game, such as Dynamix Universe songs, with `in_original_game: false`; only songs with `in_original_game: true` need positive evidence of being in the original game, and only they form the coverage denominator. Metric definitions are in [docs/coverage.md](docs/coverage.md).

## Collection statistics

The numbers between the markers below are generated by `scripts/update_docs_stats.py` from `data/coverage.json` and `data/catalog.json` after each build, are not written by hand, and report records, files, images and songs as separate quantities.

<!-- STATS:BEGIN -->
<!-- Generated by scripts/update_docs_stats.py from data/coverage.json and data/catalog.json; rebuild the data and rerun the script instead of editing this block. -->

Song index of 2026-10-10. Latest acquisition per family (manifest `fetched_at`): `wikis` 2026-10-11T02:42:56Z, `artists` 2026-10-10T21:00:40Z, `official` 2026-10-11T00:14:38Z, `archives` 2026-10-11T00:19:24Z.

| Measure | Value |
| --- | --- |
| Sources | 104 registered: 65 available, 5 partially available, 1 temporarily unavailable, 4 manual access, 15 metadata only, 14 excluded |
| Discovery snapshots | 93 complete, 11 incomplete, 0 without a snapshot |
| Candidates | 10,183: 7,129 downloaded, 32 duplicate bytes, 2,890 excluded, 7 failed, 35 blocked, 90 pending |
| Most frequent exclusion reasons | `person_photo` 885, `video_placeholder` 572, `universe_only` 387, `game_ui` 299, `animated_capture` 288 |
| Pending candidates (not decided yet) | 90 |
| Asset records (one per source and file) | 7,129 |
| Unique files (distinct SHA-256) | 3,769 |
| Exact duplicate records (asset records minus unique files) | 3,360 |
| Gallery images | 2,680 |
| Slideshow images | 715 |
| Songs in the original-game denominator | 537 |
| Songs with confirmed song art | 531 (98.9%) |
| Songs with a confirmed artist or official original | 18 (3.4%) |
| Songs with confirmed song art of long edge ≥ 1920 px | 54 (10.1%) |
| Songs without confirmed song art | 6: 6 inferred only, 0 conflict only, 0 without song art |
| Songs whose confirmed song art credits no illustrator | 256 (47.7%) |
| Artwork entries without a credited illustrator | 2,055 |
| Original bytes, site tier (the website serves the original) | 801,936,070 B (801.9 MB) |
| Original bytes, archive tier (original in the repository, preview on the website) | 541,525,196 B (541.5 MB) |
| Staged website estimate | 929,745,564 B (929.7 MB) of the 950,000,000 B budget (97.9%, within the budget) |

<!-- STATS:END -->

## Reproducible pipeline

### Environment

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# Optional, for the browser test only:
.venv/bin/python -m pip install -r requirements-dev.txt
```

`requirements.txt` pins Pillow 12.3.0, requests 2.34.2 and beautifulsoup4 4.15.0; `requirements-dev.txt` adds Playwright 1.62.0, which drives an installed Google Chrome (it downloads no browser). CI uses Python 3.14. The previews are encoded with Pillow 12.3.0 and libwebp 1.6.0, and `data/thumbs.json` records both versions; with the same versions a rebuild produces identical bytes. Every script runs in isolated mode (`python -I`).

### Acquisition (network)

Each source family has its own fetcher, which writes its manifest `data/sources/<family>.json`, its discovery snapshots `data/discovery/<source>.json` and its files under `assets/public/<family>/`:

```sh
.venv/bin/python -I scripts/fetch_wikis.py --metadata-only   # enumerate and classify, download nothing
.venv/bin/python -I scripts/fetch_wikis.py --resume          # download accepted candidates, re-hashing archived files
.venv/bin/python -I scripts/fetch_artists.py --resume
.venv/bin/python -I scripts/fetch_official.py --resume
.venv/bin/python -I scripts/fetch_archives.py --resume
```

All network access goes through one shared client: an explicit host allowlist checked again on every redirect, one request at a time per host with at least one second between requests, `Retry-After` honoured up to 60 s, at most three attempts for HTTP 429, 5xx and timeouts, no retry after 401, 403 or a bot challenge (recorded as blocked), a 64 MiB response limit, and the User-Agent `DynamixArtworkArchive/1.0 (+https://github.com/FridrichMethod/dynamix)`. These are the project's own defaults, not limits published by the sources. Downloads go to a temporary file, are decoded and checked, and only then moved into place, so an interrupted run never leaves a record that looks successful. Fetchers hold an exclusive lock and write manifests atomically, so a second run of the same family stops instead of competing, and `--resume` re-hashes existing files instead of trusting that they exist. Files that disappear or change upstream are not dropped: the earlier bytes stay archived, marked with their `upstream_status`. Per-source flags and limits are described in the source documents (see [Sources](#sources)).

`fetch_wikis.py` also proposes a song index, `data/discovery/song-index.wikis.json`; the maintainer reviews it and reconciles it into `data/sources/song-index.json`, keeping the disagreements between the wikis (see [docs/coverage.md](docs/coverage.md#metadata-disagreements)).

### Build and offline verification

```sh
.venv/bin/python -I scripts/fetch_wikis.py --verify
.venv/bin/python -I scripts/fetch_artists.py --verify
.venv/bin/python -I scripts/fetch_official.py --verify
.venv/bin/python -I scripts/fetch_archives.py --verify
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python -I scripts/build_catalog.py --verify
.venv/bin/python -I scripts/build_coverage.py
.venv/bin/python -I scripts/update_docs_stats.py
```

`--verify` re-hashes and decodes every archived file and reconciles each manifest with its discovery snapshots, entirely offline. `build_thumbnails.py` writes the previews (`--prune` also deletes previews it listed whose original left; it never touches originals). `build_catalog.py` writes `data/catalog.json`, `data/catalog.js` and `index.html`. `build_coverage.py` writes `data/coverage.json` and refuses to write when the inputs do not reconcile. `update_docs_stats.py` then rewrites the statistics blocks of this README, `docs/coverage.md` and `docs/sources-*.md` in both languages; it reads only `data/coverage.json` and `data/catalog.json`, refuses the two files when their shared totals, per-source counts, byte sums, source statuses or acquisition snapshots disagree (see [docs/coverage.md](docs/coverage.md)), and never changes text outside the markers. Nothing in these outputs depends on the clock, so a rebuild from the same inputs is byte-identical. To check that the committed outputs are current without writing anything:

```sh
.venv/bin/python -I scripts/build_thumbnails.py --check
.venv/bin/python -I scripts/build_catalog.py --verify --check
.venv/bin/python -I scripts/build_coverage.py --check
.venv/bin/python -I scripts/update_docs_stats.py --check
```

`scripts/build_thumbnails.py --verify` additionally re-encodes every preview and compares the bytes; it is slower and needs the pinned Pillow and libwebp.

### Staging the website

```sh
out="$(mktemp -d)"
.venv/bin/python -I scripts/prepare_pages.py --output "$out/dynamix" --summary "$out/summary.json" --manifest "$out/manifest.sha256"
(cd "$out/dynamix" && sha256sum --check --quiet --strict ../manifest.sha256)
```

`prepare_pages.py` copies into a new directory outside the checkout only Git-tracked public files: the page entry points and attribution files, `assets/site/`, `docs/` (without `docs/implementation/`), `licenses/`, `src/`, the generated catalog and coverage files, the site-tier originals named by the catalog (one copy per SHA-256) and the catalog's previews. Scripts, tests, workflows, templates, source manifests and discovery snapshots are never staged. It refuses symlinks and unsafe paths, files that differ from `HEAD` (unless `--allow-dirty`), any file over 100 MiB and a payload over 950,000,000 bytes, and it re-hashes every staged original against the catalog. If the payload would exceed the budget, `data/catalog.json` is left out first (the pages never load it: they read `data/catalog.js`, and their links to the full manifest name the repository copy), and the summary records it; only a payload that still does not fit is refused. If the site tier alone ever exceeds the budget, that is a reported blocker; it is never solved by recompressing or dropping originals. The allowlist limits what the website would publish; it is not a check for sensitive content, so review files before committing them.

## Tests and CI

```sh
.venv/bin/python -I tests/run_all.py
```

`tests/run_all.py` runs every `tests/test_*.py` module in its own isolated interpreter and fails on any failure, on a module that runs no tests, and when no module is found. The tests are offline: they use the fixtures under `tests/fixtures/` and never contact a source site.

The browser acceptance test drives an installed Google Chrome:

```sh
.venv/bin/python -I tests/browser_smoke.py --fixture --browser /usr/bin/google-chrome
.venv/bin/python -I tests/browser_smoke.py --base http://127.0.0.1:8765/ --root . --browser /usr/bin/google-chrome
```

`--fixture` builds sites from `tests/fixtures/ui/` and serves them itself (HTTP root, a `/dynamix/` subpath, `file://`, empty and hostile catalogs); `--base` checks a checkout that you serve on loopback as shown in [Browsing](#browsing). Either run fails on a console error, a failed request or any request that leaves the local server.

The [Tests workflow](.github/workflows/tests.yml) runs on every push and pull request with read-only permissions. Its offline job runs `tests/run_all.py`, `build_catalog.py --verify --check`, `build_thumbnails.py --check`, `build_coverage.py --check`, `update_docs_stats.py --check` (the generated statistics blocks of the documents are current), `fetch_<family>.py --verify` for every family manifest present, and `git diff --check`, then fails if any check changed the checkout. A browser job runs the fixture sites and the served checkout. A site job stages the website with `prepare_pages.py`, smoke-tests it under `/dynamix/` and uploads it as an ordinary CI artifact kept for seven days; this is not a Pages deployment. Actions are pinned to full commit SHAs.

## Source check (manual, read-only)

The [Check sources workflow](.github/workflows/check-sources.yml) runs only when started by hand from the Actions page; it has no schedule. It runs `scripts/check_sources.py`, which re-lists the Miraheze and Fandom file metadata (`list=allimages`, no downloads) and compares it with the committed discovery snapshots. The report lists added files, changed files (SHA-1, size, dimensions, MIME type or upload time) and removed files; a file counts as removed only when the live listing was read completely, otherwise it is reported as "not seen". The report and a JSON summary are uploaded as a run artifact. The run exits with status 3 when a live listing was incomplete, so a partial check is never mistaken for a clean one.

The workflow has a read-only token: it never commits, pushes, creates branches or pull requests, or changes data. Locally:

```sh
.venv/bin/python -I scripts/check_sources.py --report /tmp/dynamix-source-check.md --summary /tmp/dynamix-source-check.json
```

Acting on a report means running the fetchers locally, reviewing the diff and committing it like any other change.

## Release hold

Nothing has been released. The website is not deployed, no version tag exists and no GitHub Release has been published.

- The [Pages workflow](.github/workflows/pages.yml) never runs on push, pull request or a schedule. It runs only from a manual dispatch, and it deploys only when the dispatch targets `main`, its `release_sha` input is the full SHA of the dispatched commit, and the repository variable `RELEASE_APPROVED_SHA` names that same commit.
- `RELEASE_APPROVED_SHA` is unset, so every dispatch stops at the approval job: no tests, no artifact, no deployment.
- The Tests workflow prepares and checks the website on every push but only uploads a CI artifact; it cannot deploy.

A future release would require an explicit instruction from the repository owner, and then: a reviewed `main` commit whose CI passed and which also updates the Status line and this section in both READMEs (they are staged with the website, so a deployed copy must not say that it is not deployed); repository settings checked so that no branch-based Pages publishing exists and Pages uses GitHub Actions; `RELEASE_APPROVED_SHA` set to that commit's full SHA; and the Pages workflow dispatched on `main` with the same SHA as `release_sha`. That run would check the approval again, rerun every test on the same commit, stage the site with `prepare_pages.py` and deploy it. Tags and GitHub Releases are separate decisions that have not been made.

## Sources

| Family | Document | What it covers |
| --- | --- | --- |
| Wikis | [docs/sources-wikis.md](docs/sources-wikis.md) | The current English wiki on Miraheze, the legacy Fandom wiki, and two Japanese wikis read for song lists only. |
| Artists | [docs/sources-artists.md](docs/sources-artists.md) | Explicit posts and releases by credited illustrators and composers (X, pixiv, SoundCloud, Bandcamp, Bilibili, ArtStation, a portfolio site), plus research leads. |
| Official | [docs/sources-official.md](docs/sources-official.md) | App Store, Google Play and TapTap listings, the official X, SoundCloud, Facebook and YouTube accounts, C4Cat's corporate Facebook page, and the official and company websites. |
| Archives | [docs/sources-archives.md](docs/sources-archives.md) | Wayback Machine captures, a press screenshot gallery, press articles, an institutional press release, and historical research leads. |

Coverage metrics and their definitions: [docs/coverage.md](docs/coverage.md).

## Directory layout

```text
assets/public/{wikis,artists,official,archives}/<source>/  archived files, named <slug>--<sha256[:12]>.<ext>
assets/thumbs/                         derived WebP previews (display copies, not archive files)
assets/site/                           project-made icons (see assets/site/README.md)
data/sources/<family>.json             family manifests: sources, asset records, failures
data/sources/song-index.json           dated song index (in_original_game: true = coverage denominator)
data/discovery/<source>.json           discovery snapshots: every candidate and its disposition
data/catalog.json, data/catalog.js     deduplicated catalog with full provenance; offline browsing payload
data/thumbs.json, data/coverage.json   preview manifest; coverage report
scripts/                               fetchers, builders, source check and site staging; scripts/lib/ is shared
src/, templates/slideshow.html         page code and slideshow template
archive.html, index.html               the two pages (index.html is generated)
tests/                                 offline tests, fixtures and the browser test
docs/                                  source and coverage documents (docs/implementation/ is the planning packet)
licenses/                              third-party license texts and the license index
LICENSE, NOTICE                        software license and attribution
```

The public documents (this README, `docs/sources-*.md`, `docs/coverage.md` and `licenses/README.md`) each have a Simplified Chinese version with the suffix `.zh-CN.md`; `assets/site/README.md` includes a Chinese section.

## Licensing and attribution

The code is licensed under the Apache License 2.0 ([LICENSE](LICENSE)). The software license does not cover the artwork: Dynamix artwork and game assets belong to C4Cat Entertainment Limited and/or the credited artists, and archiving or publishing a file here does not change who owns it. No license is inferred for any artwork; a record names a license only when one has actually been established. Per-file rights, credits and source URLs are in each record's provenance in `data/catalog.json`.

[NOTICE](NOTICE) lists what is derived from the DEEMO artwork archive and from Mashiro's original gallery, and the third-party JavaScript bundled for the screenshot feature (html2canvas, punycode.js and Pace, all MIT-licensed). The [license index](licenses/README.md) maps every part of the repository to its license or rights entry point.
