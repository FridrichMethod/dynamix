"""scripts/build_thumbnails.py: deterministic WebP previews, colour handling, drift detection, collision/overwrite
refusal, manifest-limited pruning that never touches originals, and the catalog hook.

Adapted from FridrichMethod/deemo tests/test_thumbnails.py (Apache-2.0, commit
b14bf0d5f5a05e5382c319f3fd5019a4936820b8) for the Dynamix contract; runs on a temporary copy of
tests/fixtures/contract plus synthetic originals.
"""

import contextlib
import hashlib
import io
import json
import re
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures/catalog"))

import catalog_fixture as fx  # noqa: E402
import build_catalog  # noqa: E402
import build_coverage  # noqa: E402
import build_thumbnails as thumbs  # noqa: E402
from lib import manifest  # noqa: E402

SOURCE = "artists:x:fixture_thumbs"
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
# Originals of the contract fixture that need a preview: the 1200 px artist original, the 600 px wiki cover (one
# entry for the Miraheze and Fandom copies), the 640 px background and the 320 px archive-tier screenshot.
CONTRACT_PREVIEWS = {"FIXTUREcosmic.png": (480, 480), "Cosmic Pulse.png": (480, 480),
                     "Dynamix background 03.png": (480, 270), "press-kit-2014-screenshot.png": (320, 180)}


def grey_profile(gamma):
    """A minimal ICC v2 greyscale display profile (a gamma curve and a D50 white point)."""
    def xyz(x, y, z):
        return b"XYZ " + bytes(4) + struct.pack(">3i", *(round(value * 65536) for value in (x, y, z)))
    tags = [(b"kTRC", b"curv" + bytes(4) + struct.pack(">IH", 1, round(gamma * 256))),
            (b"wtpt", xyz(0.9642, 1.0, 0.8249))]
    offset, table, body = 128 + 4 + 12 * len(tags), b"", b""
    for signature, data in tags:
        table += signature + struct.pack(">II", offset + len(body), len(data))
        body += data + bytes(-len(data) % 4)
    header = (struct.pack(">I", offset + len(body)) + bytes(4) + bytes([2, 0x10, 0, 0]) + b"mntrGRAYXYZ " + bytes(12)
              + b"acsp" + bytes(28) + xyz(0.9642, 1.0, 0.8249)[8:] + bytes(48))
    return header + struct.pack(">I", len(tags)) + table + body


