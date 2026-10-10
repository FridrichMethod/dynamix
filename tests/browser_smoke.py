"""Browser acceptance checks for archive.html and the slideshow (index.html), driving an installed Chrome via Playwright.

    python -I tests/browser_smoke.py --fixture [--workdir DIR] [--browser /usr/bin/google-chrome]
    python -I tests/browser_smoke.py --base http://127.0.0.1:8765/ [--root CHECKOUT] [--browser PATH]

--fixture builds sites from tests/fixtures/ui (the frozen catalog shape, including injection strings, archive-only files,
a PDF, unknown credits and an empty catalog) and serves them itself on 127.0.0.1: one at an HTTP root, others under
/dynamix/, /empty/ and /many/ of a second server that has nothing at its root, so a root-relative URL fails. It also opens
the site from file://. --base checks an already served checkout with the catalog it serves (fixture-only cases are
skipped); --root additionally opens that checkout from file://.

Besides its own assertions, a run fails on any uncaught exception, console error, HTTP error, failed request, request
that leaves the local server or its mount path (each is blocked before it reaches the network), or JavaScript dialog,
apart from failures the smoke causes on purpose (Monitor.expect). The exit status is nonzero on any failure.
Adapted from FridrichMethod/deemo tests/browser_smoke.py (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8)."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/ui"
sys.path.insert(0, str(ROOT / "scripts"))
from render_slideshow import render_slideshow  # noqa: E402

UI_FILES = ["archive.html", "site.webmanifest", "browserconfig.xml"]
UI_DIRS = ["src", "assets/site", "templates"]
PORTS = range(18700, 18800)
VIEWPORTS = {"desktop": (1440, 900), "phone": (375, 812)}
BOTTOM_ROW = ["photo", "github", "archive-link", "random"]
INJECTION = "<img src=x onerror=alert(1)>"
HAS_HAN = "(text) => /[\\u3400-\\u9fff]/.test(text)"
IMAGE_SHOWN = "(() => { const img = document.getElementById('full-image'); return img.complete && img.naturalWidth > 0; })()"
SLIDE_SHOWN = "imgTargets[slideIndex - 1].naturalWidth > 0 && imgTargets[slideIndex - 1].closest('.mySlides').classList.contains('is-current')"
SHOWN_ID = "imgTargets[slideIndex - 1].dataset.id"


# --------------------------------------------------------------------------------------------------------------------
# Sites and servers


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".webmanifest": "application/manifest+json",
                      ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".json": "application/json"}

    def log_message(self, *args):
        pass

    def handle(self):
        # The browser drops requests the smoke navigates away from; a closed socket is not a server error.
        try:
            super().handle()
        except ConnectionError:
            pass


def serve(directory: Path):
    """A threaded static server on 127.0.0.1, on the first free port in PORTS. The caller shuts it down."""
    handler = functools.partial(QuietHandler, directory=str(directory))
    for port in PORTS:
        try:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    raise SystemExit(f"No free loopback port in {PORTS.start}-{PORTS.stop - 1}")


def catalog_js(catalog):
    compact = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    return "window.DYNAMIX_CATALOG = " + compact.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + ";\n"


def build_site(destination: Path, catalog: dict):
    """The interface files of this checkout, the fixture's originals and previews, data/catalog.{json,js} and an
    index.html rendered by scripts/render_slideshow.py, as the catalog builder would write them."""
    destination.mkdir(parents=True)
    for name in UI_FILES:
        shutil.copy2(ROOT / name, destination / name)
    for name in UI_DIRS:
        shutil.copytree(ROOT / name, destination / name)
    shutil.copytree(FIXTURE / "assets/public", destination / "assets/public")
    shutil.copytree(FIXTURE / "assets/thumbs", destination / "assets/thumbs")
    (destination / "data").mkdir()
    (destination / "data/catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "data/catalog.js").write_text(catalog_js(catalog), encoding="utf-8")
    (destination / "index.html").write_text(render_slideshow(destination, catalog), encoding="utf-8")


def hostile(catalog: dict) -> dict:
    """A catalog the builder would never write: script and data URLs where files and pages belong. The page must still
    render, link nothing unsafe and run nothing. (No slideshow entries: the renderer rejects such paths outright.)"""
    bad = json.loads(json.dumps(catalog))
    site = next(asset for asset in bad["assets"] if asset["gallery"] and asset["site_original"] and asset["thumb"])
    site.update({"url": "javascript:alert(7)", "thumb": "java\tscript:alert(8)"})
    archived = next(asset for asset in bad["assets"] if asset["gallery"] and not asset["site_original"])
    archived["archive_url"] = "javascript:alert(9)"
    for asset in bad["assets"]:
        asset["slideshow"] = False
        for record in asset["provenance"]:
            record["page_url"] = "data:text/html,<script>alert(10)</script>"
            record["download_url"] = " javascript:alert(11)"
            record["mapping_evidence"] = ["javascript:alert(12)", {"url": "vbscript:x", "quote": "<b>q</b>"}]
    for source in bad["sources"]:
        source["url"] = "javascript:alert(13)"
    return bad


def many(catalog: dict, copies: int = 13) -> dict:
    """More than two pages of cards: every gallery entry repeated under new ids (same files)."""
    bulk = json.loads(json.dumps(catalog))
    clones = []
    for round_ in range(copies):
        for asset in catalog["assets"]:
            if asset["gallery"]:
                clone = json.loads(json.dumps(asset))
                clone["id"] = f"{asset['id']}:copy{round_:02d}"
                clone["title"] = f"Bulk {round_:02d} {asset['title']}"
                clone["variants"] = []
                clone["slideshow"] = False
                clones.append(clone)
    bulk["assets"] = catalog["assets"] + clones
    bulk["summary"]["gallery_images"] = sum(asset["gallery"] for asset in bulk["assets"])
    bulk["summary"]["unique_files"] = len(bulk["assets"])
    return bulk


# --------------------------------------------------------------------------------------------------------------------
# Monitoring


class Monitor:
    """Every sign of trouble on the pages it watches: uncaught exceptions, console errors, HTTP errors, failed requests,
    JavaScript dialogs and requests outside the allowed mounts (blocked before they leave the browser). Loads the smoke
    makes fail on purpose are expected (expect(url) first, and the failure must then happen); requests still pending
    when the smoke leaves a page are released."""

    def __init__(self):
        self.problems, self.urls, self.blocked = [], [], []
        self.expected = {}
        self.pending = {}
        self.released = set()

    def watch(self, page, mount: str | None):
        pending = self.pending.setdefault(page, set())

        def route(route):
            url = route.request.url
            scheme = urlparse(url).scheme
            if scheme in {"http", "https"} and (mount is None or not url.startswith(mount)):
                self.blocked.append(url)
                route.abort("blockedbyclient")
            else:
                route.continue_()

        def request(request):
            self.urls.append(request.url)
            pending.add(request)

        def failed(request):
            pending.discard(request)
            if request in self.released or request.url in self.blocked:
                return
            if not self.deliberate(request.url):
                self.problems.append(f"request failed ({request.failure}): {request.url}")

        def response(response):
            if response.status >= 400 and not self.deliberate(response.url):
                self.problems.append(f"HTTP {response.status}: {response.url}")

        def console(message):
            if message.type == "error" and not self.deliberate(message.location.get("url", "")):
                self.problems.append(f"console error on {page.url}: {message.text}")

        def dialog(dialog):
            self.problems.append(f"JavaScript {dialog.type} dialog on {page.url}: {dialog.message}")
            dialog.dismiss()

        page.route("**/*", route)
        page.on("request", request)
        page.on("requestfinished", lambda request: pending.discard(request))
        page.on("requestfailed", failed)
        page.on("response", response)
        page.on("console", console)
        page.on("dialog", dialog)
        page.on("pageerror", lambda error: self.problems.append(f"uncaught exception on {page.url}: {error}"))

    def deliberate(self, url):
        if url not in self.expected:
            return False
        self.expected[url] += 1
        return True

    def expect(self, url):
        self.expected.setdefault(url, 0)

    def settle(self, page, timeout=5):
        """Wait until the page's own loads are done. Chrome's favicon fetch and a duplicate of an in-flight image (when
        a re-render replaces a loading <img>) may never report completion; after the timeout, whatever is still pending
        is released by leave() rather than reported."""
        pending = self.pending.setdefault(page, set())
        deadline = time.monotonic() + timeout
        while any(not request.url.endswith(("favicon.svg", "favicon.ico")) for request in pending) and time.monotonic() < deadline:
            page.wait_for_timeout(50)

    def leave(self, page):
        self.settle(page)
        self.released |= self.pending[page]
        self.pending[page].clear()

    def verdict(self):
        unused = [url for url, seen in self.expected.items() if not seen]
        problems = list(self.problems)
        if self.blocked:
            problems.append(f"requests outside the site (blocked): {sorted(set(self.blocked))}")
        if unused:
            problems.append(f"loads the smoke made fail never failed: {unused}")
        return problems


# --------------------------------------------------------------------------------------------------------------------
# Harness


class Smoke:
    def __init__(self, browser, monitor):
        self.browser, self.monitor = browser, monitor
        self.passed, self.failed = [], []
        self.open_pages = []
        self.measurements = {}
        self.only = []

    def page(self, mount, width=1440, height=900, **options):
        """A page in a context of its own (fresh storage), watched by the monitor; mount is the allowed URL prefix."""
        context = self.browser.new_context(viewport={"width": width, "height": height}, **options)
        page = context.new_page()
        self.monitor.watch(page, mount)
        self.open_pages.append(page)
        return page

    def goto(self, page, url):
        self.monitor.leave(page)
        page.goto(url, wait_until="load")

    def finish(self, page):
        self.monitor.leave(page)
        page.context.close()
        self.open_pages.remove(page)

    def run(self, name, check, *args):
        if self.only and not any(part in name for part in self.only):
            return
        started = time.monotonic()
        try:
            check(self, *args)
        except Exception as error:  # noqa: BLE001 - every failure is reported and fails the run
            self.failed.append({"check": name, "error": f"{type(error).__name__}: {error}", "trace": traceback.format_exc(limit=4)})
            print(f"FAIL {name}: {type(error).__name__}: {error}", file=sys.stderr)
            for page in list(self.open_pages):  # a failed check leaves its pages open; close them quietly
                self.monitor.leave(page)
                page.context.close()
                self.open_pages.remove(page)
        else:
            self.passed.append(f"{name} ({time.monotonic() - started:.1f}s)")
            print(f"ok   {name}", file=sys.stderr)


def count(page):
    return page.evaluate("parseInt(document.getElementById('count').textContent, 10)")


def catalog_count(page, predicate):
    return page.evaluate(f"window.DYNAMIX_CATALOG.assets.filter((a) => a.gallery && ({predicate})(a)).length")


def open_archive(smoke, page, base, query=""):
    smoke.goto(page, urljoin(base, "archive.html" + query))
    page.wait_for_function("document.getElementById('count').textContent.includes('/')")


def find(catalog, predicate):
    for asset in catalog["assets"]:
        if predicate(asset):
            return asset
    return None


def no_horizontal_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= innerWidth")


# --------------------------------------------------------------------------------------------------------------------
# Archive checks


def check_archive_filters(smoke, base, catalog):
    """Counts, search, empty state, reset, family/source/kind/minimum filters and sorts, each against the catalog."""
    page = smoke.page(base)
    open_archive(smoke, page, base)
    total = page.evaluate("window.DYNAMIX_CATALOG.assets.filter((a) => a.gallery).length")
    assert total == count(page), (total, count(page))
    assert page.locator(".card").count() == min(60, total)
    if not total:
        assert page.locator("#empty").is_visible(), "an empty catalog says so"
        smoke.finish(page)
        return
    first_title = page.evaluate("window.DYNAMIX_CATALOG.assets.find((a) => a.gallery).title")
    word = next((part for part in first_title.split() if part.isalnum() and len(part) > 2), first_title[:3])
    page.locator("#query").fill(word)
    matched = count(page)
    assert 1 <= matched <= total and page.locator(".card").count() == min(60, matched), (word, matched)
    page.locator("#query").fill("definitely-no-such-dynamix-artwork-xyz")
    assert count(page) == 0 and page.locator("#empty").is_visible()
    page.locator("#reset-filters").click()
    assert count(page) == total
    for family in ("artists", "official", "wikis", "archives"):
        expected = catalog_count(page, f"(a) => a.provenance.some((p) => p.family === '{family}')")
        page.locator("#family").select_option(family)
        assert count(page) == expected, (family, count(page), expected)
        options = page.evaluate("[...document.querySelectorAll('#source option')].map((o) => o.value).filter(Boolean)")
        families = page.evaluate("(ids) => ids.map((id) => window.DYNAMIX_CATALOG.sources.find((s) => s.id === id).family)", options)
        assert set(families) <= {family}, (family, families)
    page.locator("#reset-filters").click()
    sources = page.evaluate("[...document.querySelectorAll('#source option')].map((o) => o.value).filter(Boolean)")
    assert sources, "the source list offers the catalog's sources"
    for source in sources:
        page.locator("#source").select_option(source)
        expected = catalog_count(page, f"(a) => a.provenance.some((p) => p.source_id === {json.dumps(source)})")
        assert count(page) == expected, (source, count(page), expected)
    page.locator("#reset-filters").click()
    kinds = page.evaluate("[...new Set(window.DYNAMIX_CATALOG.assets.filter((a) => a.gallery).map((a) => a.kind))]")
    for kind in kinds:
        page.locator("#kind").select_option(kind)
        assert count(page) == catalog_count(page, f"(a) => a.kind === '{kind}'"), kind
    page.locator("#kind").select_option("reference")
    references = page.evaluate("window.DYNAMIX_CATALOG.assets.filter((a) => !a.gallery).length")
    assert count(page) == references and page.locator(".card").count() == min(60, references)
    page.locator("#reset-filters").click()
    page.locator("#minimum").select_option("1000")
    large = catalog_count(page, "(a) => Math.max(a.width, a.height) >= 1000")
    assert count(page) == large
    sizes = page.evaluate("[...document.querySelectorAll('.card-image img')].map((img) => Math.max(+img.getAttribute('width'), +img.getAttribute('height')))")
    assert all(size >= 1000 for size in sizes), sizes
    page.locator("#reset-filters").click()
    page.locator("#sort").select_option("resolution")
    areas = page.evaluate("[...document.querySelectorAll('.card-image img')].map((img) => +img.getAttribute('width') * +img.getAttribute('height'))")
    assert areas == sorted(areas, reverse=True), areas
    page.locator("#sort").select_option("title")
    titles = page.locator(".card h2").all_inner_texts()
    assert titles == page.evaluate("(titles) => [...titles].sort(new Intl.Collator('en', {numeric: true}).compare)", titles), titles
    page.locator("#sort").select_option("source")
    order = page.evaluate("""() => { const rank = ['artists', 'official', 'wikis', 'archives'];
        const shown = [...document.querySelectorAll('.card h2')].map((h) => h.textContent);
        const assets = window.DYNAMIX_CATALOG.assets.filter((a) => a.gallery);
        return shown.map((title) => rank.indexOf(assets.find((a) => a.title === title).family)); }""")
    assert order == sorted(order), order
    # Filters live in the query string and come back on reload; values that match no option are ignored.
    page.locator("#family").select_option("wikis")
    page.locator("#minimum").select_option("1000")
    assert "family=wikis" in page.url and "minimum=1000" in page.url, page.url
    smoke.monitor.leave(page)
    page.reload(wait_until="load")
    page.wait_for_function("document.getElementById('count').textContent.includes('/')")
    assert [page.locator("#family").input_value(), page.locator("#minimum").input_value()] == ["wikis", "1000"]
    open_archive(smoke, page, base, "?minimum=4321&sort=bogus&family=nope&kind=x&source=nobody")
    state = page.evaluate("['minimum', 'sort', 'family', 'kind', 'source'].map((id) => document.getElementById(id).value)")
    assert state == ["0", "source", "", "", ""], state
    smoke.finish(page)


def check_pagination(smoke, base):
    page = smoke.page(base)
    open_archive(smoke, page, base)
    total = count(page)
    assert total > 120, f"the bulk catalog has {total} gallery files"
    assert page.locator(".card").count() == 60 and page.locator("#more").is_visible()
    page.locator("#more").click()
    assert page.locator(".card").count() == 120
    page.locator("#more").click()
    assert page.locator(".card").count() == total and page.locator("#more").is_hidden()
    # Closing the viewer after Next past the drawn cards draws up to the image shown and focuses its card.
    page.locator("#reset-filters").click()
    page.locator(".card-image").nth(59).click()
    page.locator("#next").click()
    page.locator("#next").click()
    assert page.locator("#position").inner_text().startswith("62 / ")
    page.keyboard.press("Escape")
    page.wait_for_function("document.activeElement === document.querySelectorAll('.card-image')[61]", timeout=3000)
    smoke.finish(page)


def check_viewer(smoke, base, catalog, fixture):
    """Open/close, keyboard, focus trap and return, URL fragment, deep link, download targets and provenance text."""
    page = smoke.page(base)
    open_archive(smoke, page, base)
    if not catalog["assets"]:
        smoke.finish(page)
        return
    cards = page.locator(".card-image")
    cards.first.click()
    viewer = page.locator("#viewer")
    assert viewer.evaluate("(el) => el.open")
    opened = page.evaluate("decodeURIComponent(location.hash.slice(1))")
    assert opened == page.locator(".card").first.get_attribute("data-id"), "the fragment names the open file"
    assert page.evaluate("(id) => window.DYNAMIX_CATALOG.assets.find((a) => a.id === id).title", opened) == page.locator("#viewer-title").inner_text()
    position = page.locator("#position").inner_text()
    page.keyboard.press("ArrowRight")
    assert page.locator("#position").inner_text() != position or count(page) == 1
    page.keyboard.press("ArrowLeft")
    assert page.locator("#position").inner_text() == position
    for _ in range(25):  # Tab stays inside the modal dialog
        page.keyboard.press("Tab")
        inside = page.evaluate("document.getElementById('viewer').contains(document.activeElement) || document.activeElement === document.body")
        assert inside, page.evaluate("document.activeElement.outerHTML.slice(0, 120)")
    page.keyboard.press("Escape")
    assert not viewer.evaluate("(el) => el.open")
    page.wait_for_function("document.activeElement === document.querySelector('.card-image')", timeout=3000)
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none", "keyboard focus is visible"
    page.wait_for_function("location.hash === ''", timeout=3000)  # the close event, which clears it, is asynchronous
    # Deep link: archive.html#<id> opens that file, for every catalog entry (gallery or not).
    for asset in catalog["assets"][: (None if fixture else 5)]:
        smoke.goto(page, urljoin(base, "archive.html#" + quote(asset["id"], safe="")))
        page.wait_for_function("document.getElementById('viewer').open", timeout=5000)
        assert page.locator("#viewer-title").inner_text() == asset["title"], asset["id"]
        download, original, slideshow = page.locator("#download"), page.locator("#original"), page.locator("#slideshow")
        if asset["site_original"]:
            assert download.get_attribute("href") == asset["url"], (asset["id"], download.get_attribute("href"))
            assert download.get_attribute("download") == asset["path"].split("/")[-1]
            assert original.is_visible() and original.get_attribute("href") == asset["url"]
        else:
            assert download.get_attribute("href") == asset["archive_url"], (asset["id"], download.get_attribute("href"))
            assert download.get_attribute("target") == "_blank" and "noopener" in download.get_attribute("rel")
            assert download.get_attribute("download") is None and original.is_hidden()
            assert page.locator("#archive-note").is_visible()
            if asset["thumb"]:
                assert page.evaluate("document.getElementById('full-image').getAttribute('src')") == asset["thumb"]
            else:
                assert page.locator("#no-preview").is_visible()
        assert slideshow.is_visible() == bool(asset["slideshow"]), asset["id"]
        if asset["slideshow"]:
            assert slideshow.get_attribute("href") == "index.html#" + quote(asset["id"], safe=""), slideshow.get_attribute("href")
        if asset["format"] != "PDF" and (asset["site_original"] or asset["thumb"]):
            page.wait_for_function(IMAGE_SHOWN, timeout=5000)
        blocks = page.locator("#provenance .provenance-item").count()
        assert blocks == len(asset["provenance"]), (asset["id"], blocks)
        credits = page.locator("#viewer-credits").inner_text()
        for role in ("illustrators", "composers"):
            names = asset[role]
            assert all(name in credits for name in names), (role, names, credits)
        if not asset["illustrators"]:
            assert page.evaluate("DYNAMIX_I18N.t('credit.unknown')") in credits
    smoke.finish(page)


def check_provenance_text(smoke, base, catalog):
    """Comparisons read matched / mismatched / unknown in both languages; a javascript: page URL is never a link;
    quality labels and the rights holder appear for each record."""
    duplicate = find(catalog, lambda a: len(a["provenance"]) > 2)
    hostile = find(catalog, lambda a: any(str(p["page_url"]).startswith("javascript:") for p in a["provenance"]))
    for lang in ("en", "zh-CN"):
        page = smoke.page(base)
        smoke.goto(page, urljoin(base, f"archive.html?lang={lang}#" + quote(duplicate["id"], safe="")))
        page.wait_for_function("document.getElementById('viewer').open")
        text = page.locator("#provenance").inner_text()
        t = lambda key, **values: page.evaluate("([key, values]) => DYNAMIX_I18N.t(key, values)", [key, values])
        field = t("comparison.sha1_match")
        assert t("comparison.matched", field=field) in text, (lang, "matched")
        mismatch = next(p for p in duplicate["provenance"] if p["comparisons"]["sha1_match"] is False)
        assert t("comparison.mismatched", field=field, reported=mismatch["source_reported"]["sha1"], observed=duplicate["sha1"]) in text, (lang, "mismatched")
        assert t("comparison.unknown", field=field) in text, (lang, "unknown")
        for record in duplicate["provenance"]:
            quality = record["quality"]
            line = t("provenance.quality", variant=t(f"quality.source_variant.{quality['source_variant']}"),
                     origin=t(f"quality.origin_status.{quality['origin_status']}"), master=t(f"quality.master_status.{quality['master_status']}"))
            assert line in text, (lang, line)
        assert t("provenance.rights_holder", holder=duplicate["rights"]["holder"]) in text
        assert lang == "en" or page.evaluate(HAS_HAN, t("comparison.unknown", field=field))
        smoke.goto(page, urljoin(base, f"archive.html?lang={lang}#" + quote(hostile["id"], safe="")))
        page.wait_for_function("document.getElementById('viewer').open")
        assert page.locator("a[href^='javascript:' i]").count() == 0
        heading = page.locator("#provenance .provenance-item > p").first
        assert heading.locator("a").count() == 0 and hostile["provenance"][0]["source_name"] in heading.inner_text()
        smoke.finish(page)


def check_injection(smoke, base, catalog):
    """Catalog strings render as text: card titles equal the catalog's, no injected element exists, no dialog fires."""
    page = smoke.page(base)
    open_archive(smoke, page, base)
    pairs = page.evaluate("""() => [...document.querySelectorAll('.card')].map((card) => [card.querySelector('h2').textContent,
        window.DYNAMIX_CATALOG.assets.find((a) => a.id === card.dataset.id).title])""")
    assert pairs and all(text == title for text, title in pairs), pairs[:3]
    shown = [text for text, _ in pairs]
    hostile = find(catalog, lambda a: INJECTION in a["title"])
    if hostile:
        assert hostile["title"] in shown
        smoke.goto(page, urljoin(base, "archive.html#" + quote(hostile["id"], safe="")))
        page.wait_for_function("document.getElementById('viewer').open")
        page.locator("#viewer details summary").click()
        assert page.locator("#viewer-title").inner_text() == hostile["title"]
        assert INJECTION in page.locator("#viewer-credits").inner_text()
        assert page.evaluate("document.querySelectorAll('img[src=\"x\"]').length") == 0, "no injected element"
        page.keyboard.press("Escape")
        page.locator(".source-inventory summary").first.click()
        page.locator("#failures summary").click()
        assert "</script>" in page.locator("#failures").inner_text()
        smoke.goto(page, urljoin(base, "index.html#" + quote(hostile["id"], safe="")))
        page.wait_for_function(f"{SHOWN_ID} === {json.dumps(hostile['id'])}")
        page.wait_for_function("document.querySelector('.slide-notes .notes-title') !== null")
        assert page.locator(".slide-notes .notes-title").inner_text().replace("\u00a0", " ") == hostile["title"]
        assert INJECTION in page.locator(".slide-notes").inner_text()
        assert page.locator("#asset-source").get_attribute("href") is None, "a javascript: page URL is not linked"
    page.wait_for_timeout(300)
    assert page.evaluate("document.querySelectorAll('img[src=\"x\"]').length") == 0, "no injected element"
    assert page.evaluate("[...document.scripts].every((s) => !s.textContent.includes('alert('))")
    smoke.finish(page)


