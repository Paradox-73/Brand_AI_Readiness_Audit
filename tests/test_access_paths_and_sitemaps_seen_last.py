# -*- coding: utf-8 -*-
"""Shapes from real sites that the access checks read wrong, pinned one by one.

* A software project's robots.txt closes its repository browser, its
  documentation sources and its contributed code, and a central bank's closes
  the system folders its web server creates. Both were ranked first as "paths
  that look like real content".
* A server building each page on request sends `Last-Modified` equal to the
  moment it answered, and the sitemap snippet printed the audit's own date on
  every entry beneath "Do not set <lastmod> to today's date".
* A museum answers its sitemap address with a 200 and an HTML page saying the
  page cannot be found, and the report called that a refusal.
* A library audited at `http://` was said to have "answered over HTTPS", and
  one check was listed twice with two different reasons.
* A one-page site's `x-default` names a page holding two words and a script.
* A furniture retailer's regional storefronts passed the `hreflang` check on
  the strength of the root country picker alone.
* A shop blocking only two training crawlers was told that every answer agent
  of the same companies was blocked too.
* A name probe sent from the audit's own address was ranked critical, when a
  CDN refusing an unverified address that claims a crawler's name is the rule
  working.
* `/home-visit` and `/home-visit/` were read as two pages.

Every host below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import Fetcher, SkillResult  # noqa: E402
from crawl import dedup_key, fetch_page  # noqa: E402
from robots_parser import parse_robots, substantive_disallows  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_access_paths_seen_last")

SITE = "https://library-project.test"


# --------------------------------------------------------------------------
# robots.txt: a repository's front ends and a web server's own folders
# --------------------------------------------------------------------------

def _closed(text):
    return substantive_disallows(parse_robots(text), "*")


def test_a_software_projects_repository_browsers_are_not_content():
    """Every one of these expands into a page per file per revision."""
    assert _closed("User-agent: *\nDisallow: /cvstrac\nDisallow: /src\n"
                   "Disallow: /docsrc\nDisallow: /cgi\nDisallow: /contrib\n") == []
    assert _closed("User-agent: *\nDisallow: /timeline\nDisallow: /git/\n") == []


def test_a_name_with_a_second_meaning_is_excused_only_beside_a_repository_path():
    """`/timeline` is a museum's history page before it is a diff view, and
    `/contrib` a community's contributors page. Alone, they are sections."""
    assert _closed("User-agent: *\nDisallow: /timeline\nDisallow: /contrib\n") == [
        "/contrib", "/timeline"]
    # And the strict boundary: a hyphen does not make a page into plumbing.
    assert _closed("User-agent: *\nDisallow: /src-images\nDisallow: /git-guide\n") == [
        "/git-guide", "/src-images"]


def test_a_web_servers_own_system_folders_are_not_content():
    assert _closed("User-agent: *\nDisallow: /_layouts/\nDisallow: /_vti_bin/\n"
                   "Disallow: /_catalogs/\nDisallow: /_vti_pvt/\nDisallow: /_api/\n") == []


def test_editorial_sections_are_still_reported():
    """Precision is not bought with silence: real sections still come back."""
    assert _closed("User-agent: *\nDisallow: /research\nDisallow: /stories\n"
                   "Disallow: /_vti_bin/\n") == ["/research", "/stories"]


# --------------------------------------------------------------------------
# The sitemap snippet never prints the moment of the request as a lastmod
# --------------------------------------------------------------------------

def _http_date(moment):
    return format_datetime(moment.astimezone(timezone.utc), usegmt=True)


