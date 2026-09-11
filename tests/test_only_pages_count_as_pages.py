"""What the crawl fetches, what spends a page slot, and what a ratio divides by.

One defect, four costs. A crawl of sixteen URLs on a static site downloaded
eight release archives and a patch file - 17.8 MB - because the ending
`.tar.bz2` was on nobody's list. Those nine responses took eight of the
sixteen page slots, so pages with prose on them were never requested; they
were then counted as URLs the crawl "reached but could not read", which put
the readable share at 7/16, under the bar that holds back absence claims; and
eight true findings - each checkable from `lang`, `og`, `jsonld` and
`has_viewport` on the seven pages that were read - were filed as things the
audit could not verify. The report told the reader the unread URLs "were
refused or challenged". All sixteen answered HTTP 200.

The rule these tests hold in place:

  - an ending we recognise as not-a-page costs no request at all;
  - anything else costs one request and its body is read only if the content
    type says it is a page;
  - a response that was never a page spends no page slot and leaves every
    "how much of this site was read" denominator;
  - and one coverage rule decides both whether an absence may be asserted and
    whether the site may be graded, so a run can never do one and not the
    other.
"""

from __future__ import annotations

import json
import os
import sys

from conftest import FIXTURES, SCRIPTS  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import compose_report as compose  # noqa: E402
import crawl as crawl_module  # noqa: E402
from audit_common import Fetcher  # noqa: E402
from fixture_server import FixtureServer  # noqa: E402


# --------------------------------------------------------------------------
# A site shaped like the one that produced the failure
# --------------------------------------------------------------------------

# Deliberately spare markup: no `lang`, no viewport, no Open Graph, no
# JSON-LD. Those absences are the eight findings the crawl suppressed, and a
# fixture that declares them cannot show that they come back.
_PAGE = ("<html><head><title>{title}</title></head><body>"
         "<h1>{title}</h1><p>{body}</p>{links}</body></html>")

_PAGES = {
    "index.html": "Toolkit",
    "about.html": "About the toolkit",
    "faq.html": "FAQ",
    "docs.html": "Documentation",
    "contact.html": "Contact",
    "history.html": "History",
    "downloads.html": "Downloads",
}

# Eight archives and a patch, the shape a source project publishes a release
# in. Named by suffix family rather than by any project's convention.
_ARCHIVES = ["release-{}.0.tar.bz2".format(n) for n in range(1, 9)] + ["fix-1.patch"]

# A URL with no extension at all, answering something that is not a page. The
# ending test cannot see this one, which is why it is not the test correctness
# rests on.
_UNMARKED = "handbook"


def _build_site(root):
    os.makedirs(root, exist_ok=True)
    links = "".join('<a href="/{}">{}</a>'.format(name, title)
                    for name, title in _PAGES.items() if name != "index.html")
    downloads = "".join('<a href="/{}">download</a>'.format(name)
                        for name in _ARCHIVES)
    downloads += '<a href="/{}">handbook</a>'.format(_UNMARKED)
    # An account route, linked like any other. GET only and harmless, but the
    # crawl should never spend a request on a page that wants a password.
    downloads += '<a href="/store/mypage/change">your details</a>'
    for name, title in _PAGES.items():
        body = "This project publishes its releases and documents how to use them. " * 3
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write(_PAGE.format(
                title=title, body=body,
                links=links + (downloads if name == "downloads.html" else "")))
    for name in _ARCHIVES:
        with open(os.path.join(root, name), "wb") as handle:
            handle.write(b"\x00binary release payload" * 4000)
    with open(os.path.join(root, _UNMARKED), "wb") as handle:
        handle.write(b"\x00binary handbook payload" * 4000)
    os.makedirs(os.path.join(root, "store", "mypage"), exist_ok=True)
    with open(os.path.join(root, "store", "mypage", "change"), "w",
              encoding="utf-8") as handle:
        handle.write("<html><body>sign in</body></html>")
    return root


def _crawl(tmp_path, max_pages=16):
    root = _build_site(str(tmp_path / "site"))
    with FixtureServer(root) as server:
        snapshot = crawl_module.crawl(
            server.base_url, str(tmp_path / "snapshot.json"),
            max_pages=max_pages, budget_s=60, respect_robots=False,
            delay=0, render=False)
        requested = [entry["path"] for entry in server.request_log]
    return snapshot, requested


# --------------------------------------------------------------------------
# What is fetched
# --------------------------------------------------------------------------

def test_a_release_archive_is_never_requested(tmp_path):
    """17.8 MB of somebody else's bandwidth, for nine responses with no prose
    in them. The ending is the cheap test and it saves the whole request."""
    _, requested = _crawl(tmp_path)
    for name in _ARCHIVES:
        assert "/" + name not in requested, \
            "{} was fetched; it can never be a page".format(name)


