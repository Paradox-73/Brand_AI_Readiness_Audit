"""Carrying a challenge vendor's code is not being refused by it.

The reported defect, verbatim from the published finding - the single
highest-leverage fix in the tree:

    "5 of 61 fetched URLs (8.2%) returned a verification page rather than
     content: HTTP 200 carrying a CAPTCHA widget (hCaptcha) and under 800
     characters of readable text."

What is at one of those URLs is the shop's real blog index: a heading, one
post with its title, date and excerpt. It is short because the blog has one
post. Fetched as `python-requests/2.31` and as Chrome it returned 206,276
bytes both times, byte-identical. There is no gating of any kind. Every
storefront on that hosted shop platform ships an hCaptcha form bundle on every
page, for comment and contact-form spam protection.

The cost was not one wrong finding. That finding also sets "Those pages are
excluded from every content check in this report", and the excluded blog post
is the one page on that site stating where the brand is based and the year it
was founded - so a second finding told the owner they state neither.

The second half of this file holds the other item: a block this audit provoked
by its own request rate is a fact about this run, not a fact about the site. A
municipal site's snapshot recorded one path of sixty-one challenged with six
words of text; re-fetching that exact URL under this audit's own user agent
returned HTTP 200 with 51,856 bytes of full content and no vendor marker. It
was published at high severity and led the report.
"""

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    CHALLENGE_MARKERS, SkillResult,
)


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_widget_versus_wall")

ORIGIN = "https://an-invented-bootmaker.test"
HOME = ORIGIN + "/"

WIDGET = "a CAPTCHA widget (hCaptcha)"
TURNSTILE = "a CAPTCHA widget (Cloudflare Turnstile)"


# --------------------------------------------------------------------------
# Page records, in the two shapes the detector cannot tell apart on its own
# --------------------------------------------------------------------------

def _content_page(path, challenge=None, status=200, internal=8, forms=()):
    """A page of the site: navigation, links to its own pages, real markup.

    `internal` is the whole point. The blog index that was set aside has a
    heading, a link to its one post and the shop's navigation bar; a page
    someone was held at the door of has no way past the door, which is what
    being refused means.
    """
    record = {
        "url": HOME + path,
        "status": status,
        "page_type": "other",
        "word_count": 46,
        "links": {"internal_count": internal, "external_count": 2,
                  "nav_visible_count": min(internal, 6),
                  "main_internal_count": 1},
        "forms": list(forms),
    }
    if challenge:
        record["challenge"] = challenge
    return record


def _interstitial(path, challenge="Cloudflare", status=200, word_count=6):
    """A page the visitor was actually refused: no way into the site at all."""
    record = {
        "url": HOME + path,
        "status": status,
        "page_type": "other",
        "word_count": word_count,
        "links": {"internal_count": 0, "external_count": 1,
                  "nav_visible_count": 0, "main_internal_count": 0},
        "forms": [],
    }
    if challenge:
        record["challenge"] = challenge
    return record


def _snapshot(pages):
    return {"origin": ORIGIN, "pages": pages, "crawl": {"head_supported": True}}


def _run(pages, ua_verdict=None, recheck=None):
    result = SkillResult("crawl-access-audit")
    CA._check_challenge_pages(result, _snapshot(pages), ua_verdict, recheck)
    return result


def _reason(result):
    return " ".join(s["reason"] for s in result.not_applicable
                    if s["check"] == "bot-manager-challenge-page")


# The run that produced the sentence at the top of this file: 56 storefront
# pages and 5 short ones, every one of them carrying the platform's hCaptcha
# form bundle, none of them refused.
HOSTED_SHOP = ([_content_page("p%d" % i) for i in range(56)]
               + [_content_page("blog/%d" % i, WIDGET) for i in range(5)])


# --------------------------------------------------------------------------
# The defect
# --------------------------------------------------------------------------