def check_previews(smoke, base, catalog, fixture):
    """Cards show previews and never request a previewed original; small files without a preview show the original;
    a failing preview falls back once to a published original, and to a labelled tile for an archive-only file."""
    page = smoke.page(base)
    requested = []
    page.on("request", lambda request: requested.append(urlparse(request.url).path))
    open_archive(smoke, page, base)
    smoke.monitor.settle(page)
    cards = page.evaluate("""() => [...document.querySelectorAll('.card')].map((card) => {
        const asset = window.DYNAMIX_CATALOG.assets.find((a) => a.id === card.dataset.id);
        return [card.querySelector('img')?.getAttribute('src') || null, asset.thumb || null, asset.url, asset.site_original]; })""")
    assert len(cards) == page.locator(".card").count() > 0
    for src, thumb, url, site in cards:
        assert src == (thumb or (url if site else None)), (src, thumb, url, site)
    originals = {urlparse(urljoin(base, url)).path for _, thumb, url, _ in cards if thumb}
    assert not originals & set(requested), sorted(originals & set(requested))[:3]
    image_bytes = page.evaluate("performance.getEntriesByType('resource').filter((e) => e.initiatorType === 'img').reduce((sum, e) => sum + e.encodedBodySize, 0)")
    assert image_bytes < 2_000_000, image_bytes
    smoke.measurements[f"first-screen grid image bytes at 1440x900, {base}"] = image_bytes
    smoke.finish(page)
    if not fixture:
        return
    site_thumb = find(catalog, lambda a: a["gallery"] and a["site_original"] and a["thumb"])
    archive_thumb = find(catalog, lambda a: a["gallery"] and not a["site_original"] and a["thumb"])
    for asset, fallback in ((site_thumb, site_thumb["url"]), (archive_thumb, None)):
        page = smoke.page(base)
        requested = []
        page.on("request", lambda request: requested.append(urlparse(request.url).path))
        preview = urljoin(base, asset["thumb"])
        smoke.monitor.expect(preview)
        page.route(preview, lambda route: route.abort())
        open_archive(smoke, page, base, "?query=" + quote(asset["title"].split()[0]))
        card = page.locator(f".card[data-id={json.dumps(asset['id'])}]")
        assert card.count() == 1, asset["id"]
        if fallback:
            page.wait_for_function("(card) => { const img = card.querySelector('img'); return img && img.getAttribute('src') !== null && img.complete && img.naturalWidth > 0; }",
                                   arg=card.element_handle(), timeout=10_000)
            assert card.locator("img").get_attribute("src") == fallback
        else:
            card.locator(".card-placeholder").wait_for(timeout=10_000)
            assert card.locator("img").count() == 0
            assert urlparse(urljoin(base, asset["url"])).path not in requested, "an archive-only original is never requested"
        smoke.finish(page)


