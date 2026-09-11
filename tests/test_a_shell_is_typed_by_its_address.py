# -*- coding: utf-8 -*-
"""A client-rendered storefront, and a meta tag that answers to two names.

Two defects measured on ordinary sites - not adversarial ones.

The first, on a React storefront whose delivered HTML is a 21 KB shell:

    0 of 4 `/product/<slug>` URLs were typed `product`, all four came out
    `category`, and the `/explore/<shelf>` listing URLs came out `other`

Two things followed, and the second cost more. One finding's evidence read
"Seen on 4 of the 4 category pages this crawl read" while every example URL it
printed was a `/product/` one. And the Product/Offer recommendation - the most
valuable thing that site could be told - was suppressed through a decline
reading "no product detail pages were detected on this site", contradicted by
four `/product/` URLs in the same report.

`retype_from_the_page_itself` was already there and could not help: it promotes
a listing-shaped address on the page's own evidence - one buy control, few
prices, one subject - and a shell has no content to type from. The rule this
file pins is the one for that case: **where the page delivers nothing, the
address settles the type, because it is the only evidence there is.** It is
narrow on purpose. A page that delivers a grid decides its own type, and a grid
must never be one thing for sale.

The second, on a static site:

    `<meta name="twitter:description" property="og:description">` is filed only
    under the `name`, so `og:description` is reported absent for a tag in View
    Source. That is what Jekyll's SEO plugin emits, so it misfires across a
    large slice of the static-site web.

Every host and brand below is invented.
"""

from __future__ import annotations

import sys

import pytest
from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    SkillResult, the_address_names_one_item, the_item_grouping_word,
)
from page_extract import (  # noqa: E402
    TYPE_FROM_THE_ADDRESS_TEXT_FLOOR, extract_page,
)

SITE = "https://kaviro.test"

# The delivered document, in full. A mount point, a bundle, and a line for the
# minority of consumers that cannot run the bundle - which is every byte of
# content this server sends.
SHELL = ("<html lang=\"en\"><head><title>{title}</title>"
         "<meta name=\"viewport\" content=\"width=device-width\">"
         "<script defer src=\"/static/js/main.8f2b1c.js\"></script></head>"
         "<body><div id=\"root\"></div>"
         "<noscript>You need to enable JavaScript to run this app.</noscript>"
         "</body></html>")

SLUGS = ["canvas-tote-olive", "linen-apron-clay", "waxed-holdall-navy",
         "cotton-runner-sand"]
SHELVES = ["kitchen", "bags", "table"]


def _extract(path, html):
    return extract_page(url=SITE + path, final_url=SITE + path, status=200,
                        headers={"content-type": "text/html"}, html=html,
                        redirect_chain=[], elapsed_ms=14, depth=1,
                        source="link", origin=SITE)


def _storefront():
    """The crawl, as this server delivers it: every page the same shell."""
    pages = [_extract("/product/" + slug, SHELL.format(title="Kaviro"))
             for slug in SLUGS]
    pages += [_extract("/explore/" + shelf, SHELL.format(title="Kaviro"))
              for shelf in SHELVES]
    pages.append(_extract("/", SHELL.format(title="Kaviro")))
    return pages


# --------------------------------------------------------------------------
# The typing, in both directions
# --------------------------------------------------------------------------

def test_the_four_product_addresses_are_typed_product():
    """0 of 4 before, on a page whose own content cannot say anything."""
    products = [p for p in _storefront() if p["page_type"] == "product"]
    assert len(products) == len(SLUGS), sorted(
        (p["url"], p["page_type"]) for p in _storefront())
    assert all("/product/" in p["url"] for p in products)


def test_the_shelves_are_typed_as_listings_and_not_as_documents():
    """The other half of the inversion. `/explore/<shelf>` matched no word in
    the address vocabulary, so every shelf was typed `other` - the type that is
    graded by the hygiene checks and skipped by the listing ones."""
    shelves = [p for p in _storefront() if "/explore/" in p["url"]]
    assert shelves and all(p["page_type"] == "category" for p in shelves), [
        (p["url"], p["page_type"]) for p in shelves]


def test_the_reason_names_the_measurement_and_the_address_scheme():
    """A type every downstream check is keyed to, decided against what the
    classifier said, has to carry the sentence that decided it."""
    page = _extract("/product/" + SLUGS[0], SHELL.format(title="Kaviro"))
    why = page["page_type_evidence"]
    assert "/product/" in why and "delivered nothing" in why, why


