"""Offline tests for scripts/lib/net.py against scripted loopback HTTP servers (127.0.0.1 only).

Retry/backoff sleeps are recorded by an injected ``sleep`` instead of waiting, except in the spacing test, which
uses real time with short intervals. No test resolves or contacts a non-loopback host.
"""

import email.utils
import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import net, schema  # noqa: E402


class Script:
    """Per-path response sequences (the last one repeats) plus request bookkeeping."""

    def __init__(self):
        self.routes, self.hits, self.requests, self.arrivals = {}, {}, [], []
        self.lock = threading.Lock()
        self.active = self.max_active = 0

    def add(self, path, *responses):
        self.routes[path] = list(responses)

    def take(self, path, headers):
        with self.lock:
            self.hits[path] = self.hits.get(path, 0) + 1
            self.requests.append((path, headers))
            self.arrivals.append(time.monotonic())
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            responses = self.routes.get(path, [{"status": 404, "body": b"missing"}])
            return responses[min(self.hits[path], len(responses)) - 1]

    def done(self):
        with self.lock:
            self.active -= 1


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        script = self.server.script
        response = script.take(urlsplit(self.path).path, {k.lower(): v for k, v in self.headers.items()})
        try:
            time.sleep(response.get("delay", 0))
            body = response.get("body", b"")
            self.send_response(response.get("status", 200))
            for name, value in response.get("headers", {}).items():
                self.send_header(name, value)
            if response.get("chunked"):
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for start in range(0, len(body), 1024):
                    piece = body[start:start + 1024]
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
                self.wfile.write(b"0\r\n\r\n")
            else:
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            script.done()

    def log_message(self, *args):
        pass


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):  # clients abort on purpose in tests
            super().handle_error(request, client_address)


def json_body(data):
    return {"status": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(data).encode()}


class NetTestCase(unittest.TestCase):
    def setUp(self):
        self.script = Script()
        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.server.script = self.script
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.sleeps = []
        self.temp = tempfile.TemporaryDirectory()
        self.dest = Path(self.temp.name) / "assets/public/wikis/fandom"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def url(self, path, host="127.0.0.1"):
        return f"http://{host}:{self.port}{path}"

    def client(self, **options):
        settings = {"allow_loopback": True, "min_interval": 0, "sleep": self.sleeps.append}
        settings.update(options)
        client = net.PoliteClient(settings.pop("hosts", ["127.0.0.1"]), **settings)
        self.addCleanup(client.close)
        return client

    def dest_files(self):
        return sorted(path.name for path in self.dest.iterdir()) if self.dest.exists() else []


class SuccessTests(NetTestCase):
    def test_get_json_sends_user_agent_and_no_credentials(self):
        self.script.add("/api", json_body({"ok": 1}))
        data, final_url = self.client().get_json(self.url("/api"), {"action": "query", "list": "allimages"})
        self.assertEqual(data, {"ok": 1})
        self.assertEqual(urlsplit(final_url).query, "action=query&list=allimages")
        headers = self.script.requests[0][1]
        self.assertEqual(headers["user-agent"], schema.USER_AGENT)
        self.assertNotIn("cookie", headers)
        self.assertNotIn("authorization", headers)

    def test_get_text_uses_declared_charset(self):
        self.script.add("/page", {"status": 200, "headers": {"Content-Type": "text/html; charset=shift_jis"},
                                  "body": "交差して歪む過去と未来".encode("shift_jis")})
        text, final_url, headers = self.client().get_text(self.url("/page"))
        self.assertEqual(text, "交差して歪む過去と未来")
        self.assertTrue(final_url.endswith("/page"))
        self.assertEqual(headers["content-type"], "text/html; charset=shift_jis")

    def test_redirect_within_allowlist_reports_final_url(self):
        self.script.add("/old", {"status": 302, "headers": {"Location": "/new"}})
        self.script.add("/new", json_body([1]))
        data, final_url = self.client().get_json(self.url("/old"))
        self.assertEqual((data, urlsplit(final_url).path), ([1], "/new"))

    def test_download_writes_hidden_temp_file_only(self):
        payload = bytes(range(256)) * 40
        self.script.add("/x.png", {"status": 200, "headers": {"Content-Type": "image/png", "Set-Cookie": "a=b"},
                                   "body": payload})
        result = self.client().download(self.url("/x.png"), self.dest, "Cosmic Pulse")
        self.assertEqual(result.temp_path.parent, self.dest)
        self.assertTrue(result.temp_path.name.startswith(".Cosmic-Pulse."))
        self.assertTrue(result.temp_path.name.endswith(".part"))
        self.assertEqual(self.dest_files(), [result.temp_path.name])
        self.assertEqual(result.temp_path.read_bytes(), payload)
        self.assertEqual((result.bytes, result.sha256, result.sha1),
                         (len(payload), hashlib.sha256(payload).hexdigest(), hashlib.sha1(payload).hexdigest()))
        self.assertEqual((result.status, result.content_type), (200, "image/png"))
        self.assertNotIn("set-cookie", result.headers)

    def test_cookies_are_never_stored_or_sent(self):
        self.script.add("/set", {"status": 200, "headers": {"Set-Cookie": "session=abc; Path=/"}, "body": b"{}"})
        client = self.client()
        client.get_json(self.url("/set"))
        client.get_json(self.url("/set"))
        self.assertNotIn("cookie", self.script.requests[1][1])

    def test_credential_headers_refused(self):
        with self.assertRaises(ValueError):
            self.client().get_json(self.url("/api"), headers={"Authorization": "Bearer x"})
        with self.assertRaises(ValueError):
            self.client().download(self.url("/api"), self.dest, "x", headers={"Cookie": "a=b"})
        self.assertEqual(self.script.hits, {})