def check_language(smoke, base, catalog):
    """English by default; the toggle switches in place, keeps filters, writes ?lang= and localStorage dynamix-lang;
    ?lang= wins over storage; links carry the language; the slideshow follows."""
    page = smoke.page(base)
    open_archive(smoke, page, base)
    heading_has_han = f"({HAS_HAN})(document.querySelector('h1').textContent)"
    assert page.evaluate("document.documentElement.lang") == "en" and not page.evaluate(heading_has_han)
    assert page.evaluate("document.title") == "Dynamix Artwork Archive"
    page.locator("#family").select_option("wikis")
    english = page.locator("#count").inner_text()
    page.locator("[data-lang-toggle]").first.click()
    assert page.evaluate("document.documentElement.lang") == "zh-CN" and page.evaluate(heading_has_han)
    assert page.evaluate("document.title") == "Dynamix 曲绘档案"
    assert "lang=zh-CN" in page.url and "family=wikis" in page.url and page.locator("#family").input_value() == "wikis"
    assert page.locator("#count").inner_text() != english
    assert page.evaluate("localStorage.getItem('dynamix-lang')") == "zh-CN"
    assert "lang=zh-CN" in page.locator("nav a[data-lang-link]").get_attribute("href")
    open_archive(smoke, page, base)
    assert page.evaluate("document.documentElement.lang") == "zh-CN", "the choice persists"
    open_archive(smoke, page, base, "?lang=en")
    assert page.evaluate("[document.documentElement.lang, localStorage.getItem('dynamix-lang')]") == ["en", "en"]
    smoke.monitor.leave(page)
    page.reload(wait_until="load")
    assert "lang=" not in page.url and page.evaluate("document.documentElement.lang") == "en"
    if catalog["assets"] and any(asset["slideshow"] for asset in catalog["assets"]):
        smoke.goto(page, urljoin(base, "index.html?lang=zh-CN"))
        page.wait_for_function("document.querySelector('.dynamix-view').style.opacity === '1'")
        assert page.evaluate("document.documentElement.lang") == "zh-CN"
        assert "lang=zh-CN" in page.locator(".archive-link a").get_attribute("href")
        page.locator("[data-lang-toggle]").click()
        assert page.evaluate("document.documentElement.lang") == "en" and "lang=" not in page.url
        assert "lang=" not in page.locator(".archive-link a").get_attribute("href")
    smoke.finish(page)
    # Translations apply as soon as the page is parsed, before the catalog script (held back here) arrives.
    page = smoke.page(base)
    held = []
    page.route(urljoin(base, "data/catalog.js"), lambda route: held.append(route))
    page.goto(urljoin(base, "archive.html?lang=zh-CN"), wait_until="commit")
    page.wait_for_function(f"({HAS_HAN})(document.querySelector('h1')?.textContent || '')", timeout=5000)
    assert page.evaluate("window.DYNAMIX_CATALOG === undefined")
    for route in held:
        route.continue_()
    page.wait_for_function("document.getElementById('count').textContent.includes('/')")
    smoke.finish(page)


