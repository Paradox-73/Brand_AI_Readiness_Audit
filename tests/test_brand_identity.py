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

def test_a_title_splits_on_a_separator_and_not_on_punctuation():
    """"Brand: Tagline" is a brand and a tagline, not a 47-character name - but
    a clock time and a hyphenated word are one part, not two."""
    assert TITLE_SEPARATOR.split("Zingerman's: Online Shopping for Food and Gifts") == [
        "Zingerman's", "Online Shopping for Food and Gifts"]
    assert TITLE_SEPARATOR.split("Open 9:00 to 5:00") == ["Open 9:00 to 5:00"]
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
    """A domain cannot carry an apostrophe and the homepage can. But three
    letters match too much to be worth trusting, and a page with no mention of
    the domain stem cannot spell it at all."""
    assert _site_spelling("zingermans", {"title": "Zingerman's Mail Order"}) == "Zingerman's"
    assert _site_spelling("levis", {"title": "Levi's"}) == "Levi's"
    assert _site_spelling("acme", {"title": "Totally Unrelated Words Here"}) is None
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

def test_a_boilerplate_half_is_not_the_brand():
    """"Brand | Home" split shortest-first made the brand "Home"."""
    for title in ("Example Relief | Home", "Example Relief | Official Site"):
        brand = detect_brand([_page("https://example.test/", title)],
                             "https://example.test")
        assert brand["name"] == "Example Relief", title


# --------------------------------------------------------------------------
# og:site_name: the region is not the brand
# --------------------------------------------------------------------------

def _og(value, url="https://example.test/"):
    return {"url": url, "title": "", "status": 200, "page_type": "home",
            "jsonld": [], "og": {"og:site_name": value}}


def test_a_national_suffix_is_not_the_brand():
    """og:site_name "Doctors Without Borders - USA" made the brand "USA".

    Shortest-piece-wins put "USA" at the top of the report, invented a name
    collision about it, and wrote it into the `name` field of a paste-ready
    Organization snippet - telling a medical charity its own name was "USA".
    """
    brand = detect_brand([_og("Example Without Borders - USA")], "https://example.test")
    assert brand["name"] == "Example Without Borders"


def test_a_home_word_in_another_language_is_not_the_brand():
    """"Die Bundesregierung informiert | Startseite" -> "Startseite".

    The comment on this code said splitting the title had fixed that case. It
    had not: splitting it and taking the shorter half returns the German for
    "home page".
    """
    brand = detect_brand([_og("Die Beispielregierung informiert | Startseite")],
                         "https://example.test")
    assert brand["name"] == "Die Beispielregierung informiert"


def test_a_brand_whose_name_contains_a_place_is_left_alone():
    """"USA Today" has no separator, so nothing is split off it."""
    brand = detect_brand([_og("USA Today")], "https://example.test")
    assert brand["name"] == "USA Today"


def test_the_shorter_half_still_wins_when_both_halves_are_real():
    brand = detect_brand([_og("Acme | Wheel-thrown stoneware from York")],
                         "https://example.test")
    assert brand["name"] == "Acme"


# --------------------------------------------------------------------------
# The forms of the name the checks are allowed to search for
#
# A brand written with "The" was unsatisfiable. Two sites, both at high
# confidence, were told "no page states in one sentence what the brand is" -
# one whose H1 is "The Coppergate Studio is a retreat for curious programmers",
# the other whose about page opens "Northwind Figures (NWF) is a free,
# non-profit website".
# --------------------------------------------------------------------------

from audit_common import comparison_key, name_forms  # noqa: E402


def test_a_leading_article_is_stripped_and_a_middle_one_is_not():
    assert "Coppergate Studio" in name_forms("The Coppergate Studio")
    assert all(not f.startswith("Bank of West") for f in name_forms("Bank of The West"))


def test_the_head_form_is_derived_only_when_it_spells_the_domain():
    """A restaurant group declares three words and writes one. The check
    searched for the full legal name, which appears nowhere on that site and
    nowhere on the web. Without the domain to justify it, though, "Indian
    Restaurants" would become a name the brand goes by."""
    assert "Walmgate" in name_forms("Walmgate Indian Restaurants", domain_token="walmgate")
    assert "Walmgate" not in name_forms("Walmgate Indian Restaurants")


def test_a_name_never_reduces_to_nothing():
    """`[^a-z0-9]` is the empty string for every name written in Hangul, and an
    empty key equals every other empty key - so the filter that keeps only
    exact Wikidata name matches kept all of them, and a museum was told three
    organisations share a name that nothing shares."""
    korean = u"국립중앙박물관"
    assert comparison_key(korean), "a Hangul name reduced to the empty string"
    assert comparison_key(korean) != comparison_key(u"서울시립미술관")
    assert comparison_key("Roca Baños, S.A.") == comparison_key("roca baños sa")
