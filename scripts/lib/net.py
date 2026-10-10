"""Polite, allowlisted HTTP client shared by every fetcher (network defaults of the frozen CLI contract).

* Explicit host allowlist (exact hosts, or ``.example.com`` for subdomains only), checked for the first URL and for
  every redirect hop; only http/https; URLs with credentials are refused; an https -> http redirect is refused.
* Redirects are followed manually (at most 5 hops). Every hop's host is resolved first and refused when any address
  is private, loopback, link-local, reserved or multicast; the address actually connected to is checked again, so a
  DNS answer that changes between the check and the connection cannot reach a local service. ``allow_loopback=True``
  (tests only) admits loopback addresses and nothing else.
* One request in flight per host and at least ``min_interval`` seconds (default 1.0) between the end of one request
  and the start of the next to the same host.
* At most 3 attempts for 429, 5xx, timeouts and connection errors, with exponential backoff or the server's
  ``Retry-After`` (seconds or HTTP date) when it is at most 60 s; a longer ``Retry-After`` stops retrying.
  401/403 and challenge pages are never retried: they raise :class:`BlockedError` / :class:`ChallengeError`.
* Responses are streamed and capped at 64 MiB. Cookies are never stored, environment proxies/.netrc are ignored and
  ``Authorization``/``Cookie`` request headers are refused; URLs and messages in errors and logs are redacted.

Downloads go to a unique hidden ``.part`` file inside the destination directory, never to the final name; use
:func:`lib.media.finalize_download` to decode/verify and atomically move it into place.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import ipaddress
import json
import logging
import math
import os
import re
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ReadTimeoutError

from . import manifest, paths
from .checks import redact_text, redact_url
from .schema import USER_AGENT, failure_record

LOG = logging.getLogger("dynamix.net")

DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = (10.0, 30.0)  # (connect, read) seconds
MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
MAX_RETRY_AFTER = 60.0
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
FORBIDDEN_REQUEST_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})
CHUNK_BYTES = 64 * 1024
CHALLENGE_SNIFF_BYTES = 64 * 1024
# Markers of an interstitial bot check. The strict set is specific enough for any status; the loose set only for 403.
_CHALLENGE_STRICT = re.compile(rb"<title>\s*just a moment|cf-chl|cf_chl|challenge-platform", re.IGNORECASE)
_CHALLENGE_LOOSE = re.compile(rb"challenge|captcha|cf-chl", re.IGNORECASE)
_CHARSET = re.compile(r"charset=[\"']?([A-Za-z0-9._:-]+)", re.IGNORECASE)


# --- Errors -----------------------------------------------------------------------------------------------------------

class FetchError(Exception):
    """Base class; attributes mirror the Failure contract (``error_class``, ``http_status``, ``retriable``,
    ``manual``, ``attempts``). ``url`` and ``message`` are already redacted."""

    error_class = "other"
    default_retriable = False
    default_manual = False

    def __init__(self, message: str, *, url: str | None = None, http_status: int | None = None,
                 retriable: bool | None = None, manual: bool | None = None, retry_after: float | None = None):
        self.message = redact_text(message)
        self.url = redact_url(url) if url else None
        super().__init__(f"{self.message} [{self.url}]" if self.url else self.message)
        self.http_status = http_status
        self.retriable = self.default_retriable if retriable is None else retriable
        self.manual = self.default_manual if manual is None else manual
        self.retry_after = retry_after
        self.attempts = 1

    def failure(self, *, source_id: str, candidate_id: str, phase: str = "download", attempted_at: str | None = None,
                url: str | None = None) -> dict:
        """A validated Failure record for this error (``url`` defaults to the URL that failed)."""
        target = url or self.url
        if not target:
            raise ValueError("a Failure record needs the URL that was attempted")
        return failure_record(source_id=source_id, candidate_id=candidate_id, phase=phase, url=target,
                              attempted_at=attempted_at or manifest.now_utc(), attempts=self.attempts,
                              error_class=self.error_class, http_status=self.http_status, retriable=self.retriable,
                              manual=self.manual, message=self.message)


class HostNotAllowedError(FetchError):
    """Scheme, credentials, host or resolved address outside the allowlist/public internet."""
    error_class = "host_not_allowed"


class BlockedError(FetchError):
    """401/403 or another access refusal; never retried and flagged for manual follow-up."""
    error_class = "blocked"
    default_manual = True


class ChallengeError(BlockedError):
    """A bot-check/captcha interstitial (``cf-mitigated: challenge``, ``Just a moment...``, ``cf-chl`` markers)."""
    error_class = "challenge"


class HttpError(FetchError):
    """Any other unsuccessful status; ``error_class`` is ``http_4xx``/``http_5xx`` (``other`` for 1xx-3xx)."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, **kwargs)
        status = self.http_status or 0
        self.error_class = "http_4xx" if 400 <= status <= 499 else "http_5xx" if 500 <= status <= 599 else "other"


