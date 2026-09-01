#!/usr/bin/env python3
"""engagement-audit: does a visitor who arrives actually stay? (mechanism G)

A visitor sent by an assistant arrives mid-journey, already knowing what they
want, on a page that has no idea any of that happened. This skill checks
whether the page orients them, gives them a next step, and lets them get back.

Makes at most 20 extra read-only HEAD requests to test internal links.

Usage:
    python check.py --snapshot snapshot.json --out engagement-audit.findings.json
"""

from __future__ import annotations

import argparse
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
    CONTENT_TYPES, DEEP_TYPES, example_urls, Fetcher, FetchError,
    language_of, link_verdict, load_snapshot, normalise_url, pages_of, pct,
    plural, same_site, sample, SkillResult, truncate, word_count
)

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

FORM_FIELD_LIMIT = 8          # measured: fires on 2 of 346 real forms. Reachable and rare.
WALL_OF_TEXT_WORDS = 300      # measured: fires on 21 of 123 long pages (17%). Reachable and not noisy.
LONG_PAGE_WORDS = 700         # below this a page does not need internal subheadings

GENERIC_H1 = frozenset({
    "welcome", "welcome!", "home", "homepage", "welcome to our website",
    "hello", "hi", "untitled", "index", "our website", "site", "main page",
    "welcome to our site", "landing page",
})

NAV_CONTROL_LABELS = frozenset({
    "menu", "search", "close", "open menu", "close menu", "toggle menu",
    "skip to content", "skip to main content", "skip navigation", "back",
    "home", "cart", "basket", "account", "sign in", "log in", "login",
    "language", "accessibility", "share", "subscribe",
})

GENERIC_NAV_LABELS = frozenset({
    "products", "services", "solutions", "more", "menu", "other", "misc",
    "resources", "company", "info", "information", "stuff", "items", "pages",
})

STOPWORDS = frozenset("""
a an the and or but for of to in on at by with from as is are was were be your our their its it
this that these those we you they what how why when where which who best top new free guide
""".split())


def run(snapshot, allow_network=True):
    result = SkillResult(SKILL)
    pages = pages_of(snapshot, content_only=True)
    all_ok = pages_of(snapshot)

    if not pages:
        for name in ("homepage-orientation", "primary-navigation", "dead-end-pages",
                     "broken-internal-links", "orphan-pages", "breadcrumb-navigation",
                     "title-body-alignment", "consistent-site-chrome", "page-weight",
                     "intrusive-interstitials", "form-friction", "readability",
                     "mobile-viewport"):
            result.skip(name, "no content pages returned HTTP 200")
        return result

    home = next((p for p in pages if p["page_type"] == "home"), None)

    language = language_of(snapshot)
    english = bool(language.get("prose_checks_apply"))
    result.signal("site_language", language.get("code") or "undetermined")

    _check_homepage_orientation(result, home, english)
    _check_navigation(result, home, pages)
    _check_dead_ends(result, pages)
    _check_orphans(result, snapshot, pages)
    _check_breadcrumbs(result, pages)
    _check_title_body_drift(result, pages)
    _check_chrome_consistency(result, pages)
    _check_weight_and_scripts(result, pages)
    _check_interstitials(result, pages)
    _check_forms(result, pages)
    _check_readability(result, pages)
    _check_viewport(result, pages)

    fetcher = None
    if allow_network:
        try:
            fetcher = Fetcher(max_requests=BROKEN_LINK_SAMPLE)
        except FetchError:
            fetcher = None
    _check_broken_links(result, snapshot, pages, fetcher, allow_network)
    if fetcher is not None:
        result.extra_requests_made = fetcher.count

    result.signal("newsletter_signup", any(p.get("newsletter_signup") for p in pages))
    result.signal("has_site_search", any(p.get("has_search") for p in pages))
    return result


# --------------------------------------------------------------------------

