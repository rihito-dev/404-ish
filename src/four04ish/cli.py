"""JSON Lines output identifies inputs by position, never by URL."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .checker import Checker, positive


def main(argv=None):
    parser = argparse.ArgumentParser(prog="404-ish", description="404 != dead. Observe cautiously.")
    parser.add_argument("urls", nargs="*", help="public HTTP(S) URLs without queries or fragments")
    parser.add_argument("--input", type=Path, help="UTF-8 file, one URL per nonblank line")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between requests per host")
    parser.add_argument("--timeout", type=float, default=10.0, help="socket timeout in seconds")
    args = parser.parse_args(argv)
    try:
        positive(args.interval)
        positive(args.timeout)
    except ValueError:
        parser.error("interval and timeout must be finite and positive")
    urls = list(args.urls)
    if args.input:
        try:
            urls.extend(line.strip() for line in args.input.read_text(encoding="utf-8").splitlines()
                        if line.strip())
        except (OSError, UnicodeError):
            print("404-ish: unable to read input file", file=sys.stderr)
            return 2
    if not urls:
        parser.error("provide URLs or --input")
    checker = Checker(interval=args.interval, timeout=args.timeout)
    try:
        for index, url in enumerate(urls, 1):
            print(json.dumps({"input_index": index, **asdict(checker.check(url))}))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
    return 0
