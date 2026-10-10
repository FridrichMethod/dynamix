"""Offline tests for scripts/lib/media.py: sniffing, decode verification, PDFs and atomic finalization."""

import hashlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import manifest, media, net, paths, schema  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/contract"
MINIMAL_PDF = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")


def encode(fmt, size=(40, 30), mode="RGB", **options):
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 30, 90) if mode == "RGB" else 1).save(buffer, fmt, **options)
    return buffer.getvalue()


def animated_gif():
    frames = [Image.new("RGB", (16, 16), color) for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
    return buffer.getvalue()


class MediaTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class SniffAndInspectTests(MediaTestCase):
    def test_sniff_signatures(self):
        self.assertEqual(media.sniff(encode("PNG")), ("PNG", "image/png", "png"))
        self.assertEqual(media.sniff(encode("JPEG")), ("JPEG", "image/jpeg", "jpg"))
        self.assertEqual(media.sniff(encode("WEBP")), ("WEBP", "image/webp", "webp"))
        self.assertEqual(media.sniff(animated_gif()), ("GIF", "image/gif", "gif"))
        self.assertEqual(media.sniff(MINIMAL_PDF), ("PDF", "application/pdf", "pdf"))
        for unknown in (b"", b"<html>", b"RIFF\x00\x00\x00\x00WAVE", b"BM\x00\x00"):
            self.assertEqual(media.sniff(unknown), ("unknown", None, None))

    def test_inspect_rasters(self):
        for fmt, ext in (("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")):
            data = encode(fmt)
            info = media.inspect(self.write(f"image.{ext}", data))
            self.assertEqual((info["format"], info["ext"], info["width"], info["height"]), (fmt, ext, 40, 30))
            self.assertEqual(info["bytes"], len(data))
            self.assertEqual(info["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(info["sha1"], hashlib.sha1(data).hexdigest())
            self.assertFalse(info["animated"])
            self.assertEqual(info["frames"], 1)

    def test_animated_gif(self):
        info = media.inspect(self.write("idle.gif", animated_gif()))
        self.assertEqual((info["format"], info["frames"], info["animated"]), ("GIF", 3, True))

    def test_webp_behind_png_name(self):
        info = media.inspect(self.write("Moonlight_of_Sand_Castle.png", encode("WEBP", lossless=True)))
        self.assertEqual((info["format"], info["mime"], info["ext"]), ("WEBP", "image/webp", "webp"))

    def test_pdf(self):
        info = media.inspect(self.write("press.pdf", MINIMAL_PDF))
        self.assertEqual((info["format"], info["width"], info["height"], info["frames"]), ("PDF", None, None, None))
        with self.assertRaises(media.MediaError):
            media.inspect(self.write("truncated.pdf", MINIMAL_PDF.replace(b"%%EOF", b"")))

    def test_corrupt_and_unknown_files(self):
        png = encode("PNG", size=(64, 64))
        cases = {"truncated.png": png[: len(png) // 2], "empty.png": b"", "html.png": b"<html>blocked</html>",
                 "garbage.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "badjpeg.jpg": b"\xff\xd8\xff" + b"\x00" * 50,
                 "header-only.webp": encode("WEBP")[:20]}
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(media.MediaError):
                media.inspect(self.write(name, data))

    def test_media_error_failure_record(self):
        error = media.MediaError("cannot decode PNG")
        record = error.failure(source_id="wikis:fandom", candidate_id="wikis:fandom:File:X.png",
                               url="https://static.wikia.nocookie.net/dynamix/images/x.png")
        self.assertEqual((record["error_class"], record["phase"], record["retriable"]),
                         ("decode_error", "decode", False))
        schema.validate_failure(record)


class FinalizeTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.dir / "repo"
        self.dest = self.root / "assets/public/wikis/fandom"
        self.dest.mkdir(parents=True)

    def result(self, data, name=".x.123.part"):
        temp = self.dest / name
        temp.write_bytes(data)
        return net.DownloadResult(temp, "https://example.org/x.png", 200, "image/png", len(data),
                                  hashlib.sha256(data).hexdigest(), hashlib.sha1(data).hexdigest(), {})

    def test_moves_into_place_with_sniffed_extension(self):
        data = encode("WEBP", lossless=True)
        result = self.result(data)

        def rel(info):
            return paths.asset_relpath("wikis", "fandom", "Moonlight of Sand Castle.png", info["sha256"], info["ext"])

        info = media.finalize_download(result, self.root, rel)
        self.assertTrue(info["path"].endswith(".webp"))
        self.assertEqual((self.root / info["path"]).read_bytes(), data)
        self.assertFalse(result.temp_path.exists())
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [Path(info["path"]).name])

    def test_rejects_extension_mismatch_and_cleans_up(self):
        result = self.result(encode("WEBP"))
        with self.assertRaises(media.MediaError):
            media.finalize_download(result, self.root, "assets/public/wikis/fandom/x--000000000000.png")
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_undecodable_download_cleans_up(self):
        result = self.result(b"<html>Just a moment...</html>")
        with self.assertRaises(media.MediaError):
            media.finalize_download(result, self.root, "assets/public/wikis/fandom/x--000000000000.png")
        self.assertFalse(result.temp_path.exists())

    def test_changed_temp_file_rejected(self):
        result = self.result(encode("PNG"))
        result.temp_path.write_bytes(encode("PNG", size=(41, 30)))
        with self.assertRaises(media.MediaError):
            media.finalize_download(result, self.root, "assets/public/wikis/fandom/x--000000000000.png")
        self.assertFalse(result.temp_path.exists())

    def test_existing_identical_kept_and_different_never_overwritten(self):
        data = encode("PNG")
        target = "assets/public/wikis/fandom/x--000000000000.png"
        media.finalize_download(self.result(data, ".a.part"), self.root, target)
        before = os.stat(self.root / target).st_mtime_ns
        media.finalize_download(self.result(data, ".b.part"), self.root, target)
        self.assertEqual(os.stat(self.root / target).st_mtime_ns, before)
        other = self.result(encode("PNG", size=(10, 10)), ".c.part")
        with self.assertRaises(media.MediaError):
            media.finalize_download(other, self.root, target)
        self.assertEqual((self.root / target).read_bytes(), data)
        self.assertFalse(other.temp_path.exists())

    def test_symlinked_or_escaping_destination_rejected(self):
        outside = self.dir / "outside"
        outside.mkdir()
        os.symlink(outside, self.root / "assets/public/wikis/linked")
        for target in ("assets/public/wikis/linked/x--000000000000.png", "../x--000000000000.png",
                       "data/x--000000000000.png"):
            with self.subTest(target=target):
                result = self.result(encode("PNG"), ".s.part")
                with self.assertRaises(ValueError):
                    media.finalize_download(result, self.root, target)
                self.assertFalse(result.temp_path.exists())
        self.assertEqual(list(outside.iterdir()), [])


class VerifyAssetTests(MediaTestCase):
    def test_fixture_assets_verify_and_mismatches_fail(self):
        records = [asset for family in schema.FAMILIES
                   for asset in manifest.load_json(FIXTURE / f"data/sources/{family}.json")["assets"]]
        self.assertGreaterEqual(len(records), 8)
        for asset in records:
            media.verify_asset_file(FIXTURE, asset)
        asset = next(a for a in records if a["format"] == "PNG")
        for field, value in (("sha256", "0" * 64), ("width", asset["width"] + 1), ("bytes", 1), ("format", "WEBP")):
            with self.subTest(field=field), self.assertRaises(media.MediaError):
                media.verify_asset_file(FIXTURE, {**asset, field: value})
        with self.assertRaises(media.MediaError):
            media.verify_asset_file(FIXTURE, {**asset, "path": asset["path"].replace(".png", ".missing.png")})
        with self.assertRaises(media.MediaError):
            media.verify_asset_file(FIXTURE, {**asset, "path": "../" + asset["path"]})


if __name__ == "__main__":
    unittest.main()