def test_last_modified_within_minutes_of_the_fetch_is_generated_on_request():
    """The fetch time comes from the crawl's own clock when the page record
    carries no `Date` header - which, for years, was every page record."""
    fetched = datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc)
    page = {"fetched_at": "2026-03-02T10:00:00Z",
            "headers": {"last-modified": _http_date(fetched - timedelta(minutes=2))}}
    assert CA._observed_lastmod(page) == ""
    # A real modification well before the fetch is kept.
    earlier = {"fetched_at": "2026-03-02T10:00:00Z",
               "headers": {"last-modified": "Mon, 16 Feb 2026 08:00:00 GMT"}}
    assert CA._observed_lastmod(earlier) == "2026-02-16"


def test_a_lastmod_is_never_the_audit_date():
    """With no fetch time recorded at all, the day the check runs stands in."""
    now = datetime.now(timezone.utc)
    page = {"url": SITE + "/", "final_url": SITE + "/", "status": 200, "page_type": "home",
            "dates": {}, "headers": {"last-modified": _http_date(now)}}
    snippet = CA._sitemap_snippet({"origin": SITE, "pages": [page]})
    assert "<lastmod>" not in snippet
    assert now.strftime("%Y-%m-%d") not in snippet


class _Page:
    history = ()
    content = (b"<html lang=\"en\"><head><title>Release notes</title></head>"
               b"<body><h1>Release notes</h1><p>What changed in this release.</p></body></html>")
    status_code = 200
    elapsed_ms = 1
    encoding = "utf-8"

    def __init__(self, url):
        self.url = url
        self.headers = {"content-type": "text/html; charset=utf-8",
                        "date": "Wed, 02 Sep 2026 10:00:00 GMT",
                        "last-modified": "Wed, 02 Sep 2026 10:00:00 GMT"}

    @property
    def text(self):
        return self.content.decode("utf-8")


class _Fetcher:
    def get(self, url, **kwargs):
        return _Page(url)


def test_the_crawl_records_when_each_page_was_read():
    """Without the `Date` header or the crawl's own clock in the record, the
    guard above had nothing to compare against and never fired."""
    record = fetch_page(_Fetcher(), SITE + "/notes", 1, "bfs", SITE)
    assert record["headers"]["date"] == "Wed, 02 Sep 2026 10:00:00 GMT"
    assert record["fetched_at"].endswith("Z")
    assert CA._observed_lastmod(record) == ""


# --------------------------------------------------------------------------
# A 200 carrying an HTML page at a sitemap address is no sitemap
# --------------------------------------------------------------------------

def test_an_html_page_at_the_sitemap_address_is_no_sitemap_and_no_refusal():
    snapshot = {"origin": SITE, "pages": [], "robots": {"status": 404},
                "sitemaps": [
                    {"url": SITE + path, "status": 200, "looks_like_html": True,
                     "parse_error": None, "content_type": "text/html;charset=utf-8",
                     "url_count": 0, "urls": []}
                    for path in ("/sitemap.xml", "/sitemap_index.xml")]}
    result = SkillResult("crawl-access-audit")
    CA._check_sitemaps(result, snapshot, None, snapshot["robots"])
    missing = [f for f in result.findings if f["id_hint"] == "sitemap-missing"]
    assert len(missing) == 1
    assert "HTTP 200 carrying an HTML page rather than XML" in missing[0]["evidence"]
    reasons = " ".join(entry["reason"] for entry in result.not_applicable)
    assert "not allowed to look" not in reasons
    assert "no sitemap request produced an answer" not in reasons


# --------------------------------------------------------------------------
# A host whose certificate chain is one short, audited at http://
# --------------------------------------------------------------------------

def test_a_tls_failure_after_a_redirect_is_described_in_order_and_once():
    origin = "http://national-library.test"
    error = ("HTTPSConnectionPool(host='national-library.test', port=443): Max retries "
             "exceeded with url: / (Caused by SSLError(SSLCertVerificationError(1, "
             "'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
             "local issuer certificate (_ssl.c:1010)')))")
    snapshot = {"origin": origin, "robots": {}, "sitemaps": [],
                "pages": [{"url": origin + "/", "final_url": origin + "/", "status": None,
                           "error": error, "page_type": "home", "redirect_chain": []}]}
    result = CA.run(snapshot, allow_network=False)
    finding = next(f for f in result.findings if f["id_hint"] == "homepage-not-reachable")
    assert finding["check"] == "homepage-reachable"
    assert not finding["evidence"].startswith(origin + "/ answered over HTTPS")
    assert "redirects to HTTPS, and https://national-library.test/ answered over HTTPS" \
        in finding["evidence"]
    checks = [entry["check"] for entry in result.not_applicable]
    assert checks.count("canonical-targets") == 1


