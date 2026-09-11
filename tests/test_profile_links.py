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


_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "skills", "freshness-corroboration-audit", "scripts")
sys.path.insert(0, _SCRIPTS)

from conftest import SCRIPTS as ORCH_SCRIPTS  # noqa: E402

sys.path.insert(0, ORCH_SCRIPTS)

from audit_common import (  # noqa: E402
    SkillResult, VERIFIABLE_PROFILE_PLATFORMS, FetchError,
)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "freshness_check", os.path.join(_SCRIPTS, "check.py"))
freshness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freshness)


class StubResponse:
    def __init__(self, status_code, url, body=""):
        self.status_code = status_code
        self.url = url
        self.content = body.encode("utf-8")
        self.headers = {"content-type": "text/plain; charset=utf-8"}


class StubFetcher:
    """Answers with whatever the test says, and records what was asked.

    `raise_for` lets a test make one URL fail the way a timeout does, so the
    "could not be reached" branch is exercised without waiting on one.
    """

    def __init__(self, answers, raise_for=(), robots=None):
        self.answers = answers
        self.raise_for = set(raise_for)
        self.robots = robots or {}
        self.calls = []
        self.count = 0

    def get(self, url, user_agent=None, allow_redirects=True, method="GET"):
        self.calls.append((method, url))
        self.count += 1
        if url in self.raise_for:
            raise FetchError("stubbed failure")
        # Every profile probe now asks that host's own robots.txt first, so a
        # stub that answers only the profile URLs is answering half the
        # conversation. `robots` lets a test say what a platform permits;
        # unlisted hosts serve an empty file, which permits everything.
        if url.endswith("/robots.txt") and url not in self.answers:
            return StubResponse(200, url, self.robots.get(url, ""))
        status, final = self.answers[url]
        return StubResponse(status, final)

    def try_get(self, url, **kwargs):
        """The optional-probe form, which `confirm_dead` uses.

        The profile check now confirms a HEAD 404 with a GET before calling a
        profile gone - the rule `confirm_dead` exists for and that every other
        link check in the marketplace already followed. The stub answers the
        confirmation with the same status, so a profile the test says is 404 is
        still 404 to both probes.
        """
        try:
            return self.get(url, **kwargs)
        except FetchError:
            return None


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


def _run(profiles, answers, raise_for=(), allow_network=True, fetcher=True, robots=None):
    result = SkillResult("freshness-corroboration-audit")
    stub = StubFetcher(answers, raise_for, robots) if fetcher else None
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


def test_the_evidence_and_the_affected_pages_name_the_dead_url():
    answers = {LIVE["LinkedIn"]: (404, LIVE["LinkedIn"]),
               LIVE["GitHub"]: (200, LIVE["GitHub"])}
    result, _ = _run({"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]}, answers)
    evidence = result.findings[0]["evidence"]
    assert "LinkedIn" in evidence and "404" in evidence, evidence
    assert "linkedin.com/company/kestrel-instruments" in evidence, evidence
    assert "1 other profile link(s) resolved" in evidence, evidence
    assert result.findings[0]["affected_pages"] == [LIVE["LinkedIn"]]


def test_one_dead_link_is_low_and_several_is_medium():
    one = {"LinkedIn": LIVE["LinkedIn"]}
    result, _ = _run(one, {LIVE["LinkedIn"]: (404, LIVE["LinkedIn"])})
    assert result.findings[0]["severity"] == "low"

    two = {"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]}
    result, _ = _run(two, {LIVE["LinkedIn"]: (404, LIVE["LinkedIn"]),
                           LIVE["GitHub"]: (410, LIVE["GitHub"])})
    assert result.findings[0]["severity"] == "medium"


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


def test_an_answer_that_settles_nothing_is_unchecked_not_dead():
    """Facebook sends an unknown page to /login with a 200, and a request that
    times out returns nothing at all. Neither says the profile is gone."""
    url = "https://www.linkedin.com/company/kestrel-instruments/"
    result, _ = _run(
        {"LinkedIn": url},
        {url: (200, "https://www.linkedin.com/uas/login?session_redirect=%2Fcompany")})
    assert result.findings == []
    assert result.signals["profile_links_unchecked"] == ["LinkedIn"]

    result, _ = _run({"LinkedIn": LIVE["LinkedIn"], "GitHub": LIVE["GitHub"]},
                     {LIVE["GitHub"]: (200, LIVE["GitHub"])},
                     raise_for=[LIVE["LinkedIn"]])
    assert result.findings == []
    assert result.signals["profile_links_unchecked"] == ["LinkedIn"]


# --------------------------------------------------------------------------
# Budget and manners
# --------------------------------------------------------------------------

def _profile_calls(stub):
    """The probes, without the robots.txt each host is asked for first."""
    return [(method, url) for method, url in stub.calls
            if not url.endswith("/robots.txt")]


def test_only_head_requests_are_made():
    """We want the status code, not the page. HEAD is the smaller ask.

    robots.txt is the exception and has to be: a file cannot be read with HEAD,
    and reading it is what makes the probe permitted at all.
    """
    answers = {url: (200, url) for url in LIVE.values()}
    _, stub = _run(dict(LIVE), answers)
    assert {method for method, _url in _profile_calls(stub)} == {"HEAD"}
    assert {method for method, url in stub.calls
            if url.endswith("/robots.txt")} == {"GET"}


def test_at_most_one_probe_and_one_robots_request_per_platform():
    """Six profile links on three hosts must not cost six robots requests."""
    _, stub = _run(dict(LIVE), {url: (200, url) for url in LIVE.values()})
    assert len(_profile_calls(stub)) == len(LIVE) <= len(VERIFIABLE_PROFILE_PLATFORMS)
    asked = [url for _m, url in stub.calls if url.endswith("/robots.txt")]
    assert len(asked) == len(set(asked)) == len(LIVE)


def test_a_platform_that_disallows_us_is_unchecked_not_dead():
    """LinkedIn and X both answer `User-agent: *` / `Disallow: /`. Probing them
    anyway was a hole in the one promise this marketplace makes about itself,
    and a 999 or a sign-in wall from either was being read as a site defect."""
    answers = {url: (404, url) for url in LIVE.values()}
    result, stub = _run(
        dict(LIVE), answers,
        robots={"https://www.linkedin.com/robots.txt":
                "User-agent: *" + chr(10) + "Disallow: /" + chr(10)})
    probed = [url for method, url in stub.calls if method == "HEAD"]
    assert not any("linkedin.com" in url for url in probed)
    reason = " ".join(
        f["evidence"] for f in result.findings) + " " + (_skip_reason(result) or "")
    assert "robots.txt" in reason


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