def test_an_account_route_is_never_requested(tmp_path):
    """One crawl reached a `/mypage/` route and got 401. Harmless in itself,
    but the record then sat in the snapshot as a URL this site "refused", and
    the user-agent comparison picked it as the one URL to probe under seven
    crawler names - every one of which got 401, because that is what a page
    wanting a password answers. The check measured nothing."""
    _, requested = _crawl(tmp_path)
    assert not [path for path in requested if "mypage" in path]


def test_a_body_that_is_not_a_page_is_not_downloaded(tmp_path):
    """An ending can never be complete: this URL has none and still answers
    with something that is not a page. The content type settles it, and it is
    read off the response already asked for - a HEAD would cost the same one
    request and then need a second for the body of a real page."""
    snapshot, requested = _crawl(tmp_path)
    assert "/" + _UNMARKED in requested, \
        "the ending test cannot judge this URL, so it must be fetched to find out"
    record = next(p for p in snapshot["pages"] if p["url"].endswith("/" + _UNMARKED))
    assert record["status"] == 200
    assert record["not_a_page"] is True
    assert record["body_declined"] is True
    assert record["html_len"] == 0, "the body arrived despite the content type"
    assert record["declared_bytes"] > 1000, \
        "the record should say how big the thing was that it declined"


def test_the_body_hook_this_depends_on_still_exists():
    """`PageFetcher` stops a response before its body by overriding a method of
    a class this file's subject does not own. A rename over there would send
    the crawl quietly back to downloading archives, so it fails here instead."""
    assert hasattr(Fetcher, "_read_body")
    assert crawl_module.PageFetcher._read_body is not Fetcher._read_body


def test_the_html_test_is_the_same_one_in_both_places():
    """The decision made before the body arrives and the decision made after it
    must be one function, or a response can be downloaded and then discarded."""
    assert crawl_module.looks_like_html("text/html; charset=utf-8")
    assert crawl_module.looks_like_html("application/xhtml+xml")
    # A server that declares nothing is usually serving a page, and old static
    # hosts do it often enough that refusing those would cost real pages.
    assert crawl_module.looks_like_html("")
    assert not crawl_module.looks_like_html("application/x-bzip2")
    assert not crawl_module.looks_like_html("application/rss+xml")


# --------------------------------------------------------------------------
# What spends a page slot
# --------------------------------------------------------------------------

def test_a_response_that_was_never_a_page_spends_no_page_slot(tmp_path):
    """Eight of sixteen slots went to archives, so eight pages with prose on
    them were never requested. The ceiling is on pages."""
    snapshot, _ = _crawl(tmp_path, max_pages=len(_PAGES))
    read = [p for p in snapshot["pages"] if not p.get("not_a_page")]
    assert len(read) == len(_PAGES), \
        "a non-page took a slot from a page: {}".format([p["url"] for p in read])
    titles = {p.get("title") for p in read}
    assert set(_PAGES.values()) <= titles


# --------------------------------------------------------------------------
# What the ratio divides by
# --------------------------------------------------------------------------

def _snapshot(pages):
    return {"pages": pages, "origin": "https://example.test", "site": "example.test",
            "crawl": {"pages_crawled": len(pages)}}


def _readable(url):
    return {"url": url, "final_url": url, "status": 200, "page_type": "other",
            "body_text": "This page states a fact a reader could act on today."}


def _archive(url):
    return {"url": url, "final_url": url, "status": 200, "page_type": "other",
            "skipped": crawl_module.NOT_A_PAGE_REASON, "not_a_page": True,
            "content_type": "application/x-bzip2", "body_text": ""}


def test_a_download_is_not_a_url_the_crawler_was_refused():
    """Seven pages read and nine downloads reached is a crawl that read all
    seven pages it found, not one that was shut out of nine."""
    snapshot = _snapshot([_readable("https://example.test/p%d" % i) for i in range(7)]
                         + [_archive("https://example.test/r%d.tar.bz2" % i)
                            for i in range(9)])
    assert compose.readable_counts(snapshot) == (7, 7)
    assert compose.readable_share(snapshot) == 1.0


def test_a_url_the_crawler_could_not_read_stays_in_the_denominator():
    """The share exists to catch these. A 403, a challenge, a body that could
    not be decompressed and a 200 carrying nothing are all page-shaped URLs the
    audit asked for and did not get, and dropping any of them would disarm the
    guard that stops an absence claim on a site nobody was allowed to read."""
    snapshot = _snapshot([
        _readable("https://example.test/a"),
        {"url": "https://example.test/b", "status": 403},
        {"url": "https://example.test/c", "status": 200, "challenge": True},
        {"url": "https://example.test/d", "status": 200, "body_text": ""},
        {"url": "https://example.test/e", "status": 200,
         "skipped": "the response arrived with Content-Encoding: br",
         "undecoded_encoding": "br"},
    ])
    assert compose.readable_counts(snapshot) == (5, 1)


