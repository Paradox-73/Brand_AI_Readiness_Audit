"""One cause billed once, and summaries that say what the findings say.

An independent run over seven awkward sites turned up one shape seven ways:

  - a one-page site whose homepage is a script redirect drew "nothing can be
    reached from its homepage" (right, and prescribing the server redirect),
    then "set one H1" and "give the menu three items" about the same empty page;
  - a root address landing on a cover page - no text, two picture links, one
    per language edition - was described by three findings, none of which
    called it a cover page;
  - two virtual-tour pages that are nothing but a frame drew five findings from
    three skills;
  - "Machines are being shut out before they read anything" over a crawl that
    read sixty pages, and "A machine can reach <site> and read it" directly
    above "Nothing on this site can be reached from its homepage";
  - an address with an Arabic path came out of the report with invisible
    direction marks inside it, so the link a reader copied was not the one the
    site serves;
  - sixteen framework variable names in one evidence line, one 450-character
    paragraph printed under most findings, and a one-finding report 270 lines
    long;
  - "the ten questions your sales or support team answers" for an essay;
  - separate language editions and a children's section, each with a menu of
    its own, reported as pages missing the site's navigation;
  - "105 pages crawled" where 45 of the 105 records were images and documents.

Every host below is invented.
"""

from __future__ import annotations

import html
import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import ONLINE_SELLER, SkillResult  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_one_cause_seen_last", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_one_cause_seen_last")
ENGAGE = _module("engagement-audit", "engagement_for_one_cause_seen_last")
compose = _compose()

ORIGIN = "https://www.museum-of-things.test"
HOME = ORIGIN + "/"
TOUR_A = ORIGIN + "/exhibits/2022/tour/"
TOUR_B = ORIGIN + "/exhibits/2023/tour/"
ABOUT = ORIGIN + "/about"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _finding(id_hint, root_cause, pages, skill, severity="medium", mechanism="G",
             title=None, evidence=None, steps=None,
             checked=("the page text", "the page markup")):
    return {
        "id_hint": id_hint,
        "title": title or id_hint.replace("-", " "),
        "severity": severity,
        "confidence": "high",
        "evidence": evidence or "Measured on {} page(s).".format(len(pages)),
        "mechanism": mechanism,
        "root_cause": root_cause,
        "affected_pages": list(pages),
        "affected_page_count": len(pages),
        "checked": list(checked),
        "suggested_action": {
            "summary": "Do the thing.",
            "effort": "low",
            "owner": "developer",
            "how_to_fix": list(steps or ["Do the thing on the page."]),
            "rationale": "Because it matters.",
        },
        "_skill": skill,
    }


def _results(findings, signals=None, not_applicable=None):
    """One skill result per skill named, in marketplace order, with signals."""
    by_skill = {}
    for finding in findings:
        finding = dict(finding)
        by_skill.setdefault(finding.pop("_skill"), []).append(finding)
    for skill in (signals or {}):
        by_skill.setdefault(skill, [])
    for item in not_applicable or []:
        by_skill.setdefault(item["skill"], [])
    return [{"skill": skill, "findings": by_skill[skill],
             "signals": (signals or {}).get(skill, {}),
             "checks_run": [n["check"] for n in (not_applicable or [])
                            if n["skill"] == skill],
             "not_applicable": [n for n in (not_applicable or []) if n["skill"] == skill],
             "fired_checks": [], "extra_requests_made": 0}
            for skill in compose.SKILL_ORDER if skill in by_skill]


def _page(url, body, page_type="content"):
    return {"url": url, "final_url": url, "status": 200, "title": "A page about " + url,
            "page_type": page_type, "body_text": body, "paragraphs": [body]}