def check_missing_catalog(smoke, base):
    page = smoke.page(base)
    url = urljoin(base, "data/catalog.js")
    smoke.monitor.expect(url)
    page.route(url, lambda route: route.fulfill(status=404, body="missing"))
    smoke.goto(page, urljoin(base, "archive.html"))
    page.wait_for_function("document.getElementById('count').textContent.includes('catalog.js')")
    smoke.finish(page)


def check_layout(smoke, base, catalog, lang, size):
    """No horizontal scroll; the viewer is usable (close button and download link reachable); slideshow controls in view."""
    width, height = VIEWPORTS[size]
    page = smoke.page(base, width, height)
    open_archive(smoke, page, base, f"?lang={lang}")
    assert no_horizontal_scroll(page), (lang, size, "archive")
    page.locator(".source-inventory summary").first.click()
    assert no_horizontal_scroll(page), (lang, size, "inventory")
    if page.locator(".card-image").count():
        page.locator(".card-image").first.click()
        close, download = page.locator("#close"), page.locator("#download")
        assert close.is_visible() and close.bounding_box()["x"] + close.bounding_box()["width"] <= width
        download.scroll_into_view_if_needed()
        assert download.is_visible()
        box = download.bounding_box()
        assert 0 <= box["x"] and box["x"] + box["width"] <= width and 0 <= box["y"] < height, box
        close.click()
    if any(asset["slideshow"] for asset in catalog["assets"]):
        smoke.goto(page, urljoin(base, f"index.html?lang={lang}"))
        page.wait_for_function("document.querySelector('.dynamix-view').style.opacity === '1'")
        page.wait_for_timeout(400)
        assert no_horizontal_scroll(page), (lang, size, "slideshow")
        for name in BOTTOM_ROW + ["lang-toggle"]:
            box = page.locator(f".{name}").first.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width and box["y"] + box["height"] <= height, (name, box)
        boxes = [page.locator(f".{name}").first.bounding_box() for name in BOTTOM_ROW]
        assert all(left["x"] + left["width"] <= right["x"] for left, right in zip(boxes, boxes[1:])), boxes
        for arrow in (".prev", ".next"):
            assert page.locator(arrow).evaluate("""(node) => { const box = node.getBoundingClientRect();
                return node.contains(document.elementFromPoint((box.left + box.right) / 2, (box.top + box.bottom) / 2)); }"""), arrow
    smoke.finish(page)


