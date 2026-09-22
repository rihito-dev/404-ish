import io
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from email.message import Message

from four04ish.checker import Checker, RateLimiter, Transport, ROBOTS_LIMIT, validate_url
from four04ish.cli import main


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, url, method, timeout):
        self.calls.append((url, method))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Tests(unittest.TestCase):
    def checker(self, *responses):
        self.transport = FakeTransport(*responses)
        self.clock = Clock()
        return Checker(transport=self.transport, limiter=RateLimiter(
            clock=lambda: self.clock.now, sleep=self.clock.sleep))

    def test_statuses_and_timestamps(self):
        for code, label in [(200, "reachable"), (204, "reachable"), (302, "redirected"),
                            (308, "redirected"), (304, "http_other"), (401, "restricted"),
                            (403, "restricted"), (404, "not_found"), (410, "not_found"),
                            (429, "rate_limited"), (405, "head_unsupported"),
                            (501, "head_unsupported"), (503, "server_error")]:
            with self.subTest(code=code):
                checker = self.checker((404, b""), (code, b""))
                result = checker.check("https://example.com/")
                self.assertEqual(result.label, label)
                self.assertEqual(result.http_status, code)
                self.assertIsNotNone(datetime.fromisoformat(result.checked_at).tzinfo)
                self.assertEqual(len(self.transport.calls), 2)
                self.assertEqual(self.transport.calls[-1][1], "HEAD")

    def test_robots_disallow(self):
        result = self.checker((200, b"User-agent: *\nDisallow: /private\n")).check(
            "https://example.com/private")
        self.assertEqual(result.label, "robots_disallowed")
        self.assertEqual(len(self.transport.calls), 1)

    def test_robots_fail_closed(self):
        for response in [(403, b""), (429, b""), (500, b""), (302, b""),
                         (200, b"x" * (ROBOTS_LIMIT + 1)), (200, b"\xff"),
                         (200, b"<html>"), (200, b"User-agent: *\nDisallow: /*?"),
                         (200, b"User-agent: *\nCrawl-delay: 0.5"), URLError("private text")]:
            with self.subTest(response_type=type(response)):
                result = self.checker(response).check("https://example.com/")
                self.assertEqual(result.label, "robots_unavailable")
                self.assertEqual(len(self.transport.calls), 1)

    def test_cache_and_pacing(self):
        checker = self.checker((200, b"User-agent: *\nCrawl-delay: 3\n"),
                               (200, b""), (404, b""))
        checker.check("https://example.com/a")
        checker.check("https://example.com/b")
        self.assertEqual(self.clock.sleeps, [3, 3])
        self.assertEqual(len(self.transport.calls), 3)

    def test_origin_cache_separation(self):
        checker = self.checker((404, b""), (200, b""), (403, b""))
        checker.check("https://example.com/")
        self.assertEqual(checker.check("http://example.com/").label, "robots_unavailable")
        self.assertEqual(len(checker.cache), 2)

    def test_rate_limit_independent_hosts(self):
        clock = Clock()
        limiter = RateLimiter(2, clock=lambda: clock.now, sleep=clock.sleep)
        limiter.wait("example.com")
        limiter.wait("example.org")
        limiter.wait("example.com")
        self.assertEqual(clock.sleeps, [2])

    def test_invalid_urls_make_no_requests(self):
        checker = self.checker()
        for url in ["file:///example", "https://example.com/?secret=value",
                    "https://example.com/#fragment", "https://example.com:bad/",
                    "https://example.com:0/", "https://example.com/\n", "", "https://[bad"]:
            self.assertEqual(checker.check(url).label, "invalid_url")
        self.assertEqual(self.transport.calls, [])

    def test_credentials_rejected(self):
        with self.assertRaises(ValueError):
            validate_url("https://dummy:dummy@example.com/")

    def test_network_error_is_sanitized_and_batch_continues(self):
        checker = self.checker((404, b""), URLError("sensitive path"), (200, b""))
        result = checker.check("https://example.com/")
        self.assertEqual(result.error, "request_failed")
        self.assertNotIn("sensitive", repr(result))
        self.assertEqual(checker.check("https://example.com/").label, "reachable")

    def test_numeric_validation(self):
        for value in [0, -1, float("inf"), float("nan")]:
            with self.assertRaises(ValueError):
                Checker(interval=value)
            with self.assertRaises(ValueError):
                Checker(timeout=value)

    def test_cli_private_output(self):
        checker = self.checker((404, b""), (200, b""))
        with patch("four04ish.cli.Checker", return_value=checker), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(main(["https://example.com/private-path"]), 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["input_index"], 1)
            self.assertNotIn("example.com", out.getvalue())
            self.assertNotIn("private-path", out.getvalue())

    def test_cli_file_and_order(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        checker = self.checker((404, b""), (200, b""), (404, b""))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "urls.txt"
            path.write_text("\nhttps://example.com/b\n", encoding="utf-8")
            with patch("four04ish.cli.Checker", return_value=checker), patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(main(["https://example.com/a", "--input", str(path)]), 0)
                rows = [json.loads(line) for line in out.getvalue().splitlines()]
                self.assertEqual([row["input_index"] for row in rows], [1, 2])
                self.assertEqual([row["label"] for row in rows], ["reachable", "not_found"])

    def test_cli_file_failure_is_sanitized(self):
        with patch("pathlib.Path.read_text", side_effect=OSError("private detail")), patch("sys.stderr", new_callable=io.StringIO) as out:
            self.assertEqual(main(["--input", "dummy.txt"]), 2)
            self.assertNotIn("private detail", out.getvalue())

    def test_request_rate_and_specific_agent(self):
        checker = self.checker((200, b"User-agent: four04ish\nRequest-rate: 1/7\nDisallow: /no\n"), (200, b""))
        self.assertEqual(checker.check("https://example.com/yes").label, "reachable")
        self.assertEqual(self.clock.sleeps, [7])
        self.assertEqual(checker.check("https://example.com/no").label, "robots_disallowed")

    def test_transport_head_does_not_read_and_closes(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.code = 200
        transport = Transport()
        transport.opener.open = MagicMock(return_value=response)
        self.assertEqual(transport.request("https://example.com/", "HEAD", 1), (200, b""))
        response.read.assert_not_called()
        response.__exit__.assert_called_once()
        request = transport.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "HEAD")
        self.assertIsNone(request.get_header("Cookie"))
        self.assertIsNone(request.get_header("Authorization"))

    def test_transport_robot_read_bounded(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.code = 200
        response.read.return_value = b""
        transport = Transport()
        transport.opener.open = MagicMock(return_value=response)
        transport.request("https://example.com/robots.txt", "GET", 1)
        response.read.assert_called_once_with(ROBOTS_LIMIT + 1)

    def test_redirect_not_followed(self):
        from four04ish.checker import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {},
                                                        "https://example.org/"))
        transport = Transport()
        error = HTTPError("https://example.com/", 302, "", Message(), io.BytesIO())
        with patch.object(transport.opener, "open", side_effect=error):
            self.assertEqual(transport.request("https://example.com/", "HEAD", 1), (302, b""))
        self.assertTrue(error.closed)


if __name__ == "__main__":
    unittest.main()
