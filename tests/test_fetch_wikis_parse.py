"""Unit tests of the wiki parsers and classifiers (no network): ``python -I tests/test_fetch_wikis_parse.py``."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "wikis"))

from lib import net  # noqa: E402
from wikis import acquire, classify, config, discover, jawikis, mediawiki, songindex, wikitext  # noqa: E402


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

    def test_universe_categories(self) -> None:
        for category in ("Category:Dynamix UV", "Category:DynamixUV", "Category:Songs (Dynamix Universe)"):
            self.assertTrue(classify._universe_category(category), category)
        self.assertFalse(classify._universe_category("Category:Dynamix"))


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

    def test_category_collections_skip_universe(self) -> None:
        self.assertEqual(songindex.category_collections([
            "Category:Event songs", "Category:Gold Submission Track", "Category:RAVON Songs",
            "Category:Paid Songs (Dynamix Universe)", "Category:Songs by MMRY"]),
            ["Event", "Open Submission (Gold)", "Collaboration: RAVON"])


if __name__ == "__main__":
    unittest.main()