def _snapshot(extra=()):
    body = "The museum of things keeps a collection of everyday objects from four centuries."
    pages = [_page(HOME, body, "home"), _page(ABOUT, body + " It opened in 1901."),
             _page(TOUR_A, body + " The first tour."), _page(TOUR_B, body + " The second tour.")]
    pages += list(extra)
    return {
        "site": "www.museum-of-things.test", "origin": ORIGIN,
        "brand": {"name": "Museum of Things", "host": "www.museum-of-things.test"},
        "pages": pages, "sitemaps": [],
        "crawl": {"pages_crawled": len(pages), "pages_ok": len(pages),
                  "pages_read": len(pages), "render_mode": "static",
                  "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []},
    }


def _report(findings, signals=None, not_applicable=None, snapshot=None):
    return compose.compose(snapshot or _snapshot(),
                           _results(findings, signals, not_applicable),
                           audited_at="2026-01-01T00:00:00Z")


def _by_hint(report, hint):
    return next(f for f in report["findings"] if f["stable_id"] == hint)


# --------------------------------------------------------------------------
# 1a. A script redirect is one cause
# --------------------------------------------------------------------------

def test_a_finding_about_a_script_redirect_page_defers_to_the_redirect_finding():
    front_door = _finding("homepage-reaches-no-other-page", "unreachable-from-homepage",
                          [HOME], "engagement-audit", severity="high",
                          title="Nothing on this site can be reached from its homepage")
    heading = _finding("heading-structure-unclear", "heading-structure", [HOME],
                       "fact-extractability-audit", title="Heading structure is unclear",
                       steps=["Set one H1 per page stating what the page is about."])
    report = _report([front_door, heading], signals={
        "render-readability-audit": {"short_pages_that_redirect_by_script": [HOME]}})
    cause = _by_hint(report, "homepage-reaches-no-other-page")
    consequence = _by_hint(report, "heading-structure-unclear")
    assert consequence.get("follows_from", {}).get("id") == cause["id"]
    assert consequence["id"] not in report["start_here"]
    markdown = compose.render_markdown(report)
    assert "sends the browser on to another address with a script" in markdown
    # Nothing is lost: the consequence is still published in full.
    assert "Set one H1 per page" in markdown


def test_the_menu_of_a_script_redirect_homepage_is_not_judged():
    home = {"url": HOME, "final_url": HOME, "page_type": "home",
            "script_redirect": {"computed": True, "url": ""},
            "links": {"nav": [], "footer": [], "internal": []},
            "chrome_signature": {"nav_path_count": 0, "nav_paths": []}}
    result = SkillResult("engagement-audit")
    ENGAGE._check_navigation(result, home, [home])
    assert not result.findings, [f["evidence"] for f in result.findings]
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "primary-navigation")
    assert "server redirect" in reason


# --------------------------------------------------------------------------
# 1b and 6b. A cover page is named once
# --------------------------------------------------------------------------

def _cover_home():
    editions = [{"url": ORIGIN + "/th/home.html", "text": "", "fragment": ""},
                {"url": ORIGIN + "/en/home.html", "text": "", "fragment": ""}]
    return {"url": HOME, "final_url": ORIGIN + "/th/cover.html", "status": 200,
            "page_type": "home", "title": "Coin Bank", "body_text": "Coin Bank",
            "body_text_len": 16, "text": "Coin Bank", "text_len": 16,
            "headings": {"h1": [], "h2": [], "h3": []}, "cta": {}, "forms": [],
            "links": {"internal": editions, "nav": [], "footer": [],
                      "internal_count": 2, "external": [], "external_count": 0},
            "chrome_signature": {"nav_path_count": 0, "nav_paths": []},
            "images": {"count": 4, "described_count": 3, "large_image_count": 4,
                       "missing_alt_count": 1, "undescribed_count": 1,
                       "svg_text_nodes": 0, "canvas_count": 0}}


def test_a_cover_page_is_one_finding_that_names_it_and_states_its_costs():
    home = _cover_home()
    cover = ENGAGE._cover_page_links(home)
    assert len(cover) == 2
    result = SkillResult("engagement-audit")
    ENGAGE._check_homepage_orientation(result, home, True, cover=cover)
    ENGAGE._check_navigation(result, home, [home], cover=cover)
    assert [f["id_hint"] for f in result.findings] == ["homepage-is-a-cover-page"]
    evidence = result.findings[0]["evidence"]
    assert "cover page" in evidence
    assert "one for each language edition" in evidence
    assert "none of those links carries a word of text" in evidence
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "primary-navigation")
    assert "cover page" in reason


def test_the_render_skill_does_not_bill_a_cover_pages_pictures_separately():
    result = SkillResult("render-readability-audit")
    RENDER._check_image_locked(result, [_cover_home()], [])
    assert not result.findings, [f["evidence"] for f in result.findings]
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "facts-locked-in-images")
    assert "cover page" in reason


