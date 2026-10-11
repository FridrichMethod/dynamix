"""Browser acceptance checks for archive.html and the slideshow (index.html), driving an installed Chrome via Playwright.

    python -I tests/browser_smoke.py --fixture [--workdir DIR] [--browser /usr/bin/google-chrome] [--only TEXT ...]
    python -I tests/browser_smoke.py --base http://127.0.0.1:8765/ [--root CHECKOUT] [--browser PATH] [--allow-empty]

--fixture builds sites from tests/fixtures/ui (the frozen catalog shape, including injection strings, archive-only files,
a PDF, unknown credits and an empty catalog) and serves them itself on 127.0.0.1: one at an HTTP root, others under
/dynamix/, /empty/, /many/, /hostile/ and /edges/ of a second server that has nothing at its root, so a root-relative URL
fails. It also opens the site from file://. --base checks an already served checkout with the catalog it serves
(fixture-only cases are skipped); --root additionally opens that checkout from file://.

Besides its own assertions, a run fails on any uncaught exception, console error, HTTP error, failed request, request
that leaves the local server or its mount path (each is blocked before it reaches the network), or JavaScript dialog,
apart from failures a check causes on purpose (Monitor.expect, valid only until that check ends). It also fails when no
check ran (an --only that matches nothing), when a fixture check is skipped, and in --base mode when the served catalog
has no gallery or slideshow images or its summary and rendered slides disagree with it (--allow-empty accepts an empty
catalog). The exit status is nonzero on any failure.
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
from collections import Counter
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
STATUS_ORDER = ["available", "partially_available", "temporarily_unavailable", "manual_access", "metadata_only", "excluded"]


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
    """The frozen catalog.js encoding (09-frozen-contracts.md; the DEEMO reference form), as tests/fixtures/ui uses."""
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


def edges(catalog: dict) -> tuple[dict, dict, str]:
    """Rare records: titles the builder never leaves blank (an empty one on a slideshow file with linked songs, a blank
    one on a slideshow file whose songs are removed too) and a small artwork kept only in the repository, without a
    preview (its viewer note has two sentences). Returns the catalog, the name each untitled file should be shown under
    (its songs, then its upstream file name, as scripts/render_slideshow.py and src/archive.js name it) and the small
    archive-only file's ID."""
    bare = json.loads(json.dumps(catalog))
    shown = [asset for asset in bare["assets"] if asset["gallery"] and asset["slideshow"]]
    with_songs = next(asset for asset in shown if [song for song in asset["song_titles"] if song.strip()])
    with_file = next(asset for asset in shown if asset is not with_songs and asset["original_filename"])
    names = {with_songs["id"]: " / ".join(song for song in with_songs["song_titles"] if song.strip()),
             with_file["id"]: with_file["original_filename"]}
    with_songs["title"] = ""
    with_file.update({"title": "  ", "song_titles": []})
    small = next(asset for asset in bare["assets"] if asset["gallery"] and asset["site_original"] and not asset["thumb"]
                 and asset["kind"] == "song_art" and max(asset["width"], asset["height"]) < 1000)
    small.update({"site_original": False, "archive_url": "https://github.com/FridrichMethod/dynamix/raw/main/" + small["path"]})
    return bare, names, small["id"]


# --------------------------------------------------------------------------------------------------------------------
# Monitoring


class Monitor:
    """Every sign of trouble on the pages it watches: uncaught exceptions, console errors, HTTP errors, failed requests,
    JavaScript dialogs and requests outside the allowed mounts (blocked before they leave the browser). Loads a check
    makes fail on purpose are expected (expect(url) first, and the failure must then happen before that check ends; the
    expectation does not outlive it); requests still pending when the smoke leaves a page are released. Each problem
    names the check during which it happened."""

    def __init__(self):
        self.problems, self.urls, self.blocked = [], [], []
        self.expected = {}
        self.pending = {}
        self.released = set()
        self.check = None

    def report(self, problem):
        self.problems.append(f"[{self.check or 'setup'}] {problem}")

    def begin(self, check):
        self.check, self.expected = check, {}

    def end(self):
        """Close the current check's scope; returns the loads it expected to fail that never did."""
        unused = [url for url, seen in self.expected.items() if not seen]
        self.check, self.expected = None, {}
        return unused

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
                self.report(f"request failed ({request.failure}): {request.url}")

        def response(response):
            if response.status >= 400 and not self.deliberate(response.url):
                self.report(f"HTTP {response.status}: {response.url}")

        def console(message):
            if message.type == "error" and not self.deliberate(message.location.get("url", "")):
                self.report(f"console error on {page.url}: {message.text}")

        def dialog(dialog):
            self.report(f"JavaScript {dialog.type} dialog on {page.url}: {dialog.message}")
            dialog.dismiss()

        page.route("**/*", route)
        page.on("request", request)
        page.on("requestfinished", lambda request: pending.discard(request))
        page.on("requestfailed", failed)
        page.on("response", response)
        page.on("console", console)
        page.on("dialog", dialog)
        page.on("pageerror", lambda error: self.report(f"uncaught exception on {page.url}: {error}"))

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


