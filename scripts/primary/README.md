# `scripts/primary` — artists, official and archives acquisition

`fetch_artists.py`, `fetch_official.py` and `fetch_archives.py` share one runner (`runner.py`) and narrow
per-platform adapters (`adapters/`). What to fetch is data: `seeds/<family>.json` lists every source, its adapter,
explicit seeds (post IDs, page URLs, CDX queries) and curated rules that classify each expected candidate. Nothing
crawls a whole platform.

```text
python -I scripts/fetch_<family>.py [--metadata-only] [--resume] [--verify] [--root PATH]
                                     [--source ID ...] [--seeds FILE] [--lock FILE] [-v]
```

- `--verify` is offline: manifest and snapshot validation, reconciliation, every file re-hashed and decoded,
  unreferenced files and symlinks under `assets/public/<family>/` reported, `primary_post` evidence cross-checked
  against the artists snapshots (warning).
- `--resume` re-hashes archived files instead of downloading them again; `--metadata-only` never downloads and
  never changes archived records.
- Each acquisition holds an exclusive `fcntl.flock` (`--lock`, default per root and family in the system temp
  directory); a second writer of the same family exits with status 3. Manifests and snapshots are validated and
  rewritten atomically after every resolved candidate, so a killed run loses at most the file in flight.

## Seed rules (`model.py`)

A rule (`match` regex on the candidate key) may admit a candidate only with a `role`; `game`, song mapping and credits
come from the rule. `evidence_quotes` must occur in the text the adapter fetched (pixiv caption, post text, track
description), otherwise the candidate stays `pending`; `confirmed_primary` needs such quotes, or, for a copy of a
primary post's file (Wayback), `primary_evidence` quoting that post, which `--verify` cross-checks. Two guards
override seeds: a *rejected submission* announcement excludes the candidate, and a *Dynamix Universe* mention holds it
as `pending` unless the rule sets `allow_universe_mention`. Unclassified items are `pending`, never admitted on
keywords. `variant_role` requires `variant_group` (`jacket:<song slug>` groups crops of one song's artwork).

## Adapters

| Adapter | Access | Notes |
| --- | --- | --- |
| `x_post` | `cdn.syndication.twimg.com/tweet-result` (public embed), `pbs.twimg.com …name=orig`; optional `syndication.twitter.com` embed timeline | one candidate per image (`<post>:<n>`); timeline is a recent sample (list stays open); X re-encodes uploads |
| `pixiv_artwork` | `www.pixiv.net/ajax/illust/<id>` (+ `/pages`) | `i.pximg.net` refuses requests without a pixiv Referer, which is never forged: one attempt, `blocked`, host skipped for the run; withheld URLs → `blocked` without a request |
| `release_page` | SoundCloud `__sc_hydration` / Bandcamp JSON-LD | cover upgraded to `-original` / `_0`; uploader permalink checked |
| `bilibili_opus`, `artstation_project` | public JSON | risk-control codes / 403 / challenges → `blocked` (manual access) |
| `apple_store` | product page `serialized-server-data` shelves; iTunes `lookup` per storefront | native size, Apple JPEG rendition, `transformed: true` |
| `google_play` | listing pages per locale | `=s0` native size; advertised `=wW-hH` kept as notes, size decoded from bytes |
| `taptap_app` | `__NUXT_DATA__` devalue payload | `original_url`; MD5 of the file compared with the name token; crop/upscale banner rendition excluded |
| `wayback_cdx` | CDX API with `showResumeKey` paging, `id_` raw captures | earliest capture per distinct payload; CDX digest → `source_reported.sha1` |
| `page_links`, `direct_files` | explicit pages / explicit file URLs | link patterns per page; JS galleries named file by file |
| `page_probe`, `lead_registry` | one availability read / no request | dead or login-walled sources; research leads without an acquirable file |

`seeds/leads-crosswalk.json` maps every 2026-10-10 research-sweep lead (`dynamix_original_game` yes/unclear) to the
source and candidate-key prefix that records its disposition. `testing.py` holds the offline test doubles used by
`tests/test_fetch_{artists,official,archives}.py`.