def test_a_refusal_carrying_a_json_body_is_still_a_refusal():
    """Only a 2xx may be judged by its content type. An edge that refuses this
    crawler with a JSON error body refused a page, and the denominator that
    exists to notice refusals must not lose it to a content-type test."""
    snapshot = _snapshot([
        _readable("https://example.test/a"),
        {"url": "https://example.test/b", "status": 403,
         "skipped": crawl_module.NOT_A_PAGE_REASON,
         "content_type": "application/json"},
    ])
    assert compose.readable_counts(snapshot) == (2, 1)


# --------------------------------------------------------------------------
# What the report then says
# --------------------------------------------------------------------------

def _skill_result(findings):
    return {"skill": "structured-data-audit", "checks_run": ["c"],
            "findings": findings, "not_applicable": [], "signals": {}}


def _finding(title):
    return {"id_hint": "no-org", "title": title, "severity": "medium",
            "confidence": "high", "mechanism": "B", "root_cause": "no-org-schema",
            "evidence": "Read off every page the crawl read.",
            "affected_pages": [], "affected_page_count": 0,
            "checked": ["the markup"],
            "suggested_action": {"summary": "Add it.", "effort": "low",
                                 "owner": "developer",
                                 "how_to_fix": ["Add the block."],
                                 "rationale": "Because a machine reads it."}}


def test_the_eight_findings_come_back():
    """Each of them is answerable from fields already in the snapshot on the
    pages that were read. Nothing about the downloads changes that."""
    snapshot = _snapshot([_readable("https://example.test/p%d" % i) for i in range(7)]
                         + [_archive("https://example.test/r%d.tar.bz2" % i)
                            for i in range(9)])
    report = compose.compose(
        snapshot, [_skill_result([_finding("No page carries Organization markup")])],
        audited_at="2026-01-01T00:00:00Z")
    assert [f["title"] for f in report["findings"]] == [
        "No page carries Organization markup"]
    assert report["unverifiable"] == []


def test_the_held_back_reason_names_what_the_crawl_recorded():
    """"the rest were refused or challenged" was asserted with no test for
    either, on a crawl where every URL answered 200. The sentence counts the
    reasons the crawl itself recorded instead."""
    snapshot = _snapshot([_readable("https://example.test/a")]
                         + [{"url": "https://example.test/b%d" % i, "status": 503}
                            for i in range(5)])
    _, held = compose.withhold_absence_claims(
        [_finding("The site never states a contact method")], snapshot)
    assert len(held) == 1
    reason = held[0]["reason"]
    assert "1 of the 6" in reason
    assert "5 answered HTTP 503" in reason
    assert "refused" not in reason and "challenge" not in reason


def test_coverage_cannot_hold_back_a_claim_and_award_a_clean_bill():
    """The contradiction, stated as one assertion: a run that is not allowed to
    say something is absent is not allowed to say nothing is wrong."""
    snapshot = _snapshot([_readable("https://example.test/p%d" % i) for i in range(7)]
                         + [{"url": "https://example.test/q%d" % i, "status": 403}
                            for i in range(9)])
    report = compose.compose(
        snapshot, [_skill_result([_finding("The site never states a contact method")])],
        audited_at="2026-01-01T00:00:00Z")
    assert report["unverifiable"], "the absence claim was published on 7 of 16"
    verdict = report["summary"]["verdict"]
    assert "No blocking" not in verdict
    assert "has not seen enough of this site to judge it" in verdict
    assert report["crawl"]["enough_to_judge"] is False


def test_a_crawl_that_read_what_it_reached_is_still_graded():
    """The rule must not swallow every site. Sixteen URLs, seven of them pages
    and nine of them downloads, is a crawl that read everything readable."""
    snapshot = _snapshot([_readable("https://example.test/p%d" % i) for i in range(7)]
                         + [_archive("https://example.test/r%d.tar.bz2" % i)
                            for i in range(9)])
    report = compose.compose(snapshot, [_skill_result([])],
                             audited_at="2026-01-01T00:00:00Z")
    assert report["crawl"]["enough_to_judge"] is True
    assert "has not seen enough" not in report["summary"]["verdict"]


def test_the_snapshot_is_still_json(tmp_path):
    """A record for a declined body carries a content type and a byte count and
    nothing binary, so the snapshot every sub-skill loads still parses."""
    snapshot, _ = _crawl(tmp_path)
    with open(str(tmp_path / "snapshot.json"), encoding="utf-8") as handle:
        reloaded = json.load(handle)
    assert len(reloaded["pages"]) == len(snapshot["pages"])
