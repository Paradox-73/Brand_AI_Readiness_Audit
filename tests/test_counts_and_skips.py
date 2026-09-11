# -*- coding: utf-8 -*-
"""Numbers a report prints, and the reasons it gives for not printing one.

Five defects, each of which made a report say something measurably false about
a site that a reader could have checked in a browser:

  83 enquiry forms          one newsletter box in the site-wide footer, counted
                            once for each of the 56 pages the footer sits on,
                            and a single email field read as a form a visitor
                            could ask a question through

  "each of the 56 pages     one of those 56 carries 59 characters of body text.
  carries at least 300      The guard that drops a page whose extraction looks
  characters"               incomplete cannot tell "we read this badly" from
                            "this page really is empty", because both produce
                            the same ratio

  one site, two hostnames   identical pages on `www` and the bare domain,
                            reported as a duplicate-`<title>` template defect
                            while the host split itself went unreported

  "--no-network"            three checks told a reader they had passed a flag
                            they had not passed. The site asked for ten seconds
                            between requests, the crawl obeyed, and the run had
                            no clock left by the time the checks started

  "the homepage was not     the homepage was crawled, answered 200, and was set
  crawled"                  aside because no browser ran on a page that arrives
                            as an empty framework mount point

Every host in this file is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGAGEMENT = _module("engagement-audit", "engagement_for_count_tests")
RENDER = _module("render-readability-audit", "render_for_count_tests")
ACCESS = _module("crawl-access-audit", "access_for_count_tests")

SITE = "https://an-invented-shop.test"
CHROME = ["/", "/about", "/products", "/contact", "/terms"]


def _reason(result, check):
    return next(n["reason"] for n in result.not_applicable if n["check"] == check)


def _causes(result):
    return {f["root_cause"] for f in result.findings}


# --------------------------------------------------------------------------
# Defect 1: one form in the site-wide chrome is one form
# --------------------------------------------------------------------------

def _footer_newsletter():
    """The box the retailer actually has: one email field, no label matched."""
    return {"action": "/newsletter/subscribe", "method": "post",
            "field_count": 1, "required_count": 1, "query_field": "email",
            "is_search": False, "is_newsletter": False, "submits_off_site": False}


def _enquiry_form(action="/enquiry", required=3, fields=4):
    return {"action": action, "method": "post",
            "field_count": fields, "required_count": required, "query_field": "name",
            "is_search": False, "is_newsletter": False, "submits_off_site": False}


def _pages_with(form, count=56, chrome=None):
    return [{"url": "{}/page-{}".format(SITE, i), "page_type": "other",
             "forms": [dict(form)],
             "chrome_signature": {"nav_paths": list(chrome or CHROME)}}
            for i in range(count)]


def test_a_form_in_the_site_wide_chrome_is_one_form_not_one_per_page():
    """"none of the 83 enquiry forms found requires more than 8 fields", on a
    site with one form. Every sentence built on that number described a site
    that does not exist."""
    forms = ENGAGEMENT.distinct_forms(_pages_with(_enquiry_form()))
    assert len(forms) == 1
    _, carriers = forms[0]
    assert len(carriers) == 56


def test_a_one_field_email_box_is_not_a_form_anyone_can_enquire_through():
    """The newsletter word-match saw nothing around this box and recorded
    `is_newsletter: false`. One field is still a subscription: there is no
    question a visitor can ask through a single email input."""
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_forms(result, _pages_with(_footer_newsletter()))
    reason = _reason(result, "form-friction")
    assert "no enquiry form was found" in reason
    assert "83" not in reason
    assert "subscription or lookup box" in reason


def test_two_self_posting_forms_on_two_pages_stay_two_forms():
    """The fold is not "same shape, same form". A form with no `action` posts
    back to its own page, so the same shape on an enquiry page and on a quote
    page is two forms with two owners - and they only fold where the pages
    carrying them say the form is site furniture."""
    quote = {"url": SITE + "/quote", "page_type": "service",
             "forms": [{"action": "", "method": "post", "field_count": 4,
                        "required_count": 12, "query_field": "name",
                        "is_search": False, "is_newsletter": False}],
             "chrome_signature": {"nav_paths": list(CHROME)}}
    enquiry = dict(quote, url=SITE + "/enquiry")
    enquiry["forms"] = [dict(quote["forms"][0])]
    others = [{"url": "{}/article-{}".format(SITE, i), "page_type": "article", "forms": [],
               "chrome_signature": {"nav_paths": list(CHROME)}} for i in range(6)]
    forms = ENGAGEMENT.distinct_forms([quote, enquiry] + others)
    assert len(forms) == 2


def test_a_long_form_in_the_site_wide_template_is_reported_once():
    """The counting defect from the other side: one twelve-field form the
    template repeats is one form to shorten, not fifty-six."""
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_forms(result, _pages_with(_enquiry_form(required=12)))
    finding = next(f for f in result.findings if f["root_cause"] == "form-friction")
    assert finding["title"].startswith("1 enquiry form asks"), finding["title"]
    assert len(finding["affected_pages"]) == 1
    assert "55 other page(s)" in finding["evidence"]


# --------------------------------------------------------------------------
# Defect 2: a page that is all chrome and no content
# --------------------------------------------------------------------------

CHROME_CHARS = 2400


def _full_page(index):
    """An ordinary page: the site's furniture plus 1,800 characters of prose."""
    return {"url": "{}/guide-{}".format(SITE, index), "page_type": "article",
            "status": 200, "body_text_len": 1800, "text_len": 1800 + CHROME_CHARS,
            "chrome_signature": {"nav_paths": list(CHROME)}}


