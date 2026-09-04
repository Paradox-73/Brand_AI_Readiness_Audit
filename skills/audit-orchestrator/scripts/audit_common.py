"""Shared vocabulary and helpers for every skill in this marketplace.

The orchestrator owns this module. Sub-skill `check.py` scripts import it by
walking up to `skills/audit-orchestrator/scripts/`, which keeps one definition
of the finding shape, the root-cause vocabulary, and the page-type detector
instead of six drifting copies.

Nothing here performs a write to a target site. The only network primitive is
`Fetcher`, which issues GET/HEAD with a fixed identifying user agent.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from urllib.parse import urljoin, urlparse, urlunparse

# --------------------------------------------------------------------------
# Constants shared by every skill
# --------------------------------------------------------------------------

VERSION = "1.0.0"
USER_AGENT = "BrandAIReadinessAudit/1.0 (+read-only audit)"

# Headers any complete HTTP client sends. We tested whether adding these got us
# into the 15 sites that refuse us: it did not, for any of them. They are here
# because a request with no `Accept` header is an incomplete request, not
# because we expect them to open a door. The User-Agent above never changes and
# never pretends to be a browser - a site that has decided to refuse this
# crawler is entitled to, and the refusal is reported as a finding rather than
# worked around.
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en;q=0.9",
}

# A blocked request is a decision; a dropped one is weather. Retry the weather.
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 1.5
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Crawl budget. Fixed so two runs against the same site see the same pages.
#
# Sixty pages, not thirty, and the number was measured rather than chosen.
# On a large real site thirty pages finished in 84 s and reported 16
# problems; sixty finished in 99 s and reported 19, the three extra being
# whole categories the smaller sample never saw - pages with no onward
# link, articles with no Article markup, videos with no transcript. A
# hundred pages cost a further 72 s and added one. Sixty uses 99 s of the
# 240 s budget, so a slow site still finishes.
SEED = 42
MAX_PAGES = 60
MAX_SITEMAP_SAMPLE = 8      # tried 16, identical findings, reverted
# Two, and re-measured after the page ceiling doubled - because the first
# experiment compared depth 3 against depth 2 at 30 pages, where depth 3 has no
# budget left to reach depth 3, so it could only ever have found nothing. Run
# again at 60: identical findings, and the page distribution says why. Sixty
# pages on a large site is 1 at depth 0, 29 at depth 1 and 30 at depth 2, so
# the budget is exhausted before the third level is reached at all. Depth is
# not the limit here; the page count is.
MAX_DEPTH = 2
REQUEST_TIMEOUT = 10.0
REQUEST_DELAY = 0.5
WALL_CLOCK_BUDGET = 240.0

# WALL_CLOCK_BUDGET governs the crawl. It said nothing about the phase that
# follows it, and that phase makes network requests too: up to 40 link probes,
# 16 sitemap and redirect probes, 10 profile checks and 2 Wikidata lookups.
# Each of those can sit on a REQUEST_TIMEOUT before failing, so 68 requests
# against a slow or half-dead host is 680 seconds of waiting that nothing was
# counting. One real run finished its crawl inside the budget and then took
# 496 seconds in total, which breaks the five-minute ceiling the audit is
# supposed to hold itself to.
#
# So the whole run gets a ceiling, not just the crawl. 285 leaves 15 seconds
# for composing the report and for the four interpreter start-ups, which are
# local work and take about two seconds between them; the rest is margin. A
# caller who deliberately raises --budget above this gets their number plus a
# proportional allowance instead, because at that point the five-minute
# ceiling is a limit they have chosen to leave behind.
RUN_WALL_CLOCK_LIMIT = 285.0

# Two caps on a single response. Both were measured, and both close a hole that
# a green test suite had nothing to say about.
#
# MAX_RESPONSE_BYTES. Nothing limited how much the crawler would read. Pointed
# at a 125 MB response it downloaded all 125 MB into memory and only then threw
# the page away for having the wrong content type. The cap is derived, not
# picked: sixteen real homepages, including the heaviest news and retail sites,
# ran 837 bytes to 1.38 MB with a median of 443 KB, so 5 MB is 3.6x the
# heaviest page we could find. It must also stay *above* engagement-audit's
# PAGE_WEIGHT_BYTES (3 MB), or every oversized page would weigh exactly the cap
# and the "extreme payload" finding could never fire again - the same shape of
# bug as `thin-html`, a check present in the vocabulary that nothing could
# reach. `test_budgets.py` asserts that ordering so the two cannot drift.
#
# REQUEST_TOTAL_TIMEOUT. `requests` counts silence between bytes, not elapsed
# time, so REQUEST_TIMEOUT above is not the bound it reads as. Measured: a
# server sending one byte every nine seconds held a single fetch open past 260
# seconds - against an audit that promises to finish in 300. This is the wall
# clock for one request, and 30 s is 27x the 1.1 s a page averaged on a real
# sixty-page crawl. The deadline can only be tested between chunks, so the
# true worst case is REQUEST_TOTAL_TIMEOUT + REQUEST_TIMEOUT: a stall that
# measured over 260 s now ends in 36 s, bounded by 40.
MAX_RESPONSE_BYTES = 5_000_000
REQUEST_TOTAL_TIMEOUT = 30.0
RESPONSE_CHUNK_BYTES = 65536

# Paths we never touch: they mutate state, cost the owner money, or are
# private. This list is enforced in `Fetcher`, not just documented.
FORBIDDEN_PATH_PATTERNS = (
    "/wp-admin", "/wp-login", "/admin", "/login", "/signin", "/sign-in",
    "/signup", "/sign-up", "/register", "/account", "/cart", "/checkout",
    "/basket", "/logout", "/password", "/api/", "/graphql", "/xmlrpc.php",
)

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
EFFORT_DIVISOR = {"low": 1.0, "medium": 1.5, "high": 2.0}
CONFIDENCE_LEVELS = ("high", "medium", "low")

MECHANISMS = {
    "A": "Crawler must be let in and served readable HTML",
    "B": "Assistants quote what is easy to reach, read and lift verbatim",
    "C": "A page that looks complete to a human can be empty to a machine",
    "D": "Trust comes from agreement across independent sources",
    "E": "Assistants personalise by context, so single-framing content narrows reach",
    "F": "AI email summaries are built from readable text",
    # G is this marketplace's own extension. Mechanisms A-F describe how
    # machines find and repeat content; none of them describes what happens
    # after a person clicks through. Forcing on-site engagement findings into
    # E would misdescribe them, so they get their own letter.
    "G": "A visitor arriving mid-journey from an answer carries context the site "
         "never sees, and leaves unless the page orients them and offers a next step",
}

# Fixed root-cause vocabulary. compose_report.py dedups on these, so a skill
# inventing a new tag would silently break merging - `make_finding` rejects it.
ROOT_CAUSES = frozenset({
    # Gate A - access
    "robots-block", "bot-manager-block", "sitemap-missing", "sitemap-broken",
    "non-200", "redirect-chain", "meta-refresh", "noindex", "canonical-broken",
    "insecure-transport", "host-inconsistency",
    # Distinct from `non-200`: the site answers correctly and merely takes too
    # long. Reporting it as unreachability sent one owner to read server logs
    # for a fault that did not exist, and the actual fix - caching, or a
    # slow query behind the template - is nothing like restoring an outage.
    "slow-origin",
    # Gate A/C - readability
    "js-shell", "thin-html", "image-locked-facts", "pdf-locked-facts",
    "no-transcript", "iframe-content", "uncrawlable-pagination", "alt-missing",
    # Gate C - structured data
    "no-org-schema", "no-product-schema", "no-article-schema", "no-faq-schema",
    "invalid-jsonld",
    "schema-text-mismatch", "missing-schema-props", "meta-hygiene",
    "open-graph-incomplete", "no-website-schema", "missing-lang", "microdata-only",
    # Distinct from `no-breadcrumbs`: that is the visible trail a person
    # follows, this is the machine-readable hierarchy. Different owners,
    # different fixes, so they get different tags.
    "no-breadcrumb-markup",
    # Gate C - extractability
    "no-entity-definition", "heading-structure", "fluff-first",
    "missing-core-fact", "name-inconsistency", "long-sentences",
    # D - freshness and corroboration
    "stale-content", "no-date-signal", "weak-corroboration", "entity-ambiguity",
    "nap-inconsistency", "dead-profile-link",
    # Engagement
    "no-orientation", "dead-end", "inconsistent-chrome", "broken-links", "orphan-pages",
    "no-breadcrumbs", "title-body-drift", "page-weight",
    "intrusive-interstitial", "readability", "form-friction",
})

# Off-site profiles we are willing to check the existence of.
#
# Corroboration is the strongest signal this marketplace measures, and until now
# it counted the profile links a site *declares*. A brand listing a LinkedIn page
# that was deleted two years ago scored the same as one whose page is live, so
# the number we report was a claim rather than a measurement.
#
# Checking is only honest where a "not found" means not found. We asked each
# platform for a profile that certainly does not exist and recorded the answer:
#
#   LinkedIn, X, YouTube, GitHub, Wikipedia, Wikidata   404 for a dead profile,
#                                                       200 for a live one
#   Instagram, TikTok, Medium, Pinterest, Threads       200 for anything, so a
#                                                       200 proves nothing
#   Crunchbase, Yelp, Glassdoor, Trustpilot             403 to any crawler, live
#                                                       or dead alike
#
# So we verify the first group and say nothing about the rest, rather than
# reporting a live profile as dead because its host dislikes crawlers. The same
# rule as everywhere else here: a block is not evidence.
VERIFIABLE_PROFILE_PLATFORMS = ("LinkedIn", "Wikipedia", "Wikidata", "GitHub", "X", "YouTube")

# Statuses that mean the profile is genuinely not there.
PROFILE_GONE_STATUS = frozenset({404, 410})

# A status that means "the crawler was refused", never "the page is gone".
#
# Three checks confirmed a URL existed by sending HEAD - a request that asks
# whether a page is there without downloading it - and filed anything that was
# not 200 as broken. Measured across eight major retail and banking homepages,
# three answered 403 to HEAD while answering 200 to GET. All three would have
# been reported as dead links, one of them at high severity, on the exact class
# of bot-managed commercial site this marketplace exists to audit.
#
# crawl-access-audit had already worked this out for the bot-block check and
# listed these five codes there. The link checks never got the lesson. It lives
# here now so one marketplace cannot mean two things by one status code.
REFUSED_STATUS = frozenset({401, 403, 405, 406, 429})


def confirm_dead(fetcher, url, head_status):
    """A page is dead only if the request a reader would make says so.

    HEAD is the polite probe and it is not the authoritative one. The crawl
    already asks once, per site, whether HEAD means anything here - and that is
    not enough, because support is per-URL, not per-site. A global law firm
    answers HEAD honestly on its homepage and 404 to HEAD on individual
    articles that serve 200 to GET; three of four "internal link targets return
    an error", reported at high confidence, were live pages of current content.
    Acting on that finding means redirecting or deleting working URLs, which is
    the opposite of what the audit is for.

    So a cheap probe may say "alive" on its own and may never say "dead" on its
    own. Only the negatives cost a second request, and on a healthy site there
    are almost none.

    Returns the status to judge on: the GET status when there is one, otherwise
    the HEAD status so the caller still has something to report.
    """
    confirmation = fetcher.try_get(url, method="GET")
    return confirmation.status_code if confirmation is not None else head_status


def link_verdict(status, edge_refusal=False):
    """`alive`, `dead` or `unchecked` for a probed URL.

    `unchecked` is the whole point. A refusal is a fact about the crawler's
    reception, not about whether the page exists, and reporting it as a defect
    in the site would be the same mistake as treating a bot block as evidence -
    which this marketplace refuses to do everywhere else.
    """
    if status is None:
        return "unchecked"
    # An identical few-byte body repeated across many URLs is an edge saying
    # no. The crawl marks those; they are not deleted pages.
    if edge_refusal:
        return "unchecked"
    if status in PROFILE_GONE_STATUS:
        return "dead"
    if status in REFUSED_STATUS:
        return "unchecked"
    if 200 <= status < 400:
        return "alive"
    if status >= 500:
        # The server is having a bad moment. That is not the same as the page
        # having been deleted, and a 503 during a deploy would otherwise fill a
        # report with links that work again ten minutes later.
        return "unchecked"
    return "dead"

# Page types. Detection drives every applicability guard in the marketplace:
# we never expect Product schema on a site that has no product pages.
PAGE_TYPES = (
    "home", "about", "contact", "pricing", "product", "category", "service",
    "article", "documentation", "faq", "location", "comparison", "press",
    "careers", "legal", "other",
)

# Types that carry substantive content a machine would want to quote.
# Legal/careers pages are excluded from most checks - boilerplate terms pages
# lacking schema or a CTA is not a defect.
CONTENT_TYPES = frozenset({
    "home", "about", "contact", "pricing", "product", "category", "service",
    "article", "documentation", "faq", "location", "comparison", "press",
})

# Deep types where a breadcrumb genuinely helps orientation.
DEEP_TYPES = frozenset({"product", "article", "documentation", "location", "service",
                        "comparison"})


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------

LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(?:inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|limited|plc|gmbh|s\.a\.|sa|bv|b\.v\.|"
    r"pty|pte|co|co\.|corp|corp\.|corporation|company|group|holdings|"
    r"llp|lp|ag|nv|n\.v\.|oy|ab|as|srl|spa)\s*$", re.I)


_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_:.+-]+)""", re.I)


