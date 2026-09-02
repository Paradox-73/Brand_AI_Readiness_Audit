"""Who the site says it is, and where it said so.

Three real failures, all of which put a wrong name into the fix text - the
part of the report a reader is most likely to copy and paste, in a section
whose whole subject is making the brand's identity unambiguous.
"""

from __future__ import annotations

import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from crawl import TITLE_SEPARATOR, _site_spelling, detect_brand  # noqa: E402


def _page(url, title="", jsonld=(), og=None, page_type="home"):
    return {"url": url, "title": title, "status": 200, "page_type": page_type,
            "jsonld": list(jsonld), "og": og or {}}


# --------------------------------------------------------------------------
# A tight colon is ordinary punctuation
# --------------------------------------------------------------------------

def test_a_title_split_on_a_tight_colon():
    """"Brand: Tagline" is a brand and a tagline, not a 47-character name."""
    assert TITLE_SEPARATOR.split("Zingerman's: Online Shopping for Food and Gifts") == [
        "Zingerman's", "Online Shopping for Food and Gifts"]


def test_a_clock_time_is_not_a_separator():
    assert TITLE_SEPARATOR.split("Open 9:00 to 5:00") == ["Open 9:00 to 5:00"]


def test_a_tight_hyphen_belongs_to_the_word():
    assert TITLE_SEPARATOR.split("e-commerce for all") == ["e-commerce for all"]


def test_the_apostrophe_survives_into_the_brand_name():
    """The bug in full: the report told a shop to publish its name misspelt."""
    brand = detect_brand(
        [_page("https://www.example.test/",
               "Zingerman's: Online Shopping for Food and Gifts")],
        "https://www.zingermans.example")
    assert brand["name"] == "Zingerman's"


# --------------------------------------------------------------------------
# A domain cannot carry punctuation; the homepage can
# --------------------------------------------------------------------------

def test_the_domain_spelling_defers_to_the_page_spelling():
    assert _site_spelling("zingermans", {"title": "Zingerman's Mail Order"}) == "Zingerman's"
    assert _site_spelling("levis", {"title": "Levi's"}) == "Levi's"


def test_an_unrelated_page_yields_no_spelling():
    assert _site_spelling("acme", {"title": "Totally Unrelated Words Here"}) is None


def test_a_short_token_is_not_matched():
    """Three letters match too much to be worth trusting."""
    assert _site_spelling("abc", {"title": "A B C"}) is None


# --------------------------------------------------------------------------
# Where a declaration was found decides what it is worth
# --------------------------------------------------------------------------

ORG = [{"@type": "Organization", "name": "Example Training Services"}]


def test_a_deep_page_declaration_loses_to_the_homepage_title():
    """One store page out of sixty named the whole organisation.

    A national charity declared no identity on its homepage, about page or
    contact page, and declared an Organization `name` on one product listing
    for a ring binder - naming the training subsidiary that runs the shop.
    That became the brand for the entire report and headed the generated
    llms.txt, so the file telling assistants what the organisation is would
    have named a merchandise subsidiary.
    """
    brand = detect_brand([
        _page("https://example.test/", "Example Relief | Home"),
        _page("https://example.test/store/binder/1.html", "Binder",
              jsonld=ORG, page_type="product"),
    ], "https://example.test")
    assert brand["name"] == "Example Relief"
    assert brand["source"] == "title-part-0"


def test_the_same_declaration_on_the_homepage_wins():
    """Demoting deep declarations must not demote ordinary site-wide markup."""
    brand = detect_brand([
        _page("https://example.test/", "Example Relief | Home", jsonld=ORG),
    ], "https://example.test")
    assert brand["name"] == "Example Training Services"
    assert brand["source"] == "jsonld:Organization"


def test_a_deep_declaration_still_beats_the_bare_domain():
    """It is weaker evidence, not worthless evidence."""
    brand = detect_brand([
        _page("https://example.test/", ""),
        _page("https://example.test/store/binder/1.html", "", jsonld=ORG,
              page_type="product"),
    ], "https://example.test")
    assert brand["name"] == "Example Training Services"


def test_declarations_record_the_pages_they_came_from():
    """A finding with no pages is scored as affecting the whole site."""
    brand = detect_brand([
        _page("https://example.test/", "Example Relief | Home"),
        _page("https://example.test/store/binder/1.html", "Binder",
              jsonld=ORG, page_type="product"),
    ], "https://example.test")
    assert brand["declared_on"]["Example Training Services"] == [
        "https://example.test/store/binder/1.html"]
    assert brand["pages_seen"] == 2


# --------------------------------------------------------------------------
# The shorter end of a title is not always the name
# --------------------------------------------------------------------------

def test_home_is_not_the_brand():
    """"Brand | Home" split shortest-first made the brand "Home"."""
    brand = detect_brand(
        [_page("https://example.test/", "Example Relief | Home")], "https://example.test")
    assert brand["name"] == "Example Relief"


def test_official_site_is_not_the_brand():
    brand = detect_brand(
        [_page("https://example.test/", "Example Relief | Official Site")],
        "https://example.test")
    assert brand["name"] == "Example Relief"


def test_the_shorter_end_still_wins_between_two_real_halves():
    brand = detect_brand(
        [_page("https://example.test/", "Acme | Wheel-thrown stoneware from York")],
        "https://example.test")
    assert brand["name"] == "Acme"