# --------------------------------------------------------------------------------------------------------------------
# Slideshow checks


def open_slideshow(smoke, base, query, width=1440, height=900):
    page = smoke.page(base, width, height)
    smoke.goto(page, urljoin(base, "index.html" + query))
    page.wait_for_function("document.querySelector('.dynamix-view').style.opacity === '1'")
    page.wait_for_function(SLIDE_SHOWN, timeout=10_000)
    return page


def check_slideshow(smoke, base, catalog, fixture):
    """Deep links (?asset= and #id), arrows and keys, autoplay toggle, caption links, credits, control names."""
    slides = [asset for asset in catalog["assets"] if asset["slideshow"]]
    if not slides:
        return
    target = slides[0]
    for query in (f"?asset={quote(target['id'], safe='')}", f"#{quote(target['id'], safe='')}"):
        page = open_slideshow(smoke, base, query)
        assert page.evaluate(SHOWN_ID) == target["id"], query
        assert page.locator(".random button").get_attribute("aria-pressed") == "false", "a deep link pauses autoplay"
        assert page.locator("#asset-title").inner_text() == target["title"]
        original = page.locator("#asset-original")
        assert original.get_attribute("href") == target["url"] and original.get_attribute("download") is not None
        source = page.locator("#asset-source")
        expected = page.evaluate("""(value) => { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; }
            catch { return null; } }""", target["page_url"])
        assert source.get_attribute("href") == expected, (source.get_attribute("href"), expected)
        if len(slides) > 1:
            page.locator(".next").click()
            assert page.evaluate(SHOWN_ID) != target["id"]
            page.locator(".prev").click()
            assert page.evaluate(SHOWN_ID) == target["id"]
            page.evaluate("document.activeElement.blur()")
            page.keyboard.press("ArrowRight")
            assert page.evaluate(SHOWN_ID) == slides[1]["id"]
            page.keyboard.press("ArrowLeft")
            assert page.evaluate(SHOWN_ID) == target["id"]
        smoke.finish(page)
    page = open_slideshow(smoke, base, "")
    clock = page.locator(".random button")
    assert clock.get_attribute("aria-pressed") == "true" and page.evaluate("randomFlag") == 0, "plain index.html autoplays"
    clock.click()
    assert [clock.get_attribute("aria-pressed"), page.evaluate("randomFlag")] == ["false", 1]
    clock.click()
    assert [clock.get_attribute("aria-pressed"), page.evaluate("randomFlag")] == ["true", 0]
    # Tab runs along the bottom row as it is drawn.
    row = []
    for _ in range(30):
        page.keyboard.press("Tab")
        stop = page.evaluate("""(names) => { const control = document.activeElement.closest(names.map((name) => '.' + name).join(', '));
            return control && [names.find((name) => control.classList.contains(name)), document.activeElement.getBoundingClientRect().left]; }""", BOTTOM_ROW)
        if stop and stop[0] not in [name for name, _ in row]:
            row.append(stop)
        if len(row) == len(BOTTOM_ROW):
            break
    assert [name for name, _ in row] == BOTTOM_ROW, row
    smoke.finish(page)
    for lang in ("en", "zh-CN"):
        page = open_slideshow(smoke, base, f"?asset={quote(target['id'], safe='')}&lang={lang}")
        for role, key in (("button", "slideshow.nav.prev"), ("button", "slideshow.nav.next"), ("button", "slideshow.screenshot.take"),
                          ("button", "slideshow.autoplay.label"), ("link", "slideshow.github.label"), ("link", "slideshow.archive.text"),
                          ("link", "slideshow.attribution")):
            name = page.evaluate("(key) => DYNAMIX_I18N.t(key)", key)
            assert page.get_by_role(role, name=name, exact=True).count() == 1, (lang, role, name)
            assert (lang == "zh-CN") == page.evaluate(HAS_HAN, name), (lang, name)
        smoke.finish(page)
    if fixture:
        unknown = next(asset for asset in slides if not asset["illustrators"])
        page = open_slideshow(smoke, base, f"?asset={quote(unknown['id'], safe='')}")
        page.wait_for_function("document.querySelector('.slide-notes dd.notes-artist') !== null")
        assert page.locator(".slide-notes dd.notes-artist").text_content() == "Unknown"
        assert page.locator(".slide-notes dt").all_text_contents()[:2] == ["Composer", "Illustrator"]
        smoke.finish(page)


