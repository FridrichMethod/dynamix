"""Synthetic two-wiki scenario for the offline ``fetch_wikis.py`` tests.

Everything is synthetic: page texts imitate the observed layout of the Dynamix wikis (``Template:Song`` and
``Template:SongUV`` fields, galleries with captions, Trivia sections, package pages, profile lists) and the images are
small generated patterns. The cases mirror 05-agent-handoffs.md §A: Cosmic Pulse attribution (Cover Artist ariiol
with a ``<ref>``, composer Yunosuke via ``Compound Artist Link``), ``(XD) D.jpg`` as a gameplay screenshot from its
Trivia gallery, a ``-HQ`` file that is only a background and an unused ``BG-HQ`` file that stays pending, Universe-only
and video placeholder exclusions, an animated GIF capture, a same-wiki duplicate, Unicode and case-colliding titles,
same-size different bytes, a Fandom API size/SHA-1 that differs from the served bytes, and a Fandom CDN WEBP behind a
``.png`` URL.
"""

from __future__ import annotations

from typing import Any

from wikis import config

from fakewiki import FakeClient, FakeMediaWiki, file_row, image_bytes

M_FILES = "https://static.wikitide.net/dynamixwiki/x/xx"
F_FILES = "https://static.wikia.nocookie.net/dynamixc4cat/images/y/yy"
F_SUFFIX = "/revision/latest?cb=20210211144130"

COSMIC = """{{Song
|color = 551A8B
|Title = Cosmic Pulse
|Compound Artist Link = [[Artists#雄之助|Yunosuke]]
|Mode = Regular and Extra<br>Event
|No. = 525
|Update = 17/1/2025
|Imgsuffix = CosmicPulseDynamix.png
|Cover Artist = ariiol<ref>[https://x.com/ariiol_/status/1879155241788293570 The illustrations are posted here.]</ref>
|Charter = TehLightningChicken
}}
==Gallery==
<gallery>
Cosmic Pulse Chart List.jpg|Cosmic Pulse Chart List
Cosmic Pulse copy.png|Cosmic Pulse artwork (copy)
Cosmic Pulse Squared.png|Cosmic Pulse Artwork in RAVON
</gallery>
[[Category:Songs]]
"""

XD = """{{Song
|Artist = Eu
|Imgsuffix = (XD)Dynamix.png
|Cover Artist = HAT
}}
==Gallery==
<gallery>
(XD)BG-HQ.png|Used Background "XD"
Emi-Raw.png|Used Character "Emi"
Emi-Animated.gif|Character "Emi" (animated)
</gallery>
==Trivia==
* The slide notes form an X and a D.
<gallery>
(XD) D.jpg|D
</gallery>
"""

UNIVERSE = """{{SongUV
|Artist = Somebody
|Imgsuffix = Universe Song cover.png
|Illustrator = N/A
}}
"""

UNICODE = """{{Song
|Artist = N/A
|Imgsuffix = 交差して歪む過去と未来.jpg
|Cover Artist = N/A
}}
"""

TWIN = """{{Song
|Artist = Twin Composer
|Imgsuffix = TwinDynamix.png
}}
==Gallery==
<gallery>
Abc.png|Used Background "Abc"
ABC.png|Used Part "ABC"
SameSizeA.png|Used Background "A"
SameSizeB.png|Used Background "B"
</gallery>
"""

VOCAL = """{| class="wikitable"
| [[File:CosmicPulseDynamix.png|400px|link=Cosmic Pulse]]
|}
<gallery>
File:Package Vocal.jpg|Package Vocal
</gallery>
[[Category:Dynamix]]
[[Category:Song Package]]
"""

ARTISTS = "== Yunosuke ==\n[[File:Composer Photo.jpg|200px]]\n"
LOST = """{{SongOther
|Artist = Somebody Else
|Imgsuffix = LostDynamix.png
|Cover Artist = Unknown Painter
}}
"""
WHIRLWIND = """{{Song
|Artist = Keiyuu & RED-FOX
|Imgsuffix = WHIRLWINDDynamix.png
}}
<gallery>
WHIRLWINDBG.png|Used Background "WHIRLWIND"
Fandom Only Chart List.jpg|WHIRLWIND Chart List
</gallery>
"""

