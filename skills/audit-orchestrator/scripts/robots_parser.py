"""A robots.txt parser that keeps the per-agent groups intact.

`urllib.robotparser` answers "may I fetch this URL", which is all a crawler
needs. This audit also has to answer "is *GPTBot specifically* shut out", so it
keeps the group structure and matches agents the way the REP draft does:
longest matching user-agent token wins, `*` only applies when no named group
matched.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse


def parse_robots(text):
    """Parse robots.txt into groups plus sitemap references.

    Returns `{"groups": [{"agents", "allow", "disallow", "crawl_delay"}],
    "sitemaps": [...], "errors": [...]}`. Unknown directives are ignored, which
    matches how real crawlers behave.
    """
    groups = []
    sitemaps = []
    errors = []
    current = None
    expecting_agent = False

    # A byte-order mark is a legal and common prefix on a file written on
    # Windows, and `response_text` decodes as plain UTF-8, so it survives the
    # fetch. Left in place it became part of the first field name - the first
    # line read as `﻿user-agent`, matched no branch, and was dropped
    # without even an error. The consequences were the two worst this parser
    # can have: a site-wide `Disallow: /` was reported as a clean pass, and
    # `crawl.py` gates robots compliance on there being groups, so the crawl
    # stopped respecting a file it could no longer read.
    for lineno, raw in enumerate((text or "").lstrip("﻿").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            errors.append("line {}: no directive separator".format(lineno))
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            if current is None or not expecting_agent:
                current = {"agents": [], "allow": [], "disallow": [], "crawl_delay": None}
                groups.append(current)
                expecting_agent = True
            current["agents"].append(value.lower())
        elif field in ("allow", "disallow"):
            if current is None:
                # Rules before any User-agent line apply to nobody. Record the
                # problem rather than silently attaching them to `*`.
                errors.append("line {}: {} before any user-agent".format(lineno, field))
                continue
            expecting_agent = False
            current[field].append(value)
        elif field == "crawl-delay":
            if current is not None:
                expecting_agent = False
                try:
                    current["crawl_delay"] = float(value)
                except ValueError:
                    errors.append("line {}: non-numeric crawl-delay".format(lineno))
        elif field == "sitemap":
            sitemaps.append(value)
            expecting_agent = False
        else:
            # Any other directive also ends the run of user-agent lines. Only
            # `Allow`, `Disallow` and `Crawl-delay` did, so a `Sitemap:` line -
            # or a `Request-rate:`, `Visit-time:`, `Host:` or `Clean-param:`,
            # all of which occur in the wild - sitting between two groups
            # merged them:
            #
            #     User-agent: *
            #     Sitemap: https://example.com/sitemap.xml
            #
            #     User-agent: AhrefsBot
            #     Disallow: /
            #
            # became one group naming both agents and disallowing everything,
            # and the audit reported "robots.txt disallows every crawler from
            # the entire site" at critical severity about a file that blocks
            # one backlink scraper. Consecutive `User-agent` lines are the only
            # thing that shares a group, so anything else closes it.
            expecting_agent = False

    return {"groups": groups, "sitemaps": sitemaps, "errors": errors}


def group_for(parsed, agent):
    """The group that governs `agent`: longest matching token, else `*`.

    Matching is a case-insensitive **prefix** test, which is what RFC 9309 and
    Google's implementation both specify. It used to also accept the token
    anywhere in the name, and that produced a confident, false finding on a
    public broadcaster: a legacy blocklist entry reading

        User-agent: Fetch
        Disallow: /

    was reported as blocking `Meta-ExternalFetcher` from the whole site,
    because "fetch" occurs inside "Meta-ExternalFetcher". The group that
    actually governs that crawler there is `*`, which disallows nothing. The
    report told the owner to change a rule that was doing nothing, about a
    crawler that was never blocked.

    Substring matching cannot be made safe by tuning: "bot" is a substring of
    almost every crawler name, and the shorter the stray token, the more
    crawlers it captures. A prefix is what a crawler itself looks for.
    """
    agent = (agent or "*").lower()
    best_len = -1
    for grp in parsed.get("groups", []):
        for name in grp["agents"]:
            if name != "*" and agent.startswith(name) and len(name) > best_len:
                best_len = len(name)

    # Every group naming this agent at the winning length, not the first of
    # them. RFC 9309 section 2.2.1 says records with the same product token are
    # combined, and splitting them across blocks is ordinary practice:
    #
    #     User-agent: *
    #     Disallow: /wp-admin/
    #
    #     User-agent: *
    #     Disallow: /research
    #
    # Taking only the first group lost `/research` entirely, so the check that
    # exists to report substantive disallows reported none and the report said
    # "the only disallowed paths are the standard admin, cart and search
    # routes". The same shape with `Disallow: /` in one block and `Allow: /` in
    # a later one produced the opposite error: a false, high-severity claim
    # that an AI answer crawler was blocked.
    matched = [grp for grp in parsed.get("groups", [])
               if any(name != "*" and agent.startswith(name) and len(name) == best_len
                      for name in grp["agents"])] if best_len >= 0 else [
        grp for grp in parsed.get("groups", []) if "*" in grp["agents"]]
    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]

    delays = [g["crawl_delay"] for g in matched if g.get("crawl_delay") is not None]
    return {
        "agents": sorted({name for g in matched for name in g["agents"]}),
        "allow": [rule for g in matched for rule in g.get("allow", [])],
        "disallow": [rule for g in matched for rule in g.get("disallow", [])],
        # The strictest of the stated delays, because combining records must
        # not make the file more permissive than any block in it.
        "crawl_delay": max(delays) if delays else None,
    }


# One compiled pattern per rule, kept for the life of the process.
#
# `is_disallowed` is called once per candidate link, and it walked every rule
# in the group rebuilding the pattern string each time. With more distinct
# rules than the `re` module's own 512-entry cache holds - a large retailer's
# robots.txt runs to several hundred - every call recompiled every pattern.
# Measured here: 600 rules cost 15 ms per call, and the crawl makes thousands
# of them, so a few seconds of a 240-second budget went on rebuilding strings
# that never change.
_RULE_CACHE = {}

# Runs of wildcards collapse to one. `/*/*/*/*/*/*x` is the same path
# expression as `/*x` to a matcher and exponentially cheaper to run: without
# this, a hostile robots.txt could hold the crawl in backtracking.
_WILDCARD_RUN = re.compile(r"\*+")


def _rule_matches(pattern, path):
    """REP path matching, including `*` wildcards and a `$` end anchor."""
    if pattern == "":
        return False
    compiled = _RULE_CACHE.get(pattern)
    if compiled is None:
        anchored = pattern.endswith("$")
        body = _WILDCARD_RUN.sub("*", pattern[:-1] if anchored else pattern)
        regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
        compiled = re.compile("^" + regex + ("$" if anchored else ""))
        _RULE_CACHE[pattern] = compiled
    return compiled.match(path) is not None


def is_disallowed(parsed, agent, path):
    """True when `agent` is forbidden from `path`.

    Longest matching rule wins; Allow beats Disallow at equal length, which is
    what Google and the REP draft specify.
    """
    grp = group_for(parsed, agent)
    if grp is None:
        return False
    path = unquote(path or "/") or "/"
    best_len, best_allowed = -1, True
    for rule in grp.get("allow", []):
        if _rule_matches(rule, path) and len(rule) >= best_len:
            best_len, best_allowed = len(rule), True
    for rule in grp.get("disallow", []):
        if _rule_matches(rule, path) and len(rule) > best_len:
            best_len, best_allowed = len(rule), False
    return not best_allowed


def blocks_entire_site(parsed, agent):
    """True when the agent's group shuts the whole origin, with no carve-out."""
    grp = group_for(parsed, agent)
    if grp is None:
        return False
    if not any(rule.strip() in ("/", "/*") for rule in grp.get("disallow", [])):
        return False
    # `Allow: /` ties `Disallow: /` on length, and a tie goes to Allow, so the
    # root is reachable and nothing is blocked. `is_disallowed` two functions
    # up already implements that rule; this one deliberately ignored a bare
    # `Allow: /` and therefore contradicted it - one module answering two ways
    # about one file, with the critical finding "robots.txt disallows every
    # crawler from the entire site" hanging off the wrong answer. Asking the
    # rule engine settles it in one place.
    if not is_disallowed(parsed, agent, "/"):
        return False
    # A bare `Allow: /something` reopens part of the site, so it is not a
    # site-wide block.
    return not [r for r in grp.get("allow", []) if r.strip() not in ("", "/")]


