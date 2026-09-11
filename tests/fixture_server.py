"""Serve a fixture site over HTTP so the auditor can crawl it like a real site.

`python -m http.server` cannot do the three things these fixtures need: rewrite
`{{BASE}}` into the ephemeral base URL, return 403 to a named bot user agent,
and emit response headers such as `X-Robots-Tag`. This server does, driven by
an optional `_rules.json` inside each fixture directory:

    {
      "block_user_agents": ["GPTBot"],       # -> 403 for those UAs
      "status": {"/gone.html": 404},         # forced status codes
      "headers": {"/x.html": {"X-Robots-Tag": "noindex"}},
      "redirects": {"/old.html": "/new.html"},
      "omit_charset": true,                  # -> "text/html" with no charset
      "catch_all": true                      # -> index.html for every unknown
                                             #    path, with 200, instead of 404
    }

`catch_all` is a shape a fixture cannot otherwise reproduce and that turned up
in the wild: a server that answers every address it does not recognise with
its homepage. `/sitemap.xml` and `/llms.txt` then return 200 with 1.8 MB of
`text/html`, and anything that reads a 200 as proof a file exists publishes a
finding about a file nobody wrote.

`LatentSite`, below, is the other shape a directory of files cannot be: a site
whose responses take time, and take a different amount of time on each request.
Every fixture here is served off local disk in microseconds, which is why a
suite of them ran green while two runs of one real site produced two different
reports.

Test-only. Nothing in `skills/` imports it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEXT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


# What `LatentSite` serves at /robots.txt: a file that allows everything, so
# nothing a test measures is a robots decision in disguise.
ROBOTS_ALLOW_ALL = b"User-agent: *\nAllow: /\n"


class LatentSite:
    """A generated site whose every response takes a stated number of seconds.

    `pages` is a path -> HTML mapping held in memory, so a test can build a
    site of any size without writing files. `pace` is called with the number of
    responses already sent and returns how long this one should take, which is
    what lets a test hold the speed steady, change it part-way through a crawl,
    or vary it per request from a seeded generator.

    Varying it per request is the case that matters. This tool was run
    twice against one real site, on one machine with nothing else running, and
    got 53 pages / 16 findings and then 60 pages / 19: the crawl planned its
    page count by timing its first few pages, and ordinary jitter moved that
    measurement across a rung of the ladder it was rounded onto. A server that
    answers in a constant 0.1s cannot reproduce that, and neither can one that
    answers instantly. This one can.

    `.served` counts responses and `.paths` records them in order, so a test
    can assert what was asked for as well as what came back.
    """

    def __init__(self, pages, pace=None):
        self.pages = dict(pages)
        self.pace = pace if pace is not None else (lambda served: 0.0)
        self.base_url = ""
        self.served = 0
        self.paths = []
        self.httpd = None
        self.thread = None

    def __enter__(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self._serve(True)

            def do_GET(self):
                self._serve(False)

            def _serve(self, head_only):
                path = self.path.split("?", 1)[0].split("#", 1)[0]
                # Before the answer, not after it, so the delay is part of the
                # response the client waits for rather than of the next one.
                time.sleep(site.pace(site.served))
                site.served += 1
                site.paths.append(path)
                if path == "/robots.txt":
                    body, content_type, status = (
                        ROBOTS_ALLOW_ALL, "text/plain; charset=utf-8", 200)
                elif path in site.pages:
                    body, content_type, status = (
                        site.pages[path].encode("utf-8"), "text/html; charset=utf-8", 200)
                else:
                    body, content_type, status = (
                        b"<html><body>not here</body></html>",
                        "text/html; charset=utf-8", 404)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:{}".format(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=5)
        return False


def linked_pages(count, name="Kestrel Instruments"):
    """One homepage linking to `count` ordinary pages, all on invented hosts.

    Every page is the same shape and the same length, so which of them a crawl
    read is visible in the page list and nowhere else - the point being to
    compare two runs' page sets, not to give any page something distinctive to
    be found by.
    """
    links = "".join(
        '<li><a href="/note-{0:02d}.html">Calibration note {0}</a></li>'.format(n)
        for n in range(count))
    pages = {"/": '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
                  '<title>{}</title></head><body><main>'
                  '<h1>{}</h1><p>We calibrate flow meters for '
                  'water utilities across the region.</p><ul>'.format(name, name)
                  + links + '</ul></main></body></html>'}
    for n in range(count):
        pages["/note-{0:02d}.html".format(n)] = (
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<title>Calibration note {0}</title></head><body><main>'
            '<h1>Calibration note {0}</h1><p>Flow meters drift with '
            'temperature. This note records the correction applied to batch '
            '{0} and the reference cell it was measured against.</p>'
            '</main></body></html>'.format(n))
    return pages


class FixtureServer:
    """Context manager that serves `root` and exposes `.base_url`."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        rules_path = os.path.join(self.root, "_rules.json")
        self.rules = {}
        if os.path.exists(rules_path):
            with open(rules_path, "r", encoding="utf-8") as handle:
                self.rules = json.load(handle)
        self.httpd = None
        self.thread = None
        self.base_url = ""
        self.request_log = []

    def __enter__(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence the default stderr spam
                pass

            def do_HEAD(self):
                self._serve(head_only=True)

            def do_GET(self):
                self._serve(head_only=False)

            def _serve(self, head_only):
                path = self.path.split("?", 1)[0].split("#", 1)[0]
                user_agent = self.headers.get("User-Agent", "")
                server.request_log.append({"method": self.command, "path": path,
                                           "user_agent": user_agent})

                for blocked in server.rules.get("block_user_agents", []):
                    if blocked.lower() in user_agent.lower():
                        self._respond(403, b"<html><body>Forbidden</body></html>",
                                      "text/html; charset=utf-8", head_only)
                        return

                redirect = server.rules.get("redirects", {}).get(path)
                if redirect:
                    body = b""
                    self.send_response(301)
                    self.send_header("Location", redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(body)
                    return

                forced = server.rules.get("status", {}).get(path)
                if forced and forced >= 400:
                    self._respond(forced, b"<html><body><h1>Not found</h1></body></html>",
                                  "text/html; charset=utf-8", head_only)
                    return

                rel = path.lstrip("/") or "index.html"
                if rel.endswith("/"):
                    rel += "index.html"
                full = os.path.normpath(os.path.join(server.root, rel))
                if not full.startswith(server.root) or not os.path.isfile(full):
                    index = os.path.join(full, "index.html")
                    home = os.path.join(server.root, "index.html")
                    if os.path.isfile(index):
                        full = index
                    elif server.rules.get("catch_all") and os.path.isfile(home):
                        # The homepage, at 200, for an address nobody
                        # published. Not a 404 dressed up: this server has no
                        # idea the path is unknown, which is exactly what makes
                        # a 200 here worthless as proof that a file exists.
                        full = home
                    else:
                        self._respond(404, b"<html><body><h1>Not found</h1></body></html>",
                                      "text/html; charset=utf-8", head_only)
                        return

                extension = os.path.splitext(full)[1].lower()
                content_type = TEXT_TYPES.get(extension, "application/octet-stream")
                with open(full, "rb") as handle:
                    body = handle.read()
                if extension in TEXT_TYPES:
                    body = body.replace(b"{{BASE}}", server.base_url.encode("utf-8"))

                extra = dict(server.rules.get("headers", {}).get(path, {}))
                # A fixture can drop the charset from the header to reproduce
                # the commonest real-world case: the encoding is declared in a
                # <meta> tag and nowhere else.
                if server.rules.get("omit_charset"):
                    content_type = content_type.split(";")[0]
                override = extra.pop("Content-Type", None)
                self._respond(forced or 200, body, override or content_type,
                              head_only, extra)

            def _respond(self, status, body, content_type, head_only, extra=None):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:{}".format(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=5)
        return False
