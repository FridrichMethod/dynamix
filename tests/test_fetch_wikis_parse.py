"""Unit tests of the wiki parsers and classifiers (no network): ``python -I tests/test_fetch_wikis_parse.py``."""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "wikis"))

from lib import net  # noqa: E402
from wikis import acquire, classify, config, discover, jawikis, mediawiki, reviews, songindex, wikitext  # noqa: E402


class WikitextTests(unittest.TestCase):
    def test_templates_nested_and_comments(self) -> None:
        text = "<!-- {{Song|Title=Hidden}} -->{{Song\n|Title = A {{color|#fff|B}}\n|Imgsuffix=A_B.png\n|x}}"
        [song] = wikitext.find_templates(text, {"Song"})
        self.assertEqual(song.params["Imgsuffix"], "A_B.png")
        self.assertEqual(song.params["1"], "x")
        self.assertEqual(wikitext.clean_markup(song.params["Title"]), "A B")

    def test_credit_names_unknown_and_refs(self) -> None:
        self.assertEqual(wikitext.credit_names("ariiol<ref>[https://x.com/a The post]</ref>"), ["ariiol"])
        self.assertEqual(wikitext.credit_names("[[Artists#雄之助|Yunosuke]]"), ["Yunosuke"])
        self.assertEqual(wikitext.credit_names("N/A"), [])
        self.assertEqual(wikitext.credit_names("A & B<br>C"), ["A & B", "C"])  # never split on '&'

    def test_nowiki_is_literal_text(self) -> None:
        text = (
            '<gallery>\nMassacrE RAVON.jpg|New Exclusive Song "MassacrE <nowiki>||</nowiki> 3rc4224M"\n'
            "Nyarlathotep Chart List.jpg|^/7(L<nowiki>|</nowiki>?[_(L+#<>+&<nowiki>|</nowiki>^(o) Chart List\n"
            "</gallery>\n[[File:Z.png|thumb|<nowiki>[[not a link]] ''x''</nowiki> &amp; more]]"
        )
        refs = wikitext.file_refs(text)
        self.assertEqual(
            [ref.caption for ref in refs],
            [
                'New Exclusive Song "MassacrE || 3rc4224M"',
                "^/7(L|?[_(L+#<>+&|^(o) Chart List",
                "[[not a link]] ''x'' & more",
            ],
        )
        self.assertEqual(wikitext.split_top_level("a|<nowiki>b|c</nowiki>|d"), ["a", "<nowiki>b|c</nowiki>", "d"])
        self.assertEqual(wikitext.clean_markup("a<nowiki/>b <nowiki>unclosed | x"), "ab unclosed | x")

    def test_exact_titles_keep_non_ascii_spaces(self) -> None:
        self.assertEqual(wikitext.clean_markup("MEGA　POP"), "MEGA　POP")
        self.assertEqual(wikitext.clean_markup("  A \t  B<br/>\n C  "), "A B\nC")
        [song] = wikitext.find_templates("{{Song|Title = MEGA　POP }}", {"Song"})
        self.assertEqual(wikitext.clean_markup(song.params["Title"]), "MEGA　POP")

    def test_canonical_file_names(self) -> None:
        self.assertEqual(wikitext.canonical_file_name("File:%28XD%29_Dynamix.png"), "(XD) Dynamix.png")
        self.assertEqual(wikitext.canonical_file_name("cosmicPulse.PNG"), "CosmicPulse.PNG")
        self.assertIsNone(wikitext.canonical_file_name("not a file"))

    def test_gallery_link_and_section(self) -> None:
        text = (
            "==Trivia==\n<gallery>\n(XD) D.jpg|D\nX.png|link=Cosmic Pulse|Cover art\n</gallery>\n"
            "[[File:Y.png|200px|thumb|Used Part]]"
        )
        refs = wikitext.file_refs(text)
        self.assertEqual(
            [(r.name, r.context, r.caption, r.link) for r in refs],
            [
                ("(XD) D.jpg", "gallery", "D", None),
                ("X.png", "gallery", "Cover art", "Cosmic Pulse"),
                ("Y.png", "link", "Used Part", None),
            ],
        )
        heads = wikitext.headings(text)
        self.assertEqual(wikitext.section_path(heads, refs[0].offset), ["Trivia"])

    def test_link_display_text_with_brackets(self) -> None:
        """[[Artists#.5Bsound_shinobi.5D|[sound shinobi]]] shows "[sound shinobi]" (a credited name in brackets)."""
        self.assertEqual(wikitext.credit_names("[[Artists#.5Bsound_shinobi.5D|[sound shinobi]]]"), ["[sound shinobi]"])
        self.assertEqual(wikitext.clean_markup("[[A|B]] and [[C]], [x] [[D|[E] F]]"), "B and C, [x] [E] F")

    def test_link_label_with_an_unbalanced_bracket(self) -> None:
        """MediaWiki ends a link label at the first ']]' (one more ']' joins a label holding '['), so a label may hold
        a lone '[' or ']' and '<>': the Knight of Tripletz page's caption renders as its label."""
        caption = "[[Nyarlathotep|^/7(L⎥?[_(L+#<>+&⎥^(o)]] X Knigtht of Tripletz (for DNX Edit) Comeback Chart List"
        self.assertEqual(
            wikitext.clean_markup(caption),
            "^/7(L⎥?[_(L+#<>+&⎥^(o) X Knigtht of Tripletz (for DNX Edit) Comeback Chart List",
        )
        self.assertEqual(wikitext.clean_markup("[[A|x]y]] z"), "x]y z")
        self.assertEqual(wikitext.clean_markup("[[A|a [b]]] c"), "a [b] c")
        self.assertEqual(wikitext.clean_markup("[[A|plain]]] c"), "plain] c")  # no '[' in the label: ']' stays text
        self.assertEqual(wikitext.clean_markup("[[A|see [[B|inner]] text]]"), "[[A|see inner text]]")  # no nesting
        self.assertEqual(wikitext.clean_markup("[[A|<nowiki>]]</nowiki>]]"), "]]")

    def test_loose_keys_and_upside_down_titles(self) -> None:
        # A spacing modifier letter standing in for "_" is punctuation; kana/CJK modifiers stay letters.
        self.assertEqual(
            wikitext.normalize_key("^/7(L∣?［ˍ(L+⌗‹›+&⎥^(o)"), wikitext.normalize_key("^/7(L⎥?[_(L+#<>+&⎥^(o)")
        )
        self.assertEqual(wikitext.normalize_key("々ーテスト"), "々ーテスト")
        self.assertEqual(wikitext.unturned("ǝɹǝɥdsᴉɯǝH"), "Hemisphere")
        self.assertIsNone(wikitext.unturned("Hemisphere"))
        self.assertIsNone(wikitext.unturned("∀I∃Y§§"))  # a stylized title, not turned text

    def test_tabs(self) -> None:
        text = (
            '<div class="tabberex-tab" data-tab-header="Dynamix">[[File:A.png]]</div>'
            '<div class="tabberex-tab" data-tab-header="Dynamix Universe">[[File:B.png]]</div>'
        )
        regions = wikitext.regions(text)
        self.assertEqual([wikitext.region_game(r.label) for r in regions], ["dynamix", "dynamix_universe"])


