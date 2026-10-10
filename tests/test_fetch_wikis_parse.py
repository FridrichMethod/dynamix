"""Unit tests of the wiki parsers and classifiers (no network): ``python -I tests/test_fetch_wikis_parse.py``."""

from __future__ import annotations

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

    def test_canonical_file_names(self) -> None:
        self.assertEqual(wikitext.canonical_file_name("File:%28XD%29_Dynamix.png"), "(XD) Dynamix.png")
        self.assertEqual(wikitext.canonical_file_name("cosmicPulse.PNG"), "CosmicPulse.PNG")
        self.assertIsNone(wikitext.canonical_file_name("not a file"))

    def test_gallery_link_and_section(self) -> None:
        text = "==Trivia==\n<gallery>\n(XD) D.jpg|D\nX.png|link=Cosmic Pulse|Cover art\n</gallery>\n" \
               "[[File:Y.png|200px|thumb|Used Part]]"
        refs = wikitext.file_refs(text)
        self.assertEqual([(r.name, r.context, r.caption, r.link) for r in refs], [
            ("(XD) D.jpg", "gallery", "D", None), ("X.png", "gallery", "Cover art", "Cosmic Pulse"),
            ("Y.png", "link", "Used Part", None)])
        heads = wikitext.headings(text)
        self.assertEqual(wikitext.section_path(heads, refs[0].offset), ["Trivia"])

    def test_tabs(self) -> None:
        text = ('<div class="tabberex-tab" data-tab-header="Dynamix">[[File:A.png]]</div>'
                '<div class="tabberex-tab" data-tab-header="Dynamix Universe">[[File:B.png]]</div>')
        regions = wikitext.regions(text)
        self.assertEqual([wikitext.region_game(r.label) for r in regions], ["dynamix", "dynamix_universe"])


def _evidence(role, strength, game, page="P", context="gallery", song=None):
    return classify.Evidence("wikis:miraheze", page, f"https://dynamix.miraheze.org/wiki/{page}", context, role,
                             strength, game, song, None)


class DecideTests(unittest.TestCase):
    def decide(self, items, info=None, frames=None, include_animated=False):
        return classify.decide("File:X.png", items, info or {"mime": "image/png", "width": 10, "height": 10},
                               frames=frames, include_animated=include_animated, fandom_timestamp=None,
                               threshold=None)

    def test_filename_only_hq_is_not_enough(self) -> None:
        """A '-HQ' name gives at most a filename role and no game: the file stays pending."""
        decision = self.decide(classify.filename_evidence(config.MIRAHEZE, "File:Lonely BG-HQ.png"))
        self.assertEqual(decision.disposition, "pending")
        self.assertTrue(decision.reason.startswith("unresolved_game"))

    def test_caption_beats_filename(self) -> None:
        items = classify.filename_evidence(config.MIRAHEZE, "File:SomeDynamix.png") + [
            _evidence("gameplay_screenshot", 2, "dynamix")]
        decision = self.decide(items)
        self.assertEqual((decision.role, decision.disposition), ("gameplay_screenshot", "download"))

    def test_universe_only_and_mixed(self) -> None:
        self.assertEqual(self.decide([_evidence("song_art", 3, "dynamix_universe")]).reason.split(":")[0],
                         "universe_only")
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
        self.assertEqual(self.decide([_evidence("person_photo", 2, None, "Artists")]).reason.split(":")[0],
                         "person_photo")
        self.assertEqual(classify.filename_evidence(config.FANDOM, "File:Site-background-dark")[0].role, "site_ui")

    def test_tier_revision_informational_captions(self) -> None:
        """Tier revision 1.1: chart/difficulty lists, comeback/new-chart and update notes are references; key art,
        event, anniversary and collaboration visuals and teasers stay promotional."""
        reference = ["Cosmic Pulse Chart List", "Emulisy, You Spin Me Around, Go Escape Chart List (Comeback)",
                     "春菊 & Perspicuus Aestus New Chart", "2.4 Version Update", "Crystal Clear Comeback",
                     "New Settings Announcement", "TERA Difficulty Announcement in LAST Re;SØRT",
                     "C4Cat Open Submission 2026 Old Results Announcement", "Dynamix Nov 2023 update log"]
        promotional = ["Christmas Event 2021", "9th Anniversary x Halloween Event 2023", "Dynamix X Rotaeno Collaboration",
                       "Dynamix X ???? Collaboration Decision", "New Exclusive Song \"ReLyna\"",
                       "RAVON Collaboration Pack 1", "Share Unlock new song 名無しの宣教師"]
        for caption in reference:
            self.assertEqual(classify._caption_role(caption), "reference", caption)
        for caption in promotional:
            self.assertEqual(classify._caption_role(caption), "promotional", caption)
        self.assertEqual(config.FILE_CATEGORY_ROLES["Category:Chart list images"], "reference")
        for name in ("File:Helios Comeback.png", "File:1st Comeback in Nov 2019.jpg", "File:PUPA Chart List.png",
                     "File:2.5 Version Update.jpg"):
            [item] = classify.filename_evidence(config.MIRAHEZE, name)
            self.assertEqual((item.role, item.strength, item.game), ("reference", 0, None), name)
        self.assertEqual(classify.filename_evidence(config.MIRAHEZE, "File:Comebacker.png"), [])

    def test_file_page_text_and_redirects(self) -> None:
        boiler = ("== Summary ==\nImporting images from https://dynamixc4cat.fandom.com ([[phorge:T15787|T15787]])\n")
        self.assertEqual(classify.description_text(boiler), "")
        self.assertEqual(classify.description_text("== Licensing ==\n{{Self}}\n[[Category:Parts images]]"), "")
        self.assertEqual(classify.description_text("== Summary ==\nThis is capture from Dynamix Facebook.\n"
                                                   "Copyright from C4cat."),
                         "This is capture from Dynamix Facebook. Copyright from C4cat.")
        self.assertEqual(classify.redirect_target("#REDIRECT [[File:To soar X オレンジ Comeback Chart List.jpg]]"),
                         "File:To soar X オレンジ Comeback Chart List.jpg")
        self.assertIsNone(classify.redirect_target("See [[File:A.png]]"))
        info = {"comment": "Managed to get a screenshot", "sha1": "a" * 40}
        description, notes = discover.file_page_notes(info, {"revision": {"content": "Managed to get a screenshot"}})
        self.assertEqual((description, notes), ("Managed to get a screenshot",
                                                ["file page text: 'Managed to get a screenshot'"]))

    def test_alias_with_same_bytes_is_excluded(self) -> None:
        decision = classify.decide("File:A x.jpg", [_evidence("reference", 0, None, context="filename")],
                                   {"mime": "image/jpeg"}, frames=None, include_animated=False,
                                   fandom_timestamp=None, threshold=None, alias=("File:A X.jpg", True))
        self.assertEqual((decision.disposition, decision.reason.split(":")[0]), ("excluded", "redirect_or_alias"))
        decision = classify.decide("File:A x.jpg", [], {"mime": "image/jpeg"}, frames=None, include_animated=False,
                                   fandom_timestamp=None, threshold=None, alias=("File:A X.jpg", False))
        self.assertEqual(decision.disposition, "pending")  # different bytes: not silently an alias

    def test_universe_categories(self) -> None:
        for category in ("Category:Dynamix UV", "Category:DynamixUV", "Category:Songs (Dynamix Universe)"):
            self.assertTrue(classify._universe_category(category), category)
        self.assertFalse(classify._universe_category("Category:Dynamix"))


