"""Unit tests of the list and chart-announcement rule (coordinator decision 2026-10-10 as amended):
``python -I tests/test_fetch_wikis_lists.py``.

An announcement graphic that displays chart difficulty levels (for one song or several) or whose primary content is a
list of songs or charts is kind ``reference`` regardless of the wiki category or caption that files it as
promotional; key art, event posters and teasers without level marks stay ``promotional``. No network.
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
SONGS = [
    "Cosmic Pulse",
    "PUPA",
    "Chrysoberyl",
    "Cryonix",
    "Marion.net",
    "Soul Army",
    "Angel x Angel",
    "The Path",
    "Time & Space (Dynamix Edit)",
    "16383",
    "紅",
    "Sakura Fubuki",
    "Now Is The Time, Do It",
    "Arcana Idola",
    "diSTractiOn PoInT",
    "Frontline B-Side",
    "A",
    "B",
]
NAMES = classify.SongNames.build((title, []) for title in SONGS)


def _review(**fields) -> reviews.Review:
    entry = {
        "title": TITLE,
        "sha1": ["a" * 40],
        "role": "reference",
        "list_graphic": True,
        "game": "dynamix",
        "evidence": "visual_inspection: monthly event lineup listing three event-song jackets",
        "game_evidence": "visual_inspection: original DynAMIX wordmark",
    }
    entry.update(fields)
    entry = {key: value for key, value in entry.items() if value is not None or key in ("role", "game")}
    return reviews.parse({"format": reviews.FORMAT, "reviews": [entry]})[TITLE]


def _category(category: str, key: str = "miraheze") -> list[classify.Evidence]:
    """Evidence of a file category, as :func:`classify.usage_evidence` builds it from the API."""
    return classify.usage_evidence(
        config.SOURCES[key], TITLE, {"categories": [{"ns": 14, "title": category}]}, {}, set()
    )


def _caption(caption: str, page: str = "Cosmic Pulse", title: str = TITLE) -> classify.Evidence:
    """Evidence of a gallery caption on an original-game song page, as :func:`classify.page_evidence` builds it."""
    text = "{{Song\n|Title = %s\n|Imgsuffix = Cover.png\n}}\n<gallery>\n%s|%s\n</gallery>" % (
        page,
        title.split(":", 1)[1],
        caption,
    )
    raw = {"title": page, "revision": {"content": text, "revid": 1}, "categories": ["Category:Songs"]}
    info = classify.analyze_page(config.MIRAHEZE, raw)
    files = {title: dict(INFO), "File:Cover.png": dict(INFO)}
    found = dict(classify.page_evidence(config.MIRAHEZE, raw, info, files, {}))
    return found[title]


def _decide(
    items: list[classify.Evidence],
    review: reviews.Review | None = None,
    *,
    title: str = TITLE,
    names: classify.SongNames | None = NAMES,
) -> classify.Decision:
    extra = classify.review_evidence(config.MIRAHEZE, title, review) if review is not None else []
    return classify.decide(
        title,
        items + extra,
        dict(INFO),
        frames=None,
        include_animated=False,
        fandom_timestamp=None,
        threshold=None,
        review=review,
        song_names=names,
    )


class ListEvidenceTests(unittest.TestCase):
    def test_list_captions(self) -> None:
        """List captions are references and flagged as list evidence; other informational captions are references
        without being lists; event, collaboration and teaser captions stay promotional."""
        for caption in (
            "Chrysoberyl, Cryonix, Marion.net and Soul Army Chart List",
            "Cosmic Pulse Chart List",
            "Crank Up X Evoltex (poppi'n mix) Chart List Comeback",
            "春菊 & Perspicuus Aestus New Chart",
            "Upcoming Events",
            "Coming Events in May",
            "Monthly Comeback Tracks",
            "June event lineup",
        ):
            item = _caption(caption)
            self.assertEqual((item.role, item.list_graphic), ("reference", True), caption)
        for caption in ("2.4 Version Update", "New Settings Announcement", "Dynamix Nov 2023 update log"):
            item = _caption(caption)
            self.assertEqual((item.role, item.list_graphic), ("reference", False), caption)
        for caption in (
            "Christmas Event 2021",
            "Dynamix X Rotaeno Collaboration",
            'New Exclusive Song "ReLyna"',
            "9th Anniversary x Halloween Event 2023",
            "Upcoming Event: Halloween 2023",
            "Coming Event",
        ):
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
        self.assertTrue(
            decision.role_evidence[0].startswith(
                "reference <- list and chart-announcement rule (coordinator decision 2026-10-10, amended)"
            )
        )
        self.assertIn(
            "list evidence: archivist review (File:Events in 2020 June.png): visual_inspection: monthly event lineup",
            decision.role_evidence[0],
        )
        self.assertIn("it overrides the promotional page evidence listed here", decision.role_evidence[0])
        self.assertIn(
            "promotional <- https://dynamix.miraheze.org/wiki/File:Events_in_2020_June.png (file category; "
            "Category:Event images)",
            decision.role_evidence,
        )
        [note] = decision.rule_notes
        self.assertTrue(note.startswith("Kind reference by the list and chart-announcement rule"))
        self.assertIn(
            "Promotional page evidence it overrides: https://dynamix.miraheze.org/wiki/"
            "File:Events_in_2020_June.png (file category; Category:Event images); "
            "https://dynamixc4cat.fandom.com/wiki/File:Events_in_2020_June.png",
            note,
        )
        self.assertEqual((decision.songs, decision.mapping), (["Cosmic Pulse"], "inferred"))

    def test_list_caption_overrides_promotional_evidence(self) -> None:
        """A list caption on one page against an event category (a tie) or against event captions on more pages:
        the list content decides."""
        listed = _caption("Chrysoberyl, Cryonix, Marion.net and Soul Army Chart List", "Chrysoberyl")
        tie = _decide([listed] + _category("Category:Event images"))
        self.assertEqual((tie.role, tie.disposition), ("reference", "download"))
        self.assertIn(
            "List evidence: https://dynamix.miraheze.org/wiki/Chrysoberyl (gallery; caption 'Chrysoberyl, "
            "Cryonix, Marion.net and Soul Army Chart List')",
            tie.rule_notes[0],
        )
        self.assertIn("Category:Event images", tie.rule_notes[0])
        majority = _decide([listed, _caption("November Event", "A"), _caption("November Event", "B")])
        self.assertEqual(majority.role, "reference")
        self.assertEqual(majority.mapping, "confirmed_wiki")  # page evidence, not a review, names the songs
        self.assertEqual(majority.songs, ["Chrysoberyl", "Cryonix", "Marion.net", "Soul Army"])

    def test_mapping_that_rests_on_a_list_review_is_inferred(self) -> None:
        """A non-list reference caption names a song; promotional captions on two pages win the majority; only the
        list review makes the file a reference, so the song mapping rests on the review: inferred, not confirmed."""
        items = [
            _caption("2.4 Version Update", "Cosmic Pulse"),
            _caption("November Event", "A"),
            _caption("November Event", "B"),
        ]
        self.assertEqual(_decide(items).role, "promotional")
        decision = _decide(items, _review())
        self.assertEqual((decision.role, decision.songs, decision.mapping), ("reference", ["Cosmic Pulse"], "inferred"))

    def test_event_and_teaser_without_list_evidence_stay_promotional(self) -> None:
        """No list evidence: event categories, event/teaser captions and a list-like filename stay promotional; a
        review that saw neither a list nor level marks keeps the file promotional even against a list caption."""
        name = classify.filename_evidence(config.MIRAHEZE, "File:Helios Comeback.png")
        for items in (
            _category("Category:Event images"),
            [_caption("Christmas Event 2021")],
            [_caption('New Exclusive Song "ReLyna"')],
            name + _category("Category:Event images"),
        ):
            decision = _decide(items)
            self.assertEqual((decision.role, decision.rule_notes), ("promotional", []), items)
        teaser = _review(
            role="promotional",
            list_graphic=None,
            evidence="visual_inspection: COMING SOON teaser with the Dynamix icon and no level marks",
        )
        listed = _caption("PUPA Chart List", "PUPA")
        decision = _decide([listed] + _category("Category:Event images"), teaser)
        self.assertEqual((decision.role, decision.rule_notes), ("promotional", []))
        self.assertTrue(decision.role_evidence[0].startswith("promotional <- archivist review"))

    def test_rule_replaces_only_a_promotional_role(self) -> None:
        cover = classify.Evidence(
            "wikis:miraheze",
            "Cosmic Pulse",
            "https://dynamix.miraheze.org/wiki/Cosmic_Pulse",
            "Template:Song|Imgsuffix",
            "song_art",
            3,
            "dynamix",
            "Cosmic Pulse",
            "Cosmic Pulse",
        )
        self.assertEqual(_decide([cover], _review()).role, "song_art")
        character = classify.Evidence(
            "wikis:miraheze",
            "Characters",
            "https://dynamix.miraheze.org/wiki/Characters",
            "gallery",
            "character",
            2,
            "dynamix",
        )
        self.assertEqual(
            _decide([character, _caption("Upcoming Events", "A")] + _category("Category:Event images")).disposition,
            "pending",
        )  # a three-way tie is not a list question
        self.assertEqual(_decide([character], _review()).role, "character")

    def test_reviewed_list_without_page_role(self) -> None:
        """No page gives a role: the list review decides reference and the rule is recorded without an override."""
        decision = _decide([], _review())
        self.assertEqual((decision.role, decision.disposition), ("reference", "download"))
        self.assertEqual(
            decision.role_evidence[0],
            "reference <- "
            + config.LIST_GRAPHIC_RULE
            + "; list evidence: the archivist review (File:Events in 2020 June.png)",
        )
        self.assertTrue(
            decision.role_evidence[1].startswith(
                "reference <- archivist review (File:Events in 2020 June.png): visual_inspection: monthly event lineup"
            )
        )
        [note] = decision.rule_notes
        self.assertNotIn("overrides", note)
        self.assertIn("List evidence: archivist review (File:Events in 2020 June.png)", note)
        plain = _decide([], _review(list_graphic=None))  # a reference review without the list flag: no rule
        self.assertEqual((plain.role, plain.rule_notes), ("reference", []))

    def test_page_reference_needs_no_rule(self) -> None:
        """A list captioned as such is reference by its caption (tier revision 1.1); nothing is recorded. Without
        song titles a subject cannot be read and the caption decides alone."""
        decision = _decide([_caption("Cosmic Pulse X PUPA Comeback Chart List")])
        self.assertEqual((decision.role, decision.rule_notes), ("reference", []))
        self.assertFalse(any("chart-announcement rule" in entry for entry in decision.role_evidence))
        unread = _decide([_caption("Cosmic Pulse Chart List")], names=None)
        self.assertEqual((unread.role, unread.rule_notes), ("reference", []))


class ChartAnnouncementTests(unittest.TestCase):
    """D1 of the amended decision: an announcement graphic that displays chart difficulty levels is a reference for
    one song as for several; every song its subject names is mapped."""

    def test_subject_text(self) -> None:
        self.assertEqual(classify.subject_text("Xetta Chart List (Promoted)\nand Xetta Comeback Chart List"), "Xetta")
        self.assertEqual(classify.subject_text("Starbirds x Flarebirds Release Chart List"), "Starbirds x Flarebirds")
        self.assertEqual(
            classify.subject_text("Alter Ego X Hiccup Drunk Chart List Comeback"), "Alter Ego X Hiccup Drunk"
        )
        self.assertEqual(
            classify.subject_text("Pandemik X Black：Magnam Comeback ChartList"), "Pandemik X Black：Magnam"
        )
        self.assertEqual(classify.subject_text("Comeback tracks polling"), "")
        self.assertEqual(classify.subject_text("Upcoming Events"), "")

    def test_read_subject(self) -> None:
        def read(text: str) -> tuple[list[str], bool, int]:
            reading = classify.read_subject(text, NAMES)
            return [title for title, _whole in reading.songs], reading.complete, reading.names

        self.assertEqual(read("Angel x Angel"), (["Angel x Angel"], True, 1))  # one title with a separator in it
        self.assertEqual(read("Angel x Angel X The Path"), (["Angel x Angel", "The Path"], True, 2))
        self.assertEqual(read("Time & Space X 16383"), (["Time & Space (Dynamix Edit)", "16383"], True, 2))
        self.assertEqual(read("紅 (Kurenai) X Sakura Fubuki"), (["紅", "Sakura Fubuki"], True, 2))
        self.assertEqual(
            read("Now Is The Time, Do It X Arcana Idola"), (["Now Is The Time, Do It", "Arcana Idola"], True, 2)
        )
        self.assertEqual(
            read("Chrysoberyl, Cryonix, Marion.net and Soul Army"),
            (["Chrysoberyl", "Cryonix", "Marion.net", "Soul Army"], True, 4),
        )
        self.assertEqual(read("Amaterasu and Abyss"), ([], False, 2))
        whole = classify.SongNames.build([("Hope!!", []), ("HOPE", [])])
        self.assertIsNone(whole.resolve("Hope"))  # titles sharing a loose key are ambiguous

    def test_single_song_chart_list_is_a_reference(self) -> None:
        """The superseded single-song exception no longer applies: one song's chart list is a reference by its
        caption, mapped to its song; nothing overrides it, so no rule is recorded."""
        decision = _decide([_caption("Cosmic Pulse Chart List")])
        self.assertEqual(
            (decision.role, decision.disposition, decision.songs, decision.mapping),
            ("reference", "download", ["Cosmic Pulse"], "confirmed_wiki"),
        )
        self.assertEqual(decision.rule_notes, [])
        self.assertFalse(any("single-song" in entry for entry in decision.role_evidence))
        # A title containing a separator is one song; against an event category (a tie) the rule decides reference.
        angel = _decide(
            [_caption("Angel x Angel Comeback Chart List", "Angel x Angel")] + _category("Category:Event images")
        )
        self.assertEqual((angel.role, angel.songs, angel.mapping), ("reference", ["Angel x Angel"], "confirmed_wiki"))
        [note] = angel.rule_notes
        self.assertTrue(note.startswith("Kind reference by the list and chart-announcement rule"))
        self.assertIn(
            "Promotional page evidence it overrides: https://dynamix.miraheze.org/wiki/"
            "File:Events_in_2020_June.png (file category; Category:Event images)",
            note,
        )

    def test_file_title_names_the_subject_of_a_chart_list_category(self) -> None:
        title = "File:Cosmic Pulse Chart List.png"
        [category] = classify.usage_evidence(
            config.SOURCES["miraheze"],
            title,
            {"categories": [{"ns": 14, "title": "Category:Chart list images"}]},
            {},
            set(),
        )
        decision = _decide([category], title=title)
        self.assertEqual(
            (decision.role, decision.songs, decision.mapping), ("reference", ["Cosmic Pulse"], "inferred")
        )  # read from the file title only
        self.assertEqual(decision.mapping_evidence[-1]["field"], "file title")

    def test_several_songs_are_a_list_and_all_mapped(self) -> None:
        """A comeback list shown on one song's page: reference, the caption's other song joins the mapping."""
        decision = _decide([_caption("Cosmic Pulse X PUPA Comeback Chart List")])
        self.assertEqual(
            (decision.role, decision.songs, decision.mapping), ("reference", ["Cosmic Pulse", "PUPA"], "confirmed_wiki")
        )
        self.assertIn("names the song 'PUPA'", decision.mapping_evidence[-1]["quote"])
        loose = _decide([_caption("Time & Space X 16383 Comeback Chart List", "16383")])
        self.assertEqual((loose.songs, loose.mapping), (["16383", "Time & Space (Dynamix Edit)"], "inferred"))
        unknown = _decide([_caption("Amaterasu and Abyss Comeback Chart List", "A")])
        self.assertEqual((unknown.role, unknown.songs), ("reference", ["A"]))  # unresolved names: still a list

    def test_reviewed_half_of_a_pair_maps_the_song_it_shows(self) -> None:
        """The caption names the pair of a two-song comeback; the archivist saw one song (with level marks) in this
        half: a reference that maps only that song, and dropping the other rests on the review (inferred)."""
        half = _review(
            songs=["diSTractiOn PoInT"],
            evidence="visual_inspection: one song's comeback card with level marks; the caption names the pair",
        )
        decision = _decide(
            [_caption("diSTractiOn PoInT X Frontline B-Side Comeback Chart List 1", "diSTractiOn PoInT")], half
        )
        self.assertEqual(
            (decision.role, decision.songs, decision.mapping), ("reference", ["diSTractiOn PoInT"], "inferred")
        )
        self.assertEqual(decision.mapping_evidence[-1]["field"], "archivist review")
        self.assertTrue(decision.mapping_evidence[-1]["quote"].startswith("visual_inspection: one song's"))
        self.assertIn(
            "Not mapped although page evidence names it: 'Frontline B-Side'; the archivist review "
            f"({TITLE}) transcribes the songs the bytes show",
            " ".join(decision.rule_notes),
        )
        unreviewed = _decide(
            [_caption("diSTractiOn PoInT X Frontline B-Side Comeback Chart List 1", "diSTractiOn PoInT")]
        )
        self.assertEqual(unreviewed.songs, ["diSTractiOn PoInT", "Frontline B-Side"])
        self.assertFalse(any(note.startswith("Not mapped") for note in unreviewed.rule_notes))

    def test_transcription_limits_every_mapped_song(self) -> None:
        """A list review's songs are the whole mapping, whatever named the others: a host song page whose gallery
        shows the half, or the page of a song the caption does not name, maps nothing the review did not see."""
        caption = "diSTractiOn PoInT X Frontline B-Side Comeback Chart List 1"
        half = _review(songs=["diSTractiOn PoInT"], evidence="visual_inspection: one song's card with level marks")
        for pages in (["Frontline B-Side"], ["diSTractiOn PoInT", "Frontline B-Side"]):
            decision = _decide([_caption(caption, page) for page in pages], half)
            self.assertEqual(
                (decision.role, decision.songs, decision.mapping),
                ("reference", ["diSTractiOn PoInT"], "inferred"),
                pages,
            )
            self.assertEqual(decision.mapping_evidence[-1]["field"], "archivist review")
            self.assertFalse(
                any(entry["url"].endswith("/Frontline_B-Side") for entry in decision.mapping_evidence), pages
            )  # that page's placement is no mapping evidence
            self.assertIn("names it: 'Frontline B-Side'", " ".join(decision.rule_notes))
        lineup = _review(songs=["Cosmic Pulse", "PUPA"])
        decision = _decide([_caption("Cosmic Pulse X PUPA Comeback Chart List", "A")], lineup)
        self.assertEqual((decision.songs, decision.mapping), (["Cosmic Pulse", "PUPA"], "inferred"))
        self.assertIn("names it: 'A'", " ".join(decision.rule_notes))

    def test_reference_review_without_list_graphic_is_no_transcription(self) -> None:
        """Only a review flagged list_graphic transcribes a list: a plain reference review neither removes the songs
        page evidence maps nor adds its own."""
        plain = _review(list_graphic=None, songs=["PUPA"], evidence="visual_inspection: chart list card")
        self.assertFalse(plain.list_graphic)
        items = [_caption("Cosmic Pulse X 16383 Comeback Chart List", "Cosmic Pulse")]
        decision, unreviewed = _decide(items, plain), _decide(items)
        self.assertEqual(
            (decision.role, decision.songs, decision.mapping), ("reference", unreviewed.songs, unreviewed.mapping)
        )
        self.assertEqual(decision.songs, ["Cosmic Pulse", "16383"])
        self.assertFalse(any(note.startswith("Not mapped") for note in decision.rule_notes))

    def test_list_review_adds_the_songs_it_transcribed(self) -> None:
        """A list review names a song that no page evidence maps: it joins the mapping, which is then inferred."""
        lineup = _review(songs=["Cosmic Pulse", "PUPA"])
        decision = _decide([_caption("Upcoming Events", "Cosmic Pulse")], lineup)
        self.assertEqual(
            (decision.role, decision.songs, decision.mapping), ("reference", ["Cosmic Pulse", "PUPA"], "inferred")
        )
        self.assertEqual(decision.mapping_evidence[-1]["field"], "archivist review")

    def test_chart_level_review_overrides_promotional_captions(self) -> None:
        """A review that saw level marks (e.g. a Giga chart unlock card) makes a graphic that captions file as an
        event a reference, recording the overridden captions; the mapping rests on the review."""
        giga = _review(songs=["PUPA"], evidence="visual_inspection: Giga chart announcement 'PUPA', level diamond 14")
        decision = _decide([_caption("Unlock Giga Chart by follow", "PUPA"), _caption("November Event", "B")], giga)
        self.assertEqual((decision.role, decision.songs, decision.mapping), ("reference", ["PUPA"], "inferred"))
        self.assertIn("it overrides the promotional page evidence listed here", decision.role_evidence[0])

    def test_review_without_list_or_levels_keeps_it_promotional(self) -> None:
        """A review that saw neither a list nor level marks (a pack card with one titled jacket under a caption
        that calls it a comeback) keeps the graphic promotional: the rule is not applied."""
        card = _review(
            role="promotional",
            list_graphic=None,
            songs=["PUPA"],
            evidence="visual_inspection: pack card with one titled jacket and three '?' slots, no level marks",
        )
        decision = _decide([_caption("PUPA Comeback", "PUPA")] + _category("Category:Event images"), card)
        self.assertEqual((decision.role, decision.rule_notes), ("promotional", []))
        self.assertEqual(decision.songs, ["PUPA"])

    def test_host_page_alone_names_no_subject(self) -> None:
        """A lineup caption without a subject on one song's page stays a list: the host page only corroborates a
        subject that a caption or the file title gives (an unresolved one-name subject, e.g. a spelling variant)."""
        for caption in ("Upcoming Events", "Comeback Tracks", "New Charts", "New Charts: A & B"):
            decision = _decide([_caption(caption, "Cosmic Pulse")] + _category("Category:Event images"))
            self.assertEqual(decision.role, "reference", caption)
            self.assertTrue(
                any(
                    note.startswith("List subject not read") and "name no subject" in note
                    for note in decision.rule_notes
                ),
                caption,
            )
            # The host page's song is still mapped, by gallery placement alone: inferred, and the note says so.
            self.assertEqual((decision.songs, decision.mapping), (["Cosmic Pulse"], "inferred"), caption)
            note = next(note for note in decision.rule_notes if note.startswith("List subject not read"))
            self.assertIn(
                "the original-game song page showing it (Cosmic Pulse) maps its song by that gallery "
                "placement alone (mapping inferred), not because a caption or the file title names it",
                note,
            )
            self.assertNotIn("not taken as its subject", note)
        several = _decide([_caption("Comeback tracks polling", page) for page in ("Cosmic Pulse", "PUPA")])
        self.assertEqual((several.songs, several.mapping), (["Cosmic Pulse", "PUPA"], "inferred"))
        self.assertIn(
            "pages showing it (Cosmic Pulse, PUPA) map their songs by that gallery placement alone",
            " ".join(several.rule_notes),
        )
        # A transcription that leaves the host song out: the page is then not taken as the subject at all.
        other = _decide([_caption("Upcoming Events", "Cosmic Pulse")], _review(songs=["PUPA"]))
        self.assertEqual((other.songs, other.mapping), (["PUPA"], "inferred"))
        self.assertIn("page showing it (Cosmic Pulse) is not taken as its subject", " ".join(other.rule_notes))
        variant = _decide([_caption("Cosmic Pulse Remastered Chart List", "Cosmic Pulse")])
        self.assertEqual((variant.role, variant.songs), ("reference", ["Cosmic Pulse"]))

    def test_update_notes(self) -> None:
        decision = _decide([_caption("2.4 Version Update", "Cosmic Pulse")])
        self.assertEqual((decision.role, decision.rule_notes), ("reference", []))
        both = _decide([_caption("Cosmic Pulse Chart List"), _caption("2.4 Version Update", "B")])
        self.assertEqual((both.role, both.rule_notes), ("reference", []))


if __name__ == "__main__":
    unittest.main()
