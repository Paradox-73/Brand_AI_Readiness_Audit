#!/usr/bin/env python3
"""engagement-audit: does a visitor who arrives actually stay? (mechanism G)

A visitor sent by an assistant arrives mid-journey, already knowing what they
want, on a page that has no idea any of that happened. This skill checks
whether the page orients them, gives them a next step, and lets them get back.

Makes at most 40 extra read-only requests to test internal links: HEAD to
ask, and a GET only to confirm a target that HEAD says is gone.

Usage:
    python check.py --snapshot snapshot.json --out engagement-audit.findings.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))

# The marketplace's shared library: one definition of the finding schema, the
# root-cause vocabulary and the page-type detector, so six skills cannot drift
# apart on any of the three.
#
# Looked for beside this file first, then in the orchestrator. `package.py`
# writes a copy into every skill directory when it builds the submission, so a
# skill folder lifted out on its own still runs; the checkout keeps a single
# source of truth so the copies cannot diverge from it.
_SHARED_CANDIDATES = (
    _HERE,
    os.path.join(os.path.dirname(os.path.dirname(_HERE)), "audit-orchestrator", "scripts"),
)
_SHARED = next(
    (path for path in _SHARED_CANDIDATES
     if os.path.isfile(os.path.join(path, "audit_common.py"))),
    None,
)
if _SHARED is None:
    raise SystemExit(os.linesep.join([
        "Cannot find the shared library that this skill depends on.",
        "  Looked in: " + "; ".join(_SHARED_CANDIDATES),
        "",
        "This skill reads audit_common.py, which should sit either beside this",
        "file or in skills/audit-orchestrator/scripts/. Copy the whole",
        "marketplace, or rebuild the submission with package.py.",
        "",
        "To perform these checks without it, follow the Procedure section of",
        "this skill's SKILL.md by hand. It states every check in prose and",
        "produces the same findings.",
    ]))
sys.path.insert(0, _SHARED)

from audit_common import (  # noqa: E402
    confirm_dead, deadline_from_budget, DEEP_TYPES, dominant_script, example_urls, Fetcher,
    FetchError, find_prices, group_by_edition, is_listing_page, jsonld_type_names,
    language_of, letter_runs, link_verdict,
    load_snapshot, locale_editions, no_network_reason, normalise_url, ORGANISATION,
    pages_of, pct, PERSONAL_OR_ACADEMIC, plural, PROJECT, PUBLIC_BODY, PUBLICATION,
    publishing_platform, sample, site_homepage, site_kind, sitemap_scope,
    sitemap_total_phrase,
    SkillResult, truncate, USER_AGENT, where_the_template_is, who_edits_the_template,
    word_count, words_are_separated
)
from robots_parser import is_disallowed, path_of  # noqa: E402

SKILL = "engagement-audit"

# Thresholds and their reasons.
CTA_BYTE_WINDOW = 1500        # roughly the first screen of text; a next step below this is not orientation
NAV_MIN = 3                   # under 3 top-level items leaves a visitor nowhere to go.
                              # There is deliberately no upper bound: a 29-item megamenu is
                              # normal on real retail sites and costs a machine nothing
MIN_MAIN_LINKS = 1            # a cul-de-sac is a page with no way onward at all, not a page with few
# The five numbers below were measured, not reasoned, across 302 pages on 13
# real sites (open source, government, health, museum, charity, commerce).
# Two of them turned out to be unreachable: they sat so far above anything the
# web actually does that no page could ever have tripped them, which is the
# same defect as a check with no implementation - present in the report's list,
# incapable of firing.
#
#   quantity                        median      p99        max    fired at old value
#   page weight (HTML+inline)       37,510   926,112  1,258,775   0 of 302  <- 3 MB
#   third-party script hosts             1         3         10   0 of 302  <- 25
#   required form fields                 1         5         21   2 of 346
#   words per heading (long pages)    83.7       998     18,053  21 of 123

BROKEN_LINK_SAMPLE = 40
# Forty, not twenty. A site carries a median of 292 internal link targets the
# crawl does not itself fetch, so twenty was a 7% sample and the resulting
# "X% of links are broken" rate moved in steps of five points. Forty halves the
# step and costs 20 more HEAD requests, about 10 seconds of a budget with 140
# to spare.
BROKEN_LINK_HIGH = 0.2        # a fifth of links broken is a maintenance failure, not an accident

PAGE_WEIGHT_BYTES = 2_000_000
# Two megabytes, down from three. The heaviest of 302 real pages was 1.26 MB
# and the heaviest homepage we have ever measured was 1.38 MB, so 3 MB was
# 2.4x anything observed and could not fire. Two megabytes is still 1.45x the
# worst real page - this stays a marker of pathology, not of a heavy page -
# but it is now reachable. It must also stay below MAX_RESPONSE_BYTES (5 MB),
# or a page truncated at the read cap would weigh exactly the cap; a test
# asserts that ordering.

THIRD_PARTY_SCRIPT_LIMIT = 15
# Fifteen, down from twenty-five. Across 302 pages the median page loads
# scripts from one third-party host, the 99th percentile from three, and the
# worst from ten. Twenty-five was unreachable. Fifteen sits half again above
# the worst page measured, so it still means "this is unusual" rather than
# "this is a normal amount of tag manager".

CHROME_CORE_SHARE = 0.8
# Share of the pages carrying any header/footer navigation that a link must
# appear on before it counts as part of this site's core chrome. High on
# purpose: a link on four fifths of pages is site furniture, while a link on
# half of them is a section menu, and treating a section menu as the site norm
# is how a docs sidebar came to be the standard every marketing page failed.

CHROME_CORE_PRESENT = 0.5
# How much of that core a page has to carry to count as connected to the site.
# Half, not all: a checkout that keeps the logo and the help link is reachable,
# and a page that keeps none of the core is the one a visitor cannot leave.

FORM_FIELD_LIMIT = 8          # measured: fires on 2 of 346 real forms. Reachable and rare.
WALL_OF_TEXT_WORDS = 300      # measured: fires on 21 of 123 long pages (17%). Reachable and not noisy.
LONG_PAGE_WORDS = 700         # below this a page does not need internal subheadings

# How many fields a form has to offer before someone can ask a question through
# it.
#
# A retailer's `form-friction` check passed with "none of the 83 enquiry forms
# found requires more than 8 fields", on a site whose only form is a one-field
# email box in the footer. One field is a subscription, a lookup or a
# postcode-to-branch box: the visitor supplies an identifier and the site
# replies, which is not the same act as asking a question and not what a
# question about form length is about. The newsletter heuristic is a word
# match on the surrounding text and that box carried `is_newsletter: false`,
# so a second, structural reading is needed - and the same floor is already
# what `page_extract._contact_facts` uses to decide a page has a contact form.
ENQUIRY_FORM_MIN_FIELDS = 2

# What share of the pages sharing a chrome signature a form must appear on
# before it is the site's furniture rather than one page's own form.
#
# Not every page: a template often drops the footer sign-up from the checkout
# or the search results. Not a bare majority either, because half the pages of
# a site carrying the same self-posting form is a section template, and those
# really are separate forms with separate owners.
SITE_WIDE_FORM_SHARE = 0.8

# And how many pages have to share that chrome before the share above means
# anything. Four pages is enough for "on all of them" to be a pattern; on two
# it is a coincidence.
SITE_WIDE_FORM_MIN_PAGES = 4

GENERIC_H1 = frozenset({
    "welcome", "welcome!", "home", "homepage", "welcome to our website",
    "hello", "hi", "untitled", "index", "our website", "site", "main page",
    "welcome to our site", "landing page",
})

# Chrome that operates the page rather than naming a destination. "Home",
# "Cart" and "Account" were on this list and are not controls - they are places
# you can go, and the finding's own fix text asks for "at least 3 destinations
# reachable from the top level". A shop with `Home / Shop / Cart / Account /
# Contact` was reported at high severity as having a two-item menu, quoting
# only "Shop" and "Contact" back at the owner.
NAV_CONTROL_LABELS = frozenset({
    "menu", "search", "close", "open menu", "close menu", "toggle menu",
    "skip to content", "skip to main content", "skip navigation", "back",
    "sign in", "log in", "login", "sign up", "register",
    "language", "accessibility", "share", "toggle dark mode", "dark mode",
})

# Labels that name nothing at all. "Products", "Services", "Solutions" and
# "Company" were on this list and are section names, not catch-alls: they are
# what a top-level menu is supposed to contain. A seven-item menu reading
# `Products / Solutions / Customers / Resources / Pricing / Company / Contact`
# - ordinary, correct information architecture - was reported as navigation
# that "does not tell visitors what the site contains", with an evidence line
# that listed all seven destinations and then said four of them named no
# destination, and a fix telling the owner to replace "Products" with
# "Kitchen", "Bathroom", "Outdoor".
#
# What is left is words that could head any menu on any site.
GENERIC_NAV_LABELS = frozenset({
    "more", "menu", "other", "misc", "resources", "info", "information",
    "stuff", "items", "pages",
})

VAGUE_NAV_SHARE = 0.5
# One generic label among six good ones is not a navigation problem, and
# treating it as one defeated the pass on every menu with a "Resources" tab.
# The finding is about a menu a visitor cannot read, so it needs most of the
# menu to be unreadable.

# The last path segment of a page that lists other pages rather than holding
# an argument of its own. Whole segments only, so `/blog` is an index and
# `/blog/why-we-moved-to-rust` is a post.
# Share of a page's subheadings that must also be link labels on the same page
# before it is read as a list of other pages. Half: a post index whose titles
# are all links still carries the odd section heading of its own.

STOPWORDS = frozenset("""
a an the and or but for of to in on at by with from as is are was were be your our their its it
this that these those we you they what how why when where which who best top new free guide
""".split())

# Under this much delivered text, the HTML that arrived is a container the
# browser still has to fill, not a page that is missing things.
STUB_TEXT_CHARS = 300


def _delivered_stub(page):
    """True when the HTML the crawl read is a shell rather than the page.

    Every check in this skill judges a page by what is not in it, and that is
    only a statement about the site when the document read is the document a
    visitor gets. A software vendor's help app inserts its viewport tag, its
    H1 and its body text from JavaScript: six of its pages were reported as
    having no viewport tag, in a report whose own JavaScript finding said
    scripts supply most of that site's text, and a Chromium render of one of
    them shows the tag present. Seven of its pages delivered 45 to 65
    characters and the browser pass never reached them, and those same seven
    pages were counted by three findings at once.

    A page the crawl re-read from the rendered DOM is the page a visitor sees
    and is judged normally. What is excluded is a stub nobody rendered: the
    crawl marks those `render_skipped`, and where no browser ran at all, an
    empty framework mount point beside almost no text says the same thing.
    """
    if page.get("content_from") == "rendered":
        return False
    if page.get("render_skipped"):
        return True
    if (page.get("text_len") or 0) >= STUB_TEXT_CHARS:
        return False
    spa = page.get("spa_shell") or {}
    return bool(spa.get("root_selector") or spa.get("state_blobs")
                or spa.get("noscript_demands_js"))


def _is_a_way_in(page, arrived):
    """True when a short page's content is its own links to pages that arrived.

    A furniture retailer's root address is a server-rendered country picker:
    "Choose your local site to shop" and five plain links to regional
    storefronts, each a complete server-rendered site. It carries a framework
    mount point and 127 visible characters, so `_delivered_stub` set it aside
    and every homepage check here said its menu "was not read" - about a page
    whose five links are its whole content and were read. The render skill
    decides the same question with the same rule (`gateway_destinations`
    there); it is restated here because each skill has to run on its own.

    `arrived` is the set of `_orphan_key` spellings of the crawled pages that
    are not stubs. Required: at least two of the page's distinct same-site
    links reach one of them, and at least half of them do; no state blob and
    no `<noscript>` demanding JavaScript; and the links are the content - held
    inside the mount point where there is one, otherwise at least half of the
    visible text outside the `<title>`.
    """
    spa = page.get("spa_shell") or {}
    if page.get("render_skipped") or spa.get("state_blobs") or spa.get("noscript_demands_js"):
        return False
    here = {_orphan_key(page.get(field)) for field in ("url", "final_url") if page.get(field)}
    targets = {}
    for link in (page.get("links") or {}).get("internal") or []:
        if not isinstance(link, dict) or not link.get("url") or link.get("fragment"):
            continue
        key = _orphan_key(link["url"])
        if key and key not in here:
            targets.setdefault(key, (link.get("text") or "").strip())
    reached = [key for key in targets if key in arrived]
    if len(reached) < 2 or len(reached) * 2 < len(targets):
        return False
    link_text = sum(len(text) for text in targets.values())
    if not link_text:
        return False
    if spa.get("root_selector"):
        return (spa.get("root_text_len") or 0) >= link_text
    visible = (page.get("text_len") or 0) - len((page.get("title") or "").strip())
    return link_text * 2 >= visible


def _read_by_a_browser(page):
    """Did anything execute this page's scripts before the audit judged it?

    `content_from` is set where the crawl re-read the page from the rendered
    DOM; `rendered_text_len` is set where a browser opened the page even if
    the delivered HTML was kept. Either one means what this audit is looking
    at is what a visitor gets. Neither means the audit is looking at the file
    the server sent and nothing more.
    """
    return bool(page.get("content_from") == "rendered"
                or page.get("rendered_text_len") is not None)


def _off_site_next_step(page):
    """An off-site link in the page's own main region, or "".

    A hosted form, a booking system or a donation platform is a next step even
    though it is somebody else's host. The dead-end test counted internal main
    links only, so a page whose whole purpose is an embedded third-party form
    - which ships as a container plus a link to the hosted copy - read as a
    page offering nowhere to go, and one was reported that way to the owner of
    a page called "programme proposal form".

    This is observed rather than inferred: the link is in the delivered HTML,
    in the page's own content region, and needs no browser to see.

    The list arrives already filtered - the extractor drops a main-region link
    whose destination is only in its fragment - so no second filter belongs
    here. A page whose one off-site anchor is an obfuscated address is not a
    page with an off-site next step, and the count reflects that before this
    function sees it.
    """
    sample = ((page.get("links") or {}).get("main_external_sample") or [])
    return str(sample[0]) if sample else ""


def _next_step_supplied_from_elsewhere(page):
    """The third-party host this page hands its interaction to, or "".

    For the widget that leaves no link in the main region at all: the script
    inserts the form, and the only mention of the service is a footer credit
    or nothing. `_off_site_next_step` covers the case where the link is there
    to be read; this is the inference for when it is not, so its caller gates
    it on no browser having run.

    Two shapes, both of them the page naming the third party itself:

    - the page loads a script from a host it also sends the visitor to. An
      analytics or tag-manager host is loaded and never linked, so it does not
      qualify. Hostnames are compared exactly, so a social pixel on one host
      does not pair with a profile link on another. The page-wide external
      list is read unfiltered on purpose: what is being established is that
      the page names this host, and a fragment on the link cannot change which
      host it is.
    - a visible frame from another host that is not a video, audio or map
      embed, which is an application someone else is running inside the page.
    """
    host = urlparse(normalise_url(page.get("url") or "") or "").netloc.lower()
    script_hosts = {str(h).lower()
                    for h in (page.get("scripts") or {}).get("third_party_hosts") or []}
    if script_hosts:
        for link in ((page.get("links") or {}).get("external") or []):
            linked = urlparse((link or {}).get("url") or "").netloc.lower()
            if linked and linked in script_hosts:
                return linked
    for frame in page.get("iframes") or []:
        if not isinstance(frame, dict):
            continue
        if frame.get("is_invisible") or frame.get("is_video") or frame.get("is_audio") \
                or frame.get("is_map"):
            continue
        source = urlparse(frame.get("src") or "").netloc.lower()
        if source and source != host:
            return source
    return ""


def _headings_of(page):
    """Every heading the page carries, in no particular order."""
    out = []
    headings = page.get("headings") or {}
    if isinstance(headings, dict):
        for level in ("h1", "h2", "h3"):
            value = headings.get(level)
            if isinstance(value, str):
                out.append(value)
            elif value:
                out.extend(str(v) for v in value)
    for entry in page.get("heading_sequence") or []:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            out.append(str(entry[1]))
    return out


def run(snapshot, allow_network=True, time_budget=None, snapshot_path=None):
    result = SkillResult(SKILL)
    crawled = pages_of(snapshot, content_only=True)
    # Dropped here rather than inside each check, because every check below
    # asks what a page does not have, and a stub answers no to all of them at
    # once. See `_delivered_stub`.
    stub_urls = {p["url"] for p in crawled if _delivered_stub(p)}
    # A page whose content is its own links to pages that delivered their own
    # content is a way in, not a stub - see `_is_a_way_in` - and the sentences
    # below say which of the two it is. It stays out of the checks that ask
    # what a page lacks: an orientation or menu check written for a page of
    # the site, asked of a country picker, reported "the homepage does not
    # tell an arriving visitor where they are or what to do next" about a page
    # whose whole content is the next step.
    arrived = {_orphan_key(p["url"]) for p in pages_of(snapshot)
               if not _delivered_stub(p)}
    gateway_urls = {p["url"] for p in crawled
                    if p["url"] in stub_urls and _is_a_way_in(p, arrived)}
    if gateway_urls:
        result.signal("pages_that_are_gateways", sorted(gateway_urls))
    pages = [p for p in crawled if p["url"] not in stub_urls]
    result.signal("pages_not_judged_as_javascript_stubs", sorted(stub_urls))

    # Before the early return below, and over `crawled` rather than `pages`.
    # Every other check in this skill asks what a page does not have, which a
    # stub answers no to for one reason - so a stub is set aside from all of
    # them and, where every page is a stub, the whole skill declines. This
    # check asks the opposite question: what the delivered document offers a
    # machine that arrives at it. A front page that offers nothing is the
    # answer rather than a reason to decline, and the site that produced this
    # defect is precisely the one whose pages are all stubs.
    _check_front_door(
        result, snapshot,
        next((p for p in crawled if p["page_type"] == "home"), None))

    if not pages:
        reason = "no content pages returned HTTP 200"
        if stub_urls:
            # The clause after the count is written so it reads the same at one
            # page as at twenty. "The 1 content page that returned HTTP 200
            # deliver almost no HTML" is the broken grammar a published report
            # once printed.
            reason = ("{} returned HTTP 200 while delivering almost no HTML, and no browser "
                      "ran during this audit, so what a visitor sees on this site is "
                      "unknown".format(plural(len(stub_urls), "content page")))
        for name in ("homepage-orientation", "primary-navigation", "dead-end-pages",
                     "landing-page-answers", "landing-page-leads-with-the-price",
                     "onward-links-specific-to-the-page",
                     "broken-internal-links", "orphan-pages", "breadcrumb-navigation",
                     "page-headings-name-their-own-page", "too-many-top-level-headings",
                     "title-body-alignment", "consistent-site-chrome", "page-weight",
                     "intrusive-interstitials", "form-friction", "readability",
                     "mobile-viewport"):
            result.skip(name, reason)
        return result

    # The page every skill means by "the homepage", decided once in the shared
    # library: the root, unless the root is a country or language picker. A
    # retailer's report graded its `/au` storefront's heading as "what the
    # homepage leads with" while another skill graded the root.
    front = site_homepage(snapshot) or {}
    front_page = front.get("page") or {}
    by_url = {p["url"]: p for p in pages}
    home = by_url.get(front_page.get("url")) or next(
        (p for p in pages if p["page_type"] == "home"), None)
    # Why there is no homepage to judge, when there is none. "The homepage was
    # not crawled" was printed on a client-rendered site whose homepage was
    # crawled, answered 200, and was then set aside by `_delivered_stub`
    # because no browser ran - a false sentence about the crawl, standing in
    # for a true one about the page.
    stub_home = next((p for p in crawled
                      if p["page_type"] == "home" and p["url"] in stub_urls), None)

    language = language_of(snapshot)
    english = bool(language.get("prose_checks_apply"))
    result.signal("site_language", language.get("code") or "undetermined")

    render_mode = (snapshot.get("crawl") or {}).get("render_mode", "static")
    # What kind of thing this site is, read once. Used only to word the fixes
    # that name a next step, never to decide whether a finding is made - see
    # `_advice_for`.
    kind = site_kind(snapshot)
    result.signal("site_kind", kind.kind or "undetermined")
    gateway_home = stub_home if stub_home is not None and stub_home["url"] in gateway_urls \
        else None
    # A cover page is a delivered page, not a stub, so the gateway rule above
    # never sees it. See `_cover_page_links`: it is reported once, by the
    # orientation check, with everything it costs, and the menu check does not
    # describe the same page a second time.
    cover = _cover_page_links(home)
    if cover:
        result.signal("homepage_is_a_cover_page", home["url"])
    elif front.get("is_gateway") and home is not None and home is by_url.get(
            front_page.get("url")):
        # A picker leading to regional storefronts that the shared library has
        # read as one: judged as a way in, as a stub gateway is, and named.
        gateway_home, home = home, None
    _check_homepage_orientation(result, home, english, stub_home=stub_home,
                                gateway_home=gateway_home, cover=cover, kind=kind)
    _check_navigation(result, home, pages, stub_home=stub_home, gateway_home=gateway_home,
                      cover=cover)
    _check_dead_ends(result, pages, render_mode, kind)
    _check_landing_page_answers(result, pages, english, render_mode)
    _check_landing_page_leads_with_the_price(result, pages)
    _check_onward_journey(result, pages, kind)
    _check_orphans(result, snapshot, pages, stub_urls)
    _check_breadcrumbs(result, pages)
    _check_page_headings_name_the_page(
        result, pages, home, (snapshot.get("brand") or {}).get("name") or "")
    _check_one_subject_per_page(result, pages)
    _check_title_body_drift(result, pages)
    _check_chrome_consistency(result, pages)
    _check_weight_and_scripts(result, pages)
    _check_interstitials(result, pages)
    _check_forms(result, pages)
    _check_readability(result, pages)
    _check_viewport(result, pages, render_mode, publishing_platform(snapshot))

    fetcher, why_no_fetcher = None, ""
    if not allow_network:
        # Not "disabled for this run" unconditionally: see `no_network_reason`.
        # The orchestrator appends the same flag when the caller asks for it
        # and when the run has no clock left, and a report that names the flag
        # in the second case tells the reader they asked for something they
        # did not ask for.
        why_no_fetcher = no_network_reason(
            snapshot, time_budget,
            flag_reason="extra network requests were disabled for this run",
            exhausted_reason="no link could be probed")
    else:
        try:
            fetcher = Fetcher(max_requests=BROKEN_LINK_SAMPLE,
                              deadline=deadline_from_budget(time_budget))
        except FetchError as exc:
            # Not the user's choice. `Fetcher` raises here when `requests` is
            # not installed, and the report said the requests "were disabled
            # for this run" - blaming a flag nobody passed for a library
            # nobody has.
            why_no_fetcher = ("the `requests` library is not installed on this machine, so no "
                              "link could be probed ({}). That is a property of the audit "
                              "environment, not of the site".format(exc))
    _check_broken_links(result, snapshot, pages, fetcher, why_no_fetcher,
                        snapshot_path=snapshot_path)
    if fetcher is not None:
        result.extra_requests_made = fetcher.count

    result.signal("newsletter_signup", any(p.get("newsletter_signup") for p in pages))
    result.signal("has_site_search", any(p.get("has_search") for p in pages))
    return result


# --------------------------------------------------------------------------

def _sentence_case(text):
    """Upper-case the first letter and leave every other one alone.

    `str.capitalize` lower-cases the rest of the string, and the rest of this
    string is the site's own words: a homepage whose button reads "Get a quote"
    was quoted back at its owner as 'the first call to action ("get a quote")'.
    """
    return text[:1].upper() + text[1:] if text else text


def _gateway_sentence(page):
    """What a gateway homepage is, in the words both homepage checks print."""
    reachable = sorted(_pages_reachable_from(page))
    return ("the homepage at {} is a way into the site rather than a page of it: its content "
            "is {} to other addresses of this site ({}), and the pages it leads to delivered "
            "their own content. The orientation and menu checks are written for a page of the "
            "site itself and were not asked of it".format(
                page.get("url"), plural(len(reachable), "link", "links"),
                ", ".join(example_urls(reachable, 3))))


# --------------------------------------------------------------------------
# A cover page
#
# A central bank's root address redirects to a splash page: a picture, no text
# at all, and two links - one per language edition - neither of which carries
# a word. Three findings from two skills described that one page: the homepage
# does not orient a visitor, the menu has no items, the main content is in
# images. None of them called it what it is, so the owner read three separate
# jobs where there is one decision: keep a cover page, or not.
#
# A cover page is the homepage (after any redirect) delivering almost no text
# and a handful of links into the site. It is reported once, here, as a way
# into the site, with each of its costs named in that one place.
# --------------------------------------------------------------------------

# Visible characters in the whole delivered document, header and footer
# included, at or under which the page says nothing a machine can repeat. The
# same bar as a quotable passage is not needed here: this is the whole page.
COVER_PAGE_TEXT = 200
# Links into the site a cover offers. One to three: a language choice, an
# "enter" link, a pair of editions. More than that is a menu.
COVER_PAGE_MAX_LINKS = 3
# A first path segment shaped like a language or country code.
_EDITION_SEGMENT_RE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z]{2,4})?$", re.I)


def _cover_page_links(home):
    """The addresses a cover homepage leads to, or [] when it is not one.

    Not a script redirect, which is its own finding: that page has no links at
    all. Not a stub, which the gateway rule handles.
    """
    if home is None or home.get("page_type") != "home" or home.get("script_redirect"):
        return []
    if _delivered_stub(home):
        return []
    if (home.get("text_len") or 0) > COVER_PAGE_TEXT:
        return []
    reachable = sorted(_pages_reachable_from(home))
    if not 1 <= len(reachable) <= COVER_PAGE_MAX_LINKS:
        return []
    return reachable


def _report_cover_page(result, home, destinations):
    # The orientation check's finding, raised from here: registered again so
    # the finding is stamped with the check it answers.
    result.check("homepage-orientation")
    labels = {}
    for link in (home.get("links") or {}).get("internal") or []:
        if isinstance(link, dict) and link.get("url") in destinations:
            labels.setdefault(link["url"], (link.get("text") or "").strip())
    unlabelled = [url for url in destinations if not labels.get(url)]
    segments = [urlparse(url).path.strip("/").split("/")[0] for url in destinations]
    editions = (len(destinations) > 1 and len(set(segments)) == len(segments)
                and all(_EDITION_SEGMENT_RE.match(s or "") for s in segments))
    landed = home.get("final_url") or home["url"]
    where = (" (the address {} redirects to)".format(home["url"])
             if normalise_url(landed) != normalise_url(home["url"]) else "")
    label_cost = (
        "none of those links carries a word of text, so a machine cannot tell where each "
        "one leads before it follows it" if len(unlabelled) == len(destinations) else
        "{} of those links {} no text at all".format(
            len(unlabelled), "carries" if len(unlabelled) == 1 else "carry")
        if unlabelled else
        "the links are labelled {}".format(
            ", ".join('"{}"'.format(labels[url]) for url in destinations)))
    result.add(
        id_hint="homepage-is-a-cover-page",
        title="The homepage is a cover page with almost nothing on it for a machine to read",
        severity="medium", confidence="high",
        evidence="The homepage at {}{} is a cover page: its whole delivered document holds {} "
                 "of visible text and {} into the site{} - {}. It is a way into the site "
                 "rather than a page of it, and that costs three things, stated here once "
                 "rather than as three findings: an assistant that fetches the front page "
                 "to learn what this site is finds no sentence to repeat; there is no menu "
                 "on it, so a crawler entering there reaches only the {} it links; and "
                 "{}.".format(
                     landed, where,
                     plural(home.get("text_len") or 0, "character", "characters"),
                     plural(len(destinations), "link", "links"),
                     ", one for each language edition" if editions else "",
                     ", ".join(destinations),
                     plural(len(destinations), "page", "pages"), label_cost),
        mechanism="G", root_cause="no-orientation",
        summary="Make the cover page say in text what this site is and link into it with "
                "labelled links, or replace it with a server redirect to the main edition.",
        how_to_fix=[
            "Write one or two sentences on the cover page, as HTML text rather than inside "
            "a picture, saying what this site is and who runs it. That is the sentence an "
            "assistant repeats when it fetches the front page.",
        ] + ([
            "Give each link on it a text label naming where it leads - the language or the "
            "edition - as the picture's `alt` text or as words beside it.",
        ] if unlabelled else []) + [
            "Or retire the cover: answer the homepage address with a server redirect "
            "(HTTP 302 where the choice depends on the visitor's language, 301 otherwise) "
            "to the edition most visitors want, and keep `<link rel=\"alternate\" "
            "hreflang>` tags naming every edition.",
            "If the cover stays, link the main sections of each edition from it as plain "
            "`<a href>` links, so a machine entering at the front page reaches more than "
            "{}.".format(plural(len(destinations), "page", "pages")),
        ],
        effort="low", owner="developer",
        rationale="A cover page is a door, not a room. An assistant asked what this site is "
                  "reads the front page first, and finds nothing there to quote; a crawler "
                  "entering there sees only the doors.",
        affected_pages=[home["url"]],
        checked=("the visible text of the whole delivered homepage document, its header and "
                 "footer included",
                 "every `<a href>` on it that leads to another address on this site"),
    )


def _onward_links_of_a_document(home, kind):
    """How many links a personal or single-document homepage offers onward, or 0.

    0 unless the site is established to be a person's, a lab's, or one
    document and its translations. Such a site sells nothing and books
    nothing, so its next step is not a call to action but a link: to its
    source, its author, its other language editions. A one-page essay in
    twenty languages was told "no call-to-action link, button or form was
    found anywhere in the page" at high severity, a business's shape applied
    to a document.
    """
    if kind is None or not kind.is_certainly(PERSONAL_OR_ACADEMIC):
        return 0
    links = (home or {}).get("links") or {}
    return (len(links.get("internal") or []) + len(links.get("external") or [])
            or (links.get("internal_count") or 0) + (links.get("external_count") or 0))


def _check_homepage_orientation(result, home, english=True, stub_home=None,
                                gateway_home=None, cover=None, kind=None):
    """Does the homepage say where you are and what to do next?

    Two of the three signals here are English: the list of headings that say
    nothing ("welcome", "home"), and the imperative verbs that identify a call
    to action. On a site in another language both go quiet for the wrong reason,
    and "no call-to-action link was found anywhere in the page" would be a
    confident falsehood about a page with a perfectly good button on it. So on a
    non-English site this check reports only what it can actually see: whether
    there is a heading at all.
    """
    result.check("homepage-orientation")
    if home is None:
        if gateway_home is not None:
            result.skip("homepage-orientation", _gateway_sentence(gateway_home))
            return
        if stub_home is not None:
            # Crawled, answered, and set aside - a different fact from "not
            # crawled", and the only one of the two that is true here.
            #
            # This check asks whether the page orients someone who arrives.
            # Every signal it reads - the H1, the call to action, the buttons,
            # the forms - is absent from a framework mount point, so on a page
            # nobody rendered the check would answer "no orientation" without
            # having read the page a visitor sees. That the delivered HTML
            # holds nothing a machine can read is already the `js-shell`
            # finding in `render-readability-audit`, which owns mechanism C
            # and prescribes server-side rendering; repeating it here as a
            # mechanism-G finding would tell the owner to write a better
            # heading and add a call to action on a page that has both, and
            # would count one defect twice.
            result.skip("homepage-orientation",
                        "the homepage at {} delivered {} characters of HTML around an empty "
                        "framework mount point and no browser ran during this audit, so what "
                        "a visitor sees on it was not measured. That the delivered HTML "
                        "carries nothing a crawler can read is reported once, as a "
                        "JavaScript-shell finding, rather than a second time as an "
                        "orientation one".format(
                            stub_home.get("url"), stub_home.get("text_len") or 0))
            return
        result.skip("homepage-orientation", "the homepage was not crawled")
        return
    if cover:
        _report_cover_page(result, home, cover)
        return

    h1s = (home.get("headings") or {}).get("h1") or []
    cta = home.get("cta") or {}
    # A third independent next-step signal, alongside the anchor and the
    # button count. A homepage whose whole offer is an enquiry form or a quote
    # request - no call-to-action anchor, no <button>, a plain <input
    # type="submit"> - was told at high severity that it offers a visitor
    # nothing to do. The dead-end check has counted forms as a next step for
    # this exact reason; the homepage check was reading two of the three.
    # Search is excluded: a search box is how a visitor looks for the next
    # step, not the next step itself.
    next_step_forms = [f for f in (home.get("forms") or []) if not f.get("is_search")]
    # A fourth: the homepage may hand its next step to somebody else's widget.
    # The same class the dead-end check handles, and it belongs here too,
    # because "no call-to-action link, button or form was found anywhere in
    # the page" is the highest-severity sentence this skill writes and it is a
    # statement about the delivered HTML only. See
    # `_next_step_supplied_from_elsewhere`.
    supplier = "" if _read_by_a_browser(home) else _next_step_supplied_from_elsewhere(home)
    # A fifth: on a site that is one person's, or one document, a link onward
    # is the whole of what a next step is. See `_onward_links_of_a_document`.
    onward = _onward_links_of_a_document(home, kind)
    problems = []
    # The finding has two halves - where am I, and what do I do next - and its
    # title used to claim both whichever one failed. A project-management
    # product whose H1 is a full sentence naming what it is and who it suits
    # was told its homepage "does not tell an arriving visitor where they
    # are", on the strength of a call to action measured late. Each half is
    # recorded separately so the title, the summary and the fix name only
    # what failed.
    heading_problem = False
    next_step_problem = False
    # That same product's next step is a `<button>` offering a three-minute
    # video tour, directly under the H1. The extractor then read links only,
    # so the first link worded as an action - a testimonials link 2,072
    # characters down - was quoted as "the first call to action". It reads
    # buttons now and lists them in `kinds_read`; on a snapshot written before
    # that, all that is known of the buttons is how many there are, and a late
    # link beside them is not proof of a late next step.
    buttons_read = "button" in (cta.get("kinds_read") or ())
    buttons_unplaced = False

    if not h1s:
        problems.append("the homepage has no H1")
        heading_problem = True
    elif english and h1s[0].strip().lower() in GENERIC_H1:
        problems.append('the H1 is "{}", which says nothing about what the business does'.format(h1s[0]))
        heading_problem = True

    if not english:
        if not problems:
            result.skip("homepage-orientation",
                        "the homepage has an H1. Whether its wording orients a visitor, and "
                        "whether its call to action reads as one, were not judged: both tests "
                        "are English-only and this site is not in English")
            return
    elif not cta.get("found") and not cta.get("button_count") and not next_step_forms \
            and not supplier and not onward:
        problems.append("no call-to-action link, button or form was found anywhere in the page")
        next_step_problem = True
    # `cta.get("found")` guards both arms below because `within_first_1500` is
    # False when nothing was found at all, and the else arm then formatted
    # `cta["text"]`, which is "". A homepage with a good H1 and four <button>
    # elements and no call-to-action anchor was told `The first call to action
    # ("") is not in the page copy at all` - a sentence asserting that a
    # specific call to action exists and is invisible, about a page where no
    # call-to-action anchor exists to be quoted.
    elif cta.get("found") and not cta.get("within_first_1500"):
        next_step_problem = True
        if cta.get("offset_known"):
            # The sentence names what was measured, and says what was not.
            # The extractor reads button labels as well as links, and says so
            # in `kinds_read`; a snapshot written before it did lacks the
            # field, and all this check then knows of the buttons is how many
            # there are.
            if buttons_read:
                late = ('the first link or button worded as a call to action (the {} "{}") '
                        "appears {} characters into the body text".format(
                            cta.get("kind") or "link", cta.get("text"), cta.get("offset")))
            else:
                late = ('the first link worded as a call to action ("{}") appears {} '
                        "characters into the body text".format(
                            cta.get("text"), cta.get("offset")))
            if cta.get("button_count") and not buttons_read:
                buttons_unplaced = True
                late += ("; the page also carries {}, and this crawl records how many there "
                         "are but not what they say or where they sit, so whether one of them "
                         "is an earlier next step was not measured".format(
                             plural(cta.get("button_count"), "button")))
            problems.append(late)
        elif _is_site_chrome(home, cta.get("url")):
            # What "not in the page copy" actually means when the link is a
            # menu item. `body_text` is the main region with the site's
            # header, menu and footer taken out, so a call to action that
            # lives in the menu is never in it and `offset_known` is False -
            # for every site built that way, not for one.
            #
            # The sentence that used to be printed here said the label "sits
            # in markup a text-only reader never sees". A software vendor's
            # `<li><a class="sitenav-overview" href="/download.html">Download
            # overview</a></li>`, inside a labelled `<nav>`, is ordinary
            # anchor text: every text-only reader sees it. The observation is
            # that the action is standing site furniture rather than something
            # this page says, which is a real weakness and a different claim.
            problems.append('the first call to action ("{}") is a link in the site-wide '
                            "menu rather than anything this page itself says"
                            .format(cta.get("text")))
        else:
            # Neither the body copy nor the menu carries the label. The honest
            # statement is that its position could not be measured; why it
            # could not - an image button, a label assembled by a script, text
            # this audit's extractor lost - is not something this check knows,
            # and it used to assert one of those answers as fact.
            problems.append('the first call to action ("{}") does not appear in the page\'s '
                            "body copy, so how early a visitor meets it could not be measured"
                            .format(cta.get("text")))

    if not problems:
        if cta.get("found"):
            offer = 'offers "{}" within the first {} characters'.format(
                cta.get("text"), CTA_BYTE_WINDOW)
        elif cta.get("button_count"):
            # The buttons branch above. Quoting `cta["text"]` here would print
            # `offers ""`, the same empty quotation the finding used to make.
            offer = "carries {} button(s) as its next step".format(cta.get("button_count"))
        elif supplier and not next_step_forms:
            # The widget branch. Nothing was found and nothing is claimed: the
            # sentence says what stopped the check rather than passing the page
            # or failing it.
            offer = ("hands its next step to a third party it both loads a script from and "
                     "links to ({}), which no browser ran to expand during this audit, so "
                     "whether the expanded page offers one was not judged".format(supplier))
        elif onward and not next_step_forms:
            # The document branch: no call to action was looked for, because
            # none is expected, and the sentence says why and what stands in
            # for one.
            offer = ("offers {} onward - to its source, its author or its other editions - "
                     "which is the next step a site read as {} ({}) is expected to offer; a "
                     "call to action to buy, book or contact sales was not looked for".format(
                         plural(onward, "link", "links"), kind.kind, kind.why()))
        else:
            # The forms branch. Same rule: name what was actually found rather
            # than printing "carries 0 button(s)" about a page whose next step
            # is the quote form sitting in the middle of it.
            offer = "carries {} as its next step".format(
                plural(len(next_step_forms), "form"))
        result.skip("homepage-orientation",
                    'the homepage leads with the H1 "{}" and {}'.format(h1s[0], offer))
        return

    # A missing H1 belongs to whoever owns heading structure, and that is not
    # this skill. A charity's report carried "1 page has no H1" from
    # fact-extractability-audit and "The homepage has no h1" from this one, as
    # two separate medium findings about one `<h1>` tag - two entries in the
    # count, two effort estimates, one fix. Cross-skill dedup cannot merge them
    # because they are different root causes, and they are different root
    # causes for a good reason: heading structure is a markup problem and
    # orientation is a visitor problem. So this check stays quiet when the
    # heading is the only thing it has to say.
    if problems == ["the homepage has no H1"]:
        result.skip("homepage-orientation",
                    "the homepage has no H1, which is reported once as a heading-structure "
                    "finding rather than twice. Everything else this check looks at - a "
                    "heading that names the business, and a call to action inside the first "
                    "{} characters - is present".format(CTA_BYTE_WINDOW))
        return


    # A missing H1 is a markup fact, not proof that a visitor is lost. Three
    # real sites - a database engine, an operating system and a JavaScript
    # library - each drew the only high-severity finding in their whole report
    # from "the homepage has no h1", while their first screen read "SQLite is a
    # C-language library that implements a small, fast ... SQL database engine"
    # with a Download button beside it. All three were wrong, and all three led
    # the report.
    #
    # So: if the opening screen carries a sentence that says what this is, the
    # visitor is oriented whatever the heading level. The finding drops to
    # `low` and says what is actually missing.
    opening = (home.get("above_fold_text") or home.get("body_text") or "")[:1200]
    orients_in_prose = bool(re.search(
        r"\b(is|are)\s+(a|an|the)\b", opening)) and len(opening.split()) >= 12

    # The extractor reads `<a href>` only, so a homepage whose action is a
    # `<button>Get a quote</button>`, a form submit, or a JavaScript handler
    # had "no call-to-action link was found anywhere in the page" reported at
    # high severity - the top finding in the report, about a page with four
    # buttons on it. `button_count` was already being measured and read by
    # nothing.
    no_cta = (english and not cta.get("found") and not cta.get("button_count")
              and not next_step_forms and not supplier and not onward)
    # The prose reading answers the heading half only. Applied when the heading
    # was fine, it told a homepage whose only problem was a late call to action
    # that it had "a headings and markup problem".
    if orients_in_prose and not no_cta and heading_problem:
        severity = "low"
        problems.append("the opening text does explain what this is, so this is a headings "
                        "and markup problem rather than a visitor who cannot tell where they are")
    elif no_cta or not h1s:
        severity = "high" if no_cta else "medium"
    else:
        severity = "medium"

    if severity == "low":
        title = "The homepage explains itself in prose but not in its headings"
    elif heading_problem and next_step_problem:
        title = "The homepage does not tell an arriving visitor where they are or what to do next"
    elif heading_problem:
        title = "The homepage's heading does not tell an arriving visitor where they are"
    elif no_cta:
        title = "The homepage gives an arriving visitor nothing to do next"
    else:
        title = "The homepage does not show an arriving visitor what to do next in its own copy"

    result.add(
        id_hint="homepage-does-not-orient-visitors",
        title=title,
        severity=severity,
        # A late link beside buttons nobody placed is a claim about the links
        # only; the evidence says so, and the confidence says so too.
        confidence="medium" if buttons_unplaced else "high",
        # `str.capitalize` lower-cases everything after the first character, so
        # a homepage whose button reads "Get a quote" was quoted back as
        # 'the first call to action ("get a quote")'. The site's own words are
        # not ours to re-case; only the first letter of our sentence is.
        evidence="{}.".format(_sentence_case("; ".join(problems))),
        mechanism="G", root_cause="no-orientation",
        # Same rule as the title: advice for the half that failed only. "Replace
        # the H1" printed under a finding whose H1 was fine asks the owner to
        # rewrite the one part of the page that works.
        summary=("Lead with a specific H1 and put one obvious next step in the first screen."
                 if heading_problem and next_step_problem else
                 "Lead with a heading that names what this is and who it is for."
                 if heading_problem else
                 "Put one obvious next step in the first screen of the page's own copy."),
        how_to_fix=[step for step, needed in (
            # "what the business does" was written for a company. A project, a
            # public body and a village hall are none of them a business, and
            # the sentence says the same thing without the noun.
            ("Replace the H1 with a line naming what this is and who it is for.",
             heading_problem),
            # The examples used to be "See pricing" and "Book a table", offered
            # to a free command-line tool with no purchase funnel and to a
            # charity. Naming the shape rather than the sector makes the advice
            # usable by whoever is reading it.
            # A person's or a single document's site gets the document's
            # shape of next step, not a business's.
            ("Put one link onward within the first screen - the document's source, its author, "
             "or its other language editions - worded as what the reader gets there."
             if kind is not None and kind.is_certainly(PERSONAL_OR_ACADEMIC) else
             "Put one primary call to action within the first screen of content, worded as the "
             "specific thing the visitor came to do - whatever that is on this site, whether it "
             "is downloading, booking, donating, reading the documentation or seeing the prices "
             "- rather than \"Learn more\".",
             next_step_problem),
            ("Keep one primary action; competing buttons of equal weight split attention.",
             next_step_problem),
        ) if needed],
        effort="low", owner="marketing",
        rationale="A visitor sent by an assistant already knows roughly what they "
                  "want. They are checking whether this is the right place. A generic heading "
                  "and no obvious next step answers neither question, so they go back.",
        affected_pages=[home["url"]],
        # Four separate readings of the same page, so the claim does not rest
        # on the call-to-action anchor alone. That is the detector that told a
        # homepage with four <button> elements it had no call to action
        # anywhere on it.
        # The limits worth stating are vocabularies, not places: what counts as
        # a call to action is a fixed list of opening verbs and class names,
        # and a link this audit does not recognise reads to it as no link.
        checked=("the heading the homepage leads with",
                 "{} in the first {:,} characters of copy whose first word is one of the "
                 "action verbs this audit recognises (get, book, download, contact and the "
                 "like) or whose class or id contains btn, button or cta".format(
                     "links and buttons" if buttons_read else "links", CTA_BYTE_WINDOW),
                 "buttons and non-search forms anywhere on the page",
                 "the opening screen of body text"),
    )


def _check_navigation(result, home, pages, stub_home=None, gateway_home=None, cover=None):
    result.check("primary-navigation")
    if home is not None and cover:
        result.skip("primary-navigation",
                    "the homepage at {} is a cover page carrying {} into the site and no "
                    "menu, which is reported once, under homepage-orientation, with what it "
                    "costs a visitor and a machine".format(
                        home.get("url"), plural(len(cover), "link", "links")))
        return
    # A homepage that is a script sending the browser on is not a page with a
    # bad menu. It carries no links at all, and what that costs a machine is the
    # front-door finding, which prescribes the server redirect. "Give the site
    # three top-level navigation items" was printed under it about the same
    # empty page.
    if home is not None and home.get("script_redirect") and not _pages_reachable_from(home):
        result.skip("primary-navigation",
                    "the homepage at {} is a script that sends the browser to another address "
                    "and carries no link of its own, so it has no menu to judge; what that "
                    "costs a crawler is homepage-links-into-the-site's to report, and the fix "
                    "is a server redirect rather than a menu".format(home.get("url")))
        return
    if home is None:
        if gateway_home is not None:
            result.skip("primary-navigation", _gateway_sentence(gateway_home))
            return
        if stub_home is not None:
            # Same correction as in `_check_homepage_orientation`: the page was
            # crawled and set aside, and saying it was never reached is a false
            # statement about the crawl standing in for a true one about the
            # page.
            result.skip("primary-navigation",
                        "the homepage at {} delivered an empty framework mount point and no "
                        "browser ran during this audit, so the menu a visitor sees on it was "
                        "not read. Navigation is judged on the homepage rather than on "
                        "whichever page happened to be crawled first".format(
                            stub_home.get("url")))
            return
        result.skip("primary-navigation",
                    "the homepage was not reached by this crawl, and navigation is judged on the "
                    "homepage rather than on whichever page happened to be crawled first")
        return
    source = home
    nav = (source.get("links") or {}).get("nav") or []
    # `.get`, like everything else that reads a snapshot here: a snapshot
    # written by a different version of the extractor, or hand-built by a
    # caller, raised KeyError rather than reporting on what it did have.
    labels = [(link.get("text") or "").strip() for link in nav
              if (link.get("text") or "").strip()]
    # Drop interface controls. "Menu", "Search" and "Skip to content" are
    # affordances, not destinations, and counting them made a real site's
    # navigation look both larger and vaguer than it is.
    labels = [label for label in labels if label.lower() not in NAV_CONTROL_LABELS]
    unique_labels = list(dict.fromkeys(labels))
    result.signal("nav_item_count", len(unique_labels))

    # Present but hidden. A different problem from having no menu, and the two
    # fixes have nothing in common - so it gets its own finding rather than
    # being reported as an absence with advice to build what already exists.
    links = source.get("links") or {}
    hidden_nav = links.get("nav_hidden_count", 0)
    visible_nav = links.get("nav_visible_count", 0)
    # The footer is the second reading, and it is the one that decides whether
    # the claim in the rationale is true. "To a crawler this page is a dead
    # end" is a falsehood about a site whose footer repeats every top-level
    # destination in plain HTML - which is how most sites with a JavaScript
    # burger menu are built. The menu still being invisible to a person is a
    # smaller problem than the one this finding used to assert, so it is
    # reported as a smaller one.
    footer_paths = _chrome_paths(source, ("footer",))
    if hidden_nav >= NAV_MIN and visible_nav < NAV_MIN:
        crawler_stranded = len(footer_paths) < NAV_MIN
        result.add(
            id_hint="primary-navigation-is-hidden-from-crawlers",
            title="The navigation exists but is hidden until JavaScript runs",
            severity="high" if crawler_stranded else "medium", confidence="high",
            evidence="{} carries {} internal navigation link(s), and every one of them sits "
                     "inside an element marked `aria-hidden=\"true\"` or `hidden`. The links "
                     "are real HTML, already written; a reader without JavaScript - which "
                     "includes every AI crawler - is simply never shown them. {}".format(
                         source["url"], hidden_nav,
                         "The footer carries no menu either, so nothing on this page shows a "
                         "reader without JavaScript where else the site goes."
                         if crawler_stranded else
                         "The footer does carry {} internal destination(s) in plain HTML, so a "
                         "crawler is not stranded here; a person looking for the menu still "
                         "sees nothing until the script runs.".format(len(footer_paths))),
            mechanism="G", root_cause="no-orientation",
            summary="Render the primary navigation visible in the delivered HTML, and let "
                    "JavaScript collapse it rather than reveal it.",
            how_to_fix=[
                "Find the element wrapping the menu and remove the `aria-hidden=\"true\"` or "
                "`hidden` attribute from the delivered HTML.",
                "Invert the default: ship the menu open and let the script close it on small "
                "screens, so the links are present before any script runs.",
                "If the panel must stay hidden, put the same top-level links in a plain "
                "`<nav>` or in the footer as well.",
                "Check the result by fetching the page with `curl` and searching the HTML for "
                "one of the menu labels.",
            ],
            effort="medium", owner="developer",
            rationale="A hidden element is not read. The links are in the file, so nothing "
                      "needs authoring - but to a crawler this page is a dead end, and every "
                      "page it cannot reach from here is a page it never sees.",
            affected_pages=[source["url"]],
            checked=("navigation links visible in the delivered HTML",
                     "internal destinations linked from the page footer"),
        )
        return


    # Guard against our own selector failing. `chrome_signature` sweeps a wider
    # net (nav, header, footer, role=navigation). If that found a real menu but
    # `links.nav` did not, we cannot isolate the primary navigation on this
    # site, and saying so beats reporting a site as having one menu item.
    chrome_paths = (source.get("chrome_signature") or {}).get("nav_path_count", 0)
    if len(unique_labels) < NAV_MIN and chrome_paths >= 5:
        result.skip("primary-navigation",
                    "the primary navigation could not be isolated on this site: {} found "
                    "inside <nav>, but the header and footer together contain {} distinct "
                    "internal destinations, so the menu is built in a way this check cannot "
                    "read".format(plural(len(unique_labels), "link was", "links were"),
                                  chrome_paths))
        return

    # No menu markup at all is not a menu with no items. An investment
    # holding company's homepage is a table of plain links - annual reports,
    # news releases, letters, corporate governance - and a religious-studies
    # portal's links 56 distinct addresses of its own; neither uses `<nav>`,
    # `<header>` or `role=navigation`. Both were told at high severity that
    # "the primary navigation has 0 item(s)", in reports whose own appendix
    # said "the homepage links 15 other addresses of this site". Where no menu
    # markup exists, the homepage's own links to other addresses of the site
    # are its navigation, counted by the same function the appendix sentence
    # is, so the two cannot disagree. "0 items" is kept for a homepage that
    # really links nowhere.
    if not unique_labels:
        reachable = sorted(_pages_reachable_from(source))
        if len(reachable) >= NAV_MIN:
            result.skip("primary-navigation",
                        "the homepage carries no <nav>, <header> or role=navigation menu "
                        "this audit could read, and links {} other addresses of this site "
                        "from its own body ({}), which serve as its navigation".format(
                            len(reachable), ", ".join(example_urls(reachable, 3))))
            return

    vague = [label for label in unique_labels if label.lower() in GENERIC_NAV_LABELS]
    mostly_vague = bool(vague) and len(vague) > len(unique_labels) * VAGUE_NAV_SHARE

    # The site's map does not have to sit in a <nav>. Plenty of sites put four
    # items in the header and the whole index in the footer, and reading the
    # header alone reported "the primary navigation has 3 items" at high
    # severity about a page naming twenty destinations further down.
    footer_labels = [(link.get("text") or "").strip() for link in links.get("footer") or []
                     if (link.get("text") or "").strip()]
    footer_labels = [label for label in dict.fromkeys(footer_labels)
                     if label.lower() not in NAV_CONTROL_LABELS]

    if len(unique_labels) >= NAV_MIN and not mostly_vague:
        if not vague:
            result.skip("primary-navigation",
                        "the primary navigation has {} items with descriptive labels".format(
                            len(unique_labels)))
        else:
            result.skip("primary-navigation",
                        "the primary navigation has {} items, {} of which name a specific "
                        "destination; the remaining {} ({}) {} beside labels that do".format(
                            len(unique_labels), len(unique_labels) - len(vague),
                            plural(len(vague), "label"),
                            ", ".join('"{}"'.format(v) for v in vague[:4]),
                            "sits" if len(vague) == 1 else "sit"))
        return

    problems = []
    severity = "medium"
    if len(unique_labels) < NAV_MIN:
        problems.append("the primary navigation has {} item(s)".format(len(unique_labels)))
        if len(footer_labels) >= NAV_MIN:
            problems.append("though the footer names {} destination(s) the header menu does "
                            "not".format(len(footer_labels)))
        else:
            severity = "high"
    if mostly_vague:
        problems.append("labels that name no specific destination: {}".format(
            ", ".join('"{}"'.format(v) for v in vague[:4])))

    result.add(
        id_hint="primary-navigation-does-not-guide",
        title="The primary navigation does not tell visitors what the site contains",
        severity=severity, confidence="medium",
        evidence="On {}: {}. Labels found: {}.".format(
            source["url"], "; ".join(problems),
            ", ".join('"{}"'.format(x) for x in unique_labels[:9]) or "none"),
        mechanism="G", root_cause="no-orientation",
        summary="Give the site at least {} top-level navigation items, each naming a real "
                "destination.".format(NAV_MIN),
        how_to_fix=[
            'Replace catch-all labels with the destinations they contain: "Resources" '
            'becomes "Guides", "Case studies", "Downloads" if those are what is under it.',
            "Make sure at least {} destinations are reachable from the top level.".format(NAV_MIN),
            "Make sure the navigation is real HTML links, so it works before JavaScript runs.",
        ],
        effort="medium", owner="marketing",
        rationale="The navigation is the map. A visitor who cannot see where else "
                  "to go from the page they landed on has one option, which is to leave.",
        affected_pages=[source["url"]],
        # The constraint is a word list, not a place. A menu item is discounted
        # when its label is one of the controls that operate the page ("Menu",
        # "Search", "Sign in") or one of the catch-alls that name nothing
        # ("More", "Resources"), so a site whose menu this audit cannot read is
        # indistinguishable here from one that has no menu.
        checked=("links inside the delivered <nav>, <header> or role=navigation "
                 "markup, minus the labels this audit treats as controls rather "
                 "than destinations",
                 "internal destinations linked from the page footer",
                 "the same page's header and footer link fingerprint, which is "
                 "collected by a wider net than the menu selector"),
    )


# --------------------------------------------------------------------------
# The front door, and where it leads
#
# One audit covered a client-rendered storefront whose delivered homepage
# carries two `href`s, both to a font host, plus one `href=""`. The shell itself
# was reported. What was never reported is the consequence, which is the single
# most actionable sentence available about that site:
# a machine that arrives at the front page and follows links reads one page and
# stops. The nine pages that run read all came from the sitemap, against the
# 5,000 URLs the sitemap lists.
#
# It is a defect in its own right rather than a restatement of the shell,
# because it survives on sites that are not shells: a homepage whose whole
# navigation is assembled by JavaScript from a JSON payload delivers the same
# zero links to the same crawler, and `primary-navigation` above passes on it
# whenever the menu markup is present but empty of `href`s.
#
# What counts as reaching a page is deliberately narrow, because the question
# is what a machine can actually fetch from here:
#
#   another host           a font host, a CDN, a payment provider. Not a page
#                          of this site, and both hrefs in the real case were
#                          one. `links.internal` already separates them.
#   `href="#"`             resolves to nothing. `_links` drops it.
#   `javascript:`          resolves to nothing. `_links` drops it.
#   `href=""`             resolves to the page's own address, so it arrives
#                          here as a self-link and is dropped below.
#   a fragment-only        the email-obfuscation shape: the real destination is
#   destination            in the fragment, so following the path it appears to
#                          name reaches something else or nothing. Set aside
#                          for the same reason `_check_dead_ends` sets it aside.
FRONT_DOOR_MIN_SITE_PAGES = 2
# Chosen, not measured. A site that is one page has nowhere onward to link,
# and telling a one-pager that its front door leads nowhere is telling it that
# it is a one-pager. Two is the smallest number at which "there is somewhere
# else to link to" is true, and it is counted over the crawl and the sitemap
# together so a site whose second page the crawl never reached still counts.


def _pages_reachable_from(page):
    """Distinct addresses on this site that a machine can reach from this page.

    Its own address is not one of them: `href=""` and a logo linking `/` from
    the homepage both resolve to the page the reader is already on, and a front
    door that only leads back to itself leads nowhere.
    """
    links = page.get("links") or {}
    here = {normalise_url(page.get(field) or "") for field in ("url", "final_url")}
    entries = [e for e in (links.get("internal") or []) if isinstance(e, dict)]
    plainly_linked = {e.get("url") for e in entries if not e.get("fragment")}
    reachable = set()
    for entry in entries:
        url = entry.get("url")
        if not url or url in here:
            continue
        if entry.get("fragment") and url not in plainly_linked:
            continue
        reachable.add(url)
    return reachable


def _check_front_door(result, snapshot, home):
    """How many pages of this site a machine reaches from the homepage alone."""
    result.check("homepage-links-into-the-site")
    if home is None:
        result.skip("homepage-links-into-the-site",
                    "the homepage was not reached by this crawl, so what a machine entering "
                    "at the front page can reach from it was not measured")
        return

    reachable = _pages_reachable_from(home)
    # The second reading, and the guard against our own anchor loop failing.
    # `chrome_signature` is collected by a wider net - <nav>, <header>,
    # <footer> and the matching ARIA roles - through a different code path in
    # the extractor. If that net holds destinations while the anchor loop holds
    # none, the disagreement is about this audit rather than about the site,
    # and saying so beats publishing a count of zero.
    own_path = urlparse(normalise_url(home.get("url") or "") or "").path.rstrip("/") or "/"
    chrome_destinations = _chrome_paths(home) - {own_path}
    if reachable:
        result.skip("homepage-links-into-the-site",
                    "the homepage links {} of this site, so a machine entering at the front "
                    "page has somewhere to go from it".format(
                        plural(len(reachable), "other address", "other addresses")))
        return
    if chrome_destinations:
        result.skip("homepage-links-into-the-site",
                    "what the homepage links could not be settled: no anchor on it names "
                    "another address on this site, while its own header and footer "
                    "fingerprint holds {} - collected by a wider net than the anchor loop - "
                    "so the links on this page are built in a way this check cannot "
                    "read".format(plural(len(chrome_destinations), "internal destination")))
        return

    # A one-page site is not a site whose front door leads nowhere. Counted
    # over the crawl and the sitemap together, so a second page the crawl never
    # reached still stops this from firing.
    published = {p.get("url") for p in pages_of(snapshot) if p.get("url")}
    for record in snapshot.get("sitemaps") or []:
        for entry in record.get("urls") or []:
            url = normalise_url((entry or {}).get("loc") or "")
            if url:
                published.add(url)
    if len(published) < FRONT_DOOR_MIN_SITE_PAGES:
        result.skip("homepage-links-into-the-site",
                    "no anchor on the homepage names another address on this site, and this "
                    "site has no other address to name: the crawl and the sitemap together "
                    "know of {}".format(plural(len(published), "page")))
        return

    # How the rest of this crawl was found, which is the consequence stated as
    # a measurement rather than as a prediction. `source` is written by the
    # crawl on every record: `bfs` is a page reached by following a link off
    # another page, and everything else was named to the crawl by the sitemap,
    # by the seed list or by being the root itself.
    crawled = [p for p in pages_of(snapshot) if p.get("url")]
    followed = [p for p in crawled if p.get("source") == "bfs"]
    scope = sitemap_scope(snapshot.get("sitemaps"))
    if scope["urls_counted"]:
        sitemap_note = ("The sitemap lists {}, and it is the only thing on this site naming "
                        "them to a machine.".format(sitemap_total_phrase(scope, "URLs")))
    else:
        sitemap_note = ("No sitemap answered either, so nothing on this site names another "
                        "page of it to a machine at all.")
    read_from = (" These are the links a browser found after running this page's scripts, so "
                 "a crawler that does not run them sees fewer still."
                 if home.get("content_from") == "rendered" else
                 " No browser ran during this audit, so the links were looked for in the HTML "
                 "the server delivered and nowhere else.")
    # Where the front page delivered a framework mount point and nothing else,
    # say so and point at the shell rather than presenting this as an unrelated
    # content fault. The shell is the cause; this finding is what it costs, and
    # a reader who has just read the shell finding is entitled to the join.
    shell_note = ""
    # A front page whose only content is a script sending the browser on. It
    # links nothing because it is not a page at all, and "ship the menu as
    # plain links" is the wrong fix for it: the right one is a server redirect,
    # or the content served at this address.
    redirect = home.get("script_redirect") or None
    if redirect:
        shell_note = (" The homepage is a script that sends the browser to {} - a redirect "
                      "only something that runs JavaScript follows, so a crawler that does "
                      "not stops here.".format(
                          redirect.get("url") or "an address it works out as it runs"))
    elif _delivered_stub(home):
        shell_note = (" The homepage delivered {} characters of text inside an empty "
                      "framework mount point, which is why there is nothing in it to link: "
                      "the shell is reported separately, and this is what the shell costs."
                      .format(home.get("text_len") or 0))

    result.add(
        id_hint="homepage-reaches-no-other-page",
        title="Nothing on this site can be reached from its homepage",
        severity="high", confidence="high",
        evidence="The homepage at {} carries no link to any other address on this site. Its "
                 "own address, the links whose destination is held in a fragment rather than "
                 "in the path they name, and the links to other hosts are all set aside, and "
                 "what is left is nothing. A machine that arrives at the front page and "
                 "follows links reads this one page and stops.{} Of the {} this crawl read, "
                 "{} reached by following a link from another page; the rest were read "
                 "because something outside the site's own links named them. {}{}".format(
                     home.get("url"), shell_note, plural(len(crawled), "page"),
                     plural(len(followed), "page was", "pages were"), sitemap_note, read_from),
        # Not `dead-end`, which is the cause every other check in this file
        # raises about a page a visitor cannot leave. This is a different
        # defect: a machine entering at the front door discovers nothing, and
        # that stays true on a site with no dead end anywhere. Sharing the tag
        # also crossed the rule that holds an absence claim back on a site
        # most of which the crawl could not read - that rule is keyed on the
        # cause, and this claim is about one document that *was* read.
        mechanism="G", root_cause="unreachable-from-homepage",
        summary="Put the site's main destinations in the homepage's delivered HTML as plain "
                "links, so a machine entering at the front page can reach the rest of it.",
        how_to_fix=([
            "Replace the script at the homepage with a server redirect (HTTP 301, or 302 "
            "where the destination depends on the visitor's language), or serve the "
            "destination's content at the homepage address itself.",
            "Where the choice depends on language, keep the `<link rel=\"alternate\" "
            "hreflang>` tags and add plain `<a href>` links to each edition, so a machine "
            "that runs no script still reaches every one.",
        ] if redirect else []) + [
            "Ship the top-level menu as real `<a href>` elements in the HTML the server "
            "sends, and let JavaScript restyle or collapse it rather than create it.",
            "If the menu has to stay dynamic, put the same destinations in the footer as "
            "plain links: one block of HTML, present before any script runs.",
            # Not "the key products or services": this finding reaches a
            # library, a docs tree and a municipality as readily as a shop, and
            # the marketplace's rule is that a fix names the shape of the thing
            # rather than the sector.
            "Link the pages you would most want quoted - the main sections, and the handful "
            "of pages that carry what this site is for - from the front page itself, rather "
            "than relying on the sitemap to introduce them.",
            "Check it with `curl -s <the homepage> | grep -o 'href=\"[^\"]*\"'` and count how "
            "many of the addresses that come back are on this site.",
            who_edits_the_template(publishing_platform(snapshot)),
        ],
        effort="medium", owner="developer",
        rationale="A sitemap is a hint that some crawlers read and many do not. A link is the "
                  "route all of them take. A homepage that names no other page of the site "
                  "makes every other page depend on something outside the site to introduce "
                  "it, and an assistant that arrives at the front door finds one page.",
        affected_pages=[home.get("url")],
        # Two readings through two different code paths in the extractor, and
        # both had to come back empty. The second is what stops this firing on
        # a site whose menu this audit simply cannot read - see the skip above.
        checked=("every `<a href>` in the delivered homepage document, counted as addresses "
                 "on this site once the page's own address, the destinations held in a "
                 "fragment and the links to other hosts are removed",
                 "the same page's header and footer link fingerprint, collected inside "
                 "<nav>, <header>, <footer> and the matching ARIA roles by a wider net than "
                 "the anchor loop"),
    )


# --------------------------------------------------------------------------
# Advice that fits what the site is
#
# "Add one call to action appropriate to the page: contact, pricing, or the
# next step in the journey" was delivered to the maintainer of an open-source
# client library. That project sells nothing: it has no pricing page, no
# purchase funnel and no journey towards one, so two of the three examples
# name things it does not have and the third is the sentence with its examples
# removed. The same fix elsewhere in this file offered "the next size", "the
# related model" and "the delivery terms" to sites with no goods.
#
# `site_kind` answers what kind of thing a site is from its own structure, and
# `is_certainly` is the right reader for this: it changes the wording of
# advice and never whether the advice is given, so a site the classifier
# cannot place, or places weakly, keeps exactly the wording it has today.
_ADVICE_BY_KIND = {
    PROJECT: (
        "the installation instructions, the API reference or the issue tracker",
        "the reference page for what this page describes, the next page of the "
        "manual, or the changelog",
    ),
    PUBLICATION: (
        "the next article in the series, the section index or the subscription page",
        "the earlier article this one follows on from, or the index of the "
        "section it belongs to",
    ),
    PUBLIC_BODY: (
        "the form that starts the service, or the page saying who to ask",
        "the other steps of the same service, and the page above them that "
        "lists all of them",
    ),
    PERSONAL_OR_ACADEMIC: (
        "the publication, the course page, or a way to get in touch",
        "the related paper, the rest of the course, or the group this work "
        "belongs to",
    ),
    ORGANISATION: (
        "contact, or the next step the visitor came for",
        "the related service, and the page above it listing the rest",
    ),
}

# For a site that does sell, and for every site this cannot place. The second
# half of each pair is the neutral wording: it names the shape of the link
# rather than the sector, which is the same move the homepage-orientation fix
# already makes a few hundred lines above.
_ADVICE_DEFAULT = (
    "contact, pricing, or the next step in the journey",
    "the next item in the same section, the supporting page the visitor is "
    "about to want, or the page above that lists the rest",
)


def _advice_for(kind, index):
    """The wording of one fix, chosen for what this site actually is."""
    for name, texts in _ADVICE_BY_KIND.items():
        if kind is not None and kind.is_certainly(name):
            return texts[index]
    return _ADVICE_DEFAULT[index]


# --------------------------------------------------------------------------
# Where a page's own way onward is allowed to sit
#
# "Links inside the main content region" is a guess about where a page puts
# its onward links, and a large share of the web puts previous/next straight
# after the content rather than inside it. One documentation theme renders its
# sequential navigation in a `<footer class="prev-next-footer">` that is a
# sibling of `<article>`, and those two buttons are the largest clickable
# things on the page; reading `<article>` alone classified them as chrome and
# reported 30 of 50 pages as ending "without a way onward", on a site where
# every one of those pages says in type the size of a heading what to read
# next.
#
# The fix is not a list of themes. What separates real sequential or
# contextual navigation from site-wide furniture is measurable and already
# measured here: a prev/next target is different on every page, while a menu,
# a logo and a privacy link are the same on all of them. So a page has a way
# onward if it carries any internal link at all that four fifths of the
# crawled pages do not also carry - wherever on the page that link sits.
#
# This leaves the two engagement checks a proper hierarchy rather than a
# duplicate: a dead end is a page with nothing, and
# `onward-links-specific-to-the-page` is the separate, weaker finding about a
# deep page whose links are all furniture. Nothing moves from one to the
# other; the pages that leave this check are pages that were never dead ends.
#
# It needs a crawl big enough to tell furniture from a section menu, which is
# the floor `_site_furniture_targets` is already used behind. Below it this
# check reads the main region alone, exactly as before.
def _pages_offering_a_link_of_their_own(pages):
    """{url} for pages carrying an internal link that is not on nearly every page.

    Empty when the crawl is too small for "on nearly every page" to mean
    anything, which leaves every page judged on its main region alone.
    """
    if len(pages) < FURNITURE_MIN_PAGES:
        return set()
    furniture = _site_furniture_targets(pages)
    return {p["url"] for p in pages if _links_this_page_alone_offers(p, furniture)}


def _check_dead_ends(result, pages, render_mode="static", kind=None):
    result.check("dead-end-pages")
    dead_ends, embedded = [], []
    own_link = _pages_offering_a_link_of_their_own(pages)
    for page in pages:
        if page["page_type"] == "home":
            continue
        links = page.get("links") or {}
        main_links = links.get("main_internal_count", 0)
        # The CTA has to be in the page's own content. Counting a footer
        # "Contact" link meant no site with a footer could ever be a dead end.
        has_cta = (page.get("cta") or {}).get("in_main")
        # A form is a next step too. An enquiry page whose whole purpose is the
        # form was being reported as offering nothing to do.
        has_form = any(not f.get("is_search") for f in page.get("forms") or [])
        # `cta` is deliberately not filtered the way the link counts above are.
        # The extractor drops a main-region link whose destination is only in
        # its fragment, because it is not an onward link to the page it names -
        # but an anchor that opens a visitor's mail client is still something
        # to do, and a page offering an address is not a page offering nothing.
        # The two questions differ, and each answer is right for its own.
        # A fourth way onward, and the one this check used not to look for at
        # all: a link out of the main region to another host. See
        # `_off_site_next_step`. No browser is needed to read it, so it settles
        # the page whether or not one ran.
        # And the fifth: a link this page carries that nearly every other page
        # does not. See the block above `_pages_offering_a_link_of_their_own`
        # for why position on the page is the wrong test and repetition across
        # pages is the right one.
        #
        # And the sixth, which this check never read though the extractor has
        # always recorded it: a control that puts the thing on this page into a
        # basket. Three product pages on a server-rendered shop were
        # reported as ending "without a way onward: no link out of the main
        # content ... no call to action", each rendering an Add-to-Cart button
        # and seventeen internal links. Two of the three causes were in the
        # extractor and are fixed; this is the third. `cta` cannot cover it -
        # that field only ever inspects an `<a href>`, because the homepage
        # branch quotes its text and measures its offset in the copy, and a
        # `<button>` has neither. Buying the thing is the least ambiguous next
        # step a commerce page offers, and it was the one way onward this list
        # did not count.
        buys_the_thing = (page.get("add_to_cart_controls") or {}).get("count")
        if (main_links >= MIN_MAIN_LINKS or has_cta or has_form
                or buys_the_thing
                or _off_site_next_step(page) or page["url"] in own_link):
            continue
        # Nothing in the delivered HTML - which is a statement about the file,
        # and only a statement about the page when the file is the page. Where
        # nothing executed this page's scripts and the page names a third party
        # it both loads code from and links to, the next step is very likely
        # the widget that third party inserts, and this check has not seen it.
        # Reported as not judged, with the host named, rather than as a page
        # with nothing to do on it.
        #
        # Not judged rather than lower-confidence, because the evidence is
        # about this page specifically: the alternative - firing everywhere
        # with the confidence dropped whenever no browser ran - would attach a
        # caveat to every genuine dead end on every machine with no browser
        # installed, which is the ordinary case, and say nothing more true
        # about any of them.
        supplier = "" if _read_by_a_browser(page) else _next_step_supplied_from_elsewhere(page)
        if supplier:
            embedded.append((page, supplier))
        else:
            dead_ends.append(page)

    result.signal("pages_whose_next_step_may_be_embedded",
                  sorted(p["url"] for p, _h in embedded))

    # Links the extractor set aside from the counts above, on the pages this
    # check is about to name. A page whose only main-region link is an
    # obfuscated-email anchor now counts zero onward links, which is right -
    # that anchor leads to an address, not to the path it appears to name -
    # but a reader opening the page sees a link, and a report telling them
    # there is none is a report they stop believing. So the finding says which
    # link was set aside and why, rather than presenting a count they can
    # contradict at a glance.
    set_aside = [(page, urls) for page in dead_ends
                 for urls in [((page.get("links") or {}).get(
                     "main_links_with_a_fragment_destination") or [])]
                 if urls]
    fragment_note = ""
    if set_aside:
        fragment_note = (" {} a main-content link whose destination is held in its fragment "
                         "rather than in its address - an obfuscated email address is the "
                         "usual case - which leads somewhere other than the path it appears "
                         "to name, and is not counted as an onward link: {}.".format(
                             plural(len(set_aside), "of them does carry", "of them do carry"),
                             ", ".join(sorted({url for _p, urls in set_aside
                                               for url in urls})[:3])))

    not_judged = ""
    if embedded:
        not_judged = (" {} left out of this count: in each case the delivered HTML carries no "
                      "link, button or form of its own but does load a script from a "
                      "third-party host it also links to ({}), and no browser ran during this "
                      "audit, so a next step that widget inserts would not have been "
                      "seen.".format(
                          plural(len(embedded), "page was", "pages were"),
                          ", ".join(sorted({host for _p, host in embedded})[:3])))

    if not dead_ends:
        # "every crawled content page" is a false quantity once any page has
        # been set aside, and the sentence naming the ones set aside sat right
        # beside it. The subject narrows to the pages actually judged.
        result.skip("dead-end-pages",
                    "{} crawled content page offers a way onward: a link out of its main "
                    "content, on this site or off it, a call to action, a form, or a link "
                    "elsewhere on the page that most of this site's pages do not carry.{}".format(
                        "every" if not embedded else "every judged", not_judged))
        return

    rate = pct(len(dead_ends), len(pages))
    result.add(
        id_hint="pages-with-no-next-step",
        title="{} no next step".format(
            plural(len(dead_ends), "page offers", "pages offer")),
        severity="medium", confidence="medium",
        evidence="{} of {} content pages ({}%) end without a way onward: no link out of the "
                 "main content to anywhere, on this site or off it, no call to action, no "
                 "form, and no link anywhere else on the page that four fifths of this "
                 "site's pages do not also carry. Examples: {}.{}{}{}".format(
                     len(dead_ends), len(pages), rate,
                     ", ".join(example_urls([p["url"] for p in dead_ends])),
                     fragment_note, not_judged,
                     # Said even where nothing was left out, because the reader
                     # is entitled to know which document was read. This check
                     # can only ever describe the HTML the server sent.
                     "" if render_mode == "rendered" else
                     " No browser ran during this audit, so the next step on any of these "
                     "pages was looked for in the HTML the server delivered and nowhere else."),
        mechanism="G", root_cause="dead-end",
        summary="End every page with a related-content block or a clear next action.",
        how_to_fix=[
            'Add a "Read next" or "Related" block with 2 to 4 relevant internal links to the '
            "page template.",
            "Add one call to action appropriate to the page: {}.".format(
                _advice_for(kind, 0)),
            "Because these are templates, fixing the template fixes every page using it.",
        ],
        effort="low", owner="content owner",
        rationale="A visitor who arrived from an answer landed deep in the site "
                  "with no journey behind them. If the page ends without an onward path, the "
                  "session ends with it.",
        affected_pages=[p["url"] for p in dead_ends],
        # Four readings of the same page, all of which had to come back empty.
        # An enquiry page whose whole purpose is its form was once reported as
        # offering a visitor nothing to do.
        checked=("links inside each page's main content region, on this site or "
                 "off it, minus any whose destination is carried in a fragment "
                 "rather than in the address they appear to name",
                 "every other internal link on the page, wherever it sits, "
                 "against the links four fifths of the crawled pages carry - so "
                 "previous/next buttons rendered beside the content rather than "
                 "inside it count as a way onward, while the menu and the footer "
                 "do not",
                 "which of those leave the site, since a hosted form, a booking "
                 "system or a donation platform is a next step on somebody "
                 "else's host",
                 "a call to action in that same region, meaning a link whose first "
                 "word is one of the action verbs this audit recognises or whose "
                 "class or id contains btn, button or cta",
                 "forms on the page other than site search"),
    )


# --------------------------------------------------------------------------
# The page an answer actually deep-links to
#
# Everything above this line judges a visitor who arrived at the homepage and
# browsed. That is not the visit this skill exists for. Someone who asks an
# assistant about one specific thing, is given its name and clicks through
# lands on the page for that one thing - a product, a service, an article -
# already holding the question. The three checks below ask whether that page
# answers it: whether the facts are on the page at all, whether they are in
# the first screen or under several screens of brand copy, and whether the
# page offers a way further into the section it belongs to or only the
# site-wide menu it shares with every other page.
#
# All three are template questions. One page missing a returns policy is an
# oversight; half the product pages missing one is the template, and the fix
# is one edit either way - so each of them needs a handful of comparable pages
# before it will say anything, and reports the share it measured.
# --------------------------------------------------------------------------

LANDING_PAGE_MINIMUM = 3
# Below three comparable pages there is no template to describe, and "1 of 1
# product pages" is a sentence about whichever page the crawl happened to
# reach rather than about the site. The same floor the breadcrumb check uses,
# for the same reason.

LANDING_FAIL_SHARE = 0.5
# Half or more. A single page built from a different template is not what
# these findings claim to have found, and telling an owner their product
# template is wrong on the evidence of one page is how a check earns a
# reputation for guessing.

_OFFER_TYPES = frozenset({"offer", "aggregateoffer", "pricespecification",
                          "unitpricespecification"})
_PRODUCT_TYPES = frozenset({"product", "productgroup", "productmodel",
                            "individualproduct"})

# schema.org properties that answer each of the three questions a buyer still
# has once they know the price. Written lower-case and matched lower-case:
# `hasMerchantReturnPolicy` is typed by hand into CMS templates and a template
# that spells it `hasmerchantreturnpolicy` has still stated the fact.
_STOCK_PROPERTIES = ("availability", "inventorylevel", "availabilitystarts",
                     "availableatorfrom")
_DELIVERY_PROPERTIES = ("shippingdetails", "deliverytime", "deliveryleadtime",
                        "estimateddeliverydate", "availabledeliverymethod",
                        "shippingrate", "shippingdestination")
_RETURNS_PROPERTIES = ("hasmerchantreturnpolicy", "merchantreturnpolicy",
                       "merchantreturnlink", "returnpolicycategory",
                       "merchantreturndays", "returnpolicyseasonaloverride")

# The second reading of the same three facts: a link from the page to the page
# that states them. This is a path test rather than a word test, and paths are
# written in ASCII on sites whose prose is in any script at all, so it keeps
# working where the third reading below cannot run.
_DELIVERY_PATH_RE = re.compile(
    r"(?:^|[/_-])(?:shipping|delivery|deliveries|postage|dispatch|freight)(?:$|[/_.-])", re.I)
# A whole path segment that is the policy, not a word inside another name: a
# shop's `/collections/return-gifts` is a festival gift collection, and it was
# read as the page stating the returns policy.
_RETURNS_PATH_RE = re.compile(
    r"(?:^|/)(?:[\w-]*[-_])?(?:returns?|refunds?|exchanges?|cancellations?)"
    r"(?:[-_](?:and|&)[-_](?:returns?|refunds?|exchanges?|cancellations?))?"
    r"(?:[-_](?:polic(?:y|ies)|info|information|centre|center|terms|process|guide))?"
    r"(?:$|[/.?#])", re.I)

# And the third: the page's own words. English, and deliberately narrow -
# every phrase here has to be about the thing being sold rather than a word
# that happens to appear on the page. A bare `returns` matches "the swallow
# returns in April"; what a buyer is looking for is a policy, so the pattern
# asks for one.
_STOCK_TEXT_RE = re.compile(
    r"\b(?:in\s+stock|out\s+of\s+stock|sold\s+out|back\s+in\s+stock|low\s+stock"
    r"|only\s+\d+\s+left|pre[\s-]?order|back[\s-]?order|made\s+to\s+order"
    r"|add\s+to\s+(?:cart|bag|basket)|notify\s+me\s+when)\b", re.I)
_DELIVERY_TEXT_RE = re.compile(
    r"(?:free\s+(?:shipping|delivery)|shipping\s+(?:polic|charge|rate|cost|fee|time)"
    r"|delivery\s+(?:polic|charge|time|date|window|estimate|within|in\s+\d)"
    r"|ships?\s+(?:in|within|on|by|next|same)\b|dispatch(?:ed)?\s+(?:in|within|on|by)\b"
    r"|estimated\s+delivery|cash\s+on\s+delivery|delivered\s+(?:in|within|by)\b)", re.I)
_RETURNS_TEXT_RE = re.compile(
    r"(?:returns?\s+(?:polic|window|period|within|accepted|are\s+free)"
    r"|free\s+returns?\b|easy\s+returns?\b|no[\s-]questions[\s-]asked"
    r"|refund\s+polic|money[\s-]back|exchange\s+polic"
    r"|\d+[\s-]day\s+(?:return|refund|exchange|money))", re.I)


def _schema_nodes(page):
    """Every JSON-LD, Microdata and RDFa node this page carries, flattened.

    The extractor already shapes Microdata and RDFa items like JSON-LD nodes,
    so one walk covers all three notations. Reading JSON-LD alone would tell a
    shop whose theme states stock and returns in `itemprop` attributes that its
    pages answer none of a buyer's questions, about pages that answer all of
    them in the notation the theme happens to use.
    """
    out = []
    stack = [page.get("jsonld") or [], page.get("microdata_items") or []]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            out.append(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _node_types(node):
    return {name.lower() for name in jsonld_type_names((node or {}).get("@type"))}


def _property_in_markup(page, names):
    """The name of the first of `names` this page's markup carries, or "".

    Searched across every node rather than only inside the Offer, because
    plenty of templates hang `shippingDetails` off the Product and plenty of
    others off the Offer, and which one a site chose says nothing about
    whether a visitor is told when the thing arrives.
    """
    wanted = set(names)
    for node in _schema_nodes(page):
        for key, value in node.items():
            if value in (None, "", [], {}):
                continue
            if str(key).lower().lstrip("@") in wanted:
                return str(key)
    return ""


def _price_in_markup(page):
    """The price this page's commerce markup states, or "".

    Only from a node that is an offer or a product. A `price` property under
    anything else on the page - a donation widget's suggested amount, a review
    of somebody else's item - is not what this page sells, and reading one as
    a price is how a charity came to be audited as a shop.
    """
    for node in _schema_nodes(page):
        if not (_node_types(node) & (_OFFER_TYPES | _PRODUCT_TYPES)):
            continue
        for key in ("price", "lowPrice", "lowprice", "highPrice", "highprice"):
            value = node.get(key)
            if value in (None, "", [], {}) or isinstance(value, (dict, list)):
                continue
            currency = node.get("priceCurrency") or node.get("pricecurrency") or ""
            return "{}{}{}".format(currency, " " if currency else "", value)
    return ""


def _for_sale_pages(pages):
    """[(page, the price it publishes)] for the pages that offer something.

    The class is established before anything is asked of it, which is the
    order that keeps this quiet on the sites it has nothing to say about. A
    consultancy's insight article mentioning a competitor's licence fee is not
    a page anybody buys from, and asking it for a stock state would produce a
    finding telling a writer to publish delivery times for an essay.

    So a page joins this class only where it says it sells this thing itself:
    the crawl typed it a product page, or it publishes product markup about
    itself - and either way it carries a price. Listing pages are excluded. A
    grid of twelve items with twelve prices answers a browsing visitor; the
    questions below are about the one thing somebody arrived asking for.

    Both readings of the price are language-neutral. `find_prices` matches a
    currency symbol or an ISO code and `price` is a schema.org property, so a
    shop written in any script at all is judged the same way as one written in
    English.
    """
    out = []
    for page in pages:
        if page.get("page_type") == "home" or is_listing_page(page):
            continue
        markup_types = {t.lower() for t in page.get("jsonld_types") or []}
        if page.get("page_type") != "product" and not (markup_types & _PRODUCT_TYPES):
            continue
        visible = page.get("prices") or find_prices(page.get("body_text") or "")
        declared = _price_in_markup(page)
        if visible:
            out.append((page, visible[0]))
        elif declared:
            out.append((page, declared))
    return out


def _purchase_facts(page, english):
    """{fact: where it was found} for the three a price does not answer.

    An empty value means every reading available for that fact came back
    empty. Each fact is read from the page's own markup first, then from the
    paths of the page's own links, and only then - where the site's prose is
    in the language these patterns are written for - from the page's own words.
    """
    links = (page.get("links") or {}).get("internal") or []
    paths = [urlparse(normalise_url((link or {}).get("url") or "")).path or ""
             for link in links]
    text = page.get("body_text") or ""

    found = {}
    stock = _property_in_markup(page, _STOCK_PROPERTIES)
    if not stock and english:
        match = _STOCK_TEXT_RE.search(text)
        stock = '"{}" in the page copy'.format(match.group(0).strip()) if match else ""
    found["a stock state"] = stock

    for fact, properties, path_re, text_re in (
            ("a delivery promise", _DELIVERY_PROPERTIES, _DELIVERY_PATH_RE, _DELIVERY_TEXT_RE),
            ("a returns policy", _RETURNS_PROPERTIES, _RETURNS_PATH_RE, _RETURNS_TEXT_RE)):
        where = _property_in_markup(page, properties)
        if not where:
            path = next((path for path in paths if path_re.search(path)), "")
            where = "a link to {}".format(path) if path else ""
        if not where and english:
            match = text_re.search(text)
            where = '"{}" in the page copy'.format(match.group(0).strip()) if match else ""
        found[fact] = where
    return found

def _check_landing_page_answers(result, pages, english, render_mode="static"):
    """Does the page a deep link lands on carry the facts that decide the buy?

    A shopper who asks an assistant about one item, is given its name and
    clicks through arrives already knowing what they want. Price is the fact
    they get: what the page has to add is whether the thing exists, when it
    arrives and what happens if it is wrong. A page that publishes a figure
    and none of those three has told a visitor what to want and nothing about
    how to get it.

    Nothing here fires on one page. Half or more of a site's comparable pages
    have to be missing all three, because that is a template and a template is
    one edit.
    """
    result.check("landing-page-answers")
    for_sale = _for_sale_pages(pages)
    if len(for_sale) < LANDING_PAGE_MINIMUM:
        result.skip("landing-page-answers",
                    "{} crawled, fewer than the {} it takes to tell a template from one "
                    "page's oversight".format(
                        plural(len(for_sale),
                               "page that publishes a price and is not a listing was",
                               "pages that publish a price and are not listings were"),
                        LANDING_PAGE_MINIMUM))
        return

    judged, set_aside, silent = [], [], []
    for page, price in for_sale:
        # Two of the three facts have a reading that needs no prose - the
        # schema.org property and the path of a link to the policy page - and
        # a stock state has only the property. On a site whose prose these
        # patterns cannot read, a page carrying no commerce markup at all
        # leaves one reading standing out of three, and "this page does not
        # say when it arrives" would then be a statement about the audit's
        # vocabulary rather than about the page. Those pages are set aside and
        # named, which is the honest answer and not a quiet one.
        if not english and not _schema_nodes(page):
            set_aside.append(page)
            continue
        found = _purchase_facts(page, english)
        judged.append((page, price, found))
        if not any(found.values()):
            silent.append((page, price, found))

    # Emitted whether or not anything fires. The report's recommendations are
    # written from signals, and a recommendation about the page an answer
    # deep-links to should be gated on pages this audit measured rather than
    # on the site having a product page at all - which is how three of the
    # fourteen came to fire on six sites out of six.
    result.signal("landing_pages_judged_for_purchase_facts", len(judged))
    result.signal("landing_pages_stating_only_a_price",
                  sorted(p["url"] for p, _price, _f in silent))

    aside_note = ""
    if set_aside:
        aside_note = (" {} left out: this site's prose is not in a language this audit reads "
                      "for delivery and returns wording, and there is no commerce markup "
                      "either, so only one of the three facts could be looked "
                      "for.".format(plural(len(set_aside), "page was", "pages were")))

    if not judged:
        result.skip("landing-page-answers",
                    "no page that publishes a price could be judged.{}".format(aside_note))
        return
    if len(silent) < len(judged) * LANDING_FAIL_SHARE or not silent:
        # Named, not merely counted. A pass that says "every page answers a
        # buyer" without saying which fact it found where is a pass a reader
        # cannot check, and this check's whole claim is about what is on a
        # page.
        example = next(((p, f) for p, _price, f in judged if any(f.values())), None)
        result.skip("landing-page-answers",
                    "{} of {} crawled pages that publish a price also state at least one of "
                    "a stock state, a delivery promise or a returns policy{}.{}".format(
                        len(judged) - len(silent), len(judged),
                        "" if example is None else
                        " - {} states {}".format(
                            example[0]["url"],
                            ", ".join("{} ({})".format(fact, where)
                                      for fact, where in sorted(example[1].items()) if where)),
                        aside_note))
        return

    missing = sorted({fact for _p, _price, found in silent
                      for fact, where in found.items() if not where})
    result.add(
        id_hint="landing-pages-do-not-answer-the-arriving-question",
        title="{} a price and nothing else a buyer needs to decide".format(
            plural(len(silent), "landing page states", "landing pages state")),
        severity="medium", confidence="high",
        evidence="{} of {} crawled pages that publish a price state none of {}. For example "
                 "{} publishes {} and carries no stock state, no delivery promise and no "
                 "returns policy anywhere on it. Others: {}.{}{}".format(
                     len(silent), len(judged), ", ".join(missing),
                     silent[0][0]["url"], silent[0][1],
                     ", ".join(example_urls([p["url"] for p, _price, _f in silent[1:]]))
                     or "none",
                     aside_note,
                     "" if render_mode == "rendered" else
                     " No browser ran during this audit, so a fact a script writes into the "
                     "page after it loads was not seen; open one of these pages and check "
                     "before acting."),
        mechanism="G", root_cause="no-orientation",
        summary="Put the stock state, the delivery promise and the returns terms on the "
                "product template, beside the price.",
        how_to_fix=[
            "Add the three facts to the product template so every page carries them: whether "
            "it is in stock, when it arrives, and how long a buyer has to send it back.",
            "Write them as text on the page rather than only inside a tab a script opens, so "
            "they are there before any script runs.",
            "Mirror the same three in the Offer markup - `availability`, `shippingDetails` "
            "and `hasMerchantReturnPolicy` - so an assistant can quote them.",
            "Because this is the template, one edit fixes every page built from it.",
        ],
        effort="medium", owner="developer",
        rationale="Someone sent here by an assistant already knows what the thing is and "
                  "roughly what it costs; that is why they clicked. The questions they still "
                  "have are whether they can have it, when, and what happens if it is wrong. "
                  "A page that answers none of them sends them to a competitor's page that "
                  "does.",
        affected_pages=[p["url"] for p, _price, _f in silent],
        checked=("the page's own commerce markup, in JSON-LD, Microdata or RDFa, for "
                 "availability, a shipping or delivery property, and a merchant return "
                 "policy",
                 "the paths of the page's own internal links, for a link to a shipping, "
                 "delivery, returns, refund or cancellation page",
                 "the page's delivered body text, for a stock state, a delivery promise or a "
                 "returns policy stated in English - a phrase list, so a promise worded some "
                 "other way reads to this audit as no promise"),
    )


def _check_landing_page_leads_with_the_price(result, pages):
    """Is the price in the first screen, or under several screens of copy?

    Same class of page as the check above, and the question the case for this
    skill is built on: someone arrives holding a question and has to hunt for
    the answer. The measurement is the offset of the first currency figure in
    the page's own body copy, which is a character count and needs no
    language: the brand story is as long in Hindi as it is in English.

    Document order is the reading order on a phone, whatever a wide screen
    does with a sidebar, and most people arriving from an assistant are on a
    phone. That is what makes this measurable at all, and it is also its
    limit: a price a desktop layout floats beside the opening paragraph is
    read here as sitting wherever its markup sits.
    """
    result.check("landing-page-leads-with-the-price")
    for_sale = _for_sale_pages(pages)
    measurable, buried = [], []
    for page, _price in for_sale:
        text = page.get("body_text") or ""
        prices = page.get("prices") or find_prices(text)
        # A price that exists only in the markup has no position in the copy.
        # Reporting one as buried would be a claim about a figure the visitor
        # never reads in the first place, which is a different finding and one
        # this check does not make.
        offset = text.find(prices[0]) if prices else -1
        if offset < 0:
            continue
        measurable.append(page)
        if offset > CTA_BYTE_WINDOW:
            buried.append((page, prices[0], offset))

    result.signal("landing_pages_that_bury_the_price",
                  sorted(p["url"] for p, _pr, _o in buried))
    if len(measurable) < LANDING_PAGE_MINIMUM:
        result.skip("landing-page-leads-with-the-price",
                    "{} crawled, fewer than the {} it takes to say anything about a "
                    "template. A price stated only in the page's markup has no position in "
                    "the copy and was not measured".format(
                        plural(len(measurable),
                               "page that publishes a price in its own copy was",
                               "pages that publish a price in their own copy were"),
                        LANDING_PAGE_MINIMUM))
        return
    if not buried or len(buried) < len(measurable) * LANDING_FAIL_SHARE:
        result.skip("landing-page-leads-with-the-price",
                    "{} of {} crawled pages that publish a price state it within the first "
                    "{:,} characters of their own copy, which is roughly the first "
                    "screen".format(len(measurable) - len(buried), len(measurable),
                                    CTA_BYTE_WINDOW))
        return

    page, price, offset = max(buried, key=lambda row: row[2])
    opening = " ".join((page.get("body_text") or "")[:160].split())
    result.add(
        id_hint="landing-pages-bury-the-price",
        title="{} the price under several screens of copy".format(
            plural(len(buried), "landing page states", "landing pages state")),
        severity="medium", confidence="medium",
        evidence='{} of {} crawled pages that publish a price state it only after the first '
                 '{:,} characters of their own copy. The worst is {}, where "{}" first '
                 'appears {:,} characters in and the page opens "{}...". Others: {}.'.format(
                     len(buried), len(measurable), CTA_BYTE_WINDOW, page["url"], price,
                     offset, truncate(opening, 140),
                     ", ".join(example_urls([p["url"] for p, _pr, _o in buried
                                             if p["url"] != page["url"]])) or "none"),
        mechanism="G", root_cause="no-orientation",
        summary="Move the price, and the rest of what a buyer came to check, above the brand "
                "copy on this template.",
        how_to_fix=[
            "Put the price, the stock state and the delivery promise in the first screen of "
            "the template, above the description and the brand story.",
            "Keep the longer copy - it is what the page ranks on - but put it after the "
            "facts rather than in front of them.",
            "Check the order in the HTML rather than on a wide screen: a phone reads the "
            "page in the order the markup is written.",
        ],
        effort="medium", owner="developer",
        rationale="A visitor an assistant sent here arrived with one question and gave the "
                  "page a few seconds to answer it. Copy that argues for the brand before it "
                  "says what the thing costs is answering a question this visitor did not ask.",
        affected_pages=[p["url"] for p, _pr, _o in buried],
        # An absence claim, because "the opening screen does not carry the
        # price" is what it says. Two readings of the same opening screen, and
        # the second is what stops a page whose price the extractor could not
        # find at all from being reported as a page that buries one.
        checked=("the position of the first currency figure in the page's delivered body "
                 "copy, which is the main region with the site's header, menu and footer "
                 "taken out",
                 "whether that figure appears in the first {:,} characters of it, measured in "
                 "document order, which is the order a phone reads the page in".format(
                     CTA_BYTE_WINDOW)),
    )


FURNITURE_MIN_PAGES = 8
# How many pages a crawl needs before "a link on four fifths of them" means
# anything. On a five-page crawl four fifths is four pages, so a link in one
# section menu becomes site furniture and every page reads as carrying nothing
# of its own - which would report the whole site on the strength of the crawl
# being small.


def _site_furniture_targets(pages):
    """Internal link targets that appear on four fifths of the crawled pages.

    This is what "the same generic page to everyone" reduces to, measured: a
    link the site puts on nearly every page is the menu, the basket, the
    privacy notice or the logo, and it is there whatever question brought the
    visitor. A link that appears on a handful of pages is about those pages.

    Four fifths is the share `CHROME_CORE_SHARE` already uses to decide that a
    link is site furniture rather than a section menu, and the two questions
    are the same question, so they use the same number.

    Compared through `_orphan_key`, because a menu written with a trailing
    slash and a body link written without one are the same destination.
    """
    seen = Counter()
    for page in pages:
        targets = set()
        for bucket in ("internal", "nav", "footer"):
            for link in (page.get("links") or {}).get(bucket) or []:
                key = _orphan_key((link or {}).get("url") or "")
                if key:
                    targets.add(key)
        seen.update(targets)
    needed = max(2, int(round(len(pages) * CHROME_CORE_SHARE)))
    return {key for key, count in seen.items() if count >= needed}


def _links_this_page_alone_offers(page, furniture):
    """Internal links on this page that are not on nearly every other page.

    A breadcrumb into the page's own section counts, and so does a related
    item, a size guide or the next article in a series. Where the link sits is
    not asked: a trail in the header is as much a way onward as a block at the
    foot of the copy, and judging by position would have reported a page whose
    section trail is part of its header template as offering nothing.
    """
    self_key = _orphan_key(page.get("url") or "")
    links = page.get("links") or {}
    out = []
    for bucket in ("internal", "nav", "footer"):
        for link in links.get(bucket) or []:
            url = (link or {}).get("url") or ""
            key = _orphan_key(url)
            if key and key != self_key and key not in furniture and url not in out:
                out.append(url)
    return out


def _check_onward_journey(result, pages, kind=None):
    """Does the page offer a next step about the thing the visitor came for?

    The dead-end check asks whether a page offers any way onward at all, and
    almost every real page does: a menu, a footer, a logo. That is the wrong
    question for somebody who arrived mid-journey. The menu is the same menu
    every page carries; following it means starting again at the top of a
    search they had already finished. What continues their journey is a link
    this page has and its neighbours do not - the next item, the size guide,
    the following article, the trail into the section this page sits in.

    So the measurement is not where the links are but whether any of them is
    this page's own. Every internal link the page carries is compared against
    the links four fifths of the crawled pages carry, and a page whose whole
    link set survives that subtraction empty is a page offering the visitor
    exactly what it offers everybody.
    """
    result.check("onward-links-specific-to-the-page")
    judged = [p for p in pages
              if p["page_type"] in DEEP_TYPES and p.get("depth", 0) >= 1
              and not is_listing_page(p)]
    # Two floors, and the reason names the one that was not met. A single
    # sentence reciting both told a site with fifty-two pages and no deep ones
    # that its crawl was too small, which is a false statement about the crawl
    # standing in for a true one about what it found.
    if len(pages) < FURNITURE_MIN_PAGES:
        result.skip("onward-links-specific-to-the-page",
                    "only {} were crawled, fewer than the {} it takes to establish which "
                    "links this site puts on every page - and without that, a link in one "
                    "section menu would read as site furniture".format(
                        plural(len(pages), "content page"), FURNITURE_MIN_PAGES))
        return
    if len(judged) < LANDING_PAGE_MINIMUM:
        result.skip("onward-links-specific-to-the-page",
                    "{} was crawled - a product, article, documentation, location, service or "
                    "comparison page below the homepage - fewer than the {} it takes to "
                    "describe a template rather than one page".format(
                        plural(len(judged), "deep page"), LANDING_PAGE_MINIMUM))
        return

    furniture = _site_furniture_targets(pages)
    own = {p["url"]: _links_this_page_alone_offers(p, furniture) for p in judged}
    stranded = [p for p in judged if not own[p["url"]]]
    result.signal("deep_pages_offering_only_site_wide_links",
                  sorted(p["url"] for p in stranded))
    if not stranded or len(stranded) < len(judged) * LANDING_FAIL_SHARE:
        example = next((p for p in judged if own[p["url"]]), None)
        result.skip("onward-links-specific-to-the-page",
                    "{} of {} crawled deep pages carry at least one internal link that is not "
                    "on nearly every other page of this site{}".format(
                        len(judged) - len(stranded), len(judged),
                        "" if example is None else
                        ", such as {} linking {}".format(
                            example["url"], ", ".join(own[example["url"]][:2]))))
        return

    # What these pages do link to, so the sentence above is checkable. A reader
    # who opens one sees links on it, and a finding that only says what is
    # missing is one they stop believing.
    shared = sorted({url for page in stranded[:5]
                     for bucket in ("internal", "nav", "footer")
                     for link in ((page.get("links") or {}).get(bucket) or [])
                     for url in [(link or {}).get("url") or ""] if url})
    result.add(
        id_hint="deep-pages-offer-only-the-links-every-page-has",
        title="{}".format(
            plural(len(stranded),
                   "deep page offers the same links as every other page and nothing of its own",
                   "deep pages offer the same links as every other page and nothing of their own")),
        severity="medium", confidence="medium",
        evidence="{} of {} crawled deep pages carry no internal link that is not already on "
                 "four fifths of this site's pages: no related item, no link into the section "
                 "they sit in, nothing specific to what the page is about. Every link on them "
                 "is site furniture{}. Examples: {}.".format(
                     len(stranded), len(judged),
                     " - {}".format(", ".join(shared[:4])) if shared else "",
                     ", ".join(example_urls([p["url"] for p in stranded]))),
        mechanism="G", root_cause="dead-end",
        summary="Give the deep templates links of their own: the section they sit in, and two "
                "to four related pages.",
        how_to_fix=[
            "Add a block to the deep templates listing 2 to 4 other pages from the same "
            "section.",
            # Named for what this site actually has. The examples used to be
            # "the next size", "the related model" and "the delivery terms",
            # offered to sites with nothing for sale. See `_advice_for`.
            "Link what the visitor is about to want: {}.".format(_advice_for(kind, 1)),
            "Add a trail into the section above the heading, so the page says which part of "
            "the site it belongs to and one click reaches the rest of it.",
            "Render those links in the HTML rather than fetching them after the page loads, "
            "so a crawler following the site can reach them too.",
        ],
        effort="medium", owner="developer",
        rationale="A visitor sent here by an assistant has no history behind them and no "
                  "reason to have seen anything else on this site. A menu shared with every "
                  "other page takes them back to the top and asks them to start the search "
                  "they had already finished. A link to the next thing in the same section is "
                  "the only one that continues what they came to do.",
        affected_pages=[p["url"] for p in stranded],
        checked=("every internal link in each page's delivered HTML, taken from its body, its "
                 "menu and its footer alike, because a trail in the header is as much a way "
                 "onward as a block under the copy",
                 "which of those links four fifths of the crawled pages also carry, which is "
                 "the site's furniture and is on the page whatever question brought the "
                 "visitor"),
    )



def _check_orphans(result, snapshot, pages, stub_urls=()):
    """Pages the sitemap advertises that nothing on the site links to."""
    result.check("orphan-pages")
    records = snapshot.get("sitemaps") or []
    sitemap_urls = set()
    for record in records:
        for entry in record.get("urls") or []:
            url = normalise_url(entry.get("loc") or "")
            if url:
                sitemap_urls.add(url)

    # Whether any sitemap answered at all, before counting what it lists. An
    # empty URL list reads the same way whether a site publishes a sitemap with
    # three URLs in it or publishes none, and the second case was reported as
    # the first: one report said "No XML sitemap is available" as a finding and
    # then declined this check with "the sitemap lists fewer than 5 URLs", a
    # sentence about a file that does not exist.
    answered = [r for r in records if r.get("status") == 200 and not r.get("parse_error")]
    if not answered:
        if not records:
            why = "no sitemap was found at the usual addresses or named in robots.txt"
        elif any(r.get("status") == 200 and r.get("parse_error") for r in records):
            why = ("the file served at the sitemap address is not XML that parses, so it "
                   "names no pages this audit can read")
        else:
            why = "every sitemap address tried answered HTTP {}".format(
                ", ".join(sorted({str(r.get("status")) for r in records})))
        result.skip("orphan-pages",
                    "no sitemap is published, so there is no list of pages to compare the "
                    "crawl against: {}. Without it an orphan cannot be told from a page the "
                    "crawl simply did not reach".format(why))
        return
    # Answered and listing nothing is no sitemap. A site answering 200 with its
    # ordinary HTML page at every address was told "the sitemap that answered
    # lists 0 URLs", in a report whose sitemap finding said no XML sitemap is
    # available - a sentence about a file that is not there.
    if not sitemap_urls:
        result.skip("orphan-pages",
                    "no sitemap listing any page was found: {} answered at a sitemap address "
                    "and none of them held a single `<loc>` entry, which is what a page "
                    "served in a sitemap's place looks like. Without a list of pages an "
                    "orphan cannot be told from a page the crawl simply did not "
                    "reach".format(plural(len(answered), "address", "addresses")))
        return
    if len(sitemap_urls) < 5:
        result.skip("orphan-pages",
                    "the sitemap that answered lists {}, too few to identify orphans "
                    "reliably".format(plural(len(sitemap_urls), "URL")))
        return

    # On a large site a 30-page crawl sees a tiny slice, so "no crawled page
    # links here" says more about the budget than about the site. Only judge
    # orphans when the crawl covered a meaningful share of the sitemap.
    crawled_count = len(pages_of(snapshot))
    if snapshot.get("crawl", {}).get("budget_exhausted") or len(sitemap_urls) > crawled_count * 3:
        result.skip("orphan-pages",
                    "the sitemap lists {} but only {} pages were crawled, so an absent "
                    "internal link is more likely to reflect the crawl budget than a genuine "
                    "orphan".format(
                        sitemap_total_phrase(sitemap_scope(snapshot.get("sitemaps")), "URLs"),
                        crawled_count))
        return

    # Compared through one key, because the sitemap and the site's own links
    # do not have to agree on spelling. A sitemap listing the bare host
    # against pages linking the `www.` host shares not one string, so
    # every crawled sitemap URL was an orphan - a medium finding, on a site
    # whose navigation links every page it lists.
    # Two link buckets, not one. `links.internal` is capped at the first 300
    # anchors in document order, and the footer is the last thing in the
    # document - so on a page with a long product grid the site-wide footer
    # menu falls off the end of the list, and every page reachable only from
    # the footer was reported as linked from nowhere. `links.nav` and
    # `links.footer` are deduplicated and capped separately, so they still
    # hold what `internal` truncated away.
    linked = set()
    for page in pages_of(snapshot):
        buckets = page.get("links") or {}
        for bucket in ("internal", "nav", "footer"):
            for link in buckets.get(bucket) or []:
                linked.add(_orphan_key(link.get("url") or ""))

    # A page whose HTML arrived as a stub delivered no links either, so it can
    # neither be judged an orphan nor rule one out. It is dropped from the
    # candidates, and the evidence says the graph has holes in it rather than
    # letting a page linked from a stub be reported as linked from nowhere.
    stub_urls = set(stub_urls or ())
    by_key = {_orphan_key(p["url"]): p["url"] for p in pages_of(snapshot)
              if p["url"] not in stub_urls}
    # Only pages we actually visited can be judged: a sitemap URL we never
    # fetched might well be linked from a page outside the crawl budget.
    orphans = sorted(by_key[k] for k in
                     ({_orphan_key(u) for u in sitemap_urls} & set(by_key)) - linked)
    if not orphans:
        result.skip("orphan-pages",
                    "every crawled sitemap URL is linked from at least one other crawled page")
        return

    result.add(
        id_hint="orphan-pages-in-sitemap",
        title="{} listed in the sitemap but linked from nowhere".format(
            plural(len(orphans), "page is", "pages are")),
        severity="medium", confidence="medium",
        evidence="Of {} sitemap URLs that were crawled, {} received no internal link from any "
                 "other crawled page: {}.{}".format(
                     len({_orphan_key(u) for u in sitemap_urls} & set(by_key)),
                     len(orphans), ", ".join(orphans[:5]),
                     "" if not stub_urls else
                     " {} delivered almost no HTML and no browser read that HTML, so any link "
                     "inside is invisible to this audit and a page listed above may be linked "
                     "from there.".format(
                         plural(len(stub_urls), "crawled page", "crawled pages"))),
        mechanism="G", root_cause="orphan-pages",
        summary="Link these pages from the navigation or from related content.",
        how_to_fix=[
            "For each page, decide where a visitor would expect to find it, and link it there.",
            "Add it to the navigation, a category listing, or a related-links block on a "
            "relevant page.",
            "If the page is genuinely obsolete, remove it from the sitemap instead.",
        ],
        effort="low", owner="content owner",
        rationale="A page nothing links to sits outside the site's structure. "
                  "Visitors who land on it cannot see how it relates to anything else, and "
                  "crawlers weigh it as unimportant because the site itself never points at it.",
        affected_pages=orphans,
        checked=("the first 300 internal links in each crawled page's HTML, which "
                 "is where the crawl stops recording them",
                 "the menu and footer links those pages carry, which are collected "
                 "separately and so survive that cut",
                 "the URLs the site's own sitemap lists"),
    )


def _unknown_paths_resolve(snapshot, snapshot_path=None):
    """Does this site answer HTTP 200 for a path that does not exist?

    True, False, or None when nothing in this run settled it.

    It matters here because it decides whether a link resolving means
    anything. A browser-compatibility database serves its app shell with HTTP
    200 for every unknown path, including the invented one this audit requests
    to prove it, and this skill still printed "all 98 internal link target(s)
    tested resolved successfully" - a sentence that is true of every URL on
    that site, including URLs nobody ever published.

    `crawl-access-audit` runs the probe and emits the answer as a signal.
    Signals reach `compose_report.py`, not each other's processes, so it is
    read from the two places it can be found: the crawl record, if the crawl
    ever carries it, and otherwise that skill's own findings file, which the
    orchestrator writes beside the snapshot before this skill runs.
    """
    recorded = (snapshot.get("crawl") or {}).get("unknown_path_status")
    if recorded is not None:
        return recorded == 200
    if not snapshot_path:
        return None
    sibling = os.path.join(os.path.dirname(os.path.abspath(snapshot_path)),
                           "crawl-access-audit.findings.json")
    try:
        with open(sibling, "r", encoding="utf-8") as handle:
            signals = (json.load(handle) or {}).get("signals") or {}
    except (OSError, ValueError):
        return None
    value = signals.get("unknown_paths_resolve")
    return value if isinstance(value, bool) else None


# A label that is an email address, or the placeholder an email-obfuscation
# scheme leaves where one used to be. Most such schemes - edge networks, CMS
# plugins and standalone scripts alike - put the address in the href's
# fragment, and the fragment test above catches those without knowing anything
# about the scheme. This is for the rest: an anchor whose address is held
# somewhere the snapshot does not record, leaving a page path that no server
# serves as a page.
_EMAIL_LABEL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PROTECTED_EMAIL_LABEL_RE = re.compile(
    r"^[\[(]?\s*e-?mail\s+(?:address\s+)?protected\s*[\])]?$", re.I)


def _destination_is_not_the_recorded_url(link):
    """Why this anchor points somewhere other than the URL stored for it, or "".

    The class, of which an obfuscated email address is one instance: a link
    whose destination is carried in its fragment is not a link to the bare
    path. One such anchor appeared on 16 pages of one site, the audit spent a
    request proving the bare path answers 404, and the owner was told they had
    a broken internal link on every page of their site.
    """
    # The raw fragment the extractor kept beside the URL `normalise_url`
    # produced. This settles the whole class on its own: whatever the scheme
    # and whatever the vendor, an href with a fragment on it names a
    # destination the bare path does not.
    if str((link or {}).get("fragment") or "").strip():
        return "carries its destination in a fragment, which the recorded URL does not include"
    # The second reading, for an anchor that carries its address somewhere the
    # snapshot does not hold at all - a `data-` attribute, or a payload a
    # script fetches. It claims only what is on the page: the label is an
    # address and the href is a page path, so the address is what the link
    # offers a visitor and the path is not.
    label = str((link or {}).get("text") or "").strip()
    if label and (_EMAIL_LABEL_RE.match(label) or _PROTECTED_EMAIL_LABEL_RE.match(label)):
        return "shows an email address as its whole label while pointing at a page path"
    return ""


def _targets_that_are_not_page_addresses(pages):
    """{url: why} for internal link targets that are not what the anchor points at."""
    unreadable, plain = {}, set()
    for page in pages:
        for link in ((page.get("links") or {}).get("internal") or []):
            url = (link or {}).get("url") or ""
            if not url:
                continue
            why = _destination_is_not_the_recorded_url(link)
            if why:
                unreadable.setdefault(url, why)
            else:
                plain.add(url)
    # A URL that some anchor somewhere links plainly is a page address whatever
    # another anchor did with it. `/docs#install` and `/docs` are one document,
    # and refusing to probe `/docs` because one link jumped into the middle of
    # it would drop a real target from the sample.
    return {url: why for url, why in unreadable.items() if url not in plain}


# What the crawl marks on an anchor carrying `aria-hidden="true"` together with
# `tabindex="-1"`, and the one definition of "a link a visitor can follow" that
# this skill and `crawl.py` both apply. The reasoning for that pair, and for
# rejecting `display:none`, `rel=nofollow` and `rel=noindex` as evidence on
# their own, is written out above `anchors_no_visitor_can_reach` in `crawl.py`.
_UNREACHABLE_FLAG = "unreachable_by_a_visitor"

_NO_VISITOR_CAN_FOLLOW = (
    'carries `aria-hidden="true"` together with `tabindex="-1"`, so it is absent from '
    "the accessibility tree and from the keyboard focus order alike and nothing a "
    "visitor does reaches it")


def _targets_no_visitor_can_follow(pages):
    """{url: why} for targets every anchor on this site closes to people.

    This skill's question is whether the person who clicked through stays. A
    hosted site builder injects an empty `aria-hidden`, `tabindex="-1"` anchor
    into every page it serves, pointing at an asset route that answers 404 to
    anything but its own script; that route was reported to the owner of a
    two-page site as an internal link returning an error, under a rationale
    saying "a broken link is a visitor stopped mid-journey". No visitor can be
    stopped by an element no visitor can see, announce or focus.

    The same rule as the fragment case above: a URL some anchor somewhere
    offers plainly is a link this site offers, whatever another anchor did with
    it, so it stays in the sample and is probed normally.
    """
    hidden, plain = {}, set()
    for page in pages:
        for link in ((page.get("links") or {}).get("internal") or []):
            url = (link or {}).get("url") or ""
            if not url:
                continue
            if (link or {}).get(_UNREACHABLE_FLAG):
                hidden.setdefault(url, _NO_VISITOR_CAN_FOLLOW)
            else:
                plain.add(url)
    return {url: why for url, why in hidden.items() if url not in plain}


def _check_broken_links(result, snapshot, pages, fetcher, why_no_fetcher="",
                        snapshot_path=None):
    """Which internal link targets are dead.

    Runs with or without a fetcher. The crawl already fetched dozens of pages
    with GET and recorded their statuses, so a 404 the crawl walked into is a
    broken link whether or not this skill may probe anything - and returning
    early threw that away, so `--no-network` reported no broken links on a site
    with a known 404 in its own snapshot.
    """
    result.check("broken-internal-links")
    known = {p["url"]: p.get("status") for p in snapshot.get("pages") or []}
    known_refusals = {p["url"]: bool(p.get("edge_refusal"))
                      for p in snapshot.get("pages") or []}
    # What some page on this site actually points at. The crawl's page table is
    # not that: it also holds up to 8 URLs seeded straight from the sitemap,
    # which no page has to link to. Judging every crawled page as a link target
    # broke this finding three ways at once - a snapshot with three healthy
    # pages, one sitemap-seeded 404 and not a single `<a href>` anywhere
    # reported "1 of 4 tested internal link targets (25.0%) are gone", so the
    # rate was the crawl size rather than the link count and cleared
    # BROKEN_LINK_HIGH on any small site, the 404 was called a broken internal
    # link when nothing links to it, and its URL went into `affected_pages` as
    # if some page had to be edited.
    #
    # Compared through `_orphan_key` because a menu and the crawl queue do not
    # have to agree on `www.` or on a trailing slash.
    #
    # Targets whose recorded URL is not the destination the anchor names are
    # left out of both halves: not probed, and not judged from a status the
    # crawl already has. See `_targets_that_are_not_page_addresses`.
    unreadable = _targets_that_are_not_page_addresses(pages)
    result.signal("link_targets_that_are_not_page_addresses", sorted(unreadable))
    # The second exclusion, kept as its own set and its own signal because it
    # is a different claim: the address is a real page address, and no anchor
    # on this site offers it to a person. See `_targets_no_visitor_can_follow`.
    unfollowable = _targets_no_visitor_can_follow(pages)
    result.signal("link_targets_no_visitor_can_follow", sorted(unfollowable))
    set_aside = dict(unreadable)
    for url, why in unfollowable.items():
        set_aside.setdefault(url, why)
    unreadable_keys = {_orphan_key(url) for url in set_aside}

    # Which pages carry each link, as well as which targets are linked. A dead
    # target is fixed on the page that links to it, and a report that named
    # only the target - `https://<host>/fil/<code-host>/<user>` - never said
    # that the link to fix sits on `/fil/`.
    linked, carried_by = set(), {}
    for page in pages:
        for link in ((page.get("links") or {}).get("internal") or []):
            key = _orphan_key(link.get("url") or "")
            if key and key not in unreadable_keys:
                linked.add(key)
                carried_by.setdefault(key, set()).add(page["url"])

    # robots.txt governs this probe too. The crawl filters its own queue, so a
    # disallowed path is never fetched and therefore never lands in `known` -
    # which put it straight into the candidate set here, and this check then
    # sent a HEAD request to the one class of URL the whole marketplace
    # promises not to touch. A site that disallows `/search` had its search
    # URLs probed on every run.
    robots = snapshot.get("robots") or {}
    known_keys = {_orphan_key(url) for url in known}
    candidates = set()
    for page in pages:
        for link in ((page.get("links") or {}).get("internal") or []):
            url = link.get("url") or ""
            if not url or _orphan_key(url) in known_keys:
                continue
            if _orphan_key(url) in unreadable_keys:
                continue
            if robots.get("groups") and is_disallowed(robots, USER_AGENT, path_of(url)):
                continue
            candidates.add(url)

    # Pages the crawl fetched with GET: a verdict on those needs no probe.
    broken, checked, unchecked = [], 0, 0
    for url, status in known.items():
        if _orphan_key(url) not in linked:
            continue
        verdict = link_verdict(status, known_refusals.get(url, False))
        if verdict == "dead":
            broken.append((url, status))
            checked += 1
        elif verdict == "alive":
            checked += 1
        elif status is not None:
            unchecked += 1

    # Links the crawl never reached. HEAD is the polite way to ask, but only
    # where HEAD means anything: three of eight major commercial sites answer
    # 403 to HEAD and 200 to GET, and probing them would manufacture broken
    # links that are not broken. The crawl establishes this once per site.
    head_supported = (snapshot.get("crawl") or {}).get("head_supported")
    probe_note = ""
    if fetcher is None:
        # Nothing beyond the crawl. The unreached candidates are counted as
        # unchecked rather than silently dropped, which is the rule this skill
        # already applies to a link a server refused.
        unchecked += min(len(candidates), BROKEN_LINK_SAMPLE)
        probe_note = " " + why_no_fetcher if why_no_fetcher else ""
    elif head_supported is False:
        # Only ever as many as we would have sampled. Counting every candidate
        # would report hundreds of "unchecked" targets on a large site, when
        # the check would have probed twenty of them at most - true in the
        # letter and misleading in the reading.
        unchecked += min(len(candidates), BROKEN_LINK_SAMPLE)
    else:
        for url in sample(sorted(candidates), BROKEN_LINK_SAMPLE):
            response = fetcher.try_get(url, method="HEAD")
            status = response.status_code if response is not None else None
            verdict = link_verdict(status)
            if verdict == "dead":
                # HEAD may say alive on its own; it may not say dead on its
                # own. Support is per-URL, not per-site, and a site that
                # answers HEAD honestly on its homepage can still 404 to HEAD
                # on an article that serves 200 to a reader.
                status = confirm_dead(fetcher, url, status)
                verdict = link_verdict(status)
            if verdict == "dead":
                broken.append((url, status))
                checked += 1
            elif verdict == "alive":
                checked += 1
            else:
                unchecked += 1

    result.signal("link_targets_checked", checked)
    result.signal("link_targets_unchecked", unchecked + len(set_aside))

    # Why anything is unchecked, said once and reused, because a check that
    # goes quiet without explaining itself is the thing this marketplace
    # promises not to do.
    #
    # Accumulated rather than chosen. A single branch said "the server refused
    # or errored" about every unchecked target, which would be a false account
    # of a target this audit deliberately never requested.
    reasons = []
    if unreadable:
        reasons.append(
            "{} not requested at all, because the anchor's real destination is "
            "not the URL recorded for it: each {}, so the bare path is not something the page "
            "links to and its status would say nothing about the site".format(
                plural(len(unreadable), "target was", "targets were"),
                "; each ".join(sorted(set(unreadable.values())))))
    # Said separately from the sentence above, because it is a different
    # statement about a different defect: those addresses are not what the
    # anchor points at, these are addresses no anchor offers a person.
    if unfollowable:
        reasons.append(
            "{} not requested at all, because every anchor on this site that "
            "points at them {} - so a status for them would say nothing about what a visitor "
            "can reach".format(plural(len(unfollowable), "target was", "targets were"),
                               _NO_VISITOR_CAN_FOLLOW))
    if not unchecked:
        # "0 further target(s) were not probed" was printed on every run with
        # no fetcher, including runs where every link target had already been
        # answered by the crawl.
        pass
    elif probe_note:
        reasons.append("{} not probed because {}".format(
            plural(unchecked, "further target was", "further targets were"),
            probe_note.strip()))
    elif head_supported is False:
        reasons.append(
            "{} unchecked because this site refuses HEAD requests "
            "while answering GET normally, and verifying them would mean fetching every one "
            "in full".format(plural(unchecked, "further target is", "further targets are")))
    else:
        reasons.append("{}".format(plural(
            unchecked,
            "further target could not be verified - the server refused or errored rather "
            "than answering - and is counted neither way",
            "further targets could not be verified - the server refused or errored rather "
            "than answering - and are counted neither way")))
    why_unchecked = ("; " + "; ".join(reasons)) if reasons else ""

    if not checked:
        result.skip("broken-internal-links",
                    ("no internal link target could be verified" + why_unchecked)
                    if reasons else "no internal links were available to test")
        return
    if not broken:
        # On a site that answers 200 for everything, "every link resolved" is
        # a fact about the server and not about the links. Said in the same
        # sentence, because a reader who takes the clean pass at face value
        # there has been reassured by nothing.
        soft_404_site = _unknown_paths_resolve(snapshot, snapshot_path)
        result.skip("broken-internal-links",
                    "all {} tested resolved successfully{}{}".format(
                        plural(checked, "internal link target", "internal link targets"),
                        why_unchecked,
                        ". This site also answers HTTP 200 for a path that does not exist, "
                        "which this audit tested with a URL it invented, so a link resolving "
                        "here is not evidence that its target is a real page: a link to a "
                        "page that has been deleted resolves exactly the same way"
                        if soft_404_site else ""))
        return

    rate = len(broken) / float(checked)
    # Source and target, for every dead link. The page to edit is the source;
    # the target is what it points at. A target no crawled page links to is
    # impossible here - `linked` above and the probe candidates are both read
    # off pages' own links - so the fallback is only ever the target itself.
    sources = {url: sorted(carried_by.get(_orphan_key(url)) or []) for url, _ in broken}
    result.signal("broken_link_targets", {url: srcs for url, srcs in sorted(sources.items())})
    scheme_less = {url: _written_without_a_scheme(url) for url, _ in broken}
    scheme_less = {url: rest for url, rest in scheme_less.items() if rest}

    def carried(url):
        srcs = sources.get(url) or []
        if not srcs:
            return "{} (no crawled page's link to it was recorded)".format(url)
        return "{} links to {}".format(" and ".join(example_urls(srcs, 2)), url)

    examples = "; ".join("{} -> {}".format(carried(u), s) for u, s in sorted(broken)[:5])
    missing_scheme = ""
    if scheme_less:
        missing_scheme = (
            " {} written without `https://`: {}. A browser reads an address with no scheme "
            "as a path on the site it is on, so the link lands under the page carrying it and "
            "gets an error instead of reaching the other site.".format(
                "That link is" if len(scheme_less) == 1 else
                "{} of those links are".format(len(scheme_less)),
                "; ".join("{} links `{}`, meaning `https://{}`".format(
                              " and ".join(example_urls(sources.get(url) or [], 2))
                              or "the page carrying it", rest, rest)
                          for url, rest in sorted(scheme_less.items())[:3])))
    steps = []
    if scheme_less:
        steps.append(
            "Add `https://` to the start of each link written without it - {} - on the page "
            "named beside it. That is the whole fix for those: the site they name exists, and "
            "the link is being read as a path on this one.".format(
                "; ".join("on {}, `{}` becomes `https://{}`".format(
                              " and ".join(example_urls(sources.get(url) or [], 2))
                              or "the page carrying it", rest, rest)
                          for url, rest in sorted(scheme_less.items())[:3])))
    steps += [
        "On each page named above, correct the link to its current URL, or remove it if the "
        "destination is gone.",
        "Add a 301 from the old URL where the page moved, so existing links keep working.",
        "Run a link check as part of publishing so this does not accumulate again.",
    ]
    pages_to_edit = sorted({src for srcs in sources.values() for src in srcs}
                           | {url for url, srcs in sources.items() if not srcs})
    result.add(
        id_hint="broken-internal-links",
        title="{} an error".format(
            plural(len(broken), "internal link target returns", "internal link targets return")),
        severity="high" if rate > BROKEN_LINK_HIGH else "medium", confidence="high",
        evidence="{} of {} tested internal link targets ({}%) are gone: {}.{}{}".format(
                     len(broken), checked, pct(len(broken), checked), examples,
                     missing_scheme,
                     " A further {} could not be verified and are excluded from "
                     "the rate.".format(unchecked + len(set_aside))
                     if (unchecked or set_aside) else ""),
        mechanism="G", root_cause="broken-links",
        summary="Fix or remove the internal links that lead nowhere, on the pages that carry "
                "them.",
        how_to_fix=steps,
        effort="medium", owner="developer",
        rationale="A broken link is a visitor stopped mid-journey with nothing to "
                  "do but leave. At this rate it is happening often enough to be a pattern "
                  "rather than an accident.",
        # The pages carrying the dead links, because those are the pages an
        # owner opens to fix them. The targets are in the evidence above and in
        # the `broken_link_targets` signal.
        affected_pages=pages_to_edit,
    )


# A path segment that is a host name: labels joined by dots, ending in a label
# of letters. `code-host.test` in `/fil/code-host.test/<user>` is one; `news.html` is
# not, because a host name is followed by more of the address or starts with
# `www.`, and a file name ends it.
_HOST_SEGMENT_RE = re.compile(
    r"^(?:www\.[a-z0-9-]+(?:\.[a-z0-9-]+)*|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24})$", re.I)


def _written_without_a_scheme(url):
    """The part of `url` that is another site's address, or "".

    A link written `code-host.test/<user>` rather than `https://code-host.test/<user>`
    is read by every browser as a relative path, so on `/fil/` it resolves to
    `/fil/code-host.test/<user>` on this site and answers 404. Read off the
    resolved address, because the crawl records that and not the raw `href`:
    a segment shaped like a host name, which either starts with `www.` or has
    more of the address after it, is where the other site's address begins.
    """
    path = urlparse(url or "").path
    segments = [s for s in path.split("/") if s]
    for index, segment in enumerate(segments):
        if not _HOST_SEGMENT_RE.match(segment):
            continue
        if segment.lower().startswith("www.") or index < len(segments) - 1:
            rest = "/".join(segments[index:])
            query = urlparse(url).query
            return rest + ("?" + query if query else "")
    return ""


def _path_segments(url):
    """How many path segments sit below the root: `/docs.html` is 1, `/a/b/` is 2."""
    return len([s for s in urlparse(url or "").path.split("/") if s])


def _check_breadcrumbs(result, pages):
    """Visible breadcrumb navigation, distinct from BreadcrumbList markup.

    structured-data-audit owns the machine-readable version; this owns whether
    a human can see where they are.
    """
    result.check("breadcrumb-navigation")
    # Deep by its address as well as by its type and its crawl depth. A page one
    # segment below the root - `/docs.html`, `/news/` - sits at the top level,
    # where the menu is the trail and a breadcrumb would read "Home > This
    # page". Two documentation hubs one click from a homepage were counted as
    # deep pages missing a trail. The one exception is the site's own word: a
    # top-level page whose markup declares a BreadcrumbList has been given a
    # trail by its template, and one a visitor cannot see is the gap this
    # check exists for.
    deep = [p for p in pages if p["page_type"] in DEEP_TYPES and p.get("depth", 0) >= 1
            and (_path_segments(p.get("url")) >= 2
                 or (p.get("breadcrumb") or {}).get("markup"))]
    if len(deep) < 3:
        result.skip("breadcrumb-navigation",
                    "fewer than 3 deep pages were crawled, so breadcrumbs would not change "
                    "how the site is navigated")
        return
    # Three readings, because one detector looking for the word "breadcrumb"
    # in an attribute told a restaurant group that all 28 of its deep pages
    # show no trail, when every one of them ships a plain <ul> of links from
    # the homepage down. The visible element decides it first; a body link
    # pointing up to the page's own parent section gives the visitor the same
    # click and so counts too. BreadcrumbList markup does not: markup a
    # visitor cannot see does not help the visitor who landed mid-journey, and
    # structured-data-audit owns it. It is read only to say whether the site
    # has any breadcrumb concept at all.
    without = [p for p in deep
               if not (p.get("breadcrumb") or {}).get("visible")
               and not _links_to_own_parent(p)]
    marked_up = sum(1 for p in without if (p.get("breadcrumb") or {}).get("markup"))
    if len(without) < len(deep) * 0.5:
        result.skip("breadcrumb-navigation",
                    "{} of {} deep pages already show a breadcrumb trail".format(
                        len(deep) - len(without), len(deep)))
        return

    result.add(
        id_hint="deep-pages-have-no-breadcrumb",
        title="Deep pages give visitors no visible sense of where they are",
        severity="medium", confidence="high",
        evidence="{} of {} crawled deep pages show no breadcrumb trail and no link up to the "
                 "section they sit in.{} Examples: {}.".format(
                     len(without), len(deep),
                     " {} of them do carry BreadcrumbList structured data, which a machine "
                     "reads and a visitor cannot see.".format(marked_up) if marked_up else "",
                     ", ".join(example_urls([p["url"] for p in without]))),
        mechanism="G", root_cause="no-breadcrumbs",
        summary="Add a visible breadcrumb trail to deep page templates.",
        how_to_fix=[
            "Add a breadcrumb above the H1 on whichever templates sit below the top level "
            "of this site: Home > Section > This page.",
            "Make every step except the current page a working link.",
            "Mirror it in BreadcrumbList structured data so machines read the same hierarchy.",
        ],
        effort="low", owner="developer",
        rationale="Someone arriving from an answer lands deep with no history. A "
                  "breadcrumb is the cheapest way to show them what section they are in and "
                  "give them one click upward instead of a click back.",
        affected_pages=[p["url"] for p in without],
        checked=("a breadcrumb element named in each page's own markup",
                 "links in the page body pointing up to its parent section")
                + (() if marked_up else ("BreadcrumbList structured data on those pages",)),
    )



def _orphan_key(url):
    """One spelling for a URL, so a sitemap and a menu can be compared.

    Host lowercased with `www.` dropped, path without its trailing slash. The
    query is kept: on plenty of sites it selects the content.
    """
    normalised = normalise_url(url or "")
    if not normalised:
        return ""
    parts = urlparse(normalised)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    return "{}{}{}".format(host, path, ("?" + parts.query) if parts.query else "")


def _same_host(url, host):
    """True when this URL is on `host`, ignoring `www.`."""
    other = urlparse(normalise_url(url or "")).netloc.lower()
    return (other[4:] if other.startswith("www.") else other) == host


def _chrome_paths(page, buckets=("nav", "footer")):
    """Same-site paths this page links from its menu and footer.

    A second reading of the same chrome, independent of `chrome_signature`.
    `chrome_signature` only looks inside <nav>, <header>, <footer> and the
    matching ARIA roles. `links.nav` falls back to a shape test that finds a
    menu built as a plain <ul> of links inside a <div>, which those selectors
    miss completely - so on a site built that way every page was reported as
    not carrying the site's own navigation.
    """
    links = page.get("links") or {}
    host = urlparse(normalise_url(page.get("url") or "")).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    paths = set()
    for bucket in buckets:
        for link in links.get(bucket) or []:
            url = (link or {}).get("url") or ""
            if not url or not _same_host(url, host):
                continue
            paths.add(urlparse(normalise_url(url)).path.rstrip("/") or "/")
    return paths


def _is_site_chrome(page, url):
    """Is this exact link one of the page's own menu or footer links?

    Both sides are normalised URLs written by the same extractor, so the
    comparison is a string comparison and not a guess. Used to say which of
    two different things is true of a call to action the body copy does not
    contain: that it is a menu item, or that its label could not be located
    at all.
    """
    if not url:
        return False
    links = page.get("links") or {}
    for bucket in ("nav", "footer"):
        for link in links.get(bucket) or []:
            if (link or {}).get("url") == url:
                return True
    return False


def _links_to_own_parent(page):
    """Does this page link, from its own copy, up to the section it sits in?

    The trail does not have to say "breadcrumb" anywhere. A restaurant group
    marks its trail as a bare <ul> whose only clue is a `data-` attribute, and
    all 28 of its deep pages were reported as showing no trail while the trail
    was on the screen. A link to the page's parent path is the thing a
    breadcrumb is for, whatever it is built out of.

    The site-wide menu and footer are excluded on purpose: a top-level
    "Products" link appears on every page of the site and says nothing about
    where this one page sits.

    Three readings, each forced by a trail this used to miss:

      a link inside the page's own content region to any address above it. A
        coffee roaster ends every blog post with "Back to <brand> Coffee
        Roasters", linking the blog index from inside `<main>`. The index is
        in the site menu as well, and the menu exclusion threw away the one
        link on the page that was the way up: 21 of 21 deep pages were
        reported as showing none. A link in the content region is the page's
        own, however many menus also carry its target.
        Only where the content region is the page's own: a region holding
        most of the menu and footer is the whole document read as content,
        and its menu links would pass every page.
      anywhere else on the page, a link to the parent address that is not one
        of the menu or footer links, as before. The parent only: a
        furniture retailer's logo links each regional storefront root, which
        sits above every page of that region, and taking grandparents from
        outside the content region passed every one of them on the logo.
      a link whose text is a step the page's own BreadcrumbList names. A
        furniture retailer shows "Home > Beds > Mattresses" above each
        product's H1, linking a category at `/sg/beds/mattress` while the
        product lives at `/sg/products/<slug>`, so no step of that trail is an
        address above the product's. Its BreadcrumbList names the same steps,
        "Beds" and "Mattresses", with `@id` values that do not resolve, so the
        names are the part that can be matched. A visible, non-menu link
        carrying the name the page's own markup gives a step of its trail is
        that trail, rendered. Steps named like the page itself, links to the
        homepage and links back to this same page do not count: a store
        locator's markup lists "Store Locator" as its own last step, and its
        own address in the menu matched it.
    """
    path = urlparse(normalise_url(page.get("url") or "")).path.rstrip("/")
    links = page.get("links") or {}
    chrome = {(link or {}).get("url") for bucket in ("nav", "footer")
              for link in links.get(bucket) or []}

    def path_of(url):
        return urlparse(normalise_url(url or "")).path.rstrip("/") or "/"

    here = path or "/"
    if path.count("/") >= 2:
        parent = path.rsplit("/", 1)[0]
        ancestors = set()
        step = path
        while step.count("/") >= 2:
            step = step.rsplit("/", 1)[0]
            ancestors.add(step)
        main = [link for link in links.get("main_internal_links") or []
                if isinstance(link, dict)]
        chrome_in_main = len(chrome & {link.get("url") for link in main})
        if chrome_in_main < max(3, len(chrome) // 2):
            for link in main:
                if path_of(link.get("url")) in ancestors:
                    return True
        for link in links.get("internal") or []:
            url = (link or {}).get("url") or ""
            if url and url not in chrome and path_of(url) == parent:
                return True

    steps = _breadcrumb_step_names(page)
    if not steps:
        return False
    for link in links.get("internal") or []:
        url = (link or {}).get("url") or ""
        text = re.sub(r"\s+", " ", (link or {}).get("text") or "").strip().casefold()
        if not url or url in chrome or not text or path_of(url) in ("/", here):
            continue
        if text in steps:
            return True
    return False


def _breadcrumb_step_names(page):
    """The names this page's own BreadcrumbList gives the steps above it.

    Lowercased, and without any step named like the page itself - its `<h1>`
    or its title - or like the homepage, since a link to either says nothing
    about which section the page sits in.
    """
    own = {re.sub(r"\s+", " ", text or "").strip().casefold()
           for text in list((page.get("headings") or {}).get("h1") or [])
           + [page.get("title") or ""]}
    names = set()
    for block in page.get("jsonld") or []:
        if not isinstance(block, dict):
            continue
        if "breadcrumblist" not in str(block.get("@type") or "").lower():
            continue
        for element in block.get("itemListElement") or []:
            if not isinstance(element, dict):
                continue
            item = element.get("item")
            name = element.get("name") or (item.get("name") if isinstance(item, dict) else "")
            name = re.sub(r"\s+", " ", str(name or "")).strip().casefold()
            if len(name) >= 3 and name not in own and name != "home":
                names.add(name)
    return names


def _repeated_end(pages, index):
    """True when the segment at `index` is the same on most of these titles.

    A title's brand is the part that does not change. Whichever end repeats
    across more than half the pages is the brand; the other end is the page.
    """
    ends = []
    for page in pages:
        parts = [p.strip().lower() for p in
                 re.split(r"\s[|\-–—:·•]\s", page.get("title") or "") if p.strip()]
        if len(parts) > 1:
            ends.append(parts[index])
    if len(ends) < 3:
        return False
    return max(Counter(ends).values()) > len(ends) / 2.0


# Writing systems that spell a whole syllable, or a whole word, in a single
# character. `UNSPACED_SCRIPTS` in the shared module is a different list for a
# different reason - it names the scripts that put no space between one word
# and the next - and the two only partly overlap. Hangul writes spaces, so it
# is absent from that list and arrives here.
SYLLABIC_SCRIPTS = frozenset({"hangul", "kana", "han"})


def _title_words_are_comparable(page):
    """Can this page's words be matched against its title's, one word at a time?

    Everything below rests on two properties of the writing system, and the
    check is only honest where it can confirm both.

    The first is spaces: the comparison splits the title on them, so a script
    that writes none has no words to split into.

    The second is that one character spells part of a sound rather than a whole
    syllable. The match below keeps a title word's first six characters, so
    that a word the page writes in another grammatical form still counts -
    "reports" found in "reporting". Where one character is a syllable, six
    characters is six syllables, which is longer than almost every whole word,
    so the window trims nothing and the test silently becomes an exact match on
    the surface form. In a language that marks grammar by adding a syllable to
    the end of a word, the title and the sentence write the same noun with
    different endings, so an exact match fails on words the page does deliver.
    A homepage whose title was the company's own tagline was reported as
    delivering none of it and named three words missing whose stems were all in
    its first paragraph. No shorter window recovers this: two syllables of a
    three-syllable stem matches unrelated words. Whitespace-token overlap is
    not measurable there at any threshold, so the check says so instead.

    The script has to be read from the page body. `dominant_script` returns
    None below 20 letters, and a title is shorter than that, so a test asked of
    the title alone answers "comparable" for every short non-Latin title there
    is.
    """
    body = page.get("body_text") or ""
    if not words_are_separated(body):
        return False
    return dominant_script(body) not in SYLLABIC_SCRIPTS


def _set_aside_note(listings, unmeasured):
    """What was left out of the comparison, for the reason a decline gives."""
    notes = []
    if listings:
        notes.append("{}, so a title naming the category is delivered by the list rather "
                     "than by prose".format(
                         plural(len(listings),
                                "page whose content is a list of other pages was left out: "
                                "its words are the names of the items it lists",
                                "pages whose content is a list of other pages were left out: "
                                "their words are the names of the items they list")))
    if unmeasured:
        notes.append("on {} the comparison could not run at all: the text there is written "
                     "in a script that spells a whole syllable in one character, where matching "
                     "a title word by its opening characters cannot separate the word from its "
                     "grammatical ending".format(plural(len(unmeasured), "page", "pages")))
    return notes


# --------------------------------------------------------------------------
# One heading, ten pages
#
# A volunteer-run hall's ten pages all carry the same `<h1>`: the site's own
# name, wrapped in a link back to the root. No page's top-level heading names
# what that page is about. The only heading check in this marketplace fires on
# a *missing* H1, so an identical site-wide H1 read as a clean pass on nine
# pages - and it is the same defect one step disguised. A heading that does not
# change cannot be describing pages that do, and a machine deciding which part
# of a site answers a question gets exactly as much from it as from no heading
# at all.
#
# Whose question this is: the machine-readable side of a page's identity - the
# `name` of a WebPage or Organization node - belongs to `structured-data-audit`
# and is untouched here. The heading a person reads at the top of the page
# belongs to this skill, because the visitor who arrived mid-journey reads that
# heading to find out whether they are in the right place, and the site's name
# answers that question the same way on every page of the site.
#
# It deliberately carries none of the guards `title-body-alignment` learned:
# that check matches a title's words against the body one word at a time, so it
# declines on listing pages, whose words are the names of the things they list,
# and on scripts that spell a whole syllable per character, where matching a
# word by its opening characters cannot separate it from its ending. This
# compares whole strings for equality and matches no words at all, so neither
# limit applies - a listing page whose H1 is the site's name is as unhelpful as
# any other page's, in any writing system.

# How much of a site's pages have to carry one and the same H1 before that
# heading is naming the site rather than any page.
SITEWIDE_H1_SHARE = 0.8

# And how many pages that has to be. On three pages "the same on all of them"
# is a coincidence - a site can genuinely have three pages about one subject -
# and the finding would rest on two comparisons.
SITEWIDE_H1_MIN_PAGES = 4


def _h1s_of(page):
    """Every H1 this page carries, tidied for comparison.

    Read from the whole markup as well as from the visible document, and for
    the same reason the heading check in `fact-extractability-audit` reads
    both: `headings` is built with hidden markup removed, which is right for
    reading a page as prose and wrong for asking what heading the document
    carries, and themes routinely ship a screen-reader-only H1 that names the
    page exactly as a machine wants.
    """
    out = []
    for source in ("headings_in_markup", "headings"):
        value = (page.get(source) or {}).get("h1")
        if isinstance(value, str):
            out.append(value)
        elif value:
            out.extend(str(v) for v in value)
    return [text for text in
            (re.sub(r"\s+", " ", str(v)).strip() for v in out) if text]


def _one_line(value):
    """A heading or title with its whitespace collapsed, for comparing."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _check_page_headings_name_the_page(result, pages, home=None, brand_name=""):
    result.check("page-headings-name-their-own-page")
    judged = [p for p in pages if _h1s_of(p)]
    if len(judged) < SITEWIDE_H1_MIN_PAGES:
        result.skip("page-headings-name-their-own-page",
                    "only {} an H1 at all, fewer than the {} it takes to tell a heading "
                    "that names the site from a site that happens to have a few pages about "
                    "one subject".format(
                        plural(len(judged), "crawled content page carries",
                               "crawled content pages carry"),
                        SITEWIDE_H1_MIN_PAGES))
        return

    carried = Counter()
    for page in judged:
        carried.update({text.casefold() for text in _h1s_of(page)})
    shared, carriers = carried.most_common(1)[0]
    if carriers < max(SITEWIDE_H1_MIN_PAGES, len(judged) * SITEWIDE_H1_SHARE):
        result.skip("page-headings-name-their-own-page",
                    "the {} carrying an H1 carry {} between them, so the top-level heading "
                    "changes with the page rather than naming the site".format(
                        plural(len(judged), "crawled content page"),
                        plural(len(carried), "distinct heading")))
        return

    # A page counts only when that repeated string is its *whole* set of H1s -
    # a page carrying the site's name and its own subject as two H1s has said
    # what it is - and only when its `<title>` says something else. That second
    # condition is the second reading: it is what establishes that the page has
    # a subject of its own for the heading to have failed to name. Without it
    # this would fire on a site that really is one page at ten addresses.
    home_url = (home or {}).get("url")
    repeated = [p for p in judged
                if {text.casefold() for text in _h1s_of(p)} == {shared}
                and p.get("url") != home_url
                and _one_line(p.get("title")).casefold() != shared]
    if len(repeated) < SITEWIDE_H1_MIN_PAGES:
        result.skip("page-headings-name-their-own-page",
                    "{}, so there is no page here whose heading withholds a subject the page "
                    "itself states".format(
                        plural(len(judged) - len(repeated),
                               "crawled content page carries a top-level heading of its own, "
                               "or says the same thing in its title as in its H1",
                               "crawled content pages carry a top-level heading of their own, "
                               "or say the same thing in their title as in their H1")))
        return

    written = next((text for text in _h1s_of(repeated[0])
                    if text.casefold() == shared), shared)
    # Whether the repeated heading is also the name the site declares for
    # itself. An observation rather than a premise: the finding stands on the
    # heading being the same everywhere, and this only tells the reader what
    # the string in front of them is.
    names_the_site = bool(brand_name) and _one_line(brand_name).casefold() in shared
    result.add(
        id_hint="page-headings-do-not-name-the-page",
        title="{} the same H1 as the rest of the site and nothing of their own".format(
            plural(len(repeated), "page carries", "pages carry")),
        severity="medium", confidence="high",
        evidence="{} of the {} crawled content pages that carry an H1 have one and the same "
                 "top-level heading, \"{}\"{}, as their only H1, while each states a "
                 "different subject in its own <title>. The homepage is not counted: a "
                 "homepage whose H1 is the site's name is naming its own subject. "
                 "Examples: {}.".format(
                     len(repeated), len(judged), truncate(written, 80),
                     " - which is also the name this site declares for itself"
                     if names_the_site else "",
                     ", ".join(example_urls([p["url"] for p in repeated]))),
        mechanism="G", root_cause="no-orientation",
        summary="Give every page an H1 that names that page's subject, and leave the site's "
                "name to the logo and the title.",
        how_to_fix=[
            "In the page template, set the H1 from the page's own title rather than from the "
            "site name.",
            "Keep the site name where it belongs: the logo, which can stay a link back to the "
            "homepage, and the tail of the <title>.",
            "Because this is one template, one edit fixes every page built from it.",
        ],
        effort="low", owner="developer",
        rationale="A visitor sent here by an assistant reads the heading first to check they "
                  "are in the right place. A heading that says the same thing on every page "
                  "answers that for none of them, and a machine choosing which page of this "
                  "site answers a question has nothing to choose on.",
        affected_pages=[p["url"] for p in repeated],
        # Two readings, and the second is what makes this more than a
        # curiosity: the pages are demonstrably about different things, and the
        # heading is the same on all of them.
        checked=("the h1 elements on every crawled content page, read from the "
                 "delivered markup as well as from the visible document, so a "
                 "heading hidden with CSS still counts as a heading",
                 "each of those pages' own <title>, which states a different "
                 "subject from the shared heading on every page counted here"),
    )