class Skip(Exception):
    """Raised by a check that does not apply to the served catalog (e.g. it has no slideshow images): reported as
    skipped, never as passed."""


class Smoke:
    def __init__(self, browser, monitor):
        self.browser, self.monitor = browser, monitor
        self.passed, self.failed, self.skipped = [], [], []
        self.open_pages = []
        self.measurements = {}
        self.only = []
        self.selected = 0

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
        self.selected += 1
        started = time.monotonic()
        self.monitor.begin(name)
        failure = skipped = None
        try:
            check(self, *args)
        except Skip as reason:
            skipped = str(reason)
        except Exception as error:  # noqa: BLE001 - every failure is reported and fails the run
            failure = {"check": name, "error": f"{type(error).__name__}: {error}", "trace": traceback.format_exc(limit=4)}
        for page in list(self.open_pages):  # a failed check leaves its pages open; close them quietly
            self.monitor.leave(page)
            page.context.close()
            self.open_pages.remove(page)
        unused = self.monitor.end()
        if unused and not failure:
            failure = {"check": name, "error": f"loads this check made fail never failed: {unused}", "trace": ""}
        if failure:
            self.failed.append(failure)
            print(f"FAIL {name}: {failure['error']}", file=sys.stderr)
        elif skipped is not None:
            self.skipped.append(f"{name}: {skipped}")
            print(f"skip {name}: {skipped}", file=sys.stderr)
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


def translator(page):
    return lambda key, **values: page.evaluate("([key, values]) => DYNAMIX_I18N.t(key, values)", [key, values])


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
    page.locator("#more").focus()
    page.keyboard.press("Enter")
    assert page.locator(".card").count() == 120
    assert page.evaluate("document.activeElement === document.getElementById('more')"), "focus stays on Load more while it is shown"
    page.keyboard.press("Enter")
    assert page.locator(".card").count() == total and page.locator("#more").is_hidden()
    # The last Load more hides itself; focus moves to the first card it added, so Tab continues through the new cards.
    assert page.evaluate("document.activeElement === document.querySelectorAll('.card-image')[120]"), page.evaluate("document.activeElement.outerHTML.slice(0, 80)")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement === document.querySelectorAll('.card-image')[121]")
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
    if not catalog["assets"]:
        raise Skip("the catalog has no files")
    page = smoke.page(base)
    open_archive(smoke, page, base)
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
        # The note under the image is composed of whole sentences: no separator before the first (as rendered text,
        # which keeps the leading space innerText would hide).
        note = page.locator("#archive-note").text_content()
        assert note == note.strip(), (asset["id"], note)
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
    hostile = find(catalog, lambda a: any("javascript:alert(6)" in p["mapping_evidence"] for p in a["provenance"]))
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
        # Every credit-evidence role has a label (no raw "other"); each record lists the packs it names, and only those.
        other = next(entry for record in duplicate["provenance"] for entry in record["credit_evidence"] or [] if entry["role"] == "other")
        assert f"{t('credit.role.other')} · {other['name']}" in text and f"other · {other['name']}" not in text, (lang, "credit role")
        packs = [record["collections"] for record in duplicate["provenance"] if record["collections"]]
        assert packs and len(packs) < len(duplicate["provenance"]), "the fixture has records that name packs and one that does not"
        for names in packs:
            line = t("provenance.collections", names=" / ".join(names))
            assert text.count(line) == packs.count(names), (lang, line)
        smoke.goto(page, urljoin(base, f"archive.html?lang={lang}#" + quote(hostile["id"], safe="")))
        page.wait_for_function("document.getElementById('viewer').open")
        assert page.locator("a[href^='javascript:' i]").count() == 0
        assert "javascript:alert(6)" in page.locator("#provenance").inner_text(), "a script-looking string is shown as text"
        smoke.finish(page)


def check_injection(smoke, base, catalog):
    """Catalog strings render as text: card titles equal the catalog's, no injected element exists, no dialog fires."""
    if not any(asset["gallery"] for asset in catalog["assets"]):
        raise Skip("the catalog has no gallery images")
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
        assert page.locator("#asset-source").get_attribute("href") == hostile["page_url"], "the caption links the source page"
        assert page.locator(".slide-notes").evaluate("(node) => node.querySelector('img, script, b') === null"), "no markup from the data"
    page.wait_for_timeout(300)
    assert page.evaluate("document.querySelectorAll('img[src=\"x\"]').length") == 0, "no injected element"
    assert page.evaluate("[...document.scripts].every((s) => !s.textContent.includes('alert('))")
    smoke.finish(page)


