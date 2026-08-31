"""A profile link the brand publishes has to actually lead somewhere.

Off-site profile breadth is the strongest signal this marketplace measures, and
for most of its life the check counted the links a site *declares*. A brand
listing a LinkedIn page that was deleted two years ago scored exactly the same
as one whose page is live. The headline number was a claim, not a measurement.

Verifying it is only honest where "not found" means not found. We asked each
platform for a profile that certainly does not exist:

  LinkedIn, X, YouTube, GitHub, Wikipedia, Wikidata   404 dead, 200 live
  Instagram, TikTok, Medium, Pinterest, Threads       200 for anything
  Crunchbase, Yelp, Glassdoor, Trustpilot             403 to any crawler

So six platforms are checked and the rest are left alone. Reporting a live
Instagram account as dead because Instagram answers 200 to everything would be
worse than not looking.

These tests use a stub fetcher rather than the network. A test that depends on
LinkedIn being up is a test that fails for reasons that have nothing to do with
this code, and it would also mean the suite hammers third-party sites every run.
"""

from __future__ import annotations

import os
import sys

import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "skills", "freshness-corroboration-audit", "scripts")
sys.path.insert(0, _SCRIPTS)

from conftest import SCRIPTS as ORCH_SCRIPTS  # noqa: E402

sys.path.insert(0, ORCH_SCRIPTS)

from audit_common import (  # noqa: E402
    ROOT_CAUSES, SkillResult, VERIFIABLE_PROFILE_PLATFORMS, FetchError,
)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "freshness_check", os.path.join(_SCRIPTS, "check.py"))
freshness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freshness)


class StubResponse:
    def __init__(self, status_code, url):
        self.status_code = status_code
        self.url = url


class StubFetcher:
    """Answers with whatever the test says, and records what was asked.

    `raise_for` lets a test make one URL fail the way a timeout does, so the
    "could not be reached" branch is exercised without waiting on one.
    """

    def __init__(self, answers, raise_for=()):
        self.answers = answers
        self.raise_for = set(raise_for)
        self.calls = []
        self.count = 0

    def get(self, url, user_agent=None, allow_redirects=True, method="GET"):
        self.calls.append((method, url))
        self.count += 1
        if url in self.raise_for:
            raise FetchError("stubbed failure")
        status, final = self.answers[url]
        return StubResponse(status, final)


LIVE = {
    "LinkedIn": "https://www.linkedin.com/company/kestrel-instruments/",
    "GitHub": "https://github.com/kestrel-instruments",
    "X": "https://x.com/kestrelinstr",
}
UNVERIFIABLE = {
    "Instagram": "https://www.instagram.com/kestrelinstruments/",
    "TikTok": "https://www.tiktok.com/@kestrelinstruments",
    "Trustpilot": "https://www.trustpilot.com/review/kestrel.example",
}


def _run(profiles, answers, raise_for=(), allow_network=True, fetcher=True):
    result = SkillResult("freshness-corroboration-audit")
    stub = StubFetcher(answers, raise_for) if fetcher else None
    freshness._check_profile_links_resolve(result, profiles, stub, allow_network)
    return result, stub


def _skip_reason(result, name="profile-links-resolve"):
    for entry in result.not_applicable:
        if entry["check"] == name:
            return entry["reason"]
    return None


# --------------------------------------------------------------------------
# The finding
# --------------------------------------------------------------------------