def test_a_shop_that_ships_a_spam_widget_on_every_page_is_not_walled():
    """No finding, and the reason says what was measured rather than that
    nothing was found."""
    result = _run(HOSTED_SHOP)
    assert result.findings == [], result.findings
    reason = _reason(result)
    assert "5 of 61" in reason
    assert WIDGET in reason
    assert "not a wall" in reason
    assert HOME + "blog/0" in reason


def test_the_pages_it_declines_to_report_are_named_for_the_skills_that_still_hide_them():
    """This skill can stop asserting the block. It cannot lift it: the crawl's
    own record still carries `challenge` on those URLs, so `pages_of` in the
    shared library still hides them from every content check in every other
    skill - which is how a page stating where a brand is based was excluded
    and then reported missing."""
    result = _run(HOSTED_SHOP)
    hidden = result.signals["challenge_markers_without_a_refusal"]
    assert hidden == sorted(HOME + "blog/%d" % i for i in range(5))
    assert result.signals["challenge_pages"] == 0
    assert "content checks elsewhere in this report did not read them" in _reason(result)


def test_length_is_not_the_discriminator_in_either_direction():
    """Raising the text ceiling would have been the wrong fix twice over.

    A one-post blog index is short and legitimate; an interstitial can be long
    and is still a refusal. What separates them is whether the page offers a
    way into the site.
    """
    short_and_fine = [_content_page("blog", WIDGET, internal=4)] + [
        _content_page("p%d" % i) for i in range(9)]
    assert _run(short_and_fine).findings == []

    long_and_refused = [dict(_interstitial("p%d" % i), word_count=120)
                        for i in range(6)]
    assert _run(long_and_refused).findings


@pytest.mark.parametrize("vendor", sorted(CHALLENGE_MARKERS) + [WIDGET, TURNSTILE])
def test_no_vendor_marks_a_page_that_carries_the_site(vendor):
    """Every vendor in the table has at least one marker a protected site
    ships on pages nobody was refused: Imperva's `_incapsula_resource`,
    PerimeterX's sensor, DataDome's cookie, AWS WAF's token SDK, Cloudflare's
    `/cdn-cgi/` beacon. The rule is the same for all of them."""
    pages = [_content_page("p%d" % i) for i in range(9)]
    pages.append(_content_page("w", vendor))
    assert _run(pages).findings == [], vendor


@pytest.mark.parametrize("vendor", sorted(CHALLENGE_MARKERS) + [WIDGET, TURNSTILE])
def test_every_vendor_still_fires_where_the_visitor_was_refused(vendor):
    pages = [_interstitial("p%d" % i, vendor) for i in range(6)]
    assert _run(pages).findings, vendor


# --------------------------------------------------------------------------
# What still counts as a refusal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403, 405, 406, 429, 500, 503])
def test_a_status_the_server_refused_with_stands_whatever_the_page_carries(status):
    """A page wired into its site is not a challenge - unless the server said
    it was declining to serve it."""
    pages = [_content_page("p%d" % i) for i in range(9)]
    pages.append(_content_page("w", WIDGET, status=status))
    assert _run(pages).findings, status


def test_a_form_that_posts_to_the_provider_is_the_wall_and_not_the_widget():
    """An anti-spam widget's form posts back to the site it protects; only a
    challenge posts to the provider. This is read on the form action alone and
    never on an iframe, because an hCaptcha widget on an ordinary contact form
    renders inside an iframe on the vendor's own host."""
    wall = _content_page(
        "w", WIDGET,
        forms=[{"action": "https://hcaptcha.test/challenge", "method": "post"}])
    assert _run([_content_page("p%d" % i) for i in range(9)] + [wall]).findings

    widget = _content_page(
        "c", WIDGET,
        forms=[{"action": "/contact-form", "method": "post"}])
    assert _run([_content_page("p%d" % i) for i in range(9)] + [widget]).findings == []