def _empty_page(url="/about-us"):
    """A `<main>` holding one `<h1>` and nothing else, inside the same chrome."""
    return {"url": SITE + url, "page_type": "about", "status": 200,
            "body_text_len": 59, "text_len": 59 + CHROME_CHARS,
            "chrome_signature": {"nav_paths": list(CHROME)}}


def _badly_extracted_page(url="/live"):
    """The case the guard exists for: 1,660 visible characters, 89 kept.

    The article sits in a nested element none of the content selectors reach,
    so the extracted body is a fraction of a page that is complete and
    server-rendered.
    """
    return {"url": SITE + url, "page_type": "article", "status": 200,
            "body_text_len": 89, "text_len": 89 + CHROME_CHARS + 4000,
            "chrome_signature": {"nav_paths": list(CHROME)}}


def test_a_page_that_is_all_chrome_is_reported_as_empty_not_hidden_by_the_guard():
    """`extraction_looks_incomplete` drops any page whose body text is under a
    quarter of the whole document's. On a page that is genuinely all furniture
    that ratio is always tiny, so the guard written to stop false positives
    guaranteed a false negative on exactly the pages this check exists to
    find."""
    pages = [_full_page(i) for i in range(10)] + [_empty_page()]
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, pages, [])
    finding = next(f for f in result.findings if f["root_cause"] == "thin-html")
    assert finding["affected_pages"] == [SITE + "/about-us"]
    assert "accounted for by the site's own repeated header" in finding["evidence"]


def test_a_page_the_extractor_read_badly_is_still_not_reported_as_thin():
    """The guard's original job survives the fix: this page carries 4,000
    characters more than the site's furniture accounts for, so the missing text
    is this audit's extraction rather than the page."""
    pages = [_full_page(i) for i in range(10)] + [_badly_extracted_page()]
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, pages, [])
    assert "thin-html" not in _causes(result)
    reason = _reason(result, "thin-html")
    assert SITE + "/live" in reason
    assert "this audit's extraction rather than the page" in reason


def test_the_passing_sentence_never_claims_a_page_it_set_aside():
    """"each of the 56 pages this check assessed carries at least 300
    characters" was printed on a run that had quietly dropped one of the 56.
    The count is now of the pages actually assessed, and the rest are named."""
    pages = [_full_page(i) for i in range(10)] + [_badly_extracted_page()]
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, pages, [])
    reason = _reason(result, "thin-html")
    assert "each of the 10 pages this check could assess" in reason
    assert "Set aside" in reason


def test_a_site_where_nothing_extracted_well_reports_the_page_as_undecided():
    """No page of any template extracted well enough to measure how much text
    the chrome holds, so neither answer is available. That belongs in the
    not-applicable list with its reason, not in a passed check."""
    pages = [_badly_extracted_page("/live-{}".format(i)) for i in range(3)]
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, pages, [])
    assert "thin-html" not in _causes(result)
    reason = _reason(result, "thin-html")
    assert "could not be decided" in reason
    assert result.signals["short_pages_this_audit_could_not_judge"]


def test_the_same_document_on_two_hostnames_is_counted_once():
    """The denominator of every sentence this check prints counts documents,
    and a page reachable on `www` and on the bare domain is one document."""
    bare = _full_page(1)
    www = dict(bare, url="https://www.an-invented-shop.test/guide-1",
               final_url="https://www.an-invented-shop.test/guide-1")
    bare["final_url"] = bare["url"]
    bare["title"] = www["title"] = "Guide"
    assert len(RENDER._one_page_per_document([bare, www])) == 1


# --------------------------------------------------------------------------
# Defect 4: one site on two hostnames, with nothing saying which is real
# --------------------------------------------------------------------------

def _host_pair(canonical_bare="", canonical_www=""):
    def page(url, canonical):
        return {"url": url, "final_url": url, "status": 200, "page_type": "home",
                "title": "Invented Project", "body_text_len": 900,
                "canonical": canonical, "links": {"internal": [], "external": []},
                "jsonld": [], "headers": {}}
    return [page("https://an-invented-project.test/", canonical_bare),
            page("https://www.an-invented-project.test/", canonical_www)]


