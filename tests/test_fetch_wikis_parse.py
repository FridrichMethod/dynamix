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
from wikis import (  # noqa: E402
    acquire,
    classify,
    config,
    discover,
    jawikis,
    mediawiki,
    outputs,
    reviews,
    songindex,
    wikitext,
)


class WikitextTests(unittest.TestCase):
    def test_templates_nested_and_comments(self) -> None:
        text = "<!-- {{Song|Title=Hidden}} -->{{Song\n|Title = A {{color|#fff|B}}\n|Imgsuffix=A_B.png\n|x}}"
        [song] = wikitext.find_templates(text, {"Song"})
        self.assertEqual(song.params["Imgsuffix"], "A_B.png")
        self.assertEqual(song.params["1"], "x")
        self.assertEqual(wikitext.clean_markup(song.params["Title"]), "A B")

    def test_template_values_drop_comments(self) -> None:
        """DX2-02: MediaWiki removes a comment without inserting anything, so a comment inside a value joins its
        neighbours ('CosmicPulse'); delimiters inside a comment still separate nothing, and a value holding only a
        comment is empty."""
        text = (
            "{{Song|Title=Cosmic<!--x-->Pulse|Cover Artist=ari<!--?-->iol|Imgsuffix=Cos<!--c-->mic.png"
            "|Artist=<!-- none -->|Number=1<!-- |Update=hidden -->}}"
        )
        [song] = wikitext.find_templates(text, {"Song"})
        self.assertEqual(
            song.params,
            {"Title": "CosmicPulse", "Cover Artist": "ariiol", "Imgsuffix": "Cosmic.png", "Artist": "", "Number": "1"},
        )
        self.assertEqual(wikitext.credit_names(song.params["Cover Artist"]), ["ariiol"])
        page = {"title": "Cosmic Pulse", "revision": {"content": text, "revid": 1}, "categories": []}
        [record] = classify.song_records("wikis:miraheze", page, "https://dynamix.miraheze.org/wiki/Cosmic_Pulse")
        self.assertEqual(
            (record.title, record.illustrators, record.imgsuffix), ("CosmicPulse", ["ariiol"], "Cosmic.png")
        )
        [ref] = [ref for ref in wikitext.file_refs(text) if ref.context == "template"]
        self.assertEqual((ref.name, ref.param), ("Cosmic.png", "Imgsuffix"))
        self.assertEqual(wikitext.canonical_file_name("Cos<!--c-->mic.png"), "Cosmic.png")

    def test_self_closing_ref_with_a_slash_in_its_attributes(self) -> None:
        """DX2-03: a tag ends at its first '>' and is self-closing when '/' precedes it, whatever its attribute
        values hold: <ref name="a/b"/> removes itself only, never the text up to a later </ref>."""
        self.assertEqual(
            wikitext.credit_names('ariiol<ref name="a/b"/><br>Yunosuke<ref>cite</ref>'), ["ariiol", "Yunosuke"]
        )
        self.assertEqual(wikitext.clean_markup('A<ref name="x/y" /> B<ref>c</ref>'), "A B")
        self.assertEqual(
            wikitext.clean_markup('ariiol<ref name="a/b"/> & Yunosuke<ref>cite</ref>'), "ariiol & Yunosuke"
        )
        self.assertEqual(wikitext.clean_markup('A<ref name="a/b">cite / more</ref> B'), "A B")  # a paired ref
        self.assertEqual(wikitext.clean_markup("A<ref name=x/> B<ref name=y>c</ref> C"), "A B C")

    def test_private_use_characters_are_plain_text(self) -> None:
        """DX2-04: page text is untrusted; private-use characters that look like the internal nowiki placeholder are
        kept as text, never crash the parser or pull in another nowiki's content."""
        held = "\ue0005\ue001"
        self.assertEqual(wikitext.clean_markup(f"A {held} B"), f"A {held} B")
        self.assertEqual(wikitext.clean_markup("<nowiki>A</nowiki> \ue0000\ue001"), "A \ue0000\ue001")
        self.assertEqual(wikitext.clean_markup("<nowiki>\ue000|\ue001</nowiki>"), "\ue000|\ue001")
        self.assertEqual(wikitext.credit_names("x\ue0009\ue001"), ["x\ue0009\ue001"])
        self.assertEqual(wikitext.headings("== H\ue0003\ue001 ==\n"), [(0, 2, "H\ue0003\ue001")])
        page = {"title": "P", "revision": {"content": "{{Song|Title=T\ue0001\ue001}}", "revid": 1}, "categories": []}
        [record] = classify.song_records("wikis:miraheze", page, "https://dynamix.miraheze.org/wiki/P")
        self.assertEqual(record.title, "T\ue0001\ue001")

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

    def test_gallery_captions_are_not_link_options(self) -> None:
        """DX2-05: a gallery line knows only alt=, link=, class=, lang= and page=; every other part is caption text
        (the last one wins), including words that are options of a [[File:]] link."""
        text = (
            "<gallery>\nX.png|Left\nY.png|200px\nZ.png|alt=Alt text|link=Cosmic Pulse|thumb\n"
            "W.png|page=2|Cover art\n</gallery>"
        )
        self.assertEqual(
            [(ref.caption, ref.link) for ref in wikitext.file_refs(text)],
            [("Left", None), ("200px", None), ("thumb", "Cosmic Pulse"), ("Cover art", None)],
        )
        [link] = wikitext.file_refs("[[File:X.png|Left]] [[File:Y.png|200px|thumb|first|last]]")[:1]
        self.assertEqual(link.caption, "")  # in a link these are options
        self.assertEqual(wikitext.file_refs("[[File:Y.png|200px|thumb|first|last]]")[0].caption, "last")

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