def _review(**fields):
    entry = {"title": "File:X.png", "sha1": ["a" * 40], "role": "promotional", "game": "dynamix",
             "evidence": "visual_inspection: event key art", "game_evidence": "visual_inspection: original logo"}
    entry.update(fields)
    return reviews.parse({"format": reviews.FORMAT, "reviews": [entry]})["File:X.png"]


class ReviewTests(unittest.TestCase):
    def decide(self, items, review, info=None, stale=False):
        source = config.MIRAHEZE
        extra = classify.review_evidence(source, "File:X.png", review) if review else []
        return classify.decide("File:X.png", items + extra, info or {"mime": "image/png"}, frames=None,
                               include_animated=False, fandom_timestamp=None, threshold=None, review=review,
                               review_stale=stale)

    def test_parse_rejects_malformed_reviews(self) -> None:
        bad = [dict(sha1=["xyz"]), dict(role="artwork"), dict(game="unknown"), dict(evidence="looks like art"),
               dict(game_evidence=""), dict(role="song_art", game=None), dict(role="not_dynamix", game="dynamix"),
               dict(game="mixed"), dict(title="X.png"), dict(colour="red")]
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

    def test_find_pins_the_reviewed_revision(self) -> None:
        table = {"File:X.png": _review()}
        self.assertEqual(reviews.find(table, "File:X.png", "A" * 40), (table["File:X.png"], False))
        self.assertEqual(reviews.find(table, "File:X.png", "b" * 40), (None, True))
        self.assertEqual(reviews.find(table, "File:Y.png", "a" * 40), (None, False))

    def test_review_fills_role_game_and_inferred_song(self) -> None:
        decision = self.decide(classify.filename_evidence(config.MIRAHEZE, "File:X Chart List.png"),
                               _review(songs=["Cosmic Pulse"]))
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
        tie = [_evidence("character", 2, "dynamix", "A"), _evidence("part", 2, "dynamix", "B")]
        self.assertEqual(self.decide(tie, _review(role="part")).role, "part")
        self.assertIsNone(self.decide(tie, _review(role="background")).role)
        undecided = self.decide(tie, _review(role=None, game=None, game_evidence="", pending="item not identifiable"))
        self.assertIn("conflicting", undecided.reason)
        self.assertIn("item not identifiable", undecided.reason)

    def test_review_exclusions_and_pending(self) -> None:
        photo = self.decide([], _review(role="person_photo", game=None, game_evidence=""))
        self.assertEqual((photo.disposition, photo.reason), ("excluded", "person_photo: archivist review "
                                                             "(File:X.png): visual_inspection: event key art"))
        other = self.decide([], _review(role="not_dynamix", game="not_dynamix",
                                        game_evidence="visual_inspection: Minecraft screenshot"))
        self.assertEqual((other.disposition, other.game), ("excluded", "not_dynamix"))
        conflict = self.decide([_evidence(None, 0, "dynamix", context="embedded")],
                               _review(role="not_dynamix", game="not_dynamix", game_evidence="visual_inspection: x"))
        self.assertEqual(conflict.disposition, "pending")
        no_game = self.decide([], _review(game=None, game_evidence="", pending="no original-game evidence"))
        self.assertEqual((no_game.disposition, no_game.role), ("pending", "promotional"))
        self.assertIn("no original-game evidence", no_game.reason)
        stale = self.decide([], None, stale=True)
        self.assertIn("earlier revision", stale.reason)


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
        client = self.Client([
            {"query": {"allimages": [{"name": "A.png", "title": "File:A.png"}]},
             "continue": {"aicontinue": "B.png", "continue": "-||"}},
            {"query": {"allimages": [{"name": "B.png", "title": "File:B.png"}]}},
        ])
        result = mediawiki.allimages(mediawiki.MediaWikiAPI(client, "https://dynamix.miraheze.org/w/api.php"))
        self.assertTrue(result.complete)
        self.assertEqual(sorted(result.items), ["File:A.png", "File:B.png"])
        self.assertEqual((client.calls[1]["aicontinue"], client.calls[1]["continue"]), ("B.png", "-||"))

    def test_error_keeps_open_continuation(self) -> None:
        client = self.Client([
            {"query": {"allimages": [{"name": "A.png", "title": "File:A.png"}]},
             "continue": {"aicontinue": "B.png", "continue": "-||"}},
            net.HttpError("HTTP 503", url="https://dynamix.miraheze.org/w/api.php", http_status=503),
        ])
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