# Paths whose disallow is normal hygiene, never an AI-discoverability defect.
_BENIGN_DISALLOW = re.compile(
    r"^/(wp-admin|admin|administrator|cgi-bin|cart|checkout|basket|account|"
    r"login|signin|sign-in|logout|register|signup|search|\?|.*\?|tmp|temp|"
    r"private|internal|api|graphql|xmlrpc|wp-includes|wp-json|feed|print|"
    # `order`, `customer`, `user` and `profile` moved to the strict-boundary
    # group below: the boundary here allows a hyphen, so `/customer-stories`
    # and `/user-guides` - marketing sections on a great many sites - were
    # excused as account areas and a real robots block on them went unreported.
    r"thank-you|my-account|preview|draft|"
    # The plurals and near-synonyms of what is already here. A hosted store
    # platform generates `Disallow: /checkouts/` and `Disallow: /orders`
    # alongside `/checkout` and `/account`, and this list excused the singular
    # and flagged the plural - so "robots.txt disallows 2 paths that look like
    # real content" was the second Start-here item on every storefront on that
    # platform, telling shopkeepers to open their checkout to crawlers.
    #
    # `bag` and `bags` are deliberately absent: `/bags` is a handbag shop's
    # product listing before it is a synonym for a basket, and swallowing real
    # content is the more expensive of the two mistakes.

    # Authentication and invitation routes. A site is right to keep these out
    # of an index, and reporting `/oauth`, `/confirm`, `/notifications` and
    # `/invites/` as "paths that look like real content" put three wrong items
    # at the top of a real report. The list already knew `/login` and
    # `/signup`; it had simply never been extended to the rest of the category.
    r"oauth|auth|preauth|preauthorize|sso|saml|callback|confirm|verify|verification|activate|"
    r"reset-password|forgot|password|invite|invites|invitation|notifications|"
    r"onboarding|billing|unsubscribe|webhooks?|"
    # Asset directories. Blocking them is ordinary bandwidth hygiene and holds
    # back no sentence anyone would quote. Reporting `/icons/` and `/images/`
    # as "paths that look like real content" was true of neither.
    r"icons?|images?|img|assets|static|media|files|uploads|css|js|"
    r"javascript|fonts?|styles?|scripts?|dist|build|vendor|node_modules|"
    # Files that exist for machines, not readers. Two shop reports opened
    # with "robots.txt disallows N paths that look like real content" and
    # named `/apple-app-site-association` - an Apple deep-linking manifest,
    # 89 bytes of JSON, not a page - as the number one thing to fix.
    r"apple-app-site-association|apple-touch-icon|humans\.txt|ads\.txt|"
    r"app-ads\.txt|security\.txt|browserconfig\.xml|crossdomain\.xml|"
    r"manifest\.json|sw\.js|service-worker|serviceworker|\.well-known|"
    # A framework's own directory and its install notes. The stock robots.txt
    # a widely used CMS ships disallows `/core/`, `/README.txt`, `/web.config`
    # and `/profiles/`, and the finding named the first three as "paths that
    # look like real content" - a framework directory, an install readme and a
    # web-server config file, none of which can hold a page. The exclusion
    # list caught `/profiles/` on the same block and not its neighbours,
    # which is the shape of a name list rather than a rule: what these have in
    # common is that they are shipped with the software rather than written by
    # the site.
    r"readme(?:\.[a-z]+)?|changelog(?:\.[a-z]+)?|license(?:\.[a-z]+)?|"
    r"licence(?:\.[a-z]+)?|copyright(?:\.[a-z]+)?|install(?:\.[a-z]+)?|"
    r"upgrade(?:\.[a-z]+)?|maintainers(?:\.[a-z]+)?|composer\.(?:json|lock)|"
    r"package(?:-lock)?\.json|yarn\.lock|web\.config|\.htaccess|\.env|"
    r"xmlmap|xmlrpc\.php|update\.php|install\.php|"
    # Health endpoints. One report excluded `/api/commerce/healthcheck/` under
    # this same rule and then listed `/healthcheck.html` as a path that looks
    # like real content, in the same finding - the rule was right and the list
    # it was applied to was not.
    #
    # Only the unambiguous endpoint names. This regex matches a prefix with no
    # word boundary, so a bare `health` would swallow `/healthcare` and a bare
    # `status` would swallow `/statuses-of-liberty`; on an insurance site or a
    # gallery those are the content.
    r"healthcheck|health-check|healthz|statusz|heartbeat|readiness|liveness|"
    # Framework and build directories, which hold code rather than prose.
    # `.hg`, `.bzr` and `_darcs` join `.git` and `.svn` as the same class seen
    # from a different tool: the metadata directory of a working copy, holding
    # file histories. A leading full stop or underscore is what makes these
    # safe to match without case: no ordinary word starts with one.
    r"app_themes|app_data|app_code|app_start|controls|_next|_nuxt|_astro|"
    r"_app|_layouts|web-inf|meta-inf|bin|obj|\.git|\.svn|\.hg|\.bzr|_darcs|cgi|"
    # The system folders a Windows web server's collaboration and publishing
    # extensions create at the root: `_vti_bin`, `_vti_pvt`, `_vti_cnf` and
    # the rest of that family, the list-and-library store `_catalogs` and the
    # REST endpoint `_api`, beside `_layouts`, which was already here. A
    # central bank's robots.txt closes `/_layouts/`, `/_vti_bin/` and
    # `/_catalogs/` and nothing else; the first was excused, the other two
    # were named as "paths that look like real content" and ranked first in
    # the report. A leading underscore is why these are safe to match wide:
    # no word a person reads begins with one.
    r"_vti_[a-z0-9_]*|_catalogs|_api|"
    # Error, maintenance and utility pages. A site is right to keep these
    # out of an index, and none of them is content anyone would quote.
    r"pageerror|error|errors|404|500|not-found|notfound|maintenance|"
    r"old-browser|unsupported-browser|browser-not-supported|browser-upgrade|"
    r"addtocart|internationaladdress|"
    # Shopify's own boilerplate. `/services` and `/recommendations` are its
    # internal endpoints, and a shop's robots.txt says so in a comment
    # directly above them - a comment robots parsers drop. Reported as "paths
    # that look like real content", `/services` was the number one thing to
    # fix on a nail-care shop's report, and it returns 404.
    r"services|recommendations|cdn|_shopify|shopify)"
    # The boundary the comment sixty lines up says this pattern needs. It was
    # applied to `health` and `status` alone, and every other alternative had
    # the same hole: `print` swallowed a printer retailer's `/printers`,
    # `media` a mediation firm's `/mediation` and a broadcaster's `/mediateca`,
    # `bin` an optician's `/binoculars`, `feed` a support site's `/feedback`.
    # Each was silently excused, and the decline line then told the owner that
    # the only disallowed paths were the standard admin and cart routes - about
    # a rule blocking a section of their site.
    #
    # A separator, a digit or the end of the rule. That still admits every
    # shape the list was written for: `/wp-admin`, `/_next/static`,
    # `/health-check`, `/apple-app-site-association`, `/500`.
    r"(?=$|[/.\-_?*]|\d)",
    re.I,
)


