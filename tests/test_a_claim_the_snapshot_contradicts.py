# -*- coding: utf-8 -*-
"""Three findings the run's own data disproved.

Each of these was a sentence in a published report that the same run already
held the answer to. Nothing here needed a fetch to check: the contradiction is
inside one document.

  a product page called a shelf     `/products/box-sling-bag-in-olive-green-belle`
                                    carries `Product` with an `Offer` - 5300 INR,
                                    InStock - and was listed as "lists other
                                    pages rather than describing one", while the
                                    same report's "What an assistant would
                                    quote" table quoted it as a product. The
                                    same shape on two more product pages of a
                                    second shop.
  a complete Offer called absent    `(no offers object)` about a page whose
                                    snapshot in the same run stores
                                    `offers[0].priceSpecification[0]` with the
                                    price and the currency in it. Twelve
                                    identical pages, four of them named.
  a docs tree called a shop         "161 of the 3250 URLs this sitemap lists
                                    (5.0%) are cart, checkout, account or
                                    wishlist addresses", on a sitemap with zero
                                    of those and 157 URLs under `/api/`. The fix
                                    would have deleted a database project's
                                    C-API reference from its own sitemap.

Every host here is invented.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult, is_forbidden_path  # noqa: E402
from page_extract import extract_page  # noqa: E402


def _module(name, alias):
    path = os.path.join(ROOT, "skills", name, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module("structured-data-audit", "sd_for_contradiction_tests")
CA = _module("crawl-access-audit", "ca_for_contradiction_tests")

SHOP = "https://olive-satchels.test"


def _page(url, page_type="product", title="", links=(), jsonld=(), body_text="",
          headings=None):
    """A snapshot page record carrying only the keys these checks read."""
    return {
        "url": SHOP + url, "page_type": page_type, "status": 200, "title": title,
        "canonical": "", "headings": headings or {}, "paragraphs": [],
        "body_text": body_text,
        "links": {"internal": [{"url": SHOP + u, "text": t} for u, t in links],
                  "nav": [], "footer": [], "external": []},
        "jsonld": [dict(node) for node in jsonld],
        "jsonld_types": sorted({str(node.get("@type", "")).lower() for node in jsonld}),
        "og": {}, "contact_facts": {},
    }


def _by_type(pages):
    out = {}
    for page in pages:
        out.setdefault(page["page_type"], []).append(page)
    return out


# --------------------------------------------------------------------------
# 1 - a page whose own markup declares Product is never a shelf
# --------------------------------------------------------------------------

# The real page, reduced: one `Product` block with a complete `Offer`, and
# nine links to category addresses under one parent. Nine is over
# `_SHELF_FAMILY_MINIMUM`, which is what the link-counting branch reads and all
# it used to read.
BELLE = {
    "@context": "https://schema.org", "@type": "Product",
    "name": "Belle box sling bag in olive green",
    "description": "Belle is a mid-sized satchel to haul around your stuff easily.",
    "offers": {"@type": "Offer", "price": "5300", "priceCurrency": "INR",
               "availability": "https://schema.org/InStock"},
}

CATEGORY_LINKS = [("/collections/" + handle, handle.replace("-", " ").title())
                  for handle in ("bags", "slings", "totes", "wallets", "belts",
                                 "new-in", "sale", "gifting", "clutches")]


def _detail_page():
    return _page("/products/box-sling-bag-in-olive-green-belle",
                 title="Belle box sling bag in olive green",
                 headings={"h1": ["Belle box sling bag in olive green"]},
                 links=CATEGORY_LINKS, jsonld=[BELLE],
                 body_text="Belle is a mid-sized satchel to haul around your stuff "
                           "easily. Rs. 5300")


def test_a_product_page_is_not_a_shelf_however_many_categories_it_links_to():
    """The link-counting branch read nine outbound category links and called a
    priced product detail page the shelf those categories sit on."""
    detail = _detail_page()
    assert SD._the_shelf_this_page_links_to(detail)[1] >= SD._SHELF_FAMILY_MINIMUM, \
        "the fixture must still trip the link count, or it tests nothing"
    assert SD._why_this_page_is_a_list(detail) == ""
    assert SD._not_the_type_it_was_given(detail) == ""


def test_the_listing_finding_does_not_fire_on_the_page_the_report_quotes():
    """One document, two sentences: the listing finding named this address and
    the citation table quoted it as a product."""
    result = SkillResult("structured-data-audit")
    SD._check_listing_markup(result, _by_type([_detail_page()]))
    assert result.findings == []
    assert result.not_applicable, "a check that stays quiet has to say why"


def test_the_product_check_still_grades_it_as_a_product_page():
    """The other half. Being excluded from the shelf population is worth
    nothing if the page is also dropped out of the product population - the
    Offer check reads `products` to decide whose price is missing."""
    detail = _detail_page()
    result = SkillResult("structured-data-audit")
    SD._check_product(result, _by_type([detail]), {"name": "Olive Satchels"})
    assert result.findings == []
    dropped = json.dumps(result.signals)
    assert detail["url"] not in dropped, \
        "the page was quietly dropped from the product population"
    assert "carry Product markup" in json.dumps(result.not_applicable)


def _grid(count=36, jsonld=()):
    """A grid of item tiles: many links to item pages, and no subheadings."""
    return _page("/shop-all", title="Shop All", headings={"h1": ["Shop All"]},
                 links=[("/product-page/candle-{}".format(n), "Candle {}".format(n))
                        for n in range(count)],
                 jsonld=jsonld,
                 body_text="Shop All. " + " ".join(
                     "Candle {} 24.00".format(n) for n in range(count)))


def test_a_grid_of_thirty_six_links_is_still_a_shelf():
    """The direction this must not reopen: a grid of 36 links to item pages was
    listed under "Product pages with no Product JSON-LD", and `Product` markup
    on it would declare the whole shelf to be one item at one price."""
    why = SD._why_this_page_is_a_list(_grid())
    assert "36" in why and "/product-page" in why


def test_a_grid_whose_template_prints_a_product_per_tile_is_still_a_shelf():
    """The guard is "declares one thing for sale", not "declares Product".
    A theme that emits a block per tile declares thirty-six things for sale, and
    a page selling thirty-six things is a shelf."""
    tiles = [dict(BELLE, name="Candle {}".format(n)) for n in range(36)]
    assert SD._why_this_page_is_a_list(_grid(jsonld=tiles)) != ""
    assert not SD._declares_one_thing_for_sale(_grid(jsonld=tiles))


def test_a_category_address_is_a_listing_whatever_blocks_it_carries():
    """The guard is asked only of the link-counting branch. An address that
    names a grouping is a grouping even where the theme prints one featured
    product on it."""
    collection = _page("/collections/slings", title="Sling bags",
                       headings={"h1": ["Sling bags"]},
                       links=CATEGORY_LINKS, jsonld=[BELLE],
                       body_text="Sling bags. Rs. 5300")
    assert "grouping" in SD._why_this_page_is_a_list(collection)


def test_a_product_a_review_only_mentions_does_not_exempt_the_shelf():
    """A block lifted out of another block is a mention, not a declaration.
    Counting one would let any page carrying a review out of the shelf test."""
    mention = dict(BELLE, _nested_in="itemReviewed")
    del mention["offers"]
    assert not SD._declares_one_thing_for_sale(_grid(jsonld=[mention]))
    assert SD._mentions_another_thing(mention)


# `_why_this_page_is_a_list` has six readings and the guard above was wired
# into one of them, so the other five could publish the same contradiction the
# real report carried. Three of the five are inferences this audit makes about a page
# and are now guarded; two are the site's own address scheme and are not. One
# test per branch, in both directions, so neither half can be undone quietly.


def test_the_crawls_page_type_guess_does_not_outrank_the_pages_own_offer():
    """`is_listing_page` returns True for anything the crawl typed `category`,
    and that typing has been seen inverted on two client-rendered
    storefronts - 0 of 4 `/product/` URLs typed `product` on one of them. A
    priced detail page arrives here already mislabelled."""
    mistyped = _detail_page()
    mistyped["page_type"] = "category"
    assert SD.is_listing_page(mistyped), "the fixture must trip the branch under test"
    assert SD._why_this_page_is_a_list(mistyped) == ""


def test_a_detail_pages_own_section_headings_do_not_make_it_an_index():
    """The subheading share: h2s that are also the labels of links on the same
    page. A product page with in-page jump links to its own sections scores
    three of three and was called a list of other pages."""
    tabbed = _page("/products/belle-satchel", title="Belle satchel",
                   headings={"h1": ["Belle satchel"],
                             "h2": ["Description", "Reviews", "Delivery"]},
                   links=[("/products/belle-satchel#description", "Description"),
                          ("/products/belle-satchel#reviews", "Reviews"),
                          ("/products/belle-satchel#delivery", "Delivery")],
                   jsonld=[BELLE], body_text="Belle is a mid-sized satchel. Rs. 5300")
    assert SD.is_listing_page(tabbed), "the fixture must trip the branch under test"
    assert SD._why_this_page_is_a_list(tabbed) == ""


def test_a_page_with_no_prose_of_its_own_is_still_the_thing_its_offer_prices():
    """"It carries no words of its own" is a reading of the page's prose, and a
    detail page whose description is rendered by script has none to read while
    its `Product` block states the price outright."""
    scripted = _page("/products/belle-satchel", title="Belle satchel",
                     headings={"h1": ["Belle satchel"], "h2": ["Belle satchel"]},
                     links=[("/products/belle-satchel", "Belle satchel")],
                     jsonld=[BELLE], body_text="")
    assert SD._is_only_a_list_of_other_pages(scripted), \
        "the fixture must trip the branch under test"
    assert SD._why_this_page_is_a_list(scripted) == ""


def test_page_two_of_a_products_reviews_is_still_that_product():
    """`?page=2` names an offset into some list on the page, not the page. A
    shop paginating a product's reviews serves the priced item again there."""
    paged = _page("/products/belle-satchel?page=2", title="Belle satchel",
                  headings={"h1": ["Belle satchel"]}, jsonld=[BELLE],
                  body_text="Belle is a mid-sized satchel. Rs. 5300")
    assert SD._PAGINATION_QUERY_RE.search("page=2"), "the fixture must trip the branch"
    assert SD._why_this_page_is_a_list(paged) == ""
    # And the same query on an address that does name a grouping is untouched.
    shelf = _page("/collections/slings?page=2", title="Sling bags",
                  headings={"h1": ["Sling bags"]}, jsonld=[BELLE],
                  body_text="Sling bags. Rs. 5300")
    assert SD._why_this_page_is_a_list(shelf) != ""


