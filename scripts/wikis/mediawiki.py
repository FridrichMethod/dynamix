"""MediaWiki Action API enumeration with exact continuation handling (``list=allimages``, file usage, pages).

Every list follows the server's ``continue`` object verbatim until it is absent (MediaWiki's documented protocol, which
also covers generator + prop continuation). A failed request ends the list as *incomplete*: the result keeps the
continuation that was being requested and the error, so a snapshot can record ``discovery_complete: false`` instead of
treating a partial inventory as the whole wiki. So does an answer that carries no ``query`` object or no list under it
(MediaWiki's form of an empty generator), and a list that ends without a single entry: an empty answer is never taken
as the complete enumeration of a wiki (AGENTS.md, 07 §Integrity).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib import manifest, net
from lib.checks import redact_text

MAX_LIMIT = "max"
TITLE_BATCH = 50  # MediaWiki's default limit for titles= per request


class ApiError(Exception):
    """The API answered with an ``error`` object (or malformed data)."""

    def __init__(self, message: str, *, url: str):
        super().__init__(message)
        self.message = message
        self.url = url


@dataclass
class ListResult:
    """Outcome of one enumeration: merged items, completeness and the continuation to resume from."""

    name: str
    items: dict[str, Any] = field(default_factory=dict)
    complete: bool = False
    continuation: dict | None = None  # the continue object still to be requested (None once complete)
    requests: int = 0
    errors: list[dict] = field(default_factory=list)  # {"phase": "discover", "url": ..., "message": ...}


class MediaWikiAPI:
    """Thin query helper around :class:`lib.net.PoliteClient` (``formatversion=2`` JSON).

    ``cache_dir`` (development only) stores raw responses keyed by the request parameters so classification can be
    re-run without new requests; it is never used for committed data provenance beyond what the response contained.
    """

    def __init__(
        self, client: Any, api_url: str, *, cache_dir: str | Path | None = None, max_requests: int | None = None
    ):
        self.client = client
        self.api_url = api_url
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.max_requests = max_requests
        self.requests = 0
        self.cache_hits = 0
        self.cached_times: list[float] = []  # modification times of reused cached responses

    def _cache_path(self, params: Mapping[str, Any]) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(
            json.dumps([self.api_url, sorted(params.items())], ensure_ascii=False).encode()
        ).hexdigest()
        return self.cache_dir / f"{key[:32]}.json"

    def request_url(self, params: Mapping[str, Any]) -> str:
        """The URL of a request (for error records); parameters are public API arguments."""
        from urllib.parse import urlencode

        return f"{self.api_url}?{urlencode(sorted(params.items()))}"

    def call(self, params: Mapping[str, Any]) -> dict:
        """One API request; raises :class:`ApiError` or :class:`lib.net.FetchError`."""
        full = {"format": "json", "formatversion": "2", **params}
        full = {key: str(value) for key, value in full.items()}
        cached = self._cache_path(full)
        if cached is not None and cached.is_file():
            self.cache_hits += 1
            self.cached_times.append(cached.stat().st_mtime)
            return manifest.load_json(cached)
        if self.max_requests is not None and self.requests >= self.max_requests:
            raise ApiError(f"request budget of {self.max_requests} exhausted", url=self.request_url(full))
        self.requests += 1
        data, _final = self.client.get_json(self.api_url, full)
        if not isinstance(data, dict):
            raise ApiError("response is not a JSON object", url=self.request_url(full))
        if "error" in data:
            error = data["error"] if isinstance(data["error"], dict) else {"info": str(data["error"])}
            raise ApiError(
                f"API error {error.get('code', '?')}: {error.get('info', '')}"[:300], url=self.request_url(full)
            )
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_json_atomic(cached, data)
        return data

    def enumerate(
        self,
        name: str,
        params: Mapping[str, Any],
        merge: Callable[[dict[str, Any], dict], None],
        *,
        start: Mapping[str, Any] | None = None,
        list_key: str | None = None,
    ) -> ListResult:
        """Follow ``continue`` from ``start`` (default: the beginning) and ``merge`` each response into ``items``.

        With ``list_key`` (``allimages`` or ``pages``) every response must carry ``query.<list_key>`` as a list, and
        the finished list must hold at least one entry; otherwise the list stays incomplete with an error."""
        result = ListResult(name)
        continuation: dict | None = dict(start) if start else {}
        seen: set[str] = set()
        while continuation is not None:
            marker = json.dumps(sorted(continuation.items()))
            if marker in seen:
                result.errors.append(
                    {
                        "phase": "discover",
                        "url": self.request_url({**params, **continuation}),
                        "message": f"{name}: the API repeated a continuation; stopped",
                    }
                )
                result.continuation = continuation
                return result
            seen.add(marker)
            try:
                data = self.call({**params, **continuation})
            except (ApiError, net.FetchError) as error:
                url = getattr(error, "url", None) or self.request_url({**params, **continuation})
                result.errors.append(
                    {
                        "phase": "discover",
                        "url": url,
                        "message": redact_text(f"{name}: {type(error).__name__}: {error}")[:500],
                    }
                )
                result.continuation = continuation  # {} = the list failed before its first page
                return result
            result.requests += 1
            problem = query_problem(data, list_key)
            if problem:
                result.errors.append(
                    {
                        "phase": "discover",
                        "url": self.request_url({**params, **continuation}),
                        "message": f"{name}: {problem}; not taken as a complete page",
                    }
                )
                result.continuation = continuation
                return result
            merge(result.items, data)
            next_continuation = data.get("continue")
            if next_continuation is not None and not isinstance(next_continuation, dict):
                result.errors.append(
                    {
                        "phase": "discover",
                        "url": self.request_url(params),
                        "message": f"{name}: malformed continue value",
                    }
                )
                result.continuation = continuation
                return result
            continuation = dict(next_continuation) if next_continuation else None
        if list_key is not None and not result.items:
            result.errors.append(
                {
                    "phase": "discover",
                    "url": self.request_url({**params, **(start or {})}),
                    "message": f"{name}: the API listed no entry at all; an empty answer is not taken as "
                    "the complete enumeration of the wiki",
                }
            )
            result.continuation = dict(start) if start else {}
            return result
        result.complete = True
        result.continuation = None
        return result


def query_problem(data: Mapping[str, Any], list_key: str | None) -> str | None:
    """Why a 200 JSON answer is not a usable page of the list ``list_key`` (None when it is, or when no key is
    required): MediaWiki omits ``query`` for an empty generator, and a list query always names its list."""
    if list_key is None:
        return None
    query = data.get("query")
    if not isinstance(query, dict):
        return "the answer carries no query object (an empty or failed listing)"
    if not isinstance(query.get(list_key), list):
        return f"the answer's query object has no {list_key} list"
    return None


# --- Concrete lists ---------------------------------------------------------------------------------------------------

ALLIMAGES_PARAMS = {
    "action": "query",
    "list": "allimages",
    "ailimit": MAX_LIMIT,
    "aisort": "name",
    "aiprop": "timestamp|user|comment|url|size|mime|sha1",
}
FILE_USAGE_PARAMS = {
    "action": "query",
    "generator": "allimages",
    "gailimit": MAX_LIMIT,
    "gaisort": "name",
    "prop": "fileusage|categories",
    "fulimit": MAX_LIMIT,
    "fuprop": "title",
    "cllimit": MAX_LIMIT,
}
# File description pages: current wikitext (redirects, descriptions) and the pages linking to them ([[:File:X]]).
# Revision content allows at most 50 pages per request.
FILE_PAGES_PARAMS = {
    "action": "query",
    "generator": "allimages",
    "gailimit": "50",
    "gaisort": "name",
    "prop": "revisions|linkshere",
    "rvprop": "ids|timestamp|content",
    "rvslots": "main",
    "lhlimit": MAX_LIMIT,
    "lhprop": "title|redirect",
}
PAGES_PARAMS = {
    "action": "query",
    "generator": "allpages",
    "gapnamespace": "0",
    "gapfilterredir": "nonredirects",
    "gaplimit": "50",
    "prop": "revisions|categories|info",
    "rvprop": "ids|timestamp|content",
    "rvslots": "main",
    "cllimit": MAX_LIMIT,
    "inprop": "url",
}


def _merge_allimages(items: dict[str, Any], data: dict) -> None:
    for row in data.get("query", {}).get("allimages", []):
        title = row.get("title") or ("File:" + str(row.get("name", "")).replace("_", " "))
        items[title] = {
            key: row.get(key)
            for key in (
                "name",
                "title",
                "timestamp",
                "user",
                "comment",
                "url",
                "descriptionurl",
                "size",
                "width",
                "height",
                "mime",
                "sha1",
            )
        }
        items[title]["title"] = title


def _merge_pages_lists(fields: Iterable[str]) -> Callable[[dict[str, Any], dict], None]:
    def merge(items: dict[str, Any], data: dict) -> None:
        for page in data.get("query", {}).get("pages", []):
            entry = items.setdefault(
                page["title"], {"title": page["title"], "ns": page.get("ns"), "missing": bool(page.get("missing"))}
            )
            for name in fields:
                values = entry.setdefault(name, [])
                for value in page.get(name, []) or []:
                    if value not in values:
                        values.append(value)
            for key in ("pageid", "fullurl", "lastrevid", "touched"):
                if key in page:
                    entry[key] = page[key]
            for revision in page.get("revisions", []) or []:
                slot = (revision.get("slots") or {}).get("main") or {}
                entry["revision"] = {
                    "revid": revision.get("revid"),
                    "timestamp": revision.get("timestamp"),
                    "content": slot.get("content", slot.get("*")),
                }

    return merge


def allimages(api: MediaWikiAPI, *, start: Mapping[str, Any] | None = None) -> ListResult:
    """Every file of the wiki with its current revision's metadata, keyed by ``File:`` title."""
    return api.enumerate("allimages", ALLIMAGES_PARAMS, _merge_allimages, start=start, list_key="allimages")