def response_text(response):
    """Decode a response the way a browser would, not the way HTTP/1.1 says.

    `requests` follows the specification: a `text/*` response with no `charset`
    in the header is ISO-8859-1. Almost nothing on the web means that. A page
    served as UTF-8 that declares its encoding only in a `<meta charset>` tag -
    which is most of them - comes back through `response.text` as mojibake, and
    a brand called "El Corte Ingles" with an accent arrives with a replacement
    character embedded in its name. That then flows into the brand name, the
    evidence text and the report a judge reads.

    Header first, because a server that states a charset means it. Then the
    document's own declaration. Then charset detection. Then UTF-8, which is
    right far more often than Latin-1.
    """
    content = response.content or b""
    if not content:
        return ""

    header = (response.headers.get("content-type") or "").lower()
    declared = ""
    if "charset=" in header:
        declared = header.split("charset=", 1)[1].split(";")[0].strip()
        declared = declared.strip(chr(34) + chr(39))
    if not declared:
        match = _META_CHARSET_RE.search(content[:4096])
        if match:
            declared = match.group(1).decode("ascii", "ignore")
    for candidate in (declared, getattr(response, "apparent_encoding", None), "utf-8"):
        if not candidate:
            continue
        try:
            return content.decode(candidate, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Language
#
# Every prose check in this marketplace reasons about English: the definition
# pattern "<Brand> is a ...", the warm-up phrases that mark a section as not
# answering first, the thirty-word sentence threshold, the imperative verbs that
# identify a call to action, and the list of headings that say nothing ("home",
# "welcome"). None of that transfers. Run on a German page they do not produce
# subtly worse answers - they produce confident nonsense, which is the one thing
# an audit must never do.
#
# So the language is detected once and the checks that cannot judge it say so.
# A report that states "the prose checks were not run: this site is in German,
# and they only reason about English" is more useful than one that guesses, and
# it fits the rule the rest of the marketplace already follows - silence is
# explained.
# --------------------------------------------------------------------------

PROSE_LANGUAGE = "en"

# Function words are the cheapest reliable signal there is: they are frequent,
# short, and almost disjoint between these languages. Used only when a page
# declares no `lang` attribute at all.
_LANGUAGE_MARKERS = {
    "en": (" the ", " and ", " of ", " to ", " that ", " with ", " for ", " is "),
    "de": (" der ", " die ", " das ", " und ", " mit ", " für ", " ist ", " den "),
    "fr": (" le ", " la ", " les ", " des ", " et ", " pour ", " avec ", " est "),
    "es": (" el ", " la ", " los ", " las ", " de ", " para ", " con ", " es "),
    "nl": (" de ", " het ", " een ", " en ", " van ", " voor ", " met ", " is "),
    "it": (" il ", " la ", " di ", " che ", " per ", " con ", " del ", " una "),
    "pt": (" o ", " a ", " de ", " que ", " para ", " com ", " uma ", " dos "),
}


def primary_subtag(lang):
    """`de-DE` -> `de`. Empty string for anything unusable."""
    value = str(lang or "").strip().lower()
    return value.split("-")[0].split("_")[0] if value else ""



# --------------------------------------------------------------------------
# Language editions
#
# A site with one edition per language puts the language in the first path
# segment: /de/ueber-uns, /fr/a-propos, /en-gb/about. Every URL-shaped
# comparison then has to be made inside an edition rather than across the
# whole site, because a German page's navigation legitimately points at
# German URLs and shares not one path with the English menu.
#
# Comparing them raw reported a fully navigable German edition as pages
# carrying no site navigation - a medium finding, on a site whose menu is
# present, complete and translated. The fix is not a list of language codes to
# ignore. It is to establish the site's normal chrome once per edition.
# --------------------------------------------------------------------------

# A two-letter language code, optionally with a region or script subtag.
_LOCALE_SEGMENT_RE = re.compile(
    r"^[a-z]{2}(?:[-_](?:[a-z]{2}|[a-z]{4}|\d{3}))?$", re.I)

# Two-letter path segments that are ordinary sections on English sites, where
# reading them as a language would split a monolingual site into editions.
_NOT_A_LOCALE = frozenset({"us", "uk", "eu", "ac", "co", "hr", "id", "pr", "tv"})


def _locale_candidate(url):
    """The leading path segment, if it is shaped like a language code."""
    path = urlparse(url).path
    first = path.strip("/").split("/")[0].lower() if path.strip("/") else ""
    if not first or first in _NOT_A_LOCALE:
        return ""
    if not _LOCALE_SEGMENT_RE.match(first):
        return ""
    return first


def locale_editions(pages):
    """Map each page URL to its language edition, or "" if the site has none.

    A leading segment counts as an edition only on evidence, never on shape
    alone. Either the page's own `lang` attribute names that language - the
    site saying so itself, which is the strongest evidence available - or at
    least two different language-shaped prefixes appear across the crawl, which
    no monolingual site produces by accident.
    """
    candidates = {}
    for page in pages or []:
        if page.get("status") != 200:
            continue
        candidate = _locale_candidate(page.get("url") or "")
        if candidate:
            candidates[page["url"]] = candidate

    if not candidates:
        return {}

    self_declared = set()
    for page in pages or []:
        candidate = candidates.get(page.get("url"))
        if candidate and primary_subtag(page.get("lang")) == candidate.split("-")[0].split("_")[0]:
            self_declared.add(candidate)

    distinct = set(candidates.values())
    trusted = self_declared if self_declared else (distinct if len(distinct) > 1 else set())
    if not trusted:
        return {}
    return {url: code for url, code in candidates.items() if code in trusted}


def group_by_edition(pages, editions):
    """Pages grouped by language edition, smallest useful unit last.

    Pages outside any edition - a shared root, a sitemap page, an asset
    landing page - are their own group, because they genuinely do share one
    chrome with each other.
    """
    groups = {}
    for page in pages:
        groups.setdefault(editions.get(page.get("url"), ""), []).append(page)
    return groups


# --------------------------------------------------------------------------
# Language editions
#
# A site with one edition per language puts the language in the first path
# segment: /de/ueber-uns, /fr/a-propos, /en-gb/about. Every URL-shaped
# comparison then has to be made inside an edition rather than across the
# whole site, because a German page's navigation legitimately points at
# German URLs and shares not one path with the English menu.
#
# Comparing them raw reported a fully navigable German edition as pages
# carrying no site navigation - a medium finding, on a site whose menu is
# present, complete and translated. The fix is not a list of language codes to
# ignore. It is to establish the site's normal chrome once per edition.
# --------------------------------------------------------------------------

# A two-letter language code, optionally with a region or script subtag.
_LOCALE_SEGMENT_RE = re.compile(
    r"^[a-z]{2}(?:[-_](?:[a-z]{2}|[a-z]{4}|\d{3}))?$", re.I)

# Two-letter path segments that are ordinary sections on English sites, where
# reading them as a language would split a monolingual site into editions.
_NOT_A_LOCALE = frozenset({"us", "uk", "eu", "ac", "co", "hr", "id", "pr", "tv"})


def _locale_candidate(url):
    """The leading path segment, if it is shaped like a language code."""
    path = urlparse(url).path
    first = path.strip("/").split("/")[0].lower() if path.strip("/") else ""
    if not first or first in _NOT_A_LOCALE:
        return ""
    if not _LOCALE_SEGMENT_RE.match(first):
        return ""
    return first


def locale_editions(pages):
    """Map each page URL to its language edition, or "" if the site has none.

    A leading segment counts as an edition only on evidence, never on shape
    alone. Either the page's own `lang` attribute names that language - the
    site saying so itself, which is the strongest evidence available - or at
    least two different language-shaped prefixes appear across the crawl, which
    no monolingual site produces by accident.
    """
    candidates = {}
    for page in pages or []:
        if page.get("status") != 200:
            continue
        candidate = _locale_candidate(page.get("url") or "")
        if candidate:
            candidates[page["url"]] = candidate

    if not candidates:
        return {}

    self_declared = set()
    for page in pages or []:
        candidate = candidates.get(page.get("url"))
        if candidate and primary_subtag(page.get("lang")) == candidate.split("-")[0].split("_")[0]:
            self_declared.add(candidate)

    distinct = set(candidates.values())
    trusted = self_declared if self_declared else (distinct if len(distinct) > 1 else set())
    if not trusted:
        return {}
    return {url: code for url, code in candidates.items() if code in trusted}


def group_by_edition(pages, editions):
    """Pages grouped by language edition, smallest useful unit last.

    Pages outside any edition - a shared root, a sitemap page, an asset
    landing page - are their own group, because they genuinely do share one
    chrome with each other.
    """
    groups = {}
    for page in pages:
        groups.setdefault(editions.get(page.get("url"), ""), []).append(page)
    return groups


# --------------------------------------------------------------------------
# Language editions
#
# A site with one edition per language puts the language in the first path
# segment: /de/ueber-uns, /fr/a-propos, /en-gb/about. Every URL-shaped
# comparison then has to be made inside an edition rather than across the
# whole site, because a German page's navigation legitimately points at
# German URLs and shares not one path with the English menu.
#
# Comparing them raw reported a fully navigable German edition as pages
# carrying no site navigation - a medium finding, on a site whose menu is
# present, complete and translated. The fix is not a list of language codes to
# ignore. It is to establish the site's normal chrome once per edition.
# --------------------------------------------------------------------------

# A two-letter language code, optionally with a region or script subtag.
_LOCALE_SEGMENT_RE = re.compile(
    r"^[a-z]{2}(?:[-_](?:[a-z]{2}|[a-z]{4}|\d{3}))?$", re.I)

# Two-letter path segments that are ordinary sections on English sites, where
# reading them as a language would split a monolingual site into editions.
_NOT_A_LOCALE = frozenset({"us", "uk", "eu", "ac", "co", "hr", "id", "pr", "tv"})


def _locale_candidate(url):
    """The leading path segment, if it is shaped like a language code."""
    path = urlparse(url).path
    first = path.strip("/").split("/")[0].lower() if path.strip("/") else ""
    if not first or first in _NOT_A_LOCALE:
        return ""
    if not _LOCALE_SEGMENT_RE.match(first):
        return ""
    return first


def locale_editions(pages):
    """Map each page URL to its language edition, or "" if the site has none.

    A leading segment counts as an edition only on evidence, never on shape
    alone. Either the page's own `lang` attribute names that language - the
    site saying so itself, which is the strongest evidence available - or at
    least two different language-shaped prefixes appear across the crawl, which
    no monolingual site produces by accident.
    """
    candidates = {}
    for page in pages or []:
        if page.get("status") != 200:
            continue
        candidate = _locale_candidate(page.get("url") or "")
        if candidate:
            candidates[page["url"]] = candidate

    if not candidates:
        return {}

    self_declared = set()
    for page in pages or []:
        candidate = candidates.get(page.get("url"))
        if candidate and primary_subtag(page.get("lang")) == candidate.split("-")[0].split("_")[0]:
            self_declared.add(candidate)

    distinct = set(candidates.values())
    trusted = self_declared if self_declared else (distinct if len(distinct) > 1 else set())
    if not trusted:
        return {}
    return {url: code for url, code in candidates.items() if code in trusted}


def group_by_edition(pages, editions):
    """Pages grouped by language edition, smallest useful unit last.

    Pages outside any edition - a shared root, a sitemap page, an asset
    landing page - are their own group, because they genuinely do share one
    chrome with each other.
    """
    groups = {}
    for page in pages:
        groups.setdefault(editions.get(page.get("url"), ""), []).append(page)
    return groups


# --------------------------------------------------------------------------
# Language editions
#
# A site with one edition per language puts the language in the first path
# segment: /de/ueber-uns, /fr/a-propos, /en-gb/about. Every URL-shaped
# comparison then has to be made inside an edition rather than across the
# whole site, because a German page's navigation legitimately points at
# German URLs and shares not one path with the English menu.
#
# Comparing them raw reported a fully navigable German edition as pages
# carrying no site navigation - a medium finding, on a site whose menu is
# present, complete and translated. The fix is not a list of language codes to
# ignore. It is to establish the site's normal chrome once per edition.
# --------------------------------------------------------------------------

# A two-letter language code, optionally with a region or script subtag.
_LOCALE_SEGMENT_RE = re.compile(
    r"^[a-z]{2}(?:[-_](?:[a-z]{2}|[a-z]{4}|\d{3}))?$", re.I)

# Two-letter path segments that are ordinary sections on English sites, where
# reading them as a language would split a monolingual site into editions.
_NOT_A_LOCALE = frozenset({"us", "uk", "eu", "ac", "co", "hr", "id", "pr", "tv"})


def _locale_candidate(url):
    """The leading path segment, if it is shaped like a language code."""
    path = urlparse(url).path
    first = path.strip("/").split("/")[0].lower() if path.strip("/") else ""
    if not first or first in _NOT_A_LOCALE:
        return ""
    if not _LOCALE_SEGMENT_RE.match(first):
        return ""
    return first


def locale_editions(pages):
    """Map each page URL to its language edition, or "" if the site has none.

    A leading segment counts as an edition only on evidence, never on shape
    alone. Either the page's own `lang` attribute names that language - the
    site saying so itself, which is the strongest evidence available - or at
    least two different language-shaped prefixes appear across the crawl, which
    no monolingual site produces by accident.
    """
    candidates = {}
    for page in pages or []:
        if page.get("status") != 200:
            continue
        candidate = _locale_candidate(page.get("url") or "")
        if candidate:
            candidates[page["url"]] = candidate

    if not candidates:
        return {}

    self_declared = set()
    for page in pages or []:
        candidate = candidates.get(page.get("url"))
        if candidate and primary_subtag(page.get("lang")) == candidate.split("-")[0].split("_")[0]:
            self_declared.add(candidate)

    distinct = set(candidates.values())
    trusted = self_declared if self_declared else (distinct if len(distinct) > 1 else set())
    if not trusted:
        return {}
    return {url: code for url, code in candidates.items() if code in trusted}


def group_by_edition(pages, editions):
    """Pages grouped by language edition, smallest useful unit last.

    Pages outside any edition - a shared root, a sitemap page, an asset
    landing page - are their own group, because they genuinely do share one
    chrome with each other.
    """
    groups = {}
    for page in pages:
        groups.setdefault(editions.get(page.get("url"), ""), []).append(page)
    return groups


# --------------------------------------------------------------------------
# Language editions
#
# A site with one edition per language puts the language in the first path
# segment: /de/ueber-uns, /fr/a-propos, /en-gb/about. Every URL-shaped
# comparison then has to be made inside an edition rather than across the
# whole site, because a German page's navigation legitimately points at
# German URLs and shares not one path with the English menu.
#
# Comparing them raw reported a fully navigable German edition as pages
# carrying no site navigation - a medium finding, on a site whose menu is
# present, complete and translated. The fix is not a list of language codes to
# ignore. It is to establish the site's normal chrome once per edition.
# --------------------------------------------------------------------------

# A two-letter language code, optionally with a region or script subtag.
_LOCALE_SEGMENT_RE = re.compile(
    r"^[a-z]{2}(?:[-_](?:[a-z]{2}|[a-z]{4}|\d{3}))?$", re.I)

# Two-letter path segments that are ordinary sections on English sites, where
# reading them as a language would split a monolingual site into editions.
_NOT_A_LOCALE = frozenset({"us", "uk", "eu", "ac", "co", "hr", "id", "pr", "tv"})


def _locale_candidate(url):
    """The leading path segment, if it is shaped like a language code."""
    path = urlparse(url).path
    first = path.strip("/").split("/")[0].lower() if path.strip("/") else ""
    if not first or first in _NOT_A_LOCALE:
        return ""
    if not _LOCALE_SEGMENT_RE.match(first):
        return ""
    return first


def locale_editions(pages):
    """Map each page URL to its language edition, or "" if the site has none.

    A leading segment counts as an edition only on evidence, never on shape
    alone. Either the page's own `lang` attribute names that language - the
    site saying so itself, which is the strongest evidence available - or at
    least two different language-shaped prefixes appear across the crawl, which
    no monolingual site produces by accident.
    """
    candidates = {}
    for page in pages or []:
        if page.get("status") != 200:
            continue
        candidate = _locale_candidate(page.get("url") or "")
        if candidate:
            candidates[page["url"]] = candidate

    if not candidates:
        return {}

    self_declared = set()
    for page in pages or []:
        candidate = candidates.get(page.get("url"))
        if candidate and primary_subtag(page.get("lang")) == candidate.split("-")[0].split("_")[0]:
            self_declared.add(candidate)

    distinct = set(candidates.values())
    trusted = self_declared if self_declared else (distinct if len(distinct) > 1 else set())
    if not trusted:
        return {}
    return {url: code for url, code in candidates.items() if code in trusted}


def group_by_edition(pages, editions):
    """Pages grouped by language edition, smallest useful unit last.

    Pages outside any edition - a shared root, a sitemap page, an asset
    landing page - are their own group, because they genuinely do share one
    chrome with each other.
    """
    groups = {}
    for page in pages:
        groups.setdefault(editions.get(page.get("url"), ""), []).append(page)
    return groups


# --------------------------------------------------------------------------
# Language editions
#
# A site with one edition per language puts the language in the first path
# segment: /de/ueber-uns, /fr/a-propos, /en-gb/about. Every URL-shaped
# comparison then has to be made inside an edition rather than across the
# whole site, because a German page's navigation legitimately points at
# German URLs and shares not one path with the English menu.
#
# Comparing them raw reported a fully navigable German edition as pages
# carrying no site navigation - a medium finding, on a site whose menu is
# present, complete and translated. The fix is not a list of language codes to
# ignore. It is to establish the site's normal chrome once per edition.
# --------------------------------------------------------------------------

# A two-letter language code, optionally with a region or script subtag.
_LOCALE_SEGMENT_RE = re.compile(
    r"^[a-z]{2}(?:[-_](?:[a-z]{2}|[a-z]{4}|\d{3}))?$", re.I)

# Two-letter path segments that are ordinary sections on English sites, where
# reading them as a language would split a monolingual site into editions.
_NOT_A_LOCALE = frozenset({"us", "uk", "eu", "ac", "co", "hr", "id", "pr", "tv"})


def _locale_candidate(url):
    """The leading path segment, if it is shaped like a language code."""
    path = urlparse(url).path
    first = path.strip("/").split("/")[0].lower() if path.strip("/") else ""
    if not first or first in _NOT_A_LOCALE:
        return ""
    if not _LOCALE_SEGMENT_RE.match(first):
        return ""
    return first


def locale_editions(pages):
    """Map each page URL to its language edition, or "" if the site has none.

    A leading segment counts as an edition only on evidence, never on shape
    alone. Either the page's own `lang` attribute names that language - the
    site saying so itself, which is the strongest evidence available - or at
    least two different language-shaped prefixes appear across the crawl, which
    no monolingual site produces by accident.
    """
    candidates = {}
    for page in pages or []:
        if page.get("status") != 200:
            continue
        candidate = _locale_candidate(page.get("url") or "")
        if candidate:
            candidates[page["url"]] = candidate

    if not candidates:
        return {}

    self_declared = set()
    for page in pages or []:
        candidate = candidates.get(page.get("url"))
        if candidate and primary_subtag(page.get("lang")) == candidate.split("-")[0].split("_")[0]:
            self_declared.add(candidate)

    distinct = set(candidates.values())
    trusted = self_declared if self_declared else (distinct if len(distinct) > 1 else set())
    if not trusted:
        return {}
    return {url: code for url, code in candidates.items() if code in trusted}


def group_by_edition(pages, editions):
    """Pages grouped by language edition, smallest useful unit last.

    Pages outside any edition - a shared root, a sitemap page, an asset
    landing page - are their own group, because they genuinely do share one
    chrome with each other.
    """
    groups = {}
    for page in pages:
        groups.setdefault(editions.get(page.get("url"), ""), []).append(page)
    return groups

def guess_language_from_text(text):
    """Language of a body of text from function-word frequency.

    Deliberately crude and deliberately cautious: it returns "" unless one
    language leads clearly, because guessing wrongly is worse than not guessing.
    """
    sample = " " + re.sub(r"\s+", " ", (text or "")[:4000]).lower() + " "
    if len(sample) < 200:
        return ""
    scores = {code: sum(sample.count(word) for word in words)
              for code, words in _LANGUAGE_MARKERS.items()}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, runner_up = ranked[0], ranked[1]
    if best[1] < 5 or best[1] < runner_up[1] * 1.5:
        return ""
    return best[0]


def detect_site_language(pages):
    """The language of the site, and how we know.

    Declared `lang` attributes first, because a site stating its own language is
    better evidence than anything inferred from its words. Text only decides
    when nothing is declared.
    """
    declared = {}
    for page in pages or []:
        if page.get("status") != 200:
            continue
        code = primary_subtag(page.get("lang"))
        if code:
            declared[code] = declared.get(code, 0) + 1
    if declared:
        code = max(declared, key=lambda k: (declared[k], k))
        return {"code": code, "source": "declared",
                "pages_declaring": sum(declared.values()),
                "prose_checks_apply": code == PROSE_LANGUAGE}

    text = " ".join((p.get("body_text") or "")[:2000] for p in (pages or [])[:5])
    guessed = guess_language_from_text(text)
    if guessed:
        return {"code": guessed, "source": "inferred from the page text",
                "pages_declaring": 0,
                "prose_checks_apply": guessed == PROSE_LANGUAGE}
    # Nothing declared and nothing clear in the text. Proceed as English, which
    # is what every earlier version did unconditionally, but record that it is
    # an assumption so a reader can discount the prose findings if it is wrong.
    return {"code": "", "source": "not declared and not inferable",
            "pages_declaring": 0, "prose_checks_apply": True}


def language_of(snapshot):
    """The language record a check should consult. Always returns a dict."""
    value = snapshot.get("site_language")
    if isinstance(value, dict) and value:
        return value
    return detect_site_language(snapshot.get("pages") or [])




# --------------------------------------------------------------------------
# A name written in one script cannot be looked for in another
# --------------------------------------------------------------------------

_SCRIPT_RANGES = (
    ("latin", ((0x41, 0x5A), (0x61, 0x7A), (0xC0, 0x24F))),
    ("greek", ((0x370, 0x3FF),)),
    ("cyrillic", ((0x400, 0x4FF),)),
    ("hebrew", ((0x590, 0x5FF),)),
    ("arabic", ((0x600, 0x6FF), (0x750, 0x77F))),
    ("devanagari", ((0x900, 0x97F),)),
    ("thai", ((0xE00, 0xE7F),)),
    ("hangul", ((0xAC00, 0xD7AF), (0x1100, 0x11FF))),
    ("kana", ((0x3040, 0x30FF),)),
    ("han", ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))),
)


