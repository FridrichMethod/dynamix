"""Offline test doubles for the primary-source fetchers (used by tests/test_fetch_*.py; never by acquisition).

:class:`FakeNetwork` serves canned responses by exact URL (query parameters included, encoded like ``requests``) and
records every call. Each source gets a :class:`FakeClient` limited to the allowlist the runner passes, so tests also
exercise the allowlist wiring. Responses are ``{"json": obj}``, ``{"text": str}``, ``{"bytes": b, "content_type": str}``
or ``{"error": FetchError}``; a list of responses is consumed in order (the last one repeats).
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from lib import net

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
FIXED_NOW = "2026-10-10T12:00:00Z"


def image_bytes(
    fmt: str = "PNG", size: tuple[int, int] = (64, 36), color: tuple[int, int, int] = (200, 30, 90)
) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


def full_url(url: str, params: Mapping[str, Any] | None = None) -> str:
    if not params:
        return url
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params, doseq=True)}"


class FakeNetwork:
    def __init__(self, routes: Mapping[str, Any] | None = None):
        self.routes: dict[str, Any] = dict(routes or {})
        self.calls: list[tuple[str, str]] = []
        self.factories: list[list[str]] = []

    def factory(self, hosts: list[str], min_interval: float) -> FakeClient:
        self.factories.append(list(hosts))
        return FakeClient(self, hosts)

    def respond(self, method: str, url: str) -> Mapping[str, Any]:
        self.calls.append((method, url))
        if url not in self.routes:
            raise net.HttpError("HTTP 404 (no fake route)", url=url, http_status=404)
        response = self.routes[url]
        if isinstance(response, list):
            response = response.pop(0) if len(response) > 1 else response[0]
        if "error" in response:
            raise response["error"]
        return response

    def count(self, host: str) -> int:
        return sum(1 for _, url in self.calls if urlsplit(url).hostname == host)


class FakeClient:
    def __init__(self, network: FakeNetwork, hosts: list[str]):
        self.network, self.hosts = network, hosts

    def _check(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        if not any(host == h or (h.startswith(".") and host.endswith(h)) for h in self.hosts):
            raise net.HostNotAllowedError(f"host {host} is not allowed", url=url)

    def get_json(self, url: str, params: Mapping[str, Any] | None = None, **_: Any) -> tuple[Any, str]:
        target = full_url(url, params)
        self._check(target)
        return self.network.respond("get_json", target)["json"], target

    def get_text(self, url: str, **_: Any) -> tuple[str, str, dict]:
        self._check(url)
        response = self.network.respond("get_text", url)
        return response["text"], response.get("final_url", url), {}

    def download(self, url: str, dest_dir: Any, stem: str, **_: Any) -> net.DownloadResult:
        self._check(url)
        response = self.network.respond("download", url)
        data = response["bytes"]
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{stem[:20] or 'file'}.", suffix=".part", dir=dest_dir)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        return net.DownloadResult(
            temp_path=Path(name),
            final_url=response.get("final_url", url),
            status=200,
            content_type=response.get("content_type"),
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            sha1=hashlib.sha1(data).hexdigest(),
            headers={},
        )

    def close(self) -> None:
        pass