def test_a_dated_archive_address_is_a_listing_whatever_blocks_it_carries():
    """The second address reading, held open the same way `/collections` is.
    A page at `/2024/08` is the month it archives, not one priced item."""
    archive = _page("/2024/08", title="August 2024",
                    headings={"h1": ["August 2024"]}, jsonld=[BELLE],
                    body_text="August 2024. Rs. 5300")
    assert "date it archives" in SD._why_this_page_is_a_list(archive)


def test_a_page_declaring_collectionpage_is_a_listing_whatever_else_it_declares():
    """The guard stands down for anything the site itself declares, and a
    `CollectionPage` block is the site answering this question directly.
    Without this a theme printing one featured item on a shelf would exempt it."""
    featured = dict(BELLE)
    shelf = _page("/featured", title="Featured", headings={"h1": ["Featured"]},
                  jsonld=[featured, {"@type": "CollectionPage", "name": "Featured"}])
    assert SD._declares_one_thing_for_sale(shelf), \
        "the fixture must carry exactly one sellable block, or it tests nothing"
    assert SD._the_site_itself_says_this_is_a_list(shelf)
    assert SD._why_this_page_is_a_list(shelf) != ""


def test_every_branch_of_the_shelf_reading_answers_the_same_guard():
    """The defect was one question asked on one branch of six. Read the source
    of `_why_this_page_is_a_list`: every `return` that rests on an inference
    about the page has to sit behind the guard, and the guard is computed once.

    Source-level because the defect is structural - a seventh branch added
    later would reopen it, and no fixture can be written for a branch that does
    not exist yet."""
    import inspect
    source = inspect.getsource(SD._why_this_page_is_a_list)
    assert source.count("sells_one = ") == 1, "the guard must be asked once, not per branch"
    inferences = ("is_listing_page(page)", "_is_only_a_list_of_other_pages(page)",
                  "_PAGINATION_QUERY_RE.search", "_SHELF_FAMILY_MINIMUM")
    for reading in inferences:
        line = next(l for l in source.splitlines() if reading in l and l.lstrip().startswith("if"))
        assert "not sells_one" in line, "unguarded inference: {}".format(line.strip())