# --------------------------------------------------------------------------
# A page that names fifteen subjects names none
#
# One homepage carried 15 `<h1>` elements. The only heading check
# that existed reports a page with *zero* H1s - it lives in
# `fact-extractability-audit`, and its own comment records why it stops there:
# reporting more than one fired it on 27 of 29 real sites, because two or three
# H1s are ordinary HTML5 sectioning and stop no machine reading the page.
# Fifteen is not sectioning. Fifteen tells a machine that fifteen different
# things are the subject of this page, which is the same defect as none at all
# seen from the other side, and nothing in the marketplace said a word about it.
H1_FLOOD = 10
# Chosen, not measured, and the reason there is no measurement is the whole
# point of the number: this repository's six fixture sites carry at most one H1
# on each of their 42 pages, so they cannot tell 10 from 3, and the corpus the
# zero-H1 check was tuned on measured only how often a page carries *more than
# one*. What 10 is chosen against is the shape of the two failures either side
# of it. Below it sit the patterns that are correct or harmless - an article
# with a sectioned H1 per `<section>`, a theme shipping a visible H1 beside a
# screen-reader-only one, a slider repeating its title. Above it sits a
# template that has used `<h1>` as a style for every card in a grid, which is
# the case seen in the wild at 15. A page genuinely about ten different subjects is a
# page with no subject, whichever way the tag got there.
#
# Counted over *distinct* strings, folded across both readings of the page's
# headings, so a heading duplicated between the visible document and the whole
# markup - which `_h1s_of` deliberately reads twice - counts once, and a slider
# that repeats one heading ten times counts once.


