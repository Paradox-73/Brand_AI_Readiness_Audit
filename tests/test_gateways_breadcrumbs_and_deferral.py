# -*- coding: utf-8 -*-
"""A way into a site, a trail up it, and a site whose pages arrive in the browser.

The shapes these tests hold, each measured on a real consumer shop:

* A furniture retailer's root address is a server-rendered country picker -
  "Choose your local site to shop" and five plain links to regional
  storefronts, each a complete server-rendered site. Its framework mount point
  held those 86 characters, 492 bytes of script were inline, and the report
  led with "the homepage is delivered as an empty JavaScript shell", critical,
  several days of development.
* A jewellery and textiles shop delivered its homepage, its products and its
  category pages as shells, and its store pages as a menu around 71
  characters of their own. "32 of 34 pages have no H1", "no structured data at
  all" and "write two or three sentences of body copy" were each issued as
  their own job.
* The same retailer's blog index delivered a megabyte and not one link to a
  post, and was told to write copy.
* A coffee roaster ends every post with a "Back to the journal" link inside
  the content; the retailer shows "Home > Beds > Mattresses" above each
  product's H1. Both were reported as showing no trail.
* A one-page site's root is a script sending the browser to a language path.
* Two table-layout homepages - a directory of plain links - were told their
  menu had 0 items, beside an appendix saying the homepage links 15 others.
* The one-page summary said unread profiles "do not say it either", and
  "nothing off the site corroborates it" beside a finding naming the brand's
  Wikidata item.
* A national library's own meta description, joined with one extra space,
  was replaced in a snippet as a value "not found anywhere on this site".
* Every report said "92 checks were run" beside a README saying 96.

Every host here is invented and every brand is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_gateway_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_gateway_tests")
ENGAGE = _module("engagement-audit", "engagement_for_gateway_tests")
COMPOSE = _compose()


def _snapshot(origin, pages, brand="Northwind Anvils"):
    return {"origin": origin, "site": origin.split("//", 1)[1], "pages": pages,
            "brand": {"name": brand, "host": origin.split("//", 1)[1]},
            "sitemaps": [],
            "crawl": {"pages_crawled": len(pages), "pages_ok": len(pages),
                      "pages_read": len(pages), "render_mode": "static",
                      "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []}}


def _hints(result):
    return {f["id_hint"] for f in result.findings}


def _skip(result, check):
    return next(n["reason"] for n in result.not_applicable if n["check"] == check)


# --------------------------------------------------------------------------
# A country picker is a way in, not a container
# --------------------------------------------------------------------------

PICKER = "https://a-country-picker.test"
REGIONS = (("/us", "United States"), ("/sg", "Singapore"), ("/au", "Australia"),
           ("/ca", "Canada"), ("/uk", "United Kingdom"))


def _picker_home(root_text=86):
    links = [{"url": PICKER + path, "text": name, "fragment": ""} for path, name in REGIONS]
    return {"url": PICKER + "/", "final_url": PICKER + "/", "status": 200,
            "page_type": "home", "title": "Modern furniture online | Picker",
            "body_text": "c" * 127, "body_text_len": 127, "text": "c" * 127, "text_len": 127,
            "html_bytes": 55000,
            "spa_shell": {"root_selector": "#root", "root_text_len": root_text,
                          "state_blobs": [], "state_blob_bytes": 0,
                          "noscript_demands_js": False, "visible_text_len": 127},
            "scripts": {"inline_bytes": 492, "external_count": 3},
            "chrome_signature": {"nav_paths": []},
            "links": {"internal": links, "nav": links, "footer": [],
                      "main_internal_links": links},
            "headings": {"h2": ["Choose your local site to shop"]}}


def _storefront(path):
    url = PICKER + path
    return {"url": url, "final_url": url, "status": 200, "page_type": "content",
            "title": "Storefront " + path,
            "body_text": "b" * 4000, "body_text_len": 4000, "text": "v" * 8000, "text_len": 8000,
            "html_bytes": 900000,
            "spa_shell": {"root_selector": None, "root_text_len": None,
                          "state_blobs": ["self.__next_f"], "state_blob_bytes": 0,
                          "noscript_demands_js": False, "visible_text_len": 8000},
            "scripts": {"inline_bytes": 800000, "external_count": 80},
            "chrome_signature": {"nav_paths": ["/a", "/b", "/c"]},
            "links": {"internal": [], "nav": [{"url": url + "/sofas", "text": "Sofas"}],
                      "footer": [{"url": url + "/help", "text": "Help"}]},
            "headings": {"h1": ["Storefront"]}}


def _picker_site(root_text=86):
    return _snapshot(PICKER, [_picker_home(root_text)]
                     + [_storefront(path) for path, _ in REGIONS[1:]])


def test_a_country_picker_whose_links_reach_delivered_storefronts_is_not_a_shell():
    result = RENDER.run(_picker_site())
    assert "homepage-is-javascript-shell" not in _hints(result), [
        f["evidence"] for f in result.findings]
    assert result.signals.get("pages_that_are_gateways") == [PICKER + "/"]
    for finding in result.findings:
        assert PICKER + "/" not in finding.get("affected_pages", []), finding["title"]


def test_a_menu_around_an_empty_mount_point_is_still_a_shell():
    """The guard: the links are real, but none of them is inside the mount."""
    result = RENDER.run(_picker_site(root_text=0))
    assert "homepage-is-javascript-shell" in _hints(result)


def test_three_bundles_and_a_little_inline_script_are_not_a_lot_of_script():
    page = {"url": PICKER + "/app", "final_url": PICKER + "/app", "status": 200,
            "page_type": "content", "title": "App",
            "body_text": "a" * 50, "body_text_len": 50, "text": "a" * 50, "text_len": 50,
            "spa_shell": {"root_selector": None, "root_text_len": None, "state_blobs": [],
                          "state_blob_bytes": 0, "noscript_demands_js": True,
                          "visible_text_len": 50},
            "scripts": {"inline_bytes": 400, "external_count": 3}}
    verdict, reasons = RENDER.shell_verdict(page)
    assert verdict == RENDER.SHELL_UNSETTLED, reasons
    assert not any("against 400 bytes" in reason for reason in reasons), reasons


# --------------------------------------------------------------------------
# A site whose pages arrive in the browser
# --------------------------------------------------------------------------

BUILT = "https://a-browser-built-shop.test"
NAV = ["/c/{}".format(n) for n in range(40)]


def _page(path, page_type, body, whole, inline, chrome=True, h1=True):
    url = BUILT + path
    return {"url": url, "final_url": url, "status": 200, "page_type": page_type,
            "title": "Page " + path,
            "body_text": "b" * body, "body_text_len": body, "text": "v" * whole,
            "text_len": whole, "html_bytes": inline + 20000,
            "spa_shell": {"root_selector": None, "root_text_len": None,
                          "state_blobs": ["self.__next_f"], "state_blob_bytes": 0,
                          "noscript_demands_js": False, "visible_text_len": whole},
            "scripts": {"inline_bytes": inline, "external_count": 15},
            "chrome_signature": {"nav_paths": list(NAV) if chrome else [],
                                 "nav_path_count": len(NAV) if chrome else 0},
            "links": {"nav": [{"url": BUILT + p, "text": p} for p in NAV] if chrome else [],
                      "footer": [], "internal": []},
            "headings": {"h1": ["Page"]} if h1 else {}}


def _browser_built_shop():
    pages = [_page("/", "home", 71, 71, 327172, chrome=False, h1=False)]
    pages += [_page("/p/brooch-{}.html".format(n), "product", 63, 63, 214000,
                    chrome=False, h1=False) for n in range(4)]
    pages += [_page("/content/{}".format(name), "about", 700, 1400, 13500)
              for name in ("about", "story", "craft")]
    pages += [_page("/store-locator/{}".format(city), "location", 71, 783, 14400)
              for city in ("north", "south")]
    return _snapshot(BUILT, pages)


def test_a_short_page_carrying_the_shells_own_payload_is_not_told_to_write_copy():
    result = RENDER.run(_browser_built_shop())
    assert "pages-too-thin-to-quote" not in _hints(result), [
        f["affected_pages"] for f in result.findings]
    arrived = set(result.signals.get("pages_whose_copy_did_not_arrive") or [])
    assert {BUILT + "/store-locator/north", BUILT + "/store-locator/south"} <= arrived
    assert BUILT + "/content/about" not in arrived


def test_the_chrome_sentence_and_the_shell_count_give_one_answer():
    result = RENDER.run(_browser_built_shop())
    reason = _skip(result, "client-rendered-chrome")
    assert "not reported above as a JavaScript shell" in reason, reason


def test_a_server_rendered_site_publishes_no_undelivered_pages():
    """The guard: a hydration payload on every page is no evidence on its own."""
    pages = [p for p in _browser_built_shop()["pages"]
             if not p["url"].endswith(".html") and p["page_type"] != "home"]
    result = RENDER.run(_snapshot(BUILT, pages))
    assert not result.signals.get("pages_whose_copy_did_not_arrive")


JOURNAL = "https://a-journal-index.test"


def _journal_page(path, page_type, body, whole, links=()):
    url = JOURNAL + path
    return {"url": url, "final_url": url, "status": 200, "page_type": page_type,
            "title": "Journal " + path, "body_text": "b" * body, "body_text_len": body,
            "text": "v" * whole, "text_len": whole, "html_bytes": 1000000,
            "spa_shell": {"root_selector": None, "root_text_len": None,
                          "state_blobs": ["self.__next_f"], "state_blob_bytes": 0,
                          "noscript_demands_js": False, "visible_text_len": whole},
            "scripts": {"inline_bytes": 760000, "external_count": 80},
            "chrome_signature": {"nav_paths": ["/shop", "/about", "/contact"]},
            "links": {"nav": [{"url": JOURNAL + "/shop", "text": "Shop"}], "footer": [],
                      "internal": [{"url": JOURNAL + p, "text": p} for p in links]},
            "headings": {"h1": ["Journal"]}}


def _journal_site(index_links=()):
    pages = [_journal_page("/journal", "category", 250, 1300, index_links)]
    pages += [_journal_page("/guide-{}".format(n), "about", 1000, 2050) for n in range(3)]
    pages += [_journal_page("/journal/{}".format(slug), "article", 5000, 6050)
              for slug in ("sofa-fabrics", "cooling-covers")]
    return _snapshot(JOURNAL, pages)


def test_an_index_that_links_none_of_its_crawled_posts_is_not_told_to_write_copy():
    result = RENDER.run(_journal_site())
    assert "pages-too-thin-to-quote" not in _hints(result), [
        f["affected_pages"] for f in result.findings]
    assert result.signals.get("listing_pages_filled_in_by_script") == [JOURNAL + "/journal"]


def test_an_index_that_links_its_posts_is_judged_by_the_usual_rule():
    result = RENDER.run(_journal_site(index_links=("/journal/sofa-fabrics",)))
    assert "pages-too-thin-to-quote" in _hints(result)


NO_GREETING = "https://a-no-greeting.test"


def test_a_page_whose_content_is_a_script_redirect_is_not_told_to_write_copy():
    page = {"url": NO_GREETING + "/", "final_url": NO_GREETING + "/", "status": 200,
            "page_type": "home", "title": "no greeting",
            "body_text": "no greeting", "body_text_len": 11, "text": "no greeting",
            "text_len": 11,
            "spa_shell": {"root_selector": None, "root_text_len": None, "state_blobs": [],
                          "state_blob_bytes": 0, "noscript_demands_js": False,
                          "visible_text_len": 11},
            "scripts": {"inline_bytes": 359, "external_count": 1},
            "links": {"internal": [], "nav": [], "footer": []},
            "script_redirect": {"target": NO_GREETING + "/en/", "via": "window.location"}}
    result = RENDER.run(_snapshot(NO_GREETING, [page]))
    assert "pages-too-thin-to-quote" not in _hints(result)
    assert "server redirect (HTTP 301 or 302)" in _skip(result, "thin-html")


# --------------------------------------------------------------------------
# A trail up the site
# --------------------------------------------------------------------------

ROASTERY = "https://a-roastery.test"
MENU = ["/blogs/journal", "/shop", "/about", "/contact"]


def _roastery_post(main_links):
    chrome = [{"url": ROASTERY + p, "text": p} for p in MENU]
    return {"url": ROASTERY + "/blogs/journal/storing-beans", "page_type": "article",
            "depth": 2, "title": "Storing beans", "headings": {"h1": ["Storing beans"]},
            "links": {"nav": chrome, "footer": [],
                      "internal": chrome + list(main_links),
                      "main_internal_links": list(main_links)}}


def test_a_back_link_inside_the_content_counts_even_when_the_menu_carries_it():
    back = {"url": ROASTERY + "/blogs/journal", "text": "Back to the Roastery journal"}
    assert ENGAGE._links_to_own_parent(_roastery_post([back]))


def test_a_content_region_holding_the_menu_does_not_count():
    whole_menu = [{"url": ROASTERY + p, "text": p} for p in MENU]
    assert not ENGAGE._links_to_own_parent(_roastery_post(whole_menu))


FURNITURE = "https://a-furniture-shop.test"


def _product(with_markup=True):
    page = {"url": FURNITURE + "/sg/products/cloud-mattress", "page_type": "product",
            "depth": 2, "title": "Cloud Mattress | Furnish",
            "headings": {"h1": ["Cloud Mattress"]},
            "links": {"nav": [{"url": FURNITURE + "/sg/contact", "text": "Contact"}],
                      "footer": [{"url": FURNITURE + "/sg/help", "text": "Help"}],
                      "internal": [{"url": FURNITURE + "/sg", "text": ""},
                                   {"url": FURNITURE + "/sg/beds/all-bedroom", "text": "Beds"},
                                   {"url": FURNITURE + "/sg/beds/mattress",
                                    "text": "Mattresses"}],
                      "main_internal_links": []},
            "jsonld": []}
    if with_markup:
        page["jsonld"] = [{"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "item": {"@id": "/sgbeds", "name": "Beds"}},
            {"@type": "ListItem", "position": 2,
             "item": {"@id": "/sgbeds/mattresses", "name": "Mattresses"}}]}]
    return page


def test_a_trail_to_a_category_its_own_markup_names_counts():
    assert ENGAGE._links_to_own_parent(_product())


def test_the_logo_link_to_a_regional_root_is_not_a_trail():
    assert not ENGAGE._links_to_own_parent(_product(with_markup=False))


def test_a_link_back_to_the_page_itself_is_not_a_trail():
    url = FURNITURE + "/store-locator/"
    page = {"url": url, "page_type": "location", "depth": 1, "title": "Find a store",
            "headings": {"h1": ["Find a store near you"]},
            "links": {"nav": [], "footer": [],
                      "internal": [{"url": url, "text": "Store Locator"}]},
            "jsonld": [{"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home"},
                {"@type": "ListItem", "position": 2, "name": "Store Locator"}]}]}
    assert not ENGAGE._links_to_own_parent(page)


# --------------------------------------------------------------------------
# A homepage with no menu markup
# --------------------------------------------------------------------------

DIRECTORY = "https://a-holding-company.test"


def _directory_home(labels):
    links = [{"url": DIRECTORY + "/" + label.lower().replace(" ", "-"), "text": label,
              "fragment": ""} for label in labels]
    return {"url": DIRECTORY + "/", "final_url": DIRECTORY + "/", "page_type": "home",
            "links": {"nav": [], "footer": [], "internal": links},
            "chrome_signature": {"nav_path_count": 0, "nav_paths": []}}


def test_a_homepage_without_menu_markup_uses_its_own_links_as_navigation():
    home = _directory_home(["Annual Reports", "News Releases", "Letters",
                            "Corporate Governance"])
    result = SkillResult("engagement-audit")
    ENGAGE._check_navigation(result, home, [home])
    assert not result.findings, [f["evidence"] for f in result.findings]
    assert "links 4 other addresses of this site" in _skip(result, "primary-navigation")


def test_a_homepage_that_links_nowhere_still_has_no_navigation():
    home = _directory_home([])
    result = SkillResult("engagement-audit")
    ENGAGE._check_navigation(result, home, [home])
    assert "0 item(s)" in result.findings[0]["evidence"]


# --------------------------------------------------------------------------
# The report: what defers, what it says, what it counts
# --------------------------------------------------------------------------

SHOP = "https://a-deferring-shop.test"
HOME, P1, P2, P3, ABOUT = (SHOP + "/", SHOP + "/p/one", SHOP + "/c/two", SHOP + "/c/three",
                           SHOP + "/content/about")


def _finding(id_hint, root_cause, pages, skill, mechanism="C", severity="medium"):
    return {"id_hint": id_hint, "title": id_hint.replace("-", " "), "severity": severity,
            "confidence": "high", "evidence": "Measured on {} pages.".format(len(pages)),
            "mechanism": mechanism, "root_cause": root_cause,
            "affected_pages": list(pages), "affected_page_count": len(pages),
            "checked": ["the delivered HTML", "the page markup"],
            "suggested_action": {"summary": "Fix it.", "effort": "low",
                                 "owner": "developer", "how_to_fix": ["Fix it."],
                                 "rationale": "It matters."},
            "_skill": skill}


def _results(findings, render_signals=None):
    by_skill = {}
    for finding in findings:
        finding = dict(finding)
        by_skill.setdefault(finding.pop("_skill"), []).append(finding)
    return [{"skill": skill, "findings": by_skill.get(skill, []),
             "signals": (render_signals or {}) if skill == "render-readability-audit" else {},
             "checks_run": ["spa-shell-detection"] if skill == "render-readability-audit"
             else [], "not_applicable": [], "fired_checks": [], "extra_requests_made": 0}
            for skill in COMPOSE.SKILL_ORDER
            if skill in by_skill or skill == "render-readability-audit"]


def _shop_snapshot(meta=""):
    pages = []
    for url in (HOME, P1, P2, P3, ABOUT):
        body = "The workshop forges garden tools by hand in small batches every week."
        pages.append({"url": url, "final_url": url, "status": 200, "title": "A page",
                      "page_type": "home" if url == HOME else "content",
                      "body_text": body, "paragraphs": [body], "meta_description": meta})
    return _snapshot(SHOP, pages)


def _compose_shop(findings, undelivered, meta=""):
    return COMPOSE.compose(
        _shop_snapshot(meta),
        _results(findings, {"pages_whose_copy_did_not_arrive": list(undelivered)}),
        audited_at="2026-01-01T00:00:00Z")


def _by_hint(report, hint):
    return next(f for f in report["findings"] if f.get("stable_id") == hint)


SHELL = ("content-pages-are-javascript-shells", "js-shell", [P1], "render-readability-audit")
HEADINGS = ("heading-structure-unclear", "heading-structure", [HOME, P2, P3, ABOUT],
            "fact-extractability-audit")


def test_a_finding_most_of_whose_pages_did_not_arrive_says_so_and_leaves_start_here():
    report = _compose_shop([_finding(*SHELL), _finding(*HEADINGS)], [P1, HOME, P2, P3])
    headings = _by_hint(report, "heading-structure-unclear")
    part = headings.get("partly_follows_from")
    assert part and part["page_count"] == 3 and part["rest"] == [ABOUT], part
    assert headings["id"] not in report["start_here"]
    assert "partly the same defect as" in COMPOSE.render_markdown(report)


def test_a_finding_every_page_of_which_did_not_arrive_defers_whole():
    report = _compose_shop([_finding(*SHELL), _finding(*HEADINGS)],
                           [P1, HOME, P2, P3, ABOUT])
    follows = _by_hint(report, "heading-structure-unclear").get("follows_from")
    assert follows and follows["basis"] == "copy", follows


def test_without_a_shell_finding_the_list_defers_nothing():
    """The guard: on a site no shell was found on, content findings stand."""
    report = _compose_shop([_finding(*HEADINGS)], [HOME, P2, P3])
    headings = _by_hint(report, "heading-structure-unclear")
    assert not headings.get("partly_follows_from") and not headings.get("follows_from")


COUNTS = {"critical": 0, "high": 1, "medium": 8, "low": 3, "info": 0}
UNDECLARED = [{"root_cause": "no-entity-definition", "severity": "medium"},
              {"root_cause": "weak-corroboration", "severity": "medium"}]


def test_the_verdict_does_not_report_what_unread_profiles_say():
    breadth = COMPOSE.THIN_PROFILE_BREADTH + 3
    verdict = COMPOSE._verdict(COUNTS, UNDECLARED, {}, {"profile_breadth": breadth},
                               "Northwind Anvils")
    assert "do not say it either" not in verdict, verdict
    assert "counted and did not read" in verdict, verdict


def test_the_verdict_names_the_wikidata_item_another_skill_found():
    verdict = COMPOSE._verdict(COUNTS, UNDECLARED, {},
                               {"profile_breadth": 0, "wikidata_own_entity": "Q4242424",
                                "wikidata_item_exists": True}, "Northwind Anvils")
    assert "Q4242424" in verdict, verdict
    assert "nothing off the site corroborates it" not in verdict, verdict
    assert "nothing it can safely repeat about who the brand is" not in verdict, verdict


def test_the_appendix_says_how_many_checks_the_marketplace_has():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    stated = int(re.search(r"\*\*(\d+) checks\*\*", readme).group(1))
    assert len(COMPOSE.checks_the_marketplace_registers()) == stated
    report = _compose_shop([_finding(*HEADINGS)], [])
    paragraph = COMPOSE._appendix_paragraph(report)
    assert "1 of the marketplace's {} checks ran".format(stated) in paragraph, paragraph


ADVERT = ("FOR A FREE QUOTE ON GARDEN TOOL COVER VISIT WWW.AN-INSURER.TEST OR CALL "
          "1-800-000-0000, 24 HOURS A DAY")
DESCRIPTION = "Northwind Anvils forges garden tools by hand in a two-person workshop."


def test_the_top_sheet_prefers_the_sites_own_description_over_an_advert():
    report = _compose_shop([_finding(*HEADINGS)], [], meta=DESCRIPTION)
    report["citation_simulation"] = [{"page_type": "home", "likely_citation": ADVERT}]
    _, what, _ = COMPOSE.top_sheet(report)[0]
    assert DESCRIPTION in what, what
    assert "FREE QUOTE" not in what, what


def test_a_licence_line_and_an_advert_are_not_descriptions():
    assert not COMPOSE.reads_as_a_description(ADVERT)
    assert not COMPOSE.reads_as_a_description(
        "本中文文档采用 知识共享署名-非商业性使用-相同方式共享 4.0 国际许可协议 "
        "(CC BY-NC-SA 4.0) 进行许可。")
    assert COMPOSE.reads_as_a_description(DESCRIPTION)


def test_the_top_sheet_says_what_kind_of_site_the_advice_assumes():
    report = _compose_shop([_finding(*HEADINGS)], [], meta=DESCRIPTION)
    _, what, _ = COMPOSE.top_sheet(report)[0]
    assert ("This audit read it as" in what
            or "could not tell what kind of site this is" in what), what


def test_a_value_that_differs_only_in_spacing_was_observed():
    meta = "架空図書館の公式ウェブサイトです。当館は資料を広く収集しています。"
    haystack = COMPOSE.observed_values(_shop_snapshot(meta))
    snippet = ('{\n  "@type": "Organization",\n  "description": '
               '"架空図書館の公式ウェブサイトです。 当館は資料を広く収集しています。"\n}')
    _, invented = COMPOSE.hold_back_invented_values(snippet, haystack)
    assert invented == [], invented


def test_a_telephone_number_still_has_to_match_as_written():
    """The guard: the whitespace-blind reading is not for the claim fields."""
    snapshot = _shop_snapshot()
    snapshot["pages"][0]["body_text"] += " Call +442079460000."
    haystack = COMPOSE.observed_values(snapshot)
    _, invented = COMPOSE.hold_back_invented_values(
        '{\n  "telephone": "+44 20 7946 0000"\n}', haystack)
    assert invented, "a telephone number was accepted with its spacing changed"


def _date_recommendation(coverage):
    finding = dict(_finding("sitemap-lastmod-sparse", "no-date-signal", [],
                            "freshness-corroboration-audit", mechanism="D"))
    finding.pop("_skill")
    return [r for r in COMPOSE.build_recommendations(
        _shop_snapshot(), {"date_coverage": coverage}, [finding])
        if r["id"] == "R-DATE-SIGNALS"]


def test_the_date_recommendation_does_not_fire_against_its_own_measurement():
    assert not _date_recommendation(0.96)


def test_the_date_recommendation_still_fires_where_few_pages_are_dated():
    assert _date_recommendation(0.1)
