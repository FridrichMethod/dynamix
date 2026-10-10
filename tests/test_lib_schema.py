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

from lib import manifest, schema  # noqa: E402

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
    records = sorted((asset | {"family": family} for family, value in manifests.items() for asset in value["assets"]),
                     key=schema.provenance_sort_key)
    names = {source["id"]: source["name"] for value in manifests.values() for source in value["sources"]}
    groups = {}
    for record in records:
        groups.setdefault(record["sha256"], []).append(record)
    assets = []
    for group in groups.values():
        primary = {key: value for key, value in group[0].items() if key != "family"}
        provenance = [{"source_id": r["source_id"], "source_name": names[r["source_id"]], "family": r["family"],
                       **{key: r.get(key) for key in schema.PROVENANCE_FIELDS[3:]}, "asset_id": r["id"]}
                      for r in group]
        entry = {**primary, **{field: schema.ordered_union(r[field] for r in group)
                               for field in ("illustrators", "composers", "song_titles", "song_ids", "collections")}}
        url = schema.catalog_url(primary["path"])
        entry.update({"family": group[0]["family"], "source_name": names[primary["source_id"]], "url": url,
                      "thumb": schema.thumb_relpath(primary["sha256"]) if schema.is_gallery(primary) else None,
                      "gallery": schema.is_gallery(primary), "slideshow": schema.is_slideshow(primary),
                      "site_original": schema.tier_for(primary["kind"], primary["format"]) == "site",
                      "archive_url": schema.ARCHIVE_URL_PREFIX + url, "provenance": provenance, "variants": []})
        assets.append(schema.canonical(entry, "catalog_asset"))
    for entry in assets:
        group = entry.get("variant_group")
        entry["variants"] = sorted(other["id"] for other in assets
                                   if group and other.get("variant_group") == group and other is not entry)
    assets.sort(key=schema.catalog_sort_key)
    sources = [{**source, "asset_count": sum(r["source_id"] == source["id"] for r in records)}
               for value in manifests.values() for source in value["sources"]]
    failures = [failure for value in manifests.values() for failure in value["failures"]]
    site = sum(entry["bytes"] for entry in assets if entry["site_original"])
    total = sum(entry["bytes"] for entry in assets)
    by_family, by_kind = {}, {}
    for record in records:
        by_family[record["family"]] = by_family.get(record["family"], 0) + 1
    for entry in assets:
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
    return {"schema_version": 1, "owner": schema.OWNER, "game": schema.CATALOG_GAME, "repository": schema.REPOSITORY,
            "snapshots": {family: value["fetched_at"] for family, value in manifests.items()},
            "sources": sources, "assets": assets, "failures": failures,
            "summary": {"source_count": len(sources), "source_asset_records": len(records),
                        "unique_files": len(assets), "gallery_images": sum(a["gallery"] for a in assets),
                        "slideshow_images": sum(a["slideshow"] for a in assets),
                        "exact_duplicate_records": len(records) - len(assets),
                        "by_family": {family: by_family.get(family, 0) for family in schema.FAMILIES},
                        "by_kind": by_kind, "failure_count": len(failures), "downloaded_bytes": total,
                        "site_original_bytes": site, "archive_only_bytes": total - site}}