def check_previews(smoke, base, catalog, fixture):
    """Cards show previews and never request a previewed original; small files without a preview show the original;
    a failing preview falls back once to a published original, and to a labelled tile for an archive-only file."""
    if not any(asset["gallery"] for asset in catalog["assets"]):
        raise Skip("the catalog has no gallery images")
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
        # A URL with only a fragment (the archive viewer's "View in slideshow" link) gains ?lang= too, so a copied
        # link opens in the chosen language in a fresh browser.
        slide = quote(next(asset for asset in catalog["assets"] if asset["slideshow"])["id"], safe="")
        smoke.goto(page, urljoin(base, "index.html#" + slide))
        page.wait_for_function("document.querySelector('.dynamix-view').style.opacity === '1'")
        page.locator("[data-lang-toggle]").click()
        assert page.url == urljoin(base, "index.html?lang=zh-CN#" + slide), page.url
        shared = smoke.page(base)
        smoke.goto(shared, page.url)
        assert shared.evaluate("document.documentElement.lang") == "zh-CN"
        smoke.finish(shared)
        page.locator("[data-lang-toggle]").click()
        assert page.url == urljoin(base, "index.html#" + slide), page.url
    if catalog["assets"]:
        opened = quote(catalog["assets"][0]["id"], safe="")
        smoke.goto(page, urljoin(base, "archive.html#" + opened))
        page.wait_for_function("document.getElementById('viewer').open")
        page.evaluate("DYNAMIX_I18N.setLang('zh-CN')")  # the header toggle is inert behind the modal viewer
        assert page.url == urljoin(base, "archive.html?lang=zh-CN#" + opened), page.url
        page.evaluate("DYNAMIX_I18N.setLang('en')")
        assert page.url == urljoin(base, "archive.html#" + opened), page.url
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


def check_viewer_failures(smoke, base, catalog):
    """A viewer image that fails to load is hidden behind a message naming the link the viewer offers for that file (the
    repository link for an archive-only file, whose note then no longer says a preview is shown); the next file shows
    its image again."""
    archived = find(catalog, lambda a: a["gallery"] and not a["site_original"] and a["thumb"])
    published = find(catalog, lambda a: a["gallery"] and a["site_original"] and a["thumb"])
    if not (archived and published):
        raise Skip("the catalog lacks an archive-only or a published gallery image with a preview")
    for asset, failing, key, link in ((archived, archived["thumb"], "viewer.preview_error", "viewer.repository"),
                                      (published, published["url"], "viewer.image_error", "viewer.original")):
        page = smoke.page(base)
        url = urljoin(base, failing)
        smoke.monitor.expect(url)
        page.route(url, lambda route: route.abort())
        smoke.goto(page, urljoin(base, "archive.html#" + quote(asset["id"], safe="")))
        page.wait_for_function("document.getElementById('viewer').open")
        page.locator("#image-error").wait_for(state="visible", timeout=5000)
        t = translator(page)
        for lang in ("en", "zh-CN"):
            page.evaluate("(lang) => DYNAMIX_I18N.setLang(lang)", lang)
            assert page.locator("#full-image").is_hidden(), (asset["id"], "no broken-image glyph")
            assert page.locator("#image-error").inner_text() == t(key, link=t(link)), (lang, page.locator("#image-error").inner_text())
            note = page.locator("#archive-note").text_content()
            if not asset["site_original"]:
                assert note.startswith(t("viewer.archive_only_no_preview")) and t("viewer.archive_only") not in note, (lang, note)
            assert note == note.strip(), (lang, note)
        page.keyboard.press("ArrowRight")
        page.wait_for_function(IMAGE_SHOWN, timeout=5000)
        assert page.locator("#image-error").is_hidden() and page.locator("#full-image").is_visible()
        smoke.finish(page)


def check_inventory(smoke, base, catalog):
    """The inventory summary names only the source statuses the catalog's sources have, with counts, in the language's
    punctuation (full-width colons and no space after a Chinese full stop), as whole sentences with nothing before the
    first; failure lines count attempts in the language's plural form."""
    tally = Counter(source["status"] for source in catalog["sources"])
    totals = catalog.get("summary") or {}
    pick = lambda key, fallback: fallback if totals.get(key) is None else totals[key]  # the page's `??`
    counts = {"sources": pick("source_count", len(catalog["sources"])), "records": pick("source_asset_records", 0),
              "files": pick("unique_files", len(catalog["assets"])),
              "gallery": pick("gallery_images", sum(bool(asset["gallery"]) for asset in catalog["assets"])),
              "failures": pick("failure_count", 0)}
    for lang in ("en", "zh-CN"):
        page = smoke.page(base)
        open_archive(smoke, page, base, f"?lang={lang}")
        t = translator(page)
        page.locator(".source-inventory summary").first.click()
        summary = page.locator("#inventory-summary").inner_text()
        # Compared as rendered text, which keeps a leading space that innerText would hide.
        composed = t("inventory.summary", **counts)
        if tally:
            statuses = " · ".join(t("inventory.status_count", status=t("status." + status), count=tally[status])
                                  for status in STATUS_ORDER if tally[status])
            assert t("inventory.statuses", statuses=statuses) in summary, (lang, summary)
            composed = t("text.sentences", first=composed, second=t("inventory.statuses", statuses=statuses))
        assert page.locator("#inventory-summary").text_content() == composed, (lang, page.locator("#inventory-summary").text_content(), composed)
        if lang == "zh-CN":
            assert ":" not in summary and "。 " not in summary and "：" in summary, summary
        else:
            assert ". Source statuses: " in summary or not tally, summary
        for status in STATUS_ORDER:
            if not tally[status]:
                assert t("status." + status) not in summary, (lang, status, summary)
        if catalog["failures"]:
            page.locator("#failures summary").click()
            failures = page.locator("#failures").inner_text()
            assert "1 attempts" not in failures, failures
            for failure in catalog["failures"]:
                form = page.evaluate("([lang, count]) => new Intl.PluralRules(lang).select(count)", [lang, failure["attempts"]])
                assert t(f"inventory.attempts.{form}", count=failure["attempts"]) in failures, (lang, failure["id"])
        smoke.finish(page)


