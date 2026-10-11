# Wiki sources

**English** · [简体中文](sources-wikis.zh-CN.md)

This document describes the `wikis` source family: how `scripts/fetch_wikis.py` reads the Dynamix community wikis, how it decides which files belong to the original game and what kind they are, and where its records are kept. Results are in `data/sources/wikis.json` (sources, asset records, failures) and in one discovery snapshot per source, `data/discovery/<source>.json`, which lists every candidate with its disposition and the evidence behind it.

## Sources

| Source ID | Site | Used for | Platform |
| --- | --- | --- | --- |
| `wikis:miraheze` | [Dynamix Wiki on Miraheze](https://dynamix.miraheze.org/wiki/Main_Page) | Files and song metadata; the current English community wiki | MediaWiki |
| `wikis:fandom` | [Dynamix Wiki on Fandom](https://dynamixc4cat.fandom.com/wiki/Main_Page) | Files and song metadata; the legacy English wiki | MediaWiki |
| `wikis:atwiki-ja` | [Dynamix wiki on atwiki](https://w.atwiki.jp/dynamix2ch/) (Japanese) | Song lists of the original game only; no files | atwiki |
| `wikis:rhythmgame-ja` | [Rhythm game wiki: Dynamix](https://wikiwiki.jp/rhythmgame/Dynamix) (Japanese, wikiwiki.jp) | One song table, as corroboration only; no files | wikiwiki |

The Fandom wiki of this game is `dynamixc4cat.fandom.com`; `dynamix.fandom.com` belongs to unrelated games and is not used. The Miraheze wiki is where the English community moved in 2026; most of its files were imported from Fandom by a maintenance script on 2026-08-09, so their upload timestamps on Miraheze are import times, not original upload dates.

## Access

### MediaWiki APIs (Miraheze and Fandom)

Both English wikis are read through their public MediaWiki APIs (`https://dynamix.miraheze.org/w/api.php` and `https://dynamixc4cat.fandom.com/api.php`), without logging in:

- `list=allimages` for every file of the wiki with its current URL, size, MIME type, SHA-1 and upload time;
- `generator=allimages` with `prop=fileusage|categories` for the pages that embed each file and the file's own categories;
- `generator=allpages` (main namespace, no redirects) for the current wikitext, revision IDs and categories of every article;
- `prop=imageinfo&iiprop=metadata` for the frame count of GIF files;
- `action=expandtemplates` for song titles that are built from templates.

Every list is paged until the API returns no continuation. A snapshot is `discovery_complete: true` only when every list was followed to its end; otherwise it keeps the open continuation tokens and the errors, and its unread part is never treated as "no files". Raw API responses are not committed; with `--cache-dir` they can be reused to recover one interrupted discovery.

Miraheze serves files unmodified from `static.wikitide.net`; for sampled files the SHA-1 the API reports equals the served bytes.

### Fandom's CDN and `format=original`

Fandom's CDN (`static.wikia.nocookie.net`) answers `/revision/latest` URLs with a transformed WebP by default, even when the path ends in `.png`. Every Fandom download therefore appends `format=original`, which returns the file Fandom's CDN stores instead of that WebP; whether that file is the upload is checked against the API values, not assumed. The saved file is named after the format decoded from its bytes, never after the URL suffix.

For many Fandom files, the byte length and SHA-1 that the Fandom API records for the upload differ from the bytes `format=original` returns. Only this much was measured: in the acquisition recorded in `data/sources/wikis.json`, the bytes Fandom returned were byte-identical to the Miraheze copy of the same title for every accepted title that both wikis hold except one, and those Miraheze copies match the SHA-1 that Miraheze's API reports. For these titles neither copy therefore has the byte length and SHA-1 that Fandom's API records for the upload; why they differ is not established, and neither copy is described as that upload. The records say the same: the API values stay in `source_reported`, the measured values in `download_observed`, `comparisons` marks the differing fields `false`, the Fandom record's quality note says that its bytes are not the upload Fandom's API records, and the Miraheze record's `notes` say that its copy is not byte-identical to that upload. The difference is not treated as a second version of the file. How many catalog entries two or more wiki sources hold is generated under [Counts](#counts).

The remaining title is `File:Dangerous_Insanity_Comeback_Chart_List.jpg`. There Fandom's API and Miraheze's API report the same SHA-1 and size for the upload, but Fandom's CDN served other bytes, so those bytes are known to be a platform transform of the stored upload: the Fandom record carries `transformed: true` and a quality note that says so, and it is kept as a separate file beside the Miraheze copy. A Fandom file whose API values differ from Miraheze's as well is not marked as a transform, because the cause is not established.

### Japanese song lists

The two Japanese wikis are read as HTML pages, one request per registered page, and parsed as untrusted HTML. Only tables whose header names a title column and a credit column are read; rows are kept verbatim. The atwiki pages are its rank/share unlock list, song pack list, event list, collaboration list and removed-song list; the wikiwiki page is its single Dynamix song table. Attachments are not enumerated, so these sources contribute song-list evidence but no files, and their snapshots stay `discovery_complete: false`. A page that fails or is blocked is recorded as an error, never as an empty list.

## Classification

The wiki file namespaces contain much more than artwork: profile photos, interface icons, gameplay captures, announcement graphics, videos and Dynamix Universe material. A file's total count in a wiki is therefore neither an artwork count nor a song count.

### Kind

The kind of a file comes from where and how the wikis use it. Evidence is pooled by file title across Miraheze and Fandom, because Miraheze imported Fandom's files under the same titles, and every decision keeps the evidence strings that name the page, the context and the quoted caption or field. The strongest evidence decides:

1. the cover field `Imgsuffix` of a song template, or the "Package …" image of an original-game package page;
2. a gallery or link caption, a reviewed list page or section (for example the Characters and Parts pages), or a file category;
3. an image linked from an original-game list, an image in a song page's Trivia or Notes section, or the documented size heuristic on interface pages;
4. a filename convention (`…Dynamix.png`, `…BG-HQ.png`, `…-Raw.png` and similar), used only to fill a role that page usage leaves open. Suffixes such as `-HQ` or `-Raw` say nothing about whether the image is a full illustration.

A file named only by its filename, or with a tie between roles named by equally many pages, stays `pending` with a concrete reason instead of being guessed. A large image is not artwork just because it is large: `(XD)_D.jpg` measures 2048×1152 px, but its page places it among gameplay notes, so it is a gameplay screenshot.

Graphics that inform rather than illustrate are `reference`, by the classification rule of 2026-10-10:

- any announcement graphic that shows chart difficulty levels, whether for one song or several, including a single song's "Chart List", "COMING SOON" or "i'm back!" teaser with level marks;
- any graphic whose main content is a list of songs or charts: the `*_Chart_List*`, `*_Comeback*` and `*_New_Charts*` graphics and their equivalents, event and comeback lineups, "Upcoming Events" and "Comeback Tracks" graphics, and pack or collaboration lineup cards that print several titled jackets;
- update notes and similar informational graphics.

The official X and Facebook posts of the same designs follow the same rule (see [sources-official.md](sources-official.md)). The jacket artwork shown inside such a graphic is archived separately, as the song's own `song_art` file. `promotional` keeps graphics without chart-level information that concern a single song, pack or event: key art, event and anniversary posters, single collaboration key visuals, banners, logos and wordmarks. Gameplay captures are `gameplay_screenshot`. `reference` and `gameplay_screenshot` files are archived but only previewed on the website (archive tier).

### Titles

A record's `title` is a readable name, never a bare file name. Song art whose candidate maps to exactly one song with `confirmed_wiki` or stronger evidence is titled with that song's exact title. Other files take the wiki's own caption when there is one, otherwise the `File:` title without its extension and with underscores shown as spaces. The upstream file name stays verbatim in `original_filename`.

### Game

The game of a file is the union of the games of the contexts that use it: `Template:Song` marks the original game, `Template:SongUV` marks Dynamix Universe, and pages and categories named for one game count for that game. A page about both games, an artist list or the main page establishes no game by itself, and a filename never establishes a game. On a song page with both games' templates, each game's material sits in its own tab, and the page-level gallery after the tabs lists original-game event items. A file whose role comes from a song page and whose Fandom upload predates the wiki's first Universe-named upload counts as original-game material, and the evidence string says so. Covers of unreleased or cancelled songs (`Template:SongOther`) stay pending because their in-game use is not established.

A file used by both games is accepted as `mixed` and keeps `dynamix_universe` in its `game_associations`; a caption that names artwork for another game or event ("Artwork in …") records that game as an association as well. Only files used solely in Universe contexts are excluded.

### Exclusions and pending candidates

Every candidate has exactly one disposition: `downloaded`, `duplicate_bytes`, `excluded`, `failed`, `blocked` or `pending`. Exclusion reasons used for the wikis include `universe_only`, `animated_capture`, `person_photo` (composer, artist and charter profile images), `site_ui` (wiki skins, logos, navigation templates), `game_ui` (icons, buttons, grade badges, store bits; a whole game screen that a page about a game feature shows is a `gameplay_screenshot` once an archivist review saw the whole screen, as for the code-entry tutorial screenshots), `external_release_art` (covers of separate music releases), `video_placeholder` (`video/*` entries, which are embedded videos rather than files) and `out_of_scope_other`.

Animated GIF captures are excluded by default: they stay recorded as candidates with their metadata but are not downloaded. The larger still rendition, such as a character's `-Raw.png`, is a separate candidate and is archived when its own evidence accepts it. `--include-animated` accepts and downloads the animated files as well, so the exclusion can be reversed. Pending candidates carry `awaiting_download`, `unresolved_role` or `unresolved_game` as their reason, and the coverage report lists them.

## Duplicates, revisions and the two English wikis

Each wiki keeps its own records at its own paths (`assets/public/wikis/miraheze/…` and `assets/public/wikis/fandom/…`). When both wikis serve identical bytes, Git stores a single blob, and the catalog merges the two records into one entry whose provenance lists both sources; the website stages only one copy. The Miraheze record is that entry's primary record, because Miraheze is the current wiki and stores the bytes that both wikis serve, unless an artist or official record holds the same bytes (see [Primary record](../README.md#provenance-model)). Within one wiki, a second file title with the same bytes becomes `duplicate_bytes` and points at the existing record instead of writing another file. Files whose bytes differ are always kept as separate files, even when they look alike.

A re-upload keeps the file title and therefore the candidate. The new bytes become the current record, and the older bytes stay archived with `upstream_status: superseded` and `superseded_by` naming the newer record when it was archived. A file that disappears from a wiki keeps its archived record as `removed`. If the current evidence no longer accepts a file that is already archived and no reviewed withdrawal covers it, the fetcher writes nothing, lists the file for review and exits with status 4.

A reviewed withdrawal is the only way an archived wiki file leaves the archive. It is a curated entry under `withdrawals` in `scripts/wikis/reviews.json` that names the `File:` title, the SHA-256 of the archived bytes, the exclusion reason and the archivist's evidence. When the current evidence excludes that file with the reviewed reason, an acquiring run deletes the archived record and its file; a `--metadata-only` run only reports the withdrawal (`WITHDRAWAL PENDING`) and keeps the file, its record and its classification. The candidate stays recorded as `excluded` with its reason. The withdrawn bytes are no longer in the tree or the catalog, but they remain in the Git history (see [What is excluded](../README.md#what-is-excluded)); withdrawals carried out so far removed a wiki upscale (`upscaled`) and eyecatch images that are the artwork of a separate release (`external_release_art`), on both wikis.

## Song index

`fetch_wikis.py` proposes `data/discovery/song-index.wikis.json`, which the maintainer reconciles into `data/sources/song-index.json`; its songs with `in_original_game: true` are the coverage denominator, and it also lists Universe songs and removed songs with `in_original_game: false`. Songs are grouped by English page title, and the exact title is the original `Template:Song` `Title` (Miraheze first); other spellings become aliases. In `Template:Song`, `Artist` (or `Compound Artist Link`) is the composer and `Cover Artist` (or `Cover Art`) is the illustrator. They are stored separately and verbatim, placeholders such as `N/A` mean unknown, an explicitly labelled illustrator note inside a composer field is removed from the composer credit, and names are never split on `&`, `,` or `x`. An illustrator is attached only to the song's own cover file, never to every image on its page.

A song counts as part of the original game (`in_original_game: true`) only with positive evidence: an original `Template:Song` on a page that is not in `Category:Unreleased`, or a row of the atwiki original-game lists. The wikiwiki table only corroborates; it never adds a song on its own. Pages with only `Template:SongUV` are Universe songs, and `Template:SongOther` pages are unreleased songs; both stay out of the denominator. The atwiki removed-song list ("removed or never implemented") marks a song `removed` but does not put it in the denominator by itself; such a song counts only when an original song page also establishes it. Where the wikis disagree, the proposal records a disagreement instead of choosing silently (see [Metadata disagreements](coverage.md#metadata-disagreements)).

## Evidence levels and access limitations

The English wikis are read through their live APIs (`live_api`); the Japanese wikis are page reads (`page_read`). On 2026-10-10 the metadata-only discovery of both English wikis followed every continuation to its end; whether a later snapshot is complete is recorded in its `discovery_complete` field and in the coverage report, not assumed. No wiki required a login, and none is accessed past an error: a 401, 403 or bot challenge is recorded as `blocked` and not retried. Download failures stay in the manifest's `failures` with their phase, error class and attempt count.

## Counts

The numbers between the markers below are generated by `scripts/update_docs_stats.py` from `data/coverage.json` (`sources.records` of the `wikis` family) and `data/catalog.json` after each build; they are not written by hand.

<!-- STATS:BEGIN -->
<!-- Generated by scripts/update_docs_stats.py from data/coverage.json and data/catalog.json; rebuild the data and rerun the script instead of editing this block. -->

Family `wikis`: 4 sources; latest acquisition (manifest `fetched_at`) 2026-10-11T04:27:27Z; 9,393 candidates; 6,701 asset records, 2,226,855,913 B (2,226.9 MB) in all; 3,354 catalog entries whose primary record comes from this family, 1,103,602,296 B (1,103.6 MB).

| Source | Status | Discovery | Candidates | `downloaded` | `duplicate_bytes` | `excluded` | `failed` | `blocked` | `pending` | Asset records | Catalog entries (primary) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `wikis:atwiki-ja` | `metadata_only` | incomplete | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `wikis:fandom` | `available` | complete | 4,924 | 3,339 | 13 | 1,546 | 1 | 0 | 25 | 3,339 | 2 |
| `wikis:miraheze` | `available` | complete | 4,469 | 3,362 | 13 | 1,067 | 0 | 0 | 27 | 3,362 | 3,352 |
| `wikis:rhythmgame-ja` | `metadata_only` | incomplete | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Catalog entries held by two or more sources of this family (the same bytes: one entry with several source records): 3,337.

Exclusion reasons in this family: `person_photo` 875, `video_placeholder` 569, `universe_only` 379, `animated_capture` 288, `game_ui` 278, `site_ui` 137, `out_of_scope_other` 39, `external_release_art` 22, `not_dynamix` 22, `redirect_or_alias` 2, `upscaled` 2.

<!-- STATS:END -->

## Rights

Wiki uploads are copies of game artwork, labelled `origin_status: wiki_copy` and `master_status: unverified`: a wiki file is not the artist's master, even when it is the largest copy found. Rights stay with C4Cat Entertainment Limited and/or the credited artist; when the file is also tied to another game than Dynamix Universe (its `game_associations`, for example RAVON), the record leaves `rights.holder` empty, as the other families do, because no cited source names that art's holder. The record's `rights.creator` names the credited illustrator when the song template states one, and `rights.license` stays empty because no license is established. A wiki's content license does not establish a license for the game artwork it hosts.

## Commands

```sh
.venv/bin/python -I scripts/fetch_wikis.py --metadata-only            # enumerate and classify; no downloads
.venv/bin/python -I scripts/fetch_wikis.py --resume                   # download accepted candidates; re-hash archived files
.venv/bin/python -I scripts/fetch_wikis.py --resume --source miraheze --limit 100
.venv/bin/python -I scripts/fetch_wikis.py --resume --only "File:CosmicPulseDynamix.png"
.venv/bin/python -I scripts/fetch_wikis.py --verify                   # offline
```

Every run enumerates both English wikis, because classification pools their evidence; `--source` limits which source's snapshot is written and which source is downloaded. A run without `--resume` checks that archived files are present with the recorded size; `--resume` re-hashes them. `--limit` caps the download attempts of one run and `--only` restricts downloads to named candidates. The fetcher takes an exclusive lock on the root, `<root>/.cache/fetch_wikis/root.lock`, and one more in its work directory (`--lock-file`, by default `<root>/.cache/fetch_wikis/work/lock`; `--work-dir` moves the work directory). Both are inside the checkout but ignored by Git, and the work directory must be private to the current user. The fetcher journals every download before the next request and rewrites the manifest and snapshots atomically every few minutes, so an interrupted run loses at most the file in flight and the next run replays the journal. `--verify` runs offline: it validates the manifest and every snapshot, reconciles dispositions, re-hashes and decodes every file, reports files without a record, and checks the song index proposal.

Exit status: 0 done; 1 some downloads failed or were blocked, or a damaged archived file could not be repaired; 2 discovery incomplete; 3 another run holds a lock, or the work directory, a lock file, the journal or a wiki asset directory cannot be used safely (another owner, a symlink, a journal of another root or of an interrupted run in another work directory); 4 an archived file is no longer accepted and no reviewed withdrawal covers it (nothing written, review needed); 5 internal error (an unexpected exception; outputs are written only up to the last completed write); 130 interrupted (outputs written up to the last completed download). A file host that answers 401, 403 or a bot challenge is not contacted again in the same run: its remaining files are recorded as `blocked` without a request and are tried again by the next run.
