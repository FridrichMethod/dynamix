"""Offline tests for scripts/lib/schema.py (+ checks.py, catalog_contract.py): enumerations, helpers, validators.

Valid records come from the committed contract fixture (tests/fixtures/contract); each negative test mutates a deep
copy and expects a ValueError naming the record and the field.
"""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import checks, manifest, schema  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/contract"


def load_fixture():
    manifests = {family: manifest.load_json(FIXTURE / f"data/sources/{family}.json") for family in schema.FAMILIES}
    discoveries = {}
    for path in sorted((FIXTURE / "data/discovery").glob("*.json")):
        snapshot = manifest.load_json(path)
        discoveries[snapshot["source_id"]] = snapshot
    return manifests, discoveries, manifest.load_json(FIXTURE / "data/sources/song-index.json")


def asset_by_title(manifests, title, source_id=None):
    for asset in (asset for value in manifests.values() for asset in value["assets"]):
        if asset["title"] == title and (source_id is None or asset["source_id"] == source_id):
            return copy.deepcopy(asset)
    raise KeyError(title)


def build_catalog(manifests):
    """A minimal catalog assembled exactly as 09-frozen-contracts.md describes, to exercise validate_catalog."""
    records = sorted(
        (asset | {"family": family} for family, value in manifests.items() for asset in value["assets"]),
        key=schema.provenance_sort_key,
    )
    names = {source["id"]: source["name"] for value in manifests.values() for source in value["sources"]}
    groups = {}
    for record in records:
        groups.setdefault(record["sha256"], []).append(record)
    assets = []
    for group in groups.values():
        primary = {key: value for key, value in group[0].items() if key != "family"}
        provenance = [
            {
                "source_id": r["source_id"],
                "source_name": names[r["source_id"]],
                "family": r["family"],
                **{key: r.get(key) for key in schema.PROVENANCE_FIELDS[3:]},
                "asset_id": r["id"],
            }
            for r in group
        ]
        entry = {
            **primary,
            **{
                field: schema.ordered_union(r[field] for r in group)
                for field in ("illustrators", "composers", "song_titles", "song_ids", "collections")
            },
        }
        url = schema.catalog_url(primary["path"])
        entry.update(
            {
                "family": group[0]["family"],
                "source_name": names[primary["source_id"]],
                "url": url,
                "thumb": schema.thumb_relpath(primary["sha256"]) if schema.is_gallery(primary) else None,
                "gallery": schema.is_gallery(primary),
                "slideshow": schema.is_slideshow(primary),
                "site_original": schema.tier_for(primary["kind"], primary["format"]) == "site",
                "archive_url": schema.ARCHIVE_URL_PREFIX + url,
                "provenance": provenance,
                "variants": [],
            }
        )
        assets.append(schema.canonical(entry, "catalog_asset"))
    for entry in assets:
        group = entry.get("variant_group")
        entry["variants"] = sorted(
            other["id"] for other in assets if group and other.get("variant_group") == group and other is not entry
        )
    assets.sort(key=schema.catalog_sort_key)
    sources = [
        {**source, "asset_count": sum(r["source_id"] == source["id"] for r in records)}
        for value in manifests.values()
        for source in value["sources"]
    ]
    failures = [failure for value in manifests.values() for failure in value["failures"]]
    site = sum(entry["bytes"] for entry in assets if entry["site_original"])
    total = sum(entry["bytes"] for entry in assets)
    by_family, by_kind = {}, {}
    for record in records:
        by_family[record["family"]] = by_family.get(record["family"], 0) + 1
    for entry in assets:
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
    return {
        "schema_version": 1,
        "owner": schema.OWNER,
        "game": schema.CATALOG_GAME,
        "repository": schema.REPOSITORY,
        "snapshots": {family: value["fetched_at"] for family, value in manifests.items()},
        "sources": sources,
        "assets": assets,
        "failures": failures,
        "summary": {
            "source_count": len(sources),
            "source_asset_records": len(records),
            "unique_files": len(assets),
            "gallery_images": sum(a["gallery"] for a in assets),
            "slideshow_images": sum(a["slideshow"] for a in assets),
            "exact_duplicate_records": len(records) - len(assets),
            "by_family": {family: by_family.get(family, 0) for family in schema.FAMILIES},
            "by_kind": by_kind,
            "failure_count": len(failures),
            "downloaded_bytes": total,
            "site_original_bytes": site,
            "archive_only_bytes": total - site,
        },
    }