def _evidence(role, strength, game, page="P", context="gallery", song=None):
    return classify.Evidence(
        "wikis:miraheze", page, f"https://dynamix.miraheze.org/wiki/{page}", context, role, strength, game, song, None
    )


class DecideTests(unittest.TestCase):
    def decide(self, items, info=None, frames=None, include_animated=False):
        return classify.decide(
            "File:X.png",
            items,
            info or {"mime": "image/png", "width": 10, "height": 10},
            frames=frames,
            include_animated=include_animated,
            fandom_timestamp=None,
            threshold=None,
        )

    def test_filename_only_hq_is_not_enough(self) -> None:
        """A '-HQ' name gives at most a filename role and no game: the file stays pending."""
        decision = self.decide(classify.filename_evidence(config.MIRAHEZE, "File:Lonely BG-HQ.png"))
        self.assertEqual(decision.disposition, "pending")
        self.assertTrue(decision.reason.startswith("unresolved_game"))

    def test_caption_beats_filename(self) -> None:
        items = classify.filename_evidence(config.MIRAHEZE, "File:SomeDynamix.png") + [
            _evidence("gameplay_screenshot", 2, "dynamix")
        ]
        decision = self.decide(items)
        self.assertEqual((decision.role, decision.disposition), ("gameplay_screenshot", "download"))

    def test_universe_only_and_mixed(self) -> None:
        self.assertEqual(
            self.decide([_evidence("song_art", 3, "dynamix_universe")]).reason.split(":")[0], "universe_only"
        )
        mixed = self.decide([_evidence("song_art", 3, "dynamix_universe"), _evidence("song_art", 3, "dynamix", "Q")])
        self.assertEqual((mixed.game, mixed.disposition), ("mixed", "download"))

    def test_video_and_animation(self) -> None:
        video = self.decide([], info={"mime": "video/youtube"})
        self.assertEqual(video.reason.split(":")[0], "video_placeholder")
        gif = {"mime": "image/gif", "width": 25, "height": 25}
        items = [_evidence("character", 2, "dynamix")]
        self.assertEqual(self.decide(items, gif, frames=3).reason.split(":")[0], "animated_capture")
        self.assertEqual(self.decide(items, gif, frames=3, include_animated=True).disposition, "download")
        self.assertEqual(self.decide(items, gif, frames=1).disposition, "download")  # a still GIF is no capture
        self.assertEqual(self.decide(items, gif, frames=None).reason.split(":")[0], "animated_capture")

    def test_conflicting_roles_stay_pending(self) -> None:
        decision = self.decide([_evidence("character", 2, "dynamix", "A"), _evidence("part", 2, "dynamix", "B")])
        self.assertEqual(decision.disposition, "pending")
        self.assertIn("conflicting", decision.reason)

    def test_exclusion_roles(self) -> None:
        self.assertEqual(
            self.decide([_evidence("person_photo", 2, None, "Artists")]).reason.split(":")[0], "person_photo"
        )
        self.assertEqual(classify.filename_evidence(config.FANDOM, "File:Site-background-dark")[0].role, "site_ui")

    def test_tier_revision_informational_captions(self) -> None:
        """Tier revision 1.1: chart/difficulty lists, comeback/new-chart and update notes are references; key art,
        event, anniversary and collaboration visuals and teasers stay promotional."""
        reference = [
            "Cosmic Pulse Chart List",
            "Emulisy, You Spin Me Around, Go Escape Chart List (Comeback)",
            "春菊 & Perspicuus Aestus New Chart",
            "2.4 Version Update",
            "Crystal Clear Comeback",
            "New Settings Announcement",
            "TERA Difficulty Announcement in LAST Re;SØRT",
            "C4Cat Open Submission 2026 Old Results Announcement",
            "Dynamix Nov 2023 update log",
        ]
        promotional = [
            "Christmas Event 2021",
            "9th Anniversary x Halloween Event 2023",
            "Dynamix X Rotaeno Collaboration",
            "Dynamix X ???? Collaboration Decision",
            'New Exclusive Song "ReLyna"',
            "RAVON Collaboration Pack 1",
            "Share Unlock new song 名無しの宣教師",
        ]
        for caption in reference:
            self.assertEqual(classify._caption_role(caption), "reference", caption)
        for caption in promotional:
            self.assertEqual(classify._caption_role(caption), "promotional", caption)
        self.assertEqual(config.FILE_CATEGORY_ROLES["Category:Chart list images"], "reference")
        for name in (
            "File:Helios Comeback.png",
            "File:1st Comeback in Nov 2019.jpg",
            "File:PUPA Chart List.png",
            "File:2.5 Version Update.jpg",
        ):
            [item] = classify.filename_evidence(config.MIRAHEZE, name)
            self.assertEqual((item.role, item.strength, item.game), ("reference", 0, None), name)
        self.assertEqual(classify.filename_evidence(config.MIRAHEZE, "File:Comebacker.png"), [])

    def test_file_page_text_and_redirects(self) -> None:
        boiler = "== Summary ==\nImporting images from https://dynamixc4cat.fandom.com ([[phorge:T15787|T15787]])\n"
        self.assertEqual(classify.description_text(boiler), "")
        self.assertEqual(classify.description_text("== Licensing ==\n{{Self}}\n[[Category:Parts images]]"), "")
        self.assertEqual(
            classify.description_text("== Summary ==\nThis is capture from Dynamix Facebook.\nCopyright from C4cat."),
            "This is capture from Dynamix Facebook. Copyright from C4cat.",
        )
        self.assertEqual(
            classify.redirect_target("#REDIRECT [[File:To soar X オレンジ Comeback Chart List.jpg]]"),
            "File:To soar X オレンジ Comeback Chart List.jpg",
        )
        self.assertIsNone(classify.redirect_target("See [[File:A.png]]"))
        info = {"comment": "Managed to get a screenshot", "sha1": "a" * 40}
        description, notes = discover.file_page_notes(info, {"revision": {"content": "Managed to get a screenshot"}})
        self.assertEqual(
            (description, notes), ("Managed to get a screenshot", ["file page text: 'Managed to get a screenshot'"])
        )

    def test_alias_with_same_bytes_is_excluded(self) -> None:
        decision = classify.decide(
            "File:A x.jpg",
            [_evidence("reference", 0, None, context="filename")],
            {"mime": "image/jpeg"},
            frames=None,
            include_animated=False,
            fandom_timestamp=None,
            threshold=None,
            alias=("File:A X.jpg", True),
        )
        self.assertEqual((decision.disposition, decision.reason.split(":")[0]), ("excluded", "redirect_or_alias"))
        decision = classify.decide(
            "File:A x.jpg",
            [],
            {"mime": "image/jpeg"},
            frames=None,
            include_animated=False,
            fandom_timestamp=None,
            threshold=None,
            alias=("File:A X.jpg", False),
        )
        self.assertEqual(decision.disposition, "pending")  # different bytes: not silently an alias

    def test_bga_captions_keep_the_art_role(self) -> None:
        """BGA art is artwork of its kind (not an informational reference); numbered BGA scenes are captures."""
        cases = {
            'Character "PUPA" from the BGA': "character",
            "Background from the BGA": "background",
            'Background used in BGA of "CROSSING DELTA"': "background",
            "Character used in BGA": "character",
            "Scene 3": "gameplay_screenshot",
            '"Crossing Delta" logo used in POP SEQUENCE\'s BGA': "promotional",
            "The C4CAT logo when game starts": None,
        }
        for caption, role in cases.items():
            self.assertEqual(classify._caption_role(caption), role, caption)

    def test_external_release_event_artwork_is_excluded(self) -> None:
        """'Artwork in PABAT! 2017' is the song's BMS event release art; 'Artwork in RAVON' is shared artwork."""
        text = (
            "{{Song\n|Artist = Linne X SCTL\n|Imgsuffix = WHIRLWINDDynamix.png\n}}\n<gallery>\n"
            'Eyecatch hard.jpg|WHIRLWIND "Hard" Artwork in PABAT! 2017\n'
            "WHIRLWIND Squared.png|WHIRLWIND Artwork in RAVON\n</gallery>"
        )
        page = {"title": "WHIRLWIND", "revision": {"content": text, "revid": 1}, "categories": []}
        info = classify.analyze_page(config.MIRAHEZE, page)
        files = {
            name: {"mime": "image/png", "width": 10, "height": 10}
            for name in ("File:WHIRLWINDDynamix.png", "File:Eyecatch hard.jpg", "File:WHIRLWIND Squared.png")
        }
        found = dict(classify.page_evidence(config.MIRAHEZE, page, info, files, {}))
        pabat = self.decide([found["File:Eyecatch hard.jpg"]])
        self.assertEqual(
            (pabat.disposition, pabat.reason.split(":")[0], pabat.associations),
            ("excluded", "external_release_art", []),
        )
        ravon = self.decide([found["File:WHIRLWIND Squared.png"]])
        self.assertEqual((ravon.disposition, ravon.game, ravon.associations), ("download", "mixed", ["ravon"]))

    def test_embedding_song_page_maps_filename_only_song_art(self) -> None:
        """A cover named only by the filename convention, embedded by one original-game song page: inferred."""
        name = classify.filename_evidence(config.MIRAHEZE, "File:SetFreeDynamix.jpg")
        host = classify.Evidence(
            "wikis:miraheze",
            "Set Free",
            "https://dynamix.miraheze.org/wiki/Set_Free",
            "embedded",
            None,
            0,
            "dynamix",
            "Set Free -KIVΛ Edition-",
            "Set Free",
        )
        decision = self.decide(name + [host])
        self.assertEqual(
            (decision.role, decision.songs, decision.mapping), ("song_art", ["Set Free -KIVΛ Edition-"], "inferred")
        )
        self.assertEqual(decision.mapping_evidence[0]["field"], "embedded")
        other = classify.Evidence(
            "wikis:miraheze",
            "B",
            "https://dynamix.miraheze.org/wiki/B",
            "embedded",
            None,
            0,
            "dynamix",
            "Another Song",
            "B",
        )
        self.assertEqual(self.decide(name + [host, other]).mapping, "unmapped")  # two song pages: no mapping
        background = self.decide([_evidence("background", 2, "dynamix"), host])
        self.assertEqual((background.songs, background.mapping), ([], "unmapped"))  # only for song art

    def test_universe_categories(self) -> None:
        for category in ("Category:Dynamix UV", "Category:DynamixUV", "Category:Songs (Dynamix Universe)"):
            self.assertTrue(classify._universe_category(category), category)
        self.assertFalse(classify._universe_category("Category:Dynamix"))


