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
  unreferenced files and symlinks under `assets/public/<family>/` reported, and errors for: a seed source without a
  source record (when checking this checkout or with `--seeds`), a source record without a valid snapshot at
  `data/discovery/<slug>.json`, a snapshot file named for one of the family's sources that holds no valid source ID
  (an unknown file name is a warning), a credit entry without a source URL, `primary_post` evidence that no artists
  candidate admitted for Dynamix (not excluded, not held) corroborates for the same file, and an archived candidate
  that a later run now *excludes* (kept for review; a now pending/failed one is a warning).
- `--resume` re-hashes archived files instead of downloading them again; `--metadata-only` never downloads and
  never changes archived records.
- Each acquisition holds an exclusive `fcntl.flock` (`--lock`; default: the family's `assets/public/<family>`
  directory under the root, so `TMPDIR` cannot give two writers different locks and no lock file is created); a second
  writer of the same family exits with status 3. Manifests and snapshots are validated and rewritten atomically after
  every resolved candidate, so a killed run loses at most the file in flight.
- Refresh bookkeeping: an archived candidate that every covering *complete* enumeration (`Discovery.coverage`: page
  links, store listings, a fully rendered SoundCloud `/tracks` list) no longer lists becomes `upstream_status: removed`
  (bytes kept, candidate leaves the snapshot); one the seeds no longer reach after a complete run becomes `unknown`
  (and `current` again when rediscovered); a failed or partial read changes no status. New bytes supersede the
  previous record, also when they revert to an earlier archived one. A refresh that fails keeps the previous candidate
  record unchanged. An archived file that no longer verifies is replaced atomically when a fresh download hashes to
  the record's SHA-256; otherwise the damage is reported as a `verify` discovery error and `--verify` fails.
  Current assets of one source that decode to identical pixels with different bytes (e.g. App Store renditions of two
  uploads of one image) name each other and share a `variant_group`. A re-download (a run without `--resume`) whose
  bytes differ from the candidate's archived file but decode to identical pixels keeps the archived record current
  and notes the re-fetched SHA-256 instead of archiving a copy and superseding. A file that a run lists under a new
  key with the bytes of a removed, superseded or unknown record of the same source is never represented only by that
  record: the record is adopted by the new candidate (`current`, note naming the first candidate) or, when its own
  candidate still names it, restored to `current`.
- One source's unexpected adapter error does not stop the run: its snapshot keeps the previous candidates, an open
  `adapter` continuation and the error, the remaining sources run, and the CLI exits 1. A page or API read that fails
  keeps its list open (`not-read:<error class>`), so `discovery_complete` is never inferred from an error.

## Seed rules (`model.py`)