# Tokens that make a rule plumbing wherever they appear, not only at the start.
# Real robots.txt files are full of rules like `/*/print$`, `/*?*add-to-cart=`
# and `/collections/*+*`, which an anchored match treated as real content.
_BENIGN_ANYWHERE = re.compile(
    r"(?:^|[/*?&=_-])(?:print|preview|draft|cart|checkout|basket|login|signin|"
    r"logout|register|signup|account|admin|search|filter|sort|sortby|orderby|"
    r"session|sessionid|utm_|replytocom|add-to-cart|wishlist|compare|currency|"
    r"variant|cgi-bin|feed|rss|atom|json|xml|amp|oauth|sso|saml|"
    r"callback|confirm|verify|activate|invite|invitation|webhook)\b", re.I)

# `api` and `graphql` left this list for the reason they left
# `FORBIDDEN_PATH_SEGMENTS`: they name an endpoint at the root of a site and
# name reference documentation everywhere else. A documentation site whose
# robots.txt closes `/docs/api/` was having that reported as blocking nothing
# of value. Anchored, they still read `Disallow: /api/` as the plumbing it is.
_ENDPOINT_ROOT_RULE = re.compile(r"^/(?:api|graphql)(?:[/*.]|$)", re.I)


# A path made only of digits is an internal identifier, not a page anyone
# reads. One shop's robots.txt disallows `/26657478`; it returns 404, and it
# was reported alongside a real content path as something to unblock.
_NUMERIC_PATH_RE = re.compile(r"^/\d+/?$")