class WikitextRuleTests(unittest.TestCase):
    """DX2-11: every parsing rule that classification relies on (game tabs, comments, parser functions, captions,
    credits, titles, sections) has a test that fails when the rule is removed."""

    def test_tabber_tabs_name_their_games(self) -> None:
        text = "<tabber>\nDynamix=\n[[File:A.png]]\n|-|\nUV=\n[[File:B.png]]\n</tabber>"
        regions = wikitext.regions(text)
        self.assertEqual([region.label for region in regions], ["Dynamix", "UV"])
        games = [
            wikitext.region_game(wikitext.regions_at(regions, ref.offset)[-1].label) for ref in wikitext.file_refs(text)
        ]
        self.assertEqual(games, ["dynamix", "dynamix_universe"])
        self.assertEqual(wikitext.region_game("Dynamix UV"), "dynamix_universe")  # the UV word wins over "Dynamix"
        self.assertEqual(wikitext.region_game("Dynamix Universe"), "dynamix_universe")
        self.assertIsNone(wikitext.region_game("Gallery"))

    def test_a_tab_ends_at_the_next_tab(self) -> None:
        """Tabs are siblings: an unclosed <div> inside one tab does not nest the next tab in it."""
        text = (
            '<div class="tabberex-tab" data-tab-header="Dynamix"><div>[[File:A.png]]'
            '<div class="tabberex-tab" data-tab-header="Dynamix Universe">[[File:B.png]]</div></div>'
        )
        first, second = wikitext.regions(text)
        self.assertEqual(first.end, second.start)
        [_, b] = wikitext.file_refs(text)
        self.assertEqual(wikitext.regions_at([first, second], b.offset), [second])

    def test_unclosed_comment_hides_the_rest(self) -> None:
        text = "{{Song|Title=A}}[[File:A.png]]<!-- {{Song|Title=B}} [[File:B.png]]"
        self.assertEqual([t.params["Title"] for t in wikitext.find_templates(text, {"Song"})], ["A"])
        self.assertEqual([ref.name for ref in wikitext.file_refs(text)], ["A.png"])
        self.assertEqual(wikitext.mask_comments("a<!--b\nc"), "a" + " " * 5 + "\n ")  # same length, newline kept

    def test_parser_functions_and_colon_magic_words_are_skipped(self) -> None:
        text = "{{#if:x|y}}{{lc:ABC}}{{DISPLAYTITLE:x}}{{Song|Title=A}}"
        self.assertEqual([t.name for t in wikitext.find_templates(text)], ["Song"])

    def test_keys_with_links_or_templates_are_positional(self) -> None:
        [song] = wikitext.find_templates("{{Song|[[A|b]]=c|{{x|y}}=z|Title=T}}", {"Song"})
        self.assertEqual(song.params, {"1": "[[A|b]]=c", "2": "{{x|y}}=z", "Title": "T"})

    def test_gallery_links_are_not_counted_twice(self) -> None:
        refs = wikitext.file_refs("<gallery>\nX.png|caption with [[File:Y.png]]\n</gallery>\n[[File:Z.png|z]]")
        self.assertEqual([(ref.name, ref.context) for ref in refs], [("X.png", "gallery"), ("Z.png", "link")])

    def test_last_caption_part_wins(self) -> None:
        self.assertEqual(wikitext.file_refs("<gallery>\nX.png|first|last\n</gallery>")[0].caption, "last")
        self.assertEqual(wikitext.file_refs("[[File:X.png|first|thumb|last]]")[0].caption, "last")

    def test_illustrator_notes_and_duplicate_credits(self) -> None:
        """docs/sources-wikis.md: an explicitly labeled illustrator note in a composer credit is removed (it is the
        illustrator's credit, stated separately); names are verbatim otherwise, each once."""
        for raw, name in (
            ("Linne X SCTL(Illust.Keiyuu&RED-FOX)", "Linne X SCTL"),
            ("Street (jacket : Sin:cK)", "Street"),
        ):
            self.assertEqual(wikitext.credit_names(raw, strip_illustrator_notes=True), [name])
            self.assertEqual(wikitext.credit_names(raw), [raw])
        self.assertEqual(wikitext.credit_names("A<br>B<br>A"), ["A", "B"])

    def test_page_titles_drop_the_fragment(self) -> None:
        self.assertEqual(wikitext.canonical_page_title("a_b#c"), "A b")
        self.assertEqual(wikitext.canonical_page_title("Artists#.5Bsound_shinobi.5D"), "Artists")

    def test_heading_levels_and_sections_since_a_tab(self) -> None:
        text = "==Outer==\n<div>\n===Inner===\n[[File:X.png]]\n"
        heads = wikitext.headings(text)
        self.assertEqual([(level, title) for _, level, title in heads], [(2, "Outer"), (3, "Inner")])
        offset = wikitext.file_refs(text)[0].offset
        self.assertEqual(wikitext.section_path(heads, offset), ["Outer", "Inner"])
        self.assertEqual(wikitext.section_path(heads, offset, since=text.index("<div>")), ["Inner"])


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
    original game itself, so the page or tab game (and the pre-Universe rule) still decide. Owner decision
    (2026-10-11, 09-frozen-contracts.md "Other games named in captions"): the name records another game only when it
    names a game on the reviewed list (config.OTHER_GAMES); any other name records none and asks for a curator."""

    THRESHOLD = "2023-08-18T00:00:00Z"  # the wiki's first Universe-named Fandom upload
    REVIEW = "which is not on the reviewed game list"  # the start of config.OTHER_GAME_REVIEW

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
        self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ("RAVON",)))
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
        self.assertEqual((item.game, item.other_games), ("dynamix_universe", ("RAVON",)))
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
        self.assertEqual((item.game, item.other_games), (None, ("Arcaea",)))
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
        self.assertEqual((item.game, item.other_games), (None, ("RAVON",)))
        decision = self.decide([item], fandom_timestamp="2021-08-22T00:00:00Z")
        self.assertEqual((decision.disposition, decision.game, decision.associations), ("download", "mixed", ["ravon"]))
        self.assertTrue(any(entry.startswith("pre-Universe upload") for entry in decision.game_evidence))
        self.assertTrue(any("another game/event" in entry for entry in decision.game_evidence))
        later = self.decide([item], fandom_timestamp="2024-01-01T00:00:00Z")  # no original-game basis at all
        self.assertEqual((later.disposition, later.reason.split(":")[0]), ("pending", "unresolved_game"))
        # A page-level gallery role of such a page is original-game evidence; the caption still adds the association.
        listed = found["File:B Chart List.png"]
        self.assertEqual((listed.role, listed.game, listed.other_games), ("reference", "dynamix", ("RAVON",)))
        self.assertEqual((self.decide([listed]).game, self.decide([listed]).associations), ("mixed", ["ravon"]))

    def test_dynamix_events_are_not_another_game(self) -> None:
        """DX1-04: artwork captioned for one of Dynamix's own events or anniversaries is original-game art: no
        association (which would also drop the default C4Cat rights holder), the page game stays. Since the owner
        decision of 2026-10-11 no event name is on the reviewed game list, so the evidence asks a curator to review
        the name instead of calling it a Dynamix event."""
        captions = {
            "Jacket for the Halloween Event": "the Halloween Event",
            "Artwork from the 2nd Anniversary": "the 2nd Anniversary",
            "Artwork for Christmas Event 2021": "Christmas Event 2021",
            "Illustration in Event 23 June to 27 June": "Event 23 June to 27 June",
            "Artwork for the New Year Festival": "the New Year Festival",
            "Artwork for the 9th Anniversary x Halloween Event 2023": "the 9th Anniversary x Halloween Event 2023",
            "Jacket for the Mid-Autumn Festival": "the Mid-Autumn Festival",
            "Artwork for the Collaboration Event": "the Collaboration Event",
        }
        for caption, occasion in captions.items():
            item = self.gallery_item(caption)
            self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()), caption)
            self.assertIn(f"the caption names {occasion}, {self.REVIEW}", item.detail)
            decision = self.decide([item])
            self.assertEqual((decision.disposition, decision.game, decision.associations), ("download", "dynamix", []))

    def test_events_of_another_game_are_that_games(self) -> None:
        """W1: an event or anniversary name that also names a listed game is that game's, a shared artwork with the
        same association as 'Artwork in <game>' (so the default C4Cat rights holder is not applied)."""
        captions = {
            "S Artwork for the RAVON Collaboration Event": "RAVON",
            "S Jacket in Arcaea Event": "Arcaea",
            "S Artwork for Lanota Anniversary": "Lanota",
            "S Artwork for the Dynamix X Rotaeno Collaboration Event": "Rotaeno",
            "S Artwork in RAVON": "RAVON",  # controls: a plain other-game caption, as before
            "S Artwork in Rotaeno": "Rotaeno",
        }
        for caption, other in captions.items():
            with self.subTest(caption=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", (other,)))
                self.assertNotIn(self.REVIEW, item.detail)
                self.assertIn("(another game/event", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations),
                    ("download", "mixed", [other.casefold()]),
                )

    def test_possessive_occasions_of_dynamix_record_no_game(self) -> None:
        """A possessive in an occasion name belongs to the occasion, never to a game: "the New Year's Event", "New
        Year's Eve" and "April Fool's Day" are Dynamix's own occasions like their non-possessive controls (no
        association, the page game stays), with a typographic apostrophe or a plural possessive as well."""
        captions = (
            "S Jacket for the New Year's Event",
            "S Jacket for the New Year Event",  # control
            "S Jacket for the New Year’s Event",
            "S Artwork for New Year's Eve",
            "S Artwork for New Year Eve",  # control
            "S Artwork for April Fool's Day",
            "S Artwork for April Fools Day",  # control
            "S Artwork for April Fools' Day",
        )
        for caption in captions:
            with self.subTest(caption=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertIn(self.REVIEW, item.detail)  # owner decision 2026-10-11: no list entry, curator review
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations), ("download", "dynamix", [])
                )

    def test_possessive_occasions_of_another_game_name_that_game(self) -> None:
        """An occasion of another game named with a possessive is that game's, as its non-possessive control:
        "Arcaea's 3rd Anniversary" gives the association 'arcaea', never 'arcaea_s', and the ordinals, numbers and
        connecting words beside the name name nothing."""
        captions = {
            "S Artwork for Arcaea's 3rd Anniversary": "Arcaea",
            "S Artwork for Arcaea 3rd Anniversary": "Arcaea",  # control
            "S Artwork for Arcaea’s 10th Anniversary": "Arcaea",
            "S Artwork for the 10th Anniversary of Arcaea": "Arcaea",
            "S Jacket for RAVON's Collaboration Event 2024": "RAVON",
            "S Artwork in RAVON's": "RAVON",  # the possessive ending a listed name
        }
        for caption, other in captions.items():
            with self.subTest(caption=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", (other,)))
                self.assertIn("(another game/event", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations),
                    ("download", "mixed", [other.casefold()]),
                )

    def test_listed_games_are_read_with_their_possessives(self) -> None:
        """classify.listed_games on the name alone (it replaces the occasion heuristic): a listed game named with a
        possessive, in any letter case, is that game under its canonical name; the occasions of Dynamix name none.
        Changed by the owner decision of 2026-10-11: an unlisted game ("Assassin's Creed") names no game any more,
        and a name in capitals gives the list's canonical name ("Arcaea"), not the caption's spelling ("ARCAEA")."""
        cases = {
            "the New Year's Event": (),
            "New Year’s Eve": (),
            "April Fool's Day": (),
            "April Fools' Day": (),
            "Dynamix's 10th Anniversary": (),
            "the 3rd Anniversary – 2024": (),
            "Arcaea's 3rd Anniversary": ("Arcaea",),
            "ARCAEA'S 3RD ANNIVERSARY": ("Arcaea",),
            "Lanota’s 2nd Anniversary Event!": ("Lanota",),
            "the RAVON Collaboration Event": ("RAVON",),
            "RAVON's": ("RAVON",),
            "RAVON": ("RAVON",),
            "Assassin's Creed": (),
        }
        for named, expected in cases.items():
            with self.subTest(named=named):
                self.assertEqual(tuple(game.name for game in classify.listed_games(named)), expected)

    def test_unlisted_names_record_no_game_and_ask_for_review(self) -> None:
        """Owner decision 2026-10-11: a name that is not on the reviewed game list -- an unlisted game, an event or
        holiday, a name in another script -- records no association (so the default rights holder stays), the page
        game stays, and the evidence names the caption's name and asks a curator to review it. The occasion
        heuristic read each of these as another game (an association 'assassin_s_creed', 'tokyo_game_show_2019',
        'let', or a 'mixed' game without any association for a name without Latin letters)."""
        names = (
            "Assassin's Creed",
            "Tokyo Game Show 2019",
            "Let's Party Event",
            "the Dynamix Universe Event",
            "春节活动",
            "新年's Event",
            "Arcaea Lanota",  # control: two listed names side by side are two games
        )
        for named in names[:-1]:
            with self.subTest(named=named):
                item = self.gallery_item(f"S Artwork for {named}")
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations), ("download", "dynamix", [])
                )
                self.assertIn(f"the caption names {named}, {self.REVIEW}", item.detail)
                self.assertIn("(curator review)", item.detail)
                self.assertEqual((item.game, item.other_games), ("dynamix", ()))
                self.assertTrue(any(self.REVIEW in entry for entry in decision.game_evidence))
        control = self.decide([self.gallery_item(f"S Artwork for {names[-1]}")])
        self.assertEqual((control.game, control.associations), ("mixed", ["arcaea", "lanota"]))

    def test_names_that_start_with_dynamix_go_through_the_reviewed_list(self) -> None:
        """A name that starts with "Dynamix" is read like any other name (before, it skipped the reviewed list and the
        curator note): a listed partner of a collaboration records its association ("Dynamix x RAVON Collaboration"),
        and an unlisted partner records none and asks a curator to review it. Dynamix's own occasions keep their
        classification (no association, the page game stays) and, like every unlisted name, carry the curator note.
        The original game's own name alone ("Artwork in Dynamix") names no other game and needs no review. A
        possessive is read the same with a straight or a typographic apostrophe ("Dynamix's", "Dynamix’s")."""
        listed = {
            "S Artwork for Dynamix x RAVON Collaboration": "RAVON",
            "S Artwork for Dynamix X Rotaeno Collaboration": "Rotaeno",
            "S Jacket for Dynamix×Lanota Event": "Lanota",
            "S Artwork for Dynamix's Arcaea Collaboration Event": "Arcaea",
            "S Artwork for Dynamix’s Arcaea Collaboration Event": "Arcaea",  # a typographic apostrophe
        }
        for caption, other in listed.items():
            with self.subTest(caption=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", (other,)))
                self.assertIn(f"(another game/event: {other})", item.detail)
                self.assertNotIn(self.REVIEW, item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations),
                    ("download", "mixed", [other.casefold()]),
                )
        for named in (
            "Dynamix x Foo Collaboration",
            "Dynamix's 5th Anniversary",
            "Dynamix’s 5th Anniversary",
            "Dynamix Halloween Event 2020",
        ):
            with self.subTest(named=named):
                item = self.gallery_item(f"S Artwork for {named}")
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertIn(f"the caption names {named}, {self.REVIEW}", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations), ("download", "dynamix", [])
                )
        for caption in (
            "S Artwork in Dynamix",
            "S Artwork for Dynamix's",
            "S Artwork for Dynamix’s",
            "S Artwork for Dynamix’s, by HAT",
            "S Artwork for Dynamix'",
            "S Jacket from Dynamix (2016)",
        ):
            with self.subTest(caption=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertNotIn("the caption names", item.detail)
                self.assertNotIn("another game/event", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations), ("download", "dynamix", [])
                )

    def test_the_game_name_alone_ends_at_sentence_punctuation_or_a_trailing_dash(self) -> None:
        """The original game's own name ends, as at the end of the caption or a comma, at trailing sentence
        punctuation ("Artwork in Dynamix.", "Dynamix!") and at a trailing dash (after a space, with nothing after it
        but the end of the name: "Artwork in Dynamix -"): it names no other game and needs no review. Owner decision
        2026-10-11: any further text after the name is a name like any other, a dash-separated qualifier included
        ("Dynamix - original", "Dynamix - Halloween Event", "Dynamix - 5th Anniversary"), so an unlisted one records no
        association and carries the curator note, while a listed game in it is recorded (round 4 read a qualifier
        after a dash as the name alone and left out the note). A name that only goes on after the punctuation, a
        hyphenated or dash-joined name ("Dynamix-exclusive", "Dynamix–Lanota", "Dynamix- Event") and an occasion
        before the punctuation are read as names as before."""
        for caption in (
            "S Artwork in Dynamix.",
            "S Artwork in Dynamix!",
            "S Artwork in Dynamix?!",
            "S Artwork in Dynamix...",
            "S Artwork in Dynamix…",
            "S Artwork for Dynamix's.",
            "S Artwork in Dynamix! (2016)",
            "S Artwork in Dynamix -",
        ):
            with self.subTest(caption=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertNotIn("the caption names", item.detail)
                self.assertNotIn("another game/event", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations), ("download", "dynamix", [])
                )
        for caption, other in (
            ("S Artwork for Dynamix - Lanota Collaboration", "Lanota"),
            ("S Artwork for Dynamix–Lanota Event", "Lanota"),
            ("S Artwork for Dynamix x RAVON Collaboration!", "RAVON"),
            ("S Artwork for Dynamix! Lanota Collaboration", "Lanota"),
        ):
            with self.subTest(listed=caption):
                item = self.gallery_item(caption)
                self.assertEqual(item.other_games, (other,))
                self.assertNotIn(self.REVIEW, item.detail)
                self.assertEqual(self.decide([item]).associations, [other.casefold()])
        for caption, named in (
            ("S Artwork in Dynamix - original", "Dynamix - original"),
            ("S Artwork in Dynamix – 2016 version", "Dynamix – 2016 version"),
            ("S Jacket from Dynamix — HQ", "Dynamix — HQ"),
            ("S Artwork in Dynamix’s -- uncropped", "Dynamix’s -- uncropped"),
            ("S Artwork in Dynamix - Halloween Event", "Dynamix - Halloween Event"),
            ("S Artwork for Dynamix - 5th Anniversary", "Dynamix - 5th Anniversary"),
            ("S Artwork for Dynamix! - Halloween Event", "Dynamix! - Halloween Event"),
            ("S Artwork for Dynamix-exclusive Event", "Dynamix-exclusive Event"),
            ("S Artwork for Dynamix- Halloween Event", "Dynamix- Halloween Event"),
            ("S Artwork for Dynamix. Halloween Event 2020", "Dynamix. Halloween Event 2020"),
            ("S Artwork for Dynamix's 5th Anniversary!", "Dynamix's 5th Anniversary!"),
            ("S Artwork for Dynamix Halloween Event - 2020", "Dynamix Halloween Event - 2020"),
        ):
            with self.subTest(named=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertIn(f"the caption names {named}, {self.REVIEW}", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations), ("download", "dynamix", [])
                )

    def test_the_boundaries_of_the_bare_game_name(self) -> None:
        """The edges of the bare-name rule (config.OTHER_GAME_ARTWORK) that the round-4 re-verification found
        unpinned: every dash of the class ends the bare name as a trailing dash, also a run of dashes, and also before
        a comma, a semicolon or a parenthesis after a space. A dash right after the name, text right after a dash, a
        second dash after a space and a name that only contains "Dynamix" later are names with the curator note."""
        bare = [f"S Artwork in Dynamix {dash}" for dash in "-‐‑‒–—―"]
        bare += [
            "S Artwork in Dynamix --",
            "S Artwork in Dynamix ——",
            "S Artwork in Dynamix! -",  # sentence punctuation, then the trailing dash
            "S Jacket from Dynamix - (2016)",
            "S Artwork in Dynamix -, by HAT",
            "S Artwork for Dynamix’s —; HQ",
        ]
        for caption in bare:
            with self.subTest(bare=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertNotIn("the caption names", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations), ("download", "dynamix", [])
                )
        for caption, named in (
            ("S Artwork in Dynamix-", "Dynamix-"),
            ("S Artwork in Dynamix—", "Dynamix—"),
            ("S Artwork in Dynamix -original", "Dynamix -original"),
            ("S Artwork in Dynamix - -", "Dynamix - -"),  # one run of dashes is the trailing dash
            ("S Artwork for the Dynamix -", "the Dynamix -"),
            ("S Artwork for the Dynamix", "the Dynamix"),
        ):
            with self.subTest(named=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertIn(f"the caption names {named}, {self.REVIEW}", item.detail)

    def test_the_boundaries_of_the_dynamix_prefix_before_a_release_event(self) -> None:
        """The edges of config.DYNAMIX_NAME, which keeps a name that starts with "Dynamix" from being a BMS release
        event: it is the whole word in any letter case at the start of the caption's name as written, so a name in
        straight quotes (stripped only for the evidence) and a longer word ("Dynamixer") stay release events, as
        before round 3, while "DYNAMIX PABAT 2017" is the song's art with the curator note."""
        for caption, named in (
            ("S Artwork for DYNAMIX PABAT 2017", "DYNAMIX PABAT 2017"),
            ("S Artwork for dynamix BOF 2013 Event", "dynamix BOF 2013 Event"),
        ):
            with self.subTest(song_art=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertIn(f"the caption names {named}, {self.REVIEW}", item.detail)
                self.assertEqual(self.decide([item]).disposition, "download")
        for caption in (
            'S Artwork for "Dynamix PABAT 2017"',
            "S Artwork for 'Dynamix PABAT 2017'",
            "S Artwork for Dynamixer PABAT 2017",
        ):
            with self.subTest(release=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.other_games), ("external_release_art", ()))
                self.assertIn("(an external release event)", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.reason.split(":")[0], decision.associations),
                    ("excluded", "external_release_art", []),
                )

    def test_names_that_start_with_dynamix_are_never_a_release_event(self) -> None:
        """A name that starts with "Dynamix" is not tested against the BMS release events (EXTERNAL_RELEASE_EVENT:
        PABAT, BOF, BMS), as before such names went through the reviewed list (R3): the caption keeps its art role and
        the page game instead of becoming an excluded external release. It still goes through the reviewed list: an
        unlisted name carries the curator note, a listed partner records its association. The same event names
        without the Dynamix prefix, also when Dynamix comes later in the name, stay external releases."""
        for named in ("Dynamix PABAT 2017", "Dynamix's BOF 2013 Event", "Dynamix BMS Collaboration Event"):
            with self.subTest(named=named):
                item = self.gallery_item(f"S Artwork for {named}")
                self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ()))
                self.assertIn(f"the caption names {named}, {self.REVIEW}", item.detail)
                self.assertNotIn("external release event", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.game, decision.associations), ("download", "dynamix", [])
                )
        item = self.gallery_item("S Jacket for Dynamix x RAVON BMS Event")
        self.assertEqual((item.role, item.game, item.other_games), ("song_art", "dynamix", ("RAVON",)))
        self.assertNotIn(self.REVIEW, item.detail)
        decision = self.decide([item])
        self.assertEqual((decision.disposition, decision.game, decision.associations), ("download", "mixed", ["ravon"]))
        for caption in (
            "S Artwork in PABAT! 2017",
            "S Artwork for the BOF 2013 Event",
            "S Artwork for the Dynamix BMS",
        ):
            with self.subTest(control=caption):
                item = self.gallery_item(caption)
                self.assertEqual((item.role, item.other_games), ("external_release_art", ()))
                self.assertIn("(an external release event)", item.detail)
                decision = self.decide([item])
                self.assertEqual(
                    (decision.disposition, decision.reason.split(":")[0], decision.associations),
                    ("excluded", "external_release_art", []),
                )

    def test_listed_names_match_whole_words_longest_first(self) -> None:
        """A listed spelling matches only as whole words, the longest first ("Cytus II" before "Cytus"); a name that
        names several listed games records each; a plural possessive ("Phigros'") is read as its game."""
        captions = {
            "S Artwork for Cytus II's 5th Anniversary Event": ["cytus_ii"],
            "S Artwork in Cytus": ["cytus"],
            "S Artwork for Phigros' 2nd Anniversary": ["phigros"],
            "S Artwork for the Arcaea x Lanota Collaboration Event": ["arcaea", "lanota"],
            "S Artwork for the Lanota and Arcaea Festival": ["arcaea", "lanota"],
            "S Artwork for Arcaeana": [],
            "S Artwork for the RAVONs": [],
            "S Artwork for Cytus2": [],
        }
        for caption, associations in captions.items():
            with self.subTest(caption=caption):
                decision = self.decide([self.gallery_item(caption)])
                self.assertEqual(decision.associations, associations)
                self.assertEqual(decision.game, "mixed" if associations else "dynamix")
        item = self.gallery_item("S Artwork for the Lanota and Arcaea Festival")
        self.assertEqual(item.other_games, ("Lanota", "Arcaea"))  # in the caption's order
        self.assertIn("(another game/event: Lanota, Arcaea)", item.detail)

    def test_quoted_names_keep_their_quotes_in_the_evidence(self) -> None:
        """A name in typographic quotes ("‘Cytus’") is its game, and the evidence quotes the caption's name intact:
        the occasion heuristic read the closing quote as a plural possessive and left "‘Cytus" in the evidence."""
        item = self.gallery_item("S Artwork for ‘Cytus’")
        self.assertIn("artwork of the song for ‘Cytus’ (another game/event: Cytus)", item.detail)
        self.assertEqual(item.other_games, ("Cytus",))
        self.assertEqual(self.decide([item]).associations, ["cytus"])

    def test_the_reviewed_game_list_is_well_formed(self) -> None:
        """config.OTHER_GAMES: unique canonical names, association keys and spellings; each canonical name is one of
        its spellings; no entry is Dynamix itself or a BMS release event; RAVON, the one other game that committed
        captions name, is listed."""
        names = [game.name for game in config.OTHER_GAMES]
        slugs = [game.slug for game in config.OTHER_GAMES]
        spellings = [spelling.casefold() for game in config.OTHER_GAMES for spelling in game.spellings]
        for values in (names, slugs, spellings):
            self.assertEqual(len(values), len(set(values)), values)
        for game in config.OTHER_GAMES:
            with self.subTest(game=game.name):
                self.assertRegex(game.slug, r"^[a-z0-9_]+$")
                self.assertNotIn(game.slug, ("dynamix", "dynamix_universe", "unknown_other"))
                self.assertIn(game.name, game.spellings)
                for spelling in game.spellings:
                    self.assertNotRegex(spelling, r"(?i)^dynamix\b")
                    self.assertIsNone(config.EXTERNAL_RELEASE_EVENT.search(spelling))
                    self.assertEqual(classify.listed_games(spelling), (game,))
        self.assertIn("ravon", slugs)

    def test_each_reviewed_entry_says_what_it_is(self) -> None:
        """The comment above each OTHER_GAMES entry in scripts/wikis/config.py says what the entry is, in the same
        words for every entry: an owner-approved reviewed entry, since the owner decision of 2026-10-11 keeps all seven
        (RAVON, Arcaea, Lanota, Cytus, Cytus II, Phigros and Rotaeno), and the list header names the seven. RAVON's
        comment also names the committed captions that evidence it. No comment calls an entry a test entry, its
        membership an open owner question, or an entry one that awaits the owner's confirmation any more."""
        source = (ROOT / "scripts/wikis/config.py").read_text(encoding="utf-8")
        start = source.index("\nOTHER_GAMES = (\n")
        header = source[source.index("# Other games named in captions") : start]
        header = " ".join(line.lstrip("#").strip() for line in header.splitlines())
        block = source[start : source.index("\n)\n", start)].splitlines()[2:]
        comments: dict[str, str] = {}
        pending: list[str] = []
        for line in block:
            stripped = line.strip()
            if stripped.startswith("#"):
                pending.append(stripped.lstrip("#").strip())
            elif stripped.startswith("OtherGame("):
                comments[stripped.split('"')[1]] = " ".join(pending)
                pending = []
        approved = ("RAVON", "Arcaea", "Lanota", "Cytus", "Cytus II", "Phigros", "Rotaeno")
        self.assertEqual(sorted(comments), sorted(approved))
        self.assertEqual(sorted(comments), sorted(game.name for game in config.OTHER_GAMES))
        stale = (
            "test entry",
            "list's own tests",
            "earlier caption tests",
            "added with this list",
            "open owner question",
            "awaits",
            "owner's confirmation",
            "does not name",
        )
        for name, comment in comments.items():
            with self.subTest(game=name):
                self.assertTrue(comment.startswith("Owner-approved reviewed entry (2026-10-11)"), comment)
                for phrase in stale:
                    self.assertNotIn(phrase, comment.casefold())
        self.assertIn("committed captions", comments["RAVON"].casefold())
        self.assertIn("seven entries", header)
        self.assertIn(", ".join(approved[:-1]) + " and " + approved[-1], header)
        for phrase in stale:
            with self.subTest(header=phrase):
                self.assertNotIn(phrase, header.casefold())

    def gallery_item(self, caption: str):
        """The evidence of a gallery image captioned ``caption`` on an original-game song page."""
        text = f"{{{{Song\n|Title = S\n|Imgsuffix = S.png\n}}}}\n<gallery>\nS Event.png|{caption}\n</gallery>"
        return self.found("S", text, ["Category:Songs"], ["S.png", "S Event.png"])["File:S Event.png"]

    def test_the_documented_caption_examples_behave_as_documented(self) -> None:
        """docs/sources-wikis.md (and its translation) quote these captions in the game paragraph: a listed game's
        artwork or event records an association (with a possessive too, and in a name that starts with Dynamix),
        Dynamix's own occasions and other unlisted names do not and carry the curator note, the game's own name alone
        (also before trailing sentence punctuation) names nothing to review while any further text after it, a
        dash-separated qualifier included, carries the note, and a BMS release event is the song's separate release
        (external_release_art) unless the name starts with Dynamix, which keeps it song art with the note (round 4,
        B2). The paragraph names the reviewed game list (config.OTHER_GAMES, which replaced the
        word lists DYNAMIX_OCCASION and OCCASION_WORDS by the owner decision of 2026-10-11) and no longer the removed
        word lists. The stale 'another game or event' wording is gone, and so are the claim that a name starting with
        "Dynamix" is never another game, which has been false since such names go through the reviewed list, and the
        round-4 claim that a dash-separated qualifier after the name names nothing to review."""
        expected = {  # caption: (disposition, associations, whether the evidence asks a curator to review the name)
            "Artwork in RAVON": ("download", ["ravon"], False),
            "Artwork for the RAVON Collaboration Event": ("download", ["ravon"], False),
            "Artwork for Arcaea's 3rd Anniversary": ("download", ["arcaea"], False),
            "Jacket for the Halloween Event": ("download", [], True),
            "Artwork for New Year's Eve": ("download", [], True),
            "Artwork for Dynamix x RAVON Collaboration": ("download", ["ravon"], False),
            "Artwork for Dynamix's 5th Anniversary": ("download", [], True),
            "Artwork in Dynamix": ("download", [], False),
            "Jacket from Dynamix (2016)": ("download", [], False),
            "Artwork in Dynamix!": ("download", [], False),
            "Artwork in Dynamix -": ("download", [], False),
            "Artwork in Dynamix - original": ("download", [], True),
            "Artwork in Dynamix - Halloween Event": ("download", [], True),
            "Artwork in PABAT! 2017": ("excluded", [], False),
            "Artwork for Dynamix PABAT 2017": ("download", [], True),
        }
        for relative, stale in (
            ("docs/sources-wikis.md", "another game or event"),
            ("docs/sources-wikis.zh-CN.md", "其他游戏或活动"),
            ("docs/sources-wikis.md", 'a name that starts with "Dynamix" is never another game'),
            ("docs/sources-wikis.zh-CN.md", "以“Dynamix”开头的名称从不算作其他游戏"),
            ("docs/sources-wikis.md", "punctuation or a dash-separated qualifier ("),
            ("docs/sources-wikis.zh-CN.md", "句末标点或以破折号隔开的补充说明时"),
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            [paragraph] = [line for line in text.splitlines() if "`mixed`" in line and "`game_associations`" in line]
            with self.subTest(document=relative, stale=stale):
                self.assertNotIn(stale, paragraph)
                self.assertIn("`external_release_art`", paragraph)
                self.assertIn("`scripts/wikis/config.py`", paragraph)
                self.assertIn("`OTHER_GAMES`", paragraph)
                self.assertTrue(config.OTHER_GAMES)
                for name in ("DYNAMIX_OCCASION", "OCCASION_WORDS"):
                    self.assertNotIn(name, paragraph)
                    self.assertFalse(hasattr(config, name), name)
                for caption in expected:
                    self.assertIn(caption, paragraph)
                # The exception for a name that starts with Dynamix stands in the BMS sentence, after its example.
                self.assertGreater(
                    paragraph.index("Artwork for Dynamix PABAT 2017"), paragraph.index("Artwork in PABAT! 2017")
                )
        for caption, (disposition, associations, review) in expected.items():
            with self.subTest(caption=caption):
                item = self.gallery_item("S " + caption)
                decision = self.decide([item])
                self.assertEqual((decision.disposition, decision.associations), (disposition, associations))
                self.assertEqual(self.REVIEW in item.detail, review, item.detail)
                if disposition == "excluded":
                    self.assertEqual(decision.reason.split(":")[0], "external_release_art")
                else:
                    self.assertEqual(item.role, "song_art")


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

    def test_titles_must_be_canonical(self) -> None:
        """DX1-03: reviews, notes and withdrawals are looked up by the API's canonical title (spaces, first letter
        upper-case); a hand-edited spelling would parse but never apply, so it is refused."""
        base = {"format": reviews.FORMAT, "reviews": []}
        review = {
            "title": "File:A b.png",
            "sha1": ["a" * 40],
            "role": "promotional",
            "game": "dynamix",
            "evidence": "visual_inspection: event key art",
            "game_evidence": "visual_inspection: original logo",
        }
        note = {"title": "File:A b.png", "sha1": ["a" * 40], "note": "visual_inspection: same picture as File:B.png"}
        withdrawal = {
            "title": "File:A b.png",
            "sha256": ["b" * 64],
            "reason": "fanart",
            "evidence": "visual_inspection: x",
        }
        self.assertIn("File:A b.png", reviews.parse(dict(base, reviews=[review])))
        self.assertIn("File:A b.png", reviews.parse_notes(dict(base, notes=[note])))
        self.assertIn("File:A b.png", reviews.parse_withdrawals(dict(base, withdrawals=[withdrawal])))
        for title in ("File:Unused_Event.png", "File:unused Event.png", "File:Unused  Event.png", "File: Unused.png"):
            with self.assertRaises(ValueError, msg=title) as caught:
                reviews.parse(dict(base, reviews=[dict(review, title=title)]))
            self.assertIn("canonical", str(caught.exception))
            with self.assertRaises(ValueError, msg=title):
                reviews.parse_notes(dict(base, notes=[dict(note, title=title)]))
            with self.assertRaises(ValueError, msg=title):
                reviews.parse_withdrawals(dict(base, withdrawals=[dict(withdrawal, title=title)]))

    def test_first_letter_is_capitalized_as_mediawiki_does(self) -> None:
        """MediaWiki upper-cases the first letter one character for one character: ß (whose upper case is "SS")
        stays, and a lowercase digraph takes its titlecase form (ǆ -> ǅ, not Ǆ). Titles, file references and page
        links are canonicalized alike, so a review of such a file is accepted and matches its references."""
        cases = {
            "ß.png": "ß.png",
            "ǆungla.png": "ǅungla.png",
            "ǅungla.png": "ǅungla.png",
            "Ǆ.png": "Ǆ.png",
            "ŉ.png": "ŉ.png",
            "ﬁle.png": "ﬁle.png",
            "é b.png": "É b.png",
            "a_b.png": "A b.png",
            "日本.png": "日本.png",
            "1st.png": "1st.png",
        }
        for name, canonical in cases.items():
            with self.subTest(name=name):
                self.assertEqual(reviews.canonical_title("File:" + name), "File:" + canonical)
                self.assertEqual(wikitext.canonical_file_name(name), canonical)
                self.assertEqual(wikitext.canonical_page_title(name.removesuffix(".png")), canonical[:-4])
        review = {
            "title": "File:ß.png",
            "sha1": ["a" * 40],
            "role": "promotional",
            "game": "dynamix",
            "evidence": "visual_inspection: event key art",
            "game_evidence": "visual_inspection: original logo",
        }
        base = {"format": reviews.FORMAT, "reviews": []}
        for title in ("File:ß.png", "File:ǅ.png", "File:SS.png"):
            with self.subTest(title=title):
                self.assertIn(title, reviews.parse(dict(base, reviews=[dict(review, title=title)])))
        with self.assertRaises(ValueError) as caught:
            reviews.parse(dict(base, reviews=[dict(review, title="File:ǆ.png")]))
        self.assertIn("'File:ǅ.png'", str(caught.exception))

    def test_committed_review_titles_are_discovered_files(self) -> None:
        """Every committed review, note and withdrawal names a file that a committed wiki discovery snapshot lists,
        so none of them is silently inert."""
        import json

        discovered = set()
        for slug in ("miraheze", "fandom"):
            snapshot = json.loads((ROOT / f"data/discovery/{slug}.json").read_text(encoding="utf-8"))
            # upstream_key is the File: title with spaces as underscores (discover.upstream_key)
            discovered |= {candidate["upstream_key"].replace("_", " ") for candidate in snapshot["candidates"]}
        titles = set(reviews.load()) | set(reviews.load_notes()) | set(reviews.load_withdrawals())
        self.assertTrue(titles)
        self.assertEqual(sorted(titles - discovered), [])

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

    def test_flusher_writes_every_n_downloads_and_every_few_minutes(self) -> None:
        """DX6-10: outputs are rewritten periodically during a run (acquire module docstring), not only at the end."""
        now = [0.0]
        writes: list[float] = []
        flusher = acquire.Flusher(lambda: writes.append(now[0]), every=3, seconds=300.0, clock=lambda: now[0])
        for _ in range(7):
            flusher.tick()
        self.assertEqual(len(writes), 2)  # after the third and the sixth download
        now[0] = 301.0
        flusher.tick()  # the eighth: only two since the last write, but five minutes have passed
        self.assertEqual(writes, [0.0, 0.0, 301.0])

    def test_snapshot_errors_have_a_stable_order(self) -> None:
        """DX6-10: discovery errors are written in a stable order, so snapshot bytes do not depend on the order in
        which the lists failed."""
        errors = [
            {
                "phase": "discover",
                "url": "https://dynamix.miraheze.org/w/api.php?list=allimages",
                "message": "HTTP 503",
            },
            {
                "phase": "discover",
                "url": "https://dynamix.miraheze.org/w/api.php?generator=allpages",
                "message": "HTTP 503",
            },
            {"phase": "discover", "url": None, "message": "timeout"},
        ]
        snapshots = []
        for ordered in (errors, errors[::-1]):
            inventory = discover.Inventory(
                config.MIRAHEZE,
                "2026-10-10T12:00:00Z",
                "2026-10-10T12:00:01Z",
                continuation={name: {"continue": "x"} for name in discover.LISTS},
                errors=list(ordered),
            )
            snapshots.append(outputs.mediawiki_snapshot(inventory, [], pooled_with=["wikis:fandom"]))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual([error["url"] for error in snapshots[0]["errors"]], [None, errors[1]["url"], errors[0]["url"]])

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
