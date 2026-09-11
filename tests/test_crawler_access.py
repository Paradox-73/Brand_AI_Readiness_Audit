# -*- coding: utf-8 -*-
"""What the crawler is allowed to fetch, and what a refusal is allowed to mean.

Everything here decides one of two things before a single check runs: which
URLs this audit may request, and what it may conclude when a request does not
come back with a page. Both have produced findings that were not merely wrong
but harmful, so they are held together rather than beside the checks that
consume them.

  a stray `User-agent: Fetch` line     a crawler that was never blocked,
                                       reported as disallowed from the site
  eleven `-preprod` and `-dev` paths   "robots.txt disallows paths that look
                                       like real content", on a broadcaster's
                                       staging copies
  `/robots.txt` on the bare domain     "No XML sitemap is available", about a
                                       site whose valid sitemap index sits one
                                       redirect away on `www`
  every page answered 403              three sites told to allow-list crawler
                                       user agents, on edges that refused the
                                       request whatever name it carried
  a request for `/llms.txt` refused    a recommendation to publish the file the
                                       site may already have
  ten to forty seconds per page        "the site is unreachable", with a fix
                                       reading "read the server log for the
                                       failing request", on a site that was up

The rule underneath all of them: a refusal, a redirect and a disallow line are
observations about this run, and a sentence about the site may not claim more
than the observation supports.

The last section is the read-only guarantee itself. A file extension and a
query string once walked straight through the one function that keeps this
audit from changing anything on a site it visits.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    SkillResult, detect_challenge, is_forbidden_path, make_soup,
    response_timing, sitemap_scope, sitemap_total_phrase, slow_origin_note,
    third_party_allows,
)
from page_extract import _meta_refresh, _meta_tags  # noqa: E402
from robots_parser import benign_disallow, is_disallowed, parse_robots  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_crawler_access")


# --------------------------------------------------------------------------
# A robots.txt token matches by prefix, never by substring
# --------------------------------------------------------------------------

FETCH_ROBOTS = parse_robots(
    "User-agent: Fetch\nDisallow: /\n\nUser-agent: *\nAllow: /\n")


def test_a_stray_token_inside_a_crawler_name_does_not_block_it():
    """A broadcaster ships a legacy blocklist entry reading `User-agent: Fetch`.

    Because "fetch" occurs inside "Meta-ExternalFetcher", the report said that
    crawler was disallowed from the whole site. The group that governs it there
    is `*`, which disallows nothing, so the finding was about a rule doing
    nothing to a crawler that was never blocked.

    Substring matching cannot be tuned safe: "bot" is inside almost every
    crawler name, and a shorter stray token captures more of them.
    """
    assert not is_disallowed(FETCH_ROBOTS, "Meta-ExternalFetcher", "/")
    assert not is_disallowed(FETCH_ROBOTS, "ChatGPT-User", "/")
    # The agent the rule names is still blocked, and so is a longer name that
    # starts with the token: prefix matching is what RFC 9309 specifies.
    assert is_disallowed(FETCH_ROBOTS, "Fetch", "/")
    assert is_disallowed(FETCH_ROBOTS, "Fetcher", "/")


# --------------------------------------------------------------------------
# Which disallowed paths are worth reporting
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rule", ["/26657478", "/services", "/recommendations/products"])
def test_platform_internals_are_not_real_content(rule):
    """`/services` was the number one fix on a shop's report. It returns 404,
    and the shop's own robots.txt calls it platform-internal in a comment
    directly above it - a comment robots parsers drop.
    """
    assert benign_disallow(rule) is True


@pytest.mark.parametrize("rule", ["/club-preprod", "/magazine-dev", "/staging", "/uat/"])
def test_a_disallowed_staging_copy_is_not_blocked_content(rule):
    """All eleven "paths that look like real content" in one broadcaster's
    report were -preprod, -dev or -test. Keeping a staging copy out of an index
    is the reason robots.txt exists, and opening it up would put unfinished
    pages into answers about the brand."""
    assert benign_disallow(rule)


@pytest.mark.parametrize("rule", [
    "/development-services", "/testing", "/legacy-giving", "/archive",
])
def test_a_word_with_a_second_meaning_is_not_an_environment(rule):
    """A word that also names a real section turns content into plumbing, which
    is the more expensive mistake - so development, testing, legacy, demo and
    mirror are deliberately left out."""
    assert not benign_disallow(rule)


@pytest.mark.parametrize("rule,benign", [
    ("/api/commerce/healthcheck/", True),
    ("/healthz", True),
    # A bare `health` or `status` would swallow these, and on an insurance site
    # or a gallery they are the content.
    ("/healthcare", False),
    ("/statuses-of-liberty", False),
])
def test_a_health_endpoint_is_excluded_and_a_health_section_is_not(rule, benign):
    """One report excluded `/api/commerce/healthcheck/` under the machine-file
    rule and then listed `/healthcheck.html` as a path that looks like real
    content, in the same finding."""
    assert benign_disallow(rule) is benign


@pytest.mark.parametrize("rule,benign", [
    ("/checkouts/", True),      # a hosted store platform writes the plural
    ("/orders", True),
    ("/wishlist", True),
    ("/bags", False),           # a handbag shop's product listing
    ("/customer-stories", False),
    ("/user-guides", False),
])
def test_a_plural_checkout_is_as_benign_as_a_singular_one(rule, benign):
    """Excusing `/checkout` and flagging `/checkouts/` put "robots.txt
    disallows paths that look like real content" second in Start here on every
    storefront on one platform, telling shopkeepers to open their checkout."""
    assert benign_disallow(rule) is benign


def test_the_robots_all_clear_says_what_it_set_aside():
    """A hospital's appendix said the only disallowed paths were the standard
    admin and cart routes. Its robots.txt closes a department's patient-guide
    documents and all of its video content to every crawler; those rules were
    filtered out before the sentence was written."""
    result = CA.SkillResult("crawl-access-audit")
    robots = {"status": 200, "groups": [{"agents": ["*"], "disallow": [
        "/-/media/orthopaedic/documents/patient-guides/", "/health/video-archive"],
        "allow": [], "crawl_delay": None}], "sitemaps": [], "errors": []}
    CA._check_robots_blocks(result, robots, "https://example.com")
    reason = next(r["reason"] for r in result.not_applicable
                  if r["check"] == "robots-blocks-content-paths")
    assert "set aside" in reason
    assert "unverified" in reason
    assert "the only disallowed paths are" not in reason


# --------------------------------------------------------------------------
# Every robots directive on the page, not the first
# --------------------------------------------------------------------------

def test_a_second_robots_tag_cannot_be_lost():
    """A theme writes `index,follow` into the head and a plugin appends
    `noindex` further down. Search engines apply the most restrictive directive
    they find; a first-wins read called the page indexable."""
    soup = make_soup('<meta name="robots" content="index,follow">'
                     '<meta name="robots" content="noindex">')
    assert "noindex" in _meta_tags(soup)["robots"]


def test_a_per_crawler_directive_is_recorded_and_other_tags_are_unchanged():
    """A `googlebot` meta tag with no `robots` tag beside it was stored and
    consulted by nothing. Accumulating robots directives must not change how
    an ordinary repeated tag reads, which is first-wins."""
    assert _meta_tags(make_soup(
        '<meta name="googlebot" content="noindex">'))["googlebot"] == "noindex"
    soup = make_soup('<meta name="description" content="first">'
                     '<meta name="description" content="second">')
    assert _meta_tags(soup)["description"] == "first"


# --------------------------------------------------------------------------
# robots.txt on somebody else's host
# --------------------------------------------------------------------------

class _Response:
    def __init__(self, body, status=200):
        self.content = body.encode("utf-8")
        self.status_code = status
        self.headers = {"content-type": "text/plain; charset=utf-8"}


class _Fetcher:
    """Answers robots.txt and records what it was asked for."""

    def __init__(self, files):
        self.files = files
        self.asked = []

    def try_get(self, url, **kwargs):
        self.asked.append(url)
        body = self.files.get(url)
        return _Response(body) if body is not None else None


def test_a_platform_that_disallows_every_client_is_not_probed():
    """`User-agent: *` then `Disallow: /` is LinkedIn's and X's real file.

    The audit's headline is "robots.txt respected", and the off-site profile
    probe never asked the platform's. Both those platforms disallow every
    automated client at `/`, so every audit of a brand with either link in its
    footer sent a request those sites refuse while the report beside it said
    otherwise.
    """
    fetcher = _Fetcher({
        "https://www.linkedin.com/robots.txt": "User-agent: *\nDisallow: /\n",
    })
    assert third_party_allows(
        fetcher, "https://www.linkedin.com/company/acme", {}) is False


def test_a_platform_that_allows_the_path_is_probed():
    """A host with no robots.txt at all permits everything, which is what the
    standard says and what every crawler does."""
    fetcher = _Fetcher({
        "https://github.com/robots.txt": "User-agent: *\nDisallow: /*/*/pulse\n",
    })
    assert third_party_allows(fetcher, "https://github.com/acme", {}) is True
    assert third_party_allows(_Fetcher({}), "https://example.test/acme", {}) is True


# --------------------------------------------------------------------------
# robots.txt is read from the host that answers
# --------------------------------------------------------------------------

def test_the_crawl_settles_on_the_host_the_homepage_redirects_to():
    """A retailer redirects `/` from the bare domain to `www`, and answers 404
    for `/robots.txt` there. robots.txt and the sitemap were both fetched from
    the host that has neither, and the report's top finding was "No XML sitemap
    is available" about a site with a valid sitemap index, referenced from a
    valid robots.txt, one hop away.
    """
    import crawl as crawl_module

    class _Redirected(object):
        url = "https://www.x.test/"

    class _RedirectingFetcher(object):
        def try_get(self, url, method=None, **kwargs):
            return _Redirected()

    notes = []
    origin, seed, off_site = crawl_module._settle_origin(
        _RedirectingFetcher(), "https://x.test", "https://x.test/", notes)
    assert off_site is None
    assert origin == "https://www.x.test"
    assert seed == "https://www.x.test/"
    assert notes and "redirects to" in notes[0]


def test_the_crawl_never_follows_a_redirect_onto_another_site():
    """Auditing whatever the homepage redirects to, without that check, would
    report somebody else's site under this one's name."""
    import crawl as crawl_module

    class _Elsewhere(object):
        url = "https://somewhere-else.test/"

    class _WanderingFetcher(object):
        def try_get(self, url, method=None, **kwargs):
            return _Elsewhere()

    notes = []
    origin, seed, off_site = crawl_module._settle_origin(
        _WanderingFetcher(), "https://x.test", "https://x.test/", notes)
    # Off-site, so the move is handed back as a record for the report rather
    # than made silently, and nothing goes in `notes`.
    assert off_site is not None and off_site.get("followed") is not True
    assert notes == []


def test_an_unreachable_homepage_leaves_the_host_alone():
    import crawl as crawl_module

    class _SilentFetcher(object):
        def try_get(self, url, method=None, **kwargs):
            return None

    notes = []
    origin, seed, off_site = crawl_module._settle_origin(
        _SilentFetcher(), "https://x.test", "https://x.test/", notes)
    assert origin == "https://x.test" and notes == [] and off_site is None


def test_a_meta_refresh_inside_noscript_is_not_followed():
    """A language-learning site ships one. The crawl followed it, collapsed six
    seed URLs into one legacy stub, finished with three pages instead of sixty,
    and the report told the owner to make the redirect permanent - which would
    send every visitor to the stub for good. A refresh outside `noscript` is a
    real redirect and is still followed.
    """
    fallback = ('<html><head><noscript>'
                '<meta http-equiv="refresh" content="0; url=/nojs/splash">'
                '</noscript></head><body><p>the real page</p></body></html>')
    assert _meta_refresh(make_soup(fallback), "https://example.test/") is None

    real = ('<html><head><meta http-equiv="refresh" content="0; url=/moved">'
            '</head><body></body></html>')
    assert _meta_refresh(make_soup(real), "https://example.test/") == {
        "delay": 0, "url": "https://example.test/moved"}


# --------------------------------------------------------------------------
# A total is only a total if everything was counted
# --------------------------------------------------------------------------

INDEX = [
    {"url": "https://example.com/sitemap-index.xml", "status": 200, "urls": [],
     "url_count": 0, "lastmod_count": 0, "is_index": True,
     "child_sitemaps": ["https://example.com/s-{}.xml".format(n) for n in range(12)]},
]
INDEX += [{"url": "https://example.com/s-{}.xml".format(n), "status": 200,
           "urls": [], "url_count": 100, "lastmod_count": 0, "child_sitemaps": []}
          for n in range(3)]


def test_a_partial_sitemap_total_says_that_it_is_partial():
    """A documentation site was told it publishes 7,033 sitemap entries. It
    publishes 32,820 across twelve language sitemaps, three of which were
    read, and that number was then used as a denominator twice."""
    scope = sitemap_scope(INDEX)
    assert scope["urls_counted"] == 300
    assert not scope["complete"]
    phrase = sitemap_total_phrase(scope)
    assert phrase.startswith("at least 300")
    assert "12 sub-sitemaps" in phrase and "read 3" in phrase

    # A sitemap that was read whole is stated plainly, with no hedge.
    plain = [{"url": "https://example.com/sitemap.xml", "status": 200, "urls": [],
              "url_count": 42, "lastmod_count": 0, "child_sitemaps": []}]
    assert sitemap_total_phrase(sitemap_scope(plain)) == "42 entries"


# --------------------------------------------------------------------------
# A verification page, recognised without knowing the vendor
# --------------------------------------------------------------------------

def test_a_challenge_page_from_an_unlisted_vendor_is_still_a_challenge():
    """A museum's homepage answered 429 with the title "Vercel Security
    Checkpoint" and the header `x-vercel-mitigated: challenge`. The appendix
    said no page answered with a verification page."""
    vendor = detect_challenge(
        "<html><title>Vercel Security Checkpoint</title><body>Verifying your browser</body></html>",
        "Vercel Security Checkpoint Verifying your browser",
        429, {"x-vercel-mitigated": "challenge"})
    assert vendor == "Vercel"


def test_a_javascript_shell_is_not_mistaken_for_a_challenge():
    """The single-page app's own noscript sentence is a real finding this
    audit must keep reporting, so it is deliberately not a challenge phrase -
    and a header that merely looks like one cannot outvote a page full of
    text."""
    assert detect_challenge(
        "<html><body><noscript>You need to enable JavaScript to run this app.</noscript></body></html>",
        "You need to enable JavaScript to run this app.", 200, {"server": "nginx"}) is None
    assert detect_challenge("<html><body>{}</body></html>".format("real content " * 200),
                            "real content " * 200, 200,
                            {"x-cache-mitigated": "1"}) is None


# --------------------------------------------------------------------------
# Why a refusal happened is observed, not assumed
# --------------------------------------------------------------------------

def test_a_blanket_refusal_is_not_blamed_on_the_user_agent():
    """Three sites were told to allow-list crawler user agents. On all three
    the refusal followed the request whatever name it sent, so the allow-list
    step is not earned - and neither is its opposite, which is why the first
    step is now the measurement rather than a conclusion. See
    `test_a_refusal_under_every_name_does_not_conclude_the_name_is_ignored`
    below for the confound that decides it."""
    steps = CA._refusal_steps(CA.UA_CLIENT_REFUSED)
    assert "curl" in steps[0] and "$ua" in steps[0]
    assert "allow-list the ai answer crawlers" not in " ".join(steps).lower()
    assert CA._refusal_steps(CA.UA_KEYED)[1].lower().startswith("allow-list")
    # And where nothing was tested, the first step is the comparison itself.
    untested = CA._refusal_steps(CA.UA_UNTESTED)[0].lower()
    assert "compare" in untested or "twice" in untested


def test_the_cause_note_never_names_a_cause_that_was_not_tested():
    assert "not established" in CA._refusal_cause_note(CA.UA_UNTESTED)
    assert "not established" in CA._refusal_cause_note(CA.UA_CLIENT_REFUSED)


# --------------------------------------------------------------------------
# A comparison whose arms share a confound settles nothing
# --------------------------------------------------------------------------

ORIGIN = "https://an-invented-host.test"
HOME = ORIGIN + "/"


def _reason(result, check):
    return next(entry["reason"] for entry in result.not_applicable
                if entry["check"] == check)


class _Answer:
    def __init__(self, status_code):
        self.status_code = status_code
        self.headers = {}
        self.history = []
        self.url = HOME


class _RefusesEverything:
    """One client, one answer, whatever name the request carries.

    The shape of the charity's edge that produced the finding: measured with
    plain curl it answered four crawler names HTTP 200 and three others 403,
    while every one of those names was answered 403 through this audit's HTTP
    client. Nothing this skill can send tells those two edges apart.
    """

    def __init__(self, status=403):
        self.status = status
        self.count = 0

    def try_get(self, url, **kwargs):
        self.count += 1
        return _Answer(self.status)


def test_a_refusal_under_every_name_does_not_conclude_the_name_is_ignored():
    result = SkillResult("crawl-access-audit")
    verdict = CA._probe_a_refusal(
        result, _RefusesEverything(), HOME, 403,
        list(CA.ANSWER_CRAWLERS[:2]))

    assert verdict == CA.UA_CLIENT_REFUSED
    reason = _reason(result, "bot-manager-user-agent-comparison")
    assert "not keyed to the user agent" not in reason
    assert "one HTTP client" in reason
    assert "could not be determined" in reason
    assert result.signals["refusal_cause_undetermined"] is True


def test_an_undetermined_cause_measures_before_it_recommends_a_name():
    """The allow list is earned by UA_KEYED and by nothing else.

    Handing over every answer crawler gave a charity's owner thirteen names,
    several of which that edge was already answering HTTP 200, and named none
    of the three it was refusing.
    """
    undetermined = CA._challenge_steps(["a bot manager"], CA.UA_CLIENT_REFUSED)
    assert "curl" in undetermined[1] and "$ua" in undetermined[1]
    named = [token for token in CA.ANSWER_CRAWLERS
             if token in " ".join(undetermined)]
    assert len(named) < len(CA.ANSWER_CRAWLERS)

    keyed = " ".join(CA._challenge_steps(["a bot manager"], CA.UA_KEYED))
    assert "Add an allow rule for these user agents" in keyed


def test_only_a_name_that_was_read_earns_the_title_about_crawlers():
    """A request naming a crawler is still this audit's own HTTP client."""
    snapshot = {
        "origin": ORIGIN,
        "pages": [{"url": HOME, "status": 403, "challenge": "a bot manager"}],
    }
    for verdict, claims_crawlers in ((CA.UA_KEYED, True),
                                     (CA.UA_CLIENT_REFUSED, False),
                                     (CA.UA_UNTESTED, False)):
        result = SkillResult("crawl-access-audit")
        CA._check_challenge_pages(result, snapshot, verdict)
        title = result.findings[0]["title"]
        assert ("served to crawlers" in title) is claims_crawlers, verdict


