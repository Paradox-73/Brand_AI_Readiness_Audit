# -*- coding: utf-8 -*-
"""Five defects in crawl-access-audit, and their measurements.

  * "Remove `noindex` from pages you want found", aimed at WordPress tag
    archives, which carry `noindex, follow` - the default of both major SEO
    plugins and a deliberate choice on millions of sites. That is advice that
    makes the site worse: following it publishes a set of thin near-duplicate
    listings that then compete with the articles they list.

  * A page shipping `<meta name="robots" content="noindex,nofollow">` was named
    in a missing-meta-description finding and counted in a missing-`og:title`
    one, while the check whose whole job is to report the directive said "no
    crawled content page carries a noindex directive". The page was typed
    `other`, so one check skipped it and the others did not.

  * On a server answering every unknown path with its homepage,
    `sitemap-present`, `sitemap-parses` and `unknown-paths-return-200` were
    filed under "Checks that contributed to a finding above" in a report
    holding no such finding, and the reader learned nothing about the
    catch-all.

  * A `robots.txt` containing a JavaScript fragment - `Disallow:
    /g,mt=RegExp(` - found by hand in a live file and reported by nothing.

  * A three-locale site with `grep -c hreflang` returning 0, whose other
    locales this audit's crawl had visited.

Every host and every brand in this file is invented; the fixture server listens
on the loopback address.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from audit_common import Fetcher, SkillResult  # noqa: E402
from fixture_server import FixtureServer  # noqa: E402


def _skill_module(skill, name):
    scripts = os.path.abspath(
        os.path.join(os.path.dirname(SCRIPTS), "..", skill, "scripts"))
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCESS = _skill_module("crawl-access-audit", "access_for_noindex_tests")

SITE = "https://an-invented-workshop.test"


# --------------------------------------------------------------------------
# Which noindex is a defect
# --------------------------------------------------------------------------

def _page(path, page_type="article", robots="noindex, follow", **over):
    url = SITE + path
    record = {
        "url": url, "final_url": url, "status": 200, "page_type": page_type,
        "meta_robots": robots, "meta_robots_by_agent": {}, "x_robots_tag": "",
        "canonical": url, "title": "A page", "headings": {"h1": ["A page"]},
        "body_text": "Words on the page. " * 30, "body_text_len": 570,
        "text": "Words on the page. " * 30,
        "headers": {"content-type": "text/html"}, "redirect_chain": [], "depth": 1,
        "dates": {}, "links": {}, "elapsed_ms": 20,
    }
    record.update(over)
    return record


def _indexability(pages):
    """Run only the status-and-indexability half of the skill, offline."""
    snapshot = {"origin": SITE, "pages": pages}
    result = SkillResult("crawl-access-audit")
    ACCESS._check_status_and_indexability(
        result, snapshot, pages, [p for p in pages if not p.get("not_gradable")],
        fetcher=None)
    return result


def _noindex_finding(result):
    return next((f for f in result.findings
                 if f["id_hint"] == "noindex-on-content-pages"), None)


def _noindex_decline(result):
    return next((n["reason"] for n in result.not_applicable
                 if n["check"] == "noindex-on-content-pages"), "")


# The case that prompted this, in the four addresses it is published at.
DELIBERATE = [
    ("/tag/roasting/", "category", "noindex, follow"),
    ("/blog/page/2/", "category", "noindex, follow"),
    ("/author/an-invented-person/", "category", "noindex, follow"),
    ("/2024/06/", "category", "noindex, follow"),
    ("/collections/mugs?sort=price", "category", "noindex, follow"),
    ("/my-account/", "other", "noindex, nofollow"),
]


@pytest.mark.parametrize("path,page_type,robots", DELIBERATE)
def test_a_noindex_a_site_puts_there_on_purpose_is_not_a_defect(path, page_type, robots):
    """The original defect. Each of these is the standard arrangement, and the
    fix the report used to print - "Remove `noindex` where the page should be
    public" - publishes a near-duplicate or a per-visitor address."""
    home = _page("/", "home", robots="")
    result = _indexability([home, _page(path, page_type, robots)])
    assert _noindex_finding(result) is None, \
        "{} was reported as a defect".format(path)
    reason = _noindex_decline(result)
    assert "on purpose" in reason, reason
    assert path.split("?")[0] in reason or path in reason, reason


