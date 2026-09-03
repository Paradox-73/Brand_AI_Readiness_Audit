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

    for lineno, raw in enumerate((text or "").splitlines(), start=1):
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
    best = None
    best_len = -1
    wildcard = None
    for grp in parsed.get("groups", []):
        for name in grp["agents"]:
            if name == "*":
                if wildcard is None:
                    wildcard = grp
                continue
            if agent.startswith(name) and len(name) > best_len:
                best, best_len = grp, len(name)
    return best if best is not None else wildcard


def _rule_matches(pattern, path):
    """REP path matching, including `*` wildcards and a `$` end anchor."""
    if pattern == "":
        return False
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    regex = "^" + regex + ("$" if anchored else "")
    return re.match(regex, path) is not None


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
    # A bare `Allow: /something` reopens part of the site, so it is not a
    # site-wide block.
    return not [r for r in grp.get("allow", []) if r.strip() not in ("", "/")]


def agents_named(parsed):
    """Every user-agent token that appears in the file."""
    names = set()
    for grp in parsed.get("groups", []):
        names.update(grp["agents"])
    return sorted(names)


# Paths whose disallow is normal hygiene, never an AI-discoverability defect.
_BENIGN_DISALLOW = re.compile(
    r"^/(wp-admin|admin|administrator|cgi-bin|cart|checkout|basket|account|"
    r"login|signin|sign-in|logout|register|signup|search|\?|.*\?|tmp|temp|"
    r"private|internal|api|graphql|xmlrpc|wp-includes|wp-json|feed|print|"
    r"thank-you|order|my-account|customer|user|profile|preview|draft|"
    # Authentication and invitation routes. A site is right to keep these out
    # of an index, and reporting `/oauth`, `/confirm`, `/notifications` and
    # `/invites/` as "paths that look like real content" put three wrong items
    # at the top of a real report. The list already knew `/login` and
    # `/signup`; it had simply never been extended to the rest of the category.
    r"oauth|auth|sso|saml|callback|confirm|verify|verification|activate|"
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
    r"app_themes|app_data|app_code|app_start|controls|_next|_nuxt|_astro|"
    r"_app|_layouts|web-inf|meta-inf|bin|obj|\.git|\.svn|cgi|"
    # Error, maintenance and utility pages. A site is right to keep these
    # out of an index, and none of them is content anyone would quote.
    r"pageerror|error|errors|404|500|not-found|notfound|maintenance|"
    r"addtocart|internationaladdress|"
    # Shopify's own boilerplate. `/services` and `/recommendations` are its
    # internal endpoints, and a shop's robots.txt says so in a comment
    # directly above them - a comment robots parsers drop. Reported as "paths
    # that look like real content", `/services` was the number one thing to
    # fix on a nail-care shop's report, and it returns 404.
    r"services|recommendations|cdn|_shopify|shopify)",
    re.I,
)


# Tokens that make a rule plumbing wherever they appear, not only at the start.
# Real robots.txt files are full of rules like `/*/print$`, `/*?*add-to-cart=`
# and `/collections/*+*`, which an anchored match treated as real content.
_BENIGN_ANYWHERE = re.compile(
    r"(?:^|[/*?&=_-])(?:print|preview|draft|cart|checkout|basket|login|signin|"
    r"logout|register|signup|account|admin|search|filter|sort|sortby|orderby|"
    r"session|sessionid|utm_|replytocom|add-to-cart|wishlist|compare|currency|"
    r"variant|cgi-bin|feed|rss|atom|json|xml|api|graphql|amp|oauth|sso|saml|"
    r"callback|confirm|verify|activate|invite|invitation|webhook)\b", re.I)


# A path made only of digits is an internal identifier, not a page anyone
# reads. One shop's robots.txt disallows `/26657478`; it returns 404, and it
# was reported alongside a real content path as something to unblock.
_NUMERIC_PATH_RE = re.compile(r"^/\d+/?$")

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


def benign_disallow(rule):
    """True for the admin/cart/search/parameter paths every site blocks on purpose."""
    rule = (rule or "").strip()
    if not rule or rule in ("/", "/*"):
        return False
    if _NUMERIC_PATH_RE.match(rule):
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
    """Disallow rules for `agent` that block real content, not just plumbing."""
    grp = group_for(parsed, agent)
    if grp is None:
        return []
    return sorted({r.strip() for r in grp.get("disallow", []) if r.strip() and not benign_disallow(r)})


def path_of(url):
    parts = urlparse(url)
    return (parts.path or "/") + (("?" + parts.query) if parts.query else "")