# --------------------------------------------------------------------------
# "We looked and found nothing" is not "we could not look"
# --------------------------------------------------------------------------

def _agent_files(llms_record):
    result = SkillResult("crawl-access-audit")
    CA._check_agent_files(
        result,
        {"origin": ORIGIN, "llms_txt": llms_record,
         "llms_full_txt": {"url": ORIGIN + "/llms-full.txt", "status": 404}},
        {}, None)
    return result


def test_an_unanswered_agent_file_is_unchecked_rather_than_absent():
    """The recommendation to publish `/llms.txt` reads this signal, and fired
    on a site whose request for the file was refused."""
    url = ORIGIN + "/llms.txt"
    refused = _agent_files({"url": url, "status": 429, "present": False})
    assert refused.signals["llms_txt_present"] is None
    assert "429" in refused.signals["llms_txt_unchecked_reason"]
    assert "unchecked rather than absent" in _reason(refused, "llms-txt-presence")

    disallowed = _agent_files({"url": url, "status": None, "present": False,
                               "unchecked_reason": "robots.txt disallows it for this auditor"})
    assert disallowed.signals["llms_txt_present"] is None

    answered = _agent_files({"url": url, "status": 404, "present": False})
    assert answered.signals["llms_txt_present"] is False

    published = _agent_files({"url": url, "status": 200, "present": True})
    assert published.signals["llms_txt_present"] is True