# Writing systems that put no space between one word and the next. Splitting
# on spaces there does not merely lose precision: 86 characters of Japanese -
# about forty words of English - count as seven, and an entire page counts as
# one sentence. Every threshold in this audit written in words is therefore
# unmeasurable on these scripts, and the checks that use one say so rather
# than reporting the number they would have got.
UNSPACED_SCRIPTS = frozenset({"han", "kana", "thai"})

# Below this many letters, no script is dominant enough to decide anything.
SCRIPT_SAMPLE_MINIMUM = 20


def script_counts(text):
    """How many letters of each writing system this text holds."""
    counts = {}
    for char in text or "":
        code = ord(char)
        for name, ranges in _SCRIPT_RANGES:
            if any(low <= code <= high for low, high in ranges):
                counts[name] = counts.get(name, 0) + 1
                break
    return counts


def scripts_in(text):
    """Which writing systems this text uses, ignoring digits and punctuation."""
    return set(script_counts(text))


def dominant_script(text):
    """The writing system most of this text is in, or None if it is too short.

    A page of English quoting one Japanese product name is English. The answer
    is the script holding most of the letters, not every script present.
    """
    counts = script_counts(text)
    if sum(counts.values()) < SCRIPT_SAMPLE_MINIMUM:
        return None
    return max(sorted(counts), key=counts.get)


def words_are_separated(text):
    """True when counting the words in this text means what it usually means.

    True as well when the script cannot be decided, so a short string is
    measured rather than silently skipped.
    """
    return dominant_script(text) not in UNSPACED_SCRIPTS


def written_in_the_same_script(name, text):
    """Could this name appear in this text at all?

    A page in Arabic does not contain a Latin-script company name, and its
    absence says nothing about the markup. Reported as a conflict, an Arabic
    edition of a foundation's site was told at high severity that its
    structured data contradicted the page - about markup that was correct and
    a page that was correct, on the strength of a comparison that could only
    ever have one answer.

    True when nothing can be decided from script alone: a name with no letters
    at all, or a page whose text is too short to characterise.
    """
    name_scripts = scripts_in(name)
    text_scripts = scripts_in((text or "")[:4000])
    if not name_scripts or not text_scripts:
        return True
    return bool(name_scripts & text_scripts)

def pages_in_prose_language(pages):
    """Pages this audit's English prose checks are entitled to read.

    Site language is one majority vote across whatever the crawl sampled, and
    for a monolingual site that is right. On a bilingual broadcaster it was
    decided by luck: 28 pages tagged `en`, 18 tagged `de` and five other
    languages besides, so "English" won the vote and the English-tuned checks
    - a 30-word sentence threshold, the "<Brand> is a ..." pattern, the
    call-to-action verbs - then ran over every content page including the
    German ones. One German category page scored 59% long sentences and
    escaped a finding only because its page type was not in the allowlist.

    A page that declares its own language is the authority on it. A page that
    declares none inherits the site verdict, which is the best available
    answer and the common case on smaller sites.
    """
    out = []
    for page in pages or []:
        declared = primary_subtag(page.get("lang"))
        if declared and declared != PROSE_LANGUAGE:
            continue
        out.append(page)
    return out


def other_language_pages(pages):
    """The pages `pages_in_prose_language` set aside, for saying so."""
    return [p for p in pages or []
            if primary_subtag(p.get("lang"))
            and primary_subtag(p.get("lang")) != PROSE_LANGUAGE]

def prose_skip_reason(language):
    """Why a prose check declined, in words a site owner can act on."""
    return ("this site is in {} ({}), and this check reasons about English prose only. "
            "It was not run rather than guessed at. The structural checks in this skill "
            "were unaffected.".format(
                (language.get("code") or "an undetermined language").upper(),
                language.get("source", "detected")))


def name_forms(declared):
    """Every way an organisation could reasonably refer to itself in a sentence.

    A declared name is often formal or legal: "Acme Analytics Holdings, Inc.",
    "Department of Computer Science, University of Oxford". Neither appears
    verbatim in the sentences the organisation writes about itself, and a site
    is not wrong to write "Acme is a ..." while declaring the full name in its
    markup. Two separate checks required the whole string and therefore could
    not be satisfied by any organisation with a long formal name.

    Longest first, so a caller matching in order reports the most specific form
    the text actually used.
    """
    declared = re.sub(r"\s+", " ", str(declared or "")).strip()
    if not declared:
        return []
    forms = {declared}
    # Stacked suffixes are common: "Holdings, Inc.", "Group Ltd", "Co. Limited".
    # One pass leaves "... Holdings", which is still not what anyone writes.
    trimmed = declared
    for _ in range(4):
        stripped = LEGAL_SUFFIX_RE.sub("", trimmed).strip().rstrip(",").strip()
        if stripped == trimmed or not stripped:
            break
        trimmed = stripped
        forms.add(trimmed)
    for value in list(forms):
        head = value.split(",")[0].strip()
        if head:
            forms.add(head)
    return sorted({f for f in forms if len(f) > 2}, key=len, reverse=True)


def pct(part, whole):
    """Percentage rounded to one decimal; 0.0 when the denominator is zero."""
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 1)


def share(part, whole):
    """Fraction in [0, 1]; 0.0 when the denominator is zero."""
    if not whole:
        return 0.0
    return part / float(whole)


def sample(items, n=5, seed=SEED):
    """Deterministic sample of up to `n` items.

    Sorts first so the input order (which depends on crawl timing) cannot
    change the output, then draws with a fixed seed.
    """
    pool = sorted(set(items))
    if len(pool) <= n:
        return pool
    rng = random.Random(seed)
    return sorted(rng.sample(pool, n))


def strip_www(host):
    return host[4:] if host.startswith("www.") else host


def same_site(url_a, url_b):
    """True when two URLs share a registrable-ish host, ignoring `www.`."""
    try:
        return strip_www(urlparse(url_a).netloc.lower()) == strip_www(
            urlparse(url_b).netloc.lower()
        )
    except ValueError:
        return False


# Default documents that every common server also serves at the directory root.
# Folding them together stops the crawler spending half its budget fetching `/`
# and `/index.html` as if they were two pages.
#
# `home.html` was in this list and should not have been. No server serves it at
# the directory root; it is an ordinary page name, and a great many sites use it
# for a real page. Folding it meant that page was merged into the homepage and
# never crawled as itself - and if a site genuinely publishes the same content at
# both `/` and `/home.html`, that is duplication we should report rather than
# quietly hide.
_INDEX_FILE_RE = re.compile(r"/(?:index|default)\.(?:html?|php|aspx?|jsp)$", re.I)


def normalise_url(url, base=None):
    """Absolutise, drop the fragment, and fold default index documents to `/`.

    Query strings are preserved: on many sites they select real content.
    """
    if base:
        url = urljoin(base, url)
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    path = parts.path or "/"
    path = _INDEX_FILE_RE.sub("/", path) or "/"
    return urlunparse((parts.scheme, parts.netloc, path, parts.params, parts.query, ""))


def origin_of(url):
    """`https://example.com` from any URL on that origin."""
    parts = urlparse(url if "://" in url else "https://" + url)
    return "{}://{}".format(parts.scheme or "https", parts.netloc)


def site_label(url):
    """Bare host used as the `site` field of the report."""
    return urlparse(url if "://" in url else "https://" + url).netloc or url


def is_forbidden_path(url):
    """Guardrail: state-changing and private areas are never fetched."""
    path = (urlparse(url).path or "/").lower()
    return any(pattern in path for pattern in FORBIDDEN_PATH_PATTERNS)


def truncate(text, limit=280):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def word_count(text):
    return len(re.findall(r"\b[\w'’-]+\b", text or ""))


# Three ways a sentence ends, because one of them only works in English.
#
# The first branch is the Latin case: a full stop, a space, and something that
# is not the continuation of the same sentence - the lookahead is what keeps
# "Dr. Smith" together. It used to require a capital letter next, which meant
# it never fired in a script that has no capitals. An Arabic news site and a
# Japanese one were each reported as a single sentence hundreds of words long,
# and the check that measures sentence length skipped with the reason "no page
# has enough prose to measure" - about sites that are nothing but prose.
#
# The second and third branches are terminators that end a sentence on their
# own, with no space and no capital to look for: the full-width stops used
# with Chinese, Japanese and Korean text, and the Indic danda, Urdu full stop
# and Arabic question mark.
_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?])\s+(?![a-z\d])"
    r"|(?<=[。！？])\s*"
    r"|(?<=[।॥۔؟])\s*")


def sentences(text):
    """Rough sentence split. Good enough for length statistics."""
    parts = _SENTENCE_SPLIT.split((text or "").strip())
    return [p.strip() for p in parts if p and p.strip()]


# --------------------------------------------------------------------------
# Page-type detection
# --------------------------------------------------------------------------

