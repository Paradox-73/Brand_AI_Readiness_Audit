# -*- coding: utf-8 -*-
"""Grading a page on a type it earned, and on nothing else.

Three of six sites in one validation pass produced the same wrong finding, and
it was one mistake made in several places: a check read the type the crawl
assigned and treated it as settled. The crawl's type is a candidate. A page
earns it or it does not, and the tests here are the cases where it does not.

  a gift category                 "3 of 3 product pages have no Product
  a best-sellers list             markup", about three pages of which none is
  a search-results address        a product page, with a fix that would put a
                                  `Product` block on every search a visitor
                                  runs

The second half is the other end of the same defect. A retailer's report said
46 of 60 crawled pages share a title, and separately that no page declares a
canonical, and joined neither sentence to the other: the 46 were one listing
reached through filter and sort parameters, and the canonical nobody declares
is the single edit that resolves the duplicates.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import DISTINCT_PRICE_CEILING, SkillResult  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_page_type_tests")

SITE = "https://a-tea-shop-that-does-not-exist.test"


def _page(url, page_type="other", title="", body="", headings=None, links=(),
          paragraphs=(), canonical="", og=None):
    """A snapshot page record with only the keys these checks read."""
    return {
        "url": SITE + url,
        "page_type": page_type,
        "status": 200,
        "title": title,
        "body_text": body,
        "body_text_len": len(body),
        "meta_description": "A description long enough that the hygiene check has "
                            "nothing to say about this page at all.",
        "lang": "ko",
        "canonical": canonical,
        "og": dict(og or {"og:title": "t", "og:description": "d", "og:image": SITE + "/i.png"}),
        "headings": headings or {},
        "paragraphs": list(paragraphs),
        "links": {"internal": [{"url": SITE + u, "text": t} for u, t in links]},
        "jsonld": [],
        "jsonld_types": [],
    }


def _grid_prices(count):
    """The text of a shelf of things for sale, one price per thing."""
    return " ".join("Item {} 12{}00 KRW".format(n, n) for n in range(count))


def _product_result(pages):
    result = SkillResult("structured-data-audit")
    by_type = {}
    for page in pages:
        by_type.setdefault(page["page_type"], []).append(page)
    SD._check_product(result, by_type, {"name": "A Tea Shop"})
    return result


def _reason_from(result, name):
    for entry in result.not_applicable:
        if entry["check"] == name:
            return entry["reason"]
    return ""


# --------------------------------------------------------------------------
# A shelf of things is not a thing
# --------------------------------------------------------------------------

def test_a_grid_of_priced_items_is_not_asked_for_product_markup():
    """The address gives no help: no grouping word, no listing segment, and a
    locale prefix pushing it far enough down that the classifier's depth rule
    calls anything with a price on it a detail page. What separates a grid from
    a detail page is that it prices many things, which is the shared library's
    own measured line.
    """
    grid = _page("/kr/ko/shop/item/gift_new", "product",
                 title="Gifts", body=_grid_prices(DISTINCT_PRICE_CEILING + 3))

    result = _product_result([grid])

    assert not result.findings, "a shelf of things carries no Product markup of its own"
    reason = _reason_from(result, "product-markup")
    assert "list rather than a thing" in reason
    assert grid["url"] in reason, "the page dropped from the count has to be named"


def test_a_detail_page_that_prices_one_thing_is_still_graded():
    """The other direction, which is what makes the rule a rule rather than an
    exemption: a page under the same section that states one price is a product
    page and is still reported when it declares no markup.
    """
    item = _page("/kr/ko/shop/item/12345", "product",
                 title="Loose leaf, 100g", body="Loose leaf, 100g 12000 KRW. In stock.")

    result = _product_result([item])

    assert len(result.findings) == 1
    assert result.findings[0]["affected_pages"] == [item["url"]]


def test_an_address_naming_a_grouping_is_not_a_product_page():
    """`is_faceted_listing` already answers this and the product check never
    asked it. The page prices one thing, so the price ceiling cannot help; the
    address is the whole of the evidence and it is the site's own scheme.
    """
    grouping = _page("/collections/gifts", "product",
                     title="Gifts", body="Everything in gifts. From 12000 KRW.")

    result = _product_result([grouping])

    assert not result.findings
    assert "names a grouping" in _reason_from(result, "product-markup")


def test_the_article_check_and_the_product_check_give_one_answer():
    """Two checks, one page, one verdict. They each had their own reading of
    "is this an index", and a page could be an index to one and a post to the
    other - which is how a set of monthly archives was excluded from one check
    in a report and named by another.
    """
    linked = [("/blog/first-harvest", "First harvest"),
              ("/blog/second-flush", "Second flush"),
              ("/blog/roasting-notes", "Roasting notes")]
    index = _page("/blog/tasting-notes", "article",
                  headings={"h3": [text for _url, text in linked]}, links=linked)

    assert SD._not_the_type_it_was_given(index)
    index_as_product = dict(index, page_type="product")
    assert SD._not_the_type_it_was_given(index_as_product)


# --------------------------------------------------------------------------
# A page made from a query is not a page the site published
# --------------------------------------------------------------------------

def _snapshot(pages):
    return {"origin": SITE, "site": SITE, "brand": {"name": "A Tea Shop"},
            "pages": list(pages)}


def test_a_search_results_address_is_graded_by_nothing_here():
    """It was counted as a product page missing Product markup, and it was also
    in the denominator of every "N of M pages" this skill prints. Neither is a
    statement about a document the site publishes.
    """
    results = _page("/kr/ko/shop/search/main/product?keyword=gift", "product",
                    title="Search", body=_grid_prices(3))
    results["lang"] = ""
    results["meta_description"] = ""
    results["og"] = {}
    home = _page("/", "home", title="A Tea Shop, since 1934",
                 body="A Tea Shop is a tea merchant in the old quarter.")

    result = SD.run(_snapshot([home, results]))

    everything = json.dumps(result.findings)
    assert results["url"] not in everything, (
        "a page assembled from a typed query is not a page with markup missing")
    assert results["url"] in json.dumps(result.signals["search_result_pages_not_graded"]), (
        "the exclusion has to be visible rather than silent")


# --------------------------------------------------------------------------
# Addresses that are one page reached several ways
# --------------------------------------------------------------------------

def _filtered(query, title="Best sellers"):
    return _page("/kr/ko/shop/item/list/best?" + query, "category", title=title,
                 body="Best sellers. 12000 KRW. 14000 KRW.")


def _canonical_result(pages):
    result = SkillResult("structured-data-audit")
    SD._check_canonical_declaration(result, pages)
    return result


def test_filter_addresses_with_no_canonical_are_named_as_the_trap():
    pages = [_filtered("sort=new"), _filtered("sort=price"),
             _filtered("sort=price&line=green"), _page("/", "home", title="A Tea Shop")]

    result = _canonical_result(pages)

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding["affected_pages"] == sorted(p["url"] for p in pages[:3])
    assert "/kr/ko/shop/item/list/best" in json.dumps(finding["suggested_action"]), (
        "the address to canonicalise onto has to be stated, not described")
    assert len(finding["checked"]) >= 2, (
        "a claim that two tags are absent may not rest on one of them")


def test_addresses_whose_titles_differ_are_left_alone():
    """`?id=41` and `?id=42` are two things, and canonicalising them onto one
    address would delete every item but one from every index that lists them.
    The page's own title is what separates the two cases.
    """
    result = _canonical_result([
        _filtered("id=41", title="Sencha, 100g"),
        _filtered("id=42", title="Gyokuro, 100g"),
        _filtered("id=43", title="Hojicha, 100g"),
    ])

    assert not result.findings
    assert "select different pages rather than filtering one" in _reason_from(
        result, "canonical-declaration")


def test_a_group_that_names_itself_in_open_graph_is_not_reported():
    """The absence claim is that nothing says which address is the page, and
    two tags can say it. A template carrying `og:url` and no canonical has
    said it, and reporting that is the single-detector absence claim every
    false positive here has been."""
    named = {"og:title": "t", "og:description": "d", "og:image": SITE + "/i.png",
             "og:url": SITE + "/kr/ko/shop/item/list/best"}
    pages = [_filtered("sort=new"), _filtered("sort=price"), _filtered("line=green")]
    pages[1]["og"] = named

    assert not _canonical_result(pages).findings


def test_two_addresses_are_not_yet_a_template_producing_them():
    result = _canonical_result([_filtered("sort=new"), _filtered("sort=price")])

    assert not result.findings
    assert "below the" in _reason_from(result, "canonical-declaration")