def cmyk_profile(lightness):
    """A minimal ICC v2 CMYK output profile: an 8-bit lookup table (2 grid points per ink) to neutral Lab, L* =
    ``lightness`` without black ink and 0 with full black. Its black point is L* 0, so no black-point scaling."""
    def xyz(x, y, z):
        return b"XYZ " + bytes(4) + struct.pack(">3i", *(round(value * 65536) for value in (x, y, z)))
    identity, matrix = bytes(range(256)), struct.pack(">9i", 65536, 0, 0, 0, 65536, 0, 0, 0, 65536)
    nodes = b"".join(bytes([0 if index & 1 else round(lightness * 255 / 100), 128, 128]) for index in range(16))
    table = b"mft1" + bytes(4) + bytes([4, 3, 2, 0]) + matrix + identity * 4 + nodes + identity * 3
    tags = [(b"A2B0", table), (b"wtpt", xyz(0.9642, 1.0, 0.8249))]
    offset, directory, body = 128 + 4 + 12 * len(tags), b"", b""
    for signature, data in tags:
        directory += signature + struct.pack(">II", offset + len(body), len(data))
        body += data + bytes(-len(data) % 4)
    header = (struct.pack(">I", offset + len(body)) + bytes(4) + bytes([2, 0x10, 0, 0]) + b"prtrCMYKLab "
              + bytes(12) + b"acsp" + bytes(28) + xyz(0.9642, 1.0, 0.8249)[8:] + bytes(48))
    return header + struct.pack(">I", len(tags)) + directory + body


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Fixture(unittest.TestCase):
    """The contract fixture plus one artist source with large, cut-out, photo, small and animated originals."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = fx.copy_contract(self.base / "checkout")
        self.names = {}
        self.add("wide.png", fx.encode(fx.pattern((1000, 600)), "PNG"))
        self.add("cutout.png", fx.encode(fx.pattern((700, 900), "RGBA"), "PNG"))
        exif = Image.Exif()
        exif[0x010E] = "fixture description"  # must not reach the preview; the colour profile must
        self.add("photo.jpg", fx.encode(fx.pattern((800, 800)), "JPEG", quality=90, exif=exif, icc_profile=SRGB))
        self.add("small.png", fx.encode(fx.pattern((300, 200)), "PNG"))
        self.add("anim.gif", fx.animated_gif((600, 400), [(255, 0, 0), (0, 255, 0), (0, 0, 255)]), kind="character")
        for name in CONTRACT_PREVIEWS:  # the current background, not its superseded 480 px predecessor
            extra = {"upstream_status": "current"} if name.startswith("Dynamix background") else {}
            self.names[name] = fx.find_record(self.root, original_filename=name, **extra)

    def add(self, name, data, **fields):
        record = fx.add_record(self.root, fx.make_record(self.root, SOURCE, data, name, **fields))
        self.names[name] = record
        return record

    def sha(self, name):
        return self.names[name]["sha256"]

    def manifest(self, root=None):
        return json.loads(((root or self.root) / thumbs.MANIFEST).read_text(encoding="utf-8"))

    def write_manifest(self, value):
        (self.root / thumbs.MANIFEST).write_text(json.dumps(value), encoding="utf-8")

    def files(self, root=None):
        directory = (root or self.root) / thumbs.THUMB_DIR
        return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}

    def preview(self, name):
        return self.root / thumbs.thumb_path(self.sha(name))

    def drop(self, name):
        """Remove an original's record from its manifest (the file stays, as an upstream removal would not)."""
        record = self.names[name]
        family = record["source_id"].split(":", 1)[0]
        value = fx.load_manifest(self.root, family)
        value["assets"] = [item for item in value["assets"] if item["id"] != record["id"]]
        for source in value["sources"]:
            if source["id"] == record["source_id"] and "asset_count" in source:
                source["asset_count"] -= 1
        fx.save_manifest(self.root, family, value)
        (self.root / record["path"]).unlink()


