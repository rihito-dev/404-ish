# 404-ish

[![test](https://github.com/rihito-dev/404-ish/actions/workflows/test.yml/badge.svg)](https://github.com/rihito-dev/404-ish/actions/workflows/test.yml)

**Is it dead? Eh, 404-ish.**

A tiny URL checker that records what happened without pretending to know what
it means. `404 != dead`: a missing page does not prove that a project,
organization, or service no longer exists.

Python 3.10+; no runtime dependencies. MIT licensed.

## Quick start

From a checkout of this repository:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
404-ish https://example.com/
404-ish --input examples/urls.txt --interval 2 --timeout 10
```

These commands make network requests. All included example URLs use reserved
example domains; no third-party dataset or live observation is bundled.
Without installation, use `PYTHONPATH=src python3 -m four04ish --help`.

Output is JSON Lines on stdout, one record per input. A **synthetic** record:

```json
{"input_index": 1, "checked_at": "2026-01-01T00:00:00+00:00", "label": "not_found", "http_status": 404, "redirected": false, "robots": "absent", "error": null}
```

`input_index` is one-based: positional URLs first, then nonblank file lines.
`checked_at` is the UTC completion time for the observation, including skipped
checks. `http_status` is the target HEAD response only; it is null when skipped
or when no response was obtained. Output intentionally contains no URL, path,
redirect destination, response headers, exception message, title, or page text.
Keep the input privately if you need to map results back to URLs.

## What gets requested

1. Validate an ASCII HTTP(S) URL. Credentials, query strings, fragments, control
   characters and backslashes are rejected before any request. Percent-encode
   ordinary non-ASCII paths and use IDNA hostnames yourself.
2. Fetch `/robots.txt` once per origin (scheme + authority), per checker instance.
   Only a 200 rule document or a 404 permits evaluation to continue. Other statuses,
   redirects, network errors, oversized documents and invalid UTF-8 cause a skip.
3. Check the rules for `four04ish/0.1`. Wait at least one second between request
   starts to the same hostname by default, including the robots request. Supported
   `Crawl-delay` and `Request-rate` values can increase the wait.
4. Send one HEAD request. Do not follow redirects, retry, or fall back to GET.

Only robots text is read (at most 128 KiB plus one overflow byte); it stays in
memory for rule parsing and is never output or written to disk. Target page
bodies are not read by the application. The server still receives requests and
may log the client's IP and requested paths. No cookie jar, authentication,
environment proxy, API integration, archive lookup, or site-specific scraping
is used.

## Labels

| Label | Observation |
| --- | --- |
| `reachable` | HEAD returned 2xx; content and relevance are unknown |
| `redirected` | 301, 302, 303, 307 or 308; destination unverified |
| `restricted` | 401, 403, 407 or 451 |
| `not_found` | 404 or 410 at this moment |
| `rate_limited` | 429; no retry |
| `head_unsupported` | 405 or 501; GET might behave differently |
| `server_error` | Other 5xx |
| `http_other` | Any other HTTP response |
| `robots_disallowed` | Parsed rules disallow the path; no target request |
| `robots_unavailable` | Rules could not be safely evaluated; no target request |
| `network_error` | Target request failed; cause intentionally not disclosed |
| `invalid_url` | Input rejected; no request |

`redirected` means a redirect status was observed, even if Location is absent.
`robots` is `not_checked`, `absent`, `parsed`, `disallowed`, or `unavailable`.
Expected network failures do not stop the batch. Exit 0 means records were
produced, not that every URL was reachable; input/usage errors return 2 and
interruption returns 130.

## Deliberate limits

- A small sequential command-line tool for URLs you select; not an untrusted-URL
  web service or an SSRF sandbox. Local/private addresses are not blocked.
- Robots parsing uses Python's `urllib.robotparser`, not a complete RFC 9309
  implementation. Wildcard/end-anchor path rules and non-integer crawl delays
  cause a conservative skip. Other rule precedence follows the standard-library
  parser and can differ from full RFC implementations. Cache lasts for one
  checker instance; create a fresh instance to refresh rules.
- No redirects are followed, including redirects of robots.txt. There is no
  final-destination URL or destination-health claim.
- The timeout is a socket timeout, not a hard total-run deadline; DNS and a slow
  robots transfer can take longer. Large batches and long crawl delays take time.
- Do not submit sensitive paths. Omitting URLs from output reduces disclosure;
  it cannot remove them from shell history, input files or destination logs.
- Instances are intended for sequential use, not concurrent callers.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests use synthetic responses and fake clocks; no public network is needed.
They cover classifications, request suppression, pacing, bounded robots reads,
HEAD body avoidance, redirect suppression, sanitized errors and CLI output.

## Where it came from

Inspired by the `RateLimiter`, `RobotsCache` and separation of observation from
interpretation in [UDC-project-continuity](https://github.com/rihito-dev/UDC-project-continuity).
The reference reviewed was `scripts/collect_udc_projects.py`, blob
`37dbe5efcd1ea352db3abbbaeaac69e4c3914399`. This is a small independent
implementation of those general ideas. No research datasets, personal names,
page text, or UDC-specific collection logic were transferred. The existing MIT
license is retained.
