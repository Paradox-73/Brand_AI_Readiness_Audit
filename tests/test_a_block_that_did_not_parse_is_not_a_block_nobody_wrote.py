# -*- coding: utf-8 -*-
"""An unparsed JSON-LD block read as "no markup" by every absence check.

A report on a consumer handbag shop carried these two findings:

    "https://<shop>/ declares no Organization-level JSON-LD type and no
     inline Microdata or RDFa saying the same"

    "https://<shop>/ declares no WebSite type with a potentialAction"

Both false. The homepage's second `<script type="application/ld+json">` block
holds an `Organization` and a `WebSite` with a `potentialAction`, and it is one
comma short of being valid JSON. `jsonld_types` lists the types of the blocks
that parsed, so the checks read the parser's blind spot and published it as a
fact about the site. The same report quoted that block two findings earlier,
under the JSON-LD validity check - so one document said the markup does not
exist and printed it.

The prescribed fix made it worse: "move the Organization block into the
site-wide template" is a template migration ordered for a missing comma, and
the block is already in the site-wide template.

This is a class, not two findings. An earlier fix added
`_set_aside_unparsed_markup` and wired it into the Product, Article and FAQ
checks; five more absence checks in the same file never routed through it.
Three test groups below:

  1. the case that prompted this, in both directions
  2. every absence check in the file, one at a time
  3. a rule over the whole skill, so a check written next month cannot
     reintroduce the class without failing the build

No real brand, site or domain appears here: the shop is invented and the
domain is a `.test` name, which by RFC 6761 can never resolve.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_unparsed_markup_tests")

SITE = "https://a-handbag-shop-that-does-not-exist.test"
BRAND = "Neelkanth Leatherworks"


# The excerpt the parser keeps of the block it could not read, in the shape the
# real report printed it: the block opens with the WebSite node, and the
# Organization the second finding denied is further along than the excerpt
# reaches. So the excerpt names one of the two denied types and not the other,
# which is the real case and the harder one - the gate may not depend on being
# able to read the type out of the wreckage.
BROKEN_IDENTITY_BLOCK = {
    "error": "Expecting ',' delimiter: line 12 column 5 (char 418)",
    "excerpt": ('[ { "@context": "https://schema.org", "@type": "WebSite", '
                '"name": "…", … "potentialActi'),
}


def _page(path, page_type="other", **extra):
    """A snapshot page record carrying only the keys these checks read."""
    page = {
        "url": SITE + path,
        "final_url": SITE + path,
        "status": 200,
        "page_type": page_type,
        "depth": 0 if path == "/" else 1,
        "title": BRAND,
        "text": "Hand-stitched leather bags, made in small batches. " * 12,
        "body_text": "Hand-stitched leather bags, made in small batches. " * 12,
        "headings": {"h1": [BRAND], "h2": [], "h3": []},
        "sections": [],
        "paragraphs": ["Hand-stitched leather bags, made in small batches."],
        "meta_description": "Hand-stitched leather bags from a small workshop.",
        "og": {},
        "links": {"internal": [], "external": []},
        "forms": [],
        "jsonld": [],
        "jsonld_types": [],
        "jsonld_errors": [],
        "microdata_types": [],
        "microdata_count": 0,
        "rdfa_count": 0,
    }
    page.update(extra)
    return page


ORG_NODE = {
    "@context": "https://schema.org",
    "@type": "Organization",
    "name": BRAND,
    "url": SITE + "/",
    "logo": SITE + "/static/mark.png",
    "description": "A workshop making hand-stitched leather bags in small batches.",
    "sameAs": [],
}

SEARCH_FORM = {"is_search": True, "action": "/search", "query_field": "q"}


def _real_site(broken=True):
    """The shop as crawled: identity markup on every page, unreadable on the home.

    `broken` off gives the site the checks were right about - a homepage that
    really does ship no identity block and no WebSite - so every assertion
    below has its own control.
    """
    home = _page("/", "home", forms=[SEARCH_FORM],
                 links={"internal": [{"url": SITE + "/search?q=bags", "text": "Search"}],
                        "external": []})
    if broken:
        home["jsonld_errors"] = [dict(BROKEN_IDENTITY_BLOCK)]
    inner = []
    for path, kind in (("/about", "about"), ("/contact", "contact")):
        page = _page(path, kind)
        page["jsonld"] = [dict(ORG_NODE)]
        page["jsonld_types"] = ["organization"]
        inner.append(page)
    return {
        "origin": SITE,
        "site": SITE.split("//", 1)[1],
        "brand": {"name": BRAND},
        "pages": [home] + inner,
        "sitemaps": [],
    }


def _hints(result):
    return {f.get("id_hint") for f in result.findings}


def _reason(result, check):
    return " ".join(entry["reason"] for entry in result.not_applicable
                    if entry["check"] == check)


# --------------------------------------------------------------------------
# 1. The case that prompted this
# --------------------------------------------------------------------------

def test_the_homepage_is_not_told_it_declares_no_organization():
    """The first of the two published sentences. The block is in the crawl."""
    result = SD.run(_real_site())
    assert "homepage-declares-no-organization-schema" not in _hints(result), (
        "the homepage ships an Organization block that does not parse, so it is "
        "not a homepage the site-wide template skipped")
    assert "no-organization-schema" not in _hints(result)


def test_the_homepage_is_not_told_it_declares_no_website_searchaction():
    """The second. The same block carries the WebSite and its potentialAction."""
    result = SD.run(_real_site())
    assert "no-website-searchaction" not in _hints(result)


def test_both_declines_name_the_finding_that_holds_those_pages():
    """A check that goes quiet without saying where the subject went reads as a
    check that found nothing. So the decline says so explicitly: "markup
    declared but unreadable - see the invalid-JSON-LD finding"."""
    result = SD.run(_real_site())
    reason = _reason(result, "website-searchaction-markup")
    assert "markup declared but unreadable" in reason, reason
    assert "does not parse" in reason, reason
    assert SD._THE_PARSE_FINDING in reason, reason
    assert SITE + "/" in reason, reason