# A path made of a random token is a trap, not a page. A fashion shop's
# robots.txt closes four addresses shaped like `/QX47RT2MZP9K` and
# `/kd8vn3w1pq5z` - twelve characters, capitals and digits shuffled together,
# no word in them - beside one real section, and all five were reported as
# "paths that look like real content". Nobody links a reader to a string like
# that. It exists so that a crawler which ignores robots.txt and requests it
# can be recognised and refused, and the rule is the site doing exactly that.
#
# The shape is the class, not the strings. One segment, letters and digits
# only, eight or more of them, and the two kinds switching back and forth at
# least four times. A name with a number in it switches once or twice -
# `/phone15pro`, `/covid19-update`, `/2024collection`, `/mp3players` - and a
# separator anywhere means somebody chose words, so none of those is excused.
RANDOM_TOKEN_MIN_LENGTH = 8
RANDOM_TOKEN_MIN_SWITCHES = 4


def _is_a_random_token(rule):
    """True for `/QX47RT2MZP9K`: a crawler trap, not a section. See above."""
    segment = (rule or "").strip().rstrip("$").strip("/")
    if (len(segment) < RANDOM_TOKEN_MIN_LENGTH or not segment.isascii()
            or not segment.isalnum()):
        return False
    digits = sum(ch.isdigit() for ch in segment)
    if digits < 2 or len(segment) - digits < 2:
        return False
    switches = sum(1 for a, b in zip(segment, segment[1:]) if a.isdigit() != b.isdigit())
    return switches >= RANDOM_TOKEN_MIN_SWITCHES