def _check_one_subject_per_page(result, pages):
    """Pages whose top-level heading names too many things to name one."""
    result.check("too-many-top-level-headings")
    flooded = []
    for page in pages:
        distinct = {text.casefold() for text in _h1s_of(page)}
        if len(distinct) >= H1_FLOOD:
            flooded.append((page, len(distinct)))
    if not flooded:
        worst = max((len({t.casefold() for t in _h1s_of(p)}) for p in pages), default=0)
        result.skip("too-many-top-level-headings",
                    "the most top-level headings on any crawled content page is {}, under "
                    "the {} it takes to say that a page names no one subject. Two or three "
                    "H1s are ordinary HTML5 sectioning and are deliberately not counted as a "
                    "defect here".format(plural(worst, "distinct heading"), H1_FLOOD))
        return

    flooded.sort(key=lambda pair: (-pair[1], pair[0]["url"]))
    page, count = flooded[0]
    quoted = sorted({truncate(text, 40) for text in _h1s_of(page)})[:4]
    home = next((p for p, _n in flooded if p.get("page_type") == "home"), None)
    result.add(
        id_hint="page-declares-many-subjects",
        title="{} many top-level headings to name any one subject".format(
            plural(len(flooded), "page carries too", "pages carry too")),
        severity="medium", confidence="high",
        evidence="{} carries {} - {}, among them \"{}\". An <h1> states what the page is "
                 "about, so {} of them state {} different things and a machine choosing "
                 "which page of this site answers a question has no single subject to match "
                 "against.{} Repeats are folded first: a heading carried in both the visible "
                 "document and the delivered markup, or repeated by a slider, counts once. "
                 "Two or three H1s are ordinary HTML5 sectioning and are not counted "
                 "here.{}".format(
                     page["url"], plural(count, "distinct top-level heading"),
                     "the most of any page crawled" if len(flooded) > 1
                     else "the only page crawled that carries {} or more".format(H1_FLOOD),
                     '", "'.join(quoted), count, count,
                     " This is the site's homepage, which has exactly one subject - the site."
                     if home is not None and home["url"] == page["url"] else "",
                     " In total {}.".format(
                         plural(len(flooded), "page carries", "pages carry"))
                     if len(flooded) > 1 else ""),
        mechanism="G", root_cause="no-orientation",
        summary="Keep one <h1> per page, the one that names what the page is about, and "
                "demote the rest to <h2>.",
        how_to_fix=[
            "Find the template that emits the repeated <h1> - on a grid it is usually the "
            "card, on a long page the section header - and change the tag to <h2>. The "
            "styling can stay identical; only the tag changes.",
            "Leave one <h1> above them naming what the page as a whole is about.",
            "Because this is a template, one edit fixes every page built from it.",
            "Check it by fetching the page and counting: `curl -s <the page> | grep -c "
            "'<h1'`.",
        ],
        effort="low", owner="developer",
        rationale="A visitor sent here by an assistant reads the top-level heading first to "
                  "check they are in the right place. A page that answers that question "
                  "fifteen times has not answered it, and the machine that chose the page "
                  "had the same fifteen answers to choose between.",
        affected_pages=[p["url"] for p, _n in flooded],
        # Both readings of the page's headings, folded to distinct strings.
        # `headings` has hidden markup removed, which is right for reading a
        # page as prose and wrong for asking what the document carries;
        # `headings_in_markup` is the document. A count that used either alone
        # would be inflated by a screen-reader-only duplicate or deflated by a
        # visually hidden card title.
        checked=("the h1 elements on every crawled content page, read from the delivered "
                 "markup as well as from the visible document",
                 "the same page's h1 strings folded to distinct values, so a heading present "
                 "in both readings, or repeated by a carousel, is counted once"),
    )