def check_history(smoke, base, catalog):
    """Back and Forward keep the page and its URL in step: Back from a file opened by the inventory's reference list
    closes the viewer and restores the filters of the previous URL; Forward opens the file again."""
    reference = find(catalog, lambda a: not a["gallery"])
    if reference is None:
        raise Skip("the catalog has no reference files")
    page = smoke.page(base)
    open_archive(smoke, page, base, "?sort=title")
    page.locator(".source-inventory summary").first.click()
    page.locator("#references a").first.click()
    page.wait_for_function("document.getElementById('viewer').open")
    assert page.locator("#viewer-title").inner_text() == reference["title"]
    assert urlparse(page.url).query == "kind=reference" and urlparse(page.url).fragment == quote(reference["id"], safe=""), page.url
    page.go_back()
    page.wait_for_function("!document.getElementById('viewer').open", timeout=3000)
    assert (urlparse(page.url).query, urlparse(page.url).fragment) == ("sort=title", ""), page.url
    assert [page.locator(f"#{field}").input_value() for field in ("kind", "sort")] == ["", "title"]
    assert count(page) == catalog_count(page, "() => true")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert urlparse(page.url).query == "sort=title", "the URL keeps the restored filters"
    page.go_forward()
    page.wait_for_function("document.getElementById('viewer').open", timeout=3000)
    assert page.locator("#viewer-title").inner_text() == reference["title"] and page.locator("#kind").input_value() == "reference"
    page.keyboard.press("Escape")
    page.wait_for_function("location.hash === ''", timeout=3000)
    assert urlparse(page.url).query == "kind=reference", page.url
    smoke.finish(page)


def check_served_catalog(smoke, base, catalog, allow_empty):
    """The catalog is not degenerate: it has gallery and slideshow images (unless an empty one is allowed), its summary
    counts them, and index.html renders exactly its slideshow entries, in catalog order."""
    gallery = sum(bool(asset["gallery"]) for asset in catalog["assets"])
    slides = [asset["id"] for asset in catalog["assets"] if asset["slideshow"]]
    summary = catalog.get("summary") or {}
    assert (summary.get("gallery_images"), summary.get("slideshow_images")) == (gallery, len(slides)), (summary, gallery, len(slides))
    assert allow_empty or (gallery and slides), f"the catalog has {gallery} gallery and {len(slides)} slideshow images"
    page = smoke.page(base)
    smoke.goto(page, urljoin(base, "index.html"))
    rendered = page.evaluate("[...document.querySelectorAll('.mySlides img')].map((img) => img.dataset.id)")
    assert rendered == slides, (rendered[:3], slides[:3], len(rendered), len(slides))
    smoke.finish(page)


def check_layout(smoke, base, catalog, lang, size):
    """No horizontal scroll; the viewer is usable (close button and download link reachable); slideshow controls in view."""
    width, height = VIEWPORTS[size]
    page = smoke.page(base, width, height)
    open_archive(smoke, page, base, f"?lang={lang}")
    assert no_horizontal_scroll(page), (lang, size, "archive")
    if size == "phone" and page.locator(".card").count():  # the first phone screen reaches the artwork
        top = page.locator(".card").first.bounding_box()["y"]
        assert top < height - 24, (lang, size, "the first row of cards starts below the first screen", top)
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
        raise Skip("the catalog has no slideshow images")
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
            # A later fragment (Back, an edited URL, a link) turns to its slide even on a page opened with ?asset=.
            page.evaluate("(id) => { location.hash = '#' + encodeURIComponent(id); }", slides[1]["id"])
            page.wait_for_function(f"{SHOWN_ID} === {json.dumps(slides[1]['id'])}", timeout=3000)
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


# Phones held sideways (533 x 320: the compact control row of windows under 600px wide), short narrow landscape windows
# (560 x 440 to 700 x 560, where the text column is narrowest for its height), short portrait phones (browser bars
# included) and short desktop windows, where the notes, the caption and the bottom control row compete for room.
SHORT_SCREENS = [(533, 320), (568, 320), (640, 360), (667, 375), (812, 375), (844, 390), (560, 440), (600, 450), (700, 560),
                 (960, 540), (320, 454), (375, 553), (360, 568), (360, 640)]
