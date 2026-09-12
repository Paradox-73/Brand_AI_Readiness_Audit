"""Shared vocabulary and helpers for every skill in this marketplace.

The orchestrator owns this module. Sub-skill `check.py` scripts import it by
walking up to `skills/audit-orchestrator/scripts/`, which keeps one definition
of the finding shape, the root-cause vocabulary, and the page-type detector
instead of six drifting copies.

Nothing here performs a write to a target site. The only network primitive is
`Fetcher`, which issues GET/HEAD with a fixed identifying user agent.
"""

from __future__ import annotations

import codecs
import http.client
import json
import os
import random
import re
import unicodedata
import sys
import time
# Imported under a private name on purpose. Half a dozen functions in this file
# take a parameter called `html`, and a bare `import html` would be shadowed
# inside every one of them - a failure that shows up as an AttributeError only
# on the input that needs decoding.
from html import unescape as _html_unescape
import urllib.error
import urllib.request
from urllib.parse import (parse_qsl, unquote, urlencode, urljoin, urlparse,
                          urlunparse)

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

# The two HTTP clients `Fetcher` can put a request on the wire with.
#
# One client was a confound this marketplace could not see past. Every probe
# in the user-agent comparison left through the same `requests` session, so
# when an edge refused all of them the comparison could not tell "this edge
# refuses every crawler name" from "this edge refuses this HTTP client
# whatever name it sends" - two rules with opposite first moves. Measured on
# a museum: `requests` and plain `curl` sending one identical string were
# answered 403 and 200 by the same origin, seconds apart.
#
# So there are two, and the difference between them is the client, not the
# permission. `SECOND_TRANSPORT` sends through `urllib.request` from the
# standard library: a different TLS handshake, a different default header set
# and a different header order, and no new dependency. Both go through the
# one guard in `Fetcher.get`, which is where the read-only promise lives -
# forbidden paths, GET or HEAD only, the request budget, the delay and the
# wall clock. A second way to send bytes is not a second set of rules.
PRIMARY_TRANSPORT = "requests"
SECOND_TRANSPORT = "urllib"
TRANSPORTS = (PRIMARY_TRANSPORT, SECOND_TRANSPORT)

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

# When most of what a crawl has read is a bot manager's verification screen,
# reading more of them spends the budget on the bot manager rather than on the
# site. One crawl used 274 of its 300 seconds collecting 33 copies of the same
# screen, and every percentage in the report then had those 33 in its
# denominator.
CHALLENGE_GIVE_UP_AFTER = 8
CHALLENGE_GIVE_UP_SHARE = 0.6
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
# measured over 260 s now ends in 36 s, bounded by 40. That is the bound for a
# fetcher with no deadline - a single skill run from the command line. A
# fetcher given one is bounded against the deadline instead; see
# DEADLINE_OVERSHOOT_SECONDS below.
MAX_RESPONSE_BYTES = 5_000_000
REQUEST_TOTAL_TIMEOUT = 30.0
RESPONSE_CHUNK_BYTES = 65536

# What one request may still cost after the deadline it was told to respect.
#
# The two caps above bound a request against its own start. Neither bounded it
# against the *phase's* deadline: `Fetcher.get` tested the clock before it
# opened a socket and not again, so a request begun with one second left ran
# REQUEST_TOTAL_TIMEOUT = 30 s past the second the phase was supposed to stop.
# That is why `run_audit.py` had to hold back 40 s of every network phase's
# wall clock, and the crawl paid for the whole of it in pages. Measured against
# a server dribbling one byte every half second, before and after:
#
#     12 s of budget left     +18.12 s past the deadline  ->  +0.03 s
#      2 s of budget left     +28.05 s past the deadline  ->   +3.0 s
#
# REQUEST_CLAMP_FLOOR_SECONDS - how short the clamp is allowed to make a
# request. Both caps below are shrunk to what is left of the phase's clock, and
# both stop shrinking here.
#
# A floor is needed because a FetchError is read downstream as evidence about
# the site: "did not answer in time" and "sent its response so slowly the audit
# stopped waiting" are sentences about the owner's server. A half-second
# timeout on the last half-second of a budget would print one of them about
# every ordinary page the crawl happened to start late, and a report full of
# those reads as a blocked site.
#
# Five seconds, which is above where this marketplace already calls an origin
# slow (SLOW_ORIGIN_MEDIAN_MS, 3 s, "outside anything normal") and below where
# it calls one very slow (10 s). It bites only on a request begun in the last
# five seconds of a phase, whose answer was going to arrive after the deadline
# whatever happened.
#
# It is REQUEST_TIMEOUT / 2 rather than a number of its own because that is
# what keeps the bound below equal to one socket timeout; see the arithmetic
# there.
#
# There is deliberately no "too little time left, refuse to start" rule on top
# of this. Tried at ten seconds, and a run with `--budget 6` - which the test
# suite makes and a caller may ask for - opened no socket at all and produced
# an empty snapshot. A floor that can exceed the whole budget is not a floor.
#
# DEADLINE_OVERSHOOT_SECONDS - the bound the clamp can promise, and the number
# `run_audit.py` should read for its per-phase safety margin.
#
# It is not zero and cannot be. Every deadline here is advisory to a socket
# that is already open. `connect` and `first byte` each get their own socket
# timeout, so `session.request` can block for twice the clamped timeout before
# the elapsed check is reached at all, and the chunk read that straddles the
# deadline blocks until the socket returns control. Writing `F` for the floor,
# `T` for REQUEST_TIMEOUT and `L` for the clock left when the request starts,
# the clamped timeout is max(F, min(T, L)) and the worst finish is
# 2 x max(F, min(T, L)) - L past the deadline: T where L = T, and 2F where L is
# nearly zero. F = T / 2 makes both terms T, which is why the floor is half the
# socket timeout and the bound is one whole one.
#
# Cutting it finer would mean closing the socket from another thread mid-body,
# which turns a slow response into a truncated one - a worse lie about the site
# than a late answer.
#
# This is the bound for a `Fetcher` built with the default `timeout`, which is
# every fetcher in this marketplace. One built with a larger `timeout` gets a
# proportionally larger bound, by the same arithmetic.
REQUEST_CLAMP_FLOOR_SECONDS = REQUEST_TIMEOUT / 2
DEADLINE_OVERSHOOT_SECONDS = REQUEST_TIMEOUT

# Paths we never touch: they mutate state, cost the owner money, or are
# private. This list is enforced in `Fetcher`, not just documented.
#
# Matched as whole path segments, never as substrings. A substring test refused
# `/basketball` for containing "basket", `/cartridges` and `/cartoons` and
# `/carte-des-vins` for "cart", `/services/accounts-payable` and `/accountancy`
# for "account", `/administration` for "admin", `/registered-charity` for
# "register", and `/printers` was safe only by luck. A basketball club, a
# printer-supplies shop and an accountancy firm each lost whole sections of
# their site, and the crawl recorded the refusal as "state-changing or private
# path" - so the report was quietly describing a fraction of the site while
# claiming to describe the site.
FORBIDDEN_PATH_SEGMENTS = (
    "wp-admin", "wp-login", "admin", "login", "signin", "sign-in",
    "signup", "sign-up", "register", "account", "accounts", "cart", "checkout",
    "logout", "password", "xmlrpc.php",
    "my-account",
    # A visitor's own saved list, which is the same private area as their
    # account whichever app renders it.
    "wishlist", "wishlists", "iwish",
)

# A storefront's app routes. A hosted store platform serves installed apps
# through a proxy prefix - `/apps/<app>` or `/a/<app>` - and the app behind a
# wishlist, an account area, a review form or a loyalty wallet renders the
# visitor's own state there. The crawl fetched a wishlist app's page, read an
# empty basket and a footer off it, and published findings about "the page".
# Read by shape: the prefix, then an app whose name says whose state it is.
_APP_PROXY_RE = re.compile(
    r"^/(?:[a-z]{2}(?:[-_][a-z]{2,4})?/)?(?:apps|a)/[^/]*"
    r"(?:wish|account|login|review|loyalty|reward|referral|subscription|order"
    r"|customer|profile)", re.I)

# The words that name a shop's basket, and where in an address they may stand
# to mean one.
#
# "basket" is also a thing a shop sells. A handicrafts store files its baskets
# at `/c/basket`, titled "Buy Basket Online", and the guard above refused the
# whole category because the segment appeared anywhere in the path - and the
# sitemap check then published it at confident tier as a cart address. `/basket`
# really is the cart on a great many shops, and so is `/checkout/basket`, so the
# word is judged by its position: the first segment, the first after a locale
# prefix (`/en-gb/basket`), or directly under an account, checkout or order
# segment. Anywhere else it is a shelf. `cart` stays in the anywhere-list above,
# because the crawler's guard is the cautious question; `cart_path_word` below
# is the one the sitemap finding asks, and it applies the position to both.
CART_WORDS = ("cart", "carts", "basket")
_POSITIONAL_ONLY_CART_WORDS = frozenset({"basket"})
_CART_PARENT_SEGMENTS = frozenset({
    "account", "accounts", "my-account", "myaccount", "customer", "customers",
    "checkout", "order", "orders", "cart"})
_REGION_SHAPED_SEGMENT_RE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2,4})?$", re.I)


def cart_path_word(path, words=CART_WORDS):
    """The basket word this path uses in a basket's position, or "".

    A segment counts with or without a file extension (`/basket.aspx`), and
    only where it stands first, first after a locale-shaped prefix, or directly
    under an account, checkout or order segment. See `CART_WORDS`.
    """
    segments = [s for s in str(path or "").split("?")[0].split("#")[0].split("/") if s]
    wanted = {w.lower() for w in words}
    for index, segment in enumerate(segments):
        word = segment.split(".")[0].lower()
        if word not in wanted:
            continue
        if index == 0:
            return word
        parent = segments[index - 1].split(".")[0].lower()
        if index == 1 and _REGION_SHAPED_SEGMENT_RE.match(parent):
            return word
        if parent in _CART_PARENT_SEGMENTS:
            return word
    return ""

# Two segments that mean an application endpoint at the root of a site and mean
# "reference documentation" anywhere else. They are matched at the first
# segment only, and they used to be in the tuple above, which matches a segment
# anywhere in the path.
#
# Measured, that cost this much: on a database project's documentation tree,
# `/docs/0.10/api/c/api.html` and 156 more answered this guard "yes", so the
# crawler refused to fetch the reference manual - the most valuable part of a
# documentation site - and never said so. The sitemap check then read the same
# answer and published those 157 URLs at confident tier as "cart, checkout,
# account or wishlist addresses", offering to delete them.
#
# `^/api/` is still refused, because that really is an endpoint root and a GET
# to one is not a page anyone reads. `READ_ONLY_API_ALLOWLIST` below does not
# solve this: it is exact-URL only, and no allowlist can enumerate a
# documentation tree.
_ENDPOINT_ROOT_RE = re.compile(r"^/(?:api|graphql)(?:[./]|/|$)", re.I)
# The segment may carry a file extension. `(?:/|$)` alone let `/wp-login.php`
# through - WordPress's canonical login page, on a large share of the web -
# along with `/login.php`, `/admin.php` and `/checkout.aspx`. A segment ends at
# a slash, at the end of the path, or at the dot before its extension.
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|/)(?:{})(?:[./]|$)".format(
        "|".join(re.escape(segment) for segment in FORBIDDEN_PATH_SEGMENTS)),
    re.I)

# Query parameters that perform an action rather than select a view. A GET to
# `/shop/?add-to-cart=9` adds an item to a basket on every WooCommerce store,
# and `is_forbidden_path` read `urlparse(url).path` only, so the guard that
# exists to keep this audit read-only never saw it. This marketplace's own
# robots parser names the `add-to-cart` family in a comment; the guard did not
# know about it.
_STATE_CHANGING_QUERY_RE = re.compile(
    r"(?:^|&)(?:"
    r"add[-_]?to[-_]?cart|remove[-_]?item|add[-_]?to[-_]?bag|update[-_]?cart"
    r"|action=(?:logout|signout|sign[-_]?out|delete|remove|purge|edit)"
    r"|do=(?:logout|signout)|logout|signout|do_?action|wc-ajax"
    r")(?:=|&|$)", re.I)

# The one endpoint this marketplace calls that is not a page on the audited
# site. Wikimedia asks bots to use its API instead of crawling wiki pages, and
# the README names this exception rather than hiding it - but the guard above
# forbids any path segment "api", so the read-only lookup was refused with
# "refusing to fetch a state-changing or private path" on every run. The
# entity-ambiguity check then never ran on any site, and all four reports in
# one validation pass told the owner "the Wikidata search API could not be
# reached; this is an audit-environment limitation, not a site defect" - a
# sentence about the audit's own guard, printed as a fact about the network.
#
# An allowlist rather than a hole in the pattern: the guard exists to keep the
# audit off private and state-changing URLs, and the way to keep it strict is
# to enumerate the documented read endpoints it may call, in full, here. Each
# entry is matched as an exact scheme-and-path prefix, so nothing on the
# audited site can pass by resembling one.
READ_ONLY_API_ALLOWLIST = (
    "https://www.wikidata.org/w/api.php",
)


def is_allowed_read_api(url):
    """Is this one of the documented public read endpoints named above?"""
    bare = (url or "").split("?")[0].split("#")[0]
    return any(bare == allowed for allowed in READ_ONLY_API_ALLOWLIST)


# How every finding that quotes the field study behind this marketplace names
# its sample, in one string so that no two findings can name it differently.
#
# Two reports said "29 crawlable sites" in one sentence and "36 sites" in
# another, and a reader could not tell whether the study had been
# misquoted. It had not - 36 were sampled and 7 refused the crawler - but a
# reader cannot see that from either number alone, and a report that cites its
# own evidence inconsistently invites exactly that doubt. Both numbers, one
# phrase, every time. See `references/cited-vs-uncited-study.md`.
STUDY_SAMPLE_PHRASE = ("our within-category study of 29 crawlable sites, "
                       "36 sampled, in six categories")

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
    # Distinct from `js-shell`, and the distinction is the fix. A shell is a
    # page whose content arrives later, and the fix is rendering. This is a
    # page that published the *instruction* instead of what the instruction was
    # meant to produce - `{{getCtrlKey()}}`, `#native_company#`, a store
    # locator whose whole content is `[wpsl]` - and the fix is usually one
    # broken plugin or one unsubstituted merge field, which an owner can put
    # right the day they read it. It turned up on three unrelated
    # sites and nothing in the marketplace looked for it.
    "unrendered-template-artefact",
    "no-transcript", "iframe-content", "uncrawlable-pagination", "alt-missing",
    # Gate C - structured data
    "no-org-schema", "no-product-schema", "no-article-schema", "no-faq-schema",
    "invalid-jsonld",
    "schema-text-mismatch", "missing-schema-props", "meta-hygiene",
    # One template filter, run over a value that was already escaped. The
    # marketplace reported it inside `<script>` - where a character reference
    # is a parse-level fault - and nowhere else, while the same filter puts
    # `&amp;` into the `<title>` and the meta description of every page the
    # template renders, and a search result prints those five characters at a
    # reader. Its own cause and not `meta-hygiene`, because that is an absence
    # cause and this is the opposite claim: the sentence is written, it is
    # correct, and five characters of it are wrong. The fix is a developer's
    # and takes a minute; the hygiene fix would have told a content owner to
    # rewrite copy that needs no rewriting.
    "template-escapes-values-twice",
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
    # Distinct from `dead-end`, and it has to be. `dead-end` is "a visitor who
    # lands on this page has nowhere to go next"; this is "a machine entering
    # at the front door discovers nothing", which stays true on a site with no
    # dead ends anywhere and is a discoverability failure rather than an
    # engagement one. Sharing the tag also broke the rule that holds an
    # absence claim back on a site most of which was not read: that rule is
    # keyed on the cause, and this claim is about one document that *was*
    # read, so it must be answerable separately.
    "unreachable-from-homepage",
})

# --------------------------------------------------------------------------
# Findings that assert a negative
#
# Every false positive this marketplace has made on a real site reduces to one
# move: a detector did not find something, and the report said the site does
# not have it. A breadcrumb named only in a `data-` attribute, a call to action
# that is a `<button>`, search offered as a link rather than a form, an FAQ
# with no markup, a maintainer named in the third person, a definition written
# with a verb other than "to be" - in each case the page had the thing and the
# detector had a finite list of shapes.
#
# No detector can be proved complete, so the rule is not "be sure". The rule is
# that a claim of absence must say what it looked at, and may not be stated at
# high confidence on the word of a single detector. Two independent sources
# that both come back empty is evidence; one is a guess with a number on it.
#
# These are the causes that assert a negative. Everything else in ROOT_CAUSES
# asserts something observed - a 404, a parse error, a price that disagrees -
# and carries its own evidence.
ABSENCE_CAUSES = frozenset({
    "sitemap-missing",
    "no-org-schema", "no-product-schema", "no-article-schema", "no-faq-schema",
    "no-website-schema", "no-breadcrumb-markup", "missing-schema-props",
    "open-graph-incomplete", "missing-lang",
    "no-entity-definition", "missing-core-fact", "heading-structure",
    "fluff-first", "no-transcript", "alt-missing",
    "no-date-signal", "weak-corroboration",
    "no-orientation", "no-breadcrumbs", "dead-end", "orphan-pages",
    "inconsistent-chrome",
    # "The homepage carries no link to any other address on this site" is a
    # negative, so it names the two readings that came back empty rather than
    # asserting the count on its own.
    "unreachable-from-homepage",
    # Added to close a hole: naming zero sources bought a HIGHER
    # confidence than naming one, because a cause outside this set is never
    # asked. These four assert a negative as plainly as the rest.
    #   title-body-drift  "under a third of the title's words appear in the body"
    #                     - and it reads `body_text`, which excludes headings,
    #                     so it called two words missing that sit in the page's
    #                     own H2.
    #   meta-hygiene      "N pages have no meta description"
    #   no-transcript, alt-missing are already here; `thin-html` says a page
    #                     carries almost nothing, which is the same shape.
    "title-body-drift", "meta-hygiene", "thin-html", "no-website-schema",
    # "no form of the Organization name appears in the page title, headings or
    # body text" is a pure negative, and it escaped this set for some time. It
    # became one site's only high-severity finding and its first Start-here
    # item, printed no "Where we looked", and was false: the check reads the
    # first 4,000 characters and the name was at 9,336.
    "schema-text-mismatch",
})

# How many independent sources an absence claim needs before it may be stated
# at high confidence.
CORROBORATED_ABSENCE_SOURCES = 2


# --------------------------------------------------------------------------
# What a matcher is entitled to deny
#
# The rule: a matcher may report what it found; it may not report that a thing
# does not exist unless the reading that came back empty could not have missed
# it. Where a finite list of shapes or words decided, the report says what this
# audit could not find, not what the site does not have.
#
# Two validation populations, one shape. A pass over direct-to-consumer shops
# turned up three false positives and a pass over awkward sites a fourth, and
# all four are the same move:
#
#   "no founding year appears in     `/our-story` reads "<Brand> was founded in
#    the page text or in the page     1998 in <city>, <country>". The crawl read
#    markup"                          the page and the report's own "what an
#                                     assistant would quote" table prints that
#                                     sentence.
#   "no postal address appears in    the contact page prints an Indonesian
#    the page text or in the page     RT/RW-and-Plus-Code address. The street
#    markup"                          matcher wants a street with a house
#                                     number, so the address is invisible to it.
#   "No sentence of the form         the about page reads "<Legal name> is a
#    '<Brand> is a ...' appears"      leading manufacturer and supplier of ...".
#                                     The legal name sits in the site's own
#                                     `<title>` and was never tried as a
#                                     subject.
#   "Pages carrying no date a        the page carries 54,990 characters
#    reader or a machine could use"   including four Thai Buddhist-era dates.
#
# Every one of those checks is a finite list - English founding verbs, a
# street-address shape, a definition subject built from one resolved name,
# Gregorian date formats. Widening a list fixes the two sites in front of you
# and fails on the next language. Two of these lists have already been
# widened and both defects came back. What limits accuracy is not that the
# lists are short: it is that the tool asserts absence from a list it knows is
# incomplete.
#
# The tool already does the right thing in exactly one place:
# `site_language.prose_checks_apply` is false for Thai, so
# `fact-extractability-audit` produced no founding-year finding on a Thai
# fixture rather than claiming absence. That restraint existed for one language
# gate and nothing else. This is the same restraint, made general.
#
# **Why it is enforced here and not in each check.** A check cannot see that
# its own vocabulary is incomplete - if it could, it would have a longer
# vocabulary. Nor can the next check anybody writes remember to say so. So the
# sentence is attached by `make_finding`, which every skill goes through, keyed
# on the root cause. Same reasoning as the `read_in` field, and the same failure
# it was written against: a rule applied inside the check that raised it is a
# rule the next check forgets.
#
# **Why the sentence and not the tier.** All three causes below are already
# INFERRED, so `published_tier` already prints them under "worth checking", and
# they were still counted as false positives. What a reader acts on is
# the sentence, and the sentence said the site does not have the thing.
#
# **Why not deleted, and why not moved to `unverifiable`.** A miss costs as
# much as a false positive, and an `unverifiable[]` entry
# carries no severity, no owner and no fix - moving "state your founding year
# on the about page" there deletes an action that is worth having. The
# finding stays, keeps its remedy, and stops
# asserting a fact it cannot establish.
DENIAL_RESTS_ON_A_FINITE_LIST = (
    " The readings this rests on are finite lists of shapes and words, so a value written "
    "in a form or a language those lists do not cover would not have been seen: this is "
    "what this audit could not find, and not a statement that the site does not publish it.")

# The causes whose absence a finite list decides.
#
# Three, and they are the three the two passes above turned up. Membership is a claim
# about how the check reaches its answer, so it is not a place to be generous:
# `non-200` is a status code and `invalid-jsonld` is a parser raising, and
# neither can miss what it is looking for.
#
# The ones left out that belong here, and why they are not in yet: adding a
# cause here rewrites the evidence sentence of a finding in another skill's
# file, so each one waits until that file changes with it.
# `fluff-first`, `long-sentences`, `no-breadcrumbs`, `dead-end`,
# `inconsistent-chrome`, `title-body-drift` and `schema-text-mismatch` are all
# described in `EVIDENCE_BASIS` above as a word list or a selector list
# deciding a negative, which is this set's own test. They are the next entries.
ABSENCE_DECIDED_BY_A_VOCABULARY = frozenset({
    # Four facts, and the two that went wrong - a founding year and a postal
    # address - are read with an English verb list and a street shape.
    "missing-core-fact",
    # A subject built from the one name this audit resolved, matched against
    # four English sentence templates.
    "no-entity-definition",
    # Date formats. `no-date-signal` also reads `datePublished` and `<time>`,
    # which cannot miss; the visible-text half is a list of formats, and the
    # finding is one claim over both, so the weaker half governs it.
    "no-date-signal",
})


# --------------------------------------------------------------------------
# How the evidence was obtained
#
# A validation pass over eight unseen sites scored 83.8% correct overall, 84.6% in
# the confident tier and 82.9% in the lower one. The split was honestly built
# and it separated nothing, because the field it keyed on - `confidence` - is a
# value each check writes about itself. It records how sure the author of that
# check felt, so ordering the report by it orders the report by the boldness of
# fourteen authors.
#
# What did separate the three worst errors from the two plainly-true findings
# printed below them is the kind of evidence underneath, and that is derivable:
#
#   "no off-site profile is linked"           the site's own snapshot held five
#                                             official accounts. The check had
#                                             to decide whose account each URL
#                                             was, and decided wrong.
#   "4 of 4 pages that print their own        the "addresses" were product
#    street address declare no LocalBusiness"  codes printed beside prices, so
#                                             a single-site business was told
#                                             it was a chain with four branches.
#   "1 of 2 product pages have no Product      the second page was a
#    markup"                                   promotional landing page.
#
# Every one of those is this audit having identified something - an account, a
# street, a page type - and been wrong about what it was. Neither of the two
# demoted truths identified anything: a postal address and a founding year were
# absent from every page read and from the markup property that would hold
# them, which is a count of zero rather than a judgement.
#
# So the question each cause is asked is not "how sure is the check" but:
#
#   OBSERVED  the claim is settled by what the document or the HTTP exchange
#             literally contains - a status code, an attribute, a tag, a
#             JSON-LD node, a character count. A reader can confirm or refute
#             it in View Source without agreeing with this audit about
#             anything.
#   INFERRED  the claim only holds if this audit correctly decided what
#             something IS: which pages are product pages, which URL is the
#             brand's own account, which string is a street address, whether a
#             sentence defines the brand. Each of those decisions can be wrong
#             about a document the audit read perfectly.
#
# Choosing the population counts as deciding what something is. A finding
# headed "N of M product pages have no Product markup" rests on the page typing
# as much as on the markup, and that is the third error above. Where a cause's
# whole subject is a class of page this audit assigned, the cause is INFERRED
# however literal the property it then reads.
#
# INFERRED is not an accusation and it hides nothing: the finding is still
# published, still counted, still written out in full. It means a person should
# glance at it before spending money on it, which is what the lower tier of the
# report already tells them.
#
# **The recalibration, and what forced it.** The table above was written
# against four English-language consumer shops, where it measured 85.2% correct
# in the confident tier against 64.9% in the lower one. A second validation
# pass then covered five sites deliberately unlike those - a Japanese dental clinic, a one-page
# design firm, a town government, a documentation tree, a village hall on a
# hosted builder from around 2010 - and measured 78% against 79%. The split
# stopped separating anything at all on the population it was not calibrated
# against, which is a generalisation failure sitting inside the mechanism whose
# whole job is to stop over-claiming.
#
# The cause was one class of entry. Where a detector is a list of English words
# or a list of HTML shapes, the earlier reading credited it with reading the
# document, because what it finally counts is zero. But the counting is not
# where the decision is: a pattern that does not fire has decided the prose
# does not say the thing, and a selector list that matches nothing has decided
# the page has no navigation. Both are decisions about what something is, made
# with a list that cannot be complete, and both are wrong most often on exactly
# the sites furthest from the ones the list was written against. Five causes
# moved down for that reason - `missing-core-fact`, `long-sentences`,
# `readability`, `no-breadcrumbs`, `inconsistent-chrome` - and one override was
# deleted; each carries the site it was measured wrong on beside it. Nothing
# moved up.
#
# The rule that follows, and the one to apply to any cause added later: a
# vocabulary is an identification. "This English verb pattern did not match"
# and "no element matching these five selectors was found" are INFERRED
# however many pages the count covers, and however literal the tag being
# counted. Only a claim a reader can settle in View Source without sharing any
# of this audit's opinions is OBSERVED.
# --------------------------------------------------------------------------

OBSERVED = "observed"
INFERRED = "inferred"

# One entry per root cause, and a test fails the build if that stops being
# true in either direction. Keyed on the root cause because that vocabulary is
# closed and owned by exactly one check each, which makes the whole map
# auditable on one screen against `ROOT_CAUSES`.
EVIDENCE_BASIS = {
    # Gate A - access. All of these read the HTTP exchange or a file written
    # for crawlers to parse. robots.txt names its own user-agent tokens and
    # path prefixes, and a status code is a number the origin sent, so there
    # is nothing here to be wrong about the meaning of.
    "robots-block": OBSERVED,
    "bot-manager-block": OBSERVED,
    "sitemap-missing": OBSERVED,
    "sitemap-broken": OBSERVED,
    "non-200": OBSERVED,
    "redirect-chain": OBSERVED,
    "meta-refresh": OBSERVED,
    "noindex": OBSERVED,
    "canonical-broken": OBSERVED,
    "insecure-transport": OBSERVED,
    "host-inconsistency": OBSERVED,
    "slow-origin": OBSERVED,

    # Gate A/C - readability. The line here runs between counting bytes and
    # deciding what those bytes mean.
    # Script bytes, an empty root element, and the gap between the static and
    # the rendered text length. Every signal is a measurement of the document.
    "js-shell": OBSERVED,
    # A literal string in the delivered document, matched on punctuation
    # shapes and no word list, so the reading is the same in every language.
    "unrendered-template-artefact": OBSERVED,
    # The character reference is in the parsed attribute value. An HTML parser
    # resolves `&amp;` to `&` on its own, so one that survived parsing was
    # written twice - read, not inferred.
    "template-escapes-values-twice": OBSERVED,
    # A character count against a stated floor, with the rest of the delivered
    # document's visible text accounted for as the site's own chrome in the
    # evidence line. Visible text against visible text: "the delivered
    # document" claimed an accounting over bytes the check never reads, and on
    # the site behind that validation pass's one confident-tier falsehood 224,637 of
    # 227,011 bytes were script.
    "thin-html": OBSERVED,
    # Asserts that the images carry the facts, and never reads an image.
    "image-locked-facts": INFERRED,
    # Asserts a linked PDF is fact-bearing and that no page states the same
    # figure in HTML. Two judgements about content, neither of them read off a
    # tag.
    "pdf-locked-facts": INFERRED,
    # "No nearby transcript" is a decision about what a nearby block of text
    # is, not a check for an element.
    "no-transcript": INFERRED,
    # Asserts the page's main content is inside a frame this audit did not
    # fetch, from the fact that the page itself is short.
    "iframe-content": INFERRED,
    # Decides a control is a load-more button and that the page is a listing.
    "uncrawlable-pagination": INFERRED,
    # An img element carries an alt attribute or it does not.
    "alt-missing": OBSERVED,

    # Gate C - structured data. Reading the `@type` of a JSON-LD block against
    # a fixed type list is as literal as reading a status code. What varies is
    # which pages the count is taken over.
    # Counted over every crawled page, so no page typing enters it. One check
    # under this cause counts a population a regular expression chose instead;
    # see EVIDENCE_BASIS_BY_FINDING.
    "no-org-schema": OBSERVED,
    # "N of M product pages" - the M is the page typing, and a promotional
    # landing page counted as a product page is how this cause got a graded
    # run wrong.
    "no-product-schema": INFERRED,
    # The same shape, over pages typed as articles.
    "no-article-schema": INFERRED,
    # Fires on pages read as answering questions, which is a reading of prose
    # before it is a reading of markup.
    "no-faq-schema": INFERRED,
    # A JSON parser raised, or the block was empty.
    "invalid-jsonld": OBSERVED,
    # Compares a value in the markup against the prose of the page inside a
    # fixed window. It once called a name absent that sat at character 9,336.
    "schema-text-mismatch": INFERRED,
    # A block that is present lacks a property. The block and the property
    # name are both literals.
    "missing-schema-props": OBSERVED,
    "meta-hygiene": OBSERVED,
    "open-graph-incomplete": OBSERVED,
    # The markup half is literal, but the finding only fires where a form was
    # read as on-site search, so a form that is not one produces the whole
    # finding.
    "no-website-schema": INFERRED,
    "missing-lang": OBSERVED,
    "microdata-only": OBSERVED,
    # "Deep" is the number of path segments in the URL, which is arithmetic
    # rather than a page type.
    "no-breadcrumb-markup": OBSERVED,

    # Gate C - extractability. This is where prose is read, and reading prose
    # is where this audit does most of its guessing.
    "no-entity-definition": INFERRED,
    # An h1 element is present or it is not, read twice - once from the visible
    # document and once from the markup. The slogan-heading check shares this
    # cause and grades wording; see EVIDENCE_BASIS_BY_FINDING.
    "heading-structure": OBSERVED,
    # Decides that an opening paragraph defers rather than asserts.
    "fluff-first": INFERRED,
    # Moved from OBSERVED, and it was the single worst entry in this table.
    # The old reasoning was that nothing had been identified as anything, so
    # there was no misidentification available to make. That is wrong about
    # where the identification happens: the detector *is* a list of English
    # verbs, prepositions and figure shapes, and deciding that none of them
    # fired is deciding what the prose says. A town government's report
    # published "across the 43 of 44 page(s) this audit read, no founding year
    # appears" as the second item of Start here, on a site that prints its 1764
    # charter in full. The claim holds only if the audit correctly decided what
    # a founding sentence looks like, which is what INFERRED means.
    "missing-core-fact": INFERRED,
    # Decides that two strings are one name written two ways rather than two
    # different names.
    "name-inconsistency": INFERRED,
    # Moved from OBSERVED. "Words per sentence" is two decisions before it is a
    # number: where a sentence ends, and what counts as a word. Both are
    # calibrated on English, and `test_language.py` records what that costs -
    # a whole Japanese page measured as one sentence, an Arabic page as none
    # because the splitter looks ahead for a capital letter, and a Hindi
    # newspaper measured at 3,541 words against a real 2,254. A figure that
    # moves by half depending on the script it is read in is not a figure read
    # off the document.
    "long-sentences": INFERRED,

    # D - freshness and corroboration
    # Dates read from markup, from the footer copyright line and from sitemap
    # <lastmod>, compared against a reference date. Two checks under this cause
    # read prose or page types instead; see EVIDENCE_BASIS_BY_FINDING.
    "stale-content": OBSERVED,
    # A date property is present and parses, or it is not. The on-page check
    # counts only pages where a date is "expected"; see the overrides.
    "no-date-signal": OBSERVED,
    # The count is of profiles judged to be this brand's. It reported none on a
    # site whose own snapshot held five official accounts, because attributing
    # an account to a brand from a URL is a guess.
    "weak-corroboration": INFERRED,
    # Matches the brand name against the labels of other entities, which is
    # name matching and nothing else.
    "entity-ambiguity": INFERRED,
    # Decides that two differently-written strings are the same fact stated
    # twice, which is the only way a contradiction can be claimed at all.
    "nap-inconsistency": INFERRED,
    # A URL the site itself publishes answered 404 or 410. Whose profile it is
    # never enters the claim, which is what separates it from the cause above.
    "dead-profile-link": OBSERVED,

    # Engagement
    # Mostly "this page does not orient a visitor", which is a reading of what
    # the page does for a person. Two checks under it read tags; see the
    # overrides.
    "no-orientation": INFERRED,
    # "No call to action" is a decision about what a link or a button is for.
    # A site whose links read "Join the mailing list" and "Read papers" was
    # told it had none, judged against opening verbs drawn from retail.
    "dead-end": INFERRED,
    # Moved from OBSERVED. The set arithmetic over href values is exact; which
    # links are *the chrome* is a five-selector guess - `nav`, `header`,
    # `footer`, `[role=navigation]`, `[role=contentinfo]` - and a site built
    # before those elements existed puts its whole menu in a table. On a
    # village hall's site running a hosted builder from around 2010 the
    # signature is empty on every page, so the comparison is between two blanks
    # and whatever it concludes is a conclusion about our selector list.
    "inconsistent-chrome": INFERRED,
    "broken-links": OBSERVED,
    # Counting the `<a href>` elements of one document and resolving each
    # against the origin the crawl was given. Nothing is identified as
    # anything: no page type, no vocabulary, no reading of what a link is
    # *for* - which is exactly what puts `dead-end` above in the other tier.
    # The one decision is whether an address is on this site, which is a string
    # comparison against the origin, and it is corroborated before anything is
    # published: the check declines outright where the header-and-footer
    # fingerprint - collected through a different code path in the extractor -
    # holds destinations the anchor loop did not.
    "unreachable-from-homepage": OBSERVED,
    # The link graph of what was crawled, against the URLs the sitemap lists.
    "orphan-pages": OBSERVED,
    # Moved from OBSERVED. The elements are in the document; deciding that one
    # of them *is* a trail, or that a link goes up to the section this page
    # sits in, is not. `test_absence_claims.py` opens with what that costs:
    # a breadcrumb named only in a `data-` attribute, reported absent on 28
    # pages, all 28 wrong. A detector with a finite list of shapes stating a
    # negative is the exact reading this tier is meant to hold back.
    "no-breadcrumbs": INFERRED,
    # A word count taken both ways, and four decisions in front of it: which
    # end of the title is the brand (a punctuation split and a vote on the
    # repeated end), what counts as a word (four letters), what counts as the
    # same word (a six-character prefix), and what share counts as delivery
    # (34%). Every unit calibrated on English, which is the shape of
    # `schema-text-mismatch` and `headings-are-slogans`, and both of those are
    # inferred.
    #
    # The classification was doing damage rather than describing: the check
    # states `medium` with two sources and its cause is in `ABSENCE_CAUSES`,
    # which is exactly the combination `published_tier` promotes on an
    # observed reading - so the table was overriding the check's own certainty
    # and printing it as confident. It fired there on a homepage whose title is
    # the brand's tagline, and on a page titled "New Arrivals Footwear & Bags"
    # which is the new-arrivals grid of footwear and bags.
    "title-body-drift": INFERRED,
    # Bytes transferred before anything is visible.
    "page-weight": OBSERVED,
    # Decides that an overlay covers the content on arrival, from markup that
    # does not say when the overlay appears.
    "intrusive-interstitial": INFERRED,
    # Moved from OBSERVED, with `long-sentences` and for the same reason, plus
    # one of its own: paragraph counts are taken over the region
    # `main_text_and_region` picked as the page's content, so the number
    # depends on that region having been picked correctly. Two decisions - what
    # a sentence is, and what the content is - stand between the document and
    # the figure.
    "readability": INFERRED,
    # The field count is exact; deciding which of a page's forms is an enquiry
    # form rather than a search box, a login or a newsletter signup is not.
    "form-friction": INFERRED,
}

# Where one root cause is raised by two checks that do not obtain their
# evidence the same way. Keyed on the finding's own `id_hint` - published as
# `stable_id` - because that is the only field that separates them: the checks
# below share a cause and a skill.
#
# Short on purpose. Every entry is a claim that the cause-level answer is wrong
# for one check, and every entry names the identification that check has to
# make, or the one it does not.
#
# One entry was removed rather than flipped. `primary-navigation-is-hidden-from-
# crawlers` used to override `no-orientation` up to OBSERVED on the grounds
# that counting anchor elements is arithmetic. It is, but *which* element is
# the primary navigation is `_nav_by_shape`, and on a site whose menu predates
# the `<nav>` element that guess is the whole finding. Deleting the override
# lets the cause-level INFERRED stand, which is what a nav-identification claim
# is worth; adding an INFERRED entry here instead would have said the same
# thing twice and `test_no_override_repeats_what_its_cause_already_says` would
# have failed the build for it.
EVIDENCE_BASIS_BY_FINDING = {
    # A cover page is read straight off the delivered homepage: its character
    # count and its links, nothing decided about what the site is.
    "homepage-is-a-cover-page": OBSERVED,
    # `no-org-schema` counts every crawled page for its site-wide claim. This
    # check counts only the pages a regular expression read a street and a
    # postcode out of, and on one real site those were product codes printed
    # beside prices.
    "branch-pages-carry-no-location-markup": INFERRED,
    # `heading-structure` is an h1 element being there or not. This check
    # decides instead that an H2's vocabulary "appears nowhere else in the page
    # text", which grades wording.
    "headings-are-slogans": INFERRED,
    # `stale-content` reads dates. This check reads sentences and decides they
    # claim the copy is current as of a year.
    "copy-references-an-old-year": INFERRED,
    # ... and this one counts only pages typed as articles, so one mistyped
    # page is a whole finding about a template that does not exist.
    "published-content-has-gone-stale": INFERRED,
    # `no-date-signal` from the sitemap is arithmetic over <lastmod> values.
    # The on-page check counts only pages where a date is "expected", which is
    # the page type plus a guess about the section, so it carries the same risk
    # as the product-page count.
    "content-pages-carry-no-date": INFERRED,
    # `no-orientation` is a reading of what a page does for a visitor, except
    # here: a viewport meta tag is in the head or it is not. Nothing has to be
    # identified as anything for that to be true, which is what leaves this the
    # only survivor of the pair that used to sit here.
    "missing-mobile-viewport": OBSERVED,
}


def evidence_basis(finding):
    """OBSERVED or INFERRED for one finding: how its evidence was obtained.

    A cause nobody has classified reads as INFERRED. That case is exactly the
    one where nobody has decided, and the safe reading of "nobody decided" is
    the one that asks a person to look rather than the one that puts an
    unexamined claim in front of an owner as a certainty.
    """
    override = EVIDENCE_BASIS_BY_FINDING.get(
        finding.get("id_hint") or finding.get("stable_id"))
    if override:
        return override
    return EVIDENCE_BASIS.get(finding.get("root_cause"), INFERRED)


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


# --------------------------------------------------------------------------
# Fetching somebody else's host
#
# The marketplace's read-only promise names robots.txt, and the crawl honours
# the audited site's. Two checks also fetch URLs on *other* hosts - the
# off-site profile links a brand publishes, and the pages a link check
# confirms - and neither asked those hosts anything. Two of the six profile
# platforms disallow every automated client at the root:
#
#     www.linkedin.com   User-agent: *  Disallow: /
#     x.com              User-agent: *  Disallow: /
#
# So every run that audited a brand with a LinkedIn or X link in its footer
# sent a request those sites' own rules refuse, while the report beside it
# said robots.txt was respected. A promise with an exception nobody wrote down
# is not a promise.
#
# One robots.txt per host, fetched once and cached for the run. A host that
# has no robots.txt, or whose robots.txt cannot be read, permits everything -
# which is what the standard says and what every crawler does.
# --------------------------------------------------------------------------

def third_party_allows(fetcher, url, cache):
    """May this audit fetch `url`, according to that host's own robots.txt?

    `cache` is a dict owned by the caller, so one run asks each host once.
    Returns True when allowed, when the host has no robots.txt, or when the
    file could not be read - never blocking on our own inability to check.
    """
    from robots_parser import is_disallowed, parse_robots, path_of

    try:
        parts = urlparse(url)
    except ValueError:
        return False
    host = (parts.netloc or "").lower()
    if not host or parts.scheme not in ("http", "https"):
        return False

    if host not in cache:
        parsed = {"groups": []}
        if fetcher is not None:
            response = fetcher.try_get("{}://{}/robots.txt".format(parts.scheme, host))
            if response is not None and 200 <= response.status_code < 300:
                parsed = parse_robots(response_text(response))
        cache[host] = parsed
    parsed = cache[host]
    if not parsed.get("groups"):
        return True
    return not is_disallowed(parsed, USER_AGENT, path_of(url))


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


# Content encodings this client decodes without help. `requests` handles gzip
# and deflate on its own and needs a package for the others.
_DECODABLE_ENCODINGS = {"", "identity", "gzip", "deflate", "x-gzip"}

# How much of the body is read to decide whether it is text at all, and how
# many control bytes it may hold before it is not text.
#
# Control bytes, not printable ones. "Printable" would have to count every
# byte above 127, because a page in Cyrillic, Japanese or any other script is
# full of them - and so is compressed data, so that test passes everything.
# What separates them is the bottom of the range. Measured on this machine: a
# Brotli stream carries about 22% of its bytes below 32; decoded HTML, koi8-r
# Cyrillic and UTF-8 Japanese each carry 0%.
_TEXT_SNIFF_BYTES = 2048
_TEXT_SNIFF_CONTROL_CEILING = 0.05


def looks_like_text(content):
    """Does this body read as text rather than as compressed bytes?

    Decided on the bytes, never on the header. `Content-Encoding: br` survives
    on the response object even when urllib3 has decompressed the body
    perfectly - it is a header the server sent, not a claim about what this
    client did with it - and reading it as "we could not decode this" marked
    every page of a museum's site as unreadable while the audit held its
    decoded HTML. The report then said forty-nine times that no page returned
    HTTP 200, about eight pages that all did, and never mentioned an encoding
    anywhere. That is worse than the defect it was written to stop: the first
    version published wrong findings, this one published no findings and did
    not say why.

    So the test is the thing itself. Compressed bytes are high-entropy and
    full of control characters; a web page, in any encoding and any script,
    carries essentially none. This is
    deliberately not a check for `<html`, because an undecoded body can
    contain those five characters by chance and a valid text file may not
    contain them at all.
    """
    head = (content or b"")[:_TEXT_SNIFF_BYTES]
    if not head:
        return True
    control = sum(1 for b in bytearray(head) if b < 32 and b not in (9, 10, 13))
    return control <= len(head) * _TEXT_SNIFF_CONTROL_CEILING


def undecoded_body(response):
    """The encoding a body arrived in when nothing decoded it, else "".

    Both halves have to hold: the server named an encoding this client has no
    decoder for, and the bytes do not read as text. Either alone is wrong -
    the header survives a successful decode, and a binary body with no
    encoding header is an image served with the wrong content type, which is a
    different thing and already handled.

    Nothing in this marketplace could previously tell an unreadable response
    from a site with no content. An edge answering Brotli to a client without
    a Brotli decoder handed the crawl a page whose title was empty, whose link
    list was empty and whose body text was compressed bytes - and seventy-four
    checks read that as a site with no title, no navigation and no address,
    published fourteen findings about it, three at high severity, and printed
    a verdict saying a machine can reach and read the site.
    """
    encoding = (response.headers.get("content-encoding") or "").strip().lower()
    if encoding in _DECODABLE_ENCODINGS:
        return ""
    if looks_like_text(getattr(response, "content", b"")):
        return ""
    return encoding


def response_text(response):
    """Decode a response the way a browser would, not the way HTTP/1.1 says.

    `requests` follows the specification: a `text/*` response with no `charset`
    in the header is ISO-8859-1. Almost nothing on the web means that. A page
    served as UTF-8 that declares its encoding only in a `<meta charset>` tag -
    which is most of them - comes back through `response.text` as mojibake, and
    a brand called "El Corte Ingles" with an accent arrives with a replacement
    character embedded in its name. That then flows into the brand name, the
    evidence text and the report a person reads.

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
            text = content.decode(candidate, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        return _utf8_if_misread(content, candidate, text)
    return content.decode("utf-8", errors="replace")


# What UTF-8 looks like read one byte at a time in a Western single-byte
# encoding: a lead byte read as `Â`, `Ã` or `â`, then a continuation byte read
# as a Latin-1 symbol or one of the Windows-1252 punctuation marks. `â€˜` is a
# curly quote, `Ã©` an e with an acute, `Â ` a non-breaking space. A single-
# byte encoding accepts every byte, so decoding UTF-8 as one never fails - it
# only turns every curly quote on the page into three characters of noise,
# which then reach the brand name, the quoted evidence and the report.
_MOJIBAKE_RE = re.compile(
    u"[\u00c2\u00c3\u00e2][\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d"
    u"\u017e\u0192\u02c6\u02dc\u2013\u2014\u2018-\u201e\u2020-\u2022\u2026"
    u"\u2030\u2039\u203a\u20ac\u2122]")


def _utf8_if_misread(content, encoding, text):
    """The body as UTF-8 where the encoding it was read in turned it to noise.

    Both halves have to hold: the text read in the declared or detected
    encoding carries UTF-8's signature, and the bytes are valid UTF-8. A page
    genuinely in Windows-1252 is almost never valid UTF-8 once it holds a
    single accented letter, so the second half is what keeps this from
    rewriting a page that meant what it said.
    """
    try:
        if codecs.lookup(encoding).name.startswith("utf"):
            return text
    except LookupError:
        return text
    if not _MOJIBAKE_RE.search(text):
        return text
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return text


# How much of a body to read before deciding it is an HTML page rather than the
# file that was asked for.
#
# Was 400 characters in one place and 600 in another, and both are short. A
# page may open with a licence comment, a set of conditional comments for old
# browsers, or a base64 favicon in a `<link>` before `<html` is reached, and a
# single-page-app shell built by a bundler routinely carries several hundred
# characters of preload hints first. The body is already downloaded and in
# memory, so reading further costs nothing; missing the tag costs a confident
# finding about a file that does not exist.
HTML_SNIFF_CHARS = 4000

# Only the two tags an HTML page must open with. `<head`, `<body` and `<meta`
# were tried and taken out again: this same test decides whether `/llms.txt` or
# `/agents.md` is a file, and a Markdown document explaining how to mark a page
# up quotes those tags in its first screenful. `<html` appears in every HTML
# page and in almost nothing else.
HTML_PAGE_MARKERS = ("<html", "<!doctype html")


def response_is_an_html_page(response):
    """Is this response a page a browser renders, rather than the file asked for?

    Two independent tests, because either one alone has been wrong here. The
    `Content-Type` the server sets is the site's own statement about what it
    just sent and settles it on its own - a sitemap or `/llms.txt` address
    answering `text/html` is not serving that file, whatever the body looks
    like. The body sniff catches the servers that label an HTML page
    `text/plain` or `text/xml`, or send no type at all.

    Measured on a temple whose server answers every unknown path with its
    homepage: 200, `text/html`, 1.8 MB, byte for byte the homepage. Reading the
    status alone published a file the site does not have.

    Here rather than in a skill because two skills ask it. `crawl.py` decides
    whether a sitemap record or `/llms.txt` is a page; `crawl-access-audit`
    decides whether a sitemap that would not parse and an announced
    `/agents.md` are pages. Both carried their own byte-equivalent copy, and
    two spellings of one rule is the drift every other shared definition in
    this file exists to prevent.
    """
    headers = getattr(response, "headers", None) or {}
    declared = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if declared in ("text/html", "application/xhtml+xml"):
        return True
    head = response_text(response)[:HTML_SNIFF_CHARS].lower()
    return any(marker in head for marker in HTML_PAGE_MARKERS)


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
# Two-letter segments that are almost never a language edition. `id` left
# this set: it is the ISO code for Indonesian, and a site was found
# serving `/en`, `/id` and `/jp` whose Indonesian edition was invisible
# here, so the check that reports a multi-locale site with no `hreflang`
# saw one edition and declined. The `/id/123` identifier shape it was
# guarding against is handled by the rule below instead: an edition has to
# be corroborated, and one page under a numeric path never is.
_NOT_A_LOCALE = frozenset({"us", "uk", "eu", "ac", "co", "hr", "pr", "tv"})

# Three-letter language codes as editions write them: ISO 639-2 (`/eng`,
# `/jpn`, `/kor`, `/deu`) and the commonest informal forms beside them (`/chn`,
# `/esp`), in either case. A national museum serves `/ENG`, `/JPN` and `/CHN`
# beside its Korean pages, none of them was read as an edition, and the
# navigation check compared each translated menu with the Korean one and
# reported the editions as pages without the site's normal navigation.
#
# A list, not a shape. Three letters are what ordinary sections are called -
# `/api`, `/faq`, a hotel's `/spa`, a newspaper's `/pol` - so only codes that
# name nothing else are here, and the corroboration a two-letter code needs
# applies to these too.
_THREE_LETTER_LANGUAGES = {
    "eng": "en", "jpn": "ja", "kor": "ko", "chn": "zh", "zho": "zh",
    "cht": "zh", "chs": "zh", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de",
    "esp": "es", "ita": "it", "por": "pt", "rus": "ru", "ara": "ar", "tha": "th",
    "vie": "vi", "vnm": "vi", "idn": "id", "nld": "nl", "dut": "nl", "heb": "he",
    "hin": "hi", "urd": "ur", "fas": "fa", "swe": "sv", "ukr": "uk", "ell": "el",
    "ces": "cs", "cze": "cs", "hun": "hu", "ron": "ro", "khm": "km", "msa": "ms",
}


def locale_language(code):
    """The language an edition's path code names: `en-gb` -> `en`, `ENG` -> `en`."""
    base = primary_subtag(code)
    return _THREE_LETTER_LANGUAGES.get(base, base)


def _locale_candidate(url):
    """The leading path segment, if it is shaped like a language code."""
    path = urlparse(url).path
    first = path.strip("/").split("/")[0].lower() if path.strip("/") else ""
    if not first or first in _NOT_A_LOCALE:
        return ""
    if first in _THREE_LETTER_LANGUAGES:
        return first
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
        # A skipped record - a PDF, an image - is not a page of any edition.
        if page.get("status") != 200 or page.get("skipped"):
            continue
        candidate = _locale_candidate(page.get("url") or "")
        if candidate:
            candidates[page["url"]] = candidate

    if not candidates:
        return {}

    self_declared = set()
    for page in pages or []:
        candidate = candidates.get(page.get("url"))
        if candidate and primary_subtag(page.get("lang")) == locale_language(candidate):
            self_declared.add(candidate)

    distinct = set(candidates.values())
    # Where some editions declare their language and others do not, all of them
    # are kept - not only the declaring ones.
    #
    # The old reading was `self_declared if self_declared else ...`, which threw
    # away every edition that had not matched. The shape it
    # fails on: a site serving `/en`, `/id` and `/jp`, whose Japanese pages
    # correctly declare `lang="ja"`. The segment and the code differ - `jp` is
    # the country, `ja` is the language, and writing the segment that way is
    # ordinary - so `/jp/` was dropped, the site read as one edition, and the
    # `hreflang` check that exists for exactly this site declined.
    #
    # A declaration anywhere is evidence the *site* is divided by language, and
    # that is the question being asked. Once one edition has proved it, a
    # sibling segment of the same shape is an edition too.
    trusted = distinct if (self_declared or len(distinct) > 1) else set()
    if not trusted:
        return {}
    editions = {url: code for url, code in candidates.items() if code in trusted}
    # The two country codes `_NOT_A_LOCALE` holds back, once the site has shown
    # it is divided by edition and the page itself declares that very region. A
    # furniture retailer serves one storefront per country - `/au`, `/ca`,
    # `/sg`, `/uk`, `/us` - and the last two were read as sections of some
    # other edition, so their menus were compared against a different
    # country's. `uk` is written `GB` in a language tag; see
    # `_REGION_SUBTAG_ALIASES`.
    for page in pages or []:
        if page.get("status") != 200 or page.get("skipped"):
            continue
        url = page.get("url") or ""
        path = urlparse(url).path.strip("/")
        first = path.split("/")[0].lower() if path else ""
        if first not in ("us", "uk"):
            continue
        region = str(page.get("lang") or "").strip().lower().replace("_", "-").split("-")[1:]
        if region and region[-1] == _REGION_SUBTAG_ALIASES.get(first, first):
            editions[url] = first
    return editions


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


# Writing systems in which no English page is written. `guess_language_from
# _text` above scores Latin function words and can only ever answer with a
# Latin-alphabet language, so on a page of Cyrillic it either returns nothing
# or - when a handful of Latin words survive in the markup - returns whichever
# Latin language those stray words happened to score, and the caller then ran
# the English matchers on Russian prose.
#
# The script a page is written in is a harder fact than the language it
# declares. A site chooses what to put in `lang` and a 1990s template usually
# puts nothing; the letters are the letters either way. These nine are
# unambiguous - none of them writes English - so text overwhelmingly in one of
# them settles the only question the prose gate actually asks.
NON_LATIN_PROSE_SCRIPTS = frozenset({
    "cyrillic", "greek", "han", "kana", "hangul", "arabic", "hebrew",
    "devanagari", "thai",
})


def language_sample(pages):
    """The text the language detector reads: the opening of the first pages."""
    return " ".join((p.get("body_text") or "")[:2000] for p in (pages or [])[:5])


# What writing system each language is normally written in, for one question
# only: do the letters on a site's pages agree with the `lang` it declares?
#
# The test used to be asked of English alone - a site declaring `lang="en"` and
# writing in a non-Latin script - because that was the case in front of us. A
# site declaring `lang="ar"` and writing in Cyrillic is the same fault and was
# invisible, and so is every other pairing. The map is what makes the question
# askable in general, and a language it does not list produces no claim at all.
#
# Japanese takes two, because a Japanese page mixes kana and han and
# `dominant_script` returns whichever holds more letters on that particular
# sample. Chinese takes one. Where a language really is written in several
# scripts by different communities - Serbian, Azerbaijani, Uzbek, Kurdish,
# Punjabi - every script it uses is listed, so this can never call one
# community's spelling of its own language a mistake.
LANGUAGE_SCRIPTS = {
    "en": ("latin",), "fr": ("latin",), "de": ("latin",), "es": ("latin",),
    "pt": ("latin",), "it": ("latin",), "nl": ("latin",), "sv": ("latin",),
    "no": ("latin",), "nb": ("latin",), "nn": ("latin",), "da": ("latin",),
    "fi": ("latin",), "is": ("latin",), "pl": ("latin",), "cs": ("latin",),
    "sk": ("latin",), "hu": ("latin",), "ro": ("latin",), "hr": ("latin",),
    "sl": ("latin",), "lt": ("latin",), "lv": ("latin",), "et": ("latin",),
    "tr": ("latin",), "vi": ("latin",), "id": ("latin",), "ms": ("latin",),
    "tl": ("latin",), "sw": ("latin",), "af": ("latin",), "ca": ("latin",),
    "gl": ("latin",), "eu": ("latin",), "cy": ("latin",), "ga": ("latin",),
    "sq": ("latin",), "mt": ("latin",), "zu": ("latin",), "xh": ("latin",),
    "ha": ("latin",), "yo": ("latin",), "ig": ("latin",), "so": ("latin",),
    "ru": ("cyrillic",), "uk": ("cyrillic",), "bg": ("cyrillic",),
    "be": ("cyrillic",), "mk": ("cyrillic",), "kk": ("cyrillic",),
    "ky": ("cyrillic",), "mn": ("cyrillic",), "tg": ("cyrillic",),
    "sr": ("cyrillic", "latin"), "az": ("latin", "cyrillic", "arabic"),
    "uz": ("latin", "cyrillic"), "ku": ("latin", "arabic"),
    "pa": ("devanagari", "arabic"),
    "el": ("greek",),
    "he": ("hebrew",), "yi": ("hebrew",),
    "ar": ("arabic",), "fa": ("arabic",), "ur": ("arabic",), "ps": ("arabic",),
    "sd": ("arabic",), "ckb": ("arabic",),
    "hi": ("devanagari",), "mr": ("devanagari",), "ne": ("devanagari",),
    "sa": ("devanagari",),
    "th": ("thai",),
    "zh": ("han",), "ja": ("kana", "han"), "ko": ("hangul",),
}

# How lopsided the letters have to be before a declaration is called wrong.
#
# `dominant_script` answers with a plurality, and a plurality is not enough to
# tell a site its own `lang` is a mistake. A Japanese shop whose product codes
# and brand names are written in Latin can have Latin as its commonest script
# on a short sample while being a Japanese site that correctly says so.
#
# The first version guarded that with one absolute rule in both directions -
# the declared language's script must hold under a tenth of the letters - and
# that was measured to cost this much. A Thai Buddhist temple serves `lang="en"` on
# 57 of 60 crawled pages over Thai prose, and its sample counts
# `{'thai': 3616, 'latin': 2146, 'han': 73}` of 5835 letters. Thai is 62% and
# clears the share test; Latin is 37%, so the ceiling bailed out and the check
# written for exactly this site could not fire on it. Any site with a language
# switcher, an English section or a Latin-script brand name in its nav clears
# a tenth.
#
# What separates the temple from the Japanese shop is not how much Latin there
# is. It is which script is doing the outnumbering, because the two scripts are
# not symmetrical evidence:
#
#   Latin turns up on sites of every language and means nothing on its own.
#   Product codes, brand names, URLs printed as text, a nav item reading "EN",
#   loanwords and untranslated boilerplate all leave Latin letters behind on a
#   site that is not in a Latin-script language at all. So when Latin is the
#   script that leads, it may be doing so as noise, and only the near-absence
#   of the declared language's own script - the original ceiling - is evidence
#   the declaration is wrong.
#
#   A non-Latin script does not turn up as noise. Nothing about publishing an
#   English page produces 3,616 Thai letters. So when the leading script is
#   non-Latin, the comparison that decides it is the ratio between the two
#   scripts in question: the script the site is written in against the script
#   its declaration implies. On the temple that ratio is 3616:2146, or 1.7 to
#   one. Below `SCRIPT_CONTRADICTION_RATIO` the answer stays "cannot tell",
#   which is what a genuinely bilingual site gets.
#
# Both directions still refuse a bare plurality, which is the property
# `tests/test_script_and_attribution.py` holds: Arabic declared over a sample
# that is 124 Latin letters to 48 Arabic ones is Latin leading 2.6 to one and
# is still no finding, because Latin leading proves nothing and 28% Arabic is
# nowhere near absent.
SCRIPT_CONTRADICTION_SHARE = 0.6
SCRIPT_CONTRADICTION_CEILING = 0.1
# Half again as many letters as the declared script has. At the share test's
# own 60/40 boundary the ratio is already 1.5, so this never contradicts it; it
# is what carries the decision on a sample with three scripts in it, where an
# absolute share is measured against a total that includes letters belonging to
# neither side of the question.
SCRIPT_CONTRADICTION_RATIO = 1.5


def script_contradicts_language(code, sample):
    """The script this text is written in, when the declared language rules it out.

    Returns the observed script's name, or `""` where there is no contradiction
    to report - including where the language is one this map does not know, and
    where the letters are too mixed to be sure.
    """
    expected = LANGUAGE_SCRIPTS.get(primary_subtag(code) or "")
    if not expected:
        return ""
    counts = script_counts(sample or "")
    total = sum(counts.values())
    if total < SCRIPT_SAMPLE_MINIMUM:
        return ""
    observed = max(sorted(counts), key=counts.get)
    if observed in expected:
        return ""
    if counts[observed] < total * SCRIPT_CONTRADICTION_SHARE:
        return ""
    declared_letters = sum(counts.get(s, 0) for s in expected)
    if observed == "latin":
        # Latin leading is not evidence by itself - see the block above - so
        # this direction keeps the original test: the declared script has to be
        # all but absent before the declaration is called wrong.
        return observed if declared_letters <= total * SCRIPT_CONTRADICTION_CEILING else ""
    if counts[observed] < declared_letters * SCRIPT_CONTRADICTION_RATIO:
        return ""
    return observed


def site_script(pages):
    """The writing system most of this crawl's text is in, "" if undecided.

    "" for a crawl with too few letters to characterise, which is
    `dominant_script`'s own answer and not a claim that the site is Latin.
    """
    return dominant_script(language_sample(pages)) or ""


def _front_door_languages(pages):
    """(the homepage's declared language, the `x-default` edition's), "" where unknown.

    The homepage is the page at `/`. The `x-default` edition is the address
    any page's `hreflang` set names as the default, read back through the
    crawled page at that address.
    """
    by_url, home, default_url = {}, "", ""
    for page in pages or []:
        if page.get("status") != 200 or page.get("skipped"):
            continue
        code = primary_subtag(page.get("lang"))
        for address in (page.get("url"), page.get("final_url")):
            if address and code:
                by_url.setdefault(address.rstrip("/"), code)
        try:
            parsed = urlparse(page.get("url") or "")
        except ValueError:
            continue
        if code and not home and (parsed.path or "/") == "/" and not parsed.query:
            home = code
        for alternate in page.get("hreflang") or []:
            if (not default_url and isinstance(alternate, dict)
                    and str(alternate.get("hreflang") or "").lower() == "x-default"):
                default_url = str(alternate.get("url") or "")
    return home, (by_url.get(default_url.rstrip("/"), "") if default_url else "")


def detect_site_language(pages):
    """The language of the site, and how we know.

    Declared `lang` attributes first, because a site stating its own language is
    better evidence than anything inferred from its words. Text only decides
    when nothing is declared.

    The writing system overrules both for the one question this record is
    consulted about. A site whose text is overwhelmingly Cyrillic, Greek, Han,
    Kana, Hangul, Arabic, Hebrew, Devanagari or Thai is not a site the English
    matchers may be run against, whether it declares `lang="en"`, declares
    nothing, or declares something the function-word scorer then mistook.

    Naming the script is not naming the language: Cyrillic is Russian,
    Ukrainian, Bulgarian and Serbian, and this returns no `code` for it rather
    than picking one. `script` carries what is actually known.
    """
    sample = language_sample(pages)
    script = dominant_script(sample) or ""
    non_latin = script in NON_LATIN_PROSE_SCRIPTS

    declared, first_seen = {}, {}
    for index, page in enumerate(pages or []):
        if page.get("status") != 200 or page.get("skipped"):
            continue
        code = primary_subtag(page.get("lang"))
        if code:
            declared[code] = declared.get(code, 0) + 1
            first_seen.setdefault(code, index)
    if declared:
        # A tie is broken by what the site says its front door is, never by
        # the alphabet. An English-first site serving one page per language
        # had `/` and `/en` in English and `/pt` and `/pt-br` in Portuguese:
        # two each, `pt` sorted last and won, and four English prose checks
        # were skipped on an English site "because it is in PT". The homepage
        # declares its language, and the `x-default` edition is the one the
        # site offers a visitor it knows nothing about; after those, the
        # language the crawl met first.
        home_code, default_code = _front_door_languages(pages)
        code = max(declared, key=lambda k: (declared[k], k == home_code,
                                             k == default_code, -first_seen[k]))
        source = "declared"
        # The declaration and the letters disagree, and the letters win. Asked
        # of every language rather than of English alone: a site declaring
        # `lang="ar"` while writing in Cyrillic is the same fault, and until
        # `script_contradicts_language` existed it was invisible because the
        # test named one language.
        contradicting = script_contradicts_language(code, sample)
        if contradicting:
            source = ("declared as {}, contradicted by page text written in the {} "
                      "script".format(code, contradicting))
        elif script == "latin":
            # Latin leading is not evidence against a declaration - see the
            # block above `SCRIPT_CONTRADICTION_SHARE` - and the test just run
            # found the declared script holding more than a trace of the
            # letters. So the record names the declared script, and a report
            # reading it cannot print "written in the latin script" over a
            # declaration this function has just accepted.
            expected = LANGUAGE_SCRIPTS.get(code) or ()
            counts = script_counts(sample)
            held = [name for name in expected if counts.get(name)]
            if held and "latin" not in expected:
                script = max(sorted(held), key=counts.get)
        return {"code": code, "source": source, "script": script,
                "pages_declaring": sum(declared.values()),
                "prose_checks_apply": code == PROSE_LANGUAGE and not non_latin}

    if non_latin:
        # No `code`, deliberately. The script is known and the language is not,
        # and a caller printing a guessed `en` here would have a site owner
        # stamp `lang="en"` on pages that are not in English - worse than the
        # missing attribute the same report is asking them to fix.
        return {"code": "", "script": script,
                "source": "not declared; read from the writing system of the page text",
                "pages_declaring": 0, "prose_checks_apply": False}

    guessed = guess_language_from_text(sample)
    if guessed:
        return {"code": guessed, "source": "inferred from the page text",
                "script": script, "pages_declaring": 0,
                "prose_checks_apply": guessed == PROSE_LANGUAGE}
    # Nothing declared and nothing clear in the text. Proceed as English, which
    # is what every earlier version did unconditionally, but record that it is
    # an assumption so a reader can discount the prose findings if it is wrong.
    return {"code": "", "source": "not declared and not inferable",
            "script": script, "pages_declaring": 0, "prose_checks_apply": True}


def language_of(snapshot):
    """The language record a check should consult. Always returns a dict."""
    value = snapshot.get("site_language")
    if isinstance(value, dict) and value:
        return value
    return detect_site_language(snapshot.get("pages") or [])


# --------------------------------------------------------------------------
# A date the page did not write in the Gregorian calendar, and a founding year
# it did not write in English
#
# Two sites, measured, told they carry facts they print on every
# page.
#
#   a Thai temple      Every article prints its date in the Buddhist era:
#                      "43932 VIEW | 08/12/2561", "72878 VIEW | 07/12/2561",
#                      "224144 VIEW | 31/03/2563". พ.ศ. 2561 is 2018 CE. Every
#                      date pattern in this repository anchors on
#                      `(?:19|20)\d{2}`, so 2561 is not a year to any of them,
#                      and the report said "Pages carrying no date a reader or
#                      a machine could use - nothing in the visible text, and
#                      no usable datePublished, dateModified or `<time>`
#                      value."
#
#   a Norwegian site   Prints "( Fra 07.11.2004)" directly under its own name.
#                      The founding vocabulary is `since`, `founded`,
#                      `established` and their neighbours, all English, so the
#                      report said "no founding year appears in the page text
#                      or in the page markup".
#
# Neither of these is a general date library and neither should become one.
# What is added is what was measured: one calendar, and the founding words of
# the languages the crawl already names in `crawl.py::_is_title_boilerplate`.
#
# **A wrongly converted date is worse than an unread one.** A page printing
# `31/03/2563` in a Latin-script European language is printing something that
# is not a year at all, and subtracting 543 from it would publish an invented
# date as the site's own. So the conversion is refused unless the page's own
# script or its declared language says which calendar it is in - the same rule
# `script_contradicts_language` follows, for the same reason.
# --------------------------------------------------------------------------

# พ.ศ. (Buddhist Era) runs 543 years ahead of CE.
BUDDHIST_ERA_OFFSET = 543
# The window in which a four-digit number is a Buddhist-era year and cannot be
# anything else: 2400 BE is 1857 CE and 2600 BE is 2057 CE. Below 2400 the
# number is more likely a quantity; above 2600 it is a number, not a date.
BUDDHIST_ERA_MIN = 2400
BUDDHIST_ERA_MAX = 2600

# Where a page says which calendar it is using in words. `พ.ศ.` is the era's
# own abbreviation and `ปี` the word for "year".
THAI_ERA_MARKER_RE = re.compile(u"พ\\.?ศ\\.?|ปี")

# The shape the temple's dates are written in: day, month, Buddhist-era year.
# Both separators, because a Thai page writes either.
BUDDHIST_ERA_DATE_RE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](2[4-6]\d{2})\b")


def reads_in_a_buddhist_era_calendar(script="", language=""):
    """May a four-digit year on this page be read as a Buddhist-era year?

    Only where the page itself says so, in one of the two ways that need
    nothing from us: the writing system its prose is in, or the language it
    declares. Thailand is the only country that dates its ordinary web pages
    this way, so Thai script or `lang="th"` is the whole of the evidence and
    the absence of both is a refusal rather than a maybe.
    """
    return script == "thai" or primary_subtag(language) == "th"


def gregorian_year(year, script="", language=""):
    """A four-digit year read off a page, in the common era.

    Returns the year unchanged wherever the conversion cannot be justified -
    an out-of-window number, a page that is not Thai - so a caller may run
    every year it finds through this without having to know which ones need it.
    """
    try:
        value = int(str(year).strip())
    except (TypeError, ValueError):
        return None
    if (reads_in_a_buddhist_era_calendar(script, language)
            and BUDDHIST_ERA_MIN <= value <= BUDDHIST_ERA_MAX):
        return value - BUDDHIST_ERA_OFFSET
    return value


def buddhist_era_dates(text, script="", language=""):
    """Every `d/m/BE` date in this text, as `(what the page printed, CE year)`.

    Empty on any page this may not be read of, which is every page that is not
    written or declared in Thai.
    """
    if not reads_in_a_buddhist_era_calendar(script, language):
        return []
    found = []
    for match in BUDDHIST_ERA_DATE_RE.finditer(str(text or "")):
        year = int(match.group(3))
        if BUDDHIST_ERA_MIN <= year <= BUDDHIST_ERA_MAX:
            found.append((match.group(0), year - BUDDHIST_ERA_OFFSET))
    return found


# The other shape a Thai page dates itself in, as real pages print it:
# a day, the month written out in Thai, and the era year - `28 สิงหาคม 2569` -
# or the era's own abbreviation in front of the year, `พ.ศ. 2569`. Fourteen
# pages of a government site and sixteen of a school printed these and every
# one was recorded as carrying no date at all, in a report that quoted one of
# them in its own citation table two paragraphs above the finding.
#
# This one needs no `script` argument and cannot have a useful one: the Thai
# letters immediately in front of the year are the evidence, in the match
# itself, so it reads the same on a page whose prose is mostly English as on
# one that declares `lang="th"`. Nothing here reads a bare four-digit number.
# Two Thai letters with optional full stops is the abbreviation (`พ.ศ.`, `ปี`);
# three or more is a word, which is what a month name is.
#
# Which Thai words may stand in front of the year is a closed list, not "any
# Thai letters". The open version read `\u0e23\u0e32\u0e04\u0e32 2500 \u0e1a\u0e32\u0e17` ("price 2,500 baht") as
# the year 2500 BE, 1957, and `\u0e08\u0e33\u0e19\u0e27\u0e19 2450 \u0e40\u0e25\u0e48\u0e21` ("2,450 volumes") as 1907 - the
# two-letter branch matched the last two letters of a longer word. A year
# follows the era mark, the word for "year", or a month name, full or
# abbreviated, and never runs straight into a currency or a counting word.
_THAI_MONTHS = (
    u"\u0e21\u0e01\u0e23\u0e32\u0e04\u0e21", u"\u0e01\u0e38\u0e21\u0e20\u0e32\u0e1e\u0e31\u0e19\u0e18\u0e4c", u"\u0e21\u0e35\u0e19\u0e32\u0e04\u0e21", u"\u0e40\u0e21\u0e29\u0e32\u0e22\u0e19", u"\u0e1e\u0e24\u0e29\u0e20\u0e32\u0e04\u0e21", u"\u0e21\u0e34\u0e16\u0e38\u0e19\u0e32\u0e22\u0e19",
    u"\u0e01\u0e23\u0e01\u0e0e\u0e32\u0e04\u0e21", u"\u0e2a\u0e34\u0e07\u0e2b\u0e32\u0e04\u0e21", u"\u0e01\u0e31\u0e19\u0e22\u0e32\u0e22\u0e19", u"\u0e15\u0e38\u0e25\u0e32\u0e04\u0e21", u"\u0e1e\u0e24\u0e28\u0e08\u0e34\u0e01\u0e32\u0e22\u0e19", u"\u0e18\u0e31\u0e19\u0e27\u0e32\u0e04\u0e21")
_THAI_MONTH_ABBREVIATIONS = (
    u"\u0e21.\u0e04.", u"\u0e01.\u0e1e.", u"\u0e21\u0e35.\u0e04.", u"\u0e40\u0e21.\u0e22.", u"\u0e1e.\u0e04.", u"\u0e21\u0e34.\u0e22.", u"\u0e01.\u0e04.", u"\u0e2a.\u0e04.",
    u"\u0e01.\u0e22.", u"\u0e15.\u0e04.", u"\u0e1e.\u0e22.", u"\u0e18.\u0e04.")
_THAI_ERA_WORDS = (u"\u0e1e.\u0e28.", u"\u0e1e.\u0e28", u"\u0e1e\u0e28.", u"\u0e1e\u0e28", u"\u0e1b\u0e35")
# Currency and counting words: a number in front of one is an amount.
_THAI_AMOUNT_WORDS = (
    u"\u0e1a\u0e32\u0e17", u"\u0e40\u0e25\u0e48\u0e21", u"\u0e04\u0e19", u"\u0e0a\u0e34\u0e49\u0e19", u"\u0e23\u0e32\u0e22\u0e01\u0e32\u0e23", u"\u0e40\u0e23\u0e37\u0e48\u0e2d\u0e07", u"\u0e2b\u0e19\u0e49\u0e32", u"\u0e04\u0e23\u0e31\u0e49\u0e07",
    u"\u0e41\u0e2b\u0e48\u0e07", u"\u0e23\u0e32\u0e22", u"\u0e09\u0e1a\u0e31\u0e1a", u"\u0e15\u0e31\u0e27", u"\u0e40\u0e04\u0e23\u0e37\u0e48\u0e2d\u0e07", u"\u0e01\u0e34\u0e42\u0e25", u"\u0e40\u0e21\u0e15\u0e23", u"\u0e25\u0e49\u0e32\u0e19", u"\u0e1e\u0e31\u0e19")
THAI_ERA_YEAR_RE = re.compile(
    u"(?:\\d{1,2}\\s*)?"
    u"(?<![\u0e00-\u0e7f])(?:" + u"|".join(
        re.escape(word) for word in sorted(
            _THAI_MONTHS + _THAI_MONTH_ABBREVIATIONS + _THAI_ERA_WORDS,
            key=len, reverse=True)) + u")"
    u"\\s*(2[4-6]\\d{2})(?!\\d)"
    u"(?!\\s*(?:" + u"|".join(re.escape(word) for word in _THAI_AMOUNT_WORDS) + u"))")

# The Japanese era years, and the common-era year each era began in. Same rule:
# the era name is in the string, so nothing outside the match is consulted.
# `元年` is the first year of an era and is written for it rather than `1`.
JAPANESE_ERA_START_YEARS = {
    u"\u4ee4\u548c": 2019,   # Reiwa
    u"\u5e73\u6210": 1989,   # Heisei
    u"\u662d\u548c": 1926,   # Showa
    u"\u5927\u6b63": 1912,   # Taisho
}
JAPANESE_ERA_DATE_RE = re.compile(
    u"(\u4ee4\u548c|\u5e73\u6210|\u662d\u548c|\u5927\u6b63)"
    u"\\s?(\u5143|\\d{1,2})\\s?\u5e74"
    u"(?:\\s?\\d{1,2}\\s?\u6708(?:\\s?\\d{1,2}\\s?\u65e5)?)?")

# Nothing an era converts to may be later than this. `令和8年` is 2026 and
# `令和99年` is somebody's product code, not a date eighty years out.
ERA_YEAR_CEILING = 2100


def thai_era_dates(text):
    """Every Thai-script Buddhist-era date here, as `(printed, CE year)`."""
    found = []
    for match in THAI_ERA_YEAR_RE.finditer(str(text or "")):
        year = int(match.group(1))
        if BUDDHIST_ERA_MIN <= year <= BUDDHIST_ERA_MAX:
            found.append((match.group(0).strip(), year - BUDDHIST_ERA_OFFSET))
    return found


def japanese_era_dates(text):
    """Every 令和/平成/昭和/大正 date here, as `(printed, CE year)`."""
    found = []
    for match in JAPANESE_ERA_DATE_RE.finditer(str(text or "")):
        start = JAPANESE_ERA_START_YEARS.get(match.group(1))
        if start is None:
            continue
        number = 1 if match.group(2) == u"\u5143" else int(match.group(2))
        year = start + number - 1
        if number < 1 or year > ERA_YEAR_CEILING:
            continue
        found.append((match.group(0).strip(), year))
    return found


# The Hijri calendar, which used to return nothing for every shape tried on
# an Arabic news agency's pages.
#
# Bounds rather than a formula's whole range: 1300 AH is 1882 CE and 1500 AH is
# 2076 CE, so anything outside that is not a date on a page anybody is auditing.
_HIJRI_YEAR_MIN, _HIJRI_YEAR_MAX = 1300, 1500

# A Hijri year is 354 or 355 days, so the two calendars slide against each
# other and one Hijri year straddles two Gregorian ones. The standard
# conversion is `CE = AH * 0.970224 + 621.5774`, and it is accurate to the year
# it starts in.
#
# That imprecision is why this reads the *year* and nothing finer. Everything
# downstream wants "roughly how old is this page", and the marketplace's rule -
# a wrongly converted date is worse than an unread one - forbids inventing a
# month from a formula that cannot supply one.
_HIJRI_TO_CE_RATE = 0.970224
_HIJRI_TO_CE_OFFSET = 621.5774

# Arabic-Indic digits, so `١٤٤٧` is read as well as `1447`.
# Two digit sets, not one. `\u0660-\u0669` is Arabic-Indic, what an Arabic page
# prints; `\u06f0-\u06f9` is the Extended set, what Persian, Dari, Pashto and Urdu
# pages print. They are different codepoints for the same ten digits, and
# reading only the first set meant a Persian `\u06f1\u06f4\u06f4\u06f7` returned nothing while an
# Arabic `\u0661\u0664\u0664\u0667` read fine - so the Iranian and Afghan web was invisible to a
# reader whose own comment claimed to cover it.
_EASTERN_DIGITS = {}
for _base in (0x0660, 0x06F0):
    for _offset in range(10):
        _EASTERN_DIGITS[chr(_base + _offset)] = str(_offset)
del _base, _offset

# Every digit either set uses, as a character-class body.
_ANY_DIGIT = r"0-9\u0660-\u0669\u06f0-\u06f9"

# The year has to be marked as Hijri by the page itself. `هـ` and `ه‍` are the
# abbreviation an Arabic page writes after the year, and the spelled-out forms
# are what a page writes when it has room. Without one of these a four-digit
# number in that range is just a number - `1447` is a street address, a product
# code and a price as often as it is a year.
# The year, then optionally the rest of a numeric date, then the mark. Both
# orders occur: `1447\u0647\u0640` writes the mark straight onto the year, and
# `1447/03/15 \u0647\u0640` puts the whole date first and marks the lot. Reading only
# the first shape missed the second, which is the one a real page used.
#
# Two calendars share this shape and they are 42 years apart, so the mark has
# to be read to its end. `\u0647\u0640.\u0634` is *shamsi*, the solar Hijri calendar every
# Iranian site dates itself in; `\u0647\u0640.\u0642` is *qamari*, the lunar one an Arabic
# page means by a bare `\u0647\u0640`. Reading the first two characters and stopping
# converted the Persian `1404 \u0647\u0640.\u0634` - March 2025 - to 1983, and a wrongly
# converted date is worse than an unread one. The lookaheads below refuse the
# solar mark here; `_JALALI_MARKED_RE` reads it with its own arithmetic.
_YEAR_THEN_DATE = (
    r"([" + _ANY_DIGIT + r"]{4})"
    r"(?:\s*[/.-]\s*[" + _ANY_DIGIT + r"]{1,2}){0,2}\s*")

# `\u0634` is the shamsi mark and `\u0642` the qamari one. A tatweel, a zero-width
# joiner, a full stop or an Arabic decimal separator can sit between the
# `\u0647` and either letter, and pages use all of them.
_SOLAR_MARK = r"[\u0640\u200d]?\s*[.\u066b]?\s*\u0634"

# One letter, three codepoints. Arabic writes the final yeh `\u064a`, Persian
# writes `\u06cc`, and Egyptian and Levantine pages often write the dotless
# `\u0649` for the same letter. `\u0647\u062c\u0631\u064a` and `\u0647\u062c\u0631\u06cc` are the same word, so a class
# rather than one codepoint - matching only the Arabic form read Arabic sites
# and skipped Iranian ones spelling the identical word.
_YEH = r"[\u064a\u06cc\u0649]"

# Every alternative joined with an explicit `+`. Two of them end in `_YEH` and
# the line after each opens a new literal, which Python would otherwise glue on
# by implicit concatenation - a silent change of meaning, and here a syntax
# error rather than a wrong pattern only because of where the parentheses fell.
_HIJRI_MARKED_RE = re.compile(
    _YEAR_THEN_DATE
    + r"(?:\u0647\u0640(?!" + _SOLAR_MARK + r")"
    + r"|\u0647\u200d(?!" + _SOLAR_MARK + r")"
    + r"|\u0647\u062c\u0631" + _YEH + r"\u0629"
    + r"|\u0647\u062c\u0631" + _YEH + r"(?!\s*\u0634)"
    + r"|\u0642\u0645\u0631" + _YEH
    + r"|\u0647(?![\u0600-\u06ff]|" + _SOLAR_MARK + r"))")

# The solar Hijri calendar, which Iran and Afghanistan date everything in - and
# which no reader here covered, so an Iranian site's dates were either unread
# or read as lunar and reported 42 years early.
#
# Its new year is the March equinox, so a Jalali year runs across two Gregorian
# ones and `+ 621` is the one it starts in. That is the same promise the lunar
# reader makes and the same reason neither returns a month.
_JALALI_TO_CE_OFFSET = 621

_JALALI_MARKED_RE = re.compile(
    _YEAR_THEN_DATE
    + r"(?:\u0647" + _SOLAR_MARK
    + r"|\u0647\u062c\u0631" + _YEH + r"\s*\u0634\u0645\u0633" + _YEH
    + r"|\u0634\u0645\u0633" + _YEH + r")")


def _arabic_digits(text):
    return "".join(_EASTERN_DIGITS.get(ch, ch) for ch in text)


def _lunar_hijri_year(year):
    return int(year * _HIJRI_TO_CE_RATE + _HIJRI_TO_CE_OFFSET)


def _solar_hijri_year(year):
    return year + _JALALI_TO_CE_OFFSET


def hijri_dates(text):
    """Every Hijri year marked as one here, as `(printed, CE year)`.

    Both Hijri calendars. The lunar one is what an Arabic page means by a bare
    `\u0647\u0640`; the solar one is what an Iranian page marks `\u0647\u0640.\u0634`. They share a
    year number and stand 42 years apart, so which mark the page printed
    decides the arithmetic, and a number carrying neither mark is read as
    neither calendar.

    The year only. See `_HIJRI_TO_CE_RATE`: a lunar Hijri year straddles two
    Gregorian ones, so the month a formula would produce is not a fact this
    audit has.
    """
    found = []
    for pattern, convert in ((_HIJRI_MARKED_RE, _lunar_hijri_year),
                             (_JALALI_MARKED_RE, _solar_hijri_year)):
        for match in pattern.finditer(str(text or "")):
            try:
                year = int(_arabic_digits(match.group(1)))
            except ValueError:
                continue
            if not (_HIJRI_YEAR_MIN <= year <= _HIJRI_YEAR_MAX):
                continue
            found.append((match.group(0).strip(), convert(year)))
    return found


# --------------------------------------------------------------------------
# A numeric date, read in the order the page's language writes it
#
# A Vietnamese cosmetics shop dates every post "06.05.26" and one post
# "09.05.26"; a Vietnamese date can also be written "ngày 6 tháng 5 năm 2026".
# Neither shape was read, so the site's 2026 posts carried no date and its
# three English-language posts from 2022 were the newest it had: "3 of 3 dated
# articles are older than 18 months, and the newest is 2022-11-01". The same
# shop's product pages print "03/09/2026" for the third of September, which a
# reader that assumes month first turns into the ninth of March.
#
# Which number is the day is a fact about the language, not the string, and
# that is what this reads: day first where the number over 12 says so, or
# where the page's declared language writes dates day first; month first on a
# slash date otherwise, which is the convention the Americas write and the one
# every earlier reader assumed; and nothing at all for an ambiguous dotted
# date on a page that does not say, because nobody writes month first with
# dots and a wrong date is worse than an unread one.
# --------------------------------------------------------------------------

# The languages that write a numeric date day first. English is decided by
# its region (see `_DAY_FIRST_ENGLISH_REGIONS`), so a US page is untouched.
DAY_FIRST_LANGUAGES = frozenset({
    "vi", "id", "ms", "th", "fr", "de", "es", "it", "pt", "nl", "ru", "pl", "tr",
    "cs", "sk", "ro", "el", "da", "nb", "nn", "no", "fi", "uk", "bg", "hr", "sr",
    "sl", "et", "lv", "lt", "he", "ar", "fa", "ur", "hi", "bn", "ta", "te", "mr",
    "gu", "kn", "ml", "ca", "gl", "eu", "is", "ga", "cy", "sq", "mk", "be", "ka",
    "az", "kk", "sw", "af",
})
_DAY_FIRST_ENGLISH_REGIONS = frozenset({
    "gb", "uk", "ie", "au", "nz", "in", "za", "sg", "my", "pk", "ng", "ke", "hk",
    "mt", "cy", "ae", "sa", "eg", "gh", "lk", "bd", "np", "jm",
})


def writes_day_first(lang):
    """Does a page declaring this language write a numeric date day first?"""
    code = str(lang or "").strip().lower().replace("_", "-")
    if not code:
        return False
    primary, _, rest = code.partition("-")
    if primary == "en":
        return rest[:2] in _DAY_FIRST_ENGLISH_REGIONS
    return primary in DAY_FIRST_LANGUAGES


# Day, month and year separated by one dot or one slash, the year in two
# digits or four. Nothing may run into it on either side, so a version number
# `1.10.20.4` and a dotted IP address are not dates.
NUMERIC_DATE_RE = re.compile(
    r"(?<![0-9.,/])([0-9]{1,2})([./])([0-9]{1,2})\2((?:19|20)?[0-9]{2})"
    r"(?![0-9]|[.,/][0-9])")
# "ngày 6 tháng 5 năm 2026" (the sixth day of the fifth month of 2026) and
# "tháng 5 năm 2026" (May 2026), with the words written out.
VIETNAMESE_DATE_RE = re.compile(
    u"(?:ngày\\s+([0-9]{1,2})\\s+)?tháng\\s+([0-9]{1,2})\\s*(?:năm|,|/)\\s*"
    u"((?:19|20)[0-9]{2})\\b", re.I)


def read_printed_date(printed, lang="", today=None):
    """The calendar date a numeric or Vietnamese date on a page states, or None.

    `lang` is the page's declared language, and settles which number is the
    day wherever the numbers themselves do not. A two-digit year is this
    century's unless that would put it more than a year ahead of `today`.
    A month with no day is read as its first day, the earliest the page can
    be, so the reading can never make a page look fresher than it is.
    """
    import datetime as _datetime
    text = str(printed or "")

    def made(year, month, day):
        try:
            return _datetime.date(year, month, day)
        except ValueError:
            return None

    match = VIETNAMESE_DATE_RE.search(text)
    if match:
        return made(int(match.group(3)), int(match.group(2)), int(match.group(1) or 1))
    match = NUMERIC_DATE_RE.search(text)
    if not match:
        return None
    first, separator, second, year_text = (
        int(match.group(1)), match.group(2), int(match.group(3)), match.group(4))
    year = int(year_text)
    if len(year_text) == 2:
        today = today or _datetime.date.today()
        year += 2000
        if year > today.year + 1:
            year -= 100
    if first > 12 and second > 12:
        return None
    if first > 12:
        day, month = first, second
    elif second > 12:
        month, day = first, second
    elif writes_day_first(lang):
        day, month = first, second
    elif separator == "/":
        month, day = first, second
    else:
        return None
    return made(year, month, day)


def non_gregorian_dates(text, script="", language=""):
    """Every date in this text written in a calendar that is not the Gregorian.

    The one entry point, so a caller does not have to know which reader covers
    which shape and no caller can wire in half of them. That is what happened:
    the three readers above were written, tested and called from nowhere, and
    the same defect showed up on two different sites against the shipped
    build while the code that answers it sat in this file.

    `script` and `language` are consulted for the numeric shape alone -
    `31/03/2563` is a year only if the page says which calendar it is in. The
    Thai-script, Japanese-era and Hijri shapes carry that evidence in the
    match: each requires a mark the page itself printed.
    """
    found = list(buddhist_era_dates(text, script, language))
    seen = {printed for printed, _year in found}
    for printed, year in (thai_era_dates(text) + japanese_era_dates(text)
                          + hijri_dates(text)):
        if printed in seen:
            continue
        seen.add(printed)
        found.append((printed, year))
    return found


# The words that put a body into existence, in the languages this audit already
# names elsewhere - `crawl.py::_is_title_boilerplate` lists the same set for
# "home page", and it is the set the crawl actually meets. English is here so
# there is one list rather than two; `fact-extractability-audit` holds the
# English *shapes* (the passive "was formed", the charter as a document), which
# are grammar and do not translate word for word.
#
# Every entry is a word no English sentence contains, so a site in one language
# cannot be matched by another's vocabulary. That is why "fra" and "od" are
# safe to include at two and three letters: they are only ever reached with a
# year beside them.
FOUNDING_WORDS_BY_LANGUAGE = {
    "en": ("since", "founded", "established", "incorporated", "chartered",
           "opened in", "in business since"),
    "de": ("seit", u"gegründet", "gegr", "errichtet", u"eröffnet", "entstanden"),
    "fr": ("depuis", u"fondé", u"fondée", u"créé", u"créée", "etabli", u"établi",
           "ouvert", "ouverte"),
    "es": ("desde", "fundado", "fundada", "creado", "creada", "establecido",
           "establecida"),
    "pt": ("desde", "fundado", "fundada", "criada", "criado", "estabelecida",
           "estabelecido"),
    "it": ("dal", "dall", "fondato", "fondata", "creata", "aperto", "aperta"),
    "nl": ("sinds", "opgericht", "gesticht", "geopend"),
    "sv": ("sedan", "grundat", "grundad", "grundades", "startade", "etablerad"),
    "no": ("fra", "siden", "grunnlagt", "stiftet", "etablert", "startet",
           "opprettet", u"åpnet"),
    "da": ("siden", "grundlagt", "stiftet", "etableret", "oprettet", u"åbnet"),
    "fi": ("vuodesta", "perustettu", "perustettiin", "avattu"),
    "pl": ("od", u"założona", u"założony", u"założono", u"powstała", u"powstał"),
    "th": (u"ก่อตั้ง", u"ตั้งแต่", u"เปิด", u"สร้าง"),
}
# Norwegian is written two ways and both codes appear in `lang` attributes.
FOUNDING_WORDS_BY_LANGUAGE["nb"] = FOUNDING_WORDS_BY_LANGUAGE["no"]
FOUNDING_WORDS_BY_LANGUAGE["nn"] = FOUNDING_WORDS_BY_LANGUAGE["no"]


def founding_words(language=""):
    """The founding vocabulary to search a page with.

    Every language's words when the site's language is unknown, which is the
    common case and is safe here: no word in this table is an English word, so
    reading them all over an English page adds nothing an English matcher would
    not already have found.
    """
    code = primary_subtag(language)
    if code and code in FOUNDING_WORDS_BY_LANGUAGE:
        return tuple(FOUNDING_WORDS_BY_LANGUAGE[code])
    seen, out = set(), []
    for words in FOUNDING_WORDS_BY_LANGUAGE.values():
        for word in words:
            if word not in seen:
                seen.add(word)
                out.append(word)
    return tuple(out)


def founding_word_pattern(language=""):
    """`founding_words` as an alternation a caller splices into its own regex.

    Longest first, so `in business since` is matched whole rather than as
    `since` with three loose words in front of it.
    """
    words = sorted(founding_words(language), key=lambda w: (-len(w), w))
    return "|".join(re.escape(word) for word in words)




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


# The scripts one East Asian page writes in together. Japanese prose is kanji
# and kana in the same sentence, and Korean mixes hanja into hangul. Counted
# one by one, each is a minority of a page that is entirely theirs: a
# university's Japanese pages held 1,599 han and 1,922 kana letters against
# 2,014 Latin ones from brand names, codes and an English edition in the same
# sample, so Latin led the plurality and the report said the site was "written
# in the latin script" while every page declared `lang="ja"`.
_EAST_ASIAN_SCRIPTS = ("han", "kana", "hangul")


def dominant_script(text):
    """The writing system most of this text is in, or None if it is too short.

    A page of English quoting one Japanese product name is English. The answer
    is the script holding most of the letters, not every script present.

    Han, kana and hangul are weighed together against whatever leads, because
    they are one page's writing and not three rival ones - see
    `_EAST_ASIAN_SCRIPTS`. Where together they outnumber the leader, the answer
    is whichever of them holds the most letters, which is the name every caller
    already knows.
    """
    counts = script_counts(text)
    if sum(counts.values()) < SCRIPT_SAMPLE_MINIMUM:
        return None
    leader = max(sorted(counts), key=counts.get)
    east_asian = {name: counts[name] for name in _EAST_ASIAN_SCRIPTS if counts.get(name)}
    if leader not in east_asian and sum(east_asian.values()) > counts[leader]:
        return max(sorted(east_asian), key=east_asian.get)
    return leader


# How many English characters one character of each script is worth.
#
# Every text-length threshold in this marketplace was measured on English pages
# and then applied to a character count, and a character is not the same
# quantity in every writing system. "People's Republic of China" is 26
# characters; the same words in Han are seven. A page of 150 Han characters
# carries what an English page needs about 500 for, and it was being told, at
# high severity, that it holds "too little text to be quoted".
#
# **Chosen from published translation-length ratios, not measured here.** The
# translation industry prices English against Chinese at roughly 1.7 Han
# characters per English word, and an English word runs about six characters
# with its space, which puts one Han character at about three and a half.
# Japanese mixes kana with kanji and runs shorter per character; Korean
# syllable blocks shorter still. The numbers are deliberately conservative -
# each is nearer 1.0 than the ratio it stands for - because the direction that
# costs more is calling a real page substantial, not calling a thin one thin.
#
# Everything else is 1.0 and is meant to be. Arabic, Hebrew, Devanagari, Thai,
# Cyrillic and Greek are alphabets or abugidas with word-length runs much like
# Latin's, so scaling them would be inventing a difference to look thorough.
#
# `structured-data-audit`'s `UNSPACED_CHARACTER_WIDTH = 2.5` is the same fact,
# measured rather than chosen: counting one for one reported 40 of 57 complete
# titles on a city government's site as too short. It is one number for every
# unspaced script and this is three, and the two agree - 2.5 sits between kana
# at 2.2 and Han at 3.0. Kept separate because that one was measured on titles
# and these were not measured here at all, and a constant that says where it
# came from is worth more than a constant shared by two things that measured
# different quantities.
CHARACTERS_PER_ENGLISH_CHARACTER = {
    "han": 3.0,
    "kana": 2.2,
    "hangul": 1.8,
}


def english_equivalent_length(text):
    """How long this text would be written in English, in characters.

    The unit every length threshold here was measured in. Returns the plain
    length for a script this makes no claim about, so a threshold applied to
    the answer is unchanged on the pages it was measured on.
    """
    body = str(text or "")
    factor = CHARACTERS_PER_ENGLISH_CHARACTER.get(dominant_script(body))
    return int(round(len(body) * factor)) if factor else len(body)


def length_floor_for(text, floor):
    """`floor`, restated in the characters this text is actually written in.

    The same comparison as `english_equivalent_length(text) >= floor`, given
    the other way round so that a finding can print the number it applied.
    "under 300 characters" is a claim a reader checks against the page, and on
    a page where the number used was 100 the report has to say 100.
    """
    factor = CHARACTERS_PER_ENGLISH_CHARACTER.get(dominant_script(str(text or "")))
    return int(round(floor / factor)) if factor else floor


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
    answer and the common case on smaller sites - unless its own letters say
    otherwise. A page written in Cyrillic or Kana is not an English page that
    forgot to say so, and inheriting an English verdict is how a page of
    Russian prose came to be measured against the "<Brand> is a ..." pattern.
    """
    out = []
    for page in pages or []:
        if not _page_is_in_the_prose_language(page):
            continue
        out.append(page)
    return out


def _page_is_in_the_prose_language(page):
    """May the English matchers read this page at all?

    Declaration first, then the script of the page's own text. Both can be
    absent, and then the answer is yes - the site verdict has already been
    taken and this is the common case.
    """
    declared = primary_subtag(page.get("lang"))
    if declared:
        return declared == PROSE_LANGUAGE
    return dominant_script(page.get("body_text") or "") not in NON_LATIN_PROSE_SCRIPTS


def other_language_pages(pages):
    """The pages `pages_in_prose_language` set aside, for saying so."""
    return [p for p in pages or [] if not _page_is_in_the_prose_language(p)]

def prose_skip_reason(language):
    """Why a prose check declined, in words a site owner can act on.

    Two shapes, because two different things can be known. A site that
    declares `de` is named by its language. A site that declares nothing and
    writes in Cyrillic is named by its script, because that is the whole of
    what was established - Cyrillic is four languages and this audit did not
    decide which, and saying "an undetermined language" of a page plainly not
    in English hides the reason the check declined.
    """
    script = str(language.get("script") or "").strip()
    if not language.get("code") and script in NON_LATIN_PROSE_SCRIPTS:
        return ("this site's text is written in the {} script ({}), so it is not in English, "
                "and this check reasons about English prose only. It was not run rather than "
                "guessed at. The structural checks in this skill were unaffected.".format(
                    script.capitalize(), language.get("source", "detected")))
    return ("this site is in {} ({}), and this check reasons about English prose only. "
            "It was not run rather than guessed at. The structural checks in this skill "
            "were unaffected.".format(
                (language.get("code") or "an undetermined language").upper(),
                language.get("source", "detected")))


# Words that carry no identity of their own. A name whose only missing part is
# one of these is still the name a page is using.
NAME_FILLER_WORDS = frozenset({"and", "the", "of", "for", "a", "an", "at",
                               "de", "la", "le", "el", "du", "des", "und"})


def _name_words(value):
    """Identity-carrying words of a name, lower case."""
    return [w for w in re.findall(r"[^\W_]+", str(value or "").lower())
            if len(w) > 1 and w not in NAME_FILLER_WORDS]


def _same_word(a, b):
    """One word against another, allowing for an English plural.

    A restaurant group declares "... Restaurants" and its branch pages say
    "... Restaurant". That single letter produced 44 conflicts across 59 pages
    and the report's only high-severity finding, about markup and copy that
    agree.
    """
    if a == b:
        return True
    for short, long_ in ((a, b), (b, a)):
        if long_ in (short + "s", short + "es"):
            return True
    return False


def names_are_one_name(declared, candidate):
    """The strict half of `names_match`: the same name, spelt two ways.

    `names_match` also accepts a name whose every word appears in the other,
    which is what a trading name against a legal name looks like and is right
    for that job. It is wrong for asking what an encyclopedia article is
    about: a one-word brand key then matches any title containing that word,
    and a digital library was credited with the article about the
    15th-century printer it is named after, and with a different
    organisation's article in another language, as its own profiles.
    """
    left, right = str(declared or "").strip(), str(candidate or "").strip()
    if not left or not right:
        return False
    if comparison_key(left) == comparison_key(right):
        return True
    for a in [left] + name_forms(left):
        for b in [right] + name_forms(right):
            if comparison_key(a) and comparison_key(a) == comparison_key(b):
                return True
    return False


def names_match(declared, candidate):
    """Are these two strings the same organisation name?

    One comparison, used everywhere, because each check that invented its own
    got a different subset of the variation the web actually contains and each
    one produced a false positive on the part it did not know about:

      legal suffix   "Harrowgate Law LLP" against "Harrowgate Law"
      plural         "Indian Restaurants" against "Indian Restaurant"
      domain suffix  "Le Havre.fr" against "Le Havre"
      article        "The Museum of Modern Craft" against "Museum of Modern Craft"
      punctuation    "charity: shelter" against "charity water"
      case           any of the above

    Not a fuzzy match. Every word of one name has to be present in the other,
    allowing only the variations above; "Acme Bakeries" and "Acme Breweries"
    are still different names.
    """
    left, right = str(declared or "").strip(), str(candidate or "").strip()
    if not left or not right:
        return False
    if names_are_one_name(left, right):
        return True
    a_words, b_words = _name_words(left), _name_words(right)
    if not a_words or not b_words:
        return False
    shorter, longer = sorted((a_words, b_words), key=len)
    return all(any(_same_word(w, other) for other in longer) for w in shorter)


def name_appears_in(declared, haystack):
    """Does this text use the declared name, in any of the ways a page writes it?

    The page-side half of `names_match`. A title splits a name across its
    separators - "Walmgate | Indian Restaurants | From Bombay with Love" - so a
    substring test cannot see a name that is demonstrably there. Every word
    present somewhere in the text is what a reader means by "the name is here".
    """
    text = str(haystack or "")
    if not text:
        return False
    low = text.lower()
    if any(form.lower() in low for form in [declared] + name_forms(declared) if form):
        return True
    words = _name_words(declared)
    if not words:
        return False
    on_page = set(re.findall(r"[^\W_]+", low))
    return all(any(_same_word(w, seen) for seen in on_page) for w in words)


LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.I)

# The registered, trade, copyright and service marks a site prints after its
# name. They decorate the name and are never part of it: "<Name>®" in the
# markup and "<Name> is a ..." in the copy are one name, and a matcher that
# kept the sign could not find the second from the first.
NAME_MARKS = u"®™©℠"
_NAME_MARK_RE = re.compile(u"\\s*[" + NAME_MARKS + u"]")


def without_marks(name):
    """`name` with any ®, ™, © or ℠ taken out, spacing tidied."""
    return re.sub(r"\s+", " ", _NAME_MARK_RE.sub("", str(name or ""))).strip()


# A country written after a name, which is where the business trades rather
# than part of what it is called. Abbreviations are matched in capitals only,
# so a name ending in the word "us" keeps it.
_TRAILING_PLACES = (
    "singapore", "india", "malaysia", "indonesia", "thailand", "philippines",
    "vietnam", "japan", "korea", "south korea", "china", "hong kong", "taiwan",
    "australia", "new zealand", "canada", "united kingdom", "united states",
    "america", "ireland", "germany", "france", "spain", "italy", "netherlands",
    "belgium", "switzerland", "austria", "sweden", "norway", "denmark", "finland",
    "poland", "portugal", "brazil", "mexico", "argentina", "chile", "dubai",
    "saudi arabia", "qatar", "egypt", "south africa", "nigeria", "kenya",
    "pakistan", "bangladesh", "sri lanka", "nepal", "europe", "asia",
)
_TRAILING_PLACE_RE = re.compile(
    r"[\s,|–—-]+(?:(?i:" + "|".join(re.escape(p) for p in sorted(
        _TRAILING_PLACES, key=len, reverse=True)) + r")|US|USA|UK|UAE)\.?$")
# Words after which a place is part of the name: "Bank of <Country>".
_NAME_JOINING_WORDS = frozenset({
    "of", "for", "and", "&", "de", "du", "des", "del", "della", "von", "van",
    "the", "in", "at",
})


def name_forms(declared, domain_token=""):
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
    # A registered or trade mark is not part of the name. A home-decor shop
    # declares "<Name>®" in its markup and `og:site_name` and writes "<Name> is
    # a lifestyle brand in home decor solutions" on its homepage, and every
    # comparison that started from the declared string was looking for a sign
    # the sentence never carries. Everything below is derived from the bare
    # name, and the declared spelling is kept as well.
    bare = without_marks(declared)
    if bare and bare != declared:
        forms.add(bare)
        declared = bare
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
        # A name that carries its own domain suffix is the same name without
        # it. A newspaper declares "Le Havre.fr" in `og:site_name` and
        # "Le Havre" in its Organization markup, and the naming check reported
        # the two as a brand written more than one way - about a site that
        # writes it one way and appends a TLD in one field.
        without_tld = _BRAND_TLD_RE.sub("", value).strip()
        if without_tld and without_tld != value:
            forms.add(without_tld)
    # Longest first, and alphabetical among equals. Sorting a *set* by length
    # alone leaves ties in set-iteration order, which varies with the hash
    # seed: {"Poppy Ltd", "Poppy Inc", "Poppy Grp"} came back in three
    # different orders across four interpreter starts, and the caller returns
    # the first form that matches - so the quoted definition sentence, and the
    # page it was attributed to, could differ between two runs of one snapshot.
    # A leading article is not part of the name in a sentence about the brand.
    # "The Coppergate Studio is a retreat for curious programmers" is the site
    # stating in one sentence what it is, and the definition check reported at
    # HIGH confidence that no such sentence exists - on two separate sites -
    # because the declared name begins with "The" and the matcher wanted the
    # sentence to begin with the brand. Every organisation conventionally
    # written with an article was unsatisfiable.
    for value in list(forms):
        without_article = LEADING_ARTICLE_RE.sub("", value).strip()
        if without_article and without_article != value:
            forms.add(without_article)
    # The name without the country it trades in. A design studio declares
    # "<Name> Singapore" in `og:site_name` and its Organization markup, and its
    # homepage's meta description reads "<Name> is a multidisciplinary design
    # studio based in Singapore". Every form derived from the declared string
    # kept the country, so that sentence was never tried against the name it
    # uses, and the definition check quoted a press line from a blog post
    # instead. The place comes off only where what is left is still a name -
    # two words or more, or the word the domain spells, so "Air <Country>"
    # keeps its country - and never after "of", "for" or "and", where the
    # place is part of the name ("Bank of <Country>").
    place_token = re.sub(r"[^a-z0-9]", "", (domain_token or "").lower())
    for value in list(forms):
        match = _TRAILING_PLACE_RE.search(value)
        if not match:
            continue
        rest = value[:match.start()].strip().rstrip(",|–—-").strip()
        words = rest.split()
        if not words or words[-1].lower() in _NAME_JOINING_WORDS:
            continue
        if len(words) >= 2 or re.sub(r"[^a-z0-9]", "", rest.lower()) == place_token:
            forms.add(rest)
    # The leading words that spell the domain. A restaurant group declares
    # "Walmgate Indian Restaurants" and writes "Each Walmgate is a love letter to
    # Bombay"; the check searched for the full three-word legal name, which
    # appears nowhere on that site and nowhere on the web. Only the prefix that
    # matches the domain is offered, so "Indian Restaurants" never is.
    token = re.sub(r"[^a-z0-9]", "", (domain_token or "").lower())
    if token:
        for value in list(forms):
            words = value.split()
            for count in range(1, len(words)):
                head = " ".join(words[:count])
                if re.sub(r"[^a-z0-9]", "", head.lower()) == token:
                    forms.add(head)
                    break
    return sorted({f for f in forms if len(f) > 2}, key=lambda f: (-len(f), f))


# --------------------------------------------------------------------------
# Which strings are this site's name, and how sure each one is
#
# Every matcher above answers "are these two names the same name". None of them
# answers the question in front of it: *which* strings should be offered as
# this site's name in the first place. That was decided once, in one place, by
# taking `og:site_name` or `<title>` verbatim and the bare host label.
# Measured on sites that are not English shops, that costs this much:
#
#   a design firm      the two names offered were "San Francisco Premier
#                      Interior Designer" and "arrowooddesign". The homepage
#                      opens "Arrowood Design is a full-service, client-focused
#                      design firm specializing in residential projects" - a
#                      textbook definition sentence - and the report said no
#                      page states in one sentence what the brand is. The words
#                      the domain spells were never tried.
#   a dental clinic    the report's opening sentence read "A machine can reach
#                      <the whole 31-character SEO title, three segments and
#                      two separators> and read it", and the same string was
#                      sent to an encyclopedia as a search term.
#   a town government  the declared name carries "Town of" in front of it, and
#                      nothing the town writes about itself repeats that.
#
# Two failures with opposite cures, which is why one list cannot serve both.
# Missing "Arrowood Design" made a check state a falsehood about the site, and
# the cure is more candidates. Sending an SEO title to an encyclopedia would
# have published somebody else's identifier under this brand's name, and the
# cure is fewer. So candidates carry which of the two they are:
#
#   ASSERTED  the site published this string as its name, or it is what is left
#             of one after removing something the site also wrote - a legal
#             suffix, a leading article, a trailing domain suffix, the tail of
#             a title after its separator. Removing is safe. A run of words the
#             site printed whose letters are exactly its own domain label is
#             asserted too: the site wrote the words and the address confirms
#             them, and no coincidence produces that.
#   DERIVED   this audit built the string and nobody published it - the host
#             label on its own, a civic prefix dropped. Useful, and never
#             allowed to be the reason a match succeeds where a wrong match
#             publishes a fact.
#
# The danger is on the record: a substring test once counted fifteen unrelated
# encyclopedia articles as one brand's profiles, and a one-word brand key
# credited a digital library with the article about the 15th-century printer it
# is named after. A wrong alias is worse than no alias, so the tier a check may
# read is part of the check's contract and not a matter of taste.
# --------------------------------------------------------------------------

ASSERTED = "asserted"
DERIVED = "derived"

# What separates one segment of a title from the next. Deliberately not a
# colon: "charity: shelter" is a brand name and splitting it invents two. The
# hyphen is here only with space on both sides, or "Coca-Cola" becomes two
# names; the full-width bar is here because a Japanese title uses it and the
# ASCII one is what everybody else uses.
_TITLE_SEPARATOR_RE = re.compile(
    u"\\s*(?:[|\uff5c\u2013\u2014\u2015\u00b7\u2022\u00bb\u00ab\u203a\u2039]|(?<=\\s)-(?=\\s))\\s*")

# A run of words this long that flattens to the site's own domain label is the
# site's own spelling of its name. Four is enough for "Ben & Jerry's" and short
# enough that no sentence matches one by accident; the four-letter floor stops
# a short domain from matching a common word. Both measured in `crawl.py`,
# where this reading was first written, and kept identical here so the crawl
# and the matchers cannot come to different answers about one site.
NAME_SPELLING_MAX_WORDS = 4
NAME_SPELLING_MIN_CHARS = 4

# Above this a string is a page title rather than a name. Names in the wild run
# to about forty characters and six words; the strings that caused the failure
# above ran to 31 characters of Japanese across three segments and to five
# English words of search-engine copy that name no organisation.
NAME_DISPLAY_MAX_CHARS = 60
NAME_DISPLAY_MAX_WORDS = 7

# Prefixes an institution carries in its formal name and drops in every
# sentence it writes about itself. A town publishes "Town of <place>" in its
# markup and writes "<place> was chartered in 1764" in its prose.
#
# Kept to civic bodies. "University of X" and "Department of Y" look like the
# same shape and are not: dropping the prefix there leaves "Computer Science",
# which is a subject and not anybody's name, and a definition check handed that
# as a subject would accept a sentence about the field.
_CIVIC_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:town|city|village|borough|township|county|district|"
    r"municipality|parish|commune)\s+of\s+", re.I)


def title_segments(title):
    """The parts of a page title, in the order it writes them.

    A title is a sentence built out of a name and everything the site wants a
    search engine to see beside it. Splitting it is what lets the name be
    found; taking it whole is what put a 31-character SEO title into a report's
    opening sentence as an organisation's name.
    """
    parts = [p.strip(" \t\u3000,.;") for p in _TITLE_SEPARATOR_RE.split(str(title or ""))]
    return [p for p in parts if p]


def without_civic_prefix(name):
    """"Town of Ashcombe" -> "Ashcombe". The name itself when there is none."""
    stripped = _CIVIC_PREFIX_RE.sub("", str(name or "")).strip()
    return stripped or str(name or "").strip()


def looks_like_a_page_title(value):
    """Is this string a page title rather than the name of an organisation?

    Used to keep the report's prose out of a failure seen in a real report:
    "A machine can reach <site's whole SEO title> and read it". A name has no
    separator in it, and no organisation needs sixty characters to say what it
    is called.
    """
    text = str(value or "").strip()
    if not text:
        return True
    if len(title_segments(text)) > 1:
        return True
    return len(text) > NAME_DISPLAY_MAX_CHARS or len(text.split()) > NAME_DISPLAY_MAX_WORDS


def name_the_site_spells(token, texts, max_words=NAME_SPELLING_MAX_WORDS):
    """The words the site prints that its own domain label spells, or "".

    A hostname cannot carry a space, an apostrophe or an ampersand, so the
    label is a flattened version of something the site writes properly:
    `arrowooddesign` for "Arrowood Design", `benjerry` for "Ben & Jerry's".
    Every run of up to `max_words` words in `texts` is flattened the same way
    and compared; the comparison is exact letter equality, so this cannot
    return a string the site did not print or a string the domain does not
    spell, which is what makes its answer asserted rather than guessed.

    `crawl.py` had this reading and used it in one place - only when the domain
    was the last resort for the brand name. On the design firm above the brand
    name came from a title, so it never ran, and the name the whole site is
    built around was never offered to any matcher. It lives here now so the
    crawl and the six skills that match names cannot answer differently about
    one site; `crawl.py::_site_spelling` is the copy to delete.
    """
    key = re.sub(r"[^a-z0-9]", "", str(token or "").lower())
    if len(key) < NAME_SPELLING_MIN_CHARS:
        return ""
    for text in texts or ():
        words = str(text or "").split()
        for start in range(len(words)):
            for end in range(start + 1, min(start + max_words, len(words)) + 1):
                phrase = " ".join(words[start:end])
                if re.sub(r"[^a-z0-9]", "", phrase.lower()) == key:
                    trimmed = phrase.strip(u" \t\u3000,.:;|\uff5c-")
                    if trimmed and trimmed.lower() != str(token or "").lower():
                        return trimmed
    return ""


class NameForms:
    """Every string this site might be called, split by who said so.

    Three readers, and which one a check uses is part of that check's contract:

      for_matching()  asserted forms and derived ones together, longest first.
                      For a check whose failure mode is a miss - the definition
                      sentence, the name-appears-on-the-page test. Accepting a
                      weak alias there suppresses a finding, and a suppressed
                      finding is a smaller harm than the falsehood printed in
                      its place when the alias is withheld.
      for_lookup()    asserted forms only, best first. For a check whose
                      failure mode is publishing somebody else's identity - an
                      encyclopedia or knowledge-base lookup, attributing an
                      off-site account to this brand, filling the `name` of a
                      snippet the owner will paste. A derived string may never
                      be the reason one of those succeeds.
      display()       one string, for a sentence a person reads. Never a page
                      title, never the flattened host label.

    `tiers()` is the audit trail: the same strings with the tier each one is
    in, so a finding can say which reading it accepted and a reader can argue
    with it.
    """

    __slots__ = ("asserted", "derived")

    def __init__(self, asserted=(), derived=()):
        seen = []
        for value in asserted:
            value = re.sub(r"\s+", " ", str(value or "")).strip()
            if len(value) > 2 and value not in seen:
                seen.append(value)
        self.asserted = tuple(sorted(seen, key=lambda f: (-len(f), f)))
        strong = {comparison_key(f) for f in self.asserted}
        weak = []
        for value in derived:
            value = re.sub(r"\s+", " ", str(value or "")).strip()
            if len(value) > 2 and comparison_key(value) not in strong and value not in weak:
                weak.append(value)
        self.derived = tuple(sorted(weak, key=lambda f: (-len(f), f)))

    def __repr__(self):
        return "NameForms(asserted={!r}, derived={!r})".format(self.asserted, self.derived)

    def __eq__(self, other):
        if isinstance(other, NameForms):
            return (self.asserted, self.derived) == (other.asserted, other.derived)
        return NotImplemented

    def __hash__(self):
        return hash((self.asserted, self.derived))

    def for_matching(self):
        """Both tiers, longest first. The most specific form that matches wins."""
        return tuple(sorted(self.asserted + self.derived, key=lambda f: (-len(f), f)))

    def for_lookup(self):
        """Asserted forms only, best first: shortest that is still a name.

        Ordered shortest-first and not longest-first, which is the opposite of
        every other reader here and is deliberate. A lookup sends one string to
        somebody else's index, and the string with the best chance of naming
        the organisation there is the organisation's name, not its name with a
        legal suffix and a tagline attached.

        This is a list of strings to *try*, and it is not permission to accept
        what comes back. Whatever an index returns still has to survive
        `names_are_one_name` against the label it returned - never a substring
        test, which once counted fifteen unrelated encyclopedia articles as one
        brand's profiles - and a search string being in this list says nothing
        about whether its first result is this organisation.
        """
        usable = [f for f in self.asserted if not looks_like_a_page_title(f)]
        return tuple(sorted(usable or self.asserted, key=lambda f: (len(f), f)))

    def display(self):
        """One string to print in a sentence about the brand, or ""."""
        for form in self.for_lookup():
            if not looks_like_a_page_title(form):
                return form
        for form in self.asserted:
            segments = title_segments(form)
            if segments:
                return min(segments, key=len)
        return ""

    def tiers(self):
        """[(form, ASSERTED or DERIVED)], in `for_matching` order."""
        strong = set(self.asserted)
        return [(f, ASSERTED if f in strong else DERIVED) for f in self.for_matching()]


def name_candidates(brand, extra_asserted=(), texts=()):
    """Build this site's `NameForms` from the record `crawl.py` writes.

    `brand` is the snapshot's `brand` object. `extra_asserted` is for a caller
    holding a string the site published that the record does not carry.
    `texts` are strings the site printed - titles, headings - searched for the
    run of words its own domain spells; the homepage title and H1 are the two
    that matter and cost nothing to pass.

    Everything `name_forms` derives from an asserted string stays asserted,
    because every one of those derivations removes something rather than
    inventing something. The host label on its own and a dropped civic prefix
    are derived: nobody published either.
    """
    brand = brand or {}
    host = str(brand.get("host") or "")
    token = str(brand.get("domain_token") or "")
    if re.match(r"^\d+$", token):        # an IP address is not a name
        token = ""

    published = [brand.get("name")]
    published += list(brand.get("authoritative_variants") or [])
    published += list(brand.get("alternate_names") or [])
    published += list(extra_asserted or [])
    published = [str(v).strip() for v in published if str(v or "").strip()]

    # The segments of anything that is really a title, so the name inside it is
    # offered as well as the whole string. The whole string stays: a site whose
    # `og:site_name` genuinely contains a bar has not been misread.
    for value in list(published):
        if looks_like_a_page_title(value):
            published.extend(title_segments(value))

    searchable = list(texts or []) + published
    spelled = ""
    for candidate_token in ([token] if token else []) + host_name_forms(host):
        spelled = name_the_site_spells(candidate_token, searchable)
        if spelled:
            published.append(spelled)
            break

    asserted = []
    for value in published:
        asserted.append(value)
        asserted.extend(name_forms(value, domain_token=token))

    derived = []
    for label in host_name_forms(host):
        derived.append(label.replace("-", " ").strip())
    for value in published:
        without = without_civic_prefix(value)
        if without and without != value:
            derived.append(without)
            derived.extend(name_forms(without, domain_token=token))
    return NameForms(asserted, derived)


# --------------------------------------------------------------------------
# Is the chosen name a name at all?
#
# `crawl.py` picks the brand name from whatever the site declares, best source
# first, and never asks whether the string it picked is a name. Two sites in
# one validation pass showed what that costs, and both failures ended in the same
# place: the `"name"` field of a paste-ready `Organization` block, which the
# owner is told to copy into their own site.
#
#   the empty brand slot   A vegetable farm's theme means `<brand> - <tagline>`
#                          and its brand slot is empty, so every page carries
#                          `og:site_name` = `"- Empowering communities"`. The
#                          leading separator was stripped as stray punctuation
#                          and the tagline became the company's name: in the
#                          verdict paragraph, in four fix templates - one
#                          reading "Empowering communities was founded in
#                          <year>" - in a knowledge-base lookup that returned
#                          two unrelated entities for the phrase and produced a
#                          finding that the brand's name collides with a
#                          scientific article, and in the snippet's `name` and
#                          `description`. Everything needed to reject it was on
#                          the page: the registrable domain label spells
#                          `truefarm`, an H2 reads "Welcome to True farm", and
#                          the body opens "At Truefarm.in, we bring you fresh
#                          ... vegetables". Nothing on the site spells the
#                          phrase that was adopted.
#
#   the escaped name       A handbag brand's theme HTML-escapes its own values
#                          twice: `og:site_name` is `Tan&amp;amp;Loom` and its
#                          JSON-LD `name` is `Tan&amp;Loom`. Both are the
#                          string `Tan&Loom`. The report printed "A machine can
#                          reach Tan&amp;amp;Loom and read it" in its verdict,
#                          raised a medium finding that two distinct names are
#                          asserted as the site's identity - the two being one
#                          name and its own escaping - and put
#                          `<h1>Tan&amp;amp;Loom</h1>` in its only paste-ready
#                          code block, reproducing the exact bug the same
#                          report's top finding diagnoses.
#
# Three rules, applied in `resolve_brand` before any skill reads the name:
# decode it, reject a template with an empty slot, reject a name no part of the
# site spells.
#
# Decoding is the one to be careful about. An escaped name is a defect to
# *report*, never an identity to adopt, so this decodes the brand block and
# nothing else - the page records keep their raw values, and
# `structured-data-audit::_escapes_html_inside_json` reads those, so the
# template bug still gets exactly one finding. That skill's `_as_published` is
# the same decode written first and confined to one skill; it is `as_published`
# here so the crawl, the six skills and the report cannot disagree about what a
# site published.
# --------------------------------------------------------------------------

# A character reference with its semicolon, which is the only form this will
# decode. `html.unescape` also decodes semicolon-less legacy references, so on
# a name like "Salt&notes" it produces "Salt¬tes" - a corruption invented
# by the decoder. Substituting match by match means only well-formed references
# are touched and the rest of the string is returned unchanged.
_CHARACTER_REFERENCE_RE = re.compile(
    r"&(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6});")

# How many decodes to attempt. A theme that escapes a value which was already
# escaped writes `&amp;#39;`, which needs two passes; three is one more than
# any template this audit has met and stops a contrived string from looping.
_UNESCAPE_PASSES = 3


def as_published(value):
    """A declared string with any HTML character references decoded.

    What a site published is what a reader sees, and a value that has been
    through an HTML-escape filter one time too many does not say what its
    bytes say: `Tan&amp;amp;Loom` is the name `Tan&Loom` written by a broken
    theme. Inside `<script type="application/ld+json">` there is no HTML
    parsing at all, so `&#39;` there is those five characters and every
    consumer reads them that way.

    Non-strings come back untouched, so this is safe to map over a record
    whose values are not all strings.
    """
    if not isinstance(value, str):
        return value
    for _ in range(_UNESCAPE_PASSES):
        if "&" not in value:
            break
        decoded = _CHARACTER_REFERENCE_RE.sub(
            lambda match: _html_unescape(match.group(0)), value)
        if decoded == value:
            break
        value = decoded
    return value


# What a theme puts between the slots of a title template. The same characters
# `_TITLE_SEPARATOR_RE` splits on, held as a character class because this test
# reads the ends of a string rather than the middle of one.
_SLOT_SEPARATOR_CHARS = (
    u"|｜–—―·•»«›‹")
# Punctuation that means "template" only at an edge. A colon or a comma inside
# a name is ordinary - "charity: shelter" is a brand name, and splitting it once
# cut a charity's name down to one word - but a value that *opens* with one has
# nothing in front of it, and what has nothing in front of it is an empty slot.
_EDGE_ONLY_SEPARATOR_CHARS = u":,~/"
# The dash last in every class: anywhere else it would be a range.
_SLOT_CLASS = u"[" + _SLOT_SEPARATOR_CHARS + u"-]"
_EDGE_CLASS = u"[" + _SLOT_SEPARATOR_CHARS + _EDGE_ONLY_SEPARATOR_CHARS + u"-]"
# The ideographic space as well as the ASCII ones: a Japanese title is spaced
# with it, and `\s` does match it, but writing it out keeps the reading of
# "whitespace between two separators" the same in every script.
_TEMPLATE_SPACE = u"[\\s　]"

_EMPTY_SLOT_HEAD_RE = re.compile(u"^" + _EDGE_CLASS)
_EMPTY_SLOT_TAIL_RE = re.compile(_EDGE_CLASS + u"$")
_EMPTY_SLOT_MIDDLE_RE = re.compile(
    _TEMPLATE_SPACE + _SLOT_CLASS + _TEMPLATE_SPACE + u"*" + _SLOT_CLASS + _TEMPLATE_SPACE)


def is_a_broken_title_template(value):
    """Is this string a title template whose brand slot came out empty?

    A theme writing `"<brand> - <tagline>"` with no brand configured emits
    `"- Empowering communities"`. The separator is the evidence and it is the
    first thing a cleaner throws away, so this has to read the string as the
    site published it, before any stripping.

    Three shapes, and each one is a slot with nothing in it: a separator at the
    front, a separator at the back, and two separators with only whitespace
    between them.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) < 2:
        return False
    return bool(_EMPTY_SLOT_HEAD_RE.search(text)
                or _EMPTY_SLOT_TAIL_RE.search(text)
                or _EMPTY_SLOT_MIDDLE_RE.search(text))


def the_brand_slot_is_empty(value):
    """Is the *first* slot of this template the empty one?

    Which slot is empty decides whether the name is wrong, and only the leading
    separator answers that. A theme's template reads `"<brand> - <tagline>"`, so
    a value opening with the separator has lost its brand and everything left is
    the tagline - which is the failure. A value *ending* with one has lost its
    tagline, and what is left is the brand: `"Acme -"` cleans to "Acme", which
    is the right name and must not be thrown away for its neighbour's fault.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return len(text) >= 2 and bool(_EMPTY_SLOT_HEAD_RE.search(text))


def the_site_spells(name, texts=(), labels=()):
    """Does any part of this site spell this name, letter for letter?

    `comparison_key` on both sides, so this asks about letters and not about
    punctuation or case: "True farm" is spelled by "At Truefarm.in, we bring
    you", and "Silt & Stone" by "Silt and Stone" is *not* - the ampersand
    survives as nothing and the word "and" as three letters, which is the
    conservative answer and the right one for a test whose false pass costs
    less than its false failure.

    `labels` are the site's own address labels, compared in both directions: a
    hostname cannot carry a space, so "Truefarm Organics" spells `truefarm` by
    containing it, and `truefarm` spells "True farm" the same way.

    Deliberately weak in one direction. A page title that carries the tagline
    as well as the name spells both, so this confirms some strings that are not
    the site's name; it is a backstop against a name the site never writes at
    all, not a test of which of two strings on the page is the better name.
    Wrongly rejecting a real name sends the whole report to the domain label,
    which is the worse failure, so the bar to reject is "nowhere on this site".
    """
    key = comparison_key(name)
    if len(key) < NAME_SPELLING_MIN_CHARS:
        return False
    for label in labels or ():
        other = comparison_key(label)
        if other and (other in key or key in other):
            return True
    for text in texts or ():
        if text and key in comparison_key(text):
            return True
    return False


# How much of one page's body text is read when asking whether the site spells
# a name. A name a site uses appears in its first screens; flattening sixty
# full page texts to ask again costs more than the answer is worth.
NAME_EVIDENCE_TEXT_LIMIT = 4000


def _name_evidence_labels(snapshot):
    """The address labels that are the site's own, not a registry's."""
    brand = snapshot.get("brand") or {}
    host = str(brand.get("host") or "") or (
        urlparse(str(snapshot.get("origin") or "")).hostname or "")
    labels = list(host_name_forms(host))
    token = str(brand.get("domain_token") or "").strip()
    if token and not re.match(r"^\d+$", token):
        labels.append(token)
    return labels


def _name_evidence_texts(snapshot, include_body=True):
    """Strings the site printed, cheapest and most name-like first.

    A generator, so a name confirmed by the first heading never pays for
    flattening sixty pages of body text. `include_body` is off for the caller
    that searches word by word rather than asking one containment question: a
    site writes its own name in a title or a heading, and running that search
    over sixty page texts costs seconds to find nothing.
    """
    pages = snapshot.get("pages") or []
    for page in pages:
        yield str(page.get("title") or "")
    for page in pages:
        headings = page.get("headings") or {}
        for level in ("h1", "h2", "h3"):
            for heading in (headings.get(level) or []):
                yield str(heading or "")
    if not include_body:
        return
    for page in pages:
        yield str(page.get("body_text") or "")[:NAME_EVIDENCE_TEXT_LIMIT]


def _declared_strings(snapshot):
    """Every identity string the pages declare, exactly as they published it.

    Read from the page records rather than from `brand`, because `crawl.py`
    strips the leading separator before it records anything and that separator
    is the whole of the evidence that a slot was empty.
    """
    raw = []
    for page in (snapshot.get("pages") or []):
        value = (page.get("og") or {}).get("og:site_name")
        if isinstance(value, str):
            raw.append(value)
        for node in (page.get("jsonld") or []):
            if not isinstance(node, dict):
                continue
            if not jsonld_type_names(node.get("@type")) & (
                    ORG_IDENTITY_TYPES | VISITABLE_JSONLD_TYPES):
                continue
            for key in ("name", "alternateName"):
                value = node.get(key)
                for item in ([value] if isinstance(value, str) else (value or [])):
                    if isinstance(item, str):
                        raw.append(item)
    return raw


def _cleaned_like(raw, candidate):
    """Is `candidate` what `crawl.py`'s cleaner makes of this whole raw string?

    Whole, and never a piece of it. A raw value the crawl *split* has already
    had its empty slot dealt with - "- Acme | Home" yields "Acme", which is a
    good name that came out of a broken template - so matching a candidate
    against a piece would reject names for their neighbours' faults.
    """
    cleaned = re.sub(r"\s+", " ", as_published(raw)).strip()
    cleaned = cleaned.strip(u"|-–—·•").strip()
    key = comparison_key(candidate)
    return bool(key) and comparison_key(cleaned) == key


# --------------------------------------------------------------------------
# The other half of "the brand name came out of the <title>"
#
# `the_brand_slot_is_empty` fixed the `og:site_name` half of this. The `<title>`
# half is a separate failure, measured on two sites of the same
# shape, both of which end where the first one did - in the `name` of a
# paste-ready Organization block and in a definition check hunting the site for
# a sentence that opens with a string nobody wrote.
#
#   a documentation tree   `<title>` is "Index - Frostvane user guide", the brand
#                          became "Frostvane user guide", the Organization snippet
#                          published that as the project's name, and the
#                          definition check went looking for "Frostvane user guide
#                          is a ..." above a homepage whose first paragraph
#                          reads "Frostvane is a blazingly fast DataFrame library
#                          for manipulating structured data". The hostname's
#                          leftmost label offered a second candidate, `docs`.
#
#   a one-page coaching    `<title>` is a whole marketing sentence, so the
#   site                   brand became "Mentorship for women with <a person>"
#                          and was searched for as a literal string in prose
#                          that opens "Hi! I'm <a person>! I'm a Holistic Life
#                          Coach for parents and entrepreneurs."
#
# `the_site_spells` passed both, and could only ever pass both: the title the
# candidate was cut out of is one of the strings it searches, so a title
# segment spells itself. The two tests below ask something the title cannot
# answer on its own, and both are skipped for a string the site declares in its
# identity markup - a site that puts a sentence in its own `Organization.name`
# has asserted it, and asserted is not derived.
# --------------------------------------------------------------------------

# Words that name a section of a site rather than the site. Kept to the
# documentation and site-furniture vocabulary, because that is what was
# measured: no organisation is named "user guide" or "changelog", while plenty
# are named "<Place> News" and "<Place> Community Centre" - so those words are
# deliberately absent from this list.
_SECTION_WORD_RE = re.compile(
    r"\b(?:docs?|documentation|user\s+guide|guides?|manual|handbook|"
    r"reference|api|wiki|knowledge\s*base|changelog|release\s+notes|"
    r"tutorials?|cookbook|getting\s+started|overview|index|homepage|"
    r"home\s+page|sitemap|search\s+results|faq|dashboard|log\s?in|sign\s?in|"
    r"my\s+account|checkout|basket|shopping\s+cart)\b", re.I)

# Words a line of copy joins its clauses with. Deliberately not "of", "the",
# "a", "an" or "and": those are the words real names are built out of - "Museum
# of Craft", "Boys & Girls Clubs of America" - and counting them would reject
# names for being names. Everything here addresses or qualifies a reader, which
# is what a marketing title does and what no organisation is called.
_COPY_WORD_RE = re.compile(
    r"\b(?:for|with|without|from|about|into|onto|your|yours|our|ours|my|mine|"
    r"you|we|us|they|them|who|whom|which|what|when|where|how|why|that|"
    r"is|are|was|were|be|been|will|can|could|should|must|more|than|because|"
    r"if|but|so)\b", re.I)

# Two, not one. "Habitat for Humanity" and "Center for Disease Control" each
# have one and each is a name; the two failures above have two apiece
# ("for" and "with"), and no name measured here has ever had two.
COPY_WORDS_THAT_MAKE_A_SENTENCE = 2


# Address labels that name a section of a site rather than the site. The
# leftmost label is what `brand.domain_token` holds and what the brand falls
# back to, and on `docs.<name>.<tld>` that is the documentation and not the
# project. Written as `comparison_key` output - letters and digits only - so
# the lookup matches however the label is punctuated.
SECTION_HOST_LABELS = frozenset({
    "docs", "doc", "documentation", "wiki", "help", "support", "kb",
    "knowledgebase", "faq", "forum", "forums", "blog", "news", "api", "dev",
    "developer", "developers", "app", "apps", "my", "portal", "account",
    "accounts", "login", "signin", "auth", "status", "cdn", "static",
    "assets", "media", "img", "images", "mail", "webmail", "mobile", "beta",
    "staging", "test", "demo", "old", "new", "web", "www2", "shop", "store",
})


def without_section_labels(labels):
    """`labels` with the section subdomains dropped.

    Everything is kept when nothing else is left: a site whose whole address is
    `docs.<tld>` really is called that, and returning nothing here would send
    the brand to the bare origin instead.
    """
    kept = [label for label in (labels or ())
            if comparison_key(label) not in SECTION_HOST_LABELS]
    return kept or list(labels or ())


def _not_a_section_label(token, labels):
    """The address label to fall back on when the leftmost one is a section."""
    value = str(token or "").strip()
    if not value or comparison_key(value) not in SECTION_HOST_LABELS:
        return value
    for label in labels or ():
        if comparison_key(label) not in SECTION_HOST_LABELS:
            return str(label).strip()
    return value


def _the_site_declares(candidate, declared):
    """Did the site assert this exact string as its own identity?"""
    return any(_cleaned_like(raw, candidate) for raw in declared or ())


def _section_word_the_address_does_not_spell(candidate, labels):
    """A section word in this candidate that no label of the address spells.

    The address is the escape hatch and it has to be: a site living at
    `tvguide.test` really is named for a guide, and one at `docs.<name>.test`
    is not named for its documentation. Comparison is containment on
    `comparison_key`, the same reading `the_site_spells` uses, so "guide" is
    spelled by `tvguide` and not by `polars`.
    """
    for match in _SECTION_WORD_RE.finditer(str(candidate or "")):
        word = comparison_key(match.group(0))
        if not word:
            continue
        if not any(word in comparison_key(label) for label in labels or ()):
            return match.group(0)
    return ""


def why_this_is_not_a_name(candidate, snapshot, declared=None):
    """Why this string cannot be the site's name, or "" if it can be.

    The string returned is the reason a finding or an evidence line prints, so
    it names the measurement rather than the rule.
    """
    value = str(candidate or "").strip()
    if not value:
        return "it is empty"
    declared = _declared_strings(snapshot) if declared is None else declared
    for raw in declared:
        if _cleaned_like(raw, value) and the_brand_slot_is_empty(raw):
            return ('the site declares it as "{}", which is a title template '
                    "with an empty brand slot rather than a name".format(
                        truncate(re.sub(r"\s+", " ", raw).strip(), 80)))
    if not _the_site_declares(value, declared):
        section = _section_word_the_address_does_not_spell(
            value, _name_evidence_labels(snapshot))
        if section:
            return ('it carries "{}", which names a section of a site rather '
                    "than the site, and no label of the address spells "
                    "it".format(section))
        joins = [m.group(0).lower() for m in _COPY_WORD_RE.finditer(value)]
        if len(joins) >= COPY_WORDS_THAT_MAKE_A_SENTENCE:
            return ('it reads as a line of copy rather than a name: it joins '
                    'its words with "{}"'.format('" and "'.join(joins[:2])))
    if not the_site_spells(value, _name_evidence_texts(snapshot),
                           _name_evidence_labels(snapshot)):
        return ("no title, heading, body text or address label on this site "
                "spells it")
    return ""


def _brand_as_published(brand):
    """The brand block with every name in it decoded, and equal names merged.

    Merging matters as much as decoding. `Tan&amp;Loom` and `Tan&Loom` are one
    name written twice by a theme that escapes its own output, and left as two
    keys of `declared_on` they are two names asserted as one site's identity -
    which is the medium finding one real report carried, against a site with one name.
    """
    out = dict(brand)
    for key in ("name", "source"):
        if isinstance(out.get(key), str):
            out[key] = as_published(out[key])
    for key in ("authoritative_variants", "alternate_names", "fallback_candidates"):
        values = out.get(key) or []
        if isinstance(values, list):
            out[key] = sorted({as_published(v) for v in values if isinstance(v, str)})
    candidates = out.get("name_candidates") or []
    if isinstance(candidates, list):
        merged, seen = [], set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate = dict(candidate)
            candidate["name"] = as_published(candidate.get("name"))
            if candidate["name"] in seen:
                continue
            seen.add(candidate["name"])
            merged.append(candidate)
        out["name_candidates"] = merged
    for key in ("declared_on", "declared_by"):
        mapping = out.get(key) or {}
        if not isinstance(mapping, dict):
            continue
        folded = {}
        for name, values in mapping.items():
            folded.setdefault(as_published(name), set()).update(
                v for v in (values or []) if isinstance(v, str))
        out[key] = {name: sorted(values) for name, values in sorted(folded.items())}
    return out


def _authoritative_in_order(brand):
    """The declared names, best source first, the way `crawl.py` ranked them.

    `declared_by` records which source asserted each name, so the order the
    crawl chose between them can be reconstructed here rather than guessed.
    """
    priority = {"jsonld:Organization": 0, "og:site_name": 1,
                "jsonld:Organization-off-home": 2, "og:site_name-off-home": 3}
    declared_by = brand.get("declared_by") or {}

    def rank(name):
        sources = declared_by.get(name) or []
        return min([priority.get(s, 8) for s in sources] or [8])

    return sorted([v for v in (brand.get("authoritative_variants") or []) if v],
                  key=lambda name: (rank(name), -len(name), name))


def _without_the_name(brand, rejected):
    """The brand block with a rejected string removed from every list it is in.

    Left in `authoritative_variants`, a rejected template string is still a
    second name asserted as the site's identity, and the naming-consistency
    check would report the disagreement between a name and the thing that was
    just ruled not to be one.
    """
    out = dict(brand)
    for key in ("authoritative_variants", "alternate_names", "fallback_candidates"):
        out[key] = [v for v in (out.get(key) or []) if v != rejected]
    out["name_candidates"] = [c for c in (out.get("name_candidates") or [])
                              if c.get("name") != rejected]
    for key in ("declared_on", "declared_by"):
        out[key] = {k: v for k, v in (out.get(key) or {}).items() if k != rejected}
    # Only where the crawl recorded which source said what. Recomputing from an
    # absent `declared_by` would replace a true list of sources with an empty
    # one and the naming check would then report "sources read: none" about a
    # site that declares its name in two places.
    if brand.get("declared_by"):
        out["authoritative_sources"] = sorted(
            {s for sources in out["declared_by"].values() for s in sources})
    return out


# How many crawled pages have to carry a declaration before it speaks for the
# site rather than for the page it sits on.
#
# Two measurements, and they are three orders apart. A Thai school declares its
# `Organization` name on 48 of its 60 pages, and the report addressed it
# throughout as `หน้าหลัก` - the Thai for "Home page", cut out of a page title
# - then built a knowledge-base collision finding against "Wikimedia main
# page" on it. A national charity declares an `Organization` name on exactly
# one of sixty pages: a store listing for a ring binder, naming the training
# subsidiary that runs the shop, which `crawl.py` demotes below the homepage's
# own title for that reason and must go on demoting.
#
# More than one page is the line between them. It is deliberately not a share
# of the crawl: a site that declares its name on its about page and its contact
# page and nowhere else has still said what it is called twice, in markup, and
# a title segment is a guess.
PAGES_A_DECLARATION_SPEAKS_FROM = 2

# The sources `crawl.py` records when the name was cut out of a page title:
# `title`, `title-part-0`, `title-part-1`, `title-long`.
_TITLE_DERIVED_SOURCE = "title"


def _declared_name_that_outranks_a_title(brand, why):
    """A name the site declares in markup, where the brand came from a title.

    `(name, source)`, or None where the title's own reading stands.

    `crawl.py` ranks a declaration found away from the homepage below the
    homepage's `<title>`, which is right about the ring binder above and wrong
    about every site whose identity markup is site-wide but whose homepage
    template omits it. What separates those two is how many pages carry the
    declaration, which is a question the ranking never asked.

    Restricted to `Organization` JSON-LD on purpose. `og:site_name` is a
    declaration too, and it is also the field themes paste a whole page title
    into - `crawl.py` records the published string there and reads a half of it
    as the name, so promoting from that list can adopt the title this rule
    exists to get away from. A homepage `og:site_name` already outranks a title
    where it exists.

    `why` is `resolve_brand`'s own reason function, so the two guards an
    earlier fix installed still hold: a declared name that is a title
    template with an empty brand slot is refused here as well, and so is one no
    part of the site spells.
    """
    if not str(brand.get("source") or "").startswith(_TITLE_DERIVED_SOURCE):
        return None
    declared_on = brand.get("declared_on") or {}
    declared_by = brand.get("declared_by") or {}
    for name in _authoritative_in_order(brand):
        sources = declared_by.get(name) or []
        if not any(s.startswith("jsonld:Organization") for s in sources):
            continue
        if len(declared_on.get(name) or []) < PAGES_A_DECLARATION_SPEAKS_FROM:
            continue
        if why(name):
            continue
        return name, "; ".join(sources)
    return None


def resolve_brand(snapshot):
    """`snapshot["brand"]`, with the name checked before anything adopts it.

    Called from `load_snapshot`, which is how every skill and the report reader
    reach a crawl. Doing it there rather than in each check is the point: a
    name that six skills read six times has to be one string, and the two
    failures above reached the report through four different checks each.

    Two things happen here, in this order. A name the site declares in its own
    markup outranks a segment split out of a page title - see
    `_declared_name_that_outranks_a_title` - and then whatever name is left is
    checked against the three rules above before anything adopts it.

    The snapshot file on disk is not rewritten. What `crawl.py` observed stays
    observed - a rejected string is kept under `rejected_names` with the
    measurement that rejected it, an outranked one under `outranked_names` -
    and only what the audit *adopts* changes.
    """
    brand = snapshot.get("brand")
    if not isinstance(brand, dict) or not brand:
        return brand
    brand = _brand_as_published(brand)
    declared = _declared_strings(snapshot)
    rejected = []

    def reason(candidate):
        return why_this_is_not_a_name(candidate, snapshot, declared)

    chosen = str(brand.get("name") or "").strip()
    brand["outranked_names"] = []
    promoted = _declared_name_that_outranks_a_title(brand, reason)
    if promoted and promoted[0] != chosen:
        name, source = promoted
        pages = len((brand.get("declared_on") or {}).get(name) or [])
        brand["outranked_names"] = [{
            "name": chosen,
            "source": brand.get("source"),
            "why": ('the site declares "{}" as its own name in {} on {} crawled '
                    "pages, and a name the site declares outranks a segment split "
                    "out of a page title".format(truncate(name, 60), source, pages))}]
        brand["name"] = chosen = name
        brand["source"] = source
    why = reason(chosen) if chosen else "it is empty"
    if not why:
        brand["rejected_names"] = []
        return brand

    # The name fell through. Everything the site could be called instead, in
    # the order the evidence justifies: what else it declares, then the words
    # it prints that its own address spells, then the guesses from its titles,
    # then the address label on its own.
    #
    # `domain_token` is the leftmost label, and on a documentation subdomain
    # that is the section and not the site: the real docs tree offered `docs`
    # here as its second candidate after "Frostvane user guide" was adopted from
    # the title. Section labels are dropped from both the search and the
    # fallback - but only while a label that is not one survives, because on a
    # site whose whole address is `docs.<tld>` the section word is all there is.
    token = _not_a_section_label(brand.get("domain_token"),
                                 _name_evidence_labels(snapshot))
    labels = without_section_labels(_name_evidence_labels(snapshot))
    spelled = ""
    for label in ([token] if token else []) + labels:
        spelled = name_the_site_spells(
            label, list(_name_evidence_texts(snapshot, include_body=False)))
        if spelled:
            break
    replacements = [(name, "; ".join((brand.get("declared_by") or {}).get(name) or [])
                     or "declared by the site")
                    for name in _authoritative_in_order(brand) if name != chosen]
    if spelled:
        replacements.append(
            (spelled, "the words the site prints that its own address spells"))
    replacements += [(name, "a name guessed from the site's own titles")
                     for name in sorted(brand.get("fallback_candidates") or [])
                     if name != chosen]
    if token and not re.match(r"^\d+$", token):
        replacements.append((token.replace("-", " ").strip().title(), "domain"))

    source = brand.get("source")
    while True:
        rejected.append({"name": chosen, "source": source, "why": why})
        brand = _without_the_name(brand, chosen)
        replacements = [(name, src) for name, src in replacements if name != chosen]
        if not replacements:
            # Nothing survived. The address is what is left, and it is what
            # `crawl.py` falls back to as well: an empty brand name is not
            # neutral, it makes the definition check hunt every page for a
            # sentence beginning with nothing and report that no page states
            # what the site is.
            chosen = strip_www(site_label(str(snapshot.get("origin") or "")))
            source, why = "the site's own address", ""
            break
        chosen, source = replacements[0]
        why = reason(chosen)
        if not why:
            break

    brand["name"] = chosen
    brand["source"] = source
    brand["rejected_names"] = rejected
    return brand


def pct(part, whole):
    """Percentage rounded to one decimal; 0.0 when the denominator is zero."""
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 1)


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


# `:443` on https and `:80` on http name the port the scheme already implies,
# so they are the same address as no port at all. Both spellings are legal and
# a server is free to put one in a `Location` header - a publisher's site
# redirected the bare domain to `https://www.example.test:443/`, and every URL
# the crawl recorded from there carried the port.
#
# Nothing downstream knew the two spellings were one host, and one report
# carried three findings that were all artefacts of the spelling: 36 pages
# accused of declaring a canonical "on a different domain" whose own example
# was a URL pointing at itself, and a "canonical URLs are split across more
# than one hostname" finding listing `www.example.test` and
# `www.example.test:443` as the two hostnames. The site's canonicals are
# relative and name no host at all.
#
# So the port comes off once, here, and everything built on these functions -
# comparison, deduplication, canonical-host grouping, and the URL strings the
# report prints - sees one address.
DEFAULT_PORTS = {"http": "80", "https": "443"}


def strip_default_port(netloc, scheme=None):
    """`www.example.test:443` -> `www.example.test` for an https URL.

    `netloc`, not `hostname`: the point is to remove the port, and
    `urlparse().hostname` has already dropped it along with any userinfo.

    With no `scheme` given - which is every caller that has only a bare host
    string in hand - either default port comes off. `http://host:443` is the
    one spelling that treats as equal a pair that is strictly not, and no
    server in the wild answers http on 443 while also answering it on 80.
    """
    netloc = netloc or ""
    host, separator, port = netloc.rpartition(":")
    if not separator or not port.isdigit():
        return netloc          # no port, or an IPv6 literal, or userinfo
    defaults = ({DEFAULT_PORTS[scheme]} if scheme in DEFAULT_PORTS
                else set(DEFAULT_PORTS.values()))
    return host if port in defaults else netloc


def strip_www(host):
    """The `www.`-free, default-port-free form of a host used for comparison.

    Callers pass either a `hostname` or a `netloc`, and a `netloc` carries the
    port - which is why the default port is dropped here rather than at each of
    the twenty-odd call sites that compare two hosts for equality.
    """
    host = strip_default_port(host or "")
    return host[4:] if host.startswith("www.") else host


def same_site(url_a, url_b):
    """True when two URLs share a registrable-ish host, ignoring `www.`.

    On the hostname, not on `netloc`, which carries the port. A page on
    `https://example.com` linking to `https://example.com:443/x` is the same
    site by any reading, and comparing `netloc` called it external - so the
    link was dropped from the frontier and a redirect to it was recorded as
    "redirects off this origin".
    """
    try:
        return strip_www((urlparse(url_a).hostname or "").lower()) == strip_www(
            (urlparse(url_b).hostname or "").lower()
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
    """Absolutise, drop the fragment, drop a default port, and fold default
    index documents to `/`.

    Query strings are preserved: on many sites they select real content.
    """
    try:
        # Inside the guard, not in front of it. `urljoin` parses too, so
        # `<a href="http://[::1">` - a malformed IPv6 literal, and ordinary
        # broken markup - raised `ValueError: Invalid IPv6 URL` one frame above
        # the except clause written to catch exactly that. One bad anchor
        # anywhere on any page ended the crawl with a traceback and no report.
        if base:
            url = urljoin(base, url)
        parts = urlparse(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    path = parts.path or "/"
    path = _INDEX_FILE_RE.sub("/", path) or "/"
    netloc = strip_default_port(parts.netloc, parts.scheme)
    return urlunparse((parts.scheme, netloc, path, parts.params, parts.query, ""))


# --------------------------------------------------------------------------
# One page, one identity
#
# A query parameter that records where a visitor came from does not change what
# page they arrived at. Three addresses on one retailer's site -
# `/?ref=nav-topALTM`, `/?ref=nav-topM`, `/?ref=nav-topSLW`, produced by three
# menu items pointing at one destination - were crawled as three pages, each
# declaring the same canonical, and the report then said "25 of 60 pages share
# a title". Thirteen of the sixty page slots that run had were spent on
# variants of pages already read, and the denominator under every site-wide
# share was inflated by the same thirteen.
#
# `normalise_url` deliberately keeps the query string, because on a great many
# sites it selects real content (`/search?q=`, `/product?id=`), and that is
# still right. The distinction is between a parameter that chooses what is
# shown and a parameter that records how the visitor got there. Only the second
# kind is dropped, and only for the purpose of asking "is this the same page" -
# the URL the report prints is still the address the site published.
#
# Named by shape wherever a shape exists, because the vendors are endless and a
# list of their names never will be:
#
#   `utm_*`          the campaign-tagging convention every analytics product
#                    reads, whoever wrote it
#   `*clid`          a click identifier minted by an ad network at click time;
#                    the suffix is the convention, the prefix is the network
#   `ref`, `source`  where the visitor came from, in the words sites use for it
#
# The short list at the end is the parameters that carry no shared shape. It is
# deliberately small: a parameter this audit has not met stays in the URL,
# which costs a page slot, where dropping a parameter that selects content
# would merge two different pages into one and hide whichever lost.
_TRACKING_PARAM_RE = re.compile(
    r"^(?:"
    r"utm_[a-z_]+"
    r"|[a-z]{1,8}cl[ik]?id"
    r"|ref|referrer|referer|source|src|from|via"
    r"|mc_[ce]id|_ga|_gl|igshid|epik|si|srsltid"
    # The selected option on a hosted store platform's product page. The page
    # is the same product page whichever swatch is preselected, and one
    # retailer's forty crawlable pages included the same jacket five times.
    r"|variant"
    r")$", re.I)


def is_tracking_parameter(name):
    """True for a query parameter that records where a visitor came from.

    Never for one that chooses what the page shows: `q`, `page`, `id` and
    everything else this pattern does not name stay in the URL.
    """
    return bool(_TRACKING_PARAM_RE.match((name or "").strip()))


def strip_tracking_parameters(url):
    """The same URL with only the parameters that choose content left on it.

    Order is preserved, so two addresses that differ only by a dropped
    parameter come out byte-identical and everything downstream compares them
    as equal without knowing this function exists.
    """
    try:
        parts = urlparse(url or "")
    except ValueError:
        return url
    if not parts.query:
        return url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not is_tracking_parameter(k)]
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params,
                       urlencode(kept), parts.fragment))


def page_identity(url):
    """The key under which two addresses are the same page, or None.

    `normalise_url`, then the navigation-source parameters off, then the host
    lower-cased and stripped of `www.` - the three things that differ between
    addresses serving one page. Anything asking "have we already read what is
    at this address" wants this and not the URL string.
    """
    normalised = normalise_url(url)
    if not normalised:
        return None
    normalised = strip_tracking_parameters(normalised)
    scheme, _, rest = normalised.partition("://")
    host, slash, path = rest.partition("/")
    return "{}://{}{}{}".format(scheme, strip_www(host.lower()), slash, path)


def origin_of(url):
    """`https://example.com` from any URL on that origin."""
    parts = urlparse(url if "://" in url else "https://" + url)
    scheme = parts.scheme or "https"
    return "{}://{}".format(scheme, strip_default_port(parts.netloc, scheme))


def site_label(url):
    """Bare host used as the `site` field of the report."""
    parts = urlparse(url if "://" in url else "https://" + url)
    return strip_default_port(parts.netloc, parts.scheme or "https") or url


def is_forbidden_path(url):
    """Guardrail: state-changing and private areas are never fetched.

    The query string counts. A GET to `/shop/?add-to-cart=9` adds an item to a
    basket on every store running the commonest e-commerce plugin, and reading
    only `urlparse(url).path` meant the one guard that makes this audit
    read-only could not see it.
    """
    try:
        parts = urlparse(url)
    except ValueError:
        return True          # unparsable, so unsafe: refuse it
    path = parts.path or "/"
    if _FORBIDDEN_PATH_RE.search(path):
        return True
    # A basket by position, not by word: `/basket` and `/checkout/basket` are
    # refused, a shop's `/c/basket` category is read. See `CART_WORDS`.
    if cart_path_word(path, _POSITIONAL_ONLY_CART_WORDS):
        return True
    # A store app rendering one visitor's state. See `_APP_PROXY_RE`.
    if _APP_PROXY_RE.search(path):
        return True
    # `api` and `graphql` at the first segment only. See `_ENDPOINT_ROOT_RE`:
    # matched anywhere, they cost a documentation tree its reference manual.
    if _ENDPOINT_ROOT_RE.search(path):
        return True
    return bool(parts.query and _STATE_CHANGING_QUERY_RE.search(parts.query))


def truncate(text, limit=280):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# A letter is not `[a-z]`, and a word is not a run of `\w`.
#
# Python's `\w` holds letters, digits and underscore, and no combining marks -
# a mark is not a letter. In an abugida the marks are how the vowels are
# written, so `\w` breaks every word at every vowel sign. A Hindi newspaper's
# code of ethics was counted at 3,541 words against a real 2,254, and the page
# was then reported as carrying a subheading only every 393 words, over the
# threshold of 300, when the true figure is 250 - under the threshold, and
# under the target the finding's own fix recommends. The finding existed
# because of the tokeniser and nothing else.
#
# The same assumption in two other places produced two more false findings in
# the same validation pass. A German page title split at the a-umlaut into "besch" and
# "ftigenvertretungen", neither of which appears in the body, and the page was
# reported as not delivering what its title promises. A Korean museum's name
# reduced to the empty string in a comparison key, so every Wikidata result
# compared equal to it and the report announced three organisations sharing a
# name that nothing shares.
#
# The ranges are read from Python's own Unicode tables at import rather than
# typed out here, so a script nobody has thought of is covered. The scan stops
# at U+3000, which is past every alphabet, abugida and abjad in the basic
# plane; the scripts above it - Han, Kana, Hangul syllables - write no
# combining marks.
def _combining_mark_ranges(limit=0x3000):
    ranges, start, previous = [], None, None
    for point in range(limit):
        if unicodedata.category(chr(point)).startswith("M"):
            if start is None:
                start = point
            previous = point
        elif start is not None:
            ranges.append((start, previous))
            start = None
    if start is not None:
        ranges.append((start, previous))
    return ranges


_MARKS = "".join(
    re.escape(chr(low)) if low == high
    else "{}-{}".format(re.escape(chr(low)), re.escape(chr(high)))
    for low, high in _combining_mark_ranges())

#: One letter of any script, including the marks that complete it. A union of
#: two classes rather than one, because `[^\W\d_]` is a negated class: a mark
#: added inside it is a mark *excluded*, which is the opposite of the point and
#: is what the first attempt at this did.
LETTER = r"(?:[^\W\d_]|[{}])".format(_MARKS) if _MARKS else r"[^\W\d_]"
_WORD_RE = re.compile(u"(?:{}|['\u2019-])+".format(LETTER))
_LETTER_RUN_RE = re.compile("{}+".format(LETTER))
_NOT_LETTER_OR_DIGIT_RE = re.compile("(?:(?!{})[^0-9])+".format(LETTER))


def letter_runs(text, minimum=1):
    """Every run of letters in any script, each mark kept with its letter."""
    return [run for run in _LETTER_RUN_RE.findall(text or "") if len(run) >= minimum]


def comparison_key(value):
    """A name reduced to its letters and digits, for comparing two spellings.

    Never empty for a name that has any letters in it. The version this
    replaces kept `[a-z0-9]`, which is the empty string for every name written
    in Hangul, Devanagari, Arabic, Greek or Cyrillic - and an empty key
    compares equal to every other empty key, so a check meant to find exact
    name matches matched everything it was shown.
    """
    return _NOT_LETTER_OR_DIGIT_RE.sub("", (value or "").strip().lower())


def word_count(text):
    return len([word for word in _WORD_RE.findall(text or "")
                if any(ch.isalnum() for ch in word)])


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
#
# The item and the article words in the languages the shops and publishers
# this audit meets actually write their addresses in. A Vietnamese cosmetics
# shop keeps every product at `/san-pham/<slug>` and every post at
# `/bai-viet/<slug>`; neither word was in any list, so fourteen product pages
# were typed `other`, the shop was told no product detail page was crawled,
# and its articles were graded as documents. One tuple each, read by the
# page-type patterns, the listing test and the section-root test alike, so the
# three cannot disagree about which word means "a post".
PRODUCT_SLUGS_ELSEWHERE = ("san-pham", "sanpham", "produk", "producto", "productos",
                           "produto", "produtos", "produit", "produits", "produkt",
                           "produkte", "prodotto", "prodotti", "urun", "urunler")
ARTICLE_SLUGS_ELSEWHERE = ("bai-viet", "baiviet", "tin-tuc", "tintuc", "artikel",
                           "berita", "noticias", "noticia", "articulos", "articulo",
                           "artigos", "actualites", "actualite", "notizie",
                           "nieuws", "haberler", "blogg", "blogue",
                           u"блог", u"بلاگ", u"مدونة", u"ブログ", u"博客",
                           u"블로그", u"บล็อก")

_URL_TYPE_SLUGS = (
    # The named forms as well as the bare words. `_DERIVED_SUFFIX` covers
    # `-policy` and not `-notice` or `-statement`, so `/help/privacy-notice`
    # typed as `other` - and an address printed in its footer then counted
    # towards "this site has separate places to visit", on a government site.
    ("legal", ("privacy", "privacy-policy", "privacy-notice", "privacy-statement",
               "terms", "terms-of-service", "terms-of-use", "terms-and-conditions",
               "tos", "legal", "legal-notice", "cookie", "cookies", "cookie-notice",
               "cookie-statement", "gdpr", "imprint", "disclaimer", "accessibility",
               "eula", "modern-slavery-statement")),
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
    # `story` as a whole segment: a fashion retailer's brand story lives at
    # `/<region>/global/story`, typed `article`, and was asked for a
    # publication date. The segment has to be the whole word, so a blog post
    # slugged `story-behind-the-range` is still not an about page.
    ("about", ("about", "about-us", "company", "who-we-are", "our-story", "our-team",
               "team", "mission", "our-history", "company-history",
               "our-mission", "our-values", "leadership", "story", "brand-story",
               "our-brand")),
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
                 "transcripts") + ARTICLE_SLUGS_ELSEWHERE),
    ("product", ("product", "products", "item", "p", "sku") + PRODUCT_SLUGS_ELSEWHERE),
    # "explore" and "shelf" are here because of a measured storefront
    # whose listing pages are `/explore/<shelf>`: no word in that address was
    # recognised, so all of them were typed `other` - the type that is in the
    # hygiene checks and out of the listing ones, so a shelf was graded as if
    # it were a document and asked for none of what a shelf is asked for.
    ("category", ("collection", "collections", "category", "categories", "catalog",
                  "catalogue", "shop", "browse", "explore", "shelf", "shelves")),
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
#
# `campus` is here for an education and healthcare estate, which had no word
# at all: every slug in this list names a place a business trades from, and a
# university's places are not shops. Four campus pages, each printing its own
# street as bare prose, were typed `other`, and the multi-location branch of
# that report went dark. It sits under the plural-or-qualified rule with the
# rest rather than in the plain list, so `/campuses` and `/our-campus` are
# places and a bare `/campus` - which is as often a product name or a training
# portal as it is an address - is not.
#
# `ward` is deliberately absent. A ward is a room inside one hospital, not a
# place the hospital trades from, and typing those pages `location` would read
# a single-site hospital as a chain - the same false multi-location headline
# `pages_with_their_own_address` exists to keep out. `site` is absent because
# it names no kind of place at all.
_PLURAL_OR_QUALIFIED_ONLY = {
    "location": ("shop", "store", "branch", "clinic", "venue", "office",
                 "studio", "salon", "outlet", "dealer", "stockist", "surgery",
                 "practice", "restaurant", "cafe", "hotel", "gym", "depot",
                 "campus"),
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

# A grouping word, then the name of the group. Every hosted platform this audit
# meets - store fronts, blogging engines, content management systems - builds
# the address of "everything filed under X" the same way, and the word in front
# of the handle is the site saying outright that the page is a list.
#
# Two measured failures, both on consumer brands, both from reading the handle
# as the name of one thing:
#
#   /collections/<handle>       On the commonest hosted store platform,
#                               `/products/<handle>` is the item and
#                               `/collections/<handle>` is the shelf it sits
#                               on. The shelf renders its items' prices and
#                               their quick-buy buttons, which is exactly what
#                               `_looks_like_product_detail` reads, so a
#                               collection of four jackets was typed `product`
#                               and the report told a retailer its product page
#                               carried no Product markup. It is not a product
#                               page; the markup it is missing is markup it
#                               must not have.
#   /blogs/<blog>/tagged/<tag>  A tag archive, typed `article` because `blogs`
#                               is an article slug and the path is too deep for
#                               the section-root rule. The fix offered was
#                               Article markup with a `headline` and a
#                               `datePublished` for a page that is a list of
#                               other pages' headlines.
#
# Anchored at the end of the path, so `/collections/all/products/<handle>` -
# the same platform's way of writing an item's address inside a collection - is
# still the item.
#
# Pagination is the second alternative and needs its own shape. `page` is the
# grouping word only when a section comes before it and a number after it:
# `/blog/page/2` is the second screen of an index, while a bare `/page/17` is
# how a content management system with opaque addresses names an ordinary page,
# and reading that as a listing cost an FAQ page its classification.
_FACETED_LISTING_RE = re.compile(
    r"(?:"
    r"/(?:collection|collections|category|categories|catalog|catalogue"
    r"|tag|tags|tagged|topic|topics|label|labels|author|authors"
    r"|brand|brands|vendor|vendors|type|types|archive|archives)/[^/]+"
    r"|/[^/]+/page/[0-9]+"
    r")/?$", re.I)


def is_faceted_listing(url):
    """True when the URL says the page lists everything under one heading.

    The site's own address scheme, not a guess from its text - which is the
    point, because the text of a listing is the text of the things it lists.
    """
    try:
        path = urlparse(url or "").path or "/"
    except ValueError:
        return False
    return bool(_FACETED_LISTING_RE.search(path.rstrip("/") or "/"))


# The grouping words that name one item rather than a shelf of them. The
# `product` row of `_URL_TYPE_SLUGS`, read as a set, because the question
# "which of these segments is the grouping word" is not one a slug regex over
# the whole path can answer.
_ITEM_ADDRESS_SLUGS = frozenset({"product", "products", "item", "items",
                                 "p", "sku", "skus"}) | frozenset(PRODUCT_SLUGS_ELSEWHERE)


def the_address_names_one_item(url):
    """Does this address say the page is one thing for sale?

    `/product/<slug>`, `/products/<handle>`, `/item/<sku>` - a grouping word
    followed by the name of one thing in the group. False for the grouping word
    on its own, which is the way in to the shelf, and false for a faceted
    listing, a search address or an archive index, each of which says "a list"
    whatever else is in the path.

    Read only where the page delivered nothing to read. On a page with content
    the content decides, which is what `_looks_like_product_detail` and
    `retype_from_the_page_itself` are for; this is the answer for the shell
    that delivers no content at all, where the address is the only evidence
    there is.
    """
    return bool(the_item_grouping_word(url))


def the_item_grouping_word(url):
    """The segment of this address that says the page is one item, or "".

    The word itself rather than a yes, because the evidence line a reader is
    shown has to quote the site's own address scheme back to them: `/product/`
    and `/sku/` are different claims by different shops and neither of them is
    "the address looks like a product page".
    """
    try:
        path = unquote(urlparse(url or "").path or "/").rstrip("/").lower()
    except ValueError:
        return ""
    if is_faceted_listing(url) or is_search_result_page(url):
        return ""
    if _ARCHIVE_INDEX_RE.search(path):
        return ""
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2 or segments[-1] in _ITEM_ADDRESS_SLUGS:
        return ""
    for segment in segments[:-1]:
        if segment in _ITEM_ADDRESS_SLUGS:
            return segment
    return ""


# The schema.org types that name one thing for sale. `ProductGroup` is one
# item in several sizes or colours, and its sizes arrive as `Product` nodes
# nested under `hasVariant` - variants of the one thing, not other things.
_ONE_ITEM_JSONLD = frozenset({"product", "productgroup", "individualproduct",
                              "productmodel"})


def declares_one_product(page):
    """Does this page's own markup name one product, at an item's address?

    The price count is the measured separator between an item and a shelf, and
    it is the right test where the page says nothing else. It is the wrong one
    where the page does: an Indian clothing shop's `/products/<slug>` declares a
    `ProductGroup` for the one blouse on it, with its five sizes as variants,
    and prints 32 different prices - every one of them from the cross-sell
    carousels under the blouse. The page was set aside as "a grid of things for
    sale", and the report said no product detail page was crawled, beside two
    findings counting "11 product pages this crawl read".

    True where the address names one item (`the_address_names_one_item`) and
    the page's top-level product nodes carry one name between them. A node
    nested under another - a variant, a related item, a list entry - is not a
    second declaration of what this page is. A shelf that declares a Product
    per tile carries many names, and a shelf's address is not an item's, so
    this cannot promote one.
    """
    page = page or {}
    if not the_address_names_one_item(page.get("url") or ""):
        return False
    names, groups = set(), 0
    for node in page.get("jsonld") or []:
        if not isinstance(node, dict) or node.get("_nested_in"):
            continue
        declared = node.get("@type")
        declared = declared if isinstance(declared, list) else [declared]
        types = {str(t).split("/")[-1].lower() for t in declared if t}
        if not types & _ONE_ITEM_JSONLD:
            continue
        if "productgroup" in types:
            groups += 1
        name = " ".join(str(node.get("name") or "").split()).lower()
        if name:
            names.add(name)
    if groups == 1:
        return True
    return len(names) == 1

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
# Money, in the notations the world actually writes it in.
#
# The pattern this replaces required the symbol before the digits and a dot
# before the cents. Most of Europe and Latin America writes neither.
#
# A Spanish manufacturer's product page reads "PVPR (IVA incl.): 396,88 EUR /
# Online Desde (IVA incl.) 309,74 EUR" with the euro sign after each number.
# The extractor's entire list of prices for that page was `['\u20ac 09']` - the
# euro sign, then the colour code "09" from "309,74 \u20ac 09 - Blanco" - and the
# report then said the declared Offer price of 309.74 "is not among the prices
# shown on the page (9)", about a page that shows it twice.
#
# The currency list had the same shape of hole: five symbols and eight ISO
# codes, none of them Korean. A national museum's exhibition-fee table -
# 8,000 won, 6,000 won, 4,500 won - was invisible, so the audit stated that
# "nothing on this site is for sale", which is a claim about the world drawn
# from the absence of a symbol from a tuple.
#
# The hole the hand-written symbol list left was the same shape a third time.
# A footwear brand prints "MRP Rs. 399 Rs. 399" on every product page. The
# symbol tuple carried the rupee sign and not the two letters that country
# writes far more often, so the only figure any product page recorded was the
# 500 out of a site-wide banner reading "FREE SHIPPING ON ORDERS ABOVE 500" -
# and nineteen pages were then reported, at high severity, as declaring an
# Offer price that "is not among the prices shown on the page".
#
# A list of symbols cannot be finished by adding to it, so this one is not a
# list any more. Unicode already classifies a character as a currency sign -
# general category Sc - and asking it is the whole rule: the rupee sign, the
# rupee sign with two strokes, the taka, the riyal, the fullwidth yen a
# Japanese page writes instead of the halfwidth one, and every sign added
# after this was written. 63 characters, read once at import.
CURRENCY_SYMBOLS = "".join(
    chr(code) for code in range(0x20000)
    if unicodedata.category(chr(code)) == "Sc")
# The three-letter codes. Only codes that are not also ordinary English words
# belong here: the branches below read "<number> <code>" as well as "<code>
# <number>", so adding MAD, ALL, TOP or PEN would read "5 mad" and "3 top" as
# money. What was missing was every market between Karachi and Jakarta, plus
# most of Africa and the Caucasus.
_CURRENCY_WORDS = (r"USD|EUR|GBP|INR|AUD|CAD|SGD|AED|JPY|KRW|CNY|CHF|SEK|NOK|DKK|PLN|"
                   r"CZK|HUF|RON|TRY|BRL|MXN|ARS|CLP|ZAR|NZD|HKD|TWD|THB|IDR|MYR|PHP|"
                   r"VND|RUB|SAR|QAR|ILS|EGP|NGN|KES|"
                   r"PKR|LKR|NPR|BDT|MMK|KHR|LAK|BND|MOP|MNT|"
                   r"UAH|KZT|UZS|AZN|GEL|RSD|BGN|ISK|"
                   r"COP|UYU|PYG|GTQ|JMD|TTD|"
                   r"TND|DZD|JOD|KWD|BHD|OMR|LBP|GHS|TZS|UGX|MUR|"
                   # The unit written as a word in its own script.
                   r"\uc6d0|\u5143|\u5186|\u0440\u0443\u0431|zl|kr|R\$|"
                   r"\u0930\u0941\u092a\u092f\u0947|\u0e1a\u0e32\u0e17|"
                   # Unit names that are not English words. "Won", "crown" and
                   # "pound" are, so they are not here.
                   r"rupees?|rupiah|ringgit|baht|taka|dirhams?|riyals?|"
                   # The dong as Vietnam prints it: "339.000 đ", "339,000đ",
                   # "99.000 VNĐ". The letter is a Latin letter and not a
                   # currency sign, so the Unicode rule below never saw it,
                   # and a cosmetics shop printing a price on every product
                   # page was told "0 pages that publish a price". It is
                   # never a word on its own in Vietnamese - every word it
                   # starts carries a vowel after it - so the `\b` the
                   # branches put after a unit keeps it to the price.
                   u"vnđ|đ")
# The everyday abbreviations, which are not ISO codes and are what these
# markets actually print: Rs. and Rs across South Asia, Rp in Indonesia, RM in
# Malaysia, RMB alongside the yuan sign.
#
# Two restrictions, both of which the ISO codes do not need. They are read
# case-sensitively, because a lowercase "rs" or "rm" is a run of letters
# inside ordinary prose and an abbreviation for money is written with its
# capital. And they are read only in front of the figure, never behind it,
# because "12 rm" is twelve rooms and "3 Rs" is a teaching slogan, while
# "Rs. 399" and "RM 500" can be nothing else.
_CURRENCY_ABBREVIATIONS = r"RMB|Rs|Rp|RM"
# A number with grouping and decimals in either convention: 1,234.56 or
# 1.234,56 or 1 234,56 (the space is the one French and Scandinavian use).
#
# The grouping run is `+` and not `*`, and that one character was a real
# defect. With `*` the first alternative matches one to three digits and stops,
# and in the currency-symbol branch nothing follows the number to force
# backtracking into the second alternative - so a shop's own page price
# `₹ 1349` was read as `134`, and `₹ 1199` as `119`. The report then raised two
# high-severity findings saying the site's markup contradicts its own page,
# about a page and a markup block that agree exactly.
#
# It only ever bit the symbol branch: the `Rs`/`Rp` branch ends in `\b`, which
# forces the backtrack, which is why `Rp 25000` was always read whole. `+`
# makes the first alternative fail on an ungrouped number so `\d+` takes it,
# and every grouped notation this audit meets is unchanged - measured on
# `₹1,349`, `Rp 1.198.000`, `RM 12.90`, `$12.00`, `309,74 EUR`, `₹ 12,999.00`.
_MONEY_NUMBER = r"\d{1,3}(?:[.,\u00a0\u202f ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_PRICE_RE = re.compile(
    # The letters in front of the sign, where a place shares a sign with
    # somewhere else and says which one it means: S$, HK$, NT$, US$, A$. Read
    # case-sensitively, and only where a letter does not run into them, so
    # that the quoted price on a page reading "MRP<sign>399" is the figure and
    # its sign rather than the tail of the word in front of it.
    r"(?:(?:(?<![A-Za-z])(?-i:[A-Z]{{1,2}}))?[{sym}]\s?(?:{num})"  # $12.99, S$12
    r"|(?:{num})\s?[{sym}]"             # number first: 309,74 EUR
    r"|\b(?:{num})\s?(?:{words})\b"    # number then a code or unit word
    r"|\b(?:{words})\s?(?:{num})\b"    # code then number: R$ 670
    # The local abbreviation, in front of the figure only.
    r"|(?-i:\b(?:{abbr}))[.]?\s?(?:{num})\b)"      # Rs. 399, RM 500, Rp 25.000
    .format(sym=CURRENCY_SYMBOLS, num=_MONEY_NUMBER, words=_CURRENCY_WORDS,
            abbr=_CURRENCY_ABBREVIATIONS),
    re.I,
)


# A currency figure that states a condition rather than a price.
#
# The same class of problem as the gift ask below, arriving from the other
# side. A footwear brand's site-wide banner reads "FREE SHIPPING ON ORDERS
# ABOVE 500", and while its product pages' real prices were invisible that
# threshold was the only figure any of them recorded: the pricing check
# reported nineteen Offer prices as contradicting the page, and two engagement
# checks then said "19 of 19 crawled pages that publish a price" about a
# delivery rule. The figure is real and the sentence is not about what
# anything costs.
#
# Two readings, in the discipline `not_a_telephone_number` uses: the word
# immediately in front of the figure has to make it a floor ("above", "over",
# "at least"), and the sentence around it has to be about an order rather than
# about an item. Either alone would throw away real prices. A product page's
# "Add to cart 16.00" carries the second and none of the first; a pricing tier
# reading "Enterprise: above 500 a month" carries the first and none of the
# second; both are prices and both survive. "From" is deliberately absent from
# the first list, because "from 399" is how a starting price is written.
_THRESHOLD_WORD_RE = re.compile(
    r"(?:above|over|worth|exceeding|at least|minimum(?: of)?|min\.)\W{0,4}$", re.I)
_THRESHOLD_SUBJECT_RE = re.compile(
    r"orders?|carts?|baskets?|purchases?|subtotals?|spend|shipping|shipped|"
    r"delivery|postage|checkout", re.I)
# How much of the sentence in front of the figure is read for both. Long
# enough for "free shipping on all prepaid orders above", short enough that
# the previous sentence is out of reach.
THRESHOLD_WINDOW = 80


def _figure_is_a_threshold(text, start):
    """Do the words in front of this figure make it a floor rather than a price?"""
    before = text[max(0, start - THRESHOLD_WINDOW):start]
    return bool(_THRESHOLD_WORD_RE.search(before)
                and _THRESHOLD_SUBJECT_RE.search(before))


# A currency figure that reports a company's results rather than a price.
#
# A holding company's shareholder letter opens "Operating earnings in 1977 of
# $21,904,000, or $22.54 per share, were moderately better than anticipated",
# and its press-release list reads "... to Acquire <a house builder> for $8.5
# Billion". Both were read as the site stating what it charges: the site was
# typed an online shop, told "the site sells something", and its pricing fact
# was an earnings figure. Nothing is for sale on it.
#
# Three readings, each one a figure no shop prints as its price: an amount per
# share; an amount in billions or trillions; and an amount whose sentence, just
# in front of it, names a line of a financial statement - earnings, revenue,
# net income, assets, dividends, book or market value. "Revenue share from $10
# a month" is the case the last one has to leave alone, which is why it reads
# only the few words straight in front of the figure and only the nouns an
# accounts page is made of.
_PER_SHARE_AFTER_RE = re.compile(
    r"^\s*(?:(?:billion|bn|million|mn|thousand)\s+)?(?:per|a|each)\s+"
    r"(?:class\s+[a-z]\s+|diluted\s+|basic\s+|common\s+|ordinary\s+)?shares?\b", re.I)
_VERY_LARGE_AFTER_RE = re.compile(r"^\s*(?:billion|bn|trillion|tn)\b", re.I)
_FINANCIAL_LINE_BEFORE_RE = re.compile(
    r"\b(?:earnings|net\s+income|income\s+from|revenues?|turnover|profits?|losses|"
    r"ebitda|assets|liabilities|shareholders'?\s+equity|net\s+worth|book\s+value|"
    r"market\s+(?:value|capitali[sz]ation)|dividends?|cash\s+flows?|operating\s+"
    r"(?:income|profit|result))\b[^.!?]{0,40}$", re.I)
FINANCIAL_LINE_WINDOW = 60


def _figure_is_a_financial_result(text, start, end):
    """Does this figure report a result, a share value or a deal size?"""
    after = text[end:end + 40]
    if _PER_SHARE_AFTER_RE.match(after) or _VERY_LARGE_AFTER_RE.match(after):
        return True
    before = text[max(0, start - FINANCIAL_LINE_WINDOW):start]
    return bool(_FINANCIAL_LINE_BEFORE_RE.search(before))


# A question is a heading that ends in a question mark. The English opener
# list stays, because it catches a heading whose mark was dropped, but it
# cannot be the only test: a Spanish manufacturer's page of 66 questions -
# each one opening with an inverted question mark and closing with a plain one
# - was called no FAQ at all, a real finding was suppressed, and the report
# recommended creating a page the site already had.
#
# The gate that consumes this still requires four such headings and half of
# all H2s, which is what keeps a single "Ready to start?" from making a
# landing page into an FAQ.
_QUESTION_HEADING_RE = re.compile(
    r"^\s*(what|why|how|when|where|who|which|can|do|does|is|are|will|should)\b.*[?\uff1f]\s*$"
    r"|^\s*\u00bf.*[?\uff1f]\s*$"
    # The catch-all branch: any heading that ends in a question mark. The word
    # in front of the mark has to contain a letter, because a question ends in
    # a word and this branch was matching dates. A book catalogue writes an
    # uncertain death year as "Bierce, Ambrose, 1842-1914?"; three of that
    # page's 511 author headings do, three is the threshold downstream, and an
    # alphabetical list of authors and titles with no question on it was
    # reported as a page of questions and answers needing FAQPage markup.
    r"|^(?=.{6,140}$).*[^\W\d_][^\s]*[?\uff1f]\s*$",
    re.I,
)


# A page that answers 200 while telling the visitor there is nothing here.
#
# These are matched against the <title> and the first heading only, never the
# body: a help page explaining what a 404 is would otherwise be mistaken for
# one, and a shop's search results legitimately say "no results found" in the
# body of a page that is working correctly.
# The same statement in the languages this marketplace already names
# elsewhere. A national museum answers a missing address with HTTP 200, the
# title "Notice" in Korean and one line of text - "the web page cannot be
# found" - and the report said the response "says nothing about the page being
# missing". Short, whole phrases only: each says "this page does not exist" and
# nothing else, so none turns up in an ordinary page's prose.
_SOFT_404_PHRASES = (
    # Korean
    "페이지를 찾을 수 없", "페이지가 존재하지 않", "존재하지 않는 페이지", "찾을 수 없는 페이지",
    # Japanese
    "ページが見つかりません", "ページは見つかりません", "ページが存在しません",
    "ページは存在しません", "お探しのページは",
    # Chinese
    "页面不存在", "頁面不存在", "网页不存在", "網頁不存在", "找不到页面", "找不到頁面",
    "页面未找到", "頁面未找到", "找不到网页", "找不到網頁",
    # Thai
    "ไม่พบหน้า", "หน้านี้ไม่มีอยู่",
    # Arabic
    "الصفحة غير موجودة", "الصفحة المطلوبة غير موجودة", "لم يتم العثور على الصفحة",
    # Persian
    "صفحه یافت نشد", "صفحه مورد نظر یافت نشد", "صفحه پیدا نشد",
    # Hebrew
    "הדף לא נמצא", "העמוד לא נמצא", "הדף המבוקש לא נמצא",
    # Russian
    "страница не найдена", "страница не существует", "такой страницы нет",
    # German
    "seite nicht gefunden", "seite wurde nicht gefunden", "seite existiert nicht",
    # French
    "page introuvable", "page non trouvée", "page n'existe pas",
    # Spanish
    "página no encontrada", "no se encontró la página", "no se ha encontrado la página",
    "página no existe",
    # Portuguese
    "página não encontrada", "página não existe",
    # Italian
    "pagina non trovata", "pagina non esiste", "pagina non è stata trovata",
    # Dutch
    "pagina niet gevonden", "pagina bestaat niet",
    # Indonesian
    "halaman tidak ditemukan", "halaman tidak ada",
    # Vietnamese
    "không tìm thấy trang", "trang không tồn tại",
)

_SOFT_404_MARKERS = (
    "page not found", "not found", "404", "no longer available", "nothing to see here",
    "page doesn't exist", "page does not exist", "page unavailable", "oops",
    "sorry, we can't find", "we couldn't find that page",
) + _SOFT_404_PHRASES

# What a missing-page notice's body may be read for. The title and heading
# markers above include the bare "404" and "oops", which in running text are a
# product code and an apology; the body is held to whole phrases.
_SOFT_404_TEXT_MARKERS = (
    "page not found", "page doesn't exist", "page does not exist",
    "sorry, we can't find", "we couldn't find that page", "error 404", "404 error",
    "404 not found",
) + _SOFT_404_PHRASES

# A missing-page notice is short. Its body is read as well as its title and
# heading when the whole of it is under this many characters, because the
# museum's notice has no heading and a title that says only "notice"; on a
# longer page the same phrase is more likely a sentence about some other page.
SOFT_404_TEXT_MAX_CHARS = 600



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
    haystack = "{} {}".format(page.get("title") or "", heading).lower().replace(u"’", "'")
    if any(marker in haystack for marker in _SOFT_404_MARKERS):
        return True
    # The body, where the whole page is a notice: short, and with no heading of
    # its own. A page with a heading that does not say "missing" is a page about
    # something - an article on designing error pages quotes "page not found"
    # in its first line. See `SOFT_404_TEXT_MAX_CHARS`.
    if heading.strip():
        return False
    text = " ".join(str(page.get("body_text") or page.get("text") or "").split())
    if not text or len(text) > SOFT_404_TEXT_MAX_CHARS:
        return False
    text = text.lower().replace(u"’", "'")
    return any(marker in text for marker in _SOFT_404_TEXT_MARKERS)


# schema.org types that mean "this node is the organisation itself".
#
# Four definitions of this idea were in the tree, and they disagreed:
# structured-data-audit's `ORG_TYPES` (13 members), freshness's
# `ORG_IDENTITY_TYPES` (8), and two inline literals in freshness and crawl.py.
# The 8-member copy was the stale one, missing `brand`, `financialservice`,
# `governmentorganization`, `medicalbusiness` and `professionalservice` - so on
# a site marked up as `ProfessionalService` or `MedicalBusiness` the telephone
# and postcode consistency checks found no organisation node at all and
# reported nothing, while the description check sixty lines below found it and
# reported normally. One file, two answers about one page.
ORG_IDENTITY_TYPES = frozenset({
    "organization", "localbusiness", "corporation", "store", "restaurant",
    "ngo", "educationalorganization", "governmentorganization", "onlinestore",
    "professionalservice", "medicalbusiness", "financialservice", "brand",
    # A museum's identity node is `["TouristAttraction", "Museum"]`, carrying
    # name, url, logo, description, address, telephone and sameAs - everything
    # this check asks for. Without these seven it reads as no identity markup
    # at all, and "No Organization or LocalBusiness markup anywhere on the
    # site" fires at high confidence on every museum, university, theatre,
    # library and charity that marks itself up correctly.
    "museum", "touristattraction", "collegeoruniversity", "library",
    "nonprofitorganization", "performinggroup", "sportsorganization",
})

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

    The rule used to be "fold a page onto a canonical the crawl reached", and
    that missed the case it most needed to catch. A retailer's three menu items
    pointed at one destination through three navigation-source parameters, so
    the crawl held `/?ref=nav-topALTM`, `/?ref=nav-topM` and `/?ref=nav-topSLW`
    and never the bare address any of them named. Each of the three declared
    that address canonical; none of the three *was* it; so no fold happened and
    the report counted three pages sharing a title where the site has one page.

    What the old rule was protecting against is real and is kept: a site that
    mass-declares a canonical nobody has read must not have its whole crawl
    hidden. Folding a *group* onto one of its own members cannot do that,
    because every group keeps a page. So the question is no longer "was the
    canonical reached" but "which of these crawled pages are one page", and a
    group is folded down to a single entry - the canonical itself when the
    crawl has it, otherwise the group's first page.

    Two pages that differ only by a navigation-source parameter are one page
    even when neither declares a canonical, which is what `page_identity`
    settles and what no reading of `rel=canonical` could.
    """
    groups, order = {}, []
    for page in pages:
        url = page.get("url") or ""
        own = page_identity(url) or url
        canonical = (page.get("canonical") or "").strip()
        key = (page_identity(canonical) or own) if canonical else own
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((own, page))
    kept = []
    for key in order:
        members = groups[key]
        # The canonical target itself when the crawl holds it, so the entry
        # that survives is the address the site names rather than whichever
        # tagged variant happened to be fetched first.
        chosen = next((page for own, page in members if own == key), members[0][1])
        kept.append(chosen)
    return kept

# What `page_extract` writes in `contact_facts["street_source"]` when the only
# street it could find on a page came from the header or the footer. The string
# is the extractor's, and it is named here because this file is where it is
# compared against; `tests/test_two_matchers.py` fails the build if the two ever
# stop being the same string.
CHROME_ADDRESS_SOURCE = "in the site header or footer"


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
    seen, pages = set(), []
    for page in snapshot.get("pages") or []:
        key = _own_address_key(page)
        if key is None or key in seen:
            continue
        seen.add(key)
        pages.append(page)
    return pages


def _own_address_key(page):
    """What identifies the postal address this page prints of its own, or None.

    One page at a time, so that a caller asking about a subset of pages - the
    ones typed `location` - is not told a page has no address merely because
    another page printed the same one first.
    """
    # A privacy notice, a terms page and a set of search results all print
    # an address without being a place anyone visits. On a government site
    # three such pages - a department's profile, a privacy notice and a
    # freedom-of-information listing - each carried a different postcode,
    # met the three-address threshold exactly, and the report told GOV.UK
    # it looks like "a business with separate branches" that should publish
    # LocalBusiness markup per branch. The page types are already known;
    # they were simply not consulted.
    if page.get("page_type") in ("legal", "careers"):
        return None
    if is_search_result_page(page.get("url") or ""):
        return None
    facts = page.get("contact_facts") or {}
    # A street the page carries only in its own furniture is the site's
    # address, not this page's. `street_source` records which of the five
    # places the extractor found it, and the header and the footer are last
    # for exactly this reason: they are the same run of characters on every
    # page. Counting them makes one office N branches, which is the
    # high-severity multi-location headline a single-office shop was given,
    # and it is the error `postcode_source` already guards against a few
    # lines below. A chain whose branch pages state nothing of their own now
    # contributes nothing rather than collapsing onto the head office.
    if (facts.get("street_source") or "") == CHROME_ADDRESS_SOURCE:
        return None
    street = (facts.get("street_hint") or "").strip().lower()
    postcode = (facts.get("postcode_hint") or "").strip().lower()
    if not street or not postcode:
        return None
    # Keyed on the postal code, and on the street too when the code did
    # not come from beside it.
    #
    # The code alone was the whole key, because the street hint is a fuzzy
    # substring match and one head-office address came out three slightly
    # different ways across three pages of a single-site fixture, counting
    # as three branches. That reasoning holds only while the code belongs
    # to the page. `ADDRESS_REGION_SELECTORS` ends in `footer`, so where a
    # page prints its own street but no code of its own, the extractor
    # falls back to the code in the site-wide footer - and four campus
    # pages, each with a different street, were handed one head-office
    # code, collapsed to a single place, and a four-campus university was
    # read as a business with one address. The whole multi-location branch
    # of that report went dark.
    #
    # So: a code read from beside the street identifies the place on its
    # own, and the fuzzy street is ignored as before. A code borrowed from
    # the furniture identifies nothing by itself, and the street it was
    # joined to is what tells two pages apart.
    key = re.sub(r"[^a-z0-9]", "", postcode)
    if (facts.get("postcode_source") or "").startswith("in an address region"):
        key += "|" + re.sub(r"[^a-z0-9]", "", street)
    return key


def location_pages_with_an_address(snapshot):
    """Pages typed `location` that print a postal address of their own.

    One per distinct address, first page first. A page is typed `location` by
    its address alone - `/locations/`, `/find-us`, `/stores` - and the path
    says what the page is about, not whose place it is. A language foundation's
    site keeps its event calendar at `/events/<calendar>/locations/<id>/`: four
    of those were crawled, each a venue listing for somebody else's user-group
    meeting with no street, no postcode and no place markup, all four under one
    page title. The path alone made that site a local business at high
    confidence, with the reason "the site declares a place with a postal
    address that visitors go to" - true of nothing the crawl held - and every
    tailored action after it was advice for a shop with premises.

    A branch page of the site's own prints the branch's address; that is what
    a visitor opens it for. So a `location` page counts as a place the site has
    only when it does, read by the same test `pages_with_their_own_address`
    applies to every page - not the furniture, not a legal page.
    """
    seen, pages = set(), []
    for page in snapshot.get("pages") or []:
        if page.get("page_type") != "location":
            continue
        key = _own_address_key(page)
        if key is None or key in seen:
            continue
        seen.add(key)
        pages.append(page)
    return pages


def is_multi_location(snapshot):
    """True when the site describes more than one place you can visit."""
    if len(declared_locations(snapshot)) > 1:
        return True
    # Two `location` pages each printing an address of their own, not two
    # pages whose path says `location`. See `location_pages_with_an_address`.
    if len(location_pages_with_an_address(snapshot)) > 1:
        return True
    return len(pages_with_their_own_address(snapshot)) >= BRANCH_PAGE_MINIMUM


# --------------------------------------------------------------------------
# The loose reading of an address
#
# `_street_hint` in `page_extract.py` is the strict one: a street with a house
# number, or a named street with a postal code within a few words of it. It has
# to be strict, because what it finds is quoted back to a site as its own
# address, and a run of advisory ticket numbers quoted as a postal address is a
# defect this marketplace has already shipped once.
#
# It is also, unavoidably, a shape drawn from British and American addresses.
# One real site's contact page prints
#
#     <Plus code>, RT.10/RW.11, <district>, <district>, <city> City, <province>
#
# which is how a very large number of Indonesian businesses write where they
# are: an administrative hierarchy, no street number, no postal code in the
# line. The strict matcher cannot see it, and the report then published "no
# postal address appears in the page text or in the page markup" - with the
# same address in the same document's citation table.
#
# So this is the second reading, and it exists only to refuse a denial. It
# never supplies the address the report quotes; `_street_hint` keeps that job
# and keeps its precision. What this recognises is the *shape* an address has
# in scripts and countries the strict list was not built from: a comma-
# separated hierarchy of place parts, at least one of which carries digits.
# Nothing here is a word list, so it does not stop working in the next country.
#
# Three discriminators against an ordinary comma list, and each one is a real
# list this matched before it had them:
#
#   two place parts of more than one word    "Open Monday, Tuesday, Wednesday,
#                                            Thursday, 9 to 5" has one
#                                            ("Open Monday", and only because
#                                            the run swallowed the word in
#                                            front of it). Every address
#                                            written as a hierarchy has at
#                                            least two.
#   a digit part that is not a bare year     "Terms, Privacy, Cookie Policy,
#                                            Contact Us, 2024" is a footer with
#                                            a copyright year on the end.
#   no part longer than a few words          a run of clauses is prose with
#                                            commas in it.
#
# What keeps a navigation menu out of this entirely is upstream: `body_text`
# joins block elements with a space, so a menu arrives as spaced words and
# never as a comma-separated run. A comma in `body_text` came from inside one
# text node, which is prose or a printed address line.
ADDRESS_SHAPE_MIN_PARTS = 4
ADDRESS_SHAPE_MIN_NAMED_PLACES = 2
ADDRESS_SHAPE_MAX_PART_WORDS = 6
ADDRESS_SHAPE_MAX_CODE_DIGITS = 8
ADDRESS_SHAPE_MIN_CHARS = 25
ADDRESS_SHAPE_MAX_CHARS = 250

# A part that is nothing but a four-digit year. A copyright line is the
# commonest way a page prints digits next to a comma list, and it is not an
# address element anywhere.
_BARE_YEAR_RE = re.compile(r"^(?:1[5-9]|20)\d{2}$")

# A part of the run is anything up to the next comma, semicolon or line break.
# The full stop is deliberately allowed inside one, because `RT.10/RW.11` and
# `No. 12` are how two of these hierarchies write their own numbers.
_ADDRESS_RUN_RE = re.compile(
    u"[^,;\n\r]{2,40}(?:\\s*,\\s*[^,;\n\r]{2,40}){3,8}")


def _address_part(part):
    """One part of the run, with anything before a label colon removed.

    The run is found by a regular expression, so its first part reaches here
    with whatever preceded the first comma still attached: "Sizes: Small,
    Medium, Large, Extra Large, 42 EU" arrived with a first part of "Sizes:
    Small" and was read as a two-word place name. The label is not part of the
    value in any of these, and dropping it is also what turns "Alamat kami:
    <plus code>" into the code the page is actually printing.
    """
    return part.rsplit(":", 1)[-1].strip()


def _is_a_named_place(part):
    """Two or more words, two of which are words rather than figures.

    "West Cilandak", "South Jakarta City", "Barangay San Roque" and "12 High
    Street" all pass; "42 EU", "020 7123 4567" and "Small" do not. It is the
    one test that separates an address hierarchy from a list of sizes.
    """
    return len(part.split()) > 1 and len(letter_runs(part, 2)) > 1


def _is_an_address_code(part):
    """A house number, a block, a postal code or a plus code - not a phone.

    Digits, not a bare year, and not the nine-or-more digits of a telephone
    number: "Home, About Us, Our Services, Contact, 020 7123 4567" is a footer
    and the only thing numeric in it is the number to ring.
    """
    digits = sum(1 for ch in part if ch.isdigit())
    return bool(digits) and digits <= ADDRESS_SHAPE_MAX_CODE_DIGITS and not _BARE_YEAR_RE.match(part)


def reads_like_an_address(text):
    """A comma-separated run of place parts, or "".

    Deliberately loose, and the errors are meant to fall this way: a run that
    is not an address silences a claim the audit was making from an incomplete
    list, which costs a sentence. A run that is an address and is not
    recognised costs a false statement about somebody's business.

    Not for quoting. What comes back is only ever used to decline.
    """
    if not text:
        return ""
    for run in _ADDRESS_RUN_RE.finditer(text):
        candidate = " ".join(run.group(0).split())
        if not (ADDRESS_SHAPE_MIN_CHARS <= len(candidate) <= ADDRESS_SHAPE_MAX_CHARS):
            continue
        parts = [_address_part(p) for p in candidate.split(",")]
        parts = [p for p in parts if p]
        if len(parts) < ADDRESS_SHAPE_MIN_PARTS:
            continue
        # A part longer than a few words is a clause, and a run of clauses is
        # prose with commas in it rather than an address.
        if any(len(p.split()) > ADDRESS_SHAPE_MAX_PART_WORDS for p in parts):
            continue
        if not any(_is_an_address_code(p) for p in parts):
            continue
        if sum(1 for p in parts if _is_a_named_place(p)) < ADDRESS_SHAPE_MIN_NAMED_PLACES:
            continue
        return candidate
    return ""


_COMMERCE_JSONLD_TYPES = frozenset({
    "product", "productgroup", "productmodel", "offer", "aggregateoffer",
    "onlinestore", "store", "individualproduct",
})

# Page types where a currency figure is the site quoting its own price rather
# than mentioning somebody else's. Shared, so `sells_something` and the pricing
# check cannot come to different conclusions from the same page.
# `collection` is not a page type this codebase emits - `detect_page_type`
# calls `/collections/*` a `category` - but the fork of this set in
# fact-extractability-audit carried it, so it is kept here to make the two
# provably identical rather than merely equal today.
# A currency figure is not always a price. Charities, museums and campaign
# sites write more figures than shops do, and most of them are asks: "a gift of
# GBP 25 could provide a family with clean water for a month", "donate USD
# 500". Reading those as the site quoting its own price made a medical charity
# a seller, opened every pricing check on it, and produced a high-severity
# finding that it "never states its pricing" with a fix reading "contact sales
# for a quote".
#
# The page type cannot settle this. A donation ask lives on the homepage, and
# so does a design studio's "Projects start at 18,000 GBP". Only the words
# around the figure can, so that is what is read: a figure is a price unless
# the sentence holding it is asking for a gift, and once a page mixes the two,
# a figure counts only when its sentence also says what something costs.
_GIFT_RE = re.compile(
    r"donat|donor|fundrais|philanthrop|gift aid|bequest|charitable"
    r"|(?:your|a|every|monthly|one-off|regular|small) gift"
    r"|gifts? (?:of|to)"
    r"|pledg|sponsor a |your contribution|chip in|give (?:today|monthly|now)",
    re.I)

_COST_RE = re.compile(
    r"pric|cost|tariff|charged? at|from |starts? (?:at|from)"
    r"|(?<![a-z])fees?(?![a-z])|(?<![a-z])rates?(?![a-z])"
    r"|per (?:month|year|week|night|hour|day|person|seat|user|licen[cs]e)"
    r"|[+] ?vat|incl[.] vat|excl[.] vat|/mo(?![a-z])|/yr(?![a-z])|a month|a year"
    r"|subscri|(?<![a-z])plans?(?![a-z])|(?<![a-z])buy(?![a-z])"
    r"|add to (?:cart|bag|basket)|in stock|checkout",
    re.I)


def quotes_its_own_price(text):
    """Does this text state what the site charges, as opposed to what it wants?

    Both are currency figures and no pattern can tell them apart on their own.
    What separates them is the sentence: an ask names the giving, a price names
    the buying.
    """
    # `find_prices`, not the raw pattern, so a page whose only figure is a
    # free-delivery floor is not read as a page that sells at that figure.
    priced = [s for s in sentences(text) if find_prices(s, limit=1)]
    if not priced:
        return False
    plain = [s for s in priced if not _GIFT_RE.search(s)]
    if not plain:
        return False                      # every figure here is an ask
    if len(plain) == len(priced):
        return True                       # no ask anywhere near a figure
    return any(_COST_RE.search(s) for s in plain)


# --------------------------------------------------------------------------
# Pages whose content is a list of other pages
# --------------------------------------------------------------------------

# One question, one answer. Two skills each carried their own
# `_is_listing_page`, built from different signals, so a blog index could be a
# listing to `engagement-audit` and an ordinary article to
# `fact-extractability-audit` - and one report then declined the wall-of-text
# rule on that page while telling the same page to put its answer first. The
# union of both sets of signals lives here and both skills ask it.
LISTING_SEGMENTS = frozenset({
    "blog", "blogs", "news", "articles", "article", "posts", "post", "insights",
    "stories", "resources", "guides", "press", "updates", "journal", "episodes",
    "podcast", "podcasts", "transcripts", "archive", "archives", "index", "all",
    "latest", "browse", "overview", "list", "listing", "category", "categories",
    "tag", "tags", "author", "authors", "events", "library", "case-studies",
}) | frozenset(ARTICLE_SLUGS_ELSEWHERE)

# Half the subheadings on the page also appearing as link labels on it. Below
# that, a page that happens to link to a few of its own sections is not an
# index.
LISTING_HEADING_SHARE = 0.5

# A paragraph this long is the page saying something, rather than a caption or
# a date line under a link. Ten words, because the line this has to reject is
# "Published 3 November 2025 - 4 min read" and the line it has to keep is a
# short sentence of ordinary reporting. The threshold only matters on a page
# with fewer than three subheadings, where the stronger index test applies.
LISTING_OWN_PROSE_WORDS = 10

_LISTING_PATH_RE = re.compile(
    r"/(?:blog|news|posts?|articles?|stories|archives?|category|categories"
    r"|tags?|topics?|latest|browse|search|collections?|index|"
    + "|".join(re.escape(slug) for slug in ARTICLE_SLUGS_ELSEWHERE) + r")"
    r"(?:/page/[0-9]+)?/?$", re.I)

_LISTING_JSONLD_TYPES = frozenset({
    "collectionpage", "itemlist", "searchresultspage", "blog", "webfeed",
})

_YEAR_SEGMENT_RE = re.compile(r"^(?:19|20)[0-9]{2}$")


def listing_key(text):
    """One spelling for a heading or a link label, so the two can be compared."""
    return " ".join((text or "").split()).lower()[:100]


# A page that is mostly links and little of its own writing. A conglomerate's
# news section is one page per year - `/news/2019news.html` - each an H2 and
# forty links to that year's releases, about a hundred words in all, and the
# 27 of them were typed articles: the report said "27 of 27 article pages have
# no Article markup" and a site-kind rule called the company a publication on
# the strength of them. An article carrying forty links carries hundreds of
# words around them; a list of links carries its labels and little else.
LINK_LIST_MIN_LINKS = 15
LINK_LIST_MAX_WORDS_PER_LINK = 5


def reads_as_a_link_list(page):
    """Is this page mostly a list of links with little prose of its own?

    Reads the links of the page's own content region where the record has
    them, so a site's menu cannot make every page on it a list.
    """
    links = page.get("links") or {}
    content_links = links.get("main_internal_links")
    if content_links is None:
        content_links = links.get("internal") or []
    count = len(content_links)
    if count < LINK_LIST_MIN_LINKS:
        return False
    words = page.get("word_count")
    if words is None:
        words = word_count(page.get("body_text") or page.get("text") or "")
    if words > count * LINK_LIST_MAX_WORDS_PER_LINK:
        return False
    return not _writes_prose_of_its_own(page)


def is_listing_page(page):
    """True for a page whose job is to point at other pages.

    Five ways in, and a page needs only one of them. A blog index's headings
    are the titles of the posts it lists, so every rule that measures prose -
    words between subheadings, whether the first paragraph answers the
    question, whether the page states a price - is measuring the wrong thing
    there. Both real firings of the wall-of-text rule were index pages, each
    told to "add a subheading every 3 to 5 paragraphs" about a page whose
    subheadings are its post titles.
    """
    url = page.get("url") or ""
    if page.get("page_type") == "category":
        return True
    if is_search_result_page(url):
        return True
    if {t.lower() for t in page.get("jsonld_types") or []} & _LISTING_JSONLD_TYPES:
        return True
    path = urlparse(url).path or ""
    if _LISTING_PATH_RE.search(path):
        return True
    parts = [p for p in path.split("/") if p]
    if parts and len(parts) <= 2:
        last = parts[-1].lower()
        if last in LISTING_SEGMENTS or _YEAR_SEGMENT_RE.match(last):
            return True
    # What a list of posts looks like from a snapshot: most of the subheadings
    # are also link labels on the same page.
    headings = page.get("headings") or {}
    titles = [listing_key(h) for h in (headings.get("h2") or []) + (headings.get("h3") or [])
              if (h or "").strip()]
    labels = {listing_key(link.get("text"))
              for link in ((page.get("links") or {}).get("internal") or [])}
    labels.discard("")
    if not titles:
        return False
    linked = sum(1 for title in titles if title in labels)
    # Three subheadings was the floor, and a monthly archive listing one post
    # has one. Below the floor the stronger test applies instead: every
    # subheading is a link label *and* the page writes no prose of its own.
    # A page that is nothing but other pages' titles is an index however few
    # of them there are.
    if len(titles) < 3:
        return linked == len(titles) and not _writes_prose_of_its_own(page)
    return linked >= len(titles) * LISTING_HEADING_SHARE


def _writes_prose_of_its_own(page):
    """Does this page carry a paragraph that is its own writing?"""
    for paragraph in (page.get("paragraphs") or []):
        text = paragraph if isinstance(paragraph, str) else (paragraph or {}).get("text") or ""
        if word_count(text) >= LISTING_OWN_PROSE_WORDS:
            return True
    return False


PRICE_BEARING_TYPES = frozenset({
    "pricing", "product", "category", "collection", "home", "location",
})


# The sections a site files its writing under, and the words a page reporting
# to shareholders is titled with. A page in one of them may print a figure; it
# is reporting one, not charging it.
#
# A holding company's press releases sit one year to a page at
# `/news/<year>news.html`, and a list of links is a listing, which is a type
# that bears prices - so the one release headline reading "... to Acquire <a
# house builder> for $8.5 Billion" made the company a seller, and the report
# read it as an online shop. A Vietnamese cosmetics shop's article index at
# `/en/articles` mentions a 10,000 VND voucher, and the report named that index
# as the first of the pages that "sell something".
_WRITING_SECTION_SEGMENTS = frozenset(
    slug for page_type, slugs in _URL_TYPE_SLUGS
    if page_type in ("article", "press") for slug in slugs) | frozenset({
        "releases", "press-releases", "news-releases", "pressreleases", "letters",
        "shareholder-letters", "annual-report", "annual-reports", "investors",
        "investor-relations"})
_REPORT_TO_SHAREHOLDERS_RE = re.compile(
    r"\b(?:share|stock)holders?\b|\bannual\s+reports?\b|\b(?:press|news)\s+releases?\b"
    r"|\binvestor\s+relations\b|\bquarterly\s+(?:reports?|results|earnings)\b"
    r"|\b(?:chairman|chairwoman|ceo)'?s?\s+letter\b", re.I)


def a_page_of_writing(page):
    """Is this page filed with the site's writing, or a report to its owners?

    Read from the address and from the page's own title and H1 - never from
    its prose, where every shop mentions its investors somewhere. A faceted
    shop address is never one: `/collections/<handle>` is a shelf whatever the
    handle says.
    """
    page = page or {}
    url = page.get("url") or ""
    if is_faceted_listing(url):
        return False
    try:
        path = unquote(urlparse(url).path or "/").lower()
    except ValueError:
        path = "/"
    if any(segment in _WRITING_SECTION_SEGMENTS for segment in path.split("/") if segment):
        return True
    names = [str(page.get("title") or "")] + [
        str(h) for h in ((page.get("headings") or {}).get("h1") or [])]
    return any(_REPORT_TO_SHAREHOLDERS_RE.search(name) for name in names)


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
    product detail page, a pricing page, commerce markup, or a price on a page
    whose job is prices. Donation tiers are prices; a charity that publishes
    them is not thereby a shop, so the pricing *check* stays quiet either way
    and only the framing changes.

    The fourth signal used to read any page's text, and that put the stray
    figure back in charge of the question the first three exist to settle
    honestly. A law firm's insights article reading "the threshold for stamp
    duty land tax rose to £250,000" made the firm a seller; so did a charity's
    "a gift of £25 could provide a family with clean water for a month". Both
    were then told "the site never states its pricing in plain text", with a
    fix instructing them to publish figures on a pricing page. A number in an
    article is not what anything costs, and no pattern can tell the difference
    - but the *page* it sits on can, which is the same reasoning the pricing
    check itself already applies one level down.
    """
    pages = pages if pages is not None else (snapshot.get("pages") or [])
    for page in pages:
        if page.get("page_type") == "product":
            return True
        if {t.lower() for t in page.get("jsonld_types") or []} & _COMMERCE_JSONLD_TYPES:
            return True
        # A `pricing` page-type is a URL shape, not a transaction. `subscribe`,
        # `membership`, `plan`, `plans` and `rates` all resolve to it, so a free
        # newsletter with `/subscribe`, a club with `/membership` and a public
        # library with `/plans` were each read as sellers - and then told, at
        # HIGH severity, that they "never state their pricing", with a fix
        # reading "contact sales for a quote". That is the same false positive
        # the first three signals were added to stop, arriving through the page
        # type instead of through a stray figure. A pricing page counts when it
        # shows a figure, like every other page here.
        # The delivered text, and then the state blob behind a shell.
        #
        # "Does this site sell anything" gates the whole pricing family, and
        # getting it wrong silences checks rather than adding a finding. On a
        # client-rendered site with no browser available - the default on a
        # machine without Playwright - the delivered document is a container, so a
        # studio quoting its project rates only inside `__NEXT_DATA__` read as
        # a site with nothing for sale, and the finding that its rate card is
        # locked in a PDF could not fire on exactly the kind of site where
        # that matters. `shell_state_text` is recorded only for thin pages and
        # is used only for questions about which facts the site holds, never
        # for how its prose reads.
        # A list of posts or press releases, or a letter to shareholders, is
        # not a page whose job is prices. See `a_page_of_writing`.
        if page.get("page_type") in PRICE_BEARING_TYPES and not a_page_of_writing(page) and (
                quotes_its_own_price(page.get("body_text") or "")
                or quotes_its_own_price(page.get("shell_state_text") or "")):
            return True
    return False


# --------------------------------------------------------------------------
# What kind of thing is this site?
#
# `sells_something` was the only question of this shape the codebase could ask,
# and every other piece of advice was written as if the answer to all the rest
# were yes. What that assumed, in one sentence: every site is a company, with
# premises, customers, a founding story and a marketing department. Four
# measured failures from one real run, on four sites where it is not true:
#
#   a command-line JSON processor   "The site never states its founding facts
#                                   in plain text", with a paste-ready fix
#                                   reading 'X was founded in <year> in
#                                   <place>' - about a program. And "The site
#                                   never states its location or service area",
#                                   fix 'X serves customers across <regions>'.
#   a national weather service      told to claim a company page on a
#                                   professional network and a profile on a
#                                   startup-funding database.
#   a software heritage society     told its homepage has no call to action,
#                                   judged against opening verbs drawn from
#                                   retail, while its links read "Join the
#                                   mailing list", "Browse the old source code"
#                                   and "Read papers".
#
# None of those findings is wrong about the facts. Every one is wrong about
# what the site is, and a reader who meets one of them stops reading - which
# costs the report every true finding underneath it.
#
# Built from what the site's own structure says, never from a list of names. A
# name list cannot generalise and would be a way of hard-coding the six sites
# this was measured on. The signals are: a registry-controlled address suffix,
# the identity type the site declares in its own markup, whether anything is
# for sale, whether there is a place to visit, whether there is a team or a
# careers page, whether there is a repository and a licence, and how the
# crawled pages divide between documentation, articles and everything else.
#
# The answer is allowed to be "undetermined", and that is the important part.
# Guessing wrong is what produced the failures above, so a weak answer must not
# silence anything: `might_be` returns True for an undetermined site and for a
# low-confidence one, and gating advice on it leaves that advice exactly as it
# is today. Only a confident answer suppresses anything.
# --------------------------------------------------------------------------

# The closed vocabulary. Small on purpose: each value has to name a group whose
# advice genuinely differs, or it is a distinction that costs more than it pays.
LOCAL_BUSINESS = "local-business"          # a place you can walk into
ONLINE_SELLER = "online-seller"            # sells, with nowhere to visit
ORGANISATION = "organisation"              # a company or non-profit, no shop,
                                           # no premises on the site
PROJECT = "project"                        # a program or product that is not
                                           # an organisation at all
PUBLIC_BODY = "public-body"                # government, agency, public service
PERSONAL_OR_ACADEMIC = "personal-or-academic"   # a person, a university, a lab
PUBLICATION = "publication"                # its output is articles

SITE_KINDS = (LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PROJECT,
              PUBLIC_BODY, PERSONAL_OR_ACADEMIC, PUBLICATION)

# The empty string, so `if kind.kind:` reads as "was anything decided".
UNDETERMINED = ""


class SiteKind:
    """What kind of thing a site is, how sure that is, and on what evidence.

    Two methods, and which one to use is the whole of the interface:

      might_be(...)      gates advice. True when the site could be one of these
                         kinds - including when nothing was determined and when
                         the determination is weak. Advice guarded by it fires
                         exactly as it does today on every site this cannot
                         classify, and goes quiet only where the site's own
                         structure says the advice does not apply.
      is_certainly(...)  asserts. True only on a determination this would
                         defend. For wording a finding, never for hiding one.
    """

    __slots__ = ("kind", "confidence", "signals")

    def __init__(self, kind=UNDETERMINED, confidence="low", signals=()):
        self.kind = kind
        self.confidence = confidence
        self.signals = tuple(signals)

    def __repr__(self):
        return "SiteKind({!r}, {!r}, {!r})".format(
            self.kind, self.confidence, self.signals)

    def __eq__(self, other):
        if isinstance(other, SiteKind):
            return (self.kind, self.confidence, self.signals) == (
                other.kind, other.confidence, other.signals)
        return NotImplemented

    def __hash__(self):
        return hash((self.kind, self.confidence, self.signals))

    @property
    def determined(self):
        """A kind was decided and the evidence is worth acting on."""
        return bool(self.kind) and self.confidence in ("high", "medium")

    def might_be(self, *kinds):
        """True unless this site is confidently something else.

        An undetermined kind means "do not gate on this", so it answers True to
        everything - which is what keeps a classifier failure from deleting
        advice rather than merely failing to tailor it.
        """
        if not self.determined:
            return True
        return self.kind in kinds

    def is_certainly(self, *kinds):
        return self.determined and self.kind in kinds

    def why(self):
        """The evidence, as a phrase a finding can print."""
        return ", ".join(self.signals)


# Address suffixes a registry controls. Nobody can buy one, which makes them
# the strongest single statement a site makes about what it is - stronger than
# any markup, because markup is written by whoever built the theme.
#
# Held as labels rather than as domain strings, because the pattern is the
# shape: a role label, then a two-letter country. That covers the national
# spellings this audit has met and the ones it has not, without a list of
# countries and without naming a single real site.
# One missing label did real damage: `lg` is Japan's label for local
# government - every city, town and village sits under
# `<name>.<prefecture>.lg.jp` - and without it a Japanese city of
# 1.6 million people was classed as a business and told to "claim the company
# record too - a business database entry and an employer profile".
#
# The rule was right and the list under it was short, so the list was written
# out rather than waiting for the next country to turn up on a real site. Each
# label below is one a national registry controls and nobody can buy:
#
#   `lg` local government and `ed` schools, Japan; `nhs`, `police`, `mod` and
#   `parliament`, the United Kingdom; `nic`, India's National Informatics
#   Centre, which hosts most of its government; `gv` Austria; `gub` Uruguay;
#   `jus` and `leg`, Brazil's judiciary and legislature; `re` and `res`,
#   research institutes in Korea and India; `int`, the treaty organisations.
#
# The two-letter country rule below is what makes this safe: `nic` is only read
# as government in `<something>.nic.in`, never in a name someone registered.
_PUBLIC_LABELS = frozenset({"gov", "mil", "govt", "gouv", "gob", "gub", "go",
                            "gc", "gv", "lg", "nhs", "police", "mod", "nic",
                            "parliament", "jus", "leg", "int",
                            "admin", "bund", "public"})
_ACADEMIC_LABELS = frozenset({"edu", "ac", "sch", "uni", "ed", "re", "res"})


# Which of those labels are top-level domains in their own right. Only these
# four, and the distinction is not pedantry: `re` is Korea's research label and
# also Reunion's country code, so reading every role label in final position
# turned `<any name>.re` into a research institute. A label that is not a TLD
# can only ever be evidence in the middle of an address.
_REGISTRY_TOP_LEVEL = frozenset({"gov", "mil", "edu", "int"})


def _registry_suffix_kind(host):
    """"public-body", "personal-or-academic" or "" from the address alone."""
    labels = [label for label in strip_www((host or "").lower()).split(".") if label]
    if len(labels) < 2:
        return ""
    last, second = labels[-1], labels[-2]
    if last in _REGISTRY_TOP_LEVEL:
        if last in _PUBLIC_LABELS:
            return PUBLIC_BODY
        if last in _ACADEMIC_LABELS:
            return PERSONAL_OR_ACADEMIC
    # A role label under a two-letter country code, which is how most of the
    # world spells the same thing.
    if len(last) == 2:
        if second in _PUBLIC_LABELS:
            return PUBLIC_BODY
        if second in _ACADEMIC_LABELS:
            return PERSONAL_OR_ACADEMIC
    return ""


_PUBLIC_BODY_JSONLD = frozenset({
    "governmentorganization", "governmentservice", "governmentbuilding",
    "governmentoffice", "governmentpermit", "citystate", "administrativearea",
})

_ACADEMIC_JSONLD = frozenset({
    "collegeoruniversity", "educationalorganization", "researchorganization",
    "researchproject",
})

# --------------------------------------------------------------------------
# The institutions a registry suffix misses
#
# A New Hampshire town's public library was classified `personal-or-academic`,
# and the proactive recommendations then told a library to "claim or update the
# profiles that apply to you: your researcher identifier, your institution's
# staff directory, the repository host your code or data lives on", and to
# repeat its boilerplate in "your researcher profile, and every conference or
# speaker biography". A town library has no researcher identifier and sends
# nobody to conferences.
#
# The cause is that the address rule is the only thing in this classifier that
# knows what a public body is, and the library's address ends `.net`. A public
# library, a museum, a school, a place of worship and a town office are a
# public body whatever their address ends in, and both of the other things this
# classifier already reads say so on those sites.
#
# Two readings, and the second one is where the care goes:
#
#   markup   the institution's own schema.org type. `Library`, `Museum`,
#            `PlaceOfWorship`, `CityHall`, `School` are declarations of what a
#            body *is*, in the same way `GovernmentOrganization` is. A company
#            that sells software to libraries declares `Organization` and
#            `Product`; it does not declare itself a Library.
#
#   the name the site calls itself by, and only that - the brand name, the
#            names it declares in its identity markup, the segments of its
#            homepage title and its homepage H1. Never the body text: the words
#            "library" and "school" appear on plenty of commercial sites, in a
#            customer list, a case study or a footer, and a rule that read
#            prose would classify every vendor selling to the public sector as
#            part of it.
#
#            The shapes below are institution names rather than the bare word:
#            "<town> Public Library", "Library of <place>", "Town of <place>",
#            "<place> Historical Society", "<place> School District". A vendor
#            is not named that way, and the vendor words below veto the reading
#            outright, so "Ashcombe Library Systems Inc" is not a library.
#
# The name reading also has to be corroborated by the structure: nothing for
# sale and no basket. That is what separates the institution from the business
# named after one - a museum's trading arm, a bookshop called "The Old School".
# --------------------------------------------------------------------------

_CIVIC_JSONLD = frozenset({
    "library", "librarysystem", "museum", "archiveorganization",
    "placeofworship", "church", "catholicchurch", "mosque", "synagogue",
    "hindutemple", "buddhisttemple", "cityhall", "school", "highschool",
    "elementaryschool", "middleschool", "preschool", "firestation",
    "policestation",
})

# An institution's name has a shape: a place plus what the body is, or the body
# plus "of" plus a place. The bare noun is deliberately not enough on its own
# except where the noun names nothing else - a synagogue, a mosque, a cathedral.
#
# The civic adjective is not part of that shape, and requiring one is what this
# pattern got wrong. Most public libraries are named after the place they stand
# in and nothing else: a New Jersey town library reached the academic rules, its
# WordPress author markup made it `personal-or-academic`, and its report told a
# town library to claim "your researcher identifier" and to repeat its
# boilerplate in "every conference or speaker biography". The comment above this
# pattern describes that exact bug being fixed for a New Hampshire library; the
# fix covered `.gov` suffixes and `Library` markup, and the common case - a
# placename, then the word - still fell through both.
#
# So `<Placename> Library` and `<Placename> Museum` are added, with the same
# care the rest of this pattern takes about the other direction. Four things
# have to hold before a bare placename counts, and each blocks a different way
# of being wrong:
#
#   the word before the noun is not a determiner  "The Library" is a bar or a
#                                                 co-working space, not a body
#   the word before the noun does not name what   "Component Library", "Pattern
#     is collected (`_COLLECTION_NOUN_RE`)        Library", "Photo Library"
#   no trading word anywhere in the string        "Ashcombe Library Systems Inc"
#   nothing for sale and no basket path           the museum's trading arm
#
# The last two were already the guard and stay exactly as they were.
_CIVIC_NAME_RE = re.compile(
    r"\b(?:public|memorial|free|town|city|village|county|community|district|"
    r"borough|township|parish|regional|municipal|state|athenaeum)\s+"
    r"librar(?:y|ies)\b"
    r"|\blibrar(?:y|ies)\s+(?:of\b|district\b|board\b|system\b|commission\b)"
    # A placename and the word, which is how most public libraries and many
    # town museums are named. The leading word must be three letters or more,
    # so "My Library" and a one-letter initial cannot supply it.
    r"|\b[^\W\d_][\w'’-]{2,}\s+(?:librar(?:y|ies)|museum)\b"
    r"|\b(?:museum|archives?)\s+of\b|\b(?:city|town|county|state|national|"
    r"regional|historical|heritage|art|science|children'?s)\s+museum\b"
    r"|\bhistoric(?:al)?\s+society\b"
    r"|\b(?:town|city|village|borough|township|county|municipality|parish|"
    r"district)\s+of\s+\w"
    r"|\b(?:town|city|village|borough|parish|county)\s+"
    r"(?:hall|office|council|clerk|assembly|administration)\b"
    r"|\b(?:public\s+schools?|school\s+district|board\s+of\s+education|"
    r"schools?\s+authority)\b"
    r"|\b(?:elementary|middle|high|primary|secondary|grammar|preparatory|"
    r"nursery|infant|junior)\s+school\b"
    r"|\bschool\s+of\s+\w"
    r"|\b(?:cathedral|synagogue|mosque|masjid|gurdwara|monastery|abbey|"
    r"congregation|diocese|parish\s+church)\b"
    r"|\b(?:baptist|methodist|lutheran|presbyterian|episcopal|orthodox|"
    r"catholic|anglican|community|memorial|first|st\.?|saint)\s+"
    r"(?:church|chapel|temple)\b"
    # "St Aidan's Church", "Saint Mary Magdalene Chapel": the dedication sits
    # between the honorific and the noun, so the two cannot be adjacent.
    r"|\b(?:st|saint)\.?\s+[^\W\d_][\w'’-]*(?:\s+[^\W\d_][\w'’-]*)?\s+"
    r"(?:church|chapel|parish|school|academy)\b"
    r"|\b(?:church|chapel)\s+of\s+\w"
    r"|\b(?:fire|police)\s+department\b"
    r"|\b(?:parks?\s+and\s+recreation|recreation\s+department)\b",
    re.I)

# Words that say a name belongs to a business, whatever institution it is named
# after. Read from the same string as the pattern above and vetoing it, so a
# company selling software to libraries is not a library and a museum's trading
# arm is not a museum.
_TRADING_NAME_RE = re.compile(
    r"\b(?:software|systems?|solutions?|technolog\w*|consult\w*|holdings?|"
    r"labs?|saas|platform|analytics|ventures?|capital|partners?|associates|"
    r"agency|marketing|shop|store|supplies|supply|inc\.?|llc|l\.l\.c\.|ltd\.?|"
    r"limited|corp\.?|corporation|gmbh|pty|plc|s\.a\.|b\.v\.)\b", re.I)

# Words that, put in front of "library" or "museum", name what is collected
# rather than where the building is. A design system's homepage H1 reads
# "Component Library" and a stock photography site's reads "Image Library";
# both are capitalised, neither carries a trading word, and neither has a
# basket - so all three of the other guards pass them and this is the one that
# has to refuse. A determiner in the same position is refused for the same
# reason: "The Library" and "Our Museum" name no place.
_COLLECTION_NOUN_RE = re.compile(
    r"\b(?:the|a|an|our|my|your|this|that|their|its|his|her"
    r"|component|pattern|code|source|icon|font|type|typeface|colou?r|swatch"
    r"|media|photo|picture|image|video|audio|sound|music|sample|loop|clip"
    r"|stock|asset|content|template|design|ui|ux|js|javascript|python|java"
    r"|ruby|rust|css|html|api|class|standard|reference|prompt|model|dataset"
    r"|link|resource|document|plugin|widget|snippet|script|game|toy|tool"
    r"|shader|texture|preset|effects?|brush|recipe|test|example)\s+"
    r"(?:librar(?:y|ies)|museum)\b", re.I)


def _reads_as_a_proper_name(text):
    """Is this run of words a name, or a line of copy that contains one?

    "Ashcombe Public Library" and "Software for public libraries" both match
    the pattern above; only the first is capitalised the way a name is. Filler
    words are exempt because a name is written "Museum of Craft", and a script
    with no capitals passes because `upper()` leaves those letters alone -
    which is the right answer, not an accident: this must never be the reason a
    non-Latin institution is refused.
    """
    words = [w for w in re.findall(r"[^\W\d_]+", text or "")
             if len(w) >= 3 and w.lower() not in NAME_FILLER_WORDS]
    return bool(words) and all(w[0] == w[0].upper() for w in words)


def names_a_civic_institution(value):
    """Is this string the name of a public library, museum, school, place of
    worship or town office - rather than of a business named after one?"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or _TRADING_NAME_RE.search(text):
        return False
    if _COLLECTION_NOUN_RE.search(text):
        return False
    match = _CIVIC_NAME_RE.search(text)
    return bool(match) and _reads_as_a_proper_name(match.group(0))


# --------------------------------------------------------------------------
# A national institution, named in its own language
#
# A national library and archive writes its name in Persian - "the Documents
# and National Library Organisation of <country>" - in its title and its
# Organization markup, and links from its homepage to the head of state's
# site, the supreme leader's site and the culture ministry's. The report
# called it "a company or non-profit that does not sell online" and told it
# to claim a business database entry and an employer profile. Every pattern
# above reads English words, and `.ir` is not a suffix a registry reserves.
#
# Two readings, both from what the site says about itself:
#
#   its name   the national library, archive, museum or a ministry, in the
#              site's own language - read from the same strings the civic
#              reading reads, and never from prose. A bare "library" or
#              "museum" is not enough on its own in any language, because
#              "Biblioteca" is a café and "bibliothèque" is a JavaScript
#              package; the national form, or a ministry, is.
#   its links  government sites the homepage links: addresses under a
#              suffix a registry reserves for government, and a head of
#              state's, parliament's or ministry's own two-part address under
#              a country code. Two or more of them corroborate the name, and
#              they let the bare institution noun count.
#
# Nothing for sale and no basket, exactly as for the civic reading. A
# university is not read here: the academic rules decide it, and decide it
# `organisation` on purpose - see the block above `_INSTITUTION_NAME_RE`.
# --------------------------------------------------------------------------

_NATIONAL_INSTITUTION_NAME_RE = re.compile(
    # English, Spanish, Portuguese, Italian, French, German, Indonesian and
    # Vietnamese: the national library, archive and museum, and a ministry.
    r"\b(?:national|state)\s+(?:library|archives?|museum)\b|\bministry\s+of\b"
    r"|\bbiblioteca\s+(?:nacional|nazionale)\b|\bbiblioth[eè]que\s+nationale\b"
    r"|\b(?:national|staats|landes)bibliothek\b|\bperpustakaan\s+nasional\b"
    r"|\barchivo\s+(?:general\s+de\s+la\s+naci[oó]n|nacional|hist[oó]rico\s+nacional)\b"
    r"|\barquivo\s+nacional\b|\barchivio\s+(?:centrale\s+dello\s+stato|di\s+stato)\b"
    r"|\barchives\s+(?:nationales|d[ée]partementales|municipales)\b"
    r"|\b(?:bundes|staats|landes|stadt)archiv\b|\barsip\s+nasional\b"
    r"|\bmuseo\s+(?:nacional|nazionale)\b|\bmuseu\s+nacional\b|\bmus[ée]e\s+national\b"
    r"|\b(?:national|landes)museum\b|\bmuseum\s+nasional\b"
    r"|\bministerio\s+de\b|\bminist[ée]rio\s+d[aoe]s?\b|\bministero\s+d|\bminist[èe]re\s+(?:de|des|du)\b"
    r"|\b(?:bundes)?ministerium\b|\bkementerian\s+\w"
    u"|thư\\s+viện\\s+quốc\\s+gia|lưu\\s+trữ\\s+quốc\\s+gia"
    u"|bảo\\s+tàng\\s+quốc\\s+gia"
    u"|\\bbộ\\s+(?:văn\\s+hóa|giáo\\s+dục|y\\s+tế|tài\\s+chính"
    u"|ngoại\\s+giao|nội\\s+vụ|quốc\\s+phòng|tư\\s+pháp"
    u"|khoa\\s+học|thông\\s+tin)"
    # Persian: the national library ("ketabkhane-ye melli"), the national
    # documents or archive, the national museum, and "vezarat" (ministry).
    u"|کتابخانه[\\s\u200c]*(?:ی[\\s\u200c]*)?ملی"
    u"|اسناد\\s+(?:و\\s+\\S+\\s+)?ملی"
    u"|آرشیو\\s+ملی"
    u"|موزه[\\s\u200c]*(?:ی[\\s\u200c]*)?ملی"
    u"|وزارت\\s+\\S"
    # Arabic: the national library, "dar al-kutub", the national archive,
    # "dar al-watha'iq", the national museum, and "wizarat" (ministry).
    u"|المكتبة\\s+الوطنية"
    u"|دار\\s+الكتب"
    u"|الأرشيف\\s+الوطني"
    u"|دار\\s+الوثائق"
    u"|المتحف\\s+الوطني"
    u"|وزارة\\s+\\S"
    # Hebrew: the national library, the national or state archive, the
    # national museum. "Misrad" is also a firm's office, so a ministry is read
    # only with the portfolio after it.
    u"|הספרייה\\s+הלאומית"
    u"|הארכיון\\s+(?:הלאומי|הממלכתי)"
    u"|המוזיאון\\s+הלאומי"
    u"|משרד\\s+ה(?:חינוך|בריאות"
    u"|תרבות|חוץ|פנים|אוצר"
    u"|משפטים|ביטחון)"
    # Thai: the national library, national archives, national museum, and
    # "krasuang" (ministry).
    u"|หอสมุดแห่งชาติ"
    u"|หอจดหมายเหตุแห่งชาติ"
    u"|พิพิธภัณฑ(?:์|สถาน)แห่งชาติ"
    u"|กระทรวง"
    # Japanese and Chinese: a national ("kokuritsu", "guojia") library,
    # museum or archive, and a public records office.
    u"|国立[\u4e00-\u9fff]{0,4}(?:図書館|博物館|美術館"
    u"|圖書館|图书馆|博物馆)"
    u"|公文書館"
    u"|[国國][家](?:图书馆|圖書館|档案馆|檔案館"
    u"|博物馆|博物館)"
    # Korean: a national ("gungnip") library, museum or gallery, and the
    # national records service.
    u"|국립[\uac00-\ud7a3]{0,6}(?:도서관|박물관|미술관)"
    u"|국가기록원"
    # Hindi: the national ("rashtriya") library, museum or archive, and
    # "mantralaya" (ministry).
    u"|राष्ट्रीय\\s+(?:पुस्तकालय"
    u"|संग्रहालय|अभिलेखागार)"
    u"|मंत्रालय",
    re.I)

# A ministry written the way Chinese, Japanese and Korean write one: a short
# name that is the portfolio followed by the character for ministry. Read
# only as the whole of a name, because the same character ends a club, a
# headquarters and a sales department, which the veto below names.
_CJK_MINISTRY_NAME_RE = re.compile(
    u"[\u4e00-\u9fff]{1,6}[省部]|[\uac00-\ud7a3]{1,8}부")
_CJK_MINISTRY_VETO_RE = re.compile(
    u"俱乐部|倶楽部|本部|事業部|営業部"
    u"|支部|全部|본부|사업부|지부")

# The institution nouns on their own, in the same languages. Counted only
# where the homepage's government links corroborate them.
_INSTITUTION_NOUN_RE = re.compile(
    r"\b(?:librar(?:y|ies)|archives?|museum|biblioteca|biblioth[eè]que|bibliothek"
    r"|museo|museu|mus[ée]e|archivo|arquivo|archivio|perpustakaan|arsip)\b"
    u"|thư\\s+viện|bảo\\s+tàng|lưu\\s+trữ"
    u"|کتابخانه|موزه|آرشیو"
    u"|مكتبة|متحف|أرشيف"
    u"|ספרי|מוזיאון|ארכיון"
    u"|ห้องสมุด|หอสมุด"
    u"|พิพิธภัณฑ"
    u"|図書館|博物館|美術館|文書館|图书馆"
    u"|圖書館|博物馆|档案馆|檔案館"
    u"|도서관|박물관|기록원"
    u"|पुस्तकालय|संग्रहालय"
    u"|अभिलेखागार",
    re.I)

# A trading word in the languages above, read beside `_TRADING_NAME_RE`: a
# company, a shop, a store. "Shop" in Persian and Arabic, "company" in
# Chinese, Japanese, Thai, Hindi and Vietnamese, and so on.
_TRADING_NAME_ELSEWHERE_RE = re.compile(
    u"有限公司|株式会社|公司|集团|集團"
    u"|商店|ショップ|شرکت|فروشگاه"
    u"|شركة|متجر|חנות"
    u"|บริษัท|ร้าน|कंपनी"
    u"|công\\s+ty|cửa\\s+hàng|\\btoko\\b|\\btienda\\b|\\bboutique\\b|\\bladen\\b",
    re.I)


def names_a_public_institution(value):
    """"national", "institution" or "": how plainly this name is a public
    institution's, in any of the languages above.

    "national" is the national library, archive or museum, or a ministry - a
    name nothing else is called. "institution" is the bare noun, which the
    caller counts only beside government links.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or _TRADING_NAME_RE.search(text) or _TRADING_NAME_ELSEWHERE_RE.search(text):
        return ""
    if _COLLECTION_NOUN_RE.search(text):
        return ""
    if _NATIONAL_INSTITUTION_NAME_RE.search(text):
        return "national"
    if _CJK_MINISTRY_NAME_RE.fullmatch(text) and not _CJK_MINISTRY_VETO_RE.search(text):
        return "national"
    if _INSTITUTION_NOUN_RE.search(text):
        return "institution"
    return ""


# The first label of a head of state's, a parliament's or a government's own
# address under a country code - `<word>.<cc>`, two labels and no more. A
# country's ministries mostly sit under its reserved suffix, which
# `_registry_suffix_kind` already reads; its president and its parliament
# often do not.
_GOVERNMENT_HOST_LABELS = frozenset({
    "president", "presidency", "presidencia", "presidence", "presidenza",
    "praesident", "leader", "primeminister", "parliament", "parlament",
    "majlis", "ministry", "ministerio", "ministere", "ministero", "ministerie",
    "ministerium", "government", "gobierno", "governo", "gouvernement",
    "regierung", "bundesregierung", "kerajaan", "chinhphu",
})

# How many government sites a homepage has to link before they count as the
# site placing itself inside the state rather than citing it once.
GOVERNMENT_LINKS_MINIMUM = 2


def is_a_government_host(host):
    """Is this address a government's own: a reserved suffix, or a head of
    state's, parliament's or ministry's two-part address under a country code?"""
    host = strip_www((host or "").lower().strip("."))
    if not host:
        return False
    if _registry_suffix_kind(host) == PUBLIC_BODY:
        return True
    labels = [label for label in host.split(".") if label]
    return (len(labels) == 2 and len(labels[-1]) == 2
            and labels[0] in _GOVERNMENT_HOST_LABELS)


def government_sites_linked(snapshot):
    """The government hosts the site's homepage links, sorted.

    The homepage, because that is where a body places itself; every page when
    the crawl typed none as the homepage. The site's own host and its
    subdomains are never counted.
    """
    pages = snapshot.get("pages") or []
    own = strip_www((urlparse(snapshot.get("origin") or "").hostname or "").lower())
    homes = [p for p in pages if p.get("page_type") == "home"] or pages
    found = set()
    for page in homes:
        for link in ((page.get("links") or {}).get("external") or []):
            try:
                host = strip_www((urlparse((link or {}).get("url") or "").hostname or "").lower())
            except ValueError:
                continue
            if not host or (own and (host == own or host.endswith("." + own))):
                continue
            if is_a_government_host(host):
                found.add(host)
    return sorted(found)


def _public_institution_name(snapshot):
    """`(name, strength)` for the strongest public-institution name the site
    calls itself by, or `("", "")`."""
    names = list(_site_identity_strings(snapshot))
    for page in snapshot.get("pages") or []:
        if page.get("page_type") != "home":
            continue
        og = page.get("og") or {}
        for key in ("og:site_name", "site_name"):
            if og.get(key):
                names.append(str(og.get(key)))
    best = ("", "")
    for name in names:
        strength = names_a_public_institution(name)
        if strength == "national":
            return name.strip(), strength
        if strength and not best[0]:
            best = (name.strip(), strength)
    return best


# A public body saying what it is, in its own words.
#
# The registry suffix is the strongest reading this classifier has, and it is
# only as wide as the suffixes a country reserves for government. A central
# bank's address sits under its country's label for organisations, its markup
# declares nothing, and its about page says "<its name> is a public
# institution with a mandate to ...". The site kind came back undetermined and
# the report gave a central bank advice written for a business.
#
# Read from the site's own prose, and only where the sentence's subject is the
# site: its own name, or "we". A commercial bank's page saying the central bank
# "is the regulator" is about somebody else, and the subject is what tells the
# two apart. The noun has to name a public body outright - an institution
# qualified as public, a central bank, a ministry of something, an authority or
# regulator qualified as statutory, national, financial and the like - because
# the bare words are also trade words: a maker of pressure regulators is "a
# regulator manufacturer", and a charity can be "a ministry".
_PUBLIC_BODY_QUALIFIER = (
    r"(?:independent|national|federal|state|public|statutory|government(?:al)?|"
    r"financial|monetary|central|regional|provincial|municipal|local|autonomous|"
    r"official|competent|supervisory|prudential|banking|sole|chief|principal)")
_PUBLIC_BODY_NOUN = (
    r"(?:public\s+(?:institution|body|authority|agency|corporation|"
    r"organi[sz]ation|entity|enterprise)"
    r"|central\s+bank|reserve\s+bank"
    r"|(?:government|state)\s+(?:agency|department|ministry|body|institution|"
    r"organi[sz]ation|entity)"
    r"|ministry\s+(?:of|responsible)"
    r"|statutory\s+(?:body|authority|corporation|board|agency)"
    r"|" + _PUBLIC_BODY_QUALIFIER + r"\s+(?:authority|regulator|agency)"
    r"|regulator\s+(?:of|for|responsible))\b")
# How much of each page the reading looks at. An institution says what it is
# near the top of the page that introduces it.
PUBLIC_BODY_SELF_DESCRIPTION_CHARS = 20000


def public_body_self_description(snapshot, pages=None):
    """The words in which the site calls itself a public body, or "".

    The subject is the site's own name or "we"; see the block above.
    """
    brand = re.sub(r"\s+", " ", str((snapshot.get("brand") or {}).get("name") or "")).strip()
    subjects = [r"we"] + ([re.escape(brand)] if len(brand) >= 3 else [])
    pattern = re.compile(
        r"\b(?:" + "|".join(subjects) + r")\s+(?:is|are|serves\s+as|acts\s+as|"
        r"was\s+established\s+as)\s+(?:a|an|the)\s+(?:[\w'’-]+\s+){0,3}?"
        + _PUBLIC_BODY_NOUN, re.I)
    for page in (snapshot.get("pages") or []) if pages is None else pages:
        if page.get("status") != 200 or page.get("skipped"):
            continue
        text = re.sub(r"\s+", " ", str(page.get("body_text") or ""))
        match = pattern.search(text[:PUBLIC_BODY_SELF_DESCRIPTION_CHARS])
        if match:
            return match.group(0)
    return ""


_PUBLICATION_JSONLD = frozenset({
    "newsmediaorganization", "periodical", "publicationissue",
    "publicationvolume", "blog",
})

# A program, a library, a specification - a thing rather than a body that
# employs people.
_PROJECT_JSONLD = frozenset({
    "softwareapplication", "softwaresourcecode", "webapplication",
    "mobileapplication", "computerlanguage", "apireference", "techarticle",
})

# --------------------------------------------------------------------------
# A byline is not an identity
#
# `site_kind` read "a Person is declared and no Organization is" as "this site
# is a person's". The rule's own comment says both halves matter because "an
# article's `author` is a Person too" - and the half that was supposed to hold
# that line reads `jsonld_types`, which is a flat set of type names with the
# nesting thrown away. Every WordPress site with a byline declares `Person`,
# and the commonest SEO plugin does it as a *top-level* node in an `@graph`
# that the `Article` node points at by `@id`, so nothing in the flattened set
# distinguishes it from a personal homepage. A town library came out
# `personal-or-academic` on exactly that.
#
# The nodes themselves carry the distinction, in two forms, and this reads
# both: a node lifted out of an authorship property (`_nested_in`, written by
# `page_extract._flatten_jsonld`), and a node whose `@id` some authorship
# property points at. A Person that is neither is the site talking about
# itself.
# --------------------------------------------------------------------------

# Properties whose value names whoever made a piece of content, not whoever
# runs the site. A subset of `SUBJECT_CHANGING_PROPERTIES`: `publisher` and
# `provider` are excluded because on a personal site the person genuinely is
# the publisher, and treating that as a byline would throw the identity away.
AUTHORSHIP_PROPERTIES = frozenset({
    "author", "creator", "contributor", "editor", "translator", "reviewedby",
    "actor", "director", "performer", "producer", "character",
})


def _referenced_ids(value):
    """Every `@id` a JSON-LD property value points at, however it is written."""
    out = set()
    for item in (value if isinstance(value, (list, tuple)) else [value]):
        if isinstance(item, dict):
            node_id = item.get("@id")
            if isinstance(node_id, str) and node_id.strip():
                out.add(node_id.strip())
        elif isinstance(item, str) and item.strip():
            out.add(item.strip())
    return out


def person_speaks_for_the_site(pages):
    """Is a declared `Person` the site's own identity, or somebody's byline?

    `True`  at least one Person node is neither nested in an authorship
            property nor pointed at by one - the site is describing itself.
    `False` every Person node on the site is a byline.
    `None`  no page in this crawl recorded its JSON-LD nodes, so there is no
            evidence either way and the caller should read type names as it
            did before. Never a guess dressed as an answer.
    """
    seen_nodes = False
    authored, people = set(), []
    for page in pages or []:
        nodes = page.get("jsonld")
        if not isinstance(nodes, list):
            continue
        seen_nodes = True
        for node in nodes:
            if not isinstance(node, dict):
                continue
            for key, value in node.items():
                if str(key).lower() in AUTHORSHIP_PROPERTIES:
                    authored |= _referenced_ids(value)
            if "person" in jsonld_type_names(node.get("@type")):
                people.append(node)
    if not seen_nodes:
        return None
    for node in people:
        if str(node.get("_nested_in") or "").lower() in AUTHORSHIP_PROPERTIES:
            continue
        node_id = node.get("@id")
        if isinstance(node_id, str) and node_id.strip() in authored:
            continue
        return True
    return False


_TEAM_PATH_RE = re.compile(
    r"(?:^|/)(?:our-)?(?:team|people|staff|leadership|board|trustees|"
    r"executives|management|who-we-are)(?:$|/|\.)", re.I)

_BASKET_PATH_RE = re.compile(
    r"(?:^|/)(?:cart|checkout|basket|bag|shopping-cart|my-cart)(?:$|/|\.)", re.I)

_LICENCE_PATH_RE = re.compile(
    r"(?:^|/)(?:licen[cs]e|licen[cs]es|licen[cs]ing|copying)(?:$|/|\.)", re.I)

# How a project states its licence in prose. Not a list of licence names: the
# phrase is the convention, and it is the same phrase whichever licence follows.
#
# And the licence's own short name, which is how a documentation footer states
# it in any language. A JavaScript framework's translated docs print "(CC
# BY-NC-SA 4.0)" at the foot of every page inside a sentence in Chinese, and
# the English phrase above could not see it - so the site read as having no
# licence and was typed an organisation. The short names are an identifier
# scheme, not vocabulary, so they read the same on every page whatever its
# language.
_LICENCE_TEXT_RE = re.compile(
    r"\b(?:licen[cs]ed under|released under|available under|under the [\w.+-]{2,20} "
    r"licen[cs]e|open[- ]source licen[cs]e|spdx|creative\s+commons"
    r"|CC[- ]BY(?:[- ](?:NC|SA|ND)){0,3}(?:[- ]\d\.\d)?"
    r"|CC0(?:[- ]1\.0)?"
    r"|(?:MIT|BSD|Apache|GPL|LGPL|AGPL|MPL)[- ](?:licen[cs]e|[1-3]\.\d))(?![\w-])", re.I)

# Enough crawled content pages for a share of them to mean anything. Below
# this, one page of a kind is 50% of the site and says nothing.
SITE_KIND_MIN_CONTENT_PAGES = 4

# What share of a site's content pages has to be one kind before the site is
# that kind. Two thirds, because a publication that also runs an about page and
# a contact page still reads as a publication, and a company with a busy blog
# does not.
SITE_KIND_DOMINANT_SHARE = 0.66


def _site_identity_strings(snapshot):
    """The strings this site calls *itself*, and nothing else it prints.

    The brand block first, then the homepage's own title segments and H1s. Body
    text is deliberately absent: the words "library" and "school" appear on
    plenty of commercial sites, and a rule that read prose would read a
    customer list as a self-description.
    """
    brand = snapshot.get("brand") or {}
    out = [str(brand.get("name") or "")]
    out += [str(v) for v in (brand.get("authoritative_variants") or [])]
    out += [str(v) for v in (brand.get("alternate_names") or [])]
    for page in (snapshot.get("pages") or []):
        if page.get("page_type") != "home":
            continue
        out.extend(title_segments(page.get("title") or ""))
        out.extend(str(h) for h in ((page.get("headings") or {}).get("h1") or []))
    return [value for value in out if value.strip()]


def _site_kind_signals(snapshot):
    """Everything the rules below read, gathered once from the snapshot."""
    pages = snapshot.get("pages") or []
    host = urlparse(snapshot.get("origin") or "").hostname or ""
    jsonld = set()
    content_types = []
    paths, licence_text = [], False
    external_hosts = set()
    for page in pages:
        jsonld |= {str(t).split("/")[-1].lower()
                   for t in (page.get("jsonld_types") or [])}
        jsonld |= {str(t).split("/")[-1].lower()
                   for t in (page.get("microdata_types") or [])}
        page_type = page.get("page_type") or ""
        if page_type in CONTENT_TYPES:
            content_types.append(page_type)
        try:
            paths.append(urlparse(page.get("url") or "").path or "/")
        except ValueError:
            pass
        for link in ((page.get("links") or {}).get("internal") or []):
            try:
                paths.append(urlparse((link or {}).get("url") or "").path or "/")
            except ValueError:
                pass
        for link in ((page.get("links") or {}).get("external") or []):
            try:
                external_hosts.add(strip_www(
                    (urlparse((link or {}).get("url") or "").hostname or "").lower()))
            except ValueError:
                pass
        # The whole visible text as well as the body: a licence line lives in
        # the footer, and the body text is what is left once the footer has
        # been stripped as boilerplate.
        if not licence_text and (_LICENCE_TEXT_RE.search(page.get("body_text") or "")
                                 or _LICENCE_TEXT_RE.search(page.get("text") or "")):
            licence_text = True
    # One path at a time, never a joined blob. These patterns anchor on the end
    # of the path, and `$` in a joined string only matches the very end of it -
    # so `/about/team` in the middle of the list matched nothing and a software
    # company with a team page was classified as a project.
    def matched(rule):
        return any(rule.search(path) for path in paths)
    counts = {}
    for page_type in content_types:
        counts[page_type] = counts.get(page_type, 0) + 1
    return {
        "registry": _registry_suffix_kind(host),
        "jsonld": jsonld,
        "content_pages": len(content_types),
        "type_counts": counts,
        "page_types": {p.get("page_type") for p in pages},
        "sells": sells_something(snapshot, pages),
        "sells_online": sells_online(snapshot, pages),
        "has_basket": matched(_BASKET_PATH_RE),
        "declared_places": len(declared_locations(snapshot)),
        "own_address_pages": len(pages_with_their_own_address(snapshot)),
        # Paths, so the reason printed can name the page that was read.
        "location_pages_with_address": [
            urlparse(p.get("url") or "").path or "/"
            for p in location_pages_with_an_address(snapshot)],
        "multi_location": is_multi_location(snapshot),
        "has_team_page": matched(_TEAM_PATH_RE),
        "civic_markup": bool(jsonld & _CIVIC_JSONLD),
        "civic_name": next((name for name in _site_identity_strings(snapshot)
                            if names_a_civic_institution(name)), ""),
        # See `names_a_public_institution` and `government_sites_linked`.
        "public_institution": _public_institution_name(snapshot),
        "government_links": government_sites_linked(snapshot),
        "public_self_description": public_body_self_description(snapshot, pages),
        # `None` where no page recorded its nodes, which the rule below reads
        # as "no evidence" and not as "no".
        "person_is_the_site": person_speaks_for_the_site(pages),
        "repository": any(
            host_name == repo or host_name.endswith("." + repo)
            for host_name in external_hosts if host_name
            for repo in _ACCOUNT_IS_FIRST_SEGMENT_HOSTS),
        "licence": matched(_LICENCE_PATH_RE) or licence_text,
    }


# The markup a shop puts on the thing it sells, as opposed to the markup a
# place puts on itself. `store` is deliberately absent: it is the schema.org
# type of a shop you walk into, which is the other half of the question.
_SELLS_ONLINE_JSONLD = frozenset({
    "product", "productgroup", "productmodel", "individualproduct", "onlinestore",
})


def sells_online(snapshot, pages=None):
    """What says this site takes an order on the site itself, or "".

    A product detail page, an add-to-basket control, product markup, or a
    basket address - each is the site selling from its own pages, which a
    showroom or a branch does not do. Returned as a phrase so the kind can say
    which one it read.
    """
    pages = pages if pages is not None else (snapshot.get("pages") or [])
    for page in pages:
        if page.get("page_type") == "product":
            return "product pages"
        if ((page.get("add_to_cart_controls") or {}).get("count") or 0) > 0:
            return "an add-to-basket control"
    for page in pages:
        declared = {str(t).split("/")[-1].lower()
                    for t in (page.get("jsonld_types") or [])}
        declared |= {str(t).split("/")[-1].lower()
                     for t in (page.get("microdata_types") or [])}
        if declared & _SELLS_ONLINE_JSONLD:
            return "product markup"
    for page in pages:
        for link in [{"url": page.get("url")}] + list(
                (page.get("links") or {}).get("internal") or []):
            try:
                path = urlparse((link or {}).get("url") or "").path or "/"
            except ValueError:
                continue
            if _BASKET_PATH_RE.search(path):
                return "a basket"
    return ""


# --------------------------------------------------------------------------
# An institution under an academic suffix
#
# The education suffix was read as "a person, a university, a lab" and the
# answer was always the person. A national university's report told it to say
# "what you work on, where you are based, and what you are known for", to
# claim "your researcher identifier" and to repeat its boilerplate in "every
# conference or speaker biography" - advice for one researcher's homepage,
# given to an institution with ten faculties and a name that is the word
# "university" in its own language.
#
# The suffix says the site is academic; it cannot say whether it is one
# academic or the whole institution. Three things the site states can:
#
#   its name     the brand name or the homepage's H1 is an institution's name -
#                University, College, Institute, or the same noun in the
#                site's own language. Never the title segments: a professor's
#                page is titled "<name> | <University>", and that names where
#                she works, not what the site is.
#   its markup   a top-level `CollegeOrUniversity` or `EducationalOrganization`
#                node. A nested one is a Person's `affiliation`.
#   its shape    many faculties and departments linked from its own pages.
#
# A person speaking for the site in its markup outranks all three, and a
# person's role word beside the name ("Professor", "Lab") vetoes the name
# reading, so a researcher's page under the same suffix stays hers.
#
# The kind returned is `organisation`, not `public-body`. Nothing in an
# address or a name says whether a university is national, state or private,
# and `organisation` is the advice that fits every one of them: a founding
# year, a register entry, the professional network where universities keep
# school pages. `public-body` would tell a private college to find "the
# official register of public bodies that lists you".
# --------------------------------------------------------------------------

_INSTITUTION_NAME_RE = re.compile(
    r"\b(?:university|universit(?:y|ies)|universit[äaé]t|universit[ée]|universidad|"
    r"universidade|universit[àa]|universiteit|uniwersytet|univerzit[ae]t?|college|"
    r"institute|institut|instituto|istituto|polytechnic|politecnico|hochschule|"
    r"academy\s+of|school\s+of\s+(?:economics|medicine|law|business|engineering))\b"
    u"|大学|大學|学院|學院|대학교|"
    u"มหาวิทยาลัย|"
    u"جامعة", re.I)

# Words that put one person or one group in front of the institution's name.
_ONE_ACADEMIC_RE = re.compile(
    r"\b(?:prof(?:essor)?\.?|dr\.?|lecturer|researcher|scientist|student|"
    r"candidate|ph\.?\s?d|postdoc|fellow|lab|laboratory|research\s+group|"
    r"homepage|personal|cv|curriculum\s+vitae)\b", re.I)

_INSTITUTION_JSONLD = frozenset({"collegeoruniversity", "educationalorganization"})

# A faculty or a department, in the words an institution's own menu uses.
_DEPARTMENT_RE = re.compile(
    r"\b(?:faculty|faculties|department|departments|school\s+of|graduate\s+school|"
    r"college\s+of|fakult[äa]t|facult[ée]|facultad|faculdade|dipartimento)\b"
    u"|学部|研究科|학부|대학원|"
    u"คณะ", re.I)
INSTITUTION_MIN_DEPARTMENTS = 5


def _names_an_academic_institution(snapshot):
    """The name the site calls itself that is an institution's, or ""."""
    brand = snapshot.get("brand") or {}
    names = [str(brand.get("name") or "")]
    names += [str(v) for v in (brand.get("authoritative_variants") or [])]
    names += [str(v) for v in (brand.get("alternate_names") or [])]
    home_h1s, home_titles = [], []
    for page in snapshot.get("pages") or []:
        if page.get("page_type") == "home":
            home_h1s += [str(h) for h in ((page.get("headings") or {}).get("h1") or [])]
            home_titles.append(str(page.get("title") or ""))
    # A person's role anywhere the homepage names itself is a person's site,
    # whatever institution the same line also names. The titles are read for
    # this veto and for nothing else.
    if any(_ONE_ACADEMIC_RE.search(value) for value in names + home_h1s + home_titles):
        return ""
    for value in names + home_h1s:
        value = re.sub(r"\s+", " ", value).strip()
        if value and _INSTITUTION_NAME_RE.search(value):
            return value
    return ""


def _declares_an_academic_institution(snapshot):
    """Does a top-level markup node say the site is a university or college?"""
    for page in snapshot.get("pages") or []:
        for node in page.get("jsonld") or []:
            if not isinstance(node, dict) or node.get("_nested_in"):
                continue
            if jsonld_type_names(node.get("@type")) & _INSTITUTION_JSONLD:
                return True
    return False


def _departments_linked(snapshot):
    """How many distinct faculties and departments the site's own links name."""
    seen = set()
    for page in snapshot.get("pages") or []:
        for link in (page.get("links") or {}).get("internal") or []:
            label = re.sub(r"\s+", " ", str((link or {}).get("text") or "")).strip()
            if label and len(label) <= 120 and _DEPARTMENT_RE.search(label):
                seen.add(label.lower())
    return len(seen)


def academic_institution(snapshot, person_is_the_site=None):
    """`(confidence, evidence)` where an academic site is an institution, else None.

    See the block above for the three readings and the one veto.
    """
    if person_is_the_site is True:
        return None
    name = _names_an_academic_institution(snapshot)
    if name:
        return "high", 'the site calls itself "{}", which is the name of an ' \
                       "institution rather than of one person".format(truncate(name, 80))
    if _declares_an_academic_institution(snapshot):
        return "high", ("the site declares itself a university or college in its "
                        "own markup")
    departments = _departments_linked(snapshot)
    if departments >= INSTITUTION_MIN_DEPARTMENTS:
        return "medium", ("its own pages link {} faculties and departments".format(
            departments))
    return None


# --------------------------------------------------------------------------
# One document, in several languages
#
# A one-page essay - one page and its translations, nothing else - was given
# the whole of the business advice: claim LinkedIn, Instagram and X, state a
# founding year and a street address, add a call to action. Every rule below
# asks for a structure the essay does not have, so every one of them passed it
# by and the kind came back undetermined, which is the answer that changes
# nothing.
#
# The shape itself is the evidence. The crawl found one substantial page, the
# other pages it found are that page in other languages (a leading segment
# shaped like a language code, on a page declaring a different language), and
# its links lead almost nowhere else. And none of the things a body has: nothing
# for sale, no organisation markup, no contact, about, careers or location
# page, no street address or telephone number on it.
# --------------------------------------------------------------------------

_LANGUAGE_SEGMENT_RE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z0-9]{2,8})?$", re.I)
SINGLE_DOCUMENT_MIN_CHARS = 1000
# Other addresses the document may link or the crawl may have read and still
# be one document: a stray relative link, a colophon.
SINGLE_DOCUMENT_OTHER_ADDRESSES = 2
_SINGLE_DOCUMENT_REFUSING_TYPES = frozenset({
    "about", "contact", "location", "careers", "pricing", "product", "category",
    "service", "press",
})


def _document_path(url, lang, home_language):
    """The address with a language edition's leading segment taken off."""
    try:
        path = urlparse(url or "").path or "/"
    except ValueError:
        return ""
    segments = [s for s in path.split("/") if s]
    if segments and re.match(r"^index\.[a-z]{3,4}$", segments[-1], re.I):
        segments = segments[:-1]
    code = primary_subtag(lang)
    if segments and code and _LANGUAGE_SEGMENT_RE.match(segments[0]):
        segment_code = re.split(r"[-_]", segments[0].lower())[0]
        if code != home_language or segment_code == code:
            segments = segments[1:]
    return "/" + "/".join(segments)


def single_document_site(snapshot):
    """`(editions, others)` where the site is one document, else None.

    `editions` is how many crawled pages are that document, the original
    included, and `others` how many other addresses the crawl met. See the
    block above.
    """
    pages = pages_of(snapshot)
    if not pages:
        return None
    home = next((p for p in pages
                 if (urlparse(p.get("url") or "").path or "/") == "/"), pages[0])
    home_language = primary_subtag(home.get("lang"))
    by_document = {}
    for page in pages:
        if page.get("page_type") in _SINGLE_DOCUMENT_REFUSING_TYPES:
            return None
        facts = page.get("contact_facts") or {}
        if facts.get("phones") or facts.get("street"):
            return None
        key = _document_path(page.get("url"), page.get("lang"), home_language)
        by_document.setdefault(key, []).append(page)
    document = max(sorted(by_document), key=lambda k: (
        len(by_document[k]),
        max(english_equivalent_length(p.get("body_text") or "") for p in by_document[k])))
    editions = by_document[document]
    if max(english_equivalent_length(p.get("body_text") or "")
           for p in editions) < SINGLE_DOCUMENT_MIN_CHARS:
        return None
    edition_segments = {s for p in editions
                        for s in [(urlparse(p.get("url") or "").path or "/").strip("/")
                                  .split("/")[0]] if s}
    others = {key for key in by_document if key != document}
    for page in pages:
        for link in (page.get("links") or {}).get("internal") or []:
            try:
                path = urlparse((link or {}).get("url") or "").path or "/"
            except ValueError:
                continue
            segments = [s for s in path.split("/") if s]
            if segments and segments[0] in edition_segments:
                segments = segments[1:]
            key = "/" + "/".join(segments)
            if key != document:
                others.add(key)
    if len(others) > SINGLE_DOCUMENT_OTHER_ADDRESSES:
        return None
    return len(editions), len(others)


def _dominant_content_type(facts, page_type):
    """Is this page type most of what the site publishes?"""
    if facts["content_pages"] < SITE_KIND_MIN_CONTENT_PAGES:
        return False
    share = facts["type_counts"].get(page_type, 0) / float(facts["content_pages"])
    return share >= SITE_KIND_DOMINANT_SHARE


# A documentation tree, counted from the pages the crawl read rather than from
# their types: a translated docs site's guide pages were typed `other`, and 38
# of its 52 crawled pages sit under `/guide/`, `/api/` or `/examples/`. Eight
# is a tree; one `/docs` page is a vendor's product manual.
_DOCS_TREE_PATH_RE = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:docs?|guide|guides|api|reference|tutorials?|manual"
    r"|handbook|learn|examples?|cookbook)(?:/|$)", re.I)
DOCS_TREE_MIN_PAGES = 8


def documentation_tree_pages(snapshot):
    """How many crawled pages sit in a documentation tree, by address or by type."""
    count = 0
    for page in snapshot.get("pages") or []:
        if page.get("status") != 200 or page.get("skipped"):
            continue
        try:
            path = urlparse(page.get("url") or "").path or "/"
        except ValueError:
            continue
        if page.get("page_type") == "documentation" or _DOCS_TREE_PATH_RE.match(path):
            count += 1
    return count


def site_kind(snapshot):
    """A `SiteKind`: what kind of thing this site is, and how sure that is.

    Rules in descending strength, first match wins. The order is the order of
    how hard the evidence is to fake, not the order of how common the kinds
    are: an address suffix a registry controls, then the identity the site
    declares in its markup, then what its structure shows it doing.
    """
    facts = _site_kind_signals(snapshot)
    said = facts["jsonld"]

    if facts["registry"] == PUBLIC_BODY:
        return SiteKind(PUBLIC_BODY, "high",
                        ["the address sits under a suffix reserved for "
                         "government, which nobody can buy"])
    if said & _PUBLIC_BODY_JSONLD:
        return SiteKind(PUBLIC_BODY, "high",
                        ["the site declares itself a government body in its own markup"])
    # Before the academic rules, because this is the one they got wrong: a town
    # library on a `.net` address reached them and came out a researcher.
    if facts["civic_markup"]:
        return SiteKind(PUBLIC_BODY, "high",
                        ["the site declares itself a library, museum, school, "
                         "place of worship or town office in its own markup"])
    if facts["civic_name"] and not (facts["sells"] or facts["has_basket"]):
        return SiteKind(PUBLIC_BODY, "medium",
                        ['the site calls itself "{}", which is the name of a '
                         "public institution".format(facts["civic_name"]),
                         "nothing is for sale"])
    # A national library, archive, museum or ministry named in the site's own
    # language, corroborated by the government sites its homepage links. See
    # `names_a_public_institution`.
    name, strength = facts["public_institution"]
    governments = facts["government_links"]
    corroborated = len(governments) >= GOVERNMENT_LINKS_MINIMUM
    if (name and (strength == "national" or corroborated)
            and not (facts["sells"] or facts["has_basket"])):
        signals = ['the site calls itself "{}", which is the name of {}'.format(
            truncate(name, 100),
            "a national library, archive or museum, or a ministry"
            if strength == "national" else "a library, archive or museum")]
        if corroborated:
            signals.append("its homepage links {} government sites ({})".format(
                len(governments), ", ".join(governments[:4])))
        signals.append("nothing is for sale")
        return SiteKind(PUBLIC_BODY,
                        "high" if strength == "national" and corroborated else "medium",
                        signals)
    # See `public_body_self_description`: the site's own sentence about what
    # it is, where no suffix and no markup says it.
    if facts["public_self_description"] and not (facts["sells"] or facts["has_basket"]):
        return SiteKind(PUBLIC_BODY, "medium",
                        ['the site says of itself "{}"'.format(
                            truncate(facts["public_self_description"], 140)),
                         "nothing is for sale"])

    # An academic site that is the institution rather than one of its people.
    # See `academic_institution`: the suffix says academic, and the site's own
    # name, markup or faculties say which.
    if facts["registry"] == PERSONAL_OR_ACADEMIC or said & _ACADEMIC_JSONLD:
        institution = academic_institution(snapshot, facts["person_is_the_site"])
        if institution:
            confidence, evidence = institution
            where = ("the address sits under a suffix reserved for education"
                     if facts["registry"] == PERSONAL_OR_ACADEMIC
                     else "the site declares itself an educational organisation in "
                          "its own markup")
            return SiteKind(ORGANISATION, confidence, [where, evidence])
    if facts["registry"] == PERSONAL_OR_ACADEMIC:
        return SiteKind(PERSONAL_OR_ACADEMIC, "high",
                        ["the address sits under a suffix reserved for education"])
    if said & _ACADEMIC_JSONLD:
        return SiteKind(PERSONAL_OR_ACADEMIC, "medium",
                        ["the site declares itself an educational or research "
                         "organisation in its own markup"])
    # A person is the subject, and no organisation is. Both halves matter: an
    # article's `author` is a Person too, and reading that alone would call
    # every blog a personal site. `said` is type names with the nesting thrown
    # away and cannot tell those apart, so the nodes are read as well - see
    # `person_speaks_for_the_site`. `False` there means every Person on the
    # site is somebody's byline; `None` means the crawl recorded no nodes and
    # this rule behaves as it did before that reading existed.
    if ("person" in said and not (said & ORG_IDENTITY_TYPES)
            and facts["person_is_the_site"] is not False):
        return SiteKind(PERSONAL_OR_ACADEMIC, "medium",
                        ["a person, and no organisation, is the identity the "
                         "markup declares"])

    # A project is a thing, not a body. What separates it from a company that
    # publishes a repository is everything a company also has: something for
    # sale, somewhere to visit, a careers page, a team page. A project has a
    # repository, a licence, and none of those.
    #
    # A team page alone is not one of them where the site publishes a
    # repository, a licence and a documentation tree. Open-source projects
    # list their maintainers: a JavaScript framework's documentation site links
    # a GitHub organisation, prints its licence on every page, keeps 38 of 52
    # crawled pages under `/guide/` and `/api/`, and has an About -> Team page -
    # and that one page typed the whole site an organisation. A team page still
    # counts beside anything else corporate, and on a vendor with a repository
    # and a single manual page. See `DOCS_TREE_MIN_PAGES`.
    open_project = bool(facts["repository"] and facts["licence"]
                        and documentation_tree_pages(snapshot) >= DOCS_TREE_MIN_PAGES)
    nothing_corporate = not (facts["sells"] or facts["has_basket"]
                             or facts["declared_places"] or facts["multi_location"]
                             or (facts["has_team_page"] and not open_project)
                             or "careers" in facts["page_types"])
    if said & _PROJECT_JSONLD and nothing_corporate:
        return SiteKind(PROJECT, "high",
                        ["the site declares itself software in its own markup",
                         "nothing is for sale and there is no team, careers "
                         "page or address"])
    if facts["repository"] and facts["licence"] and nothing_corporate:
        return SiteKind(
            PROJECT, "high",
            ["a source repository and a licence are published",
             "nothing is for sale and there is no team, careers page or address"])
    if (facts["repository"] and nothing_corporate
            and _dominant_content_type(facts, "documentation")):
        return SiteKind(PROJECT, "medium",
                        ["a source repository is published and most of the "
                         "crawled content is documentation",
                         "nothing is for sale and there is no team, careers "
                         "page or address"])

    # One document and its translations. See `single_document_site`; every
    # rule above asks for a structure such a site does not have, and every
    # rule below would give it advice for a body it is not.
    if not (facts["sells"] or facts["has_basket"] or facts["declared_places"]
            or facts["has_team_page"] or said & ORG_IDENTITY_TYPES):
        single = single_document_site(snapshot)
        if single:
            editions, others = single
            return SiteKind(PERSONAL_OR_ACADEMIC, "medium", [
                "the crawl found one document{}{}".format(
                    "" if editions < 2 else " in {} language editions".format(editions),
                    " and nothing else" if not others else
                    " and {} other address{}".format(others, "" if others == 1 else "es")),
                "nothing is for sale, and there is no organisation markup, contact "
                "or about page, or address"])

    if said & _PUBLICATION_JSONLD and not facts["sells"]:
        return SiteKind(PUBLICATION, "medium",
                        ["the site declares itself a publication in its own markup",
                         "nothing is for sale"])
    if (_dominant_content_type(facts, "article") and not facts["sells"]
            and not facts["declared_places"]):
        return SiteKind(PUBLICATION, "medium",
                        ["most of the crawled content pages are dated articles",
                         "nothing is for sale and no address is declared"])

    # Selling on the site outranks having somewhere to visit. A furniture
    # retailer with product pages, a basket and two showrooms was typed a local
    # business on the strength of one showroom page, and every tailored line
    # after that - map listings, a trade association's directory - was advice
    # for a shop you walk into, given to a shop whose trade is its own website.
    # A local business is one that does not sell from its pages; see
    # `sells_online` for what counts as doing so.
    if facts["sells_online"] and (facts["sells"] or facts["has_basket"]):
        places = facts["declared_places"] or "location" in facts["page_types"]
        return SiteKind(
            ONLINE_SELLER,
            "high" if facts["sells"] and facts["has_basket"] else "medium",
            ["the site sells from its own pages ({})".format(facts["sells_online"]),
             "it also names places to visit, which a retailer with showrooms or "
             "stores does" if places else "it declares no place to visit"])

    # Two kinds of evidence, each reported as what it is. A page typed
    # `location` by its path and printing no address of its own is neither:
    # see `location_pages_with_an_address` for the venue listings that made a
    # language foundation a local business on four such pages.
    if facts["declared_places"]:
        return SiteKind(LOCAL_BUSINESS, "high",
                        ["the site declares a place with a postal address that "
                         "visitors go to"])
    if facts["location_pages_with_address"]:
        located = facts["location_pages_with_address"]
        return SiteKind(LOCAL_BUSINESS, "high",
                        ["{} about where to find the site ({}) print{} a street "
                         "address and postal code of {} own".format(
                             "a page" if len(located) == 1
                             else "{} pages".format(len(located)),
                             ", ".join(located[:3]),
                             "s" if len(located) == 1 else "",
                             "its" if len(located) == 1 else "their")])
    if facts["multi_location"] or facts["own_address_pages"] >= BRANCH_PAGE_MINIMUM:
        return SiteKind(LOCAL_BUSINESS, "medium",
                        ["separate pages print their own street address"])

    if facts["sells"] or facts["has_basket"]:
        return SiteKind(
            ONLINE_SELLER,
            "high" if facts["sells"] and facts["has_basket"] else "medium",
            ["the site sells something and declares no place to visit"])

    if (said & ORG_IDENTITY_TYPES or "careers" in facts["page_types"]
            or facts["has_team_page"]):
        return SiteKind(ORGANISATION, "medium",
                        ["the site presents itself as an organisation with "
                         "people, and neither sells nor names a place to visit"])

    # Nothing decided. Say so, and let every caller behave as it did before
    # this function existed.
    return SiteKind(UNDETERMINED, "low", ["no structural signal was decisive"])


# --------------------------------------------------------------------------
# What is this site built with, and where is its template?
#
# Every markup fix this marketplace emits ends in the same sentence: "add the
# snippet to the site-wide template". It was printed about forty times
# across four real reports, and it is a template instruction nobody can
# follow, because it names no file, no screen and no person.
#
# The evidence to do better was already in `snapshot.json` and nothing read it.
# Three of those four sites were one hosted storefront and one was a hosted
# site builder; the crawl had fetched, parsed and quoted that platform's CDN
# hostname on every page of three of them. The platform's name appeared in the
# four reports exactly once, inside a URL the tool quoted back in its own
# snippet.
#
# The second half of the same failure is who. Everything typed `low` /
# `developer` - Organization properties, Article and FAQPage blocks,
# BreadcrumbList, `og:image`, the JSON-LD escaping - is seven to nine of every
# twelve to fourteen findings in a report, and across 2,795 lines of output the
# words "your theme", "your developer" and "whoever built the site" appear
# nowhere. For a shop owner with no developer, "low effort, developer" is not
# an hour of work. It is a person they do not have.
#
# **A wrong name is far worse than none.** Telling somebody on a hosted site
# builder to open `theme.liquid` costs the reader's trust in every other
# finding in the report, and buys less than the generic sentence it replaced.
# So the certainty distinction is the design and the platform list is the
# detail: only a `high` answer may print a platform's own vocabulary, a
# `medium` one hedges out loud, and anything weaker says nothing about the
# platform at all.
#
# **Evidence, not vibes.** A page that merely mentions a platform in its prose
# must never be read as a fact about the site - this repository has been bitten
# by that repeatedly, most recently a "Powered by" line in a footer counted as
# the brand's own off-site profile. So nothing here reads prose, and nothing
# here reads `<a href>`. Six families, each one a thing the site's own software
# or server emitted:
#
#   generator          the `<meta name="generator">` tag, and the `server`,
#                      `x-powered-by` and `x-generator` header values. The
#                      software naming itself.
#   asset-host         a hostname the page fetches a script, stylesheet or
#                      image from. A brand cannot get its assets served from a
#                      platform's CDN without being on the platform.
#   asset-path         the leading path segments of those subresources -
#                      `/cdn/shop`, `/wp-content`, `/_next/static`. A shape the
#                      platform's own build produces.
#   markup-attribute   an attribute name the platform stamps on `<html>` or
#                      `<body>`. Names only; the values are per-site ids.
#   response-header    a header name only that platform sends.
#   cookie-name        the name of a cookie the origin set. Names only, never
#                      values - a cookie value is a session.
#
# The last three are *self-declaring*: an embedded third-party widget can add a
# script host, a path shape, a root attribute and a framework marker to
# somebody else's page, and it cannot make that page's own origin send a header
# or set a cookie. That is what separates a shop from a blog with a buy button
# glued into its footer, and it is why the confidence rule below asks for one
# of those three before it will name anything.
#
# **The fallback is the common case and gets the better sentence.** Most of the
# open web runs something no table can name. That reader still knows one true
# thing - somebody, somewhere, edits their pages - and one true thing about
# every site ever built: exactly one file produces the top of every page, and
# it is the file with `</head>` in it. That is searchable and it is actionable,
# which "add it to the site-wide template" never was.
# --------------------------------------------------------------------------

# How the person who makes this change reaches the template.
ADMIN_SCREEN = "admin-screen"      # a screen they log into; no deployment
SOURCE_FILE = "source-file"        # a file in the site's source; needs a release

# The families, in the order the evidence line lists them.
PLATFORM_SIGNAL_FAMILIES = ("generator", "asset-host", "asset-path",
                            "markup-attribute", "framework-marker",
                            "response-header", "cookie-name")

# Families only the site's own origin can produce. See the block comment: a
# widget pasted into somebody else's page can forge the other four.
PLATFORM_SELF_DECLARING_FAMILIES = frozenset({"generator", "response-header",
                                              "cookie-name"})

# How much of the crawl has to carry the signal before it describes the site
# rather than one page on it. Half, because a platform's own template is on
# every page it renders and an embedded widget is usually on one section.
PLATFORM_SITE_WIDE_SHARE = 0.5

# Where a head snippet goes when the reader has to find the place themselves.
DEFAULT_TEMPLATE_SLOT = "just above the closing `</head>` tag"


class _Signature:
    """One platform's fingerprint and the words its own documentation uses.

    Naming a platform whose signature the code has to recognise is the same
    thing this file already does for bot managers, CAPTCHA vendors, media hosts
    and consent tools: a product we detect, never an example site and never
    keyed to any brand being audited. Held as hostname *labels* rather than
    addresses, which is also how `_PUBLIC_LABELS` and `_REGISTRY_HOST_LABELS`
    are held - a label is the word somebody chose, and the suffix beside it is
    a registry's.
    """

    __slots__ = ("name", "reach", "words", "hosts", "paths", "attributes",
                 "frameworks", "headers", "cookies", "admin_path", "file",
                 "head_slot", "note")

    def __init__(self, name, reach, admin_path="", file="", head_slot=False,
                 note="", words=(), hosts=(), paths=(), attributes=(),
                 frameworks=(), headers=(), cookies=()):
        self.name = name
        self.reach = reach
        self.admin_path = admin_path
        self.file = file
        self.head_slot = head_slot
        self.note = note
        self.words = tuple(words)
        self.hosts = frozenset(hosts)
        self.paths = tuple(paths)
        self.attributes = tuple(attributes)
        self.frameworks = tuple(frameworks)
        self.headers = tuple(headers)
        self.cookies = tuple(cookies)


# The table. Two shapes of row, and the difference is the whole reason the
# fallback below can still be useful:
#
#   ADMIN_SCREEN  the owner reaches the template by logging in. `admin_path` is
#                 the click path in that product's own menu wording, and
#                 `head_slot` says the product has a box that puts the code in
#                 the head for you - so the reader is not sent hunting for a
#                 `</head>` that the screen never shows them.
#   SOURCE_FILE   the template is a file in the site's source and the change
#                 ships with a release. `file` names it.
#
# Rows are here because their signature is unambiguous, not because they are
# popular. A row whose click path might be wrong is worse than no row.
PLATFORM_SIGNATURES = (
    _Signature(
        "Shopify", ADMIN_SCREEN,
        admin_path="Online Store → Themes → the … button on your live "
                   "theme → Edit code",
        file="`layout/theme.liquid`",
        words=("shopify",),
        hosts=("shopify", "myshopify", "shopifycdn", "shopifycloud", "shopifysvc"),
        paths=("/cdn/shop", "/cdn/shopifycloud"),
        headers=("x-shopid", "x-shardid", "x-sorting-hat-shopid",
                 "x-storefront-renderer-rendered"),
        cookies=("_shopify_", "_secure_session_id", "cart_currency", "shopify_pay")),
    _Signature(
        "WordPress", ADMIN_SCREEN,
        admin_path="Appearance → Theme File Editor in the WordPress admin",
        file="`header.php`",
        note="Put it in a child theme or a header-snippet plugin rather than in the "
             "theme itself, or the theme's next update will remove it.",
        words=("wordpress", "woocommerce"),
        paths=("/wp-content", "/wp-includes", "/wp-json"),
        headers=("x-pingback",),
        cookies=("wordpress_", "wp-settings-", "woocommerce_", "wp_woocommerce_")),
    _Signature(
        "Squarespace", ADMIN_SCREEN,
        admin_path="Settings → Advanced → Code Injection → Header",
        head_slot=True,
        words=("squarespace",),
        hosts=("squarespace", "squarespace-cdn", "sqspcdn", "sqsp"),
        headers=("x-contextid",),
        cookies=("crumb", "ss_c", "ss_i")),
    _Signature(
        "Wix", ADMIN_SCREEN,
        admin_path="Settings → Custom code → Add code to Head",
        head_slot=True,
        words=("wix",),
        hosts=("wix", "wixstatic", "wixsite", "parastorage"),
        headers=("x-wix-request-id", "x-wix-published-version"),
        cookies=("ssr-caching", "xsrf-token_")),
    _Signature(
        "Webflow", ADMIN_SCREEN,
        admin_path="Site settings → Custom code → Head code",
        head_slot=True,
        note="A block that belongs to one page goes in that page's own Page settings "
             "→ Custom code instead.",
        words=("webflow",),
        hosts=("webflow", "website-files", "webflow-files"),
        attributes=("data-wf-site", "data-wf-page", "data-wf-domain")),
    _Signature(
        "Ghost", ADMIN_SCREEN,
        admin_path="Settings → Code injection → Site header",
        head_slot=True,
        words=("ghost",),
        headers=("x-ghost-cache-status",),
        cookies=("ghost-members-ssr",)),
    _Signature(
        "HubSpot CMS", ADMIN_SCREEN,
        admin_path="Settings → Website → Pages → Templates → "
                   "Site header HTML",
        head_slot=True,
        words=("hubspot",),
        hosts=("hubspot", "hs-sites", "hsforms", "hubspotusercontent", "hscollectedforms"),
        cookies=("hubspotutk", "__hstc", "__hssrc")),
    _Signature(
        "BigCommerce", ADMIN_SCREEN,
        admin_path="Storefront → Script Manager, or Storefront → Themes "
                   "→ Edit Theme Files",
        file="`templates/layout/base.html`",
        words=("bigcommerce",),
        hosts=("bigcommerce", "mybigcommerce", "bigcommercecdn"),
        paths=("/stencil",),
        cookies=("shop_session_token", "stencil_")),
    _Signature(
        "Adobe Commerce", ADMIN_SCREEN,
        admin_path="Content → Design → Configuration → your store view "
                   "→ HTML Head → Scripts and Style Sheets",
        head_slot=True,
        words=("magento", "adobe commerce"),
        paths=("/pub/static", "/static/version", "/media/catalog"),
        cookies=("mage-", "form_key", "private_content_version")),
    _Signature(
        "Weebly", ADMIN_SCREEN,
        admin_path="Settings → SEO → Header Code",
        head_slot=True,
        words=("weebly",),
        hosts=("weebly", "editmysite", "weeblysite")),
    _Signature(
        "Duda", ADMIN_SCREEN,
        admin_path="Site → Settings → Head HTML",
        head_slot=True,
        words=("duda",),
        hosts=("dudamobile", "multiscreensite", "duda")),
    _Signature(
        "Framer", ADMIN_SCREEN,
        admin_path="Site settings → General → Custom code → Start of "
                   "`<head>` tag",
        head_slot=True,
        words=("framer",),
        hosts=("framer", "framerusercontent")),
    _Signature(
        "Blogger", ADMIN_SCREEN,
        admin_path="Theme → the … menu → Edit HTML",
        file="the theme XML, inside its `<head>` section",
        words=("blogger",),
        hosts=("blogspot", "blogger", "bloggerusercontent")),
    _Signature(
        "Drupal", SOURCE_FILE,
        file="your theme's `html.html.twig`",
        note="A site-wide block placed in the `Header` region through Structure → "
             "Block layout is the same change without a release, where your build "
             "allows it.",
        words=("drupal",),
        paths=("/sites/default", "/sites/all", "/core/misc", "/core/themes"),
        headers=("x-drupal-cache", "x-drupal-dynamic-cache")),
    _Signature(
        "Joomla", SOURCE_FILE,
        file="your template's `index.php`",
        words=("joomla",),
        paths=("/media/system", "/media/templates", "/templates/system")),
    _Signature(
        "TYPO3", SOURCE_FILE,
        file="the TypoScript `page.headerData` setting for your site",
        words=("typo3",),
        paths=("/typo3conf", "/typo3temp", "/fileadmin")),
    _Signature(
        "PrestaShop", SOURCE_FILE,
        file="`themes/<your theme>/templates/_partials/head.tpl`",
        words=("prestashop",),
        cookies=("prestashop-",)),
    _Signature(
        "Salesforce Commerce Cloud", SOURCE_FILE,
        file="the `htmlHead` template your cartridge overrides",
        paths=("/on/demandware.static", "/on/demandware.store"),
        hosts=("demandware",),
        cookies=("dwsid", "dwanonymous_", "dwsecuretoken_")),
    _Signature(
        "Adobe Experience Manager", SOURCE_FILE,
        file="your page component's `customheaderlibs.html`",
        paths=("/etc.clientlibs", "/content/dam", "/libs/granite")),
    _Signature(
        "Next.js", SOURCE_FILE,
        file="the layout that wraps every page - `app/layout.tsx`, or "
             "`pages/_document.js` on older versions",
        words=("next.js",),
        paths=("/_next/static", "/_next/image"),
        frameworks=("__NEXT_DATA__", "self.__next_f", "#__next")),
    _Signature(
        "Nuxt", SOURCE_FILE,
        file="the `app.head` block in `nuxt.config.ts`, or `app.vue`",
        words=("nuxt",),
        paths=("/_nuxt",),
        frameworks=("__NUXT__", "#__nuxt")),
    _Signature(
        "Gatsby", SOURCE_FILE,
        file="`gatsby-ssr.js`, in its `onRenderBody` hook",
        words=("gatsby",),
        paths=("/page-data",),
        frameworks=("#gatsby-focus-wrapper",)),
    _Signature(
        "Astro", SOURCE_FILE,
        file="the layout every page imports, under `src/layouts/`",
        words=("astro",),
        paths=("/_astro",)),
    _Signature(
        "Hugo", SOURCE_FILE,
        file="`layouts/_default/baseof.html`, or `layouts/partials/head.html`",
        words=("hugo",)),
    _Signature(
        "Jekyll", SOURCE_FILE,
        file="`_layouts/default.html`, or `_includes/head.html`",
        words=("jekyll",)),
)

_SIGNATURE_BY_NAME = {sig.name: sig for sig in PLATFORM_SIGNATURES}


def _host_labels(host):
    """The words in a hostname, minus `www` and the empty pieces."""
    return {label for label in strip_www(str(host or "").lower()).split(".") if label}


def _families_that_fired(signals, own_labels):
    """{platform name: {family: the value that fired}} for one page's signals.

    The value is kept, not just the fact that something matched, because a
    finding that names a platform has to be able to print what said so. "We
    read `cdn.<host>` in your page's script tags" is arguable; "we detected
    Shopify" is not.
    """
    signals = signals or {}
    declared = " ".join(str(signals.get(key) or "")
                        for key in ("generator", "powered_by")).lower()
    hosts = set(own_labels)
    host_text = {}
    for host in signals.get("asset_hosts") or ():
        for label in _host_labels(host):
            hosts.add(label)
            host_text.setdefault(label, str(host))
    paths = [str(p).lower() for p in signals.get("asset_paths") or ()]
    attributes = [str(a).lower() for a in signals.get("markup_attributes") or ()]
    frameworks = [str(f) for f in signals.get("framework_markers") or ()]
    headers = [str(h).lower() for h in signals.get("header_names") or ()]
    cookies = [str(c).lower() for c in signals.get("cookie_names") or ()]

    out = {}
    for sig in PLATFORM_SIGNATURES:
        fired = {}
        word = next((w for w in sig.words if w in declared), "")
        if word:
            fired["generator"] = "the page declares itself built with \"{}\"".format(word)
        label = next((h for h in sorted(sig.hosts) if h in hosts), "")
        if label:
            fired["asset-host"] = "the pages load assets from \"{}\"".format(
                host_text.get(label, label))
        path = next((p for p in sig.paths
                     if any(seen.startswith(p) for seen in paths)), "")
        if path:
            fired["asset-path"] = "assets are served under \"{}\"".format(path)
        attribute = next((a for a in sig.attributes if a in attributes), "")
        if attribute:
            fired["markup-attribute"] = "the page's root element carries \"{}\"".format(
                attribute)
        marker = next((f for f in sig.frameworks if f in frameworks), "")
        if marker:
            fired["framework-marker"] = "the page ships \"{}\"".format(marker)
        header = next((h for h in sig.headers
                       if any(seen == h or seen.startswith(h) for seen in headers)), "")
        if header:
            fired["response-header"] = "the server sends a \"{}\" header".format(header)
        cookie = next((c for c in sig.cookies
                       if any(seen.startswith(c) for seen in cookies)), "")
        if cookie:
            fired["cookie-name"] = "the server sets a \"{}\" cookie".format(cookie)
        if fired:
            out[sig.name] = fired
    return out


class PublishingPlatform:
    """What a site is built with, how sure that is, and on what evidence.

    Modelled on `SiteKind`, and read the same way. Three gates, and choosing
    between them is the whole of the interface:

      named        the answer is `high`, so a finding may print this platform's
                   own menu wording and its own file names. `bool(platform)` is
                   this, so `if platform:` means "may I name it".
      determined   `high` or `medium`. Enough to say "your site looks like it
                   is built with X" out loud, in a sentence that also says what
                   to do if it is not.
      is_certainly(*names) / might_be(*names)
                   the `SiteKind` gates, for a check that behaves differently
                   on a hosted platform - `might_be` answers True for an
                   undetermined site, so gating on it removes nothing.

    Nothing below reads the platform's name out of prose, and `.name` is "" far
    more often than it is filled in. That is the intended shape: the sentence
    for a site nobody can name is the one most readers get, and it is written
    to be worth reading.
    """

    __slots__ = ("name", "confidence", "signals", "evidence", "pages", "reach")

    def __init__(self, name="", confidence="low", signals=(), evidence=(),
                 pages=0, reach=""):
        self.name = name
        self.confidence = confidence
        self.signals = tuple(signals)
        self.evidence = tuple(evidence)
        self.pages = pages
        self.reach = reach

    def __repr__(self):
        return "PublishingPlatform({!r}, {!r}, {!r})".format(
            self.name, self.confidence, self.signals)

    def __eq__(self, other):
        if isinstance(other, PublishingPlatform):
            return ((self.name, self.confidence, self.signals, self.evidence,
                     self.pages, self.reach)
                    == (other.name, other.confidence, other.signals,
                        other.evidence, other.pages, other.reach))
        return NotImplemented

    def __hash__(self):
        return hash((self.name, self.confidence, self.signals, self.evidence,
                     self.pages, self.reach))

    def __bool__(self):
        return self.named

    @property
    def named(self):
        """May a finding print this platform's own vocabulary?"""
        return bool(self.name) and self.confidence == "high"

    @property
    def determined(self):
        """Is the guess worth saying out loud, hedged?"""
        return bool(self.name) and self.confidence in ("high", "medium")

    def is_certainly(self, *names):
        return self.named and self.name in names

    def might_be(self, *names):
        """True unless this site is confidently something else."""
        if not self.determined:
            return True
        return self.name in names

    def why(self):
        """The evidence, as a phrase a finding can print."""
        return "; ".join(self.evidence)


def publishing_platform(snapshot):
    """A `PublishingPlatform`: what this site is built with, and how sure.

    Always an object, never None. A site nobody can name comes back with an
    empty `.name` and falsy, so `if platform:` reads the way `is not None`
    would - and the six callers do not each have to remember a None branch
    before they can ask the object a question.

    Reads `page["platform_signals"]`, which `page_extract` records during the
    one shared crawl. Nothing is re-parsed and no page is re-read: the cost
    here is a loop over at most sixty small dictionaries.

    The rules, in the order they decide:

      ambiguity   two platforms leaving equally thick traces is a site whose
                  template nobody here can locate - a headless storefront, a
                  blog bolted onto a shop. It returns unnamed, with both names
                  in the evidence, because sending that reader to either
                  console is sending them to the wrong one.
      certainty   `high` needs two families *and* one of them self-declaring
                  *and* the signal on half the crawl. Two families with no
                  self-declaring one, or one self-declaring family across the
                  site, is `medium`. Anything else is `low` and names nothing.
                  A runner-up with two families of its own caps the winner at
                  `medium` whatever else it has.
    """
    pages = [p for p in (snapshot or {}).get("pages") or []
             if isinstance(p.get("platform_signals"), dict)]
    if not pages:
        return PublishingPlatform()
    host = str(((snapshot or {}).get("brand") or {}).get("host") or "")
    if not host:
        host = urlparse(pages[0].get("final_url") or pages[0].get("url") or "").netloc
    own_labels = {label for label in _host_labels(host)
                  if label not in _REGISTRY_HOST_LABELS}

    tally = {}
    for page in pages:
        for name, fired in _families_that_fired(page["platform_signals"], own_labels).items():
            entry = tally.setdefault(name, {"families": {}, "pages": 0})
            entry["pages"] += 1
            for family, value in fired.items():
                entry["families"].setdefault(family, value)
    if not tally:
        return PublishingPlatform(pages=len(pages))

    ranked = sorted(tally.items(),
                    key=lambda kv: (-len(kv[1]["families"]), -kv[1]["pages"], kv[0]))
    name, best = ranked[0]
    runner_name, runner = (ranked[1] if len(ranked) > 1 else ("", {"families": {}}))
    families = [f for f in PLATFORM_SIGNAL_FAMILIES if f in best["families"]]
    evidence = tuple(best["families"][f] for f in families)

    if runner_name and len(runner["families"]) >= len(families):
        return PublishingPlatform(
            "", "low", families,
            ("two platforms left equal traces here, {} and {}, so this audit "
             "will not name either".format(name, runner_name),) + evidence,
            best["pages"])

    share = best["pages"] / float(len(pages))
    self_declaring = bool(set(families) & PLATFORM_SELF_DECLARING_FAMILIES)
    site_wide = share >= PLATFORM_SITE_WIDE_SHARE
    if len(families) >= 2 and self_declaring and site_wide:
        confidence = "high"
    elif len(families) >= 2 or (self_declaring and site_wide):
        confidence = "medium"
    else:
        confidence = "low"
    if confidence == "high" and len(runner["families"]) >= 2:
        confidence = "medium"
    if confidence == "low":
        name = ""
    signature = _SIGNATURE_BY_NAME.get(name)
    return PublishingPlatform(name, confidence, families, evidence, best["pages"],
                              signature.reach if signature else "")


def _as_platform(value):
    """Accept a `PublishingPlatform`, a snapshot, or None from any caller.

    A check that already resolved the platform once should not pay for it
    again, and a check that has only the snapshot should not have to know that.
    """
    if isinstance(value, PublishingPlatform):
        return value
    if isinstance(value, dict) and "pages" in value:
        return publishing_platform(value)
    return PublishingPlatform()


# What to tell somebody whose platform this audit could not name. Written for
# the reader who knows one thing only - that somebody edits their pages - and
# deliberately better than the sentence it replaces, because this is the answer
# most of the open web gets.
UNKNOWN_PLATFORM_LOCATION = (
    "This audit could not tell what this site is built with, so it cannot name "
    "the file. Whatever produces the pages, one template produces the top of "
    "every one of them: the part that holds the `<title>` tag and ends with "
    "`</head>`. Search the site's theme or source files for the text `</head>` "
    "- the file that contains it and is used by every page is the one to "
    "change, and changing it once covers the whole site.")

UNKNOWN_PLATFORM_OWNER = (
    "If you do not know who edits your pages, ask whoever built or maintains "
    "the site: an agency, a freelancer, or failing both your hosting "
    "provider's support, who can at least tell you which system the site runs "
    "on. That is the first thing any developer you hire will ask you.")


def where_the_template_is(platform):
    """One sentence: where this site's site-wide template is, in its own words.

    Accepts a `PublishingPlatform` or a snapshot. Says where the template *is*
    and not what to put in it - the slot inside the template belongs to the
    caller, because a check adding an `og:image` and a check adding a
    BreadcrumbList are not putting it in the same place.

    Never names a platform this audit is not sure of: `medium` says the name
    and immediately says what to do if the guess is wrong, and anything weaker
    returns the sentence for a site nobody can name - which is the sentence
    most readers get and is not the leftover one.
    """
    platform = _as_platform(platform)
    signature = _SIGNATURE_BY_NAME.get(platform.name) if platform.determined else None
    if signature is None:
        return UNKNOWN_PLATFORM_LOCATION

    if signature.reach == ADMIN_SCREEN and signature.head_slot:
        sentence = "On {} this does not need a file at all: {}, which puts the " \
                   "code into the head of every page for you.".format(
                       signature.name, signature.admin_path)
    elif signature.reach == ADMIN_SCREEN:
        sentence = "On {} that template is {}, reached through {}.".format(
            signature.name, signature.file, signature.admin_path)
    else:
        sentence = "This site is built with {}, so the template every page shares " \
                   "is {}.".format(signature.name, signature.file)
    if signature.note:
        sentence += " " + signature.note
    if platform.confidence == "high":
        return sentence
    return ("This site looks like it is built with {} ({}), though not certainly. "
            "If it is: {} If that screen or file is not there, it is not {}: {}".format(
                signature.name, platform.why(), sentence, signature.name,
                UNKNOWN_PLATFORM_LOCATION))


def who_edits_the_template(platform):
    """One sentence naming who makes this change, for a reader who has nobody.

    The half of the failure that is not about files. A report can be exactly
    right that a change is an hour's work and still be useless to the person
    reading it, because "developer" is a role they do not employ. Every
    sentence here ends somewhere a reader can actually go.

    Three answers, not two, and which one is right is settled by what
    `where_the_template_is` just told the same reader one line above:

      a settings screen    the product has a box for head code, the sentence
                           above names that box, and no file is involved
      a named file         the sentence above names `header.php` or
                           `layout/theme.liquid`. Reaching a file through an
                           admin menu does not stop it being theme code
      no platform named    the fallback, which is most of the open web

    The middle answer is the row that went wrong in ten places across
    three reports: "On WordPress that template is `header.php`, reached through
    Appearance -> Theme File Editor" was followed immediately by "This is a
    settings screen in the admin rather than a code release", contradicting the
    line above it. Editing `header.php` is a code change. The same
    sentence read correctly on the site-builder instance beside it -
    because there the fix really was a settings panel - which is exactly why
    the owner sentence has to follow the location sentence rather than the
    reach alone.
    """
    platform = _as_platform(platform)
    signature = _SIGNATURE_BY_NAME.get(platform.name) if platform.named else None
    if signature is None:
        return UNKNOWN_PLATFORM_OWNER
    if signature.reach == ADMIN_SCREEN and signature.head_slot:
        return ("This is a settings screen in the {name} admin rather than a code "
                "release, so whoever holds the {name} login can make it - often the "
                "person who runs the site day to day. If that is not you, it is the "
                "agency or freelancer who set the site up.".format(name=signature.name))
    if signature.reach == ADMIN_SCREEN:
        return ("This edits a template file rather than a setting, so it is a code "
                "change - made through the {name} admin, with no deployment, but a "
                "change to the theme all the same. Whoever looks after the site's "
                "theme makes it: in-house that is whoever edits templates, and "
                "otherwise the agency, freelancer or contractor who built the site - "
                "the name on your web-development invoices is usually the right "
                "person to ask.".format(name=signature.name))
    return ("This is a file in the site's source code, so it ships with a release "
            "and needs whoever deploys the site. If nobody in-house does that, it "
            "is the agency, freelancer or contractor who built it - the name on "
            "your web-development or hosting invoices is usually the right person "
            "to ask.")


def template_change_steps(platform, what="the block below",
                          where=DEFAULT_TEMPLATE_SLOT):
    """The two `how_to_fix` steps every site-wide markup fix ends with.

    Returns a tuple, so a check splices it onto whatever steps are its own:

        how_to_fix=["Write the Organization block.",
                    *template_change_steps(platform, "that block")]

    One function rather than six copies, for the reason this library exists:
    six checks each phrasing "add it to the site-wide template" their own way
    is how a marketplace ends up saying six different things about one file.
    """
    platform = _as_platform(platform)
    signature = _SIGNATURE_BY_NAME.get(platform.name) if platform.determined else None
    if signature is not None and signature.head_slot and platform.confidence == "high":
        # No `</head>` in this branch on purpose: the screen never shows the
        # reader one, and sending them to look for a tag their product does not
        # expose is the same class of unfollowable instruction this replaces.
        first = "Put {} into your site's head code, so every page carries it. {}".format(
            what, where_the_template_is(platform))
    else:
        first = "Put {} {} in the template every page shares, so one edit covers " \
                "the whole site. {}".format(what, where, where_the_template_is(platform))
    return (first, who_edits_the_template(platform))


# Properties whose value is a different subject from the node holding it. An
# Event's `location` is a venue, a Product's `brand` is a manufacturer, an
# Article's `author` is a person. Extraction flattens nested nodes so that a
# presence check can see them, and a flattened node looks exactly like one the
# page declared about itself.
#
# What that cost: a ticket marketplace's paste-ready Organization block was
# given `"streetAddress": "334 1st Ave N", "addressLocality": "Seattle"` - the
# address of an arena, lifted out of an Event's `location` on a performer page
# and published as the company's own headquarters. The same report knew
# better two sections later, quoting the company's real address off its
# privacy page and its own sentence saying where it is based. A developer who
# pastes that snippet publishes a false head office.
SUBJECT_CHANGING_PROPERTIES = frozenset({
    "location", "performer", "performers", "author", "creator", "publisher",
    "provider", "seller", "sponsor", "funder", "brand", "manufacturer",
    "partof", "ispartof", "about", "mentions", "subject", "attendee",
    "organizer", "affiliation", "worksfor", "memberof", "member",
    "suborganization", "parentorganization", "recipient", "producer",
    "director", "actor", "contributor", "translator", "reviewedby", "review",
    "itemreviewed", "vendor", "hostingorganization", "alumniof",
})


def speaks_for_the_brand(node, brand_name=""):
    """May a value on this node be published as a fact about the audited site?

    Three answers, in order:

      a node the page declared at the top level speaks for the page
      a node lifted out of a subject-changing property does not, unless the
        name on it or on its parent is the brand's own
      a node lifted out of any other property inherits its parent's standing

    The name test runs through the shared comparison, so a legal suffix or a
    trading name still matches. Where nothing carries a name there is nothing
    to contradict, and a nested `address` or `contactPoint` is read as the
    parent's - which is what those properties mean.
    """
    if not isinstance(node, dict):
        return False
    nested_in = str(node.get("_nested_in") or "").lower()
    if not nested_in:
        return True
    if nested_in not in SUBJECT_CHANGING_PROPERTIES:
        return True
    for candidate in (node.get("name"), node.get("_parent_name")):
        if isinstance(candidate, str) and candidate.strip():
            return names_match(brand_name, candidate) if brand_name else False
    return False


def jsonld_type_names(value):
    """Every schema.org type name in an `@type`, however the site writes it.

    Three shapes the web uses that a naive reader gets wrong:

      a list         `["Product", "Offer"]`
      a full IRI     `"https://schema.org/Product"`
      a compact IRI  `"schema:Product"`, valid whenever `@context` maps the
                     prefix, and what several CMS templates emit

    Only the first two were handled, so a site declaring `schema:Organization`
    - correct, and validated by Google - was told it carries no Organization
    markup at all.

    Anything that is neither a string nor a list yields nothing rather than
    raising. One template interpolated a numeric id into `@type`, and
    `for v in 5` ended the whole run with a TypeError and no report.
    """
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return set()
    return {str(v).split("/")[-1].split("#")[-1].split(":")[-1].lower()
            for v in values if isinstance(v, (str, int, float))}


def is_question_heading(heading):
    """True for a heading a visitor would recognise as a question.

    Shared so the classifier and the FAQPage snippet cannot disagree about
    what a question is - they did, and the snippet was the one marking up
    policy statements and legal text as question-and-answer pairs.
    """
    return bool(_QUESTION_HEADING_RE.match(heading or ""))


# A region's storefront at a one-segment address, which is a homepage.
#
# A furniture retailer answers `/` with a country picker and keeps one full
# storefront per country at `/sg`, `/au`, `/ca` and `/uk`. Each of those carries
# FAQPage markup, so each was typed `faq`, and only `/` was ever called home -
# so a check about on-site search graded the picker, while every regional home
# declares a `SearchAction`. The language rule above could not help: the
# segment is a country and the page says `lang="en-SG"`, so the segment is the
# region subtag, not the language.
#
# Three things together, so an ordinary two-letter section is never promoted:
# the path is one segment shaped like a country or a language-region pair; the
# root page links to it and to at least one more such segment, which is what a
# region picker is and an "IT" or "HR" page is not; and the page's own `lang`
# names that region (or that language). `uk` is the one country whose path
# segment and region subtag differ by convention - the subtag is `GB`.
_REGION_PATH_RE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2})?$", re.I)
_REGION_SUBTAG_ALIASES = {"uk": "gb"}
REGIONAL_HOME_MIN_SIBLINGS = 2


def _region_segment(url):
    """The one path segment of `url`, lower case, if it is region-shaped."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.query:
        return ""
    segments = [s for s in (parsed.path or "").split("/") if s]
    if len(segments) != 1 or not _REGION_PATH_RE.match(segments[0]):
        return ""
    return segments[0].lower().replace("_", "-")


def regional_homepage(url, lang, root_links):
    """Is this page one region's homepage, linked from a region picker at `/`?

    `root_links` is the internal links of the site's root page. See
    `_REGION_PATH_RE` above for the three conditions and why each is there.
    """
    code = _region_segment(url)
    if not code:
        return False
    # A crawled page records each link as `{"url": ..., "text": ...}`; a
    # caller holding bare addresses passes strings. Both are read.
    linked = {_region_segment(link.get("url") or "" if isinstance(link, dict) else link)
              for link in root_links or () if isinstance(link, (str, dict))}
    linked.discard("")
    if code not in linked or len(linked) < REGIONAL_HOME_MIN_SIBLINGS:
        return False
    declared = str(lang or "").strip().lower().replace("_", "-")
    if not declared:
        return False
    parts = declared.split("-")
    if "-" in code:
        return declared == code
    region = _REGION_SUBTAG_ALIASES.get(code, code)
    if len(parts) > 1 and parts[-1] == region:
        return True
    return parts[0] == code


# How little of its own a root page carries before a row of regional homes
# makes it a gateway rather than a homepage: no H1 and less text than this.
GATEWAY_MAX_TEXT_CHARS = 600


def site_homepage(snapshot):
    """The site's front door, decided once for every skill that says "the homepage".

    `{"url", "page", "is_gateway", "regional_homes", "why"}`.

    The root page, unless the root is a gateway - a country or language picker
    leading to separate regional storefronts. Then `is_gateway` is true,
    `regional_homes` lists the storefronts' addresses, and `page` is still the
    root: a skill describing "the homepage" of such a site describes the picker
    and says it is one, instead of quietly choosing one country. A furniture
    retailer's root has no H1 and links five storefronts; one skill graded the
    root as having no heading while another quoted the `/au` storefront's H1,
    "Up to $600 off", as what "the homepage leads with" - two different pages
    called the homepage in one report.

    A gateway needs both halves: at least `REGIONAL_HOME_MIN_SIBLINGS` regional
    homes the root links to (`regional_homepage`), and a root that carries
    little of its own - no H1 and under `GATEWAY_MAX_TEXT_CHARS` of text. A
    full homepage with a country switcher in its footer is still the homepage.
    """
    pages = pages_of(snapshot)
    root = None
    for page in pages:
        try:
            parsed = urlparse(page.get("url") or "")
        except ValueError:
            continue
        if (parsed.path or "/") == "/" and not parsed.query:
            root = page
            break
    if root is None:
        first_home = next((p for p in (snapshot.get("pages") or [])
                           if p in pages and p.get("page_type") == "home"), None)
        return {"url": (first_home or {}).get("url") or snapshot.get("origin") or "",
                "page": first_home, "is_gateway": False, "regional_homes": [],
                "why": "the root page was not among the pages read" if first_home is None
                       else "the root page was not read, so the first homepage the crawl reached"}
    root_links = (root.get("links") or {}).get("internal") or []
    regional = [p["url"] for p in pages if p is not root
                and regional_homepage(p.get("url") or "", p.get("lang"), root_links)]
    heading = any(str(h).strip() for h in ((root.get("headings") or {}).get("h1") or []))
    text_len = root.get("body_text_len")
    if text_len is None:
        text_len = len(root.get("body_text") or "")
    thin = not heading and text_len < GATEWAY_MAX_TEXT_CHARS
    is_gateway = len(regional) >= REGIONAL_HOME_MIN_SIBLINGS and thin
    return {
        "url": root.get("url"), "page": root, "is_gateway": is_gateway,
        "regional_homes": sorted(regional) if is_gateway else [],
        "why": ("the root page is a gateway: it has no H1, little text, and links {} regional "
                "homepages".format(len(regional)) if is_gateway else "the root page"),
    }


def detect_page_type(url, html_meta):
    """Classify a page from URL shape, then confirm or override with content.

    `html_meta` is a dict with `headings`, `text`, `jsonld_types`, `title`.
    URL patterns are checked first because they are the strongest and most
    portable signal; content signals then correct the common mistakes (a
    `/shop/thing` listing page vs a real product page, a page that is a FAQ
    without saying so in the URL).
    """
    parsed = urlparse(url)
    # Decoded, so a slug written in its own script - a Persian or a Japanese
    # blog section - is compared as the word it is rather than as the
    # percent-escapes it travels in.
    path = unquote(parsed.path or "/").rstrip("/").lower() or "/"
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
            and primary_subtag(html_meta.get("lang")) == locale_language(locale)):
        return "home"
    # One region's storefront, where the caller knows what the root page links
    # to. See `regional_homepage`: the crawl passes the root's links once it
    # has read the root, and a caller that has not read it passes nothing.
    if regional_homepage(url, html_meta.get("lang"), html_meta.get("root_links")):
        return "home"

    # Date-shaped paths are decided before anything else. A blog permalink such
    # as /2004/Jun/29/job/ ends in a slug that would otherwise be read as a
    # careers page, and its date is far stronger evidence than its slug.
    if _YEAR_ARCHIVE_RE.match(path):
        return "category"
    # A path that ends at the date it archives, rather than at a slug.
    #
    # `_DATED_PERMALINK_RE` reads a year followed by anything as a post, so a
    # blog's monthly archives - `/blog/2025/1/`, `/blog/2025/10/` - were typed
    # `article`, told they carry no Article markup, and handed a paste-ready
    # block giving the archive index a `headline`, a `datePublished` and a
    # named author. `_YEAR_ARCHIVE_RE` knew this shape only when the year was
    # the first segment, which it is on almost no real blog. A permalink ends
    # in words; an archive ends in numbers that are a month or a day.
    if DATED_ARCHIVE_RE.search(path):
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

    # A grouping word followed by the name of the group. Decided here, above
    # both the markup branch and the slug branch, because those two are what
    # got it wrong: `/collections/<handle>` reached the slug branch as
    # `category` and was then promoted to `product` by the buying affordances
    # its own items carry, and `/blogs/<blog>/tagged/<tag>` reached it as
    # `article` because `blogs` is an article slug. A page whose address says
    # "everything filed under this heading" is a listing whatever is printed on
    # it, and a listing is never asked for Product or Article markup.
    if _FACETED_LISTING_RE.search(path):
        return "category"

    # A page assembled from a typed query is not a document the site publishes,
    # and it is a list of other pages whatever its address looks like. Nothing
    # in this function asked, so a search address carrying a shop slug reached
    # the product branch and was typed `product` - and then counted among the
    # product pages missing Product markup, which is advice about a page its
    # owner cannot edit. `is_listing_page` has read these as listings all
    # along; this is the classifier agreeing with it.
    if is_search_result_page(url):
        return "category"

    # Does the page read as a list of links to other pages? Computed once and
    # asked by both branches below that can call something an article, because
    # a page that is an index is an index whether the slug or the markup was
    # what suggested otherwise.
    looks_like_an_index = is_listing_page({
        "url": url,
        "headings": headings,
        "links": html_meta.get("links") or {},
        "jsonld_types": sorted(jsonld_types),
        "text": text,
    })
    # A page of links with little prose of its own, which is a listing
    # wherever the address or the markup would otherwise call it an article.
    # Asked only of the article decision: a shop's item page with a carousel of
    # related links is settled by its own price and buy control, not by this.
    # See `reads_as_a_link_list`.
    is_a_link_list = reads_as_a_link_list({
        "links": html_meta.get("links") or {}, "text": text})

    # A sign-up address is a pricing page only where the page prices
    # something. See `_SIGN_UP_PRICING_PATTERN`.
    not_pricing = _signs_up_without_a_price(path, text)

    # Unambiguous URL slugs win outright. A pricing page that also carries
    # Product schema is still a pricing page, and classifying it as a product
    # would make every downstream expectation wrong.
    for page_type, pattern in _URL_TYPE_PATTERNS:
        if page_type == "pricing" and not_pricing:
            continue
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
    # Unless the questions are one block on a page about something else. See
    # `_reads_as_a_page_of_questions`.
    if jsonld_types & {"faqpage", "qapage"} and _reads_as_a_page_of_questions(
            headings, html_meta.get("title") or ""):
        return "faq"
    if jsonld_types & {"blogposting", "newsarticle", "article", "techarticle"}:
        # Unless the page is the index of those articles. A blog template
        # emits one `BlogPosting` node per post it lists, so the archive
        # declares the same types its posts do, and the site is not wrong to
        # do that - the nodes describe the items, not the page. Only the page
        # itself can say which it is, and it says so by putting its headings
        # in the links leaving it.
        return "category" if (looks_like_an_index or is_a_link_list) else "article"
    if jsonld_types & {"localbusiness", "store", "restaurant", "hotel"} and path != "/":
        return "location"

    for page_type, pattern in _URL_TYPE_PATTERNS:
        if page_type == "pricing" and not_pricing:
            continue
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
                #
                # Depth raises confidence that a page names one thing. It does
                # not answer whether the page is a shelf, and while it was
                # allowed to short-circuit, it waived the only test that does:
                # `DISTINCT_PRICE_CEILING`, the measured separator between an
                # item and a list of them, is applied inside
                # `_looks_like_product_detail` and nowhere else. Every site
                # that prefixes its paths with a language and a country is deep
                # on every page, so every grid it publishes was an item. A
                # retailer was told "3 of 3 product pages have no Product
                # markup" about a gift category, a sale listing and a
                # search-results address.
                #
                # The page reading as an index of other pages settles it the
                # same way and was computed above without this branch ever
                # asking.
                deep = len([s for s in path.strip("/").split("/") if s]) >= 2
                names_one_item = (
                    deep
                    and (_PRICE_RE.search(lower_text) or _product_signal_count(lower_text))
                    and not shows_a_shelf_of_prices(lower_text)
                    and not looks_like_an_index)
                if not names_one_item:
                    if not _looks_like_product_detail(lower_text, html_meta):
                        return "category"
            if page_type == "category" and _looks_like_product_detail(lower_text, html_meta):
                return "product"
            # `/blog` is the index of a section; `/blog/a-post` is the article.
            # Treating the index as an article would demand `datePublished` and
            # an author on a page that is a list of links.
            if page_type == "article" and (_is_section_root(path)
                                           or looks_like_an_index or is_a_link_list):
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


# The pricing slugs that name joining rather than paying. A national museum's
# five membership sign-up pages - choose a membership type, agree to the terms,
# enter your details - sit at `/membership.do` and carry forms and no figure
# at all; all five were typed `pricing`, and the sign-up form became the site's
# "Pricing" key page. The address word is the evidence for a pricing page only
# where it is the word for a price; a word for signing up needs the page to
# price something before it means the same.
_SIGN_UP_PRICING_SLUGS = ("subscribe", "membership", "memberships")
_SIGN_UP_PRICING_PATTERN = _url_type_pattern("pricing", _SIGN_UP_PRICING_SLUGS)
_PRICE_WORD_PATTERN = _url_type_pattern("pricing", tuple(
    slug for page_type, slugs in _URL_TYPE_SLUGS if page_type == "pricing"
    for slug in slugs if slug not in _SIGN_UP_PRICING_SLUGS))


def _signs_up_without_a_price(path, text):
    """Is this a sign-up address whose page states no price?"""
    if not re.search(_SIGN_UP_PRICING_PATTERN, path or ""):
        return False
    if re.search(_PRICE_WORD_PATTERN, path or ""):
        return False
    return not find_prices(text or "", limit=1)


# A heading or title that says the page is the questions.
_NAMES_ITSELF_A_FAQ_RE = re.compile(
    r"\b(?:faqs?|frequently\s+asked\s+questions|q\s*&\s*a)\b", re.I)


def _reads_as_a_page_of_questions(headings, title):
    """Does a page declaring FAQPage markup read as a page of questions?

    The markup describes a block, and a block can sit on any page. An app's
    landing page - what the app does, why to download it, and a "Frequently
    asked questions" section at the foot - declares FAQPage for that section,
    was typed `faq` on the markup alone, and a finding then counted it as "1
    of the 2 faq pages". The page says what it is in its headings: its H1 or
    its title naming the questions, questions for most of its headings, or no
    sections at all beside them.
    """
    headings = headings or {}
    if _NAMES_ITSELF_A_FAQ_RE.search(title or "") or any(
            _NAMES_ITSELF_A_FAQ_RE.search(str(h)) for h in headings.get("h1") or []):
        return True
    sections = list(headings.get("h2") or []) + list(headings.get("h3") or [])
    if not headings.get("h2"):
        return True
    questions = sum(1 for heading in sections if is_question_heading(heading))
    return questions * 2 >= len(sections)


_SECTION_ROOTS = frozenset({
    "blog", "news", "articles", "article", "posts", "post", "insights",
    "stories", "resources", "guides", "press", "updates", "journal",
    "episodes", "podcast", "podcasts", "transcripts",
}) | frozenset(ARTICLE_SLUGS_ELSEWHERE)


# A year, then optionally a month, then optionally a day, and nothing after
# it. `/blog/2025/`, `/news/2025/04/`, `/2025/4/12/` are archives; `/blog/2025/
# the-year-in-review` and `/blog/2025/4471` are posts.
#
# Public, and stable. `structured-data-audit` carried a character-for-character
# copy of this pattern under its own name, differing only in spelling `\d` as
# `[0-9]`, and two copies of one rule in two files that two people edit drift
# apart without either of them noticing - which is how a page typed `article`
# here and `category` there gets told twice about markup it should not carry.
# Renamed from `_DATED_ARCHIVE_RE` so the other copy can be deleted and this
# one imported.
DATED_ARCHIVE_RE = re.compile(
    r"/(?:19|20)[0-9]{2}"
    r"(?:/(?:0?[1-9]|1[0-2]))?"
    r"(?:/(?:0?[1-9]|[12][0-9]|3[01]))?/?$")


def _is_section_root(path):
    """True for `/blog` or `/blogs/news`, false for `/blog/a-post`.

    A word list on the path, which is one of two tests the caller applies. It
    catches the conventional addresses and nothing else, and the naming space
    is not closed: a shop platform lets an owner name each blog handle
    (`/blogs/coffee-spotlight`) and a blogging platform puts the section word
    in the first segment (`/category/news/`). Both were typed `article`, both
    were then told their index page carries no Article markup, and one was
    handed a snippet declaring the archive's listing heading as a `headline`
    with a `datePublished`. The caller's second test, `is_listing_page`, reads
    the page instead of the slug and covers what no word list can.

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


def shows_a_shelf_of_prices(lower_text):
    """More different prices than one item can have.

    The measured separator, on its own, so the depth of a URL can consult it
    without going through the whole detail test. A deep path is evidence that a
    page names one thing; it is not evidence that the page is not a shelf, and
    the two readings have to be asked separately or the stronger one silently
    waives the other.
    """
    prices = {m.group(0).strip() for m in _PRICE_RE.finditer(lower_text)}
    return len(prices) >= DISTINCT_PRICE_CEILING


def _looks_like_product_detail(lower_text, html_meta):
    """A single purchasable item, not a page listing several of them.

    Used where the URL gives no help. Where the URL does - a deeper path under a
    products section - that evidence is stronger and is applied first.
    """
    if not _PRICE_RE.search(lower_text) or shows_a_shelf_of_prices(lower_text):
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


# --------------------------------------------------------------------------
# A run of digits that is not a telephone number
#
# A university book-review page prints an ISBN under a caption naming it as
# one, and the phone pattern - nine or more digits - read the thirteen-digit
# barcode as the university's contact route. That suppressed a real "no
# contact route" finding.
#
# It lives here because three skills read the same `contact_facts` field and
# would otherwise each need their own copy: the core-facts check, the NAP
# comparison in freshness, and the identity check in structured data. Two
# copies of one rule is the drift this codebase argues against everywhere.
# --------------------------------------------------------------------------

_CODE_LABEL_RE = re.compile(
    r"\b(?:isbn|issn|ismn|ean|upc|gtin|sku|asin|doi|iban|vat|abn|acn|barcode"
    r"|serial|invoice|order|reference|ref|account|licence|license|registration"
    r"|company|charity|batch|lot|item|model|part|product|id)"
    r"[\s.:#-]*(?:no|nos|num|number|code|コード|番号)?[\s.:#-]*$",
    re.I)

# How many characters in front of the digits are read for that label. A caption
# sits directly above the number it names, and the extractor joins the two
# blocks with a space.
CODE_LABEL_WINDOW = 48


def _isbn13_check_digit_computes(digits):
    """Does this run satisfy the ISBN-13 check digit, prefix included?

    Arithmetic rather than a list of known book numbers: weights of 1 and 3
    alternating, and the total is a multiple of ten. The 978/979 prefix is
    required as well, because it is what every ISBN-13 starts with and no
    international dialling code does.
    """
    if len(digits) != 13 or digits[:3] not in ("978", "979"):
        return False
    total = sum(int(d) * (1 if index % 2 == 0 else 3) for index, d in enumerate(digits))
    return total % 10 == 0


def _isbn10_check_digit_computes(text):
    """Does this run satisfy the ISBN-10 check digit? Weights 10 down to 1, mod 11."""
    body = text.strip().upper()
    if not re.match(r"^\d{9}[\dX]$", body):
        return False
    total = sum((10 - index) * (10 if char == "X" else int(char))
                for index, char in enumerate(body))
    return total % 11 == 0


# The letters that end the text in front of a digit run, or begin the text
# after it, when the run is a slice of one longer word. Two or more, so a
# trailing `x22` extension is still a telephone number.
_GLUED_TO_LETTERS_BEFORE_RE = re.compile(r"[A-Za-z]{2,}$")
_GLUED_TO_LETTERS_AFTER_RE = re.compile(r"^[A-Za-z]{2,}")


def not_a_telephone_number(run, before="", after=""):
    """Why this run of digits is some other kind of code, or '' if it is a number.

    Rules, not a blocklist. A telephone number is written for a person to dial,
    so it carries spaces, dots, brackets or a leading `+`; a barcode is one
    unbroken run whose last digit is computed from the others.

    `after` is read for one rule only: a run with letters glued to either end
    is part of a longer token and was never dialable. A documentation tree
    prints commit identifiers - `a1b2c3d4e5f60718` - and the digits inside one
    were reported to that project as its telephone number, which then marked
    the "states a contact method" check as passed and suppressed the finding
    that the site states none.
    """
    text = str(run or "").strip()
    digits = re.sub(r"\D", "", text)
    unbroken = not re.search(r"\D", text)
    if _isbn13_check_digit_computes(digits):
        return "its ISBN-13 check digit computes, so the run is a book number"
    if unbroken and len(digits) == 13:
        return "thirteen digits with no separator is a barcode, not a dialable number"
    if unbroken and _isbn10_check_digit_computes(text):
        return "its ISBN-10 check digit computes, so the run is a book number"
    if (_GLUED_TO_LETTERS_BEFORE_RE.search(before or "")
            or _GLUED_TO_LETTERS_AFTER_RE.search(after or "")):
        return ("letters are joined to it with no space, so the digits are part of a "
                "longer identifier rather than a number written to be dialled")
    if _CODE_LABEL_RE.search(before or ""):
        return "the words in front of it name it as another kind of code"
    return ""


def has_price(text):
    return bool(find_prices(text, limit=1))


# How many figures a page is read for before the reading stops. The number
# bounds work on a pathological page; it is not a way of choosing which prices
# matter, and it was doing the second job by accident.
#
# It was five. One real report carried a high-severity finding on a collection page of
# forty items reading "Offer price 18450.0 for <an item> is not among the
# prices the page prints beside that name (500, 1000, 2000, 2001)" - the page
# prints 18,450.00 next to that item's heading, and 500/1000/2000/2001 are the
# figures in its price-filter sidebar, which are simply the first five in the
# document. Every reader of `page["prices"]` inherited that: the field meant to
# say what the page charges said what its filter widget offers.
#
# 120 rather than a number chosen for a grid of forty, because it is the limit
# the callers that already pass one explicitly use, and two numbers for one
# question is the drift this file argues against everywhere.
PRICES_READ_PER_PAGE = 120


def find_prices(text, limit=PRICES_READ_PER_PAGE):
    """The currency figures this text states as prices, in document order.

    A free-delivery floor is filtered out here rather than by each caller,
    because every caller is asking the same question - what does this page say
    something costs - and one of them answering it differently is how a
    shipping banner became a product's price on nineteen pages of one report.
    """
    text = text or ""
    found = []
    for match in _PRICE_RE.finditer(text):
        if _figure_is_a_threshold(text, match.start()):
            continue
        if _figure_is_a_financial_result(text, match.start(), match.end()):
            continue
        found.append(match.group(0).strip())
        if len(found) >= limit:
            break
    return found


def prices_the_page_states(page, limit=20):
    """Every price this page states, its variant menus included.

    Two questions look like one and are not, and `page["prices"]` can only
    answer the first:

      where in the copy is the price?   `page["prices"]`, which is
                                        `find_prices(body_text)` and stays
                                        exactly that. The landing-page check
                                        finds its offset with `body_text.find`,
                                        so a figure that is not in `body_text`
                                        has no position and folding one in
                                        would silently drop that page from the
                                        measurement rather than widen it. The
                                        shelf-versus-detail rule counts
                                        distinct prices in the copy for the
                                        same reason: forty variant prices in
                                        one `<select>` would turn every product
                                        detail page on a storefront into a
                                        category listing, and mistyped product
                                        pages are the biggest single source of
                                        wrong structured-data findings here.
      does the page state this price     this. A product page declaring an
      anywhere at all?                   `Offer` price of 569.00 printed 609
                                         beside its heading and 569.00 inside
                                         `<option>6 - Sold Out - 569.00</option>`,
                                         and correct markup was reported at
                                         high severity as contradicting the
                                         page.

    So the snapshot carries both readings under separate names and neither
    field's meaning moved. `body_text` prices come first and in document order,
    because a caller taking `[0]` wants the price the page leads with; menu
    prices follow, deduplicated by value against them so one price written two
    ways is one price.
    """
    page = page or {}
    visible = list(page.get("prices") or find_prices(page.get("body_text") or ""))
    seen = {v for v in (price_value(p) for p in visible) if v is not None}
    for price in (page.get("control_prices") or ()):
        value = price_value(price)
        if value is not None and value in seen:
            continue
        if value is not None:
            seen.add(value)
        visible.append(price)
    return visible[:limit]


# --------------------------------------------------------------------------
# Finding construction
# --------------------------------------------------------------------------

# How many of a finding's affected addresses the report lists.
#
# Five, until one of this audit's claims was disproved by hand and the
# reader could not check it from the report. The title said 48 pages, the
# list gave five, and the other 43 addresses exist nowhere in the run - so the
# one number a reader would want to spot-check was unavailable at any price.
#
# Twenty is enough to sample a 48-page claim and short enough that a finding
# does not become a page of URLs; the report says in words that the list is the
# first N in alphabetical order rather than a ranking, so nobody reads the
# truncation as a severity order. `affected_page_count` remains the number that
# was not truncated, and every count printed in a finding reads that.
AFFECTED_PAGES_LISTED = 20


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
    checked=None,
):
    """Build one finding in the shape every skill must emit.

    Deliberately strict: a typo in a severity or root-cause tag becomes a
    loud failure in the unit tests rather than a finding that silently never
    dedups or sorts into the wrong bucket.

    `checked` is required for a finding whose root cause asserts a negative,
    and names the independent sources that were consulted and came back empty:

        checked=("visible trail", "BreadcrumbList markup")

    It is not decoration. It goes in the report so a reader can see what the
    claim rests on, and a claim resting on one source is capped at medium
    confidence - because every false positive this marketplace has made on a
    real site was one detector with a finite list of shapes, reported as a
    fact about the site.

    Name the constraint, not only the location. Where what actually limits a
    check is a vocabulary rather than a place - a platform allowlist, a
    language, a set of schema types, a regular expression - the place is the
    less useful half. A finding that names places looked when the real limit
    is a list of platforms recognised leaves the reader unable to tell from
    'we looked in the footer' that a footer link to Mastodon does not count.
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

    sources = tuple(s for s in (checked or ()) if s)
    denial_rests_on_a_list = False
    if root_cause in ABSENCE_CAUSES:
        if not sources:
            raise ValueError(
                "{!r} asserts that something is absent, so it must name what was "
                "checked: pass checked=(...) with the independent sources that came "
                "back empty".format(root_cause))
        if len(sources) < CORROBORATED_ABSENCE_SOURCES and confidence == "high":
            confidence = "medium"
        # The rule, applied where every skill passes through rather than inside
        # the check that raised it. See ABSENCE_DECIDED_BY_A_VOCABULARY above
        # for the four real claims that forced it.
        #
        # Appended to `evidence` and not to `checked`, for the reason the
        # `read_in` field records at the same junction: `len(checked)` decides whether a
        # `medium` absence claim is promoted into the confident tier, so a
        # sentence added there would promote the very readings it qualifies.
        # `evidence` is the field both renderers print directly under the
        # claim, and a reader who has read the claim as a fact has taken it as
        # one.
        #
        # Two sources that are two finite lists are still two lists, so the
        # count clamp above does not cover this: an Indonesian address was
        # missed by the text scan and by the markup scan at once, and two
        # empty readings bought `high`. A denial a list decided is capped at
        # `medium` however many lists agreed.
        if root_cause in ABSENCE_DECIDED_BY_A_VOCABULARY:
            denial_rests_on_a_list = True
            if DENIAL_RESTS_ON_A_FINITE_LIST.strip() not in (evidence or ""):
                evidence = (evidence or "").rstrip() + DENIAL_RESTS_ON_A_FINITE_LIST
            if confidence == "high":
                confidence = "medium"
    elif sources:
        raise ValueError(
            "{!r} reports something observed rather than something absent, so it "
            "carries its own evidence and must not pass checked=(...)".format(root_cause))

    pages = sorted(set(affected_pages or []))
    finding = {
        "id_hint": id_hint,
        "title": title,
        "severity": severity,
        "confidence": confidence,
        "evidence": evidence,
        "mechanism": mechanism,
        "root_cause": root_cause,
        "affected_pages": pages[:AFFECTED_PAGES_LISTED],
        "affected_page_count": len(pages),
        "suggested_action": {
            "summary": summary,
            "effort": effort,
            "owner": owner,
            "how_to_fix": list(how_to_fix),
            "rationale": rationale,
        },
    }
    if sources:
        finding["checked"] = list(sources)
    if denial_rests_on_a_list:
        # Machine-readable beside the sentence, so a test can walk every
        # published finding and fail the build on one that denies from a list
        # without saying so. The sentence alone would make that test a string
        # match on prose somebody will reword.
        finding["denial_rests_on"] = "a finite list"
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
    list is the false-positive guard made visible - a reader can read it and see
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

    def unresolved(self):
        """Checks registered here that neither fired nor declined.

        Registered and executed are different facts, and registration is the
        only trace a check leaves: `check()` is called at the top of a function,
        before it looks at anything. So this accumulator cannot tell a check
        that examined its data and was satisfied from one whose data never
        arrived - both are registered, neither fires, neither declines.

        The failure that made the difference matter: a store answering HTTP 402
        on every URL, with zero pages fetched, printed `homepage-reachable` and
        `non-200-rate` under "These ran against this site and were clean - they
        are not omissions", two sections after the same report said the
        homepage was never fetched.

        This is therefore the candidate list, not the answer. The orchestrator
        resolves it, because only the orchestrator can see whether the crawl
        produced anything for these checks to examine.
        """
        declined = {item["check"] for item in self.not_applicable}
        return sorted(name for name in self.checks_run
                      if name not in self.fired_checks and name not in declined)

    def to_dict(self):
        return {
            "skill": self.skill,
            "findings": sort_findings(self.findings),
            "checks_run": sorted(self.checks_run),
            "fired_checks": sorted(self.fired_checks),
            "not_applicable": sorted(
                self.not_applicable, key=lambda n: (n["check"], n["reason"])
            ),
            # Registered, unanswered, and therefore not provably executed. See
            # `unresolved` above for why this cannot be settled here.
            "unresolved_checks": self.unresolved(),
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
    """The crawl snapshot, with the brand name checked before anything reads it.

    Every sub-skill and the report reader come through here, which is why the
    check sits here: the name a report prints in its verdict, sends to a
    knowledge base and pastes into an `Organization` snippet has to be one
    string, and the two failures `resolve_brand` documents each reached the
    report through four separate checks. The file on disk is not rewritten -
    what the crawl observed stays observed.
    """
    snapshot = read_json(path)
    if "pages" not in snapshot:
        raise ValueError("{} is not a crawl snapshot (no `pages` key)".format(path))
    brand = resolve_brand(snapshot)
    if isinstance(brand, dict):
        snapshot["brand"] = brand
    return snapshot


# The two ways a TLS library says the server's chain stops short of an
# authority it trusts: OpenSSL's verify error 20 and error 21. Both mean the
# host answered and handed over its own certificate without the intermediate
# one between it and the root.
INCOMPLETE_CHAIN_MARKERS = ("unable to get local issuer certificate",
                            "unable to verify the first certificate")
INCOMPLETE_CHAIN_MEANING = (
    "answered over HTTPS but sent its certificate without the intermediate certificate "
    "that links it to a trusted authority, so a client that does not already hold that "
    "intermediate refuses the connection")


def incomplete_certificate_chain(error):
    """Is this fetch error a server chain missing its intermediate certificate?"""
    low = str(error or "").lower()
    return any(marker in low for marker in INCOMPLETE_CHAIN_MARKERS)


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
    # Before the general certificate entry, because this is the one certificate
    # fault with a fix measured in minutes. A national library's server sent
    # its own certificate without the intermediate that links it to an
    # authority clients trust - `openssl s_client` prints "unable to verify the
    # first certificate" - and the report called that "the host did not respond
    # at all" and told the owner to read the CDN log for several days. The host
    # answered; the chain it answered with was one certificate short.
    (INCOMPLETE_CHAIN_MARKERS[0], INCOMPLETE_CHAIN_MEANING),
    (INCOMPLETE_CHAIN_MARKERS[1], INCOMPLETE_CHAIN_MEANING),
    ("certificate", "has an HTTPS certificate a client will not accept"),
    ("sslerror", "has an HTTPS certificate a client will not accept"),
    ("connection reset", "closed the connection part-way through the response"),
    ("connection aborted", "closed the connection part-way through the response"),
    # Before "still arriving", which this text also contains and which is a
    # sentence about the site. This one is a sentence about us: the response
    # was cut off because the phase's own clock ran out, not because the server
    # was slow, and the first match in this tuple wins.
    ("time budget ran out",
     "was still arriving when this audit's own time limit ran out - a limit of "
     "this audit, not a fault in the site"),
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

# There is no list of the words brands add around their own name in a handle.
# One used to sit here - `get`, `the`, `team`, `official`, `hq` and a dozen
# more - and it was unreachable code: every branch of it asked whether the
# handle is one of those words joined to the name, and the containment test
# above it answers yes to all of them first. Reading it as the live rule is
# how a report gets diagnosed as missing `happy<name>` when what it actually
# counts is `<name>` inside anything at all, and a list of decorations could
# never be finished anyway - `happy`, `weare`, `try` and `shop` are not on it.
#
# What containment does not have on its own is a floor, and that is what these
# two bound. A three-letter name is a syllable, and a syllable turns up inside
# handles belonging to other people, so counting it adds somebody else's
# account to the breadth number - the strongest separator this marketplace
# measures, and the one the verdict paragraph reads.
#
#   four characters or more   below that a name is not a name inside a longer
#                             word; it is a coincidence
#   half the handle or more   the rest of a handle that is mostly the name is
#                             decoration. The rest of a handle that is mostly
#                             something else is somebody else's word, and the
#                             name inside it is an accident of spelling
#
# The second bound applies to short names only. What makes a coincidence a
# coincidence is that a few letters are cheap: a four-letter name straddles the
# words of somebody else's handle often enough to matter, and a six-letter one
# does not - so above that length the floor is the whole test, and a brand
# whose handle is its name plus a long word is not thrown away for arithmetic.
# The cost is the other kind of long handle: a name of six letters or more
# inside a fan's or a reseller's account still counts.
#
# Both are waived where the handle marks its own word boundaries - a capital
# or a separator, read before the handle was lower-cased and flattened. A
# handle written as three words has said which of them it is about, and that
# is evidence rather than arithmetic, so it buys back the letter the floor
# costs: a three-letter name is found in `<name>-store` and not in
# `<name>store`.
_HANDLE_NAME_MIN_CHARS = 4
_HANDLE_NAME_UNBOUNDED_CHARS = 6
_HANDLE_WORD_MIN_CHARS = 3

# The words a handle separates for itself, before it was lower-cased: runs of
# digits, runs of capitals, and a capital or nothing followed by lower-case.
_HANDLE_WORD_RE = re.compile(r"[0-9]+|[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


def _handle_spells(handle, key):
    """Does this handle write the brand name as one of its own words?

    A run of them, not one: `TheExampleGroupOnline` separates four words and
    the name is two of them. Runs longer than the name are abandoned rather
    than joined further, so this stays linear in the length of the handle.
    """
    if len(key) < _HANDLE_WORD_MIN_CHARS:
        return False
    words = [word.lower() for word in _HANDLE_WORD_RE.findall(handle or "")]
    for start in range(len(words)):
        joined = ""
        for word in words[start:]:
            joined += word
            if joined == key:
                return True
            if len(joined) >= len(key):
                break
    return False


# A site that names itself after its domain gives a brand name carrying the
# TLD - "Example.org", "Example.com" - and no profile handle includes it.
_BRAND_TLD_RE = re.compile(r"\.(?:com|org|net|io|co|ai|dev|app|uk|de|fr)$", re.I)


def brand_key(name):
    """A brand name reduced to comparable letters and digits."""
    # The mark comes off first: "Example.com®" ends in the sign, not the
    # domain suffix, so the suffix rule could not see it.
    return re.sub(r"[^a-z0-9]", "", _BRAND_TLD_RE.sub("", without_marks(name)).lower())


# Hosts where the account is the first path segment and everything after it
# belongs to somebody's project. Reading the last segment on one of these took
# a build system's repository path, ending `/package/libwidget`, as the
# widget library's own account - because the last segment contains the brand
# name and the brand is "widget". The account is the build system's, and the
# URL says so in its first segment. The last segment of a repository URL is a
# file in somebody's tree, not an identity.
_ACCOUNT_IS_FIRST_SEGMENT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org",
                     "sourceforge.net", "hub.docker.com", "gitea.com")


def _profile_handle(path):
    """The segment of this URL that names whose profile it is.

    Usually the last one: `/company/acme`, `/@acme`, `/wiki/Acme_Ltd`. On a
    repository host it is the first, because everything after the account name
    is a repository, a branch and a file path - none of which is an identity.
    """
    without_scheme = path.split("://")[-1]
    host = without_scheme.split("/")[0].lower()
    segments = [seg for seg in without_scheme.split("/")[1:] if seg]
    if not segments:
        return ""
    if any(host == repo or host.endswith("." + repo) for repo in _ACCOUNT_IS_FIRST_SEGMENT_HOSTS):
        return segments[0]
    return segments[-1]


# Labels that name a registry rather than a site. Everything to the right of
# the name in an address is somebody else's naming scheme - the top-level
# domain, and the second-level label a country registry hands out - so none of
# them is a form of the site's own name.
_REGISTRY_HOST_LABELS = frozenset({
    "www", "web", "site", "com", "net", "org", "gov", "edu", "int", "biz",
    "info", "co", "ne", "or", "ac", "lg", "go", "gob", "gouv", "govt", "nic",
})


def host_name_forms(host):
    """The romanised names a site publishes about itself in its own address.

    `profile_names_brand` compares a URL handle against a brand name, and a
    handle can only be written in ASCII. Where the brand's name is not - a
    Japanese city government's name is three Japanese characters - `brand_key`
    reduces it to the empty string, so that comparison can never return true
    however plainly the handle names the site. A name test that cannot pass is
    not evidence, and it must not be the thing that rejects.

    The site's own address is the romanised form it publishes about itself,
    and its labels are the words it chose. Every label, not the leftmost:
    `brand.domain_token` is the leftmost one, and on a government, university
    or agency host that is the department - `city.`, `www2.`, `physics.` -
    while the name is the label after it. A city's five official accounts, one
    on each of five crawled pages and none of them in site chrome, were all
    dropped as somebody else's citations, and the report's verdict then said
    "nothing off the site corroborates it" about a city government whose own
    snapshot recorded a disaster-prevention account, two ward pages, the city
    assembly's account and another ward's.
    """
    labels = [label for label in strip_www(host or "").lower().split(".") if label]
    if len(labels) > 1:
        labels = labels[:-1]
    # A label beginning `xn--` is the non-Latin name encoded, not a romanised
    # spelling of it. Keeping it would leave a comparable-looking string that
    # no handle can ever match, which is exactly the dead end this exists to
    # get out of.
    return [label for label in labels
            if len(label) >= 3 and not label.startswith("xn--")
            and label not in _REGISTRY_HOST_LABELS]


def profile_names_brand(url, brand_name):
    """Does this profile URL's handle name the brand?

    Without a brand name there is nothing to compare against, and the honest
    answer is no: an unattributed profile is not evidence about this brand.

    The name may sit anywhere inside the handle, bounded as the block comment
    above sets out. Anywhere is what the plain containment test already said,
    and the list of end decorations underneath it never got a chance to
    disagree; what containment did not say is how much of the handle the name
    has to be, so a three-letter name inside a stranger's handle counted as
    this brand's account.

    The two directions are not the same question and are not bounded the same
    way. A name inside a longer handle is the case that can go wrong: the rest
    of the handle belongs to somebody, and the arithmetic above decides whether
    that somebody is this brand. A handle inside a longer name cannot go wrong
    the same way - there is no other word in the handle to belong to anyone -
    so it asks only that the handle be long enough to be a name.
    """
    key = brand_key(brand_name)
    if not key:
        return False
    path = re.sub(r"[?#].*$", "", url or "").rstrip("/")
    written = _profile_handle(path)
    handle = re.sub(r"[^a-z0-9]", "", written.lower())
    if not handle:
        return False
    if handle == key:
        return True
    if handle in key:
        return len(handle) >= _HANDLE_NAME_MIN_CHARS
    if key not in handle:
        return False
    if _handle_spells(written, key):
        return True
    if len(key) < _HANDLE_NAME_MIN_CHARS:
        return False
    return len(key) >= _HANDLE_NAME_UNBOUNDED_CHARS or 2 * len(key) >= len(handle)


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


# --------------------------------------------------------------------------
# The brand, or the platform it is built on
#
# Off-site profile breadth is the strongest separator this marketplace
# measured, so a host counted wrongly here moves the one number the README
# stakes its case on. Four hosts on one consumer brand's site - the storefront
# platform it runs on, that platform's developer site, a protocol site and a
# demonstration store, every one of them reached only from a `<script src>` or
# named in the platform's own boilerplate - were recorded as the brand's own
# off-site profiles. The count read 8 where the truth was 3, which is the
# difference between "at or above the level typical of brands assistants name"
# and a finding.
#
# The class, not the names. A content delivery network, a storefront platform,
# an analytics vendor, a consent manager, a review widget: what they share is
# not a list of domains, it is that the site loads code and pictures from them
# rather than keeping an account on them. Two readings say so without naming
# anybody:
#
#   what the address is for   a role word where the host names a machine
#                             (`cdn.`, `assets.`, `static.`, `analytics.`), or
#                             a path that is a file rather than a page
#   what the address names     a profile is an account - a handle inside a
#                             platform. A bare origin with nothing after the
#                             slash is a company's front door, and a company's
#                             front door is not a profile on it.
#
# The second is the one that does the work, and it is why this needs no list at
# all: all four wrong entries were origins with no account in them.
# --------------------------------------------------------------------------

# Leading host labels that name a machine's job rather than an organisation.
# Role words, chosen by whoever operates the host and readable by anybody: no
# vendor is named and none needs to be.
_INFRASTRUCTURE_HOST_LABELS = frozenset({
    "cdn", "cdn1", "cdn2", "cdns", "static", "assets", "asset", "media",
    "img", "imgs", "image", "images", "photos", "files", "uploads", "downloads",
    "js", "css", "fonts", "font", "scripts", "script", "lib", "libs",
    "analytics", "analytic", "stats", "metrics", "telemetry", "tag", "tags",
    "tagmanager", "pixel", "pixels", "track", "tracker", "tracking", "beacon",
    "widget", "widgets", "embed", "embeds", "player", "players", "api", "apis",
    "consent", "cookie", "cookies", "privacy-portal", "edge", "origin", "cache",
})

# Path shapes that are a file being loaded rather than a page being read.
_ASSET_PATH_RE = re.compile(
    r"(?:^|/)(?:cdn|assets|static|dist|build|_next|_nuxt|wp-content|wp-includes)/"
    r"|\.(?:js|mjs|cjs|css|map|woff2?|ttf|otf|eot|svg|png|jpe?g|gif|webp|avif|ico)"
    r"(?:$|[?#])", re.I)


def is_infrastructure_url(url):
    """True when this address is part of how the site is built and served.

    A CDN, a storefront platform's asset host, an analytics collector, a
    consent manager, a review widget. The site loads it; the site does not have
    an account on it.
    """
    try:
        parts = urlparse(url or "")
    except ValueError:
        return False
    host = strip_www((parts.hostname or "").lower())
    if not host:
        return False
    if host.split(".")[0] in _INFRASTRUCTURE_HOST_LABELS:
        return True
    return bool(_ASSET_PATH_RE.search(parts.path or ""))


def looks_like_an_account(url):
    """Does this URL name somebody's account, rather than a company's front door?

    An off-site profile is a handle inside a platform: `/company/<handle>`,
    `/@<handle>`, `/organization/<handle>`. An address with nothing after the
    host is the home page of whoever operates that host - the storefront
    platform the site runs on, the payment processor in its footer, the
    delivery firm it links for tracking. None of those is a place this brand
    keeps a profile, and every one of them was being counted as one.

    Navigation-source parameters come off first, because "powered by" links
    carry them and a bare origin with a campaign tag on it is still a bare
    origin.
    """
    try:
        parts = urlparse(strip_tracking_parameters(url or ""))
    except ValueError:
        return False
    path = (parts.path or "").strip("/")
    if not path:
        # Unless the account is the host. A platform that gives each customer a
        # subdomain has no path to read, and the site declaring such an address
        # as its own is the whole of the evidence either way - so this returns
        # False and the caller decides, which is what `counts_as_off_site_profile`
        # does with its `declared` argument.
        return False
    return not _ASSET_PATH_RE.search(parts.path or "")


def counts_as_off_site_profile(url, declared=False, subresource_hosts=()):
    """Is this address somewhere the brand keeps an account?

    `declared` is the site itself saying so, in `sameAs`, in `rel="me"`, or in
    its own agent file. It is how a customer subdomain on a hosted platform,
    which has no path to read, still counts - but it does not make a machine
    host into a profile, because the four wrong entries this exists to stop
    arrived through exactly such a declaration: a site's own agent file listing
    the platform it runs on.

    `subresource_hosts` is the hosts this site loads code and pictures from,
    which the extractor already records per page as `scripts.third_party_hosts`.
    A host that appears only there is the platform the site is built on. That
    is the strongest reading available and the one no URL can supply on its
    own, so it is an argument rather than a guess, and it is checked before the
    declaration for the same reason.
    """
    if is_infrastructure_url(url):
        return False
    try:
        host = strip_www((urlparse(url or "").hostname or "").lower())
    except ValueError:
        return False
    if not host:
        return False
    if any(host == strip_www((h or "").lower())
           or host.endswith("." + strip_www((h or "").lower()))
           for h in subresource_hosts if h):
        return False
    return True if declared else looks_like_an_account(url)


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


def _title_from_article_url(url):
    """The article title an encyclopedia URL states, as ordinary words.

    `/wiki/Alice%27s_Adventures_in_Wonderland` is a title, not a handle, so it
    can be read back and compared as a name.
    """
    path = re.sub(r"[?#].*$", "", url or "").rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    return unquote(slug).replace("_", " ").strip()


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
        # The whole title, not a substring of it. An article path is a
        # statement of subject, so the test can be the strict one: does this
        # title name the brand, by the same comparison used everywhere else?
        #
        # The substring test that was here accepted any title containing the
        # brand key. A digital library called "Project Gutenberg" linked
        # fifteen Wikipedia articles, none of them its own, and two of them -
        # an article about the 15th-century printer Johannes Gutenberg, and a
        # separate German organisation's article - contained the key. Both
        # were counted as the brand's own profile, and the report then said
        # the site links to five off-site profiles, one of which stood for
        # fifteen unrelated citations.
        title = _title_from_article_url(url)
        if any(names_are_one_name(name, title) for name in candidates):
            out[platform] = url
    return out


# --------------------------------------------------------------------------
# Somewhere a brand keeps an account
#
# An account-shaped link is a profile only on a host where brands keep
# accounts. Four sites had hosts of other kinds counted, and every one of them
# spelled the brand's name, which is how it got in:
#
#   a shipping service's tracking page      `<shop>.<tracking vendor>/`, linked
#                                           "Track Order", was a clothing
#                                           shop's one off-site profile, and a
#                                           site with no account at all was
#                                           told it had one, at medium
#   the store's own platform subdomain and  two of a craft label's six, which
#   a fashion magazine's article about it   passed it on the strongest measure
#                                           here with four real accounts - and
#                                           the article went into its `sameAs`
#   a certification vendor's course page    one of a documentation site's own
#   a social page's messaging address       counted beside that same page as a
#                                           second account
#
# A name says whose page something is. It says nothing about whether the host
# is somewhere a brand keeps an account: a checkout, a tracking page, a press
# article, a vendor's catalogue entry and a partner's page all carry the
# brand's name for reasons of their own.
#
# So the host decides, from the kinds of place that hold accounts: social
# networks and messaging channels, video and podcast hosts, code and package
# hosts, professional networks and company records, review and
# business-listing sites, the marketplaces a brand keeps a storefront on, app
# stores, and encyclopaedias and knowledge bases. A list of them cannot be
# finished, and the cost of that is bounded: the site's own `sameAs` or
# `rel="me"` still counts on any host at all, because a site saying "this
# account is me" needs no list - which is how a self-hosted Mastodon account
# is found.
# --------------------------------------------------------------------------

# Hosts, or a host and the path its accounts live under. A host matches itself
# and every host beneath it, so `open.<music service>` and `<name>.substack.com`
# are both found. Every address the page extractor names in `SOCIAL_PLATFORMS`
# is recognised here too, and a test holds the two together.
_PROFILE_PLATFORM_HOSTS = (
    # Social networks and messaging channels.
    "facebook.com", "fb.com", "instagram.com", "x.com", "twitter.com", "threads.net",
    "bsky.app", "tiktok.com", "pinterest.com", "reddit.com", "vk.com", "weibo.com",
    "xiaohongshu.com", "douyin.com", "line.me", "t.me", "telegram.me", "whatsapp.com",
    "wa.me", "kakao.com", "naver.com", "discord.gg", "discord.com", "mastodon.social",
    "linktr.ee",
    # Video, music and podcasts.
    "youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "bilibili.com", "soundcloud.com",
    "spotify.com", "podcasts.apple.com", "music.apple.com", "podcasts.google.com",
    "podbean.com", "anchor.fm",
    # Writing, creators and funding.
    "medium.com", "substack.com", "patreon.com", "opencollective.com", "dev.to",
    # Code and packages.
    "github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "sourceforge.net",
    "gitea.com", "hub.docker.com", "readthedocs.io", "stackoverflow.com", "pkg.go.dev",
    "pub.dev", "hex.pm", "scholar.google.com",
    # Professional networks and company records.
    "linkedin.com", "crunchbase.com", "glassdoor.com",
    # Review and business-listing sites, and the map entries a business claims.
    "yelp.com", "trustpilot.com", "g.page", "maps.google.com", "google.com/maps",
    "goo.gl/maps", "maps.app.goo.gl", "business.site", "bcorporation.net",
    # Marketplaces a brand keeps a storefront on.
    "shopee.com", "lazada.com", "rakuten.com", "rakuten.co.jp", "zozo.jp",
    "shopping.yahoo.co.jp", "tiki.vn", "sendo.vn",
    # App stores.
    "apps.apple.com", "itunes.apple.com", "play.google.com", "chromewebstore.google.com",
    # Encyclopaedias and knowledge bases.
    "wikipedia.org", "wikidata.org", "namu.wiki",
)

# The same kinds of place, held by the label each platform chooses rather than
# by a whole address - the way the publishing platforms are held - so one entry
# covers every country a platform keeps an address in: `<platform>.co.uk`,
# `<platform>.com.vn`, `<platform>.co.id`. Matched against the label a registry
# sold, never against any label of the host, so `<platform>.<somebody>.test`
# is somebody else's host.
_PROFILE_PLATFORM_LABELS = frozenset({
    # Social networks, video and creators.
    "snapchat", "tumblr", "zhihu", "douban", "dailymotion", "deezer", "pocketcasts",
    "castbox", "behance", "dribbble", "flickr", "producthunt", "ko-fi", "buymeacoffee",
    # Code, packages and research records.
    "huggingface", "npmjs", "pypi", "crates", "rubygems", "packagist", "nuget", "metacpan",
    "anaconda", "readthedocs", "stackexchange", "orcid", "researchgate", "zenodo",
    "figshare", "osf", "doi",
    # Professional networks and company records.
    "xing", "wellfound", "indeed",
    # Review and business-listing sites.
    "tripadvisor", "trustpilot", "foursquare", "bbb", "capterra", "sitejabber", "houzz",
    "zomato", "opentable", "tabelog", "dianping", "justdial", "yell",
    # Marketplaces.
    "amazon", "ebay", "etsy", "rakuten", "shopee", "lazada", "zalora", "tokopedia",
    "bukalapak", "blibli", "flipkart", "myntra", "nykaa", "ajio", "tmall", "taobao",
    "coupang", "mercadolibre", "mercadolivre", "allegro",
    # Encyclopaedias and knowledge bases.
    "britannica", "wikimedia",
})


def _registered_label(host):
    """The label a registry sold: `shop.<name>.co.uk` -> `<name>`."""
    labels = [label for label in strip_www(host or "").lower().split(".") if label]
    if len(labels) < 2:
        return ""
    labels = labels[:-1]
    while len(labels) > 1 and labels[-1] in _REGISTRY_HOST_LABELS:
        labels = labels[:-1]
    return labels[-1]


def on_a_profile_platform(url):
    """Is this address on a host where brands keep accounts?

    A bare host is accepted as well as a URL, because the article-address test
    below holds a host and nothing else.
    """
    text = str(url or "")
    try:
        parts = urlparse(text if "://" in text else "https://" + text)
    except ValueError:
        return False
    host = strip_www((parts.hostname or "").lower())
    if not host:
        return False
    path = (parts.path or "").lstrip("/").lower()
    for entry in _PROFILE_PLATFORM_HOSTS:
        entry_host, _, prefix = entry.partition("/")
        if (host == entry_host or host.endswith("." + entry_host)) and path.startswith(prefix):
            return True
    return _registered_label(host) in _PROFILE_PLATFORM_LABELS


def claimed_by_the_site(declared_urls):
    """The addresses the site's own `sameAs` or `rel="me"` claims, in every spelling."""
    claimed = {url for url in declared_urls or () if url}
    return claimed | {one_account("", url)[1] for url in claimed}


def could_be_an_account(url, claimed=frozenset()):
    """May this address be listed as one of the brand's own accounts at all?

    Only on a host where brands keep accounts, or where the site's own `sameAs`
    or `rel="me"` claims the address, on any host. The one rule for both places
    a report names the brand's accounts - the corroboration count and the
    paste-ready `sameAs` block - so neither lists an address the other left out.
    `claimed` is what `claimed_by_the_site` returns.
    """
    account = one_account("", url)[1]
    return url in claimed or account in claimed or on_a_profile_platform(account)


# A social page's messaging address. `m.me/<handle>` opens a conversation with
# the page at `facebook.com/<handle>`: one account, reached two ways, and a
# cosmetics shop linking both on every page was counted as having two.
_MESSAGING_ADDRESS_HOSTS = frozenset({"m.me"})


def one_account(platform, url):
    """`(platform, url)`, with a messaging address folded into the page it opens."""
    try:
        parts = urlparse(str(url or ""))
    except ValueError:
        return platform, url
    if strip_www((parts.hostname or "").lower()) in _MESSAGING_ADDRESS_HOSTS:
        handle = next((s for s in (parts.path or "").split("/") if s), "")
        if handle:
            return "Facebook", "https://www.facebook.com/" + handle
    return platform, url


# Below this many crawled pages there is no such thing as "linked from only
# one page", so the citation test stays out of the way of a small crawl.
CITATION_MIN_PAGES = 5


def site_own_profiles(pages, declared, names, host=""):
    """({platform: url}, [dropped as citations], [not on a profile platform]).

    The accounts the site links as its own.

    `social_profiles` holds one URL per platform per page, and flattening the
    pages with `dict.update` let whichever page came last win. On
    a statistics charity that handed an article's citations to the brand:
    `youtube.com/@altrufisica` and
    `en.wikipedia.org/wiki/History_of_ethanol_fuel_in_Brazil` were recorded as
    the brand's own profiles, and the report then certified "the site links to
    8 distinct off-site profiles" and "all 4 verifiable profile link(s)
    resolve" about two accounts belonging to other people.

    An account is linked from the site's chrome and so appears on page after
    page; a citation appears in the one article that cites it. Where a URL
    appears once and neither names the brand, sits in the site's own `sameAs`,
    nor is an opaque identifier that cannot be read either way, it is a
    citation. That test runs for every platform, not for Wikipedia alone.

    And before any of that, the host has to be somewhere a brand keeps an
    account, unless the site's own `sameAs` or `rel="me"` claims the address.
    See `on_a_profile_platform`.

    `host` is read for the same reason the declared names are: the labels of
    the site's own address are romanised forms of its name, and on a site whose
    name is not written in the Latin alphabet they are the only forms a handle
    could ever carry. See `host_name_forms`.
    """
    declared = declared or {}
    # The name test is only ever an escape from the appearance test below, so
    # widening the names it may use can keep a profile and can never drop one.
    names = [n for n in list(names) + host_name_forms(host) if n]
    claimed = claimed_by_the_site(declared.values())
    counts = {}
    elsewhere = set()
    # Where the link sits decides this better than how often it appears. A
    # brand's own account is in the header or the footer; a citation is in the
    # body of the one article that cites it. Counting appearances alone dropped
    # a site's real X account, linked once from its footer and once from its
    # `sameAs`, on a run where the `sameAs` had been broken - and a footer link
    # is exactly the evidence that settles it.
    in_chrome = set()
    for page in pages:
        links = page.get("links") or {}
        for bucket in ("nav", "footer"):
            for link in links.get(bucket) or []:
                url = (link or {}).get("url") if isinstance(link, dict) else None
                if url:
                    in_chrome.add(url)
                    in_chrome.add(one_account("", url)[1])
        for platform, url in (page.get("social_profiles") or {}).items():
            if not url:
                continue
            platform, account = one_account(platform, url)
            if not could_be_an_account(url, claimed):
                elsewhere.add(url)
                continue
            counts[(platform, account)] = counts.get((platform, account), 0) + 1

    by_platform = {}
    for (platform, url), count in counts.items():
        by_platform.setdefault(platform, []).append((count, url))

    chosen, dropped = {}, []
    total = len(pages)
    for platform, entries in sorted(by_platform.items()):
        claimed_here = declared.get(platform)
        # The site's own `sameAs` first, then the URL the most pages link, then
        # the shortest, then alphabetically, so two runs of one snapshot cannot
        # pick different accounts for one platform.
        order = sorted(entries,
                       key=lambda e: (0 if e[1] == claimed_here else 1, -e[0], len(e[1]), e[1]))
        for count, url in order:
            if looks_like_a_citation(url, count, total, claimed_here, names, in_chrome):
                dropped.append(url)
                continue
            chosen[platform] = url
            break
    return chosen, sorted(dropped), sorted(elsewhere)


# An article's address, not an account's. The last segment of the path is a
# headline: several hyphenated words, very often ending in the publisher's
# numeric story id. A furniture retailer's report counted
# "<magazine>.test/<brand>-nesting-coffee-table-review-37439576" among its own
# off-site profiles - a review, linked "Read more" from three regional press
# pages - and it survived the citation test twice over: the address contains
# the brand's name, and it appears on three pages. A review names the brand
# because it is about the brand, and a press page per region links it once
# each. Neither makes it the brand's account.
#
# Only on a host that is not a profile platform. On one of those, an account
# is an account by where it lives, and a handle is allowed to be long.
_ARTICLE_ID_RE = re.compile(r"-\d{5,}$")
_ARTICLE_SLUG_MIN_WORDS_WITH_ID = 3
_ARTICLE_SLUG_MIN_WORDS = 6
_PAGE_SUFFIX_RE = re.compile(r"\.(?:html?|php|aspx?)$", re.I)


def is_coverage_not_an_account(url):
    """Is this address an article about the brand rather than the brand's account?"""
    parts = urlparse(url or "")
    host = strip_www(parts.netloc.lower())
    if not host or on_a_profile_platform(host):
        return False
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return False
    slug = _PAGE_SUFFIX_RE.sub("", segments[-1].lower())
    words = [w for w in slug.split("-") if w and not w.isdigit()]
    if _ARTICLE_ID_RE.search(slug) and len(words) >= _ARTICLE_SLUG_MIN_WORDS_WITH_ID:
        return True
    return len(words) >= _ARTICLE_SLUG_MIN_WORDS


def looks_like_a_citation(url, pages_linking, page_count, claimed, names, in_chrome=()):
    """Is this a link to somebody else's account rather than the brand's own?"""
    if claimed and url == claimed:
        return False
    # Before the chrome, the name and the count, because none of the three can
    # turn a headline into an account. See `is_coverage_not_an_account`.
    if is_coverage_not_an_account(url):
        return True
    if url in in_chrome:
        return False
    if profile_is_opaque(url):
        return False
    if any(profile_names_brand(url, name) for name in names):
        return False
    # A test that cannot pass must not be the thing that rejects.
    #
    # The handle test above compares ASCII against ASCII: `brand_key` keeps
    # letters and digits and drops everything else, so a name written in
    # Japanese, Korean, Arabic, Greek or Cyrillic reduces to nothing and no
    # handle on earth can match it. Where every name the crawl found does
    # that, the only reading left is the appearance test - and the appearance
    # test alone drops every account a site links once from the body of one
    # page, which is what a government publishing a different official account
    # on each of five pages looks like. A high-severity "no off-site profile is
    # linked from the crawled pages" and a verdict reading "nothing off the
    # site corroborates it" followed, about a city government with five.
    if not any(brand_key(name) for name in names):
        return False
    return page_count >= CITATION_MIN_PAGES and pages_linking < 2


def brand_profiles(pages, brand=None):
    """The off-site accounts that are this brand's own, read off the crawled pages.

    One reading for every place a report names the brand's accounts. The
    corroboration count read one list and the paste-ready `sameAs` block read
    another, so a magazine article about a craft label was kept out of neither
    and printed in both - once as a profile the label keeps, once as an address
    the label should declare as itself.

    `pages` are the pages to read, and `brand` is the snapshot's `brand`
    record. Returns a dict:

      profiles                   {platform: url} - what to count and to declare
      citations                  somebody else's accounts, linked from one body
      unattributed               platforms whose URL states a subject that is
                                 not this brand
      not_on_a_profile_platform  account-shaped links on hosts where nobody keeps
                                 an account
      names                      the names the handles were compared against
      declared                   {platform: url} the site claims in `sameAs`
    """
    brand = brand or {}
    brand_name = brand.get("name") or ""
    declared = {}
    for page in pages or ():
        declared.update(page.get("declared_profiles") or {})
    names = [brand_name, brand.get("domain_token")] \
        + list(brand.get("alternate_names") or []) \
        + list(brand.get("authoritative_variants") or []) \
        + list(brand.get("fallback_candidates") or [])
    names = [n for n in names if n]
    candidates, citations, elsewhere = site_own_profiles(
        list(pages or ()), declared, names, host=brand.get("host") or "")
    profiles = profiles_naming_brand(candidates, brand_name, declared, name_forms=names[1:])
    return {"profiles": profiles, "citations": citations,
            "unattributed": sorted(k for k in candidates if k not in profiles),
            "not_on_a_profile_platform": elsewhere, "names": names, "declared": declared}


def cut_at_read_cap(page):
    """Did the crawl stop reading this page at MAX_RESPONSE_BYTES?

    Such a page was read up to a point and not past it, so anything below that
    point is not missing from it - it was never read. `truncated_at_read_cap`
    is what the crawl writes now; `truncated` is the same fact on a page record
    written before that field existed. A snapshot older than both has neither,
    and its pages are taken as read in full, as they always were.
    """
    return bool(page.get("truncated_at_read_cap") or page.get("truncated") is True)


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
    # A CAPTCHA widget on a page with almost no text is a verification screen,
    # whoever is running it. The vendor table can never be complete - these
    # three were missing, and 33 of the 41 pages one crawl "read" were a
    # CrowdSec screen carrying a Cloudflare Turnstile widget. Every count in
    # that report used them as its denominator, the duplicate-title finding
    # named the challenge screen as the site's most repeated page, and the
    # appendix stated that no page answered with a verification page.
    "CrowdSec": ("crowdsec", "cs-captcha"),
}

# The widget, not the product running it. A CAPTCHA embedded in a page with
# almost no text is a verification screen whoever put it there, so this is the
# fallback when nothing in the table above matched - and it is a fallback
# rather than a table entry because the fix is to allow-list crawlers in the
# bot manager, and the widget's name is not the bot manager's. One screen
# carried a Cloudflare Turnstile widget and the words "Security provided by
# CrowdSec"; naming Turnstile there would send the owner to the wrong console.
_CAPTCHA_WIDGET_MARKERS = {
    "a CAPTCHA widget (Cloudflare Turnstile)": (
        "cf-turnstile", "challenges.cloudflare.com/turnstile"),
    "a CAPTCHA widget (hCaptcha)": ("hcaptcha.com/1/api.js", "h-captcha-response"),
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
    # What the screen in front of a Turnstile or an hCaptcha actually says.
    # "security check" alone is deliberately not here: an ordinary page may
    # mention one. Under the text ceiling, with these words, nothing else is
    # being shown.
    "complete this security check", "security check to continue",
    "checking your connection", "verify that you are human",
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


# A script standing in front of the page, recognised by its shape.
#
# A museum answered every request with HTTP 200 and 101,079 bytes of
# obfuscated script - one inline `<script>`, no title, no visible word, no
# link - under a `Server` header naming a product in no table here. Nothing
# above could know it, so the report said the homepage "answered 200 with an
# empty body" and that "no page answered with a bot-manager verification
# page", and missed the one fact that mattered: nothing that does not run
# JavaScript ever receives the museum's pages.
#
# The shape is what a browser check looks like whoever sells it: nearly every
# byte is script, nothing is shown and nothing is linked. A JavaScript
# application shell is not this - a shell carries a title and a mount point
# (`<div id="app">`), and it is `render-readability-audit`'s finding. A
# redirect stub is not this either: it is a few hundred bytes, under the floor.
SCRIPT_WALL_MIN_BYTES = 5000
SCRIPT_WALL_SCRIPT_SHARE = 0.9
SCRIPT_WALL_TEXT_MAX = 20

_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.I | re.S)
_ANCHOR_WITH_HREF_RE = re.compile(r"<a\b[^>]*\bhref\s*=", re.I)
_TITLE_WITH_TEXT_RE = re.compile(r"<title\b[^>]*>\s*[^<\s][^<]*</title", re.I)
_APP_MOUNT_RE = re.compile(
    r"""\bid\s*=\s*["']?(?:app|root|__next|__nuxt|svelte|q-app)["'\s>]""", re.I)


def is_script_wall(html, page_text, status=None):
    """Is this 2xx body a script and nothing else - a check the browser must run?"""
    try:
        if status is not None and not 200 <= int(status) < 300:
            return False
    except (TypeError, ValueError):
        return False
    html = html or ""
    if len(html) < SCRIPT_WALL_MIN_BYTES or len((page_text or "").strip()) > SCRIPT_WALL_TEXT_MAX:
        return False
    if (_ANCHOR_WITH_HREF_RE.search(html) or _TITLE_WITH_TEXT_RE.search(html)
            or _APP_MOUNT_RE.search(html)):
        return False
    script = sum(len(match.group(1)) for match in _SCRIPT_BLOCK_RE.finditer(html))
    return script >= SCRIPT_WALL_SCRIPT_SHARE * len(html)


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
    for widget, markers in sorted(_CAPTCHA_WIDGET_MARKERS.items()):
        if any(marker in low for marker in markers):
            return widget
    # Last, because it names nobody: see `is_script_wall`.
    if is_script_wall(html, page_text, status):
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
# Across eight sites in one validation pass, five had a fact recorded as found on the
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
# A project speaks about itself in the third person. "The site was built and
# is maintained by Alexis Deveria" is a browser-compatibility database naming
# its own maintainer, and the report said "no founding year and no named team
# or leadership detail appears in the page text" - about a sentence the crawl
# had captured and this function then rejected as somebody else's.
# Nouns an organisation uses for itself. "The studio was founded in 2015 by
# Nell Achterberg" is a pottery naming its own founder; "The site was built and
# is maintained by Alexis Deveria" is a database naming its own maintainer.
# Both were rejected as somebody else's fact, and the report then said "no
# founding year and no named team detail appears in the page text" about a
# sentence the crawl had captured and printed elsewhere in the same run.
#
# A list, and a coherent one: these are the words a thing calls itself, not a
# list of sites we have been burned by.
_SELF_NOUNS = (
    "company", "firm", "practice", "team", "site", "website", "project",
    "studio", "agency", "shop", "store", "business", "charity", "foundation",
    "organisation", "organization", "group", "school", "college", "clinic",
    "restaurant", "cafe", "brewery", "gallery", "museum", "library",
    "publisher", "magazine", "newsroom", "platform", "service", "app",
    "product", "tool", "brand", "association", "society", "institute",
    # The nouns a workshop, a maker or a small practice uses for itself. A
    # blacksmith's about page reads "The forge was founded in 1994 by <person>"
    # and this list rejected it, so both the founding year and the named person
    # on that page were invisible to every skill that quotes a sentence back -
    # and the site was told it states neither.
    "forge", "workshop", "mill", "bakery", "kitchen", "surgery", "yard",
    "garage", "press", "label", "farm", "nursery", "centre", "center",
    "trust", "cooperative", "co-operative", "partnership", "collective",
    "studio", "atelier", "practice", "office", "bureau", "guild",
)

_SELF_REFERENCE_RE = re.compile(
    r"\b(?:we|we're|we've|our|ours|us"
    r"|(?:the|this) (?:" + "|".join(_SELF_NOUNS) + "))" + r"\b", re.I)


# "The association", "The foundation", "The institute" - the same words that
# make a sentence self-referential also open another organisation's proper
# name, and then the whole name is capitalised and runs on.
#
# A charity's partners page carries "The Association of Youths Against Malaria
# was established in June of 1997 in the town of Bakau". `_SELF_REFERENCE_RE`
# matched "The Association", the sentence was attributed to the audited
# charity, its founding fact was recorded as present, and the finding that the
# site never states its own founding year was suppressed - so a wrong
# attribution did not merely add a false line, it hid a true one. The charity
# was founded in 2004.
#
# What separates them: "The museum was founded in 1917" continues in lower
# case, and a proper name continues in capitals. Two more capitalised words
# after the noun, and this is somebody's name rather than a thing calling
# itself a thing - unless the name is the brand's own, which the caller's
# brand comparison settles.
_CAPITALISED_RUN = r"[A-Z][\w'-]+(?:\s+(?:of|for|and|the|de|du|van)?\s*[A-Z][\w'-]+)*"

_SELF_NOUN_GROUP = "(?i:" + "|".join(_SELF_NOUNS) + ")"

_NAMED_ORGANISATION_RE = re.compile(
    # "The Association of Youths Against Malaria" - the noun, then the name.
    "(?i:the|this)" + r"\s+" + _SELF_NOUN_GROUP + r"\s+"
    + r"(?:of\s+|for\s+)?" + _CAPITALISED_RUN + r"\s+" + _CAPITALISED_RUN
    # "The Sunrise Foundation" - the name, then the noun.
    + "|(?i:the|this)" + r"\s+" + _CAPITALISED_RUN + r"\s+"
    + _SELF_NOUN_GROUP + r"(?![a-z])"
)


def _opens_another_organisation(sentence, brand_name=""):
    """Does "the <noun>" here open somebody else's proper name?

    False when the name that follows is the brand's own, because then the
    sentence really is about the site.
    """
    match = _NAMED_ORGANISATION_RE.search(sentence or "")
    if not match:
        return False
    if brand_name and name_appears_in(brand_name, match.group(0)):
        return False
    return True


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
    if _SELF_REFERENCE_RE.search(sentence) and not _opens_another_organisation(
            sentence, brand_name):
        return True
    # Both sides through the same reducer. `brand_key` strips spaces and
    # punctuation, so a two-word brand produced "harrowgatepress" and was then
    # looked for inside a sentence that says "Harrowgate Press" - a substring
    # test that could only ever succeed for a single-word name. Every
    # multi-word brand's own sentence about itself was read as somebody
    # else's, and the founding and team facts a site does state were reported
    # as absent.
    key = brand_key(brand_name) if brand_name else ""
    return bool(key) and key in brand_key(sentence)


# --------------------------------------------------------------------------
# Does this text state, in one sentence, what the brand is?
#
# One rule, two answers a reader sees. `fact-extractability-audit` raises
# `no-entity-definition` when nothing on a site answers it, and
# `structured-data-audit` puts what it finds into the `description` of the
# paste-ready Organization block a developer is told to copy.
#
# The rule used to be written twice, once in each of those skills, because
# neither may import the other: they communicate only through the snapshot and
# `compose_report`, `structured-data-audit` runs first, and `package.py` ships
# each skill directory as something that runs on its own. Two copies gave one
# report two answers - an open-source project's Organization block declared
# that the organisation *is* a point-release announcement lifted off its own
# homepage, while three findings above, the other skill printed the identity
# sentence from the about page. This file is vendored into every skill for
# exactly that reason, so the rule sits here beside `names_match`,
# `name_forms`, `sentences` and `speaks_for_the_brand`.
#
# What a caller may add on top is a filter, never a shape: `defining_sentence`
# takes an `accept` callback, which `structured-data-audit` uses to require
# that a description outlive its own page's news cycle. A rejected match keeps
# the search going, so one unusable sentence near the top of a page does not
# hide the usable one below it.
#
# This is a closed English vocabulary - four English sentence shapes, English
# function words, English empty nouns - so it answers "no sentence" for prose
# in any other language rather than measuring it. `fact-extractability-audit`
# gates its caller on `prose_checks_apply` for that reason. The Organization
# snippet does not gate, and does not need to: a shape that fails to match
# leaves the snippet's `description` to the site's own declared value.
# --------------------------------------------------------------------------

# How much has to follow "is a" before the sentence has said anything. The
# floor was 20 characters, which is a proxy for content and a bad one: it threw
# out "Fossgate is a botanic garden." (16 characters after the copula) while
# passing "Poppy is a car-sharing service." (21). What it was reaching for is
# "Acme is a company." - a predicate made only of nouns that name no category -
# and `says_something` tests for that directly, so the length floor only has to
# be short enough to let a real short definition through.
DEFINITION_MIN_PREDICATE = 12

# An alias in brackets straight after the name is part of the same subject.
# A statistics charity's about page opens "<Brand> (<initials>) is a free,
# non-profit website", and a pattern requiring the verb to follow the name
# directly read that sentence as no definition at all - so the site was told
# at high severity that no page says what it is, one clause after its own
# about page says it. The braces are doubled because these templates go
# through `.format(brand=..., minlen=...)` before they are compiled.
_DEFINITION_ALIAS = r"(?:\s*[(\[][^)\]\n]{{1,40}}[)\]])?"

# A registered or trade mark straight after the name. "<Name>® is a lifestyle
# brand" is the same sentence as "<Name> is a lifestyle brand", and the sign
# sits between the `\b` that closes the name and the verb, where nothing let it
# stand. See `NAME_MARKS`.
_NAME_MARK = u"(?:\\s?[" + NAME_MARKS + u"])?"

# What may not follow the copula, because it opens a slogan rather than a
# category. "<Brand> is about bringing the world a little closer together" was
# accepted as the one sentence saying what a handicrafts retailer is, and the
# fix told the site to paste it under its H1. "is about", "is all about", "is
# more than", "is not just", "is here to", "is proud to" - each says what the
# brand values or does for someone, and none says what kind of thing it is.
# A lookahead at the first letter after the verb, so `\s+` cannot give back a
# space and let the phrase through behind it; "is a", "is the", "is one of"
# and a bare noun phrase ("is independent software for ...") are untouched.
# A past participle, for the passive-voice branch below: the regular "-ed"
# form and the irregular ones a site uses of itself.
_PASSIVE_PARTICIPLE = (r"(?:[a-z]+ed|built|made|known|run|written|held|sold|owned|grown"
                       r"|given|taken|kept|sent|taught|brought|found|led|shown|driven|chosen)")

_DEFINITION_NOT_A_CATEGORY = (
    r"(?!\s)(?!(?:(?:all|really|just|truly)\s+)?about\b"
    r"|more\s+than\b|so\s+much\s+more\b"
    # A negative says what the brand is not, which is never what it is. "<Brand>
    # is not affiliated with any marketplace" on a shop's FAQ page was quoted as
    # the one sentence saying what a design studio is. `not just`, `not only`
    # and the like were already here for the same reason.
    r"|not\b|no\s+longer\b|neither\b"
    r"|here\s+(?:to|for)\b|there\s+for\b|for\s+(?:everyone|anyone|you|people)\b"
    r"|where\b|what\b|how\b|why\b|when\b"
    r"|(?:proud|committed|dedicated|passionate|determined|driven|devoted)\b"
    r"|on\s+a\s+mission\b"
    # News rather than identity: "<Brand> is opening its first store in ...",
    # "<Brand> is thrilled to announce ...". Each reports what the brand is
    # doing this month, and none says what kind of thing it is.
    r"|(?:now\s+)?(?:opening|launching|expanding|celebrating|returning|coming\s+to"
    r"|thrilled|excited|delighted|pleased)\b"
    # The passive voice: a past participle and the preposition that says by
    # whom, under what or where. It reports something done to the brand, not
    # what kind of thing it is. A programming language's about page reads
    # "<Language> is developed under an OSI-approved open source license,
    # making it freely usable and distributable, even for commercial use." -
    # a sentence about the licence - and it was printed as the site's
    # one-sentence definition and offered as the line to paste under the
    # homepage H1. "is used in", "is based in", "is run by" and "is licensed
    # under" are the same sentence about other things, and so is "is used
    # successfully in", with the adverb after the participle.
    #
    # "for" is not among the prepositions: "Brightpath is built for retail
    # operations teams at ..." says who the product is for, which is half a
    # definition, and the mutation suite accepts it on purpose. Only the words
    # straight after the copula are read, so "<Brand> is a library used by
    # ..." - an article first - is untouched.
    #
    # A run of participles is the same voice. A project-management product's
    # footer reads "<Product> is designed, built, and backed by <company>, the
    # people behind <two other products>", which names the maker, and it was
    # printed as what the product is.
    r"|(?:[a-z]+ly\s+)?" + _PASSIVE_PARTICIPLE +
    r"(?:\s*,\s*(?:and\s+)?" + _PASSIVE_PARTICIPLE + r"|\s+and\s+" + _PASSIVE_PARTICIPLE + r")*"
    r"(?:\s+[a-z]+ly)?\s+(?:under|by|in|at|on|from)\b)")

_DEFINITION_COPULAR = (
    r"\b{brand}\b" + _NAME_MARK + _DEFINITION_ALIAS +
    r"\s+(?:is|are|was|remains)\s+" + _DEFINITION_NOT_A_CATEGORY +
    r"(?:an?|the)?\s*(?P<rest>[^.!?]{{{minlen},400}})")

# The shape a documentation or reference site almost always uses: the brand as
# the subject of a verb that says what it does. A browser-compatibility
# database opens "<Brand> provides up-to-date browser support tables for
# front-end web technologies" - a definition by any reading - and was told no
# page states in one sentence what it is, while a paste-ready snippet three
# findings below used that very sentence as the brand's description.
DEFINING_VERBS = ("provides", "offers", "makes", "builds", "publishes",
                  "sells", "designs", "runs", "helps", "lets", "gives",
                  "delivers", "supplies", "creates", "tracks", "maintains",
                  # A maker's verbs, which a furniture or a clothing brand
                  # uses where a software company writes "builds".
                  "crafts", "produces", "manufactures")

# What may not follow a defining verb, because the sentence reports an event
# rather than saying what the brand is. A design studio's press page reads
# "<Brand> makes its debut in the country's capital with the opening of the
# <Brand> Design House", which has the verb and a long predicate and says
# nothing about what the studio is. It was quoted as the one sentence on the
# site saying what the brand is - over the homepage's own meta description,
# which says it. The event nouns are read only where the verb takes them as
# its object ("makes its debut", "makes a splash", "makes history"), so "provides
# a way to publish" and "makes launch videos" are untouched. Braces are doubled
# for the `.format` the templates go through.
_DEFINITION_NOT_AN_EVENT = (
    r"(?!its\s+(?:[\w-]+\s+){{0,2}}?(?:debut|return|comeback|way|mark|entrance|"
    r"arrival|bow|premiere|appearance|move)\b)"
    r"(?!(?:an?|the)\s+(?:[\w-]+\s+){{0,2}}?(?:debut|return|comeback|splash|"
    r"appearance|entrance|arrival|bow|premiere|milestone)\b)"
    r"(?!(?:history|headlines|waves|news)\b)")

# What may not be the object of a defining verb either: a policy or a service
# procedure. An Indian clothing shop's FAQ answers "<Brand> provides refunds in
# 2 different forms: Online Payment: For Orders placed through Online
# Payment, refunds will be done through ..." and that answer was quoted as the
# sentence in which the shop says what it is. It says how a refund is paid.
#
# The noun is refused only as the head of the object - followed by a
# preposition, a conjunction, the end of the clause, or a word such as
# "policy" or "options" - so "<Brand> provides delivery services to small
# shops" and "<Brand> builds shipping containers", which are a courier and a
# maker saying what they are, still pass. The words allowed in front of it are
# a closed list of the modifiers a policy line uses ("free", "easy",
# "30-day"), so "<Brand> sells shoes with free returns" is still read on its
# first three words. Cookies, the reader's data and access to the site are
# read only in the shapes a consent banner uses, because a bakery offers
# cookies and a clinic provides access to care.
_DEFINITION_POLICY_MODIFIERS = (
    r"(?:(?:an?|the|free|full|partial|easy|fast|quick|express|standard|secure|safe|"
    r"simple|hassle-free|same-day|next-day|international|worldwide|domestic|"
    r"nationwide|instant|prompt|complete|extended|limited|lifetime|different|"
    r"various|several|[0-9]+[\w%-]*|\w+-day|\w+-free)\s+){{0,3}}")
_DEFINITION_POLICY_NOUNS = (
    r"refunds?|returns?|exchanges?|replacements?|cancellations?|shipping|"
    r"delivery|deliveries|cash\s+on\s+delivery|cod|warrant(?:y|ies)|"
    r"guarantees?|order\s+tracking")
_DEFINITION_NOT_A_POLICY = (
    r"(?!" + _DEFINITION_POLICY_MODIFIERS + r"(?:" + _DEFINITION_POLICY_NOUNS + r")\b"
    r"(?:\s*(?:[,.;:!?(]|$)|\s+(?:in|on|for|within|to|of|at|through|via|if|when|"
    r"only|and|or|with|from|under|upon|after|before|as|by|is|are|will|can|may|"
    r"options?|polic(?:y|ies)|facilit(?:y|ies)|terms|process|requests?)\b))"
    r"(?!(?:\w+\s+){{0,2}}?cookies\s+(?:to|that|which|so|on|in\s+order)\b)"
    r"(?!(?:access\s+to\s+)?(?:your|the\s+user'?s?)\s+(?:personal\s+)?"
    r"(?:data|information|details)\b)"
    r"(?!(?:\w+\s+){{0,2}}?access\s+to\s+(?:your|our|this|these|the\s+(?:site|website|"
    r"store|shop|account|app|services?|platform))\b)")

_DEFINITION_VERB = (
    r"\b{brand}\b" + _NAME_MARK + _DEFINITION_ALIAS +
    r"\s+(?:" + "|".join(DEFINING_VERBS) + r")\s+" + _DEFINITION_NOT_AN_EVENT +
    _DEFINITION_NOT_A_POLICY +
    r"(?P<rest>[^.!?]{{{minlen},400}})")

# The other shape a definition comes in. A tagline set off from the name by a
# dash, a colon or a pipe is the same sentence with the verb left out, and it
# is how a great many sites write theirs:
#
#     <Brand> - The web framework for perfectionists with deadlines.
#
# Read only for the copular form, that page was reported as never stating in
# one sentence what the brand is, at medium severity, with a fix telling its
# owners to write the sentence they had already written. The separator has to
# be a real one - an en or em dash, a colon, a pipe, or a hyphen with spaces
# around it - so a hyphenated retailer's name is not read as a definition of
# its first word.
#
# The comma appositive is the third way English writes this and was not read
# at all: "<Brand>, a car-sharing service, operates 1,500 vehicles across the
# city." That page was reported as never stating in one sentence what it is,
# on the strength of its opening sentence stating exactly that. The article
# after the comma is required, so "<Brand>, Inc." and an ordinary list comma
# are not read as definitions.
_DEFINITION_APPOSITIVE = (
    r"\b{brand}\b" + _NAME_MARK + r"\s*[–—:|]\s*(?P<rest>[^.!?–—|]{{{minlen},300}})"
    r"|\b{brand}\b" + _NAME_MARK + r"\s+-\s+(?P<dashrest>[^.!?|]{{{minlen},300}})"
    r"|\b{brand}\b" + _NAME_MARK +
    r"\s*,\s+(?:an?|the)\s+(?P<commarest>[^.!?]{{{minlen},300}})")

# The shape a site uses when it does not name itself: the first person. A
# university department's staff page reads "I am a Professor in the Department
# of Statistics at Example University."; a small shop writes "We are a bakery
# on the market square." Both state an identity as plainly as any of the shapes
# above, and neither can ever match a pattern that requires the detected brand
# string to be the grammatical subject - so one such site was told at medium
# severity that no page states in one sentence what it is, on the strength of a
# page whose opening two lines both do.
#
# The article after the copula is what keeps this from matching any sentence at
# all: "We are open on Sundays" and "We are committed to quality" have no
# "a"/"an"/"the" after the verb and are not read as definitions. What follows
# the article still has to name a category, which `says_something` decides, so
# "We are a leading global company" is not a definition either.
_DEFINITION_SELF = (
    r"\b(?:I|we)\s+(?:am|are)\s+(?:an?|the)\s+(?P<rest>[^.!?]{{{minlen},400}})")

# An opening quotation mark immediately in front of the first-person verb. A
# quoted customer writes "We are a family of four and this is the third one we
# have bought" on plenty of sites, and a definition lifted out of a testimonial
# is a statement about the customer. The brand-subject shapes do not need this
# guard: a sentence naming the brand is about the brand whoever is speaking.
_DEFINITION_OPENING_QUOTE_RE = re.compile("[\"'‘“«„]\\s*$")

# Where the sentence holding a match begins. Anything after the last
# terminator, and a line break counts: a heading and the paragraph beneath it
# are two statements even with no full stop between them. The terminators are
# the full stop plus the ones used with Chinese, Japanese, Korean, Indic, Urdu
# and Arabic text.
_DEFINITION_HEAD_RE = re.compile(
    "[^.!?。！？।॥۔؟\n\r]*$")

# The words that may stand in front of the name without pushing it out of the
# subject. A brand conventionally written with a definite article - "The
# <Brand> is a retreat for curious programmers." - is still the subject of that
# sentence, and rejecting the article made the whole check unsatisfiable for
# every such brand: a programming school whose H1 and whose meta description on
# 38 of 60 pages both open with that sentence was told at high severity that no
# page states what it is.
#
# `each` and `every` for the same reason, from a restaurant group whose
# homepage H2 reads "Each <Brand> is a love letter to <city>". One determiner
# and no more: the mention this test exists to reject sits behind two words or
# more of somebody else's clause.
_DEFINITION_DETERMINERS = frozenset({"the", "a", "an", "each", "every"})

# The one clause that may stand in front of the name: a past participle of
# coming into being or being placed, its complement, and the comma that closes
# it. "Founded in Singapore in 2013, <Brand> creates quality furniture for real
# life" was refused because two words - and a year - stood before the name, and
# the report put "no page states in one sentence what the brand is" second in
# its Start-here list, over that sentence. The phrase describes the subject it
# precedes; it is not somebody else's clause. The predicate guards - slogan,
# news, event - still run on what follows the verb.
_LEADING_PARTICIPLE_RE = re.compile(
    r"^\W*(?:founded|established|started|launched|born|based|headquartered|"
    r"incorporated|created|built|designed|made)\b[^,;:.!?]{1,120},\s*$", re.I)

# English function words, so a predicate can be judged on the nouns it carries.
#
# "one" sits beside "some", "any", "both" and "each" for the same reason they
# do, and its absence was doing damage: "<Brand> is one of the leading global
# brands trusted by innovative teams" carries no noun that names a category,
# and `says_something` counted "one" as the content word that saves it. The
# check then reported that the site states in one sentence what it is, and
# quoted that sentence back to its owner as the one that works. Nothing else
# reads this set for words of three letters - `_content_words` starts at four -
# so the whole of the change lands where the hole was.
STOPWORDS = frozenset("""
a an the and or but for nor so yet of to in on at by with from as is are was were be been being
your our their its it this that these those we you they he she what how why when where which who
whom whose can could will would shall should may might must do does did done have has had not no
more most other some such only own same than too very just about into over under again further
then once here there all any both each few one own s t don now
""".split())

# Nouns that name no category. This is what the character floor on the
# predicate was reaching for: "Acme is a company." is a definition by shape and
# tells a reader nothing, and so does "Acme is a leading global company."
CONTENTLESS_NOUNS = frozenset("""
company companies business businesses brand brands organisation organization
organisations organizations firm firms agency agencies website websites site
sites page pages platform platforms provider providers solution solutions
service services product products group groups team teams leader leaders
leading global international innovative trusted premier world class
""".split())

# A definition is a sentence, and the next three patterns are what separates
# one from a row of a table that happens to have the same shape. A
# browser-support database's issue page produced "<Brand>: Constructable
# Stylesheet 10 74" - one row of a compatibility matrix, read through the
# colon-appositive shape - and that string was printed as the site's own
# definition of itself, which suppressed the "no quotable definition" finding
# on a site that has none.
#
# 1. Where the sentence ends. Every predicate group is `[^.!?]`, so the match
#    stops before the terminator and the terminator is the next character. End
#    of text counts too: the opening window is cut at a fixed number of words
#    and a definition sitting on the cut has not stopped being one.
_DEFINITION_TAIL_RE = re.compile(
    "^[\"'’”»)\\]]*\\s*"
    "(?:[.!?。！？।॥۔؟]|$)")

# 2. Two numbers side by side with nothing between them. Prose puts a word
#    between its numbers; two columns of a support matrix do not. Both tokens
#    have to be bare digits, so "founded April 1, 2019" - a comma between them -
#    is not read as a table row.
_DEFINITION_NUMBER_RUN_RE = re.compile(r"\b\d+\s+\d+\b")

# 3. The words that hold a sentence together. The copular and the verb shapes
#    carry their verb inside the match; the appositive shape leaves the verb out
#    - "<Brand> - the web framework for perfectionists with deadlines" - but
#    still has the article and the prepositions. A table row has none of them.
_DEFINITION_GLUE = frozenset("""
is are was were be been being remains has have had provides offers makes builds
publishes sells designs runs helps lets gives delivers supplies creates tracks
maintains crafts produces manufactures
a an the of for with to in on at by from and or that which who whose
where when it its our your their
""".split())

# The one written shape that carries no function word at all: the title line.
# "Ada Lovelace - Professor, Statistics, Example University." is an appositive
# definition with nothing in it for the glue vocabulary to find, and rejecting
# it for that told a departmental site that no page says what it is, over the
# first line of the page. What separates a title line from a row of cells is
# the comma: a person writing a list of roles punctuates it, and a table row
# arrives as bare tokens. Digits anywhere are still a cell - the figures are
# what a compatibility matrix or a price table is made of - so the two readings
# cannot collide.
_DEFINITION_TITLE_LIST_RE = re.compile(r"[^\W\d_]{2,},\s+[^\W\d_]{2,}")


# The smallest stem a trailing "s" may be made optional on. Below this the
# rule stops being about one name written two ways: on a two-letter name it
# would leave a single letter, and a single letter matches a word that is not
# the brand at all.
BRAND_SINGULAR_MIN_STEM = 3


def brand_pattern(brand_name):
    """Match the brand name allowing for extra internal whitespace, and for the
    trailing "s" a site writes its own name both with and without.

    The markup and the prose disagree about that one letter far more often than
    they disagree about anything else. A footwear retailer declares "<Name>s" in
    `og:site_name` and in the title of every page, and writes "<Name> is one of
    the country's largest branded footwear retailers by number of exclusive
    stores" as the opening of a paragraph on its about page. Every shape in the
    definition rule matched that sentence; none of them ever got the chance,
    because the subject the patterns were built from was the declared spelling
    and the sentence uses the other one. The finding said no page states in one
    sentence what the brand is, and the verdict paragraph at the top of the
    report repeated it.

    Which of the two spellings the markup happens to carry is not a fact about
    the site's identity, so the pattern accepts either. Only the last token,
    because that is the one a name is pluralised on; only where a stem of
    `BRAND_SINGULAR_MIN_STEM` letters is left; and never on a token already
    ending "ss", where dropping the last letter would leave a different word
    rather than the same one spelled two ways.

    Nothing else uses these patterns: `brand_pattern` is read by
    `defining_sentence`, by the declared-description reading beside it and by
    the snippet's `description`, and by nothing that compares two names -
    `names_match` and `name_forms` are untouched, so no other check inherits
    this.
    """
    # Built from the name without its ® or ™: the templates accept the sign
    # after the name wherever the text prints one (`_NAME_MARK`), and a
    # pattern ending in the sign could never be followed by the `\b` they
    # put after it.
    tokens = [re.escape(t) for t in without_marks(brand_name).split() if t]
    if not tokens:
        return None
    last = tokens[-1]
    stem = last[:-1] if last[-1:].lower() == "s" else last
    if len(stem) >= BRAND_SINGULAR_MIN_STEM and stem[-1:].lower() != "s":
        tokens[-1] = stem + "s?"
    return r"\s+".join(tokens)


def brand_forms(brand_name, brand=None):
    """Every way the site could reasonably refer to itself in a sentence.

    A site is not obliged to write its full legal or formal name as the subject
    of its own definition. "Department of Computer Science, University of
    Example" is a correct declared name and will never appear verbatim in the
    sentence "The Department is one of the largest in the country". Requiring
    the whole string made the check unsatisfiable for any organisation with a
    long formal name - which is most institutions, and 81% of a holdout sample.

    Returns the declared name, every variant the site asserts, every name it
    declares as an alternate, the word its own domain is built on, and whatever
    short forms `name_forms` derives from each - longest first, so the most
    specific match wins.

    Nothing `name_forms` returns is filtered out here. The filter that used to
    sit at the end dropped short forms, and this function's own promise of "the
    short forms derivable from them" is the reason a restaurant group was told
    no sentence of the form "<full formal name> is a ..." appears on its site,
    while its homepage H2 reads "Each <head word of the name> is a love letter
    to <city>". Which forms are derivable is `name_forms`' question, and it is
    answered in one place for every skill that asks.
    """
    brand = brand or {}
    declared = [brand_name]
    declared += [v for v in (brand.get("authoritative_variants") or []) if v]
    declared += [v for v in (brand.get("alternate_names") or []) if v]
    # The word the site's own domain is built on. It is how a site refers to
    # itself as often as its formal name is, and it was the one source of a
    # short form that this function never asked for. An all-digit token is an
    # IP address rather than a name, which is what `crawl.py` decides before
    # offering the same token as a fallback brand name.
    domain_token = str(brand.get("domain_token") or "").strip()
    if re.match(r"^\d+$", domain_token):
        domain_token = ""
    if domain_token:
        declared.append(domain_token)
    # The words the site prints that its own domain label spells, letter for
    # letter. `name_forms` derives a short form only where a *prefix* of the
    # declared name matches the token, and on a design firm whose declared name
    # is the search-engine copy "San Francisco Premier Interior Designer" no
    # prefix does - so "Arrowood Design", which the domain spells and the
    # homepage opens with, was never offered to any matcher, and the report
    # said no page states in one sentence what the brand is about a page
    # opening "Arrowood Design is a full-service design firm".
    #
    # Only this one reading of the fallback candidates, and not the candidates
    # themselves: they include the long title, and letting a headline into the
    # subject list would let a definition check accept a sentence about
    # something the site merely talks about. The letters have to match exactly,
    # so this cannot invent a name.
    spelled = name_the_site_spells(
        domain_token, [brand_name] + list(brand.get("fallback_candidates") or []))
    if spelled:
        declared.append(spelled)

    forms = set()
    for value in declared:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(value) < 2:
            continue
        # The declared string itself as well as its derivatives: `name_forms`
        # drops anything two characters or shorter, and a two-letter name is a
        # name a site writes about itself.
        #
        # The token goes in as well as coming out. `name_forms` derives the
        # head word of a multi-word name only where the domain spells it, so
        # "Indian Restaurants" is never invented as a name the brand goes by,
        # and without the token there is nothing to justify the head form the
        # restaurant group actually writes.
        forms.add(value)
        forms.update(name_forms(value, domain_token=domain_token))

    return sorted(forms, key=lambda f: (-len(f), f))


def says_something(predicate):
    """Does the predicate of a definition name a category, or only fill space?"""
    words = [w.lower() for w in letter_runs(predicate or "", 2)]
    content = [w for w in words if w not in STOPWORDS]
    if not content:
        return False
    return any(w not in CONTENTLESS_NOUNS for w in content)


# A line spoken to the reader rather than about the brand. "Your home should
# feel like an extension of you." sits under a furniture shop's H1 and was
# printed at the top of its report as the line in which the shop "describes
# itself as" - campaign copy whose only subject is the visitor, with no
# category in it at all. A definition's subject is the thing being defined; a
# line that opens with the reader's word is about the reader.
_READER_OPENING_RE = re.compile(
    r"^\W*(?:you|your|yours|you're|you’re|you'll|you’ll|you've|you’ve)\b", re.I)


def addresses_the_reader(text):
    """Does this line open by addressing the reader, the way campaign copy does?

    For a caller that reads a line with no subject of its own - the line under
    a heading, a meta description - as what the brand is.
    """
    return bool(_READER_OPENING_RE.match(text or ""))


def _starts_a_sentence(text, index):
    """Is the brand mention at `index` the subject of its own sentence?

    "Billing for <Brand> is monthly per organization." matched mid-clause, and
    the check quoted "<Brand> is monthly per organization" as the site's
    definition of itself - a sentence the site does not contain and a statement
    that is not true. Because the check then passed, the site never got the
    missing-definition finding it had earned, while its homepage JSON-LD
    carried a clean definition nobody was told about.

    So only punctuation, quotation marks, bullets and a single determiner may
    sit between the start of the sentence and the name. Two words in front of
    it, or any digit, and the name is inside somebody else's clause again.
    """
    head = _DEFINITION_HEAD_RE.search(text[:index] or "").group(0)
    # "Founded in <city> in 2013, <Brand> creates ..." - a participle phrase
    # set off by its comma, and the name after it still the subject. The
    # phrase carries the digits a founding year is written in, which is why
    # it is read before the digit test below and not after it.
    if _LEADING_PARTICIPLE_RE.match(head):
        return True
    if any(ch.isdigit() for ch in head):
        return False
    words = [word.lower() for word in letter_runs(head, 1)]
    if not words:
        return True
    return len(words) == 1 and words[0] in _DEFINITION_DETERMINERS


def _reads_as_a_sentence(text, match):
    """Is this match a sentence, or a table row that matched a sentence shape?"""
    whole = match.group(0)
    # A slice, not the rest of the page: `$` then means "the text ended here",
    # which is the case this test has to let through.
    tail = _DEFINITION_TAIL_RE.match(text[match.end():match.end() + 8])
    if not tail:
        return False
    # A question asks; it does not say what anything is. A shop's FAQ entry "I
    # am an international customer, can I order from abroad?" has the
    # first-person shape exactly, and it was quoted as the one sentence saying
    # what a design studio is.
    terminator = tail.group(0).rstrip()[-1:]
    if terminator and terminator in u"?？؟":
        return False
    if _DEFINITION_NUMBER_RUN_RE.search(whole):
        return False
    if {w.lower() for w in letter_runs(whole, 1)} & _DEFINITION_GLUE:
        return True
    return bool(_DEFINITION_TITLE_LIST_RE.search(whole)) and not any(
        ch.isdigit() for ch in whole)


def _usable_definition(text, match):
    """The three tests every shape has to pass, or "".

    The subject has to open its own sentence, the predicate has to name a
    category rather than fill space, and the whole has to read as a sentence
    rather than as a row of a table that shares its shape.
    """
    if not _starts_a_sentence(text, match.start()):
        return ""
    predicate = next((v for v in match.groupdict().values() if v), "")
    if not says_something(predicate):
        return ""
    if not _reads_as_a_sentence(text, match):
        return ""
    return truncate(match.group(0), 300)


# The pages on which a sentence in the first person is the site speaking. On
# every other page it is somebody the site quotes: an Indonesian shop's blog
# interviews an artist, whose answer "I am a woman who cares deeply about
# starting conversations with women about bodies ..." was quoted as the
# sentence in which the shop says what it is. A caller that knows the page is
# the one document of a single-document site says so with `single_document`.
FIRST_PERSON_PAGE_TYPES = frozenset({"home", "about"})

# A second separator inside a separator-shape description: two blocks of a
# page run together, not one tagline. See `_separator_shape_runs_on`.
_SECOND_SEPARATOR_RE = re.compile(r"\S\s*[:|–—]\s|\s-\s")


def _folded(value):
    return " ".join(str(value or "").split()).lower()


def _first_person_counts_on(page):
    """May a first-person sentence on this page be the site speaking?"""
    return (page.get("page_type") in FIRST_PERSON_PAGE_TYPES
            or bool(page.get("single_document")))


def _runs_on_from_the_title(match, page, brand_name):
    """Does this match begin with the page's own `<title>` and carry on past it?

    A Vietnamese fashion shop's delivered text opens with its title, "<Brand>
    | Fashion From the Ground Up", and then its first heading, "Our Latest:
    <collection>", with nothing between them. The separator shape read the
    two as one tagline, and "<Brand> | Fashion From the Ground Up Our Latest:
    <collection>" was quoted as what the brand is. A title the site wrote as
    one line is still read - only a match that runs past its end is refused.
    """
    title = _folded(page.get("title"))
    if not title or len(title) <= len(_folded(brand_name)) + 2:
        return False
    whole = _folded(match.group(0))
    return whole.startswith(title) and len(whole) > len(title) + 1


def _page_headings(page):
    headings = page.get("headings") or {}
    return [h for level in ("h1", "h2", "h3") for h in (headings.get(level) or [])
            if str(h or "").strip()]


def _separator_shape_runs_on(match, page):
    """Does a dash, colon or pipe description run into another block?

    Two ways it shows: a second separator inside the description ("... Up Our
    Latest: <collection>"), and - where the caller says which page the text is
    from - one of the page's own headings starting part-way through it.
    """
    groups = match.groupdict()
    description = groups.get("rest") or groups.get("dashrest") or ""
    if not description:
        return False
    if _SECOND_SEPARATOR_RE.search(description):
        return True
    if page is None:
        return False
    whole, folded = _folded(match.group(0)), _folded(description)
    for heading in _page_headings(page):
        folded_heading = _folded(heading)
        if len(folded_heading) < 4 or whole in folded_heading:
            continue
        if folded.find(folded_heading) > 0:
            return True
    return False


def defining_sentence(text, brand_name, brand=None, accept=None, page=None):
    """A quotable one-line definition in `text`, or "".

    The question is whether any sentence here says what this is, not whether
    one particular English template appears. Four shapes answer it: the brand
    as the subject of a copula or of a defining verb, the brand set against a
    description by a dash, a colon or a comma, and the site describing itself
    in the first person. A site that writes any one of them has written the
    sentence this rule exists to look for.

    The brand shapes are tried first, and against every form the site could use
    for itself, longest first, so a match reports the most specific subject the
    sentence actually used. The first-person shape comes last, because a
    sentence naming the brand names its subject and that one only implies it.

    `accept` is a caller's extra test on a sentence that has already passed the
    rule, and it is called with the sentence alone. Neither caller needs more
    than that: `structured-data-audit` asks whether the sentence outlives its
    own page's news cycle, which reads only the words and a brand name it
    already holds, and `fact-extractability-audit` asks nothing. Where a match
    was found matters to no caller either - the entity-definition finding says
    "at the top of" or "further down" from which slice of the page it passed
    in, not from where inside that slice the match landed - so the position and
    the match object stay private. A rejected match keeps the search going
    instead of ending it, so one unusable sentence near the top of a page does
    not hide the usable one below it.

    `page` says which page the text is from, for a caller that knows: the
    snapshot page record, of which `page_type`, `title` and `headings` are
    read, plus `single_document` (True) where the caller has established with
    `single_document_site` that the page is the one document of such a site.
    With it, a first-person sentence counts only on the homepage, an about or
    team page, or a single-document site (`FIRST_PERSON_PAGE_TYPES`); a match
    that begins with the page's `<title>` and runs past it is refused; and so
    is a separator-shape match whose description runs into one of the page's
    headings. Without it every shape is read as before, less the one test that
    needs no page: a separator-shape description holding a second separator.

    English only, by construction; see the block comment above.
    """
    page = page if isinstance(page, dict) else None

    def usable(match):
        definition = _usable_definition(text, match)
        if not definition:
            return ""
        if page is not None and _runs_on_from_the_title(match, page, brand_name):
            return ""
        if _separator_shape_runs_on(match, page):
            return ""
        if accept is None or accept(definition):
            return definition
        return ""

    for template in (_DEFINITION_COPULAR, _DEFINITION_APPOSITIVE, _DEFINITION_VERB):
        for form in brand_forms(brand_name, brand):
            pattern = brand_pattern(form)
            if not pattern:
                continue
            regex = re.compile(
                template.format(brand=pattern, minlen=DEFINITION_MIN_PREDICATE), re.I)
            # Every match, not the first: the first mention of the name on a
            # page is often mid-clause, and the definition is two sentences
            # later.
            for match in regex.finditer(text):
                definition = usable(match)
                if definition:
                    return definition

    if page is not None and not _first_person_counts_on(page):
        return ""
    regex = re.compile(
        _DEFINITION_SELF.format(minlen=DEFINITION_MIN_PREDICATE), re.I)
    for match in regex.finditer(text):
        head = _DEFINITION_HEAD_RE.search(text[:match.start()] or "").group(0)
        if _DEFINITION_OPENING_QUOTE_RE.search(head):
            continue
        # A frequently asked question written in the customer's voice. A shop's
        # FAQ reads "I am an international customer. What are the payment
        # methods available to me?", and the first sentence was quoted as the
        # one saying what a design studio is. The asker introduces themselves
        # and then asks; the question straight after is what says whose voice
        # the first sentence is in.
        if _NEXT_SENTENCE_IS_A_QUESTION_RE.match(text[match.end():match.end() + 240]):
            continue
        definition = usable(match)
        if definition:
            return definition
    return ""


# The rest of a sentence, its full stop, and a whole next sentence that ends in
# a question mark. See the first-person loop in `defining_sentence`.
_NEXT_SENTENCE_IS_A_QUESTION_RE = re.compile(r"^[^.!?]*[.!]\s+[^.!?]{1,200}[?？]")


# A capitalised run standing where `defining_sentence` puts the brand: one to
# five words, the particles a proper name carries between them, and nothing
# else. Not a name detector - it cannot tell a company from a person from a
# product, and it is not asked to.
_CAPITALISED_WORD = u"[A-ZÀ-ÖØ-Þ][^\\W\\d_]*(?:['’&.-][^\\W\\d_]+)*"
_A_NAMED_SUBJECT = (
    _CAPITALISED_WORD +
    u"(?:\\s+(?:of|and|for|the|de|del|van|von|di|da)|\\s+" + _CAPITALISED_WORD + u"){0,4}")

# The subject has to be a name, and a capital at the start of a sentence is not
# evidence of one: every sentence opens with a capital. "We are open on
# Sundays" and "Delivery is a flat rate" both matched the copular template with
# the brand taken out of it, and either one would have silenced this finding on
# most sites in the marketplace - which would have traded those false
# positives for a miss on every site.
#
# Two words, two of them capitalised, is the floor. It is what "Vinayak
# Enterprise", "Arrowood Design" and "The Museum of Modern Craft" have and what
# "We", "Delivery" and "Shipping" do not. The cost is a one-word alternate name
# - and a one-word name is exactly the case `brand_forms` already derives, so
# the strict matcher has it.
_LOOSE_SUBJECT_MIN_WORDS = 2
_LOOSE_SUBJECT_MIN_CAPITALS = 2

# The copular template with the article made compulsory.
#
# `_DEFINITION_COPULAR` leaves `(?:an?|the)?` optional because the brand
# standing as the subject is itself the evidence that the sentence is a
# definition. Take the brand out and that evidence goes with it: "Delivery is
# free above fifty" has the shape and defines nothing. The article is what
# `_DEFINITION_SELF` already requires for the same reason, one template down.
_LOOSE_DEFINITION_COPULAR = (
    r"\b{brand}\b" + _DEFINITION_ALIAS +
    r"\s+(?:is|are|was|remains)\s+(?:an?|the)\s+(?P<rest>[^.!?]{{{minlen},400}})")

# The comma appositive alone, out of `_DEFINITION_APPOSITIVE`'s three shapes.
#
# The other two set the name against a description with a dash, a colon or a
# pipe, and with the brand identified that punctuation is evidence: a site
# writes "<Brand> - the web framework for perfectionists with deadlines" and
# means it as a definition. Take the identification out and the same shape is
# every bulleted feature line on the internet - "Free Delivery - on all orders
# over fifty pounds" read as a definition of a delivery policy. The comma form
# keeps its article, which is what makes it survive the loosening.
_LOOSE_DEFINITION_APPOSITIVE = (
    r"\b{brand}\b\s*,\s+(?:an?|the)\s+(?P<commarest>[^.!?]{{{minlen},300}})")


def _reads_as_a_name(subject):
    """Is this capitalised run a name, or the first word of a sentence?"""
    words = subject.split()
    if len(words) < _LOOSE_SUBJECT_MIN_WORDS:
        return False
    return sum(1 for w in words if w[:1].isupper()) >= _LOOSE_SUBJECT_MIN_CAPITALS


def some_subject_is_defined(text):
    """`(subject, sentence)` where this text defines *some* named subject.

    The same three templates and the same three tests as `defining_sentence`
    above, with one thing taken out: which subject. That single removal is the
    whole function, and it is the whole point.

    `defining_sentence` asks "does a sentence define **this** brand", and it
    can only ask that with the name this audit resolved. A jute-bag maker's
    `<title>` carries its legal name, `resolve_brand` picked the search-engine
    copy in the same title instead, and the report published "No sentence of
    the form '<the copy> is a ...', '<the domain word> is a ...' appears" over
    an about page reading "<Legal name> is a leading manufacturer and supplier
    of eco-friendly packaging products". The sentence was on the page. The
    subject was never tried.

    So this is the reading the denial has to fail against before it may be
    published, and it is deliberately loose. Its errors cost silence: a
    sentence defining a partner, a product or a person stops the audit saying
    "no page states what this is", which is a claim the audit was not entitled
    to make on that evidence anyway. `defining_sentence` stays exactly as
    strict as it is, because a false positive there is a sentence quoted back
    to a site as its own identity - the two matchers have two error budgets and
    that is what lets each of them be right.

    Latin capitals and English templates, like everything else in this block.
    Widening those is a separate job from this one: what this fixes is a
    denial made on a subject list of one, on a site written in the language the
    templates were built for.
    """
    if not text:
        return None
    for template in (_LOOSE_DEFINITION_COPULAR, _LOOSE_DEFINITION_APPOSITIVE,
                     _DEFINITION_VERB):
        regex = re.compile(
            template.format(brand=_A_NAMED_SUBJECT, minlen=DEFINITION_MIN_PREDICATE))
        for match in regex.finditer(text):
            definition = _usable_definition(text, match)
            if not definition:
                continue
            # The words the subject pattern actually consumed, which is the
            # name to print. Read back off the definition rather than captured,
            # because two of these templates are shared with
            # `defining_sentence` and a fourth named group in them would reach
            # `_usable_definition`, whose predicate test takes the first
            # non-empty group it finds.
            subject = re.match(_A_NAMED_SUBJECT, definition)
            if subject is None or not _reads_as_a_name(subject.group(0)):
                continue
            return subject.group(0).strip(), definition
    return None


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

# The second way a total is only a subtotal is inside one file. The crawl stops
# reading a sitemap's entries at `SITEMAP_URLS_READ_PER_FILE`, and at its byte
# cap, and neither is the file ending. A library's one child sitemap holds
# 44,909 URLs and 4,014 `<lastmod>` values; the crawl read the first 5,000, the
# index was "complete" because its one child had been fetched, and the reports
# said "The sitemap lists 5,000 URLs" and "47 of 5,000 entries (0.9%)" carry a
# date - a count of this audit's reading, printed as the site's own.
# --------------------------------------------------------------------------

# The entry count at which `crawl.py` stops reading one sitemap file. A file
# read to exactly this count was stopped by the audit, not by its own end.
SITEMAP_URLS_READ_PER_FILE = 5000


def _sitemap_cut_short(record):
    """Did the crawl stop reading this sitemap file before the file ended?"""
    if not record.get("url_count"):
        return False
    return bool(record.get("url_cap_reached") or record.get("truncated")
                or record.get("url_count", 0) >= SITEMAP_URLS_READ_PER_FILE)


def sitemap_scope(sitemaps):
    """What the sitemap counts cover, and what they leave out."""
    declared, read, counted, lastmods, cut_short = set(), set(), 0, 0, 0
    for record in sitemaps or ():
        declared.update(record.get("child_sitemaps") or [])
        if record.get("status") == 200:
            read.add(record.get("url"))
            counted += record.get("url_count", 0)
            lastmods += record.get("lastmod_count", 0)
            if _sitemap_cut_short(record):
                cut_short += 1
    unread = declared - read
    return {
        "urls_counted": counted,
        "lastmods_counted": lastmods,
        "children_declared": len(declared),
        "children_read": len(declared & read),
        # Files this audit stopped reading part-way. Their counts, and any
        # share computed over them, are of the entries read.
        "files_cut_short": cut_short,
        "complete": not unread and not cut_short,
    }


def sitemap_total_phrase(scope, noun="entries"):
    """`32,820 entries`, or `at least 7,033 entries` with the reason attached.

    Every caller puts a count or a percentage beside this phrase, so where the
    reading stopped at a cap the phrase says what that count is of.
    """
    if scope["complete"]:
        return "{:,} {}".format(scope["urls_counted"], noun)
    unread = scope["children_declared"] - scope["children_read"]
    if scope.get("files_cut_short") and not unread:
        return ("at least {:,} {} (this audit read the first {:,}, and any share given "
                "here is of those)").format(scope["urls_counted"], noun, scope["urls_counted"])
    if scope.get("files_cut_short"):
        return ("at least {:,} {} - the sitemap index names {} sub-sitemaps, this audit read "
                "{} of them and stopped part-way through {}, so the real total is higher and "
                "any share given here is of the entries read").format(
                    scope["urls_counted"], noun, scope["children_declared"],
                    scope["children_read"], plural(scope["files_cut_short"], "file"))
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


# When the classifier could not work out what most of a crawl was, a check
# that finds no page of some type has learned something about the classifier,
# not about the site.
#
# `_URL_TYPE_PATTERNS` matches path segments against English words - `press`,
# `news`, `blog`, `pricing`, `contact`. Government and enterprise systems
# across east Asia route by numeric menu code and query string, so a Korean
# museum's crawl came back with 59 of 60 pages typed `other`, and four checks
# declined with "no article or blog post pages were crawled", "no category or
# listing pages were crawled" and the like - about a crawl holding 26 press
# releases and two listing pages. A Spanish manufacturer's frequently-asked-
# questions page was invisible to a slug list that reads only English, and the
# report told the company to create a page it already has.
UNCLASSIFIED_SHARE_WORTH_SAYING = 0.5


def unclassified_note(pages):
    """A sentence to append to "no pages of type X", or "" when it would mislead.

    Takes the page list, or the by-type mapping a check may hold instead.
    """
    if isinstance(pages, dict):
        pages = [page for bucket in pages.values() for page in bucket]
    considered = [p for p in (pages or [])
                  if p.get("status") == 200 and not p.get("skipped")]
    if not considered:
        return ""
    unknown = [p for p in considered if (p.get("page_type") or "other") == "other"]
    if len(unknown) < UNCLASSIFIED_SHARE_WORTH_SAYING * len(considered):
        return ""
    return (" This audit could not work out what {} of the {} crawled pages are - it reads a "
            "page's type from the words in its URL, and this site's URLs do not carry them - "
            "so that is a limit of this audit here, and not a statement that the site has no "
            "page of this kind.".format(len(unknown), len(considered)))


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


def pages_the_crawl_read(snapshot):
    """How many crawled URLs were pages: answered 200 and were read as HTML.

    `crawl.pages_crawled` and `crawl.pages_ok` count every URL that answered,
    and a record the crawler skipped - a PDF, an image, a redirect off the
    origin - answered too. A museum's crawl fetched 105 URLs of which 45 were
    documents and images, and the report said "105 pages crawled" and sized
    findings "on 5 of the 105 pages" when 60 pages had been read. The README
    says a non-HTML response never enters a count; this is the count a report
    may call pages.
    """
    return sum(1 for page in snapshot.get("pages") or []
               if page.get("status") == 200 and not page.get("skipped"))


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
        # A page that redirected to a sign-in screen is the sign-in screen.
        # `crawlable` filters the URL that was queued; nothing filtered where
        # it landed, so a school's `/calendar` - a 302 to `/login` - was audited
        # and then named in a finding as a page with no H1, telling the owner
        # to fix a heading on a page they do not control. The audit's own
        # promise is that it touches nothing under `/login` or `/account`, and
        # a redirect is how it got there.
        if is_forbidden_path(page.get("final_url") or page.get("url") or ""):
            continue
        if content_only and not looks_like_content(page):
            continue
        if types and page.get("page_type") not in types:
            continue
        out.append(page)
    return sorted(out, key=lambda p: p["url"])


# --------------------------------------------------------------------------
# HTTP - read-only, identified, rate-limited
# --------------------------------------------------------------------------

class FetchError(Exception):
    pass


# The six sub-skills, in the order the report deduplicates them: an earlier
# skill owns an observation two skills both make. Four copies of this list were
# in the tree - here as the dedup precedence, in `run_audit.py` as the run
# order, and twice in the tests - and a reordering in one of them would have
# silently changed which skill owns a shared root cause.
SUB_SKILLS = (
    "crawl-access-audit",
    "render-readability-audit",
    "structured-data-audit",
    "fact-extractability-audit",
    "freshness-corroboration-audit",
    "engagement-audit",
)


def deadline_from_budget(time_budget):
    """Turn a remaining-seconds allowance into a monotonic deadline.

    The orchestrator knows how much of the five-minute ceiling the crawl
    already spent; a sub-skill does not, so it is told what is left rather
    than assuming a fixed slice. `None` means no ceiling, which is what a
    direct command-line run of one skill gets.
    """
    if time_budget is None:
        return None
    return time.monotonic() + max(0.0, float(time_budget))


# Below this many seconds of the run's wall clock left, the orchestrator stops
# handing a sub-skill a network budget and appends `--no-network` instead.
#
# `run_audit.py` imports this as `MIN_SUB_SKILL_SECONDS`, the name the decision
# reads better under where it is made. A sub-skill is handed only the
# consequence - the same flag, whether the caller asked for it or the clock ran
# out - so it has to know the threshold to tell the two apart, and the
# inference only holds while both ends read the same number. One literal, here.
NO_TIME_LEFT_SECONDS = 30.0


def no_network_reason(snapshot, time_budget, flag_reason, exhausted_reason):
    """Why no extra requests were made: the flag, or a spent clock.

    Three checks in one report said their work was skipped because "extra
    network requests were disabled for this run (--no-network)". No such flag
    was passed. The site's robots.txt asked for ten seconds between requests,
    the crawl honoured that, the run's wall-clock budget was gone by the time
    the sub-skills started, and the orchestrator ran them with `--no-network`
    so they would not overrun. The reader was told they had asked for
    something they had not asked for, and the real cause - the site asked for
    a slow crawl and this audit obeyed - appeared nowhere in the report.

    The flag alone cannot separate the two. What is left of the clock can: a
    caller who passes `--no-network` still leaves the run its whole budget, and
    a run that has spent its budget is handed a `--time-budget` below the
    threshold at which the orchestrator switches the network off for it.

    `time_budget is None` means nobody imposed a ceiling, which is what a
    direct command-line run of one skill gets: there the flag is the only
    explanation left.
    """
    if time_budget is not None and time_budget < NO_TIME_LEFT_SECONDS:
        return "{}: {:.0f}s of this audit's wall-clock budget was left when this check " \
               "started, which is below the point at which the run stops making extra " \
               "requests rather than overrun its own time limit. {}This is a spent clock " \
               "rather than a `--no-network` flag, which may not have been passed at " \
               "all".format(exhausted_reason, time_budget, _where_the_clock_went(snapshot))
    return "{} (--no-network)".format(flag_reason)


def _where_the_clock_went(snapshot):
    """One sentence naming what used the run's time, or "" when nothing did.

    A site that asks for a slow crawl is the common answer and the one the
    reader can act on: the audit read fewer pages and made no extra requests
    because the site asked to be read slowly, which is not a defect in either
    of them.
    """
    from robots_parser import group_for

    crawl = (snapshot or {}).get("crawl") or {}
    parts = []
    asked = (group_for((snapshot or {}).get("robots") or {}, USER_AGENT)
             or {}).get("crawl_delay")
    if asked:
        parts.append("robots.txt asks for {:.0f}s between requests and this audit honoured "
                     "that in full".format(float(asked)))
    if crawl.get("stopped_by") == "wall clock":
        parts.append("the crawl itself was stopped by the clock after {:.0f}s".format(
            float(crawl.get("elapsed_s") or 0)))
    return ("Where the time went: {}. ".format("; ".join(parts))) if parts else ""


class _AltHeaders(dict):
    """One response's headers, read without regard to case.

    `requests` hands back a mapping that ignores case, and two readers here
    depend on that: `response_text` asks for `content-type`, and
    `detect_challenge` walks every header name looking for the marker a bot
    manager sets on its own refusal. `urllib` hands back an
    `email.message.Message` instead, so it is copied into this.

    Lookups are lowercased; iteration gives the names back in the server's own
    spelling, because `_vendor_from_headers` reads the vendor's name out of
    the header name itself.
    """

    def __init__(self, pairs=()):
        super().__init__()
        self._spelling = []
        for name, value in pairs:
            self._spelling.append(name)
            super().__setitem__(str(name).lower(), value)

    def __iter__(self):
        return iter(self._spelling)

    def keys(self):
        return list(self._spelling)

    def get(self, name, default=None):
        return super().get(str(name).lower(), default)


class _AltHop:
    """One redirect the second transport followed, in `history` order."""

    def __init__(self, status_code, url):
        self.status_code = status_code
        self.url = url


class _AltResponse:
    """What the second transport returns, shaped like a `requests.Response`.

    Only the attributes this marketplace actually reads - `status_code`,
    `headers`, `content`, `url`, `history`, `truncated`, `elapsed_ms` and
    `close`. Anything more would be a promise about a library that is not the
    one sending these bytes.
    """

    def __init__(self, status_code, headers, content, url, history=()):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.url = url
        self.history = list(history)
        self.truncated = False
        self.elapsed_ms = 0

    def close(self):
        return None


class _RecordRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects and remember each hop, or follow none at all.

    `_redirect_hop` reads `history` off a response to say where a request
    actually landed without paying for a second request, so the second
    transport has to record the same thing the first one does. Returning
    `None` from `redirect_request` makes `urllib` stop and raise the 3xx,
    which the caller then reads as the response - the same shape
    `allow_redirects=False` has on the primary client.
    """

    def __init__(self, follow=True):
        self.follow = follow
        self.hops = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.follow:
            return None
        self.hops.append(_AltHop(code, req.full_url))
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


class Fetcher:
    """GET/HEAD-only HTTP client with a fixed UA, delay and request ceiling.

    Every skill that touches the network goes through this class, so the
    read-only guarantee is enforced in one place rather than trusted to six
    scripts.

    Two ways to send, one gate. `get(..., transport=SECOND_TRANSPORT)` puts
    the request on the wire through `urllib.request` instead of the `requests`
    session, and passes every guard the primary path passes on the way: the
    forbidden-path refusal, the GET-or-HEAD rule, the request budget, the
    delay between requests and the wall clock. The read-only promise is about
    which method and which path, not about which library writes the socket.
    """

    # Which second client this instance can use, or None on an object that has
    # none. A check reads it rather than assuming, because a check under test
    # is often handed a stub with a `try_get` and nothing else.
    second_transport = SECOND_TRANSPORT

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
        return self._room_for_a_request()

    def _remaining(self):
        """Seconds of this phase's wall clock left, or None when there is none."""
        if self.deadline is None:
            return None
        return self.deadline - time.monotonic()

    def _room_for_a_request(self, wait=0.0):
        """Is the deadline still ahead, once `wait` seconds are paid out of it?

        `wait` is the delay owed to the site, or a retry's backoff, before the
        request goes out. It is part of the request's cost and belongs inside
        this test rather than after it: a `Crawl-delay` of ten seconds slept
        through after the clock test put the socket ten seconds past the
        deadline before a byte had moved.
        """
        left = self._remaining()
        return left is None or (left - wait) > 0

    def _socket_timeout(self):
        """The per-socket timeout for a request starting now, clamped to the clock.

        Never below REQUEST_CLAMP_FLOOR_SECONDS: a half-second timeout on the
        last half-second of a budget fails ordinary pages, and this audit
        reports a failed fetch as something the site did.
        """
        left = self._remaining()
        if left is None:
            return self.timeout
        return max(REQUEST_CLAMP_FLOOR_SECONDS, min(self.timeout, left))

    def _elapsed_cap(self, started):
        """How long this one request may run in total, clamped to the deadline.

        The unclamped cap is REQUEST_TOTAL_TIMEOUT, which bounds a request
        against its own start and says nothing about the phase's deadline. This
        is the repair: a response that began one second before the deadline no
        longer gets all thirty.

        Read off `self` at call time rather than passed down, because
        `PageFetcher` in `crawl.py` overrides `_read_body` with the signature
        this class had before, and adding a parameter would break it from a
        file this one does not own.
        """
        if self.deadline is None:
            return REQUEST_TOTAL_TIMEOUT
        return max(REQUEST_CLAMP_FLOOR_SECONDS,
                   min(REQUEST_TOTAL_TIMEOUT, self.deadline - started))

    def _stall_message(self, cap):
        """Why the read stopped, in terms of whose limit ended it.

        Two different facts wear one sentence otherwise. A response that ran
        past REQUEST_TOTAL_TIMEOUT is slow, and `explain_fetch_error` says so:
        "sent its response so slowly the audit stopped waiting". A response cut
        short because this phase's own clock ran out is not evidence about the
        site at all, and printing the first sentence for the second case
        publishes a limit of ours as a fault of theirs.
        """
        if self.deadline is not None and cap < REQUEST_TOTAL_TIMEOUT:
            return ("this audit's time budget ran out while the response was "
                    "still arriving ({:.0f}s of it)".format(cap))
        return "response was still arriving after {:.0f}s".format(cap)

    def get(self, url, user_agent=None, allow_redirects=True, method="GET",
            transport=PRIMARY_TRANSPORT):
        """Fetch a URL. Returns a response object or raises `FetchError`.

        `transport` chooses which HTTP client writes the bytes and nothing
        else. The gate below runs first and runs identically for both, so a
        second client cannot reach a path, a method, a request or a second of
        wall clock the first one could not.
        """
        if transport not in TRANSPORTS:
            raise FetchError("unknown transport: {!r}".format(transport))
        if is_forbidden_path(url) and not is_allowed_read_api(url):
            raise FetchError("refusing to fetch a state-changing or private path: {}".format(url))
        if method not in ("GET", "HEAD"):
            raise FetchError("only GET and HEAD are permitted, got {}".format(method))
        if not self.budget_left:
            raise FetchError("request budget exhausted ({})".format(self.max_requests))

        # The delay owed to the site is counted before the clock test, not
        # slept through after it. The old order tested the clock, then slept
        # out a `Crawl-delay` of up to ten seconds, and only then opened the
        # socket - so the request the clock had approved began after the
        # deadline it was approved against.
        wait = 0.0
        if self._last_request:
            since = time.monotonic() - self._last_request
            if since < self.delay:
                wait = self.delay - since
        if not self._room_for_a_request(wait):
            raise FetchError("wall-clock budget exhausted")
        if wait:
            time.sleep(wait)

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
                # The clock is tested before the backoff, not after it. Sleeping
                # first spent the backoff past the deadline and only then
                # noticed there had been no room for the retry.
                backoff = RETRY_BACKOFF_SECONDS * attempt
                if not self._room_for_a_request(backoff):
                    last_error = FetchError("wall-clock budget exhausted")
                    break
                time.sleep(backoff)
                self.count += 1
            if transport == SECOND_TRANSPORT:
                # The body arrives inside `_send_urllib`, under the same two
                # caps `_read_body` applies, so there is nothing left to read
                # after it returns.
                try:
                    response = self._send_urllib(url, method, headers,
                                                 allow_redirects, started)
                except FetchError as exc:
                    last_error = exc
                    if getattr(exc, "stalled", False):
                        break
                    continue
                if response.status_code in RETRYABLE_STATUS and attempt + 1 < RETRY_ATTEMPTS:
                    last_error = FetchError("HTTP {}".format(response.status_code))
                    continue
                self._last_request = time.monotonic()
                response.elapsed_ms = int((self._last_request - started) * 1000)
                return response

            try:
                # `stream=True` so nothing is downloaded until `_read_body`
                # says so. Both caps are then enforced before the bytes arrive
                # rather than discovered after they have already cost us.
                response = self.session.request(
                    method, url, timeout=self._socket_timeout(),
                    allow_redirects=allow_redirects,
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
        # The smaller of "thirty seconds" and "what is left of this phase". The
        # second half is the repair: without it a response that started one
        # second before the deadline was allowed all thirty.
        cap = self._elapsed_cap(started)
        try:
            # `iter_content` decodes Content-Encoding as it goes, so the cap
            # counts decompressed bytes and a compression bomb is bounded too.
            for chunk in response.iter_content(RESPONSE_CHUNK_BYTES):
                if time.monotonic() - started > cap:
                    response.close()
                    stall = FetchError(self._stall_message(cap))
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

    def _send_urllib(self, url, method, headers, allow_redirects, started):
        """One request through the second client. Guards have already run.

        This is only the code that writes the socket: `get` has already
        refused a forbidden path, refused anything but GET or HEAD, spent a
        request from the budget, checked the wall clock and waited out the
        delay. Nothing here may be reached any other way.
        """
        redirects = _RecordRedirects(follow=allow_redirects)
        opener = urllib.request.build_opener(redirects)
        request = urllib.request.Request(url, headers=dict(headers), method=method)
        try:
            stream = opener.open(request, timeout=self._socket_timeout())
        except urllib.error.HTTPError as exc:
            # A refusal is a response, and it is the response this transport
            # exists to collect. `urlopen` raises on 4xx and 5xx, so the 403
            # that arrives here as an exception is the measurement itself.
            stream = exc
        except urllib.error.URLError as exc:
            raise FetchError(str(getattr(exc, "reason", exc)))
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise FetchError(str(exc))

        try:
            content, truncated = self._read_stream(stream, method, started)
            status = getattr(stream, "status", None)
            if status is None:
                status = getattr(stream, "code", None)
            headers_seen = _AltHeaders(
                (getattr(stream, "headers", None) or {}).items())
            landed = getattr(stream, "url", None) or url
        finally:
            try:
                stream.close()
            except OSError:
                pass

        response = _AltResponse(status, headers_seen, content, landed, redirects.hops)
        response.truncated = truncated
        return response

    def _read_stream(self, stream, method, started):
        """`_read_body` for a stdlib response. Same two caps, same reasons.

        Returns `(bytes, truncated)`. The size cap keeps a 125 MB response
        from being pulled into memory before its content type is read, and the
        total-time cap ends a server that dribbles one byte every nine seconds
        instead of letting it hold a request open past the audit's own
        deadline.
        """
        if method == "HEAD":
            return b"", False
        chunks, total, truncated = [], 0, False
        # Clamped to the deadline exactly as `_read_body` is: the second client
        # may not reach a second of wall clock the first one could not.
        cap = self._elapsed_cap(started)
        while True:
            if time.monotonic() - started > cap:
                stall = FetchError(self._stall_message(cap))
                # Not weather, so not retried - the same reading `_read_body`
                # takes of the same behaviour.
                stall.stalled = True
                raise stall
            try:
                chunk = stream.read(RESPONSE_CHUNK_BYTES)
            except (OSError, http.client.HTTPException) as exc:
                raise FetchError(str(exc))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_RESPONSE_BYTES:
                truncated = True
                break
        return b"".join(chunks)[:MAX_RESPONSE_BYTES], truncated

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
    return main_text_and_region(soup, prepared)[0]


def main_text_and_region(soup, prepared=None):
    """`main_text`, and the element the text was taken from.

    Anything measured *about* that text - how many subheadings break it up,
    how many paragraphs are walls - has to be counted in the same element,
    or the two numbers describe different documents and their ratio describes
    neither.

    A Spanish manufacturer's homepage was reported as "1208 words with a
    subheading only every 1208 words". It carries 38 h2, h3 and h4 elements,
    and the audit's own snapshot lists all of them. The words came from the
    region below; the headings were counted in a different tree, which had
    none, and 26 pages were reported as walls of text on that arithmetic.
    """
    whole = (prepared if prepared is not None
             else tidy_spacing(re.sub(r"\s+", " ",
                                      content_soup(soup).get_text(separator=" ")).strip()))
    best, best_node = "", None
    for selector in MAIN_TEXT_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        text = _strip_chrome(node)
        if len(text) < MAIN_TEXT_MIN_CHARS:
            continue
        # Complete enough to be the content region: keep the chrome out.
        if len(text) >= MAIN_TEXT_COMPLETE_SHARE * len(whole):
            return text, node
        if len(text) > len(best):
            best, best_node = text, node
    if len(best) > len(whole):
        return best, best_node
    return (whole or visible_text(soup)), None


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
    before = len(visible_text_of(soup))
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
    # Hiding is a declaration about part of a page, never about all of it. A
    # store theme wraps its whole body in a `display:none` div and reveals it
    # with script, and taking the declaration literally deleted 2,489 of 2,489
    # characters - after which four separate findings said the site states no
    # founding year, no definition, no contact and no headings, about a page
    # that says all four. The browser pass would have caught it and never ran,
    # because a page with 5,102 characters of raw text does not look like a
    # shell.
    #
    # So: if stripping what the page calls hidden leaves almost nothing, the
    # page is not hidden, the rule is wrong here, and the document stands.
    if before and len(visible_text_of(clone)) < before * HIDDEN_STRIP_FLOOR:
        return make_soup(str(soup))
    return clone


# How much of a page's text may be removed as "hidden" before the conclusion
# becomes absurd. A tenth is a cookie banner and a set of modals; nine tenths
# is a theme that hides the page and reveals it with script.
HIDDEN_STRIP_FLOOR = 0.10


def visible_text_of(node):
    """Text of a parsed node, without reparsing it. Used to measure a strip."""
    return re.sub(r"\s+", " ", node.get_text(separator=" ")).strip()


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


# `header`, `footer` and `aside` are scoped to the nearest section they sit
# in, not to the page. That is what the HTML specification says they mean, and
# component frameworks take it literally.
#
# A denim shop's collection page wraps every product card's name and price in
# that card's own `<header class="my-4">` - "The Benjamin Tapered Fit - OG
# Japanese Selvedge. \u00a3325" - and puts the collection's description in a
# `<header>` inside `<main>`. Removing every element named `header` therefore
# deleted all 21 product names, all 21 prices and the description, taking the
# page from 2,696 characters of text to 105. Four findings followed: the page
# carries its content in images, its sections open with warm-up prose,
# JavaScript supplies most of its text, and its homepage has nothing worth
# quoting. All four are false. The page is a plain server-rendered list of
# products with their prices in the HTML.
#
# So the test is where the element sits, not what it is called. A `header`
# with a sectioning ancestor is that section's header and stays; one at the
# top of the document is the site's masthead and goes. `nav` is different and
# is removed wherever it appears: an in-page table of contents is navigation
# too.
_SECTION_SCOPED = ("header", "footer", "aside")
_SECTIONING_ANCESTORS = frozenset({"main", "article", "section", "li", "td", "th"})


def _is_page_furniture(found):
    """Is this landmark the page's own chrome, or some section's header?"""
    if found.name not in _SECTION_SCOPED:
        return True
    for parent in found.parents:
        if getattr(parent, "name", None) in _SECTIONING_ANCESTORS:
            return False
    return True


def _decompose_declared_chrome(clone, selector):
    """Remove chrome this page declares, leaving each section its own header."""
    for found in clone.select(selector):
        if getattr(found, "attrs", None) is None:
            continue                      # already removed with its parent
        if not _is_page_furniture(found):
            continue
        found.decompose()


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
    - a landmark element, `aria-hidden`, `display:none` - comes out, except
    where a landmark is scoped to a section rather than to the page, because
    a product card's own header is that card's content. What is content but
    is not the page's own words comes out too, unless that empties the page.
    What a CSS class name merely suggests
    is chrome comes out only while it stays small, because a guess that
    removes most of a page has stopped being a guess about furniture and
    become a claim about content.
    """
    clone = make_soup(str(node))
    for tag in clone(list(_NON_CONTENT_TAGS)):
        tag.decompose()
    for selector in _DECLARED_CHROME_SELECTORS:
        _decompose_declared_chrome(clone, selector)
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