def test_the_report_says_plainly_that_it_looks_deliberate():
    """"Either stay quiet or say plainly that it looks deliberate." The decline
    is what a reader sees, so it has to name the address and the reason."""
    result = _indexability([_page("/", "home", robots=""),
                            _page("/tag/roasting/", "category", "noindex, follow")])
    reason = _noindex_decline(result)
    assert "/tag/roasting/" in reason
    assert "SEO plugins" in reason
    assert "no page this site publishes to be read carries a noindex" in reason


@pytest.mark.parametrize("path,page_type", [
    ("/about", "about"),
    ("/products/an-invented-mug", "product"),
    ("/services/restoration", "service"),
    ("/", "home"),
])
def test_a_noindex_on_a_page_the_site_wants_found_is_still_reported(path, page_type):
    """The other half. Narrowing the population may not cost the check the
    pages it exists for, and `noindex, follow` on an article is still a
    mistake - the pairing excuses a listing, not a document."""
    result = _indexability([_page(path, page_type, "noindex, follow")])
    finding = _noindex_finding(result)
    assert finding is not None, "{} went unreported".format(path)
    assert SITE + path in finding["affected_pages"]


def test_the_fix_steps_say_which_pages_to_leave_alone():
    """A reader acting on the finding must not go on to strip the directive off
    the archives the audit deliberately said nothing about."""
    result = _indexability([_page("/about", "about", "noindex")])
    steps = " ".join(_noindex_finding(result)["suggested_action"]["how_to_fix"])
    assert "Leave it on" in steps
    assert "tag, category, author and dated archives" in steps
    assert "`noindex, follow`" in steps


def test_a_page_typed_other_is_not_invisible_to_the_check_that_reports_hiding():
    """The page the hygiene checks read and this one skipped.

    It is typed `other`, carries `noindex,nofollow`, holds real content, and is
    in the population every other check reads - so the check whose whole job is
    to report the directive may not be the one place it does not appear.
    """
    hidden = _page("/an-invented-landing-page", "other", "noindex,nofollow")
    result = _indexability([_page("/", "home", robots=""), hidden])
    finding = _noindex_finding(result)
    assert finding is not None, "the page typed `other` was skipped again"
    assert hidden["url"] in finding["affected_pages"]
    assert "noindex" in finding["evidence"]


def test_the_directive_is_read_from_all_three_places_on_any_page_type():
    """A per-crawler meta tag and the response header carry it too, and the
    page type must not gate any of them."""
    header = _page("/an-invented-report.html", "other", robots="",
                   x_robots_tag="noindex, nofollow")
    per_agent = _page("/an-invented-note.html", "other", robots="",
                      meta_robots_by_agent={"googlebot": "noindex"})
    result = _indexability([_page("/", "home", robots=""), header, per_agent])
    pages = _noindex_finding(result)["affected_pages"]
    assert header["url"] in pages and per_agent["url"] in pages


def test_an_export_endpoint_the_crawl_set_aside_is_not_told_to_publish_itself():
    """A town library's iCalendar export answers 200 with `text/html`, an empty
    body and `X-Robots-Tag: noindex, nofollow`. It reaches this check through
    `pages_including_noindexed` and is not a page anybody wants found."""
    ical = _page("/events/list/?ical=1", "category", robots="",
                 x_robots_tag="noindex, nofollow", body_text="", body_text_len=0,
                 not_gradable=True, not_gradable_reason="empty-body")
    result = _indexability([_page("/", "home", robots=""), ical])
    assert _noindex_finding(result) is None
    assert "empty-body" in _noindex_decline(result)


# --------------------------------------------------------------------------
# A robots.txt with code in it
# --------------------------------------------------------------------------

def _robots(raw):
    sys.path.insert(0, SCRIPTS)
    from robots_parser import parse_robots
    parsed = parse_robots(raw)
    parsed.update({"status": 200, "url": SITE + "/robots.txt", "raw": raw, "errors": []})
    return parsed


def _rules_result(raw):
    result = SkillResult("crawl-access-audit")
    ACCESS._check_robots_directive_values(result, _robots(raw))
    return result


def test_a_javascript_fragment_in_robots_txt_is_reported():
    """The real line, found by hand in a live file: a build step had
    written code into robots.txt and nothing in this audit said a word."""
    result = _rules_result(
        "User-agent: *\nDisallow: /wp-admin/\nDisallow: /g,mt=RegExp(\n")
    finding = next(f for f in result.findings
                   if f["id_hint"] == "robots-txt-holds-something-that-is-not-a-rule")
    assert finding["root_cause"] == "robots-block"
    assert "/g,mt=RegExp(" in finding["evidence"]
    assert "1 of the 2" in finding["evidence"]


