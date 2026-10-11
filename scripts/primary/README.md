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
  candidate admitted for Dynamix (not excluded, not held) corroborates for the same file, an archived candidate
  that a later run now *excludes* (kept for review; a now pending/failed one is a warning), and a downloaded or
  `duplicate_bytes` candidate whose role differs from the kind of the record of its bytes (one file, one kind within
  a source), and a `pending` candidate whose reason is `awaiting_download` but that names no file URL (that prefix is
  for an admitted candidate with a known file URL not archived yet; a lead with nothing to fetch is `unresolved_role`;
  `lead_registry` seeds reject such an item). An `archive_under` rule whose target the run did not list as a download
  candidate is a warning.
- `--resume` re-hashes archived files instead of downloading them again (an archived fallback URL of the draft is its
  file, and an adapter may identify an archived URL with this run's, e.g. a re-signed Facebook CDN URL);
  `--metadata-only` never downloads and never changes archived records. A record's `download_url`, `resolved_url` and
  `fetched_at` always describe the one fetch that archived its bytes: a refresh that receives the same bytes from
  another (re-signed or moved) URL keeps them, notes `Re-fetched <date> ...` (latest only) and the downloaded
  candidate names the archived URL. An adapter's download check can hold a completed download as `pending` (nothing
  archived): a Wayback raw request answered from another capture, or bytes that are not the CDX payload.
- Each acquisition holds an exclusive `fcntl.flock` on the family's `assets/public/<family>` directory under the root
  (so `TMPDIR` cannot give two writers different locks and no lock file is created); `--lock` takes one more lock in
  addition, never instead of it. A second writer of the same family exits with status 3. Manifests and snapshots are validated and rewritten atomically after
  every resolved candidate, so a killed run loses at most the file in flight. A file that no record names any more (a
  taken-over or withdrawn record) is deleted only after the manifest without it has been written.
- A download whose drafted URL answers HTTP 404 tries the adapter's fallback URLs in order (a fallback on a host the
  run may not contact is skipped); when none downloads, the recorded failure is the drafted URL's 404. An archived
  fallback is described by the adapter's `fallback_adjust` (e.g. the advertised URL, a rendition's own file name, a
  quality that does not call it the upload) on the acquisition and on every refresh.
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
  candidate still names it, restored to `current`. Identical pixels means that every frame (and an animation's frame
  count and frame durations), the EXIF orientation and the embedded ICC profile agree; an animation whose later frames
  changed, or a re-encode that only rotates the image or changes its profile, is a new revision.