# Type slugs, matched against whole path *segments*. Substring matching was
# classifying `/2004/Jun/29/job/` as a careers page and
# `/2002/Jul/3/alternativeValidatorIcons/` as a comparison page, because "job"
# and "alternative" appeared somewhere in the path. A slug only counts when it
# is a segment, or the start of a hyphenated segment ("about" matches
# "about-us" but not "roundabout").
_URL_TYPE_SLUGS = (
    ("legal", ("privacy", "privacy-policy", "terms", "tos", "legal", "cookie", "cookies",
               "gdpr", "imprint", "disclaimer", "accessibility", "eula")),
    ("careers", ("career", "careers", "job", "jobs", "vacancies", "work-with-us", "join-us")),
    ("press", ("press", "newsroom", "media-kit", "media-centre", "media-center",
               "brand-assets", "press-kit")),
    ("comparison", ("compare", "comparison", "alternative", "alternatives", "vs", "versus")),
    ("faq", ("faq", "faqs", "frequently-asked-questions", "help-center", "help-centre")),
    ("pricing", ("pricing", "price", "prices", "plan", "plans", "packages", "rates",
                 "subscribe", "membership")),
    ("contact", ("contact", "contact-us", "get-in-touch", "reach-us", "enquiries",
                 "inquiries", "enquiry")),
    # "history" alone is not an about page. A broadcaster runs a History
    # documentary vertical, and four of the five pages a brand-definition
    # finding named were episodes of it - subjected to a check about what the
    # organisation is, because a bare content word sat in this list. The
    # qualified forms are unambiguous and the bare one never was.
    ("about", ("about", "about-us", "company", "who-we-are", "our-story", "our-team",
               "team", "mission", "our-history", "company-history",
               "our-mission", "our-values", "leadership")),
    ("location", ("location", "showroom", "find-us", "visit-us", "where-we-are",
                  "where-to-find-us", "store-finder", "store-locator")),
    # Episodes are articles for our purposes: dated published pieces that a
    # machine should be able to read, quote and date. Without them a podcast
    # episode page classified as "other" and every content check skipped it.
    # Reference material, checked before "article" because a documentation
    # page is not an authored piece. It has no byline and no publication date
    # by design, and demanding `author` and `datePublished` on it produced
    # four findings across two sites telling engineering teams to attribute a
    # third-party integration reference and a set of onboarding guides to a
    # named person. Everything else a content page is asked for still applies.
    ("documentation", ("docs", "doc", "documentation", "reference", "manual",
                       "handbook", "wiki", "knowledge-base", "knowledgebase", "kb",
                       "tutorial", "tutorials", "enablement", "learn", "api")),
    ("article", ("blog", "news", "article", "articles", "post", "posts", "insight",
                 "insights", "stories", "story", "guides", "resources", "journal",
                 "episode", "episodes", "podcast", "podcasts", "transcript",
                 "transcripts")),
    ("product", ("product", "products", "item", "p", "sku")),
    ("category", ("collection", "collections", "category", "categories", "catalog",
                  "catalogue", "shop", "browse")),
    ("service", ("service", "services", "solution", "solutions", "what-we-do",
                 "capabilities", "offerings", "expertise")),
)

# Three shapes a real slug takes that a bare list of words does not match.
# A multi-location business kept its branches at `/our-shops`, and no slug in
# any list matched, so per-branch `LocalBusiness` markup was never recommended
# on a site with fourteen shops. Adding "our-shops" to the location list, then
# "our-locations", "our-stores", "store-finder" and "where-we-are" as the next
# five sites arrived, is the sixty-patches approach. These are the rules those
# cases share:
#
#   a qualifier in front    /our-shops, /the-team, /all-locations, /find-a-store
#   a plural                /branches, /clinics, /faqs
#   a derived noun after    /store-finder, /store-locator, /branch-directory
#
# Measured against the risk of over-matching: the qualifier list is closed and
# short, the plural is `s` or `es`, and the suffix list names nouns that only
# ever turn a place word into another place word. "-on-sponge" is still not a
# suffix, so /products/press-on-sponge is still not a press page.
_QUALIFIER_PREFIX = r"(?:our|the|all|my|your|find(?:-a)?|browse)-"

_DERIVED_SUFFIX = (r"us|kit|centre|center|page|policy|info|list|room|"
                   r"finder|locator|directory|map|near-me|near-you|us\.html")

# Words that name a place only in the plural. `/shop` and `/store` are the way
# into an online shop; `/shops` and `/stores` are a list of branches. `/office`
# could be a product aisle; `/offices` is where a firm has people. The same
# word, one letter apart, means two different kinds of page - so a singular
# match here would have turned every e-commerce entry point into a locations
# page, which is worse than the miss it was fixing.
#
# A qualifier settles it too: `/our-shop` is a place even in the singular,
# because a shop nobody owns is a catalogue and a shop somebody owns is a room.
_PLURAL_OR_QUALIFIED_ONLY = {
    "location": ("shop", "store", "branch", "clinic", "venue", "office",
                 "studio", "salon", "outlet", "dealer", "stockist", "surgery",
                 "practice", "restaurant", "cafe", "hotel", "gym", "depot"),
}

def _url_type_pattern(page_type, slugs):
    """One regex per page type, from the slug list and the three shape rules."""
    boundary = r"(?:$|/|\.|-(?:{})\b)".format(_DERIVED_SUFFIX)
    plain = r"(?:{})?(?:{})(?:e?s)?".format(
        _QUALIFIER_PREFIX, "|".join(re.escape(slug) for slug in slugs))
    alternatives = [plain]
    strict = _PLURAL_OR_QUALIFIED_ONLY.get(page_type)
    if strict:
        words = "|".join(re.escape(slug) for slug in strict)
        alternatives.append(r"(?:{})(?:{})(?:e?s)?".format(_QUALIFIER_PREFIX, words))
        alternatives.append(r"(?:{})e?s".format(words))
        # A singular counts when a derived noun follows it: `/store-finder` and
        # `/branch-directory` are lists of places, and the word alone is not.
        # The lookahead leaves the suffix for the shared boundary to consume.
        alternatives.append(r"(?:{})(?=-(?:{})\b)".format(words, _DERIVED_SUFFIX))
    return r"(?:^|/)(?:{}){}".format("|".join(alternatives), boundary)


_URL_TYPE_PATTERNS = tuple(
    # Boundary includes `.` so `/about.html` counts, and `-` so `/about-us`
    # counts, while `/roundabout` and `/2004/Jun/29/job/`-style slugs inside
    # dated permalinks do not.
    #
    # The trailing `-` is what makes `/about-us` work and what made
    # `/products/press-on-sponge-replenishment` a press page: "press" is a
    # strong slug, so it won outright, and a nail-varnish product was reported
    # as an article page carrying no publication date. A hyphen may follow the
    # slug only where the slug is not also an ordinary English word that starts
    # other words - and rather than judge that word by word, the segment now has
    # to be the slug, or the slug plus a suffix, and never the slug plus a
    # different word that happens to begin with it. `[a-z]*` after the hyphen
    # would allow either; requiring the rest of the segment to be short does
    # not. The simplest rule that separates the real cases is that a hyphen may
    # follow only when what precedes it is the whole first word of the segment
    # AND the segment's remaining words do not turn it into a different noun -
    # which is what `-us`, `-kit` and `-centre` are and `-on-sponge` is not.
    (page_type, _url_type_pattern(page_type, slugs))
    for page_type, slugs in _URL_TYPE_SLUGS
)

# A dated permalink is an article whatever its slug says, and a bare year is
# that year's archive index.
_DATED_PERMALINK_RE = re.compile(r"/(?:19|20)\d{2}(?:/[^/]+){1,}/?$")
_YEAR_ARCHIVE_RE = re.compile(r"^/(?:19|20)\d{2}(?:/[a-z]{3,9})?/?$", re.I)

# The last segment of a listing of other pages. Whole words only: `/previous`
# is an index, `/previously-unseen-photographs` is an article.
_ARCHIVE_INDEX_RE = re.compile(
    r"/(?:archive|archives|previous|past|back-issues|backissues|all|index|"
    r"list|listing|overview|browse|latest|older|earlier|more)/?$", re.I)

# Buying affordances that only appear on a real product detail page. Generic
# commerce words ("quantity", "sku") are deliberately excluded: they show up in
# ordinary B2B copy and were turning service pages into false "product" hits.
# URL slugs specific enough to override any structured-data declaration.
_STRONG_URL_TYPES = frozenset({
    "legal", "careers", "press", "comparison", "faq", "pricing", "contact",
    "about", "location",
})

_PRODUCT_TEXT_SIGNALS = (
    "add to cart", "add to bag", "add to basket", "add to wishlist",
    "buy now", "in stock", "out of stock", "free shipping", "select size",
    "choose a size", "product code", "item number", "notify me when",
)
_PRICE_RE = re.compile(
    r"(?:[$€£¥₹]\s?\d[\d,]*(?:\.\d{2})?"
    r"|\b\d[\d,]*(?:\.\d{2})?\s?(?:USD|EUR|GBP|INR|AUD|CAD|SGD|AED)\b)"
)
_QUESTION_HEADING_RE = re.compile(
    r"^\s*(what|why|how|when|where|who|which|can|do|does|is|are|will|should)\b.*\?\s*$",
    re.I,
)


# A page that answers 200 while telling the visitor there is nothing here.
#
# These are matched against the <title> and the first heading only, never the
# body: a help page explaining what a 404 is would otherwise be mistaken for
# one, and a shop's search results legitimately say "no results found" in the
# body of a page that is working correctly.
_SOFT_404_MARKERS = (
    "page not found", "not found", "404", "no longer available", "nothing to see here",
    "page doesn't exist", "page does not exist", "page unavailable", "oops",
    "sorry, we can't find", "we couldn't find that page",
)



# A page generated from what somebody typed. `?search=Artemis`, `/search?q=...`,
# `?s=mars` - the results exist because of the query, not because the site
# published them, and every search-engine guideline says to keep them out of an
# index.
#
# `noindex` on one of these is the site doing the right thing. Reported as a
# defect on a space agency's site, the fix - "remove `noindex` where the page
# should be public" - would have put five search-result URLs into the index,
# and it was ranked second in what to fix first.
_SEARCH_RESULT_RE = re.compile(
    r"(?:^|[/?&])(?:search|suche|recherche|busqueda|zoeken|ricerca)(?:[/?&=]|$)"
    r"|[?&](?:q|s|query|keyword|keywords|term|searchterm|search_query)=",
    re.I)



# How much of a page's visible text the content extractor has to find before
# its answer can be trusted. Below this, the shortfall is more likely ours than
# the site's.
#
# `text_len` is every visible character on the page; `body_text_len` is what
# the content-region extractor kept. On an Arabic news site's liveblog template
# the first was 1,660 and the second 89 - the extractor found the title and
# nothing else, because the article sits in a nested `<div id="wysiwyg">` that
# none of the content selectors reach and the whole-document fallback lost to
# the chrome. Two findings followed: "delivered as an empty JavaScript shell"
# and "too little text to quote", on a page that is server-rendered and
# complete. The fix offered was several days of development work.
#
# The site is not obliged to use markup we recognise. When these two numbers
# disagree by this much, the honest reading is that we failed to find the text,
# not that the text is absent.
EXTRACTION_TRUSTED_SHARE = 0.25


def extraction_looks_incomplete(page):
    """Did the content extractor find far less than the page visibly holds?

    False when there is nothing to compare - a page with no visible text at all
    really is empty, and that is a finding rather than a failure.
    """
    whole = page.get("text_len") or 0
    kept = page.get("body_text_len") or 0
    if whole < 200:
        return False
    return kept < EXTRACTION_TRUSTED_SHARE * whole

def is_search_result_page(url):
    """True for a page whose content is a response to a typed query."""
    return bool(_SEARCH_RESULT_RE.search(url or ""))

def looks_like_soft_404(page):
    """True for a 200 response whose own title says the page is missing.

    A retailer's `/collections/all` answers 200 with `noindex, nofollow`, the
    title "Page Not Found" and the body "Uh-Oh, Nothing To See Here!". The
    audit reported the `noindex` as a high-severity defect and told the
    developer to remove it, which would have put a broken page into the index -
    a fix that makes the site worse in exactly the way the finding claims to
    prevent. `noindex` on a soft-404 is the correct thing for a site to do.
    """
    heading = ""
    headings = page.get("headings") or {}
    if isinstance(headings, dict):
        h1s = headings.get("h1") or []
        heading = h1s[0] if h1s else ""
    heading = heading or (page.get("h1") or "")
    haystack = "{} {}".format(page.get("title") or "", heading).lower()
    return any(marker in haystack for marker in _SOFT_404_MARKERS)


# schema.org types that mean "somewhere a customer physically goes".
VISITABLE_JSONLD_TYPES = frozenset({
    "localbusiness", "store", "restaurant", "hotel", "cafe", "bakery", "bar",
    "clothingstore", "grocerystore", "healthandbeautybusiness", "medicalclinic",
    "hospital", "dentist", "autorepair", "professionalservice",
    "foodestablishment", "fastfoodrestaurant", "library", "museum",
})


def declared_locations(snapshot):
    """Distinct postal addresses the site declares for visitable places.

    A chain declares one `LocalBusiness` node per branch, each with its own
    address and telephone, and that is correct. Read as facts about a single
    organisation they look like the site contradicting itself: a restaurant
    group with hundreds of branches was told, at high severity and second in
    what to fix first, to force every location onto "one canonical version" of
    its address and phone number - which would delete the real addresses
    customers use to find their nearest branch.
    """
    forms = []
    for page in snapshot.get("pages") or []:
        for node in page.get("jsonld") or []:
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not {str(t).split("/")[-1].lower() for t in types} & VISITABLE_JSONLD_TYPES:
                continue
            address = node.get("address")
            if isinstance(address, list):
                address = address[0] if address else None
            if isinstance(address, dict):
                forms.append(json.dumps(address, sort_keys=True))
    return set(forms)


# How many pages have to carry their own address before the site is a chain.
# Two is a head office and a shop; three distinct addresses is a pattern.
BRANCH_PAGE_MINIMUM = 3



def fold_declared_duplicates(pages):
    """One entry per page the site itself considers distinct.

    A site that serves the same page at `/thing` and `/thing/` and declares one
    of them canonical has told the crawler they are one page. Counting both
    inflated a duplicate-title finding on a broadcaster - two of its eleven
    "duplicate" pairs were a URL and its own declared canonical target - and
    inflated the denominator underneath it at the same time.

    Only a canonical the crawl actually reached folds. A canonical pointing
    somewhere the crawl never saw is a separate matter, reported by
    `crawl-access-audit`, and folding on it would hide pages rather than count
    them.
    """
    reached = {p.get("url") for p in pages if p.get("url")}
    kept, folded = [], set()
    for page in pages:
        canonical = (page.get("canonical") or "").strip()
        url = page.get("url")
        if (canonical and canonical != url and canonical in reached
                and canonical not in folded):
            folded.add(url)
            continue
        kept.append(page)
    return kept

