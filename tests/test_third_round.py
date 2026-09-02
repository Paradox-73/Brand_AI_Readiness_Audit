"""What the third round of real-site verification found.

Sixteen more sites, several behind edge protection. The headline was a crash:
a change made earlier the same day to strip hidden markup killed the crawl
outright on three of them, producing no snapshot, no report and a Python stack
trace. Everything else here is a false finding that reached a report.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    detect_page_type, find_prices, main_text, make_soup, price_value,
    visible_soup,
)
from page_extract import extract_page  # noqa: E402
from robots_parser import benign_disallow  # noqa: E402


# --------------------------------------------------------------------------
# The crash
# --------------------------------------------------------------------------

def test_a_hidden_block_containing_a_styled_child_does_not_crash_the_crawl():
    """`select` lists every match before anything is removed.

    Decomposing a parent leaves its already-matched descendants in the list
    with `.attrs` set to None, and calling `.get` on one of those raised
    `AttributeError: 'NoneType' object has no attribute 'get'`. That killed the
    whole run on two major retailers and a hospital group - no report at all,
    just a stack trace. The markup is ordinary: a hidden banner with an
    inline-styled child inside it.
    """
    html = ('<html><body><div style="display:none">'
            '<span style="color:red">hidden inner</span></div>'
            '<main><p>' + ("Real readable content. " * 20) + '</p></main></body></html>')
    text = main_text(make_soup(html))
    assert "hidden inner" not in text
    assert "Real readable content" in text


def test_several_nested_styled_children_all_survive():
    html = ('<html><body><div style="display:none">'
            + "".join('<span style="color:{}">x</span>'.format(colour)
                      for colour in ("red", "blue", "green", "teal", "grey"))
            + '</div><main><p>' + ("Real content here. " * 20)
            + '</p></main></body></html>')
    assert "Real content here" in main_text(make_soup(html))


# --------------------------------------------------------------------------
# Hidden markup, in every extractor rather than one
# --------------------------------------------------------------------------

MODAL_PAGE = (
    '<html><body><header><div class="external-modal" aria-hidden="true">'
    '<h2>Leaving our site</h2>'
    '<p>You are about to leave our website and enter a third party website.</p>'
    '</div></header><main><h1>Walmgate mug</h1><p>'
    + ("Wheel-thrown stoneware made in the Walmgate studio. " * 8)
    + '</p></main></body></html>')


def test_a_hidden_modal_reaches_none_of_the_text_fields():
    """It was the sentence offered as the quote for thirteen pages.

    `main_text` stripped it; the paragraph, section and heading extractors read
    the raw document and did not, so the citation table, the answer-first check
    and the heading list all still carried it.
    """
    record = extract_page("https://example.test/", "https://example.test/", 200, {},
                          MODAL_PAGE, [], 10, 0, "seed", "https://example.test")
    assert "third party" not in (record.get("body_text") or "")
    assert "third party" not in str(record.get("paragraphs") or [])
    assert "third party" not in str(record.get("sections") or [])
    assert "Leaving our site" not in str(record.get("headings") or {})
    assert "Walmgate mug" in str(record.get("headings") or {})


def test_visible_content_is_untouched():
    clone = visible_soup(make_soup(
        '<html><body><main><p>Kept.</p><div hidden><p>Dropped.</p></div>'
        '</main></body></html>'))
    text = clone.get_text(" ")
    assert "Kept." in text and "Dropped." not in text


# --------------------------------------------------------------------------
# The announcement bar
# --------------------------------------------------------------------------

BANNER_PAGE = (
    '<html><body><div class="promo-bar__text">'
    'Free standard shipping on orders $40+ | $60 CAD+ | $110 AUD+ | R$670+'
    '</div><main><h1>Balm Dotcom</h1><p>'
    + ("A lip balm in a tube. " * 12) + '$16.00</p></main></body></html>')


def test_a_shipping_banner_is_not_the_prices_on_the_page():
    """The retailer's own correct $16 markup was reported as contradicting
    "the prices shown on the page (40, 60, 110, 670)" - four free-shipping
    thresholds, one per locale, stacked in the delivered HTML and switched
    client-side. The same report's appendix called that markup complete.
    """
    text = main_text(make_soup(BANNER_PAGE))
    assert find_prices(text) == ["$16.00"]
    assert "CAD" not in text


@pytest.mark.parametrize("class_name", [
    "announcement-bar", "promo-bar", "promobar", "announce-bar__inner",
    "site-top-bar", "usp-bar", "marquee-text",
])
def test_every_common_banner_class_is_treated_as_chrome(class_name):
    html = ('<html><body><div class="' + class_name + '">Free shipping over $40</div>'
            '<main><p>' + ("Real content. " * 20) + '</p></main></body></html>')
    assert "$40" not in main_text(make_soup(html))


# --------------------------------------------------------------------------
# Prices, in whichever way the world writes them
# --------------------------------------------------------------------------

@pytest.mark.parametrize("written,expected", [
    ("28,00 EUR", 28.0), ("57,99 EUR", 57.99), ("40,99", 40.99), ("15,0", 15.0),
    ("$480", 480.0), ("480.00", 480.0), ("$1,500.00", 1500.0),
    ("2,800", 2800.0), ("1.234,56", 1234.56), ("1,234.56", 1234.56),
])
def test_a_decimal_comma_is_a_decimal_point(written, expected):
    """A French bakery's "28,00 EUR" was read as 2800, so its own correct
    structured data was reported as contradicting its own page - four times,
    second in what to fix first, with up to a day of work recommended.
    """
    assert price_value(written) == expected


# --------------------------------------------------------------------------
# Slugs that are whole words
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://x.test/products/press-on-sponge-replenishment", "category"),
    ("https://x.test/products/contact-lens-solution", "category"),
    ("https://x.test/press", "press"),
    ("https://x.test/press-kit", "press"),
    ("https://x.test/about-us", "about"),
    ("https://x.test/contact-us", "contact"),
])
def test_a_slug_must_be_the_word_not_the_start_of_one(url, expected):
    """"press" is a strong slug, so `/products/press-on-sponge-replenishment`
    became a press page - and a nail-varnish product was then reported as an
    article carrying no publication date.
    """
    assert detect_page_type(url, {"text": "", "jsonld_types": [],
                                  "headings": {}, "title": ""}) == expected


# --------------------------------------------------------------------------
# robots.txt paths that are not pages
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rule", ["/26657478", "/services", "/recommendations/products"])
def test_platform_internals_are_not_real_content(rule):
    """`/services` was the number one fix on a shop's report. It returns 404,
    and the shop's own robots.txt calls it platform-internal in a comment
    directly above it - a comment robots parsers drop.
    """
    assert benign_disallow(rule) is True


@pytest.mark.parametrize("rule", ["/blog", "/products", "/about", "/policies/", "/123abc"])
def test_real_paths_are_still_reported(rule):
    assert benign_disallow(rule) is False