def file_usage(api: MediaWikiAPI, *, start: Mapping[str, Any] | None = None) -> ListResult:
    """``prop=fileusage|categories`` for every file (generator=allimages), keyed by ``File:`` title."""
    return api.enumerate(
        "fileusage", FILE_USAGE_PARAMS, _merge_pages_lists(("fileusage", "categories")), start=start, list_key="pages"
    )


def file_pages(api: MediaWikiAPI, *, start: Mapping[str, Any] | None = None) -> ListResult:
    """Every file description page (generator=allimages) with its current wikitext and the pages linking to it."""
    return api.enumerate(
        "filepages", FILE_PAGES_PARAMS, _merge_pages_lists(("linkshere",)), start=start, list_key="pages"
    )


def content_pages(api: MediaWikiAPI, *, start: Mapping[str, Any] | None = None) -> ListResult:
    """Every non-redirect article (namespace 0) with its current wikitext, revision ID and categories."""
    return api.enumerate("pages", PAGES_PARAMS, _merge_pages_lists(("categories",)), start=start, list_key="pages")


def gif_frames(api: MediaWikiAPI, titles: list[str]) -> ListResult:
    """Frame counts of GIF files from ``iiprop=metadata`` (``frameCount``); keyed by title, value int or None."""
    result = ListResult("gifmetadata", complete=True)
    for start in range(0, len(titles), TITLE_BATCH):
        batch = titles[start : start + TITLE_BATCH]
        params = {"action": "query", "prop": "imageinfo", "iiprop": "metadata", "titles": "|".join(batch)}
        part = api.enumerate("gifmetadata", params, _merge_gif_metadata, list_key="pages")
        result.items.update(part.items)
        result.requests += part.requests
        result.errors.extend(part.errors)
        if not part.complete:
            result.complete = False
            result.continuation = {"titles_from": batch[0]}
    return result