class OtherGameCaptionTests(unittest.TestCase):
    """DX1-01: a caption "<song> Artwork in <other game>" adds a shared-game association; it never supplies the
    original game itself, so the page or tab game (and the pre-Universe rule) still decide."""

    THRESHOLD = "2023-08-18T00:00:00Z"  # the wiki's first Universe-named Fandom upload

    def found(self, title: str, text: str, categories: list[str], names: list[str]) -> dict:
        page = {"title": title, "revision": {"content": text, "revid": 1}, "categories": categories}
        info = classify.analyze_page(config.MIRAHEZE, page)
        files = {"File:" + name: {"mime": "image/png", "width": 10, "height": 10} for name in names}
        return dict(classify.page_evidence(config.MIRAHEZE, page, info, files, {}))

    def decide(self, items, fandom_timestamp=None):
        return classify.decide(
            "File:X.png",
            items,
            {"mime": "image/png", "width": 10, "height": 10},
            frames=None,
            include_animated=False,
            fandom_timestamp=fandom_timestamp,
            threshold=self.THRESHOLD,
        )

    def test_original_game_page_keeps_its_game_and_adds_the_association(self) -> None:
        text = "{{Song\n|Title = WHIRLWIND\n|Imgsuffix = WHIRLWINDDynamix.png\n}}\n<gallery>\n"
        text += "WHIRLWIND Squared.png|WHIRLWIND Artwork in RAVON\n</gallery>"
        found = self.found("WHIRLWIND", text, ["Category:Songs"], ["WHIRLWINDDynamix.png", "WHIRLWIND Squared.png"])
        item = found["File:WHIRLWIND Squared.png"]
        self.assertEqual((item.role, item.game, item.other_game), ("song_art", "dynamix", "RAVON"))
        decision = self.decide([item])
        self.assertEqual((decision.disposition, decision.game, decision.associations), ("download", "mixed", ["ravon"]))
        self.assertTrue(any("another game/event" in entry for entry in decision.game_evidence))

    def test_universe_only_page_stays_universe_only(self) -> None:
        """A Template:SongUV page (Category:Songs (Dynamix Universe)) captioning its square art 'Artwork in RAVON':
        Universe art shared with RAVON, not original-game art."""
        text = "{{SongUV\n|Title = UVSong\n|Imgsuffix = UVSong cover.png\n}}\n<gallery>\n"
        text += "UVSong RAVON.png|UVSong Artwork in RAVON\n</gallery>"
        found = self.found(
            "UVSong", text, ["Category:Songs (Dynamix Universe)"], ["UVSong cover.png", "UVSong RAVON.png"]
        )
        item = found["File:UVSong RAVON.png"]
        self.assertEqual((item.game, item.other_game), ("dynamix_universe", "RAVON"))
        decision = self.decide([item])
        self.assertEqual(
            (decision.disposition, decision.reason.split(":")[0], decision.game),
            ("excluded", "universe_only", "dynamix_universe"),
        )
        self.assertEqual(decision.associations, ["dynamix_universe", "ravon"])

    def test_page_without_game_evidence_stays_pending(self) -> None:
        """The same caption on a page that names no game ('Songs', a neutral list page): no original-game context."""
        text = "<gallery>\nSome Song Arcaea.png|Some song Artwork in Arcaea\n</gallery>"
        item = self.found("Songs", text, [], ["Some Song Arcaea.png"])["File:Some Song Arcaea.png"]
        self.assertEqual((item.game, item.other_game), (None, "Arcaea"))
        decision = self.decide([item])
        self.assertEqual((decision.disposition, decision.reason.split(":")[0]), ("pending", "unresolved_game"))
        self.assertEqual((decision.game, decision.associations), ("unknown", ["arcaea"]))

    def test_two_game_page_gallery_rests_on_the_pre_universe_rule(self) -> None:
        """The page-level gallery of a two-game song page (Black：Magnam's layout): the item gives no game, so a
        pre-Universe upload establishes the original game and the caption the shared association."""
        text = (
            '<div class="tabberex-tab" data-tab-header="Dynamix">{{Song\n|Title = B\n|Imgsuffix = B.png\n}}</div>'
            '<div class="tabberex-tab" data-tab-header="Dynamix Universe">{{SongUV\n|Title = B\n'
            "|Imgsuffix = B cover.png\n}}</div>\n<gallery>\nB Squared.png|B Artwork in RAVON\n"
            "B Chart List.png|B Chart List cover for RAVON\n</gallery>"
        )
        names = ["B.png", "B cover.png", "B Squared.png", "B Chart List.png"]
        found = self.found("B", text, ["Category:Songs"], names)
        item = found["File:B Squared.png"]
        self.assertEqual((item.game, item.other_game), (None, "RAVON"))
        decision = self.decide([item], fandom_timestamp="2021-08-22T00:00:00Z")
        self.assertEqual((decision.disposition, decision.game, decision.associations), ("download", "mixed", ["ravon"]))
        self.assertTrue(any(entry.startswith("pre-Universe upload") for entry in decision.game_evidence))
        self.assertTrue(any("another game/event" in entry for entry in decision.game_evidence))
        later = self.decide([item], fandom_timestamp="2024-01-01T00:00:00Z")  # no original-game basis at all
        self.assertEqual((later.disposition, later.reason.split(":")[0]), ("pending", "unresolved_game"))
        # A page-level gallery role of such a page is original-game evidence; the caption still adds the association.
        listed = found["File:B Chart List.png"]
        self.assertEqual((listed.role, listed.game, listed.other_game), ("reference", "dynamix", "RAVON"))
        self.assertEqual((self.decide([listed]).game, self.decide([listed]).associations), ("mixed", ["ravon"]))


