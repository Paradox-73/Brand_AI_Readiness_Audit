"""A refusal is not a dead link.

Three checks confirmed a URL existed by sending HEAD - a request that asks
whether a page is there without downloading it - and filed anything that was
not 200 as broken:

  broken-internal-links   up to high severity
  sitemap-dead-urls
  canonical-broken        high severity

Measured across eight major retail and banking homepages, three answer 403 to
HEAD while answering 200 to GET. Every one would have been reported as a dead
link, on exactly the class of bot-managed commercial site this marketplace
exists to audit.

The marketplace already knew this. `crawl-access-audit` lists 401, 403, 405,
406 and 429 as "the crawler was refused" for the bot-block check, and has since
the beginning. The link checks never got the lesson, so one marketplace meant
two different things by one status code. `link_verdict` is now the single
answer, and the crawl establishes once per site whether HEAD means anything
here at all - one request, not one per link.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ORCH = os.path.join(_ROOT, "skills", "audit-orchestrator", "scripts")
sys.path.insert(0, _ORCH)

from audit_common import REFUSED_STATUS, SkillResult, link_verdict  # noqa: E402


def _load(skill):
    scripts = os.path.join(_ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "{}_lv".format(skill.replace("-", "_")), os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engagement = _load("engagement-audit")
access = _load("crawl-access-audit")

HOME = "https://an-invented-host.test/"


# --------------------------------------------------------------------------
# The shared verdict
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (200, "alive"), (204, "alive"), (301, "alive"), (399, "alive"),
    (404, "dead"), (410, "dead"), (400, "dead"),
    (401, "unchecked"), (403, "unchecked"), (405, "unchecked"),
    (406, "unchecked"), (429, "unchecked"),
    (500, "unchecked"), (503, "unchecked"),
    (None, "unchecked"),
])
def test_the_verdict_table(status, expected):
    assert link_verdict(status) == expected


def test_every_refused_status_is_unchecked_not_dead():
    assert all(link_verdict(s) == "unchecked" for s in REFUSED_STATUS)


def test_the_bot_block_check_and_the_link_checks_agree():
    """These five codes were hard-coded in the bot-block check for months.

    If someone edits one list, this fails rather than letting the marketplace
    quietly hold two opinions again.
    """
    path = os.path.join(_ROOT, "skills", "crawl-access-audit", "scripts", "check.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "(401, 403, 405, 406, 429)" in source
    assert set(REFUSED_STATUS) == {401, 403, 405, 406, 429}


# --------------------------------------------------------------------------
# Broken internal links
# --------------------------------------------------------------------------

class StubResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class StubFetcher:
    def __init__(self, status):
        self.status = status
        self.calls = []
        self.count = 0

    def try_get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("method")))
        self.count += 1
        return StubResponse(self.status)


def _snapshot(head_supported, crawled, linked):
    pages = []
    for url, status in crawled.items():
        pages.append({
            "url": url, "status": status,
            "page_type": "home" if url == HOME else "other",
            "links": {"internal": [{"url": u, "text": "x"} for u in linked],
                      "external": []},
        })
    return {"origin": "https://an-invented-host.test", "site": "audited site",
            "pages": pages, "crawl": {"head_supported": head_supported}}


def _links(head_supported, crawled, linked, probe_status):
    snapshot = _snapshot(head_supported, crawled, linked)
    result = SkillResult("engagement-audit")
    fetcher = StubFetcher(probe_status)
    engagement._check_broken_links(result, snapshot, snapshot["pages"], fetcher, True)
    return result, fetcher


def test_a_404_link_target_is_still_reported():
    result, _ = _links(True, {HOME: 200}, ["https://an-invented-host.test/gone"], 404)
    assert any(f["root_cause"] == "broken-links" for f in result.findings)


def test_a_403_link_target_is_not_reported_as_broken():
    result, _ = _links(True, {HOME: 200}, ["https://an-invented-host.test/waf"], 403)
    assert not any(f["root_cause"] == "broken-links" for f in result.findings)


def test_a_503_link_target_is_not_reported_as_broken():
    """A server having a bad moment is not a page that was deleted."""
    result, _ = _links(True, {HOME: 200}, ["https://an-invented-host.test/deploying"], 503)
    assert not any(f["root_cause"] == "broken-links" for f in result.findings)


def test_a_site_that_refuses_head_is_not_probed_at_all():
    result, fetcher = _links(False, {HOME: 200}, ["https://an-invented-host.test/x"], 403)
    assert fetcher.calls == []
    assert not any(f["root_cause"] == "broken-links" for f in result.findings)


def test_a_site_that_refuses_head_says_so_rather_than_going_quiet():
    result, _ = _links(False, {HOME: 200}, ["https://an-invented-host.test/x"], 403)
    reasons = " ".join(s["reason"] for s in result.not_applicable)
    assert "refus" in reasons and "unchecked" in reasons


def test_unverifiable_targets_are_counted_and_reported_as_such():
    result, _ = _links(True, {HOME: 200}, ["https://an-invented-host.test/waf"], 403)
    assert result.signals["link_targets_unchecked"] >= 1


def test_unverifiable_targets_are_excluded_from_the_rate():
    """Otherwise a refused link would make the broken-link rate look worse."""
    crawled = {HOME: 200, "https://an-invented-host.test/gone": 404,
               "https://an-invented-host.test/waf": 403}
    snapshot = _snapshot(True, crawled, [])
    result = SkillResult("engagement-audit")
    engagement._check_broken_links(result, snapshot, snapshot["pages"], StubFetcher(200), True)
    finding = next(f for f in result.findings if f["root_cause"] == "broken-links")
    assert "1 of 2" in finding["evidence"]
    assert "could not be verified" in finding["evidence"]


# --------------------------------------------------------------------------
# Canonical targets
# --------------------------------------------------------------------------

def _canonicals(head_supported, canonical_status):
    page = {"url": HOME, "status": 200, "page_type": "home",
            "canonical": "https://an-invented-host.test/real"}
    target = {"url": "https://an-invented-host.test/real",
              "status": canonical_status, "page_type": "other"}
    snapshot = {"origin": "https://an-invented-host.test",
                "pages": [page, target],
                "crawl": {"head_supported": head_supported}}
    result = SkillResult("crawl-access-audit")
    access._check_canonicals(result, snapshot, [page], StubFetcher(canonical_status))
    return result


def test_a_canonical_pointing_at_a_404_is_reported():
    assert any(f["root_cause"] == "canonical-broken"
               for f in _canonicals(True, 404).findings)


def test_a_canonical_pointing_at_a_403_is_not_reported():
    assert not any(f["root_cause"] == "canonical-broken"
                   for f in _canonicals(True, 403).findings)


def test_a_canonical_pointing_at_a_503_is_not_reported():
    assert not any(f["root_cause"] == "canonical-broken"
                   for f in _canonicals(True, 503).findings)


# --------------------------------------------------------------------------
# The site-wide non-200 rate
#
# Twenty lines above it in the same function, the homepage check has always
# distinguished a bot refusal from an outage and offered a different fix for
# each. The site-wide rate did not, so on a real bot-managed retail site it
# reported "95.5% of crawled pages do not return HTTP 200" and told the owner
# to add redirects for 42 pages that work perfectly in a browser.
# --------------------------------------------------------------------------

def _rate_snapshot(statuses, good=0):
    pages = [{"url": HOME, "status": 200, "page_type": "home"}]
    for i in range(good):
        pages.append({"url": "https://an-invented-host.test/ok{}".format(i),
                      "status": 200, "page_type": "other"})
    for i, status in enumerate(statuses):
        pages.append({"url": "https://an-invented-host.test/p{}".format(i),
                      "status": status, "page_type": "other"})
    return {"origin": "https://an-invented-host.test", "pages": pages,
            "crawl": {"head_supported": True}}


def _rate(statuses, good=0):
    snapshot = _rate_snapshot(statuses, good)
    result = SkillResult("crawl-access-audit")
    ok = [p for p in snapshot["pages"] if p["status"] == 200]
    access._check_status_and_indexability(result, snapshot, snapshot["pages"], ok, None)
    return result


def _rate_finding(result):
    return next((f for f in result.findings
                 if f["title"].endswith("refuse this crawler")
                 or "do not return HTTP 200" in f["title"]), None)


def test_a_site_of_dead_pages_is_reported_as_dead_pages():
    finding = _rate_finding(_rate([404] * 9))
    assert finding is not None
    assert finding["root_cause"] == "non-200"
    assert "redirect" in finding["suggested_action"]["summary"].lower()


def test_a_site_that_refuses_the_crawler_is_reported_as_a_refusal():
    finding = _rate_finding(_rate([403] * 9))
    assert finding is not None
    assert finding["root_cause"] == "bot-manager-block"
    assert "refuse this crawler" in finding["title"]


def test_the_refusal_fix_is_about_bot_rules_not_redirects():
    finding = _rate_finding(_rate([403] * 9))
    steps = " ".join(finding["suggested_action"]["how_to_fix"]).lower()
    assert "waf" in steps or "bot" in steps
    assert "301" not in steps


def test_the_refusal_evidence_says_it_is_not_an_outage():
    finding = _rate_finding(_rate([403] * 9))
    assert "not an outage" in finding["evidence"]


def test_a_mixed_site_falls_back_to_the_dead_page_wording():
    """Only a majority of refusals changes the diagnosis."""
    finding = _rate_finding(_rate([403, 403, 404, 404, 404, 500, 404, 404, 404]))
    assert finding["root_cause"] == "non-200"


def test_a_low_rate_is_not_reported_either_way():
    result = _rate([403], good=19)   # 1 of 21 = 4.8%, under the 10% threshold
    assert _rate_finding(result) is None
    assert any("below the 10% threshold" in s["reason"] for s in result.not_applicable)
