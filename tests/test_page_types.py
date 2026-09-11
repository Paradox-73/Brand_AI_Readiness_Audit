# -*- coding: utf-8 -*-
"""What kind of page each URL is, and what the audit may say when it cannot tell.

`detect_page_type` decides which checks see a page at all. A page typed `other`
is dropped by `content_only`, so a wrong type is not a wrong label - it is a
check that never ran, reported as a site that has nothing of that kind.

Each row below is a real site where the type was wrong and the report was wrong
in the same move:

  `/products/press-on-sponge-...`   typed `press`, so a nail-varnish product was
                                    reported as an article carrying no
                                    publication date
  `/pizzerias/`, `/restaurants/`    43 branch pages typed `other`, dropped from
                                    every content check, and per-branch
                                    `LocalBusiness` markup - the highest-value
                                    fix a chain can make - never recommended
  `/news/previous/`                 asked for Article markup, producing a
                                    snippet whose headline was "News archive"
  `/de/`                            not a homepage, so on a path-prefixed site
                                    the audit's idea of "the homepage" snapped
                                    to whatever sat at the root
  `/?search=Artemis`                "remove noindex from these pages" ranked
                                    second in what to fix first, on five
                                    search-result URLs where `noindex` is what
                                    every search-engine guideline asks for

The last section is the honest answer when the table cannot decide: a crawl
that is mostly `other` says so, instead of letting four checks decline with
"no article or blog post pages were crawled" about a crawl holding 26 press
releases.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    detect_page_type, is_search_result_page, looks_like_content, unclassified_note,
)


def _typed(url, **fields):
    signals = {"text": "", "headings": [], "jsonld_types": [], "title": ""}
    signals.update(fields)
    return detect_page_type(url, signals)


# --------------------------------------------------------------------------
# A slug is a whole word, not the start of one
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://x.test/products/press-on-sponge-replenishment", "category"),
    ("https://x.test/products/contact-lens-solution", "category"),
    ("https://x.test/press", "press"),
    ("https://x.test/about-us", "about"),
])
def test_a_slug_must_be_the_word_not_the_start_of_one(url, expected):
    """"press" is a strong slug, so `/products/press-on-sponge-replenishment`
    became a press page - and a nail-varnish product was then reported as an
    article carrying no publication date.
    """
    assert detect_page_type(url, {"text": "", "jsonld_types": [],
                                  "headings": {}, "title": ""}) == expected


# --------------------------------------------------------------------------
# A place word in the plural is a place; in the singular it is a shop front
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/our-shops", "/store-locator", "/where-we-are", "/clinics", "/restaurants",
])
def test_a_locations_page_is_recognised_however_it_is_named(path):
    """A pizza chain kept its sixty branches at `/pizzerias/` and
    `/restaurants/`. No slug matched, so all 43 branch pages in the crawl were
    typed "other", dropped from every content check, and per-branch
    `LocalBusiness` markup - the highest-value fix a chain can make - was never
    recommended at all.
    """
    assert _typed("https://x.test" + path) == "location"


@pytest.mark.parametrize("path", ["/shop", "/shop/dresses", "/office"])
def test_a_singular_shop_front_is_not_a_locations_page(path):
    """`/shop` is the way into an online shop; `/shops` is a list of branches.
    One letter apart, two kinds of page - so matching the singular would have
    turned every e-commerce entry point into a locations page, which is worse
    than the miss it was fixing."""
    assert _typed("https://x.test" + path) != "location"


@pytest.mark.parametrize("path", [
    "/services/office-fitout", "/restaurant-week-menu", "/locate-a-vein",
])
def test_the_new_shapes_do_not_capture_ordinary_pages(path):
    assert _typed("https://x.test" + path) != "location"


# --------------------------------------------------------------------------
# An index of articles is not an article
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/news/previous/", "category"),
    ("/blog/all", "category"),
    ("/blog/previously-unseen-photographs", "article"),
    ("/news/my-archive-of-photos", "article"),
])
def test_an_archive_listing_is_not_asked_for_article_markup(path, expected):
    """`/news/previous/` is a news archive. Demanding Article markup on it
    produced a snippet whose headline was "News archive" - telling a machine
    the archive page is a piece of journalism published on a date."""
    assert _typed("https://x.test" + path, lang="") == expected


# --------------------------------------------------------------------------
# A localised homepage is a homepage
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,lang,expected", [
    ("https://x.test/", "", "home"),
    ("https://x.test/de/", "de", "home"),
    ("https://x.test/en-gb/", "en-GB", "home"),
    # Not on shape alone: plenty of English sites keep an IT department here.
    ("https://x.test/it", "en", "other"),
    ("https://x.test/de/about", "de", "about"),
])
def test_a_language_prefixed_root_is_home_when_the_page_says_so(url, lang, expected):
    """Only the bare root qualified, so on a path-prefixed site the audit's
    idea of "the homepage" snapped to whatever sat at the root - on a bilingual
    broadcaster, an English redirect target - even when the audit had been
    pointed at the localised entry point explicitly."""
    assert _typed(url, lang=lang) == expected


# --------------------------------------------------------------------------
# noindex on a page that should not be indexed is correct
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://x.test/?search=Artemis",
    "https://x.test/search?q=mars",
    "https://x.test/suche?q=x",
])
def test_a_search_result_page_is_recognised(url):
    """A space agency's report ranked "remove noindex from these pages" second
    in what to fix first. All five were search-result URLs, where `noindex` is
    what every search-engine guideline asks for, and following the advice would
    have published five URLs of somebody else's search history."""
    assert is_search_result_page(url)


@pytest.mark.parametrize("url", [
    "https://x.test/products/searchlight",
    "https://x.test/?page=2",
])
def test_an_ordinary_page_is_not_a_search_result(url):
    assert not is_search_result_page(url)


# --------------------------------------------------------------------------
# A page the table does not recognise can still be content
# --------------------------------------------------------------------------

def test_an_unrecognised_url_with_a_heading_and_text_is_content():
    """Everything the slug table does not recognise is typed `other`, which
    `content_only` then drops - so the checks see a slice and the report
    describes the site."""
    post = {"page_type": "other", "body_text_len": 900, "headings": {"h1": ["Release 5.2"]}}
    assert looks_like_content(post)

    # `legal` is not `other`: it was recognised and left out deliberately.
    privacy = {"page_type": "legal", "body_text_len": 4000,
               "headings": {"h1": ["Privacy"]}}
    assert not looks_like_content(privacy)


# --------------------------------------------------------------------------
# A decline about the classifier is not a decline about the site
# --------------------------------------------------------------------------

def _pages(types):
    return [{"status": 200, "page_type": t, "url": "https://example.test/{}".format(i)}
            for i, t in enumerate(types)]


def test_a_crawl_we_could_not_classify_says_so():
    """A Korean museum's crawl came back with 59 of 60 pages typed `other`, and
    four checks declined with "no article or blog post pages were crawled" -
    about a crawl holding 26 press releases."""
    note = unclassified_note(_pages(["other"] * 9 + ["home"]))
    assert note, "a crawl that is nine tenths unclassified should say so"
    assert "9 of the 10" in note
    assert "not a statement that the site has no page of this kind" in note


def test_a_crawl_we_did_classify_says_nothing_extra():
    """The note has to stay off a normal run, or it is noise on every report.

    It also has to accept the by-type mapping: three of the four checks that
    need it hold the buckets rather than the pages.
    """
    assert unclassified_note(_pages(["home", "about", "article", "product", "other"])) == ""
    by_type = {"other": _pages(["other"] * 9), "home": _pages(["home"])}
    assert "9 of the 10" in unclassified_note(by_type)