def _review(**fields):
    entry = {
        "title": "File:X.png",
        "sha1": ["a" * 40],
        "role": "promotional",
        "game": "dynamix",
        "evidence": "visual_inspection: event key art",
        "game_evidence": "visual_inspection: original logo",
    }
    entry.update(fields)
    return reviews.parse({"format": reviews.FORMAT, "reviews": [entry]})["File:X.png"]


class ReviewTests(unittest.TestCase):
    def decide(self, items, review, info=None, stale=False):
        source = config.MIRAHEZE
        extra = classify.review_evidence(source, "File:X.png", review) if review else []
        return classify.decide(
            "File:X.png",
            items + extra,
            info or {"mime": "image/png"},
            frames=None,
            include_animated=False,
            fandom_timestamp=None,
            threshold=None,
            review=review,
            review_stale=stale,
        )

    def test_parse_rejects_malformed_reviews(self) -> None:
        bad = [
            dict(sha1=["xyz"]),
            dict(role="artwork"),
            dict(game="unknown"),
            dict(evidence="looks like art"),
            dict(game_evidence=""),
            dict(role="song_art", game=None),
            dict(role="not_dynamix", game="dynamix"),
            dict(game="mixed"),
            dict(title="X.png"),
            dict(colour="red"),
        ]
        for fields in bad:
            with self.assertRaises(ValueError, msg=fields):
                _review(**fields)
        with self.assertRaises(ValueError):
            reviews.parse({"format": "other", "reviews": []})
        self.assertEqual(_review(role=None, game=None, game_evidence="", pending="role not identifiable").role, None)

    def test_committed_reviews_parse(self) -> None:
        table = reviews.load()
        for review in table.values():
            self.assertTrue(review.evidence.split(":", 1)[0].isidentifier(), review.title)

    def test_committed_review_songs_are_song_index_titles(self) -> None:
        """Review songs are compared with list subjects by exact title and attached through the index: each one is an
        exact title of the committed song index proposal (a misspelling would silently drop a captioned song)."""
        import json

        index = json.loads((ROOT / "data/discovery/song-index.wikis.json").read_text(encoding="utf-8"))
        titles = {song["title"] for song in index["songs"]}
        unknown = [
            (review.title, song) for review in reviews.load().values() for song in review.songs if song not in titles
        ]
        self.assertEqual(unknown, [])

    def test_review_naming_an_unknown_song_is_refused(self) -> None:
        entry = songindex.SongEntry("cosmic pulse", "Cosmic Pulse", aliases=["Cosmic pulse"], in_original_game=True)
        index = songindex.SongIndex({"cosmic pulse": entry}, [])
        discover.check_review_songs({"File:X.png": _review(songs=["Cosmic Pulse"])}, index)
        for spelling in ("Cosmic pulse", "PUPA!"):  # an alias is not the exact title either
            with self.assertRaises(reviews.ReviewSongError) as caught:
                discover.check_review_songs({"File:X.png": _review(songs=["Cosmic Pulse", spelling])}, index)
            self.assertIn(f"File:X.png: {spelling!r}", str(caught.exception))
        discover.check_review_songs({"File:X.png": _review(songs=["PUPA!"])}, None)  # no index: nothing to check

    def test_find_pins_the_reviewed_revision(self) -> None:
        table = {"File:X.png": _review()}
        self.assertEqual(reviews.find(table, "File:X.png", "A" * 40), (table["File:X.png"], False))
        self.assertEqual(reviews.find(table, "File:X.png", "b" * 40), (None, True))
        self.assertEqual(reviews.find(table, "File:Y.png", "a" * 40), (None, False))

    def test_review_fills_role_game_and_inferred_song(self) -> None:
        decision = self.decide(
            classify.filename_evidence(config.MIRAHEZE, "File:X Chart List.png"), _review(songs=["Cosmic Pulse"])
        )
        self.assertEqual((decision.role, decision.game, decision.disposition), ("promotional", "dynamix", "download"))
        self.assertEqual((decision.songs, decision.mapping), (["Cosmic Pulse"], "inferred"))
        self.assertTrue(decision.role_evidence[0].startswith("promotional <- archivist review (File:X.png)"))
        self.assertTrue(decision.game_evidence[-1].startswith("archivist review (File:X.png): visual_inspection"))

    def test_page_evidence_wins_over_review(self) -> None:
        decision = self.decide([_evidence("character", 2, "dynamix")], _review(role="part"))
        self.assertEqual((decision.role, decision.game), ("character", "dynamix"))
        universe = self.decide([_evidence("song_art", 3, "dynamix_universe")], _review(role="song_art"))
        self.assertEqual(universe.reason.split(":")[0], "universe_only")
        # A review may break a tie between equally supported page roles, but only by choosing one of them.
        tie = [_evidence("character", 2, "dynamix", "A", song="S"), _evidence("part", 2, "dynamix", "B", song="S")]
        broken = self.decide(tie, _review(role="part"))
        self.assertEqual((broken.role, broken.songs, broken.mapping), ("part", ["S"], "inferred"))  # never confirmed
        self.assertIsNone(self.decide(tie, _review(role="background")).role)
        undecided = self.decide(tie, _review(role=None, game=None, game_evidence="", pending="item not identifiable"))
        self.assertIn("conflicting", undecided.reason)
        self.assertIn("item not identifiable", undecided.reason)

    def test_review_exclusions_and_pending(self) -> None:
        photo = self.decide([], _review(role="person_photo", game=None, game_evidence=""))
        self.assertEqual(
            (photo.disposition, photo.reason),
            ("excluded", "person_photo: archivist review (File:X.png): visual_inspection: event key art"),
        )
        other = self.decide(
            [], _review(role="not_dynamix", game="not_dynamix", game_evidence="visual_inspection: Minecraft screenshot")
        )
        self.assertEqual((other.disposition, other.game), ("excluded", "not_dynamix"))
        conflict = self.decide(
            [_evidence(None, 0, "dynamix", context="embedded")],
            _review(role="not_dynamix", game="not_dynamix", game_evidence="visual_inspection: x"),
        )
        self.assertEqual(conflict.disposition, "pending")
        no_game = self.decide([], _review(game=None, game_evidence="", pending="no original-game evidence"))
        self.assertEqual((no_game.disposition, no_game.role), ("pending", "promotional"))
        self.assertIn("no original-game evidence", no_game.reason)
        stale = self.decide([], None, stale=True)
        self.assertIn("earlier revision", stale.reason)

    def test_mixed_review_adds_its_associations_to_page_game(self) -> None:
        """File:MassacrE RAVON.jpg: its song page gives the original game; the review saw RAVON's announcer and logo
        (game 'mixed', associations ['ravon']). The shared-game identity is kept, and the game evidence says why."""
        page = _evidence("promotional", 2, "dynamix", "MassacrE", song="MassacrE || 3rc4224M")
        shared = _review(
            game="mixed",
            associations=["ravon"],
            game_evidence="visual_inspection: RAVON's announcer and logo on a collaboration post",
        )
        decision = self.decide([page], shared)
        self.assertEqual((decision.role, decision.game, decision.associations), ("promotional", "mixed", ["ravon"]))
        self.assertTrue(decision.game_evidence[-1].endswith("(shared-game identity added to the page evidence)"))
        plain = self.decide([page], _review())  # a review with game 'dynamix' adds nothing
        self.assertEqual((plain.game, plain.associations), ("dynamix", []))
        universe = self.decide([dataclasses.replace(page, game="dynamix_universe")], shared)
        self.assertEqual(universe.game, "dynamix_universe")  # never added to another game's page context

    def test_review_refines_an_exclusion_only(self) -> None:
        """Page evidence that excludes a file (wiki banner template, profile list) yields to the exclusion a review
        saw in the bytes; a review never turns such a file into a download."""
        banner = [_evidence("site_ui", 1, None, "Template:MainPageBanner", context="embedded in template")]
        universe = self.decide(
            banner,
            _review(
                role="universe_only", game="dynamix_universe", game_evidence="visual_inspection: DYNAMIX UNIVERSE logo"
            ),
        )
        self.assertEqual(
            (universe.disposition, universe.game, universe.reason.split(":")[0]),
            ("excluded", "dynamix_universe", "universe_only"),
        )
        listed = [_evidence("person_photo", 2, "dynamix", "Artists")]
        placeholder = self.decide(listed, _review(role="game_ui", game=None, game_evidence=""))
        self.assertEqual(placeholder.reason.split(":")[0], "game_ui")
        kept = self.decide(banner, _review(role="song_art"))
        self.assertEqual((kept.disposition, kept.reason.split(":")[0]), ("excluded", "site_ui"))

    def test_official_branding_overrides_wiki_interface_use(self) -> None:
        """The game's own wordmark that the wiki shows as its Main Page header: site_ui page evidence describes the
        wiki's use, not the file, so a review that identified official branding in the bytes decides (inferred)."""
        main_page = [
            _evidence("site_ui", 2, None, "Main Page", context="embedded"),
            _evidence("site_ui", 1, None, "Template:Main Page", context="embedded in template"),
        ]
        logo = _review(official_branding=True, evidence="visual_inspection: the original DynAMIX wordmark")
        decision = self.decide(main_page, logo)
        self.assertEqual(
            (decision.role, decision.game, decision.disposition, decision.mapping),
            ("promotional", "dynamix", "download", "unmapped"),
        )
        self.assertTrue(decision.role_evidence[0].startswith("promotional <- archivist review (File:X.png)"))
        self.assertTrue(any("site_ui" in note and "official branding" in note for note in decision.rule_notes))
        # Without the flag, or against any other page role, page evidence still wins.
        self.assertEqual(self.decide(main_page, _review()).reason.split(":")[0], "site_ui")
        listed = [_evidence("person_photo", 2, "dynamix", "Artists")]
        self.assertEqual(self.decide(listed, logo).reason.split(":")[0], "person_photo")
        for fields in (
            dict(official_branding="yes"),
            dict(official_branding=True, role="site_ui", game=None, game_evidence=""),
        ):
            with self.assertRaises(ValueError, msg=fields):
                _review(**fields)

    def test_whole_screen_overrides_a_game_feature_page(self) -> None:
        """The code-entry tutorial screenshots that the wiki's Code page links: game_ui page evidence describes the
        page's topic, so a review that saw a whole game screen in the bytes decides; without the flag page evidence
        wins, and the flag needs role gameplay_screenshot."""
        code_page = [_evidence("game_ui", 1, "dynamix", "Code", context="link")]
        screen = _review(
            role="gameplay_screenshot",
            whole_screen=True,
            evidence="visual_inspection: annotated in-game screenshot of the code entry screen",
        )
        decision = self.decide(code_page, screen)
        self.assertEqual(
            (decision.role, decision.game, decision.disposition, decision.mapping),
            ("gameplay_screenshot", "dynamix", "download", "unmapped"),
        )
        self.assertTrue(any("game_ui page evidence describes the page's topic" in note for note in decision.rule_notes))
        self.assertEqual(self.decide(code_page, _review(role="gameplay_screenshot")).reason.split(":")[0], "game_ui")
        self.assertEqual(
            self.decide([_evidence("site_ui", 2, None, "Main Page", context="embedded")], screen).reason.split(":")[0],
            "site_ui",
        )
        for fields in (dict(whole_screen="yes"), dict(whole_screen=True, role="promotional")):
            with self.assertRaises(ValueError, msg=fields):
                _review(**fields)

    def test_official_animation_and_download_probe(self) -> None:
        gif = {"mime": "image/gif", "width": 480, "height": 270}
        official = classify.decide(
            "File:X.png",
            [_evidence("character", 2, "dynamix")],
            gif,
            frames=2,
            include_animated=False,
            fandom_timestamp=None,
            threshold=None,
            review=_review(role="character", animated="official"),
        )
        self.assertEqual(official.disposition, "download")
        with self.assertRaises(ValueError):
            _review(animated="yes")
        probe = _review(
            role=None,
            game=None,
            game_evidence="",
            pending="the content could not be inspected",
            evidence="download_probe: HTTP 404 from the CDN",
        )
        decision = self.decide([], probe)
        self.assertEqual((decision.disposition, decision.reason.split(":")[0]), ("probe", "unresolved_role"))
        self.assertEqual(
            self.decide([_evidence("character", 0, None, context="filename")], probe).disposition, "pending"
        )  # a role hint exists: not a probe

    def test_notes_and_withdrawals_tables(self) -> None:
        base = {"format": reviews.FORMAT, "reviews": []}
        note = {"title": "File:A.png", "sha1": ["a" * 40], "note": "visual_inspection: same picture as File:B.png"}
        table = reviews.parse_notes(dict(base, notes=[note]))
        self.assertTrue(table["File:A.png"].applies_to("A" * 40))
        self.assertEqual(
            table["File:A.png"].text(), "archivist note (File:A.png): visual_inspection: same picture as File:B.png"
        )
        withdrawal = {
            "title": "File:A.png",
            "sha256": ["b" * 64],
            "reason": "external_release_art",
            "evidence": "visual_inspection: BMS event eyecatch",
        }
        entry = reviews.parse_withdrawals(dict(base, withdrawals=[withdrawal]))["File:A.png"]
        excluded = classify.Decision(None, "dynamix", [], [], [], [], "excluded", "external_release_art: x", "unmapped")
        self.assertTrue(entry.covers("b" * 64, excluded))
        self.assertFalse(entry.covers("c" * 64, excluded))  # other bytes than the reviewed ones
        self.assertFalse(entry.covers("b" * 64, dataclasses.replace(excluded, reason="fanart: x")))
        bad = [dict(note, note="no basis"), dict(note, sha1=["x"]), dict(note, extra=1)]
        for fields in bad:
            with self.assertRaises(ValueError, msg=fields):
                reviews.parse_notes(dict(base, notes=[fields]))
        for fields in (
            dict(withdrawal, reason="wrong"),
            dict(withdrawal, sha256=["b" * 40]),
            dict(withdrawal, evidence=""),
        ):
            with self.assertRaises(ValueError, msg=fields):
                reviews.parse_withdrawals(dict(base, withdrawals=[fields]))
        self.assertTrue(reviews.load_withdrawals())  # the committed table parses
        self.assertTrue(reviews.load_notes())