# Words in a title that promise content somebody else's script supplies: the
# reviews, testimonials and ratings a review widget fills in after the page
# loads. English, like every other title-word test in this check.
_SCRIPT_SUPPLIED_TITLE_RE = re.compile(r"\b(?:reviews?|testimonials?|ratings?)\b", re.I)


def _promises_what_a_script_supplies(page):
    """True when the title promises reviews and the page loads outside scripts.

    A shop's "Customer Reviews and Testimonials" page was reported as not
    delivering what its title promises, "missing reviews, testimonials". The
    reviews are there for a visitor: a review widget loads them by script, and
    no browser ran. The vendor is never named here; that the page loads code
    from somewhere else and its title promises what such code delivers is the
    whole test. A page read by a browser is judged as usual.
    """
    if page.get("content_from") == "rendered":
        return False
    if not _SCRIPT_SUPPLIED_TITLE_RE.search(page.get("title") or ""):
        return False
    return bool((page.get("scripts") or {}).get("external_count"))


def _check_title_body_drift(result, pages):
    """Does the page deliver what its title promised?"""
    result.check("title-body-alignment")
    candidates, listings, unmeasured, supplied = [], [], [], []
    for page in pages:
        if not page.get("title") or page.get("body_text_len", 0) < 400:
            continue
        # A title naming a category, on the page that lists that category, is
        # not a broken promise. The candidate list had no page-type test at
        # all, so a new-arrivals grid titled for the two kinds of goods it
        # lists was reported as missing both of those words - absent from the
        # prose only because the grid's words are the individual product
        # names, which is what a listing page is.
        if is_listing_page(page):
            listings.append(page)
        elif not _title_words_are_comparable(page):
            unmeasured.append(page)
        elif _promises_what_a_script_supplies(page):
            supplied.append(page)
        else:
            candidates.append(page)
    set_aside = _set_aside_note(listings, unmeasured)
    if supplied:
        set_aside.append(
            "{} whose title promises reviews or testimonials and which load{} scripts from "
            "other addresses ({}): that is how a review widget delivers its reviews, no "
            "browser ran during this audit, so what the title promises may be on the page "
            "a visitor sees".format(
                plural(len(supplied), "page", "pages"), "s" if len(supplied) == 1 else "",
                ", ".join(example_urls([p["url"] for p in supplied], 3))))
    if len(candidates) < 3:
        result.skip("title-body-alignment",
                    "fewer than 3 pages could be compared: a page needs a title, 400+ "
                    "characters of body text, prose of its own rather than a list of other "
                    "pages, and a writing system this comparison can be run on{}".format(
                        " ({})".format("; ".join(set_aside)) if set_aside else ""))
        return

    # Which end of the title carries the brand is decided from the titles
    # themselves, not assumed. `re.split(...)[0]` assumed the brand always sits
    # last, so on a site titled "Northern Lights Trading | <product>" - which is
    # ordinary practice - the *product* was dropped and the brand kept, and
    # every page was then reported as failing to deliver a title promising
    # "northern lights trading". The repeated end is the brand, whichever end
    # it is.
    drop_first = _repeated_end(candidates, 0)
    drifted = []
    for page in candidates:
        parts = re.split(r"\s[|\-–—:·•]\s", page["title"])
        if len(parts) > 1 and drop_first:
            title = " ".join(parts[1:])
        else:
            title = parts[0]
        # `[a-z]{4,}` splits a word at every letter English does not use.
        # "Beschaeftigenvertretungen", written with the a-umlaut, became
        # "besch" and "ftigenvertretungen" - two strings that appear nowhere on
        # the page - and the page was reported as not delivering what its title
        # promises, with those two fragments printed as the evidence.
        tokens = {w for w in letter_runs(title.lower(), 4) if w not in STOPWORDS}
        # A homepage title that is a greeting plus the site name reduces to
        # one content word, and a single miss then reads as 100% drift.
        # Two tokens minimum.
        if len(tokens) < 2:
            continue
        # The page's headings belong in the haystack. `body_text` is the prose
        # with headings taken out, so a browser-compatibility site's usage page
        # was reported as missing the words "table" and "usage" from a page
        # whose own H2 reads "Browser usage table, based on data from" a
        # statistics service. Two words called absent, both of them printed on
        # the page in type larger than the body.
        body = " ".join([page.get("body_text") or ""] + _headings_of(page)).lower()
        # Report the words that are actually absent. This used to print
        # `sorted(tokens)[:4]` - the first four title words alphabetically -
        # under the label "missing", whether or not they appeared. On a German
        # government page it printed "missing bereich, bundesregierung,
        # service, webseite" about a page whose body text contains
        # "Bundesregierung" five times. The evidence was not merely weak; it
        # was false, and a reader who checked would have caught us.
        absent = [token for token in tokens if token[:6] not in body]
        if len(tokens) - len(absent) < 0.34 * len(tokens):
            drifted.append((page, title, sorted(absent)[:4]))

    if not drifted:
        # The count is here because "every page" was not every page: pages set
        # aside above were never compared, and a pass stated over them is a
        # claim about text this check did not read.
        result.skip("title-body-alignment",
                    "the title vocabulary of all {} compared page(s) appears in their own body "
                    "text or headings{}".format(
                        len(candidates),
                        " ({})".format("; ".join(set_aside)) if set_aside else ""))
        return

    result.add(
        id_hint="page-title-does-not-match-page-content",
        title="{}".format(
            plural(len(drifted),
                   "page does not deliver what its title promises",
                   "pages do not deliver what their titles promise")),
        severity="medium", confidence="medium",
        # Two readings of the same page, because one of them was wrong on its
        # own. The words compared are the title's own words with the site name
        # and the stopwords removed, matched on their first six characters, so
        # a title word the page writes in another grammatical form still counts.
        checked=("the page's body prose, which is the delivered text with the "
                 "site's header, footer and navigation removed",
                 "every heading the page carries, h1 to h3, which the body prose "
                 "excludes"),
        evidence="Of {} page(s) compared, these are the ones where under a third of the "
                 "title's own words appear in either the body text or the page's own "
                 "headings. Examples: {}.{}".format(
                     len(candidates),
                     "; ".join('{} (title "{}", missing {})'.format(p["url"], truncate(t, 50),
                                                                    ", ".join(toks))
                               for p, t, toks in sorted(drifted, key=lambda x: x[0]["url"])[:4]),
                     # Which pages were never compared belongs beside the ones
                     # that were, or the count above reads as the whole site.
                     " Not compared: {}.".format("; ".join(set_aside)) if set_aside else ""),
        mechanism="G", root_cause="title-body-drift",
        summary="Make the page's opening deliver the specific thing its title names.",
        how_to_fix=[
            "For each page, either rewrite the title to describe what the page actually says, "
            "or add the promised content.",
            "The first paragraph should repeat the title's subject in plain words.",
            "This matters most on pages people arrive at from a search or an answer, because "
            "the title is the promise that got them to click.",
        ],
        effort="medium", owner="content owner",
        rationale="The title set the expectation. A visitor who does not see it "
                  "confirmed in the first few lines assumes they are in the wrong place, and "
                  "the fastest way to check is to go back.",
        affected_pages=[p["url"] for p, _, _ in drifted],
    )


