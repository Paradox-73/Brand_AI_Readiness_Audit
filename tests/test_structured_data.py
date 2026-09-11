# -*- coding: utf-8 -*-
"""Grading the schema.org markup a site already publishes.

`test_snippets_are_true.py` holds the other half of this skill: what the
paste-ready block a report hands a developer is allowed to contain. This file
holds the reading - which nodes are found, which pages are asked for markup at
all, and what a check may say when it has nothing to compare.

Every case is a real site whose correct markup was reported as defective, or
whose page was graded as something it is not. They turned up on different sites
at different times, and they are all one mistake: a rule that was true of the sites
it was written on and false of the sites it was then applied to.

  `offers[0].priceSpecification[0].price`   eighteen pages of valid markup
                                            reported as offers "missing price
                                            and priceCurrency", with a fix that
                                            would have flattened working data
  an `AggregateOffer` with a range          reported as an offer "missing
                                            price", with a fix replacing a
                                            correct range with a made-up number
  `/blogs/coffee-spotlight`                 an index of posts graded as a post,
                                            and the snippet declared the
                                            archive's listing heading as the
                                            `headline` of a dated authored piece
  `/blog/2025/1/`                           five monthly archives given a
                                            `headline`, two dates and a `Person`
                                            author
  a nine-character Japanese title           "falls outside the recommended
                                            length", counted in Python
                                            characters
  a search box posting to a web engine      both sites told to declare that
                                            engine's results URL as their own
                                            `SearchAction` target
  no JSON-LD at all                         "the names and prices declared in
                                            JSON-LD match what the pages
                                            display", printed as a verified pass
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    ORG_IDENTITY_TYPES, SkillResult, jsonld_type_names,
)
from page_extract import _jsonld_types  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_structured_data_tests")

SITE = "https://a-roastery-that-does-not-exist.test"


def _page(url, page_type="other", headings=None, links=(), sections=(),
          paragraphs=(), title=""):
    """A snapshot page record with only the keys these checks read."""
    return {
        "url": SITE + url,
        "page_type": page_type,
        "status": 200,
        "title": title,
        "headings": headings or {},
        "sections": list(sections),
        "paragraphs": list(paragraphs),
        "links": {"internal": [{"url": SITE + u, "text": t} for u, t in links]},
        "jsonld": [],
        "jsonld_types": [],
    }


# --------------------------------------------------------------------------
# A price stated in a nested object is still stated
# --------------------------------------------------------------------------

def test_a_price_inside_a_price_specification_is_not_missing():
    """A restaurant's shop states every price as
    offers[0].priceSpecification[0].price, which is valid, is what its platform
    emits, and carries more than a flat price does. Reading only offer["price"]
    reported eighteen pages of correct markup as offers "missing price and
    priceCurrency", and the fix would have had the owner flatten working
    data."""
    offer = {"@type": "Offer",
             "priceSpecification": [{"@type": "UnitPriceSpecification",
                                     "price": "30.00", "priceCurrency": "GBP"}]}
    missing = SD._missing_props(offer, SD._offer_props(offer))
    assert "price" not in missing and "priceCurrency" not in missing


def test_an_aggregate_offer_is_graded_on_the_properties_it_has():
    """A coffee roaster's valid range pricing was reported as an offer
    "missing price", and the fix would have replaced a correct range with a
    single made-up number - which this report's own advice tells people not to
    do."""
    aggregate = {"@type": "AggregateOffer", "lowPrice": "8.75",
                 "highPrice": "9.75", "priceCurrency": "GBP"}
    assert SD._missing_props(aggregate, SD._offer_props(aggregate)) == []


def test_an_offer_stating_no_price_anywhere_is_still_reported():
    """The guard both of the fixes above have to survive: a flat offer that
    names a currency and no price is still incomplete markup."""
    offer = {"@type": "Offer", "priceCurrency": "GBP"}
    assert "price" in SD._missing_props(offer, SD._offer_props(offer))


# --------------------------------------------------------------------------
# One reader for `@type`, in every file
# --------------------------------------------------------------------------

def test_the_shared_reader_and_the_snapshot_agree():
    """One reader for `@type`, in every file.

    `schema:Organization` is correct and Google accepts it. The hand-rolled
    copy in `page_extract` split on the slash only, so it came out as
    `schema:organization` and matched nothing downstream - and a node whose
    type is a number used to end the run.
    """
    assert _jsonld_types([{"@type": "schema:Organization"}]) == ["organization"]
    assert _jsonld_types([{"@type": 5}, {"@type": "Product"}]) == ["product"]
    node = {"@type": ["https://schema.org/LocalBusiness", "schema:Store"]}
    assert set(_jsonld_types([node])) == jsonld_type_names(node["@type"])


def test_the_organisation_types_cover_the_professions():
    """The stale eight-member copy was missing these five, so on a site marked
    up as `ProfessionalService` the telephone and postcode checks found no
    organisation node while the description check sixty lines below found it."""
    for name in ("brand", "financialservice", "governmentorganization",
                 "medicalbusiness", "professionalservice"):
        assert name in ORG_IDENTITY_TYPES


# --------------------------------------------------------------------------
# An index of posts is not a post, whatever its URL segment is called
# --------------------------------------------------------------------------

def _article_result(pages):
    result = SkillResult("structured-data-audit")
    by_type = {}
    for page in pages:
        by_type.setdefault(page["page_type"], []).append(page)
    SD._check_article(result, by_type)
    return result


def test_a_blog_index_named_after_its_subject_is_not_graded_as_an_article():
    """Two shapes no list of English section words can catch.

    A shop platform lets the owner name each blog handle, so one index sits at
    `/blogs/coffee-spotlight`. A blogging platform puts the section word
    first, so a category archive is `/category/news/`. Both were reported as
    articles missing Article markup, and the generated snippet declared the
    archive's own listing heading as the `headline` of a dated authored piece.
    """
    linked = [("/blogs/coffee-spotlight/kenya-nyeri", "Kenya Nyeri, washed"),
              ("/blogs/coffee-spotlight/brazil-cerrado", "Brazil Cerrado, natural"),
              ("/blogs/coffee-spotlight/peru-cajamarca", "Peru Cajamarca, honey")]
    handle_index = _page(
        "/blogs/coffee-spotlight", "article",
        headings={"h1": ["Coffee spotlight"], "h3": [text for _url, text in linked]},
        links=linked)
    archive = _page("/category/news/", "article",
                    headings={"h1": [""], "h3": ["Opening hours change"]})
    post = _page("/blogs/coffee-spotlight/kenya-nyeri", "article",
                 headings={"h1": ["Kenya Nyeri, washed"]})

    result = _article_result([handle_index, archive, post])

    assert len(result.findings) == 1, "the one real post must still be reported"
    finding = result.findings[0]
    # The post's URL sits under the index's, so compare whole URLs rather than
    # asking whether one string contains the other.
    assert finding["affected_pages"] == [post["url"]]
    assert finding["evidence"].rstrip(".").endswith(post["url"])
    assert archive["url"] not in json.dumps(finding)
    assert handle_index["headings"]["h1"][0] not in json.dumps(finding), (
        "the index's own heading must never reach the snippet as a headline")


def test_a_site_whose_only_article_pages_are_indexes_produces_no_finding():
    """Nothing to grade is a decline with a reason, not a finding about zero."""
    linked = [("/blogs/brewing-methods/v60", "How to brew with a V60"),
              ("/blogs/brewing-methods/aeropress", "How to brew with an AeroPress"),
              ("/blogs/brewing-methods/chemex", "How to brew with a Chemex")]
    result = _article_result([_page(
        "/blogs/brewing-methods", "article",
        headings={"h3": [text for _url, text in linked]}, links=linked)])
    assert not result.findings
    assert any("index of links" in entry["reason"] for entry in result.not_applicable)


def test_a_dated_archive_path_is_not_asked_for_article_markup():
    """`/blog/2025/1/` carries the h1 "JANUARY 2025 News Archive" and one
    linked post. Five such archives on one site were reported as posts with no
    Article markup, and the generated block gave the January archive a
    `headline` of "JANUARY 2025 News Archive", a `datePublished`, a
    `dateModified` and a `Person` author.

    The page-type detector reads a year followed by any further segment as a
    permalink, so the archive arrives typed `article`; and the shared index
    test wants three link-shaped subheadings before it will answer, so an
    archive listing one post falls through it.
    """
    archive = _page("/blog/2025/1/", "article",
                    title="JANUARY 2025 News Archive | A Newsroom",
                    headings={"h1": ["JANUARY 2025 News Archive"],
                              "h2": ["Ferries resume after the storm"]},
                    links=[("/blog/ferries-resume/", "Ferries resume after the storm")])
    result = SkillResult("structured-data-audit")
    SD._check_article(result, {"article": [archive]})

    assert not result.findings, "an archive of posts is not a post"
    assert any("index of links" in entry["reason"] for entry in result.not_applicable)


def test_a_post_under_the_same_dated_section_is_still_graded():
    """The exclusion must not switch the check off for the pages it is for: a
    permalink ends in a slug, and that is what separates it from the archive."""
    post = _page("/blog/2025/ferries-resume/", "article",
                 title="Ferries resume after the storm",
                 headings={"h1": ["Ferries resume after the storm"]},
                 paragraphs=["The first sailing since Tuesday left at dawn, "
                             "with two hundred passengers aboard."])
    result = SkillResult("structured-data-audit")
    SD._check_article(result, {"article": [post]})

    assert [f["root_cause"] for f in result.findings] == ["no-article-schema"]


def test_a_page_whose_whole_content_is_a_list_of_pages_is_an_index():
    """The three-heading minimum protects against a page that links to a few of
    its own sections, and it stops mattering once the page has nothing else on
    it: every heading is the title of a page somewhere else and no paragraph is
    the page speaking."""
    index = _page("/what-we-published/", "article",
                  headings={"h2": ["Ferries resume after the storm"]},
                  links=[("/blog/ferries-resume/", "Ferries resume after the storm")])
    assert bool(SD._why_this_page_is_a_list(index)) is True

    with_prose = dict(index, paragraphs=[
        "The first sailing since Tuesday left at dawn, with two hundred aboard."])
    assert bool(SD._why_this_page_is_a_list(with_prose)) is False


# --------------------------------------------------------------------------
# A title says as much in seven characters of one script as in twenty of another
# --------------------------------------------------------------------------

# "<city> / disaster and crime prevention" and "<city> / home": nine and seven
# characters, both complete and specific, both reported as too short.
_UNSPACED_TITLES = [
    u"鏌倉市／防災・防犯",
    u"鏌倉市／ホーム",
    u"鏌倉市／ごみと資源",
    u"鏌倉市／子育て支援",
    u"鏌倉市／図書館案内",
]


def _title_problems(titles):
    result = SkillResult("structured-data-audit")
    pages = []
    for index, title in enumerate(titles):
        page = _page("/p{}".format(index), title=title)
        page["meta_description"] = (
            "A description long enough to be nobody's problem here. Page {}.".format(index))
        pages.append(page)
    SD._check_titles_and_descriptions(result, pages)
    return " ".join(f["evidence"] for f in result.findings)


def test_titles_in_an_unspaced_script_are_measured_in_that_script():
    assert "fall outside" not in _title_problems(_UNSPACED_TITLES)


def test_titles_that_really_are_too_short_are_still_reported():
    """The same five pages titled with one English word each."""
    assert "fall outside" in _title_problems(["Home", "News", "Shop", "Help", "Jobs"])


# --------------------------------------------------------------------------
# A SearchAction declares this site's search, not somebody else's
# --------------------------------------------------------------------------

def test_the_search_action_evidence_states_the_form_not_the_snippet():
    """The sentence said the snippet used the site's own target.

    The target is assembled from the form's action and the page it sits on, so
    the finished URL is a string no page ever showed, and the composer's last
    pass replaces any snippet value the crawl did not record. The sentence was
    printed above `"target": "<target - not found on the site, fill this in>"`.
    """
    home = _page("/", "home")
    home["has_search"] = True
    home["forms"] = [{"is_search": True, "action": "/site-search",
                      "query_field": "keywords"}]
    result = SkillResult("structured-data-audit")
    SD._check_website_searchaction(result, {"home": [home]})

    evidence = result.findings[0]["evidence"]
    assert "/site-search" in evidence and "keywords" in evidence
    assert "snippet" not in evidence.lower(), (
        "no evidence sentence may assert what the generated block contains: "
        "a later pass can replace any value in it")


def test_a_search_box_posting_off_site_is_reported_as_no_on_site_search():
    """Two sites' "search box" is a general web-search form, and both were told
    to publish a SearchAction whose target is that engine's own results URL -
    markup no consumer can use, about an index the site does not own."""
    home = _page("/", "home")
    home["has_search"] = True
    home["forms"] = [{"is_search": True, "query_field": "q",
                      "action": "https://a-search-engine-elsewhere.test/search"}]
    result = SkillResult("structured-data-audit")
    SD._check_website_searchaction(result, {"home": [home]})

    assert not result.findings, "a SearchAction may not name another site's endpoint"
    reason = " ".join(entry["reason"] for entry in result.not_applicable)
    assert "another site's search index" in reason
    assert "no on-site search" in reason


def test_a_search_form_on_the_sites_own_host_is_still_on_site_search():
    """An absolute action pointing back at the site is the same search box as a
    relative one, and the check must keep firing on it."""
    home = _page("/", "home")
    home["has_search"] = True
    home["forms"] = [{"is_search": True, "query_field": "keywords",
                      "action": SITE + "/site-search"}]
    result = SkillResult("structured-data-audit")
    SD._check_website_searchaction(result, {"home": [home]})

    assert [f["root_cause"] for f in result.findings] == ["no-website-schema"]
    assert SITE + "/site-search" in result.findings[0]["suggested_action"]["snippet"]


# --------------------------------------------------------------------------
# A page with its own address describes a place
# --------------------------------------------------------------------------

def _branch(i, street, postcode):
    return {"url": "https://x.test/restaurants/branch{}".format(i), "status": 200,
            "page_type": "other", "jsonld_types": [],
            "contact_facts": {"street_hint": street, "postcode_hint": postcode,
                              "declared_phones": ["+441000000{}".format(i)]}}


CHAIN = {"origin": "https://x.test", "site": "x.test", "brand": {"name": "Example"},
         "pages": [_branch(1, "51 Berwick Street", "W1F 8SJ"),
                   _branch(2, "12 Park Row", "LS1 5HD"),
                   _branch(3, "9 Union Street", "BS1 5EF"),
                   _branch(4, "9 Union Street", "BS1 5EF")]}


def test_branch_pages_with_no_place_markup_are_reported():
    result = SkillResult("structured-data-audit")
    SD._check_per_location_markup(result, CHAIN, CHAIN["pages"])
    assert [f["root_cause"] for f in result.findings] == ["no-org-schema"]
    finding = result.findings[0]
    snippet = finding["suggested_action"]["snippet"]
    assert "LocalBusiness" in finding["suggested_action"]["summary"]
    assert snippet.startswith('<script type="application/ld+json">')
    # The finding names the pages. It does not put their text-scraped addresses
    # in the snippet, where they would be an instruction to publish a regular
    # expression's output as schema.org ground truth.
    assert "branch" in finding["evidence"]
    assert "51 Berwick Street" not in snippet


def test_the_branch_snippet_never_publishes_a_regex_match_as_an_address():
    """`street_hint` and `postcode_hint` are patterns run over page text.

    Read as an address they produced, on real sites: a blog headline ("2026
    Shake Shack Hits the Road") as a street, a cosmetic colour-index code
    (15880) as a postal code, and the digits of the price "$0.00005 / event" as
    another. `_contact_facts_for_snippet` was fixed to refuse them; this
    snippet was not, and it is the one a chain would paste into every branch
    template. `pages_with_their_own_address` only returns pages where both
    hints matched, so the placeholder fallback could never fire and the guess
    went out every time.
    """
    junk = {"origin": "https://x.test", "site": "x.test", "brand": {"name": "Example"},
            "pages": []}
    for index, (street, postcode) in enumerate(
            [("2026 Shake Shack Hits the Road", "15880"),
             ("1 million row", "00005"),
             ("9 Let's Circle", "SW1A 1AA")], start=1):
        page = _branch(index, street, postcode)
        junk["pages"].append(page)

    result = SkillResult("structured-data-audit")
    SD._check_per_location_markup(result, junk, junk["pages"])
    assert result.findings, "a chain with no place markup should still be reported"
    snippet = result.findings[0]["suggested_action"]["snippet"]
    for guess in ("2026 Shake Shack Hits the Road", "15880", "1 million row", "00005"):
        assert guess not in snippet, "{!r} was published as an address".format(guess)
    assert "<this branch's street address>" in snippet
    assert "<this branch's postal code>" in snippet


def test_branch_pages_that_already_declare_a_place_are_left_alone():
    marked = {"origin": "https://x.test", "site": "x.test", "pages": []}
    for page in CHAIN["pages"]:
        copy = dict(page)
        copy["jsonld_types"] = ["Restaurant"]
        marked["pages"].append(copy)
    result = SkillResult("structured-data-audit")
    SD._check_per_location_markup(result, marked, marked["pages"])
    assert not result.findings
    assert result.not_applicable


# --------------------------------------------------------------------------
# A pass over nothing
# --------------------------------------------------------------------------

def _reason_from(result, name):
    payload = result.to_dict() if hasattr(result, "to_dict") else result
    for entry in payload.get("checks_skipped") or payload.get("not_applicable") or []:
        if entry.get("check") == name or entry.get("name") == name:
            return entry.get("reason", "")
    return ""


def test_agreement_is_not_reported_over_an_empty_set():
    """"the names and prices declared in JSON-LD match what the pages display",
    printed for four sites that carry no JSON-LD at all - in the same report
    whose leading finding is that the site declares none."""
    pages = [{"url": "https://example.test/", "final_url": "https://example.test/",
              "status": 200, "page_type": "home", "title": "Example",
              "body_text": "Example is a company that does a thing. " * 20,
              "text": "Example is a company that does a thing. " * 20,
              "headings": {"h1": ["Example"], "h2": [], "h3": []},
              "jsonld": [], "jsonld_types": {}, "jsonld_errors": [],
              "meta_description": "A description of reasonable length for a homepage.",
              "og": {}, "links": {"internal": [], "external": []}}]
    result = SD.run({
        "origin": "https://example.test", "site": "example.test",
        "brand": {"name": "Example"}, "pages": pages, "sitemaps": [],
    })
    reason = _reason_from(result, "jsonld-matches-visible-text")
    assert reason, "the check should still report why it stayed quiet"
    assert "nothing to compare" in reason
    assert "This is not a pass" in reason


# --------------------------------------------------------------------------
# The fold's own fix steps name the page kinds it folded, not a fixed list
# --------------------------------------------------------------------------

def _absence_finding(result, cause, title, pages, severity="medium"):
    """One "no X markup" finding, in the shape `_fold_absent_markup` reads."""
    result.check("organization-markup")
    result.add(
        id_hint=cause, title=title, severity=severity, confidence="high",
        evidence="Checked every crawled page.", mechanism="C", root_cause=cause,
        summary="Add the block.", how_to_fix=["Add the block to the template."],
        effort="low", owner="developer", affected_pages=pages,
        rationale="These facts are only stated as data in structured markup.",
        checked=("every JSON-LD block on every crawled page: there are none",),
    )


def _fold_over(causes):
    """Run the fold over exactly these absence causes and return its finding."""
    result = SkillResult("structured-data-audit")
    for cause, title in causes:
        _absence_finding(result, cause, title, [SITE + "/"])
    pages = [{"url": SITE + "/", "jsonld": [], "jsonld_types": [], "jsonld_errors": [],
              "microdata_types": [], "rdfa_count": 0}]
    SD._fold_absent_markup(result, pages)
    folded = [f for f in result.findings if "publishes no structured data" in f["title"]]
    assert len(folded) == 1, [f["title"] for f in result.findings]
    return " ".join(folded[0]["suggested_action"]["how_to_fix"])


def test_the_folds_step_names_the_page_kinds_it_actually_folded():
    """It used to read "the post template, the product page, the page of
    questions" whatever had been folded, and real reports printed that verbatim
    on a school, a shrine, a library and a personal homepage. The step lists page
    kinds; it has to list this run's."""
    steps = _fold_over([("no-org-schema", "No Organization markup"),
                        ("no-website-schema", "No WebSite markup"),
                        ("no-breadcrumb-markup", "No BreadcrumbList markup")])
    assert "the homepage template" in steps
    assert "the trail above a page's heading" in steps
    # None of the three kinds this site has no pages of.
    for absent in ("product", "post or article", "questions and answers"):
        assert absent not in steps, "the step names a page kind nothing folded: " + absent


def test_the_folds_step_still_names_a_product_template_on_a_shop():
    """The other direction: neutral wording is not the same as silence. A site
    whose product pages carry no markup must still be told where the block
    goes."""
    steps = _fold_over([("no-org-schema", "No Organization markup"),
                        ("no-product-schema", "No Product markup"),
                        ("no-article-schema", "No Article markup")])
    assert "one item for sale" in steps
    assert "one post or article" in steps
    assert "questions and answers" not in steps