def check_screenshot_overlay(smoke, base, catalog):
    slides = [asset for asset in catalog["assets"] if asset["slideshow"]]
    if not slides:
        return
    page = open_slideshow(smoke, base, f"?asset={quote(slides[0]['id'], safe='')}#screenshot")
    overlay_open = "document.getElementById('screenshot').classList.contains('is-open')"
    assert not page.evaluate(overlay_open), "a #screenshot URL opens nothing"
    history = page.evaluate("[history.length, location.href]")
    page.locator(".photo button").click()
    page.wait_for_function(overlay_open, timeout=20_000)
    assert page.evaluate("Boolean(document.activeElement.closest('#screenshot'))")
    assert page.evaluate("document.querySelector('#screenshot-content canvas') !== null")
    start = page.evaluate("slideIndex")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("slideIndex") == start, "the arrow keys wait while the overlay is open"
    page.keyboard.press("Escape")
    assert not page.evaluate(overlay_open)
    assert page.evaluate("document.getElementById('screenshot-content').childElementCount") == 0
    assert page.evaluate("document.activeElement === document.querySelector('.photo button')")
    assert page.evaluate("[history.length, location.href]") == history
    smoke.finish(page)


def check_empty(smoke, base):
    """An empty catalog: the archive says so and still renders its inventory; the slideshow keeps its title card."""
    page = smoke.page(base)
    open_archive(smoke, page, base)
    assert count(page) == 0 and page.locator(".card").count() == 0
    assert page.locator("#empty").is_visible()
    assert page.locator("#empty").inner_text() == page.evaluate("DYNAMIX_I18N.t('results.empty_catalog')")
    page.locator(".source-inventory summary").first.click()
    assert page.locator("#inventory-summary").inner_text()
    smoke.goto(page, urljoin(base, "index.html"))
    page.wait_for_function("document.querySelector('.dynamix-view').style.opacity === '1'")
    assert page.locator(".intro-empty").is_visible() and page.locator(".prev").is_hidden() and page.locator(".asset-caption").is_hidden()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(200)
    assert page.evaluate("document.querySelectorAll('.mySlides').length") == 0
    smoke.finish(page)