# How many links make a signature a menu rather than a logo.
CHROME_MENU_MIN = 3


def _stranded_within(pages, own_sections=None):
    """Pages missing the header/footer links most of this group shares.

    Returns (stranded, carrying_count, reason_it_could_not_judge). Pages that
    sit in a section of their own and carry that section's menu are appended
    to `own_sections` rather than to either count - see
    `_carries_its_own_sections_menu`.

    Judged against the links shared across the group, not against one
    template's whole set. Matching one template exactly picks whichever
    template has the most pages: on a cloud host that was the 40-path documentation
    sidebar carried by 22 doc pages, so every marketing page was measured
    against a docs sidebar and 35 pages were reported as not carrying the
    site's navigation - the homepage named first, a page with three <nav>, two
    <header>, one <footer> and 27 nav paths of its own in the same snapshot.
    The same finding fired on a programming school against /blog/archive, which carries
    the full site chrome and 222 internal links.

    A shared core is what a visitor needs: the handful of links that get them
    back into the site from wherever they landed. A page carrying those is not
    stranded however much of some other template it lacks.
    """
    own_sections = [] if own_sections is None else own_sections
    signatures = [tuple((p.get("chrome_signature") or {}).get("nav_paths") or [])
                  for p in pages]
    # A page carrying one link is not carrying a menu. Taking any non-empty
    # signature as evidence of the site's chrome let stripped templates define
    # it: on a crawl where eight of eleven pages had lost their header and
    # footer and kept only a logo linking home, the "shared" core came out as
    # the single path `/`, every page held it, and the check reported that all
    # eleven carry the site's navigation. The core has to be drawn from the
    # pages that have a menu at all.
    with_chrome = [s for s in signatures if len(s) >= CHROME_MENU_MIN]
    if not with_chrome:
        return [], 0, ("no page carries header or footer navigation, which the navigation "
                       "finding covers rather than this one")
    # One page with a big menu among nineteen without one does not establish a
    # site norm, and treating it as one flagged every page including itself.
    if len(with_chrome) < 2 or len(with_chrome) < len(pages) * 0.2:
        return [], len(with_chrome), (
            "no header or footer navigation appears on enough pages to establish what "
            "this site's normal chrome looks like ({} of {} pages carry any at "
            "all)".format(len(with_chrome), len(pages)))

    frequency = Counter(path for signature in with_chrome for path in set(signature))
    needed = max(2, int(round(len(with_chrome) * CHROME_CORE_SHARE)))
    core = {path for path, seen in frequency.items() if seen >= needed}
    if not core:
        return [], 0, (
            "no header or footer link appears on {}% of the {} pages that carry chrome, so "
            "this site has no one navigation for a page to be missing".format(
                int(CHROME_CORE_SHARE * 100), len(with_chrome)))

    required = max(1, int(len(core) * CHROME_CORE_PRESENT))
    stranded, carrying = [], 0
    # Sections the main menu navigates inside, not ones it merely links the
    # front of: every page of the museum links `/CHILD`, and that link does not
    # make the children's museum's own pages part of the main site's menu.
    core_sections = {_first_segment(path) for path in core
                     if "/" in path.strip("/")}
    for page, signature in zip(pages, signatures):
        # Two readings before a page is called stranded. The norm above is
        # still drawn from `chrome_signature` alone so it cannot shift, but a
        # page gets the benefit of `links.nav` and `links.footer` as well:
        # those find a menu built as a plain <ul> in a <div>, which the
        # <nav>/<header>/<footer> selectors behind `chrome_signature` never
        # see. A page carrying the site's links in a shape one detector cannot
        # read is not a page that strands a visitor.
        carried = set(signature) | _chrome_paths(page)
        if len(core & carried) >= required:
            carrying += 1
        elif _carries_its_own_sections_menu(page, carried, core_sections):
            own_sections.append(page)
        else:
            stranded.append(page)
    return stranded, carrying, ""