# --------------------------------------------------------------------------
# Slow is not down
# --------------------------------------------------------------------------

def _timed(*millis):
    return [{"url": "https://x.test/{}".format(i), "status": 200, "elapsed_ms": ms}
            for i, ms in enumerate(millis)]


def test_a_slow_origin_is_described_as_slow_not_as_an_outage():
    """One site answered every page in ten to forty seconds. Enough requests
    timed out that the report called it unreachable, with outage wording and a
    fix reading "read the server log for the failing request" - on a site that
    was up, serving correct HTML, and merely slow."""
    note = slow_origin_note(_timed(11000, 24000, 38000))
    assert "answers slowly rather than not at all" in note
    assert "24.0 s" in note

    # A normal origin gets no such sentence, and neither does an untimed run:
    # `--no-network` and a fully blocked site both produce no measurement, and
    # a missing measurement is not a fast one.
    assert slow_origin_note(_timed(180, 240, 310)) == ""
    assert response_timing([]) is None
    assert slow_origin_note([]) == ""


def test_the_slow_origin_finding_fires_and_says_what_it_measured():
    result = SkillResult("crawl-access-audit")
    CA._report_response_time(result, _timed(11000, 24000, 38000))
    assert [f["root_cause"] for f in result.findings] == ["slow-origin"]
    assert "24.0 s" in result.findings[0]["evidence"]
    assert result.findings[0]["severity"] == "high"


