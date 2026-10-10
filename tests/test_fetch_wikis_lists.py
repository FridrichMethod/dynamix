"""Unit tests of the list-graphic rule (coordinator decision 2026-10-10): ``python -I tests/test_fetch_wikis_lists.py``.

A graphic whose primary content is a list of songs or charts is kind ``reference`` regardless of the wiki category or
caption that files it as promotional; a graphic about a single song or event stays ``promotional``. No network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wikis import classify, config, reviews  # noqa: E402

TITLE = "File:Events in 2020 June.png"
INFO = {"mime": "image/png", "width": 820, "height": 462}


def _review(**fields) -> reviews.Review:
    entry = {"title": TITLE, "sha1": ["a" * 40], "role": "reference", "list_graphic": True, "game": "dynamix",
             "evidence": "visual_inspection: monthly event lineup listing three event-song jackets",
             "game_evidence": "visual_inspection: original DynAMIX wordmark"}
    entry.update(fields)
    entry = {key: value for key, value in entry.items() if value is not None or key in ("role", "game")}
    return reviews.parse({"format": reviews.FORMAT, "reviews": [entry]})[TITLE]


def _category(category: str, key: str = "miraheze") -> list[classify.Evidence]:
    """Evidence of a file category, as :func:`classify.usage_evidence` builds it from the API."""
    return classify.usage_evidence(config.SOURCES[key], TITLE, {"categories": [{"ns": 14, "title": category}]}, {},
                                   set())


def _caption(caption: str, page: str = "Cosmic Pulse") -> classify.Evidence:
    """Evidence of a gallery caption on an original-game song page, as :func:`classify.page_evidence` builds it."""
    text = "{{Song\n|Title = %s\n|Imgsuffix = Cover.png\n}}\n<gallery>\n%s|%s\n</gallery>" % (
        page, TITLE.split(":", 1)[1], caption)
    raw = {"title": page, "revision": {"content": text, "revid": 1}, "categories": ["Category:Songs"]}
    info = classify.analyze_page(config.MIRAHEZE, raw)
    files = {TITLE: dict(INFO), "File:Cover.png": dict(INFO)}
    found = dict(classify.page_evidence(config.MIRAHEZE, raw, info, files, {}))
    return found[TITLE]


def _decide(items: list[classify.Evidence], review: reviews.Review | None = None) -> classify.Decision:
    extra = classify.review_evidence(config.MIRAHEZE, TITLE, review) if review is not None else []
    return classify.decide(TITLE, items + extra, dict(INFO), frames=None, include_animated=False,
                           fandom_timestamp=None, threshold=None, review=review)


class ListEvidenceTests(unittest.TestCase):
    def test_list_captions(self) -> None:
        """List captions are references and flagged as list evidence; other informational captions are references
        without being lists; event, collaboration and teaser captions stay promotional."""
        for caption in ("Chrysoberyl, Cryonix, Marion.net and Soul Army Chart List", "Cosmic Pulse Chart List",
                        "Crank Up X Evoltex (poppi'n mix) Chart List Comeback", "春菊 & Perspicuus Aestus New Chart",
                        "Upcoming Events", "Coming Events in May", "Monthly Comeback Tracks", "June event lineup"):
            item = _caption(caption)
            self.assertEqual((item.role, item.list_graphic), ("reference", True), caption)
        for caption in ("2.4 Version Update", "New Settings Announcement", "Dynamix Nov 2023 update log"):
            item = _caption(caption)
            self.assertEqual((item.role, item.list_graphic), ("reference", False), caption)
        for caption in ("Christmas Event 2021", "Dynamix X Rotaeno Collaboration", "New Exclusive Song \"ReLyna\"",
                        "9th Anniversary x Halloween Event 2023"):
            item = _caption(caption)
            self.assertEqual((item.role, item.list_graphic), ("promotional", False), caption)

    def test_categories_and_filenames(self) -> None:
        [chart] = _category("Category:Chart list images")
        self.assertEqual((chart.role, chart.list_graphic), ("reference", True))
        [event] = _category("Category:Event images")
        self.assertEqual((event.role, event.list_graphic), ("promotional", False))
        # A filename is never list evidence (it only fills a missing role, see config.FILENAME_ROLES).
        [name] = classify.filename_evidence(config.MIRAHEZE, "File:1st Comeback in Nov 2019.jpg")
        self.assertEqual((name.role, name.strength, name.list_graphic), ("reference", 0, False))

    def test_review_field(self) -> None:
        self.assertTrue(_review().list_graphic)
        self.assertFalse(_review(list_graphic=None).list_graphic)
        for fields in (dict(role="promotional"), dict(list_graphic="yes"), dict(list_graphic=1)):
            with self.assertRaises(ValueError, msg=fields):
                _review(**fields)
        committed = [review for review in reviews.load().values() if review.list_graphic]
        self.assertTrue(committed)
        self.assertEqual({review.role for review in committed}, {"reference"})


class ListRuleTests(unittest.TestCase):
    def test_list_review_overrides_an_event_category(self) -> None:
        """Category:Event images says promotional; the archivist saw a monthly lineup: reference, recording both the
        category evidence and the rule; the mapping that rests on the review is inferred at most."""
        items = _category("Category:Event images") + _category("Category:Event images", "fandom")
        before = _decide(items)
        self.assertEqual((before.role, before.rule_notes), ("promotional", []))
        decision = _decide(items, _review(songs=["Cosmic Pulse"]))
        self.assertEqual((decision.role, decision.game, decision.disposition), ("reference", "dynamix", "download"))
        self.assertTrue(decision.role_evidence[0].startswith(
            "reference <- list-graphic rule (coordinator decision 2026-10-10)"))
        self.assertIn("list evidence: archivist review (File:Events in 2020 June.png): visual_inspection: monthly "
                      "event lineup", decision.role_evidence[0])
        self.assertIn("it overrides the promotional page evidence listed here", decision.role_evidence[0])
        self.assertIn("promotional <- https://dynamix.miraheze.org/wiki/File:Events_in_2020_June.png (file category; "
                      "Category:Event images)", decision.role_evidence)
        [note] = decision.rule_notes
        self.assertTrue(note.startswith("Kind reference by the list-graphic rule"))
        self.assertIn("Promotional page evidence it overrides: https://dynamix.miraheze.org/wiki/"
                      "File:Events_in_2020_June.png (file category; Category:Event images); "
                      "https://dynamixc4cat.fandom.com/wiki/File:Events_in_2020_June.png", note)
        self.assertEqual((decision.songs, decision.mapping), (["Cosmic Pulse"], "inferred"))

    def test_list_caption_overrides_promotional_evidence(self) -> None:
        """A list caption on one page against an event category (a tie) or against event captions on more pages:
        the list content decides."""
        listed = _caption("Chrysoberyl, Cryonix, Marion.net and Soul Army Chart List", "Chrysoberyl")
        tie = _decide([listed] + _category("Category:Event images"))
        self.assertEqual((tie.role, tie.disposition), ("reference", "download"))
        self.assertIn("List evidence: https://dynamix.miraheze.org/wiki/Chrysoberyl (gallery; caption 'Chrysoberyl, "
                      "Cryonix, Marion.net and Soul Army Chart List')", tie.rule_notes[0])
        self.assertIn("Category:Event images", tie.rule_notes[0])
        majority = _decide([listed, _caption("November Event", "A"), _caption("November Event", "B")])
        self.assertEqual(majority.role, "reference")
        self.assertEqual(majority.mapping, "confirmed_wiki")  # page evidence, not a review, names the song

    def test_single_song_or_event_stays_promotional(self) -> None:
        """No list evidence: event categories, event/teaser captions and a list-like filename stay promotional; a
        review that saw one song or event keeps the file promotional even against a list caption."""
        name = classify.filename_evidence(config.MIRAHEZE, "File:Helios Comeback.png")
        for items in (_category("Category:Event images"), [_caption("Christmas Event 2021")],
                      [_caption("New Exclusive Song \"ReLyna\"")], name + _category("Category:Event images")):
            decision = _decide(items)
            self.assertEqual((decision.role, decision.rule_notes), ("promotional", []), items)
        teaser = _review(role="promotional", list_graphic=None,
                         evidence="visual_inspection: single-song COMING SOON teaser with difficulty marks")
        listed = _caption("PUPA Chart List", "PUPA")
        decision = _decide([listed] + _category("Category:Event images"), teaser)
        self.assertEqual((decision.role, decision.rule_notes), ("promotional", []))

    def test_rule_replaces_only_a_promotional_role(self) -> None:
        cover = classify.Evidence("wikis:miraheze", "Cosmic Pulse", "https://dynamix.miraheze.org/wiki/Cosmic_Pulse",
                                  "Template:Song|Imgsuffix", "song_art", 3, "dynamix", "Cosmic Pulse", "Cosmic Pulse")
        self.assertEqual(_decide([cover], _review()).role, "song_art")
        character = classify.Evidence("wikis:miraheze", "Characters", "https://dynamix.miraheze.org/wiki/Characters",
                                      "gallery", "character", 2, "dynamix")
        self.assertEqual(_decide([character, _caption("Upcoming Events", "A")] + _category(
            "Category:Event images")).disposition, "pending")  # a three-way tie is not a list question
        self.assertEqual(_decide([character], _review()).role, "character")

    def test_reviewed_list_without_page_role(self) -> None:
        """No page gives a role: the list review decides reference and the rule is recorded without an override."""
        decision = _decide([], _review())
        self.assertEqual((decision.role, decision.disposition), ("reference", "download"))
        self.assertEqual(decision.role_evidence[0], "reference <- " + config.LIST_GRAPHIC_RULE
                         + "; list evidence: the archivist review (File:Events in 2020 June.png)")
        self.assertTrue(decision.role_evidence[1].startswith("reference <- archivist review (File:Events in 2020 "
                                                             "June.png): visual_inspection: monthly event lineup"))
        [note] = decision.rule_notes
        self.assertNotIn("overrides", note)
        self.assertIn("List evidence: archivist review (File:Events in 2020 June.png)", note)
        plain = _decide([], _review(list_graphic=None))  # a reference review without the list flag: no rule
        self.assertEqual((plain.role, plain.rule_notes), ("reference", []))

    def test_page_reference_needs_no_rule(self) -> None:
        """A chart list captioned as such is reference by its caption (tier revision 1.1); nothing is recorded."""
        decision = _decide([_caption("Cosmic Pulse Chart List")])
        self.assertEqual((decision.role, decision.rule_notes), ("reference", []))
        self.assertFalse(any("list-graphic rule" in entry for entry in decision.role_evidence))


if __name__ == "__main__":
    unittest.main()
