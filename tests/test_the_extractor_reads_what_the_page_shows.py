# -*- coding: utf-8 -*-
"""Three defects found on real Indian and Indonesian shops.

Each is an extractor reading its input wrongly, and each produced a false
finding at the other end.

  1.    A four-digit rupee amount lost its last digit. The page prints
        "₹ 1349" under the product title; the snapshot recorded 134, and
        the offer price was reported at high severity as "not among the prices
        shown on the page, including its size and variant menus (119, 134,
        999)". 119 and 134 are 1199 and 1349 truncated.
  2.    `og:site_name` was split at its colon and the halves compared as
        rivals. All 58 pages declare "The Bicyclist: Handmade Leather Goods"
        and the Organization `name` is the identical string; the report said
        two distinct names are asserted, one of which no page carries.
  3.    A breadcrumb the page renders, and an add-to-basket control it
        renders, were both invisible. Three deep pages were reported as
        showing "no breadcrumb trail and no link up to the section they sit
        in" over a plain `<nav>` of ancestor links, and the same product page
        recorded `add_to_cart_controls: {"count": 0}`.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "audit-orchestrator", "scripts"))

from audit_common import find_prices, make_soup, price_value  # noqa: E402
from crawl import detect_brand  # noqa: E402
from page_extract import (  # noqa: E402
    _add_to_cart_controls, _breadcrumb, retype_from_the_page_itself,
)


# --------------------------------------------------------------------------
# The price reader drops the last digit
# --------------------------------------------------------------------------

# What the shops this audit was tested on actually print, and what each one
# costs. Every notation here was taken from a real site: the rupee amount
# with a space and no grouping is the one that broke, and the other four are
# here so a fix for it cannot quietly break them.
PRICES_ON_THE_REAL_SHOPS = [
    (u"₹ 1349", 1349.0),        # India, spaced symbol, no grouping
    (u"₹ 1199", 1199.0),
    (u"₹1,349", 1349.0),        # India, tight symbol, grouped
    (u"Rp 1.198.000", 1198000.0),    # Indonesia, dot grouping
    (u"Rp 25000", 25000.0),          # Indonesia, no grouping
    (u"RM 12.90", 12.9),             # Malaysia
    (u"$12.00", 12.0),
    (u"₹999", 999.0),           # three digits: the case that always worked
    (u"309,74 EUR", 309.74),         # comma decimal, code after the figure
]


@pytest.mark.parametrize("printed,value", PRICES_ON_THE_REAL_SHOPS)
def test_a_price_is_read_to_its_last_digit(printed, value):
    """The whole figure, whichever way the market writes it.

    `₹ 1349` recorded 134. A page and its own Offer markup agreed exactly
    and two findings said they contradicted each other, both high severity.
    """
    read = find_prices(printed)
    assert read, printed
    assert read[0].endswith(printed.split()[-1]), \
        "{!r} was read as {!r}".format(printed, read[0])
    assert price_value(read[0]) == value


def test_the_quoted_menu_prices_come_back_whole():
    """The finding's own list - (119, 134, 999) - against the page's own text."""
    text = u"₹ 1349 ₹ 1199 ₹ 999"
    assert sorted(price_value(p) for p in find_prices(text)) == [999.0, 1199.0, 1349.0]


# --------------------------------------------------------------------------
# A name the site published, and the half it did not
# --------------------------------------------------------------------------

DECLARED = u"The Bicyclist: Handmade Leather Goods"


def _bicyclist_pages(count=8):
    """Every page declaring the one long name, in both of the two sources."""
    pages = []
    for index in range(count):
        url = "https://bicyclist.test/" if index == 0 else \
            "https://bicyclist.test/goods/item-{}".format(index)
        pages.append({
            "url": url, "status": 200, "title": DECLARED,
            "page_type": "home" if index == 0 else "product",
            "jsonld": [{"@type": "Organization", "name": DECLARED}],
            "og": {"og:site_name": DECLARED},
        })
    return pages


def test_a_colon_in_og_site_name_is_punctuation_and_not_a_separator():
    """The site published one string; the reader must not manufacture two.

    "Handmade Leather Goods" is 22 characters, two over the piece bound, so
    the value was cut and the bare "The Bicyclist" - which appears on no page
    of the site - was compared against the Organization name as a rival.
    """
    brand = detect_brand(_bicyclist_pages(), "https://bicyclist.test")
    assert brand["authoritative_variants"] == [DECLARED]
    assert list(brand["declared_by"]) == [DECLARED]


def test_no_declared_name_is_a_string_no_page_carries():
    """The general rule, of which the colon is one case.

    A declared name is a string the site published. Whatever the reader splits
    a value into, the fragment is a reading and only the whole value may be
    filed under "the site declared this" - so every key of `declared_by` and
    every entry of `authoritative_variants` has to be findable, verbatim, in
    what some page actually published.
    """
    pages = _bicyclist_pages()
    # A second site shape, with the separator this reader does split on, so
    # the rule is tested where a split really happens rather than only where
    # one was stopped.
    pages += [{
        "url": "https://bicyclist.test/journal", "status": 200, "title": "",
        "page_type": "other", "jsonld": [],
        "og": {"og:site_name": u"Bicyclist | Wheel-thrown stoneware from York"},
    }]
    published = {p["og"]["og:site_name"] for p in pages}
    published |= {n.get("name") for p in pages for n in p["jsonld"]}
    for name in list(brand_names(pages)):
        assert name in published, \
            "{!r} is filed as declared and no page declares it".format(name)


def brand_names(pages):
    brand = detect_brand(pages, "https://bicyclist.test")
    return set(brand["authoritative_variants"]) | set(brand["declared_by"])


def test_the_split_still_decides_what_to_call_the_site():
    """The reading is kept; only its filing changed.

    Two earlier defects are pinned by these: "Doctors Without Borders - USA"
    made the brand "USA", and a government site's "<name> | Startseite" made
    it the German for "home page". The split that fixed both still runs - it
    just no longer records its output as something the site asserted.
    """
    def og(value):
        return [{"url": "https://example.test/", "title": "", "status": 200,
                 "page_type": "home", "jsonld": [], "og": {"og:site_name": value}}]

    brand = detect_brand(og("Example Without Borders - USA"), "https://example.test")
    assert brand["name"] == "Example Without Borders"
    assert brand["authoritative_variants"] == ["Example Without Borders - USA"]

    brand = detect_brand(og("Acme | Wheel-thrown stoneware from York"),
                         "https://example.test")
    assert brand["name"] == "Acme"
    assert brand["declared_by"] == {
        "Acme | Wheel-thrown stoneware from York": ["og:site_name"]}


# --------------------------------------------------------------------------
# A breadcrumb with no name and no markup
# --------------------------------------------------------------------------

PRODUCT_URL = "https://marisel.test/en/face/serum/seastem-marine-essence"

# The trail the real site renders on every product page. No class, no id, no
# aria-label, no `itemListElement` - a plain `<nav>` of ancestor links with a
# slash between them, which is what most hand-built breadcrumbs are.
PLAIN_NAV_TRAIL = (
    '<body><header><a href="/en"><img src="/logo.png" alt="Marisel"></a>'
    '<nav class="menu"><a href="/en/face">Face</a><a href="/en/body">Body</a>'
    '<a href="/en/hair">Hair</a><a href="/en/about">About</a>'
    '<a href="/en/journal">Journal</a><a href="/en/contact">Contact</a></nav>'
    '</header>'
    '<nav><a href="/en">Home</a> / <a href="/en/face">Face</a> / '
    '<a href="/en/face/serum">Serum</a> / Marisel Marine Essence</nav>'
    '<main><h1>Marisel Marine Essence</h1></main></body>'
)


def test_a_plain_nav_of_ancestor_links_is_a_trail():
    """Three deep pages were reported as showing no trail over this markup.

    Both halves of "no breadcrumb trail and no link up to the section they sit
    in" are false of it: the trail is on the screen and its third step is the
    page's own section.
    """
    breadcrumb = _breadcrumb(make_soup(PLAIN_NAV_TRAIL), [], PRODUCT_URL)
    assert breadcrumb["visible"] is True
    assert breadcrumb["ancestor_link_trail"] is True
    # And it is still not machine-readable, which is a different finding and
    # the one that site should get.
    assert breadcrumb["markup"] is False


def test_a_trail_whose_steps_are_separated_by_css_counts():
    """`<ol><li>` with the delimiter in a stylesheet has none in its text."""
    html = ('<body><nav><ol>'
            '<li><a href="/en">Home</a></li>'
            '<li><a href="/en/face">Face</a></li>'
            '<li><a href="/en/face/serum">Serum</a></li>'
            '<li>Marisel Marine Essence</li></ol></nav></body>')
    assert _breadcrumb(make_soup(html), [], PRODUCT_URL)["visible"] is True


def test_a_twelve_item_carousel_is_not_a_trail():
    """The direction an earlier fix guarded, from the other side.

    A row of links to twelve other products is not a trail: they are siblings
    of this page, not the prefixes of its address.
    """
    html = '<body><ul class="you-may-also-like">' + "".join(
        '<li><a href="/en/face/serum/item-{n}">Item {n}</a></li>'.format(n=n)
        for n in range(12)) + '</ul></body>'
    assert _breadcrumb(make_soup(html), [], PRODUCT_URL)["visible"] is False


def test_a_site_wide_menu_is_not_a_trail():
    """A four-item menu on a deep page carries two of that page's ancestors in
    deepening order, and stops one level above the section the page is in. A
    trail reaches the page's own parent."""
    html = ('<body><nav><a href="/en">Home</a><a href="/en/face">Face</a>'
            '<a href="/en/body">Body</a><a href="/en/about">About</a></nav></body>')
    assert _breadcrumb(make_soup(html), [], PRODUCT_URL)["visible"] is False


