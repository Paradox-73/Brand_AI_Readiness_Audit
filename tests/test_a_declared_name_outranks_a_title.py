# -*- coding: utf-8 -*-
"""The brand a Thai school was given, and the one it declares on 48 pages.

Measured on a school site written in Thai:

    the brand resolved to หน้าหลัก - the Thai for "Home page" - taken from the
    <title>, while the site declares its real name in Organization markup on
    48 of its 60 pages, and the report reprints that name in its own
    paste-ready snippet. The wrong name then reached the verdict and produced
    a Wikidata collision finding against "Wikimedia main page".

Earlier fixes closed the `og:site_name` half of this (a template with an empty
brand slot) and the English-title half (a section word, a line of marketing
copy). What was left is precedence: `crawl.py` ranks a declaration found away
from the homepage *below* the homepage's own title, so a site whose identity
markup is site-wide but whose homepage template omits it loses its own name to
a page-title segment.

The tests run through `crawl.detect_brand` and then `audit_common.resolve_brand`
- the two halves of the real path, in order - because the defect is in how the
second reads what the first recorded, and a test that hand-builds the brand
block cannot see that.

Every school, charity and host below is invented.
"""

from __future__ import annotations

import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    PAGES_A_DECLARATION_SPEAKS_FROM, resolve_brand,
)
from crawl import detect_brand  # noqa: E402

# An invented Thai school. `หน้าหลัก` is the word for "home page" and is what
# the site's title template puts in front of its name.
SCHOOL = u"โรงเรียนสระวดีวิทยา"
HOME_PAGE_WORD = u"หน้าหลัก"
THAI_BODY = SCHOOL + u" ยินดีต้อนรับ นักเรียนและผู้ปกครองทุกท่าน"


def _page(url, title, page_type="other", jsonld=(), og=None, h1=None, body=""):
    return {
        "url": url, "title": title, "status": 200, "page_type": page_type,
        "jsonld": list(jsonld), "og": og or {},
        "headings": {"h1": [h1 if h1 is not None else SCHOOL], "h2": [], "h3": []},
        "body_text": body or THAI_BODY,
        "links": {"internal": [], "external": []},
    }


def _school(declaring_pages=48):
    """The 60-page crawl, with `Organization` on the deep pages and not at home."""
    org = [{"@type": "Organization", "name": SCHOOL}]
    pages = [_page("https://sarawadee.test/",
                   HOME_PAGE_WORD + u" | " + SCHOOL, "home")]
    for index in range(declaring_pages):
        pages.append(_page("https://sarawadee.test/news/{}".format(index),
                           u"ข่าว | " + SCHOOL, "article", jsonld=org))
    return {"origin": "https://sarawadee.test", "site": "sarawadee.test",
            "pages": pages, "brand": detect_brand(pages, "https://sarawadee.test")}


def test_the_crawl_still_records_what_it_measured():
    """The defect, unchanged where it is measured. `crawl.py` reads a title
    segment and ranks it above a declaration found away from the homepage, and
    this fix does not rewrite that record - it decides differently from it."""
    snapshot = _school()
    assert snapshot["brand"]["name"] == HOME_PAGE_WORD
    assert snapshot["brand"]["source"] == "title-part-0"


def test_the_name_the_site_declares_is_the_name_the_audit_adopts():
    """The failure in full: the report addressed a school as "Home
    page" throughout, in the verdict, in the top sheet and in a knowledge-base
    lookup that found "Wikimedia main page"."""
    brand = resolve_brand(_school())
    assert brand["name"] == SCHOOL
    assert brand["source"].startswith("jsonld:Organization")


def test_the_reader_is_told_which_name_lost_and_why():
    """A name every check is keyed to, changed after the crawl recorded it, has
    to carry the measurement that changed it."""
    outranked = resolve_brand(_school())["outranked_names"]
    assert outranked and outranked[0]["name"] == HOME_PAGE_WORD
    assert outranked[0]["source"] == "title-part-0"
    assert "48" in outranked[0]["why"], outranked[0]["why"]


