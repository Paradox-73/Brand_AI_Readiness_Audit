# -*- coding: utf-8 -*-
"""Hostnames, schemes, names and robots rules, read the way the site wrote them.

  one site, two names     a crawl read 54 pages on `www.` and 6 on the bare
                          domain, every one answering 200 where it was asked,
                          no canonical anywhere - and the host check said "no
                          path answered on more than one hostname", because the
                          two halves of the crawl visited different paths

  http:// internal links  a menu linking the site's own sections as `http://`,
                          each answering 307 to https://, passed as "the site
                          is served over HTTPS"

  a title that describes  "The Programming Language <Name>" became the
                          project's name, on a site that signs every page with
                          the one word

  a random token          `/QX47RT2MZP9K`, a trap for crawlers that ignore
                          robots.txt, listed as a path that looks like content

  the bare token          a probe sending `OAI-SearchBot` was served where the
                          crawler's own published string was refused

Every host in this file is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult, USER_AGENT  # noqa: E402
from crawl import detect_brand  # noqa: E402
from robots_parser import benign_disallow, parse_robots, substantive_disallows  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCESS = _module("crawl-access-audit", "access_for_hosts_and_names")


# --------------------------------------------------------------------------
# Shared builders
# --------------------------------------------------------------------------

def _page(url, title="Invented Project", canonical="", internal=(), final_url=None,
          page_type="other", **extra):
    record = {"url": url, "final_url": final_url or url, "status": 200,
              "page_type": page_type, "title": title, "body_text_len": 900,
              "canonical": canonical,
              "links": {"internal": [{"url": u, "text": "a section"} for u in internal],
                        "external": []},
              "jsonld": [], "headers": {}}
    record.update(extra)
    return record


def _snapshot(pages, origin="https://www.an-invented-project.test", **extra):
    snapshot = {"origin": origin, "site": "an invented project", "pages": pages,
                "crawl": {"head_supported": True}}
    snapshot.update(extra)
    return snapshot


def _hosts(snapshot, fetcher=None, robots=None):
    result = SkillResult("crawl-access-audit")
    ACCESS._check_transport_and_hosts(result, snapshot, snapshot["pages"], fetcher, robots)
    return result


def _reason(result, check):
    return next(item["reason"] for item in result.not_applicable if item["check"] == check)


def _by_id(result, id_hint):
    return [f for f in result.findings if f.get("id_hint") == id_hint]


class _Response:
    def __init__(self, url, status=200, html="", history=()):
        self.url = url
        self.status_code = status
        self.history = list(history)
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.content = html.encode("utf-8")
        self.text = html


class _Fetcher:
    """Answers from a function of (url, user agent); counts what it was asked."""

    def __init__(self, answer, budget=5):
        self.answer = answer
        self.max_requests = budget
        self.count = 0
        self.calls = []
        self.second_transport = "urllib"

    @property
    def budget_left(self):
        return self.count < self.max_requests

    @property
    def time_left(self):
        return True

    def try_get(self, url, user_agent=None, **_):
        self.count += 1
        self.calls.append((url, user_agent))
        return self.answer(url, user_agent)


# --------------------------------------------------------------------------
# One site on two names, with no path read under both
# --------------------------------------------------------------------------

def _split_crawl(canonical_www="", canonical_bare=""):
    return [
        _page("https://www.an-invented-project.test/", page_type="home",
              canonical=canonical_www),
        _page("https://www.an-invented-project.test/docs/", "Docs", canonical=canonical_www),
        _page("https://www.an-invented-project.test/about/", "About", canonical=canonical_www),
        _page("https://an-invented-project.test/forum/", "Forum", canonical=canonical_bare),
        _page("https://an-invented-project.test/cli.html", "Shell", canonical=canonical_bare),
    ]


def test_pages_read_on_both_the_bare_and_www_name_are_one_site_on_two_names():
    """No path was read twice, and the names are the evidence: each answered
    200 where it was asked instead of handing over to the other, and nothing
    says which is real."""
    result = _hosts(_snapshot(_split_crawl()))
    finding = _by_id(result, "site-answers-on-more-than-one-hostname")
    assert finding, [i["reason"] for i in result.not_applicable]
    finding = finding[0]
    assert finding["severity"] == "medium"
    assert finding["check"] == "canonical-host-consistency"
    assert "an-invented-project.test and www.an-invented-project.test" in finding["title"]
    assert "2 pages on an-invented-project.test" in finding["evidence"]
    assert "3 pages on www.an-invented-project.test" in finding["evidence"]
    assert len(finding["affected_pages"]) == 5


def test_copies_that_carry_a_canonical_naming_the_real_name_settle_it():
    real = "https://www.an-invented-project.test/"
    result = _hosts(_snapshot(_split_crawl(canonical_www=real, canonical_bare=real)))
    assert not _by_id(result, "site-answers-on-more-than-one-hostname")
    assert "single hostname" in _reason(result, "canonical-host-consistency")


def test_the_other_name_answering_the_homepage_itself_is_the_finding():
    """The crawl stayed on one name; one GET of the other name's homepage
    answered 200 with the same page, without redirecting."""
    pages = [_page("https://www.an-invented-project.test/", page_type="home"),
             _page("https://www.an-invented-project.test/docs/", "Docs")]
    html = "<html><head><title>Invented Project</title></head><body>hi</body></html>"
    fetcher = _Fetcher(lambda url, ua: _Response(url, 200, html))
    result = _hosts(_snapshot(pages), fetcher, parse_robots("User-agent: *\nAllow: /\n"))
    assert [url for url, _ in fetcher.calls] == ["https://an-invented-project.test/"]
    finding = _by_id(result, "site-answers-on-more-than-one-hostname")[0]
    assert "answered HTTP 200 without redirecting" in finding["evidence"]


def test_the_other_name_redirecting_here_is_one_request_and_no_finding():
    pages = [_page("https://www.an-invented-project.test/", page_type="home")]
    fetcher = _Fetcher(lambda url, ua: _Response(
        "https://www.an-invented-project.test/", 200, "<title>Invented Project</title>",
        history=[_Response(url, 301)]))
    result = _hosts(_snapshot(pages), fetcher, {})
    assert len(fetcher.calls) == 1
    assert not _by_id(result, "site-answers-on-more-than-one-hostname")
    assert "redirected to https://www.an-invented-project.test/" in _reason(
        result, "canonical-host-consistency")


def test_the_other_name_is_not_asked_for_where_robots_txt_closes_it():
    pages = [_page("https://www.an-invented-project.test/", page_type="home")]
    fetcher = _Fetcher(lambda url, ua: _Response(url, 200, "<title>Invented Project</title>"))
    _hosts(_snapshot(pages), fetcher, parse_robots("User-agent: *\nDisallow: /\n"))
    assert fetcher.calls == []


# --------------------------------------------------------------------------
# Internal links written http:// on an HTTPS site
# --------------------------------------------------------------------------

def _museum(followed=True, statuses=(307,)):
    origin = "https://www.an-invented-museum.test"
    link = "http://www.an-invented-museum.test/plates/"
    pages = [_page(origin + "/", "Invented Museum", page_type="home", internal=[link])]
    if followed:
        pages.append(_page(link, "Plates", final_url=origin + "/plates/main.do",
                           redirect_chain=[link, "https://www.an-invented-museum.test/plates/"],
                           redirect_statuses=list(statuses)))
    return _snapshot(pages, origin=origin)


def test_internal_http_links_that_redirect_are_reported_as_transport():
    result = _hosts(_museum())
    finding = _by_id(result, "internal-links-use-http")
    assert finding, [i["reason"] for i in result.not_applicable]
    finding = finding[0]
    assert finding["severity"] == "low"
    assert finding["root_cause"] == "insecure-transport"
    assert finding["check"] == "https-transport"
    assert "HTTP 307" in finding["evidence"] and "temporary redirect" in finding["evidence"]
    assert finding["affected_pages"] == ["https://www.an-invented-museum.test/"]


def test_an_http_link_the_crawl_never_followed_is_not_known_to_redirect():
    result = _hosts(_museum(followed=False))
    assert not _by_id(result, "internal-links-use-http")
    assert _reason(result, "https-transport") == "the site is served over HTTPS"


# --------------------------------------------------------------------------
# A title that describes the project before it names it
# --------------------------------------------------------------------------

def _home(title, url="https://www.frostvane.test/", body=""):
    return {"url": url, "final_url": url, "status": 200, "page_type": "home",
            "title": title, "body_text": body, "jsonld": [], "og": {},
            "headings": {"h1": [], "h2": [], "h3": []}, "links": {"internal": []}}


def test_the_name_after_a_descriptive_lead_is_the_name():
    brand = detect_brand([_home("The Programming Language Frostvane")],
                         "https://www.frostvane.test")
    assert brand["name"] == "Frostvane"


def test_the_copyright_line_corroborates_a_name_the_address_does_not_spell():
    notice = "Freely available. Copyright © 1994–2026 Frostvane.test, An Invented University. "
    pages = [_home("The Scripting Language Frostvane", url="https://www.fv-lang.test/"),
             _home("Manual", url="https://www.fv-lang.test/manual/", body=notice),
             _home("About", url="https://www.fv-lang.test/about/", body=notice)]
    for page in pages[1:]:
        page["page_type"] = "other"
    assert detect_brand(pages, "https://www.fv-lang.test")["name"] == "Frostvane"


def test_a_lead_that_is_part_of_the_name_stays():
    brand = detect_brand([_home("Friends of Frostvane")], "https://www.frostvane.test")
    assert brand["name"] == "Friends of Frostvane"
    brand = detect_brand([_home("The Official Site of Frostvane")],
                         "https://www.frostvane.test")
    assert brand["name"] == "Frostvane"


# --------------------------------------------------------------------------
# A random token in robots.txt is a trap, not a section
# --------------------------------------------------------------------------

def test_a_random_token_is_not_a_content_path():
    for rule in ("/QX47RT2MZP9K", "/kd8vn3w1pq5z", "/HT2Q9ZL41MWB/"):
        assert benign_disallow(rule) is True, rule
    for rule in ("/phone15pro", "/2024collection", "/covid19-update", "/mp3players",
                 "/lunar-new-year/", "/summercollection"):
        assert benign_disallow(rule) is False, rule


def test_only_the_real_section_survives_among_the_traps():
    robots = parse_robots("User-agent: *\nDisallow: /lunar-new-year/\n"
                          "Disallow: /QX47RT2MZP9K\nDisallow: /kd8vn3w1pq5z\n"
                          "Disallow: /HT2Q9ZL41MWB\n")
    assert substantive_disallows(robots, "*") == ["/lunar-new-year/"]


# --------------------------------------------------------------------------
# The probe sends the crawler's own published string
# --------------------------------------------------------------------------

def test_the_probe_string_is_the_operators_published_one():
    sent = ACCESS._ua_string("OAI-SearchBot")
    assert sent.startswith("Mozilla/5.0 ")
    assert "compatible; OAI-SearchBot/" in sent and "+https://openai.com/searchbot" in sent
    assert USER_AGENT in sent
    # An operator that publishes only the token gets the token, not a guess.
    assert ACCESS._ua_string("Claude-SearchBot").startswith("Claude-SearchBot (")
    for name in ACCESS.ANSWER_CRAWLERS:
        assert name.lower() in ACCESS._ua_string(name).lower()


def test_an_edge_refusing_the_published_string_is_measured_as_refusing_it():
    """The edge serves a request carrying the bare token and refuses the
    string the crawler actually sends."""
    page = "<html><head><title>Invented Shop</title></head><body>{}</body></html>".format(
        "Handmade things for sale. " * 40)

    def edge(url, ua):
        if ua and "compatible; OAI-SearchBot/" in ua:
            return _Response(url, 403, "Forbidden")
        return _Response(url, 200, page)

    fetcher = _Fetcher(edge, budget=10)
    probe = ACCESS._ask_each_name_again(SkillResult("crawl-access-audit"), fetcher,
                                        "https://www.an-invented-shop.test/", {})
    assert probe is not None
    assert "OAI-SearchBot" in probe.refused