class SmallHelperTests(unittest.TestCase):
    def test_fandom_download_url_requests_original(self) -> None:
        url = "https://static.wikia.nocookie.net/d/images/9/9b/A.png/revision/latest?cb=20210409081347"
        self.assertEqual(discover.download_url(config.FANDOM, url), url + "&format=original")
        self.assertEqual(discover.download_url(config.FANDOM, "https://static.wikia.nocookie.net/a.png"),
                         "https://static.wikia.nocookie.net/a.png?format=original")
        self.assertEqual(discover.download_url(config.MIRAHEZE, "https://static.wikitide.net/a.png"),
                         "https://static.wikitide.net/a.png")

    def test_same_revision(self) -> None:
        a = {"sha1": "a" * 40, "bytes": 5, "timestamp": "2021-01-01T00:00:00Z"}
        self.assertTrue(acquire.same_revision(a, dict(a)))
        self.assertFalse(acquire.same_revision(a, dict(a, sha1="b" * 40)))
        self.assertFalse(acquire.same_revision(a, None))

    def test_japanese_tables(self) -> None:
        html = ('<div id="wikibody"><table><tr><th colspan="2">Title</th><th>Composer</th></tr>'
                '<tr><td>Xetta ( *4 )</td><td>x</td><td>Somebody</td></tr>'
                '<tr><td>春菊（*1）</td><td>x</td><td>SUWAKI</td></tr></table></div>')
        rows = jawikis.parse_tables(html, "list", "https://w.atwiki.jp/dynamix2ch/pages/37.html")
        self.assertEqual([(r.title, r.credit) for r in rows], [("Xetta", "Somebody"), ("春菊", "SUWAKI")])

    def test_edit_suffix_base_key(self) -> None:
        self.assertEqual(songindex.base_key("Elph's Sadness -DNX Edit-"), songindex.base_key(
            "Elph’s Sadness -Dynamix Edition-"))
        self.assertEqual(songindex.base_key("Magic of love (Dynamix Ver)"), songindex.base_key(
            "Magic of love (Dynamix Edit)"))
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
        self.assertEqual(songindex.category_collections([
            "Category:Event songs", "Category:Gold Submission Track", "Category:RAVON Songs",
            "Category:Paid Songs (Dynamix Universe)", "Category:Songs by MMRY"]),
            ["Event", "Open Submission (Gold)", "Collaboration: RAVON"])


if __name__ == "__main__":
    unittest.main()