# A menu of its own, as opposed to a logo and two legal links: at least this
# many same-site destinations in the page's own header and footer.
SECTION_MENU_MIN = 8


def _first_segment(path):
    """The first segment of a path, lower-cased: `/ENG/main` -> `eng`."""
    return (urlparse(path or "").path or path or "").strip("/").split("/")[0].lower()


def _carries_its_own_sections_menu(page, carried, core_sections):
    """True when a page sits in a section of its own and carries that section's menu.

    A museum's English and Japanese editions and its children's museum each
    have a full menu of their own - 12 to 23 links, into `/ENG/...`,
    `/JPN/...`, `/CHILD/...` - and were reported as pages that "do not carry
    the site's normal navigation", because they were compared with the main
    site's menu. The same shape on a storefront whose root is a country
    picker: `/us` carries fifty `/us/...` links in its footer and was compared
    against the `/au/...` edition's. An edition or a section with a menu of its
    own is a different site's chrome, not a page with none.

    Both conditions: the page's first path segment holds none of the main
    menu's destinations, so it is a section the main site does not navigate
    as its own, and the page carries a real menu.
    """
    section = _first_segment(page.get("url"))
    if not section or section in core_sections:
        return False
    return len(carried) >= SECTION_MENU_MIN


def _check_chrome_consistency(result, pages):
    result.check("consistent-site-chrome")
    if len(pages) < 4:
        result.skip("consistent-site-chrome",
                    "fewer than 4 pages were crawled, too few to establish what the site's "
                    "normal navigation looks like")
        return

    # A German page's menu points at German URLs and shares not one path with
    # the English menu, so the site's normal chrome has to be established once
    # per language edition. Comparing the two raw reported a complete,
    # translated navigation as absent. `locale_editions` returns {} unless the
    # site gives real evidence of having editions, so a monolingual site is
    # judged exactly as before.
    editions = locale_editions(pages)
    groups = group_by_edition(pages, editions) if editions else {"": list(pages)}
    judged = {code: group for code, group in groups.items() if len(group) >= 4}
    if not judged:
        result.skip("consistent-site-chrome",
                    "this site is split into {} language editions and none of them was "
                    "crawled deeply enough to establish its normal navigation".format(
                        len(groups)))
        return

    stranded, carrying, reasons, own_sections = [], 0, [], []
    for code, group in sorted(judged.items()):
        found, count, why = _stranded_within(group, own_sections)
        carrying += count
        stranded.extend(found)
        if why:
            reasons.append("{}{}".format("/{}: ".format(code) if code else "", why))
    # Named wherever they are set aside, so a page left out of both counts is
    # never one the reader believes was judged.
    sections = sorted({"/" + urlparse(p["url"]).path.strip("/").split("/")[0]
                       for p in own_sections})
    set_aside = (" Not compared with the main site's menu: {} in a section of {} own ({}) "
                 "and {} that section's menu of {} or more links, which is a separate "
                 "edition's or section's navigation rather than a missing one".format(
                     plural(len(own_sections), "page sits", "pages sit"),
                     "its" if len(own_sections) == 1 else "their", ", ".join(sections),
                     "carries" if len(own_sections) == 1 else "carry", SECTION_MENU_MIN)
                 if own_sections else "")

    if not stranded:
        # A reason means the check could not judge, and that is not a pass.
        # `_stranded_within` bails when too few pages carry chrome to establish
        # a norm. Testing the page count it returns instead of the reason it
        # returns threw the reason away whenever exactly one page had a menu,
        # and a site where four of five pages carry no navigation at all was
        # reported as "all 5 crawled pages share the same header and footer
        # navigation". That is the case this check exists to catch, announced
        # as a clean bill of health.
        result.skip("consistent-site-chrome",
                    (reasons[0] if reasons else
                     "all {} crawled pages carry the header and footer links this site "
                     "shares across its templates{}".format(
                         sum(len(g) for g in judged.values()) - len(own_sections),
                         " within their own language edition ({})".format(
                             ", ".join("/{}".format(c) for c in sorted(judged) if c))
                         if len(judged) > 1 else "")) + (
                        "." + set_aside if set_aside else ""))
        return

    aside = {id(p) for p in own_sections}
    pages = [p for group in judged.values() for p in group if id(p) not in aside]

    result.add(
        id_hint="pages-missing-site-navigation",
        title="{} not carry the site's normal navigation".format(
            plural(len(stranded), "page does", "pages do")),
        severity="medium", confidence="medium",
        # Two buckets that add up to the pages judged, so a reader can do the
        # subtraction and be right. There used to be three, because the
        # comparison was against one template's whole set: pages matching it
        # exactly, pages with almost none of it, and pages that
        # share enough of it to be fine, which the sentence never named. "9 of
        # 32 pages" then read as 23 broken pages when 7 were. Comparing against
        # the shared core removes the middle bucket rather than describing it.
        evidence="Of {} judged, {} the header/footer links that most pages on "
                 "this site share and {} few or none of them. These are the ones that "
                 "do not: {}.{}".format(
                     plural(len(pages), "page", "pages"),
                     plural(carrying, "page carries", "pages carry"),
                     plural(len(stranded), "page carries", "pages carry"),
                     ", ".join(example_urls([p["url"] for p in stranded])),
                     set_aside + "." if set_aside else ""),
        mechanism="G", root_cause="inconsistent-chrome",
        summary="Apply the standard header and footer to every page template.",
        how_to_fix=[
            "Check whether these pages use a different layout, a landing-page template, or a "
            "third-party tool that renders its own shell.",
            "Include the standard header and footer partials in those templates.",
            # "A checkout" was the only example, on a report that may be about
            # a library or a manual. A single-purpose form is the general case
            # and a checkout is one of them, so the sentence now names the
            # shape rather than the sector - the same move the call-to-action
            # advice in this file already makes.
            "If a stripped-down template is deliberate (a single-purpose form, say), at "
            "minimum keep a logo linking home.",
        ],
        effort="medium", owner="developer",
        rationale="A visitor who arrives on a page with no navigation has no way "
                  "back into the site. The page becomes the whole experience, and when it ends, "
                  "so does the visit.",
        affected_pages=[p["url"] for p in stranded],
        checked=("the header and footer link fingerprint of every page crawled",
                 "the menu and footer link lists on the pages named here"),
    )


