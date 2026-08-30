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
      "omit_charset": true                   # -> "text/html" with no charset
    }

Test-only. Nothing in `skills/` imports it.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEXT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


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
                server.request_log.append({"path": path, "user_agent": user_agent})

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
                    if os.path.isfile(index):
                        full = index
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
