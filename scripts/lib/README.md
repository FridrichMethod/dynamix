# `scripts/lib` — shared contract library (schema version 1)

Executable form of [`docs/implementation/09-frozen-contracts.md`](../../docs/implementation/09-frozen-contracts.md).
If the prose contract and this code disagree, report it to the coordinator; do not pick one silently.
Contract changes go through the coordinator, so fetchers and builders use these helpers instead of copies.

```python
# scripts/<name>.py, run as: python -I scripts/<name>.py [--root PATH]
from lib import manifest, media, net, paths, schema
```

Tests put `<repo>/scripts` on `sys.path` and import the same way. Dependencies are listed in `requirements.txt`.
`net` needs `requests`; `media` needs Pillow; `schema`, `checks`, `paths` and `manifest` use only the standard library.

## `schema` — enumerations, helpers, validators

Constants: `SCHEMA_VERSION`, `GAME`, `OWNER`, `REPOSITORY`, `ARCHIVE_URL_PREFIX`, `USER_AGENT`, `DEFAULT_RIGHTS_HOLDER`,
`PAGES_BUDGET_BYTES`, `MAX_FILE_BYTES`; `FAMILIES`, `FAMILY_PRIORITY`, `PREFERRED_SOURCES`, `REGISTERED_SOURCE_IDS`, `KINDS` (catalog
order), `SITE_KINDS`, `ARCHIVE_KINDS`, `SLIDESHOW_KINDS`, `SLIDESHOW_MIN_LONG_EDGE`, `GAMES`, `SOURCE_STATUSES`,
`EVIDENCE_LEVELS`, `DISPOSITIONS`, `EXCLUSION_REASONS`, `MAPPING_STATUSES`, `SOURCE_VARIANTS`, `ORIGIN_STATUSES`,
`MASTER_STATUSES`, `UPSTREAM_STATUSES`, `FAILURE_PHASES`, `ERROR_CLASSES`, `CREDIT_ROLES`, `FORMATS`,
`RASTER_FORMATS`, `FORMAT_MIME`, `FORMAT_EXT`; field-order tuples `*_FIELDS`; regexes `SOURCE_ID_RE`, `ASSET_ID_RE`,
`SONG_ID_RE`, `TIMESTAMP_RE`.

| Function | Meaning |
| --- | --- |
| `asset_id(source_id, sha256)` | `<source_id>:<sha256[:16]>` |
| `candidate_id(source_id, upstream_key)` | `<source_id>:<key>` (wiki key: `File:` title, spaces as `_`) |
| `failure_id(candidate_id, phase)` | `<candidate_id>:<phase>` |
| `source_family(source_id)` | family prefix; `ValueError` otherwise |
| `tier_for(kind, fmt)` | `"archive"` for reference/gameplay screenshots and any PDF, else `"site"` |
| `is_gallery(asset)` / `is_slideshow(asset)` | raster and not `reference` / gallery, slideshow kind, long edge ≥ 1000 |
| `make_source_reported(**values)` | `source_reported` object; omitted keys are `null` |
| `make_download_observed(info, content_type)` | `download_observed` from a `media.inspect()` dict |
| `compute_comparisons(source_reported, observed)` | tri-state `sha1_match`/`bytes_match`/`dimensions_match`/`format_match` (`format_match` compares the advertised MIME with the sniffed one) |
| `default_rights(creator=, url=, restrictions=, holder=, license=)` | rights object, `license` null unless established |
| `failure_record(**fields)` | validated Failure; URL redacted with the stored-URL rule, message with the strict rule |
| `canonical(record, kind)` | copy with keys in contract order (`source`, `asset`, `catalog_asset`, `failure`, `candidate`) |
| `normalize_title`, `catalog_sort_key`, `provenance_sort_key`, `ordered_union`, `catalog_url`, `thumb_relpath` | catalog ordering/merging helpers exactly as the contract defines them; `provenance_sort_key` is the primary-record order: family priority, `PREFERRED_SOURCES` within the family (`wikis:miraheze`, then `wikis:fandom`, then every other source, tied), `quality.source_variant` (`site_original` < `preview` < `unknown`), source ID, asset ID |
| `redact_url(url, *, strict=True)`, `redact_text(text, *, strict=True)`, `contains_credentials(text, *, strict=True)` | strip userinfo, credential `name=value` pairs (in URLs and anywhere in text: relative paths, bare queries), `Authorization`/`Cookie` values, bearer tokens. `strict=True` (logs, Failure/discovery messages) uses `SENSITIVE_QUERY_KEYS`; `strict=False` (stored URLs and evidence) only `CREDENTIAL_QUERY_KEYS` (`token`, `access_token`, `signature`, `x-amz-*`, `x-goog-*`, `password`, ...), so a public `image.php?key=12` is recorded exactly |
| `check_source_slugs(source_ids)` | `ValueError` when two source IDs share a source slug (one flat `data/discovery/`); manifests and the catalog are checked automatically, fetchers registering new IDs call it with `REGISTERED_SOURCE_IDS` + their IDs |

