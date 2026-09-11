# -*- coding: utf-8 -*-
"""A request may not outlive the deadline the phase was told to respect.

`Fetcher.get` tested the wall clock before it opened a socket and not again.
A server that keeps dribbling bytes then held that one request open for
`REQUEST_TOTAL_TIMEOUT` - thirty seconds - past the second the phase was
supposed to stop, and a `Crawl-delay` slept through after the test put the
socket on the wire after the deadline it had just been approved against.

That is what `run_audit.py` had to design around. `engagement-audit` was told
83 s, honoured it exactly, and was killed at 85 s, because a two-second margin
is not a margin against a thirty-second stall; a sixth of that audit was
thrown away. The margin was raised to 40 s per network phase, and the crawl
paid for all 40 in pages it did not read.

Measured here against a local server dribbling one byte every 0.5 s, with the
clamp removed and with it in place:

    12 s of budget left     +18.12 s past the deadline  ->  +0.03 s
     2 s of budget left     +28.05 s past the deadline  ->   +3.0 s

What this can and cannot promise. Every deadline here is advisory to a socket
that is already open: the elapsed check runs between chunks, and the read that
straddles the deadline blocks until the socket's own timeout returns control.
So the promise is `DEADLINE_OVERSHOOT_SECONDS` - one socket timeout - and not
zero. Cutting it finer would mean closing the socket from another thread
mid-body, which turns a slow response into a truncated one and tells a worse
lie about the site than a late answer does.

There is no "too little left, do not start" rule, and that is deliberate. Tried
at ten seconds, a `--budget 6` crawl - which `tests/test_runtime.py` makes and
a caller may ask for - opened no socket at all and wrote an empty snapshot.

Every address in this file is the loopback interface or an invented `.test`
host; nothing here reaches the internet.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

import audit_common  # noqa: E402
from audit_common import (  # noqa: E402
    DEADLINE_OVERSHOOT_SECONDS, FetchError, Fetcher, REQUEST_CLAMP_FLOOR_SECONDS,
    REQUEST_TIMEOUT, REQUEST_TOTAL_TIMEOUT, SLOW_ORIGIN_MEDIAN_MS,
    VERY_SLOW_ORIGIN_MEDIAN_MS, explain_fetch_error,
)

# A port nothing listens on, so the connection is refused rather than waited on.
DEAD_ADDRESS = "http://127.0.0.1:1/"


class _Dribble(BaseHTTPRequestHandler):
    """One byte every half second, for two minutes. A server under load."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for _ in range(240):
                time.sleep(0.5)
                self.wfile.write(b"1\r\nx\r\n")
                self.wfile.flush()
        except OSError:
            pass                              # we hang up on it on purpose