class RetryTests(NetTestCase):
    def test_retry_after_seconds_then_success(self):
        self.script.add("/api", {"status": 429, "headers": {"Retry-After": "2"}}, json_body({"ok": True}))
        data, _ = self.client().get_json(self.url("/api"))
        self.assertEqual(data, {"ok": True})
        self.assertEqual(self.script.hits["/api"], 2)
        self.assertEqual(self.sleeps, [2.0])

    def test_retry_after_http_date(self):
        when = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10), usegmt=True)
        self.script.add("/api", {"status": 503, "headers": {"Retry-After": when}}, json_body(1))
        self.client().get_json(self.url("/api"))
        self.assertEqual(len(self.sleeps), 1)
        self.assertTrue(7 <= self.sleeps[0] <= 10, self.sleeps)

    def test_server_errors_retry_three_times_with_backoff(self):
        self.script.add("/api", {"status": 503, "body": b"busy"})
        with self.assertRaises(net.HttpError) as caught:
            self.client().get_json(self.url("/api"))
        error = caught.exception
        self.assertEqual((error.error_class, error.http_status, error.retriable, error.attempts),
                         ("http_5xx", 503, True, 3))
        self.assertEqual(self.script.hits["/api"], 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])
        record = error.failure(source_id="wikis:fandom", candidate_id="wikis:fandom:File:X.png")
        self.assertEqual((record["attempts"], record["error_class"]), (3, "http_5xx"))

    def test_long_retry_after_is_not_retried(self):
        self.script.add("/api", {"status": 429, "headers": {"Retry-After": "120"}})
        with self.assertRaises(net.HttpError) as caught:
            self.client().get_json(self.url("/api"))
        self.assertEqual(self.script.hits["/api"], 1)
        self.assertTrue(caught.exception.retriable)
        self.assertIn("not retried", caught.exception.message)
        self.assertEqual(self.sleeps, [])

    def test_failed_attempts_leave_no_temp_files(self):
        self.script.add("/x.png", {"status": 502})
        with self.assertRaises(net.HttpError):
            self.client().download(self.url("/x.png"), self.dest, "x")
        self.assertEqual(self.dest_files(), [])

    def test_timeouts_are_retried(self):
        self.script.add("/slow", {"status": 200, "body": b"{}", "delay": 1.0})
        with self.assertRaises(net.TimeoutFetchError) as caught:
            self.client(timeout=0.2).get_json(self.url("/slow"))
        self.assertEqual((caught.exception.error_class, caught.exception.attempts), ("timeout", 3))
        self.assertTrue(caught.exception.retriable)

    def test_connection_refused_is_retried(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        with self.assertRaises(net.ConnectionFetchError) as caught:
            self.client().get_json(f"http://127.0.0.1:{closed_port}/x")
        self.assertEqual((caught.exception.error_class, caught.exception.attempts), ("other", 3))

    def test_invalid_json_is_not_retried(self):
        self.script.add("/api", {"status": 200, "body": b"<html>"})
        with self.assertRaises(net.FetchError) as caught:
            self.client().get_json(self.url("/api"))
        self.assertEqual((caught.exception.error_class, self.script.hits["/api"]), ("other", 1))

    def test_parse_retry_after(self):
        now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(net.parse_retry_after(" 2.5 ", now=now), 2.5)
        self.assertEqual(net.parse_retry_after("Sat, 10 Oct 2026 12:00:30 GMT", now=now), 30.0)
        self.assertEqual(net.parse_retry_after("Sat, 10 Oct 2026 11:00:00 GMT", now=now), 0.0)
        for value in (None, "", "soon", "nan"):
            self.assertIsNone(net.parse_retry_after(value, now=now))


class BlockTests(NetTestCase):
    def test_forbidden_and_unauthorized_are_blocked_without_retry(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.script.add(f"/{status}", {"status": status, "body": b"denied"})
                with self.assertRaises(net.BlockedError) as caught:
                    self.client().get_json(self.url(f"/{status}"))
                error = caught.exception
                self.assertNotIsInstance(error, net.ChallengeError)
                self.assertEqual((error.error_class, error.retriable, error.manual, error.http_status),
                                 ("blocked", False, True, status))
                self.assertEqual(self.script.hits[f"/{status}"], 1)
        self.assertEqual(self.sleeps, [])

    def test_challenge_pages(self):
        self.script.add("/marker", {"status": 403, "body": b"<html>Please complete the captcha</html>"})
        self.script.add("/title", {"status": 503, "body": b"<html><title>Just a moment...</title></html>"})
        self.script.add("/header", {"status": 503, "headers": {"cf-mitigated": "challenge"}})
        self.script.add("/ok-challenge", {"status": 200, "headers": {"Content-Type": "text/html"},
                                          "body": b"<title>Just a moment...</title><script src='/cdn-cgi/cf-chl'>"})
        for path in ("/marker", "/title", "/header"):
            with self.subTest(path=path), self.assertRaises(net.ChallengeError) as caught:
                self.client().get_json(self.url(path))
            self.assertEqual(caught.exception.error_class, "challenge")
            self.assertEqual(self.script.hits[path], 1)
        with self.assertRaises(net.ChallengeError):
            self.client().get_text(self.url("/ok-challenge"))
        record = caught.exception.failure(source_id="wikis:fandom", candidate_id="wikis:fandom:x")
        self.assertEqual((record["error_class"], record["retriable"], record["manual"]), ("challenge", False, True))

    def test_not_found_is_not_retried(self):
        with self.assertRaises(net.HttpError) as caught:
            self.client().get_json(self.url("/missing"))
        self.assertEqual((caught.exception.error_class, caught.exception.retriable), ("http_4xx", False))
        self.assertEqual(self.script.hits["/missing"], 1)


class BoundaryTests(NetTestCase):
    def test_redirect_to_unlisted_host_is_rejected_before_contact(self):
        self.script.add("/a", {"status": 302, "headers": {"Location": "http://evil.invalid/steal"}})
        with self.assertRaises(net.HostNotAllowedError) as caught:
            self.client().get_json(self.url("/a"))
        self.assertIn("evil.invalid", caught.exception.message)
        self.assertEqual(self.script.hits, {"/a": 1})

    def test_redirect_to_private_address_is_rejected(self):
        self.script.add("/a", {"status": 301, "headers": {"Location": "http://10.0.0.1/x"}})
        with self.assertRaises(net.HostNotAllowedError):
            self.client(hosts=["127.0.0.1", "10.0.0.1"]).get_json(self.url("/a"))

    def test_https_to_http_redirect_is_refused(self):
        """Offline stand-in for TLS: the exchange is stubbed, the redirect policy in _attempt is real."""
        client = self.client(hosts=["secure.example"])
        calls = []

        @contextmanager
        def fake_exchange(url, headers):
            calls.append(url)
            yield types.SimpleNamespace(status_code=302, headers={"Location": "http://secure.example/plain"})

        client._exchange = fake_exchange
        with self.assertRaises(net.HostNotAllowedError) as caught:
            client.get_json("https://secure.example/a")
        self.assertIn("downgrades https to http", caught.exception.message)
        self.assertEqual(calls, ["https://secure.example/a"])

    def test_invalid_urls_are_refused(self):
        client = self.client()
        for url in ("http://127.0.0.1:99999/x", "http:///nohost", "http://\u2603" + "x" * 70 + ".example/"):
            with self.subTest(url=url), self.assertRaises(net.HostNotAllowedError):
                client.get_json(url, {"q": 1})
        self.assertEqual(self.script.hits, {})

    def test_redirect_hop_limit(self):
        self.script.add("/loop", {"status": 302, "headers": {"Location": "/loop"}})
        with self.assertRaises(net.HttpError) as caught:
            self.client().get_json(self.url("/loop"))
        self.assertIn("more than 5 redirects", caught.exception.message)
        self.assertEqual(self.script.hits["/loop"], 6)

    def test_scheme_credentials_and_allowlist(self):
        client = self.client(hosts=["127.0.0.1", ".wikia.nocookie.net", "dynamix.miraheze.org"])
        for url in ("ftp://127.0.0.1/x", "file:///etc/passwd", f"http://user:secretpw@127.0.0.1:{self.port}/x",
                    "http://localhost/x", "http://[::1]/x"):
            with self.subTest(url=url), self.assertRaises(net.HostNotAllowedError) as caught:
                client.get_json(url)
            self.assertNotIn("secretpw", str(caught.exception))
        self.assertTrue(client.is_allowed_host("static.wikia.nocookie.net"))
        self.assertTrue(client.is_allowed_host("Dynamix.Miraheze.org."))
        for host in ("wikia.nocookie.net", "evilwikia.nocookie.net", "miraheze.org", "x.dynamix.miraheze.org"):
            self.assertFalse(client.is_allowed_host(host), host)
        self.assertEqual(self.script.hits, {})
        with self.assertRaises(ValueError):
            net.PoliteClient([])

    def test_loopback_rejected_unless_allowed(self):
        self.script.add("/api", json_body(1))
        with self.assertRaises(net.HostNotAllowedError) as caught:
            self.client(allow_loopback=False).get_json(self.url("/api"))
        self.assertEqual(caught.exception.error_class, "host_not_allowed")
        self.assertEqual(self.script.hits, {})

    def test_private_dns_answer_rejected_before_connecting(self):
        client = self.client(hosts=["internal.invalid"], allow_loopback=False, resolver=lambda host: ["192.168.1.5"])
        with self.assertRaises(net.HostNotAllowedError):
            client.get_json("http://internal.invalid/x")

    def test_connected_address_is_checked_against_rebinding(self):
        """The resolver claims a public address, but the connection actually reaches 127.0.0.1."""
        self.script.add("/api", json_body(1))
        client = self.client(hosts=["localhost"], allow_loopback=False, resolver=lambda host: ["93.184.216.34"])
        with self.assertRaises(net.HostNotAllowedError) as caught:
            client.get_json(self.url("/api", host="localhost"))
        self.assertIn("non-public address", caught.exception.message)
        self.assertEqual(self.script.hits, {})

    def test_size_limit_by_content_length_and_by_stream(self):
        self.script.add("/big", {"status": 200, "body": b"x" * 4096})
        self.script.add("/chunked", {"status": 200, "body": b"y" * 4096, "chunked": True})
        client = self.client(max_bytes=2048)
        for path in ("/big", "/chunked"):
            with self.subTest(path=path), self.assertRaises(net.SizeLimitError) as caught:
                client.download(self.url(path), self.dest, "big")
            self.assertEqual((caught.exception.error_class, caught.exception.retriable), ("size_limit", False))
            self.assertEqual(self.dest_files(), [])
            self.assertEqual(self.script.hits[path], 1)

    def test_error_messages_are_redacted(self):
        self.script.add("/api", {"status": 404})
        with self.assertRaises(net.HttpError) as caught:
            self.client().get_json(self.url("/api") + "?token=topsecret&format=original")
        self.assertNotIn("topsecret", str(caught.exception))
        self.assertIn("format=original", caught.exception.url)


class PolitenessTests(NetTestCase):
    def test_min_interval_between_requests_to_one_host(self):
        self.script.add("/api", json_body(1))
        clock = [100.0]
        client = self.client(min_interval=1.0, clock=lambda: clock[0])
        client.get_json(self.url("/api"))
        client.get_json(self.url("/api"))
        self.assertEqual(self.sleeps, [1.0])

    def test_one_request_in_flight_per_host_with_spacing(self):
        self.script.add("/slow", {"status": 200, "body": b"{}", "delay": 0.05})
        client = self.client(min_interval=0.15, sleep=time.sleep)
        threads = [threading.Thread(target=client.get_json, args=(self.url("/slow"),)) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(self.script.hits["/slow"], 3)
        self.assertEqual(self.script.max_active, 1)
        gaps = [later - earlier for earlier, later in zip(self.script.arrivals, self.script.arrivals[1:], strict=False)]
        self.assertTrue(all(gap >= 0.15 for gap in gaps), gaps)


if __name__ == "__main__":
    unittest.main()