# The visible text blocks of the liner column that overlap a control of the bottom row; what the caption's lines cross
# besides (the stage, the arrows, the language toggle, the notes or the title card); the blocks not wholly on screen;
# and whether the illustrator's whole credit (when slide notes are up) lies on screen above that row, every line of it
# inside the credits' box (none hidden by a clamp). The caption is measured by its lines: on short landscape screens
# its box is a fixed band beside the control row.
CLEARANCE = """(names) => { const row = names.map((name) => document.querySelector('.' + name).getBoundingClientRect());
    const overlaps = (a, b) => a.top < b.bottom && a.bottom > b.top && a.left < b.right && a.right > b.left;
    const extent = (node) => { const boxes = node.classList.contains('asset-caption')
            ? [...node.children].filter((child) => child.getClientRects().length).map((child) => child.getBoundingClientRect())
            : [node.getBoundingClientRect()];
        return {top: Math.min(...boxes.map((box) => box.top)), bottom: Math.max(...boxes.map((box) => box.bottom)),
                left: Math.min(...boxes.map((box) => box.left)), right: Math.max(...boxes.map((box) => box.right))}; };
    const blocks = [...document.querySelectorAll('.intro, .slide-notes, .asset-caption')]
        .filter((node) => getComputedStyle(node).visibility !== 'hidden' && node.getClientRects().length);
    const hits = blocks.filter((node) => row.some((box) => overlaps(extent(node), box))).map((node) => node.className);
    const caption = document.querySelector('.asset-caption');
    const others = [...document.querySelectorAll('.slideshow-container, .prev, .next, .lang-toggle')].concat(blocks.filter((node) => node !== caption));
    const crossed = blocks.includes(caption) ? others.filter((node) => overlaps(extent(caption), node.getBoundingClientRect())).map((node) => node.className) : [];
    const offscreen = blocks.filter((node) => { const box = extent(node);
        return box.top < 0 || box.left < 0 || box.bottom > innerHeight || box.right > innerWidth; }).map((node) => node.className);
    const credit = document.querySelector('.slide-notes dd.notes-artist'), notes = blocks.some((node) => node.classList.contains('slide-notes'));
    const top = Math.min(...row.map((box) => box.top)), lines = credit ? [...credit.getClientRects()] : [];
    const meta = credit && credit.parentNode.getBoundingClientRect();
    const creditShown = !notes || (lines.length > 0 && lines.every((line) => line.top >= 0 && line.bottom <= top && line.bottom <= meta.bottom + 1));
    return {hits, crossed, offscreen, creditShown}; }"""
CLEAR = {"hits": [], "crossed": [], "offscreen": [], "creditShown": True}
# The notes' texts a short screen cut with an ellipsis, each with its tooltip (which must hold the full text).
CUT = "[...document.querySelectorAll('.slide-notes [data-full][title]')].map((node) => [node.dataset.full, node.title])"
# The parts notes too long for their screen give up, in this order (fitNotes() in templates/slideshow.html).
FIT_STEPS = ["collection", "composer", "reference", "title", "eyebrow"]
FIT = "document.querySelector('.slide-notes').dataset.fit || ''"


def check_short_screens(smoke, base, catalog, fixture):
    """On phones held sideways, short narrow landscape windows, short portrait phones and short desktop windows,
    stepping through the whole show, the title card, every slide's notes and the caption stay on screen and clear of the
    bottom control row, the caption clear of the stage, the arrows, the language toggle and the notes, in both
    languages, and the illustrator's whole credit is on screen. Notes too long for their screen give up parts in the
    fixed order, never the illustrator's credit; a text cut short shows its full wording in a tooltip (the fixture's
    crowded slides give up parts on the narrow short windows, and the Latin one's file name is cut on a landscape
    phone)."""
    slides = [asset for asset in catalog["assets"] if asset["slideshow"]]
    if not slides:
        raise Skip("the catalog has no slideshow images")
    cut, fitted = set(), set()
    for lang in ("en", "zh-CN"):
        for width, height in SHORT_SCREENS:
            page = smoke.page(base, width, height, reduced_motion="reduce")
            smoke.goto(page, urljoin(base, f"index.html?lang={lang}"))
            page.wait_for_function("document.querySelector('.dynamix-view').style.opacity === '1'")
            page.evaluate("randomClick(); setIntro(true)")  # pause, with the title card up
            state = page.evaluate(CLEARANCE, BOTTOM_ROW)
            assert state == CLEAR, (lang, width, height, "title card", page.evaluate(SHOWN_ID), state)
            for _ in slides:
                page.evaluate("plusSlides(1)")
                state = page.evaluate(CLEARANCE, BOTTOM_ROW)
                assert state == CLEAR, (lang, width, height, page.evaluate(SHOWN_ID), state)
                steps = page.evaluate(FIT).split()
                assert steps == FIT_STEPS[:len(steps)], (lang, width, height, page.evaluate(SHOWN_ID), steps)
                if steps:
                    fitted.add(page.evaluate(SHOWN_ID))
                for full, tooltip in page.evaluate(CUT):
                    assert tooltip == full, (lang, width, height, page.evaluate(SHOWN_ID), full, tooltip)
                    cut.add((width, height, full))
            smoke.finish(page)
    if fixture:
        crowded = [asset for asset in slides if len(asset["collections"]) >= 5]
        assert len(crowded) == 2 and {asset["id"] for asset in crowded} <= fitted, ("the crowded slides give up parts", sorted(fitted))
        latin = next(asset for asset in crowded if asset["title"].isascii())
        assert (568, 320, latin["title"]) in cut, ("the crowded slide's file name is cut on a landscape phone", sorted(cut))