def _check_homepage_orientation(result, home, english=True):
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
        result.skip("homepage-orientation", "the homepage was not crawled")
        return

    h1s = home.get("headings", {}).get("h1") or []
    cta = home.get("cta") or {}
    problems = []

    if not h1s:
        problems.append("the homepage has no H1")
    elif english and h1s[0].strip().lower() in GENERIC_H1:
        problems.append('the H1 is "{}", which says nothing about what the business does'.format(h1s[0]))

    if not english:
        if not problems:
            result.skip("homepage-orientation",
                        "the homepage has an H1. Whether its wording orients a visitor, and "
                        "whether its call to action reads as one, were not judged: both tests "
                        "are English-only and this site is not in English")
            return
    elif not cta.get("found"):
        problems.append("no call-to-action link was found anywhere in the page")
    elif not cta.get("within_first_1500"):
        if cta.get("offset_known"):
            problems.append('the first call to action ("{}") appears {} characters into the '
                            "body text".format(cta.get("text"), cta.get("offset")))
        else:
            problems.append('the first call to action ("{}") is not in the page copy at all - '
                            "its label sits in markup a text-only reader never sees"
                            .format(cta.get("text")))

    if not problems:
        result.skip("homepage-orientation",
                    'the homepage leads with the H1 "{}" and offers "{}" within the first '
                    "{} characters".format(h1s[0], cta.get("text"), CTA_BYTE_WINDOW))
        return


    no_cta = english and not cta.get("found")
    result.add(
        id_hint="homepage-does-not-orient-visitors",
        title="The homepage does not tell an arriving visitor where they are or what to do next",
        severity="high" if (no_cta or not h1s) else "medium", confidence="high",
        evidence="{}.".format("; ".join(problems).capitalize()),
        mechanism="G", root_cause="no-orientation",
        summary="Lead with a specific H1 and put one obvious next step in the first screen.",
        how_to_fix=[
            "Replace the H1 with a line naming what the business does and for whom.",
            "Put one primary call to action within the first screen of content, worded as the "
            "action the visitor wants (\"See pricing\", \"Book a table\"), not \"Learn more\".",
            "Keep one primary action; competing buttons of equal weight split attention.",
        ],
        effort="low", owner="marketing",
        rationale="A visitor sent by an assistant already knows roughly what they "
                  "want. They are checking whether this is the right place. A generic heading "
                  "and no obvious next step answers neither question, so they go back.",
        affected_pages=[home["url"]],
    )


def _check_navigation(result, home, pages):
    result.check("primary-navigation")
    if home is None:
        result.skip("primary-navigation",
                    "the homepage was not reached by this crawl, and navigation is judged on the "
                    "homepage rather than on whichever page happened to be crawled first")
        return
    source = home
    nav = source.get("links", {}).get("nav") or []
    labels = [link["text"].strip() for link in nav if link["text"].strip()]
    # Drop interface controls. "Menu", "Search" and "Skip to content" are
    # affordances, not destinations, and counting them made a real site's
    # navigation look both larger and vaguer than it is.
    labels = [label for label in labels if label.lower() not in NAV_CONTROL_LABELS]
    unique_labels = list(dict.fromkeys(labels))
    result.signal("nav_item_count", len(unique_labels))

    # Guard against our own selector failing. `chrome_signature` sweeps a wider
    # net (nav, header, footer, role=navigation). If that found a real menu but
    # `links.nav` did not, we cannot isolate the primary navigation on this
    # site, and saying so beats reporting a site as having one menu item.
    chrome_paths = (source.get("chrome_signature") or {}).get("nav_path_count", 0)
    if len(unique_labels) < NAV_MIN and chrome_paths >= 5:
        result.skip("primary-navigation",
                    "the primary navigation could not be isolated on this site: {} link(s) were "
                    "found inside <nav>, but the header and footer together contain {} distinct "
                    "internal destinations, so the menu is built in a way this check cannot "
                    "read".format(len(unique_labels), chrome_paths))
        return

    if len(unique_labels) >= NAV_MIN:
        vague = [label for label in unique_labels if label.lower() in GENERIC_NAV_LABELS]
        if not vague:
            result.skip("primary-navigation",
                        "the primary navigation has {} items with descriptive labels".format(
                            len(unique_labels)))
            return

    problems = []
    severity = "medium"
    if len(unique_labels) < NAV_MIN:
        problems.append("the primary navigation has {} item(s)".format(len(unique_labels)))
        severity = "high"
    vague = [label for label in unique_labels if label.lower() in GENERIC_NAV_LABELS]
    if vague:
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
            'Replace catch-all labels with the categories they contain: "Products" becomes '
            '"Kitchen", "Bathroom", "Outdoor" if those are the ranges.',
            "Make sure at least {} destinations are reachable from the top level.".format(NAV_MIN),
            "Make sure the navigation is real HTML links, so it works before JavaScript runs.",
        ],
        effort="medium", owner="marketing",
        rationale="The navigation is the map. A visitor who cannot see where else "
                  "to go from the page they landed on has one option, which is to leave.",
        affected_pages=[source["url"]],
    )