PAGES_COMMON = {
    "Cosmic Pulse": {"wikitext": COSMIC, "categories": ["Category:Songs", "Category:Event songs"], "revid": 42090},
    "(XD)": {"wikitext": XD, "categories": ["Category:Songs", "Category:Event songs"], "revid": 40001},
    "Universe Song": {"wikitext": UNIVERSE, "categories": ["Category:Songs (Dynamix Universe)"], "revid": 40002},
    "交差して歪む過去と未来": {"wikitext": UNICODE, "categories": ["Category:Songs"], "revid": 40003},
    "Twin Song": {"wikitext": TWIN, "categories": ["Category:Songs"], "revid": 40004},
    "Vocal": {"wikitext": VOCAL, "categories": ["Category:Dynamix", "Category:Song Package"], "revid": 40005},
    "Artists": {"wikitext": ARTISTS, "categories": ["Category:Artists"], "revid": 40006},
    "Lost Song": {"wikitext": LOST, "categories": ["Category:Unreleased"], "revid": 40007},
}

ATWIKI_25 = """<html><body><div id="wikibody"><table>
<tr><th>Title</th><th>Casual</th><th>Composer</th></tr>
<tr><td>Cosmic Pulse</td><td>4</td><td>Yunosuke</td></tr>
<tr><td>(XD) ( *1 )</td><td>3</td><td>Eu</td></tr>
<tr><td>Japanese Only Song</td><td>2</td><td>JP Composer</td></tr>
</table></div></body></html>"""
ATWIKI_529 = """<html><body><div id="wikibody"><table>
<tr><th>Title</th><th>Casual</th><th>Composer</th></tr>
<tr><td>Removed Song</td><td>2</td><td>Gone</td></tr>
</table></div></body></html>"""
WIKIWIKI = """<html><body><div id="body"><table>
<tr><th>曲名</th><th>名義</th></tr>
<tr><td>Cosmic Pulse</td><td>Yunosuke</td></tr>
<tr><td>Universe Song</td><td>Somebody</td></tr>
</table></div></body></html>"""


