# -*- coding: utf-8 -*-
"""Snippet values, core facts, dates and names, read off the pages that own them.

Every case here is one value the report printed as the site's own when the
page it came from said something else:

  a language edition's description    a one-page site in nineteen languages
                                      got the Czech edition's line as its
                                      Organization `description`
  a date with two time zones          `2025-08-11T02:26:36.174Z+09:00`, copied
                                      into a paste-ready Article block, and
                                      passed by the validity check on every page
  a company record for a non-company  a central bank and an essay told to claim
                                      an employer profile
  an example number in a manual       `202-...` from a command-line example
                                      printed as the project's contact route
  a licence grant read as a price     "application code is free to use these
                                      routines" filed as "costs nothing"
  a source folder read as a place     "available in the source tree in the
                                      ext/misc/ subfolder" printed as a
                                      service area
  another program's author            "<Person>'s sql.js" printed as the team
  an event date as the newest post    "the newest article is dated 2026-10-20"
                                      on an audit run five weeks before it
  a sign-up form expected to be dated counted among the pages "where a date is
                                      expected"
  a children's museum as a misspelling "the brand name is written more than one
                                      way" about a section with its own name
  "Home" on every share card          an og:title of "Home" and a breadcrumb in
                                      og:site_name, passed as present
  a program given an Organization     a software project's block named the
                                      program as a company
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    ONLINE_SELLER, ORGANISATION, SiteKind, SkillResult, UNDETERMINED,
)


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module("structured-data-audit", "structured_for_seen_last")
FACTS = _module("fact-extractability-audit", "facts_for_seen_last")
FRESHNESS = _module("freshness-corroboration-audit", "freshness_for_seen_last")

HOST = "https://ferncastle-museum.test"
PROGRAM = "https://brookline-streams.test"


def _page(url, page_type="other", **extra):
    page = {"url": url, "final_url": url, "status": 200, "page_type": page_type,
            "lang": "en", "title": "", "meta_description": "", "paragraphs": [],
            "headings": {}, "links": {"internal": [], "external": []}, "og": {},
            "jsonld": [], "jsonld_types": [], "body_text": "", "text": ""}
    page.update(extra)
    return page


def _snapshot(pages, origin=HOST, name="Ferncastle Museum"):
    return {"site": origin, "origin": origin, "pages": pages, "sitemaps": [],
            "brand": {"name": name, "host": origin.split("//")[1]},
            "crawl": {"pages_crawled": len(pages), "pages_ok": len(pages),
                      "render_mode": "static", "user_agent": "test-agent",
                      "elapsed_s": 1.0, "notes": []}}


def _payload(snippet):
    body = re.sub(r"</?script[^>]*>", "", snippet)
    return json.loads(body)


# --------------------------------------------------------------------------
# 1. The organisation speaks in its front door's language
# --------------------------------------------------------------------------

def test_an_organisation_is_described_in_its_front_doors_language():
    """Nineteen editions of one page, all typed home. The English line was
    shared by `/` and `/en` and set aside as a duplicate, and the next home
    page in address order was the Czech one - whose line went into the block
    while the report quoted the English one above it."""
    english = "please keep your first chat message to the point"
    pages = [
        _page(HOST + "/", "home", lang="en", meta_description=english),
        _page(HOST + "/en", "home", lang="en", meta_description=english),
        _page(HOST + "/cs", "home", lang="cs",
              meta_description="prosím pište rovnou k věci v první zprávě"),
        _page(HOST + "/de", "home", lang="de",
              meta_description="bitte schreiben Sie gleich zur Sache"),
    ]
    snippet = SD._org_snippet(_snapshot(pages), pages, {"name": "Ferncastle Museum"})
    description = _payload(snippet)["description"]
    assert "prosím" not in description and "bitte" not in description, description
    assert "please keep" in description, "the front door's own line is what is quoted"


# --------------------------------------------------------------------------
# 2. A value that is not a date is named, never copied
# --------------------------------------------------------------------------

_TWO_ZONES = "2025-08-11T02:26:36.174Z+09:00"


def _dated_article(url, published):
    return _page(url, "article", jsonld=[{"@context": "https://schema.org",
                                          "@type": "Article", "headline": "Opening hours",
                                          "datePublished": published}],
                 jsonld_types=["Article"])


def test_a_snippet_does_not_copy_a_date_with_two_time_zones():
    snippet = SD._article_snippet(_dated_article(HOST + "/news/hours", _TWO_ZONES))
    published = _payload(snippet)["datePublished"]
    assert published != _TWO_ZONES
    assert published.startswith("<") and "time zone twice" in published, published


def test_a_date_with_two_time_zones_is_a_markup_fault():
    pages = [_dated_article(HOST + "/news/{}".format(i), _TWO_ZONES) for i in range(3)]
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, pages)
    faults = [f for f in result.findings if f["id_hint"] == "jsonld-date-is-not-a-date"]
    assert faults and len(faults[0]["affected_pages"]) == 3
    assert "Z" in faults[0]["evidence"] and "+09:00" in faults[0]["evidence"]


def test_well_formed_dates_are_not_reported():
    pages = [_dated_article(HOST + "/news/a", "2025-08-11"),
             _dated_article(HOST + "/news/b", "2025-08-11T02:26:36Z"),
             _dated_article(HOST + "/news/c", "2025-08-11T11:26:36+09:00"),
             _dated_article(HOST + "/news/d", "2025-08-11 11:26")]
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, pages)
    assert not result.findings, [f["evidence"] for f in result.findings]


# --------------------------------------------------------------------------
# 3. Business words only for a business
# --------------------------------------------------------------------------

def test_a_site_nothing_was_decided_about_is_not_told_to_claim_a_company_record():
    joined = " ".join(FRESHNESS._how_to_widen_the_footprint(
        SiteKind(UNDETERMINED, "low", ()))).lower()
    for phrase in ("company record", "employer profile", "industry"):
        assert phrase not in joined, phrase
    assert "registers and directories where organisations like this one are listed" in joined


def test_an_organisation_is_not_sent_to_trade_bodies():
    joined = " ".join(FRESHNESS._how_to_widen_the_footprint(
        SiteKind(ORGANISATION, "medium", ("an organisation with people",)))).lower()
    assert "trade bodies" not in joined and "company record" not in joined


def test_a_shop_is_still_told_to_claim_its_company_record():
    joined = " ".join(FRESHNESS._how_to_widen_the_footprint(
        SiteKind(ONLINE_SELLER, "high", ("sells",)))).lower()
    assert "employer profile" in joined


def test_the_same_as_step_names_no_industry_for_a_site_nothing_was_decided_about():
    step = SD._same_as_fix_step([], {"name": "Ferncastle Museum"}, _snapshot([]))
    assert "industry" not in step.lower()


# --------------------------------------------------------------------------
# 4. Core facts that belong to somebody else's sentence
# --------------------------------------------------------------------------

def test_a_number_in_a_command_example_is_not_a_contact_route():
    text = ("To quote a value, for example, (unless one intends a number): "
            ".parameter set @phone \"'202-555-0147'\" Note the quotes.")
    page = _page(PROGRAM + "/cli", "documentation", text=text,
                 body_text="Command Line Shell. Home About Documentation.")
    assert FACTS._usable_phones({"phones": ["202-555-0147"]}, page) == []


def test_a_vulnerability_identifier_is_not_a_telephone_number():
    page = _page(PROGRAM + "/forum", body_text="Fix for CVE-2026-50813 not merged into the branch?")
    assert FACTS._usable_phones({"phones": ["2026-50813"]}, page) == []


def test_a_number_the_site_prints_for_calling_it_is_kept():
    page = _page(HOST + "/contact", "contact", body_text="Call the front desk on 020 7946 0321.")
    assert FACTS._usable_phones({"phones": ["020 7946 0321"]}, page) == ["020 7946 0321"]


def _free(sentence):
    match = FACTS.COSTS_NOTHING_RE.search(sentence)
    assert match, sentence
    return match


def test_a_licence_grant_is_not_a_price():
    for sentence in ("Application code is free to use these routines as well, if desired.",
                     "To keep Brookline completely free and unencumbered by copyright, "
                     "patches are not accepted."):
        assert FACTS._free_is_a_permission(sentence, _free(sentence)), sentence
    sentence = "The toolkit is free to use."
    assert not FACTS._free_is_a_permission(sentence, _free(sentence))


def test_a_source_folder_is_not_a_service_area():
    sentence = ("These extensions are all available in the Brookline source tree in the "
                "ext/misc/ subfolder.")
    match = FACTS.SERVICE_AREA_RE.search(sentence)
    assert match and not FACTS._states_a_service_area(sentence, match)
    sentence = "Our couriers are available in Leeds, York and Hull."
    assert FACTS._states_a_service_area(sentence, FACTS.SERVICE_AREA_RE.search(sentence))


def _names_a_person(sentence, brand="Brookline"):
    return any(FACTS._states_a_named_person(sentence, match, brand, frozenset())
               for match in FACTS.TEAM_FACT_RE.finditer(sentence))


def test_another_programs_author_is_not_this_sites_team():
    for sentence in (
            "Mara Quill's widget.js is the first known port of it to a web browser.",
            "Tomas Reed's wa-brookline stores its databases inside the browser.",
            "Their demo includes a shell build. quiet-js is maintained by Nicholas Grey.",
            "2.1.1 Registering New VFS Objects Standard builds of Brookline come with "
            "two VFS objects."):
        assert not _names_a_person(sentence), sentence


def test_the_person_behind_the_site_is_still_named():
    assert _names_a_person("It is Wilma Stone's mail server that started life at a "
                           "research lab, and she continues to maintain it.")


# --------------------------------------------------------------------------
# 5. An event date is not the newest article
# --------------------------------------------------------------------------

AUDIT_DAY = datetime.date(2026, 9, 11)


def test_a_date_after_the_audit_is_not_a_publication_date():
    page = _page(HOST + "/events/festival", "article",
                 dates={"has_any": True, "machine_readable": ["2026-10-20T09:40:00Z"],
                        "visible": ["2026-08-01"]})
    assert FRESHNESS.newest_date(page, AUDIT_DAY) == datetime.date(2026, 8, 1)


def test_the_freshness_check_reads_articles_against_the_audit_day():
    pages = [_page(HOST + "/events/{}".format(i), "article",
                   dates={"has_any": True,
                          "machine_readable": ["2026-10-2{}T09:00:00Z".format(i)],
                          "visible": ["2026-07-0{}".format(i + 1)]})
             for i in range(3)]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_article_freshness(result, _snapshot(pages), pages, AUDIT_DAY)
    assert result.signals["newest_article_date"] == "2026-07-03"


# --------------------------------------------------------------------------
# 6. A form or a directions page is not a dated thing
# --------------------------------------------------------------------------

def test_a_sign_up_form_and_a_directions_page_are_not_expected_to_carry_a_date():
    dated = [_page(HOST + "/visit/notice-{}".format(i), dates={"has_any": True})
             for i in range(3)]
    signup = _page(HOST + "/visit/membership?step=details", "pricing",
                   title="Ferncastle Museum>My page>Join>Your details",
                   forms=[{"is_search": False, "is_newsletter": False, "field_count": 12,
                           "submits_off_site": False}],
                   dates={"has_any": False})
    directions = _page(HOST + "/visit/route-7", title="Ferncastle Museum>Visit>Getting here",
                       dates={"has_any": False})
    notes = _page(HOST + "/visit/exhibition-notes", title="Exhibition notes",
                  dates={"has_any": False})
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, dated + [signup, directions, notes])
    assert result.signals["utility_pages_not_expected_to_carry_a_date"] == 2
    finding = next(f for f in result.findings if f["root_cause"] == "no-date-signal")
    assert finding["affected_pages"] == [notes["url"]]


# --------------------------------------------------------------------------
# 7. A section with its own name is not a second spelling
# --------------------------------------------------------------------------

def _naming(names_on):
    pages = [_page(HOST + path, "home" if path == "/" else "other", lang="ko")
             for path in names_on.values()]
    brand = {"name": list(names_on)[0], "source": "og:site_name",
             "authoritative_variants": list(names_on),
             "authoritative_sources": ["og:site_name", "og:site_name-off-home"],
             "declared_on": {name: [HOST + path] for name, path in names_on.items()},
             "alternate_names": [], "pages_seen": len(pages)}
    result = SkillResult("fact-extractability-audit")
    FACTS._check_naming_consistency(result, {"pages": pages}, pages, brand)
    return result


def test_a_childrens_museum_is_a_part_of_the_museum_not_a_misspelling():
    result = _naming({"한빛박물관": "/", "어린이박물관": "/CHILD"})
    assert not result.findings, [f["evidence"] for f in result.findings]
    assert any("어린이박물관" in entry["reason"] for entry in result.not_applicable)


def test_two_unrelated_names_are_still_reported():
    result = _naming({"한빛박물관": "/", "푸른미술관": "/about"})
    assert result.findings


# --------------------------------------------------------------------------
# 8. Open Graph values that name no page, and a program's own block
# --------------------------------------------------------------------------

def _og_page(path, title, site_name="Ferncastle Museum", page_type="other"):
    return _page(HOST + path, page_type, og={"og:title": title, "og:site_name": site_name,
                                             "og:description": "A line.",
                                             "og:image": HOST + "/card.png"})


def test_home_on_every_page_and_a_breadcrumb_site_name_are_faults():
    pages = [_og_page("/", "Home", page_type="home")] + [
        _og_page("/visit/{}".format(i), "Home",
                 site_name="Ferncastle Museum>Visit>Hours>Room {}".format(i))
        for i in range(5)]
    result = SkillResult("structured-data-audit")
    SD._check_open_graph(result, pages)
    finding = next(f for f in result.findings if f["id_hint"] == "open-graph-values-name-no-page")
    assert '"Home"' in finding["evidence"] and "navigation path" in finding["evidence"]


def test_distinct_titles_and_a_plain_site_name_pass():
    pages = [_og_page("/", "Ferncastle Museum", page_type="home")] + [
        _og_page("/visit/{}".format(i), "Room {} guide".format(i)) for i in range(5)]
    result = SkillResult("structured-data-audit")
    SD._check_open_graph(result, pages)
    assert not result.findings


def _a_program():
    pages = [
        _page(PROGRAM + "/", "home", body_text="A small stream processor. Licensed under the "
                                               "Apache License.",
              links={"internal": [], "external": [
                  {"url": "https://github.com/invented-owner/brookline-streams"}]}),
        _page(PROGRAM + "/manual", "documentation", body_text="Filters read a stream."),
        _page(PROGRAM + "/tutorial", "documentation", body_text="Start with one filter."),
    ]
    return _snapshot(pages, origin=PROGRAM, name="Brookline"), pages


def test_a_software_project_is_given_a_software_entity_not_a_company():
    snapshot, pages = _a_program()
    assert SD.site_kind(snapshot).is_certainly(SD.PROJECT), SD.site_kind(snapshot)
    payload = _payload(SD._org_snippet(snapshot, pages, {"name": "Brookline"}))
    assert payload["@type"] == "SoftwareApplication"
    assert "address" not in payload and "telephone" not in payload
    assert not str(payload.get("logo", "")).startswith("<")


# --------------------------------------------------------------------------
# 9. A third party's robots.txt decides, Wikidata included
# --------------------------------------------------------------------------

class _Reply:
    def __init__(self, text="", payload=None):
        self.status_code = 200
        self.content = text.encode("utf-8")
        self.headers = {"content-type": "text/plain; charset=utf-8"}
        self._payload = payload

    def json(self):
        return self._payload


class _Wikidata:
    """A Wikidata that answers robots.txt with the given file and search with one item."""

    def __init__(self, robots):
        self.robots = robots
        self.urls = []

    def try_get(self, url, **kwargs):
        self.urls.append(url)
        if url.endswith("/robots.txt"):
            return _Reply(self.robots)
        if "wbgetentities" in url:
            return _Reply(payload={"entities": {}})
        return _Reply(payload={"search": [{"id": "Q1", "label": "Ferncastle Museum"}]})


def _entity_ambiguity(robots):
    stub = _Wikidata(robots)
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_entity_ambiguity(result, {"origin": HOST}, [],
                                      {"name": "Ferncastle Museum"}, {}, stub, True)
    return result, stub


def test_wikidata_is_not_asked_where_its_robots_file_closes_the_api():
    result, stub = _entity_ambiguity("User-agent: *\nDisallow: /w/\n")
    assert [u for u in stub.urls if "api.php" in u] == [], stub.urls
    assert not result.findings
    reason = next(n["reason"] for n in result.not_applicable if n["check"] == "entity-ambiguity")
    assert "robots.txt closes its API to automated clients, so no lookup was made" in reason


def test_wikidata_is_asked_where_its_robots_file_allows_it():
    result, stub = _entity_ambiguity("User-agent: *\nDisallow: /private/\n")
    assert any("wbsearchentities" in u for u in stub.urls)


# --------------------------------------------------------------------------
# 10. A product with variants agrees with a page showing any of them
# --------------------------------------------------------------------------

SHOP = "https://ferncastle-kitchen.test"


def _variant(size, price, availability):
    return {"@type": "Product", "name": "Brass Pot - " + size,
            "offers": {"@type": "Offer", "price": price, "priceCurrency": "INR",
                       "availability": "http://schema.org/" + availability}}


def test_a_page_showing_one_variants_price_agrees_with_the_markup():
    group = {"@type": "ProductGroup", "name": "Brass Pot",
             "hasVariant": [_variant("Small", "2720.00", "OutOfStock"),
                            _variant("Medium", "3000.00", "InStock")]}
    page = _page(SHOP + "/products/brass-pot", "product", title="Brass Pot",
                 headings={"h1": ["Brass Pot"]}, jsonld=[group], jsonld_types=["ProductGroup"],
                 body_text="Brass Pot Sale price Rs. 3,000.00 Add to cart. Cash on Delivery "
                           "for purchase upto Rs.20,000 within the country.")
    result = SkillResult("structured-data-audit")
    SD._check_consistency(result, [page], {"name": "Ferncastle Kitchen"})
    hints = [f["id_hint"] for f in result.findings]
    assert "jsonld-contradicts-visible-text" not in hints, [f["evidence"] for f in result.findings]
    assert "first-offer-is-out-of-stock" in hints


# --------------------------------------------------------------------------
# 11. A category in the maker's field
# --------------------------------------------------------------------------

def _branded(path, brand):
    return _page(SHOP + path, "product", jsonld=[{"@type": "Product", "name": path,
                                                  "brand": {"@type": "Brand", "name": brand}}],
                 jsonld_types=["Product"],
                 links={"internal": [{"url": SHOP + "/collections/roasted-coffee-beans"}],
                        "external": [], "nav": [{"url": SHOP + "/collections/ready-to-brew",
                                                 "text": "Ready to Brew"}]})


def test_a_product_type_in_the_brand_field_is_reported():
    pages = [_branded("/products/a", "Ferncastle Kitchen"), _branded("/products/b", "Ferncastle Kitchen"),
             _branded("/products/c", "RTB"), _branded("/products/d", "RTB"),
             _branded("/products/e", "Beans"), _branded("/products/f", "Beans")]
    snapshot = _snapshot(pages, origin=SHOP, name="Ferncastle Kitchen")
    result = SkillResult("structured-data-audit")
    SD._check_declared_brand_names(result, snapshot, pages, snapshot["brand"])
    finding = next(f for f in result.findings if f["id_hint"] == "product-brand-is-a-category")
    assert '"RTB"' in finding["evidence"] and '"Beans"' in finding["evidence"]
    assert "Ready to Brew" in finding["evidence"]


def test_a_retailer_naming_the_makers_it_stocks_is_not_reported():
    pages = [_branded("/products/a", "Northwind Boots"), _branded("/products/b", "Northwind Boots")]
    for page in pages:
        page["links"]["nav"] = [{"url": SHOP + "/collections/northwind-boots",
                                 "text": "Northwind Boots"}]
    snapshot = _snapshot(pages, origin=SHOP, name="Ferncastle Kitchen")
    result = SkillResult("structured-data-audit")
    SD._check_declared_brand_names(result, snapshot, pages, snapshot["brand"])
    assert not result.findings


# --------------------------------------------------------------------------
# 12. Snippet values from the product's own page; sameAs holds accounts only
# --------------------------------------------------------------------------

def test_same_as_holds_accounts_not_articles_or_sign_up_pages():
    assert not SD._is_an_account_and_not_an_action(
        "https://ferncastle.influence-platform.test/join/AtHomeWithFerncastle")
    assert not SD._is_an_account_and_not_an_action(
        "https://www.home-magazine.test/ferncastle-nesting-coffee-table-review-37439576")
    assert SD._is_an_account_and_not_an_action("https://social.test/ferncastlekitchen")


def test_a_product_on_a_listing_page_does_not_take_the_listings_values():
    listing = _page(SHOP + "/collections/all-rugs", "category",
                    meta_description="Shop our modern home decor for every room.",
                    og={"og:image": SHOP + "/thumbs/all-rugs.png"})
    node = {"@type": "Product", "name": "Cora Wool Rug", "image": SHOP + "/img/cora.png",
            "offers": {"@type": "Offer", "price": "549", "priceCurrency": "AUD"}}
    payload = _payload(SD._product_snippet(listing, "Ferncastle Kitchen", node))
    assert "description" not in payload
    assert payload["image"] == SHOP + "/img/cora.png"


def test_a_site_title_and_a_logo_are_not_a_products_name_and_image():
    page = _page(SHOP + "/product/red-canvas-sneakers", "product",
                 title="Online Shopping for Clothing and Accessories at Ferncastle",
                 headings={"h1": ["Online Shopping for Clothing and Accessories at Ferncastle"]},
                 og={"og:image": SHOP + "/static/img/newlogosticky.f7f01f0.png"})
    payload = _payload(SD._product_snippet(page, "Ferncastle"))
    assert payload["name"].startswith("<"), payload["name"]
    assert "image" not in payload


def test_escaped_text_carried_in_with_its_html_is_fixed_at_the_source():
    page = _page(SHOP + "/products/brass-pot", "product", jsonld=[{
        "@type": "Product", "name": "Brass Pot",
        "description": "<p>1. Keeps &gt;90% of the heat</p><p>2. Easy to clean</p>"}],
        jsonld_types=["Product"])
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, [page])
    finding = next(f for f in result.findings if f["id_hint"] == "jsonld-values-escaped-as-html")
    assert "plain text" in finding["suggested_action"]["summary"]
    assert not any("| escape" in step for step in finding["suggested_action"]["how_to_fix"])


def test_empty_faq_answers_are_a_markup_fault():
    faq = {"@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": "Do you deliver?",
         "acceptedAnswer": {"@type": "Answer", "text": ""}},
        {"@type": "Question", "name": "Can I return it?",
         "acceptedAnswer": {"@type": "Answer", "text": " "}}]}
    page = _page(SHOP + "/", "home", jsonld=[faq], jsonld_types=["FAQPage"])
    result = SkillResult("structured-data-audit")
    SD._check_faq(result, {"home": [page]}, [page])
    assert any(f["id_hint"] == "faq-markup-answers-are-empty" for f in result.findings)


def test_a_placeholder_telephone_is_a_markup_fault():
    org = {"@type": "Organization", "name": "Ferncastle Kitchen",
           "contactPoint": {"@type": "ContactPoint", "telephone": "00000000"}}
    pages = [_page(SHOP + "/", "home", jsonld=[org], jsonld_types=["Organization"])]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_fact_consistency(result, _snapshot(pages, origin=SHOP), pages)
    assert any(f["id_hint"] == "declared-telephone-is-a-placeholder" for f in result.findings)


# --------------------------------------------------------------------------
# 13. A page that lists its own section is an index, in both skills
# --------------------------------------------------------------------------

def _blog_index():
    posts = [SHOP + "/blogs/journal/post-{}".format(i) for i in range(6)]
    return _page(SHOP + "/blogs/journal", "article", dates={"has_any": False},
                 links={"internal": [{"url": u} for u in posts], "external": []},
                 readability={"sentence_count": 20, "avg_sentence_words": 30,
                              "list_text_share": 0.0, "long_sentence_share": 0.7})


def test_a_blog_front_page_is_not_asked_for_a_date():
    pages = [_page(SHOP + "/blogs/journal/post-{}".format(i), "article",
                   dates={"has_any": True}) for i in range(3)] + [_blog_index()]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, pages)
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_a_blog_front_page_is_not_graded_as_prose():
    assert not FACTS._holds_prose(_blog_index())


# --------------------------------------------------------------------------
# 14. A shop's core facts: promotions, terms clauses and copy that fits
# --------------------------------------------------------------------------

def test_a_sale_banner_is_not_a_price():
    assert FACTS._a_promotion_not_a_price("Spring Sale Up to $600 off.")
    assert not FACTS._a_promotion_not_a_price("Sale price Rs. 3,000.00")


def test_a_terms_clause_and_a_programme_page_speak_for_nobody():
    assert FACTS._a_terms_or_programme_page({"url": SHOP + "/sg/terms-of-use"})
    assert FACTS._a_terms_or_programme_page({"url": SHOP + "/au/ambassador-program"})
    assert not _names_a_person("You may not access non-public areas of the Services, "
                               "Ferncastle Pte Ltd's computer systems or its providers.",
                               brand="Ferncastle Kitchen")


def test_a_showroom_sentence_says_where_the_business_is():
    sentence = "Our showrooms in Sydney and Brisbane are open 7 days."
    assert FACTS._states_a_service_area(sentence, FACTS.SERVICE_AREA_RE.search(sentence))


def test_a_shop_is_never_handed_free_to_use_copy():
    steps = " ".join(FACTS._core_fact_steps("pricing", "Ferncastle Kitchen",
                                            any_currency_figure=False, sells=True))
    assert "free to use" not in steps


def test_a_line_addressing_the_reader_is_not_a_definition():
    home = _page(SHOP + "/", "home", headings={"h1": ["Ferncastle Kitchen"]},
                 body_text="Ferncastle Kitchen Your home should feel like an extension of you. "
                           "Shop now.")
    assert FACTS._definition_under_the_heading(home, "Ferncastle Kitchen",
                                               {"name": "Ferncastle Kitchen"}) is None


def test_a_definition_further_down_the_homepage_is_on_the_homepage():
    filler = " ".join("Handmade pieces for every room of the house." for _ in range(40))
    home = _page(SHOP + "/", "home", body_text=filler + " Who We Are? Ferncastle Kitchen is a "
                 "lifestyle brand in home decor that makes cookware by hand.")
    about = _page(SHOP + "/about", "about", body_text="Ferncastle Kitchen is a maker of brass "
                  "and copper cookware for everyday cooking.")
    pages = [about, home]
    result = SkillResult("fact-extractability-audit")
    FACTS._check_entity_definition(result, _snapshot(pages, origin=SHOP, name="Ferncastle Kitchen"),
                                   pages, "Ferncastle Kitchen", {"name": "Ferncastle Kitchen"})
    assert not any(f["id_hint"] == "definition-not-on-the-homepage" for f in result.findings), \
        [f["title"] for f in result.findings]


def test_a_site_nothing_was_decided_about_keeps_its_organization_block():
    pages = [_page(HOST + "/", "home")]
    payload = _payload(SD._org_snippet(_snapshot(pages), pages, {"name": "Ferncastle Museum"}))
    assert payload["@type"] in ("Organization", "LocalBusiness")