def _merge_gif_metadata(items: dict[str, Any], data: dict) -> None:
    for page in data.get("query", {}).get("pages", []):
        frames = None
        for info in page.get("imageinfo", []) or []:
            for entry in info.get("metadata") or []:
                if isinstance(entry, dict) and entry.get("name") == "frameCount":
                    try:
                        frames = int(entry.get("value"))
                    except (TypeError, ValueError):
                        frames = None
        items[page["title"]] = frames


def expand_templates(api: MediaWikiAPI, texts: dict[str, str]) -> ListResult:
    """``action=expandtemplates`` for song ``Title`` values built from templates (``{{MassacrE}}``): raw wikitext ->
    expanded wikitext. ``texts`` maps the raw value to the page title it appears on (the expansion context)."""
    result = ListResult("expandtemplates", complete=True)
    for raw, page in sorted(texts.items()):
        params = {"action": "expandtemplates", "text": raw, "title": page, "prop": "wikitext"}
        try:
            data = api.call(params)
        except (ApiError, net.FetchError) as error:
            result.complete = False
            result.continuation = {"text": raw}
            result.errors.append(
                {
                    "phase": "discover",
                    "url": getattr(error, "url", None) or api.request_url(params),
                    "message": redact_text(f"expandtemplates: {type(error).__name__}: {error}")[:500],
                }
            )
            continue
        result.requests += 1
        value = (data.get("expandtemplates") or {}).get("wikitext")
        if isinstance(value, str):
            result.items[raw] = value
    return result
