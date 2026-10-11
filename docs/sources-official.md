# Official sources

**English** · [简体中文](sources-official.zh-CN.md)

This document describes the `official` source family: the original game's store listings, C4Cat's official accounts and the official websites. `scripts/fetch_official.py` writes the results to `data/sources/official.json` and one discovery snapshot per source, `data/discovery/<source>.json`. Only the original game's identifiers are used; the sequel's listings (App Store ID `6741094831`, Google Play package `com.c4cat.dynamix2`) are excluded.

Store screenshots are app-store renditions of gameplay and menus, not artwork. They are kind `store_screenshot`, which belongs to the archive tier: they are archived in the repository, and the website shows only their previews and links the original.

## Sources

| Source ID | Source | How it is read | Notes |
| --- | --- | --- | --- |
| `official:apple-store` | [App Store, Dynamix (ID 945955095)](https://apps.apple.com/us/app/dynamix/id945955095) | The product page's `serialized-server-data` screenshot shelves, plus the iTunes `lookup` API per storefront for older storefront-specific sets | Screenshots are requested at their native size. For PNG uploads, Apple's lossless PNG rendition is requested instead of the JPEG the page loads; the advertised JPEG variant stays in `source_reported`. These are store re-renders, marked `transformed: true`. |
| `official:google-play` | [Google Play, Dynamix (`com.c4cat.dynamix`)](https://play.google.com/store/apps/details?id=com.c4cat.dynamix) | The listing page in three locales | Files are requested with `=s0` (native size). The size in the page's `=wW-hH` URL is kept as a note only; dimensions come from the decoded bytes. The store's app icon is excluded as `game_ui`. |
| `official:taptap` | [TapTap, Dynamix (7082)](https://www.taptap.cn/app/7082) | The page's `__NUXT_DATA__` payload; files at their `original_url` | A secondary listing that names C4Cat as developer. Each file's MD5 is compared with the MD5 token in its upload name: a mismatch means TapTap re-encoded the upload, which is recorded as `transformed: true` with the byte-exact twin named when it is archived from another store. A cropped and resized banner rendition is excluded as `upscaled`; the banner upload itself is archived. Visible update text on the page is historical. |
| `official:x` | [Dynamix official X (@c4c4cat)](https://x.com/c4c4cat) | Explicit posts through the public embed endpoint, including posts whose IDs the research recovered from archived status pages; the embed timeline when it answers | Account ID 3166512194. X re-encodes uploads (`name=orig` is X's largest copy). The full history requires a login and the remaining archived status IDs are not processed, so the source is `partially_available` and its snapshot keeps those lists open. |
| `official:soundcloud-c4cat` | [C4Cat official SoundCloud](https://soundcloud.com/c4cat) | Every track that the `/tracks` page renders | SoundCloud server-renders only part of the list, so the list stays open (`partially_available`). Tracks without their own artwork (only the uploader avatar) are excluded; uploads whose use in Dynamix is not established stay pending. |
| `official:facebook` | [Dynamix official Facebook page](https://www.facebook.com/c4cat.dynamix/) | Explicit post permalinks only, read logged out | The timeline is not served without a login, so the source is `partially_available`. A permalink that serves Open Graph data yields its `og:image`, with `og:title` and `og:description` as evidence; that image is Facebook's re-encoded and resized rendition (`preview`, `transformed: true`), not the upload. A permalink that redirects to the login page is `blocked`. |
| `official:facebook-c4cats` | [C4Cat corporate Facebook page](https://www.facebook.com/c4cats/) | One page read, logged out | Only the profile picture is served without a login and its game identity is not established, so it stays pending; the page's other photos need manual access (`metadata_only`). |
| `official:youtube` | [C4 Cat YouTube channel (@C4CatEntertainment)](https://www.youtube.com/@C4CatEntertainment) | oEmbed for each explicitly seeded video; the channel page for its banner | The video list comes from the research enumeration of the channel and is not read again, so the source is `partially_available`. Only Dynamix-related videos are seeded (including Universe updates, which are excluded, and videos whose game is not resolved, which stay pending), and oEmbed must name the official channel. Each video yields its largest YouTube-generated thumbnail (`maxresdefault`, else `sddefault`, else `hqdefault`), a JPEG rendition of the video (`preview`, `transformed: true`) rather than an uploaded image. |
| `official:website` | [Dynamix official website](https://dynamix.c4-cat.com/) | One availability probe | `temporarily_unavailable`: on 2026-10-10 the host name did not resolve. Historical captures of the site are archived under `archives:wayback` (see [sources-archives.md](sources-archives.md)). |
| `official:c4cat` | [C4Cat company site](https://c4-cat.com/) | One page read | A coming-soon/contact placeholder without a game catalog; identity evidence only (`metadata_only`). |
| `official:official-leads` | Research leads on official channels | No request | Leads from the research sweep without an acquirable file (for example a discontinued store listing, a merchandise shop, a community server that needs an account, social accounts behind a login or that do not exist), each with its own decision. |

## Classification

Store and TapTap screenshots are `store_screenshot`. Images of the official X, Facebook and YouTube accounts follow the rule that the wikis use (see [sources-wikis.md](sources-wikis.md#kind)): an announcement graphic that shows chart difficulty levels, for one song or several, and a graphic whose main content is a list of songs or charts (comeback-track and event lineups, comeback previews, pack and collaboration lineup cards that print several titled jackets) are `reference`; key art, event and anniversary posters, single collaboration key visuals, banners, logos and wordmarks are `promotional`; a post image or video thumbnail that shows gameplay is `gameplay_screenshot`. An official release cover on C4Cat's SoundCloud is `song_art`. Store app icons are excluded as `game_ui` (branding, not artwork), TapTap trailer poster frames as `video_placeholder`, platform crops or upscales as `upscaled`, thumbnails that are photos of people as `person_photo` and Dynamix Universe material as `universe_only`. Each candidate is classified by an explicit seed rule, as in the artist family: nothing is admitted on keywords alone, and a Dynamix Universe mention keeps a candidate pending unless the rule allows it.

The same screenshot often appears in several stores. Byte-identical files become one catalog entry with every store record in its provenance; files that differ by re-encoding stay separate and say which one is the byte-exact upload.

Records are titled from the text they cite: an X post's text, a Facebook post's `og:title`, a YouTube video's title or a SoundCloud track's title; store screenshots are named after their listing and position. The file name stays in `original_filename`.

## Evidence levels and access limitations

The store pages, SoundCloud and the Facebook pages are `page_read`; the X embed endpoint and YouTube's oEmbed are `live_api`; the official website, whose probe failed, is `not_established`; the leads registry is `search_index`. Logins, bot challenges and access controls are never bypassed: the Facebook timelines, the full X history and the YouTube channel's video list are not enumerated, so their snapshots keep open continuations with a reason and are never counted as complete. A failed probe of the official website is recorded with its error class; it does not mean the site's content never existed.

## Counts

The numbers between the markers below are generated by `scripts/update_docs_stats.py` from `data/coverage.json` (`sources.records` of the `official` family) and `data/catalog.json` after each build; they are not written by hand.

<!-- STATS:BEGIN -->
<!-- Generated by scripts/update_docs_stats.py from data/coverage.json and data/catalog.json; rebuild the data and rerun the script instead of editing this block. -->

Family `official`: 11 sources; latest acquisition (manifest `fetched_at`) 2026-10-11T00:14:38Z; 297 candidates; 243 asset records, 160,675,095 B (160.7 MB) in all; 243 catalog entries whose primary record comes from this family, 160,675,095 B (160.7 MB).

| Source | Status | Discovery | Candidates | `downloaded` | `duplicate_bytes` | `excluded` | `failed` | `blocked` | `pending` | Asset records | Catalog entries (primary) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `official:apple-store` | `available` | complete | 34 | 34 | 0 | 0 | 0 | 0 | 0 | 34 | 34 |
| `official:c4cat` | `metadata_only` | complete | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `official:facebook` | `partially_available` | incomplete | 37 | 22 | 1 | 3 | 0 | 11 | 0 | 22 | 22 |
| `official:facebook-c4cats` | `metadata_only` | incomplete | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| `official:google-play` | `available` | complete | 14 | 13 | 0 | 1 | 0 | 0 | 0 | 13 | 13 |
| `official:official-leads` | `metadata_only` | complete | 16 | 0 | 0 | 7 | 0 | 5 | 4 | 0 | 0 |
| `official:soundcloud-c4cat` | `partially_available` | incomplete | 7 | 1 | 0 | 6 | 0 | 0 | 0 | 1 | 1 |
| `official:taptap` | `available` | complete | 13 | 10 | 0 | 3 | 0 | 0 | 0 | 10 | 10 |
| `official:website` | `temporarily_unavailable` | incomplete | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `official:x` | `partially_available` | incomplete | 145 | 139 | 4 | 2 | 0 | 0 | 0 | 139 | 139 |
| `official:youtube` | `partially_available` | incomplete | 30 | 24 | 1 | 4 | 0 | 0 | 1 | 24 | 24 |

Catalog entries held by two or more sources of this family (the same bytes: one entry with several source records): 0.

Exclusion reasons in this family: `out_of_scope_other` 9, `not_dynamix` 5, `game_ui` 3, `person_photo` 2, `site_ui` 2, `video_placeholder` 2, `fanart` 1, `universe_only` 1, `upscaled` 1.

<!-- STATS:END -->

## Rights

Official files are labelled `origin_status: official` and `master_status: unverified`. Store renditions and platform re-encodes (Apple's and TapTap's renders, X's re-encodes, Facebook's Open Graph renditions, YouTube's generated thumbnails) are described as such in `quality.notes`, and the Facebook and YouTube renditions are labelled `preview`. The rights holder of store screenshots and official account images is recorded as C4Cat Entertainment Limited; collaboration artwork that involves another game keeps the holder empty unless its source names one. No license is established for any of these files, so `rights.license` stays empty; a store listing's public availability is not a license.

## Commands

```sh
.venv/bin/python -I scripts/fetch_official.py --metadata-only     # discover and record candidates; no downloads
.venv/bin/python -I scripts/fetch_official.py --resume            # acquire; re-hash archived files instead of downloading them again
.venv/bin/python -I scripts/fetch_official.py --resume --source official:apple-store
.venv/bin/python -I scripts/fetch_official.py --verify            # offline
```

The options, locking, atomic writes, error handling and exit status are the same as for `fetch_artists.py` (see [sources-artists.md](sources-artists.md#commands)). The lock is always held on the family's `assets/public/official/` directory; `--lock` adds a second one.