# --------------------------------------------------------------------------
# 2 - a complete Offer reported as having none
# --------------------------------------------------------------------------

WASH = "/product/acne-facial-wash-100ml/"


def _woocommerce_product(path, with_review):
    """The shape seen in the wild, built through the real extractor.

    The price is stated where schema.org allows it and where this platform puts
    it - `offers[0].priceSpecification[0]` - and the review carries an
    `itemReviewed` naming the same product again, with no `offers` on it,
    because a review is not a price list. Extraction lifts that second block out
    of the first, so the page arrives declaring two `Product` nodes.
    """
    product = {
        "@type": "Product", "@id": SHOP + path + "#product",
        "name": "Acne facial wash 100ml",
        "offers": [{"@type": "Offer", "availability": "InStock",
                    "priceSpecification": [{"@type": "UnitPriceSpecification",
                                            "price": "11.99",
                                            "priceCurrency": "USD"}]}],
    }
    if with_review:
        product["review"] = [{"@type": "Review", "reviewBody": "Cleared my skin.",
                              "itemReviewed": {"@type": "Product",
                                               "name": "Acne facial wash 100ml"}}]
    html = ('<html><head><title>Acne facial wash</title>'
            '<script type="application/ld+json">{}</script></head>'
            '<body><main><h1>Acne facial wash 100ml</h1><p>$11.99</p>'
            '</main></body></html>').format(
                json.dumps({"@context": "https://schema.org", "@graph": [product]}))
    page = dict(extract_page(url=SHOP + path, final_url=SHOP + path, status=200,
                             headers={}, html=html, redirect_chain=[], elapsed_ms=10,
                             depth=1, source="seed", origin=SHOP))
    page["page_type"] = "product"
    return page