Validators raise `ValueError("<kind> '<id>': <field>: <problem>")`; unknown fields are errors.

| Validator | Checks |
| --- | --- |
| `validate_source(source, family=None, *, catalog=False)` | fields, enums, ID pattern, family; `catalog=True` requires `asset_count` |
| `validate_asset(asset, family=None, *, catalog=False)` | every field and type; ID = source + SHA prefix; MIME from format; PDF ⇒ null size, raster ⇒ positive ints; `path` equals `paths.asset_relpath(...)`; `download_observed` equals the measured fields; `comparisons` equals `compute_comparisons`; mapping/upstream/variant rules |
| `validate_failure(failure, family=None)` | ID, HTTP status vs `http_4xx`/`http_5xx`, `blocked`/`challenge`/`host_not_allowed` not retriable, credential-free |
| `validate_candidate(candidate, source_id=None)` | ID = source + key; reason required unless `downloaded`; exclusion reason vocabulary; `asset_id` only for `downloaded`/`duplicate_bytes`, and then an asset of the candidate's own source; accepted candidates are `dynamix`/`mixed` |
| `validate_discovery(snapshot, source_id=None)` → counts | complete ⇒ no open continuation; candidates valid, unique, sorted; returns `disposition_counts` |
| `validate_manifest(manifest, family=None)` | envelope, all records, sorted unique IDs, known source IDs with unique source slugs, unique paths without case-fold collisions, `superseded_by` chains inside the manifest without cycles, `fetched_at` ≥ newest asset |
| `reconcile(manifest, {source_id: snapshot}, other_assets=())` → `{source_id: counts}` | source counts, downloaded/duplicate links (a `duplicate_bytes` target is an asset of the same source recorded under another candidate; `other_assets` only sharpen that error), failed ⇒ Failure record, complete discovery accounts for every current asset; earlier revisions may share the current revision's candidate (see below) |
| `validate_song_index(index, known_asset_ids=None)` | song fields, ID = `paths.song_id_base(title)` (+ `-N`), positive inclusion evidence, sorted, disagreements name songs |
| `validate_song_links(index, assets)` | every asset (or catalog asset) `song_id` is a song of the index whose title or an alias is among the asset's `song_titles` |
| `validate_catalog(catalog)` | top level, sources (`asset_count`, unique source slugs), failures, catalog asset fields (`url`, `thumb`, `gallery`, `slideshow`, `site_original`, `archive_url`, provenance order, ordered union of `illustrators`/`composers`/`song_titles`/`song_ids`/`collections` across provenance, `variants`), ordering, unique SHA-256, summary arithmetic |

Revisions and removals (07 §Integrity 8: archived bytes are kept). A MediaWiki re-upload keeps the `File:` title and
therefore the candidate ID; the candidate's `asset_id` moves to the new bytes. The older asset keeps its record and
`candidate_id` with `upstream_status: "superseded"` and `superseded_by` naming its successor; following
`superseded_by` must end at the asset the candidate now names. When the newest revision could not be archived
(candidate `failed`/`blocked`/...), the newest archived revision is `superseded` without `superseded_by` (only
`superseded_by` without `superseded` is invalid). A `removed` asset may stay under any candidate state or none. An
asset that its candidate still names as `downloaded` must not be `removed`/`superseded`.

Provenance entries carry `song_ids` and `collections` too (in addition to the fields listed in 09), so the catalog's
merged values are checkable unions.

## `net` — polite allowlisted HTTP client

```python
with net.PoliteClient(["dynamix.miraheze.org", ".wikia.nocookie.net"]) as client:
    data, final_url = client.get_json(api_url, {"action": "query", "list": "allimages"})
    text, final_url, headers = client.get_text(page_url)
    result = client.download(file_url, root / "assets/public/wikis/miraheze", "Cosmic Pulse")
```