def _check_dead_ends(result, pages):
    result.check("dead-end-pages")
    dead_ends = []
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
        if main_links < MIN_MAIN_LINKS and not has_cta and not has_form:
            dead_ends.append(page)

    if not dead_ends:
        result.skip("dead-end-pages",
                    "every crawled content page offers either onward internal links or a call "
                    "to action")
        return

    rate = pct(len(dead_ends), len(pages))
    result.add(
        id_hint="pages-with-no-next-step",
        title="{} no next step".format(
            plural(len(dead_ends), "page offers", "pages offer")),
        severity="medium", confidence="medium",
        evidence="{} of {} content pages ({}%) end without a way onward: no internal link in "
                 "the main content, no call to action and no form. Examples: {}.".format(
                     len(dead_ends), len(pages), rate,
                     ", ".join(example_urls([p["url"] for p in dead_ends]))),
        mechanism="G", root_cause="dead-end",
        summary="End every page with a related-content block or a clear next action.",
        how_to_fix=[
            'Add a "Read next" or "Related" block with 2 to 4 relevant internal links to the '
            "page template.",
            "Add one call to action appropriate to the page: contact, pricing, or the next "
            "step in the journey.",
            "Because these are templates, fixing the template fixes every page using it.",
        ],
        effort="low", owner="content owner",
        rationale="A visitor who arrived from an answer landed deep in the site "
                  "with no journey behind them. If the page ends without an onward path, the "
                  "session ends with it.",
        affected_pages=[p["url"] for p in dead_ends],
    )


def _check_orphans(result, snapshot, pages):
    """Pages the sitemap advertises that nothing on the site links to."""
    result.check("orphan-pages")
    sitemap_urls = set()
    for record in snapshot.get("sitemaps") or []:
        for entry in record.get("urls") or []:
            url = normalise_url(entry["loc"])
            if url:
                sitemap_urls.add(url)
    if len(sitemap_urls) < 5:
        result.skip("orphan-pages",
                    "the sitemap lists fewer than 5 URLs, too few to identify orphans reliably")
        return

    # On a large site a 30-page crawl sees a tiny slice, so "no crawled page
    # links here" says more about the budget than about the site. Only judge
    # orphans when the crawl covered a meaningful share of the sitemap.
    crawled_count = len(pages_of(snapshot))
    if snapshot.get("crawl", {}).get("budget_exhausted") or len(sitemap_urls) > crawled_count * 3:
        result.skip("orphan-pages",
                    "the sitemap lists {} URLs but only {} pages were crawled, so an absent "
                    "internal link is more likely to reflect the crawl budget than a genuine "
                    "orphan".format(len(sitemap_urls), crawled_count))
        return

    linked = set()
    for page in pages_of(snapshot):
        for link in (page.get("links", {}).get("internal") or []):
            linked.add(link["url"])

    crawled = {p["url"] for p in pages_of(snapshot)}
    # Only pages we actually visited can be judged: a sitemap URL we never
    # fetched might well be linked from a page outside the crawl budget.
    orphans = sorted((sitemap_urls & crawled) - linked)
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
                 "other crawled page: {}.".format(
                     len(sitemap_urls & crawled), len(orphans), ", ".join(orphans[:5])),
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
    )