def test_a_masthead_link_home_is_not_a_trail():
    """Every page of every site links home from its logo."""
    html = ('<body><header><a href="/en">Marisel</a></header>'
            '<main><h1>Marisel Marine Essence</h1><p>A serum.</p></main></body>')
    assert _breadcrumb(make_soup(html), [], PRODUCT_URL)["any"] is False


def test_a_shallow_page_has_nothing_to_climb():
    """On `/en` a link to `/` is the whole site, not a step above this page."""
    html = '<body><nav><a href="/">Home</a> / <a href="/en">EN</a></nav></body>'
    assert _breadcrumb(make_soup(html), [], "https://marisel.test/en")["visible"] is False


# --------------------------------------------------------------------------
# The add-to-basket control that was not seen
# --------------------------------------------------------------------------

# Four buy controls, none of which the six-attribute reader could see. The
# first is the shape seen in the wild.
CONTROLS_THAT_WERE_INVISIBLE = [
    '<form action="/en/cart" method="post">'
    '<button type="submit" class="btn btn-primary">Add to Cart</button></form>',
    '<button data-action="add-to-cart" class="btn">Beli Sekarang</button>',
    '<button onclick="addToCart(4821)" class="btn">Pesan</button>',
    '<a class="btn" data-cart-url="/en/cart/add/4821">Order</a>',
]


