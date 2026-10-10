"""Check the bilingual page strings: paired English / Simplified Chinese tables, script order, translated markup, the
Dynamix globals and storage key, and that no DEEMO branding survives outside attribution notices.

Adapted from FridrichMethod/deemo tests/test_i18n.py (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8)."""

import json
import re
import unittest
from collections import namedtuple
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "zh-CN")
HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
REGISTER = re.compile(r"DYNAMIX_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# Literal keys only; dynamic keys such as t(`comparison.${key}`) are checked by prefix.
T_CALL = re.compile(r"""\bt\(\s*(["'])([^"'\n]+)\1\s*[,)]""")
T_PREFIX = re.compile(r"""\bt\(\s*(?:`([\w.-]+)\$\{|(["'])([\w.-]+)\2\s*\+)""")
# Entry pages and the scripts whose t() calls they run (inline scripts are read from the page itself).
PAGES = {"archive.html": ["src/archive.js"], "templates/slideshow.html": []}
RUNTIME = "src/i18n.js"
Script = namedtuple("Script", "kind value timing")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# Text files of the interface (the vendor bundle is third-party and checked byte for byte in test_layout.py).
UI_TEXT_FILES = ["archive.html", "templates/slideshow.html", "site.webmanifest", "browserconfig.xml", "assets/site/README.md",
                 "scripts/render_slideshow.py", "src/i18n.js", "src/archive.js", "src/archive.css", "src/slideshow.css",
                 "src/i18n/common.js", "src/i18n/archive.js", "src/i18n/slideshow.js"]
# A line may name DEEMO only to attribute the code it was adapted from.
ATTRIBUTION = re.compile(r"mashirozx/deemo|FridrichMethod/deemo|DEEMO (?:gallery|reference)|DEEMO 图库|DEEMO 参考", re.IGNORECASE)
# DEEMO-only names and media that must not reappear in the Dynamix interface.
FOREIGN = re.compile(r"Rayark|Alice|Hans\b|Fantique|COPRGTL|Coprhtl|RocknRoll|Magnolia|SnowEgg|Sakuzyo|bg\.png|\.mp3|deemo-lang|DEEMO_I18N|DEEMO_CATALOG")


def message_tables(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    match = REGISTER.search(text)
    if not match:
        raise AssertionError(f"{relative} must end with DYNAMIX_I18N.register({{...strict JSON...}});")
    return json.loads(match.group(1))


def all_messages():
    merged = {lang: {} for lang in LANGUAGES}
    for path in sorted((ROOT / "src/i18n").glob("*.js")):
        tables = message_tables(path.relative_to(ROOT).as_posix())
        for lang in LANGUAGES:
            merged[lang].update(tables[lang])
    return merged


def timing(attributes):
    """When a <script> runs (HTML "prepare the script element"): "parser", "defer" or "async"."""
    module = (attributes.get("type") or "").strip().lower() == "module"
    external = bool(attributes.get("src"))
    if "async" in attributes and (external or module):
        return "async"
    if module or (external and "defer" in attributes):
        return "defer"
    return "parser"


def script_role(script, callers):
    if script.kind == "src":
        if script.value == RUNTIME:
            return "runtime"
        if script.value.startswith("src/i18n/"):
            return "table"
        return "caller" if script.value in callers else None
    return "caller" if "DYNAMIX_I18N" in script.value or T_CALL.search(script.value) else None


def script_order_problems(scripts, callers):
    """Reasons the i18n runtime, the message tables and the code that uses DYNAMIX_I18N might not run in that order."""
    def name(script):
        return script.value if script.kind == "src" else f"the inline script {script.value.strip()[:40]!r}"

    group = [(script_role(script, callers), script) for script in scripts if script_role(script, callers)]
    problems = [f"{name(script)} is async, so it may run before or after the others" for _, script in group if script.timing == "async"]
    runtimes = sum(kind == "runtime" for kind, _ in group)
    if runtimes != 1:
        problems.append(f"{RUNTIME} is loaded {runtimes} times")
    if not any(kind == "table" for kind, _ in group):
        problems.append("no message table (src/i18n/*.js) is loaded")
    if len({script.timing for _, script in group}) > 1:
        problems.append("mixed timings, so document order is not execution order: " + ", ".join(f"{name(script)} ({script.timing})" for _, script in group))
    if problems:
        return problems
    rank = {"runtime": 0, "table": 1, "caller": 2}
    for (before, first), (after, second) in zip(group, group[1:]):
        if rank[before] > rank[after]:
            problems.append(f"{name(first)} ({before}) runs before {name(second)} ({after}); expected the runtime, then every message table, then the code that calls t()")
    return problems


def callers_of(page):
    found = set()
    for script in page.scripts:
        if script.kind != "src" or script.value == RUNTIME or script.value.startswith("src/i18n/"):
            continue
        path = ROOT / script.value
        if path.is_file() and b"DYNAMIX_I18N" in path.read_bytes():
            found.add(script.value)
    return found


class Page(HTMLParser):
    """Collect scripts, translation keys and Han text outside zh-tagged subtrees (comments are ignored)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.keys, self.han, self.stack = [], set(), [], []
        self.root_lang = None
        self.in_script = False

    def exempt(self):
        return any(exempt for _, exempt in self.stack)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.root_lang = attributes.get("lang")
        is_zh = tag != "html" and (attributes.get("lang") or "").lower().startswith("zh")
        if not (self.exempt() or is_zh):
            self.han.extend(f"<{tag} {name}>: {value}" for name, value in attrs if value and HAN.search(value))
        if attributes.get("data-i18n"):
            self.keys.add(attributes["data-i18n"])
        for pair in (attributes.get("data-i18n-attr") or "").split(";"):
            if ":" in pair:
                self.keys.add(pair.split(":", 1)[1].strip())
        if tag == "script":
            self.in_script = True
            source = ("src", attributes["src"]) if attributes.get("src") else ("inline", "")
            self.scripts.append(Script(*source, timing(attributes)))
        if tag not in VOID:
            self.stack.append((tag, is_zh))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.in_script and self.scripts and self.scripts[-1].kind == "inline":
            self.scripts[-1] = self.scripts[-1]._replace(value=self.scripts[-1].value + data)
        if not self.exempt() and HAN.search(data):
            self.han.append(data.strip()[:80])


def parse_page(relative):
    return parse_html((ROOT / relative).read_text(encoding="utf-8-sig"))


def parse_html(text):
    page = Page()
    page.feed(text)
    page.close()
    return page


class MessageTests(unittest.TestCase):
    def test_every_message_file_pairs_languages_and_placeholders(self):
        files = sorted((ROOT / "src/i18n").glob("*.js"))
        self.assertIn(ROOT / "src/i18n/common.js", files)
        seen = {}
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(file=relative):
                tables = message_tables(relative)
                self.assertEqual(set(tables), set(LANGUAGES))
                self.assertEqual(set(tables["en"]), set(tables["zh-CN"]), "English and Chinese keys differ")
                for key, english in tables["en"].items():
                    chinese = tables["zh-CN"][key]
                    self.assertTrue(english.strip() and chinese.strip(), f"Empty string: {key}")
                    self.assertEqual(set(PLACEHOLDER.findall(english)), set(PLACEHOLDER.findall(chinese)), f"Placeholder mismatch: {key}")
                    self.assertNotIn(key, seen, f"{key} is defined in both {seen.get(key)} and {relative}")
                    seen[key] = relative

    def test_message_files_are_all_loaded_by_a_page(self):
        loaded = {script.value for page in PAGES for script in parse_page(page).scripts if script.kind == "src"}
        for path in sorted((ROOT / "src/i18n").glob("*.js")):
            self.assertIn(path.relative_to(ROOT).as_posix(), loaded)

    def test_english_strings_hold_no_han_and_chinese_strings_are_translated(self):
        """English strings carry no Chinese except the toggle naming Chinese; each Chinese UI string differs from the
        English one unless it is a name or code that reads the same in both (SHA-1, Wiki, the toggle)."""
        messages = all_messages()
        same_allowed = {"comparison.sha1_match", "family.wikis"}
        for key, english in messages["en"].items():
            with self.subTest(key=key):
                if key != "lang.toggle":
                    self.assertFalse(HAN.search(english), english)
                if key not in same_allowed and key != "lang.toggle":
                    self.assertNotEqual(english, messages["zh-CN"][key], "untranslated Chinese string")

    def test_titles_and_credit_labels(self):
        messages = all_messages()
        self.assertEqual(messages["en"]["meta.title"], "Dynamix Artwork Archive")
        self.assertEqual(messages["zh-CN"]["meta.title"], "Dynamix 曲绘档案")
        for lang in LANGUAGES:
            self.assertEqual(messages[lang]["header.title"], messages[lang]["meta.title"])
            self.assertEqual(messages[lang]["slideshow.title.short"], messages[lang]["meta.title"])
        self.assertEqual([messages["en"][key] for key in ("credit.illustrator", "credit.composer", "credit.unknown")], ["Illustrator", "Composer", "Unknown"])
        self.assertEqual([messages["zh-CN"][key] for key in ("credit.illustrator", "credit.composer", "credit.unknown")], ["画师", "曲师", "未知"])


class PageTests(unittest.TestCase):
    maxDiff = None

    def check_page(self, relative):
        page = parse_page(relative)
        self.assertEqual(page.root_lang, "en", "English is the default document language")
        sources = [script.value for script in page.scripts if script.kind == "src"]
        self.assertIn(RUNTIME, sources)
        self.assertIn("src/i18n/common.js", sources)
        tables_loaded = [src for src in sources if src.startswith("src/i18n/")]
        self.assertEqual(callers_of(page), set(PAGES[relative]), f"PAGES must list every script of {relative} that uses DYNAMIX_I18N")
        self.assertEqual(script_order_problems(page.scripts, set(PAGES[relative])), [], f"Script order in {relative}")
        keys = set()
        for src in tables_loaded:
            keys |= set(message_tables(src)["en"])
        code = "\n".join(script.value for script in page.scripts if script.kind == "inline")
        code += "\n".join((ROOT / script).read_text(encoding="utf-8") for script in PAGES[relative])
        used = page.keys | {match[1] for match in T_CALL.findall(code)}
        self.assertEqual(sorted(used - keys), [], f"Keys used by {relative} but not defined")
        for match in T_PREFIX.findall(code):
            prefix = match[0] or match[2]
            self.assertTrue(any(key.startswith(prefix) for key in keys), f"No keys start with {prefix!r}")
        self.assertEqual(page.han, [], f"Untranslated Han text in {relative}; tag it lang=\"zh-CN\" or move it to src/i18n/")
        toggles = len(re.findall(r"data-lang-toggle", (ROOT / relative).read_text(encoding="utf-8-sig")))
        self.assertGreaterEqual(toggles, 1, f"{relative} needs a language toggle")

    def test_archive_page(self):
        self.check_page("archive.html")

    def test_slideshow_template(self):
        self.check_page("templates/slideshow.html")

    def test_static_english_text_matches_the_english_table(self):
        """The markup's own text is the English page before any script runs (no flash of a different wording)."""
        messages = all_messages()["en"]

        class Texts(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.found, self.stack = [], []

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag not in VOID:
                    self.stack.append([attributes.get("data-i18n"), ""])
                for pair in (attributes.get("data-i18n-attr") or "").split(";"):
                    if ":" in pair:
                        name, key = (part.strip() for part in pair.split(":", 1))
                        self.found.append((key, attributes.get(name)))

            def handle_endtag(self, tag):
                if self.stack:
                    key, text = self.stack.pop()
                    if key:
                        self.found.append((key, text.strip()))

            def handle_data(self, data):
                if self.stack:
                    self.stack[-1][1] += data

        for relative in PAGES:
            parser = Texts()
            parser.feed((ROOT / relative).read_text(encoding="utf-8-sig"))
            for key, text in parser.found:
                with self.subTest(page=relative, key=key):
                    self.assertEqual(" ".join((text or "").split()), " ".join(messages[key].split()))

    def test_archive_brand_line_stays_english_on_purpose(self):
        class Eyebrows(HTMLParser):
            def __init__(self):
                super().__init__()
                self.found = []

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if "eyebrow" in (attributes.get("class") or "").split():
                    self.found.append(attributes)

        parser = Eyebrows()
        parser.feed((ROOT / "archive.html").read_text(encoding="utf-8-sig"))
        self.assertEqual(len(parser.found), 1)
        self.assertEqual((parser.found[0].get("lang"), parser.found[0].get("translate")), ("en", "no"))
        self.assertNotIn("data-i18n", parser.found[0])

    def test_scripts_hold_no_hardcoded_chinese(self):
        for relative in ["src/i18n.js", *sorted({script for scripts in PAGES.values() for script in scripts})]:
            with self.subTest(script=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(HAN.findall(text), [], f"Move Chinese strings in {relative} to src/i18n/")

    def test_any_timing_change_of_an_i18n_script_is_caught(self):
        for relative, callers in PAGES.items():
            scripts = parse_page(relative).scripts
            for index, script in enumerate(scripts):
                for other in sorted({"parser", "defer", "async"} - {script.timing}):
                    mutated = scripts[:index] + [script._replace(timing=other)] + scripts[index + 1:]
                    with self.subTest(page=relative, script=script.value[:40], timing=other):
                        if script_role(script, set(callers)):
                            self.assertNotEqual(script_order_problems(mutated, set(callers)), [])
                        else:
                            self.assertEqual(script_order_problems(mutated, set(callers)), [])

    def test_manifest_name_is_english(self):
        manifest = json.loads((ROOT / "site.webmanifest").read_text(encoding="utf-8"))
        for field in ("name", "short_name"):
            self.assertFalse(HAN.search(manifest[field]), field)
        self.assertIn("Dynamix", manifest["name"])


class BrandingTests(unittest.TestCase):
    """The language runtime and the pages use the Dynamix names throughout; DEEMO is named only to attribute code."""

    def test_runtime_globals_and_storage_key(self):
        runtime = (ROOT / RUNTIME).read_text(encoding="utf-8")
        self.assertIn('STORAGE_KEY = "dynamix-lang"', runtime)
        self.assertIn("window.DYNAMIX_I18N = Object.freeze(", runtime)
        self.assertIn('new URLSearchParams(location.search).get("lang")', runtime)
        self.assertIn("window.DYNAMIX_CATALOG", (ROOT / "src/archive.js").read_text(encoding="utf-8"))

    def test_deemo_is_named_only_in_attribution(self):
        for relative in UI_TEXT_FILES:
            for number, line in enumerate((ROOT / relative).read_text(encoding="utf-8-sig").splitlines(), 1):
                with self.subTest(file=relative, line=number):
                    if re.search(r"deemo", line, re.IGNORECASE):
                        self.assertRegex(line, ATTRIBUTION, "DEEMO may appear only in a derivation/attribution notice")
                    self.assertIsNone(FOREIGN.search(line), "DEEMO-specific name or media in the Dynamix interface")

    def test_repository_links_point_to_dynamix(self):
        for relative in UI_TEXT_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8-sig")
            for link in re.findall(r"https://github\.com/FridrichMethod/[\w.-]+", text):
                with self.subTest(file=relative, link=link):
                    self.assertIn(link.rsplit("/", 1)[1], {"dynamix", "deemo"})
                    if link.endswith("/deemo"):
                        self.assertRegex(text, ATTRIBUTION)


class ScriptOrderTests(unittest.TestCase):
    """The order model itself, on small pages shaped like archive.html (all defer) and the slideshow (all classic)."""

    DEFERRED = ('<script src="src/i18n.js" defer></script><script src="src/i18n/common.js" defer></script>'
                '<script src="src/i18n/archive.js" defer></script><script src="data/catalog.js" defer></script>'
                '<script src="src/archive.js" defer></script>')
    CALLERS = {"src/archive.js"}

    def problems(self, html, callers=CALLERS):
        return script_order_problems(parse_html(html).scripts, callers)

    def test_timing_follows_the_html_rules(self):
        cases = [
            ('<script src="a.js"></script>', "parser"), ('<script src="a.js" defer></script>', "defer"),
            ('<script src="a.js" async></script>', "async"), ('<script src="a.js" defer async></script>', "async"),
            ('<script type="module" src="a.js"></script>', "defer"), ('<script type="module" async src="a.js"></script>', "async"),
            ("<script defer>x()</script>", "parser"), ("<script async>x()</script>", "parser"),
            ('<script type="module">x()</script>', "defer"),
        ]
        for html, expected in cases:
            with self.subTest(html=html):
                self.assertEqual(parse_html(html).scripts[0].timing, expected)

    def test_pages_in_one_timing_and_in_order_pass(self):
        self.assertEqual(self.problems(self.DEFERRED), [])
        self.assertEqual(self.problems(self.DEFERRED.replace(" defer", "")), [])
        classic = '<script src="src/i18n.js"></script><script src="src/i18n/common.js"></script><script>DYNAMIX_I18N.t("x");</script>'
        self.assertEqual(self.problems(classic, set()), [])

    def test_a_caller_without_defer_runs_while_the_page_is_parsed(self):
        problems = self.problems(self.DEFERRED.replace('"src/archive.js" defer', '"src/archive.js"'))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("src/archive.js (parser)", problems[0])

    def test_an_async_table_may_run_at_any_time(self):
        problems = self.problems(self.DEFERRED.replace('"src/i18n/archive.js" defer', '"src/i18n/archive.js" async'))
        self.assertIn("src/i18n/archive.js is async, so it may run before or after the others", problems)

    def test_document_order_matters_within_one_timing(self):
        tables_first = self.DEFERRED.replace('<script src="src/i18n.js" defer></script>', "") + '<script src="src/i18n.js" defer></script>'
        self.assertIn("src/archive.js (caller) runs before src/i18n.js (runtime)", " ".join(self.problems(tables_first)))

    def test_inline_code_ignores_defer(self):
        page = '<script src="src/i18n.js" defer></script><script src="src/i18n/common.js" defer></script><script defer>DYNAMIX_I18N.t("x");</script>'
        self.assertTrue(any("mixed timings" in problem for problem in self.problems(page, set())))

    def test_missing_runtime_or_tables(self):
        self.assertIn("src/i18n.js is loaded 0 times", self.problems('<script src="src/i18n/common.js"></script>'))
        self.assertIn("no message table (src/i18n/*.js) is loaded", self.problems('<script src="src/i18n.js"></script>'))


class SiteReadmeTests(unittest.TestCase):
    def test_site_asset_readme_is_bilingual_and_lists_every_file(self):
        text = (ROOT / "assets/site/README.md").read_text(encoding="utf-8")
        english, _, chinese = text.partition("## 简体中文")
        self.assertTrue(chinese, "assets/site/README.md needs a Simplified Chinese section")
        self.assertEqual(english.count("\n| `"), chinese.count("\n| `"), "Both languages list the same rows")
        for path in sorted((ROOT / "assets/site").rglob("*")):
            if path.is_file() and path.name != "README.md":
                with self.subTest(file=path.name):
                    self.assertIn(path.name, english)
                    self.assertIn(path.name, chinese)


if __name__ == "__main__":
    unittest.main()