# A copy of the site that is not the site. A broadcaster's robots.txt blocks
# `club-preprod`, `digital-preprod`, `magazine-dev` and `magazine-test`, and
# all eleven "paths that look like real content" in its report were of that
# shape. Keeping a staging copy out of an index is the whole reason robots.txt
# exists, and telling the owner to open it up would put unfinished pages into
# answers about the brand.
#
# Matched anywhere in the rule, and as a whole word, because the environment
# name is a suffix on a real section name: it is `magazine-dev`, never `/dev`.
# Deliberately short. Every word here has to mean "environment" and nothing
# else, because a word with a second meaning turns a real section into
# plumbing - which is the more expensive mistake of the two. `development`
# would swallow `/development-services`, `testing` a laboratory's `/testing`,
# `legacy` a charity's `/legacy-giving`, `demo` a "book a demo" page, and
# `mirror` a shop that sells mirrors. All five are left out.
_STAGING_RE = re.compile(
    r"(?:^|[/*?&=_.-])(?:preprod|pre-prod|staging|stage|dev|test|uat|qa|"
    r"sandbox|backup|bak)(?:$|[/*?&=_.-])", re.I)


# The plurals and near-synonyms of the commerce and account words above, with
# a stricter boundary of their own.
#
# A hosted store platform generates `Disallow: /checkouts/` and
# `Disallow: /orders` alongside `/checkout` and `/account`, and the list above
# excused the singular and flagged the plural - so "robots.txt disallows 2
# paths that look like real content" was the second Start-here item on every
# storefront on that platform, telling shopkeepers to open their checkout.
#
# The boundary is `(?=$|[/?*]|\d)` and not the wider one above, because these
# words continue into real content far more readily: `/customer-stories` and
# `/user-guides` are marketing sections, not account areas, and a hyphen must
# not excuse them. `bag` and `bags` are deliberately absent - `/bags` is a
# handbag shop's product listing first.
_BENIGN_COMMERCE_DISALLOW = re.compile(
    r"^/(checkouts?|orders?|order-status|order-tracking|track-order|carts|"
    r"baskets|accounts?|my-accounts|customers?|clients|sessions?|profiles?|"
    r"users?|wishlists?|favourites|favorites|"
    # The rest of the signed-in account area. `/login`, `/account` and
    # `/notifications` were already excused and these three were not, so a
    # media site's `Disallow: /dashboard` and `Disallow: /settings` - the two
    # screens that exist only once a reader has signed in - were reported as
    # "2 sections of the site" closed to nine AI answer crawlers, and ranked
    # for the owner to reopen. A page nobody can reach without an account
    # holds nothing an answer engine could quote, and the site is right to
    # keep it out.
    #
    # In the strict-boundary group, not the wide one, for the reason that
    # group exists: the wide boundary admits a hyphen, and `/dashboard-cameras`
    # is a shop's product listing while `/dashboard/` is an account screen.
    r"dashboards?|settings|preferences|"
    # A framework's own directory, in the strict-boundary group for the same
    # reason `customer` and `user` are: the wide boundary admits a hyphen, and
    # `/core-values` is a page about a company while `/core/` is a CMS's code.
    r"core|profiles)(?=$|[/?*]|\d)",
    re.I)


