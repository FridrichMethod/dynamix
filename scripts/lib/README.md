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
`PAGES_BUDGET_BYTES`, `MAX_FILE_BYTES`; `FAMILIES`, `FAMILY_PRIORITY`, `REGISTERED_SOURCE_IDS`, `KINDS` (catalog
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
| `failure_record(**fields)` | validated Failure; URL and message redacted |
| `canonical(record, kind)` | copy with keys in contract order (`source`, `asset`, `catalog_asset`, `failure`, `candidate`) |
| `normalize_title`, `catalog_sort_key`, `provenance_sort_key`, `ordered_union`, `catalog_url`, `thumb_relpath` | catalog ordering/merging helpers exactly as the contract defines them |
| `redact_url(url)`, `redact_text(text)`, `contains_credentials(text)` | strip userinfo, sensitive query values, `Authorization`/`Cookie` values, bearer tokens |

Validators raise `ValueError("<kind> '<id>': <field>: <problem>")`; unknown fields are errors.

| Validator | Checks |
| --- | --- |
| `validate_source(source, family=None, *, catalog=False)` | fields, enums, ID pattern, family; `catalog=True` requires `asset_count` |
| `validate_asset(asset, family=None, *, catalog=False)` | every field and type; ID = source + SHA prefix; MIME from format; PDF ⇒ null size, raster ⇒ positive ints; `path` equals `paths.asset_relpath(...)`; `download_observed` equals the measured fields; `comparisons` equals `compute_comparisons`; mapping/upstream/variant rules |
| `validate_failure(failure, family=None)` | ID, HTTP status vs `http_4xx`/`http_5xx`, `blocked`/`challenge`/`host_not_allowed` not retriable, credential-free |
| `validate_candidate(candidate, source_id=None)` | ID = source + key; reason required unless `downloaded`; exclusion reason vocabulary; `asset_id` only for `downloaded`/`duplicate_bytes`; accepted candidates are `dynamix`/`mixed` |
| `validate_discovery(snapshot, source_id=None)` → counts | complete ⇒ no open continuation; candidates valid, unique, sorted; returns `disposition_counts` |
| `validate_manifest(manifest, family=None)` | envelope, all records, sorted unique IDs, known source IDs, unique paths without case-fold collisions, `fetched_at` ≥ newest asset |
| `reconcile(manifest, {source_id: snapshot}, other_assets=())` → `{source_id: counts}` | source counts, downloaded/duplicate links, failed ⇒ Failure record, complete discovery accounts for every current asset |
| `validate_song_index(index, known_asset_ids=None)` | song fields, ID from title (+ `-N`), positive inclusion evidence, sorted, disagreements name songs |
| `validate_catalog(catalog)` | top level, sources (`asset_count`), failures, catalog asset fields (`url`, `thumb`, `gallery`, `slideshow`, `site_original`, `archive_url`, provenance order/union, `variants`), ordering, unique SHA-256, summary arithmetic |

## `net` — polite allowlisted HTTP client

```python
with net.PoliteClient(["dynamix.miraheze.org", ".wikia.nocookie.net"]) as client:
    data, final_url = client.get_json(api_url, {"action": "query", "list": "allimages"})
    text, final_url, headers = client.get_text(page_url)
    result = client.download(file_url, root / "assets/public/wikis/miraheze", "Cosmic Pulse")
```

`PoliteClient(allowed_hosts, *, min_interval=1.0, max_bytes=64 MiB, timeout=(10, 30), max_attempts=3,
max_redirects=5, max_retry_after=60, allow_loopback=False, user_agent=USER_AGENT, proxies=None, sleep, clock,
resolver)`. Exact hosts or `.suffix` (subdomains only). Manual redirects re-check scheme, allowlist, https→http
downgrade and every resolved and connected address (private/loopback/link-local/reserved rejected; `allow_loopback`
is for tests). One request in flight per host, `min_interval` from the end of one request to the next. Retries:
429/5xx/timeouts/connection errors, ≤ 3 attempts, `Retry-After` ≤ 60 s honored (longer stops retrying), otherwise
1 s, 2 s. No cookies are stored, environment proxies/.netrc are ignored, `Authorization`/`Cookie` headers raise
`ValueError`. `get_text` decodes the declared charset, else UTF-8.

`download()` returns `DownloadResult(temp_path, final_url, status, content_type, bytes, sha256, sha1, headers)`;
`temp_path` is a hidden `.<slug>.*.part` file in `dest_dir` (ignored by Git), deleted on any failure. Pass it to
`media.finalize_download`. Errors (all `FetchError`, with `error_class`, `http_status`, `retriable`, `manual`,
`attempts`, redacted `url`/`message`, and `.failure(source_id=, candidate_id=, phase="download")` → Failure):
`HostNotAllowedError`, `BlockedError` (401/403), `ChallengeError` (bot check; subclass of `BlockedError`),
`HttpError` (`http_4xx`/`http_5xx`), `SizeLimitError`, `TimeoutFetchError`, `ConnectionFetchError`.
Also: `parse_retry_after(value)`, `retry_delay(error, attempt)`, `normalize_host(host)`.

## `media` — sniffing, verification, placement

| Function | Meaning |
| --- | --- |
| `sniff(head_bytes)` | `(format, mime, ext)` for PNG/JPEG/WEBP/GIF/PDF, else `("unknown", None, None)` |
| `inspect(path)` | `{format, mime, ext, width, height, mode, frames, animated, bytes, sha256, sha1}`; Pillow restricted to the sniffed format, `verify()` then full decode of every frame; PDF: signature + `%%EOF`, size `None`; raises `MediaError` |
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
| `source_slug(source_id)` | ID after the family, `:` → `-` (`artists:x:ariiol_` → `x-ariiol_`) |
| `discovery_relpath(source_id)` / `manifest_relpath(family)` | `data/discovery/<source-slug>.json` / `data/sources/<family>.json` |
| `asset_relpath(family, source_slug, original_filename, sha256, ext)` | `assets/public/<family>/<source-slug>/<slug(stem)>--<sha256[:12]>.<ext>` |
| `filename_stem(name)` | upstream name without directories and last extension |
| `check_relpath(rel, *, under=None)` / `safe_path(root, rel, *, under=None)` | reject absolute, drive, backslash, `.`/`..`/empty components, unsafe characters, symlinked components and escapes |
| `casefold_collisions(rel_paths)` | groups colliding after NFC + casefold, directories included |
| `song_id_base(title)` / `assign_song_ids(titles)` | `song:<slug(title, lower)>`; deterministic `-2`, `-3` suffixes in sorted-title order |

## `manifest` — deterministic JSON

`now_utc()`, `loads(text)` / `load_json(path)` (strict: duplicate keys, NaN and BOM rejected), `dumps(obj)`
(UTF-8, `ensure_ascii=False`, indent 2, trailing newline), `write_json_atomic(path, obj)`, `compare_json(path, obj)`
(`None` or a short diff, for `--check`), `empty_manifest(family)`, `sort_records(records, kind=None)`,
`sort_manifest(m)`, `sort_discovery(s)`, `sort_song_index(i)`, `upsert(records, record)`, `disposition_counts(c)`,
`load_manifest(root, family, missing_ok=True)`, `load_discovery(root, source_id)` (validated). Inputs are never
mutated.

## Fixture

`tests/fixtures/contract/` is a repository-shaped synthetic tree for `--root` (see its README for the scenarios);
regenerate it with `python -I tests/fixtures/contract/make_fixture.py` after a contract change.
