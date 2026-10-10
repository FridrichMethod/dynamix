"""Generic field checkers and credential redaction shared by the schema validators and the network client.

A checker is a function ``value -> None | str`` returning ``None`` when the value is valid and otherwise a short
problem description; :mod:`lib.schema` combines them into the contract's record specifications. Standard library only.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import unquote, urlsplit

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --- Credential hygiene -----------------------------------------------------------------------------------------------

# Query/fragment keys whose values are credentials wherever they appear. Stored provenance URLs must not carry them
# (checks.http_url rejects them; redact_url(url, strict=False) replaces their values).
CREDENTIAL_QUERY_KEYS = frozenset({
    "access_token", "api_key", "apikey", "auth_token", "client_secret", "id_token", "passwd", "password", "pwd",
    "refresh_token", "secret", "signature", "token", "x-amz-credential", "x-amz-security-token", "x-amz-signature",
    "x-goog-credential", "x-goog-signature",
})
# Strict set for logs and error/Failure messages: also generic names that often, but not always, carry secrets.
# Stored URLs keep these, because public URLs use them legitimately (e.g. an archived ``image.php?key=12``).
SENSITIVE_QUERY_KEYS = CREDENTIAL_QUERY_KEYS | frozenset({
    "auth", "authorization", "code", "cookie", "credential", "key", "phpsessid", "session", "sessionid", "sid", "sig",
})
REDACTED = "REDACTED"
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+")
_HEADER_SECRET = re.compile(r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie)(\s*[:=]\s*)[^\r\n]+")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{20,}")
# A ``name=value`` pair anywhere in free text: relative paths, bare query strings, ``url: /x?token=...`` fragments.
_PAIR_IN_TEXT = re.compile(r"(?<![\w.%~-])([\w.%~-]+)=([^\s&;#\"'<>]+)")


def _keys(strict: bool) -> frozenset[str]:
    return SENSITIVE_QUERY_KEYS if strict else CREDENTIAL_QUERY_KEYS


def _redact_pairs(text: str, separator: str, keys: frozenset[str]) -> str:
    pieces = []
    for piece in text.split(separator):
        name, equals, value = piece.partition("=")
        if equals and value and unquote(name).strip().lower() in keys:
            pieces.append(f"{name}={REDACTED}")
        else:
            pieces.append(piece)
    return separator.join(pieces)


def redact_url(url: str, *, strict: bool = True) -> str:
    """``url`` without userinfo and with credential query/fragment values replaced; unchanged when already clean.

    ``strict=True`` (logs, messages) uses :data:`SENSITIVE_QUERY_KEYS`; ``strict=False`` (stored provenance URLs,
    Failure ``url``) uses the narrower :data:`CREDENTIAL_QUERY_KEYS`.
    """
    if not isinstance(url, str):
        return "<non-string url>"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    keys = _keys(strict)
    netloc = parts.netloc.rpartition("@")[2]
    query = _redact_pairs(parts.query, "&", keys)
    fragment = _redact_pairs(parts.fragment, "&", keys)
    if (netloc, query, fragment) == (parts.netloc, parts.query, parts.fragment):
        return url
    rebuilt = f"{parts.scheme}://{netloc}{parts.path}" if parts.scheme else f"{netloc}{parts.path}"
    if parts.query or "?" in url:
        rebuilt += "?" + query
    if parts.fragment or "#" in url:
        rebuilt += "#" + fragment
    return rebuilt


def redact_text(text: str, *, strict: bool = True) -> str:
    """``text`` with every URL redacted, credential ``name=value`` pairs anywhere (relative paths, bare queries)
    replaced, and Authorization/Cookie/Set-Cookie header values and bearer tokens removed."""
    keys = _keys(strict)
    text = _URL_IN_TEXT.sub(lambda match: redact_url(match.group(0), strict=strict), str(text))
    text = _PAIR_IN_TEXT.sub(lambda match: f"{match.group(1)}={REDACTED}"
                             if unquote(match.group(1)).lower() in keys else match.group(0), text)
    text = _HEADER_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    return _BEARER.sub(lambda match: f"{match.group(1)} {REDACTED}", text)


def contains_credentials(text: str, *, strict: bool = True) -> bool:
    return redact_text(text, strict=strict) != text


# --- URL syntax -------------------------------------------------------------------------------------------------------

# ASCII characters RFC 3986 never allows in a URI. A backslash is the dangerous one: Python's urlsplit keeps it in the
# host while urllib3/requests (and browsers) end the authority there, so the allowlisted host and the contacted host
# would differ. Non-ASCII characters are allowed (IRIs; requests percent-encodes them, hosts go through IDNA).
URL_FORBIDDEN_ASCII = frozenset(' "<>\\^`{|}')
_HOST_LABELS = re.compile(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)*$")


def normalize_host(host: str) -> str:
    """Lowercase, trailing dot removed, IDNA (punycode) for non-ASCII names; ValueError when empty or not encodable."""
    value = host.strip().lower().rstrip(".")
    if not value:
        raise ValueError("empty host")
    if not value.isascii():
        value = value.encode("idna").decode("ascii")  # UnicodeError (a ValueError) when not encodable
    return value


def host_problem(host: str) -> str | None:
    """None for an IP literal or a host made of ``[a-z0-9_-]`` labels after :func:`normalize_host`."""
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    try:
        value = normalize_host(host)
    except ValueError as error:
        return f"host is not a valid name ({error})"
    return None if _HOST_LABELS.fullmatch(value) else f"host {value!r} has characters outside [a-z0-9_-] labels"


def url_problem(value: Any) -> str | None:
    """Syntax check shared by stored URLs (:func:`http_url`) and the network client: an absolute http(s) URL with a
    host, no userinfo, no whitespace/control characters, no ASCII characters RFC 3986 forbids (notably ``\\``) and
    an unambiguous host. Returns None when valid, else a short problem description."""
    if not isinstance(value, str) or not value:
        return "must be an http(s) URL"
    if any(char.isspace() or unicodedata.category(char) == "Cc" for char in value):
        return "must not contain whitespace or control characters"
    forbidden = sorted({char for char in value if char in URL_FORBIDDEN_ASCII})
    if forbidden:
        return f"must not contain characters RFC 3986 forbids ({''.join(forbidden)!r})"
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError as error:
        return f"must be a parseable URL ({error})"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "must be an http(s) URL with a host"
    if "@" in parts.netloc:
        return "must not contain credentials (userinfo)"
    return host_problem(parts.hostname)


# --- Checker combinators ----------------------------------------------------------------------------------------------

Checker = Callable[[Any], "str | None"]


def nonempty(value: Any) -> str | None:
    return None if isinstance(value, str) and value.strip() else "must be a non-empty string"


def text(value: Any) -> str | None:
    return None if isinstance(value, str) else "must be a string"


def boolean(value: Any) -> str | None:
    return None if isinstance(value, bool) else "must be true or false"


def tristate(value: Any) -> str | None:
    return None if value is None or isinstance(value, bool) else "must be true, false or null"


def integer(minimum: int, maximum: int | None = None) -> Checker:
    def check(value: Any) -> str | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (
                maximum is not None and value > maximum):
            bound = f" and <= {maximum}" if maximum is not None else ""
            return f"must be an integer >= {minimum}{bound}"
        return None
    return check


def nullable(checker: Checker) -> Checker:
    return lambda value: None if value is None else checker(value)


def one_of(values: Iterable[Any]) -> Checker:
    allowed = tuple(values)

    def check(value: Any) -> str | None:
        if isinstance(value, str) and value in allowed:
            return None
        return f"must be one of {', '.join(map(str, allowed))}; got {value!r}"
    return check


def const(expected: Any) -> Checker:
    return lambda value: None if value == expected and type(value) is type(expected) else f"must be {expected!r}"


def pattern(regex: re.Pattern[str], description: str) -> Checker:
    return lambda value: None if isinstance(value, str) and regex.fullmatch(value) else f"must be {description}"


def timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        return "must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ"
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return "must be a valid UTC timestamp"
    return None


def date(value: Any) -> str | None:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return "must be a date YYYY-MM-DD"
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return "must be a valid date"
    return None


def http_url(value: Any) -> str | None:
    """A stored URL: :func:`url_problem` plus no credential query parameters (:data:`CREDENTIAL_QUERY_KEYS`)."""
    problem = url_problem(value)
    if problem:
        return problem
    if redact_url(value, strict=False) != value:
        return "must not contain credential query parameters (use redact_url(url, strict=False))"
    return None


def clean_text(value: Any) -> str | None:
    """Stored evidence text (quotes, URLs, notes): non-empty and free of credentials (stored-URL rule)."""
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    return "must not contain credentials (use redact_text)" if contains_credentials(value, strict=False) else None


def message_text(value: Any) -> str | None:
    """A generated error message (Failure ``message``, discovery ``errors[].message``): strict redaction rule."""
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    return "must not contain credentials (use redact_text)" if contains_credentials(value) else None


def list_of(checker: Checker, *, unique: bool = True) -> Checker:
    def check(value: Any) -> str | None:
        if not isinstance(value, list):
            return "must be a list"
        for index, item in enumerate(value):
            problem = checker(item)
            if problem:
                return f"[{index}] {problem}"
        if unique:
            keys = [repr(item) for item in value]
            if len(set(keys)) != len(keys):
                return "must not contain duplicates"
        return None
    return check


def obj(required: Mapping[str, Checker], optional: Mapping[str, Checker] | None = None, *,
        extra: bool = False) -> Checker:
    optional = optional or {}

    def check(value: Any) -> str | None:
        if not isinstance(value, dict):
            return "must be an object"
        if not extra:
            unknown = [key for key in value if key not in required and key not in optional]
            if unknown:
                return f"unknown key {unknown[0]!r}"
        for key, checker in required.items():
            if key not in value:
                return f"missing key {key!r}"
            problem = checker(value[key])
            if problem:
                return f"{key}: {problem}"
        for key, checker in optional.items():
            if key in value:
                problem = checker(value[key])
                if problem:
                    return f"{key}: {problem}"
        return None
    return check