def test_a_record_with_no_link_data_is_left_on_the_refused_side():
    """The question only ever overturns a challenge, so the conservative
    answer where it cannot be asked is to leave the challenge standing."""
    bare = {"url": HOME + "w", "status": 202, "page_type": "other",
            "challenge": "Akamai Bot Manager"}
    assert _run([bare]).findings


def test_a_real_wall_beside_embedded_widgets_counts_only_the_wall():
    """The share the severity is decided from is the refused set, and what was
    left out of it is named in the finding rather than only in a signal."""
    pages = [_content_page("p%d" % i, WIDGET) for i in range(8)]
    pages += [_interstitial("x%d" % i, "Cloudflare", status=403) for i in range(2)]
    finding = _run(pages).findings[0]
    assert "2 of 10" in finding["evidence"]
    assert "A further 8 fetched URL(s) carry the same vendor's markup" in finding["evidence"]
    assert "Cloudflare" in finding["evidence"]


# --------------------------------------------------------------------------
# The same question, asked of a live response
# --------------------------------------------------------------------------

WIDGET_TAG = '<script src="https://hcaptcha.test/1/api.js"></script>'
# The literal marker `detect_challenge` matches. Spelled here so this test
# fails if the shared library ever stops recognising it, rather than passing
# for the wrong reason.
REAL_WIDGET_TAG = '<script src="https://hcaptcha.com/1/api.js"></script>'

NAVIGATED = ("<html><body><nav>"
             + "".join('<a href="/p{0}">Page {0}</a>'.format(i) for i in range(6))
             + "</nav><h1>Journal</h1><p>One post so far.</p>"
             + REAL_WIDGET_TAG + "</body></html>")
HELD_AT_THE_DOOR = ("<html><body><h1>Checking your browser</h1>"
                    + REAL_WIDGET_TAG + "</body></html>")


class _Response:
    """Only the four attributes `_served_challenge` reads, so this stub does
    not become a promise about whichever HTTP library sent the bytes."""

    def __init__(self, status_code, headers, content, url):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.url = url


def _response(html, status=200, headers=None, url=HOME + "blog"):
    return _Response(status, dict({"content-type": "text/html; charset=utf-8"},
                                  **(headers or {})),
                     html.encode("utf-8"), url)


def test_a_live_page_that_links_its_own_site_is_not_a_challenge():
    """`_served_challenge` is what the user-agent comparison, the name-by-name
    probe and the post-crawl recheck all read. Left uncorrected it kept the
    false positive alive through the one request that would have overturned
    it: the recheck fetched the blog index, got HTTP 200, and called that 200
    a challenge."""
    assert CA._served_challenge(_response(NAVIGATED)) is None


def test_a_live_page_with_no_way_into_the_site_still_is_one():
    assert CA._served_challenge(_response(HELD_AT_THE_DOOR)) is not None


def test_a_refusal_status_and_a_mitigation_header_each_stand_on_their_own():
    """Both are things the server or the edge chose to say about this request,
    so neither is overturned by what the body links to."""
    assert CA._served_challenge(_response(NAVIGATED, status=403)) is not None
    assert CA._served_challenge(
        _response(NAVIGATED, headers={"x-vercel-mitigated": "challenge"})) is not None


def test_the_header_reading_is_the_shared_library_s_and_not_a_second_copy():
    """A second copy of the header pattern in the skill would drift from the
    shared one in silence, so the question is asked through `detect_challenge`
    with no body at all."""
    assert CA._mitigation_header_fired({"x-vercel-mitigated": "challenge"})
    assert not CA._mitigation_header_fired({"server": "nginx"})
    assert not CA._mitigation_header_fired({})


# --------------------------------------------------------------------------
# The one request, spent where it can settle something
# --------------------------------------------------------------------------

class _FakeFetcher:
    """Records every URL asked for, so a test can assert what was spent."""

    budget_left = True
    time_left = True
    second_transport = None

    def __init__(self, answer):
        self.asked = []
        self._answer = answer

    def try_get(self, url, **kwargs):
        self.asked.append(url)
        return self._answer(url)


