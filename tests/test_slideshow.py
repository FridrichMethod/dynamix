"""Offline guards for the slideshow template and stylesheet: named, keyboard-operable controls in both languages, a
bottom row whose DOM order follows its layout, a screenshot overlay that does not live in the URL, slides that
crossfade without a keyframe fade and take no hits while hidden, an illustrator credit that no tier drops, arrows kept
below the language toggle on short landscape screens, a signature whose box leaves the toggle its taps, the visible
attribution link, no music player and no DEEMO dedication, an empty-show state, and font stacks the screenshot
renderer can draw. They read the sources only; real hit-testing, focus, keyboard handling and layout need a browser
(tests/browser_smoke.py).

Adapted from FridrichMethod/deemo tests/test_slideshow.py (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8)."""

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/slideshow.html"
STYLESHEET = ROOT / "src/slideshow.css"
REGISTER = re.compile(r"DYNAMIX_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)
NOTICE_URL = "https://github.com/FridrichMethod/dynamix/blob/main/NOTICE"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# The round controls of the bottom row, left to right on screen (DEEMO's music button is not ported).
BOTTOM_ROW = ["photo", "github", "archive-link", "random"]
# Icon-only controls, found by their own or their container's class: whether each is a toggle (aria-pressed).
ICON_BUTTONS = {"prev": False, "next": False, "photo": False, "random": True, "save": False, "close": False}
GENERIC = {"system-ui", "sans-serif", "serif", "monospace", "inherit"}


def tables(name):
    return json.loads(REGISTER.search((ROOT / f"src/i18n/{name}.js").read_text(encoding="utf-8")).group(1))


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, attrs, parent, [], ""

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.current = self.root
        self.in_script = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        self.in_script = tag == "script"
        if self.in_script and not node.attrs.get("src"):
            self.scripts.append("")
        if tag not in VOID:
            self.current = node

    def handle_endtag(self, tag):
        self.in_script = False
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        if self.in_script:
            if self.scripts:
                self.scripts[-1] += data
            return
        node = self.current
        while node is not None:
            node.text += data
            node = node.parent


def top_level_css(css):
    """The stylesheet without its @media / @supports blocks."""
    out, depth, index = [], 0, 0
    for match in re.finditer(r"@(?:media|supports)[^{]*\{|\{|\}", css):
        if depth == 0:
            out.append(css[index:match.start()])
        if match.group().startswith("@"):
            depth += 1
        elif depth and match.group() == "{":
            depth += 1
        elif depth and match.group() == "}":
            depth -= 1
        elif depth == 0:
            out.append(match.group())
        index = match.end()
    out.append(css[index:] if depth == 0 else "")
    return "".join(out)


def media_block(css, condition):
    match = re.search(r"@media[^{]*" + re.escape(condition) + r"[^{]*\{", css)
    if not match:
        return None
    depth = 1
    for brace in re.finditer(r"[{}]", css[match.end():]):
        depth += 1 if brace.group() == "{" else -1
        if depth == 0:
            return css[match.end():match.end() + brace.start()]
    return None


def css_value(css, selectors, prop):
    rule = r"(?m)^[ \t]*" + r"\s*,\s*".join(map(re.escape, selectors)) + r"\s*\{([^}]*)\}"
    values = [found.group(1) for body in re.findall(rule, css)
              for found in re.finditer(r"(?<![\w-])" + re.escape(prop) + r"\s*:\s*([^;]+?)\s*(?:;|$)", body)]
    return values[-1] if values else None


def px(value):
    match = re.fullmatch(r"(-?[\d.]+)px|(0)", value or "")
    return float(match.group(1) or 0) if match else None


def i18n_attrs(node):
    pairs = (pair.split(":", 1) for pair in (node.attrs.get("data-i18n-attr") or "").split(";") if ":" in pair)
    return {attribute.strip(): key.strip() for attribute, key in pairs}


class SlideshowTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TEMPLATE.read_text(encoding="utf-8-sig")
        cls.css = re.sub(r"/\*.*?\*/", "", STYLESHEET.read_text(encoding="utf-8"), flags=re.DOTALL)
        tree = Tree()
        tree.feed(cls.html)
        cls.nodes = list(tree.root.walk())
        cls.code = "\n".join(tree.scripts)
        cls.messages = {lang: {**tables("common")[lang], **tables("slideshow")[lang]} for lang in ("en", "zh-CN")}

    def control(self, name):
        for node in self.nodes:
            if name in node.classes:
                if node.tag in ("a", "button"):
                    return node
                inner = [child for child in node.walk() if child.tag in ("a", "button")]
                self.assertEqual(len(inner), 1, f".{name} should hold one control")
                return inner[0]
        self.fail(f"No .{name} control in the template")

    def assert_translated(self, key):
        for lang in ("en", "zh-CN"):
            self.assertTrue(self.messages[lang].get(key, "").strip(), f"{key} has no {lang} string")

    def test_placeholder_appears_once_outside_comments(self):
        self.assertEqual(self.html.count("@python-work-area"), 1)
        without_comments = re.sub(r"<!--.*?-->", "", self.html, flags=re.DOTALL)
        self.assertEqual(without_comments.count("@python-work-area"), 1)
        container = [node for node in self.nodes if "slideshow-container" in node.classes]
        self.assertEqual(len(container), 1)
        self.assertIn("@python-work-area", container[0].text)

    def test_icon_only_controls_are_named_buttons_in_both_languages(self):
        for name, toggle in ICON_BUTTONS.items():
            with self.subTest(control=name):
                node = self.control(name)
                self.assertEqual((node.tag, node.attrs.get("type")), ("button", "button"), "Actions are <button type=button>, not links")
                key = i18n_attrs(node).get("aria-label")
                self.assertTrue(key, "Icon-only controls take an aria-label translated through data-i18n-attr")
                self.assert_translated(key)
                self.assertEqual(node.attrs.get("aria-label"), self.messages["en"][key])
                if toggle:
                    self.assertIn(node.attrs.get("aria-pressed"), ("true", "false"), "Toggles expose their state with aria-pressed")

    def test_no_javascript_urls_or_html_parsing(self):
        for node in self.nodes:
            with self.subTest(tag=node.tag, href=node.attrs.get("href")):
                self.assertFalse((node.attrs.get("href") or "").strip().lower().startswith("javascript:"))
        self.assertNotRegex(self.code, r"innerHTML|outerHTML|insertAdjacentHTML|document\.write")

    def test_template_images_have_alt_text(self):
        for node in self.nodes:
            if node.tag == "img":
                with self.subTest(src=node.attrs.get("src")):
                    self.assertIn("alt", node.attrs, "Decorative icons take alt=\"\"")

    def test_accessible_names_contain_the_visible_label(self):
        """WCAG 2.5.3: an aria-label on a control with visible text must contain that text."""
        for node in self.nodes:
            label_key, text_key = i18n_attrs(node).get("aria-label"), node.attrs.get("data-i18n")
            if label_key and text_key:
                for lang in ("en", "zh-CN"):
                    with self.subTest(control=text_key, lang=lang):
                        self.assertIn(self.messages[lang][text_key].casefold(), self.messages[lang][label_key].casefold())

    def test_bottom_row_dom_order_follows_its_layout(self):
        order = [cls for node in self.nodes if "bt" in node.classes for cls in node.classes if cls in BOTTOM_ROW]
        self.assertEqual(order, BOTTOM_ROW, "Tab order should run left to right along the control row")
        css = top_level_css(self.css)
        offsets = {"photo": 0}
        for name in BOTTOM_ROW[1:]:
            match = re.search(r"(?m)^\." + re.escape(name) + r"\s*\{[^}]*?\bleft:\s*(\d+)px", css)
            self.assertTrue(match, f"No base left offset for .{name}")
            offsets[name] = int(match.group(1))
        self.assertEqual([offsets[name] for name in BOTTOM_ROW], sorted(offsets.values()))
        phone = media_block(self.css, "(max-width: 480px)")
        steps = [re.search(r"\." + re.escape(name) + r"\s*\{\s*left:\s*([^;]+);", phone).group(1) for name in BOTTOM_ROW[1:]]
        self.assertEqual(steps, ["calc(-20px + (100vw - 72px) / 3)", "calc(-20px + (100vw - 72px) * 2 / 3)", "calc(-20px + 100vw - 72px)"],
                         "Four discs spread evenly on phones")

    def test_no_music_player_or_dedication(self):
        for marker in ("<audio", "speaker", "iplayer", "playMusic", "Alice", "Hans", "dedication", ".mp3"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.html)
                self.assertNotIn(marker.lstrip("."), self.css.replace("mySlides", ""))

    def test_only_the_current_slide_takes_hits_and_reaches_assistive_tech(self):
        rule = lambda selector: re.search(r"(?m)^" + re.escape(selector) + r" \{(.*?)\n\}", self.css, re.DOTALL).group(1)
        self.assertRegex(rule(".mySlides"), r"pointer-events:\s*none")
        self.assertRegex(rule(".mySlides"), r"visibility:\s*hidden")
        self.assertRegex(rule(".mySlides.is-current"), r"pointer-events:\s*auto")
        self.assertRegex(rule(".mySlides.is-current"), r"visibility:\s*visible")
        self.assertIn('classList.add("is-current")', self.code)
        self.assertIn('classList.remove("is-current")', self.code)

    def test_slides_crossfade_without_a_keyframe_fade(self):
        self.assertNotRegex(self.css, r"@(?:-webkit-)?keyframes\s+fade\b")
        self.assertNotRegex(self.css, r"animation(?:-name)?\s*:\s*fade\b")
        reduced = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", self.css, re.DOTALL)
        self.assertTrue(reduced, "No reduced-motion block")
        self.assertIn(".mySlides", reduced.group(1), "Reduced motion turns slides without the crossfade")

    def test_short_screens_keep_the_illustrator_credit(self):
        """Tiers that drop credits keep the illustrator's row, so no layout shows an artwork without its credit line."""
        tagged = re.findall(r'if \(label === DYNAMIX_I18N\.t\("([\w.]+)"\)\) \{\s*dt\.classList\.add\("notes-artist"\);\s*dd\.classList\.add\("notes-artist"\);', self.code)
        self.assertEqual(tagged, ["credit.illustrator"], "metaRow() tags the illustrator's row")
        rows = re.findall(r'(?m)metaRow\(meta, DYNAMIX_I18N\.t\("([\w.]+)"\), data\.(\w+), (.*)\);\s*$', self.code)
        by_field = {field: (key, always) for key, field, always in rows}
        self.assertEqual(by_field["illustrators"], ("credit.illustrator", "true"), "The illustrator row is always shown")
        self.assertEqual(by_field["composers"][0], "credit.composer")
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                self.assertNotEqual(self.messages[lang]["credit.illustrator"], self.messages[lang]["credit.composer"])
        self.assertNotRegex(self.css, r"\.notes-meta\s*\{[^}]*display:\s*none")
        for selector in re.findall(r"([^{}]*\.notes-meta[^{}]*)\{[^}]*display:\s*none", self.css):
            for part in selector.split(","):
                if ".notes-meta" in part and "::before" not in part:
                    self.assertIn(":not(.notes-artist)", part)

    def test_unknown_credits_say_unknown(self):
        self.assertIn('dd.textContent = DYNAMIX_I18N.t("credit.unknown");', self.code)
        self.assertEqual((self.messages["en"]["credit.unknown"], self.messages["zh-CN"]["credit.unknown"]), ("Unknown", "未知"))

    def test_short_landscape_arrows_stay_below_the_language_toggle(self):
        base = top_level_css(self.css)
        block = media_block(self.css, "(orientation: landscape) and (max-height: 560px)")
        self.assertTrue(block, "No short-landscape block")
        lengths = {
            "toggle top": px(css_value(base, [".lang-toggle"], "top")),
            "toggle height": px(css_value(base, [".bt"], "height")),
            "toggle padding": px(css_value(base, [".bt"], "padding")),
            "arrow margin-top": px((css_value(base, [".prev", ".next"], "margin") or "").split(" ")[0]),
            "stage margin-top": px(css_value(block, [".slideshow-container"], "margin-top")),
        }
        for name, length in lengths.items():
            self.assertIsNotNone(length, f"No {name} in px in src/slideshow.css")
        clamp = re.fullmatch(r"max\(50%,\s*(\d+)px\)", css_value(block, [".prev", ".next"], "top") or "")
        self.assertTrue(clamp, "Short landscape screens clamp the arrows' top with max(50%, <n>px)")
        arrow_top = lengths["stage margin-top"] + int(clamp.group(1)) + lengths["arrow margin-top"]
        toggle_bottom = lengths["toggle top"] + lengths["toggle height"] + 2 * lengths["toggle padding"]
        self.assertGreaterEqual(arrow_top, toggle_bottom)

    def test_the_wrapped_signature_leaves_the_language_toggle_its_taps(self):
        base = top_level_css(self.css)
        self.assertEqual(css_value(base, [".signature"], "pointer-events"), "none")
        self.assertEqual(css_value(base, [".signature a"], "pointer-events"), "auto")

    def test_screenshot_overlay_does_not_live_in_the_url(self):
        self.assertNotIn(":target", self.css, "The overlay must not depend on the URL fragment")
        self.assertNotRegex(self.code, r"location\.(?:href|hash)\s*=")
        self.assertNotIn('href="#"', self.html)

    def test_visible_attribution_link(self):
        links = [node for node in self.nodes if node.tag == "a" and node.attrs.get("href") == NOTICE_URL]
        self.assertEqual(len(links), 1, "The slideshow shows one Attribution link to the NOTICE")
        self.assertIn("signature", links[0].parent.classes, "It sits in the always-visible signature")
        key = links[0].attrs.get("data-i18n")
        self.assert_translated(key)
        self.assertEqual(links[0].text.strip(), self.messages["en"][key])

    def test_the_archive_page_has_one_name(self):
        """The archive button (whose label stays in its tooltip, WCAG 2.5.3), the caption link and the empty-show link
        call archive.html by the name the page gives itself, in each language."""
        archive = tables("archive")
        links = [node for node in self.nodes if node.tag == "a" and node.attrs.get("href") == "archive.html"]
        self.assertEqual({node.attrs.get("data-i18n") for node in links}, {"slideshow.archive.text", "slideshow.caption.browse"})
        for node in links:
            self.assertEqual(node.text.strip(), self.messages["en"][node.attrs["data-i18n"]], "static text is the English string")
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                name = archive[lang]["header.title"].removeprefix("Dynamix ")  # "Artwork Archive" / "曲绘档案"
                messages = self.messages[lang]
                self.assertIn(messages["slideshow.archive.text"].casefold(), name.casefold())
                self.assertIn(name, messages["slideshow.archive.label"])
                self.assertEqual(messages["slideshow.caption.browse"], name)

    def test_empty_show_and_deep_links_are_handled(self):
        """An empty catalog keeps the title card with a pointer to the archive and turns nothing; ?asset= and #<id>
        both open a slide."""
        self.assertIn("var hasSlides = imgTargets.length > 0;", self.code)
        self.assertIn('document.querySelector(".intro-empty").hidden = false;', self.code)
        for function in ("plusSlides", "showSlides", "randomSlide", "randomClick"):
            body = re.search(r"function " + function + r"\([^)]*\) \{\n(.*?)\n        \}", self.code, re.DOTALL).group(1)
            self.assertIn("hasSlides", body.split("\n", 2)[0] + body.split("\n", 2)[1], f"{function} returns early without slides")
        self.assertIn('new URLSearchParams(location.search).get("asset")', self.code)
        self.assertIn("decodeURIComponent(location.hash.slice(1))", self.code)
        self.assertIn('window.addEventListener("hashchange"', self.code)

    def test_links_from_slide_data_are_checked(self):
        """data-page becomes a link only through httpHref(); the original link opens a new tab only for repository copies."""
        self.assertIn("setHref(source, httpHref(data.page));", self.code)
        self.assertIn('if (data.originalExternal === "true")', self.code)
        self.assertNotRegex(self.code, r"\.href\s*=\s*target\.dataset")

    def test_font_stacks_start_with_a_generic_family(self):
        """The screenshot renderer draws text with the first family of a stack only (src/vendor/legacy-ui.js joins the
        font and keeps what precedes the first comma), so each stack must start with a family every system has."""
        stacks = re.findall(r"(?:font-family|--display|--small-caps)\s*:\s*([^;]+);", self.css)
        stacks += [match.split("/", 1)[-1].split(" ", 1)[-1] for match in re.findall(r"(?<![\w-])font\s*:\s*([^;]+);", self.css)]
        self.assertTrue(stacks)
        for stack in stacks:
            first = stack.strip().split(",")[0].strip()
            with self.subTest(stack=stack[:60]):
                if first.startswith("var("):
                    continue
                self.assertIn(first.split()[-1], GENERIC)
        self.assertNotIn("@font-face", self.css)


if __name__ == "__main__":
    unittest.main()