def pages_with_their_own_address(snapshot):
    """Pages carrying a postal address no other page carries.

    The evidence a chain leaves in its HTML whatever its markup says. A pizza
    chain's forty-three branch pages each print their own street and postcode
    in plain text and declare no `LocalBusiness` node at all - so both of the
    other two signals were blind to it, and blind for the same reason the
    finding existed: the markup that would have identified the chain is the
    markup the chain is missing.

    Reading the addresses off the pages breaks that circle.
    """
    seen, pages = {}, []
    for page in snapshot.get("pages") or []:
        facts = page.get("contact_facts") or {}
        street = (facts.get("street_hint") or "").strip().lower()
        postcode = (facts.get("postcode_hint") or "").strip().lower()
        if not street or not postcode:
            continue
        # Keyed on the postal code alone. The street hint is a fuzzy substring
        # match, so the same head-office address came out three slightly
        # different ways across three pages of one single-site fixture and was
        # counted as three branches. A postal code identifies a place; two
        # pages carrying the same one are the same place however the street is
        # written. The street still has to be there, as evidence that this is
        # an address at all rather than a stray number.
        key = re.sub(r"[^a-z0-9]", "", postcode)
        if key in seen:
            continue
        seen[key] = page["url"]
        pages.append(page)
    return pages


def is_multi_location(snapshot):
    """True when the site describes more than one place you can visit."""
    if len(declared_locations(snapshot)) > 1:
        return True
    if len([p for p in (snapshot.get("pages") or [])
            if p.get("page_type") == "location"]) > 1:
        return True
    return len(pages_with_their_own_address(snapshot)) >= BRANCH_PAGE_MINIMUM


_COMMERCE_JSONLD_TYPES = frozenset({
    "product", "productgroup", "productmodel", "offer", "aggregateoffer",
    "onlinestore", "store", "individualproduct",
})


def sells_something(snapshot, pages=None):
    """Does this site sell anything, or take a booking?

    The price, pricing-page and "what does it cost" checks were written for a
    business with a sales funnel and applied to everyone. A medical charity was
    told it "never states its pricing" and handed a fix reading "contact sales
    for a quote"; a free database project got a paste-ready snippet inventing
    "$X per month on the Starter plan and $Y on Growth"; a museum and two
    open-source projects got the same. None of them sells anything, and a
    maintainer reading that discards the whole report, correctly.

    Four signals, any one of which is the site itself saying it sells: a
    product detail page, a pricing page, commerce markup, or a price in the
    text of a page. Donation tiers are prices; a charity that publishes them is
    not thereby a shop, so the pricing *check* stays quiet either way and only
    the framing changes.
    """
    pages = pages if pages is not None else (snapshot.get("pages") or [])
    for page in pages:
        if page.get("page_type") in ("product", "pricing"):
            return True
        if {t.lower() for t in page.get("jsonld_types") or []} & _COMMERCE_JSONLD_TYPES:
            return True
        if _PRICE_RE.search(page.get("body_text") or ""):
            return True
    return False


def is_question_heading(heading):
    """True for a heading a visitor would recognise as a question.

    Shared so the classifier and the FAQPage snippet cannot disagree about
    what a question is - they did, and the snippet was the one marking up
    policy statements and legal text as question-and-answer pairs.
    """
    return bool(_QUESTION_HEADING_RE.match(heading or ""))


def detect_page_type(url, html_meta):
    """Classify a page from URL shape, then confirm or override with content.

    `html_meta` is a dict with `headings`, `text`, `jsonld_types`, `title`.
    URL patterns are checked first because they are the strongest and most
    portable signal; content signals then correct the common mistakes (a
    `/shop/thing` listing page vs a real product page, a page that is a FAQ
    without saying so in the URL).
    """
    parsed = urlparse(url)
    path = (parsed.path or "/").rstrip("/").lower() or "/"
    text = (html_meta.get("text") or "")
    lower_text = text[:6000].lower()
    jsonld_types = {t.lower() for t in html_meta.get("jsonld_types") or []}
    headings = html_meta.get("headings") or {}
    h2s = headings.get("h2") or []

    # `/` is the homepage, and so is `/de/` on a site with one edition per
    # language. Only the bare root qualified, so on a path-prefixed site the
    # audit's idea of "the homepage" snapped to whatever sat at the root -
    # which on a bilingual broadcaster was an English redirect target - even
    # when the audit had been pointed at `https://.../de/` explicitly. Every
    # home-gated check then looked at the wrong page, or at no page.
    if path == "/" and not parsed.query:
        return "home"
    # On the page's own evidence only: the leading segment is a language code
    # and the page declares that language. A two-letter segment alone is not
    # enough - plenty of English sites keep an IT department at `/it`.
    locale = _locale_candidate(url)
    if (locale and not parsed.query and path.count("/") == 1
            and primary_subtag(html_meta.get("lang")) == locale.split("-")[0].split("_")[0]):
        return "home"

    # Date-shaped paths are decided before anything else. A blog permalink such
    # as /2004/Jun/29/job/ ends in a slug that would otherwise be read as a
    # careers page, and its date is far stronger evidence than its slug.
    if _YEAR_ARCHIVE_RE.match(path):
        return "category"
    if _DATED_PERMALINK_RE.search(path):
        return "article"

    # An index of articles is not an article. `/news/previous/` is a news
    # archive listing, and demanding `Article` markup on it produced a finding
    # whose paste-ready snippet declared `"headline": "News archive"` - which
    # would tell a machine the archive page is a piece of journalism published
    # on a date. The last segment says so; the check is the same one for a
    # blog index, a press listing and a category page.
    if _ARCHIVE_INDEX_RE.search(path):
        return "category"

    # Unambiguous URL slugs win outright. A pricing page that also carries
    # Product schema is still a pricing page, and classifying it as a product
    # would make every downstream expectation wrong.
    for page_type, pattern in _URL_TYPE_PATTERNS:
        if page_type in _STRONG_URL_TYPES and re.search(pattern, path):
            return page_type

    # Otherwise JSON-LD is an explicit declaration by the site owner, and
    # beats guessing from the URL shape.
    #
    # `CollectionPage` and `ItemList` are checked first because a listing page
    # legitimately embeds a Product node per item on it. Reading those as
    # "this is a product page" is how a shop's category pages came to be
    # counted as product pages that were missing the markup they were in fact
    # carrying, one node per item.
    if jsonld_types & _LISTING_JSONLD_TYPES:
        return "category"
    if jsonld_types & {"product", "productgroup"}:
        return "product"
    if jsonld_types & {"faqpage", "qapage"}:
        return "faq"
    if jsonld_types & {"blogposting", "newsarticle", "article", "techarticle"}:
        return "article"
    if jsonld_types & {"localbusiness", "store", "restaurant", "hotel"} and path != "/":
        return "location"

    for page_type, pattern in _URL_TYPE_PATTERNS:
        if re.search(pattern, path):
            # The product/category distinction is decided by the page, not the
            # slug, and it runs both ways: `/products` with no buying
            # affordances is a listing, and `/shop/<item>` with a price and an
            # add-to-cart control is a product detail page.
            if page_type == "product":
                # Path depth carries the listing-versus-item distinction that
                # retail phrasing does not. `/products` is an index; a deeper
                # path under it naming one thing is that thing. Requiring "add
                # to cart" meant every B2B item page, and every storefront that
                # renders its buy button in JavaScript, was demoted to a
                # listing - and a listing is never asked for Product markup, so
                # the sites most in need of the advice were the ones exempted
                # from it.
                deep = len([s for s in path.strip("/").split("/") if s]) >= 2
                if not (deep and (_PRICE_RE.search(lower_text)
                                  or _product_signal_count(lower_text))):
                    if not _looks_like_product_detail(lower_text, html_meta):
                        return "category"
            if page_type == "category" and _looks_like_product_detail(lower_text, html_meta):
                return "product"
            # `/blog` is the index of a section; `/blog/a-post` is the article.
            # Treating the index as an article would demand `datePublished` and
            # an author on a page that is a list of links.
            if page_type == "article" and _is_section_root(path):
                return "category"
            return page_type

    # Content-only fallbacks for sites with opaque URLs (e.g. `/page/17`).
    #
    # Four questions is not enough on its own. A contributor style guide with
    # twenty-four headings, four of them phrased as questions and the rest
    # policy statements ("Duplication is evil", "Code style"), was classified
    # as an FAQ page and then handed generated FAQPage markup wrapping those
    # statements in `Question` nodes. An FAQ page is mostly questions, so the
    # share has to carry as much weight as the count.
    question_headings = sum(1 for h in h2s if is_question_heading(h))
    if question_headings >= 4 and question_headings * 2 >= len(h2s):
        return "faq"
    if _looks_like_product_detail(lower_text, html_meta):
        return "product"
    return "other"


_SECTION_ROOTS = frozenset({
    "blog", "news", "articles", "article", "posts", "post", "insights",
    "stories", "resources", "guides", "press", "updates", "journal",
    "episodes", "podcast", "podcasts", "transcripts",
})


def _is_section_root(path):
    """True for `/blog` or `/blogs/news`, false for `/blog/a-post`.

    A retailer publishes its index at `/blogs/news`, two segments deep, so the
    one-segment rule missed it and the index was classified as an article. The
    report then told the shop its news index was missing Article markup and
    generated a snippet whose `headline` was "News - <brand>" - the title of a
    page that is a list of links to other articles. The last segment is the
    one that names the section; the segments in front of it are the shelf it
    sits on.
    """
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts or len(parts) > 2:
        return False
    if parts[-1] not in _SECTION_ROOTS:
        return False
    return len(parts) == 1 or parts[0].rstrip("s") in {p.rstrip("s") for p in _SECTION_ROOTS}


def _product_signal_count(lower_text):
    return sum(1 for s in _PRODUCT_TEXT_SIGNALS if s in lower_text)


# What separates one item from a list of items.
#
# Measured on a real retailer's 40 crawlable shop pages, using its own URL
# scheme as the answer key:
#
#   11 product detail pages   1 to 4 distinct prices (median 3), 4-5 signals
#   29 category listings      2 to 18 distinct prices (median 9), 1-2 signals
#
# The old rule asked for a price and two buying signals, and a listing page
# has both - it repeats "free shipping" and "in stock" once per item on it.
# So twenty category pages were classified as product pages, and the report
# told the retailer that "20 of 31 product pages have no Product markup" when
# every one of its real product pages already had it and not one of the twenty
# was a product page.
#
# What the two groups do not share is how many different prices they show, and
# that alone separates them completely on the measured data: 11 of 11 detail
# pages kept, 29 of 29 listings excluded. Both thresholds were swept; raising
# the signal count from 2 to 3 changed nothing there and would have demoted an
# ordinary small-shop page whose only affordances are "Add to cart" and "In
# stock", so it stays at 2 and the price ceiling does the work.
PRODUCT_SIGNAL_MINIMUM = 2
DISTINCT_PRICE_CEILING = 5

# A site saying "this page is a list" outranks any guess made from its text.
# `Blog` is the index; `BlogPosting` is the post on it.
_LISTING_JSONLD_TYPES = frozenset({"collectionpage", "itemlist", "searchresultspage",
                                   "blog"})


def _looks_like_product_detail(lower_text, html_meta):
    """A single purchasable item, not a page listing several of them.

    Used where the URL gives no help. Where the URL does - a deeper path under a
    products section - that evidence is stronger and is applied first.
    """
    prices = {m.group(0).strip() for m in _PRICE_RE.finditer(lower_text)}
    if not prices or len(prices) >= DISTINCT_PRICE_CEILING:
        return False
    return _product_signal_count(lower_text) >= PRODUCT_SIGNAL_MINIMUM


PRICE_NUMBER_RE = re.compile(r"\d[\d.,]*\d|\d")


def price_value(text):
    """The numeric value of a price string, whichever way the world writes it.

    Half the world writes 28,00 and half writes 28.00. The old reader stripped
    every comma and called `float`, so a French bakery's "28,00 EUR" became
    2800 - and the audit then reported the site's own structured data as
    contradicting its own page, four times, second in what to fix first, on
    prices that were correct. The advice was to spend up to a day reconciling
    them, and a developer following it would have changed a right number into a
    wrong one.

    The separator is read from the number rather than assumed: when both appear,
    the last one is the decimal point; when one appears, three digits after it
    make it a thousands separator and one or two make it a decimal point.
    """
    match = PRICE_NUMBER_RE.search(str(text or ""))
    if not match:
        return None
    raw = match.group(0)
    has_dot, has_comma = "." in raw, "," in raw
    if has_dot and has_comma:
        decimal = "." if raw.rfind(".") > raw.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        raw = raw.replace(thousands, "").replace(decimal, ".")
    elif has_dot or has_comma:
        separator = "." if has_dot else ","
        parts = raw.split(separator)
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3):
            raw = raw.replace(separator, "")          # 2,800 and 2.800 are 2800
        else:
            raw = raw.replace(separator, ".")         # 28,00 and 28.00 are 28
    try:
        return float(raw)
    except ValueError:
        return None


def has_price(text):
    return bool(_PRICE_RE.search(text or ""))


def find_prices(text, limit=5):
    return [m.group(0).strip() for m in _PRICE_RE.finditer(text or "")][:limit]


# --------------------------------------------------------------------------
# Finding construction
# --------------------------------------------------------------------------

def make_finding(
    id_hint,
    title,
    severity,
    confidence,
    evidence,
    mechanism,
    root_cause,
    summary,
    how_to_fix,
    effort,
    rationale,
    owner,
    affected_pages=None,
    snippet=None,
):
    """Build one finding in the shape every skill must emit.

    Deliberately strict: a typo in a severity or root-cause tag becomes a
    loud failure in the unit tests rather than a finding that silently never
    dedups or sorts into the wrong bucket.
    """
    if severity not in SEVERITY_RANK:
        raise ValueError("unknown severity: {!r}".format(severity))
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError("unknown confidence: {!r}".format(confidence))
    if mechanism not in MECHANISMS:
        raise ValueError("unknown mechanism: {!r}".format(mechanism))
    if root_cause not in ROOT_CAUSES:
        raise ValueError("unknown root_cause: {!r}".format(root_cause))
    if effort not in EFFORT_DIVISOR:
        raise ValueError("unknown effort: {!r}".format(effort))
    if not how_to_fix:
        raise ValueError("every finding needs at least one how_to_fix step")

    pages = sorted(set(affected_pages or []))
    finding = {
        "id_hint": id_hint,
        "title": title,
        "severity": severity,
        "confidence": confidence,
        "evidence": evidence,
        "mechanism": mechanism,
        "root_cause": root_cause,
        "affected_pages": pages[:5],
        "affected_page_count": len(pages),
        "suggested_action": {
            "summary": summary,
            "effort": effort,
            "owner": owner,
            "how_to_fix": list(how_to_fix),
            "rationale": rationale,
        },
    }
    if snippet:
        finding["suggested_action"]["snippet"] = snippet
    return finding