def _snapshot(pages, origin="https://an-invented-project.test"):
    return {"origin": origin, "site": "an invented project", "pages": pages,
            "crawl": {"head_supported": True}}


def test_two_hostnames_with_no_canonical_are_the_finding_not_a_missing_check():
    """The report filed this check as not applicable - "no canonical URLs were
    declared, so there is no host split to assess" - on a site serving
    identical pages under two hostnames, and the duplication was then reported
    by another skill as a duplicate-`<title>` template defect. The absent
    canonical is what makes it a finding, not what makes the check
    inapplicable."""
    pages = _host_pair()
    result = SkillResult("crawl-access-audit")
    ACCESS._check_transport_and_hosts(result, _snapshot(pages), pages)
    finding = next(f for f in result.findings if f["root_cause"] == "host-inconsistency")
    assert "2 hostnames" in finding["title"]
    assert "an-invented-project.test" in finding["evidence"]
    assert len(finding["affected_pages"]) == 2


def test_a_canonical_that_names_one_hostname_settles_it():
    """The site has said which address is real, which is exactly what the
    finding above would ask it to do."""
    real = "https://an-invented-project.test/"
    pages = _host_pair(canonical_bare=real, canonical_www=real)
    result = SkillResult("crawl-access-audit")
    ACCESS._check_transport_and_hosts(result, _snapshot(pages), pages)
    assert "host-inconsistency" not in _causes(result)
    assert "single hostname" in _reason(result, "canonical-host-consistency")


def test_two_hostnames_serving_different_pages_are_two_pages():
    """A national edition on its own hostname is not a duplicate of the main
    site, and folding it would hide a page rather than count one."""
    pages = _host_pair()
    pages[1]["title"] = "Invented Project - Deutschland"
    pages[1]["body_text_len"] = 1400
    result = SkillResult("crawl-access-audit")
    ACCESS._check_transport_and_hosts(result, _snapshot(pages), pages)
    assert "host-inconsistency" not in _causes(result)


# --------------------------------------------------------------------------
# Defect 5: a flag the caller never passed
# --------------------------------------------------------------------------

SLOW_ROBOTS = {"status": 200,
               "groups": [{"agents": ["*"], "allow": [], "disallow": [],
                           "crawl_delay": 10}]}


def _spent_snapshot():
    return {"origin": SITE, "site": "an invented shop", "pages": [],
            "robots": SLOW_ROBOTS,
            "crawl": {"stopped_by": "wall clock", "elapsed_s": 241.0,
                      "budget_exhausted": True}}


def test_an_exhausted_clock_is_not_reported_as_a_flag_the_caller_passed():
    """Three checks in one report said their work was skipped because
    `--no-network` was passed. Nobody passed it. robots.txt asked for ten
    seconds between requests, the crawl obeyed, and the orchestrator ran the
    sub-skills with the network off so the run would not overrun."""
    for module in (ENGAGEMENT, ACCESS):
        reason = module.no_network_reason(
            _spent_snapshot(), time_budget=4.0,
            flag_reason="extra network requests were disabled for this run",
            exhausted_reason="nothing was probed")
        assert "spent clock" in reason, module.__name__
        assert "may not have been passed" in reason
        assert "10s between requests" in reason
        assert "disabled for this run" not in reason


def test_the_flag_is_still_named_when_the_run_had_time_and_did_not_use_it():
    """The correction must not swing the other way: a caller who asked for no
    network, on a run with its whole budget left, asked for exactly that."""
    for module in (ENGAGEMENT, ACCESS):
        reason = module.no_network_reason(
            _spent_snapshot(), time_budget=180.0,
            flag_reason="extra network requests were disabled for this run",
            exhausted_reason="nothing was probed")
        assert reason.endswith("(--no-network)"), module.__name__
        assert "spent clock" not in reason


def test_a_skill_run_on_its_own_from_the_command_line_names_the_flag():
    """No ceiling was imposed, so the flag is the only explanation left."""
    reason = ACCESS.no_network_reason(
        _spent_snapshot(), time_budget=None,
        flag_reason="extra network requests were disabled for this run",
        exhausted_reason="nothing was probed")
    assert reason.endswith("(--no-network)")


# --------------------------------------------------------------------------
# A homepage that was crawled, and set aside
# --------------------------------------------------------------------------

def test_a_shell_homepage_is_not_described_as_one_that_was_never_crawled():
    """"the homepage was not crawled" was printed about a homepage that was
    crawled and answered 200. It was set aside because it arrives as an empty
    framework mount point and no browser ran, which is a different fact and the
    only one of the two that is true."""
    stub = {"url": SITE + "/", "page_type": "home", "status": 200, "text_len": 13,
            "spa_shell": {"root_selector": "#app"}}
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_homepage_orientation(result, None, True, stub_home=stub)
    reason = _reason(result, "homepage-orientation")
    assert "was not crawled" not in reason
    assert "framework mount point" in reason
    assert "no-orientation" not in _causes(result)
