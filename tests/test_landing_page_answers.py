# -*- coding: utf-8 -*-
"""The page an AI answer deep-links to, and whether it answers the question.

Every check above these ones judges a visitor who arrived at the homepage and
browsed. That is not the visit this marketplace exists for. Somebody asks an
assistant about one specific thing, is given its name, clicks through, and
lands on the page for that one thing already holding the question - and the
page shows them what it shows everybody.

Three questions, and this file holds both directions of each: the site that
answers must never be reported, and the site that does not must never be
missed.

  does the page carry the facts     a page that publishes a figure and no stock
  that decide the buy               state, no delivery promise and no returns
                                    terms has told a visitor what to want and
                                    nothing about how to get it

  are they in the first screen      a price 4,000 characters under the brand
                                    story is a price the visitor has to hunt for

  is there a next step of this      a page whose every link is also on four
  page's own                        fifths of the site's other pages offers the
                                    arriving visitor the menu and nothing else

The exonerations are the point. A grid of twelve items with twelve prices is
not a page somebody arrived at asking about one thing; an essay quoting a
competitor's licence fee is not a page anybody buys from; a shop stating its
facts in `itemprop` attributes has stated them; and a section trail written
into a page's header is a way onward wherever the template puts it.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import ABSENCE_CAUSES, ROOT_CAUSES, SkillResult  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGAGEMENT = _module("engagement-audit", "engagement_for_landing_page_tests")

HOST = "https://an-invented-host.test"


def _links(*urls):
    return {"internal": [{"url": url, "text": url.rsplit("/", 1)[-1]} for url in urls],
            "external": [], "nav": [], "footer": []}


def _product(slug, body="", prices=(), jsonld=None, links=None, page_type="product",
             jsonld_types=None):
    """One product page as the crawl records it."""
    url = "{}/shop/{}".format(HOST, slug)
    return {
        "url": url, "status": 200, "page_type": page_type, "depth": 1,
        "title": slug, "body_text": body, "body_text_len": len(body),
        "word_count": len(body.split()), "text_len": len(body),
        "prices": list(prices),
        "jsonld": jsonld if jsonld is not None else [],
        "jsonld_types": list(jsonld_types or []),
        "microdata_items": [],
        "headings": {"h1": [slug]},
        "links": links if links is not None else _links(),
    }


def _offer(price="49.00", currency="GBP", **extra):
    offer = {"@type": "Offer", "price": price, "priceCurrency": currency}
    offer.update(extra)
    return [{"@context": "https://schema.org", "@type": "Product", "name": "A thing",
             "offers": offer}]


def _answers(result):
    return [f for f in result.findings if f["check"] == "landing-page-answers"]


def _reason(result, check):
    return next((n["reason"] for n in result.not_applicable if n["check"] == check), "")


def _run_answers(pages, english=True, render_mode="static"):
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_landing_page_answers(result, pages, english, render_mode)
    return result


# --------------------------------------------------------------------------
# The facts that decide the buy
# --------------------------------------------------------------------------

def test_a_shop_that_states_stock_delivery_and_returns_is_not_reported():
    """The exoneration, and the first thing to break if the readings drift.

    Two real shops in one validation pass state all three, one entirely in its
    Offer markup and one entirely in the paths of its own links, and a check
    that reported either of them would be describing its own vocabulary rather
    than the site.
    """
    pages = [_product("a-{}".format(n), prices=["GBP 49"],
                      jsonld=_offer(availability="https://schema.org/InStock",
                                    shippingDetails={"@type": "OfferShippingDetails"},
                                    hasMerchantReturnPolicy={"merchantReturnDays": 30}))
             for n in range(4)]
    result = _run_answers(pages)
    assert not _answers(result)
    reason = _reason(result, "landing-page-answers")
    assert "4 of 4" in reason
    # The pass names the fact and where it was read. A pass a reader cannot
    # check is worth as little as a finding they cannot check.
    assert "availability" in reason and "hasMerchantReturnPolicy" in reason


def test_the_same_three_facts_read_out_of_microdata_exonerate_the_page():
    """A shop whose theme writes `itemprop` attributes has stated them, and
    reading JSON-LD alone would tell it that its pages answer nothing."""
    pages = []
    for n in range(4):
        page = _product("m-{}".format(n), prices=["GBP 49"])
        page["microdata_items"] = [{
            "@type": "Offer", "_notation": "microdata", "price": "49.00",
            "availability": "https://schema.org/InStock",
            "deliveryTime": "2 days",
            "hasMerchantReturnPolicy": {"merchantReturnDays": 14},
        }]
        pages.append(page)
    assert not _answers(_run_answers(pages))


def test_a_link_to_the_returns_page_is_a_returns_policy_wherever_the_prose_is():
    """The path reading, which is what keeps this working on a site whose
    words this audit cannot read. Paths are ASCII on sites written in any
    script at all."""
    pages = [_product("p-{}".format(n), prices=["GBP 49"],
                      links=_links(HOST + "/policies/refund-policy",
                                   HOST + "/pages/shipping-policy"))
             for n in range(4)]
    assert not _answers(_run_answers(pages, english=False))


def test_a_template_that_states_only_a_price_is_reported_with_the_price_quoted():
    pages = [_product("q-{}".format(n), body="A lovely thing. GBP 49 including tax.",
                      prices=["GBP 49"])
             for n in range(4)]
    result = _run_answers(pages)
    finding = _answers(result)[0]
    assert finding["root_cause"] == "no-orientation"
    assert finding["mechanism"] == "G"
    assert "GBP 49" in finding["evidence"]
    assert "4 of 4" in finding["evidence"]
    assert finding["affected_page_count"] == 4
    # No browser ran, and the finding says which document it read. A fact a
    # script writes in after the page loads was not seen.
    assert "No browser ran" in finding["evidence"]


def test_one_page_missing_everything_among_many_that_do_not_is_not_a_template():
    """A finding here says the template is wrong. One page built from a
    different one is not that, and reporting it would tell an owner to edit
    something that is right on nine pages in ten."""
    pages = [_product("r-{}".format(n), prices=["GBP 49"],
                      jsonld=_offer(availability="https://schema.org/InStock"))
             for n in range(9)]
    pages.append(_product("bare", prices=["GBP 49"]))
    assert not _answers(_run_answers(pages))


def test_too_few_priced_pages_declines_and_says_how_few():
    result = _run_answers([_product("only", prices=["GBP 49"])])
    assert not _answers(result)
    assert "1 page that publishes a price" in _reason(result, "landing-page-answers")


def test_a_listing_of_twelve_items_with_twelve_prices_is_not_judged():
    """A grid answers a browsing visitor. The question here is about the one
    thing somebody arrived asking for, and a category page has no one thing."""
    pages = [{"url": "{}/collections/bags-{}".format(HOST, n), "status": 200,
              "page_type": "category", "depth": 1, "prices": ["GBP 49", "GBP 59"],
              "body_text": "Twelve bags. GBP 49 GBP 59", "jsonld": [], "jsonld_types": [],
              "microdata_items": [], "links": _links(), "headings": {}}
             for n in range(5)]
    result = _run_answers(pages)
    assert not _answers(result)
    assert "0 pages that publish a price" in _reason(result, "landing-page-answers")


def test_an_essay_quoting_somebody_elses_price_is_not_a_page_anybody_buys_from():
    """The class is established before anything is asked of it. A consultancy
    article naming a competitor's licence fee was, in an earlier version of
    this marketplace, enough to open every pricing check on the firm."""
    pages = [{"url": "{}/insights/note-{}".format(HOST, n), "status": 200,
              "page_type": "article", "depth": 1, "prices": ["USD 1,200"],
              "body_text": "Their licence runs to USD 1,200 a seat.",
              "jsonld": [], "jsonld_types": [], "microdata_items": [],
              "links": _links(), "headings": {}}
             for n in range(5)]
    assert not _answers(_run_answers(pages))


def test_a_page_this_audit_cannot_read_is_set_aside_and_named_rather_than_reported():
    """Two of the three facts have a reading that needs no prose and one has
    only the markup property. On a site whose words these patterns are not
    written for, a page with no commerce markup leaves one reading standing
    out of three - and "this page does not say when it arrives" would then be
    a statement about the audit's vocabulary, not about the page."""
    pages = [_product("s-{}".format(n), prices=["INR 1,299"]) for n in range(4)]
    result = _run_answers(pages, english=False)
    assert not _answers(result)
    reason = _reason(result, "landing-page-answers")
    assert "4 pages were left out" in reason
    assert "not in a language this audit reads" in reason