def test_the_recommendation_that_was_suppressed_now_reaches_the_site():
    """The consequence that cost more than the false positive: with no
    product page on the site, `Product` and `Offer` are asked for nowhere, and
    the decline read "no product detail pages were detected on this site" in a
    report printing four `/product/` addresses."""
    import importlib.util
    import os
    from conftest import ROOT
    spec = importlib.util.spec_from_file_location(
        "sd_for_shell_typing", os.path.join(
            ROOT, "skills", "structured-data-audit", "scripts", "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    by_type = {}
    for page in _storefront():
        by_type.setdefault(page["page_type"], []).append(page)
    result = SkillResult("structured-data-audit")
    module._check_product(result, by_type, {"name": "Kaviro"})
    findings = [f for f in result.findings if f["id_hint"] == "no-product-schema"]
    assert findings, "the storefront is still never asked for Product markup"
    assert findings[0]["affected_page_count"] == len(SLUGS)


def test_a_promoted_page_is_one_the_markup_check_still_grades():
    """A page promoted here and set aside there is a page nothing ever grades,
    which is the miss dressed up as a fix."""
    import importlib.util
    import os
    from conftest import ROOT
    spec = importlib.util.spec_from_file_location(
        "sd_for_shell_grading", os.path.join(
            ROOT, "skills", "structured-data-audit", "scripts", "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for page in _storefront():
        if page["page_type"] == "product":
            assert module._not_the_type_it_was_given(page) == "", page["url"]


# --------------------------------------------------------------------------
# The guard: the address may only speak where the page said nothing
# --------------------------------------------------------------------------

def _grid(count=36):
    """A shelf under a product-shaped address: tiles, prices, one heading."""
    tiles = "".join(
        "<div class=\"tile\"><a href=\"/product/candle-{n}\">Candle {n}</a>"
        "<span>GBP {n}.00</span></div>".format(n=n + 1) for n in range(count))
    return ("<html><head><title>Candles</title></head><body><main>"
            "<h1>Candles</h1>{}</main></body></html>".format(tiles))


def test_a_grid_of_thirty_six_tiles_under_a_product_address_is_not_one_item():
    """The rule this correction must not undo. An earlier fix stopped a grid
    being typed `product` and told to publish `Product` markup, which would
    declare a whole shelf to be one item at one price."""
    page = _extract("/products/candles", _grid())
    assert page["page_type"] != "product", page["page_type_evidence"]


def test_a_page_that_delivered_a_sentence_is_typed_by_that_sentence():
    """The floor is what the *server sent*, chrome included - not the copy left
    after the chrome is stripped, which is the field an earlier shell defect
    was misread from."""
    html = ("<html><head><title>Returns</title></head><body>"
            "<nav><a href=\"/\">Home</a></nav><main><h1>Returns</h1>"
            "<p>" + ("Send it back within thirty days. " * 12) + "</p>"
            "</main></body></html>")
    page = _extract("/product/returns-policy", html)
    assert page["spa_shell"]["visible_text_len"] > TYPE_FROM_THE_ADDRESS_TEXT_FLOOR
    assert page["page_type_evidence"] == "", "the page had content to type from"


@pytest.mark.parametrize("path,expected", [
    ("/product/canvas-tote", True),
    ("/products/canvas-tote", True),
    ("/en/gb/product/canvas-tote", True),
    ("/item/48211", True),
    ("/sku/48211", True),
    ("/collections/all/products/canvas-tote", True),
    ("/products", False),            # the way in to the shelf, not an item
    ("/product", False),
    ("/collections/candles", False),  # the site saying "a list"
    ("/products/all", False),         # an archive index
    ("/search?q=product", False),
    ("/explore/kitchen", False),
    ("/about", False),
])
def test_which_addresses_name_one_item(path, expected):
    assert the_address_names_one_item(SITE + path) is expected, path


def test_the_evidence_quotes_the_grouping_word_the_site_actually_uses():
    """`/product/` and `/sku/` are different claims by different shops."""
    assert the_item_grouping_word(SITE + "/sku/48211") == "sku"
    assert the_item_grouping_word(SITE + "/en/product/tote") == "product"
    assert the_item_grouping_word(SITE + "/about") == ""


# --------------------------------------------------------------------------
# One tag, two names, both of them read
# --------------------------------------------------------------------------

JEKYLL = ("<html><head><title>A field guide</title>"
          "<meta name=\"twitter:description\" property=\"og:description\" "
          "content=\"Notes on hedgerow plants, month by month.\">"
          "<meta property=\"og:title\" content=\"A field guide\">"
          "</head><body><main><h1>A field guide</h1>"
          "<p>Notes on hedgerow plants, month by month, with photographs taken "
          "on the walk between the station and the allotments.</p>"
          "</main></body></html>")


def test_a_tag_carrying_both_attributes_is_filed_under_both():
    """The report said "`og:description` is absent from 60 of 60 pages" about a
    tag the reader can see in View Source."""
    page = _extract("/guide/hedgerows", JEKYLL)
    assert page["og"]["og:description"] == "Notes on hedgerow plants, month by month."
    assert page["twitter"]["twitter:description"] == (
        "Notes on hedgerow plants, month by month.")


def test_the_ordinary_single_named_tags_are_unchanged():
    page = _extract("/guide/hedgerows", JEKYLL)
    assert page["og"]["og:title"] == "A field guide"
    assert "og:title" not in page["twitter"]


def test_a_robots_directive_carrying_two_names_is_read_under_both():
    """The robots keys take a different path through the reader - they are
    joined rather than first-wins - so they are asserted separately."""
    html = ("<html><head><title>Draft</title>"
            "<meta name=\"robots\" property=\"robots\" content=\"noindex\">"
            "</head><body><main><h1>Draft</h1>"
            "<p>Not ready for readers yet, and the directive says so.</p>"
            "</main></body></html>")
    page = _extract("/drafts/1", html)
    assert page["declares_noindex"] is True
    assert "noindex" in page["meta_robots"]
