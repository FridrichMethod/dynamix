"""Polite, allowlisted HTTP client shared by every fetcher (network defaults of the frozen CLI contract).

* Explicit host allowlist (exact hosts, or ``.example.com`` for subdomains only), checked for the first URL and for
  every redirect hop; only http/https; URLs with credentials are refused; an https -> http redirect is refused.
  URLs must pass :func:`lib.checks.url_problem` (no ``\\``, whitespace, control or other characters RFC 3986 forbids)
  and the host requests/urllib3 would contact must equal the host that was checked, so parser differences cannot
  smuggle another host past the allowlist.
* Redirects are followed manually (at most 5 hops). Every hop's host is resolved first and refused when any address
  is private, loopback, link-local, reserved or multicast; the address actually connected to is checked again, so a
  DNS answer that changes between the check and the connection cannot reach a local service. ``allow_loopback=True``
  (tests only) admits loopback addresses and nothing else.
* One request in flight per host and at least ``min_interval`` seconds (default 1.0) between the end of one request
  and the start of the next to the same host. Every hop counts, so a host reached only through a redirect is spaced
  too; clients that share a :class:`HostSchedule` (e.g. the per-source clients of one fetcher run) share these per-host
  locks and ready times.
* At most 3 attempts for 429, 5xx, timeouts and connection errors, with exponential backoff or the server's
  ``Retry-After`` (seconds or HTTP date) when it is at most 60 s; a longer ``Retry-After`` stops retrying.
  401/403 and challenge pages are never retried: they raise :class:`BlockedError` / :class:`ChallengeError`.
* Responses are streamed and capped at 64 MiB; each attempt has an overall deadline (default 300 s from the start of
  the request to the end of the body) on top of the per-read timeout, so a trickling server cannot hold a host lock
  indefinitely. Cookies are never stored, environment proxies/.netrc are ignored and ``Authorization``/``Cookie``
  request headers are refused; URLs and messages in errors and logs are redacted, and transport errors carry only
  exception class names and OS error text, never urllib3's URL-bearing messages.
* Bot challenges are recognised only by real interstitial markers (``cf-mitigated: challenge``, ``_cf_chl_opt``,
  ``cf_chl``/``cf-chl``, ``/cdn-cgi/challenge-platform/h/``, or a ``Just a moment`` title on a 403/503). Cloudflare's
  JavaScript Detections script (``/cdn-cgi/challenge-platform/scripts/jsd/``), injected into ordinary pages, is not
  a challenge: such pages keep their real status (and 5xx retries).

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
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import HTTPError as Urllib3Error
from urllib3.exceptions import ReadTimeoutError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError
from urllib3.util import parse_url

from . import checks, manifest, paths
from .checks import redact_text, redact_url, url_problem
from .schema import USER_AGENT, failure_record

LOG = logging.getLogger("dynamix.net")

DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = (10.0, 30.0)  # (connect, read) seconds, per socket operation
DEFAULT_MAX_DURATION = 300.0  # seconds per attempt, request start to end of body
MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
MAX_RETRY_AFTER = 60.0
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
FORBIDDEN_REQUEST_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})
CHUNK_BYTES = 64 * 1024
CHALLENGE_SNIFF_BYTES = 64 * 1024
# Markers that only a real interstitial bot check carries (any status, including 200): Cloudflare's challenge options
# object and cf_chl/cf-chl tokens and element IDs, and the challenge orchestration path /cdn-cgi/challenge-platform/h/.
_CHALLENGE_MARKERS = re.compile(rb"_cf_chl_opt|cf_chl|cf-chl|/cdn-cgi/challenge-platform/h/", re.IGNORECASE)
CHALLENGE_TITLE_STATUSES = frozenset({403, 503})
_CHALLENGE_TITLE = re.compile(rb"<title>\s*just a moment", re.IGNORECASE)
# Cloudflare JavaScript Detections (/cdn-cgi/challenge-platform/scripts/jsd/main.js) is injected into ordinary pages;
# it is removed before the loose 403 wording check so that it cannot turn a plain 403 into a "challenge".
_CHALLENGE_SCRIPT_PATH = re.compile(rb"/cdn-cgi/challenge-platform/scripts/[^\s\"'<>]*", re.IGNORECASE)
_CHALLENGE_LOOSE = re.compile(rb"challenge|captcha", re.IGNORECASE)
NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")  # RFC 6052 well-known prefix (64:ff9b:1::/48 is non-global)
_CHARSET = re.compile(r"charset=[\"']?([A-Za-z0-9._:-]+)", re.IGNORECASE)


# --- Errors -----------------------------------------------------------------------------------------------------------


class FetchError(Exception):
    """Base class; attributes mirror the Failure contract (``error_class``, ``http_status``, ``retriable``,
    ``manual``, ``attempts``). ``url`` and ``message`` are already redacted."""

    error_class = "other"
    default_retriable = False
    default_manual = False

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        http_status: int | None = None,
        retriable: bool | None = None,
        manual: bool | None = None,
        retry_after: float | None = None,
    ):
        self.message = redact_text(message)
        # ``url`` becomes the Failure record's URL (stored-URL rule); str() is for logs (strict rule).
        self.url = redact_url(url, strict=False) if url else None
        super().__init__(f"{self.message} [{redact_url(url)}]" if url else self.message)
        self.http_status = http_status
        self.retriable = self.default_retriable if retriable is None else retriable
        self.manual = self.default_manual if manual is None else manual
        self.retry_after = retry_after
        self.attempts = 1

    def failure(
        self,
        *,
        source_id: str,
        candidate_id: str,
        phase: str = "download",
        attempted_at: str | None = None,
        url: str | None = None,
    ) -> dict:
        """A validated Failure record for this error (``url`` defaults to the URL that failed).

        A refused redirect is reported against the URL that sent it, so ``self.url`` is a valid URL except when the
        caller itself passed a malformed one; then pass ``url=`` (e.g. the candidate's page URL).
        """
        target = url or self.url
        if not target:
            raise ValueError("a Failure record needs the URL that was attempted")
        problem = url_problem(target)
        if problem:
            raise ValueError(
                f"the failed URL cannot be stored ({problem}); pass url= with a valid URL such as the "
                "candidate's page URL"
            )
        return failure_record(
            source_id=source_id,
            candidate_id=candidate_id,
            phase=phase,
            url=target,
            attempted_at=attempted_at or manifest.now_utc(),
            attempts=self.attempts,
            error_class=self.error_class,
            http_status=self.http_status,
            retriable=self.retriable,
            manual=self.manual,
            message=self.message,
        )


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
        except TypeError, ValueError, IndexError:
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
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
    """Lowercase, trailing dot removed, IDNA (punycode) for non-ASCII names (:func:`lib.checks.normalize_host`)."""
    return checks.normalize_host(host)


def _canonical_host(host: str) -> str:
    """Comparable form of a host: compressed IP literal, else :func:`normalize_host` ("" when invalid)."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        return normalize_host(host)
    except ValueError:
        return ""


def _redirect_target(current: str, location: str) -> str:
    """Absolute redirect target. Like requests: a UTF-8 Location that http.client decoded as Latin-1 is re-decoded,
    and characters RFC 3986 forbids are percent-encoded (inside the authority that leaves a ``%``, which the host
    check rejects; a backslash therefore cannot split the authority)."""
    try:
        location = location.encode("latin-1").decode("utf-8")
    except UnicodeEncodeError, UnicodeDecodeError:
        pass
    target = urljoin(current, location)
    try:
        return requests.utils.requote_uri(target)
    except requests.RequestException, ValueError:
        return target


def _system_resolver(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


def _public_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items() if name.lower() != "set-cookie"}


def _is_challenge(status: int, headers: Mapping[str, str], head: bytes) -> bool:
    """Interstitial bot check, judged from the status, headers and the first bytes of the body."""
    if headers.get("cf-mitigated", "").strip().lower() == "challenge":
        return True
    if _CHALLENGE_MARKERS.search(head):
        return True
    if status in CHALLENGE_TITLE_STATUSES and _CHALLENGE_TITLE.search(head):
        return True
    return status == 403 and bool(_CHALLENGE_LOOSE.search(_CHALLENGE_SCRIPT_PATH.sub(b"", head)))


def _is_challenge_body(head: bytes) -> bool:
    """A successful (2xx) response body that is really a challenge page: markup with a real interstitial marker."""
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<") and bool(_CHALLENGE_MARKERS.search(head))


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 address embedded by NAT64 (64:ff9b::/96); None for other IPv6 addresses."""
    if ip in NAT64_PREFIX:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _is_ipv4_compatible(ip: ipaddress.IPv6Address) -> bool:
    """Deprecated IPv4-compatible ``::a.b.c.d`` (not ``::`` or ``::1``)."""
    return int(ip) >> 32 == 0 and int(ip) > 1


def _cause_summary(error: BaseException) -> str:
    """Exception class names along the cause chain plus OS error text, e.g. ``ConnectionError <- MaxRetryError <-
    NewConnectionError <- ConnectionRefusedError [Errno 111] Connection refused``. urllib3/requests messages are
    deliberately left out: they embed the request path and query (``Max retries exceeded with url: /x?token=...``)."""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(names) < 8:
        seen.add(id(current))
        name = type(current).__name__
        if isinstance(current, OSError) and current.errno is not None and current.strerror:
            name += f" [Errno {current.errno}] {current.strerror}"
        names.append(name)
        reason = getattr(current, "reason", None)
        nested = next((arg for arg in getattr(current, "args", ()) if isinstance(arg, BaseException)), None)
        current = next(
            (
                candidate
                for candidate in (
                    reason if isinstance(reason, BaseException) else None,
                    current.__cause__,
                    nested,
                    current.__context__,
                )
                if candidate is not None
            ),
            None,
        )
    return " <- ".join(names)


class _PeerCheckMixin:
    """urllib3 connection that verifies the address it actually connected to (``peer_check(ip, host)``)."""

    peer_check: Callable[[str, str], None] | None = None

    def _new_conn(self) -> socket.socket:
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
    pools = {
        "http": type("GuardedHTTPConnectionPool", (HTTPConnectionPool,), {"ConnectionCls": connection}),
        "https": type("GuardedHTTPSConnectionPool", (HTTPSConnectionPool,), {"ConnectionCls": secure}),
    }

    class GuardedAdapter(HTTPAdapter):
        def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = dict(pools)

    return GuardedAdapter()


class _HostState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ready_at = float("-inf")


class HostSchedule:
    """Per-host request locks and ready times. A client has its own unless one is passed: clients that share a
    schedule keep one request in flight per host and the minimum spacing across all of them, for every host any of
    them contacted (redirect hops included)."""

    def __init__(self) -> None:
        self._hosts: dict[str, _HostState] = {}
        self._lock = threading.Lock()

    def state(self, host: str) -> _HostState:
        with self._lock:
            return self._hosts.setdefault(host, _HostState())


# --- Client -----------------------------------------------------------------------------------------------------------


class PoliteClient:
    """Allowlisted, rate-limited GET client. Thread-safe: requests to different hosts may run concurrently."""

    def __init__(
        self,
        allowed_hosts: Iterable[str],
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
        max_redirects: int = MAX_REDIRECTS,
        max_retry_after: float = MAX_RETRY_AFTER,
        max_duration: float = DEFAULT_MAX_DURATION,
        allow_loopback: bool = False,
        user_agent: str = USER_AGENT,
        proxies: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        resolver: Callable[[str], list[str]] | None = None,
        schedule: HostSchedule | None = None,
    ):
        entries = [normalize_host(host) for host in allowed_hosts]
        if not entries:
            raise ValueError("allowed_hosts must name at least one host")
        self._exact = {entry for entry in entries if not entry.startswith(".")}
        self._suffixes = tuple(entry for entry in entries if entry.startswith("."))
        self._min_interval, self._max_bytes, self._timeout = float(min_interval), int(max_bytes), timeout
        self._max_attempts, self._max_redirects = max(1, int(max_attempts)), int(max_redirects)
        self._max_retry_after, self._allow_loopback = float(max_retry_after), bool(allow_loopback)
        self._max_duration = float(max_duration)
        self._sleep, self._clock, self._resolver = sleep, clock, resolver or _system_resolver
        self._schedule = schedule if schedule is not None else HostSchedule()
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

    def get_json(
        self, url: str, params: Mapping[str, Any] | None = None, *, headers: Mapping[str, str] | None = None
    ) -> tuple[Any, str]:
        """GET and parse JSON. Returns ``(data, final_url)``."""

        def consume(response: requests.Response, final_url: str) -> tuple[Any, str]:
            body = b"".join(self._iter_body(response, final_url))
            try:
                return json.loads(body), final_url
            except ValueError as error:
                if _is_challenge_body(body[:CHALLENGE_SNIFF_BYTES]):
                    raise ChallengeError(
                        f"challenge page served with HTTP {response.status_code}",
                        url=final_url,
                        http_status=response.status_code,
                    ) from None
                raise FetchError(
                    f"response is not valid JSON: {error}", url=final_url, http_status=response.status_code
                ) from None

        return self._fetch(self._with_params(url, params), {"Accept": "application/json", **(headers or {})}, consume)

    def get_text(self, url: str, *, headers: Mapping[str, str] | None = None) -> tuple[str, str, dict[str, str]]:
        """GET a text/HTML page. Returns ``(text, final_url, headers)``; a 200 challenge page raises ChallengeError."""

        def consume(response: requests.Response, final_url: str) -> tuple[str, str, dict[str, str]]:
            body = b"".join(self._iter_body(response, final_url))
            if _CHALLENGE_MARKERS.search(body[:CHALLENGE_SNIFF_BYTES]):
                raise ChallengeError(
                    f"challenge page served with HTTP {response.status_code}",
                    url=final_url,
                    http_status=response.status_code,
                )
            declared = _CHARSET.search(response.headers.get("Content-Type", ""))
            try:  # a declared charset wins; otherwise UTF-8 (not requests' ISO-8859-1 default for text/*)
                text = body.decode(declared.group(1) if declared else "utf-8", errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
            return text, final_url, _public_headers(response.headers)

        return self._fetch(url, dict(headers or {}), consume)

    def download(
        self, url: str, dest_dir: str | os.PathLike[str], stem: str, *, headers: Mapping[str, str] | None = None
    ) -> DownloadResult:
        """Stream the body into a unique ``.<slug(stem)>.*.part`` file inside ``dest_dir`` and hash it.

        The temporary file is deleted on any failure (including a failed attempt that is then retried). A successful
        response whose body is a challenge page (markup with a real interstitial marker) raises ChallengeError instead
        of reaching media decoding as a ``decode_error``.
        """
        directory = Path(dest_dir)
        directory.mkdir(parents=True, exist_ok=True)

        def consume(response: requests.Response, final_url: str) -> DownloadResult:
            descriptor, name = tempfile.mkstemp(prefix=f".{paths.slug(stem)}.", suffix=".part", dir=directory)
            temp = Path(name)
            try:
                sha256, sha1, size, head = hashlib.sha256(), hashlib.sha1(), 0, b""
                with os.fdopen(descriptor, "wb") as handle:
                    for chunk in self._iter_body(response, final_url):
                        if len(head) < CHALLENGE_SNIFF_BYTES:
                            head += chunk[: CHALLENGE_SNIFF_BYTES - len(head)]
                        handle.write(chunk)
                        sha256.update(chunk)
                        sha1.update(chunk)
                        size += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if _is_challenge_body(head):
                    raise ChallengeError(
                        f"challenge page served with HTTP {response.status_code}",
                        url=final_url,
                        http_status=response.status_code,
                    )
                return DownloadResult(
                    temp,
                    final_url,
                    response.status_code,
                    response.headers.get("Content-Type"),
                    size,
                    sha256.hexdigest(),
                    sha1.hexdigest(),
                    _public_headers(response.headers),
                )
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
        problem = url_problem(url)  # before requests re-parses (and re-splits) it
        if problem:
            raise HostNotAllowedError(f"invalid URL: {problem}", url=url)
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
                LOG.info(
                    "retrying %s in %.1f s after %s (attempt %d)", redact_url(url), delay, error.error_class, attempt
                )
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
                    target = _redirect_target(current, location)
                    try:
                        downgrade = urlsplit(current).scheme == "https" and urlsplit(target).scheme == "http"
                    except ValueError:
                        raise HostNotAllowedError("unparseable redirect Location", url=current) from None
                    if downgrade:
                        raise HostNotAllowedError(f"redirect downgrades https to http: {target}", url=current)
                    try:
                        self._check_url(target)
                    except HostNotAllowedError as error:  # reported against the hop that sent the redirect
                        raise HostNotAllowedError(f"redirect refused: {error.message}", url=current) from None
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
                deadline = self._clock() + self._max_duration
                if not self._proxied:
                    self._check_resolution(host, url)
                try:
                    response = self._session.get(
                        url, headers=dict(headers), allow_redirects=False, stream=True, timeout=self._timeout
                    )
                    cast(Any, response).dynamix_deadline = deadline  # read by _body_chunks
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
        return self._schedule.state(host)

    def _check_url(self, url: str) -> str:
        """Normalized host of an acceptable URL; HostNotAllowedError otherwise (before any DNS lookup or request).

        The host is taken from Python's ``urlsplit`` for the allowlist and DNS checks and must equal the host of the
        URL requests actually prepares (urllib3 ``parse_url``), the one the connection goes to.
        """
        try:
            scheme = urlsplit(url).scheme
        except TypeError, ValueError, AttributeError:
            scheme = ""
        if scheme and scheme not in ("http", "https"):
            raise HostNotAllowedError(f"scheme {scheme!r} is not allowed", url=url)
        problem = url_problem(url)
        if problem:
            raise HostNotAllowedError(f"invalid URL: {problem}", url=url)
        host = normalize_host(urlsplit(url).hostname or "")
        if not self.is_allowed_host(host):
            raise HostNotAllowedError(f"host {host!r} is not in the allowlist", url=url)
        contacted = self._contacted_host(url)
        if _canonical_host(contacted) != _canonical_host(host):
            raise HostNotAllowedError(
                f"ambiguous authority: checked host {host!r} but requests would contact {contacted!r}", url=url
            )
        return host

    @staticmethod
    def _contacted_host(url: str) -> str:
        """The host requests/urllib3 would connect to for ``url`` (from the URL requests actually prepares)."""
        try:
            prepared = requests.Request("GET", url).prepare().url or ""
            return (parse_url(prepared).host or "").strip("[]")
        except (requests.RequestException, ValueError) as error:
            raise HostNotAllowedError(f"invalid URL: {type(error).__name__}", url=url) from None

    def _address_allowed(self, address: str) -> bool:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            elif _is_ipv4_compatible(ip):
                return False  # deprecated ::a.b.c.d; never a legitimate public destination
            else:
                embedded = _embedded_ipv4(ip)
                if embedded is not None:  # through a NAT64 translator: judge the IPv4 destination, never loopback
                    return embedded.is_global and not embedded.is_multicast
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
        head = self._read_head(response, url) if status >= 400 else b""
        if _is_challenge(status, response.headers, head):
            raise ChallengeError(f"challenge page (HTTP {status})", url=url, http_status=status)
        if status == 200:
            return
        if status in (401, 403):
            raise BlockedError(f"access refused (HTTP {status})", url=url, http_status=status)
        if status == 429 or 500 <= status <= 599:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            note = (
                ""
                if retry_after is None or retry_after <= self._max_retry_after
                else (f"; Retry-After {retry_after:.0f} s exceeds {self._max_retry_after:.0f} s, not retried")
            )
            raise HttpError(
                f"HTTP {status}{note}", url=url, http_status=status, retriable=True, retry_after=retry_after
            )
        raise HttpError(f"HTTP {status}", url=url, http_status=status)

    def _read_head(self, response: requests.Response, url: str) -> bytes:
        """Up to CHALLENGE_SNIFF_BYTES of an error body (within the deadline); b"" when it cannot be read."""
        head = b""
        try:
            for chunk in self._body_chunks(response, url):
                head += chunk
                if len(head) >= CHALLENGE_SNIFF_BYTES:
                    break
        except FetchError:  # the status decides; the body is only a hint
            pass
        return head[:CHALLENGE_SNIFF_BYTES]

    def _body_chunks(self, response: requests.Response, url: str) -> Iterator[bytes]:
        """Decoded body pieces as they arrive (``read1``), enforcing the attempt's overall deadline between reads.

        ``iter_content`` would block until a full chunk arrived, so a server trickling a byte per read timeout could
        keep one request (and its host lock) alive for hours; with ``read1`` the deadline is exceeded by at most one
        read timeout.
        """
        deadline = getattr(response, "dynamix_deadline", None)
        while True:
            if deadline is not None and self._clock() > deadline:
                raise TimeoutFetchError(
                    f"response not complete within {self._max_duration:.0f} s",
                    url=url,
                    http_status=response.status_code,
                )
            try:
                chunk = response.raw.read1(CHUNK_BYTES, decode_content=True)
            except (ReadTimeoutError, Urllib3TimeoutError, TimeoutError) as error:
                raise TimeoutFetchError(
                    f"timed out reading the body ({_cause_summary(error)})", url=url, http_status=response.status_code
                ) from None
            except (Urllib3Error, OSError) as error:
                raise ConnectionFetchError(
                    f"broken transfer ({_cause_summary(error)})", url=url, http_status=response.status_code
                ) from None
            if not chunk:
                return
            yield chunk

    def _iter_body(self, response: requests.Response, url: str) -> Iterator[bytes]:
        declared = response.headers.get("Content-Length")
        if declared and declared.strip().isdigit() and int(declared) > self._max_bytes:
            raise SizeLimitError(
                f"Content-Length {int(declared)} exceeds {self._max_bytes} bytes",
                url=url,
                http_status=response.status_code,
            )
        total = 0
        for chunk in self._body_chunks(response, url):
            total += len(chunk)
            if total > self._max_bytes:
                raise SizeLimitError(
                    f"response exceeds {self._max_bytes} bytes", url=url, http_status=response.status_code
                )
            yield chunk

    @staticmethod
    def _transport_error(error: requests.RequestException, url: str) -> FetchError:
        """Timeout/connection FetchError whose message names exception classes only (see :func:`_cause_summary`)."""
        timed_out = isinstance(error, requests.Timeout) or any(
            isinstance(arg, (ReadTimeoutError, socket.timeout, TimeoutError)) for arg in error.args
        )
        if timed_out:
            return TimeoutFetchError(f"timed out ({_cause_summary(error)})", url=url)
        return ConnectionFetchError(f"connection failed ({_cause_summary(error)})", url=url)
