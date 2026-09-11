# -*- coding: utf-8 -*-
""""Is this response a page rather than the file it claims to be", written once.

The rule had two spellings. `crawl.py` decides whether a sitemap record or
`/llms.txt` is a page; `crawl-access-audit` decides whether a sitemap that
would not parse and an announced `/agents.md` are pages. Both carried their own
`_response_is_an_html_page`, their own `HTML_SNIFF_CHARS` and their own marker
tuple, byte for byte the same, and one of them said so in its docstring - which
is how this repository describes a defect it has not fixed yet.

The definition now lives in `audit_common.py`, which both can import.

The second half is what makes it worth doing. `crawl.py` records
`content_type`, `body_bytes` and `looks_like_html` on every sitemap record,
from the response it already had in memory. `crawl-access-audit` was answering
the same question by spending an HTTP request on a probe of a path nobody
published - so on the run where the request budget was already gone, it
answered nothing, and a temple with a catch-all route was told at high
confidence to fix the XML of a sitemap it does not publish. Reading the record
costs nothing and works on every run, including that one.

Every host in this file is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

import audit_common  # noqa: E402
from audit_common import (  # noqa: E402
    HTML_SNIFF_CHARS, SkillResult, response_is_an_html_page,
)


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_one_html_page_test")
CRAWL = _module(os.path.join(SCRIPTS, "crawl.py"), "crawl_for_one_html_page_test")

ORIGIN = "https://invented-temple.test"


class _Response(object):
    """The two things this question is about: what the server said, and what it sent."""

    def __init__(self, body, content_type=None, status=200):
        self.headers = {} if content_type is None else {"content-type": content_type}
        self.content = body.encode("utf-8")
        self.text = body
        self.encoding = "utf-8"
        self.status_code = status


# The cases the rule has been wrong on, in both directions.
CASES = (
    ("<urlset><url><loc>/a</loc></url></urlset>", "text/html; charset=utf-8", True),
    ("<urlset><url><loc>/a</loc></url></urlset>", "application/xml", False),
    ("<p>hi</p>", "application/xhtml+xml", True),
    ("<!DOCTYPE html><html><body>", "text/plain", True),
    ("<html><body>hello</body></html>", None, True),
    ("# A workshop\n\nPut it in a `<meta>` tag inside `<head>`.\n", "text/markdown", False),
    ("<!-- {} -->\n<!DOCTYPE html>\n<html>".format("licence " * 120), "text/plain", True),
    (("word " * (HTML_SNIFF_CHARS // 2)) + "<html>", "text/markdown", False),
)


# --------------------------------------------------------------------------
# One definition
# --------------------------------------------------------------------------

def test_the_rule_answers_the_cases_it_has_been_wrong_on():
    for body, content_type, expected in CASES:
        assert response_is_an_html_page(_Response(body, content_type)) is expected, \
            (content_type, body[:40])


def test_crawl_access_audit_holds_no_copy_of_its_own():
    """The local spelling is gone, and the name in that file is the shared object."""
    assert CA.response_is_an_html_page is response_is_an_html_page
    assert not hasattr(CA, "_response_is_an_html_page")
    assert not hasattr(CA, "_HTML_MARKERS")


def test_the_crawl_and_the_shared_library_cannot_drift_apart():
    """`crawl.py` is not this row's file to edit, and its copy is still there.

    The single definition is in `audit_common.py` and `crawl.py`'s remaining
    step is to import it and delete three names. Until that happens this is
    what stands between the two copies and the drift the row is about: they
    must answer every case identically, and the sniff window must be one
    number.
    """
    assert CRAWL.HTML_SNIFF_CHARS == HTML_SNIFF_CHARS
    for body, content_type, _ in CASES:
        theirs = CRAWL.response_is_an_html_page(_Response(body, content_type))
        ours = response_is_an_html_page(_Response(body, content_type))
        assert theirs is ours, (content_type, body[:40])


# --------------------------------------------------------------------------
# The discrimination is free, so it happens on every run
# --------------------------------------------------------------------------

def _broken(url, looks_like_html=None):
    """A sitemap record shaped the way `crawl.py` writes one: 200, and the XML
    parser failed on whatever arrived."""
    record = {"url": url, "status": 200, "urls": [], "is_index": False,
              "parse_error": "XML parse error: syntax error: line 1, column 0",
              "child_sitemaps": [], "referenced_in_robots": False,
              "content_type": "text/html; charset=utf-8", "body_bytes": 1_800_000}
    if looks_like_html is not None:
        record["looks_like_html"] = looks_like_html
    return record


class _RefusesToBeAsked(object):
    """A fetcher with nothing left, which is the run this used to fail on."""

    budget_left = False
    time_left = False

    def try_get(self, url, **kwargs):
        raise AssertionError("a request was made on a run with no budget: " + url)


def test_a_recorded_html_body_settles_it_with_no_request_at_all():
    record = _broken(ORIGIN + "/sitemap.xml", looks_like_html=True)
    shells, redirects, verified = CA._sitemaps_that_are_html(
        _RefusesToBeAsked(), [record], robots={}, catch_all=None)
    assert shells == [record], "the crawl's own record of the response was not read"
    assert verified is True
    assert redirects == {}


def test_a_recorded_non_html_body_leaves_the_sitemap_broken():
    """The other direction, and it has to be settled too: a record the crawl
    read as XML is a sitemap with a real error in it, and saying so is the
    whole point of the check."""
    record = _broken(ORIGIN + "/sitemap.xml", looks_like_html=False)
    record["content_type"] = "application/xml"
    shells, _, verified = CA._sitemaps_that_are_html(
        _RefusesToBeAsked(), [record], robots={}, catch_all=None)
    assert shells == []
    assert verified is True, (
        "the crawl established what arrived, so the parse failure may be published")


def test_a_direct_reading_beats_the_catch_all_inference():
    """The missing-page probe says "this origin serves a page for any address",
    which is an inference drawn from a different address. A record of this
    response is a reading of this one."""
    xml = _broken(ORIGIN + "/sitemap.xml", looks_like_html=False)
    html = _broken(ORIGIN + "/sitemap_index.xml", looks_like_html=True)
    shells, _, verified = CA._sitemaps_that_are_html(
        _RefusesToBeAsked(), [xml, html], robots={}, catch_all=True)
    assert shells == [html]
    assert verified is True


def test_a_record_from_before_the_crawl_wrote_the_field_still_declines():
    """An older snapshot carries no `looks_like_html`, and absent is not False.
    With nothing recorded, nothing to probe with and no catch-all answer, this
    run has established nothing and must publish nothing."""
    shells, _, verified = CA._sitemaps_that_are_html(
        _RefusesToBeAsked(), [_broken(ORIGIN + "/sitemap.xml")],
        robots={}, catch_all=None)
    assert shells == []
    assert verified is False


class _AnswersOnce(object):
    """A fetcher with budget, for the fallback. Counts what it was asked."""

    budget_left = True
    time_left = True

    def __init__(self, response):
        self.response = response
        self.asked = []

    def try_get(self, url, **kwargs):
        self.asked.append(url)
        return self.response


def test_the_probe_is_kept_as_the_fallback_for_a_record_that_says_nothing():
    fetcher = _AnswersOnce(_Response("<!DOCTYPE html><html>", "text/html"))
    record = _broken(ORIGIN + "/sitemap.xml")
    shells, _, verified = CA._sitemaps_that_are_html(
        fetcher, [record], robots={}, catch_all=None)
    assert shells == [record]
    assert verified is True
    assert fetcher.asked == [ORIGIN + "/sitemap.xml"], \
        "the fallback probe is the one request this function may spend"


def test_a_recorded_record_costs_no_probe_even_where_one_was_affordable():
    fetcher = _AnswersOnce(_Response("<!DOCTYPE html><html>", "text/html"))
    CA._sitemaps_that_are_html(
        fetcher, [_broken(ORIGIN + "/sitemap.xml", looks_like_html=True)],
        robots={}, catch_all=None)
    assert fetcher.asked == [], "a request was spent on a question already answered"


# --------------------------------------------------------------------------
# The finding a real report carried
# --------------------------------------------------------------------------

def _sitemap_findings(records, fetcher):
    result = SkillResult("crawl-access-audit")
    snapshot = {"origin": ORIGIN, "sitemaps": records, "pages": [],
                "robots": {"status": 200, "url": ORIGIN + "/robots.txt",
                           "sitemaps": [], "errors": []}}
    CA._check_sitemaps(result, snapshot, fetcher, snapshot["robots"])
    return result


def test_the_temple_is_no_longer_told_to_fix_a_sitemap_it_does_not_publish():
    """The false positive, on the run it was published on: no request budget
    left, an origin that answers 200 with its homepage for every address.

    It was the only false positive to reach the confident tier across six sites
    in one validation pass, and its fix - "fix the XML so the sitemap can be read" - is
    work on a file that does not exist.
    """
    result = _sitemap_findings(
        [_broken(ORIGIN + "/sitemap.xml", looks_like_html=True)], _RefusesToBeAsked())
    titles = [f["title"] for f in result.findings]
    assert not any("does not parse" in title for title in titles), titles
    assert any("No XML sitemap is available" == title for title in titles), titles

    absence = [f for f in result.findings if f["title"] == "No XML sitemap is available"][0]
    # A finding that names its sources has to name the one it rests on. Here
    # that is the content type the crawl recorded, not a probe nobody ran.
    assert any("Content-Type" in source for source in absence["checked"]), \
        absence["checked"]


def test_a_sitemap_that_really_is_broken_is_still_reported():
    """The check may not go quiet. A record the crawl read as XML, with the
    parser failing on it, is a real defect and stays published."""
    record = _broken(ORIGIN + "/sitemap.xml", looks_like_html=False)
    record["content_type"] = "application/xml"
    result = _sitemap_findings([record], _RefusesToBeAsked())
    titles = [f["title"] for f in result.findings]
    assert any("does not parse" in title for title in titles), titles
