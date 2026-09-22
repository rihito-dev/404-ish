"""Small sequential checker. No page bodies, automatic redirects, or sessions."""

from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

USER_AGENT = "four04ish/0.1"
ROBOTS_LIMIT = 128 * 1024


def validate_url(url: str) -> str:
    """Reject credentials, query strings and fragments instead of silently changing URLs."""
    if not url or any(ord(c) <= 32 or ord(c) >= 127 for c in url) or "\\" in url:
        raise ValueError("invalid URL")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or "?" in url or "#" in url):
        raise ValueError("unsupported URL")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", "", ""))


def positive(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("expected a finite positive number")
    return value


class RateLimiter:
    """Per-host request starts, including robots; use one checker sequentially."""

    def __init__(self, interval: float = 1.0, *, clock=time.monotonic, sleep=time.sleep):
        self.interval = positive(interval)
        self.clock, self.sleep = clock, sleep
        self.last: dict[str, float] = {}

    def wait(self, host: str, delay: float = 0) -> None:
        interval = max(self.interval, delay)
        if host in self.last:
            remaining = interval - (self.clock() - self.last[host])
            if remaining > 0:
                self.sleep(remaining)
        self.last[host] = self.clock()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Transport:
    """No cookie jar, auth handlers, environment proxies, retries or body reads for HEAD."""

    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, url: str, method: str, timeout: float):
        request = Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.code
            body = b""
            if method == "GET" and status == 200:
                body = response.read(ROBOTS_LIMIT + 1)
            return status, body


@dataclass(frozen=True)
class Observation:
    checked_at: str
    label: str
    http_status: int | None = None
    redirected: bool = False
    robots: str = "not_checked"
    error: str | None = None


class Checker:
    """An in-memory robots cache scoped to this checker; no persistence."""

    def __init__(self, *, interval: float = 1.0, timeout: float = 10.0,
                 transport=None, limiter=None):
        self.timeout = positive(timeout)
        self.transport = transport if transport is not None else Transport()
        self.limiter = limiter if limiter is not None else RateLimiter(interval)
        self.cache: dict[str, tuple[RobotFileParser | None, str]] = {}

    def _robots(self, url: str):
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.cache:
            self.limiter.wait(parsed.hostname)
            try:
                status, body = self.transport.request(origin + "/robots.txt", "GET", self.timeout)
                parser = RobotFileParser()
                if status == 404:
                    parser.parse([])
                    entry = (parser, "absent")
                elif status == 200 and len(body) <= ROBOTS_LIMIT:
                    rules = body.decode("utf-8-sig", errors="strict")
                    # Do not interpret an HTML error page as permission to crawl.
                    if "<" in rules or "\x00" in rules:
                        raise ValueError("invalid robots text")
                    for line in rules.splitlines():
                        key, sep, value = line.partition(":")
                        value = value.split("#", 1)[0].strip()
                        if sep and key.strip().lower() in {"allow", "disallow"}:
                            if "*" in value or "$" in value:
                                raise ValueError("unsupported robots pattern")
                        if sep and key.strip().lower() == "crawl-delay" and not value.isdigit():
                            raise ValueError("unsupported crawl delay")
                    parser.parse(rules.splitlines())
                    entry = (parser, "parsed")
                else:
                    entry = (None, "unavailable")
            except (OSError, URLError, ValueError, HTTPException):
                entry = (None, "unavailable")
            self.cache[origin] = entry
        parser, state = self.cache[origin]
        if parser is None:
            return False, state, 0
        if not parser.can_fetch(USER_AGENT, url):
            return False, "disallowed", 0
        delay = parser.crawl_delay(USER_AGENT) or 0
        rate = parser.request_rate(USER_AGENT)
        if rate and rate.requests > 0:
            delay = max(delay, rate.seconds / rate.requests)
        return True, state, delay

    def check(self, url: str) -> Observation:
        """Observe one URL; expected failures become records without exception text."""
        def result(label, **kwargs):
            return Observation(datetime.now(timezone.utc).isoformat(), label, **kwargs)

        try:
            url = validate_url(url)
        except (ValueError, TypeError):
            return result("invalid_url", error="invalid_url")
        allowed, robots, delay = self._robots(url)
        if not allowed:
            return result("robots_disallowed" if robots == "disallowed" else "robots_unavailable",
                          robots=robots)
        self.limiter.wait(urlsplit(url).hostname, delay)
        try:
            status, _ = self.transport.request(url, "HEAD", self.timeout)
        except (OSError, URLError, ValueError, HTTPException):
            return result("network_error", robots=robots, error="request_failed")
        redirect = status in {301, 302, 303, 307, 308}
        if redirect:
            label = "redirected"
        elif 200 <= status < 300:
            label = "reachable"
        elif status in {401, 403, 407, 451}:
            label = "restricted"
        elif status in {404, 410}:
            label = "not_found"
        elif status == 429:
            label = "rate_limited"
        elif status in {405, 501}:
            label = "head_unsupported"
        elif 500 <= status < 600:
            label = "server_error"
        else:
            label = "http_other"
        return result(label, http_status=status, redirected=redirect, robots=robots)
