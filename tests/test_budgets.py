"""What one HTTP response is allowed to cost.

Nothing bounded this. Measured against a deliberately hostile local server,
before the fix:

  a 125 MB response      downloaded all 125,829,120 bytes into memory, and was
                         then discarded for having the wrong content type
  one byte every 9 s     held a single fetch open past 260 seconds, against an
                         audit that promises to finish inside 300

Neither is exotic. The first is any large asset a nav happens to link to; the
second is any server under load. `REQUEST_TIMEOUT` reads like it prevents the
second and does not - `requests` counts silence between bytes, not elapsed
time, and nine seconds of silence resets a ten-second timeout every time.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import FIXTURES, SCRIPTS
from fixture_server import FixtureServer

sys.path.insert(0, SCRIPTS)

import audit_common  # noqa: E402
from audit_common import (  # noqa: E402
    MAX_RESPONSE_BYTES, REQUEST_TIMEOUT, REQUEST_TOTAL_TIMEOUT, WALL_CLOCK_BUDGET,
    FetchError, Fetcher,
)

ONE_MB = b"a" * (1024 * 1024)


class _Hostile(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "huge"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.mode == "huge":
            total = 40 * len(ONE_MB)          # 40 MB, eight times the cap
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(total))
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(ONE_MB)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass                          # we hung up on purpose
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(40):
                    time.sleep(0.4)
                    self.wfile.write(b"1\r\nx\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass


@pytest.fixture
def hostile_server():
    servers = []

    class _Quiet(ThreadingHTTPServer):
        # We hang up on this server on purpose in every test here; its
        # complaints about that are not information.
        def handle_error(self, request, client_address):
            pass

    def start(mode):
        handler = type("H", (_Hostile,), {"mode": mode})
        server = _Quiet(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return "http://127.0.0.1:{}/".format(server.server_address[1])

    yield start
    for server in servers:
        server.shutdown()


# --------------------------------------------------------------------------
# The size cap
# --------------------------------------------------------------------------

def test_a_huge_response_is_read_only_up_to_the_cap(hostile_server):
    url = hostile_server("huge")
    response = Fetcher(delay=0).get(url)
    assert len(response.content) == MAX_RESPONSE_BYTES


def test_a_truncated_response_says_it_was_truncated(hostile_server):
    url = hostile_server("huge")
    assert Fetcher(delay=0).get(url).truncated is True


def test_an_ordinary_response_is_not_marked_truncated():
    with FixtureServer(os.path.join(FIXTURES, "good-site")) as server:
        response = Fetcher(delay=0).get(server.base_url + "/")
        assert response.truncated is False
        assert len(response.content) < MAX_RESPONSE_BYTES


def test_head_reads_no_body(hostile_server):
    url = hostile_server("huge")
    response = Fetcher(delay=0).get(url, method="HEAD")
    assert response.content == b""


def test_the_cap_stays_above_the_extreme_page_weight_threshold():
    """A coupling that is invisible from either file on its own.

    engagement-audit reports a page carrying more than PAGE_WEIGHT_BYTES of
    inline payload. If the read cap ever drops below that, every oversized page
    arrives weighing exactly the cap and the finding can never fire again -
    which is precisely how `thin-html` spent the build in the vocabulary with
    no code able to reach it.
    """
    sys.path.insert(0, os.path.join(
        os.path.dirname(SCRIPTS), "..", "engagement-audit", "scripts"))
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(SCRIPTS)),
                        "engagement-audit", "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("engagement_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert MAX_RESPONSE_BYTES > module.PAGE_WEIGHT_BYTES, (
        "the read cap ({:,}) must stay above PAGE_WEIGHT_BYTES ({:,}), or the "
        "extreme-payload finding becomes unreachable".format(
            MAX_RESPONSE_BYTES, module.PAGE_WEIGHT_BYTES))


# --------------------------------------------------------------------------
# The time cap
# --------------------------------------------------------------------------

def test_a_stalling_response_gives_up_instead_of_hanging(hostile_server, monkeypatch):
    monkeypatch.setattr(audit_common, "REQUEST_TOTAL_TIMEOUT", 1.0)
    url = hostile_server("dribble")
    started = time.monotonic()
    with pytest.raises(FetchError) as caught:
        Fetcher(delay=0).get(url)
    elapsed = time.monotonic() - started
    assert "still arriving" in str(caught.value)
    # The deadline can only be tested between chunks, so the bound is the total
    # timeout plus one read timeout - never unbounded, which is what it was.
    assert elapsed < 1.0 + REQUEST_TIMEOUT + 2


def test_a_stall_is_not_retried(hostile_server, monkeypatch):
    """A dropped connection is weather. A server dribbling bytes is not.

    Retrying a stall would double what one URL can cost the crawl.
    """
    monkeypatch.setattr(audit_common, "REQUEST_TOTAL_TIMEOUT", 1.0)
    url = hostile_server("dribble")
    fetcher = Fetcher(delay=0)
    with pytest.raises(FetchError):
        fetcher.get(url)
    assert fetcher.count == 1


def test_one_url_cannot_consume_more_than_a_fifth_of_the_crawl_budget():
    worst_case = REQUEST_TOTAL_TIMEOUT + REQUEST_TIMEOUT
    assert worst_case < WALL_CLOCK_BUDGET / 5, (
        "one stalled URL can cost {:.0f}s of a {:.0f}s crawl".format(
            worst_case, WALL_CLOCK_BUDGET))