def test_the_parse_error_is_still_reported_and_is_the_thing_to_fix():
    """Nothing is silenced. The page keeps a finding; it is the true one."""
    result = SD.run(_real_site())
    assert "jsonld-does-not-parse" in _hints(result)
    parse = [f for f in result.findings if f["id_hint"] == "jsonld-does-not-parse"][0]
    assert parse["affected_pages"] == [SITE + "/"]


def test_no_finding_orders_a_template_migration_for_a_syntax_error():
    """The cost to the owner: the fix offered was "move the Organization
    block into the site-wide template", which is work already done."""
    result = SD.run(_real_site())
    steps = " ".join(step for f in result.findings
                     for step in f["suggested_action"]["how_to_fix"])
    assert "into the site-wide template" not in steps, steps


def test_the_verdict_paragraph_can_no_longer_inherit_the_false_claim():
    """The same report's verdict said "the homepage carries no Organization
    markup" too.

    That sentence is written in `compose_report.py`, which this skill does not
    own - but it is not written from a signal. `identity_markup_gap` reads the
    published findings and nothing else, and licenses the two identity
    sentences off two id hints. So the composer needs no change: withholding
    the findings withholds the sentence. Asserted here against the composer's
    own two constants rather than against the sentence, so renaming a hint on
    either side of the seam fails this test instead of silently reopening the
    defect.
    """
    from compose_report import (  # noqa: E402
        IDENTITY_MARKUP_ABSENT_HINTS, IDENTITY_MARKUP_HOMEPAGE_ONLY_HINTS,
    )
    licensing = IDENTITY_MARKUP_ABSENT_HINTS | IDENTITY_MARKUP_HOMEPAGE_ONLY_HINTS
    assert not (_hints(SD.run(_real_site())) & licensing)
    # And the control: the same crawl without the parse error still licenses it.
    assert _hints(SD.run(_real_site(broken=False))) & licensing


def test_a_homepage_that_really_ships_nothing_is_still_reported():
    """The guard, and the half that must not move. A page with no JSON-LD at
    all has an empty `jsonld_errors`, and both findings are true of it."""
    result = SD.run(_real_site(broken=False))
    assert "homepage-declares-no-organization-schema" in _hints(result), _hints(result)
    assert "no-website-searchaction" in _hints(result), _hints(result)
    assert "jsonld-does-not-parse" not in _hints(result)


# --------------------------------------------------------------------------
# 2. Every absence check in the file, one at a time
# --------------------------------------------------------------------------

def _broken(page):
    page["jsonld_errors"] = [dict(BROKEN_IDENTITY_BLOCK)]
    return page


def test_the_site_wide_organization_absence_declines_on_an_unreadable_identity_page():
    """"None declares an Organization-level JSON-LD type" over a crawl whose
    about page ships one that does not parse."""
    result = SkillResult("structured-data-audit")
    pages = [_page("/", "home"), _broken(_page("/about", "about"))]
    by_type = {"home": [pages[0]], "about": [pages[1]]}
    SD._check_organization(result, {"origin": SITE}, pages, by_type, {"name": BRAND})
    assert not result.findings, [f["id_hint"] for f in result.findings]
    assert "markup declared but unreadable" in _reason(result, "organization-markup")