`PoliteClient(allowed_hosts, *, min_interval=1.0, max_bytes=64 MiB, timeout=(10, 30), max_attempts=3,
max_redirects=5, max_retry_after=60, max_duration=300, allow_loopback=False, user_agent=USER_AGENT, proxies=None,
sleep, clock, resolver, schedule=None)`. Exact hosts or `.suffix` (subdomains only). Every URL (first and each redirect hop) must
pass `checks.url_problem` (absolute http(s), no userinfo, no whitespace/control characters, none of the ASCII
characters RFC 3986 forbids — notably `\` — and a host of `[a-z0-9_-]` labels or an IP literal; non-ASCII path
characters are fine), and the host requests would actually contact (urllib3 `parse_url` of the prepared URL) must
equal the checked host. Fetchers must pass URLs as given by the API (percent-encoded); a redirect `Location` is
re-encoded like requests does. Manual redirects re-check scheme, allowlist, https→http downgrade and every resolved and
connected address (not global, i.e. private/shared/loopback/link-local/IPv6 site-local/reserved, or multicast;
NAT64-embedded non-public IPv4 and IPv4-compatible IPv6 rejected; `allow_loopback` is for tests); a refused
redirect is reported against the URL that sent it. One request in flight per host, `min_interval` from the end of
one request to the next, for every hop (a host reached only through a redirect included); clients given one
`net.HostSchedule()` share these per-host locks and ready times (the primary
runner passes one to the per-source clients of a run). `max_duration` bounds each attempt from
request start to the end of the body (bodies are read with `read1`, so a trickling server is cut off within one read
timeout of the deadline; `TimeoutFetchError`). Retries: 429/5xx/timeouts/connection errors, ≤ 3 attempts,
`Retry-After` ≤ 60 s honored (longer stops retrying), otherwise 1 s, 2 s. No cookies are stored, environment
proxies/.netrc are ignored, `Authorization`/`Cookie` headers raise `ValueError`. `get_text` decodes the declared
charset, else UTF-8.

Challenges: `cf-mitigated: challenge`, `_cf_chl_opt`, `cf_chl`/`cf-chl`, `/cdn-cgi/challenge-platform/h/` at any
status (a 2xx body counts when it is markup), `<title>Just a moment` only on 403/503, and "challenge"/"captcha"
wording only on 403. Cloudflare JavaScript Detections (`/cdn-cgi/challenge-platform/scripts/jsd/`) on ordinary pages
is ignored, so such 404/503 pages keep `http_4xx`/`http_5xx` (and retries). `get_json`, `get_text` and `download`
raise `ChallengeError` for a 200 interstitial instead of a JSON/decode error.

`download()` returns `DownloadResult(temp_path, final_url, status, content_type, bytes, sha256, sha1, headers)`;
`temp_path` is a hidden `.<slug>.*.part` file in `dest_dir` (ignored by Git), deleted on any failure. Pass it to
`media.finalize_download`. Errors (all `FetchError`, with `error_class`, `http_status`, `retriable`, `manual`,
`attempts`, redacted `url`/`message`, and `.failure(source_id=, candidate_id=, phase="download")` → Failure):
`HostNotAllowedError`, `BlockedError` (401/403), `ChallengeError` (bot check; subclass of `BlockedError`),
`HttpError` (`http_4xx`/`http_5xx`), `SizeLimitError`, `TimeoutFetchError`, `ConnectionFetchError`. Transport errors
name exception classes and OS error text only (`connection failed (ConnectionError <- ... <- ConnectionRefusedError
[Errno 111] Connection refused)`), never urllib3's `Max retries exceeded with url: /path?query`. `error.url` follows
the stored-URL rule, `str(error)` the strict log rule. `.failure()` raises `ValueError` when the failed URL itself is
malformed (only possible for a caller-supplied URL); pass `url=` (e.g. the candidate page URL) then.
Also: `parse_retry_after(value)`, `retry_delay(error, attempt)`, `normalize_host(host)`.

## `media` — sniffing, verification, placement

| Function | Meaning |
| --- | --- |
| `sniff(head_bytes)` | `(format, mime, ext)` for PNG/JPEG/WEBP/GIF/PDF, else `("unknown", None, None)` |
| `inspect(path)` | `{format, mime, ext, width, height, mode, frames, animated, bytes, sha256, sha1}`; Pillow restricted to the sniffed format, `verify()` then full decode of every frame; PDF: signature + `%%EOF`, size `None`; raises `MediaError`. A multi-picture JPEG (Pillow `MPO`, common for camera/phone files) is `JPEG` with the primary picture's size, `frames` = picture count and `animated` false; `animated` is true only for animated GIF/PNG/WEBP |
| `finalize_download(result, root, rel_path)` | inspect the temp file, check extension, `safe_path` under `assets/`, `os.replace` into place; identical existing file kept, different bytes never overwritten; temp deleted on failure; returns inspect dict + `path`. `rel_path` may be a callable receiving the inspect dict |
| `verify_asset_file(root, asset)` | offline `--verify` of one record against the bytes on disk |
| `MediaError(...).failure(source_id=, candidate_id=, url=)` | Failure with `error_class` `decode_error` |

Typical acquisition step:

```python
result = client.download(url, root / "assets/public/wikis/miraheze", original_filename)
info = media.finalize_download(result, root, lambda i: paths.asset_relpath(
    "wikis", paths.source_slug(source_id), original_filename, i["sha256"], i["ext"]))
observed = schema.make_download_observed(info, result.content_type)
comparisons = schema.compute_comparisons(reported, observed)
```

## `paths` — slugs and safe paths

| Function | Meaning |
| --- | --- |
| `slug(text, *, lower=False, max_length=80)` | NFKC, `[A-Za-z0-9._-]`, runs → `-`, trimmed, ≤ 80, `file` when empty, Windows device names prefixed |
| `source_slug(source_id)` | ID after the family, `:` → `-` (`artists:x:ariiol_` → `x-ariiol_`); artist platforms are `[a-z0-9]+` (no hyphen) so the slug splits back unambiguously |
| `source_slug_collisions(source_ids)` | groups of IDs with the same slug after casefold (`official:x`/`archives:x`; `artists:x:a--b`/`artists:x:a-b`) |
| `discovery_relpath(source_id)` / `manifest_relpath(family)` | `data/discovery/<source-slug>.json` / `data/sources/<family>.json` |
| `asset_relpath(family, source_slug, original_filename, sha256, ext)` | `assets/public/<family>/<source-slug>/<slug(stem)>--<sha256[:12]>.<ext>` |
| `filename_stem(name)` | upstream name without directories and last extension |
| `check_relpath(rel, *, under=None)` / `safe_path(root, rel, *, under=None)` | reject absolute, drive, backslash, `.`/`..`/empty components, unsafe characters, symlinked components and escapes |
| `casefold_collisions(rel_paths)` | groups colliding after NFC + casefold, directories included |
| `song_id_base(title)` / `assign_song_ids(titles)` | `song:<slug(title, lower)>`; when the slug is lossy (empty, or it drops non-ASCII letters/digits) `-<song_title_hash(title)>` is appended (`交差して歪む過去と未来` → `song:file-568a196c`, `Moonlight 砂の城` → `song:moonlight-<hash>`), so such IDs never depend on the title set; remaining collisions (ASCII titles differing only in case/punctuation) get deterministic `-2`, `-3` suffixes in sorted-title order |
| `song_title_hash(title)` | first 8 hex digits of SHA-256 over the NFC-normalized exact title (UTF-8) |

Song IDs in manifests: workers compute `paths.song_id_base(title)` for the exact title (identical in every worker).
Only ASCII case/punctuation collisions need an ordinal suffix; those are taken from the reconciled
`data/sources/song-index.json`, and `schema.validate_song_links` reports any asset song ID that is missing from the
index or names a song whose title/aliases are not among the asset's `song_titles`.

## `manifest` — deterministic JSON

`now_utc()`, `loads(text)` / `load_json(path)` (strict: duplicate keys, NaN and BOM rejected), `dumps(obj)`
(UTF-8, `ensure_ascii=False`, indent 2, trailing newline), `write_json_atomic(path, obj)`, `compare_json(path, obj)`
(`None` or a short diff, for `--check`), `empty_manifest(family)`, `sort_records(records, kind=None)`,
`sort_manifest(m)`, `sort_discovery(s)`, `sort_song_index(i)`, `upsert(records, record)`, `disposition_counts(c)`,
`load_manifest(root, family, missing_ok=True)`, `load_discovery(root, source_id)` (validated). Inputs are never
mutated.

## `checks` — field checkers and URL rules

`url_problem(url)` (shared URL syntax rule above), `http_url` (stored URL: `url_problem` + no credential parameters),
`clean_text` (stored evidence text), `message_text` (generated messages, strict), `host_problem`, `normalize_host`,
plus the checker combinators used by `schema`. `URL_FORBIDDEN_ASCII`, `CREDENTIAL_QUERY_KEYS`, `SENSITIVE_QUERY_KEYS`.

## Fixture

`tests/fixtures/contract/` is a repository-shaped synthetic tree for `--root` (see its README for the scenarios);
regenerate it with `python -I -B tests/fixtures/contract/make_fixture.py` after a contract change.
