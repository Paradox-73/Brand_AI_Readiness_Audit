#!/usr/bin/env python3
"""Serve one of the test fixtures over HTTP so you can audit it.

    python3 tests/serve_fixture.py js-shell-site

Prints the URL and stays up until Ctrl-C. This exists because the eval
instructions used to be a three-line `python -c` with backslash continuations
and single-quoted relative paths, which cannot be pasted into PowerShell - on
the one platform the README explicitly warns about.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fixture_server import FixtureServer  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    available = sorted(d for d in os.listdir(FIXTURES)
                       if os.path.isdir(os.path.join(FIXTURES, d)))
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: serve_fixture.py <fixture>")
        print("fixtures: " + ", ".join(available))
        return 0 if argv else 2
    name = argv[0]
    if name not in available:
        print("no fixture called {!r}. Available: {}".format(name, ", ".join(available)),
              file=sys.stderr)
        return 2

    with FixtureServer(os.path.join(FIXTURES, name)) as server:
        print(server.base_url, flush=True)
        print("serving {} - press Ctrl-C to stop".format(name), file=sys.stderr, flush=True)
        try:
            while True:
                sys.stdin.readline()
                if sys.stdin.closed:
                    break
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