def _check_broken_links(result, snapshot, pages, fetcher, allow_network):
    result.check("broken-internal-links")
    if not allow_network or fetcher is None:
        result.skip("broken-internal-links",
                    "extra network requests were disabled for this run")
        return

    known = {p["url"]: p.get("status") for p in snapshot.get("pages") or []}
    candidates = set()
    for page in pages:
        for link in (page.get("links", {}).get("internal") or []):
            if link["url"] not in known:
                candidates.add(link["url"])

    # Pages the crawl fetched with GET: a verdict on those needs no probe.
    broken, checked, unchecked = [], 0, 0
    for url, status in known.items():
        verdict = link_verdict(status)
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
    if head_supported is False:
        # Only ever as many as we would have sampled. Counting every candidate
        # would report hundreds of "unchecked" targets on a large site, when
        # the check would have probed twenty of them at most - true in the
        # letter and misleading in the reading.
        unchecked += min(len(candidates), BROKEN_LINK_SAMPLE)
    else:
        for url in sample(sorted(candidates), BROKEN_LINK_SAMPLE):
            response = fetcher.try_get(url, method="HEAD")
            verdict = link_verdict(response.status_code if response is not None else None)
            if verdict == "dead":
                broken.append((url, response.status_code))
                checked += 1
            elif verdict == "alive":
                checked += 1
            else:
                unchecked += 1

    result.signal("link_targets_checked", checked)
    result.signal("link_targets_unchecked", unchecked)

    # Why anything is unchecked, said once and reused, because a check that
    # goes quiet without explaining itself is the thing this marketplace
    # promises not to do.
    if head_supported is False:
        why_unchecked = (
            "; {} further target(s) are unchecked because this site refuses HEAD requests "
            "while answering GET normally, and verifying them would mean fetching every one "
            "in full".format(unchecked))
    elif unchecked:
        why_unchecked = ("; {} further target(s) could not be verified - the server refused "
                         "or errored rather than answering - and are counted neither way"
                         .format(unchecked))
    else:
        why_unchecked = ""

    if not checked:
        result.skip("broken-internal-links",
                    ("no internal link target could be verified" + why_unchecked)
                    if unchecked else "no internal links were available to test")
        return
    if not broken:
        result.skip("broken-internal-links",
                    "all {} internal link target(s) tested resolved successfully{}".format(
                        checked, why_unchecked))
        return

    rate = len(broken) / float(checked)
    result.add(
        id_hint="broken-internal-links",
        title="{} an error".format(
            plural(len(broken), "internal link target returns", "internal link targets return")),
        severity="high" if rate > BROKEN_LINK_HIGH else "medium", confidence="high",
        evidence="{} of {} tested internal link targets ({}%) are gone. Examples: {}.{}".format(
                     len(broken), checked, pct(len(broken), checked),
                     "; ".join("{} -> {}".format(u, s) for u, s in sorted(broken)[:5]),
                     " A further {} target(s) could not be verified and are excluded from "
                     "the rate.".format(unchecked) if unchecked else ""),
        mechanism="G", root_cause="broken-links",
        summary="Fix or remove the internal links that lead nowhere.",
        how_to_fix=[
            "Correct each link to its current URL, or remove it if the destination is gone.",
            "Add a 301 from the old URL where the page moved, so existing links keep working.",
            "Run a link check as part of publishing so this does not accumulate again.",
        ],
        effort="medium", owner="developer",
        rationale="A broken link is a visitor stopped mid-journey with nothing to "
                  "do but leave. At this rate it is happening often enough to be a pattern "
                  "rather than an accident.",
        affected_pages=[u for u, _ in sorted(broken)],
    )


