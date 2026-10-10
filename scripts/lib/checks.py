"""Generic field checkers and credential redaction shared by the schema validators and the network client.

A checker is a function ``value -> None | str`` returning ``None`` when the value is valid and otherwise a short
problem description; :mod:`lib.schema` combines them into the contract's record specifications. Standard library only.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import unquote, urlsplit

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --- Credential hygiene -----------------------------------------------------------------------------------------------

SENSITIVE_QUERY_KEYS = frozenset({
    "access_token", "api_key", "apikey", "auth", "authorization", "client_secret", "code", "cookie", "credential",
    "key", "password", "passwd", "pwd", "refresh_token", "secret", "session", "sessionid", "sid", "sig", "signature",
    "token", "x-amz-credential", "x-amz-security-token", "x-amz-signature",
})
REDACTED = "REDACTED"
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+")
_HEADER_SECRET = re.compile(r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie)(\s*[:=]\s*)[^\r\n]+")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{20,}")


def _redact_pairs(text: str, separator: str) -> str:
    pieces = []
    for piece in text.split(separator):
        name, equals, _value = piece.partition("=")
        if equals and unquote(name).strip().lower() in SENSITIVE_QUERY_KEYS:
            pieces.append(f"{name}={REDACTED}")
        else:
            pieces.append(piece)
    return separator.join(pieces)


def redact_url(url: str) -> str:
    """``url`` without userinfo and with sensitive query/fragment values replaced; unchanged when already clean."""
    if not isinstance(url, str):
        return "<non-string url>"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    netloc = parts.netloc.rpartition("@")[2]
    query = _redact_pairs(parts.query, "&")
    fragment = _redact_pairs(parts.fragment, "&")
    if (netloc, query, fragment) == (parts.netloc, parts.query, parts.fragment):
        return url
    rebuilt = f"{parts.scheme}://{netloc}{parts.path}" if parts.scheme else f"{netloc}{parts.path}"
    if parts.query or "?" in url:
        rebuilt += "?" + query
    if parts.fragment or "#" in url:
        rebuilt += "#" + fragment
    return rebuilt


def redact_text(text: str) -> str:
    """``text`` with every URL redacted and Authorization/Cookie/Set-Cookie header values and bearer tokens removed."""
    text = _URL_IN_TEXT.sub(lambda match: redact_url(match.group(0)), str(text))
    text = _HEADER_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    return _BEARER.sub(lambda match: f"{match.group(1)} {REDACTED}", text)


def contains_credentials(text: str) -> bool:
    return redact_text(text) != text


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
    if not isinstance(value, str) or not value:
        return "must be an http(s) URL"
    if any(char.isspace() or unicodedata.category(char) == "Cc" for char in value):
        return "must not contain whitespace or control characters"
    try:
        parts = urlsplit(value)
    except ValueError:
        return "must be a parseable URL"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "must be an http(s) URL with a host"
    if "@" in parts.netloc:
        return "must not contain credentials (userinfo)"
    if redact_url(value) != value:
        return "must not contain credential-like query parameters (use redact_url)"
    return None


def clean_text(value: Any) -> str | None:
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