# A rule naming a file a program reads rather than a page a person does.
#
# The extension is the class. Two sites' reports listed `/me.json`,
# `/follows.json` and `/translations.json` among "paths that look like real
# content": the first answers 401 to anyone not signed in, the second is a
# reader's own follow list, the third is a bundle of interface strings. None
# of them is a page, none can be quoted in an answer, and the vocabulary above
# missed all three because it matches words and these are file types.
#
# `_BENIGN_ANYWHERE` already names `json` and `xml` and could not reach them:
# its boundary class has no full stop in it, so it sees `/me.json` as the word
# `me`. This reads the suffix instead, which is what actually says "machine".
_MACHINE_FILE_RE = re.compile(
    r"\.(?:json|ndjson|geojson|xml|ya?ml|map|lock)(?:$|[/?*])", re.I)


# The metadata directory a version-control system leaves inside a working
# copy. A media site's robots.txt closes `/RCS/` in a block its own comment
# introduces as "big or useless directories", and the report named it as a
# path that looks like real content. It holds file histories.
#
# Case-sensitive, and that is the whole of this rule's safety: these tools
# create their directories in capitals, while the same letters in lower case
# are ordinary words on ordinary sites - a recruitment agency's `/cvs` is a
# page of curricula vitae, and excusing that would hide a real block on real
# content. The dot-prefixed and underscore-prefixed forms are in the wide list
# above instead, where no case rule is needed because no word begins with a
# full stop.
_VCS_METADATA_RE = re.compile(r"(?:^|/)(?:RCS|CVS|SCCS)(?:$|[/*])")


# The web front ends of a source repository: the file browser, the diff and
# timeline views, the ticket tracker bolted to it. A database library's
# robots.txt closes `/cvstrac`, `/src`, `/docsrc` and `/contrib` - its
# repository browser, its documentation sources and its contributed code -
# and all four were reported as "paths that look like real content", ranked
# first, on a project whose whole site is documentation. Every one of those
# addresses expands into a page per file per revision, an endless set of
# diffs, and closing it to crawlers is what the tools' own install notes
# recommend.
#
# Whole first segment only, with the strict boundary `(?=$|[/?*])`: a hyphen
# must not excuse `/src-images` or `/git-guide`, which are somebody's pages.
_CODE_BROWSER_RE = re.compile(
    r"^/(?:src|docsrc|cvstrac|trac|svn|cgit|gitweb|git|hg|fossil-scm)(?=$|[/?*])", re.I)

# Names a repository front end also uses, and a site of prose uses as well.
# `/timeline` is a museum's history page, `/source` a newsroom's methodology
# note, `/cvs` a recruitment agency's page of curricula vitae, `/fossil` a
# natural-history collection, `/contrib` a community's contributors page.
# Excused only where the same group also closes one of the unambiguous
# repository paths above: the file then describes a software project, and
# on a software project these are the project's tools. Alone, each is read
# as the section of a site it usually is.
_CODE_BROWSER_CONTEXT_RE = re.compile(
    r"^/(?:source|contrib|timeline|fossil|cvs)(?=$|[/?*])", re.I)


