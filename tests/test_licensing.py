"""Licensing: the software license, vendored-code license texts, and the separation of artwork rights in NOTICE.

LICENSE (Apache-2.0) is always checked. NOTICE, ``src/vendor/`` and ``licenses/`` belong to other branches until
integration: while a file or directory is absent the corresponding test is skipped with a message naming it; once it
exists, it must satisfy every check (no test passes because a file is missing).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APACHE_MARKERS = ("Apache License", "Version 2.0, January 2004", "http://www.apache.org/licenses/",
                  "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION", "1. Definitions.",
                  "2. Grant of Copyright License.", "3. Grant of Patent License.", "4. Redistribution.",
                  "9. Accepting Warranty or Additional Liability.", "END OF TERMS AND CONDITIONS")
MIT_GRANT = "Permission is hereby granted, free of charge, to any person"
MIT_DISCLAIMER = 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND'
# Components known to be bundled in vendored files: license text -> (its first line, a marker in the bundle, name).
VENDOR_LICENSES = {
    "licenses/html2canvas-MIT.txt": ("Copyright (c) 2012 Niklas von Hertzen", b"html2canvas 0.5.0-beta3",
                                     "html2canvas 0.5.0-beta3"),
    "licenses/punycode-MIT.txt": ("Copyright Mathias Bynens", b'version:"1.2.4"', "punycode.js 1.2.4"),
    "licenses/pace-MIT.txt": ("Copyright (c) 2013 HubSpot, Inc.", b"/*! pace 1.0.0 */", "Pace 1.0.0"),
}
LICENSE_INDEXES = ("licenses/README.md", "licenses/README.zh-CN.md")
ARTWORK = r"\b(?:artworks?|art|illustrations?|images?|media|covers?|jackets?|assets?)\b"
NEGATION = r"(?:\b(?:not|no|never|neither|nor|excludes?|separate|outside)\b|n't)"
LICENSE_WORD = r"(?:licen[cs]|cover|apply|appli|grant)"


def words(text: str) -> str:
    return " ".join(text.split())


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?。])\s+|\n\s*\n", text) if part.strip()]


def require(test: unittest.TestCase, relative: str) -> Path:
    path = ROOT / relative
    if not path.exists():
        test.skipTest(f"{relative} is not in this tree yet; its checks apply as soon as it exists")
    return path


class SoftwareLicenseTests(unittest.TestCase):
    def test_license_is_apache_2_0(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(text.lstrip().startswith("Apache License"))
        normalized = words(text)
        for marker in APACHE_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, normalized)
        self.assertNotIn("MIT License", normalized)


class VendorLicenseTests(unittest.TestCase):
    def vendored(self) -> list[Path]:
        directory = require(self, "src/vendor")
        files = sorted(path for path in directory.rglob("*") if path.is_file())
        self.assertTrue(files, "src/vendor/ exists but is empty; remove it or vendor the code with its license")
        return files

    def test_every_vendored_file_is_a_known_component_with_its_license_text(self):
        for path in self.vendored():
            content = path.read_bytes()
            known = [relative for relative, (_, marker, _) in VENDOR_LICENSES.items() if marker in content]
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                self.assertTrue(known, "unrecognized vendored code: add its license text under licenses/ and its "
                                       "marker to VENDOR_LICENSES in this test")
            for relative in known:
                first_line, _, _ = VENDOR_LICENSES[relative]
                with self.subTest(file=path.name, license=relative):
                    license_path = ROOT / relative
                    self.assertTrue(license_path.is_file(), f"missing license text {relative}")
                    text = license_path.read_text(encoding="utf-8")
                    self.assertTrue(text.startswith(first_line))
                    self.assertIn(MIT_GRANT, words(text))
                    self.assertIn(MIT_DISCLAIMER, words(text))

    def test_vendored_components_are_named_in_notice_and_license_indexes(self):
        bundle = b"".join(path.read_bytes() for path in self.vendored())
        documents = [relative for relative in ("NOTICE", *LICENSE_INDEXES) if (ROOT / relative).is_file()]
        if not documents:
            self.skipTest("neither NOTICE nor licenses/README*.md is in this tree yet")
        for document in documents:
            text = (ROOT / document).read_text(encoding="utf-8")
            for relative, (_, marker, component) in VENDOR_LICENSES.items():
                if marker in bundle:
                    with self.subTest(document=document, license=relative):
                        self.assertIn(Path(relative).name, text)
                        self.assertIn(component, text)


class LicenseTextTests(unittest.TestCase):
    def test_mit_texts_are_complete(self):
        texts = sorted(require(self, "licenses").glob("*-MIT.txt"))
        if not texts:
            self.skipTest("licenses/ has no *-MIT.txt yet")
        for path in texts:
            with self.subTest(file=path.name):
                text = words(path.read_text(encoding="utf-8"))
                self.assertIn(MIT_GRANT, text)
                self.assertIn(MIT_DISCLAIMER, text)
                self.assertRegex(text[:200], r"Copyright")


class NoticeTests(unittest.TestCase):
    def notice(self) -> str:
        return require(self, "NOTICE").read_text(encoding="utf-8")

    def test_notice_names_c4cat_and_the_software_license(self):
        text = words(self.notice())
        self.assertIn("C4Cat", text)
        self.assertRegex(text, r"Apache(?: License,? Version 2\.0|-2\.0| License 2\.0)")

    def test_notice_separates_artwork_rights_from_the_software_license(self):
        statements = [s for s in sentences(self.notice()) if re.search(ARTWORK, s, re.IGNORECASE)
                      and re.search(NEGATION, s, re.IGNORECASE) and re.search(LICENSE_WORD, s, re.IGNORECASE)]
        self.assertTrue(statements, "NOTICE must state that the software license does not cover the artwork")
        c4cat = [s for s in sentences(self.notice()) if "C4Cat" in s]
        self.assertTrue(any(re.search(ARTWORK, s, re.IGNORECASE) for s in c4cat),
                        "NOTICE must attribute the artwork rights to C4Cat and/or the credited artists")

    def test_notice_does_not_license_the_artwork_under_apache(self):
        for sentence in sentences(self.notice()):
            if re.search(ARTWORK, sentence, re.IGNORECASE) and re.search(r"licensed under (?:the )?Apache", sentence):
                with self.subTest(sentence=sentence[:80]):
                    self.assertRegex(sentence, re.compile(NEGATION, re.IGNORECASE),
                                     "artwork must not be offered under Apache-2.0")


if __name__ == "__main__":
    unittest.main()