def test_a_declaration_on_one_page_of_sixty_still_loses_to_the_homepage_title():
    """The guard in the other direction, and the reason `crawl.py` demotes an
    off-homepage declaration at all. A national charity declared no identity on
    its homepage, its about page or its contact page, and an `Organization`
    name on exactly one page: a store listing for a ring binder, naming the
    training subsidiary that runs the shop. That name became the brand for the
    whole report and headed the generated llms.txt."""
    org = [{"@type": "Organization", "name": "Example Training Services"}]
    body = ("Example Relief works with communities across the region. "
            "Example Training Services runs the online shop.")
    pages = [
        _page("https://exrelief.test/", "Example Relief | Home", "home",
              h1="Example Relief", body=body),
        _page("https://exrelief.test/about/us", "About us", "about",
              h1="Example Relief", body=body),
        _page("https://exrelief.test/news/appeal", "Winter appeal", "article",
              h1="Example Relief", body=body),
        _page("https://exrelief.test/store/binder/1.html", "Ring binder",
              "product", jsonld=org, h1="Ring binder", body=body),
    ]
    snapshot = {"origin": "https://exrelief.test", "site": "exrelief.test",
                "pages": pages,
                "brand": detect_brand(pages, "https://exrelief.test")}
    brand = resolve_brand(snapshot)
    assert brand["name"] == "Example Relief"
    assert brand["outranked_names"] == []


def test_the_line_between_them_is_how_many_pages_declared_it():
    """One page is that page's own name; more than one is the template every
    page shares. Asserted against the constant so the boundary cannot move
    without a test noticing."""
    assert PAGES_A_DECLARATION_SPEAKS_FROM == 2
    few = resolve_brand(_school(declaring_pages=1))
    assert few["name"] == HOME_PAGE_WORD
    enough = resolve_brand(_school(declaring_pages=2))
    assert enough["name"] == SCHOOL


def test_a_declared_name_that_is_a_broken_title_template_is_still_refused():
    """The guard an earlier fix installed, from the other side. A theme
    writing `"<brand> - <tagline>"` with no brand configured declares
    "- Empowering communities", and that must not be promoted over anything."""
    org = [{"@type": "Organization", "name": "- Empowering communities"}]
    body = ("Truefarm brings you fresh, chemical-free vegetables from our farm. "
            "Empowering communities across the district.")
    pages = [
        _page("https://truefarm.test/", "Truefarm | Home", "home",
              h1="Truefarm", body=body),
        _page("https://truefarm.test/about", "About", "about", jsonld=org,
              h1="Truefarm", body=body),
        _page("https://truefarm.test/contact", "Contact", "contact", jsonld=org,
              h1="Truefarm", body=body),
    ]
    snapshot = {"origin": "https://truefarm.test", "site": "truefarm.test",
                "pages": pages,
                "brand": detect_brand(pages, "https://truefarm.test")}
    brand = resolve_brand(snapshot)
    assert brand["name"] != "Empowering communities"
    assert brand["name"] != "- Empowering communities"


def test_a_declared_name_no_part_of_the_site_spells_is_still_refused():
    """The second guard: a name declared in markup and written nowhere a
    reader can see it. Two pages declare it, so only the spelling test stands
    between it and the brand."""
    org = [{"@type": "Organization", "name": "Coastal Freight Holdings"}]
    body = ("Truefarm brings you fresh, chemical-free vegetables from our farm "
            "to your table, picked the morning they are delivered.")
    pages = [
        _page("https://truefarm.test/", "Truefarm | Home", "home",
              h1="Truefarm", body=body),
        _page("https://truefarm.test/about", "About", "about", jsonld=org,
              h1="Truefarm", body=body),
        _page("https://truefarm.test/contact", "Contact", "contact", jsonld=org,
              h1="Truefarm", body=body),
    ]
    snapshot = {"origin": "https://truefarm.test", "site": "truefarm.test",
                "pages": pages,
                "brand": detect_brand(pages, "https://truefarm.test")}
    assert resolve_brand(snapshot)["name"] != "Coastal Freight Holdings"


def test_a_site_whose_homepage_declares_its_name_is_untouched():
    """The safety property. Where the crawl already chose a declared name,
    nothing here runs at all."""
    org = [{"@type": "Organization", "name": SCHOOL}]
    pages = [_page("https://sarawadee.test/", HOME_PAGE_WORD + u" | " + SCHOOL,
                   "home", jsonld=org)]
    snapshot = {"origin": "https://sarawadee.test", "site": "sarawadee.test",
                "pages": pages,
                "brand": detect_brand(pages, "https://sarawadee.test")}
    brand = resolve_brand(snapshot)
    assert brand["name"] == SCHOOL
    assert brand["source"] == "jsonld:Organization"
    assert brand["outranked_names"] == []