- One source's unexpected adapter error does not stop the run: its snapshot keeps the previous candidates, an open
  `adapter` continuation and the error, the remaining sources run, and the CLI exits 1. The CLI also exits 1 when the
  run leaves candidates `failed` or `blocked` (an incomplete acquisition, as `fetch_wikis.py` reports failed or blocked
  downloads), and lists them per source on stderr. A page or API read that fails
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
that a rule now excludes stays archived and fails `--verify`. A download rule with `"archive_under": "<upstream key>"`
says the same file is attached to that other candidate of the source, whose text names what it shows: that candidate
resolves first and holds the record (an existing record of this candidate is taken over: same asset ID, the other
candidate's own fetch and contract path, a note; that candidate's own earlier record of other bytes is superseded), and
this candidate becomes `duplicate_bytes`; a target the run does not list is reported, never silently ignored. A rule `title` is human
text from the cited page (`{default}` stands for the adapter's title); a file name is never a title.

## Adapters

| Adapter | Access | Notes |
| --- | --- | --- |
| `x_post` | `cdn.syndication.twimg.com/tweet-result` (public embed), `pbs.twimg.com …name=orig`; optional `syndication.twitter.com` embed timeline | one candidate per image (`<post>:<n>`); `media_url_https` and `original_info` stay `source_reported`; timeline is a recent sample (list stays open); X re-encodes uploads |
| `pixiv_artwork` | `www.pixiv.net/ajax/illust/<id>` (+ `/pages`); optional per-page Wayback captures | `i.pximg.net` refuses requests without a pixiv Referer, which is never forged: one attempt, `blocked`, host skipped for the run; withheld URLs → `blocked` without a request. A page with a seed `wayback` capture keeps the post as `page_url` and downloads `web.archive.org/web/<ts>id_/<original>` after one exact CDX check (no 200 capture → `pending`; CDX digest → `source_reported.sha1`; capture time → `upstream_revision`; a response from another capture or other bytes than the digest → `pending`, nothing archived); the original must be the URL pixiv lists for that page |
| `youtube_video` | `www.youtube.com/oembed`, `i.ytimg.com/vi/<id>/{maxres,sd,hq}default.jpg`; channel page for channel art | oEmbed channel must equal `params.channel_url` (else `pending`); largest generated thumbnail with 404 fallback, `transformed`, oEmbed's advertised `hqdefault` stays `source_reported.url` (no MIME: oEmbed advertises none and the stored rendition differs, so `format_match` is null); an oEmbed 401/403 is that video's `failed` result (private or not embeddable) and blocks the host only after 3 in a row; channel banner/avatar only when its image ID is on the channel page (`=s0`) |
| `facebook_post` | explicit `www.facebook.com/<page>/...` permalinks read logged out | `og:image` (signed `*.fbcdn.net` rendition, `transformed`) with `og:title`/`og:description` as evidence; the record note names no run date (`fetched_at` dates the archived fetch); file identity = CDN path + `stp`/`cstp`/`ctp` (signatures rotate); `source_reported.mime` is the rendition `stp=dst-…` requests (the upload name's format is noted); `og:url` must stay on `params.page`; no Open Graph image → `blocked` (login wall), refusals/challenges → `blocked`; the timeline is never enumerated |
| `release_page` | SoundCloud `__sc_hydration` / Bandcamp JSON-LD | cover upgraded to `-original` / `_0` (the advertised `artwork_url`/`og:image` stays `source_reported`); uploader permalink checked |
| `bilibili_opus`, `artstation_project` | public JSON | risk-control codes / 403 / challenges → `blocked` (manual access) |
| `apple_store` | product page `serialized-server-data` shelves; iTunes `lookup` per storefront | native size, Apple's lossless PNG rendition of PNG uploads (the advertised JPEG variant stays in `source_reported`), `transformed: true`; per-record quality note names the stored rendition and Apple's per-render identifier (see below) |
| `google_play` | listing pages per locale | `=s0` native size; the advertised `src` rendition is `source_reported`, `=wW-hH` sizes kept as notes, size decoded from bytes |
| `taptap_app` | `__NUXT_DATA__` devalue payload | `original_url`; MD5 of the file compared with the name token (a mismatch is a re-encode: `transformed: true`, byte-exact twin named when archived); crop/resize banner rendition excluded (`upscaled` only when larger than its crop), its URL kept as the upload's `source_reported.url` (size in the notes: another rendition is not comparable); trailer poster frames are `video_placeholder` exclusions |
| `wayback_cdx` | CDX API with `showResumeKey` paging (bounded: repeated key or `max_pages`), `id_` raw captures | earliest capture per distinct payload; CDX digest → `source_reported.sha1`; a response from another capture or with other bytes is held `pending` |
| `page_links`, `direct_files` | explicit pages / explicit file URLs | link patterns per page; JS galleries named file by file; `direct_files` `params.article_images` accounts for every image of a read article page's CSS selection (and `og:image`): `img`/`source` URLs, links and quoted URLs in named attributes (`attr_urls`, e.g. an `onclick` overlay) that name an image file, or the body blocks of an embedded JSON article model (`embedded_json`, e.g. Next.js `__NEXT_DATA__`, checked against the article's own image count; `extra_images` such as the main image are read but not counted); article uploads (normalized URL matching `pattern`) are keyed `<prefix><name>` (a pattern `name` group gives the upload's file name when the URL ends in a CDN signature), every other selection image (app-card icon, lazy-load placeholder, comment avatar, footer widget) `<prefix>other:<host><path>`, all classified by rules, unclassified → `pending`. `article-images` stays open on a missing selection, a selection with no matching body image (`selection-empty`), a missing payload or a count mismatch, and a complete page covers only keys under its prefix. Advertised sizes (`og:image:width`/`height` of an `og:image` listed as the upload itself, WordPress `data-orig-size`, embedded `width_field`/`height_field`) become `source_reported.width`/`height`. A normalized URL that answers 404 falls back to the URL the page lists: the record says so, names that URL as `source_reported.url`, keeps a rendition's own file name and is `source_variant: unknown`. `supplied_marker` (page text such as GNN's `【以下內容為廠商提供資料原文】`) marks the images after it as developer-supplied: `supplied_note` replaces the source's default quality note and a sentence quotes the label; no other press image is described so. Records are titled from the page headline (+ caption, `(n/N)`; a named file the article does not show is a `linked file`, another selection image an `other image`), the file name stays `original_filename` |
| `release_page` (list mode) | `{"tracks_of": "<handle>"}`: one read of `soundcloud.com/<handle>/tracks` | every server-rendered track becomes an item; the list stays open when fewer than the account's `track_count` are rendered |
| `page_probe`, `lead_registry` | one availability read / no request | dead or login-walled sources; research leads without an acquirable file |

`seeds/leads-crosswalk.json` (research sweep) and `seeds/leads-gapfill-crosswalk.json` (research gap-fill: Wayback pixiv
originals, official YouTube/Facebook/X, press, partner collaborations) map every 2026-10-10 lead (`dynamix_original_game`
yes/unclear) to the
source and candidate-key prefix that records its disposition; `lead_list` holds the research file's SHA-256 and a hash
of the lead list, so the seed test detects a dropped lead. Each lead's `files` maps every file URL the research
recorded for it to one exact candidate (matched by `file_url`, `source_reported.url` or `page_url`, or `normalized`/
`noted` with the reason) or registry item, and `files_sha256` hashes the per-lead file lists, so a file that no
candidate accounts for, or a dropped file URL, fails the seed test (a page prefix alone is not enough). `testing.py` holds the offline test doubles used by
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
