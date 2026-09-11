# -*- coding: utf-8 -*-
"""HTTP 200 at an address is not proof that a file is published there.

A temple's server answers every path it does not recognise with its homepage.
`/sitemap.xml` returned 200, `text/html`, 1.8 MB - the homepage byte for byte -
the XML parser choked on it, and the report published "an XML sitemap is
published but does not parse ... line 25, column 104" at high confidence in the
report's main body, telling the owner to validate a file that does not exist.
It was the only false positive to reach the main body across six sites in that
validation pass. `/llms.txt` on the same host was recorded as present, and
1.8 MB of homepage was then read as the file's contents.

Both readings came out of `crawl.py`, and both had the same shape: the status
code was consulted, and the `Content-Type` the server itself set was not. The
crawl now records what the server said it sent and how much of it, on every
sitemap record and every agent-file record, and decides `present` from that
rather than from the status alone - so the check that reads these records has
the evidence without spending a request on a second probe.

Every host in this file is invented; the server listens on the loopback
address and every address it serves is fictional.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from crawl import HTML_SNIFF_CHARS, response_is_an_html_page  # noqa: E402

from fixture_server import FixtureServer  # noqa: E402

CRAWL = os.path.join(SCRIPTS, "crawl.py")


class _Response(object):
    """The two things the test is about: what the server said, and what it sent."""

    def __init__(self, body, content_type=None):
        self.headers = {} if content_type is None else {"content-type": content_type}
        self.text = body
        self.content = body.encode("utf-8")
        self.encoding = "utf-8"
        self.status_code = 200


# --------------------------------------------------------------------------
# The test itself
# --------------------------------------------------------------------------

def test_the_content_type_settles_it_whatever_the_body_looks_like():
    """The server's own statement about what it just sent.

    A sitemap address answering `text/html` is not serving a sitemap, even if
    something in the body looks like XML - which is the case a catch-all route
    produces, because the homepage of a site about sitemaps can contain
    `<urlset` in a code sample.
    """
    assert response_is_an_html_page(
        _Response("<urlset><url><loc>/a</loc></url></urlset>", "text/html; charset=utf-8"))
    assert response_is_an_html_page(_Response("<p>hello</p>", "application/xhtml+xml"))
    assert not response_is_an_html_page(
        _Response("<urlset><url><loc>/a</loc></url></urlset>", "application/xml"))


def test_a_page_the_server_mislabels_is_still_a_page():
    """The other half. Servers label HTML `text/plain` and send no type at all,
    so the content type cannot be the only test either."""
    assert response_is_an_html_page(_Response("<!DOCTYPE html><html><body>", "text/plain"))
    assert response_is_an_html_page(_Response("<html><body>hello</body></html>"))


def test_a_real_agent_file_is_not_mistaken_for_a_page():
    """`/llms.txt` is Markdown, and a Markdown file about marking pages up
    quotes HTML in its first screenful. Only `<html` and `<!doctype html`
    count, so a document that mentions `<head>` or `<meta>` stays a file."""
    llms = ("# An Invented Workshop\n\n> Furniture restoration.\n\n"
            "Put the description in a `<meta>` tag inside `<head>`.\n")
    assert not response_is_an_html_page(_Response(llms, "text/markdown"))
    assert not response_is_an_html_page(_Response(llms))


def test_the_window_is_wide_enough_for_a_page_that_opens_with_a_comment():
    """400 characters was the old window, and it is short.

    A page can open with a licence comment, conditional comments for old
    browsers, or several hundred characters of preload hints from a bundler
    before `<html` is reached. Missing the tag costs a confident finding about
    a file that does not exist.
    """
    buried = "<!-- {} -->\n<!DOCTYPE html>\n<html>".format("licence " * 120)
    assert len(buried.split("<!DOCTYPE")[0]) > 400
    assert response_is_an_html_page(_Response(buried, "text/plain"))

    # And not so wide that a genuine file mentioning the tag far down reads as
    # a page: the window is a window, not the whole document.
    past_the_window = ("word " * (HTML_SNIFF_CHARS // 2)) + "<html>"
    assert not response_is_an_html_page(_Response(past_the_window, "text/markdown"))


# --------------------------------------------------------------------------
# The same question, of a real crawl of a real catch-all server
# --------------------------------------------------------------------------

_HOME = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>An Invented Workshop</title>
<meta name="description" content="Furniture restoration and the record of it.">
</head><body><nav><a href="/">Home</a> <a href="/about.html">About</a></nav>
<h1>An Invented Workshop</h1>
<p>An Invented Workshop restores wooden furniture and publishes the record of
every piece it has worked on, from the first cut to the final wax. The bench is
open on weekdays and the archive is open to anyone.</p>
</body></html>
"""


@pytest.fixture(scope="module")
def catch_all_snapshot(tmp_path_factory):
    """One crawl of a site that answers its homepage for every unknown path.

    Built here rather than taken from `tests/fixtures/`, because every fixture
    publishes a real `/sitemap.xml` and most publish a real `/llms.txt` - and
    the shape under test is the address the site never wrote.
    """
    root = tmp_path_factory.mktemp("catch_all_site")
    (root / "index.html").write_text(_HOME, encoding="utf-8")
    (root / "about.html").write_text(_HOME, encoding="utf-8")
    (root / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    out_path = str(root / "snapshot.json")

    server = FixtureServer(str(root))
    server.rules = {"catch_all": True}
    with server:
        completed = subprocess.run(
            [sys.executable, CRAWL, server.base_url, "--out", out_path,
             "--budget", "60", "--delay", "0", "--no-render"],
            cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr[-1500:]
    with open(out_path, encoding="utf-8") as handle:
        return json.load(handle)


def test_an_agent_file_the_site_never_published_is_not_recorded_as_present(
        catch_all_snapshot):
    """`present` is what the report's "publish /llms.txt" recommendation reads,
    and what decides whether the file's text is mined for off-site profiles. A
    homepage recorded as `/llms.txt` was read as the file's contents."""
    llms = catch_all_snapshot["llms_txt"]
    assert llms["status"] == 200, "this test needs the catch-all to have answered"
    assert llms["present"] is False, \
        "the homepage, served at /llms.txt, was recorded as a published file"
    assert "text/html" in llms["content_type"].lower()
    assert llms.get("body_bytes"), "no body length was recorded to weigh the 200 against"
    assert "text" not in llms, "the homepage was kept as the contents of /llms.txt"


def test_a_sitemap_record_carries_what_the_server_said_it_sent(catch_all_snapshot):
    """The evidence a check needs, on the record, without spending a request.

    Discriminating this by probing for a path nobody published costs a request
    and is unavailable on a run whose budget is gone. The `Content-Type` is
    already in memory when the response arrives.
    """
    records = [s for s in catch_all_snapshot["sitemaps"] if s.get("status") == 200]
    assert records, "no sitemap address answered, so this test measures nothing"
    for record in records:
        assert "content_type" in record and "body_bytes" in record, record["url"]

    html = [s for s in records if s.get("looks_like_html")]
    assert html, "a sitemap address serving the homepage was not recognised as HTML"
    for record in html:
        assert "text/html" in record["content_type"].lower()
        assert record["body_bytes"] > 0
