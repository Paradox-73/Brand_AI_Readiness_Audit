# -*- coding: utf-8 -*-
"""What a visitor can do next, read out of the HTML that was delivered.

Every finding this file holds is about a link, a menu, a trail or a button, and
every one of them was wrong in the same direction: the audit could not see the
thing, so the report said the page does not offer it. That is the most damaging
shape of false finding this tool makes, because the fix attached to it tells an
owner to build something they already have.

  a `mailto:` assembled by script      the bare handler path probed, 404, and
                                       "you have a broken internal link" told to
                                       the owner of all 16 pages carrying it
  a form supplied by an embedding      "this page offers a visitor nothing to
  service                              do", about a page named for its form
  a menu whose links also sit in a     "every navigation link is hidden", about
  dropdown                             a plainly visible main bar
  `data-cta-section="breadcrumbs"`     all 28 deep pages reported as showing no
                                       breadcrumb trail, with advice to add the
                                       breadcrumb they already had
  `<li><a class="sitenav-overview"     the label "sits in markup a text-only
  href="/download.html">`              reader never sees" - ordinary anchor text
                                       inside a labelled `<nav>`

The guard in each case is evidence about one page, never a blanket amnesty: a
page with nothing onward and no widget is still a dead end, a link that really
is gone is still reported, and a menu that exists only inside a hidden panel is
still hidden.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult, make_soup  # noqa: E402
from page_extract import _breadcrumb, _cta, _links, extract_page  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGAGEMENT = _module("engagement-audit", "engagement_for_engagement_tests")

HOME = "https://an-invented-host.test/"


# --------------------------------------------------------------------------
# A link whose destination is not the URL recorded for it
# --------------------------------------------------------------------------

class _StubResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _StubFetcher:
    def __init__(self, status):
        self.status = status
        self.calls = []
        self.count = 0

    def try_get(self, url, **kwargs):
        self.calls.append(url)
        self.count += 1
        return _StubResponse(self.status)


def _page(url, links):
    return {"url": url, "status": 200,
            "page_type": "home" if url == HOME else "other",
            "links": {"internal": links, "external": []}}


def test_a_link_that_only_carries_its_destination_elsewhere_is_neither_probed_nor_reported():
    """An email-obfuscation scheme rewrites the anchor into a `mailto:` once a
    script runs; what ships is a handler path plus an encoded payload in the
    fragment, and `normalise_url` drops the fragment. The audit spent a request
    on the bare path, got 404, and told the owner of 16 pages they had a broken
    internal link on every one of them.

    The real broken link in the same snapshot must still be reported, or the
    guard has swallowed the check it was meant to make honest.
    """
    protected = "https://an-invented-host.test/mail/protect"
    gone = "https://an-invented-host.test/gone"
    pages = [_page(HOME, [{"url": protected, "text": "[email protected]",
                           "fragment": "0e6f7d654e6c617b626a6b7c62676c7c6f7c7720617c69"},
                          {"url": gone, "text": "Our old brochure", "fragment": ""}])]
    snapshot = {"origin": "https://an-invented-host.test", "pages": pages,
                "crawl": {"head_supported": True}}
    result = SkillResult("engagement-audit")
    fetcher = _StubFetcher(404)
    ENGAGEMENT._check_broken_links(result, snapshot, pages, fetcher)

    assert protected not in fetcher.calls, "a URL the audit cannot interpret must not be requested"
    finding = next(f for f in result.findings if f["root_cause"] == "broken-links")
    assert protected not in finding["evidence"]
    assert gone in finding["evidence"]
    assert result.signals["link_targets_that_are_not_page_addresses"] == [protected]


def test_the_same_path_linked_plainly_somewhere_is_still_a_page_address():
    """`/docs#install` and `/docs` are one document. Refusing to probe `/docs`
    because one anchor jumped into the middle of it would drop a real target
    from the sample, so only a URL never linked on its own is set aside."""
    shared = "https://an-invented-host.test/docs"
    pages = [_page(HOME, [{"url": shared, "text": "Install", "fragment": "install"},
                          {"url": shared, "text": "Documentation", "fragment": ""}])]
    assert ENGAGEMENT._targets_that_are_not_page_addresses(pages) == {}


def test_an_address_held_where_the_snapshot_cannot_see_it_is_caught_too():
    """The second reading, for a scheme that keeps the address in a `data-`
    attribute rather than the fragment: the anchor's whole label is an address
    and its href is a page path, so the address is what the link offers."""
    handler = "https://an-invented-host.test/mail/handler"
    pages = [_page(HOME, [{"url": handler, "text": "hello@invented-host.test",
                           "fragment": ""}])]
    assert list(ENGAGEMENT._targets_that_are_not_page_addresses(pages)) == [handler]


# --------------------------------------------------------------------------
# A next step the delivered HTML does not contain
# --------------------------------------------------------------------------

HOSTED_FORM = "https://forms.invented-vendor.test/forms/?2807206-abc"


def _widget_page(url, main_external=(), **extra):
    page = {"url": url, "status": 200, "page_type": "other",
            "links": {"internal": [], "main_internal_count": 0,
                      "main_external_count": len(main_external),
                      "main_external_sample": list(main_external),
                      "external": [{"url": HOSTED_FORM, "text": "Online Form"}]},
            "scripts": {"third_party_hosts": ["forms.invented-vendor.test"]},
            "cta": {}, "forms": []}
    page.update(extra)
    return page


def test_a_link_out_of_the_main_region_is_a_next_step_wherever_it_leads():
    """A page whose whole purpose is a form supplied by an embedding service
    ships a container and a link to the hosted copy. The dead-end test counted
    internal main links only, so that page read as offering nowhere to go, and
    one was reported that way to the owner of a page named for its form. The
    link is in the delivered HTML and needs no browser to read."""
    pages = [_widget_page("https://an-invented-host.test/proposal-form/",
                          main_external=[HOSTED_FORM])]
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_dead_ends(result, pages, "static")
    assert not result.findings
    assert result.signals["pages_whose_next_step_may_be_embedded"] == []


def test_a_widget_that_leaves_no_link_at_all_is_reported_as_not_judged():
    """The other shape: the script inserts the form and the only mention of
    the service is a credit outside the main region. Nothing in the delivered
    HTML is a next step, and without a browser nothing can say whether the
    expanded page has one."""
    pages = [_widget_page("https://an-invented-host.test/proposal-form/")]
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_dead_ends(result, pages, "static")
    assert not result.findings
    reason = " ".join(s["reason"] for s in result.not_applicable)
    assert "forms.invented-vendor.test" in reason and "no browser ran" in reason
    assert result.signals["pages_whose_next_step_may_be_embedded"] == [
        "https://an-invented-host.test/proposal-form/"]


def test_a_page_with_nothing_onward_and_no_widget_is_still_a_dead_end():
    """The exclusion is evidence about one page, not a blanket amnesty for
    every audit run without a browser."""
    page = {"url": "https://an-invented-host.test/notes", "status": 200,
            "page_type": "other", "links": {"main_internal_count": 0},
            "cta": {}, "forms": [], "scripts": {}}
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_dead_ends(result, [page], "static")
    finding = next(f for f in result.findings if f["root_cause"] == "dead-end")
    assert "No browser ran" in finding["evidence"], (
        "the reader is entitled to know which document was read")


def test_a_dead_end_says_which_link_the_count_left_out():
    """The extractor drops a main-region link whose destination is only in its
    fragment, so a page whose one link is an obfuscated-email anchor now counts
    zero onward links - which is right, because that anchor leads to an address
    and not to the path it names. A reader opening the page still sees a link,
    and a report telling them there is none is one they stop believing."""
    page = {"url": "https://an-invented-host.test/notes", "status": 200,
            "page_type": "other",
            "links": {"main_internal_count": 0, "main_external_count": 0,
                      "main_external_sample": [],
                      "main_links_with_a_fragment_destination": [
                          "https://an-invented-host.test/mail/protect"]},
            "cta": {}, "forms": [], "scripts": {}}
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_dead_ends(result, [page], "static")
    finding = next(f for f in result.findings if f["root_cause"] == "dead-end")
    assert "https://an-invented-host.test/mail/protect" in finding["evidence"]
    assert "does carry" in finding["evidence"]


def test_a_page_a_browser_read_is_judged_on_what_the_browser_saw():
    """Once something has executed the scripts, the delivered HTML is no
    longer the excuse: an empty main region is then the page itself."""
    pages = [_widget_page("https://an-invented-host.test/proposal-form/",
                          content_from="rendered")]
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_dead_ends(result, pages, "rendered")
    assert any(f["root_cause"] == "dead-end" for f in result.findings)


# --------------------------------------------------------------------------
# A menu that is visible somewhere is a visible menu
# --------------------------------------------------------------------------

def test_a_menu_that_is_also_in_a_dropdown_is_not_reported_as_hidden():
    """The same link usually appears in both a visible bar and a hidden
    dropdown of the same menu. Comparing hidden links against the deduplicated
    total then said "every navigation link is hidden" about a language's own
    site, whose main bar is plainly visible - the false positive this finding
    exists to avoid making in reverse."""
    visible = "".join('<a href="/p{0}">Page {0}</a>'.format(i) for i in range(6))
    html = ("<html><body><nav>" + visible + "</nav>"
            '<div role="menu" aria-hidden="true">' + visible + "</div>"
            "<main><p>hi</p></main></body></html>")
    links = _links(make_soup(html), "https://x.test/", "https://x.test")
    assert links["nav_visible_count"] == 6
    assert links["nav_hidden_count"] == 0


def test_a_menu_that_exists_only_inside_a_hidden_panel_is_still_reported():
    html = ('<html><body><div role="dialog" aria-hidden="true"><div role="menu">'
            + "".join('<a href="/p{0}">Page {0}</a>'.format(i) for i in range(30))
            + "</div></div><main><p>hi</p></main></body></html>")
    links = _links(make_soup(html), "https://x.test/", "https://x.test")
    assert links["nav_visible_count"] == 0
    assert links["nav_hidden_count"] == 30


# --------------------------------------------------------------------------
# A breadcrumb the site does not label in `class` or `id`
# --------------------------------------------------------------------------

def test_a_breadcrumb_named_in_a_data_attribute_is_seen():
    """A restaurant group marks its trail up as a plain list, with the word
    only in `data-cta-section="breadcrumbs"` on each link. All 28 of its deep
    pages were reported as showing no breadcrumb trail, and the advice was to
    add the breadcrumb they already had."""
    html = ('<ul><li><a data-cta-section="breadcrumbs" href="/journal/">Journal</a></li>'
            '<li><span>recipes</span></li></ul>')
    assert _breadcrumb(make_soup(html), [])["visible"] is True


def test_breadcrumb_markup_is_matched_whatever_its_case():
    """`jsonld_types` is normalised to lower case, and a literal
    `"BreadcrumbList"` test against it answered False for every site. A page
    with neither a trail nor the markup still has to report none."""
    assert _breadcrumb(make_soup("<p>x</p>"), ["breadcrumblist"])["markup"] is True
    assert _breadcrumb(make_soup("<p>Just a page.</p>"), [])["any"] is False


# --------------------------------------------------------------------------
# Where a call to action actually lives
# --------------------------------------------------------------------------

def _homepage(cta, nav=()):
    return {"url": HOME, "status": 200, "page_type": "home",
            "headings": {"h1": ["Invented Name build tools"]},
            "body_text": "Invented Name is a toolkit for building things. " * 4,
            "cta": cta, "forms": [],
            "links": {"nav": list(nav), "footer": []}}


def test_a_menu_link_is_reported_as_a_menu_link():
    """The clause said the label "sits in markup a text-only reader never
    sees". The markup is `<li><a class="sitenav-overview" href="/download.html">
    Download overview</a></li>` inside a labelled `<nav>` - ordinary anchor
    text, which every text-only reader sees. `body_text` is the main region
    with the menu removed, which is the whole reason the label was not found
    in it."""
    download = "https://an-invented-host.test/download.html"
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_homepage_orientation(
        result,
        _homepage({"found": True, "text": "Download overview", "url": download,
                   "within_first_1500": False, "offset_known": False, "button_count": 0},
                  nav=[{"url": download, "text": "Download overview"}]))
    finding = next(f for f in result.findings if f["root_cause"] == "no-orientation")
    assert "site-wide menu" in finding["evidence"]
    assert "never sees" not in finding["evidence"]


def test_a_label_found_nowhere_claims_only_that_it_was_found_nowhere():
    """Not in the body copy and not in the menu either. Why it could not be
    located - an image button, a label a script assembles - is not something
    this check knows, and it used to assert one of those answers as fact."""
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_homepage_orientation(
        result,
        _homepage({"found": True, "text": "Get started", "url": "https://elsewhere.test/x",
                   "within_first_1500": False, "offset_known": False, "button_count": 0}))
    finding = next(f for f in result.findings if f["root_cause"] == "no-orientation")
    assert "could not be measured" in finding["evidence"]
    assert "never sees" not in finding["evidence"]


def _late_link(button_count):
    return {"found": True, "text": "Read over a thousand more customer stories",
            "url": HOME + "customers", "offset": 2072, "offset_known": True,
            "within_first_1500": False, "button_count": button_count}


def test_a_late_link_beside_unplaced_buttons_claims_only_the_link():
    """A project-management product's homepage: its menu, then an H1 naming
    what it is, then a `<button>` offering a three-minute video tour. The
    extractor reads `<a href>` only, so the first thing it recognised was a
    testimonials link 2,072 characters down, and the report said the homepage
    "does not tell an arriving visitor where they are or what to do next" -
    both halves, about a page with a good H1 and its next step under it."""
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_homepage_orientation(result, _homepage(_late_link(23)))
    finding = next(f for f in result.findings if f["root_cause"] == "no-orientation")
    assert "where they are" not in finding["title"]
    assert "first link worded as a call to action" in finding["evidence"]
    assert "23 buttons" in finding["evidence"] and "not measured" in finding["evidence"]
    assert finding["confidence"] == "medium"
    assert not any("H1" in step for step in finding["suggested_action"]["how_to_fix"])
    # The prose reading answers the heading half, which did not fail.
    assert finding["severity"] == "medium"
    assert "headings and markup problem" not in finding["evidence"]


def test_a_homepage_with_no_early_next_step_is_still_reported():
    """The guard is about buttons nobody placed, not an amnesty: with no
    button, no form and the only action link 2,072 characters down, the
    finding stands at full confidence."""
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_homepage_orientation(result, _homepage(_late_link(0)))
    finding = next(f for f in result.findings if f["root_cause"] == "no-orientation")
    assert finding["severity"] == "medium" and finding["confidence"] == "high"
    assert "2072 characters" in finding["evidence"]
    assert "button" not in finding["evidence"]


_TOUR_HOMEPAGE = """<html><head><title>Invented Planner</title></head><body>
<header><nav><a href="/">Invented Planner</a><a href="/pricing">Sign up free</a>
<a href="/features">Features</a><a href="/paths">Paths</a></nav></header>
<div class="intro"><ul>
<li><a href="/pricing"><span>Pricing and plans</span> - a free tier and paid upgrades</a></li>
<li><a href="/features"><span>Features</span> - simple and capable</a></li>
</ul>
<h1>The plain project planner for small teams who want fewer meetings.</h1>
<button class="video-trigger modal-trigger" data-src="/tour.mp4">
<span>Take a three minute tour of Invented Planner</span></button></div>
<div class="quotes">{quotes}
<div><a href="/customers">Check out a thousand more customer stories</a></div></div>
<main><h2>Does this sound familiar?</h2><p>{more}</p></main>
<footer><a href="/privacy">Privacy</a></footer></body></html>"""


def test_a_tour_button_under_the_heading_is_the_next_step():
    """The same shape end to end, through the extractor: the `<button>` under
    the H1 is the page's first call to action, and the late link is not."""
    quotes = "".join("<blockquote><q>We finish work sooner and clients are happier "
                     "than before, number {}.</q></blockquote>".format(n) for n in range(30))
    html = _TOUR_HOMEPAGE.format(quotes=quotes, more="Invented Planner keeps work in one place. " * 20)
    page = dict(extract_page(HOME, HOME, 200, {}, html, [], 0, 0, "seed", HOME.rstrip("/")))
    assert page["cta"]["kind"] == "button"
    assert page["cta"]["text"].startswith("Take a three minute tour")
    assert page["cta"]["within_first_1500"]
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_homepage_orientation(result, page)
    assert not [f for f in result.findings if f["root_cause"] == "no-orientation"]


def _first_cta(body):
    soup = make_soup("<html><body>{}</body></html>".format(body))
    return _cta(soup, " ".join(soup.get_text(" ").split()), HOME.rstrip("/"), HOME)


@pytest.mark.parametrize("body", [
    # The site's menu and footer are its furniture, not this page's offer.
    "<nav><button>Sign up for a trial</button></nav><main><p>Words.</p></main>",
    "<main><p>Words.</p></main><footer><button>Subscribe to the letter</button></footer>",
    # A disclosure toggle says so in markup, whatever its label.
    '<main><button aria-expanded="false">Open the filters panel</button></main>',
    # A consent notice, by its class, in any language.
    '<div class="cookie-banner"><button>Get the full experience</button></div><main>x</main>',
    # A search form's own submit.
    '<main><form role="search"><input name="q"><button>Find a branch</button></form></main>',
    # A bare control label.
    "<main><button>Search</button><button>Play video</button></main>",
])
def test_a_button_that_operates_the_site_is_not_a_call_to_action(body):
    assert not _first_cta(body)["found"]


def test_a_form_submit_is_a_call_to_action_with_no_known_position():
    """An `<input>` shows its `value`, which is not body copy: found, and
    honestly unplaced."""
    cta = _first_cta('<main><form action="/quote"><input name="a"><input name="b">'
                     '<input type="submit" value="Get a quote"></form></main>')
    assert cta["found"] and cta["kind"] == "button" and not cta["offset_known"]


def test_a_button_is_read_by_its_markup_in_any_language():
    """The verb list is English; button markup is not. The same degree of
    language-independence the link test has, and no more."""
    cta = _first_cta('<main><p>Texte.</p><button class="btn btn-primary">'
                     "Réserver une table</button></main>")
    assert cta["found"] and cta["kind"] == "button" and cta["in_main"]
    assert cta["offset_known"] and cta["within_first_1500"]


def test_a_late_link_with_buttons_read_claims_no_unmeasured_buttons():
    """Buttons are read now, so a late first call to action is a measurement
    of links and buttons both, and the caveat belongs to older snapshots only."""
    cta = dict(_late_link(23), kind="link", kinds_read=["link", "button"])
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_homepage_orientation(result, _homepage(cta))
    finding = next(f for f in result.findings if f["root_cause"] == "no-orientation")
    assert "first link or button worded as a call to action" in finding["evidence"]
    assert "not measured" not in finding["evidence"]
    assert finding["confidence"] == "high"