def test_a_review_does_not_make_a_priced_product_unpriced():
    """`(no offers object)` about a page whose own snapshot, in the same run,
    stores the Offer the finding says is absent."""
    page = _woocommerce_product(WASH, with_review=True)
    types = [(node.get("@type"), node.get("_nested_in"))
             for node in SD._nodes_of(page, SD.PRODUCT_TYPES)]
    assert ("Product", "itemReviewed") in types, \
        "the fixture must still produce the lifted mention, or it tests nothing"
    result = SkillResult("structured-data-audit")
    SD._check_product(result, _by_type([page]), {"name": "Clear Skin"})
    assert result.findings == []
    # The contradiction, stated as the test: the snapshot holds the offer.
    assert "priceSpecification" in json.dumps(page["jsonld"])


def test_twelve_identical_pages_are_all_read_the_same_way():
    """Whatever the answer is, one shape has to get one answer. Twelve pages of
    one template, four of them carrying a review, produced four findings and
    eight silences - which is how the inconsistency was visible before anyone
    checked whether the finding was true."""
    pages = [_woocommerce_product("/product/item-{}/".format(n), with_review=n < 4)
             for n in range(12)]
    result = SkillResult("structured-data-audit")
    SD._check_product(result, _by_type(pages), {"name": "Clear Skin"})
    assert result.findings == []


def test_a_product_that_states_no_price_anywhere_is_still_reported():
    """The guard the fix has to survive. A product page whose own block carries
    no Offer at all is markup with a hole in it, and stays reported."""
    page = _page("/product/unpriced-toner/", title="Toner",
                 headings={"h1": ["Toner"]},
                 jsonld=[{"@type": "Product", "name": "Toner 200ml"}])
    result = SkillResult("structured-data-audit")
    SD._check_product(result, _by_type([page]), {"name": "Clear Skin"})
    reported = [f for f in result.findings
                if f["id_hint"] == "product-schema-missing-offer-details"]
    assert reported, "a product block with no Offer must still be reported"
    assert "no offers object" in reported[0]["evidence"]


def _incomplete_offer_finding():
    """The finding on a page whose Offer states its price and omits availability."""
    node = {"@type": "Product", "name": "Acne facial wash 100ml",
            "offers": [{"@type": "Offer",
                        "priceSpecification": [{"@type": "UnitPriceSpecification",
                                                "price": "11.99",
                                                "priceCurrency": "USD"}]}]}
    page = _page("/product/acne-facial-wash-100ml/", title="Acne facial wash",
                 headings={"h1": ["Acne facial wash 100ml"]}, jsonld=[node],
                 body_text="Acne facial wash 100ml. $11.99")
    result = SkillResult("structured-data-audit")
    SD._check_product(result, _by_type([page]), {"name": "Clear Skin"})
    findings = [f for f in result.findings
                if f["id_hint"] == "product-schema-missing-offer-details"]
    assert findings, "the missing availability must still be reported"
    return findings[0]


def test_the_snippet_never_replaces_a_price_specification_with_a_placeholder():
    """"stop prescribing a fix the site already has - replacing a valid
    `priceSpecification` with a flat `price` plus a `<ISO 4217 code>`
    placeholder is a regression"."""
    snippet = _incomplete_offer_finding()["suggested_action"]["snippet"]
    payload = json.loads(re.sub(r"</?script[^>]*>", "", snippet).strip())
    offers = payload["offers"]
    assert "ISO 4217" not in snippet, "a placeholder over a currency the site states"
    spec = offers["priceSpecification"]
    spec = spec[0] if isinstance(spec, list) else spec
    assert spec["price"] == "11.99" and spec["priceCurrency"] == "USD"
    # The property that really is missing is the only placeholder in the block.
    assert offers["availability"].startswith("<")
    assert "price" not in offers and "priceCurrency" not in offers, \
        "a flat price beside the nested one is two prices for one thing"