UNSAFE_LINKS = """() => [...document.querySelectorAll('[href], [src]')].map((node) => node.href || node.src || '')
    .filter((url) => url && !/^(https?:|file:|#)/i.test(url))"""


def check_hostile(smoke, base):
    page = smoke.page(base)
    open_archive(smoke, page, base)
    assert count(page) > 0
    assert page.evaluate(UNSAFE_LINKS) == [], page.evaluate(UNSAFE_LINKS)
    catalog = page.evaluate("window.DYNAMIX_CATALOG")
    for asset in catalog["assets"]:
        smoke.goto(page, urljoin(base, "archive.html#" + quote(asset["id"], safe="")))
        page.wait_for_function("document.getElementById('viewer').open")
        page.locator(".source-inventory summary").first.evaluate("(node) => node.parentElement.open = true")
        assert page.evaluate(UNSAFE_LINKS) == [], (asset["id"], page.evaluate(UNSAFE_LINKS))
        if asset["url"].startswith("javascript:"):
            assert page.locator("#download").is_hidden() and page.locator("#original").is_hidden()
        if asset["archive_url"].startswith("javascript:"):
            assert page.locator("#download").is_hidden() and page.locator("#archive-note").is_visible()
    page.wait_for_timeout(300)
    smoke.finish(page)


def check_subpath_requests(smoke, origin, mounts):
    """Every request to the subpath server stayed under one of its mounts (pages there are watched with their mount as
    the allowed prefix, so the monitor blocks the rest; this names the result). Its root holds no site, so a
    root-relative URL would have failed as well."""
    seen = [url for url in smoke.monitor.urls if url.startswith(origin)]
    assert seen, "the subpath site was exercised"
    outside = [url for url in seen if not any(urlparse(url).path.startswith(mount) for mount in mounts)]
    assert not outside, outside


def check_file_url(smoke, root: Path, catalog):
    """Opened from disk: the archive lists the catalog and opens the viewer; the slideshow shows a slide."""
    page = smoke.page(None)
    page.goto((root / "archive.html").as_uri(), wait_until="load")
    page.wait_for_function("document.getElementById('count').textContent.includes('/')")
    gallery = sum(asset["gallery"] for asset in catalog["assets"])
    assert count(page) == gallery
    if gallery:
        page.locator(".card-image").first.click()
        page.wait_for_function(IMAGE_SHOWN, timeout=5000)
        page.keyboard.press("Escape")
    page.locator("[data-lang-toggle]").first.click()
    assert page.evaluate("document.documentElement.lang") == "zh-CN"
    if any(asset["slideshow"] for asset in catalog["assets"]):
        smoke.goto(page, (root / "index.html").as_uri())
        page.wait_for_function(SLIDE_SHOWN, timeout=10_000)
    smoke.finish(page)


def check_without_javascript(smoke, base):
    """With JavaScript off, a phone's first screen explains it in both languages, and nothing claims to load."""
    page = smoke.page(base, 375, 667, java_script_enabled=False)
    smoke.goto(page, urljoin(base, "archive.html"))
    notice = page.locator(".noscript")
    box = notice.bounding_box()
    assert notice.is_visible() and box["y"] + box["height"] <= 667, box
    assert "JavaScript" in notice.inner_text() and "需要 JavaScript" in notice.inner_text()
    assert not page.locator("#count").is_visible()
    smoke.finish(page)


