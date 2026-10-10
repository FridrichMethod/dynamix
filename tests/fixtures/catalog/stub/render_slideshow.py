"""Test stand-in for scripts/render_slideshow.py (UI-owned), used by the catalog tests only when the real module is
not importable (tests append this directory to sys.path after scripts/). It follows the frozen interface:
render_slideshow(root, catalog) reads templates/slideshow.html and replaces @python-work-area with one
<div class="mySlides fade"><img ...></div> per asset whose `slideshow` is true, attribute-escaping every value."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape


def attr(value: object) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&#39;", "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"})


def render_slideshow(root: Path, catalog: dict) -> str:
    template = (Path(root) / "templates/slideshow.html").read_text(encoding="utf-8")
    slides = []
    for asset in catalog["assets"]:
        if asset["slideshow"]:
            fields = {"data-src": asset["url"], "data-id": asset["id"], "data-title": asset["title"],
                      "alt": asset["title"]}
            attributes = " ".join(f'{key}="{attr(value)}"' for key, value in fields.items())
            slides.append(f'        <div class="mySlides fade"><img {attributes}></div>')
    return template.replace("@python-work-area", "\n".join(slides))
