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
| `archive` | `reference`, `gameplay_screenshot`, `store_screenshot`, and every PDF | yes | no: the viewer shows a preview and links the original in the GitHub repository |

`reference` covers informational graphics and documents: chart/difficulty lists, comeback/new-chart and update announcement graphics, press PDFs and similar material. `promotional` keeps key art, banners, event, anniversary and collaboration visuals and teasers that are not chart listings. Store screenshots are app-store renditions, not artwork masters.

Archive-tier originals are stored in this repository like every other file; the website leaves them out only to stay within its size budget. Their catalog entries carry an `archive_url`, the GitHub raw URL of the file on `main` (`https://github.com/FridrichMethod/dynamix/raw/main/...`), and the viewer labels them "Original kept in the repository". Such a link works once the commit that holds the file is on `main` of the GitHub repository.

### What is excluded

Candidates that are not archived are still recorded in the discovery snapshots with a reason, among them:

- artwork used only by Dynamix Universe (`universe_only`) and files that are not Dynamix material (`not_dynamix`);
- animated GIF captures (`animated_capture`); the still `-Raw.png` rendition of a character is archived when the wiki has one, and `fetch_wikis.py --include-animated` makes this choice reversible;
- photos of people (`person_photo`), wiki interface images (`site_ui`) and game interface elements such as icons, buttons and grade badges (`game_ui`);
- embedded video placeholders (`video_placeholder`), cover art of separate music releases (`external_release_art`), fanart (`fanart`), upscales (`upscaled`) and rejected submissions (`rejected_submission`);
- redirects and aliases of another file (`redirect_or_alias`) and other out-of-scope files (`out_of_scope_other`).

Paid or private material is never obtained, and logins, bot challenges and access controls are never bypassed: such sources are recorded as blocked or as needing manual access.

### Files are kept as published

Nothing is upscaled, cropped, recompressed, colour-corrected, background-removed or watermark-removed. A file's extension comes from its decoded format, not from the URL. Different crops, revisions and versions of the same artwork are separate files; superseded and removed uploads stay archived and are marked as such. The only derived images are the WebP previews in `assets/thumbs/` (long edge 480 px), which are display copies listed in `data/thumbs.json`; the viewer, the download link and the repository link always point to the original.

A "site original" means the full-size file a source serves. It is not automatically the artist's master: wiki uploads, store renditions and platform re-encodes are labelled as what they are.

### Wiki copies: Miraheze and Fandom

The current English wiki on Miraheze imported its files from the legacy Fandom wiki under the same titles. Both are archived as separate sources with their own records. Fandom files are requested with `format=original`, because its CDN otherwise serves a transformed WebP; for sampled files those bytes were identical to the Miraheze file. Byte-identical files from several sources become one catalog entry that lists every source record, and the website stages a single copy of them. The details are in [docs/sources-wikis.md](docs/sources-wikis.md).

## Provenance model

`data/catalog.json` holds one entry per distinct SHA-256. Its `provenance` list keeps every source record of those bytes, and each record carries:

