"""Offline stand-ins for the wiki APIs, HTML pages and file CDNs used by ``tests/test_fetch_wikis*.py``.

:class:`FakeMediaWiki` answers the exact Action API queries :mod:`wikis.mediawiki` sends (``list=allimages``,
``generator=allimages`` + ``prop=fileusage|categories``, ``generator=allimages`` + ``prop=revisions|linkshere``
(file description pages), ``generator=allpages`` + revisions, ``prop=imageinfo`` metadata,
``action=expandtemplates``) with MediaWiki's ``continue`` protocol and a small page size, so every list needs several
continuation round trips. :class:`FakeClient` mirrors :class:`lib.net.PoliteClient`'s public methods
(``get_json``, ``get_text``, ``download`` writing a hidden ``.part`` file) and raises the real ``lib.net`` errors for
injected failures. Nothing here opens a socket.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from lib import net, paths

FILE_REF = re.compile(r"(?:\[\[\s*(?:File|Image)\s*:\s*|^\s*(?:File:)?)([^|\]\n]+\.(?:png|jpe?g|gif|webp))", re.I | re.M)


def image_bytes(fmt: str, size: tuple[int, int], seed: int, *, frames: int = 1) -> bytes:
    """Deterministic small test image (distinct per seed)."""
    def frame(index: int) -> Image.Image:
        image = Image.new("RGB", size, ((seed * 37 + index * 11) % 256, (seed * 91) % 256, (seed * 53 + 7) % 256))
        for x in range(0, size[0], 7):
            image.putpixel((x, (x * seed + index) % size[1]), (255 - x % 256, seed % 256, 0))
        return image
    buffer = io.BytesIO()
    if fmt == "GIF" and frames > 1:
        frame(0).save(buffer, "GIF", save_all=True, append_images=[frame(i) for i in range(1, frames)], duration=50)
    else:
        frame(0).save(buffer, fmt)
    return buffer.getvalue()


class FakeMediaWiki:
    """In-memory wiki: ``files`` (allimages rows), ``pages`` (title -> wikitext/categories), ``file_categories``."""

    def __init__(self, base: str, files: list[dict], pages: Mapping[str, Mapping[str, Any]], *,
                 file_categories: Mapping[str, list[str]] | None = None, frames: Mapping[str, int] | None = None,
                 expansions: Mapping[str, str] | None = None, page_size: int = 2,
                 file_pages: Mapping[str, str] | None = None, links: Mapping[str, list[dict]] | None = None):
        self.base = base.rstrip("/")
        self.files = sorted(files, key=lambda row: row["name"])
        self.pages = dict(pages)
        self.file_categories = dict(file_categories or {})
        self.frames = dict(frames or {})
        self.expansions = dict(expansions or {})
        self.file_pages = dict(file_pages or {})  # File: title -> description wikitext
        self.links = dict(links or {})  # File: title -> linkshere entries ({"ns", "title"[, "redirect"]})
        self.page_size = page_size
        self.fail: dict[tuple[str, int], Exception] = {}  # (list name, 1-based request number) -> error
        self.calls: dict[str, int] = {}

    def usage(self, title: str) -> list[dict]:
        name = title.split(":", 1)[1]
        found = []
        for page, spec in sorted(self.pages.items()):
            refs = {match.group(1).strip().replace("_", " ") for match in FILE_REF.finditer(spec["wikitext"])}
            refs |= set(spec.get("uses", []))
            if name in refs or name.replace(" ", "_") in refs:
                found.append({"pageid": abs(hash(page)) % 10000, "ns": spec.get("ns", 0), "title": page})
        return found

    def _page(self, name: str, rows: list, key: str, params: Mapping[str, str]) -> tuple[list, dict | None]:
        number = self.calls[name] = self.calls.get(name, 0) + 1
        error = self.fail.get((name, number))
        if error is not None:
            raise error
        start = 0
        if params.get(key):
            start = next((i for i, row in enumerate(rows) if row >= params[key]), len(rows))
        chunk = rows[start:start + self.page_size]
        rest = rows[start + self.page_size:]
        return chunk, ({key: rest[0], "continue": "-||" if not key.startswith("g") else "gapcontinue||"}
                       if rest else None)

    def query(self, params: Mapping[str, str]) -> dict:
        if params.get("action") == "expandtemplates":
            return {"expandtemplates": {"wikitext": self.expansions.get(params["text"], params["text"])}}
        if params.get("list") == "allimages":
            names, cont = self._page("allimages", [row["name"] for row in self.files], "aicontinue", params)
            rows = [self._row(name) for name in names]
            return self._wrap({"allimages": rows}, cont)
        if params.get("generator") == "allimages" and "revisions" in params.get("prop", ""):
            names, cont = self._page("filepages", [row["name"] for row in self.files], "gaicontinue", params)
            pages = []
            for name in names:
                title = "File:" + name.replace("_", " ")
                page = {"pageid": abs(hash(title)) % 100000, "ns": 6, "title": title,
                        "revisions": [{"revid": 7, "timestamp": "2026-01-01T00:00:00Z",
                                       "slots": {"main": {"content": self.file_pages.get(title, "")}}}]}
                if self.links.get(title):
                    page["linkshere"] = list(self.links[title])
                pages.append(page)
            return self._wrap({"pages": pages}, cont)
        if params.get("generator") == "allimages":
            names, cont = self._page("fileusage", [row["name"] for row in self.files], "gaicontinue", params)
            pages = []
            for name in names:
                title = "File:" + name.replace("_", " ")
                page = {"pageid": abs(hash(title)) % 100000, "ns": 6, "title": title}
                usage = self.usage(title)
                if usage:
                    page["fileusage"] = usage
                if self.file_categories.get(title):
                    page["categories"] = [{"ns": 14, "title": c} for c in self.file_categories[title]]
                pages.append(page)
            return self._wrap({"pages": pages}, cont)
        if params.get("generator") == "allpages":
            titles = sorted(title for title, spec in self.pages.items() if spec.get("ns", 0) == 0)
            chunk, cont = self._page("pages", titles, "gapcontinue", params)
            pages = []
            for title in chunk:
                spec = self.pages[title]
                pages.append({
                    "pageid": abs(hash(title)) % 100000, "ns": 0, "title": title,
                    "fullurl": f"{self.base}/wiki/{paths.slug(title)}", "lastrevid": spec.get("revid", 100),
                    "revisions": [{"revid": spec.get("revid", 100), "timestamp": "2026-01-01T00:00:00Z",
                                   "slots": {"main": {"content": spec["wikitext"]}}}],
                    "categories": [{"ns": 14, "title": c} for c in spec.get("categories", [])]})
            return self._wrap({"pages": pages}, cont)
        if params.get("prop") == "imageinfo":
            pages = []
            for title in params["titles"].split("|"):
                info = {"metadata": [{"name": "frameCount", "value": self.frames[title]}]} \
                    if title in self.frames else {"metadata": []}
                pages.append({"ns": 6, "title": title, "imageinfo": [info]})
            return self._wrap({"pages": pages}, None)
        raise AssertionError(f"unexpected query {dict(params)}")

    def _row(self, name: str) -> dict:
        row = next(row for row in self.files if row["name"] == name)
        return {key: value for key, value in row.items() if not key.startswith("_")}

    @staticmethod
    def _wrap(query: dict, cont: dict | None) -> dict:
        data: dict[str, Any] = {"batchcomplete": True, "query": query}
        if cont:
            data["continue"] = cont
        return data


class FakeClient:
    """Drop-in for :class:`lib.net.PoliteClient` serving the fake wikis, HTML pages and file bytes."""

    def __init__(self, apis: Mapping[str, FakeMediaWiki], *, texts: Mapping[str, str] | None = None,
                 blobs: Mapping[str, bytes | Callable[[], bytes]] | None = None,
                 errors: Mapping[str, Exception] | None = None):
        self.apis = dict(apis)
        self.texts = dict(texts or {})
        self.blobs = dict(blobs or {})
        self.errors = dict(errors or {})
        self.downloads: list[str] = []
        self.interrupt_after: int | None = None

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> tuple[dict, str]:
        api = self.apis[url]
        return api.query({key: str(value) for key, value in (params or {}).items()}), url

    def get_text(self, url: str) -> tuple[str, str, dict]:
        if url in self.errors:
            raise self.errors[url]
        if url not in self.texts:
            raise net.HttpError("HTTP 404", url=url, http_status=404)
        return self.texts[url], url, {"content-type": "text/html; charset=utf-8"}

    def download(self, url: str, dest_dir: str | os.PathLike[str], stem: str) -> net.DownloadResult:
        if self.interrupt_after is not None and len(self.downloads) >= self.interrupt_after:
            raise KeyboardInterrupt("simulated kill")
        self.downloads.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url not in self.blobs:
            raise net.HttpError("HTTP 404", url=url, http_status=404)
        blob = self.blobs[url]
        data = blob() if callable(blob) else blob
        directory = Path(dest_dir)
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{paths.slug(stem)}.", suffix=".part", dir=directory)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        content_type = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp",
                        b"GIF8": "image/gif"}
        kind = next((value for key, value in content_type.items() if data.startswith(key)), None)
        return net.DownloadResult(Path(name), url, 200, kind, len(data), hashlib.sha256(data).hexdigest(),
                                  hashlib.sha1(data).hexdigest(), {})


def file_row(base_file_url: str, name: str, data: bytes, size: tuple[int, int], *, mime: str,
             timestamp: str = "2021-02-11T14:41:30Z", sha1: str | None = None, nbytes: int | None = None,
             description_base: str = "https://example.invalid/wiki/", suffix: str = "") -> dict:
    """An allimages row whose advertised values default to the real bytes (override to simulate mismatches)."""
    from urllib.parse import quote
    return {"name": name.replace(" ", "_"), "title": "File:" + name.replace("_", " "), "timestamp": timestamp,
            "url": f"{base_file_url}/{quote(name.replace(' ', '_'))}{suffix}",
            "descriptionurl": f"{description_base}File:{quote(name.replace(' ', '_'))}",
            "size": len(data) if nbytes is None else nbytes, "width": size[0], "height": size[1], "mime": mime,
            "sha1": sha1 or hashlib.sha1(data).hexdigest()}