@pytest.fixture
def dribbling_server():
    class _Quiet(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            pass

    server = _Quiet(("127.0.0.1", 0), _Dribble)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:{}/".format(server.server_address[1])
    server.shutdown()


# --------------------------------------------------------------------------
# The measurement
# --------------------------------------------------------------------------

def test_a_wedged_response_ends_within_one_socket_timeout_of_the_deadline(
        dribbling_server):
    """The defect, and the bound that replaces it, against a real socket.

    Before this clamp the same fetch ran 18.12 s past a 12 s deadline: the
    elapsed cap was thirty seconds measured from the request's own start, and
    nothing in it knew what the phase had left.
    """
    budget = 12.0
    fetcher = Fetcher(delay=0, deadline=time.monotonic() + budget)
    with pytest.raises(FetchError) as caught:
        fetcher.get(dribbling_server)
    past_deadline = time.monotonic() - fetcher.deadline

    assert past_deadline <= DEADLINE_OVERSHOOT_SECONDS, (
        "the fetch ran {:.2f}s past its deadline, against a published bound of "
        "{:.0f}s".format(past_deadline, DEADLINE_OVERSHOOT_SECONDS))
    assert past_deadline < REQUEST_TOTAL_TIMEOUT - budget, (
        "the unclamped thirty-second cap was still what ended it")
    assert "time budget ran out" in str(caught.value)


def test_the_clamp_still_leaves_a_short_budget_a_usable_request(dribbling_server):
    """The floor, doing its job. With two seconds left the request is not cut
    to two seconds - it gets the floor, because a request shorter than that
    fails ordinary pages and this audit publishes a failed fetch as something
    the site did."""
    fetcher = Fetcher(delay=0, deadline=time.monotonic() + 2.0)
    started = time.monotonic()
    with pytest.raises(FetchError):
        fetcher.get(dribbling_server)
    took = time.monotonic() - started
    assert took >= REQUEST_CLAMP_FLOOR_SECONDS - 0.5, (
        "a request begun with two seconds left was cut to two seconds")
    assert time.monotonic() - fetcher.deadline <= 2 * REQUEST_CLAMP_FLOOR_SECONDS


# --------------------------------------------------------------------------
# What is clamped, and what it is never clamped past
# --------------------------------------------------------------------------

def test_a_fetcher_with_no_deadline_is_unchanged():
    """A single skill run from the command line has no ceiling to clamp to,
    and must keep the thirty-second cap that bounds it against nothing else."""
    fetcher = Fetcher(delay=0)
    assert fetcher.deadline is None
    assert fetcher.time_left is True
    assert fetcher._elapsed_cap(time.monotonic()) == REQUEST_TOTAL_TIMEOUT
    assert fetcher._socket_timeout() == fetcher.timeout


def test_the_caps_are_clamped_to_what_is_left_and_no_further():
    now = time.monotonic()
    generous = Fetcher(delay=0, timeout=25.0, deadline=now + 12.0)
    assert 11.0 < generous._socket_timeout() <= 12.0, (
        "a 25 s socket timeout with 12 s left is 13 s of overshoot")
    assert 11.0 < generous._elapsed_cap(time.monotonic()) <= 12.0

    # And never below the floor, whatever the arithmetic says.
    scraping = Fetcher(delay=0, timeout=25.0, deadline=now + 0.2)
    assert scraping._socket_timeout() == REQUEST_CLAMP_FLOOR_SECONDS
    assert scraping._elapsed_cap(time.monotonic()) == REQUEST_CLAMP_FLOOR_SECONDS


def test_a_small_budget_still_makes_requests():
    """The rule that was tried and taken out again.

    A floor of ten seconds used as a "do not start" gate made a `--budget 6`
    run open no socket at all and write an empty snapshot. A caller may ask for
    a budget smaller than the floor, and the answer to that is fewer seconds
    per request, not no request.
    """
    fetcher = Fetcher(max_requests=10, delay=0, deadline=time.monotonic() + 6.0)
    assert fetcher.time_left is True
    assert fetcher._socket_timeout() > 0


# --------------------------------------------------------------------------
# The delay and the backoff are part of the request's cost
# --------------------------------------------------------------------------

def test_the_delay_owed_to_the_site_is_counted_before_the_clock_test():
    """A `Crawl-delay` is not free time.

    The old order tested the clock, slept out the delay, then opened the
    socket - so a ten-second delay put the request ten seconds past the
    deadline before a byte moved. Here there is clock left and none of it
    survives the delay, and the refusal has to come from the second reading.
    """
    fetcher = Fetcher(max_requests=10, delay=8.0, deadline=time.monotonic() + 4.0)
    fetcher._last_request = time.monotonic()
    assert fetcher.time_left is True, (
        "the clock alone still says there is time; the delay is what removes it")
    started = time.monotonic()
    with pytest.raises(FetchError) as caught:
        fetcher.get("https://nothing.test/")
    assert "wall-clock budget exhausted" in str(caught.value)
    assert time.monotonic() - started < 1.0, "it slept out the delay before refusing"
    assert fetcher.count == 0


def test_the_retry_backoff_is_charged_to_the_clock_before_it_is_spent():
    """The clock test used to sit after `time.sleep(RETRY_BACKOFF_SECONDS)`,
    so a retry with no room for its own backoff still cost the sleep and only
    then noticed."""
    fetcher = Fetcher(max_requests=10, delay=0,
                      deadline=time.monotonic() + audit_common.RETRY_BACKOFF_SECONDS / 2)
    assert fetcher.time_left is True
    assert fetcher._room_for_a_request(audit_common.RETRY_BACKOFF_SECONDS) is False


def test_a_retry_with_no_room_left_is_not_attempted():
    """The same refused connection twice, once with a clock and once without.

    The request count is the observable: a second attempt spends a second
    request, and it may not be spent when the backoff alone would outlast the
    deadline.
    """
    out_of_time = Fetcher(max_requests=10, delay=0,
                          deadline=time.monotonic() + audit_common.RETRY_BACKOFF_SECONDS / 2)
    with pytest.raises(FetchError) as caught:
        out_of_time.get(DEAD_ADDRESS)
    assert "wall-clock budget exhausted" in str(caught.value)
    assert out_of_time.count == 1, "the retry was attempted with no room for it"

    # The same refusal with a clock that has room is still retried once: a
    # dropped connection is weather, and three sites recorded as blocked
    # answered 200 on the next attempt.
    plenty = Fetcher(max_requests=10, delay=0, deadline=time.monotonic() + 600.0)
    with pytest.raises(FetchError):
        plenty.get(DEAD_ADDRESS)
    assert plenty.count == 2


def test_an_exhausted_clock_still_refuses_before_the_socket():
    """The rule that was already there and still holds: past the deadline,
    nothing is asked and nothing is counted."""
    fetcher = Fetcher(max_requests=10, deadline=time.monotonic() - 1)
    assert fetcher.time_left is False
    assert fetcher.try_get("https://nothing.test/") is None
    assert fetcher.count == 0


# --------------------------------------------------------------------------
# Whose limit ended the request
# --------------------------------------------------------------------------

def test_our_own_clock_is_not_reported_as_the_site_being_slow():
    """"Sent its response so slowly the audit stopped waiting" is a sentence
    about the owner's server. It may not be printed for a response this audit
    cut short because its own phase ran out of clock."""
    clamped = Fetcher(delay=0, deadline=time.monotonic() + 12.0)._stall_message(12.0)
    assert "this audit" in explain_fetch_error(clamped)
    assert "not a fault in the site" in explain_fetch_error(clamped)

    slow = Fetcher(delay=0)._stall_message(REQUEST_TOTAL_TIMEOUT)
    assert explain_fetch_error(slow) == \
        "sent its response so slowly the audit stopped waiting", (
            "a genuinely slow server must still be described as one")


# --------------------------------------------------------------------------
# The two numbers, and what they are answerable to
# --------------------------------------------------------------------------

def test_the_floor_sits_between_slow_and_very_slow():
    """Why the floor is five seconds and not half of one.

    A clamp shorter than an ordinary page's own response time would end
    requests the unclamped fetcher considered healthy, and the resulting
    `FetchError` is read downstream as evidence about the site - a limit of
    ours published as a fault of theirs.
    """
    assert REQUEST_CLAMP_FLOOR_SECONDS * 1000 >= SLOW_ORIGIN_MEDIAN_MS
    assert REQUEST_CLAMP_FLOOR_SECONDS * 1000 <= VERY_SLOW_ORIGIN_MEDIAN_MS


def test_the_published_bound_is_what_the_clamp_can_actually_promise():
    """The arithmetic behind DEADLINE_OVERSHOOT_SECONDS, as a test rather than
    a comment.

    Connect and first byte each get the clamped socket timeout, so a request
    starting with `left` seconds on the clock finishes at worst
    `2 x max(floor, min(timeout, left)) - left` past the deadline. The bound is
    the largest that gets, over every `left` a request can start with.
    """
    def worst_case(left):
        return 2 * max(REQUEST_CLAMP_FLOOR_SECONDS,
                       min(REQUEST_TIMEOUT, left)) - left

    lefts = [n / 10.0 for n in range(1, int(REQUEST_TOTAL_TIMEOUT * 10) + 1)]
    assert max(worst_case(left) for left in lefts) <= DEADLINE_OVERSHOOT_SECONDS
    assert DEADLINE_OVERSHOOT_SECONDS == REQUEST_TIMEOUT
    assert DEADLINE_OVERSHOOT_SECONDS < REQUEST_TOTAL_TIMEOUT + REQUEST_TIMEOUT, (
        "the clamp has to beat the unclamped worst case, or it bought nothing")


def test_the_orchestrator_margin_covers_the_bound_this_file_pins():
    """`run_audit.py` holds back a margin per network phase for exactly this
    overshoot. It may shrink to the bound; it may not drop below it."""
    spec = importlib.util.spec_from_file_location(
        "run_audit_for_deadline_clamp", os.path.join(ROOT, "run_audit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.SKILL_WEDGE_MARGIN_SECONDS >= DEADLINE_OVERSHOOT_SECONDS
    assert module.CRAWL_WEDGE_MARGIN_SECONDS >= DEADLINE_OVERSHOOT_SECONDS