class EnumerationTests(unittest.TestCase):
    """Guards against accidental edits: these literals are copied from 09-frozen-contracts.md."""

    def test_contract_enumerations(self):
        self.assertEqual(schema.KINDS, ("song_art", "pack_cover", "character", "background", "part", "promotional",
                                        "store_screenshot", "gameplay_screenshot", "process", "reference"))
        self.assertEqual(set(schema.SITE_KINDS) | set(schema.ARCHIVE_KINDS), set(schema.KINDS))
        self.assertFalse(set(schema.SITE_KINDS) & set(schema.ARCHIVE_KINDS))
        self.assertEqual(schema.FAMILY_PRIORITY, ("artists", "official", "wikis", "archives"))
        self.assertEqual(schema.DISPOSITIONS, ("downloaded", "duplicate_bytes", "excluded", "failed", "blocked",
                                               "pending"))
        self.assertEqual(len(schema.EXCLUSION_REASONS), 13)
        self.assertEqual(schema.ERROR_CLASSES, ("http_4xx", "http_5xx", "timeout", "blocked", "challenge",
                                                "decode_error", "size_limit", "host_not_allowed", "other"))
        self.assertEqual(schema.UPSTREAM_STATUSES, ("current", "removed", "superseded", "unknown"))
        self.assertEqual(schema.MAPPING_STATUSES, ("confirmed_primary", "confirmed_wiki", "inferred", "unmapped",
                                                   "conflict"))
        for source_id in schema.REGISTERED_SOURCE_IDS + ("artists:x:ariiol_", "artists:artstation:sandgodoven"):
            self.assertRegex(source_id, schema.SOURCE_ID_RE)
        for bad in ("legacy:x", "wikis:Mira", "wikis:a_b", "artists:x", "artists:x:-bad", "wikis:x:y"):
            self.assertNotRegex(bad, schema.SOURCE_ID_RE)

    def test_trailing_newlines_are_rejected(self):
        manifests, _, _ = load_fixture()
        asset = asset_by_title(manifests, "Cosmic Pulse (crop)")
        for field in ("source_id", "sha256", "sha1", "fetched_at", "id"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                schema.validate_asset({**asset, field: asset[field] + "\n"})

    def test_tiers_gallery_and_slideshow(self):
        self.assertEqual(schema.tier_for("song_art", "PNG"), "site")
        self.assertEqual(schema.tier_for("store_screenshot", "JPEG"), "site")
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
        reported = schema.make_source_reported(mime="image/png; charset=binary", bytes=10, width=2, height=3,
                                               sha1="A" * 40)
        self.assertEqual(schema.compute_comparisons(reported, observed),
                         {"sha1_match": True, "bytes_match": True, "dimensions_match": True, "format_match": False})
        self.assertEqual(schema.compute_comparisons(schema.make_source_reported(), observed),
                         dict.fromkeys(schema.COMPARISON_FIELDS))
        self.assertEqual(schema.compute_comparisons(schema.make_source_reported(mime="image/jpg"),
                                                    {**observed, "sniffed_format": "JPEG"})["format_match"], True)
        with self.assertRaises(ValueError):
            schema.make_source_reported(size=3)

    def test_redaction(self):
        self.assertEqual(schema.redact_url("https://u:pw@example.com/a?token=abc&format=original&Sig=x#access_token=y"),
                         "https://example.com/a?token=REDACTED&format=original&Sig=REDACTED#access_token=REDACTED")
        clean = "https://static.wikia.nocookie.net/dynamix/images/a/a1/X.png/revision/latest?cb=1&format=original"
        self.assertEqual(schema.redact_url(clean), clean)
        text = "GET https://a.example/x?api_key=zz -> 403; Cookie: sid=1; Authorization: Bearer abc"
        redacted = schema.redact_text(text)
        for secret in ("zz", "sid=1", "abc"):
            self.assertNotIn(secret, redacted)
        self.assertTrue(schema.contains_credentials(text))
        self.assertFalse(schema.contains_credentials("basic illustration of the bearer of news"))

    def test_canonical_orders_keys_without_mutation(self):
        manifests, _, _ = load_fixture()
        asset = asset_by_title(manifests, "Cosmic Pulse (crop)")
        shuffled = dict(reversed(list(asset.items())))
        shuffled["quality"] = dict(reversed(list(asset["quality"].items())))
        before = copy.deepcopy(shuffled)
        ordered = schema.canonical(shuffled, "asset")
        self.assertEqual(list(ordered), [key for key in schema.ASSET_FIELDS + schema.ASSET_OPTIONAL_FIELDS
                                         if key in asset])
        self.assertEqual(list(ordered["quality"]), list(schema.QUALITY_FIELDS))
        self.assertEqual(shuffled, before)

    def test_failure_record_redacts_and_validates(self):
        record = schema.failure_record(source_id="wikis:fandom", candidate_id="wikis:fandom:File:X.png",
                                       phase="download", url="https://user:pw@dynamix.fandom.com/x?token=t",
                                       attempted_at="2026-10-10T12:00:00Z", attempts=1, error_class="blocked",
                                       http_status=403, retriable=False, manual=True,
                                       message="denied for https://dynamix.fandom.com/x?token=t")
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
        for source_id, snapshot in self.discoveries.items():
            counts = schema.validate_discovery(snapshot, source_id)
            self.assertEqual(sum(counts.values()), len(snapshot["candidates"]))
        schema.validate_song_index(self.song_index)

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
            (crop, lambda r: r["comparisons"].update(sha1_match=None), "comparisons: must be"),
            (crop, lambda r: r["source_reported"].update(sha1="b" * 40), "comparisons: must be"),
            (crop, lambda r: r.update(mapping_evidence=[]), "mapping_evidence: must not be empty"),
            (crop, lambda r: r.update(mapping_status="unmapped"), "song_ids: must be empty"),
            (crop, lambda r: r.update(upstream_status="superseded"), "superseded_by"),
            (crop, lambda r: r.update(superseded_by=r["id"], upstream_status="superseded"), "must name another"),
            (crop, lambda r: r.pop("variant_group"), "variant_role: requires variant_group"),
            (crop, lambda r: r.update(game_associations=["arcaea"]), "game_associations: must include"),
            (crop, lambda r: r["quality"].update(origin_status="artist"), "quality: origin_status"),
            (crop, lambda r: r["rights"].update(license=""), "rights: license"),
            (crop, lambda r: r.update(credit_evidence=[{"role": "artist", "name": "x", "url": None, "field": None,
                                                        "quote": None}]), "credit_evidence"),
        ]
        for base, mutate, fragment in mutations:
            with self.subTest(fragment=fragment):
                record = copy.deepcopy(base)
                mutate(record)
                self.assertInvalid(schema.validate_asset, record, fragment)

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
        self.assertInvalid(schema.validate_failure, {**failure, "id": failure["id"] + "x"}, "id: must be")
        self.assertInvalid(schema.validate_failure, {**failure, "error_class": "http_4xx"}, "http_status")
        self.assertInvalid(schema.validate_failure, {**failure, "error_class": "blocked"}, "retriable: must be false")
        self.assertInvalid(schema.validate_failure, {**failure, "attempts": 0}, "attempts:")
        self.assertInvalid(schema.validate_failure, {**failure, "message": "Cookie: a=b"}, "message:")
        self.assertInvalid(schema.validate_failure, {**failure, "phase": "upload"}, "phase:")

    def test_candidates(self):
        snapshot = self.discoveries["wikis:miraheze"]
        by_disposition = {c["disposition"]: c for c in snapshot["candidates"]}
        downloaded, excluded = copy.deepcopy(by_disposition["downloaded"]), copy.deepcopy(by_disposition["excluded"])
        cases = [
            ({**downloaded, "asset_id": None}, "asset_id: is required"),
            ({**downloaded, "game": "unknown"}, "game: must be 'dynamix' or 'mixed'"),
            ({**downloaded, "asset_id": "wikis:fandom:" + "0" * 16}, "same source"),
            ({**downloaded, "id": downloaded["id"] + "x"}, "id: must be"),
            ({**downloaded, "upstream_key": "File:A b.png", "id": "wikis:miraheze:File:A b.png"}, "upstream_key"),
            ({**excluded, "reason": "too small"}, "reason: must start with an exclusion reason"),
            ({**excluded, "reason": None}, "reason: is required"),
            ({**excluded, "asset_id": downloaded["asset_id"]}, "asset_id: must be absent"),
            ({**downloaded, "disposition": "pending", "reason": "x"}, "asset_id: must be absent"),
            ({**downloaded, "role": "fanart"}, "role:"),
        ]
        universe = next(c for c in snapshot["candidates"] if (c.get("reason") or "").startswith("universe_only"))
        cases.append(({**universe, "game": "dynamix"}, "game: must be 'dynamix_universe'"))
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
        self.assertEqual(schema.validate_discovery(self.discoveries["wikis:miraheze"]),
                         {"downloaded": 4, "duplicate_bytes": 1, "excluded": 2, "failed": 0, "blocked": 0,
                          "pending": 1})

    def test_manifest_rules(self):
        wikis = self.manifests["wikis"]
        cases = [
            (lambda m: m["assets"].reverse(), "assets: must be sorted"),
            (lambda m: m["assets"].append(copy.deepcopy(m["assets"][-1])), "duplicate id"),
            (lambda m: m.update(fetched_at="2026-10-09T00:00:00Z"), "fetched_at: must be the latest"),
            (lambda m: m.update(fetched_at=None), "fetched_at"),
            (lambda m: m["sources"].pop(0), "is not a source of"),
            (lambda m: m.update(family="artists"), "belongs to family 'wikis'"),
            (lambda m: m.update(extra=1), "extra: unknown field"),
            (lambda m: m.update(schema_version=2), "schema_version"),
            (lambda m: m["assets"][0].update(upstream_status="superseded", superseded_by="wikis:fandom:" + "e" * 16),
             "superseded_by: names no asset"),
        ]
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                record = copy.deepcopy(wikis)
                mutate(record)
                self.assertInvalid(schema.validate_manifest, record, fragment)

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

        cases = [
            (lambda m, s: m["sources"][1].update(candidate_count=99), "candidate_count"),
            (lambda m, s: m["sources"][0].update(discovery_complete=True), "discovery_complete"),
            (lambda m, s: candidate(s, "wikis:miraheze", "downloaded").update(asset_id="wikis:miraheze:" + "0" * 16),
             "not an asset recorded"),
            (lambda m, s: candidate(s, "wikis:miraheze", "downloaded").update(role="background"), "role"),
            (lambda m, s: candidate(s, "wikis:miraheze", "duplicate_bytes").update(
                asset_id="wikis:miraheze:" + "1" * 16), "names no recorded asset"),
            (lambda m, s: m.update(failures=[]) or m["sources"][0].pop("failure_count"), "needs a Failure record"),
            (lambda m, s: s["wikis:miraheze"]["candidates"].remove(candidate(s, "wikis:miraheze", "downloaded"))
             or m["sources"][1].update(candidate_count=7), "missing from a complete discovery"),
            (lambda m, s: candidate(s, "wikis:fandom", "failed").update(disposition="pending"), "not failed/blocked"),
        ]
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                wikis, snaps = broken(mutate)
                with self.assertRaises(ValueError) as caught:
                    schema.reconcile(wikis, snaps, other_assets=all_assets)
                self.assertIn(fragment, str(caught.exception))

    def test_removed_asset_may_leave_complete_discovery(self):
        wikis = copy.deepcopy(self.manifests["wikis"])
        snaps = {sid: copy.deepcopy(snap) for sid, snap in self.discoveries.items() if sid.startswith("wikis:")}
        background = next(a for a in wikis["assets"] if a["kind"] == "background")
        snaps["wikis:miraheze"]["candidates"] = [c for c in snaps["wikis:miraheze"]["candidates"]
                                                 if c["id"] != background["candidate_id"]]
        wikis["sources"][1]["candidate_count"] -= 1
        background["upstream_status"] = "removed"
        schema.validate_manifest(wikis, "wikis")
        schema.reconcile(wikis, snaps)

    def test_song_index_rules(self):
        index = self.song_index
        known = [asset["id"] for value in self.manifests.values() for asset in value["assets"]]
        schema.validate_song_index(index, known)
        schema.validate_song_index(index, iter(known))  # any iterable, consumed once
        cases = [
            (lambda i: i["songs"][0].update(inclusion_evidence=[]), "in_original_game requires positive evidence"),
            (lambda i: i["songs"][0].update(id="song:cosmic"), "id: must be"),
            (lambda i: i["songs"].reverse(), "songs: must be sorted"),
            (lambda i: i["disagreements"].append({"song_id": "song:missing", "field": "x", "values": {}}),
             "unknown song"),
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


class CatalogValidationTests(unittest.TestCase):
    def setUp(self):
        manifests, _, _ = load_fixture()
        self.catalog = build_catalog(manifests)

    def test_contract_catalog_is_valid(self):
        schema.validate_catalog(self.catalog)
        summary = self.catalog["summary"]
        self.assertEqual(summary["exact_duplicate_records"], 1)  # Miraheze and Fandom share the Cosmic Pulse bytes
        self.assertEqual(summary["slideshow_images"], 1)  # only the 1200 px artist original
        cosmic = next(a for a in self.catalog["assets"] if a["source_id"] == "wikis:fandom" and a["kind"] == "song_art"
                      and a["title"] == "Cosmic Pulse")
        self.assertEqual([p["source_id"] for p in cosmic["provenance"]], ["wikis:fandom", "wikis:miraheze"])
        self.assertEqual(len(cosmic["variants"]), 2)

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
            (lambda c: first(c, source_id="wikis:fandom", title="Cosmic Pulse").update(variants=[]), "variants"),
            (lambda c: first(c, source_id="wikis:fandom", title="Cosmic Pulse")["provenance"].reverse(), "provenance"),
            (lambda c: first(c, kind="background").update(illustrators=["someone"]), "ordered union"),
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