def _check_weight_and_scripts(result, pages):
    result.check("page-weight")
    heavy = []
    for page in pages:
        scripts = page.get("scripts") or {}
        # `html_len` is the length of the whole document, so the inline script
        # and inline CSS are already inside it. Adding them on top counted the
        # same bytes twice and inflated one real homepage from its true
        # 1,430,541 bytes to a reported 2,076,957 - a 45% overstatement, and
        # enough to push pages over the threshold that were never over it.
        # Bytes, which is what the threshold is named in. `html_len` counts
        # characters of the decoded document, so a page in a non-Latin script
        # measured about a third of its real size and this check could not fire
        # on it at all.
        weight = page.get("html_bytes") or page.get("html_len") or 0
        third_party = scripts.get("third_party_host_count", 0)
        if weight > PAGE_WEIGHT_BYTES:
            inline = scripts.get("inline_bytes", 0) + scripts.get("style_bytes", 0)
            heavy.append((page, "{:,} bytes of HTML, {:,} of it inline script and CSS".format(
                weight, inline)))
        elif third_party > THIRD_PARTY_SCRIPT_LIMIT:
            heavy.append((page, "{} third-party script hosts".format(third_party)))

    if not heavy:
        # "bytes of inline payload" named a quantity this check does not
        # measure. What it compares against PAGE_WEIGHT_BYTES is `html_bytes`,
        # the whole delivered document, so a 1.9 MB page carrying 500 bytes of
        # inline script passed with a sentence a reader would take as a
        # statement about that page's inline script and CSS.
        result.skip("page-weight",
                    "no page's delivered HTML exceeds {:,} bytes, and no page loads scripts "
                    "from more than {} third-party hosts; ordinary page weight was measured "
                    "but not reported, because only extremes affect whether a visitor "
                    "stays".format(PAGE_WEIGHT_BYTES, THIRD_PARTY_SCRIPT_LIMIT))
        return

    result.add(
        id_hint="pages-are-extremely-heavy",
        title="{} an extreme payload before anything is visible".format(
            plural(len(heavy), "page carries", "pages carry")),
        severity="medium", confidence="high",
        evidence="; ".join("{}: {}".format(p["url"], why)
                           for p, why in sorted(heavy, key=lambda x: x[0]["url"])[:5]),
        mechanism="G", root_cause="page-weight",
        summary="Cut the inline payload and the number of third-party scripts on these pages.",
        how_to_fix=[
            "Audit the third-party tags and remove the ones nobody looks at any more; "
            "analytics and marketing tags accumulate and are rarely removed.",
            "Move inline scripts and styles into cached external files.",
            "Defer everything not needed for the first screen.",
        ],
        effort="medium", owner="developer",
        rationale="A visitor arriving from an answer is checking whether this page "
                  "has what they were promised. Every second before the page is readable is a "
                  "second in which going back is easier than waiting.",
        affected_pages=[p["url"] for p, _ in heavy],
    )


