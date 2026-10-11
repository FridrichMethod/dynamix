"""Offline guards for the interface files: every local resource exists and is linked relatively (so file://, an HTTP
root and an HTTP /dynamix/ subpath all work), nothing is fetched from another host, no font, audio or game media is
shipped, scripts never parse data as HTML, and the reused vendor bundle and its MIT notices are byte-identical.

Adapted from FridrichMethod/deemo tests/test_layout.py (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8)."""

import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*?))\s*\)""", re.IGNORECASE)
PAGES = ("archive.html", "templates/slideshow.html")
SCRIPTS = ("src/i18n.js", "src/archive.js", "src/i18n/common.js", "src/i18n/archive.js", "src/i18n/slideshow.js")
STYLESHEETS = ("src/archive.css", "src/slideshow.css")
# src/vendor/legacy-ui.js is FridrichMethod/deemo's byte-identical copy of the upstream bundle (html2canvas 0.5.0-beta3
# with punycode 1.2.4, and Pace 1.0.0); its MIT texts are copied verbatim from the same reference commit.
VENDOR_SHA256 = "4596246286d0f0fb9fa2c6a9c57bd05e8c226c01007026e7398f7e6bc940ef03"
LICENSES = {
    "licenses/html2canvas-MIT.txt": (
        "86200ce4e92d9a22c41c8647a55f7a5fddff304ff89b4d36ecc699ed8c123d2c",
        "Copyright (c) 2012 Niklas von Hertzen",
    ),
    "licenses/pace-MIT.txt": (
        "7415a0a1c245c068a22c955200ef39e80a4a938b2383d3f30b028e0d8b5602e3",
        "Copyright (c) 2013 HubSpot, Inc.",
    ),
    "licenses/punycode-MIT.txt": (
        "1e234f86ba3f213fbab143e195f172a40c5f943fb268c45d619ede8510b20d06",
        "Copyright Mathias Bynens",
    ),
}
FONT_OR_AUDIO = {".ttf", ".otf", ".woff", ".woff2", ".eot", ".mp3", ".ogg", ".wav", ".m4a", ".flac"}
# Ways of turning a string into markup or code; data must only ever be set as text.
UNSAFE_SINKS = re.compile(
    r"\binnerHTML\b|\bouterHTML\b|insertAdjacentHTML|document\.write|\beval\(|new Function\(|\.srcdoc\b|setHTMLUnsafe|parseFromString"
)


def css_urls(text):
    return [next(group for group in match.groups() if group is not None).strip() for match in CSS_URL.finditer(text)]


CSS_STRING = re.compile(r""""(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'""")
CSS_UNQUOTED_URL = re.compile(r"url\(\s*[^)\"']*\)", re.IGNORECASE)
CSS_PROPERTY = re.compile(r"(?<![\w-])-{0,2}[A-Za-z][\w-]*\s*:")