# The saved screenshot's copy of the page (html2canvas's clone, seen through markCuts()): each notes text with its full
# wording, its text there and whether it is still cut; the illustrator's credit there, and whether all of it is drawn
# (every line on screen and inside the credits' box). Null when the renderer never handed its copy to markCuts().
CAPTURE = """() => new Promise((resolve) => {
    window.setTimeout(() => resolve(null), 20000);
    const mark = window.markCuts;
    if (typeof mark !== 'function') return resolve(null);
    window.markCuts = (doc) => {
        window.markCuts = mark;
        mark(doc);
        const view = doc.defaultView;
        const texts = [...doc.querySelectorAll('.slide-notes [data-full]')].map((node) => [node.dataset.full, node.textContent,
            node.scrollHeight - node.clientHeight > parseFloat(view.getComputedStyle(node).lineHeight) / 2]);
        const credit = doc.querySelector('.slide-notes dd.notes-artist');
        const lines = [...credit.getClientRects()], meta = credit.parentNode.getBoundingClientRect();
        resolve({texts, credit: [...credit.childNodes].map((node) => node.nodeName === 'BR' ? '\\n' : node.textContent).join(''),
                 whole: lines.length > 0 && lines.every((line) => line.top >= 0 && line.bottom <= view.innerHeight && line.bottom <= meta.bottom + 1)});
    };
    screenshot();
})"""
OVERLAY_OPEN = "document.getElementById('screenshot').classList.contains('is-open')"


def check_screenshot_notes(smoke, base, catalog, fixture):
    """A saved screenshot never cuts a text silently: where the page cuts a title or file reference with an ellipsis
    (which the screenshot renderer would not draw), its copy for the picture ends in a written one after a start of the
    full text, and the illustrator's credit is drawn whole, on landscape phones and short narrow windows in both
    languages (the fixture's crowded slides and its slide without credits; else the slides with the longest titles and
    credits)."""
    slides = [asset for asset in catalog["assets"] if asset["slideshow"]]
    if not slides:
        raise Skip("the catalog has no slideshow images")
    if fixture:
        chosen = [asset for asset in slides if len(asset["collections"]) >= 5 or not asset["illustrators"]]
    else:
        chosen = sorted(slides, key=lambda asset: -len(asset["title"] + "".join(asset["illustrators"])))[:3]
    shortened = []
    for lang in ("en", "zh-CN"):
        for width, height in ((533, 320), (568, 320), (600, 450), (700, 560)):
            page = smoke.page(base, width, height, reduced_motion="reduce")
            for asset in chosen:
                smoke.goto(page, urljoin(base, f"index.html?lang={lang}&asset=" + quote(asset["id"], safe="")))
                page.wait_for_function("document.querySelector('.dynamix-view').style.opacity === '1'")
                page.wait_for_function(SLIDE_SHOWN, timeout=10_000)
                credit = page.evaluate("imgTargets[slideIndex - 1].dataset.illustrators || DYNAMIX_I18N.t('credit.unknown')")
                capture = page.evaluate(CAPTURE)
                where = (lang, width, height, asset["id"])
                assert capture, (where, "the screenshot is drawn from a copy that markCuts() has seen")
                page.wait_for_function(OVERLAY_OPEN, timeout=20_000)
                page.evaluate("closeScreenshot()")
                assert capture["credit"] == credit and capture["whole"], (where, capture)
                for full, text, still_cut in capture["texts"]:
                    assert not still_cut, (where, full, text)
                    if text.replace(" ", " ") != full:
                        assert text.endswith("…") and full.startswith(text[:-1]), (where, full, text)
                        shortened.append((where, text))
            smoke.finish(page)
    if fixture:
        assert shortened, "the crowded slides' cut file names end in a written ellipsis in the screenshot"


# A word the page breaks inside (overflow-wrap) keeps that break in the saved picture. html2canvas 0.5 draws a text node
# without characters beyond U+00FF word by word, at each word's range bounds, so in its copy of the page (seen through
# markCuts()) every such word must lie on one line. The notes title gets one long Latin word that the page breaks.
WRAPPED_WORDS = """(root) => { const doc = root.ownerDocument, range = doc.createRange(), spanning = [];
    const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) { const node = walker.currentNode;
        if (/[^\\u0000-\\u00ff]/.test(node.data)) continue;
        for (const match of node.data.matchAll(/[^ \\t\\r\\n-]+/g)) {
            range.setStart(node, match.index); range.setEnd(node, match.index + match[0].length);
            if (new Set([...range.getClientRects()].map((box) => Math.round(box.top))).size > 1) spanning.push(match[0]); } }
    return spanning; }"""