def test_a_file_that_is_mostly_code_is_ranked_above_one_bad_line():
    result = _rules_result(
        "User-agent: *\nDisallow: /g,mt=RegExp(\nDisallow: function(a){return a};\n")
    finding = result.findings[0]
    assert finding["severity"] == "high"
    assert "written over the file" in finding["evidence"]


@pytest.mark.parametrize("rule", [
    "/*?sort=", "/*.php$", "/cart/", "/search?q=", "/a%20b/", "/", "*",
    "/wp-admin/admin-ajax.php", "/?s=", "/en/tag/*",
])
def test_the_rules_a_real_site_publishes_are_left_alone(rule):
    """Every one of these is ordinary and correct, and firing on any of them
    would put this check on most of the web."""
    result = _rules_result("User-agent: *\nDisallow: {}\n".format(rule))
    assert result.findings == []
    assert "shaped like a URL path" in result.not_applicable[0]["reason"]


def test_a_bare_disallow_states_no_rule_and_is_not_a_broken_one():
    """`Disallow:` with nothing after it is legal and means "nothing closed"."""
    result = _rules_result("User-agent: *\nDisallow:\n")
    assert result.findings == []


def test_a_robots_txt_nobody_could_read_makes_no_claim_about_its_rules():
    result = SkillResult("crawl-access-audit")
    ACCESS._check_robots_directive_values(
        result, {"status": 403, "url": SITE + "/robots.txt", "groups": [], "errors": []})
    assert result.findings == []
    assert "refused" in result.not_applicable[0]["reason"]


# --------------------------------------------------------------------------
# Several language editions and nothing linking them
# --------------------------------------------------------------------------

def _locale_page(code, hreflang=""):
    return (
        '<!DOCTYPE html><html lang="{code}"><head><meta charset="utf-8">'
        '<title>An Invented Workshop</title>{alt}</head><body>'
        '<h1>An Invented Workshop</h1><p>An invented workshop restores wooden '
        'furniture and publishes the record of every piece it has worked on, '
        'from the first cut to the final wax.</p></body></html>'
    ).format(code=code, alt=hreflang)


ALTERNATES = ('<link rel="alternate" hreflang="en" href="/en/">'
              '<link rel="alternate" hreflang="de" href="/de/">')


@pytest.fixture(scope="module")
def locale_site(tmp_path_factory):
    """Two language editions, neither linking the other."""
    root = tmp_path_factory.mktemp("locale_site")
    for code in ("en", "de"):
        (root / code).mkdir()
        (root / code / "index.html").write_text(_locale_page(code), encoding="utf-8")
    (root / "index.html").write_text(_locale_page("en"), encoding="utf-8")
    return str(root)


def _hreflang_result(base_url, pages):
    result = SkillResult("crawl-access-audit")
    ACCESS._check_hreflang(result, {"origin": base_url, "pages": pages}, pages,
                           Fetcher(max_requests=4), robots={})
    return result


def _crawled(base_url, path, lang):
    url = base_url.rstrip("/") + path
    return {"url": url, "final_url": url, "status": 200, "lang": lang,
            "page_type": "home", "depth": 1}


def test_a_multi_locale_site_with_no_hreflang_is_reported(locale_site):
    """The measurement: `grep -c hreflang` over the pages returned 0 on
    a site whose other locales the crawl had visited."""
    with FixtureServer(locale_site) as server:
        pages = [_crawled(server.base_url, "/en/", "en"),
                 _crawled(server.base_url, "/de/", "de")]
        result = _hreflang_result(server.base_url, pages)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "no-hreflang-between-language-editions")
    assert finding["root_cause"] == "canonical-broken"
    assert "/en/" in finding["evidence"] and "/de/" in finding["evidence"]
    # The claim names what it read, and admits what it did not.
    assert "xhtml:link" in finding["evidence"]
    assert len(finding["affected_pages"]) == 2


def test_a_site_that_does_link_its_editions_is_left_alone(tmp_path_factory):
    root = tmp_path_factory.mktemp("linked_locale_site")
    for code in ("en", "de"):
        (root / code).mkdir()
        (root / code / "index.html").write_text(
            _locale_page(code, ALTERNATES), encoding="utf-8")
    (root / "index.html").write_text(_locale_page("en", ALTERNATES), encoding="utf-8")
    with FixtureServer(str(root)) as server:
        pages = [_crawled(server.base_url, "/en/", "en"),
                 _crawled(server.base_url, "/de/", "de")]
        result = _hreflang_result(server.base_url, pages)
    assert result.findings == []
    assert "declare `hreflang`" in result.not_applicable[0]["reason"]