def test_the_fix_steps_say_not_to_flatten_working_markup():
    """The snippet is the block; the steps are what a reader follows when their
    template does not look like it."""
    steps = " ".join(_incomplete_offer_finding()["suggested_action"]["how_to_fix"])
    assert "priceSpecification" in steps
    assert "flatten" in steps


# --------------------------------------------------------------------------
# 3 - a sitemap check that told a database project to delete its API reference
# --------------------------------------------------------------------------

DOCS = "https://a-columnar-store.test"


def _sitemap(urls):
    return [{"urls": [{"loc": url} for url in urls]}]


def _docs_sitemap():
    """3250 entries, 157 of them under `/api/`, none of them per-visitor."""
    return ([DOCS + "/docs/0.10/api/adbc.html", DOCS + "/docs/0.10/api/c/api.html"]
            + [DOCS + "/docs/0.10/api/page-{}.html".format(n) for n in range(155)]
            + [DOCS + "/docs/0.10/guide-{}.html".format(n) for n in range(3093)])


def test_a_documentation_tree_is_not_a_shop_basket():
    """Two defects, one vocabulary, and both are closed here.

    The finding read `is_forbidden_path`, which answers a different question
    - what this crawler may fetch - and said yes to any `/api/` segment. That
    was the first defect, and the check no longer borrows that answer.

    The second was the vocabulary itself: `api` matched a segment anywhere, so
    the crawler refused to fetch 157 of one documentation site's 3,250 pages -
    its reference manual - and said nothing. Both readings now agree, which is
    why this asserts the fetch guard rather than pinning the gap between them.
    """
    urls = _docs_sitemap()
    assert len(urls) == 3250
    assert not is_forbidden_path(DOCS + "/docs/0.10/api/c/api.html"), \
        "the crawler still refuses a documentation page for having /api/ in it"
    # An endpoint at the root of a site is still refused: a GET to one is not
    # a page anyone reads, which is what the segment was in the list for.
    assert is_forbidden_path(DOCS + "/api/v1/tables")
    assert is_forbidden_path(DOCS + "/graphql")
    assert CA._per_visitor_kind(DOCS + "/docs/0.10/api/c/api.html") == ""
    result = SkillResult("crawl-access-audit")
    CA._check_sitemap_lists_private_paths(result, _sitemap(urls))
    assert result.findings == []
    assert result.not_applicable, "a check that stays quiet has to say why"


def test_the_four_addresses_found_in_a_shop_sitemap_are_still_reported():
    """The case the check was written for, which the narrowing must not lose."""
    shop = "https://a-soap-shop.test"
    urls = [shop + p for p in ("/cart/", "/checkout/", "/my-account/", "/wishlist/",
                               "/product/oat-soap/")]
    result = SkillResult("crawl-access-audit")
    CA._check_sitemap_lists_private_paths(result, _sitemap(urls))
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert len(finding["affected_pages"]) == 4
    assert shop + "/product/oat-soap/" not in json.dumps(finding)
    # Each address carries the segment that classified it, so a reader can
    # check the classification instead of taking it.
    for url, kind in ((shop + "/cart/", "cart"), (shop + "/checkout/", "checkout"),
                      (shop + "/my-account/", "account"),
                      (shop + "/wishlist/", "wishlist")):
        assert "{} ({})".format(url, kind) in finding["evidence"]


def test_the_finding_names_only_the_kinds_it_actually_found():
    """The fix text said "cart, checkout, account and wishlist" whatever it had
    matched, which is how a docs tree was told to delete four kinds of address
    it does not have."""
    shop = "https://a-soap-shop.test"
    result = SkillResult("crawl-access-audit")
    CA._check_sitemap_lists_private_paths(
        result, _sitemap([shop + "/cart/", shop + "/product/oat-soap/"]))
    finding = result.findings[0]
    text = finding["evidence"] + " " + finding["suggested_action"]["summary"]
    assert "cart" in text
    for absent in ("checkout", "account", "wishlist"):
        assert absent not in text, "named {}, which this sitemap does not list".format(absent)


def test_a_page_about_comparing_or_wishing_is_an_article():
    """Whole path segments only. These are pages anyone can read and exactly
    what a sitemap is for."""
    shop = "https://a-soap-shop.test"
    for path in ("/blog/how-to-compare-soaps/", "/wishlist-ideas/",
                 "/guides/login-security-explained/", "/collections/carts-and-trolleys/"):
        assert CA._per_visitor_kind(shop + path) == "", path