class MediaWikiEnumerationTests(unittest.TestCase):
    class Client:
        def __init__(self, responses):
            self.responses, self.calls = list(responses), []

        def get_json(self, url, params):
            self.calls.append(dict(params))
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response, url

    def test_follows_continue_verbatim(self) -> None:
        client = self.Client(
            [
                {
                    "query": {"allimages": [{"name": "A.png", "title": "File:A.png"}]},
                    "continue": {"aicontinue": "B.png", "continue": "-||"},
                },
                {"query": {"allimages": [{"name": "B.png", "title": "File:B.png"}]}},
            ]
        )
        result = mediawiki.allimages(mediawiki.MediaWikiAPI(client, "https://dynamix.miraheze.org/w/api.php"))
        self.assertTrue(result.complete)
        self.assertEqual(sorted(result.items), ["File:A.png", "File:B.png"])
        self.assertEqual((client.calls[1]["aicontinue"], client.calls[1]["continue"]), ("B.png", "-||"))

    def test_error_keeps_open_continuation(self) -> None:
        client = self.Client(
            [
                {
                    "query": {"allimages": [{"name": "A.png", "title": "File:A.png"}]},
                    "continue": {"aicontinue": "B.png", "continue": "-||"},
                },
                net.HttpError("HTTP 503", url="https://dynamix.miraheze.org/w/api.php", http_status=503),
            ]
        )
        result = mediawiki.allimages(mediawiki.MediaWikiAPI(client, "https://dynamix.miraheze.org/w/api.php"))
        self.assertFalse(result.complete)
        self.assertEqual(result.continuation, {"aicontinue": "B.png", "continue": "-||"})
        self.assertEqual(len(result.errors), 1)

    def test_repeated_continuation_stops(self) -> None:
        page = {"query": {"allimages": []}, "continue": {"aicontinue": "A.png", "continue": "-||"}}
        client = self.Client([page, page, page])
        result = mediawiki.allimages(mediawiki.MediaWikiAPI(client, "https://dynamix.miraheze.org/w/api.php"))
        self.assertFalse(result.complete)
        self.assertIn("repeated", result.errors[0]["message"])