def _closes_a_code_browser(siblings):
    """Does this group also close an unambiguous repository front end?"""
    return any(_CODE_BROWSER_RE.match((rule or "").strip()) for rule in siblings or ())


def benign_disallow(rule, siblings=None):
    """True for the admin/cart/search/parameter paths every site blocks on purpose.

    `siblings` is the rest of the group's `Disallow` list, for the few names
    that are plumbing only in the company of others - see
    `_CODE_BROWSER_CONTEXT_RE`. Without it those names are read as content.
    """
    rule = (rule or "").strip()
    if not rule or rule in ("/", "/*"):
        return False
    if _NUMERIC_PATH_RE.match(rule):
        return True
    if _is_a_random_token(rule):
        return True
    if _BENIGN_COMMERCE_DISALLOW.match(rule):
        return True
    if _MACHINE_FILE_RE.search(rule) or _VCS_METADATA_RE.search(rule):
        return True
    if _CODE_BROWSER_RE.match(rule):
        return True
    if _CODE_BROWSER_CONTEXT_RE.match(rule) and _closes_a_code_browser(siblings):
        return True
    # A rule that is only a parameter or wildcard filter blocks duplicates,
    # not content.
    #
    # "two or more wildcards" used to be enough on its own, and it is not a
    # statement about what the rule blocks. It swallowed `/*/newsletter/existing/`
    # - a live page on a real site - and the report then told the reader the
    # only disallowed paths were "standard admin/cart/account/search routes",
    # which was not true of what had actually been excluded. A wildcard rule is
    # plumbing when it filters a query string or a file extension; when it has
    # real path segments in it, it blocks whatever is at those segments.
    if rule.startswith(("/*?", "/?", "*?")) or "?" in rule or "=" in rule:
        return True
    if rule.count("*") >= 2 and not re.search(r"[a-z]{3}", rule.replace("*", ""), re.I):
        return True
    return bool(_BENIGN_DISALLOW.match(rule) or _BENIGN_ANYWHERE.search(rule)
                or _STAGING_RE.search(rule))


def substantive_disallows(parsed, agent):
    """Disallow rules for `agent` that block real content, not just plumbing.

    A rule an `Allow` overrides is not a block, so it is not reported as one.
    Without that test a file reading `Disallow: /` followed by `Allow: /` -
    which reaches nothing, because a tie goes to Allow - was listed as blocking
    the whole site, and a `Disallow: /docs` cancelled by a longer
    `Allow: /docs/` was listed as blocking the documentation.
    """
    grp = group_for(parsed, agent)
    if grp is None:
        return []
    rules = grp.get("disallow", [])
    return sorted({r.strip() for r in rules
                   if r.strip() and not benign_disallow(r, rules)
                   and is_disallowed(parsed, agent, _sample_path(r.strip()))})


def _sample_path(rule):
    """A URL path this rule matches, so the rule can be re-tested against Allow.

    Wildcards stand for something rather than nothing, and a `$` anchor is not
    part of the path it anchors.
    """
    path = rule[:-1] if rule.endswith("$") else rule
    path = path.replace("*", "x")
    return path if path.startswith("/") else "/" + path


def names_agent(parsed, agent):
    """True when some group names `agent` with a token of its own, not with `*`.

    `group_for` deliberately falls back to the `*` group, so it can say which
    rules govern an agent and cannot say who those rules were written about.
    The two are different decisions and want different findings: a `*` rule
    closes a path to Googlebot and every feed reader as well, and a named group
    closes it to that one crawler while leaving the path open to everybody
    else. Prefix matching, for the reason `group_for` gives above.
    """
    agent = (agent or "").lower()
    return any(name != "*" and agent.startswith(name)
               for grp in parsed.get("groups", []) for name in grp["agents"])


def _rule_set(group):
    """The rules a group carries, as a comparable value.

    A bare `Disallow:` is a real line with no path, and it is the whole point
    of the comparison below: it says "nothing is disallowed" where the group
    above it disallowed several paths. Stripped to "" rather than dropped, so
    two groups that differ only by one of them being empty compare unequal.
    """
    return (tuple(sorted((r or "").strip() for r in group.get("allow", []))),
            tuple(sorted((r or "").strip() for r in group.get("disallow", []))),
            group.get("crawl_delay"))