def css_declaration_errors(text):
    """Declarations whose value holds a second "property:" token, as when a semicolon is missing between two of them.
    Browsers drop such a declaration whole ("height: 100% position: relative" sets neither property). Comments,
    strings and url() contents are blanked first; selectors and at-rule preludes (the text before "{") are skipped."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = CSS_UNQUOTED_URL.sub("url()", CSS_STRING.sub('""', text))
    errors = []
    for segment, end in re.findall(r"([^{};]*)([{};])", text):
        declaration = " ".join(segment.split())
        if end == "{" or not declaration or declaration.startswith("@"):
            continue
        _, colon, value = declaration.partition(":")
        if colon and CSS_PROPERTY.search(value):
            errors.append(declaration)
    return errors


class ResourceLinks(HTMLParser):
    """Every URL a page loads (scripts, stylesheets, icons, images, manifest) and every anchor it links to."""

    def __init__(self):
        super().__init__()
        self.loads, self.anchors, self.inline, self.styles = [], [], [], []
        self.in_style = self.in_script = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.anchors.append(attributes["href"])
        elif tag == "link" and attributes.get("href"):
            self.loads.append(attributes["href"])
        for key in ("src", "data-src", "poster"):
            if attributes.get(key):
                self.loads.append(attributes[key])
        if tag == "meta" and attributes.get("property") == "og:image":
            self.loads.append(attributes["content"])
        if tag == "meta" and attributes.get("name") == "msapplication-config":
            self.loads.append(attributes["content"])
        self.loads.extend(css_urls(attributes.get("style", "")))
        self.in_style, self.in_script = tag == "style", tag == "script" and not attributes.get("src")

    def handle_endtag(self, tag):
        self.in_style = self.in_script = False

    def handle_data(self, data):
        if self.in_style:
            self.loads.extend(css_urls(data))
            self.styles.append(data)
        if self.in_script:
            self.inline.append(data)


def parse(relative):
    parser = ResourceLinks()
    parser.feed((ROOT / relative).read_text(encoding="utf-8-sig"))
    return parser


class LayoutTests(unittest.TestCase):
    def assert_local_resource(self, page, url):
        parsed = urlsplit(url)
        self.assertFalse(
            parsed.scheme or parsed.netloc, f"{page.relative_to(ROOT)} loads a remote or scheme URL: {url}"
        )
        self.assertTrue(parsed.path, url)
        self.assertFalse(parsed.path.startswith("/"), f"Root-relative URL breaks nested hosting: {page}: {url}")
        target = (page.parent / unquote(parsed.path)).resolve()
        self.assertTrue(target.is_relative_to(ROOT), f"URL escapes repository: {page}: {url}")
        self.assertTrue(target.is_file(), f"Missing local resource: {page.relative_to(ROOT)}: {url}")

    def test_pages_load_only_existing_local_resources(self):
        """data/catalog.js is generated by the catalog builder and may be absent on this branch; it is checked by name."""
        for relative in PAGES + (("index.html",) if (ROOT / "index.html").is_file() else ()):
            with self.subTest(page=relative):
                served = ROOT / "index.html" if relative.startswith("templates/") else ROOT / relative
                for url in parse(relative).loads:
                    if url == "data/catalog.js" and not (ROOT / url).is_file():
                        continue
                    if relative == "index.html" and url.startswith("assets/public/"):
                        continue  # slide originals belong to the generated catalog, checked by its own build
                    self.assert_local_resource(served, url)

    def test_archive_loads_the_catalog_script_by_relative_path(self):
        self.assertIn("data/catalog.js", parse("archive.html").loads)

    def test_archive_links_the_manifest_it_ships_by_relative_path(self):
        """The full manifest is linked next to the page, as in DEEMO, so the link works offline and always names the
        deployed copy rather than the branch tip."""
        anchors = parse("archive.html").anchors
        self.assertGreaterEqual(anchors.count("data/catalog.json"), 2)  # noscript notice and footer
        self.assertFalse([href for href in anchors if href.endswith("catalog.json") and href != "data/catalog.json"])

    def test_anchors_are_relative_or_https(self):
        for relative in PAGES:
            for href in parse(relative).anchors:
                with self.subTest(page=relative, href=href):
                    parsed = urlsplit(href)
                    if parsed.scheme:
                        self.assertEqual(parsed.scheme, "https")
                    else:
                        self.assertFalse(href.startswith("/"), "root-relative link")

    def test_css_resources_resolve_relative_to_stylesheets(self):
        for relative in STYLESHEETS:
            sheet = ROOT / relative
            for url in css_urls(sheet.read_text(encoding="utf-8-sig")):
                with self.subTest(stylesheet=relative, url=url[:40]):
                    if url.startswith("data:image/svg+xml,"):
                        continue  # the drawn select chevron is inline
                    self.assert_local_resource(sheet, url)

    def test_css_url_parser_preserves_spaces_and_optional_quotes(self):
        self.assertEqual(
            css_urls(
                "src: url(\"../assets/a b.ttf\"); src: url(../assets/c d.ttf); background: url('../assets/e f.png');"
            ),
            ["../assets/a b.ttf", "../assets/c d.ttf", "../assets/e f.png"],
        )

    def test_css_declarations_set_one_property_each(self):
        """A missing semicolon joins two declarations into one invalid declaration that browsers drop silently."""
        sheets = {relative: (ROOT / relative).read_text(encoding="utf-8-sig") for relative in STYLESHEETS}
        sheets.update({f"{relative} <style>": "\n".join(parse(relative).styles) for relative in PAGES})
        for relative, text in sheets.items():
            with self.subTest(stylesheet=relative):
                self.assertEqual(css_declaration_errors(text), [], "a declaration holds a second property")

    def test_css_declaration_check_sees_joined_declarations_only(self):
        joined = (
            ".popup { width: 100%; height: 100% position: relative; transition: all 2s; }",
            ".a { color: red }\n.b { margin: 0 padding: 0 }",
            "@media (max-width: 480px) { .c { top: 0 --gap: 4px; } }",
        )
        valid = (
            '.a:hover, .b::before { content: "x: y"; color: red }',
            "select { background-image: url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E\"); }",
            ".d { background: url(data:image/png;base64,AAA:B) no-repeat; }",
            "@media (orientation: landscape) and (max-height: 560px) { .e { inset: 0; } }",
            "/* height: 100% position: relative */ .f { --x: 1px; width: calc(var(--x) * 2) !important }",
        )
        for text in joined:
            with self.subTest(joined=text):
                self.assertNotEqual(css_declaration_errors(text), [])
        for text in valid:
            with self.subTest(valid=text):
                self.assertEqual(css_declaration_errors(text), [])

    def test_no_fonts_imports_or_remote_requests_in_stylesheets(self):
        for relative in STYLESHEETS:
            # Comments may cite the reference project's URL; only rules can load anything.
            text = re.sub(r"/\*.*?\*/", "", (ROOT / relative).read_text(encoding="utf-8"), flags=re.DOTALL)
            with self.subTest(stylesheet=relative):
                self.assertNotIn("@font-face", text, "system font stacks only")
                self.assertNotIn("@import", text)
                self.assertNotRegex(text, r"https?://(?!www\.w3\.org/2000/svg)", "no remote URLs")

    def test_scripts_make_no_network_requests_and_never_parse_html(self):
        code = {relative: (ROOT / relative).read_text(encoding="utf-8") for relative in SCRIPTS}
        for page in PAGES:
            code[f"{page} (inline)"] = "\n".join(parse(page).inline)
        for name, text in code.items():
            with self.subTest(script=name):
                self.assertIsNone(UNSAFE_SINKS.search(text), "data must be set with textContent / setAttribute")
                self.assertIsNone(
                    re.search(
                        r"\bfetch\(|XMLHttpRequest|WebSocket|EventSource|navigator\.sendBeacon|importScripts", text
                    ),
                    "no network requests while browsing",
                )

    def test_manifest_and_browserconfig_icons_exist(self):
        manifest_path = ROOT / "site.webmanifest"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assert_local_resource(manifest_path, manifest["start_url"])
        self.assertTrue(manifest["icons"])
        for icon in manifest["icons"]:
            self.assertTrue(icon["src"].removeprefix("./").startswith("assets/site/icons/"))
            self.assert_local_resource(manifest_path, icon["src"])
        browserconfig_path = ROOT / "browserconfig.xml"
        icons = [element.attrib["src"] for element in ET.parse(browserconfig_path).iter() if "src" in element.attrib]
        self.assertTrue(icons)
        for url in icons:
            self.assertTrue(url.removeprefix("./").startswith("assets/site/icons/"))
            self.assert_local_resource(browserconfig_path, url)

    def test_no_bundled_fonts_audio_or_media_outside_site_icons(self):
        """The interface ships no font or audio file, and its only images are the project-made icons."""
        for directory in ("src", "assets/site", "templates"):
            for path in (ROOT / directory).rglob("*"):
                if path.is_file():
                    with self.subTest(path=path.relative_to(ROOT).as_posix()):
                        self.assertNotIn(path.suffix.lower(), FONT_OR_AUDIO)
                        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico"}:
                            self.assertEqual(path.parent, ROOT / "assets/site/icons")
        for relative in PAGES:
            text = (ROOT / relative).read_text(encoding="utf-8-sig")
            self.assertNotRegex(text, r"<audio|<video|<source\b", relative)

    def test_vendor_bundle_and_licenses_are_byte_identical(self):
        self.assertEqual(hashlib.sha256((ROOT / "src/vendor/legacy-ui.js").read_bytes()).hexdigest(), VENDOR_SHA256)
        for relative, (digest, copyright_line) in LICENSES.items():
            with self.subTest(license=relative):
                raw = (ROOT / relative).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
                self.assertIn(copyright_line, raw.decode("utf-8"))
                self.assertIn("Permission is hereby granted, free of charge", raw.decode("utf-8"))
        self.assertEqual(sorted(path.name for path in (ROOT / "src/vendor").iterdir()), ["legacy-ui.js"])

    def test_derived_files_name_their_origin(self):
        """Adapted files keep a derivation notice naming the Apache-2.0 reference and its commit."""
        for relative in (
            "archive.html",
            "templates/slideshow.html",
            "src/i18n.js",
            "src/archive.js",
            "src/archive.css",
            "src/slideshow.css",
            "browserconfig.xml",
            "scripts/render_slideshow.py",
            "src/i18n/common.js",
            "src/i18n/archive.js",
            "src/i18n/slideshow.js",
        ):
            with self.subTest(file=relative):
                head = "\n".join((ROOT / relative).read_text(encoding="utf-8-sig").splitlines()[:20])
                self.assertRegex(head, r"Apache-2\.0")
                self.assertIn("b14bf0d5f5a05e5382c319f3fd5019a4936820b8", head)

    def test_layout_has_no_case_insensitive_filename_collisions(self):
        seen = {}
        paths = [
            path
            for directory in ("assets/site", "src", "scripts", "templates", "licenses", "tests")
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ]
        paths.extend(path for path in ROOT.iterdir() if path.is_file())
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            self.assertNotIn(
                relative.casefold(), seen, f"Case-insensitive collision: {relative} / {seen.get(relative.casefold())}"
            )
            seen[relative.casefold()] = relative


if __name__ == "__main__":
    unittest.main()