def check_site_icons(smoke, base):
    page = smoke.page(base)
    smoke.goto(page, urljoin(base, "archive.html"))
    manifest_url = urljoin(base, "site.webmanifest")
    manifest = page.request.get(manifest_url)
    assert manifest.status == 200
    for icon in manifest.json()["icons"]:
        response = page.request.get(urljoin(manifest_url, icon["src"]))
        assert response.status == 200 and response.body(), icon
    for path in ("browserconfig.xml", "assets/site/icons/favicon.ico", "assets/site/icons/favicon.svg"):
        assert page.request.get(urljoin(base, path)).status == 200, path
    smoke.finish(page)


# --------------------------------------------------------------------------------------------------------------------


def run_suite(smoke, base, catalog, fixture, label):
    smoke.run(f"{label}: archive filters, search, sorts, URL state", check_archive_filters, base, catalog)
    smoke.run(f"{label}: viewer, keyboard, focus, deep links, download targets", check_viewer, base, catalog, fixture)
    smoke.run(f"{label}: grid previews and bounded fallback", check_previews, base, catalog, fixture)
    smoke.run(f"{label}: language toggle, ?lang= and localStorage", check_language, base, catalog)
    smoke.run(f"{label}: catalog strings render as text", check_injection, base, catalog)
    smoke.run(f"{label}: slideshow deep links, controls and names", check_slideshow, base, catalog, fixture)
    smoke.run(f"{label}: screenshot overlay", check_screenshot_overlay, base, catalog)
    smoke.run(f"{label}: notice without JavaScript", check_without_javascript, base)
    for lang in ("en", "zh-CN"):
        for size in VIEWPORTS:
            smoke.run(f"{label}: layout {lang} {size}", check_layout, base, catalog, lang, size)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base", help="URL of an already served checkout (loopback only)")
    mode.add_argument("--fixture", action="store_true", help="build and serve sites from tests/fixtures/ui")
    parser.add_argument("--root", type=Path, help="with --base: the served checkout, opened from file:// as well")
    parser.add_argument("--workdir", type=Path, help="with --fixture: directory for the generated sites (default: a new temp dir)")
    parser.add_argument("--browser", default="/usr/bin/google-chrome")
    parser.add_argument("--only", action="append", default=[], help="run only checks whose name contains this text (repeatable)")
    args = parser.parse_args(argv)
    started = time.monotonic()
    servers, cleanup = [], None
    monitor = Monitor()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True, args=["--no-sandbox"])
        browser_version = browser.version
        smoke = Smoke(browser, monitor)
        smoke.only = args.only
        try:
            if args.fixture:
                if args.workdir:
                    args.workdir.mkdir(parents=True, exist_ok=True)
                    work = Path(tempfile.mkdtemp(prefix="browser-smoke-", dir=args.workdir))
                else:
                    work = cleanup = Path(tempfile.mkdtemp(prefix="dynamix-browser-smoke-"))
                catalog = json.loads((FIXTURE / "catalog.json").read_text(encoding="utf-8"))
                empty = json.loads((FIXTURE / "catalog-empty.json").read_text(encoding="utf-8"))
                build_site(work / "root", catalog)
                build_site(work / "mounted/dynamix", catalog)
                build_site(work / "mounted/empty", empty)
                build_site(work / "mounted/many", many(catalog))
                build_site(work / "mounted/hostile", hostile(catalog))
                servers = [serve(work / "root"), serve(work / "mounted")]
                root_base = f"http://127.0.0.1:{servers[0].server_port}/"
                mounted = f"http://127.0.0.1:{servers[1].server_port}/"
                run_suite(smoke, root_base, catalog, True, "root")
                smoke.run("root: provenance comparisons and unsafe links", check_provenance_text, root_base, catalog)
                smoke.run("root: missing catalog message", check_missing_catalog, root_base)
                smoke.run("root: manifest and icons", check_site_icons, root_base)
                run_suite(smoke, mounted + "dynamix/", catalog, True, "/dynamix/")
                smoke.run("/dynamix/: requests stay under the mount", check_subpath_requests, mounted, ("/dynamix/",))
                smoke.run("/empty/: empty catalog", check_empty, mounted + "empty/")
                smoke.run("/many/: 60 per page and focus after paging", check_pagination, mounted + "many/")
                smoke.run("/hostile/: script URLs in a catalog are never linked or loaded", check_hostile, mounted + "hostile/")
                smoke.run("subpath server: every request under /dynamix/, /empty/ or /many/", check_subpath_requests, mounted,
                          ("/dynamix/", "/empty/", "/many/", "/hostile/"))
                smoke.run("file://: archive and slideshow from disk", check_file_url, work / "root", catalog)
                base = root_base
            else:
                if urlparse(args.base).hostname not in {"127.0.0.1", "localhost"}:
                    raise SystemExit("Use a loopback server for --base")
                base = args.base.rstrip("/") + "/"
                probe = smoke.page(base)
                smoke.goto(probe, urljoin(base, "archive.html"))
                catalog = probe.evaluate("window.DYNAMIX_CATALOG || null")
                smoke.finish(probe)
                if catalog is None:
                    raise SystemExit("The served archive.html has no data/catalog.js")
                run_suite(smoke, base, catalog, False, "base")
                smoke.run("base: missing catalog message", check_missing_catalog, base)
                smoke.run("base: manifest and icons", check_site_icons, base)
                if args.root:
                    smoke.run("file://: archive and slideshow from disk", check_file_url, args.root.resolve(), catalog)
        finally:
            browser.close()
            for server in servers:
                server.shutdown()
                server.server_close()
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)
    problems = monitor.verdict()
    report = {"mode": "fixture" if args.fixture else "base", "base": base, "browser": browser_version, "passed": smoke.passed,
              "failed": smoke.failed, "measurements": smoke.measurements,
              "monitor_problems": problems, "requests": len(monitor.urls), "seconds": round(time.monotonic() - started, 1)}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if smoke.failed or problems else 0


if __name__ == "__main__":
    sys.exit(main())