def test_a_broken_block_elsewhere_does_not_silence_a_site_that_declares_nothing():
    """The over-reach guard. The gate is scoped to the pages the claim is about
    - the identity pages - so a broken Recipe block on one deep page cannot
    silence the single most important structured-data question on a site that
    genuinely declares no identity anywhere."""
    result = SkillResult("structured-data-audit")
    deep = _page("/blog/hides", "article")
    deep["jsonld_errors"] = [{"error": "Invalid control character",
                              "excerpt": '{"@type": "Recipe", "name": "Wax"'}]
    pages = [_page("/", "home"), _page("/about", "about"), deep]
    by_type = {"home": [pages[0]], "about": [pages[1]], "article": [deep]}
    SD._check_organization(result, {"origin": SITE}, pages, by_type, {"name": BRAND})
    assert [f["id_hint"] for f in result.findings] == ["no-organization-schema"]


def test_a_broken_block_naming_an_identity_type_anywhere_does_silence_it():
    """The clause the page scoping cannot cover. A block whose kept excerpt
    says `"@type": "Organization"` is the missing block, wherever it sits."""
    result = SkillResult("structured-data-audit")
    deep = _page("/blog/hides", "article")
    deep["jsonld_errors"] = [{"error": "Expecting ',' delimiter",
                              "excerpt": '{"@type": "Organization", "name": "N"'}]
    pages = [_page("/", "home"), _page("/about", "about"), deep]
    by_type = {"home": [pages[0]], "about": [pages[1]], "article": [deep]}
    SD._check_organization(result, {"origin": SITE}, pages, by_type, {"name": BRAND})
    assert not result.findings, [f["id_hint"] for f in result.findings]


def test_the_breadcrumb_ratio_is_not_pushed_over_the_line_by_a_parse_error():
    """The one absence check in the file that fires on a proportion. Counting
    an unreadable page as one without markup does not just widen a list - it
    can be what produces the finding."""
    result = SkillResult("structured-data-audit")
    pages = [_broken(_page("/p/{}".format(i), "product")) for i in range(4)]
    SD._check_breadcrumbs(result, pages, {"product": pages})
    assert not result.findings, [f["id_hint"] for f in result.findings]
    assert "markup declared but unreadable" in _reason(result, "breadcrumb-markup")


def test_a_shelf_whose_itemlist_block_does_not_parse_is_not_a_shelf_without_one():
    result = SkillResult("structured-data-audit")
    shelf = _broken(_page("/shop/all", "product", title="Shop All"))
    shelf["links"] = {"internal": [{"url": SITE + "/shop/all/bag-{}".format(i),
                                    "text": "Bag {}".format(i)} for i in range(12)],
                      "external": []}
    SD._check_listing_markup(result, {"product": [shelf]})
    assert "listing-pages-have-no-itemlist-markup" not in _hints(result)
    assert "markup declared but unreadable" in _reason(result, "listing-markup")


def test_a_branch_page_whose_localbusiness_block_does_not_parse_is_not_unmarked():
    """The high-severity one. Its fix adds a second block beside the broken
    one, on the pages that already carry it."""
    result = SkillResult("structured-data-audit")
    branches = []
    for i, town in enumerate(("Fort", "Bandra", "Andheri")):
        page = _broken(_page("/stores/{}".format(town.lower()), "location"))
        page["contact_facts"] = {"declared_phones": [],
                                 "street_hint": "{} Mill Road".format(10 + i),
                                 "postcode_hint": "40000{}".format(i)}
        branches.append(page)
    snapshot = {"origin": SITE, "brand": {"name": BRAND}, "pages": branches}
    SD._check_per_location_markup(result, snapshot, branches)
    assert "branch-pages-carry-no-location-markup" not in _hints(result)


def test_the_faq_check_no_longer_calls_an_unreadable_page_marked_up():
    """The set-aside was computed here and then not said, so a page whose
    FAQPage block does not parse fell into "all detected FAQ pages carry
    FAQPage markup" - an absence check asserting a presence instead."""
    result = SkillResult("structured-data-audit")
    page = _broken(_page("/help", "faq"))
    page["headings"] = {"h1": ["Help"], "h2": ["Do you ship overseas?",
                                               "How long does a repair take?"], "h3": []}
    page["sections"] = [
        {"heading": "Do you ship overseas?",
         "first_paragraph": "Yes, to most countries, and the courier is chosen at checkout."},
        {"heading": "How long does a repair take?",
         "first_paragraph": "About three weeks, longer if the leather has to be matched."},
    ]
    SD._check_faq(result, {"faq": [page]}, [page])
    assert not result.findings, [f["id_hint"] for f in result.findings]
    reason = _reason(result, "faq-markup")
    assert "all detected FAQ pages carry FAQPage markup" not in reason, reason
    assert "markup declared but unreadable" in reason, reason


# --------------------------------------------------------------------------
# 3. A rule over the whole skill
# --------------------------------------------------------------------------

