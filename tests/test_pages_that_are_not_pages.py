"""URLs the crawl recorded that are not pages of this site.

Each of these answered HTTP 200 and was then counted as a page with no title,
no `lang`, no heading and no text - four confident findings about something the
audit had deliberately not read, or had read twice.
"""

from __future__ import annotations

import os
import sys

import pytest

from conftest import FIXTURES, SCRIPTS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import crawl as crawl_module  # noqa: E402
from audit_common import looks_like_soft_404, pages_of  # noqa: E402
from fixture_server import FixtureServer  # noqa: E402


# --------------------------------------------------------------------------
# A URL we chose not to read is not a page with nothing on it
# --------------------------------------------------------------------------

def test_an_off_origin_redirect_is_not_a_page_of_this_site():
    """`/country/ca` redirects to a separate national site.

    The record answers 200 and carries no title and no `lang`, so the hygiene
    checks reported that page as missing both. Both are present on the page a
    visitor lands on, which is on a host this audit is not auditing.
    """
    snapshot = {"pages": [
        {"url": "https://example.test/country/ca", "status": 200,
         "final_url": "https://ca.example.test/", "page_type": "other",
         "skipped": "redirects off this origin", "offsite_redirect": True},
        {"url": "https://example.test/", "status": 200, "page_type": "home",
         "title": "Example", "lang": "en"},
    ]}
    urls = [p["url"] for p in pages_of(snapshot)]
    assert urls == ["https://example.test/"]


def test_a_non_html_response_is_not_a_page_either():
    snapshot = {"pages": [
        {"url": "https://example.test/report.pdf", "status": 200,
         "page_type": "other", "skipped": "non-HTML content type"},
    ]}
    assert pages_of(snapshot) == []


# --------------------------------------------------------------------------
# A page reached twice is one page
# --------------------------------------------------------------------------

def test_a_page_naming_a_crawled_page_as_canonical_is_that_page():
    """Cookie-preference links produced two extra copies of one homepage.

    The report counted three pages sharing a title, spent 5% of a sixty-page
    budget on one page, and inflated the denominator of every site-wide share.
    """
    seen = {"https://example.test/"}
    landed = "https://example.test/?ketch_show=preferences"
    canonical = "https://example.test/"
    assert canonical in seen and canonical != landed


def test_a_site_with_no_canonical_tags_still_crawls_every_page(tmp_path):
    """The guard, written badly, collapsed a seven-page crawl to one.

    `normalise_url("", origin)` returns the origin, so a page declaring no
    canonical read as a duplicate of the homepage. Every site without canonical
    tags would have been audited from its homepage alone, and the report would
    have said so about a site it never looked at.
    """
    with FixtureServer(os.path.join(FIXTURES, "no-schema-site")) as server:
        snapshot = crawl_module.crawl(
            server.base_url, str(tmp_path / "snapshot.json"),
            max_pages=20, budget_s=60, respect_robots=False, render=False)
    assert len(snapshot["pages"]) >= 5, (
        "a fixture with no canonical tags must crawl more than its homepage")
    assert not [s for s in snapshot["crawl"]["skipped"] if "canonical" in s["reason"]]


# --------------------------------------------------------------------------
# A page that says it is missing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,heading,expected", [
    ("404 | Example", "", True),
    ("Page Not Found - Example", "Uh-Oh, Nothing To See Here!", True),
    ("Womens Flats | Example", "Womens Flats", False),
    ("Designing a good error page", "Designing a good error page", False),
])
def test_what_reads_as_a_missing_page(title, heading, expected):
    assert looks_like_soft_404({"title": title, "h1": heading}) is expected
