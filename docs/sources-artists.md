# Artist sources

**English** · [简体中文](sources-artists.zh-CN.md)

This document describes the `artists` source family: public posts and release pages by the illustrators and composers credited for original-game songs. `scripts/fetch_artists.py` writes the results to `data/sources/artists.json` and one discovery snapshot per source, `data/discovery/<platform>-<handle>.json`. Artist originals have priority over wiki copies: when an artist's file has exactly the same bytes as a wiki copy, the catalog chooses the artist's record as the entry's primary record and keeps the wiki record in its provenance.

## Scope

Nothing here crawls a whole platform. What to fetch is data: `scripts/primary/seeds/artists.json` lists every source with its adapter, explicit seeds (post IDs, page URLs, track pages) and curated rules for each expected candidate. The seeds come from the research sweep of 2026-10-10, whose leads are all accounted for (see the leads crosswalk below). Each source ID has the form `artists:<platform>:<handle>`, for example `artists:x:ariiol_`.

A source can be an illustrator (posts of the jacket artwork) or a composer (release pages whose cover may or may not be the game's jacket). Both are recorded, but they are never confused: a composer's release cover is not the illustrator's original, and a cover whose picture differs from the game jacket is excluded as `external_release_art`.

## Platforms and access

| Platform | How it is read | What is recorded | Limits |
| --- | --- | --- | --- |
| X | The public embed endpoint `cdn.syndication.twimg.com/tweet-result` for explicit posts; images from `pbs.twimg.com` with `name=orig` | One candidate per attached image (`<post>:<n>`) | X re-encodes uploads, so `name=orig` is the platform's largest copy, not the artist's file. Full account histories need a login and are not read. |
| pixiv | The public JSON `www.pixiv.net/ajax/illust/<id>` (and `/pages` for multi-page works) | One candidate per page of a work | The image host `i.pximg.net` refuses requests that do not carry a pixiv Referer. The Referer is never forged: after one refusal the host is not contacted again in that run, and the candidates are `blocked`. Works whose file URLs pixiv withholds from logged-out readers are `blocked` without a request. When a seed names a Wayback Machine capture of the original that pixiv lists for a page, the record keeps the pixiv post as its `page_url` and downloads the raw capture (`web.archive.org/web/<timestamp>id_/<original>`) after one exact CDX check; the CDX digest becomes `source_reported.sha1`, and a response from another capture or with other bytes stays pending. ArchiveTeam captures of such originals are also listed under `archives:wayback` (see [sources-archives.md](sources-archives.md)). |
| SoundCloud | The track page's embedded `__sc_hydration` data; the cover is requested in its `-original` rendition | Explicit track pages, or every track a `/tracks` page renders (`tracks_of` list mode) | SoundCloud renders only part of a long track list; such a list stays open (`discovery_complete: false`). The platform may re-encode uploads. |
| Bandcamp | The page's JSON-LD; the cover is requested in its `_0` (largest stored) rendition | Explicit track or album pages | A release cover, never assumed to be the game jacket. |
| Bilibili | Public JSON of explicit posts | Explicit posts | Anonymous clients receive risk-control codes; these are recorded as `blocked` (challenge) and need manual access. |
| ArtStation | Public project JSON | Explicit projects | Requests received a 403 challenge page; recorded as `blocked`, never bypassed. |
| Portfolio sites | Explicit file URLs of a JavaScript-rendered gallery (`direct_files`) | Named files only | Files are taken without resize suffixes; candidates without evidence stay pending. |
| Research leads | No request (`lead_registry`) | `artists:research:leads`: credit and identity evidence, login walls, bot checks and held items from the research sweep, each with its own URL and decision | Recorded so that every lead has a disposition; none is an artwork file. |

`scripts/primary/seeds/leads-crosswalk.json` maps every lead of the research sweep to the source and candidate key that records its disposition, so a dropped lead fails the seed tests.

## Seeds and classification rules

A curated rule (a regular expression on the candidate key) can admit a candidate only with a kind. The game (`dynamix` or `mixed`), song mapping and credits come from the rule, and the rule's evidence quotes must occur in the text the adapter actually fetched (the pixiv caption, the post text, the track title or description). If they do not, the candidate stays `pending` with game `unknown`. Unclassified items are `pending`; nothing is admitted on keywords alone.

Two guards override the seeds. A page that announces a **rejected submission** excludes the candidate (`rejected_submission`), even though it names Dynamix. A **Dynamix Universe** mention holds the candidate as `pending` unless the rule explicitly allows it. Crops of one song's artwork share a `variant_group` (`jacket:<song slug>`) and each states its `variant_role`, so a square jacket, a wide banner and a close-up stay distinct files.

An exclude or pending rule that sets `withdraw_archived` is an explicit curator decision to remove an earlier archived record of that candidate (the bytes stay in Git history). Without it, an archived candidate that a rule now excludes stays archived and fails `--verify`, so a rule change cannot silently drop a file.

## Credits and song mapping

Records are titled from the post or release page they cite (a pixiv work's title with its page number, an X post's text, a track or album title); the file name stays in `original_filename`.

`confirmed_primary` requires the fetched page itself to name the song (a quoted field in `mapping_evidence`); every mapped song title must be named by the fetched text, otherwise the record is `inferred`. Each credited name has a `credit_evidence` entry quoting the field that states it, or, when the page does not state it, an entry pointing to the research or wiki page the rule cites; an entry citing a wiki song page carries the `Template:Song` field and quote that the wikis family records for that page and name. Illustrators and composers stay in separate lists, and a joint credit is stored as the source writes it (`Keiyuu & RED-FOX`, `Linne X SCTL`), never split into separate names, as in the wikis family. A post's grouping, a multi-page work or an album title is not taken as the song of every image in it.

## Exclusions

Excluded artist candidates are recorded with their reason, for example `rejected_submission` (a submission the artist or label says was not used), `external_release_art` (a release cover that differs from the game jacket), `fanart`, `game_ui` (an app icon or a song-select composite) and `out_of_scope_other` (a post without an image, a gift illustration tagged Dynamix, credit-only evidence without an artwork file, an account matched only by name). Paid or private material is never obtained.

## Evidence levels and access limitations

Sources read through a public JSON endpoint are `live_api`; sources read from a page are `page_read`; a source whose only reading was refused is `blocked`; the research-lead registry is `search_index`. A source's `status` says what was possible: `available`, `partially_available` (for example a track list that renders only partly), `manual_access` (login, Referer check or bot challenge), `metadata_only` (identity or credit evidence without an acquirable file) or `excluded` (every candidate excluded, such as a negative control). A 401, 403 or challenge is recorded with error class `blocked` or `challenge` and is not retried; nothing is bypassed. The absence of a file in this family does not mean that the artist has not published one: platforms such as pixiv, Instagram and X were only partly readable without a login.

## Counts

The numbers between the markers below are generated by `scripts/update_docs_stats.py` from `data/coverage.json` (`sources.records` of the `artists` family) and `data/catalog.json` after each build; they are not written by hand.

<!-- STATS:BEGIN -->
<!-- Generated by scripts/update_docs_stats.py from data/coverage.json and data/catalog.json; rebuild the data and rerun the script instead of editing this block. -->

Family `artists`: 84 sources; latest acquisition (manifest `fetched_at`) 2026-10-10T21:00:40Z; 217 candidates; 76 asset records, 53,895,082 B (53.9 MB) in all; 76 catalog entries whose primary record comes from this family, 53,895,082 B (53.9 MB).

| Source | Status | Discovery | Candidates | `downloaded` | `duplicate_bytes` | `excluded` | `failed` | `blocked` | `pending` | Asset records | Catalog entries (primary) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `artists:artstation:sandgodoven` | `manual_access` | incomplete | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| `artists:bandcamp:craftuner` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:bandcamp:ha1uday0` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:bandcamp:lixion` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:bandcamp:vauboy` | `metadata_only` | complete | 2 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 |
| `artists:bandcamp:veirtunes` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:bilibili:ariiol` | `manual_access` | incomplete | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| `artists:pixiv:1652099` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:pixiv:17081698` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:1840049` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:19451821` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:2624455` | `manual_access` | complete | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| `artists:pixiv:3315403` | `metadata_only` | incomplete | 5 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 |
| `artists:pixiv:3381542` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:407585` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:4349073` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:5401047` | `available` | complete | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 3 |
| `artists:pixiv:548320` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:74278` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:760598` | `available` | complete | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:779504` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:pixiv:89112381` | `manual_access` | complete | 3 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 |
| `artists:research:leads` | `metadata_only` | complete | 81 | 0 | 0 | 56 | 0 | 9 | 16 | 0 | 0 |
| `artists:soundcloud:3t1a` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:aboutlogic` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:airlemonex` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:aoisoundsystem` | `available` | complete | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:arforest` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:soundcloud:calixer` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:soundcloud:cashewnuts` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:circle2` | `available` | complete | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 3 |
| `artists:soundcloud:cordantia` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:soundcloud:cosmogrph` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:dice-dd` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:ebico` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:espidev` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:felix1213tw` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:halvsan` | `available` | complete | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |
| `artists:soundcloud:hino-isuka` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:ianmirror` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:soundcloud:jehezukiel` | `available` | complete | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |
| `artists:soundcloud:jun_kuroda` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:soundcloud:kanon092` | `available` | complete | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |
| `artists:soundcloud:keshiks` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:kforest` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:kurorak` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:kuske-kuonrecords` | `available` | complete | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 3 |
| `artists:soundcloud:linne_ennil` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:misomyl` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:missionary_music` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:myukke` | `available` | complete | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |
| `artists:soundcloud:nordysantrophy` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:nuunu5` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:omocha1019` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:panbanana0729` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:r_solidor` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:re94c` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:riranofficial` | `available` | complete | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:ryokmbk` | `metadata_only` | complete | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| `artists:soundcloud:sctl_shin` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:se-u-ra` | `available` | complete | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |
| `artists:soundcloud:soundperemotion` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:street` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:trialsuperbeats` | `available` | complete | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 3 |
| `artists:soundcloud:vauboy` | `partially_available` | complete | 2 | 1 | 0 | 0 | 0 | 0 | 1 | 1 | 1 |
| `artists:soundcloud:whos-raphiiel` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:soundcloud:woof-owo` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:wix:lalaluhat` | `metadata_only` | complete | 4 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 |
| `artists:x:ariiol_` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:x:bluenoup` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:x:flan_0207` | `available` | complete | 4 | 1 | 0 | 3 | 0 | 0 | 0 | 1 | 1 |
| `artists:x:hyou_haku` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:x:hyourinakamoto2` | `excluded` | complete | 3 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 |
| `artists:x:kumeki_3` | `available` | complete | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 1 | 1 |
| `artists:x:kuskekplecraft` | `available` | complete | 7 | 5 | 0 | 2 | 0 | 0 | 0 | 5 | 5 |
| `artists:x:lalaluhat` | `metadata_only` | complete | 5 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 |
| `artists:x:matagi_14` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:x:meganegirai` | `available` | complete | 3 | 1 | 0 | 2 | 0 | 0 | 0 | 1 | 1 |
| `artists:x:r_solidor` | `available` | complete | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `artists:x:serolerr` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `artists:x:seura_nue` | `available` | complete | 4 | 2 | 0 | 2 | 0 | 0 | 0 | 2 | 2 |
| `artists:x:sojyiqui` | `metadata_only` | complete | 3 | 0 | 0 | 2 | 0 | 0 | 1 | 0 | 0 |
| `artists:x:violpasn` | `metadata_only` | complete | 4 | 0 | 0 | 3 | 0 | 0 | 1 | 0 | 0 |
| `artists:x:zatsuki_ctake` | `excluded` | complete | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |

Catalog entries held by two or more sources of this family (the same bytes: one entry with several source records): 0.

Exclusion reasons in this family: `out_of_scope_other` 65, `external_release_art` 18, `game_ui` 3, `fanart` 2, `rejected_submission` 2, `video_placeholder` 1.

<!-- STATS:END -->

## Rights

An artist's post is labelled `origin_status: artist_post`; a composer's release cover is labelled `repost`, because the composer is not its illustrator. `master_status` stays `unverified` unless a source establishes the master, and platform renditions (X's re-encode, SoundCloud's `-original`, Bandcamp's `_0`) are described as such in `quality.notes`. The rights holder defaults to "C4Cat Entertainment Limited and/or the credited artist"; a record names another holder only when its evidence does, and `rights.creator` names the credited illustrator when known. No license is established for any of these works, so `rights.license` stays empty. A public post is not a license to reuse the artwork.

## Commands

```sh
.venv/bin/python -I scripts/fetch_artists.py --metadata-only     # discover and record candidates; no downloads
.venv/bin/python -I scripts/fetch_artists.py --resume            # acquire; re-hash archived files instead of downloading them again
.venv/bin/python -I scripts/fetch_artists.py --resume --source artists:x:ariiol_
.venv/bin/python -I scripts/fetch_artists.py --verify            # offline
```

`--source` (repeatable) limits a run to the named sources and leaves the other records as they are; `--seeds` reads another seed file and `-v` logs every request decision. `--metadata-only` never downloads and never changes archived records. Each run holds an exclusive lock on the family's `assets/public/artists/` directory (`--lock` adds a second lock, it never replaces this one), and a second acquisition of the same family exits with status 3. The manifest and snapshots are validated and rewritten atomically after every resolved candidate, so a killed run loses at most the file in flight. An unexpected adapter error in one source keeps that source's previous candidates, records the error and an open `adapter` continuation, lets the other sources run, and makes the run exit with status 1. A run that leaves candidates `failed` or `blocked` also exits with status 1, like `fetch_wikis.py` with failed or blocked downloads; a page that cannot be read keeps its list open, so a failed read never looks like a complete one. `--verify` runs offline: it validates the manifest and snapshots, reconciles dispositions, re-hashes and decodes every file, reports unreferenced files and symlinks under `assets/public/artists/`, and checks that every seed source has a record.