def test_a_finding_only_about_the_cover_page_defers_to_it():
    cover = _finding("homepage-is-a-cover-page", "no-orientation", [HOME],
                     "engagement-audit")
    search = _finding("website-searchaction-missing", "no-website-schema", [HOME],
                      "structured-data-audit", severity="low")
    report = _report([cover, search], signals={
        "engagement-audit": {"homepage_is_a_cover_page": HOME}})
    assert (_by_hint(report, "website-searchaction-missing").get("follows_from", {})
            .get("stable_id")) == "homepage-is-a-cover-page"


# --------------------------------------------------------------------------
# 1c. A page that is only a frame
# --------------------------------------------------------------------------

def _tour(url):
    return {"url": url, "final_url": url, "status": 200, "page_type": "article",
            "title": "A collector's gift", "body_text": "A collector's gift, tour",
            "body_text_len": 41, "text_len": 41,
            "links": {"internal": [], "external": [], "internal_count": 0,
                      "external_count": 0, "nav": [], "footer": []},
            "iframes": [{"src": "https://streaming.museum-of-things.test/vr/tour.html",
                         "is_video": False, "is_map": False, "is_audio": False,
                         "is_invisible": True, "title": ""}]}


def test_a_page_that_is_only_a_frame_of_its_own_site_is_reported_as_one():
    assert RENDER._frame_is_the_page(_tour(TOUR_A)) is not None
    # A tag manager's hidden frame is on somebody else's host.
    tagged = _tour(TOUR_A)
    tagged["iframes"][0]["src"] = "https://tags.analytics-vendor.test/ns.html?id=1"
    assert RENDER._frame_is_the_page(tagged) is None
    result = SkillResult("render-readability-audit")
    RENDER._check_iframed_content(result, [_tour(TOUR_A), _tour(TOUR_B)], [])
    assert [f["id_hint"] for f in result.findings] == ["main-content-in-iframe"]
    assert set(result.findings[0]["affected_pages"]) == {TOUR_A, TOUR_B}


def test_findings_whose_pages_are_the_frame_pages_defer_to_the_frame_finding():
    frame = _finding("main-content-in-iframe", "iframe-content", [TOUR_A, TOUR_B],
                     "render-readability-audit", mechanism="C", checked=())
    crumbs = _finding("deep-pages-have-no-breadcrumb", "no-breadcrumbs",
                      [TOUR_A, TOUR_B], "engagement-audit")
    dead = _finding("pages-with-no-next-step", "dead-end", [TOUR_A, TOUR_B],
                    "engagement-audit")
    wider = _finding("heading-structure-unclear", "heading-structure",
                     [ABOUT, TOUR_A, TOUR_B], "fact-extractability-audit")
    report = _report([frame, crumbs, dead, wider])
    frame_id = _by_hint(report, "main-content-in-iframe")["id"]
    for hint in ("deep-pages-have-no-breadcrumb", "pages-with-no-next-step"):
        assert _by_hint(report, hint).get("follows_from", {}).get("id") == frame_id, hint
    # A finding naming a page the frame does not explain stands on its own.
    assert not _by_hint(report, "heading-structure-unclear").get("follows_from")


# --------------------------------------------------------------------------
# 2. No direction marks inside an address
# --------------------------------------------------------------------------

def test_an_address_with_a_right_to_left_path_is_left_byte_for_byte():
    address = ORIGIN + "/authors/view/17941/أ.-د.-حسن/"
    text = "Long blocks on {}: 1276 words. قال the author.".format(address)
    out = compose.isolate_right_to_left_runs(text)
    assert address in out
    # The prose around it is still isolated.
    assert "⁨قال⁩" in out
    for line in ("- " + address, "- www.museum-of-things.test/عربي/",
                 "see /عربي/page"):
        assert "⁨" not in compose.decode_rendered_markdown(line), line


# --------------------------------------------------------------------------
# 3. Summaries built from the findings
# --------------------------------------------------------------------------

COUNTS = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}