class SizeLimitError(FetchError):
    error_class = "size_limit"


class TimeoutFetchError(FetchError):
    error_class = "timeout"
    default_retriable = True


class ConnectionFetchError(FetchError):
    """DNS failure, refused/reset connection or broken transfer; retried."""
    error_class = "other"
    default_retriable = True


# --- Results ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DownloadResult:
    temp_path: Path  # hidden ``.part`` file inside dest_dir; never the final asset name
    final_url: str  # URL of the response that delivered the bytes (after redirects; not redacted)
    status: int
    content_type: str | None
    bytes: int
    sha256: str
    sha1: str
    headers: Mapping[str, str] = field(default_factory=dict)  # lowercase names, Set-Cookie removed


# --- Helpers ----------------------------------------------------------------------------------------------------------

def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds requested by a ``Retry-After`` header (delta-seconds or HTTP date), or None when absent/unparseable."""
    if not value or not value.strip():
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            when = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    return None if math.isnan(seconds) else max(seconds, 0.0)


def retry_delay(error: FetchError, attempt: int, *, max_retry_after: float = MAX_RETRY_AFTER) -> float | None:
    """Seconds to wait before attempt ``attempt + 1``; None means do not retry (not retriable, or the server asked
    for longer than ``max_retry_after``). Without Retry-After: 1, 2, 4, ... seconds, capped at 8 (as in DEEMO)."""
    if not error.retriable:
        return None
    if error.retry_after is not None:
        return None if error.retry_after > max_retry_after else error.retry_after
    return float(min(2 ** (attempt - 1), 8))


def normalize_host(host: str) -> str:
    """Lowercase, trailing dot removed, IDNA (punycode) for non-ASCII names."""
    value = host.strip().lower().rstrip(".")
    if not value:
        raise ValueError("empty host")
    if not value.isascii():
        value = value.encode("idna").decode("ascii")
    return value


def _system_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


def _public_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items() if name.lower() != "set-cookie"}


def _is_challenge(status: int, headers: Mapping[str, str], head: bytes) -> bool:
    if headers.get("cf-mitigated", "").strip().lower() == "challenge":
        return True
    if status >= 400 and _CHALLENGE_STRICT.search(head):
        return True
    return status == 403 and bool(_CHALLENGE_LOOSE.search(head))


class _PeerCheckMixin:
    """urllib3 connection that verifies the address it actually connected to (``peer_check(ip, host)``)."""

    peer_check: Callable[[str, str], None] | None = None

    def _new_conn(self):  # type: ignore[no-untyped-def]
        sock = super()._new_conn()  # type: ignore[misc]
        check = type(self).peer_check
        if check is not None:
            try:
                check(sock.getpeername()[0], self.host)  # type: ignore[attr-defined]
            except BaseException:
                sock.close()
                raise
        return sock


def _guarded_adapter(check: Callable[[str, str], None]) -> HTTPAdapter:
    connection = type("GuardedHTTPConnection", (_PeerCheckMixin, HTTPConnection), {"peer_check": staticmethod(check)})
    secure = type("GuardedHTTPSConnection", (_PeerCheckMixin, HTTPSConnection), {"peer_check": staticmethod(check)})
    pools = {"http": type("GuardedHTTPConnectionPool", (HTTPConnectionPool,), {"ConnectionCls": connection}),
             "https": type("GuardedHTTPSConnectionPool", (HTTPSConnectionPool,), {"ConnectionCls": secure})}

    class GuardedAdapter(HTTPAdapter):
        def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = dict(pools)

    return GuardedAdapter()


class _HostState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ready_at = float("-inf")


# --- Client -----------------------------------------------------------------------------------------------------------

class PoliteClient:
    """Allowlisted, rate-limited GET client. Thread-safe: requests to different hosts may run concurrently."""

    def __init__(self, allowed_hosts: Iterable[str], *, min_interval: float = DEFAULT_MIN_INTERVAL,
                 max_bytes: int = DEFAULT_MAX_BYTES, timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
                 max_attempts: int = MAX_ATTEMPTS, max_redirects: int = MAX_REDIRECTS,
                 max_retry_after: float = MAX_RETRY_AFTER, allow_loopback: bool = False, user_agent: str = USER_AGENT,
                 proxies: Mapping[str, str] | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, resolver: Callable[[str], list[str]] | None = None):
        entries = [normalize_host(host) for host in allowed_hosts]
        if not entries:
            raise ValueError("allowed_hosts must name at least one host")
        self._exact = {entry for entry in entries if not entry.startswith(".")}
        self._suffixes = tuple(entry for entry in entries if entry.startswith("."))
        self._min_interval, self._max_bytes, self._timeout = float(min_interval), int(max_bytes), timeout
        self._max_attempts, self._max_redirects = max(1, int(max_attempts)), int(max_redirects)
        self._max_retry_after, self._allow_loopback = float(max_retry_after), bool(allow_loopback)
        self._sleep, self._clock, self._resolver = sleep, clock, resolver or _system_resolver
        self._hosts: dict[str, _HostState] = {}
        self._hosts_lock = threading.Lock()
        session = requests.Session()
        session.trust_env = False  # no environment proxies or .netrc credentials
        session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))  # store no cookies
        session.headers["User-Agent"] = user_agent
        self._proxied = bool(proxies)
        if proxies:
            session.proxies.update(proxies)
        else:
            adapter = _guarded_adapter(self._check_peer)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
        self._session = session

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API --

    def get_json(self, url: str, params: Mapping[str, Any] | None = None, *,
                 headers: Mapping[str, str] | None = None) -> tuple[Any, str]:
        """GET and parse JSON. Returns ``(data, final_url)``."""
        def consume(response: requests.Response, final_url: str) -> tuple[Any, str]:
            body = b"".join(self._iter_body(response, final_url))
            try:
                return json.loads(body), final_url
            except ValueError as error:
                raise FetchError(f"response is not valid JSON: {error}", url=final_url,
                                 http_status=response.status_code) from None
        return self._fetch(self._with_params(url, params), {"Accept": "application/json", **(headers or {})}, consume)

    def get_text(self, url: str, *, headers: Mapping[str, str] | None = None) -> tuple[str, str, dict[str, str]]:
        """GET a text/HTML page. Returns ``(text, final_url, headers)``; a 200 challenge page raises ChallengeError."""
        def consume(response: requests.Response, final_url: str) -> tuple[str, str, dict[str, str]]:
            body = b"".join(self._iter_body(response, final_url))
            if _CHALLENGE_STRICT.search(body[:CHALLENGE_SNIFF_BYTES]):
                raise ChallengeError("challenge page served with HTTP 200", url=final_url,
                                     http_status=response.status_code)
            declared = _CHARSET.search(response.headers.get("Content-Type", ""))
            try:  # a declared charset wins; otherwise UTF-8 (not requests' ISO-8859-1 default for text/*)
                text = body.decode(declared.group(1) if declared else "utf-8", errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
            return text, final_url, _public_headers(response.headers)
        return self._fetch(url, dict(headers or {}), consume)

    def download(self, url: str, dest_dir: str | os.PathLike[str], stem: str, *,
                 headers: Mapping[str, str] | None = None) -> DownloadResult:
        """Stream the body into a unique ``.<slug(stem)>.*.part`` file inside ``dest_dir`` and hash it.

        The temporary file is deleted on any failure (including a failed attempt that is then retried).
        """
        directory = Path(dest_dir)
        directory.mkdir(parents=True, exist_ok=True)

        def consume(response: requests.Response, final_url: str) -> DownloadResult:
            descriptor, name = tempfile.mkstemp(prefix=f".{paths.slug(stem)}.", suffix=".part", dir=directory)
            temp = Path(name)
            try:
                sha256, sha1, size = hashlib.sha256(), hashlib.sha1(), 0
                with os.fdopen(descriptor, "wb") as handle:
                    for chunk in self._iter_body(response, final_url):
                        handle.write(chunk)
                        sha256.update(chunk)
                        sha1.update(chunk)
                        size += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                return DownloadResult(temp, final_url, response.status_code, response.headers.get("Content-Type"),
                                      size, sha256.hexdigest(), sha1.hexdigest(), _public_headers(response.headers))
            except BaseException:
                temp.unlink(missing_ok=True)
                raise
        return self._fetch(url, dict(headers or {}), consume)

    def is_allowed_host(self, host: str) -> bool:
        value = normalize_host(host)
        return value in self._exact or any(value.endswith(suffix) for suffix in self._suffixes)

    # -- internals --

    @staticmethod
    def _with_params(url: str, params: Mapping[str, Any] | None) -> str:
        if not params:
            return url
        try:
            prepared = requests.Request("GET", url, params=params).prepare().url
        except (requests.RequestException, ValueError) as error:
            raise HostNotAllowedError(f"invalid URL: {type(error).__name__}", url=url) from None
        return prepared or url

    def _fetch(self, url: str, headers: Mapping[str, str], consume: Callable[[requests.Response, str], Any]) -> Any:
        forbidden = sorted(name for name in headers if name.lower() in FORBIDDEN_REQUEST_HEADERS)
        if forbidden:
            raise ValueError(f"credential headers are not allowed: {forbidden}")
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._attempt(url, headers, consume)
            except FetchError as error:
                error.attempts = attempt
                delay = retry_delay(error, attempt, max_retry_after=self._max_retry_after)
                if delay is None or attempt == self._max_attempts:
                    raise
                LOG.info("retrying %s in %.1f s after %s (attempt %d)", redact_url(url), delay, error.error_class,
                         attempt)
                self._sleep(delay)
        raise AssertionError("unreachable")

    def _attempt(self, url: str, headers: Mapping[str, str], consume: Callable[[requests.Response, str], Any]) -> Any:
        current = url
        for _hop in range(self._max_redirects + 1):
            with self._exchange(current, headers) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise HttpError("redirect without Location", url=current, http_status=response.status_code)
                    target = urljoin(current, location)
                    if urlsplit(current).scheme == "https" and urlsplit(target).scheme == "http":
                        raise HostNotAllowedError(f"redirect downgrades https to http: {target}", url=current)
                    current = target
                    continue
                self._raise_for_status(response, current)
                try:
                    return consume(response, current)
                except requests.RequestException as error:
                    raise self._transport_error(error, current) from None
        raise HttpError(f"more than {self._max_redirects} redirects", url=url)

    @contextmanager
    def _exchange(self, url: str, headers: Mapping[str, str]) -> Iterator[requests.Response]:
        """One request/response with the host's lock held until the body is consumed or discarded."""
        host = self._check_url(url)
        state = self._state(host)
        with state.lock:
            wait = state.ready_at - self._clock()
            if wait > 0:
                self._sleep(wait)
            response = None
            try:
                if not self._proxied:
                    self._check_resolution(host, url)
                try:
                    response = self._session.get(url, headers=dict(headers), allow_redirects=False, stream=True,
                                                 timeout=self._timeout)
                except requests.RequestException as error:
                    raise self._transport_error(error, url) from None
                except HostNotAllowedError as error:  # raised by the connected-address check inside urllib3
                    raise HostNotAllowedError(error.message, url=url) from None
                LOG.debug("GET %s -> %s", redact_url(url), response.status_code)
                yield response
            finally:
                if response is not None:
                    response.close()
                state.ready_at = self._clock() + self._min_interval

    def _state(self, host: str) -> _HostState:
        with self._hosts_lock:
            return self._hosts.setdefault(host, _HostState())

    def _check_url(self, url: str) -> str:
        try:
            parts = urlsplit(url)
            parts.port  # noqa: B018 - raises ValueError for an invalid port
            host = normalize_host(parts.hostname or "")
        except ValueError as error:  # includes UnicodeError from IDNA encoding
            raise HostNotAllowedError(f"invalid URL: {error}", url=url) from None
        if parts.scheme not in ("http", "https"):
            raise HostNotAllowedError(f"scheme {parts.scheme!r} is not allowed", url=url)
        if "@" in parts.netloc:
            raise HostNotAllowedError("URLs with credentials are not allowed", url=url)
        if not self.is_allowed_host(host):
            raise HostNotAllowedError(f"host {host!r} is not in the allowlist", url=url)
        return host

    def _address_allowed(self, address: str) -> bool:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip.is_loopback:
            return self._allow_loopback
        return ip.is_global and not ip.is_multicast

    def _check_resolution(self, host: str, url: str) -> None:
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                addresses = list(self._resolver(host))
            except OSError as error:
                raise ConnectionFetchError(f"cannot resolve {host}: {error}", url=url) from None
        if not addresses:
            raise ConnectionFetchError(f"no address for {host}", url=url)
        refused = [address for address in addresses if not self._address_allowed(address)]
        if refused:
            raise HostNotAllowedError(f"{host} resolves to a non-public address {refused[0]}", url=url)

    def _check_peer(self, address: str, host: str) -> None:
        if not self._address_allowed(address):
            raise HostNotAllowedError(f"connection to {host} reached non-public address {address}")

    def _raise_for_status(self, response: requests.Response, url: str) -> None:
        status = response.status_code
        head = b""
        if status >= 400:
            try:
                head = response.raw.read(CHALLENGE_SNIFF_BYTES, decode_content=True) or b""
            except Exception:  # noqa: BLE001 - the status decides; the body is only a hint
                head = b""
        if _is_challenge(status, response.headers, head):
            raise ChallengeError(f"challenge page (HTTP {status})", url=url, http_status=status)
        if status == 200:
            return
        if status in (401, 403):
            raise BlockedError(f"access refused (HTTP {status})", url=url, http_status=status)
        if status == 429 or 500 <= status <= 599:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            note = "" if retry_after is None or retry_after <= self._max_retry_after else (
                f"; Retry-After {retry_after:.0f} s exceeds {self._max_retry_after:.0f} s, not retried")
            raise HttpError(f"HTTP {status}{note}", url=url, http_status=status, retriable=True,
                            retry_after=retry_after)
        raise HttpError(f"HTTP {status}", url=url, http_status=status)

    def _iter_body(self, response: requests.Response, url: str) -> Iterator[bytes]:
        declared = response.headers.get("Content-Length")
        if declared and declared.strip().isdigit() and int(declared) > self._max_bytes:
            raise SizeLimitError(f"Content-Length {int(declared)} exceeds {self._max_bytes} bytes", url=url,
                                 http_status=response.status_code)
        total = 0
        for chunk in response.iter_content(CHUNK_BYTES):
            total += len(chunk)
            if total > self._max_bytes:
                raise SizeLimitError(f"response exceeds {self._max_bytes} bytes", url=url,
                                     http_status=response.status_code)
            yield chunk

    @staticmethod
    def _transport_error(error: requests.RequestException, url: str) -> FetchError:
        timed_out = isinstance(error, requests.Timeout) or any(
            isinstance(arg, (ReadTimeoutError, socket.timeout, TimeoutError)) for arg in error.args)
        if timed_out:
            return TimeoutFetchError(f"timed out: {type(error).__name__}", url=url)
        return ConnectionFetchError(f"{type(error).__name__}: {error}", url=url)