def test_how_slow_it_is_decides_the_severity():
    sluggish = SkillResult("crawl-access-audit")
    CA._report_response_time(sluggish, _timed(3200, 3400, 3900))
    assert sluggish.findings and sluggish.findings[0]["severity"] == "medium"

    fast = SkillResult("crawl-access-audit")
    CA._report_response_time(fast, _timed(180, 240, 310))
    assert not fast.findings
    assert "0.2 s" in fast.not_applicable[0]["reason"]


# --------------------------------------------------------------------------
# Guardrails. These are hard constraints in the contest brief, and two of them
# were once broken: a file extension and a query string each walked straight
# through the one function that keeps this audit read-only.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "/wp-login.php",                        # the commonest login URL on the web
    "/login.php",
    "/admin.php",
    "/checkout.aspx",
    "https://shop.test/shop/?add-to-cart=9",  # a GET that fills a basket
    "https://shop.test/?action=logout",
])
def test_a_state_changing_url_is_refused(url):
    assert is_forbidden_path(url)


@pytest.mark.parametrize("url", [
    "/logins", "/basketball", "/cartridges", "/about.php",
    "https://x.test/blog?page=2", "https://x.test/search?q=login",
])
def test_an_ordinary_url_is_still_allowed(url):
    """The guard is worthless if it also refuses a basketball club."""
    assert not is_forbidden_path(url)
