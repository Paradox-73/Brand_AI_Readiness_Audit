# -*- coding: utf-8 -*-
"""Rows, links and recommendations, as seven awkward sites and five shops read them.

The shapes these tests hold, each measured on a real site:

* "An assistant asked about this category has no fact about this brand it can
  safely repeat" printed on five sites, one of which quoted its central bank's
  own definition of itself in the table of what an assistant would lift.
* One 404 billed as a broken link and again as a failed fetch; one canonical
  tag billed as an off-domain canonical and again as a split of hosts; eight
  query-string twins billed twice; a page pair behind a frame billed five
  times, every one of them counted in the severity table.
* "Affected pages: https://<host>/fil/<other-host>/<user>" - a link written
  without `https://` on `/fil/`, and `/fil/` never named.
* A snippet declaring `"@type": "<@type - not found on the site, fill this
  in>"` directly under a sentence saying which type it declares.
* `/docs.html` and `/news.html` counted as deep pages missing a trail.
* A museum's llms.txt starter naming its member sign-up form, a page that
  redirects by script, as the key page.
* Recommendations reading the same on every site.
* A 748-line report printing the same paragraphs under finding after finding.
* A help centre whose sections arrive in a hydration payload, told to write
  two or three sentences of copy.
* Product pages printing "Translation missing: en.general.social.share_on_..."
  while the template check said no page prints template source.
* A one-page essay in several languages told its homepage has no call to action.

Every host here is invented and every brand is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import PERSONAL_OR_ACADEMIC, SiteKind, SkillResult  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_rows_and_links", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_rows_and_links")
ENGAGE = _module("engagement-audit", "engagement_for_rows_and_links")
compose = _compose()

ORIGIN = "https://www.harbourline-ferries.test"
HOME = ORIGIN + "/"
ABOUT = ORIGIN + "/about/"
TIMETABLES = ORIGIN + "/timetables/"
DEAD = ORIGIN + "/timetables/ferrymaps.test/north-harbour"
TOUR_A = ORIGIN + "/crossings/2022/virtual/"
TOUR_B = ORIGIN + "/crossings/2023/virtual/"
DEFINITION = ("Harbourline Ferries is a passenger ferry service between three island "
              "harbours")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _finding(id_hint, root_cause, pages, skill, severity="medium", mechanism="G",
             steps=None, checked=("the page text", "the page markup"), evidence=None):
    return {
        "id_hint": id_hint, "title": id_hint.replace("-", " "), "severity": severity,
        "confidence": "high",
        "evidence": evidence or "Measured on {} page(s).".format(len(pages)),
        "mechanism": mechanism, "root_cause": root_cause,
        "affected_pages": list(pages), "affected_page_count": len(pages),
        "checked": list(checked),
        "suggested_action": {"summary": "Do the thing.", "effort": "low",
                             "owner": "developer",
                             "how_to_fix": list(steps or ["Do the thing on the page."]),
                             "rationale": "Because it matters."},
        "_skill": skill,
    }


def _results(findings, signals=None):
    by_skill = {}
    for finding in findings:
        finding = dict(finding)
        by_skill.setdefault(finding.pop("_skill"), []).append(finding)
    for skill in signals or {}:
        by_skill.setdefault(skill, [])
    return [{"skill": skill, "findings": by_skill[skill],
             "signals": (signals or {}).get(skill, {}), "checks_run": [],
             "not_applicable": [], "fired_checks": [], "extra_requests_made": 0}
            for skill in compose.SKILL_ORDER if skill in by_skill]


def _page(url, body, page_type="content", **extra):
    page = {"url": url, "final_url": url, "status": 200, "title": "Harbourline " + url,
            "page_type": page_type, "body_text": body, "body_text_len": len(body),
            "paragraphs": [body], "text": body, "text_len": len(body)}
    page.update(extra)
    return page


def _snapshot(pages=None):
    body = ("Harbourline Ferries sails between the north, east and south harbours every day "
            "of the year, and every sailing is listed on the timetable page.")
    pages = pages or [_page(HOME, body, "home"), _page(ABOUT, body + " It began in 1962.",
                                                       "about"),
                      _page(TIMETABLES, body + " Sailings leave on the hour.")]
    return {"site": "www.harbourline-ferries.test", "origin": ORIGIN,
            "brand": {"name": "Harbourline Ferries", "host": "www.harbourline-ferries.test"},
            "pages": pages, "sitemaps": [],
            "crawl": {"pages_crawled": len(pages), "pages_ok": len(pages),
                      "pages_read": len(pages), "render_mode": "static",
                      "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []}}


def _report(findings, signals=None, snapshot=None):
    return compose.compose(snapshot or _snapshot(), _results(findings, signals),
                           audited_at="2026-01-01T00:00:00Z")


def _by_hint(report, hint):
    return next(f for f in report["findings"] if f["stable_id"] == hint)


# --------------------------------------------------------------------------
# 1. The verdict says what an assistant can repeat
# --------------------------------------------------------------------------

COUNTS = {"critical": 0, "high": 0, "medium": 2, "low": 0, "info": 0}
IDENTITY = [{"root_cause": "no-org-schema", "severity": "medium",
             "id_hint": "no-organization-schema"},
            {"root_cause": "weak-corroboration", "severity": "medium",
             "id_hint": "thin-off-site-profile-footprint"}]


def test_the_verdict_quotes_the_definition_it_found_rather_than_denying_one_exists():
    signals = {"profile_breadth": 0, "entity_definition_found": True,
               "entity_definition": DEFINITION}
    verdict = compose._verdict(COUNTS, IDENTITY, {}, signals, "Harbourline Ferries")
    assert "safely repeat" not in verdict, verdict
    assert DEFINITION in verdict
    # What is missing is still said.
    assert "no page carries Organization or LocalBusiness markup" in verdict


def test_the_verdict_quotes_a_sentence_from_the_citation_table():
    citations = [{"url": HOME, "page_type": "home", "why": "states a number",
                  "likely_citation": "The first Harbourline Ferries sailing leaves the north "
                                     "harbour at 06:40 every day of the year."}]
    verdict = compose._verdict(COUNTS, IDENTITY, {}, {"profile_breadth": 0},
                               "Harbourline Ferries",
                               quotable=compose.what_an_assistant_can_repeat(
                                   {}, citations, "Harbourline Ferries"))
    assert "safely repeat" not in verdict, verdict
    assert "06:40" in verdict and HOME in verdict


def test_a_homepage_offer_is_not_the_fact_the_verdict_quotes():
    """A furniture shop's verdict quoted "New arrivals are here - plus $100
    off outdoor collections", then "enjoy an extra 5% off add-ons", as what an
    assistant can repeat about the brand. Neither names it."""
    citations = [{"url": HOME, "page_type": "home", "why": "states a number",
                  "likely_citation": "Complete your space and enjoy an extra 5% off add-ons, "
                                     "stacked onto existing savings."}]
    assert compose.what_an_assistant_can_repeat({}, citations, "Harbourline Ferries") is None


def test_the_verdict_keeps_the_strong_sentence_where_nothing_is_quotable():
    verdict = compose._verdict(COUNTS, IDENTITY, {}, {"profile_breadth": 0},
                               "Harbourline Ferries",
                               quotable=compose.what_an_assistant_can_repeat({}, []))
    assert "nothing it can safely repeat about who the brand is" in verdict


# --------------------------------------------------------------------------
# 2. One defect, one row
# --------------------------------------------------------------------------

def test_one_dead_address_is_billed_once_and_both_root_causes_stay_visible():
    link = _finding("broken-internal-links", "broken-links", [TIMETABLES], "engagement-audit",
                    severity="high")
    fetch = _finding("high-non-200-rate", "non-200", [DEAD], "crawl-access-audit",
                     mechanism="A")
    report = _report([link, fetch], signals={
        "engagement-audit": {"broken_link_targets": {DEAD: [TIMETABLES]}}})
    causes = {f["root_cause"] for f in report["findings"]}
    assert {"broken-links", "non-200"} <= causes
    kept, again = _by_hint(report, "broken-internal-links"), _by_hint(report, "high-non-200-rate")
    assert again.get("follows_from", {}).get("id") == kept["id"]
    assert again["id"] not in report["start_here"]
    markdown = compose.render_markdown(report)
    assert "Follows from {}, not counted again ({})".format(kept["id"], again["id"]) in markdown
    # The row a defect is counted on is the kept finding's, once.
    assert "| High | 1 |" in markdown and "| Medium | 0 |" in markdown


def test_a_fetch_failure_beside_a_different_kind_of_dead_link_is_left_alone():
    """A sitemap still listing a 404 is not the same defect as a high failure
    rate, even where both name one address: nothing is marked or merged."""
    listed = _finding("sitemap-lists-dead-urls", "broken-links", [DEAD], "crawl-access-audit",
                      mechanism="A")
    fetch = _finding("high-non-200-rate", "non-200", [DEAD], "crawl-access-audit",
                     mechanism="A")
    report = _report([listed, fetch])
    assert not any(f.get("follows_from") for f in report["findings"])
    assert len(report["findings"]) == 2


def test_one_canonical_tag_is_billed_once():
    tagged = ORIGIN + "/partners/"
    pages = [_page(HOME, "Harbourline Ferries sails between three harbours every day.", "home",
                   canonical=HOME),
             _page(tagged, "Our partner operators and the routes they sail.",
                   canonical="https://partners.harbourline-other.test/"),
             _page(ABOUT, "Harbourline Ferries began in 1962 with one boat.", "about",
                   canonical=ABOUT)]
    off_domain = _finding("canonical-points-off-domain", "canonical-broken", [tagged],
                          "crawl-access-audit", severity="high", mechanism="A")
    split = _finding("mixed-canonical-hosts", "host-inconsistency", [], "crawl-access-audit",
                     mechanism="A")
    report = _report([off_domain, split], snapshot=_snapshot(pages))
    kept, again = (_by_hint(report, "canonical-points-off-domain"),
                   _by_hint(report, "mixed-canonical-hosts"))
    assert again.get("follows_from", {}).get("id") == kept["id"]
    assert "host-inconsistency" in {f["root_cause"] for f in report["findings"]}


def test_query_string_twins_reported_by_two_checks_are_one_finding():
    twins = [ORIGIN + "/contact/enquiry.php?route=r{}".format(n) for n in range(8)]
    by_text = _finding("the-same-page-at-several-addresses-with-no-canonical", "meta-hygiene",
                       twins, "structured-data-audit", mechanism="B")
    by_title = _finding("filter-parameters-produce-pages-with-no-canonical", "meta-hygiene",
                        twins[:7], "structured-data-audit", mechanism="B",
                        steps=["Add a canonical naming {}/contact/enquiry.php to the "
                               "template.".format(ORIGIN)])
    report = _report([by_text, by_title])
    twins_found = [f for f in report["findings"] if f["root_cause"] == "meta-hygiene"]
    assert len(twins_found) == 1, [f["stable_id"] for f in twins_found]
    assert twins_found[0]["affected_page_count"] == 8
    assert any("contact/enquiry.php to the template" in step
               for step in twins_found[0]["suggested_action"]["how_to_fix"])
    assert any(item["id_hint"] == "filter-parameters-produce-pages-with-no-canonical"
               for item in report["merged_duplicates"])


def test_a_page_read_again_from_other_angles_is_not_counted_again_in_the_table():
    frame = _finding("main-content-in-iframe", "iframe-content", [TOUR_A, TOUR_B],
                     "render-readability-audit", mechanism="C")
    crumbs = _finding("deep-pages-have-no-breadcrumb", "no-breadcrumbs", [TOUR_A, TOUR_B],
                      "engagement-audit")
    onward = _finding("pages-with-no-next-step", "dead-end", [TOUR_A, TOUR_B],
                      "engagement-audit")
    report = _report([frame, crumbs, onward])
    cause = _by_hint(report, "main-content-in-iframe")
    markdown = compose.render_markdown(report)
    assert "Follows from {}, not counted again".format(cause["id"]) in markdown
    rows, follows = compose.severity_table(report)
    assert sum(confident + worth for label, confident, worth in rows) == 1, rows
    assert [ids for _, ids, _, _ in follows] == [
        [_by_hint(report, "deep-pages-have-no-breadcrumb")["id"],
         _by_hint(report, "pages-with-no-next-step")["id"]]]
    assert report["summary"]["follow_from_another_finding"] == 2
    # Still published, whole: nothing is dropped from the document.
    assert "deep pages have no breadcrumb" in markdown


# --------------------------------------------------------------------------
# 3. A broken link names the page carrying it
# --------------------------------------------------------------------------

def test_a_broken_link_names_its_source_and_a_missing_scheme_is_named_as_the_fault():
    source = _page(TIMETABLES, "Every sailing, and the harbour maps.",
                   links={"internal": [{"url": DEAD, "text": "North harbour map"}]})
    snapshot = _snapshot([source, dict(_page(DEAD, ""), status=404)])
    result = SkillResult("engagement-audit")
    ENGAGE._check_broken_links(result, snapshot, [source], None, "")
    finding = next(f for f in result.findings if f["id_hint"] == "broken-internal-links")
    assert finding["affected_pages"] == [TIMETABLES]
    assert TIMETABLES in finding["evidence"] and DEAD in finding["evidence"]
    assert "`https://ferrymaps.test/north-harbour`" in finding["suggested_action"]["how_to_fix"][0]
    assert result.signals["broken_link_targets"] == {DEAD: [TIMETABLES]}
    assert ENGAGE._written_without_a_scheme(ORIGIN + "/news.html") == ""
    assert ENGAGE._written_without_a_scheme(ORIGIN + "/links/www.ferrymaps") == "www.ferrymaps"


# --------------------------------------------------------------------------
# 4. A schema.org type is vocabulary, not a guess
# --------------------------------------------------------------------------

def test_a_schema_type_the_audit_chose_is_not_replaced_with_a_placeholder():
    snippet = ('{\n  "@context": "https://schema.org",\n  "@type": "SoftwareApplication",\n'
               '  "name": "Quillpad",\n  "author": {\n    "@type": "CollegeOrUniversity",\n'
               '    "name": "Quillpad Labs"\n  }\n}')
    out, invented = compose.hold_back_invented_values(snippet, "quillpad is an editor")
    assert '"@type": "SoftwareApplication"' in out
    assert '"@type": "CollegeOrUniversity"' in out
    # A value the site never showed is still held back.
    assert invented == ["name='Quillpad Labs'"], invented


# --------------------------------------------------------------------------
# 5. A top-level page is not a deep page
# --------------------------------------------------------------------------

def _crumbless(url, page_type):
    return _page(url, "A page of documentation about the timetable.", page_type, depth=1,
                 breadcrumb={}, links={"internal": [], "nav": [], "footer": []})


def test_pages_one_segment_below_the_root_are_not_judged_for_a_trail():
    result = SkillResult("engagement-audit")
    ENGAGE._check_breadcrumbs(result, [_crumbless(ORIGIN + "/docs.html", "documentation"),
                                       _crumbless(ORIGIN + "/news.html", "article"),
                                       _crumbless(ORIGIN + "/guide/", "documentation")])
    assert "deep-pages-have-no-breadcrumb" not in {f["id_hint"] for f in result.findings}


def test_pages_two_segments_down_still_are():
    result = SkillResult("engagement-audit")
    ENGAGE._check_breadcrumbs(result, [_crumbless(ORIGIN + "/guide/a.html", "documentation"),
                                       _crumbless(ORIGIN + "/guide/b.html", "documentation"),
                                       _crumbless(ORIGIN + "/news/c.html", "article")])
    assert "deep-pages-have-no-breadcrumb" in {f["id_hint"] for f in result.findings}


# --------------------------------------------------------------------------
# 6. Key pages are pages with content
# --------------------------------------------------------------------------

def test_the_llms_starter_names_pages_that_answered_with_content():
    redirecting = ORIGIN + "/membership.do"
    signup = ORIGIN + "/membership.do?step=signup"
    body = "Harbourline Ferries carries foot passengers and cyclists. " * 12
    pages = [_page(HOME, body, "home"),
             _page(redirecting, body, "pricing"),
             _page(signup, body, "pricing",
                   forms=[{"field_count": 14, "method": "post", "is_search": False,
                           "is_newsletter": False}]),
             _page(ABOUT, body, "about")]
    signals = {"short_pages_that_redirect_by_script": [redirecting]}
    starter = compose._llms_txt_template(_snapshot(pages), "Harbourline Ferries", signals)
    assert ABOUT in starter
    assert redirecting + ")" not in starter and signup not in starter


# --------------------------------------------------------------------------
# 7. Each recommendation names what it was measured on
# --------------------------------------------------------------------------

def test_recommendations_name_the_measurement_they_rest_on():
    body = "Harbourline Ferries sails every day between three harbours. " * 8
    signup_page = ORIGIN + "/news/"
    pages = [_page(HOME, body, "home"), _page(ABOUT, body, "about"),
             _page(signup_page, body, newsletter_signup=True)]
    signals = {"llms_txt_present": False, "newsletter_signup": True,
               "entity_definition_found": True, "entity_definition": DEFINITION}
    recs = {r["id"]: r for r in compose.build_recommendations(_snapshot(pages), signals, [])}
    assert signup_page in recs["R-EMAIL-TEXT-FIRST"]["summary"]
    assert DEFINITION in recs["R-BOILERPLATE"]["summary"]
    assert ORIGIN + "/llms.txt" in recs["R-LLMS-TXT"]["summary"]


# --------------------------------------------------------------------------
# 8. What repeats is printed once
# --------------------------------------------------------------------------

PLATFORM = ("This audit could not tell what this site is built with, so it cannot name the "
            "file. Whatever produces the pages, one template produces the top of every one "
            "of them: the part that holds the `<title>` tag and ends with `</head>`.")
LONG_SOURCES = ("the h1 elements on every crawled page, read from the delivered markup as "
                "well as from the visible document, so a heading hidden with CSS still counts",
                "each page's own title, compared word by word against the heading it leads "
                "with, which is a second reading of the same subject",
                "the menu and footer links on the same pages, which say which section each "
                "page sits in")


def test_a_paragraph_repeated_inside_different_steps_is_printed_once():
    findings = [
        _finding("hint-{}".format(n), "cause-{}".format(n), [ABOUT], "engagement-audit",
                 steps=["Put the {} tag in the shared template. {}".format(tag, PLATFORM)],
                 checked=LONG_SOURCES)
        for n, tag in enumerate(("viewport", "canonical", "hreflang"))]
    markdown = compose.render_markdown(_report(findings))
    assert markdown.count("Whatever produces the pages, one template produces") == 1
    assert markdown.count("Why this class of problem matters") == 1
    for line in markdown.splitlines():
        if line.startswith("**Where we looked.**"):
            assert len(line) < 260, line
            assert "report.json" in line


# --------------------------------------------------------------------------
# A1. Copy that arrives in a hydration payload is not missing copy
# --------------------------------------------------------------------------

def test_a_short_page_carrying_a_hydration_payload_is_not_told_to_write_copy():
    prose = "Sailings, fares and the harbour maps for every route we run. " * 40
    help_centre = _page(ORIGIN + "/help-centre", "Help centre. Search our help articles.",
                        spa_shell={"state_blobs": ["self.__next_f"],
                                   "state_blob_bytes": 600000, "root_selector": None,
                                   "root_text_len": None, "noscript_demands_js": False,
                                   "visible_text_len": 40})
    pages = [_page(ORIGIN + "/fares", prose), _page(ORIGIN + "/routes", prose), help_centre]
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, pages, [], snapshot=_snapshot(pages))
    assert "pages-too-thin-to-quote" not in {f["id_hint"] for f in result.findings}
    reason = next(n["reason"] for n in result.not_applicable if n["check"] == "thin-html")
    assert "`self.__next_f`" in reason and "server" in reason


# --------------------------------------------------------------------------
# A2. A translation key printed as text is template source
# --------------------------------------------------------------------------

def test_a_missing_translation_key_is_an_unrendered_template_artefact():
    shelf = ("Write a review Share it Translation missing: en.general.social.share_on_facebook "
             "Regular price 2,299.00 and free delivery to every harbour we sail to. " * 3)
    cart = "Your basket [missing \"en.cart.general.title\" translation] holds two tickets. " * 3
    pages = [_page(ORIGIN + "/tickets/day-return", shelf, "product"),
             _page(ORIGIN + "/basket", cart)]
    found = RENDER._template_artefacts_by_page(pages)
    kinds = {kind for url, record in found.items() if url != "_declined"
             for kind, _, _ in record["artefacts"]}
    assert ORIGIN + "/tickets/day-return" in found and ORIGIN + "/basket" in found
    assert kinds == {"missing translation"}


# --------------------------------------------------------------------------
# One document is not asked for a call to action
# --------------------------------------------------------------------------

def _essay_home():
    return {"url": "https://ask-the-question.test/", "page_type": "home",
            "headings": {"h1": ["Ask the question you actually have"]},
            "cta": {"found": False, "button_count": 0}, "forms": [],
            "body_text": "Ask the question you actually have, and people can answer it.",
            "links": {"internal": [{"url": "https://ask-the-question.test/de/",
                                    "text": "Deutsch"}],
                      "external": [{"url": "https://source-host.test/essay", "text": "Source"}],
                      "nav": [], "footer": []}}


def test_a_single_document_site_is_not_asked_for_a_commercial_call_to_action():
    kind = SiteKind(PERSONAL_OR_ACADEMIC, "medium",
                    ["the crawl found one document in 4 language editions"])
    result = SkillResult("engagement-audit")
    ENGAGE._check_homepage_orientation(result, _essay_home(), True, kind=kind)
    assert not result.findings, [f["evidence"] for f in result.findings]
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "homepage-orientation")
    assert "onward" in reason


def test_a_business_homepage_with_no_call_to_action_is_still_reported():
    result = SkillResult("engagement-audit")
    ENGAGE._check_homepage_orientation(result, _essay_home(), True, kind=None)
    assert "homepage-does-not-orient-visitors" in {f["id_hint"] for f in result.findings}