def test_a_crawl_that_read_the_site_is_not_told_machines_are_shut_out():
    refused = {"root_cause": "bot-manager-block", "mechanism": "A", "id": "F-001",
               "title": "A bot manager or WAF refuses 2 named answer-engine crawlers and "
                        "serves the rest"}
    for severity in ("critical", "high"):
        verdict = compose._verdict(
            dict(COUNTS, **{severity: 1}), [dict(refused, severity=severity)], {}, {},
            readable_pages=58, readable_share=0.97, pages_reached=60)
        assert "shut out before they read anything" not in verdict, severity
        assert "nothing else on the site can be relied on to be read" not in verdict
        assert "refuses 2 named answer-engine crawlers" in verdict
        assert "58 pages" in verdict


def test_the_verdict_does_not_say_the_site_can_be_reached_over_the_front_door_finding():
    front_door = {"root_cause": "unreachable-from-homepage", "severity": "high",
                  "mechanism": "G", "id": "F-001",
                  "title": "Nothing on this site can be reached from its homepage"}
    identity = [{"root_cause": "no-org-schema", "severity": "medium",
                 "id_hint": "no-organization-schema"},
                {"root_cause": "weak-corroboration", "severity": "medium"}]
    for findings, counts in (([front_door] + identity, dict(COUNTS, high=1, medium=2)),
                             ([front_door], dict(COUNTS, high=1))):
        verdict = compose._verdict(counts, findings, {}, {"profile_breadth": 1},
                                   "Museum of Things", readable_pages=18,
                                   readable_share=0.95, pages_reached=19)
        assert "can reach Museum of Things and read it" not in verdict
        assert "Access and delivery are sound" not in verdict
        assert "cannot follow a link from it" in verdict


# --------------------------------------------------------------------------
# 4. Written for a non-expert: cut, and said once
# --------------------------------------------------------------------------

NAMES = ["`__NAME_{}__`".format(n) for n in range(16)]
LONG_STEP = ("If you do not know who edits your pages, ask whoever built or maintains the "
             "site: an agency, a freelancer, or failing both your hosting provider's "
             "support, who can at least tell you which system the site runs on.")


def test_a_long_list_of_code_names_is_cut_in_the_documents_and_kept_in_the_json():
    finding = _finding("pages-too-thin-to-quote", "thin-html", [ABOUT],
                       "render-readability-audit", mechanism="C",
                       evidence="None holds a state blob under any of the 16 names ({}).".format(
                           ", ".join(NAMES)))
    report = _report([finding])
    for document in (compose.render_markdown(report), compose.render_html(report)):
        assert "and 13 more, all named in report.json" in document
        assert NAMES[-1].strip("`") not in document
    assert NAMES[-1] in _by_hint(report, "pages-too-thin-to-quote")["evidence"]


def test_a_repeated_long_step_is_printed_once_and_referred_to_after():
    first = _finding("no-organization-schema", "no-org-schema", [ABOUT],
                     "structured-data-audit", steps=["Add the block.", LONG_STEP])
    second = _finding("missing-mobile-viewport", "mobile-viewport", [ABOUT],
                      "engagement-audit", checked=(), steps=["Add the tag.", LONG_STEP])
    report = _report([first, second])
    for document, step in ((compose.render_markdown(report), LONG_STEP),
                           (compose.render_html(report), html.escape(LONG_STEP))):
        assert document.count(step) == 1
        assert "If you do not know who edits your pages - written out in full under" \
            in document
    for finding in report["findings"]:
        assert LONG_STEP in finding["suggested_action"]["how_to_fix"]


def test_one_reason_given_by_several_checks_is_printed_once():
    reason = "the host answered with a certificate chain missing its intermediate"
    declines = [{"check": name, "skill": "crawl-access-audit", "reason": reason}
                for name in ("sitemap-present", "sitemap-parses", "robots-txt-reachable")]
    report = _report([], not_applicable=declines)
    markdown = compose.render_markdown(report)
    section = markdown.split("### Checks that did not apply, and why")[1].split("###")[0]
    assert section.count(reason) == 1
    for name in ("sitemap-present", "sitemap-parses", "robots-txt-reachable"):
        assert "**{}**".format(name) in section


# --------------------------------------------------------------------------
# 5. Business wording for businesses
# --------------------------------------------------------------------------

class _Kind:
    def __init__(self, kind=None):
        self.kind = kind

    def is_certainly(self, *kinds):
        return self.kind in kinds


