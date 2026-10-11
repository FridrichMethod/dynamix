"""scripts/render_slideshow.py: the frozen render_slideshow(root, catalog) -> str interface, attribute escaping, slide
order and selection, single placeholder replacement, archive-only guards and cut-out layout hints."""

import json
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from render_slideshow import PLACEHOLDER, TEMPLATE, attr, render_slideshow  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/ui"
REPOSITORY = "https://github.com/FridrichMethod/dynamix"


class Slides(HTMLParser):
    """The rendered slides: one dict of decoded attributes per <img class="dynamix-draw">, plus every tag seen."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.slides, self.tags = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attributes = dict(attrs)
        if tag == "img" and attributes.get("class") == "dynamix-draw":
            self.slides.append(attributes)


def slides_of(html):
    parser = Slides()
    parser.feed(html)
    return parser


def asset(index, **overrides):
    record = {
        "id": f"wikis:miraheze:{index:016x}", "source_id": "wikis:miraheze", "source_name": "Wiki", "family": "wikis",
        "title": f"Song {index}", "kind": "song_art", "page_url": f"https://example.org/File:{index}", "path": f"assets/public/wikis/miraheze/song-{index}--{index:012x}.png",
        "url": f"assets/public/wikis/miraheze/song-{index}--{index:012x}.png", "thumb": f"assets/thumbs/{index:016x}.webp",
        "width": 1200, "height": 1200, "format": "PNG", "gallery": True, "slideshow": True, "site_original": True,
        "archive_url": f"{REPOSITORY}/raw/main/assets/public/wikis/miraheze/song-{index}--{index:012x}.png",
        "illustrators": [], "composers": [], "song_titles": [], "collections": [],
        "quality": {"source_variant": "site_original", "origin_status": "wiki_copy", "master_status": "unverified", "notes": ""},
        "provenance": [],
    }
    record.update(overrides)
    return record


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="render-slideshow-"))
        (self.root / "templates").mkdir()
        shutil.copy(ROOT / TEMPLATE, self.root / TEMPLATE)

    def tearDown(self):
        shutil.rmtree(self.root)

    def render(self, assets):
        return render_slideshow(self.root, {"assets": assets, "sources": []})

    def test_interface_returns_the_template_with_the_placeholder_replaced_once(self):
        template = (ROOT / TEMPLATE).read_text(encoding="utf-8-sig")
        html = self.render([asset(1), asset(2)])
        self.assertIsInstance(html, str)
        self.assertNotIn(PLACEHOLDER, html)
        before, after = template.split(PLACEHOLDER)
        self.assertTrue(html.startswith(before) and html.endswith(after), "only the placeholder changes")
        self.assertEqual(html.count('<div class="mySlides fade"><img '), 2)
        empty = self.render([])
        self.assertEqual(empty, before + after)

    def test_template_must_hold_exactly_one_placeholder(self):
        template = (self.root / TEMPLATE).read_text(encoding="utf-8")
        for broken in (template.replace(PLACEHOLDER, ""), template.replace(PLACEHOLDER, PLACEHOLDER + PLACEHOLDER)):
            (self.root / TEMPLATE).write_text(broken, encoding="utf-8")
            with self.subTest(count=broken.count(PLACEHOLDER)), self.assertRaises(ValueError):
                self.render([asset(1)])

    def test_slides_follow_catalog_order_and_only_slideshow_assets(self):
        assets = [asset(3), asset(1, slideshow=False), asset(2), asset(4, slideshow="true"), asset(5)]
        ids = [slide["data-id"] for slide in slides_of(self.render(assets)).slides]
        self.assertEqual(ids, [assets[0]["id"], assets[2]["id"], assets[4]["id"]])

    def test_every_attribute_value_is_escaped(self):
        hostile = 'Night <script>alert(1)</script> "quoted" & \'single\' </script><img src=x onerror=alert(2)>\r\n\tend'
        html = self.render([asset(1, title=hostile, source_name=hostile, illustrators=[hostile, "B"], composers=['"C" & D'],
                                  song_titles=[hostile], collections=["</script>"], id=f"wikis:miraheze:{hostile}")])
        parsed = slides_of(html)
        self.assertEqual(len(parsed.slides), 1)
        slide = parsed.slides[0]
        self.assertEqual(slide["data-title"], hostile)
        self.assertEqual(slide["alt"], hostile)
        self.assertEqual(slide["data-source"], hostile)
        self.assertEqual(slide["data-illustrators"], hostile.strip() + "\nB")
        self.assertEqual(slide["data-composers"], '"C" & D')
        self.assertEqual(slide["data-id"], f"wikis:miraheze:{hostile}")
        body = html.split('<div class="slideshow-container">', 1)[1].split("</div>\n", 1)[0]
        self.assertNotIn("<script>", body)
        self.assertNotIn('"quoted"', body)
        self.assertNotIn("\r", body)
        # The hostile values add no element: the slide is a div and an img, whatever the strings hold.
        template_tags = slides_of(self.render([])).tags
        self.assertEqual(len(parsed.tags) - len(template_tags), 2)

    def test_attr_escapes_quotes_markup_and_line_breaks(self):
        self.assertEqual(attr('<a href="x">\'&\'</a>\r\n\t'), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;&#13;&#10;&#9;")

    def test_unsafe_page_urls_are_not_emitted(self):
        for page in ("javascript:alert(1)", "data:text/html,x", "//example.org/x", "/relative", "", None):
            with self.subTest(page=page):
                slide = slides_of(self.render([asset(1, page_url=page)])).slides[0]
                self.assertNotIn("data-page", slide)
        slide = slides_of(self.render([asset(1, page_url="https://example.org/File:A.png")])).slides[0]
        self.assertEqual(slide["data-page"], "https://example.org/File:A.png")

    def test_slide_sources_must_be_site_relative(self):
        for url in ("https://example.org/a.png", "/assets/a.png", "assets/../../etc/passwd", "assets/%2e%2e/x.png", "javascript:x", "",
                    "java\tscript:alert(1)", " javascript:x", "assets/a b.png", "\\\\host\\a.png", "data:image/png;base64,AAAA"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.render([asset(1, url=url)])

    def test_site_original_slides_load_and_download_the_original(self):
        slide = slides_of(self.render([asset(1)])).slides[0]
        self.assertEqual(slide["data-src"], asset(1)["url"])
        self.assertEqual(slide["data-original"], asset(1)["url"])
        self.assertNotIn("data-original-external", slide)
        self.assertNotIn("src", slide, "slides load lazily from data-src")

    def test_archive_only_slide_shows_its_preview_and_links_the_repository(self):
        archive_only = asset(1, site_original=False)
        slide = slides_of(self.render([archive_only])).slides[0]
        self.assertEqual(slide["data-src"], archive_only["thumb"])
        self.assertEqual(slide["data-original"], archive_only["archive_url"])
        self.assertEqual((slide["data-original-external"], slide["data-preview-only"]), ("true", "true"))
        for missing in ({"thumb": None}, {"archive_url": None}, {"archive_url": "javascript:alert(1)"}):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                self.render([asset(1, site_original=False, **missing)])

    def test_dimensions_are_required(self):
        with self.assertRaises(ValueError):
            self.render([asset(1, width=None, height=None)])

    def test_a_missing_title_falls_back_to_the_songs_then_the_file_name(self):
        """The data provides a human title; a record without one is named by its songs, then by its upstream file name,
        then by its ID (as the archive page names it), never left blank."""
        cases = [
            (asset(1, title="Human Title", song_titles=["Song"], original_filename="Song_jacket.png"), "Human Title"),
            (asset(2, title="", song_titles=["Song A", " ", "Song B"], original_filename="a.png"), "Song A / Song B"),
            (asset(3, title="  ", song_titles=[], original_filename="Song_jacket.png"), "Song_jacket.png"),
            (asset(4, title=None, song_titles=None), asset(4)["id"]),
        ]
        slides = slides_of(self.render([case for case, _ in cases])).slides
        for slide, (_, expected) in zip(slides, cases, strict=True):
            with self.subTest(expected=expected):
                self.assertEqual((slide["data-title"], slide["alt"]), (expected, expected))
        code = (ROOT / "src/archive.js").read_text(encoding="utf-8")
        self.assertIn('const titleOf = (asset) => [asset.title, textList(asset.song_titles).join(" / "), asset.original_filename, asset.id]', code)
        # Besides titleOf() only the search reads the raw title (with the songs and the file name it already searches).
        self.assertEqual((code.count("asset.title"), code.count("other.title"), code.count("newer.title")), (2, 0, 0))
        self.assertIn("return fold([asset.title, asset.original_filename,", code)

    def test_credits_are_merged_one_per_line_and_missing_ones_stay_missing(self):
        merged = asset(1, illustrators=None, composers=None, provenance=[
            {"source_id": "a", "illustrators": ["X", "Y"], "composers": []},
            {"source_id": "b", "illustrators": ["Y", " Z "], "composers": ["C"]},
        ])
        slide = slides_of(self.render([merged])).slides[0]
        self.assertEqual(slide["data-illustrators"], "X\nY\nZ")
        self.assertEqual(slide["data-composers"], "C")
        bare = slides_of(self.render([asset(2)])).slides[0]
        for key in ("data-illustrators", "data-composers", "data-songs", "data-collections"):
            self.assertNotIn(key, bare, "an empty credit is not written as a value")

    def test_origin_comes_from_the_primary_record(self):
        primary = asset(1, provenance=[{"source_id": "artists:x:a", "quality": {"origin_status": "artist_post"}},
                                       {"source_id": "wikis:miraheze", "quality": {"origin_status": "wiki_copy"}}])
        self.assertEqual(slides_of(self.render([primary])).slides[0]["data-origin"], "artist_post")
        odd = asset(2, quality={"origin_status": "<b>"})
        self.assertNotIn("data-origin", slides_of(self.render([odd])).slides[0])

    def test_cut_out_layout_hints(self):
        path = self.root / "assets/public/wikis/miraheze/cut--000000000001.png"
        path.parent.mkdir(parents=True)
        image = Image.new("RGBA", (100, 200), (0, 0, 0, 0))
        image.paste((255, 0, 0, 255), (20, 40, 60, 200))
        image.save(path)
        opaque = self.root / "assets/public/wikis/miraheze/full--000000000002.png"
        Image.new("RGBA", (100, 100), (1, 2, 3, 255)).save(opaque)
        rel = lambda p: p.relative_to(self.root).as_posix()
        slides = slides_of(self.render([
            asset(1, kind="character", path=rel(path), url=rel(path), width=100, height=200),
            asset(2, kind="character", path=rel(opaque), url=rel(opaque), width=100, height=100),
            asset(3, kind="song_art", path=rel(path), url=rel(path), width=100, height=200),
            asset(4, kind="character", path="assets/public/missing.png", url="assets/public/missing.png"),
            asset(5, kind="character", path="../outside.png", url="assets/public/x.png"),
        ])).slides
        self.assertEqual((slides[0]["data-art-box"], slides[0]["data-bleed"]), ("0.2 0.2 0.4 0.8", "bottom"))
        for slide in slides[1:]:
            self.assertNotIn("data-art-box", slide)
            self.assertNotIn("data-bleed", slide)

    def test_rendering_is_deterministic(self):
        assets = [asset(index) for index in range(5)]
        self.assertEqual(self.render(assets), self.render(assets))


class FixtureRenderTests(unittest.TestCase):
    def test_ui_fixture_renders_every_slideshow_asset(self):
        catalog = json.loads((FIXTURE / "catalog.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="render-fixture-") as directory:
            root = Path(directory)
            (root / "templates").mkdir()
            shutil.copy(ROOT / TEMPLATE, root / TEMPLATE)
            shutil.copytree(FIXTURE / "assets", root / "assets")
            slides = slides_of(render_slideshow(root, catalog)).slides
        expected = [entry for entry in catalog["assets"] if entry["slideshow"]]
        self.assertEqual(len(slides), catalog["summary"]["slideshow_images"])
        self.assertEqual([slide["data-id"] for slide in slides], [entry["id"] for entry in expected])
        for slide, entry in zip(slides, expected):
            with self.subTest(asset=entry["title"]):
                self.assertEqual(slide["data-title"], entry["title"])
                self.assertEqual(slide["data-size"], f"{entry['width']} × {entry['height']}")
                self.assertEqual(slide["data-kind"], entry["kind"])
                self.assertEqual(slide["data-src"], entry["url"])
                self.assertEqual(slide.get("data-illustrators", ""), "\n".join(entry["illustrators"]))
        cut_outs = [slide for slide in slides if slide["data-kind"] == "character"]
        self.assertTrue(cut_outs and all("data-art-box" in slide for slide in cut_outs))


if __name__ == "__main__":
    unittest.main()