def sort_findings(findings):
    """Deterministic order: severity, then mechanism letter, then id_hint."""
    return sorted(
        findings,
        key=lambda f: (SEVERITY_RANK[f["severity"]], f["mechanism"], f["id_hint"]),
    )


class SkillResult:
    """Accumulator for a sub-skill run.

    Tracks the three things the orchestrator needs from every skill: what was
    found, what was checked, and what was skipped and why. The `not_applicable`
    list is the false-positive guard made visible - a judge can read it and see
    the skill stayed quiet on purpose.
    """

    def __init__(self, skill):
        self.skill = skill
        self.findings = []
        self.checks_run = []
        self.not_applicable = []
        self.extra_requests_made = 0
        self.signals = {}
        self._current_check = None
        # {check name: the function that registered it}. Keyed by caller, not
        # a flat list, because a flat list was a run-long accumulator: a check
        # registered in one function and answered by neither a finding nor a
        # decline sat in it until some unrelated `add()` later in the same
        # skill swept it up as "fired". On one site seven of seventy-one checks
        # that ran appeared nowhere in the report at all - not as findings, not
        # as declines, not as passes - and among them was whether robots.txt
        # blocks AI crawlers, which is the question this audit exists to
        # answer. A finding may only claim the checks its own function
        # registered.
        self._group = {}
        self.fired_checks = set()

    @staticmethod
    def _callers(depth=12):
        """Function names on the stack above this call.

        A `_check_*` function often registers its checks and then delegates
        the finding to a helper, or the other way round. Matching on the whole
        stack rather than one frame keeps those together while still refusing
        to reach across into an unrelated check.
        """
        names = set()
        frame = sys._getframe(2)
        while frame is not None and len(names) < depth:
            names.add(frame.f_code.co_name)
            frame = frame.f_back
        return names

    def check(self, name):
        # Remembered so `add()` can stamp the finding with the check that
        # produced it. Without that mapping the report cannot tell a check that
        # ran and passed from one that ran and fired, so twelve checks - robots
        # reachable, homepage reachable, sitemap parses and the rest - appeared
        # in neither the findings nor the not-applicable list. The README
        # promises every quiet check says why; those said nothing at all, and
        # they are exactly the reassuring ones a reader wants to see.
        self._current_check = name
        # Several checks are often registered together at the top of one
        # function, and any finding it produces belongs to that group. Stamping
        # only the last one registered left the others looking untouched, so a
        # report listed `sitemap-present` under "ran and found nothing wrong"
        # on a site whose findings section said "No XML sitemap is available".
        #
        # A check that might have fired is never claimed as clean.
        self._group.setdefault(name, sys._getframe(1).f_code.co_name)
        if name not in self.checks_run:
            self.checks_run.append(name)

    def skip(self, name, reason):
        self.check(name)
        # An explicit decline is a definite answer about this one check, so it
        # leaves the group rather than being tarred by a sibling that fired.
        self._group.pop(name, None)
        self.not_applicable.append({"check": name, "skill": self.skill, "reason": reason})

    def add(self, **kwargs):
        finding = make_finding(**kwargs)
        finding["check"] = getattr(self, "_current_check", None)
        # Only the checks this finding's own function registered. Anything
        # registered elsewhere and left unanswered is a check that ran and
        # found nothing, which is what the report says about it.
        callers = self._callers()
        mine = [name for name, owner in self._group.items() if owner in callers]
        self.fired_checks.update(mine)
        for name in mine:
            self._group.pop(name, None)
        self.findings.append(finding)

    def signal(self, key, value):
        self.signals[key] = value

    def to_dict(self):
        return {
            "skill": self.skill,
            "findings": sort_findings(self.findings),
            "checks_run": sorted(self.checks_run),
            "fired_checks": sorted(self.fired_checks),
            "not_applicable": sorted(
                self.not_applicable, key=lambda n: (n["check"], n["reason"])
            ),
            "extra_requests_made": self.extra_requests_made,
            "signals": dict(sorted(self.signals.items())),
        }

    def write(self, path):
        write_json(path, self.to_dict())


# --------------------------------------------------------------------------
# JSON I/O
# --------------------------------------------------------------------------