def test_the_faq_step_names_a_sales_team_only_for_a_business():
    undetermined = compose._worded_for(_Kind(), compose._FAQ_SOURCE,
                                       compose._FAQ_SOURCE_DEFAULT)
    assert "sales" not in undetermined and "support team" not in undetermined
    shop = compose._worded_for(_Kind(ONLINE_SELLER), compose._FAQ_SOURCE,
                               compose._FAQ_SOURCE_DEFAULT)
    assert "sales or support team" in shop


# --------------------------------------------------------------------------
# 6a. A section with a menu of its own is not a page without one
# --------------------------------------------------------------------------

MAIN_MENU = ["/about", "/visit/hours", "/visit/map", "/collections/objects",
             "/news/list", "/KIDS"]


def _chrome_page(path, nav_paths):
    url = ORIGIN + path
    links = [{"url": ORIGIN + p, "text": p} for p in nav_paths]
    return {"url": url, "final_url": url, "status": 200, "page_type": "content",
            "chrome_signature": {"nav_path_count": len(nav_paths), "nav_paths": nav_paths},
            "links": {"nav": links, "footer": [], "internal": links}}


def test_a_section_carrying_its_own_menu_is_not_reported_as_stranded():
    kids_menu = ["/KIDS", "/KIDS/visit", "/KIDS/play", "/KIDS/learn", "/KIDS/shows",
                 "/KIDS/map", "/KIDS/faq", "/KIDS/news", "/about"]
    pages = [_chrome_page("/", MAIN_MENU)] + [
        _chrome_page("/page-{}".format(n), MAIN_MENU + ["/page-{}".format(n)])
        for n in range(5)] + [
        _chrome_page("/KIDS", kids_menu),
        _chrome_page("/KIDS/visit", kids_menu),
        _chrome_page("/landing/offer", [])]
    result = SkillResult("engagement-audit")
    ENGAGE._check_chrome_consistency(result, pages)
    assert result.findings, "the page with no menu at all is still reported"
    finding = result.findings[0]
    assert finding["affected_pages"] == [ORIGIN + "/landing/offer"]
    assert "/KIDS" in finding["evidence"]


# --------------------------------------------------------------------------
# 7. A response that is no page enters no count
# --------------------------------------------------------------------------

def test_non_html_responses_leave_every_page_count():
    files = [{"url": ORIGIN + "/img/{}.png".format(n), "final_url": ORIGIN + "/img/x.png",
              "status": 200, "skipped": "non-HTML content type"} for n in range(6)]
    snapshot = _snapshot(files)
    assert snapshot["crawl"]["pages_crawled"] == 10
    finding = _finding("missing-mobile-viewport", "mobile-viewport", [ABOUT, HOME],
                       "engagement-audit", checked=())
    report = _report([finding], snapshot=snapshot)
    assert report["crawl"]["pages_crawled"] == 4
    markdown = compose.render_markdown(report)
    assert "4 pages crawled" in markdown and "10 pages crawled" not in markdown
    assert "of the 10" not in markdown
    assert "6 more addresses answered with a file rather than a page" in markdown


# --------------------------------------------------------------------------
# A storefront delivered as a JavaScript shell: the shell leads
# --------------------------------------------------------------------------

def test_start_here_holds_no_place_for_a_cause_the_verdict_never_named():
    shell = _finding("homepage-is-javascript-shell", "js-shell", [HOME],
                     "render-readability-audit", severity="critical", mechanism="C",
                     title="The homepage is delivered as an empty JavaScript shell",
                     checked=())
    profiles = _finding("no-off-site-profiles", "weak-corroboration", [],
                        "freshness-corroboration-audit", severity="high", mechanism="B",
                        title="No off-site profile is linked")
    definition = _finding("no-entity-definition", "no-entity-definition", [],
                          "fact-extractability-audit", title="No page says what the brand is")
    report = _report([shell, profiles, definition])
    shell_id = _by_hint(report, "homepage-is-javascript-shell")["id"]
    assert shell_id in report["summary"]["verdict"]
    assert "establish who the brand is" not in report["summary"]["verdict"]
    assert report["leads_the_verdict"] == [shell_id]
    for hint in ("no-off-site-profiles", "no-entity-definition"):
        finding = _by_hint(report, hint)
        assert finding["id"] not in report["start_here"], hint
        assert finding.get("partly_follows_from", {}).get("id") == shell_id, hint
    markdown = compose.render_markdown(report)
    assert "because the verdict above names it as the cause of the rest" not in markdown