def test_a_dead_profile_link_is_reported():
    answers = {LIVE["LinkedIn"]: (404, LIVE["LinkedIn"]),
               LIVE["GitHub"]: (200, LIVE["GitHub"])}
    result, _ = _run({"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]}, answers)
    causes = [f["root_cause"] for f in result.findings]
    assert causes == ["dead-profile-link"], causes


def test_the_evidence_names_the_url_and_the_status():
    answers = {LIVE["LinkedIn"]: (404, LIVE["LinkedIn"]),
               LIVE["GitHub"]: (200, LIVE["GitHub"])}
    result, _ = _run({"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]}, answers)
    evidence = result.findings[0]["evidence"]
    assert "LinkedIn" in evidence and "404" in evidence, evidence
    assert "linkedin.com/company/kestrel-instruments" in evidence, evidence
    assert "1 other profile link(s) resolved" in evidence, evidence


def test_one_dead_link_is_low_and_several_is_medium():
    one = {"LinkedIn": LIVE["LinkedIn"]}
    result, _ = _run(one, {LIVE["LinkedIn"]: (404, LIVE["LinkedIn"])})
    assert result.findings[0]["severity"] == "low"

    two = {"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]}
    result, _ = _run(two, {LIVE["LinkedIn"]: (404, LIVE["LinkedIn"]),
                           LIVE["GitHub"]: (410, LIVE["GitHub"])})
    assert result.findings[0]["severity"] == "medium"


def test_the_root_cause_is_in_the_shared_vocabulary():
    """Every root cause must be declared once, in `audit_common`, or the
    orchestrator will reject the finding at compose time."""
    assert "dead-profile-link" in ROOT_CAUSES


def test_the_dead_urls_are_the_affected_pages():
    answers = {LIVE["LinkedIn"]: (404, LIVE["LinkedIn"]),
               LIVE["GitHub"]: (200, LIVE["GitHub"])}
    result, _ = _run({"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]}, answers)
    assert result.findings[0]["affected_pages"] == [LIVE["LinkedIn"]]


# --------------------------------------------------------------------------
# What we refuse to conclude
# --------------------------------------------------------------------------

def test_platforms_that_answer_200_to_everything_are_never_asked():
    """Instagram returns 200 for a username that has never existed. Asking it
    would produce an answer that means nothing, so we do not spend the request."""
    result, stub = _run(dict(UNVERIFIABLE), {})
    assert stub.calls == [], "no request should have been made: {}".format(stub.calls)
    assert result.findings == []
    reason = _skip_reason(result)
    assert reason and "answers honestly" in reason, reason


def test_a_403_is_recorded_as_unchecked_not_as_dead():
    """Crunchbase and Yelp refuse every crawler. A refusal is not a 404."""
    answers = {LIVE["LinkedIn"]: (403, LIVE["LinkedIn"]),
               LIVE["GitHub"]: (200, LIVE["GitHub"])}
    result, _ = _run({"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]}, answers)
    assert result.findings == [], "a 403 must never produce a dead-link finding"
    assert result.signals["profile_links_unchecked"] == ["LinkedIn"]
    assert result.signals["profile_links_alive"] == 1


def test_a_redirect_to_a_sign_in_page_is_not_an_answer():
    """Facebook sends an unknown page to /login with a 200. That says nothing
    about whether the profile exists."""
    url = "https://www.linkedin.com/company/kestrel-instruments/"
    answers = {url: (200, "https://www.linkedin.com/uas/login?session_redirect=%2Fcompany")}
    result, _ = _run({"LinkedIn": url}, answers)
    assert result.findings == []
    assert result.signals["profile_links_unchecked"] == ["LinkedIn"]


def test_a_request_that_fails_is_unchecked_not_dead():
    answers = {LIVE["GitHub"]: (200, LIVE["GitHub"])}
    result, _ = _run({"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]}, answers,
                     raise_for=[LIVE["LinkedIn"]])
    assert result.findings == []
    assert result.signals["profile_links_unchecked"] == ["LinkedIn"]


# --------------------------------------------------------------------------
# Budget and manners
# --------------------------------------------------------------------------

def test_only_head_requests_are_made():
    """We want the status code, not the page. HEAD is the smaller ask."""
    answers = {url: (200, url) for url in LIVE.values()}
    _, stub = _run(dict(LIVE), answers)
    assert {method for method, _url in stub.calls} == {"HEAD"}


def test_at_most_one_request_per_verifiable_platform():
    answers = {url: (200, url) for url in LIVE.values()}
    _, stub = _run(dict(LIVE), answers)
    assert len(stub.calls) == len(LIVE) <= len(VERIFIABLE_PROFILE_PLATFORMS)


def test_no_network_means_no_requests_and_a_stated_reason():
    result, _ = _run(dict(LIVE), {}, allow_network=False, fetcher=False)
    reason = _skip_reason(result)
    assert reason and "network" in reason, reason
    assert result.findings == []


def test_a_clean_result_says_which_links_it_checked():
    """Silence has to be explained, like every other check here."""
    answers = {url: (200, url) for url in LIVE.values()}
    result, _ = _run(dict(LIVE), answers)
    reason = _skip_reason(result)
    assert reason and "all 3 verifiable profile link(s) resolve" in reason, reason
    for platform in LIVE:
        assert platform in reason, reason


def test_a_site_with_no_profiles_at_all_declines():
    result, stub = _run({}, {})
    assert stub.calls == []
    assert _skip_reason(result) is not None


# --------------------------------------------------------------------------
# The measure the study made is left alone
# --------------------------------------------------------------------------

def test_breadth_still_counts_declared_profiles(audit):
    """The 7.2-versus-4.6 comparison was measured on declared links.

    Subtracting dead ones from the breadth count would change the measure and
    quietly invalidate the study it is compared against. Dead links get their
    own finding instead; breadth stays as it was.
    """
    result = audit("good-site")
    signals = result.findings["freshness-corroboration-audit"]["signals"]
    assert signals["profile_breadth"] == len(signals["profile_platforms"])