# --------------------------------------------------------------------------
# hreflang: x-default naming an empty page; editions that name nothing
# --------------------------------------------------------------------------

ONE_PAGE = "https://no-greeting.test"


def _edition(path, lang, alternates, **extra):
    record = {"url": ONE_PAGE + path, "final_url": ONE_PAGE + path, "status": 200,
              "lang": lang, "depth": 1, "text_len": 3000,
              "hreflang": [{"hreflang": code, "url": ONE_PAGE + target}
                           for code, target in alternates]}
    record.update(extra)
    return record


def _hreflang_run(pages, fetcher=None, sitemaps=()):
    snapshot = {"origin": ONE_PAGE, "pages": pages, "sitemaps": list(sitemaps)}
    result = SkillResult("crawl-access-audit")
    CA._check_hreflang(result, snapshot, [p for p in pages if p.get("status") == 200],
                       fetcher)
    return result


ALTERNATES = [("en", "/en"), ("de", "/de"), ("fr", "/fr"), ("x-default", "/")]


def test_an_x_default_that_names_a_script_redirect_stub_is_reported():
    stub = {"url": ONE_PAGE + "/", "final_url": ONE_PAGE + "/", "status": 200, "lang": "en",
            "depth": 0, "text_len": 8, "script_redirect": {"computed": True, "url": ""},
            "hreflang": [{"hreflang": code, "url": ONE_PAGE + target}
                         for code, target in ALTERNATES]}
    pages = [stub] + [_edition("/" + code, code, ALTERNATES) for code in ("en", "de", "fr")]
    result = _hreflang_run(pages)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "hreflang-x-default-is-an-empty-page")
    assert "redirects by script" in finding["title"]
    assert ONE_PAGE + "/en" in finding["suggested_action"]["summary"]
    assert 'hreflang="x-default"' in finding["suggested_action"]["how_to_fix"][0]


def test_an_x_default_that_names_a_real_page_or_a_picker_is_not_reported():
    alternates = [("en", "/en"), ("de", "/de"), ("x-default", "/en")]
    pages = [_edition("/en", "en", alternates), _edition("/de", "de", alternates)]
    assert not _hreflang_run(pages).findings
    # A country picker with its choices on it is what `x-default` is for.
    picker = {"url": ONE_PAGE + "/", "final_url": ONE_PAGE + "/", "status": 200, "lang": "en",
              "depth": 0, "text_len": 127, "script_redirect": None, "hreflang": []}
    pages = [picker] + [_edition("/" + code, code, ALTERNATES) for code in ("en", "de", "fr")]
    assert not _hreflang_run(pages).findings


STORE = "https://sofa-store.test"


def _store_page(path, lang, alternates=()):
    return {"url": STORE + path, "final_url": STORE + path, "status": 200, "lang": lang,
            "depth": 0 if path == "/" else 1, "text_len": 5000,
            "hreflang": [{"hreflang": code, "url": STORE + target}
                         for code, target in alternates]}


REGIONAL = [("en-au", "/au"), ("en-sg", "/sg"), ("en-us", "/us"), ("x-default", "/")]


def _regional_store():
    return [_store_page("/", "en-us", REGIONAL), _store_page("/au", "en-AU"),
            _store_page("/sg", "en-SG"), _store_page("/us", "en-US"),
            _store_page("/au/sofas", "en-AU"), _store_page("/sg/sofas", "en-SG")]