- **Where:** source, source page, requested and resolved download URLs, original filename, fetch time and upstream revision when known.
- **`source_reported`** (what the source advertised: URL, MIME type, byte length, dimensions, SHA-1, timestamp, revision) beside **`download_observed`** (what the saved bytes are: content type, sniffed format, byte length, decoded dimensions, SHA-1, SHA-256). **`comparisons`** states for SHA-1, byte length, dimensions and format separately whether they match (`true`), differ (`false`) or cannot be compared (`null`); there is no single "verified" flag. `transformed: true` marks bytes known to be a platform re-encode.
- **Quality labels:** `source_variant` (`site_original`, `preview`, `unknown`), `origin_status` (`artist_post`, `official`, `wiki_copy`, `repost`, `scan`, `unknown`) and `master_status` (`confirmed` or `unverified`).
- **Credits:** `illustrators` and `composers` are separate lists, copied verbatim; an empty list means unknown and is shown as "Unknown". `credit_evidence` quotes the field that states each name. A composer is never taken for the illustrator.
- **Mapping to songs:** `song_titles`, `song_ids` and `collections` with a `mapping_status` of `confirmed_primary` (the artist's or an official post names the song), `confirmed_wiki` (a wiki template, caption or list page names it), `inferred` (weaker evidence, needs review), `unmapped` or `conflict` (sources disagree). `mapping_evidence` holds the URLs and quoted fields.
- **Rights:** holder, creator, rights URL, license and restrictions. The holder defaults to "C4Cat Entertainment Limited and/or the credited artist"; `license` stays empty unless a license is actually established.
- **Upstream state:** `current`, `removed`, `superseded` (with `superseded_by` naming the newer upload when it was archived) or `unknown`; related crops share a `variant_group`.

Song IDs come from `data/sources/song-index.json`, the dated original-game song list. A song enters it only with positive evidence of being in the original game; metric definitions are in [docs/coverage.md](docs/coverage.md).

## Collection statistics

The numbers between the markers below are generated from `data/coverage.json` when the catalog is built; they are not written by hand. Record, file, image and song counts are different quantities and are reported separately.

<!-- STATS:BEGIN -->
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
```

`--verify` re-hashes and decodes every archived file and reconciles each manifest with its discovery snapshots, entirely offline. `build_thumbnails.py` writes the previews (`--prune` also deletes previews it listed whose original left; it never touches originals). `build_catalog.py` writes `data/catalog.json`, `data/catalog.js` and `index.html`. `build_coverage.py` writes `data/coverage.json` and refuses to write when the inputs do not reconcile. Nothing in these outputs depends on the clock, so a rebuild from the same inputs is byte-identical. To check that the committed outputs are current without writing anything:

```sh
.venv/bin/python -I scripts/build_thumbnails.py --check
.venv/bin/python -I scripts/build_catalog.py --verify --check
.venv/bin/python -I scripts/build_coverage.py --check
```

`scripts/build_thumbnails.py --verify` additionally re-encodes every preview and compares the bytes; it is slower and needs the pinned Pillow and libwebp.

### Staging the website

```sh
out="$(mktemp -d)"
.venv/bin/python -I scripts/prepare_pages.py --output "$out/dynamix" --summary "$out/summary.json" --manifest "$out/manifest.sha256"
(cd "$out/dynamix" && sha256sum --check --quiet --strict ../manifest.sha256)
```

`prepare_pages.py` copies into a new directory outside the checkout only Git-tracked public files: the page entry points and attribution files, `assets/site/`, `docs/` (without `docs/implementation/`), `licenses/`, `src/`, the generated catalog and coverage files, the site-tier originals named by the catalog (one copy per SHA-256) and the catalog's previews. Scripts, tests, workflows, templates, source manifests and discovery snapshots are never staged. It refuses symlinks and unsafe paths, files that differ from `HEAD` (unless `--allow-dirty`), any file over 100 MiB and a payload over 950,000,000 bytes, and it re-hashes every staged original against the catalog. If the site tier alone ever exceeds the budget, that is a reported blocker; it is never solved by recompressing or dropping originals. The allowlist limits what the website would publish; it is not a check for sensitive content, so review files before committing them.

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

The [Tests workflow](.github/workflows/tests.yml) runs on every push and pull request with read-only permissions. Its offline job runs `tests/run_all.py`, `build_catalog.py --verify --check`, `build_thumbnails.py --check`, `build_coverage.py --check`, `fetch_<family>.py --verify` for every family manifest present, and `git diff --check`, then fails if any check changed the checkout. A browser job runs the fixture sites and the served checkout. A site job stages the website with `prepare_pages.py`, smoke-tests it under `/dynamix/` and uploads it as an ordinary CI artifact kept for seven days; this is not a Pages deployment. Actions are pinned to full commit SHAs.

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

A future release would require an explicit instruction from the repository owner, and then: a reviewed `main` commit whose CI passed; repository settings checked so that no branch-based Pages publishing exists and Pages uses GitHub Actions; `RELEASE_APPROVED_SHA` set to that commit's full SHA; and the Pages workflow dispatched on `main` with the same SHA as `release_sha`. That run would check the approval again, rerun every test on the same commit, stage the site with `prepare_pages.py` and deploy it. Tags and GitHub Releases are separate decisions that have not been made.

## Sources

| Family | Document | What it covers |
| --- | --- | --- |
| Wikis | [docs/sources-wikis.md](docs/sources-wikis.md) | The current English wiki on Miraheze, the legacy Fandom wiki, and two Japanese wikis read for song lists only. |
| Artists | [docs/sources-artists.md](docs/sources-artists.md) | Explicit posts and releases by credited illustrators and composers (X, pixiv, SoundCloud, Bandcamp, Bilibili, ArtStation, a portfolio site), plus research leads. |
| Official | [docs/sources-official.md](docs/sources-official.md) | App Store, Google Play and TapTap listings, the official X, SoundCloud and Facebook accounts, and the official and company websites. |
| Archives | [docs/sources-archives.md](docs/sources-archives.md) | Wayback Machine captures, a press screenshot gallery, an institutional press release, and historical research leads. |

Coverage metrics and their definitions: [docs/coverage.md](docs/coverage.md).

## Directory layout

```text
assets/public/{wikis,artists,official,archives}/<source>/  archived files, named <slug>--<sha256[:12]>.<ext>
assets/thumbs/                         derived WebP previews (display copies, not archive files)
assets/site/                           project-made icons (see assets/site/README.md)
data/sources/<family>.json             family manifests: sources, asset records, failures
data/sources/song-index.json           dated original-game song index (coverage denominator)
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

Every Markdown document has a Simplified Chinese version with the suffix `.zh-CN.md`.

## Licensing and attribution

The code is licensed under the Apache License 2.0 ([LICENSE](LICENSE)). The software license does not cover the artwork: Dynamix artwork and game assets belong to C4Cat Entertainment Limited and/or the credited artists, and archiving or publishing a file here does not change who owns it. No license is inferred for any artwork; a record names a license only when one has actually been established. Per-file rights, credits and source URLs are in each record's provenance in `data/catalog.json`.

[NOTICE](NOTICE) lists what is derived from the DEEMO artwork archive and from Mashiro's original gallery, and the third-party JavaScript bundled for the screenshot feature (html2canvas, punycode.js and Pace, all MIT-licensed). The [license index](licenses/README.md) maps every part of the repository to its license or rights entry point.