class EnumerationTests(unittest.TestCase):
    """Guards against accidental edits: these literals are copied from 09-frozen-contracts.md."""

    def test_contract_enumerations(self):
        self.assertEqual(
            schema.KINDS,
            (
                "song_art",
                "pack_cover",
                "character",
                "background",
                "part",
                "promotional",
                "store_screenshot",
                "gameplay_screenshot",
                "process",
                "reference",
            ),
        )
        self.assertEqual(set(schema.SITE_KINDS) | set(schema.ARCHIVE_KINDS), set(schema.KINDS))
        self.assertFalse(set(schema.SITE_KINDS) & set(schema.ARCHIVE_KINDS))
        self.assertEqual(schema.FAMILY_PRIORITY, ("artists", "official", "wikis", "archives"))
        self.assertEqual(
            schema.DISPOSITIONS, ("downloaded", "duplicate_bytes", "excluded", "failed", "blocked", "pending")
        )
        self.assertEqual(len(schema.EXCLUSION_REASONS), 13)
        self.assertEqual(
            schema.ERROR_CLASSES,
            (
                "http_4xx",
                "http_5xx",
                "timeout",
                "blocked",
                "challenge",
                "decode_error",
                "size_limit",
                "host_not_allowed",
                "other",
            ),
        )
        self.assertEqual(schema.UPSTREAM_STATUSES, ("current", "removed", "superseded", "unknown"))
        self.assertEqual(
            schema.MAPPING_STATUSES, ("confirmed_primary", "confirmed_wiki", "inferred", "unmapped", "conflict")
        )
        for source_id in schema.REGISTERED_SOURCE_IDS + ("artists:x:ariiol_", "artists:artstation:sandgodoven"):
            self.assertRegex(source_id, schema.SOURCE_ID_RE)
        for bad in (
            "legacy:x",
            "wikis:Mira",
            "wikis:a_b",
            "artists:x",
            "artists:x:-bad",
            "wikis:x:y",
            "artists:x-foo:bar",
        ):  # a hyphenated platform could not be split back out of its source slug
            self.assertNotRegex(bad, schema.SOURCE_ID_RE)

    def test_source_slugs_must_be_unique(self):
        schema.check_source_slugs([*schema.REGISTERED_SOURCE_IDS, "artists:x:ariiol_"])
        for ids in (["official:x", "archives:x"], ["artists:x:a--b", "artists:x:a-b"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError) as caught:
                schema.check_source_slugs(ids)
            self.assertIn("share the source slug", str(caught.exception))

    def test_trailing_newlines_are_rejected(self):
        manifests, _, _ = load_fixture()
        asset = asset_by_title(manifests, "Cosmic Pulse (crop)")
        for field in ("source_id", "sha256", "sha1", "fetched_at", "id"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                schema.validate_asset({**asset, field: asset[field] + "\n"})

    def test_tiers_gallery_and_slideshow(self):
        self.assertEqual(schema.tier_for("song_art", "PNG"), "site")
        self.assertEqual(schema.tier_for("store_screenshot", "JPEG"), "archive")
        self.assertEqual(schema.tier_for("gameplay_screenshot", "PNG"), "archive")
        self.assertEqual(schema.tier_for("song_art", "PDF"), "archive")
        with self.assertRaises(ValueError):
            schema.tier_for("fanart", "PNG")
        art = {"format": "PNG", "kind": "song_art", "width": 1000, "height": 400}
        self.assertTrue(schema.is_gallery(art) and schema.is_slideshow(art))
        self.assertFalse(schema.is_slideshow({**art, "width": 999}))
        self.assertFalse(schema.is_slideshow({**art, "kind": "background"}))
        self.assertTrue(schema.is_gallery({**art, "kind": "gameplay_screenshot"}))
        self.assertFalse(schema.is_gallery({**art, "kind": "reference"}))
        self.assertFalse(schema.is_gallery({"format": "PDF", "kind": "song_art", "width": None, "height": None}))


class HelperTests(unittest.TestCase):
    def test_compute_comparisons_tri_state(self):
        observed = {"sniffed_format": "WEBP", "bytes": 10, "width": 2, "height": 3, "sha1": "a" * 40}
        reported = schema.make_source_reported(
            mime="image/png; charset=binary", bytes=10, width=2, height=3, sha1="A" * 40
        )
        self.assertEqual(
            schema.compute_comparisons(reported, observed),
            {"sha1_match": True, "bytes_match": True, "dimensions_match": True, "format_match": False},
        )
        self.assertEqual(
            schema.compute_comparisons(schema.make_source_reported(), observed), dict.fromkeys(schema.COMPARISON_FIELDS)
        )
        self.assertEqual(
            schema.compute_comparisons(
                schema.make_source_reported(mime="image/jpg"), {**observed, "sniffed_format": "JPEG"}
            )["format_match"],
            True,
        )
        pdf_observed = {**observed, "sniffed_format": "PDF", "width": None, "height": None}
        self.assertIsNone(schema.compute_comparisons(reported, pdf_observed)["dimensions_match"])  # nothing measured
        with self.assertRaises(ValueError):
            schema.make_source_reported(size=3)

    def test_redaction(self):
        self.assertEqual(
            schema.redact_url("https://u:pw@example.com/a?token=abc&format=original&Sig=x#access_token=y"),
            "https://example.com/a?token=REDACTED&format=original&Sig=REDACTED#access_token=REDACTED",
        )
        clean = "https://static.wikia.nocookie.net/dynamix/images/a/a1/X.png/revision/latest?cb=1&format=original"
        self.assertEqual(schema.redact_url(clean), clean)
        text = "GET https://a.example/x?api_key=zz -> 403; Cookie: sid=1; Authorization: Bearer abc"
        redacted = schema.redact_text(text)
        for secret in ("zz", "sid=1", "abc"):
            self.assertNotIn(secret, redacted)
        self.assertTrue(schema.contains_credentials(text))
        self.assertFalse(schema.contains_credentials("basic illustration of the bearer of news"))

    def test_redaction_of_pairs_outside_absolute_urls(self):
        """urllib3 messages carry the path and query without a scheme (``Max retries exceeded with url: /x?...``)."""
        text = (
            "HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded with url: "
            "/x.png?X-Amz-Signature=deadbeefsecret&token=topsecret&format=original (Caused by ...)"
        )
        redacted = schema.redact_text(text)
        for secret in ("deadbeefsecret", "topsecret"):
            self.assertNotIn(secret, redacted)
        self.assertIn("format=original", redacted)
        self.assertTrue(schema.contains_credentials(text))
        self.assertEqual(schema.redact_text("?access_token=abc"), "?access_token=REDACTED")
        self.assertEqual(schema.redact_text(redacted), redacted)  # idempotent
        self.assertFalse(schema.contains_credentials("cb=20250114000000&format=original; token="))

    def test_stored_urls_keep_generic_parameters(self):
        """Stored provenance URLs only lose real credential parameters; logs/messages stay strict."""
        wayback = "https://web.archive.org/web/2014/http://www.example.com/img.php?key=12&sid=3&code=x"
        self.assertIsNone(checks.http_url(wayback))
        self.assertEqual(schema.redact_url(wayback, strict=False), wayback)
        self.assertNotIn("key=12", schema.redact_url(wayback))
        self.assertIn("key=REDACTED", schema.redact_text(f"HTTP 404 for {wayback}"))
        self.assertIsNotNone(checks.http_url("https://cdn.example.com/a.png?X-Amz-Signature=abc"))
        self.assertIsNotNone(checks.http_url("https://cdn.example.com/a.png?token=abc"))
        record = schema.failure_record(
            source_id="archives:wayback",
            candidate_id="archives:wayback:img-12",
            phase="download",
            url=wayback,
            attempted_at="2026-10-10T12:00:00Z",
            attempts=1,
            error_class="http_4xx",
            http_status=404,
            retriable=False,
            manual=False,
            message=f"HTTP 404 for {wayback}",
        )
        self.assertEqual(record["url"], wayback)
        self.assertNotIn("key=12", record["message"])

    def test_url_syntax_rules(self):
        """A backslash ends the authority for urllib3 but not for urlsplit, so it can smuggle a host past a suffix
        check; all ASCII characters RFC 3986 forbids are refused, non-ASCII (IRI) characters are allowed."""
        for bad in (
            "http://evil.example\\.miraheze.org/x",
            "http://evil.example\\@a.miraheze.org/x",
            "https://a.example/x y",
            "https://a.example/x|y",
            "https://a.example/{x}",
            "https://a.example/x<",
            "https://a.example/\x01",
            "http://evil%2eexample.miraheze.org/",
            "http://a.example:99999/",
            "http://[::1/x",
            "ftp://a.example/x",
            "https://a.example/x\t",
            "//a.example/x",
        ):
            with self.subTest(url=bad):
                self.assertIsNotNone(checks.url_problem(bad))
                self.assertIsNotNone(checks.http_url(bad))
        for good in (
            "https://dynamix.miraheze.org/wiki/交差して歪む過去と未来",
            "https://static.miraheze.org/dynamixwiki/archive/a/a1/20240601000000!Dynamix_background_03.png",
            "https://dynamix.miraheze.org/wiki/File:Cosmic_Pulse_(crop).png",
            "http://[2001:db8::1]:8080/x",
            "https://Dynamix.Miraheze.org./w/api.php?action=query&list=allimages",
        ):
            with self.subTest(url=good):
                self.assertIsNone(checks.url_problem(good))

    def test_canonical_orders_keys_without_mutation(self):
        manifests, _, _ = load_fixture()
        asset = asset_by_title(manifests, "Cosmic Pulse (crop)")
        shuffled = dict(reversed(list(asset.items())))
        shuffled["quality"] = dict(reversed(list(asset["quality"].items())))
        before = copy.deepcopy(shuffled)
        ordered = schema.canonical(shuffled, "asset")
        self.assertEqual(
            list(ordered), [key for key in schema.ASSET_FIELDS + schema.ASSET_OPTIONAL_FIELDS if key in asset]
        )
        self.assertEqual(list(ordered["quality"]), list(schema.QUALITY_FIELDS))
        self.assertEqual(shuffled, before)

    def test_failure_record_redacts_and_validates(self):
        record = schema.failure_record(
            source_id="wikis:fandom",
            candidate_id="wikis:fandom:File:X.png",
            phase="download",
            url="https://user:pw@dynamix.fandom.com/x?token=t",
            attempted_at="2026-10-10T12:00:00Z",
            attempts=1,
            error_class="blocked",
            http_status=403,
            retriable=False,
            manual=True,
            message="denied for https://dynamix.fandom.com/x?token=t",
        )
        self.assertEqual(record["id"], "wikis:fandom:File:X.png:download")
        self.assertNotIn("pw", record["url"])
        self.assertNotIn("token=t", record["message"])


class RecordValidationTests(unittest.TestCase):
    def setUp(self):
        self.manifests, self.discoveries, self.song_index = load_fixture()

    def assertInvalid(self, validate, record, *fragments, **kwargs):
        with self.assertRaises(ValueError) as caught:
            validate(record, **kwargs)
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))
        return caught.exception

    def test_fixture_records_are_valid(self):
        for family, value in self.manifests.items():
            schema.validate_manifest(value, family)
        sources = {source["id"]: source for value in self.manifests.values() for source in value["sources"]}
        for source_id, snapshot in self.discoveries.items():
            counts = schema.validate_discovery(snapshot, source_id)
            self.assertEqual(sum(counts.values()), sources[source_id]["candidate_count"], source_id)
        schema.validate_song_index(self.song_index)

    def test_timestamps_and_dates_take_ascii_digits_only(self):
        """``\\d`` and strptime accept every Unicode decimal digit, but the validators order timestamps as strings:
        fullwidth digits sort after all ASCII ones, so a year-2000 fetched_at would pass the 'latest' check."""
        for value in ("２０２６-10-10T12:00:00Z", "٢٠٢٦-10-10T12:00:00Z", "2026-1０-10T12:00:00Z"):
            with self.subTest(timestamp=value):
                self.assertIsNotNone(checks.timestamp(value))
        for value in ("٢٠٢٦-10-10", "２０２６-10-10"):
            with self.subTest(date=value):
                self.assertIsNotNone(checks.date(value))
        self.assertIsNone(checks.timestamp("2026-10-10T12:00:00Z"))
        self.assertIsNone(checks.date("2026-10-10"))
        wikis = copy.deepcopy(self.manifests["wikis"])
        wikis["fetched_at"] = "２０００-01-01T00:00:00Z"
        self.assertInvalid(schema.validate_manifest, wikis, "fetched_at")

    def test_asset_field_errors_name_record_and_field(self):
        base = asset_by_title(self.manifests, "Cosmic Pulse (crop)")
        label = f"asset {base['id']!r}"
        cases = [
            ("illustrator", ["ariiol"], "illustrator: unknown field"),
            ("kind", "fanart", "kind: must be one of"),
            ("game", "dynamix_universe", "game:"),
            ("bytes", True, "bytes: must be an integer"),
            ("sha256", base["sha256"].upper(), "sha256:"),
            ("width", None, "width: must be a positive integer"),
            ("mime", "image/jpeg", "mime:"),
            ("fetched_at", "2026-10-10 12:00:00", "fetched_at:"),
            ("page_url", "https://user:pw@dynamix.miraheze.org/x", "page_url: must not contain credentials"),
            ("download_url", "https://dynamix.miraheze.org/x.png?token=abc", "download_url:"),
            ("song_ids", ["Cosmic Pulse"], "song_ids:"),
            ("illustrators", ["ariiol", "ariiol"], "illustrators: must not contain duplicates"),
            ("path", base["path"].replace("miraheze", "fandom"), "path: must be"),
            ("id", "wikis:miraheze:0000000000000000", "id: must be"),
            ("candidate_id", "wikis:fandom:File:X.png", "candidate_id:"),
            ("variant_group", "", "variant_group:"),
        ]
        for field, value, fragment in cases:
            with self.subTest(field=field):
                record = copy.deepcopy(base)
                record[field] = value
                error = self.assertInvalid(schema.validate_asset, record, fragment)
                if field != "id":
                    self.assertIn(label, str(error))
        record = copy.deepcopy(base)
        del record["rights"]
        self.assertInvalid(schema.validate_asset, record, "rights: missing required field")
        self.assertInvalid(schema.validate_asset, base, "belongs to family 'wikis'", family="artists")

    def test_asset_cross_field_rules(self):
        crop = asset_by_title(self.manifests, "Cosmic Pulse (crop)")
        pdf = asset_by_title(self.manifests, "Synthetic press kit (reference)")
        mutations = [
            (pdf, lambda r: r.update(width=10), "width: must be null for a PDF"),
            (crop, lambda r: r["download_observed"].update(bytes=r["bytes"] + 1), "download_observed.bytes"),
            (crop, lambda r: r["download_observed"].update(sniffed_format="WEBP"), "download_observed.sniffed_format"),
            (crop, lambda r: r["download_observed"].update(sha256="0" * 64), "download_observed.sha256"),
            (crop, lambda r: r["download_observed"].update(sha1="0" * 40), "download_observed.sha1"),
            (crop, lambda r: r["download_observed"].update(width=r["width"] + 1), "download_observed.width"),
            (crop, lambda r: r["download_observed"].update(height=r["height"] + 1), "download_observed.height"),
            (
                crop,
                lambda r: r["mapping_evidence"].append(
                    {"url": "https://cdn.example/x.png?token=abc", "field": "image"}
                ),
                "must not contain credentials",
            ),
            (crop, lambda r: r["comparisons"].update(sha1_match=None), "comparisons: must be"),
            (crop, lambda r: r["source_reported"].update(sha1="b" * 40), "comparisons: must be"),
            (crop, lambda r: r.update(mapping_evidence=[]), "mapping_evidence: must not be empty"),
            (crop, lambda r: r.update(mapping_status="unmapped"), "song_ids: must be empty"),
            (crop, lambda r: r.update(superseded_by="wikis:miraheze:" + "1" * 16), "superseded_by: is only allowed"),
            (crop, lambda r: r.update(superseded_by=r["id"], upstream_status="superseded"), "must name another"),
            (crop, lambda r: r.pop("variant_group"), "variant_role: requires variant_group"),
            (crop, lambda r: r.update(game_associations=["arcaea"]), "game_associations: must include"),
            (crop, lambda r: r["quality"].update(origin_status="artist"), "quality: origin_status"),
            (crop, lambda r: r["rights"].update(license=""), "rights: license"),
            (
                crop,
                lambda r: r.update(
                    credit_evidence=[{"role": "artist", "name": "x", "url": None, "field": None, "quote": None}]
                ),
                "credit_evidence",
            ),
            (  # DX2-07: a family record's credit evidence names a credited name of its role, as the catalog's does
                crop,
                lambda r: r.update(
                    credit_evidence=[
                        {"role": "composer", "name": "Somebody Else", "url": None, "field": None, "quote": "x"}
                    ]
                ),
                f"asset {crop['id']!r}: credit_evidence: [0] names composer 'Somebody Else', who is not in composers",
            ),
            (  # a role swap: Yunosuke is credited as composer only
                crop,
                lambda r: r.update(
                    credit_evidence=[
                        {"role": "illustrator", "name": "ariiol", "url": None, "field": None, "quote": "ariiol"},
                        {"role": "illustrator", "name": "Yunosuke", "url": None, "field": None, "quote": "Yunosuke"},
                    ]
                ),
                "credit_evidence: [1] names illustrator 'Yunosuke', who is not in illustrators",
            ),
        ]
        for base, mutate, fragment in mutations:
            with self.subTest(fragment=fragment):
                record = copy.deepcopy(base)
                mutate(record)
                self.assertInvalid(schema.validate_asset, record, fragment)
        advertised = copy.deepcopy(pdf)  # a page size is advertised, but a PDF has no decoded pixel size
        advertised["source_reported"].update(width=1240, height=1754)
        schema.validate_asset(advertised)
        self.assertIsNone(advertised["comparisons"]["dimensions_match"])
        self.assertInvalid(
            schema.validate_asset,
            {**advertised, "comparisons": {**advertised["comparisons"], "dimensions_match": False}},
            "comparisons: must be",
        )

    def test_source_and_failure(self):
        source = copy.deepcopy(self.manifests["wikis"]["sources"][0])
        self.assertInvalid(schema.validate_source, {**source, "family": "artists"}, "family: must match")
        self.assertInvalid(schema.validate_source, {**source, "status": "inherited"}, "status:")
        self.assertInvalid(schema.validate_source, {**source, "candidate_count": -1}, "candidate_count:")
        schema.validate_source(source, catalog=True)
        without_count = {key: value for key, value in source.items() if key != "asset_count"}
        schema.validate_source(without_count)
        self.assertInvalid(schema.validate_source, without_count, "asset_count: missing", catalog=True)
        failure = copy.deepcopy(self.manifests["wikis"]["failures"][0])
        schema.validate_failure(failure, "wikis")
        schema.validate_failure(failure)  # without a family, any registered family
        self.assertInvalid(
            schema.validate_failure,
            failure,
            f"failure {failure['id']!r}",
            "belongs to family 'wikis'",
            family="artists",
        )
        self.assertInvalid(schema.validate_failure, {**failure, "id": failure["id"] + "x"}, "id: must be")
        self.assertInvalid(schema.validate_failure, {**failure, "error_class": "http_4xx"}, "http_status")
        self.assertInvalid(schema.validate_failure, {**failure, "error_class": "blocked"}, "retriable: must be false")
        self.assertInvalid(schema.validate_failure, {**failure, "attempts": 0}, "attempts:")
        self.assertInvalid(schema.validate_failure, {**failure, "message": "Cookie: a=b"}, "message:")
        self.assertInvalid(schema.validate_failure, {**failure, "phase": "upload"}, "phase:")
        foreign = "wikis:miraheze:File:X.png"  # a candidate of another source
        self.assertInvalid(
            schema.validate_failure,
            {**failure, "candidate_id": foreign, "id": schema.failure_id(foreign, failure["phase"])},
            "candidate_id: must start with 'wikis:fandom:'",
        )

    def test_candidates(self):
        snapshot = self.discoveries["wikis:miraheze"]
        by_disposition = {c["disposition"]: c for c in snapshot["candidates"]}
        downloaded, excluded = copy.deepcopy(by_disposition["downloaded"]), copy.deepcopy(by_disposition["excluded"])
        duplicate = copy.deepcopy(by_disposition["duplicate_bytes"])
        self.assertTrue(downloaded["game_evidence"] and duplicate["game_evidence"])
        cases = [
            ({**downloaded, "asset_id": None}, "asset_id: is required"),
            ({**downloaded, "game": "unknown"}, "game: must be 'dynamix' or 'mixed'"),
            # DX2-07: an accepted candidate keeps the evidence of its game identity
            ({**downloaded, "game_evidence": []}, "game_evidence: must not be empty for disposition 'downloaded'"),
            ({**duplicate, "game_evidence": []}, "game_evidence: must not be empty for disposition 'duplicate_bytes'"),
            ({**downloaded, "asset_id": "wikis:fandom:" + "0" * 16}, "same source"),
            ({**downloaded, "id": downloaded["id"] + "x"}, "id: must be"),
            ({**downloaded, "upstream_key": "File:A b.png", "id": "wikis:miraheze:File:A b.png"}, "upstream_key"),
            ({**excluded, "reason": "too small"}, "reason: must start with an exclusion reason"),
            ({**excluded, "reason": None}, "reason: is required"),
            ({**excluded, "asset_id": downloaded["asset_id"]}, "asset_id: must be absent"),
            ({**downloaded, "disposition": "pending", "reason": "x"}, "asset_id: must be absent"),
            ({**downloaded, "role": "fanart"}, "role:"),
            (
                {**excluded, "disposition": "pending", "reason": "pending: review"},
                "reason: must start with a pending reason",
            ),
            (
                {**excluded, "disposition": "pending", "reason": "needs review"},
                "reason: must start with a pending reason",
            ),
        ]
        for prefix in schema.PENDING_REASONS:
            schema.validate_candidate({**excluded, "disposition": "pending", "reason": f"{prefix}: detail"})
        schema.validate_candidate({**excluded, "game_evidence": []})  # only an accepted candidate needs game evidence
        universe = next(c for c in snapshot["candidates"] if (c.get("reason") or "").startswith("universe_only"))
        cases.append(({**universe, "game": "dynamix"}, "game: must be 'dynamix_universe'"))
        other_game = {**excluded, "reason": "not_dynamix: another game's key art"}
        schema.validate_candidate({**other_game, "game": "not_dynamix"})
        cases.append(({**other_game, "game": "unknown"}, "game: must be 'not_dynamix'"))
        for record, fragment in cases:
            with self.subTest(fragment=fragment):
                self.assertInvalid(schema.validate_candidate, record, fragment)

    def test_discovery_rules(self):
        complete = copy.deepcopy(self.discoveries["wikis:miraheze"])
        complete["continuation"]["allimages"] = "Next_file.png"
        self.assertInvalid(schema.validate_discovery, complete, "continuation", "not complete")
        unsorted = copy.deepcopy(self.discoveries["wikis:miraheze"])
        unsorted["candidates"].reverse()
        self.assertInvalid(schema.validate_discovery, unsorted, "candidates: must be sorted")
        duplicate = copy.deepcopy(self.discoveries["wikis:miraheze"])
        duplicate["candidates"].append(duplicate["candidates"][-1])
        self.assertInvalid(schema.validate_discovery, duplicate, "duplicate id")
        backwards = copy.deepcopy(self.discoveries["wikis:fandom"])
        backwards["finished_at"] = "2026-10-10T10:00:00Z"
        self.assertInvalid(schema.validate_discovery, backwards, "finished_at")
        foreign = copy.deepcopy(self.discoveries["wikis:fandom"])
        self.assertInvalid(schema.validate_discovery, foreign, "must be 'wikis:miraheze'", source_id="wikis:miraheze")
        self.assertEqual(
            schema.validate_discovery(self.discoveries["wikis:miraheze"]),
            {"downloaded": 4, "duplicate_bytes": 1, "excluded": 2, "failed": 0, "blocked": 0, "pending": 1},
        )

    def test_manifest_rules(self):
        wikis = self.manifests["wikis"]

        def shared_path(m):
            """A second record whose SHA-256 differs only after the 12 digits the path keeps: another ID, same path."""
            twin = copy.deepcopy(next(a for a in m["assets"] if a["title"] == "Cosmic Pulse (crop)"))
            sha = twin["sha256"]
            sha = sha[:12] + ("1" if sha[12] == "0" else "0") + sha[13:]
            twin.update(sha256=sha, id=schema.asset_id(twin["source_id"], sha))
            twin["download_observed"]["sha256"] = sha
            schema.validate_asset(twin, "wikis")
            m["assets"] = sorted([*m["assets"], twin], key=lambda a: a["id"])

        cases = [
            (shared_path, "assets: two records share a path"),
            (lambda m: m["sources"].reverse(), "sources: must be sorted"),
            (lambda m: m["failures"].append(copy.deepcopy(m["failures"][0])), "failures: duplicate id"),
            (lambda m: m["assets"].reverse(), "assets: must be sorted"),
            (lambda m: m["assets"].append(copy.deepcopy(m["assets"][-1])), "duplicate id"),
            (lambda m: m.update(fetched_at="2026-10-09T00:00:00Z"), "fetched_at: must be the latest"),
            (lambda m: m.update(fetched_at=None), "fetched_at"),
            (lambda m: m["sources"].pop(0), "is not a source of"),
            (lambda m: m.update(family="artists"), "belongs to family 'wikis'"),
            (lambda m: m.update(extra=1), "extra: unknown field"),
            (lambda m: m.update(schema_version=2), "schema_version"),
            (  # DX2-07: the credit-role rule holds for every family record, not only in the catalog
                lambda m: m["assets"][0].update(
                    credit_evidence=[{"role": "illustrator", "name": "HAT", "url": None, "field": None, "quote": "HAT"}]
                ),
                "credit_evidence: [0] names illustrator 'HAT', who is not in illustrators",
            ),
            (
                lambda m: m["assets"][0].update(upstream_status="superseded", superseded_by="wikis:fandom:" + "e" * 16),
                "superseded_by: names no asset",
            ),
            (
                lambda m: (
                    m["assets"][0].update(upstream_status="superseded", superseded_by=m["assets"][1]["id"])
                    or m["assets"][1].update(upstream_status="superseded", superseded_by=m["assets"][0]["id"])
                ),
                "chain forms a cycle",
            ),
        ]
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                record = copy.deepcopy(wikis)
                mutate(record)
                self.assertInvalid(schema.validate_manifest, record, fragment)
        artists = copy.deepcopy(self.manifests["artists"])  # slug() collapses "--", so a--b and a-b collide
        artists["sources"] = sorted(
            [{**artists["sources"][0], "id": handle, "name": handle} for handle in ("artists:x:a--b", "artists:x:a-b")],
            key=lambda s: s["id"],
        )
        artists.update(assets=[], failures=[])
        self.assertInvalid(schema.validate_manifest, artists, "share the source slug")

    def test_reconcile(self):
        all_assets = [asset for value in self.manifests.values() for asset in value["assets"]]
        snapshots = {sid: snap for sid, snap in self.discoveries.items() if sid.startswith("wikis:")}
        counts = schema.reconcile(self.manifests["wikis"], snapshots, other_assets=all_assets)
        self.assertEqual(counts["wikis:fandom"]["failed"], 1)

        def broken(mutate):
            wikis, snaps = copy.deepcopy(self.manifests["wikis"]), copy.deepcopy(snapshots)
            mutate(wikis, snaps)
            return wikis, snaps

        def candidate(snaps, source_id, disposition):
            return next(c for c in snaps[source_id]["candidates"] if c["disposition"] == disposition)

        def failure_without_candidate(m, s):
            gone = "wikis:miraheze:File:Gone.png"
            failure = {**m["failures"][0], "source_id": "wikis:miraheze", "candidate_id": gone}
            failure["id"] = schema.failure_id(gone, failure["phase"])
            schema.validate_failure(failure, "wikis")
            m["failures"] = sorted([*m["failures"], failure], key=lambda f: f["id"])
            m["sources"][1].update(failure_count=1)

        def swapped_snapshots(m, s):
            s["wikis:fandom"], s["wikis:miraheze"] = s["wikis:miraheze"], s["wikis:fandom"]

        cases = [
            (lambda m, s: m["sources"][0].update(asset_count=99), "asset_count: 99 but the data has 2"),
            (lambda m, s: m["sources"][0].update(failure_count=99), "failure_count: 99 but the data has 1"),
            (
                failure_without_candidate,
                "failure 'wikis:miraheze:File:Gone.png:download': candidate_id: missing from a complete discovery",
            ),
            (swapped_snapshots, "has no matching source"),
            (lambda m, s: m["sources"][1].update(candidate_count=99), "candidate_count"),
            (lambda m, s: m["sources"][0].update(discovery_complete=True), "discovery_complete"),
            (
                lambda m, s: candidate(s, "wikis:miraheze", "downloaded").update(asset_id="wikis:miraheze:" + "0" * 16),
                "not an asset recorded",
            ),
            (lambda m, s: candidate(s, "wikis:miraheze", "downloaded").update(role="background"), "role"),
            (
                lambda m, s: candidate(s, "wikis:miraheze", "duplicate_bytes").update(
                    asset_id="wikis:miraheze:" + "1" * 16
                ),
                "names no recorded asset",
            ),
            (lambda m, s: m.update(failures=[]) or m["sources"][0].pop("failure_count"), "needs a Failure record"),
            (
                lambda m, s: (
                    s["wikis:miraheze"]["candidates"].remove(candidate(s, "wikis:miraheze", "downloaded"))
                    or m["sources"][1].update(candidate_count=7)
                ),
                "missing from a complete discovery",
            ),
            (lambda m, s: candidate(s, "wikis:fandom", "failed").update(disposition="pending"), "not failed/blocked"),
        ]
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                wikis, snaps = broken(mutate)
                with self.assertRaises(ValueError) as caught:
                    schema.reconcile(wikis, snaps, other_assets=all_assets)
                self.assertIn(fragment, str(caught.exception))

    def wiki_snapshots(self):
        return {sid: copy.deepcopy(snap) for sid, snap in self.discoveries.items() if sid.startswith("wikis:")}

    def test_duplicate_bytes_names_another_candidate_of_the_same_source(self):
        """09: ``duplicate_bytes`` means the *same source* already archived identical bytes under another candidate.
        Identical bytes from another source are that source's own ``downloaded`` record, so the catalog keeps every
        provenance record; a cross-source target would silently drop one."""
        all_assets = [asset for value in self.manifests.values() for asset in value["assets"]]
        schema.reconcile(self.manifests["wikis"], self.wiki_snapshots(), other_assets=all_assets)  # same-source copy

        def cross_source(target_of):
            wikis, snaps = copy.deepcopy(self.manifests["wikis"]), self.wiki_snapshots()
            fandom = next(c for c in snaps["wikis:fandom"]["candidates"] if c["id"].endswith(":File:Cosmic_Pulse.png"))
            own = fandom["asset_id"]
            fandom.update(disposition="duplicate_bytes", asset_id=target_of(own), reason="identical SHA-256")
            wikis["assets"] = [asset for asset in wikis["assets"] if asset["id"] != own]
            next(s for s in wikis["sources"] if s["id"] == "wikis:fandom")["asset_count"] -= 1
            schema.validate_manifest(wikis, "wikis")  # the manifest alone stays valid
            return wikis, snaps, fandom

        miraheze_copy = cross_source(lambda own: own.replace("wikis:fandom:", "wikis:miraheze:"))
        other_family = cross_source(
            lambda own: next(a["id"] for a in all_assets if a["source_id"].startswith("artists:"))
        )
        for name, (wikis, snaps, fandom) in {"miraheze": miraheze_copy, "artists": other_family}.items():
            with self.subTest(target=name):
                self.assertInvalid(schema.validate_candidate, fandom, "asset_id", "same source")
                for others in (all_assets, ()):
                    with self.assertRaises(ValueError) as caught:
                        schema.reconcile(wikis, snaps, other_assets=others)
                    self.assertIn("wikis:fandom:File:Cosmic_Pulse.png", str(caught.exception))
                    self.assertIn("asset_id", str(caught.exception))

        snaps = self.wiki_snapshots()  # a candidate cannot be a duplicate of its own record
        own = next(c for c in snaps["wikis:miraheze"]["candidates"] if c["id"].endswith(":File:Cosmic_Pulse.png"))
        own.update(disposition="duplicate_bytes", reason="identical SHA-256")
        schema.validate_candidate(own, "wikis:miraheze")
        with self.assertRaises(ValueError) as caught:
            schema.reconcile(self.manifests["wikis"], snaps, other_assets=all_assets)
        self.assertIn("another candidate", str(caught.exception))

    def test_superseded_revision_shares_the_current_candidate(self):
        """MediaWiki re-uploads keep the File: title, so the older archived revision keeps the candidate ID."""
        wikis = self.manifests["wikis"]
        old = next(a for a in wikis["assets"] if a.get("upstream_status") == "superseded")
        current = next(a for a in wikis["assets"] if a["id"] == old["superseded_by"])
        self.assertEqual(old["candidate_id"], current["candidate_id"])
        self.assertNotEqual(old["sha256"], current["sha256"])
        schema.reconcile(wikis, self.wiki_snapshots())

        def run(mutate):
            manifest_copy, snaps = copy.deepcopy(wikis), self.wiki_snapshots()
            by_id = {a["id"]: a for a in manifest_copy["assets"]}
            candidate = next(c for c in snaps["wikis:miraheze"]["candidates"] if c["id"] == old["candidate_id"])
            mutate(by_id[old["id"]], by_id[current["id"]], candidate)
            schema.validate_manifest(manifest_copy, "wikis")
            return schema.reconcile(manifest_copy, snaps)

        def unmarked(older, newer, candidate):
            older.pop("upstream_status")
            older.pop("superseded_by")

        def new_revision_failed(older, newer, candidate):
            older.pop("superseded_by")  # the newest archived revision; its successor was not archived
            newer.update(upstream_status="superseded", superseded_by=older["id"])
            candidate.update(disposition="failed", reason="download failed: HTTP 503", asset_id=None)

        run(lambda older, newer, candidate: older.update(upstream_status="removed") or older.pop("superseded_by"))
        failing = [
            (unmarked, "its candidate is not 'downloaded' with this asset_id"),
            (lambda older, newer, candidate: older.pop("superseded_by"), "chain ends at"),
            (
                lambda older, newer, candidate: newer.update(upstream_status="removed"),
                "its candidate names this asset as downloaded",
            ),
            (new_revision_failed, "needs a Failure record"),  # superseded rule passes; the failed candidate does not
        ]
        for mutate, fragment in failing:
            with self.subTest(fragment=fragment), self.assertRaises(ValueError) as caught:
                run(mutate)
            self.assertIn(fragment, str(caught.exception))

    def test_a_superseded_asset_is_not_its_own_successor(self):
        """A chain that ends at the superseded asset itself never reaches the asset its candidate names, even when
        the candidate names that very asset. reconcile() refuses such a candidate earlier (a duplicate_bytes
        candidate cannot name its own record), so the asset-level rule is checked here on its own."""
        wikis = copy.deepcopy(self.manifests["wikis"])
        by_id = {asset["id"]: asset for asset in wikis["assets"]}
        old = next(asset for asset in wikis["assets"] if asset.get("upstream_status") == "superseded")
        snaps = self.wiki_snapshots()
        candidate = next(c for c in snaps[old["source_id"]]["candidates"] if c["id"] == old["candidate_id"])
        schema._reconcile_asset(old, candidate, True, by_id)  # the chain ends at the asset the candidate names
        old.pop("superseded_by")  # newest archived revision, successor not archived: the chain ends at itself
        candidate.update(disposition="duplicate_bytes", asset_id=old["id"], reason="identical SHA-256")
        schema.validate_asset(old, "wikis")
        schema.validate_candidate(candidate, old["source_id"])
        with self.assertRaises(ValueError) as caught:
            schema._reconcile_asset(old, candidate, True, by_id)
        self.assertIn(f"asset {old['id']!r}: superseded_by: chain ends at {old['id']!r}", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            schema.reconcile(wikis, snaps)
        self.assertIn("this candidate's own record", str(caught.exception))

    def test_removed_asset_may_leave_complete_discovery(self):
        wikis = copy.deepcopy(self.manifests["wikis"])
        snaps = {sid: copy.deepcopy(snap) for sid, snap in self.discoveries.items() if sid.startswith("wikis:")}
        background = next(
            a for a in wikis["assets"] if a["kind"] == "background" and a.get("upstream_status") == "current"
        )
        snaps["wikis:miraheze"]["candidates"] = [
            c for c in snaps["wikis:miraheze"]["candidates"] if c["id"] != background["candidate_id"]
        ]
        wikis["sources"][1]["candidate_count"] -= 1
        background["upstream_status"] = "removed"  # its superseded predecessor stays archived as well
        schema.validate_manifest(wikis, "wikis")
        schema.reconcile(wikis, snaps)
        background.pop("upstream_status")
        with self.assertRaises(ValueError) as caught:
            schema.reconcile(wikis, snaps)
        self.assertIn("missing from a complete discovery", str(caught.exception))

    def test_song_index_rules(self):
        index = self.song_index
        known = [asset["id"] for value in self.manifests.values() for asset in value["assets"]]
        schema.validate_song_index(index, known)
        schema.validate_song_index(index, iter(known))  # any iterable, consumed once
        cases = [
            (lambda i: i["songs"][0].update(inclusion_evidence=[]), "in_original_game requires positive evidence"),
            (lambda i: i["songs"][0].update(id="song:cosmic"), "id: must be"),
            (lambda i: i["songs"][0].update(id="song:cosmic-pulse-1"), "plus a -N collision suffix"),
            (lambda i: i["songs"][0].update(id="song:cosmic-pulse-01"), "plus a -N collision suffix"),
            (lambda i: i["songs"][0].update(id="song:cosmic-pulse-0"), "plus a -N collision suffix"),
            (lambda i: i["songs"].reverse(), "songs: must be sorted"),
            (
                lambda i: i["disagreements"].append({"song_id": "song:missing", "field": "x", "values": {}}),
                "unknown song",
            ),
            (lambda i: i.update(as_of="2026-13-01"), "as_of"),
        ]
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                record = copy.deepcopy(index)
                mutate(record)
                self.assertInvalid(schema.validate_song_index, record, fragment)
        broken = copy.deepcopy(index)
        broken["songs"][0]["artwork_asset_ids"].append("wikis:miraheze:" + "f" * 16)
        with self.assertRaises(ValueError):
            schema.validate_song_index(broken, known)

    def test_song_ids_follow_the_deterministic_assignment(self):
        """09: -2, -3 suffixes go to colliding slugs in sorted exact-title order (paths.assign_song_ids), so the IDs
        are a function of the titles; a gap, a gratuitous suffix or swapped IDs would renumber linked songs."""
        cosmic = next(song for song in self.song_index["songs"] if song["title"] == "Cosmic Pulse")
        upper = {**cosmic, "title": "COSMIC PULSE"}  # sorts before "Cosmic Pulse", so it takes the base ID

        def with_songs(*songs):
            record = copy.deepcopy(self.song_index)
            others = [song for song in record["songs"] if song["title"] != "Cosmic Pulse"]
            record["songs"] = sorted(others + copy.deepcopy(list(songs)), key=lambda song: song["id"])
            return record

        schema.validate_song_index(
            with_songs({**upper, "id": "song:cosmic-pulse"}, {**cosmic, "id": "song:cosmic-pulse-2"})
        )
        cases = {
            "swapped": with_songs({**upper, "id": "song:cosmic-pulse-2"}, {**cosmic, "id": "song:cosmic-pulse"}),
            "gap": with_songs({**upper, "id": "song:cosmic-pulse"}, {**cosmic, "id": "song:cosmic-pulse-7"}),
            "gratuitous suffix": with_songs({**cosmic, "id": "song:cosmic-pulse-3"}),
        }
        for name, record in cases.items():
            with self.subTest(case=name):
                self.assertInvalid(schema.validate_song_index, record, "id: must be", "sorted exact-title order")

    def test_identical_exact_titles_take_their_ids_in_any_order(self):
        """Two wiki pages can give the same exact title (wikis/songindex._finish breaks the tie by page key, which the
        index does not show). Such a group may hold its IDs in any order, but as a set they are the IDs
        paths.assign_song_ids gives it: the base ID (unless an earlier title took it) and the next free -N suffixes."""
        cosmic = next(song for song in self.song_index["songs"] if song["title"] == "Cosmic Pulse")
        upper = {**cosmic, "title": "COSMIC PULSE", "id": "song:cosmic-pulse"}
        twin = {**cosmic, "composers": ["Another composer (synthetic)"]}  # a second page with the same title

        def with_songs(*songs):
            record = copy.deepcopy(self.song_index)
            others = [song for song in record["songs"] if song["title"] != "Cosmic Pulse"]
            record["songs"] = sorted(others + copy.deepcopy(list(songs)), key=lambda song: song["id"])
            return record

        many = [{**cosmic, "id": "song:cosmic-pulse"}] + [
            {**cosmic, "id": f"song:cosmic-pulse-{number}", "aliases": [f"Twin {number}"]} for number in range(2, 12)
        ]  # sorted by ID, -10 and -11 come before -2
        valid = {
            "base and -2": with_songs({**cosmic, "id": "song:cosmic-pulse"}, {**twin, "id": "song:cosmic-pulse-2"}),
            "-2 and base": with_songs({**cosmic, "id": "song:cosmic-pulse-2"}, {**twin, "id": "song:cosmic-pulse"}),
            "eleven pages": with_songs(*many),
            "after a case variant": with_songs(
                upper, {**cosmic, "id": "song:cosmic-pulse-3"}, {**twin, "id": "song:cosmic-pulse-2"}
            ),
        }
        for name, record in valid.items():
            with self.subTest(valid=name):
                schema.validate_song_index(record)
        invalid = {
            "gap": with_songs({**cosmic, "id": "song:cosmic-pulse"}, {**twin, "id": "song:cosmic-pulse-7"}),
            "no base": with_songs({**cosmic, "id": "song:cosmic-pulse-2"}, {**twin, "id": "song:cosmic-pulse-3"}),
            "base taken by the case variant": with_songs(
                {**upper, "id": "song:cosmic-pulse-3"},
                {**cosmic, "id": "song:cosmic-pulse"},
                {**twin, "id": "song:cosmic-pulse-2"},
            ),
        }
        for name, record in invalid.items():
            with self.subTest(invalid=name):
                self.assertInvalid(schema.validate_song_index, record, "id: must be", "sorted exact-title order")

    def test_song_links(self):
        assets = [asset for value in self.manifests.values() for asset in value["assets"]]
        schema.validate_song_links(self.song_index, assets)
        kousa = copy.deepcopy(next(a for a in assets if a["song_titles"] == ["交差して歪む過去と未来"]))
        cases = [
            ({**kousa, "song_ids": ["song:file"]}, "is not in the song index"),  # the pre-hash, set-dependent ID
            ({**kousa, "song_ids": ["song:cosmic-pulse"]}, "is not among song_titles"),
        ]
        for asset, fragment in cases:
            with self.subTest(fragment=fragment):
                with self.assertRaises(ValueError) as caught:
                    schema.validate_song_links(self.song_index, [asset])
                self.assertIn(fragment, str(caught.exception))
        aliased = copy.deepcopy(self.song_index)
        song = next(s for s in aliased["songs"] if s["title"] == "交差して歪む過去と未来")
        song["title"], song["aliases"] = "Kousa (romanized, synthetic)", ["交差して歪む過去と未来"]
        schema.validate_song_links(aliased, [kousa])


class CatalogValidationTests(unittest.TestCase):
    def setUp(self):
        manifests, _, _ = load_fixture()
        self.catalog = build_catalog(manifests)

    def test_contract_catalog_is_valid(self):
        schema.validate_catalog(self.catalog)
        summary = self.catalog["summary"]
        self.assertEqual(summary["exact_duplicate_records"], 1)  # Miraheze and Fandom share the Cosmic Pulse bytes
        self.assertEqual(summary["slideshow_images"], 1)  # only the 1200 px artist original
        cosmic = next(
            a
            for a in self.catalog["assets"]
            if a["source_id"] == "wikis:miraheze" and a["kind"] == "song_art" and a["title"] == "Cosmic Pulse"
        )
        self.assertEqual([p["source_id"] for p in cosmic["provenance"]], ["wikis:miraheze", "wikis:fandom"])
        self.assertEqual(len(cosmic["variants"]), 2)

    def test_provenance_sort_key_follows_the_frozen_primary_rule(self):
        """09 "Catalog presentation rules", Primary record: family priority; wikis:miraheze, then wikis:fandom, then
        every other source, tied; quality.source_variant; source ID; asset ID."""

        def record(source_id, variant="site_original", asset_id="x"):
            return {
                "source_id": source_id,
                "asset_id": f"{source_id}:{asset_id}",
                "quality": {"source_variant": variant},
            }

        ordered = [
            record("artists:x:painter", "preview"),
            record("official:x"),
            record("official:facebook", "preview"),
            record("official:c4cat", "unknown"),
            record("wikis:miraheze", "unknown"),
            record("wikis:fandom"),
            record("wikis:atwiki-ja"),
            record("wikis:rhythmgame-ja"),
            record("wikis:atwiki-ja", "preview"),
            record("archives:wayback"),
        ]
        self.assertEqual(sorted(reversed(ordered), key=schema.provenance_sort_key), ordered)

    def test_credit_evidence_names_a_credited_name_of_its_role(self):
        """DX2-07: composer and illustrator are separate roles, and the viewer shows a record's credit_evidence beside
        its role lists (src/archive.js provenance), so in the catalog an illustrator or composer entry names a member
        of the matching list of its own provenance record (a name the record does not credit, or a role swap, is
        refused), and a catalog asset's entries name members of its merged lists. A role 'other' entry is not tied to
        a list. Committed data and every fixture satisfy the rule."""
        url = "https://x.com/ariiol_/status/1000000000000000003"

        def entry(role, name, quote="Yunosuke"):
            return {"role": role, "name": name, "url": url, "field": "post text", "quote": quote}

        def cosmic(catalog):
            return next(
                a
                for a in catalog["assets"]
                if a["source_id"] == "wikis:miraheze" and a["kind"] == "song_art" and a["title"] == "Cosmic Pulse"
            )

        base = cosmic(self.catalog)
        [primary, fandom] = base["provenance"]
        for record in (base, primary, fandom):
            self.assertEqual((record["illustrators"], record["composers"]), (["ariiol"], ["Yunosuke"]))
        refused = {
            "a composer the record does not credit": (
                1,
                [entry("composer", "Somebody Else")],
                f"provenance: {fandom['asset_id']!r} credit_evidence: [0] names composer 'Somebody Else', "
                "who is not in composers",
            ),
            "a role swap": (
                0,
                [entry("illustrator", "Yunosuke")],
                f"provenance: {primary['asset_id']!r} credit_evidence: [0] names illustrator 'Yunosuke', "
                "who is not in illustrators",
            ),
            "the second entry": (
                1,
                [entry("illustrator", "ariiol"), entry("composer", "ariiol")],
                "credit_evidence: [1] names composer 'ariiol', who is not in composers",
            ),
            "the catalog asset's own entries": (
                None,
                [entry("composer", "Somebody Else")],
                f"catalog asset {base['id']!r}: credit_evidence: [0] names composer 'Somebody Else'",
            ),
        }
        for case, (index, evidence, fragment) in refused.items():
            with self.subTest(case=case):
                catalog = copy.deepcopy(self.catalog)
                target = cosmic(catalog) if index is None else cosmic(catalog)["provenance"][index]
                target["credit_evidence"] = evidence
                with self.assertRaises(ValueError) as caught:
                    schema.validate_catalog(catalog)
                self.assertIn(fragment, str(caught.exception))
        accepted = [
            [entry("illustrator", "ariiol"), entry("composer", "Yunosuke")],
            [entry("illustrator", "ariiol", quote=None)],
            [entry("other", "Fixture Designer")],  # role other: no role list
            [],
            None,
        ]
        for evidence in accepted:
            with self.subTest(accepted=evidence):
                catalog = copy.deepcopy(self.catalog)
                cosmic(catalog)["provenance"][1]["credit_evidence"] = evidence
                if evidence is not None:
                    cosmic(catalog)["credit_evidence"] = evidence
                schema.validate_catalog(catalog)

    def test_every_catalog_credit_entry_cites_a_url_or_a_quote(self):
        """DX2-07: a primary family may record a credit without a source URL or quote (its own --verify reports it),
        but the published catalog refuses such an entry, on the catalog asset and on every provenance record."""
        url = "https://x.com/ariiol_/status/1000000000000000003"

        def cosmic(catalog):
            return next(
                a
                for a in catalog["assets"]
                if a["source_id"] == "wikis:miraheze" and a["kind"] == "song_art" and a["title"] == "Cosmic Pulse"
            )

        def entry(url=None, quote=None):
            return {"role": "illustrator", "name": "ariiol", "url": url, "field": None, "quote": quote}

        base = cosmic(self.catalog)
        fandom = base["provenance"][1]["asset_id"]
        for case, evidence in {"neither": [entry()], "an empty quote": [entry(quote=" ")]}.items():
            for index, fragment in (
                (None, f"catalog asset {base['id']!r}: credit_evidence: [0] (illustrator 'ariiol') cites no url"),
                (1, f"provenance: {fandom!r} credit_evidence: [0] (illustrator 'ariiol') cites no url and no quote"),
            ):
                with self.subTest(case=case, provenance=index):
                    catalog = copy.deepcopy(self.catalog)
                    target = cosmic(catalog) if index is None else cosmic(catalog)["provenance"][index]
                    target["credit_evidence"] = copy.deepcopy(evidence)
                    with self.assertRaises(ValueError) as caught:
                        schema.validate_catalog(catalog)
                    self.assertIn(fragment, str(caught.exception))
        for evidence in ([entry(url=url)], [entry(quote="Illustration: ariiol")], [entry(url, "ariiol")]):
            with self.subTest(accepted=evidence):
                catalog = copy.deepcopy(self.catalog)
                cosmic(catalog)["credit_evidence"] = copy.deepcopy(evidence)
                cosmic(catalog)["provenance"][1]["credit_evidence"] = copy.deepcopy(evidence)
                schema.validate_catalog(catalog)

    def test_catalog_mutations_are_rejected(self):
        def first(catalog, **match):
            return next(a for a in catalog["assets"] if all(a[k] == v for k, v in match.items()))

        cases = [
            (lambda c: c["summary"].update(unique_files=99), "unique_files"),
            (lambda c: c["summary"]["by_kind"].update(song_art=0), "by_kind"),
            (lambda c: c["assets"].reverse(), "must be ordered"),
            (lambda c: first(c, kind="background").update(gallery=False), "gallery: must be True"),
            (lambda c: first(c, kind="reference").update(site_original=True), "site_original"),
            (lambda c: first(c, kind="reference").update(thumb="assets/thumbs/x.webp"), "thumb"),
            (lambda c: first(c, kind="background").update(archive_url="https://example.com/x"), "archive_url"),
            (lambda c: first(c, source_id="wikis:miraheze", title="Cosmic Pulse").update(variants=[]), "variants"),
            (
                lambda c: first(c, source_id="wikis:miraheze", title="Cosmic Pulse")["provenance"].reverse(),
                "provenance",
            ),
            (lambda c: first(c, kind="background").update(illustrators=["someone"]), "ordered union"),
            (
                lambda c: first(c, source_id="wikis:miraheze", title="Cosmic Pulse").update(song_ids=[]),
                "song_ids: must be the ordered union",
            ),
            (
                lambda c: first(c, source_id="wikis:miraheze", title="Cosmic Pulse").update(collections=[]),
                "collections: must be the ordered union",
            ),
            (
                lambda c: first(c, source_id="wikis:miraheze", title="Cosmic Pulse")["provenance"][0].pop("song_ids"),
                "missing key 'song_ids'",
            ),
            (  # DX2-07: a provenance record's credit evidence names a credited name of its role, as a record's does
                lambda c: first(c, source_id="wikis:miraheze", title="Cosmic Pulse")["provenance"][1].update(
                    credit_evidence=[
                        {"role": "composer", "name": "Somebody Else", "url": None, "field": None, "quote": "x"}
                    ]
                ),
                "provenance: 'wikis:fandom:d3ae3e8c82b4367e' credit_evidence: [0] names composer 'Somebody Else'",
            ),
            (
                lambda c: c["sources"].append({**c["sources"][0], "id": "archives:miraheze", "family": "archives"}),
                "share the source slug",
            ),
            (lambda c: c.update(owner="mashirozx"), "owner"),
            (lambda c: c["snapshots"].update(legacy=None), "snapshots"),
            (lambda c: c["sources"].append(copy.deepcopy(c["sources"][0])), "duplicate source"),
        ]
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                catalog = copy.deepcopy(self.catalog)
                mutate(catalog)
                with self.assertRaises(ValueError) as caught:
                    schema.validate_catalog(catalog)
                self.assertIn(fragment, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