def _check_interstitials(result, pages):
    result.check("intrusive-interstitials")
    offenders = []
    unmuted_autoplay = []
    for page in pages:
        interstitial = page.get("interstitial") or {}
        signals = []
        if interstitial.get("blocking_overlay"):
            signals.append("a modal or overlay is open in the delivered markup")
        # `modal_hints` is the names of things the page mentions. It is context
        # for an overlay that has been established some other way, and it was
        # raising this finding on its own.
        #
        # Four sites in one batch were told their pages cover their content on
        # arrival, and on all four nothing covered anything: a podcast whose
        # newsletter prompt ships `style="display: none"`, a shop whose cart and
        # menu drawers are closed by their stylesheet, a newspaper matching the
        # word inside a JavaScript identifier, and a manufacturer's "see full
        # dimensions" dialog carrying a stale class token. The name of a
        # component is not a statement that it is showing.
        # `modal_hints` stays in the snapshot as context for a reader; it is
        # never a signal here, because it is not an observation about display.
        #
        # An auto-playing video is not an interstitial, and this check no
        # longer reads it as one. A software vendor's page ships
        # `<video src="....mp4" autoplay loop>` with no `muted` attribute: a
        # silent product animation whose author forgot the one attribute
        # browsers require, so no browser starts it. Nothing plays, nothing
        # covers anything, and there was no modal, no form and no overlay on
        # the page. The evidence line was true and every other line of the
        # finding - "covers their content the moment a visitor arrives",
        # "shown a form before an answer", "delay newsletter and promotional
        # modals" - described a different page.
        #
        # Kept as a signal so the observation is not lost: an unmuted
        # `autoplay` is usually a mistake in the markup, and it is a different
        # finding from a page covering its own content.
        if (page.get("video") or {}).get("intrusive_autoplay_count"):
            unmuted_autoplay.append(page["url"])
        if signals:
            offenders.append((page, "; ".join(signals)))

    if unmuted_autoplay:
        result.signal("pages_with_unmuted_autoplay_video", sorted(unmuted_autoplay)[:10])

    if not offenders:
        result.skip("intrusive-interstitials",
                    "no page ships an open overlay in its initial markup. A dialog that a page "
                    "merely contains was not counted: component libraries ship their dialogs "
                    "closed, and the markup says which. A cookie banner alone was not treated "
                    "as a defect, because most sites are required to show one{}".format(
                        "" if not unmuted_autoplay else
                        ". {} carry a <video autoplay> element with no muted attribute, which "
                        "browsers refuse to start without a click, so it covers nothing and is "
                        "reported here as markup to tidy rather than as an interstitial".format(
                            plural(len(unmuted_autoplay), "page does", "pages do"))))
        return

    result.add(
        id_hint="content-covered-on-arrival",
        title="{}".format(
            plural(len(offenders),
                   "page covers its content the moment a visitor arrives",
                   "pages cover their content the moment a visitor arrives")),
        severity="medium", confidence="medium",
        evidence="; ".join("{}: {}".format(p["url"], why)
                           for p, why in sorted(offenders, key=lambda x: x[0]["url"])[:5]),
        mechanism="G", root_cause="intrusive-interstitial",
        summary="Let visitors read the page before asking them for anything.",
        # Every step here is about the overlay this finding is now about. The
        # bullet telling the owner to "remove autoplay from video, or mute it
        # and require a click" is gone with the video signal that produced it.
        how_to_fix=[
            "Delay newsletter and promotional modals until the visitor has scrolled or spent "
            "20 seconds on the page, and never show them on a first pageview from an external link.",
            "Keep the cookie banner, which is usually required, but make sure it does not "
            "cover the H1 and first paragraph.",
        ],
        effort="low", owner="marketing",
        rationale="Someone who arrived with a specific question and is shown a "
                  "form before an answer has been given a reason to leave before the page has "
                  "made its case.",
        affected_pages=[p["url"] for p, _ in offenders],
    )


def _form_shape(form):
    """What makes two `<form>` records on two pages the same form.

    Where it posts to, how, how many fields it offers, how many it demands and
    what the first of them is called. Everything a visitor would use to tell
    two forms apart, and everything the owner would have to edit twice if they
    really were two forms.
    """
    return (
        (form.get("action") or "").strip().lower(),
        (form.get("method") or "get").lower(),
        form.get("field_count", 0),
        form.get("required_count", 0),
        (form.get("query_field") or "").strip().lower(),
    )


def _chrome_key(page):
    """The nav fingerprint that says which template a page was built from."""
    signature = page.get("chrome_signature") or {}
    return tuple(signature.get("nav_paths") or ())


def distinct_forms(pages):
    """One entry per form on the site, not one per page that carries it.

    Returns `[(form, [urls carrying it])]`.

    A retailer's report said "none of the 83 enquiry forms found requires more
    than 8 fields" about a site with one form: a newsletter box in the
    site-wide footer, counted once for each of the 56 pages the footer appears
    on, plus the other templates' copies. Every sentence built on that number
    was a statement about a site that does not exist, and the same arithmetic
    would have divided one long form into fifty-six long forms in the finding.

    A form in the site-wide chrome is one form and one fix. So the identity is
    the form's own shape, and a form that posts to a named address is that one
    form wherever it appears - the owner edits one template. A form with no
    `action` posts back to whichever page carries it, so its shape alone cannot
    prove that two copies are one: `<form method="post">` on the enquiry page
    and on the quote page are two different forms with the same shape. Those
    fold only when the pages carrying them say the form is furniture - it
    appears on nearly every page built from the same chrome.
    """
    shapes = {}
    for page in pages:
        url = page.get("url")
        for form in (page.get("forms") or []):
            shapes.setdefault(_form_shape(form), {}).setdefault(url, form)
    pages_per_chrome = Counter(_chrome_key(p) for p in pages)

    out = []
    for shape, carriers in shapes.items():
        posts_to_a_named_address = bool(shape[0])
        if posts_to_a_named_address or _is_site_furniture(carriers, pages, pages_per_chrome):
            first = sorted(carriers)[0]
            out.append((carriers[first], sorted(carriers)))
        else:
            out.extend((form, [url]) for url, form in sorted(carriers.items()))
    return out


def _is_site_furniture(carriers, pages, pages_per_chrome):
    """Does this form appear on nearly every page built from one chrome?

    Asked per chrome signature rather than per site, because a site with a
    marketing template and a documentation template has two sets of furniture
    and a form in one of them is not missing from the other.
    """
    carried = Counter(_chrome_key(p) for p in pages if p.get("url") in carriers)
    for chrome, seen in carried.items():
        total = pages_per_chrome.get(chrome, 0)
        if total >= SITE_WIDE_FORM_MIN_PAGES and seen >= SITE_WIDE_FORM_SHARE * total:
            return True
    return False


def _form_where(carriers):
    """Where a distinct form was found, in one phrase."""
    if len(carriers) == 1:
        return carriers[0]
    return "{} (and {} other page(s) carrying the same form)".format(
        carriers[0], len(carriers) - 1)


def _check_forms(result, pages):
    result.check("form-friction")
    offenders = []
    enquiry_forms = search_forms = newsletter_forms = one_field_forms = 0
    single_field_examples = []
    # Distinct forms, not `<form>` elements. See `distinct_forms`: the site-wide
    # footer box was being counted once per page it appears on.
    for form, carriers in distinct_forms(pages):
        if form.get("is_search"):
            search_forms += 1
            continue
        if form.get("is_newsletter"):
            newsletter_forms += 1
            continue
        # A single-field box is a subscription or a lookup, whatever the
        # newsletter word-match made of the text around it. See
        # `ENQUIRY_FORM_MIN_FIELDS`.
        if form.get("field_count", 0) < ENQUIRY_FORM_MIN_FIELDS:
            one_field_forms += 1
            single_field_examples.append(_form_where(carriers))
            continue
        enquiry_forms += 1
        if form.get("required_count", 0) > FORM_FIELD_LIMIT:
            offenders.append((carriers[0], carriers, form.get("required_count", 0)))

    if not offenders:
        # A research society whose only forms are 121 copies of the site search
        # box was told "no enquiry form requires more than 8 fields". True, and
        # it describes no form on the site.
        if not enquiry_forms:
            # What was actually seen, counted. The sentence used to assert
            # "the forms this crawl saw are search boxes and newsletter
            # sign-ups" whether or not any had been seen, and printed it on a
            # site whose snapshot carries `has_search: false` on all 60 pages
            # and `is_search: false` on every captured form - contradicting
            # the search finding in another skill's report about the same
            # crawl.
            #
            # Counted as distinct forms. A one-field box repeated by the
            # site-wide footer is one box, and saying "121 search boxes" about
            # one search box in one template is the same false count from the
            # other side.
            parts = [plural(search_forms, "search box", "search boxes") if search_forms else "",
                     plural(newsletter_forms, "newsletter sign-up") if newsletter_forms else "",
                     "{} of one field, which is a subscription or lookup box rather "
                     "than something a visitor can ask a question through ({})".format(
                         plural(one_field_forms, "form"),
                         "; ".join(single_field_examples[:3]))
                     if one_field_forms else ""]
            parts = [part for part in parts if part]
            if parts:
                seen = "this crawl saw only {}".format(
                    ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0])
            else:
                seen = "this crawl saw no <form> element of any kind"
            result.skip("form-friction",
                        "no enquiry form was found on the crawled pages - {} - so there was "
                        "no form whose length could be measured".format(seen))
            return
        result.skip("form-friction",
                    "no enquiry form on this site requires more than {} fields. {} measured, "
                    "counted once each: a form the site-wide template repeats is one form and "
                    "one fix, not one per page it appears on".format(
                        FORM_FIELD_LIMIT,
                        plural(enquiry_forms, "distinct enquiry form was",
                               "distinct enquiry forms were")))
        return

    result.add(
        id_hint="enquiry-forms-ask-for-too-much",
        title="{} more than {} fields".format(
            plural(len(offenders), "enquiry form asks for", "enquiry forms ask for"),
            FORM_FIELD_LIMIT),
        severity="low", confidence="high",
        evidence="; ".join("{} ({} required fields)".format(_form_where(carriers), n)
                           for _, carriers, n in sorted(offenders, key=lambda x: x[0])[:5]),
        mechanism="G", root_cause="form-friction",
        summary="Ask for the minimum now and the rest after the first reply.",
        how_to_fix=[
            "Cut required fields to name, email and one free-text field; make the rest optional.",
            "Collect qualifying details in the follow-up conversation instead.",
            "Say what happens next and how quickly, directly beside the submit button.",
        ],
        effort="low", owner="marketing",
        rationale="A long form is the last step of the journey. A visitor who "
                  "arrived from an answer has invested one click, not a decision, and will not "
                  "fill in twelve fields to ask a question.",
        # One page per distinct form, not every page the form appears on. A
        # form in the site-wide template is one edit; listing the 56 pages
        # that carry it would say the owner has 56 forms to shorten.
        affected_pages=[url for url, _, _ in offenders],
    )


# Above this share of a page's words sitting inside list or table cells, the
# page is a catalogue and its "words between subheadings" is a count of link
# labels. The same threshold `fact-extractability-audit` applies before it
# reads a page as prose, so one page cannot be a list to one skill and an
# article to the other.
PROSE_MAX_LIST_TEXT_SHARE = 0.5

# The same question asked of a page that uses no list markup at all. A
# directory built the way 1990s pages were built - anchors separated by line
# breaks, no `<li>`, no `<td>`, no `<p>` - scores `list_text_share` 0.0,
# because that measurement counts words inside list and table cells and there
# are none. Its words are still link labels: this counts them from the links
# the crawl already recorded.
DIRECTORY_LINK_TEXT_SHARE = 0.5

# Below this, a page has too few words for the share to mean anything: a
# three-link page of eight words is not a directory, it is a stub, and the
# stub checks are the ones that should speak about it.
DIRECTORY_MIN_WORDS = 100


def _link_text_share(page):
    """The share of this page's words that are the labels of links on it.

    Counted only for labels the measured text actually contains, because
    `links` is read from the whole document while `word_count` is measured on
    the content region: a page whose navigation is excluded from one and
    present in the other would otherwise score its own menu as body copy.
    """
    words = page.get("word_count") or 0
    if words < DIRECTORY_MIN_WORDS:
        return 0.0
    text = (page.get("body_text") or "").lower()
    if not text:
        return 0.0
    links = page.get("links") or {}
    seen, label_words = set(), 0
    for bucket in ("internal", "external"):
        for link in links.get(bucket) or []:
            label = " ".join(str(link.get("text") or "").split())
            if not label or label.lower() in seen or label.lower() not in text:
                continue
            seen.add(label.lower())
            label_words += word_count(label)
    return min(label_words / words, 1.0)


def _is_a_directory_of_links(page):
    """A page whose text is a list of the pages it points at.

    `is_listing_page` answers this for a blog index, whose subheadings are the
    titles it links. It cannot answer it for a plain directory: that shape has
    one subheading or none, so the heading test has nothing to measure and the
    page reads as an article of 1,000 words with no subheading in it. Reported
    as "written in blocks too large to skim", with the advice to add a
    subheading every three to five paragraphs, about a page with 240 links and
    not one paragraph.
    """
    readability = page.get("readability") or {}
    if readability.get("list_text_share", 0.0) > PROSE_MAX_LIST_TEXT_SHARE:
        return True
    return _link_text_share(page) >= DIRECTORY_LINK_TEXT_SHARE


# Every number in `page["readability"]` is measured by the extractor, while
# the page is being read, and the crawl's site-furniture pass runs afterwards -
# once every page has been fetched, because repetition is the only thing that
# identifies furniture. So the readability numbers describe the page with its
# account menu, its category nav and its footer still in the text, and the
# crawl then rewrites `body_text`, `body_text_len` and `word_count` without
# them and leaves `readability` as it was.
#
# A Malaysian grocery site's blog page was graded on fourteen "sentences" of
# which ten were unpunctuated runs of menu labels - the longest, 307 words,
# being the account menu and the category nav read as one sentence. The crawl's
# own notes on that run say it identified twelve blocks of site furniture; it
# just did not remove them before the measurement was consulted.
#
# The two numbers this check reads are recomputed from the text that survived
# that pass. Both recomputations can only lower the figure, so the effect is to
# stop grading a site's chrome and never to invent a finding.
def _words_between_subheadings(page):
    """How much of the page's own prose sits between one subheading and the next.

    `readability["words_per_subheading"]` divides the pre-strip word count by
    the subheading count. `word_count` on the page is the post-strip figure -
    the crawl recomputes it - so the same division done here is the same
    measurement taken after the menus came out.

    Falls back to the extractor's number where the page carries no subheading
    count to divide by, which is what a snapshot written before that key
    existed looks like.
    """
    readability = page.get("readability") or {}
    subheadings = readability.get("subheading_count")
    if subheadings is None:
        return readability.get("words_per_subheading", 0)
    words = page.get("word_count") or 0
    # The extractor's own convention when a page has no subheading at all: the
    # ratio is the whole page, so a reader can tell "one subheading every 1,208
    # words" from "no subheadings anywhere".
    return round(words / subheadings, 1) if subheadings else float(words)


# The extractor keeps at most 60 paragraphs per page and truncates each at 600
# characters, so a paragraph over 200 words - some 1,200 characters of English -
# is stored as a 600-character string. Half of that is the floor used below: it
# is far above any paragraph that could be under 200 words after truncation is
# undone, and low enough that a change to the extractor's truncation length
# cannot silently empty this count.
WALL_PARAGRAPH_MIN_CHARS = 300
PARAGRAPH_SAMPLE_CAP = 60


def _walls_that_survived_the_furniture_strip(page):
    """Paragraphs over 200 words that are this page's own, not the site's.

    `wall_of_text_count` is counted at extraction time, before the crawl
    removes the blocks that repeat across the site. A long "about us" paragraph
    in a footer is on every page of the site and is not any one page's wall of
    text, and the advice this check gives - split it into shorter paragraphs -
    is one edit to a template rather than work on each page it appears on.

    `page["paragraphs"]` is the list the crawl rewrites in place when it strips
    furniture, so what is left there is the page's own. This caps the
    extractor's count by how many of those survivors are long enough to have
    been a wall. It never raises it: the survivors are measured in characters,
    which overcounts, and the smaller of the two numbers is the one used.
    """
    readability = page.get("readability") or {}
    walls = readability.get("wall_of_text_count", 0)
    paragraphs = page.get("paragraphs")
    if not walls or paragraphs is None:
        return walls
    if len(paragraphs) >= PARAGRAPH_SAMPLE_CAP:
        # The list is at the extractor's cap, so it is a sample rather than the
        # page, and a paragraph missing from it proves nothing.
        return walls
    survivors = len([p for p in paragraphs
                     if len(p or "") >= WALL_PARAGRAPH_MIN_CHARS])
    return min(walls, survivors)


def _check_readability(result, pages):
    result.check("readability")
    offenders, unmeasured, listings = [], [], 0
    for page in pages:
        readability = page.get("readability") or {}
        words = page.get("word_count", 0)
        reasons = []
        walls = _walls_that_survived_the_furniture_strip(page)
        if walls >= 2:
            reasons.append("{} over 200 words".format(plural(walls, "paragraph")))
        # `word_count` splits on runs of letters, and Japanese, Chinese and
        # Thai put no space between one word and the next: 4,200 Japanese
        # characters with no subheading at all measured `word_count == 1`
        # against a threshold of 700, so the page could not trip the rule and
        # the check then printed "no long page runs without subheadings" - a
        # pass asserted about a page it had not measured.
        measurable = words_are_separated(page.get("body_text") or "")
        listing = is_listing_page(page) or _is_a_directory_of_links(page)
        if listing:
            listings += 1
        elif not measurable:
            unmeasured.append(page)
        elif (words >= LONG_PAGE_WORDS
                and _words_between_subheadings(page) > WALL_OF_TEXT_WORDS):
            # Both numbers now come from the same text: the page's own prose,
            # after the crawl removed the blocks that repeat site-wide. They
            # used to come from two different readings of the page - the gate
            # from the stripped word count and the ratio from the unstripped
            # one - so a page could be reported for a density measured on its
            # navigation.
            reasons.append("{} words with a subheading only every {} words".format(
                words, int(_words_between_subheadings(page))))
        if reasons:
            offenders.append((page, "; ".join(reasons)))

    if not offenders:
        notes = []
        if unmeasured:
            notes.append("on {} of {} the subheading test could not run at all: the text "
                         "there is written in a script that puts no space between one word and "
                         "the next, so a word count of it means nothing".format(
                             len(unmeasured), plural(len(pages), "page", "pages")))
        if listings:
            notes.append("{}".format(
                plural(listings,
                       "page whose content is a list of other pages was left out of it, "
                       "because its subheadings are the titles of the items it lists and its "
                       "words are link labels rather than prose",
                       "pages whose content is a list of other pages were left out of it, "
                       "because their subheadings are the titles of the items they list and "
                       "their words are link labels rather than prose")))
        # Every page unmeasurable is not a pass, so the check declines instead
        # of claiming one.
        if unmeasured and len(unmeasured) == len(pages):
            result.skip("readability",
                        "no page repeats walls of text, and {}".format(notes[0]))
            return
        result.skip("readability",
                    "no long page runs without subheadings or contains repeated walls of "
                    "text{}".format(" ({})".format("; ".join(notes)) if notes else ""))
        return

    result.add(
        id_hint="pages-are-hard-to-skim",
        title="{} written in blocks too large to skim".format(
            plural(len(offenders), "page is", "pages are")),
        severity="low", confidence="medium",
        evidence="; ".join("{}: {}".format(p["url"], why)
                           for p, why in sorted(offenders, key=lambda x: x[0]["url"])[:5]),
        mechanism="G", root_cause="readability",
        summary="Break long passages with subheadings roughly every 300 words.",
        how_to_fix=[
            "Add a subheading every 3 to 5 paragraphs, naming what that part covers.",
            "Split any paragraph over 200 words into shorter ones with one idea each.",
            "Use lists for anything that is genuinely a list.",
        ],
        effort="low", owner="content owner",
        rationale="Visitors scan before they read. A page with no visual entry "
                  "points offers nothing to scan, so a visitor cannot tell in three seconds "
                  "whether their answer is here.",
        affected_pages=[p["url"] for p, _ in offenders],
    )


def _check_viewport(result, pages, render_mode="static", platform=None):
    result.check("mobile-viewport")
    without = [p for p in pages if not p.get("has_viewport")]
    if not without:
        result.skip("mobile-viewport",
                    "every crawled page declares a viewport meta tag in its delivered <head>")
        return
    # The title and the evidence say "delivered", not "the site has none",
    # because the two are different claims and this check can only make the
    # first. A software vendor's help pages insert the tag from JavaScript: a
    # Chromium render shows `width=device-width,initial-scale=1` on a page this
    # finding named. Pages the crawl knows are stubs never reach here (see
    # `_delivered_stub`), and where no browser ran at all the sentence below
    # says so rather than implying a phone would see the same thing.
    unrendered_note = (
        "" if render_mode == "rendered" else
        " No browser ran during this audit, so a tag inserted by JavaScript would not have "
        "been seen; check one of these pages in a browser's element inspector before acting.")
    result.add(
        id_hint="missing-mobile-viewport",
        title="{}".format(
            plural(len(without),
                   "page has no mobile viewport tag in the HTML it delivers",
                   "pages have no mobile viewport tag in the HTML they deliver")),
        severity="medium" if len(without) == len(pages) else "low", confidence="high",
        evidence="Pages whose delivered <head> carries no <meta name=\"viewport\"> tag: "
                 "{}.{}".format(", ".join(example_urls([p["url"] for p in without])),
                                unrendered_note),
        mechanism="G", root_cause="no-orientation",
        summary="Add the standard viewport meta tag to the site template.",
        how_to_fix=[
            'Add <meta name="viewport" content="width=device-width, initial-scale=1"> to the '
            "<head> of the base template.",
            "Check the layout on a phone afterwards; the tag alone does not make a fixed-width "
            "layout responsive.",
            # Which template, and who edits it. Without these two the step is
            # the sentence repeated forty times across four real
            # reports: a change to make, and no address to make it at.
            where_the_template_is(platform),
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="Without it, phones render the page at desktop width and zoom "
                  "out. Most people arriving from an assistant are on a phone, and a page they "
                  "have to pinch to read is a page they leave.",
        affected_pages=[p["url"] for p in without],
        # One source, and deliberately one. The viewport is a single named meta
        # tag, either there or not, with no second place it could be hiding -
        # so naming a second source here would be inventing corroboration
        # rather than having it. `make_finding` caps this at medium confidence
        # for that reason, which is the right answer. The limit worth stating
        # is which document was read, because that is where this check was
        # wrong: it read the server's HTML and reported a browser's.
        checked=('a <meta name="viewport"> tag in the HTML the server delivered, '
                 "which is not the document a browser ends up with: a tag that "
                 "JavaScript inserts is not counted here",),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--time-budget", type=float,
                        help="seconds of wall clock this check may spend on its own requests; unreached targets are reported as unchecked rather than guessed at")
    parser.add_argument("--no-network", action="store_true",
                        help="skip the internal-link HEAD probes")
    args = parser.parse_args(argv)

    result = run(load_snapshot(args.snapshot), allow_network=not args.no_network,
                 time_budget=args.time_budget, snapshot_path=args.snapshot)
    result.write(args.out)
    print("{}: {} finding(s), {} extra request(s)".format(
        SKILL, len(result.findings), result.extra_requests_made), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