class SectionRoleTests(unittest.TestCase):
    def test_wave_test_instruction_stills_are_references(self) -> None:
        """Wave Test Setting > Wave Test Instructions shows stills of C4Cat's explanation video (presenters, an inset
        tablet, subtitles): reference with the page's own statement quoted, not gameplay screenshots; the size
        heuristic still applies to other sections of the interface page."""
        page = {
            "title": "Wave Test Setting",
            "categories": [],
            "revision": {
                "revid": 1,
                "content": (
                    "==Wave Test Instructions==\nThe pictures are from C4Cat's Wave Test Cantonese introduction video.\n"
                    "[[File:Wave Test Step 1.jpg|thumb]]\n==Wave Test Medals==\n[[File:Medal Screen.jpg|thumb]]\n"
                ),
            },
        }
        files = {
            "File:Wave Test Step 1.jpg": {"mime": "image/jpeg", "width": 960, "height": 540},
            "File:Medal Screen.jpg": {"mime": "image/jpeg", "width": 960, "height": 540},
        }
        evidence = classify.collect(config.MIRAHEZE, files, {}, {page["title"]: page}).evidence
        [still] = [item for item in evidence["File:Wave Test Step 1.jpg"] if item.role]
        self.assertEqual((still.role, still.strength), ("reference", 2))
        self.assertIn("stills of an official explanation video", still.detail)
        [medal] = [item for item in evidence["File:Medal Screen.jpg"] if item.role]
        self.assertEqual(medal.role, "gameplay_screenshot")