def repeated_agent_groups(parsed):
    """Agent tokens that more than one group names, where the groups disagree.

    Returns `[(token, [group, ...])]`, tokens in the order they first appear.

    Kept here rather than in a check because what counts as one group is
    decided in `parse_robots` alone: a run of consecutive `User-agent:` lines
    shares a group and any other directive - a `Sitemap:`, a `Host:`, a
    `Crawl-delay:` - closes it. `group_for` then merges every group naming the
    same token, which is what RFC 9309 section 2.2.1 requires. A check reading
    `parsed["groups"]` would have to restate both of those rules to know
    whether it was looking at one group or two, and a second copy of a
    definition is a second thing to get wrong. Being here also makes it shared:
    every skill that reads robots.txt can ask.

    Measured on a WooCommerce shop: `/robots.txt` carried the shop's own
    `User-agent: *` block with its `Disallow` rules, and then a plugin appended
    a second `User-agent: *` block with a bare `Disallow:`. RFC 9309 combines
    them and the site's rules stand; a crawler that takes the last matching
    group, or the most recently written one, reads "nothing is disallowed"
    instead. The file does not say which, so the site does not know what it is
    allowing - and nothing in this audit told the owner, because every reader
    of this module asks `group_for` for the merged answer and never sees that
    there was more than one place to merge from.

    Groups whose rules are identical are left out. Repeating the same block is
    untidy and changes no crawler's behaviour, and a finding about it would be
    a finding about whitespace.
    """
    order = []
    by_token = {}
    for group in parsed.get("groups", []):
        for name in group.get("agents", []):
            if name not in by_token:
                by_token[name] = []
                order.append(name)
            by_token[name].append(group)
    return [(token, by_token[token]) for token in order
            if len(by_token[token]) > 1
            and len({_rule_set(g) for g in by_token[token]}) > 1]


def rule_matches_path(rule, path):
    """True when a robots.txt path rule covers `path`.

    Exposed so a caller can count how many of the URLs it has already fetched
    sit behind a rule. "This path is disallowed" is a fact about a file;
    "14 of the 37 pages this crawl read are behind it" is a fact about the
    site, and only the second one tells an owner what the rule costs.
    """
    return _rule_matches(rule, unquote(path or "/") or "/")


def section_disallows(parsed, agent):
    """Real sections closed to `agent` by a group naming it, and open to `*`.

    A group that names crawlers and disallows one path was invisible to both
    of the checks that read this file: one asks only whether an agent is shut
    out of the whole origin, and the other reads only the `User-agent: *`
    group. A file whose single group named eleven answer, search and training
    crawlers and carried `Disallow: /store/` fell between them, so a shop's
    entire catalogue - every product, price and stock state - was closed to the
    crawlers that fetch pages to build AI answers, while the report listed the
    site under "checks that ran and found nothing wrong".

    Three exclusions, each of them a boundary with a check that already exists
    rather than a threshold picked to make a number come out:

    * A whole-site block. `Disallow: /` is the same defect one level up, and a
      section inside it is not a second thing for an owner to fix.
    * Anything `benign_disallow` recognises: cart, checkout, search, account,
      asset directories, staging copies, query-string filters and machine
      files. That vocabulary is the thing that tells a section of a site from
      its plumbing, and without it this would fire on most of the web.
    * Anything the `*` group closes as well. Then the path is shut to every
      search engine too, the decision is site-wide policy rather than one taken
      about AI crawlers, and it belongs to the check that reads the `*` group.
    """
    if not names_agent(parsed, agent):
        return []
    if blocks_entire_site(parsed, agent) or is_disallowed(parsed, agent, "/"):
        return []
    return [rule for rule in substantive_disallows(parsed, agent)
            if rule not in ("/", "/*")
            and not is_disallowed(parsed, "*", _sample_path(rule))]


def path_of(url):
    parts = urlparse(url)
    return (parts.path or "/") + (("?" + parts.query) if parts.query else "")