def test_markup_that_names_a_price_and_nothing_else_fires_in_any_language():
    """And the other direction, so the guard above is a guard rather than an
    exemption: where the page publishes an Offer, the three properties missing
    from it is a language-neutral observation."""
    pages = [_product("t-{}".format(n), prices=["INR 1,299"], jsonld=_offer("1299", "INR"))
             for n in range(4)]
    finding = _answers(_run_answers(pages, english=False))[0]
    assert "a stock state" in finding["evidence"]


def test_the_finding_names_three_independent_readings_of_each_fact():
    """A claim of absence resting on one detector is the shape of every false
    positive this marketplace has made on a real site."""
    pages = [_product("u-{}".format(n), prices=["GBP 49"]) for n in range(4)]
    finding = _answers(_run_answers(pages))[0]
    assert finding["root_cause"] in ABSENCE_CAUSES
    assert len(finding["checked"]) >= 3
    assert finding["confidence"] == "high"


# --------------------------------------------------------------------------
# Where on the page the answer sits
# --------------------------------------------------------------------------

def _run_position(pages):
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_landing_page_leads_with_the_price(result, pages)
    return result


def _position_findings(result):
    return [f for f in result.findings
            if f["check"] == "landing-page-leads-with-the-price"]


def test_a_price_under_several_screens_of_brand_copy_is_reported_with_the_offset():
    story = "We have been making things by hand since 1946. " * 60
    pages = [_product("v-{}".format(n), body=story + " GBP 49 including tax.",
                      prices=["GBP 49"])
             for n in range(4)]
    finding = _position_findings(_run_position(pages))[0]
    assert finding["root_cause"] == "no-orientation"
    assert "GBP 49" in finding["evidence"]
    # The opening the visitor reads instead, quoted back, so the owner can see
    # what the page spends its first screen on.
    assert "making things by hand" in finding["evidence"]
    assert "1,500 characters" in finding["evidence"]