def test_a_site_with_one_edition_is_asked_nothing_and_costs_nothing(locale_site):
    """Nearly every site. The check must not spend a request on it."""
    with FixtureServer(locale_site) as server:
        pages = [_crawled(server.base_url, "/en/", "en")]
        fetcher = Fetcher(max_requests=4)
        result = SkillResult("crawl-access-audit")
        ACCESS._check_hreflang(result, {"origin": server.base_url, "pages": pages},
                               pages, fetcher, robots={})
    assert result.findings == []
    assert fetcher.count == 0
    assert "no second edition" in result.not_applicable[0]["reason"]


# --------------------------------------------------------------------------
# A check filed under "contributed to a finding" with no such finding
# --------------------------------------------------------------------------

_HOME = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>An Invented Workshop</title>
<meta name="description" content="Furniture restoration and the record of it.">
</head><body><nav><a href="/">Home</a> <a href="/about.html">About</a></nav>
<h1>An Invented Workshop</h1>
<p>An invented workshop restores wooden furniture and publishes the record of
every piece it has worked on, from the first cut to the final wax.</p>
</body></html>
"""


@pytest.fixture(scope="module")
def catch_all_run(tmp_path_factory):
    """The whole skill, over a server answering its homepage for every path."""
    root = tmp_path_factory.mktemp("catch_all_access")
    (root / "index.html").write_text(_HOME, encoding="utf-8")
    (root / "about.html").write_text(_HOME, encoding="utf-8")
    (root / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    snapshot = str(root / "snapshot.json")
    out = str(root / "crawl-access-audit.findings.json")

    server = FixtureServer(str(root))
    server.rules = {"catch_all": True}
    with server:
        for args in ([os.path.join(SCRIPTS, "crawl.py"), server.base_url,
                      "--out", snapshot, "--budget", "60", "--delay", "0", "--no-render"],
                     [os.path.join(ROOT, "skills", "crawl-access-audit", "scripts",
                                   "check.py"),
                      "--snapshot", snapshot, "--out", out]):
            done = subprocess.run([sys.executable] + args, cwd=ROOT,
                                  capture_output=True, text=True)
            assert done.returncode == 0, done.stderr[-1500:]
    with open(out, encoding="utf-8") as handle:
        return json.load(handle)


def test_every_check_marked_as_fired_is_named_by_a_finding(catch_all_run):
    """The original defect. `sitemap-present`, `sitemap-parses` and
    `unknown-paths-return-200` were filed under "Checks that contributed to a
    finding above" - the bucket for a check the report cannot name - in a
    report holding no such finding.

    A check registered beside one that fires is swept into `fired_checks` and
    then has to be accounted for by the report rather than by this skill. The
    cure is upstream of that: answer each of them here, so nothing is swept up
    that did not contribute.
    """
    named = {f["check"] for f in catch_all_run["findings"]}
    unnamed = sorted(set(catch_all_run["fired_checks"]) - named)
    assert not unnamed, "filed as contributing to a finding this skill did not name: {}".format(
        unnamed)


def test_the_two_sitemap_siblings_say_why_they_are_quiet(catch_all_run):
    """"A check that stayed quiet must say why." There is no sitemap on this
    origin, so there was nothing to parse and no URL list to sample."""
    declines = {n["check"]: n["reason"] for n in catch_all_run["not_applicable"]}
    assert "no sitemap was published" in declines["sitemap-parses"]
    assert "no sitemap was published" in declines["sitemap-urls-resolve"]


def test_the_reader_is_told_about_the_catch_all(catch_all_run):
    """"The reader learns nothing about the catch-all." Whichever of this
    skill's findings survives the merge, the measurement that makes every 200
    on this origin meaningless has to be in a sentence somebody reads."""
    published = " ".join(
        [f["evidence"] for f in catch_all_run["findings"]]
        + [n["reason"] for n in catch_all_run["not_applicable"]])
    assert "addresses that do not exist" in published or \
           "does not exist and returned HTTP 200" in published


def test_the_missing_sitemap_finding_is_filed_under_sitemap_present(catch_all_run):
    """It was filed under `sitemap-urls-resolve`, the last of three names
    registered at the top of one function, which is the check that samples the
    URLs inside a sitemap this site does not have."""
    finding = next(f for f in catch_all_run["findings"]
                   if f["id_hint"] == "sitemap-missing")
    assert finding["check"] == "sitemap-present"