class Scenario:
    """Builds the fake Miraheze/Fandom wikis, Japanese pages and file bytes; tests mutate it between runs."""

    def __init__(self) -> None:
        size = (64, 36)
        self.blobs: dict[str, bytes] = {}
        self.data: dict[str, bytes] = {
            "CosmicPulseDynamix.png": image_bytes("PNG", size, 1),
            "Cosmic Pulse Chart List.jpg": image_bytes("JPEG", size, 2),
            "(XD)Dynamix.png": image_bytes("PNG", size, 3),
            "(XD)BG-HQ.png": image_bytes("PNG", (49, 42), 4),
            "Emi-Raw.png": image_bytes("PNG", (40, 40), 5),
            "Emi-Animated.gif": image_bytes("GIF", (25, 25), 6, frames=3),
            "(XD) D.jpg": image_bytes("JPEG", (64, 36), 7),
            "Universe Song cover.png": image_bytes("PNG", (32, 32), 8),
            "交差して歪む過去と未来.jpg": image_bytes("JPEG", size, 9),
            "TwinDynamix.png": image_bytes("PNG", size, 10),
            "Abc.png": image_bytes("PNG", (20, 20), 11),
            "ABC.png": image_bytes("PNG", (20, 20), 12),
            "SameSizeA.png": _solid((8, 8), (10, 20, 30)),
            "SameSizeB.png": _solid((8, 8), (30, 20, 10)),
            "Package Vocal.jpg": image_bytes("JPEG", size, 13),
            "Composer Photo.jpg": image_bytes("JPEG", (20, 20), 14),
            "Lonely BG-HQ.png": image_bytes("PNG", (30, 30), 15),
            "WHIRLWINDDynamix.png": image_bytes("PNG", size, 16),
            "WHIRLWINDBG.png": image_bytes("PNG", (20, 20), 17),
            "Fandom Only Chart List.jpg": image_bytes("JPEG", size, 18),
            "Cosmic Pulse Squared.png": image_bytes("PNG", (36, 36), 19),
            "LostDynamix.png": image_bytes("PNG", size, 20),
        }
        self.data["Cosmic Pulse copy.png"] = self.data["CosmicPulseDynamix.png"]
        self.webp_whirlwind = image_bytes("WEBP", (20, 20), 17)
        self.miraheze_names = [name for name in self.data if name not in ("WHIRLWINDBG.png", "WHIRLWINDDynamix.png",
                                                                         "Fandom Only Chart List.jpg")]
        self.fandom_names = [name for name in self.data if name not in ("Lonely BG-HQ.png",)]
        self.miraheze_pages = dict(PAGES_COMMON)
        self.fandom_pages = dict(PAGES_COMMON, WHIRLWIND={"wikitext": WHIRLWIND, "categories": ["Category:Songs"],
                                                          "revid": 50001})
        self.overrides: dict[tuple[str, str], dict[str, Any]] = {
            # Fandom's API advertises other bytes/SHA-1 than its format=original response (observed live).
            ("fandom", "CosmicPulseDynamix.png"): {"sha1": "0" * 40, "nbytes": len(self.data[
                "CosmicPulseDynamix.png"]) + 18},
        }
        self.texts = {
            "https://w.atwiki.jp/dynamix2ch/pages/25.html": ATWIKI_25,
            "https://w.atwiki.jp/dynamix2ch/pages/529.html": ATWIKI_529,
            "https://wikiwiki.jp/rhythmgame/Dynamix": WIKIWIKI,
        }
        for _label, url in config.ATWIKI.list_pages:
            self.texts.setdefault(url, ATWIKI_25.replace("Japanese Only Song", "Cosmic Pulse"))
        self.mime = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif"}

    def rows(self, wiki: str) -> list[dict]:
        names = self.miraheze_names if wiki == "miraheze" else self.fandom_names
        base, suffix = (M_FILES, "") if wiki == "miraheze" else (F_FILES, F_SUFFIX)
        description = ("https://dynamix.miraheze.org/wiki/" if wiki == "miraheze"
                       else "https://dynamixc4cat.fandom.com/wiki/")
        rows = []
        for name in names:
            data = self.data[name]
            from PIL import Image
            import io
            with Image.open(io.BytesIO(data)) as image:
                size = image.size
            extra = self.overrides.get((wiki, name), {})
            rows.append(file_row(base, name, data, size, mime=self.mime[name.rsplit(".", 1)[1]],
                                 description_base=description, suffix=suffix,
                                 timestamp=extra.get("timestamp", "2021-02-11T14:41:30Z"),
                                 sha1=extra.get("sha1"), nbytes=extra.get("nbytes")))
        if wiki == "fandom":
            rows.append({"name": "Some_video", "title": "File:Some video", "timestamp": "2020-01-01T00:00:00Z",
                         "url": "https://static.wikia.nocookie.net/dynamixc4cat/images/z/zz/Some_video",
                         "descriptionurl": "https://dynamixc4cat.fandom.com/wiki/File:Some_video", "size": 0,
                         "width": 0, "height": 0, "mime": "video/youtube", "sha1": "1" * 40})
        return rows

    def wikis(self) -> dict[str, FakeMediaWiki]:
        frames = {"File:Emi-Animated.gif": 3}
        return {
            config.MIRAHEZE.api_url: FakeMediaWiki("https://dynamix.miraheze.org", self.rows("miraheze"),
                                                   self.miraheze_pages, frames=frames),
            config.FANDOM.api_url: FakeMediaWiki("https://dynamixc4cat.fandom.com", self.rows("fandom"),
                                                 self.fandom_pages, frames=frames),
        }

    def client(self, wikis: dict[str, FakeMediaWiki] | None = None) -> FakeClient:
        blobs: dict[str, bytes] = {}
        for wiki in ("miraheze", "fandom"):
            for row in self.rows(wiki):
                name = row["name"].replace("_", " ")
                if name not in self.data:
                    continue
                url = row["url"] + ("&format=original" if wiki == "fandom" else "")
                blobs[url] = self.webp_whirlwind if (wiki, name) == ("fandom", "WHIRLWINDBG.png") else self.data[name]
        return FakeClient(wikis or self.wikis(), texts=self.texts, blobs=blobs)


def _solid(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()