def test_a_price_in_the_first_screen_is_a_pass_that_says_so():
    pages = [_product("w-{}".format(n), body="GBP 49 including tax. " + ("Detail. " * 200),
                      prices=["GBP 49"])
             for n in range(4)]
    result = _run_position(pages)
    assert not _position_findings(result)
    assert "4 of 4" in _reason(result, "landing-page-leads-with-the-price")


def test_a_price_stated_only_in_markup_has_no_position_and_is_not_measured():
    """Reporting one as buried would be a claim about a figure the visitor
    never reads in the copy at all, which is a different finding."""
    pages = [_product("x-{}".format(n), body="A lovely thing. " * 200,
                      jsonld=_offer())
             for n in range(4)]
    result = _run_position(pages)
    assert not _position_findings(result)
    assert "0 pages that publish a price in their own copy" in _reason(
        result, "landing-page-leads-with-the-price")


# --------------------------------------------------------------------------
# A next step that belongs to this page
# --------------------------------------------------------------------------

def _chrome():
    return [{"url": HOST + "/", "text": "Home"},
            {"url": HOST + "/about", "text": "About"},
            {"url": HOST + "/contact", "text": "Contact"},
            {"url": HOST + "/basket", "text": "Basket"}]


def _deep(slug, extra=(), page_type="product"):
    return {"url": "{}/shop/{}".format(HOST, slug), "status": 200,
            "page_type": page_type, "depth": 1, "headings": {}, "jsonld_types": [],
            "links": {"internal": [{"url": url, "text": "x"} for url in extra],
                      "external": [], "nav": _chrome(), "footer": []}}


def _filler(n):
    return [{"url": "{}/page-{}".format(HOST, i), "status": 200, "page_type": "about",
             "depth": 1, "headings": {}, "jsonld_types": [],
             "links": {"internal": [], "external": [], "nav": _chrome(), "footer": []}}
            for i in range(n)]


def _run_onward(pages):
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_onward_journey(result, pages)
    return result