def _check_breadcrumbs(result, pages):
    """Visible breadcrumb navigation, distinct from BreadcrumbList markup.

    structured-data-audit owns the machine-readable version; this owns whether
    a human can see where they are.
    """
    result.check("breadcrumb-navigation")
    deep = [p for p in pages if p["page_type"] in DEEP_TYPES and p.get("depth", 0) >= 1]
    if len(deep) < 3:
        result.skip("breadcrumb-navigation",
                    "fewer than 3 deep pages were crawled, so breadcrumbs would not change "
                    "how the site is navigated")
        return
    # The visible trail only. BreadcrumbList markup is a separate finding
    # owned by structured-data-audit; markup a visitor cannot see does not
    # help the visitor who landed mid-journey.
    without = [p for p in deep if not (p.get("breadcrumb") or {}).get("visible")]
    if len(without) < len(deep) * 0.5:
        result.skip("breadcrumb-navigation",
                    "{} of {} deep pages already show a breadcrumb trail".format(
                        len(deep) - len(without), len(deep)))
        return

    result.add(
        id_hint="deep-pages-have-no-breadcrumb",
        title="Deep pages give visitors no visible sense of where they are",
        severity="medium", confidence="high",
        evidence="{} of {} crawled deep pages show no breadcrumb trail. Examples: {}.".format(
            len(without), len(deep), ", ".join(example_urls([p["url"] for p in without]))),
        mechanism="G", root_cause="no-breadcrumbs",
        summary="Add a visible breadcrumb trail to deep page templates.",
        how_to_fix=[
            "Add a breadcrumb above the H1 on product, article and location templates: "
            "Home > Section > This page.",
            "Make every step except the current page a working link.",
            "Mirror it in BreadcrumbList structured data so machines read the same hierarchy.",
        ],
        effort="low", owner="developer",
        rationale="Someone arriving from an answer lands deep with no history. A "
                  "breadcrumb is the cheapest way to show them what section they are in and "
                  "give them one click upward instead of a click back.",
        affected_pages=[p["url"] for p in without],
    )