def test_the_post_crawl_recheck_is_not_spent_on_a_page_that_ships_a_widget():
    """Without the filter this request went to whichever marked URL sorted
    first. On the shop above that is an ordinary product page: the request was
    spent, came back HTTP 200, and `_served_challenge` called that 200 a
    challenge, so the recheck confirmed the false positive it existed to
    overturn."""
    fetcher = _FakeFetcher(lambda url: _response(NAVIGATED, url=url))
    result = SkillResult("crawl-access-audit")
    assert CA._recheck_a_refusal(result, _snapshot(HOSTED_SHOP), fetcher) is None
    assert fetcher.asked == []


def test_the_recheck_still_asks_where_something_was_refused():
    fetcher = _FakeFetcher(lambda url: _response(NAVIGATED, url=url))
    pages = HOSTED_SHOP + [_interstitial("aaa", "Cloudflare", status=403)]
    result = SkillResult("crawl-access-audit")
    verdict = CA._recheck_a_refusal(result, _snapshot(pages), fetcher)
    assert fetcher.asked == [HOME + "aaa"]
    assert verdict[0] == CA.REFUSAL_TRANSIENT


# --------------------------------------------------------------------------
# A block this run provoked is a fact about this run
# --------------------------------------------------------------------------

# One path of sixty-one, the other sixty served with content in the same crawl
# under the same user agent: the municipal site's shape.
ONE_OF_SIXTY_ONE = ([_content_page("p%d" % i) for i in range(60)]
                    + [_interstitial("z", "Imperva Incapsula")])


def test_an_unconfirmed_block_over_a_sliver_of_one_crawl_does_not_lead_the_report():
    """`compose_report.score` is severity x reach / effort and never reads
    confidence, so a finding this run could not confirm still led the report
    as long as it stayed `high`. Both come down."""
    finding = _run(ONE_OF_SIXTY_ONE).findings[0]
    assert finding["severity"] == "medium"
    assert finding["confidence"] == "medium"


def test_it_says_that_the_same_host_served_the_rest_of_the_crawl():
    evidence = _run(ONE_OF_SIXTY_ONE).findings[0]["evidence"]
    assert "answered 60 of the 61 fetched URLs with the page itself" in evidence
    assert "under this same user agent" in evidence
    assert "rate limiter this crawl's own request volume tripped" in evidence
    assert "not an established standing rule" in evidence


def test_one_request_after_the_crawl_restores_the_claim():
    """The recheck is the measurement that settles it, and it is already spent
    by `_recheck_a_refusal` - so confirming this costs nothing extra."""
    stands = (CA.REFUSAL_STANDS, HOME + "z", 403)
    finding = _run(ONE_OF_SIXTY_ONE, recheck=stands).findings[0]
    assert finding["severity"] == "high"
    assert finding["confidence"] == "high"
    assert "was refused again" in finding["evidence"]
    assert "challenge_unconfirmed_after_the_crawl" not in _run(
        ONE_OF_SIXTY_ONE, recheck=stands).signals


def test_a_wall_across_the_whole_crawl_is_still_critical_with_no_recheck():
    """Nothing here weakens the case this check was written for: where every
    URL was intercepted, no other reading is available and there is no served
    set to compare against."""
    finding = _run([_interstitial("p%d" % i) for i in range(6)]).findings[0]
    assert finding["severity"] == "critical"
    assert finding["confidence"] == "high"


def test_a_tenth_of_the_crawl_keeps_its_severity_and_loses_only_its_certainty():
    """Severity is how much of the site is hidden; confidence is how sure this
    run is that the wall stands. Above the sliver threshold only the second
    moves."""
    pages = [_content_page("p%d" % i) for i in range(8)]
    pages += [_interstitial("x%d" % i, "AWS WAF") for i in range(2)]
    finding = _run(pages).findings[0]
    assert finding["severity"] == "high"
    assert finding["confidence"] == "medium"
