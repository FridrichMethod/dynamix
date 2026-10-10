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
| `official:x` | [Dynamix official X (@c4c4cat)](https://x.com/c4c4cat) | Explicit posts through the public embed endpoint; the embed timeline when it answers | Account ID 3166512194. X re-encodes uploads (`name=orig` is X's largest copy). The full history requires a login, so the source is `partially_available` and its snapshot keeps the unread lists open. |
| `official:soundcloud-c4cat` | [C4Cat official SoundCloud](https://soundcloud.com/c4cat) | Every track that the `/tracks` page renders | SoundCloud server-renders only part of the list, so the list stays open (`partially_available`). Tracks without their own artwork (only the uploader avatar) are excluded; uploads whose use in Dynamix is not established stay pending. |
| `official:facebook` | [Dynamix official Facebook page](https://www.facebook.com/c4cat.dynamix/) | Explicit photo permalinks only | Without a login, permalinks expose only Open Graph text, and the timeline cannot be enumerated. `manual_access`; no image is recorded from it. |
| `official:website` | [Dynamix official website](https://dynamix.c4-cat.com/) | One availability probe | `temporarily_unavailable`: on 2026-10-10 the host name did not resolve. Historical captures of the site are archived under `archives:wayback` (see [sources-archives.md](sources-archives.md)). |
| `official:c4cat` | [C4Cat company site](https://c4-cat.com/) | One page read | A coming-soon/contact placeholder without a game catalog; identity evidence only (`metadata_only`). |
| `official:official-leads` | Research leads on official channels | No request | Leads from the research sweep without an acquirable file (for example a discontinued store listing, a merchandise shop, an APK mirror, trailer thumbnails), each with its own decision. |

## Classification

Store and TapTap screenshots are `store_screenshot`; banners, key art and announcement visuals from official accounts are `promotional`; an official release cover on C4Cat's SoundCloud is `song_art`. Store app icons are excluded as `game_ui` (branding, not artwork), trailer thumbnails as `video_placeholder`, and platform crops or upscales as `upscaled`. Each candidate is classified by an explicit seed rule, as in the artist family: nothing is admitted on keywords alone, and a Dynamix Universe mention keeps a candidate pending unless the rule allows it.

The same screenshot often appears in several stores. Byte-identical files become one catalog entry with every store record in its provenance; files that differ by re-encoding stay separate and say which one is the byte-exact upload.

## Evidence levels and access limitations

The store pages and SoundCloud are `page_read`; the X embed endpoint is `live_api`; the official website, whose probe failed, is `not_established`; the leads registry is `search_index`. Logins, bot challenges and access controls are never bypassed: Facebook and the full X history stay `manual_access` or partial, and their snapshots keep open continuations with a reason, so they are never counted as complete. A failed probe of the official website is recorded with its error class; it does not mean the site's content never existed.

## Counts

The numbers between the markers below are generated from `data/coverage.json` (`sources.records` of the `official` family) when the catalog is built; they are not written by hand.

<!-- STATS:BEGIN -->
<!-- STATS:END -->

## Rights

Official files are labelled `origin_status: official` and `master_status: unverified`; store renditions and platform re-encodes are described as such in `quality.notes`. The rights holder of store screenshots and official account images is recorded as C4Cat Entertainment Limited. No license is established for any of these files, so `rights.license` stays empty; a store listing's public availability is not a license.

## Commands

```sh
.venv/bin/python -I scripts/fetch_official.py --metadata-only     # discover and record candidates; no downloads
.venv/bin/python -I scripts/fetch_official.py --resume            # acquire; re-hash archived files instead of downloading them again
.venv/bin/python -I scripts/fetch_official.py --resume --source official:apple-store
.venv/bin/python -I scripts/fetch_official.py --verify            # offline
```

The options, locking, atomic writes, error handling and exit status are the same as for `fetch_artists.py` (see [sources-artists.md](sources-artists.md#commands)). The lock defaults to the family's `assets/public/official/` directory.