A rule (`match` regex on the candidate key) may admit a candidate only with a `role`; `game` (only `dynamix` or
`mixed` on a download rule; any contract value on exclude/pending rules), song mapping and credits come from the rule.
`evidence_quotes` must occur in the text the adapter fetched (pixiv caption, post text, track description), otherwise
the candidate stays `pending` with game `unknown`; `confirmed_primary` needs such quotes, or, for a copy of a primary
post's file (Wayback), `primary_evidence` quoting that post, which `--verify` cross-checks, and every mapped song title
must be named by the fetched text or a `primary_evidence` quote (else the record says `inferred`). Each credited name
gets `credit_evidence` quoting the fetched field only where a credit label *of its own role* precedes it (`Artwork:`,
`Composed by`, `イラストは`, a joint `Music&Artwork`; a title `Artist - Song`, an uploader field, a `Movie:`/`BGA` line
or the other role's label is no evidence); otherwise the entry has no quote and cites the rule's `credit_source`
(e.g. the Miraheze song page, whose Template:Song `Artist`/`Cover Artist` must state the name verbatim), which every
download rule that credits names must have unless a curated `credit_evidence` entry with a URL covers each name.
List fields are lists of non-empty strings and `min_interval` is at least 1 s. Collaboration/multi-game art
(`game_associations` beyond `dynamix`) gets `rights.holder: null` unless the rule names a holder the source supports.
Curated `credit_evidence` and `mapping_evidence` entries are kept as given. Two guards override seeds: a *rejected submission* announcement excludes the candidate,
and a *Dynamix Universe* mention holds it as `pending` unless the rule sets `allow_universe_mention`. Unclassified
items are `pending`, never admitted on keywords; X embed-timeline posts need a rule whose `match` names the post ID
(or that requires evidence quotes). `variant_role` requires `variant_group` (`jacket:<song slug>` groups crops of one
song's artwork). An exclude/pending rule with `"withdraw_archived": true` is a curator's decision to remove an earlier
archived record of that candidate (file and record; the bytes stay in Git history); without it an archived candidate
that a rule now excludes stays archived and fails `--verify`.

## Adapters

| Adapter | Access | Notes |
| --- | --- | --- |
| `x_post` | `cdn.syndication.twimg.com/tweet-result` (public embed), `pbs.twimg.com …name=orig`; optional `syndication.twitter.com` embed timeline | one candidate per image (`<post>:<n>`); `media_url_https` and `original_info` stay `source_reported`; timeline is a recent sample (list stays open); X re-encodes uploads |
| `pixiv_artwork` | `www.pixiv.net/ajax/illust/<id>` (+ `/pages`); optional per-page Wayback captures | `i.pximg.net` refuses requests without a pixiv Referer, which is never forged: one attempt, `blocked`, host skipped for the run; withheld URLs → `blocked` without a request. A page with a seed `wayback` capture keeps the post as `page_url` and downloads `web.archive.org/web/<ts>id_/<original>` after one exact CDX check (no 200 capture → `pending`; CDX digest → `source_reported.sha1`; capture time → `upstream_revision`); the original must be the URL pixiv lists for that page |
| `youtube_video` | `www.youtube.com/oembed`, `i.ytimg.com/vi/<id>/{maxres,sd,hq}default.jpg`; channel page for channel art | oEmbed channel must equal `params.channel_url` (else `pending`); largest generated thumbnail with 404 fallback, `transformed`, oEmbed's advertised `hqdefault` stays `source_reported.url`; channel banner/avatar only when its image ID is on the channel page (`=s0`) |
| `facebook_post` | explicit `www.facebook.com/<page>/...` permalinks read logged out | `og:image` (signed `*.fbcdn.net` rendition, `transformed`) with `og:title`/`og:description` as evidence; `og:url` must stay on `params.page`; no Open Graph image → `blocked` (login wall), refusals/challenges → `blocked`; the timeline is never enumerated |
| `release_page` | SoundCloud `__sc_hydration` / Bandcamp JSON-LD | cover upgraded to `-original` / `_0` (the advertised `artwork_url`/`og:image` stays `source_reported`); uploader permalink checked |
| `bilibili_opus`, `artstation_project` | public JSON | risk-control codes / 403 / challenges → `blocked` (manual access) |
| `apple_store` | product page `serialized-server-data` shelves; iTunes `lookup` per storefront | native size, Apple's lossless PNG rendition of PNG uploads (the advertised JPEG variant stays in `source_reported`), `transformed: true`; per-record quality note names the stored rendition and Apple's per-render identifier (see below) |
| `google_play` | listing pages per locale | `=s0` native size; the advertised `src` rendition is `source_reported`, `=wW-hH` sizes kept as notes, size decoded from bytes |
| `taptap_app` | `__NUXT_DATA__` devalue payload | `original_url`; MD5 of the file compared with the name token (a mismatch is a re-encode: `transformed: true`, byte-exact twin named when archived); crop/resize banner rendition excluded (`upscaled` only when larger than its crop), its URL kept as the upload's `source_reported.url` (size in the notes: another rendition is not comparable); trailer poster frames are `video_placeholder` exclusions |
| `wayback_cdx` | CDX API with `showResumeKey` paging (bounded: repeated key or `max_pages`), `id_` raw captures | earliest capture per distinct payload; CDX digest → `source_reported.sha1` |
| `page_links`, `direct_files` | explicit pages / explicit file URLs | link patterns per page; JS galleries named file by file |
| `release_page` (list mode) | `{"tracks_of": "<handle>"}`: one read of `soundcloud.com/<handle>/tracks` | every server-rendered track becomes an item; the list stays open when fewer than the account's `track_count` are rendered |
| `page_probe`, `lead_registry` | one availability read / no request | dead or login-walled sources; research leads without an acquirable file |

`seeds/leads-crosswalk.json` (research sweep) and `seeds/leads-gapfill-crosswalk.json` (research gap-fill: Wayback pixiv
originals, official YouTube/Facebook/X, press, partner collaborations) map every 2026-10-10 lead (`dynamix_original_game`
yes/unclear) to the
source and candidate-key prefix that records its disposition; `lead_list` holds the research file's SHA-256 and a hash
of the lead list, so the seed test detects a dropped lead. `testing.py` holds the offline test doubles used by
`tests/test_fetch_{artists,official,archives}.py`.

### App Store renditions (observed 2026-10-10)

Every render of an `is*-ssl.mzstatic.com/image/thumb/...` rendition writes a new 26-character token into the file
(EXIF UserComment; XMP `dc:creator` for PNG). The CDN caches one render: repeated requests served from that cache
entry (`x-cache: HIT`, same `Last-Modified`) return identical bytes from any `is*-ssl` host, which makes a URL look
byte-stable, but a cache miss returns a fresh render with a new token. Evidence for
`official:apple-store:lookup:4fdc7a95-0870-d09f-5953-18ebec3be534` (archived SHA-256 `387179b0…`, token `GAVIV…`,
served from a cache entry with `Last-Modified: 12:31:40 GMT`): one request to `is1-ssl` returned token `4Z4JML…`
(SHA-256 `f98bd636…`); two requests with a cache-busting query (`x-cache: MISS`, `Last-Modified` = request time)
returned tokens `AIJM5…` and `PPQTQ…`; all decode to the same 1024x768 pixels. So SHA-256 verifies an archived file,
and a re-fetch is compared with it by decoded pixels (the runner does this before archiving new bytes).