class SmallHelperTests(unittest.TestCase):
    def test_display_title_drops_a_repeated_extension(self) -> None:
        """Fallback titles carry no file extension, also where an upload repeated it ("... March.jpg.jpg")."""
        for title, expected in (
            ("File:Coming Events in 2018 March.jpg.jpg", "Coming Events in 2018 March"),
            ("File:Cosmic Pulse Chart List.jpg", "Cosmic Pulse Chart List"),
            ("File:Ver.2.5 Update.PNG", "Ver.2.5 Update"),
            ("File:.jpg.png", ".jpg"),
            ("File:Hope!!", "Hope!!"),
        ):
            self.assertEqual(discover.display_title(title), expected, title)

    def test_fandom_download_url_requests_original(self) -> None:
        url = "https://static.wikia.nocookie.net/d/images/9/9b/A.png/revision/latest?cb=20210409081347"
        self.assertEqual(discover.download_url(config.FANDOM, url), url + "&format=original")
        self.assertEqual(
            discover.download_url(config.FANDOM, "https://static.wikia.nocookie.net/a.png"),
            "https://static.wikia.nocookie.net/a.png?format=original",
        )
        self.assertEqual(
            discover.download_url(config.MIRAHEZE, "https://static.wikitide.net/a.png"),
            "https://static.wikitide.net/a.png",
        )

    def test_same_revision(self) -> None:
        a = {"sha1": "a" * 40, "bytes": 5, "timestamp": "2021-01-01T00:00:00Z"}
        self.assertTrue(acquire.same_revision(a, dict(a)))
        self.assertFalse(acquire.same_revision(a, dict(a, sha1="b" * 40)))
        self.assertFalse(acquire.same_revision(a, None))
        self.assertTrue(acquire.same_content(a, dict(a, timestamp="2026-01-01T00:00:00Z")))
        self.assertFalse(acquire.same_content(a, dict(a, bytes=6)))
        self.assertFalse(acquire.same_content(dict(a, sha1=None), dict(a, sha1=None)))

    def test_history_note_survives_rewrites(self) -> None:
        notes = acquire._replace_history("File description page: 'x'.", ["first", "second"])
        self.assertEqual(notes, "File description page: 'x'. Upstream history: first | second.")
        self.assertEqual(acquire.history({"notes": notes}), ["first", "second"])
        self.assertEqual(acquire.history({"notes": acquire._replace_history(notes, ["third"])}), ["third"])
        self.assertEqual(acquire.history({}), [])

    def test_japanese_rows_upside_down_and_swapped(self) -> None:
        url = config.ATWIKI.list_pages[2][1]
        entries = {
            title: songindex.SongEntry(key=title, title=title, titles=[title], composers=[composer])
            for title, composer in (("Hemisphere", "ARForest"), ("The Dystopia's Tomorrow", "Moonset"))
        }
        rows = [
            jawikis.SongRow("ǝɹǝɥdsᴉɯǝH", "ARForest", "list", url),
            jawikis.SongRow("Moonset", "The Dystopia's Tomorrow", "list", url),
        ]
        disagreements = []
        songindex._match_japanese(entries, {"rhythmgame-ja": [jawikis.ListPage("list", url, rows)]}, disagreements)
        self.assertEqual(sorted(entries), ["Hemisphere", "The Dystopia's Tomorrow"])  # no new song entries
        self.assertEqual(entries["Hemisphere"].aliases, ["ǝɹǝɥdsᴉɯǝH"])
        self.assertEqual(entries["The Dystopia's Tomorrow"].aliases, [])  # "Moonset" is the composer, not a title
        self.assertEqual({key for key, _field, _values in disagreements}, {"Hemisphere", "The Dystopia's Tomorrow"})

    def test_japanese_tables(self) -> None:
        html = (
            '<div id="wikibody"><table><tr><th colspan="2">Title</th><th>Composer</th></tr>'
            "<tr><td>Xetta ( *4 )</td><td>x</td><td>Somebody</td></tr>"
            "<tr><td>春菊（*1）</td><td>x</td><td>SUWAKI</td></tr></table></div>"
        )
        rows = jawikis.parse_tables(html, "list", "https://w.atwiki.jp/dynamix2ch/pages/37.html")
        self.assertEqual([(r.title, r.credit) for r in rows], [("Xetta", "Somebody"), ("春菊", "SUWAKI")])

    def test_edit_suffix_base_key(self) -> None:
        self.assertEqual(
            songindex.base_key("Elph's Sadness -DNX Edit-"), songindex.base_key("Elph’s Sadness -Dynamix Edition-")
        )
        self.assertEqual(
            songindex.base_key("Magic of love (Dynamix Ver)"), songindex.base_key("Magic of love (Dynamix Edit)")
        )
        self.assertEqual(songindex.base_key("Ace Sniper (DNX edit)"), "acesniper")
        self.assertEqual(songindex.base_key("Impulse (Neutral Moon VIP Edit)"), "impulse")
        self.assertEqual(songindex.base_key("A (Remix)"), "aremix")  # not an edit/version label

    def test_japanese_rows_prefer_exact_titles(self) -> None:
        """'HOPE' and 'Hope!!' share the loose key 'hope': an exact title decides, nothing is merged."""
        url = config.ATWIKI.list_pages[0][1]
        entries = {title: songindex.SongEntry(key=title, title=title, titles=[title]) for title in ("HOPE", "Hope!!")}
        rows = [jawikis.SongRow("Hope!!", "Somebody", "list", url), jawikis.SongRow("HOPE!", "Other", "list", url)]
        disagreements = []
        songindex._match_japanese(entries, {"atwiki-ja": [jawikis.ListPage("list", url, rows)]}, disagreements)
        self.assertEqual(entries["HOPE"].aliases, [])
        self.assertTrue(entries["Hope!!"].in_original_game)
        self.assertFalse(entries["HOPE"].in_original_game)  # "HOPE!" is ambiguous: matched to neither song
        self.assertNotIn("ja:hope", entries)
        self.assertTrue(any("ambiguous" in str(values) for _key, _field, values in disagreements))

    def test_category_collections_skip_universe(self) -> None:
        self.assertEqual(
            songindex.category_collections(
                [
                    "Category:Event songs",
                    "Category:Gold Submission Track",
                    "Category:RAVON Songs",
                    "Category:Paid Songs (Dynamix Universe)",
                    "Category:Songs by MMRY",
                ]
            ),
            ["Event", "Open Submission (Gold)", "Collaboration: RAVON"],
        )


def _song_page(title: str, imgsuffix: str, gallery: str = "", extra: str = "") -> dict:
    text = f"{{{{Song\n|Artist = Composer of {title}\n|Imgsuffix = {imgsuffix}\n}}}}\n"
    if gallery:
        text += f"==Gallery==\n<gallery>\n{gallery}\n</gallery>\n"
    return {
        "title": title,
        "revision": {"content": text + extra, "revid": 1},
        "categories": [{"title": "Category:Songs"}],
    }


def _pooled(pages: list[dict], names: list[str]) -> dict[str, list]:
    files = {"File:" + name: {"mime": "image/png", "width": 10, "height": 10} for name in names}
    wiki = classify.collect(config.MIRAHEZE, files, {}, {page["title"]: page for page in pages})
    return wiki.evidence