def _check_title_body_drift(result, pages):
    """Does the page deliver what its title promised?"""
    result.check("title-body-alignment")
    candidates = [p for p in pages if p.get("title") and p.get("body_text_len", 0) >= 400]
    if len(candidates) < 3:
        result.skip("title-body-alignment",
                    "fewer than 3 pages have both a title and enough body text to compare")
        return

    drifted = []
    for page in candidates:
        # Drop the brand suffix: it appears in every title and would mask drift.
        title = re.split(r"\s[|\-–—:·•]\s", page["title"])[0]
        tokens = {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in STOPWORDS}
        # A title like "Welcome to GOV.UK" reduces to one content word, and a
        # single miss then reads as 100% drift. Two tokens minimum.
        if len(tokens) < 2:
            continue
        body = page.get("body_text", "").lower()
        matched = sum(1 for t in tokens if t[:6] in body)
        if matched / float(len(tokens)) < 0.34:
            drifted.append((page, title, sorted(tokens)[:4]))

    if not drifted:
        result.skip("title-body-alignment",
                    "every page's title vocabulary appears in its own body text")
        return

    result.add(
        id_hint="page-title-does-not-match-page-content",
        title="{} not deliver what their title promises".format(
            plural(len(drifted), "page does", "pages do")),
        severity="medium", confidence="medium",
        evidence="Pages where under a third of the title's own words appear anywhere in the "
                 "body text. Examples: {}.".format(
                     "; ".join('{} (title "{}", missing {})'.format(p["url"], truncate(t, 50),
                                                                    ", ".join(toks))
                               for p, t, toks in sorted(drifted, key=lambda x: x[0]["url"])[:4])),
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


def _check_chrome_consistency(result, pages):
    result.check("consistent-site-chrome")
    if len(pages) < 4:
        result.skip("consistent-site-chrome",
                    "fewer than 4 pages were crawled, too few to establish what the site's "
                    "normal navigation looks like")
        return

    signatures = [tuple((p.get("chrome_signature") or {}).get("nav_paths") or []) for p in pages]
    # Compare against the most common *non-empty* signature. Taking the plain
    # mode would bail out on exactly the worst case: a site where most pages
    # have no navigation at all, so "no chrome" is the majority.
    counts = Counter(s for s in signatures if s)
    if not counts:
        result.skip("consistent-site-chrome",
                    "no page carries header or footer navigation, which the navigation finding "
                    "covers rather than this one")
        return
    common, common_count = counts.most_common(1)[0]
    # One page with a big menu among nineteen without one does not establish a
    # site norm, and treating it as one flagged every page including itself.
    if common_count < 2 or common_count < len(pages) * 0.2:
        result.skip("consistent-site-chrome",
                    "no header or footer navigation appears on enough pages to establish what "
                    "this site's normal chrome looks like ({} of {} pages share the most "
                    "common one)".format(common_count, len(pages)))
        return

    stranded = []
    for page, signature in zip(pages, signatures):
        if not signature:
            stranded.append(page)
        elif len(set(common) & set(signature)) < max(2, len(common) // 3):
            stranded.append(page)

    if not stranded:
        result.skip("consistent-site-chrome",
                    "all {} crawled pages share the same header and footer navigation".format(
                        len(pages)))
        return

    result.add(
        id_hint="pages-missing-site-navigation",
        title="{} not carry the site's normal navigation".format(
            plural(len(stranded), "page does", "pages do")),
        severity="medium", confidence="medium",
        evidence="The site's usual header/footer links appear on {} of {} pages. These pages "
                 "share few or none of them: {}.".format(
                     common_count, len(pages),
                     ", ".join(example_urls([p["url"] for p in stranded]))),
        mechanism="G", root_cause="inconsistent-chrome",
        summary="Apply the standard header and footer to every page template.",
        how_to_fix=[
            "Check whether these pages use a different layout, a landing-page template, or a "
            "third-party tool that renders its own shell.",
            "Include the standard header and footer partials in those templates.",
            "If a stripped-down template is deliberate (a checkout, say), at minimum keep a "
            "logo linking home.",
        ],
        effort="medium", owner="developer",
        rationale="A visitor who arrives on a page with no navigation has no way "
                  "back into the site. The page becomes the whole experience, and when it ends, "
                  "so does the visit.",
        affected_pages=[p["url"] for p in stranded],
    )


def _check_weight_and_scripts(result, pages):
    result.check("page-weight")
    heavy = []
    for page in pages:
        scripts = page.get("scripts") or {}
        weight = page.get("html_len", 0) + scripts.get("inline_bytes", 0) + scripts.get("style_bytes", 0)
        third_party = scripts.get("third_party_host_count", 0)
        if weight > PAGE_WEIGHT_BYTES:
            heavy.append((page, "{:,} bytes of HTML, inline script and inline CSS".format(weight)))
        elif third_party > THIRD_PARTY_SCRIPT_LIMIT:
            heavy.append((page, "{} third-party script hosts".format(third_party)))

    if not heavy:
        result.skip("page-weight",
                    "no page exceeds {:,} bytes of inline payload or {} third-party script "
                    "hosts; ordinary page weight was measured but not reported, because only "
                    "extremes affect whether a visitor stays".format(
                        PAGE_WEIGHT_BYTES, THIRD_PARTY_SCRIPT_LIMIT))
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
    for page in pages:
        interstitial = page.get("interstitial") or {}
        signals = []
        if interstitial.get("blocking_overlay"):
            signals.append("a modal or overlay is open in the delivered markup")
        if interstitial.get("modal_hints"):
            signals.append("markup for {}".format(", ".join(interstitial["modal_hints"][:2])))
        if (page.get("video") or {}).get("autoplay_count"):
            signals.append("{} auto-playing video element(s)".format(page["video"]["autoplay_count"]))
        if signals:
            offenders.append((page, "; ".join(signals)))

    if not offenders:
        result.skip("intrusive-interstitials",
                    "no page ships an open overlay or auto-playing video in its initial markup. "
                    "A cookie banner alone was not treated as a defect, because most sites are "
                    "required to show one")
        return

    result.add(
        id_hint="content-covered-on-arrival",
        title="{} their content the moment a visitor arrives".format(
            plural(len(offenders), "page covers", "pages cover")),
        severity="medium", confidence="medium",
        evidence="; ".join("{}: {}".format(p["url"], why)
                           for p, why in sorted(offenders, key=lambda x: x[0]["url"])[:5]),
        mechanism="G", root_cause="intrusive-interstitial",
        summary="Let visitors read the page before asking them for anything.",
        how_to_fix=[
            "Delay newsletter and promotional modals until the visitor has scrolled or spent "
            "20 seconds on the page, and never show them on a first pageview from an external link.",
            "Remove autoplay from video, or mute it and require a click.",
            "Keep the cookie banner, which is usually required, but make sure it does not "
            "cover the H1 and first paragraph.",
        ],
        effort="low", owner="marketing",
        rationale="Someone who arrived with a specific question and is shown a "
                  "form before an answer has been given a reason to leave before the page has "
                  "made its case.",
        affected_pages=[p["url"] for p, _ in offenders],
    )


def _check_forms(result, pages):
    result.check("form-friction")
    offenders = []
    for page in pages:
        for form in (page.get("forms") or []):
            if form["is_search"] or form["is_newsletter"]:
                continue
            if form["required_count"] > FORM_FIELD_LIMIT:
                offenders.append((page, form["required_count"]))
                break

    if not offenders:
        result.skip("form-friction",
                    "no enquiry form requires more than {} fields".format(FORM_FIELD_LIMIT))
        return

    result.add(
        id_hint="enquiry-forms-ask-for-too-much",
        title="{} require more than {} fields".format(plural(len(offenders), "enquiry form"), FORM_FIELD_LIMIT),
        severity="low", confidence="high",
        evidence="; ".join("{} ({} required fields)".format(p["url"], n)
                           for p, n in sorted(offenders, key=lambda x: x[0]["url"])[:5]),
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
        affected_pages=[p["url"] for p, _ in offenders],
    )


def _check_readability(result, pages):
    result.check("readability")
    offenders = []
    for page in pages:
        readability = page.get("readability") or {}
        words = page.get("word_count", 0)
        reasons = []
        if readability.get("wall_of_text_count", 0) >= 2:
            reasons.append("{} paragraph(s) over 200 words".format(readability["wall_of_text_count"]))
        if words >= LONG_PAGE_WORDS and readability.get("words_per_subheading", 0) > WALL_OF_TEXT_WORDS:
            reasons.append("{} words with a subheading only every {} words".format(
                words, int(readability["words_per_subheading"])))
        if reasons:
            offenders.append((page, "; ".join(reasons)))

    if not offenders:
        result.skip("readability",
                    "no long page runs without subheadings or contains repeated walls of text")
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


def _check_viewport(result, pages):
    result.check("mobile-viewport")
    without = [p for p in pages if not p.get("has_viewport")]
    if not without:
        result.skip("mobile-viewport", "every crawled page declares a viewport meta tag")
        return
    result.add(
        id_hint="missing-mobile-viewport",
        title="{} no mobile viewport tag".format(
            plural(len(without), "page has", "pages have")),
        severity="medium" if len(without) == len(pages) else "low", confidence="high",
        evidence="Pages with no <meta name=\"viewport\"> tag: {}.".format(
            ", ".join(example_urls([p["url"] for p in without]))),
        mechanism="G", root_cause="no-orientation",
        summary="Add the standard viewport meta tag to the site template.",
        how_to_fix=[
            'Add <meta name="viewport" content="width=device-width, initial-scale=1"> to the '
            "<head> of the base template.",
            "Check the layout on a phone afterwards; the tag alone does not make a fixed-width "
            "layout responsive.",
        ],
        effort="low", owner="developer",
        rationale="Without it, phones render the page at desktop width and zoom "
                  "out. Most people arriving from an assistant are on a phone, and a page they "
                  "have to pinch to read is a page they leave.",
        affected_pages=[p["url"] for p in without],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-network", action="store_true",
                        help="skip the internal-link HEAD probes")
    args = parser.parse_args(argv)

    result = run(load_snapshot(args.snapshot), allow_network=not args.no_network)
    result.write(args.out)
    print("{}: {} finding(s), {} extra request(s)".format(
        SKILL, len(result.findings), result.extra_requests_made), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