WRAP_CAPTURE = """(title) => new Promise((resolve) => {
    window.setTimeout(() => resolve(null), 20000);
    const spanning = WRAPPED_WORDS;""".replace("WRAPPED_WORDS", WRAPPED_WORDS) + """
    const node = document.querySelector('.slide-notes .notes-title');
    node.textContent = title;
    node.dataset.full = title;
    const page = spanning(document.querySelector('.slide-notes'));
    const mark = window.markCuts;
    if (typeof mark !== 'function') return resolve(null);
    window.markCuts = (doc) => { window.markCuts = mark; mark(doc);
        resolve({page, copy: spanning(doc.querySelector('.slide-notes'))}); };
    screenshot();
})"""


def check_screenshot_line_breaks(smoke, base, catalog):
    """A notes title that the page breaks inside a Latin word is drawn with the same line breaks in the saved
    screenshot: in the renderer's copy no word it draws spans two lines (on a desktop and on a phone)."""
    slides = [asset for asset in catalog["assets"] if asset["slideshow"]]
    if not slides:
        raise Skip("the catalog has no slideshow images")
    for width, height in ((1440, 900), (375, 812)):
        page = open_slideshow(smoke, base, "?lang=en&asset=" + quote(slides[0]["id"], safe=""), width, height)
        capture = page.evaluate(WRAP_CAPTURE, "Redhorsemassacreredhorsemassacre")
        where = (width, height, slides[0]["id"])
        assert capture, (where, "the screenshot is drawn from a copy that markCuts() has seen")
        assert capture["page"], (where, "the page breaks the long title word inside (the check's precondition)")
        assert capture["copy"] == [], (where, "words drawn over two lines in the screenshot", capture)
        page.wait_for_function(OVERLAY_OPEN, timeout=20_000)
        page.evaluate("closeScreenshot()")
        smoke.finish(page)


# Tab takes the slideshow as it is drawn (the caption stays with its notes, before the row, at every size).
TAB_GROUPS = ["signature", "prev", "next", "asset-caption", *BOTTOM_ROW, "lang-toggle"]
FOCUS_GROUP = """(names) => { const node = document.activeElement.closest(names.map((name) => '.' + name).join(', '));
    return node ? names.find((name) => node.classList.contains(name)) : document.activeElement.tagName; }"""


def check_tab_order(smoke, base, catalog):
    """Tab runs through the signature, the stage's arrows, the caption's links, the bottom row from the left and the
    language toggle, on a desktop, a landscape phone (where the caption is drawn right of the row) and a portrait one."""
    slides = [asset for asset in catalog["assets"] if asset["slideshow"]]
    if not slides:
        raise Skip("the catalog has no slideshow images")
    for width, height in ((1440, 900), (568, 320), (375, 812)):
        page = open_slideshow(smoke, base, f"?asset={quote(slides[0]['id'], safe='')}", width, height)
        page.evaluate("document.activeElement.blur()")
        groups = []
        for _ in range(30):
            page.keyboard.press("Tab")
            group = page.evaluate(FOCUS_GROUP, TAB_GROUPS)
            if not groups or groups[-1] != group:
                groups.append(group)
            if group == TAB_GROUPS[-1]:
                break
        assert groups == TAB_GROUPS, (width, height, groups)
        smoke.finish(page)


def check_screenshot_overlay(smoke, base, catalog):
    slides = [asset for asset in catalog["assets"] if asset["slideshow"]]
    if not slides:
        raise Skip("the catalog has no slideshow images")
    page = open_slideshow(smoke, base, f"?asset={quote(slides[0]['id'], safe='')}#screenshot")
    assert not page.evaluate(OVERLAY_OPEN), "a #screenshot URL opens nothing"
    history = page.evaluate("[history.length, location.href]")
    page.locator(".photo button").click()
    page.wait_for_function(OVERLAY_OPEN, timeout=20_000)
    assert page.evaluate("Boolean(document.activeElement.closest('#screenshot'))")
    assert page.evaluate("document.querySelector('#screenshot-content canvas') !== null")
    start = page.evaluate("slideIndex")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("slideIndex") == start, "the arrow keys wait while the overlay is open"
    page.keyboard.press("Escape")
    assert not page.evaluate(OVERLAY_OPEN)
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