def write_json(path, obj):
    """Write UTF-8 JSON with sorted keys, so two runs diff cleanly."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_snapshot(path):
    snapshot = read_json(path)
    if "pages" not in snapshot:
        raise ValueError("{} is not a crawl snapshot (no `pages` key)".format(path))
    return snapshot


# Raw library text is not a diagnosis. A redirect loop reached the report as
# "returned no response (Exceeded 30 redirects.)", and a homepage redirecting
# to a domain that does not exist arrived as a full urllib3 connection-pool
# message, complete with the class name of the underlying exception - in a
# document written for a marketing manager.
#
# Each entry is (substring to look for, what actually happened).
# Order matters, most specific first. urllib3 wraps a DNS failure in a message
# that also contains "Max retries exceeded", so a bare "exceeded" test called a
# homepage redirecting to a non-existent domain a redirect loop.
FETCH_ERROR_MEANINGS = (
    ("nameresolutionerror", "points at a hostname that does not exist"),
    ("failed to resolve", "points at a hostname that does not exist"),
    ("getaddrinfo", "points at a hostname that does not exist"),
    ("connection refused", "refused the connection - nothing is listening on that address"),
    ("actively refused", "refused the connection - nothing is listening on that address"),
    ("timed out", "did not answer in time"),
    ("timeout", "did not answer in time"),
    ("certificate", "has an HTTPS certificate a client will not accept"),
    ("sslerror", "has an HTTPS certificate a client will not accept"),
    ("connection reset", "closed the connection part-way through the response"),
    ("connection aborted", "closed the connection part-way through the response"),
    ("still arriving", "sent its response so slowly the audit stopped waiting"),
    ("redirects", "redirects in a loop and never arrives at a page"),
)


def explain_fetch_error(error):
    """Say what went wrong in words the site's owner can act on.

    Falls back to the raw text, so an error nobody anticipated is still
    reported rather than swallowed.
    """
    text = str(error or "").strip()
    if not text:
        return "did not respond"
    low = text.lower()
    for needle, meaning in FETCH_ERROR_MEANINGS:
        if needle in low:
            return meaning
    return "could not be fetched ({})".format(truncate(text, 120))



# --------------------------------------------------------------------------
# A slow origin is not an outage
#
# One site answered every page in ten to forty seconds. Enough requests timed
# out that the report described it as unreachable, with outage wording and a
# fix that read "read the server log for the failing request" - on a site that
# was up, serving correct HTML, and merely slow. The owner would have gone
# looking for a fault that was not there and missed the one that was.
#
# Slowness and unreachability need telling apart wherever either is reported,
# so the measurement lives here rather than in the check that noticed first.
# --------------------------------------------------------------------------

# Measured against real homepages: the slowest ordinary site in three samples
# had a median of 1.9 s. Three seconds is outside anything normal, and past
# ten an answer crawler's own timeout is the binding constraint.
SLOW_ORIGIN_MEDIAN_MS = 3000
VERY_SLOW_ORIGIN_MEDIAN_MS = 10000


def response_timing(pages):
    """Median and worst response time across the pages that answered.

    `None` when nothing was timed, which is what a `--no-network` run and a
    fully blocked site both produce; callers must not read a missing
    measurement as a fast one.
    """
    times = sorted(page["elapsed_ms"] for page in pages or []
                   if page.get("status") == 200 and (page.get("elapsed_ms") or 0) > 0)
    if not times:
        return None
    return {
        "median_ms": times[len(times) // 2],
        "slowest_ms": times[-1],
        "measured_on": len(times),
    }


def slow_origin_note(pages):
    """A sentence to append wherever a failed fetch is being explained.

    Empty string on a site that answers at a normal speed, so the caller can
    concatenate it unconditionally.
    """
    timing = response_timing(pages)
    if timing is None or timing["median_ms"] < SLOW_ORIGIN_MEDIAN_MS:
        return ""
    return (" This origin answers slowly rather than not at all: the median response across "
            "the {} page(s) it did serve was {:.1f} s, and the slowest was {:.1f} s. Treat a "
            "timeout here as the same slowness, not as an outage.".format(
                timing["measured_on"], timing["median_ms"] / 1000.0,
                timing["slowest_ms"] / 1000.0))


# --------------------------------------------------------------------------
# Whose profile is this?
#
# A URL of the right shape on the right platform is not the brand's profile.
# It is somebody's profile. Counting shape alone produced a quantified, false
# headline claim on an open-source framework's site:
#
#     "Distinct off-site profiles linked: Bluesky, GitHub, Wikipedia, X."
#
# The GitHub entry was a contributor's personal account, thanked in the
# footer. The Wikipedia entry was `/wiki/Pure_function`, cited inside a blog
# post about signals. The project's own org page is linked dozens of times,
# but only ever as a repository path, so it was never counted at all - and the
# same report said elsewhere that Wikidata returns no entity for the project,
# contradicting the Wikipedia claim two sections away.
#
# The rule is the same one `sameAs` already used and nothing else did: a
# profile corroborates a brand only if it names the brand. The check lives
# here so both skills read the same implementation.
# --------------------------------------------------------------------------

# Words brands add around their own name in a handle.
_HANDLE_AFFIXES = ("get", "the", "team", "official", "hq", "app", "inc", "co",
                   "org", "global", "group", "uk", "us", "usa", "eu", "js",
                   "hub", "labs", "io")


# A site that names itself after its domain gives a brand name carrying the
# TLD - "Example.org", "Example.com" - and no profile handle includes it.
_BRAND_TLD_RE = re.compile(r"\.(?:com|org|net|io|co|ai|dev|app|uk|de|fr)$", re.I)


def brand_key(name):
    """A brand name reduced to comparable letters and digits."""
    return re.sub(r"[^a-z0-9]", "", _BRAND_TLD_RE.sub("", (name or "").strip()).lower())


def profile_names_brand(url, brand_name):
    """Does this profile URL's handle name the brand?

    Without a brand name there is nothing to compare against, and the honest
    answer is no: an unattributed profile is not evidence about this brand.
    """
    key = brand_key(brand_name)
    if not key:
        return False
    path = re.sub(r"[?#].*$", "", url or "").rstrip("/")
    handle = re.sub(r"[^a-z0-9]", "", path.rsplit("/", 1)[-1].lower())
    if not handle:
        return False
    if handle == key or key in handle or handle in key:
        return True
    for affix in _HANDLE_AFFIXES:
        if handle.startswith(affix) and handle[len(affix):] == key:
            return True
        if handle.endswith(affix) and handle[: -len(affix)] == key:
            return True
    return False


# Platforms whose profile URL is an opaque identifier. A Wikidata item is
# `Q42` and a YouTube channel is `UCxxxxxxxx`; neither can be checked against
# a brand name, so requiring one dropped a fixture's own correct Wikidata link.
# Wikipedia is deliberately not here: `/wiki/Pure_function` is a name, which is
# exactly why an article about a computer-science term was being counted as an
# open-source project's own profile.
_OPAQUE_PROFILE_RE = (
    re.compile(r"wikidata\.org/(?:wiki|entity)/Q\d+", re.I),
    re.compile(r"youtube\.com/channel/UC[\w-]+", re.I),
)


def profile_is_opaque(url):
    """True when the URL identifies a profile by number rather than by name."""
    return any(rule.search(url or "") for rule in _OPAQUE_PROFILE_RE)


# Platforms whose URL states its own subject. A Wikipedia path is the title of
# the article, so `/wiki/Pure_function` is decisively not about a JavaScript
# framework - that is a fact about the URL, not a guess about a handle.
#
# Deliberately only these. The first version of this filter applied the name
# test to every platform and immediately made a worse mistake than the one it
# fixed: on a language's own site it dropped the foundation's LinkedIn page,
# its X account and its conference YouTube channel - all real, all the
# project's own - because none of the handles spells the site's name, and
# turned "links to only 4 off-site profiles" into "links to no off-site
# profile at all" at high severity. A handle is a name somebody chose; an
# article title is a statement of subject. Only the second can be checked.
_SUBJECT_IN_URL_PLATFORMS = frozenset({"Wikipedia"})


def profiles_naming_brand(profiles, brand_name, declared=None, name_forms=()):
    """{platform: url} for the profiles that are credibly this brand's.

    Four ways a profile earns its place, in descending strength:

      the site claims it     listed in the site's own `sameAs`, which is an
                             assertion of identity and needs nothing else
      the URL cannot lie     the platform identifies profiles by number, or by
                             a handle somebody chose, so no subject test is
                             possible either way and the link is taken at face
                             value
      it names the brand     for the platforms whose URL states its subject
      it cannot be named     an opaque identifier

    Only a Wikipedia-style article URL is ever rejected, and only when its
    subject is something other than this brand. That was the case both
    verification agents named: `/wiki/Pure_function` and `/wiki/Time_complexity`,
    cited inside a blog post about signals, counted as an open-source
    project's own profiles while its real organisation page - linked dozens of
    times, always as a repository path - was never counted at all.
    """
    declared = declared or {}
    candidates = [brand_name] + [n for n in name_forms if n]
    out = {}
    for platform, url in (profiles or {}).items():
        if declared.get(platform) or profile_is_opaque(url):
            out[platform] = url
            continue
        if platform not in _SUBJECT_IN_URL_PLATFORMS:
            out[platform] = url
            continue
        if any(profile_names_brand(url, name) for name in candidates):
            out[platform] = url
    return out

def example_urls(urls, limit=5):
    """The URLs a finding quotes, matching the ones it lists as affected.

    `affected_pages` is `sorted(set(...))[:5]`; the evidence line used a seeded
    random sample of the same population. Both were five URLs from the same
    problem and they were different five, so one finding read as two - the
    evidence naming two product pages while the affected list underneath named
    an about page and an FAQ.
    """
    return sorted(set(u for u in urls if u))[:limit]


# --------------------------------------------------------------------------
# Bot-manager challenge pages
#
# A bot manager that answers 403 is easy: the status says the crawler was
# refused, and `crawl-access-audit` reports it as a bot block. The dangerous
# case is the one that answers 2xx.
#
# Measured on real commercial homepages: one returns HTTP 202 with 3,962 bytes
# of HTML and *zero* characters of readable text; another returns 200 with
# 2,857 bytes and thirty-two characters. Both are verification pages. To every
# content check they look like a site that shipped an empty page - so the audit
# would report a JavaScript shell, no structured data, no quotable fact and
# thin content, four confident findings about a homepage that is fine.
#
# That is the same error as calling a refused link a dead link, in a different
# costume, and it fires on exactly the bot-managed commercial sites this
# marketplace is aimed at.
#
# Detection is by vendor marker rather than by shape, because "almost no text"
# is also what a genuine JavaScript shell looks like, and that is a real
# finding we must keep reporting. These strings appear in the challenge
# scaffolding itself and not in ordinary pages; the text ceiling is a second
# gate so that an article *about* bot management is never mistaken for one.
# --------------------------------------------------------------------------

CHALLENGE_MARKERS = {
    "AWS WAF": ("awswafcookiedomainlist", "reportchallengeerror", "__challenge_"),
    "Akamai Bot Manager": ("sec-if-cpt-container", "sec-bc-tile-container",
                           "scf-akamai-logo", "behavioral-content",
                           # The plain edge refusal, which is what a retailer's
                           # product pages actually returned: a 403 reading
                           # "Access Denied ... Reference #18.71cc517...".
                           # Without it the audit concluded the retailer had no
                           # products and filed the check as legitimately
                           # declined.
                           "errors.edgesuite", "reference&#32;#",
                           "you don't have permission to access"),
    "Cloudflare": ("__cf_chl", "cf_chl_opt", "cf-chl-", "just a moment...",
                   "attention required! | cloudflare"),
    "DataDome": ("captcha-delivery", "datadome-", "dd_cookie"),
    "PerimeterX": ("perimeterx", "px-captcha", "_pxhd"),
    "Imperva Incapsula": ("_incapsula_resource", "incapsula incident id"),
    "Distil": ("distil_r_captcha",),
}

# Above this much readable text the page carried content, whatever else is in
# it. Real homepages we measured run 5,000 to 10,000 characters; the challenge
# pages ran 0 and 32.
CHALLENGE_TEXT_CEILING = 800


# What a verification page says about itself, in the words it shows a human
# who lands on one. These are about the checking, never about the site, and
# they are what lets a challenge be recognised from a vendor nobody has added
# to the table yet.
#
# Deliberately not "enable javascript": a single-page app ships that sentence
# in a `<noscript>` and is a genuine finding this audit must keep reporting.
# Every phrase here names the verification itself.
_CHALLENGE_PHRASES = (
    "verifying your browser", "checking your browser", "verify you are human",
    "verify you are a human", "are you a robot", "security checkpoint",
    "please wait while we verify", "checking if the site connection is secure",
    "additional security check", "human verification",
    "enable javascript and cookies to continue", "ddos protection by",
    "your request has been blocked", "access to this page has been denied",
)

# A header the edge sets to say it interfered with the request. Matched by
# shape, because every vendor spells its own name into the header and the
# point is not to have to know them all.
_MITIGATION_HEADER_RE = re.compile(r"mitigat|challenge|bot-?(?:block|score)", re.I)


def _vendor_from_headers(headers):
    """`x-vercel-mitigated` -> `Vercel`. The name is in the header itself."""
    for name in headers or ():
        if not _MITIGATION_HEADER_RE.search(name):
            continue
        parts = [p for p in re.split(r"[-_]", name)
                 if p and p.lower() not in ("x", "mitigated", "mitigation", "challenge")]
        if parts:
            return parts[0].capitalize()
    return "the site's edge"


def detect_challenge(html, page_text, status=None, headers=None):
    """Which bot manager served a verification page here, if any.

    Returns a name, or None. The name matters: the fix is to allow-list
    crawlers in that product, and naming it saves the site owner the first
    hour of the job.

    Three ways to recognise one, in order of how much they tell you. A vendor
    marker names the product. A mitigation header names the vendor too, in the
    header's own spelling. A phrase the page shows the visitor names nothing,
    but still says this is a verification screen and not the site.

    The vendor table alone was not enough. A museum's homepage answered 429
    with the title "Vercel Security Checkpoint" and the header
    `x-vercel-mitigated: challenge`, and because that vendor was not in the
    table the appendix reported "no page answered with a bot-manager
    verification page in place of its content" - the exact opposite of what
    the snapshot beside it recorded.
    """
    if len(page_text or "") > CHALLENGE_TEXT_CEILING:
        return None
    low = (html or "").lower()
    for vendor, markers in sorted(CHALLENGE_MARKERS.items()):
        if any(marker in low for marker in markers):
            return vendor
    if headers and any(_MITIGATION_HEADER_RE.search(name) for name in headers):
        return _vendor_from_headers(headers)
    text = (page_text or "").lower()
    if any(phrase in low or phrase in text for phrase in _CHALLENGE_PHRASES):
        return _vendor_from_headers(headers)
    return None


# --------------------------------------------------------------------------
# Showing the words
#
# A check that reports a fact as MISSING already has to quote what it looked
# at. A check that reports a fact as PRESENT had no such rule, and a pass here
# is not a quiet outcome: it suppresses the finding that would otherwise be
# raised, and prints a sentence saying the site states something.
#
# Across eight sites in one round, five had a fact recorded as found on the
# strength of a regular expression that had matched something else:
#
#   - a customer's testimonial, "We save close to $30,000 per year", read as
#     the site's own pricing, on a page with no price on it
#   - a sponsor's blurb about a different company, "powers 70+ million
#     accounts worldwide", read as this site's service area
#   - the idiom "feel free to use those" read as a statement that the software
#     costs nothing
#   - the word "employees" inside a hidden terms-of-use block that appears on
#     every page, read as the site's team facts
#   - the digits inside a photo-sharing URL, `/photos/<user>/39225879230/`,
#     read as a telephone number
#
# Each one printed a sentence claiming the site states a fact it does not, and
# each one stopped a true finding from being raised.
#
# So: a claim that something is present carries the words that make it
# present. If a check cannot produce the sentence, it has not found the thing.
# --------------------------------------------------------------------------

# Enough of a sentence to be worth printing, and short enough to print.
EVIDENCE_SENTENCE_MIN = 15
EVIDENCE_SENTENCE_MAX = 220


def sentence_with(text, pattern):
    """The sentence in `text` that `pattern` matches, or None.

    None when the pattern does not match, and also when it matches something
    too short to be a sentence - a fragment of a menu, a lone word in a table
    cell - because a fragment cannot be judged by the reader the report is
    written for.
    """
    if not text:
        return None
    match = pattern.search(text)
    if match is None:
        return None
    for sentence in sentences(text):
        if match.group(0) in sentence:
            sentence = truncate(sentence.strip(), EVIDENCE_SENTENCE_MAX)
            return sentence if len(sentence) >= EVIDENCE_SENTENCE_MIN else None
    return None


# First person, or the brand's own name. Everything else on a page can be
# about somebody else: a customer, a sponsor, a case study, a client's deal.
_SELF_REFERENCE_RE = re.compile(
    r"\b(?:we|we're|we've|our|ours|us|the (?:company|firm|practice|team))\b", re.I)


def speaks_about_itself(sentence, brand_name=""):
    """Is this sentence about the site, or about someone the site mentions?

    The distinction the four false passes above all turned on. A page may
    carry a number, a place and a date that belong to a client, a sponsor or a
    quoted customer, and a pattern cannot tell whose they are. A sentence in
    the first person, or one naming the brand, can be attributed; one that
    does neither cannot.
    """
    if not sentence:
        return False
    if _SELF_REFERENCE_RE.search(sentence):
        return True
    key = brand_key(brand_name) if brand_name else ""
    return bool(key) and key.lower() in sentence.lower()


# --------------------------------------------------------------------------
# How much of the sitemap we actually read
#
# A sitemap index names its children but does not contain them, and this audit
# fetches a few of the children and stops - deliberately, because the point is
# a representative sample of URLs and not a full inventory.
#
# What was wrong was reporting the sample's total as the site's total. A
# documentation site was told it publishes 7,033 sitemap entries; it publishes
# 32,820 across twelve language sitemaps, of which three were read. A
# retailer's report said its sitemap lists 470 URLs, against a real 2,139. In
# both cases the number was then reused: as the denominator of the lastmod
# coverage percentage, and as the population the orphan-page finding compared
# the crawl against.
#
# The index says how many children exist without anyone having to fetch them,
# so the honest number is always available for free.
# --------------------------------------------------------------------------

def sitemap_scope(sitemaps):
    """What the sitemap counts cover, and what they leave out."""
    declared, read, counted, lastmods = set(), set(), 0, 0
    for record in sitemaps or ():
        declared.update(record.get("child_sitemaps") or [])
        if record.get("status") == 200:
            read.add(record.get("url"))
            counted += record.get("url_count", 0)
            lastmods += record.get("lastmod_count", 0)
    unread = declared - read
    return {
        "urls_counted": counted,
        "lastmods_counted": lastmods,
        "children_declared": len(declared),
        "children_read": len(declared & read),
        "complete": not unread,
    }


def sitemap_total_phrase(scope, noun="entries"):
    """`32,820 entries`, or `at least 7,033 entries` with the reason attached."""
    if scope["complete"]:
        return "{:,} {}".format(scope["urls_counted"], noun)
    return ("at least {:,} {} - the sitemap index names {} sub-sitemaps and this audit read "
            "{} of them, so the real total is higher").format(
                scope["urls_counted"], noun, scope["children_declared"],
                scope["children_read"])


def plural(count, singular, plural_form=None):
    """`1 page`, not `1 page(s)`.

    `report.md` is written for a marketing manager and carried "(s)" through
    every finding title, which is the first thing a reader sees. A parenthesised
    plural is a note from a programmer to themselves that the reader has to
    decode.
    """
    if plural_form is None:
        plural_form = singular + "s"
    return "{} {}".format(count, singular if count == 1 else plural_form)


# How much text an unclassified page needs before it counts as content.
# Above a paragraph or two with a heading over it, whatever the URL says.
UNCLASSIFIED_CONTENT_MIN_CHARS = 400


def looks_like_content(page):
    """A page the type table did not recognise, but which is plainly content.

    `page_type` is decided mostly from URL slugs, and a site whose URLs do not
    use those words gets everything typed `other` - which `content_only` then
    drops, so the checks see a slice and the report describes the site.

    Measured on one software company's crawl: twelve dated posts under
    `/changelog/` and `/now/`, each with a headline, a publication date and
    several paragraphs, were all typed `other`. The two freshness checks
    skipped with the reason "no article or press pages were crawled" - about a
    crawl that had just read twelve of them - and the shell check never looked
    at the page that turned out to be 246 KB of JavaScript and no text at all.

    Adding slugs to the table fixes one site. Asking the page what it holds
    fixes the ones nobody has run this on yet.
    """
    page_type = page.get("page_type")
    if page_type in CONTENT_TYPES:
        return True
    # Only for `other`, which means "not recognised". Every other type outside
    # CONTENT_TYPES is a type this audit recognised and excluded on purpose -
    # a privacy policy, a careers listing - and inspecting the text would
    # bring those back in, which is the opposite of what naming them was for.
    if page_type != "other":
        return False
    return (page.get("body_text_len", 0) >= UNCLASSIFIED_CONTENT_MIN_CHARS
            and bool((page.get("headings") or {}).get("h1")))


def pages_of(snapshot, types=None, content_only=False, ok_only=True,
             include_challenged=False):
    """Select pages from a snapshot with the usual filters applied.

    Pages where a bot manager served a verification page instead of the content
    are excluded by default. They answer 2xx and carry almost no text, so to
    every content check they look like a site that shipped an empty page - and
    the audit would report a JavaScript shell, absent structured data, no
    quotable fact and thin content, four confident findings about a homepage
    that is fine. A challenge is a fact about the crawler's reception, not
    about the site, which is the same rule that stops a refused link being
    reported as a dead one.

    `crawl-access-audit` passes `include_challenged=True`, because for that
    skill the challenge is the finding.
    """
    out = []
    for page in snapshot.get("pages", []):
        if ok_only and page.get("status") != 200:
            continue
        # A record with `skipped` set is a URL we deliberately did not read:
        # a redirect off this origin, or a non-HTML response. It answers 200
        # and carries no title, no `lang` and no text, so every hygiene check
        # counted it as a page missing all three. A retailer's `/country/ca`
        # redirects to a separate national site, and the report told them that
        # page had no title and declared no language; both are present on the
        # page a visitor actually lands on, which is on a host this audit is
        # not auditing.
        if page.get("skipped"):
            continue
        if not include_challenged and page.get("challenge"):
            continue
        if content_only and not looks_like_content(page):
            continue
        if types and page.get("page_type") not in types:
            continue
        out.append(page)
    return sorted(out, key=lambda p: p["url"])


def homepage_of(snapshot):
    for page in snapshot.get("pages", []):
        if page.get("page_type") == "home":
            return page
    ok = pages_of(snapshot)
    return ok[0] if ok else None


# --------------------------------------------------------------------------
# HTTP - read-only, identified, rate-limited
# --------------------------------------------------------------------------

class FetchError(Exception):
    pass


class Fetcher:
    """GET/HEAD-only HTTP client with a fixed UA, delay and request ceiling.

    Every skill that touches the network goes through this class, so the
    read-only guarantee is enforced in one place rather than trusted to six
    scripts.
    """

    def __init__(self, max_requests=None, delay=REQUEST_DELAY, timeout=REQUEST_TIMEOUT,
                 user_agent=USER_AGENT, deadline=None):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise FetchError("the `requests` package is required: {}".format(exc))
        self._requests = requests
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
        })
        self.max_requests = max_requests
        self.delay = delay
        self.timeout = timeout
        self.count = 0
        self.deadline = deadline
        self._last_request = 0.0

    @property
    def budget_left(self):
        if self.max_requests is None:
            return True
        return self.count < self.max_requests

    @property
    def time_left(self):
        return self.deadline is None or time.monotonic() < self.deadline

    def get(self, url, user_agent=None, allow_redirects=True, method="GET"):
        """Fetch a URL. Returns a `requests.Response` or raises `FetchError`."""
        if is_forbidden_path(url):
            raise FetchError("refusing to fetch a state-changing or private path: {}".format(url))
        if method not in ("GET", "HEAD"):
            raise FetchError("only GET and HEAD are permitted, got {}".format(method))
        if not self.budget_left:
            raise FetchError("request budget exhausted ({})".format(self.max_requests))
        if not self.time_left:
            raise FetchError("wall-clock budget exhausted")

        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < self.delay:
            time.sleep(self.delay - elapsed)

        headers = dict(DEFAULT_HEADERS)
        if user_agent:
            headers["User-Agent"] = user_agent
        self.count += 1
        started = time.monotonic()

        # One retry, and only for failures that are about the moment rather than
        # about us: a dropped connection, a timeout, a 5xx, a 429. A 403 is a
        # decision and is never retried. Without this a single hiccup marked a
        # whole site unreadable - three sites recorded as "blocked" across our
        # samples turned out to answer 200 on the next attempt, which quietly
        # overstated how many sites refuse a polite crawler.
        last_error = None
        for attempt in range(RETRY_ATTEMPTS):
            if attempt:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                self.count += 1
            if not self.time_left:
                last_error = FetchError("wall-clock budget exhausted")
                break
            try:
                # `stream=True` so nothing is downloaded until `_read_body`
                # says so. Both caps are then enforced before the bytes arrive
                # rather than discovered after they have already cost us.
                response = self.session.request(
                    method, url, timeout=self.timeout, allow_redirects=allow_redirects,
                    headers=headers, stream=True,
                )
            except self._requests.RequestException as exc:
                last_error = FetchError(str(exc))
                continue
            if response.status_code in RETRYABLE_STATUS and attempt + 1 < RETRY_ATTEMPTS:
                response.close()
                last_error = FetchError("HTTP {}".format(response.status_code))
                continue
            try:
                self._read_body(response, method, started)
            except FetchError as exc:
                last_error = exc
                if getattr(exc, "stalled", False):
                    break
                continue
            self._last_request = time.monotonic()
            response.elapsed_ms = int((self._last_request - started) * 1000)
            return response

        self._last_request = time.monotonic()
        raise last_error or FetchError("request failed")

    def _read_body(self, response, method, started):
        """Read the body under a size cap and a total-time cap.

        Sets `response.truncated`, which the crawl records on the page and the
        report states, because analysing half a document and saying nothing
        about it would turn a limit of ours into a claim about the site.
        """
        response.truncated = False
        if method == "HEAD":
            response._content = b""
            response._content_consumed = True
            return

        chunks, total = [], 0
        try:
            # `iter_content` decodes Content-Encoding as it goes, so the cap
            # counts decompressed bytes and a compression bomb is bounded too.
            for chunk in response.iter_content(RESPONSE_CHUNK_BYTES):
                if time.monotonic() - started > REQUEST_TOTAL_TIMEOUT:
                    response.close()
                    stall = FetchError(
                        "response was still arriving after {:.0f}s".format(
                            REQUEST_TOTAL_TIMEOUT))
                    # Not weather. A dropped connection is worth one more try;
                    # a server dribbling bytes will dribble them again, and
                    # retrying doubles the worst case one URL can cost the
                    # crawl from 40 seconds to 80.
                    stall.stalled = True
                    raise stall
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_RESPONSE_BYTES:
                    response.truncated = True
                    response.close()
                    break
        except self._requests.RequestException as exc:
            raise FetchError(str(exc))

        response._content = b"".join(chunks)[:MAX_RESPONSE_BYTES]
        response._content_consumed = True

    def try_get(self, url, **kwargs):
        """Fetch, returning `None` instead of raising. For optional probes."""
        try:
            return self.get(url, **kwargs)
        except FetchError:
            return None


# --------------------------------------------------------------------------
# HTML parsing helpers
# --------------------------------------------------------------------------

def make_soup(html):
    """Parse HTML with lxml, falling back to the stdlib parser."""
    from bs4 import BeautifulSoup
    try:
        return BeautifulSoup(html or "", "lxml")
    except Exception:
        return BeautifulSoup(html or "", "html.parser")


_NON_CONTENT_TAGS = ("script", "style", "template", "noscript", "svg")



# Joining inline elements with a space is what makes `get_text` readable, and
# it also puts a space in front of every punctuation mark that happened to sit
# outside a `<b>` or an `<a>`. On a database project's homepage the sentence
# came out "a small , fast , self-contained , high-reliability , full-featured
# , SQL database engine" - and that string went into a paste-ready
# `Organization` description, so an owner following the advice would publish
# their own tagline with six stray spaces in it.
#
# The site's words are unchanged. Only the spacing this audit introduced is
# taken back out.
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?)\]}%])")
_SPACE_AFTER_OPENING = re.compile(r"([(\[{])\s+")


def tidy_spacing(text):
    """Undo the separator spaces `get_text` inserts around inline markup."""
    text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text or "")
    return _SPACE_AFTER_OPENING.sub(r"\1", text)

def visible_text(soup):
    """Text a machine would treat as page content.

    Scripts, styles and `<noscript>` are removed: their bytes inflate a page
    that carries no readable prose, which is exactly the failure this
    marketplace exists to catch.
    """
    clone = make_soup(str(soup))
    for tag in clone(list(_NON_CONTENT_TAGS)):
        tag.decompose()
    text = clone.get_text(separator=" ")
    return tidy_spacing(re.sub(r"\s+", " ", text).strip())


MAIN_TEXT_SELECTORS = ("main", "article", "[role=main]", "#main", "#content", ".content")
MAIN_TEXT_MIN_CHARS = 200

# How complete a content region has to be before it is trusted over the whole
# stripped document. Measured on sixteen live pages, comparing what the chosen
# region held against what the whole document held once chrome was removed:
#
#   thirteen pages     0.89 to 1.00 - the region is the content
#   a space agency     0.39 - <main> is a shell; 62% of the page sits outside it
#   a framework's site 0.27 - <main> holds the hero; 73% sits outside it
#
# The old rule took the first region over 200 characters, so on those two it
# returned a fraction of the page and every downstream check read the rest as
# absent. That is how a page with 3,176 characters of text gets reported as
# having 30: not a crawl failure, a extraction rule that stopped too early.
#
# 0.85 sits in the gap. Nothing measured falls between 0.39 and 0.89, so the
# threshold separates the two populations rather than cutting through one.
MAIN_TEXT_COMPLETE_SHARE = 0.85


def main_text(soup, prepared=None):
    """Text of the main content region, falling back to the whole body.

    Header/nav/footer chrome repeats on every page and would otherwise mask a
    genuinely empty article body, so a real content region is preferred.

    But a content region is only better than the whole document when it
    actually holds the content. Two failures are possible and both were seen
    on live sites: a `<main>` that wraps a hero and leaves the article outside
    it, and a page whose article sits inside an element `_strip_chrome` treats
    as chrome, where the whole-document fallback is the one that loses text.
    So both candidates are measured and the fuller one wins, with the region
    preferred while it is within `MAIN_TEXT_COMPLETE_SHARE` of the best.

    `prepared` is the whole-document text when the caller has already built
    the cleaned copy, which the page extractor has, because the readability
    statistics need the same one. Building it twice parsed every page an extra
    time for nothing.
    """
    whole = (prepared if prepared is not None
             else tidy_spacing(re.sub(r"\s+", " ",
                                      content_soup(soup).get_text(separator=" ")).strip()))
    best = ""
    for selector in MAIN_TEXT_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        text = _strip_chrome(node)
        if len(text) < MAIN_TEXT_MIN_CHARS:
            continue
        # Complete enough to be the content region: keep the chrome out.
        if len(text) >= MAIN_TEXT_COMPLETE_SHARE * len(whole):
            return text
        if len(text) > len(best):
            best = text
    if len(best) > len(whole):
        return best
    return whole or visible_text(soup)


HIDDEN_SELECTORS = ("[aria-hidden=true]", "[hidden]")


def visible_soup(soup):
    """A copy of the document with the parts it has hidden removed.

    `main_text` strips these, and for a while nothing else did. So a restaurant
    chain's `aria-hidden` "you are about to leave our website" modal, which
    sits in every page's header, stayed in the paragraph and section lists -
    and it was the sentence the report offered as what an assistant would quote
    from the homepage, the about page, the contact page, the FAQ and every one
    of the location pages, plus the "opening prose" the answer-first check
    judged. One piece of markup, one verdict, thirteen rows of a report.

    Applied to the text-bearing extractions only. JSON-LD lives in a `<script>`
    and is read from the original document.
    """
    clone = make_soup(str(soup))
    for selector in HIDDEN_SELECTORS:
        for found in clone.select(selector):
            if getattr(found, "attrs", None) is None:
                continue
            found.decompose()
    for found in clone.select("[style]"):
        if getattr(found, "attrs", None) is None:
            continue
        style = (found.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            found.decompose()
    return clone


# What the page itself says is not content. A landmark element, an
# `aria-hidden` attribute and `display:none` are declarations by the author,
# so removing them is reading the page rather than guessing about it.
_DECLARED_CHROME_SELECTORS = (
    "header", "nav", "footer", "aside", "[role=navigation]", "[role=contentinfo]",
    # Markup that says "do not show this". A restaurant chain ships an
    # aria-hidden "you are leaving our site" modal in every page's header, and
    # it became the first content section of twelve pages: the answer-first
    # check read it as their opening prose, and the citation table offered the
    # same irrelevant sentence as what an assistant would quote from the
    # homepage, the contact page, the FAQ and every location page. A machine
    # reading the page for facts does not read what the page has hidden.
    "[aria-hidden=true]", "[hidden]",
)

# Guesses from a class or id name. Every one of these is a bet that a word in
# a CSS class means the same thing on this site as it did on the last one.
#
# `[class*=announcement]` was written for the announcement bar that sits above
# the header on commerce templates. On a public radio programme's site it
# matched `<article class="node-announcement">` - the CMS's own name for the
# content type - and deleted the page. Ten dated announcements, 3,228
# characters, became 34, and the audit reported the page twice: once as text
# locked in images and once as too thin to quote. Both findings were false and
# both were about our own selector.
#
# The bar in the original case is real, and so is the cosmetics retailer that
# stacks one free-shipping threshold per locale in it - "$40", "$60 CAD",
# "$110 AUD", "R$670" all at once - which the audit read as the prices on the
# page and reported as contradicting the product's own correct $16 markup.
#
# So the guesses stay, bounded: see _GUESS_MAX_SHARE.
_GUESSED_CHROME_SELECTORS = (
    "[class*=breadcrumb]", "[id*=breadcrumb]",
    "[class*=announcement]", "[id*=announcement]",
    "[class*=promo-bar]", "[class*=promobar]", "[class*=promo_bar]",
    "[class*=announce-bar]", "[class*=top-bar]", "[class*=topbar]",
    "[class*=usp-bar]", "[class*=marquee]", "[role=marquee]",
)

# A guess may not take more than this share of the text the page declared as
# content. An announcement bar is a strip above the header; it is never a
# third of the page. Anything that big is the page, whatever its class says.
#
# This is the general form of the fix. The next keyword guess someone adds -
# and there will be one, because these bars carry no standard markup - is
# bounded by the same number without anyone having to remember it.
_GUESS_MAX_SHARE = 0.3


def _decompose_safely(clone, selector):
    """Remove every match, and return how many characters went with them.

    `select` builds the whole list before anything is removed, so decomposing
    a parent leaves its already-matched descendants in the list with their
    attributes set to None. Calling `.get` on one of those raised
    `AttributeError: 'NoneType' object has no attribute 'get'` and killed the
    crawl outright - no snapshot, no report, just a stack trace - on two major
    retail sites, both of which ship a hidden banner containing an
    inline-styled child. That is ordinary markup, not an exotic case.
    """
    removed = 0
    for found in clone.select(selector):
        if getattr(found, "attrs", None) is None:
            continue                      # already removed with its parent
        removed += len(found.get_text(" ").strip())
        found.decompose()
    return removed


def _text_length_of(selector, clone):
    """How much text a selector would take, without taking it."""
    total = 0
    for found in clone.select(selector):
        if getattr(found, "attrs", None) is None:
            continue
        total += len(re.sub(r"\s+", " ", found.get_text(" ")).strip())
    return total


# Text that is on the page and is not the page's own words.
#
# Chrome is furniture the site repeats. This is different: it is content,
# written by somebody who is not the site, sitting inside the content region
# where every check reads it as the page speaking.
#
# A recipe blog's 549 reader comments were counted as the author's prose -
# 30,184 words on one page, reported as writing too dense to skim. One
# commenter's id, 598456, was published as the site's postcode. The word
# "Reply" in a reply link became a street name. And on a project-tracking
# company's site a customer's testimonial, "We save close to $30,000 per
# year", was recorded as the company's own pricing, which suppressed the
# finding that the site never states a price - while its real per-seat prices
# sat unexamined on the pricing page.
_NOT_OUR_WORDS_SELECTORS = (
    "#comments", "#respond", "#disqus_thread", "#comment-list",
    "[class*=comment-list]", "[class*=comments-area]", "[class*=comment-respond]",
    "[itemprop=comment]", "[class*=review-list]", "[class*=testimonial]",
)

# Unless taking them out leaves nothing. A forum thread, a question-and-answer
# page and a review site *are* their user content; removing it there would not
# be reading the page more carefully, it would be deleting the page - the same
# mistake `_GUESS_MAX_SHARE` exists to stop, arrived at from the other side.
_OWN_WORDS_FLOOR = 300


def without_other_peoples_words(node):
    """A copy of this document with reader comments and testimonials removed.

    `content_soup` also removes the chrome, which is wrong for the facts that
    legitimately live in the chrome: the telephone number in the footer is the
    site's telephone number. This view keeps the furniture and drops only what
    somebody else wrote, so a commenter's id cannot become the site's postcode
    and a 2019 comment cannot date the page.

    No floor here, unlike `content_soup`. A page that is nothing but comments
    still has no contact details of its own, so there is nothing to protect.

    Returns the document itself when there is nothing to remove. Parsing the
    HTML again costs about as much as parsing it the first time, and most
    pages carry no comments at all: added unconditionally, this one line put
    roughly half again on the crawl, which is spent out of a five-minute
    budget that the report's own promise depends on.
    """
    if not any(node.select_one(selector) for selector in _NOT_OUR_WORDS_SELECTORS):
        return node
    clone = make_soup(str(node))
    for selector in _NOT_OUR_WORDS_SELECTORS:
        _decompose_safely(clone, selector)
    return clone


def content_soup(node):
    """A copy of this region with everything that is not its content removed.

    Three tiers, and the differences matter. What the page declares as chrome
    - a landmark element, `aria-hidden`, `display:none` - comes out
    unconditionally. What is content but is not the page's own words comes out
    too, unless that empties the page. What a CSS class name merely suggests
    is chrome comes out only while it stays small, because a guess that
    removes most of a page has stopped being a guess about furniture and
    become a claim about content.
    """
    clone = make_soup(str(node))
    for tag in clone(list(_NON_CONTENT_TAGS)):
        tag.decompose()
    for selector in _DECLARED_CHROME_SELECTORS:
        _decompose_safely(clone, selector)
    for found in clone.select("[style]"):
        if getattr(found, "attrs", None) is None:
            continue                      # already removed with its parent
        style = (found.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            found.decompose()

    declared = len(re.sub(r"\s+", " ", clone.get_text(separator=" ")).strip())

    # Measured by removing them, not by adding up what they hold: these
    # selectors nest - a `.comment-list` sits inside `#comments` - so summing
    # their lengths counts the same text twice, and on the page this was
    # written for the doubled total came out larger than the page itself. The
    # removal then never happened, which is a quiet failure: the code looked
    # like it was guarding a floor and was in fact guarding nothing.
    if any(clone.select_one(selector) for selector in _NOT_OUR_WORDS_SELECTORS):
        trimmed = make_soup(str(clone))
        for selector in _NOT_OUR_WORDS_SELECTORS:
            _decompose_safely(trimmed, selector)
        remaining = len(re.sub(r"\s+", " ", trimmed.get_text(separator=" ")).strip())
        if remaining >= _OWN_WORDS_FLOOR:
            clone, declared = trimmed, remaining

    budget = _GUESS_MAX_SHARE * declared
    for selector in _GUESSED_CHROME_SELECTORS:
        if _text_length_of(selector, clone) > budget:
            continue                      # too big to be furniture
        _decompose_safely(clone, selector)
    return clone


def _strip_chrome(node):
    """Text of a region with scripts, navigation and other people's words out."""
    return tidy_spacing(
        re.sub(r"\s+", " ", content_soup(node).get_text(separator=" ")).strip())


def eprint(*args):
    print(*args, file=sys.stderr)