# Root causes this skill raises that are not claims about JSON-LD being absent.
# Every one of them is measured somewhere other than the parsed blocks - a
# `<title>`, a meta description, a `lang` attribute, an Open Graph tag, an
# `itemtype` attribute, or the values inside a block that did parse - so a
# parse error on the page says nothing about them and they are entitled to
# publish over one.
#
# This list is the deliberate half of the rule below. Adding a cause to it is a
# statement that the check does not read `jsonld_types` or `jsonld` to decide
# an absence, and the assertion under it refuses the shape a JSON-LD absence
# claim has, so the list cannot be widened to smuggle one in.
NOT_A_JSONLD_ABSENCE_CLAIM = frozenset({
    "invalid-jsonld",        # the finding every decline below points at
    "meta-hygiene",          # <title> and meta description text
    "missing-lang",          # the html lang attribute
    "open-graph-incomplete",  # og: meta tags
    "microdata-only",        # itemtype attributes, and it reads jsonld_errors already
    "missing-schema-props",  # the properties of a block that did parse
    "schema-text-mismatch",  # values in a parsed block against the visible page
})


def test_the_allowlist_cannot_be_widened_into_a_markup_absence_claim():
    """The tripwire on the list above. A cause named `no-<something>-schema` or
    `no-<something>-markup` is an absence claim about markup by construction,
    and the way to make this file pass is the gate, not the list."""
    smuggled = sorted(c for c in NOT_A_JSONLD_ABSENCE_CLAIM
                      if re.match(r"^no-.*-(schema|markup)$", c))
    assert not smuggled, (
        "{} is an absence-of-markup cause and cannot be excused here; route its "
        "check through _set_aside_unparsed_markup instead".format(smuggled))


def _every_kind_of_page(broken):
    """One page of every kind this skill grades, each carrying only a block
    that does not parse - or, with `broken` off, carrying no JSON-LD at all."""
    kinds = (("/", "home"), ("/about", "about"), ("/contact", "contact"),
             ("/shop/tote", "product"), ("/shop/all", "product"),
             ("/journal/tanning", "article"), ("/help", "faq"),
             ("/stores/fort", "location"), ("/stores/bandra", "location"),
             ("/stores/andheri", "location"))
    pages = []
    for path, kind in kinds:
        page = _page(path, kind)
        if path == "/":
            page["forms"] = [SEARCH_FORM]
        if kind == "location":
            page["contact_facts"] = {
                "declared_phones": [],
                "street_hint": "{} Mill Road".format(10 + len(pages)),
                "postcode_hint": "40000{}".format(len(pages))}
        if path == "/shop/all":
            page["links"] = {"internal": [{"url": SITE + "/shop/all/bag-{}".format(i),
                                           "text": "Bag {}".format(i)} for i in range(12)],
                             "external": []}
        if path == "/help":
            page["headings"] = {"h1": ["Help"],
                                "h2": ["Do you ship overseas?",
                                       "How long does a repair take?"], "h3": []}
            page["sections"] = [
                {"heading": "Do you ship overseas?",
                 "first_paragraph": "Yes, to most countries, chosen at checkout."},
                {"heading": "How long does a repair take?",
                 "first_paragraph": "About three weeks if the leather has to be matched."},
            ]
        if broken:
            page["jsonld_errors"] = [dict(BROKEN_IDENTITY_BLOCK)]
        pages.append(page)
    return {"origin": SITE, "site": SITE.split("//", 1)[1], "brand": {"name": BRAND},
            "pages": pages, "sitemaps": []}


def test_no_absence_of_markup_finding_ships_about_a_page_whose_jsonld_broke():
    """The class, stated once over the whole skill.

    Every page in this crawl ships a JSON-LD block and every one of them fails
    to parse. Any finding this skill publishes claiming markup is absent is
    therefore a claim about the parser rather than about the site. A check
    written next month that reads `jsonld_types` for an absence and does not
    route through `_set_aside_unparsed_markup` fails here, which is the point:
    the two findings in that report were the second and third instances of
    one shape, and nothing had caught the first.
    """
    result = SD.run(_every_kind_of_page(broken=True))
    offenders = sorted({(f["root_cause"], f["id_hint"]) for f in result.findings
                        if f["root_cause"] not in NOT_A_JSONLD_ABSENCE_CLAIM})
    assert not offenders, (
        "these findings claim markup is absent from pages that ship a JSON-LD "
        "block the parser could not read: {}".format(offenders))


def test_the_same_site_with_no_markup_at_all_is_still_told_so():
    """The other direction, and the one that keeps the fix from being a
    silencer. Take the parse errors away and the pages ship nothing; every
    absence claim above becomes true and has to be published."""
    result = SD.run(_every_kind_of_page(broken=False))
    causes = {f["root_cause"] for f in result.findings}
    assert causes - NOT_A_JSONLD_ABSENCE_CLAIM, (
        "a site that publishes no structured data at all must still be told so; "
        "found only {}".format(sorted(causes)))