class SongMappingTests(unittest.TestCase):
    """Review round 2: which songs a file is mapped to, and how strongly."""

    def decide(self, items, review=None):
        extra = classify.review_evidence(config.MIRAHEZE, "File:X.png", review) if review else []
        return classify.decide(
            "File:X.png",
            items + extra,
            {"mime": "image/png"},
            frames=None,
            include_animated=False,
            fandom_timestamp=None,
            threshold=None,
            review=review,
        )

    def test_cover_caption_naming_another_song(self) -> None:
        """Each of two song pages shows the other song's cover ('Cover Art of "<other song>"'): every cover maps only
        to the song whose Imgsuffix names it; a character named like a song keeps the host page's song."""
        elph = _song_page(
            "Elph’s Sadness -Dynamix Edition-",
            "ElphsSadness.jpeg",
            'File:Gallium_Phosphide.png|Cover Art of "[[Gallium Phosphide]]"',
        )
        gallium = _song_page(
            "Gallium Phosphide",
            "Gallium_Phosphide.png",
            'File:ElphsSadness.jpeg|Cover Art of "[[Elph’s Sadness -Dynamix Edition-]]"',
        )
        nova = _song_page("Nova Highway", "NovaHighwayDynamix.png", 'Nova-Raw.png|Used Character "Nova"')
        evidence = _pooled(
            [elph, gallium, nova, _song_page("NOVA", "NOVADynamix.png")],
            ["ElphsSadness.jpeg", "Gallium Phosphide.png", "Nova-Raw.png", "NovaHighwayDynamix.png", "NOVADynamix.png"],
        )
        for name, song in (
            ("Gallium Phosphide.png", "Gallium Phosphide"),
            ("ElphsSadness.jpeg", "Elph’s Sadness -Dynamix Edition-"),
        ):
            decision = self.decide(evidence["File:" + name])
            self.assertEqual((decision.role, decision.songs, decision.mapping), ("song_art", [song], "confirmed_wiki"))
            self.assertTrue(
                all(song in str(item) or "Imgsuffix" in item["field"] for item in decision.mapping_evidence),
                decision.mapping_evidence,
            )
        character = self.decide(evidence["File:Nova-Raw.png"])
        self.assertEqual((character.role, character.songs), ("character", ["Nova Highway"]))

    def test_cover_shown_only_by_another_songs_caption(self) -> None:
        """No Imgsuffix names the file: a 'Cover Art of "<song>"' caption in another song's gallery names the cover's
        song; the host page's song is not used (review round 2)."""
        host = _song_page("Host Song", "HostDynamix.png", 'Guest Cover.png|Cover Art of "Gallium Phosphide"')
        evidence = _pooled(
            [host, _song_page("Gallium Phosphide", "Gallium_Phosphide.png")],
            ["Guest Cover.png", "HostDynamix.png", "Gallium Phosphide.png"],
        )
        [caption] = [item for item in evidence["File:Guest Cover.png"] if item.context == "gallery"]
        self.assertEqual(
            (caption.role, caption.song, caption.song_page), ("song_art", "Gallium Phosphide", "Gallium Phosphide")
        )
        decision = self.decide(evidence["File:Guest Cover.png"])
        self.assertEqual((decision.songs, decision.mapping), (["Gallium Phosphide"], "confirmed_wiki"))
        self.assertIn(
            "the caption names the cover of another song (Gallium Phosphide)", decision.mapping_evidence[0]["quote"]
        )

    def test_single_conflicting_link_target(self) -> None:
        """FUNKTIME!!!Dynamix.png: two list pages link it to FUNK TIME!!!, one (a copy-paste error) to ISERA."""

        def link(page, song):
            return classify.Evidence(
                "wikis:miraheze",
                page,
                f"https://dynamix.miraheze.org/wiki/{page}",
                "link",
                "song_art",
                1,
                "dynamix",
                song,
                song,
                f"link={song}",
            )

        items = [link("2024 Event", "FUNK TIME!!!"), link("Event", "FUNK TIME!!!"), link("Store/Dynamix", "ISERA")]
        decision = self.decide(items)
        self.assertEqual((decision.songs, decision.mapping), (["FUNK TIME!!!"], "inferred"))
        self.assertNotIn("ISERA", str(decision.mapping_evidence))
        tie = self.decide([link("2024 Event", "FUNK TIME!!!"), link("Store/Dynamix", "ISERA")])
        self.assertEqual((tie.songs, tie.mapping), (["FUNK TIME!!!", "ISERA"], "conflict"))
        template = classify.Evidence(
            "wikis:miraheze",
            "A",
            "https://dynamix.miraheze.org/wiki/A",
            "Template:Song|Imgsuffix",
            "song_art",
            3,
            "dynamix",
            "A",
            "A",
            "Imgsuffix = X.png",
        )
        shared = self.decide([template, dataclasses.replace(template, page="B", song="B")])
        self.assertEqual((shared.songs, shared.mapping), (["A", "B"], "confirmed_wiki"))  # a shared cover
        weaker = self.decide([template, link("List", "B")])
        self.assertEqual(weaker.songs, ["A"])  # the strongest evidence decides

    def test_review_songs_only_when_the_review_decides(self) -> None:
        caption = _evidence("song_art", 2, "dynamix", "Song A", song="Song A")
        named_b = _review(role="song_art", songs=["Song B"], evidence="visual_inspection: jacket shows 'Song B'")
        decision = self.decide([caption], named_b)
        self.assertEqual(
            (decision.songs, decision.mapping, [e["field"] for e in decision.mapping_evidence]),
            (["Song A"], "confirmed_wiki", ["gallery"]),
        )
        alone = self.decide(classify.filename_evidence(config.MIRAHEZE, "File:XDynamix.png"), named_b)
        self.assertEqual(
            (alone.songs, alone.mapping, alone.mapping_evidence[-1]["field"]),
            (["Song B"], "inferred", "archivist review"),
        )
        tie = [_evidence("character", 2, "dynamix", "A", song="S"), _evidence("part", 2, "dynamix", "B", song="S")]
        broken = self.decide(tie, _review(role="part", songs=["T"]))
        self.assertEqual((broken.role, broken.songs, broken.mapping), ("part", ["S", "T"], "inferred"))
        self.assertIn("archivist review", [e["field"] for e in broken.mapping_evidence])

    def test_gallery_placement_maps_a_role_less_caption(self) -> None:
        """'Used "Justice Justice" Background Parts' gives no role; the Parts page does. The file sits in one
        original-game song's gallery: inferred mapping quoting the gallery caption. Two songs' galleries: none."""
        justice = _song_page(
            "Justice Justice",
            "JusticeJusticeDynamix.png",
            'JusticeJusticeBG.png|Used "Justice Justice" Background Parts',
        )
        parts = {
            "title": "Parts",
            "revision": {
                "content": "==Background==\n<gallery>\nJusticeJusticeBG.png|\nShared BG.png|\n</gallery>",
                "revid": 2,
            },
            "categories": [],
        }
        other = _song_page("Other Song", "OtherDynamix.png", "Shared BG.png|Used Background Parts")
        evidence = _pooled(
            [
                justice,
                parts,
                other,
                dict(
                    justice,
                    title="Justice Justice copy",
                    revision={
                        "content": justice["revision"]["content"].replace("JusticeJusticeBG.png", "Shared BG.png"),
                        "revid": 3,
                    },
                ),
            ],
            ["JusticeJusticeBG.png", "JusticeJusticeDynamix.png", "OtherDynamix.png", "Shared BG.png"],
        )
        decision = self.decide(evidence["File:JusticeJusticeBG.png"])
        self.assertEqual(
            (decision.role, decision.songs, decision.mapping), ("background", ["Justice Justice"], "inferred")
        )
        self.assertIn('Used "Justice Justice" Background Parts', decision.mapping_evidence[0]["quote"])
        shared = self.decide(evidence["File:Shared BG.png"])
        self.assertEqual((shared.role, shared.songs, shared.mapping), ("background", [], "unmapped"))


if __name__ == "__main__":
    unittest.main()