def check_edges(smoke, base, catalog, names, small_id):
    """A file without a title is shown under its songs, then its upstream file name: on its card, in the title sort, in
    the viewer and in the slideshow (slide, alt text, notes and caption). A viewer note of two sentences joins them in
    each language's way, with nothing before the first. Texts are compared as rendered (textContent)."""
    page = smoke.page(base)
    open_archive(smoke, page, base, "?sort=title")
    cards = page.evaluate("[...document.querySelectorAll('.card')].map((card) => [card.dataset.id, card.querySelector('h2').textContent])")
    shown = dict(cards)
    for asset_id, name in names.items():
        assert shown.get(asset_id) == name, (asset_id, shown.get(asset_id), name)
    order = [title for _, title in cards]
    assert order == page.evaluate("(titles) => [...titles].sort(new Intl.Collator('en', {numeric: true}).compare)", order), order
    for asset_id, name in names.items():
        smoke.goto(page, urljoin(base, "archive.html#" + quote(asset_id, safe="")))
        page.wait_for_function("document.getElementById('viewer').open", timeout=5000)
        assert [page.locator("#viewer-title").text_content(), page.locator("#full-image").get_attribute("alt")] == [name, name], asset_id
    small = next(asset for asset in catalog["assets"] if asset["id"] == small_id)
    t = translator(page)
    for lang in ("en", "zh-CN"):
        smoke.goto(page, urljoin(base, f"archive.html?lang={lang}#" + quote(small_id, safe="")))
        page.wait_for_function("document.getElementById('viewer').open", timeout=5000)
        expected = t("text.sentences", first=t("viewer.archive_only_no_preview"),
                     second=t("viewer.low_resolution", size=max(small["width"], small["height"])))
        note = page.locator("#archive-note").text_content()
        assert note == expected, (lang, note, expected)
    smoke.finish(page)
    for asset_id, name in names.items():
        page = open_slideshow(smoke, base, "#" + quote(asset_id, safe=""))
        slide = page.evaluate("[imgTargets[slideIndex - 1].dataset.title, imgTargets[slideIndex - 1].alt]")
        assert slide == [name, name], (asset_id, slide)
        assert page.locator("#asset-title").text_content() == name
        assert page.locator(".slide-notes .notes-title").first.get_attribute("data-full") == name
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
    smoke.run(f"{label}: Back and Forward restore filters and the viewer", check_history, base, catalog)
    smoke.run(f"{label}: inventory statuses and failure counts", check_inventory, base, catalog)
    smoke.run(f"{label}: language toggle, ?lang= and localStorage", check_language, base, catalog)
    smoke.run(f"{label}: catalog strings render as text", check_injection, base, catalog)
    smoke.run(f"{label}: slideshow deep links, controls and names", check_slideshow, base, catalog, fixture)
    smoke.run(f"{label}: screenshot overlay", check_screenshot_overlay, base, catalog)
    smoke.run(f"{label}: short screens keep notes and caption clear of the controls", check_short_screens, base, catalog, fixture)
    smoke.run(f"{label}: screenshots cut no notes text silently", check_screenshot_notes, base, catalog, fixture)
    smoke.run(f"{label}: screenshots keep the page's line breaks inside words", check_screenshot_line_breaks, base,
              catalog)
    smoke.run(f"{label}: Tab follows the drawing", check_tab_order, base, catalog)
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
    parser.add_argument("--allow-empty", action="store_true", help="with --base: accept a catalog without gallery or slideshow images")
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
                edge_catalog, edge_names, edge_small = edges(catalog)
                build_site(work / "mounted/edges", edge_catalog)
                servers = [serve(work / "root"), serve(work / "mounted")]
                root_base = f"http://127.0.0.1:{servers[0].server_port}/"
                mounted = f"http://127.0.0.1:{servers[1].server_port}/"
                smoke.run("root: served catalog has gallery and slideshow images", check_served_catalog, root_base, catalog, False)
                run_suite(smoke, root_base, catalog, True, "root")
                smoke.run("root: provenance comparisons and unsafe links", check_provenance_text, root_base, catalog)
                smoke.run("root: failed viewer images", check_viewer_failures, root_base, catalog)
                smoke.run("root: missing catalog message", check_missing_catalog, root_base)
                smoke.run("root: manifest and icons", check_site_icons, root_base)
                run_suite(smoke, mounted + "dynamix/", catalog, True, "/dynamix/")
                smoke.run("/dynamix/: requests stay under the mount", check_subpath_requests, mounted, ("/dynamix/",))
                smoke.run("/empty/: empty catalog", check_empty, mounted + "empty/")
                smoke.run("/empty/: an empty catalog is consistent when allowed", check_served_catalog, mounted + "empty/", empty, True)
                smoke.run("/many/: 60 per page and focus after paging", check_pagination, mounted + "many/")
                smoke.run("/hostile/: script URLs in a catalog are never linked or loaded", check_hostile, mounted + "hostile/")
                smoke.run("/edges/: untitled files and a two-sentence viewer note", check_edges, mounted + "edges/", edge_catalog,
                          edge_names, edge_small)
                smoke.run("subpath server: every request under /dynamix/, /empty/, /many/, /hostile/ or /edges/", check_subpath_requests,
                          mounted, ("/dynamix/", "/empty/", "/many/", "/hostile/", "/edges/"))
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
                smoke.run("base: served catalog has gallery and slideshow images", check_served_catalog, base, catalog, args.allow_empty)
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
    if args.only and not smoke.selected:
        problems.append(f"--only {args.only} matched no check")
    elif not smoke.passed and not smoke.failed:
        problems.append("no check ran to completion")
    if args.fixture and smoke.skipped:
        problems.append(f"the fixture covers every check, but these were skipped: {smoke.skipped}")
    report = {"mode": "fixture" if args.fixture else "base", "base": base, "browser": browser_version, "passed": smoke.passed,
              "failed": smoke.failed, "skipped": smoke.skipped, "measurements": smoke.measurements,
              "monitor_problems": problems, "requests": len(monitor.urls), "seconds": round(time.monotonic() - started, 1)}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if smoke.failed or problems else 0


if __name__ == "__main__":
    sys.exit(main())
