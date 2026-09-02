"""A page listing twenty products is not a product page.

A real retailer's report opened with "20 of 31 product pages have no Product
markup". Every one of the twenty was a category listing carrying
`CollectionPage` markup, which is the correct type for a listing, and every one
of the eleven real product detail pages already carried `ProductGroup`. The
count of product pages missing markup was zero.

The rule that produced it asked for a price and two buying affordances. A
listing page has both, because it repeats "in stock" and "free shipping" once
per item on it. What a listing does not share with a detail page is how many
different prices it shows, measured on that retailer's own forty shop pages
using its URL scheme as the answer key:

    11 detail pages    1 to 4 distinct prices, median 3
    29 listing pages   2 to 18 distinct prices, median 9
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    DISTINCT_PRICE_CEILING, PRODUCT_SIGNAL_MINIMUM, detect_page_type,
)

DETAIL = ("Walmgate mug. £18.00. In stock. Free shipping over £75. "
          "Add to cart. Ships within 3 working days.")
LISTING = ("Mugs. Walmgate mug £18.00. Ouse mug £22.00. Fossgate mug £26.00. "
           "Micklegate mug £31.00. Stonegate mug £44.00. In stock. Free shipping.")


def _meta(text, jsonld_types=(), headings=None):
    return {"text": text, "jsonld_types": list(jsonld_types),
            "headings": headings or {}, "title": ""}


def test_a_listing_of_many_priced_items_is_a_category():
    assert detect_page_type("https://shop.example/collections/mugs",
                            _meta(LISTING)) == "category"


def test_a_single_priced_item_is_still_a_product():
    """The fix must not cost us the pages the check exists for."""
    assert detect_page_type("https://shop.example/products/walmgate-mug",
                            _meta(DETAIL)) == "product"


def test_the_price_ceiling_is_what_separates_them():
    """Not the buying-signal count, which both page shapes satisfy."""
    from audit_common import _product_signal_count
    assert _product_signal_count(LISTING.lower()) >= PRODUCT_SIGNAL_MINIMUM, (
        "a listing page does carry the buying signals, which is why they cannot "
        "be the discriminator")
    assert DISTINCT_PRICE_CEILING == 5


@pytest.mark.parametrize("declared", ["CollectionPage", "ItemList", "SearchResultsPage"])
def test_a_declared_listing_type_outranks_embedded_product_nodes(declared):
    """A listing embeds one Product node per item; that is not a product page."""
    assert detect_page_type("https://shop.example/c/mugs",
                            _meta(LISTING, [declared, "Product"])) == "category"


def test_a_declared_product_type_is_still_honoured():
    assert detect_page_type("https://shop.example/c/walmgate-mug",
                            _meta(DETAIL, ["Product"])) == "product"