@pytest.mark.parametrize("markup", CONTROLS_THAT_WERE_INVISIBLE)
def test_a_buy_control_is_counted_however_it_is_written(markup):
    """`add_to_cart_controls: {"count": 0}` on a page rendering one.

    Two of these carry the identifier in an attribute the reader did not look
    at - the tuple held six names and no `data-` attribute, though its comment
    said otherwise - and one carries it nowhere but the label on the button.
    """
    controls = _add_to_cart_controls(make_soup("<body><main>" + markup + "</main></body>"))
    assert controls["count"] == 1, markup
    assert controls["evidence"], markup


def test_a_shelf_with_a_quick_add_button_still_counts_as_many():
    """The count is the whole shelf-versus-detail distinction, so widening
    what counts as a control must not blur it: eight tiles with a quick-add
    button under each are eight controls and never one item for sale."""
    html = "<body>" + "".join(
        '<div class="tile"><h3>Item {n}</h3>'
        '<button class="btn">Add to Cart</button></div>'.format(n=n)
        for n in range(8)) + "</body>"
    assert _add_to_cart_controls(make_soup(html))["count"] == 8


def test_prose_about_adding_to_a_basket_is_not_a_control():
    """The label reading is a word list, so it is held to a control element
    and to a label short enough to be one."""
    html = ('<body><p>Add to cart to reserve your place in the next batch, and we '
            'will email you when it ships.</p>'
            '<a href="/en/help">Add to cart help and returns policy explained</a>'
            '</body>')
    assert _add_to_cart_controls(make_soup(html))["count"] == 0


def test_a_page_the_control_promotes_is_still_only_promoted_once():
    """A control found by its label reaches page typing like any other, and
    the four conditions that guard the promotion are unchanged."""
    typed, why = retype_from_the_page_itself("category", PRODUCT_URL, {
        "add_to_cart_controls": _add_to_cart_controls(make_soup(
            '<body><main><h1>Marisel Marine Essence</h1>'
            '<form action="/en/cart" method="post">'
            '<button type="submit">Add to Cart</button></form></main></body>')),
        "meta": {}, "body_text": u"Marisel Marine Essence ₹ 1349",
        "headings": {"h1": ["Marisel Marine Essence"]},
        "links": {"internal": [], "main": []}, "jsonld_types": [],
        "title": "Marisel Marine Essence",
    })
    assert typed == "product"
    assert "Add to Cart" in why