def _onward_findings(result):
    return [f for f in result.findings
            if f["check"] == "onward-links-specific-to-the-page"]


def test_pages_whose_every_link_is_on_every_other_page_are_reported():
    pages = [_deep("a"), _deep("b"), _deep("c"), _deep("d")] + _filler(6)
    finding = _onward_findings(_run_onward(pages))[0]
    assert finding["root_cause"] == "dead-end"
    assert finding["affected_page_count"] == 4
    # What the pages do carry, named, so a reader who opens one and sees links
    # is not told there are none.
    assert HOST + "/basket" in finding["evidence"]


def test_a_trail_into_the_page_s_own_section_counts_wherever_the_template_puts_it():
    """A section trail written into a page's header is as much a way onward as
    a block under the copy. Judging by position reported four location pages
    whose header carries a trail into the section they sit in as offering a
    visitor nothing but the menu."""
    pages = [_deep(slug, extra=[HOST + "/shop/index"])
             for slug in ("a", "b", "c", "d")] + _filler(6)
    result = _run_onward(pages)
    assert not _onward_findings(result)
    assert "4 of 4" in _reason(result, "onward-links-specific-to-the-page")


def test_a_related_item_link_is_a_next_step():
    pages = [_deep("a", extra=[HOST + "/shop/b"]),
             _deep("b", extra=[HOST + "/shop/a"]),
             _deep("c", extra=[HOST + "/shop/a"]),
             _deep("d", extra=[HOST + "/shop/b"])] + _filler(6)
    assert not _onward_findings(_run_onward(pages))


def test_a_crawl_too_small_to_know_what_this_site_puts_on_every_page_declines():
    """On a five-page crawl, four fifths is four pages, so one section menu
    becomes site furniture and every page reads as carrying nothing of its
    own - a finding about the crawl rather than the site."""
    pages = [_deep("a"), _deep("b"), _deep("c")] + _filler(2)
    result = _run_onward(pages)
    assert not _onward_findings(result)
    assert "fewer than the 8" in _reason(result, "onward-links-specific-to-the-page")


def test_a_site_with_no_deep_pages_says_that_rather_than_blaming_the_crawl():
    """A crawl of fifty-two pages that found no product, article or location
    page was told its crawl was too small, which is a false statement about
    the crawl standing in for a true one about what it found."""
    result = _run_onward(_filler(12))
    assert not _onward_findings(result)
    assert "0 deep pages" in _reason(result, "onward-links-specific-to-the-page")


# --------------------------------------------------------------------------
# The three of them inside the skill
# --------------------------------------------------------------------------

def test_all_three_checks_are_registered_on_every_run_and_answered_once():
    """A check that runs and appears in neither the findings nor the declines
    is a check the report cannot account for."""
    snapshot = {"pages": [_product("y-{}".format(n), prices=["GBP 49"]) for n in range(4)],
                "crawl": {"render_mode": "static"}, "sitemaps": [], "robots": {}}
    out = ENGAGEMENT.run(snapshot, allow_network=False).to_dict()
    names = {"landing-page-answers", "landing-page-leads-with-the-price",
             "onward-links-specific-to-the-page"}
    assert names <= set(out["checks_run"])
    answered = ({n["check"] for n in out["not_applicable"]}
                | {f.get("check") for f in out["findings"]})
    assert names <= answered
    for finding in out["findings"]:
        assert finding["root_cause"] in ROOT_CAUSES
        assert finding["mechanism"] == "G"


def test_the_signals_the_report_would_gate_a_recommendation_on_are_emitted():
    """These are measurements, not the presence of a page type. Three of the
    fourteen proactive recommendations fired on six sites out of six because
    they were gated on a site having product pages at all."""
    snapshot = {"pages": [_product("z-{}".format(n), prices=["GBP 49"]) for n in range(4)],
                "crawl": {"render_mode": "static"}, "sitemaps": [], "robots": {}}
    signals = ENGAGEMENT.run(snapshot, allow_network=False).to_dict()["signals"]
    assert signals["landing_pages_judged_for_purchase_facts"] == 4
    assert len(signals["landing_pages_stating_only_a_price"]) == 4
    assert signals["landing_pages_that_bury_the_price"] == []