def _run_store(pages, fetcher=None):
    snapshot = {"origin": STORE, "pages": pages, "sitemaps": []}
    result = SkillResult("crawl-access-audit")
    CA._check_hreflang(result, snapshot, pages, fetcher)
    return result


def test_a_country_pickers_tags_do_not_link_the_editions_to_each_other():
    result = _run_store(_regional_store())
    reason = next(entry["reason"] for entry in result.not_applicable
                  if entry["check"] == "hreflang-between-language-editions")
    assert "are linked to each other" not in reason


class _Html:
    status_code = 200
    history = ()
    encoding = "utf-8"

    def __init__(self, url):
        self.url = url
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.content = b"<html><head><title>Sofas</title></head><body>Sofas</body></html>"

    @property
    def text(self):
        return self.content.decode("utf-8")


class _Reader:
    budget_left = True
    time_left = True

    def try_get(self, url, **kwargs):
        return _Html(url)


def test_regional_editions_that_name_nothing_are_reported_with_the_picker_named():
    result = _run_store(_regional_store(), _Reader())
    finding = next(f for f in result.findings
                   if f["id_hint"] == "no-hreflang-between-language-editions")
    assert STORE + "/ does declare `hreflang` alternates" in finding["evidence"]


# --------------------------------------------------------------------------
# The training-crawler finding's sentence about the same companies
# --------------------------------------------------------------------------

def _training_step(robots_text):
    result = SkillResult("crawl-access-audit")
    robots = dict(parse_robots(robots_text), status=200, url=SITE + "/robots.txt")
    CA._check_robots_blocks(result, robots, SITE, [])
    finding = next(f for f in result.findings
                   if f["id_hint"] == "robots-blocks-training-crawlers")
    return " ".join(finding["suggested_action"]["how_to_fix"])


def test_operators_with_no_answer_agent_are_not_said_to_be_blocked():
    steps = _training_step("User-agent: CCBot\nDisallow: /\n\n"
                           "User-agent: Bytespider\nDisallow: /\n\n"
                           "User-agent: *\nAllow: /\n")
    assert "also blocked" not in steps
    assert "reported separately" not in steps
    assert "None of these companies runs a separate search or live-fetch agent" in steps


def test_an_operators_open_answer_agent_is_still_named():
    steps = _training_step("User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert "robots.txt already lets them in" in steps
    assert "OAI-SearchBot" in steps


# --------------------------------------------------------------------------
# A name probe from this audit's address is not the crawler
# --------------------------------------------------------------------------

def test_a_name_probe_is_never_critical_and_says_what_it_could_not_see():
    from test_edge_blocked_crawlers import _EdgeServer, _run_comparison

    # Every answer crawler refused but the last, and this audit served: the
    # primary client's own comparison finds the difference.
    with _EdgeServer(refuse_names=CA.ANSWER_CRAWLERS[:-1], refuse_primary=False) as server:
        result, _, _ = _run_comparison(server, status=200)
    findings = [f for f in result.findings if f["root_cause"] == "bot-manager-block"]
    assert findings
    for finding in findings:
        assert finding["severity"] != "critical"
        assert finding["confidence"] == "medium"
        assert "address" in finding["evidence"]
        first = finding["suggested_action"]["how_to_fix"][0]
        assert "verified-bot" in first and "reverse DNS" in first


# --------------------------------------------------------------------------
# One page, two spellings
# --------------------------------------------------------------------------

def test_a_trailing_slash_twin_is_the_same_page():
    assert dedup_key(SITE + "/home-visit") == dedup_key(SITE + "/home-visit/")
    assert dedup_key(SITE + "/a/?page=2") == dedup_key(SITE + "/a?page=2")
    # The root keeps its slash, and different paths stay different.
    assert dedup_key(SITE) == dedup_key(SITE + "/")
    assert dedup_key(SITE + "/home") != dedup_key(SITE + "/home-visit")