# --------------------------------------------------------------------------
# Site furniture is not a quotation
# --------------------------------------------------------------------------

def test_a_banner_or_a_menu_is_not_offered_as_a_quotation():
    for furniture in (
            "Contact Us | Northwind AU WEEKEND BONUS: EXTRA $70 OFF* | CODE: SPRING70 "
            "Interior Styling Service Our Showrooms Reviews Refer a Friend",
            "Our Story - Vine Cellar Skip to content YOUR WEEK JUST GOT 20% BETTER!",
            "Home Sofas Tables Chairs Beds Storage Outdoor Accessories Sale Contact"):
        assert compose.reads_as_page_furniture(furniture), furniture
    for prose in ("The cellar ships wine from twelve family vineyards across Europe.",
                  "Wir liefern Möbel aus Holz in ganz Deutschland und Österreich.",
                  "สินค้าทุกชิ้นผลิตในประเทศไทย"):
        assert not compose.reads_as_page_furniture(prose), prose
    page = _page(ORIGIN + "/contact-us", "Contact Us | Northwind AU WEEKEND BONUS: EXTRA "
                 "$70 OFF* | CODE: SPRING70 Interior Styling Service Our Showrooms Reviews",
                 "contact")
    table = compose.simulate_citations({"pages": [page]}, "Northwind")
    assert all("WEEKEND BONUS" not in (row.get("likely_citation") or "") for row in table)


# --------------------------------------------------------------------------
# A review page whose reviews a widget loads
# --------------------------------------------------------------------------

def _drift_page(path, title, body, scripts=0):
    url = ORIGIN + path
    return {"url": url, "final_url": url, "status": 200, "page_type": "content",
            "title": title + " | Clay Pots", "body_text": body, "body_text_len": len(body),
            "headings": {"h1": [], "h2": [], "h3": []},
            "scripts": {"external_count": scripts}}


def test_a_title_promising_reviews_a_script_loads_is_not_called_undelivered():
    words = "Our potters shape every bowl by hand in a small workshop near the harbour. " * 8
    pages = [_drift_page("/a", "Hand thrown bowls", "Hand thrown bowls. " + words),
             _drift_page("/b", "Glazed serving plates", "Glazed serving plates. " + words),
             _drift_page("/c", "Workshop classes weekly", "Workshop classes weekly. " + words),
             _drift_page("/what-customers-say", "Customer Reviews and Testimonials",
                         words, scripts=6)]
    result = SkillResult("engagement-audit")
    ENGAGE._check_title_body_drift(result, pages)
    assert not result.findings, [f["evidence"] for f in result.findings]
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "title-body-alignment")
    assert "review widget" in reason


# --------------------------------------------------------------------------
# A listing page that paginates in its head
# --------------------------------------------------------------------------

def test_a_listing_page_with_a_next_link_in_its_head_is_not_told_to_add_one():
    listing = {"url": ORIGIN + "/collections/bowls", "page_type": "category",
               "body_text": "Bowls. Load more", "links": {"internal": [], "internal_count": 0},
               "rel_next": ORIGIN + "/collections/bowls?page=2"}
    result = SkillResult("render-readability-audit")
    RENDER._check_pagination(result, {"pages": [listing]}, [listing])
    assert not result.findings


# --------------------------------------------------------------------------
# Two sentences about the crawl that were not true of it
# --------------------------------------------------------------------------

def test_an_address_that_never_answered_is_not_said_to_have_answered():
    clause = compose.why_little_was_read(
        {"pages": [{"url": HOME, "final_url": HOME, "status": None}]})
    assert "HTTP None" not in clause
    assert "did not answer" in clause


def test_a_sitemap_address_that_answered_with_a_page_is_not_a_sitemap_of_zero():
    snapshot = {"sitemaps": [{"url": ORIGIN + "/sitemap.xml", "status": 200, "urls": []}],
                "pages": [], "crawl": {}}
    result = SkillResult("engagement-audit")
    ENGAGE._check_orphans(result, snapshot, [])
    reason = next(n["reason"] for n in result.not_applicable if n["check"] == "orphan-pages")
    assert "lists 0" not in reason
    assert "no sitemap listing any page" in reason
