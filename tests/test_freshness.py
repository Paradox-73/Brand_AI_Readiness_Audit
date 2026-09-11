# -*- coding: utf-8 -*-
"""Dates: which pages are expected to carry one, and how old is stated.

"Add a visible line under the headline: Published <date>, last updated <date>"
is cheap advice to give and expensive to follow, so it may only go to a page
that is one dated thing. Three real sites got it and should not have:

  twelve dated posts under            typed `other`, so the freshness checks
  `/changelog/` and `/now/`           skipped with "no article or press pages
                                      were crawled" - about a crawl that had
                                      just read twelve of them
  eight staff bios under `/people/`   one reads "joined the firm on 3 October
                                      2011", and the other seven were reported
                                      as pages "where a date is expected"
                                      carrying none, with a fix telling the firm
                                      to put published-and-updated lines on its
                                      lawyers
  a blog hub linking three            counted among "13 pages where one is
  category listings                   expected"

And two sites were told their pages carry no date at all, about dates the
snapshot had already captured: `2015年4月6日`, where the units are named rather
than punctuated, and the same shape in Korean.

The last test is arithmetic a reader can check against a visible date and find
wrong: 22.2 months is 22, not 23.
"""

from __future__ import annotations

import datetime
import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from page_extract import DATE_TEXT_RE, _looks_like_a_date  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FRESHNESS = _module("freshness-corroboration-audit", "freshness_for_freshness_tests")

HOST = "https://an-invented-host.test"


# --------------------------------------------------------------------------
# A section is dated when the site dates most of it
# --------------------------------------------------------------------------

def test_a_check_that_reads_one_page_type_says_so_when_it_finds_none():
    """Twelve dated posts under /changelog/ and /now/ were typed `other`, and
    the freshness checks skipped with "no article or press pages were
    crawled" - about a crawl that had just read twelve of them."""
    # Three pages, not two. A section counts as dated when the site dates most
    # of it, which is what "twelve dated posts under /changelog/" was; one
    # dated page out of two is also one dated page out of eight, and that was
    # letting a single date in a staff bio's prose promote a whole `/people/`
    # section. The case this test is for is unchanged.
    pages = [
        {"url": "https://example.com/changelog/one", "page_type": "other",
         "dates": {"has_any": True}},
        {"url": "https://example.com/changelog/two", "page_type": "other",
         "dates": {"has_any": True}},
        {"url": "https://example.com/changelog/three", "page_type": "other",
         "dates": {"has_any": False}},
    ]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, pages)
    assert result.findings, "the undated post in a dated section should be reported"
    assert "/changelog/three" in result.findings[0]["evidence"]


def test_one_date_in_prose_does_not_make_a_section_dated():
    """A law firm's eight staff bios sat under /people/. One reads "joined the
    firm on 3 October 2011", and the other seven were then reported as pages
    "where a date is expected" carrying none - with a fix telling the firm to
    put published-and-updated lines on its lawyers."""
    pages = [{"url": "https://example.com/people/person-{}".format(i),
              "page_type": "other",
              "dates": {"has_any": i == 0}} for i in range(8)]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, pages)
    assert not result.findings, (
        "one dated page in eight is not a dated section: {}".format(
            [f["title"] for f in result.findings]))


# --------------------------------------------------------------------------
# An index is not an item
# --------------------------------------------------------------------------

def _post(url, dated=True):
    return {"url": url, "status": 200, "page_type": "article",
            "dates": {"has_any": dated}}


def test_a_hub_that_lists_other_pages_is_not_asked_for_a_publication_date():
    """A blog hub linking three category listings was counted among "13 pages
    where one is expected", and the fix read "add a visible line under the
    headline: Published <date>, last updated <date>" - a line that belongs on
    a post and on nothing that lists posts."""
    hub = {"url": HOST + "/blogs/blog", "status": 200,
           "page_type": "category", "dates": {"has_any": False}}
    pages = [_post(HOST + "/blogs/one"),
             _post(HOST + "/blogs/two"),
             _post(HOST + "/blogs/three"), hub]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, pages)
    assert not result.findings, "an index carries its items' dates or none, and neither is its own"
    assert result.signals["listing_pages_not_expected_to_carry_a_date"] == 1


def test_an_undated_post_in_a_dated_section_is_still_reported():
    """The exclusion must not switch the check off for the pages it is for."""
    pages = [_post(HOST + "/blogs/one"),
             _post(HOST + "/blogs/two"),
             _post(HOST + "/blogs/three", dated=False)]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, pages)
    assert any(f["root_cause"] == "no-date-signal" for f in result.findings)


def test_one_index_detector_serves_every_check_in_the_file():
    """The stale-year check kept its own partial copy of "this is an index",
    which recognised fewer pages than the shared one - so a page could be an
    index to one check in this file and an item to another."""
    listing = {"url": HOST + "/news", "status": 200, "page_type": "category"}
    assert FRESHNESS._is_archive_or_index(listing) is True


# --------------------------------------------------------------------------
# A date written the way the page's own language writes it
# --------------------------------------------------------------------------

def test_a_date_written_with_its_units_named_is_a_date():
    """2015年4月6日 sat under the headline of a page reported as carrying no
    date at all."""
    assert DATE_TEXT_RE.search("更新日 2015年4月6日")
    assert DATE_TEXT_RE.search("Published 6 April 2015")


def test_a_date_written_with_a_cjk_year_mark_is_a_date():
    """It was captured into the snapshot and then reported as absent, because
    a second pattern has no word boundary before the year mark. Every site
    writing dates this way was told its pages carry none."""
    assert _looks_like_a_date("2026" + chr(0x5E74) + "4" + chr(0x6708))
    assert _looks_like_a_date("2015" + chr(0xB144) + "4" + chr(0xC6D4))
    assert not _looks_like_a_date("PT0S")


# --------------------------------------------------------------------------
# Whole months, never rounded up
# --------------------------------------------------------------------------

def test_a_month_is_only_counted_once_it_is_whole():
    """"23 months" is a number the reader can check against a visible date and
    find wrong: 22.2 months is 22, and the boundary case is 23."""
    assert FRESHNESS.months_between(datetime.date(2024, 10, 20),
                                    datetime.date(2026, 9, 3)) == 22
    assert FRESHNESS.months_between(datetime.date(2024, 10, 1),
                                    datetime.date(2026, 9, 3)) == 23