class BuildTests(Fixture):
    def test_builds_are_byte_identical_across_checkouts_and_reruns(self):
        other = self.base / "other"
        shutil.copytree(self.root, other)
        self.assertEqual(thumbs.build(self.root)["rendered"], 8)
        thumbs.build(other)
        first = self.files()
        self.assertEqual(first, self.files(other))
        self.assertEqual((self.root / thumbs.MANIFEST).read_bytes(), (other / thumbs.MANIFEST).read_bytes())
        before = {path: path.stat().st_mtime_ns for path in (self.root / thumbs.THUMB_DIR).iterdir()}
        manifest_time = (self.root / thumbs.MANIFEST).stat().st_mtime_ns
        self.assertEqual(thumbs.build(self.root)["rendered"], 0)
        self.assertEqual({path: path.stat().st_mtime_ns for path in before}, before)
        self.assertEqual((self.root / thumbs.MANIFEST).stat().st_mtime_ns, manifest_time)
        shutil.rmtree(self.root / thumbs.THUMB_DIR)
        self.assertEqual(thumbs.build(self.root)["rendered"], 8)
        self.assertEqual(self.files(), first)
        self.assertEqual(thumbs.problems(self.root, reencode=True), [])

    def test_a_preview_that_no_longer_matches_the_manifest_is_rendered_again(self):
        thumbs.build(self.root)
        first, listing = self.files(), (self.root / thumbs.MANIFEST).read_bytes()
        damaged = bytearray(self.preview("wide.png").read_bytes())
        damaged[len(damaged) // 2] ^= 0xFF
        self.preview("wide.png").write_bytes(bytes(damaged))
        summary = thumbs.build(self.root)
        self.assertEqual((summary["rendered"], summary["reused"]), (1, 7))
        self.assertEqual(self.files(), first)
        self.assertEqual((self.root / thumbs.MANIFEST).read_bytes(), listing)
        self.assertEqual(thumbs.problems(self.root), [])

    def test_previews_are_small_metadata_free_webp_files(self):
        thumbs.build(self.root)
        entries = self.manifest()["thumbnails"]
        expected = {"wide.png": (480, 288), "cutout.png": (373, 480), "photo.jpg": (480, 480),
                    "anim.gif": (480, 320), **CONTRACT_PREVIEWS}
        self.assertEqual(set(entries), {self.sha(name) for name in expected})
        for name, size in expected.items():
            with self.subTest(name=name):
                entry = entries[self.sha(name)]
                self.assertEqual(entry["path"], f"assets/thumbs/{self.sha(name)[:16]}.webp")
                path = self.root / entry["path"]
                self.assertEqual((entry["sha256"], entry["bytes"]), (digest(path), path.stat().st_size))
                with Image.open(path) as image:
                    self.assertEqual((image.format, image.size), ("WEBP", size))
                    self.assertEqual([entry["width"], entry["height"]], list(size))
                    self.assertNotIn("exif", image.info)
                    self.assertNotIn("xmp", image.info)
                    self.assertEqual(image.info.get("icc_profile"), SRGB if name == "photo.jpg" else None)
                    if name == "cutout.png":
                        self.assertEqual(image.mode, "RGBA")
                        self.assertEqual(image.getpixel((5, 240))[3], 0)
                        self.assertEqual(image.getpixel((370, 240))[3], 255)
        encoder = self.manifest()["encoder"]
        self.assertEqual({key: encoder[key] for key in ("format", "long_edge", "quality", "method")},
                         {"format": "WEBP", "long_edge": 480, "quality": 75, "method": 6})
        self.assertEqual(set(encoder), {"format", "long_edge", "quality", "method", "pillow", "libwebp"})

    def test_small_site_images_and_pdfs_get_no_preview_but_archive_rasters_do(self):
        thumbs.build(self.root)
        entries = self.manifest()["thumbnails"]
        for match in ({"original_filename": "small.png"}, {"format": "PDF"}, {"title": "交差して歪む過去と未来"},
                      {"format": "GIF", "source_id": "artists:x:ariiol_"}):
            with self.subTest(match=match):
                self.assertNotIn(fx.find_record(self.root, **match)["sha256"], entries)
        shot = fx.find_record(self.root, kind="gameplay_screenshot")
        self.assertEqual((entries[shot["sha256"]]["width"], entries[shot["sha256"]]["height"]),
                         (shot["width"], shot["height"]), "an archive-tier preview is never enlarged")

    def test_animated_gif_previews_its_first_frame(self):
        thumbs.build(self.root)
        with Image.open(self.preview("anim.gif")) as image:
            self.assertFalse(getattr(image, "is_animated", False))
            red, green, blue = image.convert("RGB").getpixel((240, 160))
        self.assertTrue(red > 200 and green < 60 and blue < 60, (red, green, blue))

    def test_originals_are_never_modified(self):
        before = fx.tree_hashes(self.root)
        thumbs.build(self.root, prune=True)
        thumbs.problems(self.root, reencode=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(thumbs.main(["--check", "--root", str(self.root)]), 0)
        self.drop("wide.png")
        del before[self.names["wide.png"]["path"]]
        thumbs.build(self.root, prune=True)
        self.assertEqual(fx.tree_hashes(self.root), before)

    def test_exif_orientation_gives_an_upright_preview(self):
        # Upright: a 600x1000 portrait with a red top band, stored rotated (1000x600) with EXIF Orientation 6.
        upright = Image.new("RGB", (600, 1000), (0, 0, 255))
        upright.paste((255, 0, 0), (0, 0, 600, 250))
        exif = Image.Exif()
        exif[0x0112] = 6
        self.add("rotated.jpg", fx.encode(upright.transpose(Image.Transpose.ROTATE_90), "JPEG", quality=95, exif=exif))
        thumbs.build(self.root)
        entry = self.manifest()["thumbnails"][self.sha("rotated.jpg")]
        self.assertEqual((entry["width"], entry["height"]), (288, 480))
        with Image.open(self.root / entry["path"]) as image:
            self.assertNotIn("exif", image.info)
            top, bottom = image.convert("RGB").getpixel((144, 20)), image.convert("RGB").getpixel((144, 460))
        self.assertTrue(top[0] > 200 and top[2] < 60, top)
        self.assertTrue(bottom[2] > 200 and bottom[0] < 60, bottom)
        self.assertEqual(thumbs.problems(self.root, reencode=True), [])

    def test_changed_encoder_settings_need_an_explicit_rebuild(self):
        thumbs.build(self.root)
        value = self.manifest()
        value["encoder"]["quality"] = 1
        self.write_manifest(value)
        with self.assertRaisesRegex(ValueError, "--rebuild"):
            thumbs.build(self.root)
        self.assertEqual(thumbs.build(self.root, rebuild=True)["rendered"], 8)
        self.assertEqual(thumbs.problems(self.root), [])


class ColourModeTests(Fixture):
    """Originals in uncommon modes or with non-RGB colour profiles are previewed faithfully, or refused."""

    def render(self, name, image, format_name="PNG", **options):
        rel = f"assets/public/artists/x-fixture_thumbs/{name}"
        (self.root / rel).write_bytes(fx.encode(image, format_name, **options))
        data, _ = thumbs.render(self.root, {"path": rel, "format": format_name})
        preview = Image.open(io.BytesIO(data))
        preview.load()
        return preview

    def test_sixteen_bit_greyscale_keeps_its_tones(self):
        tones = Image.linear_gradient("L").resize((800, 600))
        preview = self.render("deep.png", tones.convert("I").point(lambda value: value * 257).convert("I;16"))
        self.assertEqual((preview.mode, preview.size), ("RGB", (480, 360)))
        self.assertAlmostEqual(ImageStat.Stat(preview).mean[0], ImageStat.Stat(tones).mean[0], delta=2)
        low, high = preview.getextrema()[0]
        self.assertLess(low, 10)
        self.assertGreater(high, 245)

    def test_greyscale_profile_is_applied_not_embedded(self):
        flat = Image.new("L", (800, 600), 128)
        expected = ImageCms.profileToProfile(flat, ImageCms.ImageCmsProfile(io.BytesIO(grey_profile(1.0))),
                                             ImageCms.createProfile("sRGB"), outputMode="RGB").getpixel((0, 0))
        self.assertGreater(expected[0], 170)
        for name, mode, format_name in (("grey.jpg", "L", "JPEG"), ("grey-cutout.png", "LA", "PNG")):
            with self.subTest(mode=mode):
                image = flat.convert(mode)
                if mode == "LA":
                    alpha = Image.new("L", flat.size, 255)
                    alpha.paste(0, (0, 0, 200, 600))
                    image.putalpha(alpha)
                preview = self.render(name, image, format_name, icc_profile=grey_profile(1.0))
                self.assertNotIn("icc_profile", preview.info)
                self.assertEqual(preview.mode, "RGBA" if mode == "LA" else "RGB")
                pixel = preview.getpixel((300, 180))
                for channel in pixel[:3]:
                    self.assertAlmostEqual(channel, expected[0], delta=3)
                if mode == "LA":
                    self.assertEqual(pixel[3], 255)
                    self.assertEqual(preview.getpixel((5, 180))[3], 0)

    def test_sixteen_bit_greyscale_transparency_becomes_alpha(self):
        deep = Image.new("I;16", (800, 600), 40000)
        deep.paste(0, (0, 0, 200, 600))  # the tRNS value: transparent
        deep.paste(1, (200, 0, 400, 600))  # one step away: opaque
        preview = self.render("deep-cutout.png", deep, transparency=0)
        self.assertEqual((preview.mode, preview.size), ("RGBA", (480, 360)))
        self.assertEqual(preview.getpixel((50, 180))[3], 0)
        self.assertEqual(preview.getpixel((180, 180))[3], 255)
        red, green, blue, alpha = preview.getpixel((400, 180))
        self.assertEqual(alpha, 255)
        for channel in (red, green, blue):
            self.assertAlmostEqual(channel, round(40000 / 257), delta=3)

    def test_cmyk_is_previewed_as_browsers_show_it(self):
        ink = (0, 128, 255, 0)
        print_image = Image.new("CMYK", (800, 600), ink)
        naive = print_image.convert("RGB").getpixel((0, 0))
        self.assertEqual(naive, (255, 127, 0))
        profile = cmyk_profile(90)
        managed = ImageCms.profileToProfile(Image.new("CMYK", (1, 1), ink), ImageCms.ImageCmsProfile(
            io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB").getpixel((0, 0))
        self.assertTrue(all(200 < channel < 250 for channel in managed), managed)  # neutral light grey
        for name, options, expected in (("press-scan.jpg", {}, naive),
                                        ("press-scan-tagged.jpg", {"icc_profile": profile}, managed)):
            with self.subTest(name=name):
                preview = self.render(name, print_image, "JPEG", quality=95, **options)
                self.assertEqual((preview.mode, preview.size), ("RGB", (480, 360)))
                self.assertNotIn("icc_profile", preview.info, "the preview is untagged sRGB")
                for channel, wanted in zip(preview.getpixel((240, 180)), expected, strict=True):
                    self.assertAlmostEqual(channel, wanted, delta=6)

    def test_previews_that_would_change_the_colours_are_refused(self):
        flat = Image.new("L", (800, 600), 128)
        lab = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
        ink = Image.new("CMYK", (800, 600), (0, 255, 255, 0))
        cases = [
            ("lab.png", flat, "PNG", {"icc_profile": lab}, "Lab ICC profile on a L image"),
            ("rgb-on-grey.png", flat, "PNG", {"icc_profile": SRGB}, "RGB ICC profile on a L image"),
            ("grey-on-rgb.png", flat.convert("RGB"), "PNG", {"icc_profile": grey_profile(2.2)},
             "GRAY ICC profile on a RGB image"),
            ("cmyk-on-rgb.png", flat.convert("RGB"), "PNG", {"icc_profile": cmyk_profile(90)},
             "CMYK ICC profile on a RGB image"),
            ("rgb-on-cmyk.jpg", ink, "JPEG", {"icc_profile": SRGB}, "RGB ICC profile on a CMYK image"),
            ("grey-on-cmyk.jpg", ink, "JPEG", {"icc_profile": grey_profile(1.0)}, "GRAY ICC profile on a CMYK image"),
            ("broken-profile.png", flat, "PNG", {"icc_profile": b"not a profile" * 10}, "unreadable ICC profile"),
        ]
        for name, image, format_name, options, message in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, re.escape(message)):
                self.render(name, image, format_name, **options)
        for mode in ("F", "I", "YCbCr", "LAB"):  # no supported format decodes to these; refused if one ever did
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, f"unsupported image mode {mode}$"):
                thumbs.eight_bit(Image.new(mode, (4, 4)), "x.png")
        # The build stops too, before writing any preview or manifest, and names every refused original.
        refused = [self.add("rgb-on-cmyk-record.jpg", fx.encode(ink, "JPEG", icc_profile=SRGB)),
                   self.add("grey-on-rgb-record.png", fx.encode(flat.convert("RGB"), "PNG",
                                                                icc_profile=grey_profile(2.2)))]
        with self.assertRaisesRegex(ValueError, "2 original\\(s\\) cannot be previewed") as caught:
            thumbs.build(self.root)
        for record in refused:
            self.assertIn(f"Cannot preview {record['path']}: ", str(caught.exception))
        self.assertFalse((self.root / thumbs.MANIFEST).exists())
        self.assertFalse((self.root / thumbs.THUMB_DIR).exists())


class CheckTests(Fixture):
    def setUp(self):
        super().setUp()
        thumbs.build(self.root)
        self.assertEqual(thumbs.problems(self.root), [])

    def rewrite_entry(self, name, data, **fields):
        """Replace a preview and record its new hash (and any other fields), so only deeper checks can catch it."""
        value = self.manifest()
        entry = value["thumbnails"][self.sha(name)]
        (self.root / entry["path"]).write_bytes(data)
        entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), **fields)
        self.write_manifest(value)

    def webp(self, size, quality=75, format_name="WEBP", **options):
        return fx.encode(fx.pattern(size), format_name, quality=quality, **options)

    def assert_problem(self, text, reencode=False):
        found = thumbs.problems(self.root, reencode=reencode)
        self.assertTrue(any(text in problem for problem in found), found)

    def test_corrupted_preview_is_detected(self):
        raw = bytearray(self.preview("wide.png").read_bytes())
        raw[len(raw) // 2] ^= 1
        self.preview("wide.png").write_bytes(raw)
        self.assert_problem("SHA-256")

    def test_missing_preview_is_detected(self):
        self.preview("wide.png").unlink()
        self.assert_problem("Missing thumbnail file")

    def test_new_original_without_preview_is_detected(self):
        self.add("new.png", fx.encode(fx.pattern((900, 900)), "PNG"))
        self.assert_problem(f"No thumbnail for {self.names['new.png']['path']}")

    def test_retired_preview_and_unlisted_file_are_detected(self):
        (self.root / thumbs.THUMB_DIR / ".stray.webp.tmp").write_bytes(b"partial")
        self.assert_problem("Unlisted file in assets/thumbs: assets/thumbs/.stray.webp.tmp")
        self.drop("wide.png")
        self.assert_problem("belongs to no current original")

    def test_undecodable_preview_is_detected(self):
        self.rewrite_entry("wide.png", b"RIFF\x00\x00\x00\x00WEBPVP8 broken")
        self.assert_problem("does not decode")

    def test_wrong_dimensions_are_detected(self):
        self.rewrite_entry("wide.png", self.webp((400, 240)))
        self.assert_problem(f"{thumbs.MANIFEST} says 480x288")

    def test_preview_of_the_wrong_size_for_its_original_is_detected(self):
        self.rewrite_entry("wide.png", self.webp((400, 240)), width=400, height=240)
        found = thumbs.problems(self.root)
        self.assertFalse(any(" says " in problem for problem in found), found)
        self.assert_problem(f"is 400x240, expected 480x288 for {self.names['wide.png']['path']}")

    def test_preview_in_another_format_is_detected(self):
        self.rewrite_entry("wide.png", self.webp((480, 288), format_name="PNG"))
        self.assert_problem("is PNG, not WEBP")

    def test_preview_with_exif_or_xmp_is_detected(self):
        exif = Image.Exif()
        exif[0x010E] = "leaked description"
        xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
        for options in ({"exif": exif.tobytes()}, {"xmp": xmp}):
            with self.subTest(metadata=next(iter(options))):
                self.rewrite_entry("wide.png", self.webp((480, 288), **options))
                self.assertEqual(thumbs.problems(self.root),
                                 [f"Thumbnail carries EXIF or XMP metadata: {thumbs.thumb_path(self.sha('wide.png'))}"])

    def test_changed_encoder_settings_are_detected(self):
        value = self.manifest()
        value["encoder"]["long_edge"] = 512
        self.write_manifest(value)
        self.assert_problem("Encoder settings changed")

    def test_verify_reencodes_and_catches_foreign_bytes(self):
        self.assertEqual(thumbs.problems(self.root, reencode=True), [])
        self.rewrite_entry("wide.png", self.webp((480, 288), quality=95))
        self.assertEqual(thumbs.problems(self.root), [])
        self.assert_problem("does not reproduce", reencode=True)

    def test_command_line_check_fails_with_a_message(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(thumbs.main(["--check"], root=self.root), 0)
        self.assertEqual(json.loads(output.getvalue())["thumbnails"], 8)
        self.preview("photo.jpg").unlink()
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(thumbs.main(["--check"], root=self.root), 1)
        self.assertIn("Missing thumbnail file", errors.getvalue())
        for args in (["--check", "--prune"], ["--verify", "--rebuild"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                thumbs.main(args, root=self.root)
            self.assertEqual(raised.exception.code, 2)

    def test_documented_command_line_runs_isolated(self):
        result = fx.run_script("build_thumbnails", "--root", str(self.root), "--verify")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.preview("wide.png").unlink()
        result = fx.run_script("build_thumbnails", "--root", str(self.root), "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Missing thumbnail file", result.stderr)


class SafetyTests(Fixture):
    def test_prune_removes_only_listed_previews_whose_original_left(self):
        thumbs.build(self.root)
        gone = self.preview("wide.png")
        kept = {name: data for name, data in self.files().items() if name != gone.name}
        stray = self.root / thumbs.THUMB_DIR / "0123456789abcdef.webp"
        stray.write_bytes(b"not listed in data/thumbs.json")
        self.drop("wide.png")
        summary = thumbs.build(self.root)
        rel = gone.relative_to(self.root).as_posix()
        self.assertEqual((summary["retired"], summary["pruned"]), ([rel], []))
        self.assertTrue(gone.exists(), "a plain build must not delete files")
        self.assertIn(self.sha("wide.png"), self.manifest()["thumbnails"], "still listed until pruned")
        summary = thumbs.build(self.root, prune=True)
        self.assertEqual(summary["pruned"], [rel])
        self.assertEqual(summary["unlisted"], [stray.relative_to(self.root).as_posix()])
        self.assertFalse(gone.exists())
        self.assertTrue(stray.exists(), "a file the manifest never listed is not deleted")
        self.assertNotIn(self.sha("wide.png"), self.manifest()["thumbnails"])
        stray.unlink()
        self.assertEqual(self.files(), kept)
        self.assertEqual(thumbs.problems(self.root), [])

    def test_prune_refuses_a_listed_file_that_is_no_longer_the_derived_preview(self):
        thumbs.build(self.root)
        self.drop("wide.png")
        self.preview("wide.png").write_bytes(b"replaced by hand")
        before = fx.tree_hashes(self.root, thumbs.THUMB_DIR)
        with self.assertRaisesRegex(ValueError, "Refusing to prune"):
            thumbs.build(self.root, prune=True)
        self.assertEqual(fx.tree_hashes(self.root, thumbs.THUMB_DIR), before)

    def test_a_manifest_naming_an_original_is_refused_before_anything_changes(self):
        thumbs.build(self.root)
        originals = fx.tree_hashes(self.root)
        value = self.manifest()
        value["thumbnails"][self.sha("wide.png")]["path"] = self.names["wide.png"]["path"]
        self.write_manifest(value)
        self.drop("wide.png")
        del originals[self.names["wide.png"]["path"]]
        for prune in (False, True):
            with self.subTest(prune=prune), self.assertRaisesRegex(ValueError, "path must be"):
                thumbs.build(self.root, prune=prune)
        self.assertEqual(fx.tree_hashes(self.root), originals)

    def test_an_unrecorded_file_is_never_overwritten(self):
        thumbs.build(self.root)
        built = self.preview("wide.png").read_bytes()
        (self.root / thumbs.MANIFEST).unlink()
        self.preview("wide.png").write_bytes(b"someone else's file")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            thumbs.build(self.root)
        self.assertEqual(self.preview("wide.png").read_bytes(), b"someone else's file")
        self.preview("wide.png").write_bytes(built)  # identical bytes are adopted without a write
        thumbs.build(self.root)
        self.assertEqual(thumbs.problems(self.root), [])

    def test_name_collisions_fail(self):
        catalog = build_catalog.combine(self.root)
        entry = next(asset for asset in catalog["assets"] if build_catalog.needs_preview(asset))
        twin = {**entry, "sha256": entry["sha256"][:16] + "f" * 48, "path": "assets/public/artists/twin.png"}
        if twin["sha256"] == entry["sha256"]:
            twin["sha256"] = entry["sha256"][:16] + "e" * 48
        with self.assertRaisesRegex(ValueError, "Thumbnail name collision"):
            thumbs.originals(self.root, {"assets": [entry, twin]})
        thumbs.build(self.root)
        value = self.manifest()
        listed = value["thumbnails"][entry["sha256"]]
        value["thumbnails"][twin["sha256"]] = {**listed, "path": thumbs.thumb_path(twin["sha256"])}
        self.write_manifest(value)
        with self.assertRaisesRegex(ValueError, "Thumbnail name collision"):
            thumbs.build(self.root)

    def test_a_symlinked_preview_directory_is_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        try:
            (self.root / thumbs.THUMB_DIR).symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"cannot create symlinks: {error}")
        with self.assertRaisesRegex(ValueError, "symlink"):
            thumbs.build(self.root)
        self.assertEqual(list(outside.iterdir()), [])


class CatalogHookTests(Fixture):
    def test_entries_point_at_their_preview_and_keep_the_original(self):
        thumbs.build(self.root)
        catalog = build_catalog.attach_thumbnails(self.root, build_catalog.combine(self.root, verify=True), verify=True)
        listing = self.manifest()["thumbnails"]
        by_sha = {asset["sha256"]: asset for asset in catalog["assets"]}
        for name in ("wide.png", "cutout.png", "photo.jpg", "anim.gif", *CONTRACT_PREVIEWS):
            with self.subTest(name=name):
                entry = by_sha[self.sha(name)]
                self.assertEqual(entry["thumb"], listing[entry["sha256"]]["path"])
                self.assertTrue(entry["url"].startswith("assets/public/"))
                self.assertTrue(all("thumb" not in record for record in entry["provenance"]))
        self.assertIsNone(by_sha[self.sha("small.png")]["thumb"])
        self.assertIsNone(next(a for a in catalog["assets"] if a["format"] == "PDF")["thumb"])
        shot = by_sha[self.sha("press-kit-2014-screenshot.png")]
        self.assertEqual((shot["site_original"], shot["thumb"] is not None), (False, True))
        manifest.loads(manifest.dumps(catalog))  # serializable as the catalog writer needs

    def test_a_cmyk_press_scan_does_not_block_the_pipeline(self):
        """An archive-tier CMYK scan needs a preview like every archive raster; catalog and coverage then build."""
        scan = fx.add_record(self.root, fx.make_record(
            self.root, "archives:fixture-press", fx.encode(Image.new("CMYK", (300, 200), (0, 128, 255, 0)), "JPEG"),
            "press-scan.jpg", kind="gameplay_screenshot"))
        thumbs.build(self.root)
        self.assertEqual(thumbs.problems(self.root, reencode=True), [])
        with contextlib.redirect_stderr(io.StringIO()):  # notes about the absent template
            catalog, stale = build_catalog.build(self.root)
        self.assertEqual(stale, [])
        entry = next(asset for asset in catalog["assets"] if asset["id"] == scan["id"])
        self.assertEqual((entry["site_original"], entry["thumb"]), (False, thumbs.thumb_path(scan["sha256"])))
        report = build_coverage.build(self.root)
        self.assertEqual(report["assets"]["entries_with_preview"], len(self.manifest()["thumbnails"]))


if __name__ == "__main__":
    unittest.main()
