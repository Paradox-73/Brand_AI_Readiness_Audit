#!/usr/bin/env python3
"""crawl-access-audit: is the crawler let in at all? (mechanism A)

Reads snapshot.json, writes findings JSON. Makes at most 10 extra read-only
requests: one bot-user-agent comparison fetch (plus one confirmation) and up to
eight HEAD probes of sitemap URLs the crawl did not already visit.

Usage:
    python check.py --snapshot snapshot.json --out crawl-access-audit.findings.json
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                                "audit-orchestrator", "scripts"))

from audit_common import (  # noqa: E402
    CONTENT_TYPES, USER_AGENT, FetchError, Fetcher, SkillResult, load_snapshot,
    pages_of, pct, sample, strip_www,
)
from robots_parser import (  # noqa: E402
    blocks_entire_site, group_for, is_disallowed, substantive_disallows,
)

SKILL = "crawl-access-audit"

# Crawlers that fetch pages in order to build or cite an answer. A block here
# removes the brand from answers directly.
ANSWER_CRAWLERS = (
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-User",
    "Claude-SearchBot", "anthropic-ai", "PerplexityBot", "Perplexity-User",
    "Applebot", "Amazonbot", "Bytespider", "Meta-ExternalAgent", "cohere-ai",
    "DuckAssistBot", "MistralAI-User", "YouBot",
)

# Corpus collectors. Blocking these is a rights decision about training data,
# not a discoverability defect, so it is reported as info and never inflates
# the severity counts.
TRAINING_CRAWLERS = (
    "Google-Extended", "Applebot-Extended", "CCBot", "omgilibot", "Diffbot",
    "Timpibot", "Webzio-Extended", "PanguBot", "ImagesiftBot",
    "Meta-ExternalFetcher",
)

MAX_EXTRA_REQUESTS = 10
SITEMAP_PROBE_LIMIT = 8


def run(snapshot, allow_network=True):
    result = SkillResult(SKILL)
    robots = snapshot.get("robots") or {}
    origin = snapshot["origin"]
    pages = snapshot.get("pages") or []
    ok_pages = pages_of(snapshot)
    crawl_info = snapshot.get("crawl") or {}

    fetcher = None
    if allow_network:
        try:
            fetcher = Fetcher(max_requests=MAX_EXTRA_REQUESTS)
        except FetchError:
            fetcher = None

    _check_robots_reachable(result, robots)
    _check_robots_blocks(result, robots, origin)
    _check_sitemaps(result, snapshot, fetcher)
    _check_bot_manager(result, snapshot, robots, fetcher, allow_network)
    _check_status_and_indexability(result, snapshot, pages, ok_pages)
    _check_transport_and_hosts(result, snapshot, ok_pages)

    result.check("llms-txt-presence")
    result.signal("llms_txt_present", bool((snapshot.get("llms_txt") or {}).get("present")))
    result.signal("robots_present", robots.get("status") == 200)
    result.signal("sitemap_present", any(s.get("status") == 200 for s in snapshot.get("sitemaps") or []))
    result.signal("pages_crawled", crawl_info.get("pages_crawled", 0))

    if fetcher is not None:
        result.extra_requests_made = fetcher.count
    return result


# --------------------------------------------------------------------------

def _check_robots_reachable(result, robots):
    result.check("robots-txt-reachable")
    status = robots.get("status")

    if status is None:
        result.add(
            id_hint="robots-txt-unreachable",
            title="robots.txt could not be fetched",
            severity="medium", confidence="medium",
            evidence="Request to {} failed: {}. Crawlers that cannot read robots.txt "
                     "often fall back to conservative behaviour.".format(
                         robots.get("url"), robots.get("fetch_error") or "no response"),
            mechanism="A", root_cause="robots-block",
            summary="Make robots.txt return a 200 with plain text, or a clean 404.",
            how_to_fix=[
                "Request {} and confirm it returns HTTP 200 with Content-Type text/plain.".format(robots.get("url")),
                "If the file does not exist, make sure the server returns 404 rather than timing out.",
                "Check that a firewall or CDN rule is not dropping requests to /robots.txt.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: a crawler that cannot resolve robots.txt cannot "
                      "establish it is permitted to fetch, and several major crawlers "
                      "treat an unreachable robots.txt as a signal to back off.",
        )
        return

    if 500 <= status < 600:
        # This is the non-obvious one: a 5xx on robots.txt is treated by
        # Google (and others) as "disallow everything" for up to 30 days.
        result.add(
            id_hint="robots-txt-server-error",
            title="robots.txt returns a server error, which major crawlers read as disallow-all",
            severity="high", confidence="high",
            evidence="{} returned HTTP {}. Google treats a persistent 5xx on robots.txt as a "
                     "full disallow; other crawlers behave similarly.".format(robots.get("url"), status),
            mechanism="A", root_cause="robots-block",
            summary="Fix the server error on /robots.txt, or remove the route so it returns a clean 404.",
            how_to_fix=[
                "Reproduce the error by requesting /robots.txt directly and reading the server log.",
                "If robots.txt is generated by the application, add a static fallback file so a "
                "runtime failure cannot take it down.",
                "A clean 404 is safe and means 'no restrictions'; a 5xx is not.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: an unresolvable robots.txt fails the first gate for "
                      "every crawler, whatever the rest of the site looks like.",
        )
        return

    if status != 200:
        result.skip("robots-txt-rules",
                    "robots.txt returned HTTP {}; no rules to evaluate, which means nothing "
                    "is disallowed. Absence of robots.txt is not a defect.".format(status))
        return

    if robots.get("errors"):
        result.add(
            id_hint="robots-txt-parse-errors",
            title="robots.txt contains lines crawlers will ignore",
            severity="low", confidence="high",
            evidence="{} problem line(s): {}".format(
                len(robots["errors"]), "; ".join(robots["errors"][:3])),
            mechanism="A", root_cause="robots-block",
            summary="Correct the malformed robots.txt lines so the intended rules actually apply.",
            how_to_fix=[
                "Open robots.txt and fix the lines listed in the evidence.",
                "Every Allow/Disallow must sit under a User-agent line; rules above the first "
                "User-agent apply to nobody.",
                "Re-test with a robots.txt tester after the change.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: rules that do not parse silently do nothing, so the "
                      "access policy the site believes it has is not the one crawlers see.",
        )


def _check_robots_blocks(result, robots, origin):
    result.check("robots-blocks-all-crawlers")
    result.check("robots-blocks-ai-answer-crawlers")
    result.check("robots-blocks-ai-training-crawlers")
    result.check("robots-blocks-content-paths")

    if robots.get("status") != 200 or not robots.get("groups"):
        for name in ("robots-blocks-all-crawlers", "robots-blocks-ai-answer-crawlers",
                     "robots-blocks-ai-training-crawlers", "robots-blocks-content-paths"):
            result.skip(name, "no parsable robots.txt rules on this site, so nothing is disallowed")
        result.signal("blocked_answer_crawlers", [])
        return

    if blocks_entire_site(robots, "*"):
        result.add(
            id_hint="robots-blocks-all-crawlers",
            title="robots.txt disallows every crawler from the entire site",
            severity="critical", confidence="high",
            evidence="The `User-agent: *` group contains `Disallow: /` with no Allow exception. "
                     "Every compliant crawler, including every AI answer engine, will fetch nothing.",
            mechanism="A", root_cause="robots-block",
            summary="Remove the site-wide `Disallow: /` from the `User-agent: *` group.",
            how_to_fix=[
                "Open /robots.txt and delete the `Disallow: /` line under `User-agent: *`.",
                "Replace it with targeted rules for the areas you actually want private, "
                "for example `Disallow: /cart` and `Disallow: /account`.",
                "If this rule was left over from a staging environment, check the deployment "
                "pipeline is not shipping the staging robots.txt to production.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A, gate 1: a crawler that is not let in never reaches the "
                      "reading or extraction stages. Nothing else on the site can compensate.",
            snippet="User-agent: *\nAllow: /\nDisallow: /cart\nDisallow: /account\n\n"
                    "Sitemap: {}/sitemap.xml".format(origin.rstrip("/")),
        )
        result.signal("blocked_answer_crawlers", [c for c in ANSWER_CRAWLERS])
        return

    blocked_answer = [name for name in ANSWER_CRAWLERS
                      if is_disallowed(robots, name, "/") or blocks_entire_site(robots, name)]
    blocked_training = [name for name in TRAINING_CRAWLERS
                        if is_disallowed(robots, name, "/") or blocks_entire_site(robots, name)]
    result.signal("blocked_answer_crawlers", blocked_answer)
    result.signal("blocked_training_crawlers", blocked_training)

    if blocked_answer:
        named = [name for name in blocked_answer if group_for(robots, name)
                 and name.lower() in [a for g in robots["groups"] for a in g["agents"]]]
        result.add(
            id_hint="robots-blocks-answer-crawlers",
            title="robots.txt blocks {} AI answer crawler{} from the homepage".format(
                len(blocked_answer), "" if len(blocked_answer) == 1 else "s"),
            severity="high" if len(blocked_answer) >= 2 else "medium",
            confidence="high",
            evidence="Disallowed at `/`: {}. {}".format(
                ", ".join(blocked_answer[:8]),
                "Named explicitly in robots.txt: {}.".format(", ".join(named[:8])) if named
                else "These fall under a wildcard rule rather than being named."),
            mechanism="A", root_cause="robots-block",
            summary="Allow the AI answer crawlers you want to be cited by; keep blocks only where "
                    "you have deliberately opted out.",
            how_to_fix=[
                "Decide which of these you want quoting your pages: {}.".format(", ".join(blocked_answer[:6])),
                "For each one you want, add an explicit allow group in /robots.txt (see snippet).",
                "Leave blocks in place only where the opt-out is a deliberate rights decision, "
                "and note that decision somewhere your team will find it later.",
                "Re-test with a robots.txt checker using each crawler's user-agent string.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: these crawlers fetch live pages to build cited answers. "
                      "A brand they cannot fetch cannot be quoted, however good the content is.",
            snippet="\n\n".join(
                "User-agent: {}\nAllow: /".format(name) for name in blocked_answer[:4]
            ),
        )

    if blocked_training:
        result.add(
            id_hint="robots-blocks-training-crawlers",
            title="robots.txt blocks AI training crawlers (this is often deliberate)",
            severity="info", confidence="high",
            evidence="Disallowed at `/`: {}. These collect training corpora rather than "
                     "fetching pages to answer a live question.".format(", ".join(blocked_training[:8])),
            mechanism="A", root_cause="robots-block",
            summary="Confirm the training-crawler opt-out is intentional; it does not by itself "
                    "stop the brand being cited.",
            how_to_fix=[
                "Confirm with whoever owns content rights that opting out of training corpora "
                "is the intended policy.",
                "If it is, no change is needed: answer crawlers are a separate group and are "
                "governed by their own user-agent lines.",
                "If it is not, remove these user-agent groups from /robots.txt.",
            ],
            effort="low", owner="marketing",
            rationale="Mechanism A: blocking training collection limits what a model absorbs "
                      "offline, but retrieval-time citation depends on the answer crawlers, "
                      "which are controlled separately. Reported for awareness, not as a defect.",
        )

    content_blocks = [rule for rule in substantive_disallows(robots, "*")
                      if rule not in ("/", "/*")]
    if content_blocks:
        result.add(
            id_hint="robots-blocks-content-paths",
            title="robots.txt disallows {} path(s) that look like real content".format(len(content_blocks)),
            severity="medium", confidence="medium",
            evidence="Disallowed for all crawlers: {}. Admin, cart, checkout and search paths "
                     "were excluded from this list because blocking those is normal.".format(
                         ", ".join(content_blocks[:5])),
            mechanism="A", root_cause="robots-block",
            summary="Review these disallow rules and remove any that cover pages you want quoted.",
            how_to_fix=[
                "For each path listed, open it in a browser and ask whether a customer would "
                "want that page in an answer.",
                "Remove the disallow rules covering pages you do want found.",
                "Keep rules covering duplicate, paginated or parameterised URLs.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: a disallowed path is invisible to every compliant crawler, "
                      "so the content behind it cannot be retrieved or cited.",
        )
    else:
        result.skip("robots-blocks-content-paths",
                    "the only disallowed paths are standard admin, cart, account or search routes")


def _check_sitemaps(result, snapshot, fetcher):
    result.check("sitemap-present")
    result.check("sitemap-parses")
    result.check("sitemap-lastmod-coverage")
    result.check("sitemap-urls-resolve")

    sitemaps = snapshot.get("sitemaps") or []
    reachable = [s for s in sitemaps if s.get("status") == 200 and not s.get("parse_error")]
    broken = [s for s in sitemaps if s.get("status") == 200 and s.get("parse_error")]

    if not reachable and not broken:
        result.add(
            id_hint="sitemap-missing",
            title="No XML sitemap is available",
            severity="medium", confidence="high",
            evidence="Checked {} and any Sitemap: line in robots.txt; none returned a parsable "
                     "sitemap.".format(snapshot["origin"].rstrip("/") + "/sitemap.xml"),
            mechanism="A", root_cause="sitemap-missing",
            summary="Publish /sitemap.xml listing every page you want found, and reference it from robots.txt.",
            how_to_fix=[
                "Generate a sitemap from your CMS or site builder (most have this built in).",
                "Publish it at {}/sitemap.xml.".format(snapshot["origin"].rstrip("/")),
                "Add `Sitemap: {}/sitemap.xml` as the last line of robots.txt.".format(snapshot["origin"].rstrip("/")),
                "Include a <lastmod> date on every entry and keep it accurate.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: without a sitemap a crawler only finds what it can reach by "
                      "following links, so pages more than a couple of clicks deep, or linked "
                      "only from JavaScript menus, may never be fetched.",
            snippet='<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                    '  <url><loc>{0}/</loc><lastmod>2026-01-31</lastmod></url>\n'
                    '  <url><loc>{0}/about</loc><lastmod>2026-01-31</lastmod></url>\n'
                    '</urlset>'.format(snapshot["origin"].rstrip("/")),
        )
        result.signal("sitemap_url_count", 0)
        return

    if broken:
        result.add(
            id_hint="sitemap-does-not-parse",
            title="An XML sitemap is published but does not parse",
            severity="medium", confidence="high",
            evidence="{}: {}".format(broken[0]["url"], broken[0]["parse_error"]),
            mechanism="A", root_cause="sitemap-broken",
            summary="Fix the XML so the sitemap can be read.",
            how_to_fix=[
                "Validate the file with any XML validator and fix the reported error.",
                "Check the file is served as application/xml or text/xml, not text/html.",
                "Confirm there is no HTML error page or BOM prefixed to the XML.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: a sitemap that fails to parse is worse than none, because "
                      "the site believes its pages are being advertised when they are not.",
        )

    total_urls = sum(s.get("url_count", 0) for s in reachable)
    total_lastmod = sum(s.get("lastmod_count", 0) for s in reachable)
    result.signal("sitemap_url_count", total_urls)

    if not any(s.get("referenced_in_robots") for s in sitemaps) and reachable:
        result.add(
            id_hint="sitemap-not-referenced-in-robots",
            title="The sitemap is not referenced from robots.txt",
            severity="low", confidence="high",
            evidence="{} was found at the conventional location but robots.txt contains no "
                     "`Sitemap:` line.".format(reachable[0]["url"]),
            mechanism="A", root_cause="sitemap-missing",
            summary="Add a `Sitemap:` line to robots.txt.",
            how_to_fix=[
                "Append `Sitemap: {}` to /robots.txt.".format(reachable[0]["url"]),
                "The line can go anywhere in the file and applies to all user agents.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: robots.txt is the first file a crawler reads, so a Sitemap "
                      "line is the cheapest way to hand it the full page list.",
        )

    if total_urls and total_lastmod / total_urls < 0.5:
        result.add(
            id_hint="sitemap-lastmod-sparse",
            title="Most sitemap entries carry no <lastmod> date",
            severity="low", confidence="high",
            evidence="{} of {} sitemap entries ({}%) have a <lastmod> value.".format(
                total_lastmod, total_urls, pct(total_lastmod, total_urls)),
            mechanism="D", root_cause="no-date-signal",
            summary="Emit an accurate <lastmod> for every sitemap entry.",
            how_to_fix=[
                "Configure the sitemap generator to write <lastmod> from the page's real "
                "modification date.",
                "Do not set <lastmod> to today's date on every build; a date that always "
                "changes carries no information and crawlers learn to ignore it.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism D: <lastmod> is how a crawler decides which pages are worth "
                      "re-fetching. Without it, updated pages are re-read on a slow default cycle.",
        )

    _check_sitemap_urls_resolve(result, snapshot, reachable, fetcher)


def _check_sitemap_urls_resolve(result, snapshot, reachable, fetcher):
    """Find sitemap entries that 404, preferring statuses the crawl already has."""
    known = {p["url"]: p.get("status") for p in snapshot.get("pages") or []}
    listed = []
    for record in reachable:
        for entry in record["urls"]:
            listed.append(entry["loc"])
    if not listed:
        result.skip("sitemap-urls-resolve", "no URLs listed in the sitemap")
        return

    dead = []
    checked = 0
    for url in listed:
        if url in known:
            checked += 1
            if known[url] is not None and known[url] != 200:
                dead.append((url, known[url]))

    to_probe = [u for u in sample(listed, SITEMAP_PROBE_LIMIT) if u not in known]
    if fetcher is not None:
        for url in to_probe:
            response = fetcher.try_get(url, method="HEAD")
            if response is None:
                continue
            checked += 1
            if response.status_code != 200:
                dead.append((url, response.status_code))
    elif to_probe:
        result.skip("sitemap-urls-resolve",
                    "network probes disabled; only the {} sitemap URL(s) already crawled "
                    "were checked".format(checked))

    if not checked:
        return
    if not dead:
        return

    rate = pct(len(dead), checked)
    result.add(
        id_hint="sitemap-lists-dead-urls",
        title="The sitemap lists URLs that do not return 200",
        severity="high" if rate >= 25 else "medium", confidence="high",
        evidence="{} of {} checked sitemap entries ({}%) returned a non-200 status. "
                 "Examples: {}.".format(
                     len(dead), checked, rate,
                     "; ".join("{} -> {}".format(u, s) for u, s in sorted(dead)[:5])),
        mechanism="A", root_cause="sitemap-broken",
        summary="Remove dead URLs from the sitemap, or restore the pages they point to.",
        how_to_fix=[
            "For each dead URL, decide whether the page should exist. If it should, restore it.",
            "If it should not, remove it from the sitemap and add a 301 to the closest live page.",
            "Set the sitemap to regenerate on publish so deleted pages drop out automatically.",
        ],
        effort="medium", owner="developer",
        rationale="Mechanism A: a sitemap full of dead links teaches crawlers that this site's "
                  "sitemap is unreliable, which reduces how much of it they act on.",
        affected_pages=[u for u, _ in sorted(dead)],
    )


def _check_bot_manager(result, snapshot, robots, fetcher, allow_network):
    """Compare the homepage response for our UA against one AI crawler UA.

    A WAF or bot manager that returns 403 to a crawler is invisible in
    robots.txt, so this is the only way to see it.
    """
    result.check("bot-manager-user-agent-comparison")
    home_url = snapshot["origin"].rstrip("/") + "/"
    home = next((p for p in snapshot.get("pages") or [] if p["url"] == home_url), None)

    if not allow_network or fetcher is None:
        result.skip("bot-manager-user-agent-comparison",
                    "extra network requests were disabled for this run")
        return
    if home is None or home.get("status") is None:
        result.skip("bot-manager-user-agent-comparison",
                    "the homepage was not fetched, so there is no baseline to compare against")
        return

    candidate = next(
        (name for name in ANSWER_CRAWLERS if not is_disallowed(robots, name, "/")), None)
    if candidate is None:
        result.skip("bot-manager-user-agent-comparison",
                    "robots.txt already disallows every AI answer crawler, so the robots finding "
                    "covers this and no probe request was made")
        return

    response = fetcher.try_get(home_url, user_agent=_ua_string(candidate))
    if response is None:
        result.skip("bot-manager-user-agent-comparison",
                    "the comparison request failed for a reason unrelated to the site's bot rules")
        return

    baseline = home.get("status")
    if response.status_code == baseline:
        result.signal("bot_manager_block", False)
        return

    # One confirmation before reporting: a single differing response can be a
    # rate limiter or a transient edge error.
    confirm = fetcher.try_get(home_url, user_agent=_ua_string(candidate))
    if confirm is None or confirm.status_code == baseline:
        result.skip("bot-manager-user-agent-comparison",
                    "the first probe differed but the confirmation request matched the baseline; "
                    "treated as transient rather than a bot block")
        return

    result.signal("bot_manager_block", True)
    blocking = confirm.status_code in (401, 403, 405, 406, 429) or confirm.status_code >= 500
    result.add(
        id_hint="bot-manager-blocks-ai-crawlers",
        title="A bot manager or WAF serves AI crawlers a different response from the homepage",
        severity="critical" if blocking else "medium",
        confidence="high",
        evidence="{} returned HTTP {} for user agent `{}` on two consecutive requests, but "
                 "HTTP {} for the audit user agent. robots.txt does not disallow {}.".format(
                     home_url, confirm.status_code, candidate, baseline, candidate),
        mechanism="A", root_cause="bot-manager-block",
        summary="Allow-list the AI answer crawlers in your CDN or WAF bot rules.",
        how_to_fix=[
            "Open your CDN or WAF bot-management settings (Cloudflare, Akamai, Fastly, "
            "AWS WAF and similar all have a bot category list).",
            "Add {} and the other AI answer crawlers to the allow list, or set their "
            "category to 'allow' rather than 'challenge' or 'block'.".format(candidate),
            "Verify by requesting the homepage with that user-agent string and confirming "
            "a 200 with real HTML, not a challenge page.",
            "Keep rate limiting in place; allow-listing does not mean removing throttles.",
        ],
        effort="medium", owner="developer",
        rationale="Mechanism A: robots.txt permission is irrelevant if the edge returns a "
                  "challenge page. The crawler receives no content, so the brand cannot appear "
                  "in an answer even though the site looks open on paper.",
        affected_pages=[home_url],
    )


def _ua_string(name):
    """The probe identifies itself as both the crawler and this audit tool."""
    return "{} ({}; comparison probe)".format(name, USER_AGENT)


def _check_status_and_indexability(result, snapshot, pages, ok_pages):
    result.check("homepage-reachable")
    result.check("non-200-rate")
    result.check("redirect-chain-length")
    result.check("noindex-on-content-pages")
    result.check("canonical-targets")

    home_url = snapshot["origin"].rstrip("/") + "/"
    home = next((p for p in pages if p["url"] == home_url), None)
    if home is not None and home.get("status") != 200:
        status = home.get("status")
        # A 403 or 429 to an identified, rate-limited, robots-respecting crawler
        # is a bot rule, not an outage, and the fix is completely different.
        looks_like_bot_block = status in (401, 403, 405, 406, 429)
        result.add(
            id_hint="homepage-not-reachable",
            title="The homepage refuses this crawler" if looks_like_bot_block
                  else "The homepage does not return HTTP 200",
            severity="critical", confidence="high",
            evidence="{} returned {}.{}".format(
                home_url, status or "no response ({})".format(home.get("error")),
                " The request identified itself as an audit crawler, respected robots.txt and "
                "was rate limited, so this is a bot-management rule rather than an outage."
                if looks_like_bot_block else ""),
            mechanism="A", root_cause="non-200",
            summary="Allow identified crawlers to fetch the homepage."
                    if looks_like_bot_block else "Restore a 200 response on the homepage.",
            how_to_fix=[
                "Check your CDN or WAF bot rules: a blanket block on unrecognised user agents "
                "also blocks every AI answer crawler." if looks_like_bot_block
                else "Request the homepage and read the server or CDN log for the failing request.",
                "Allow-list the AI answer crawlers by user agent, keeping rate limits in place."
                if looks_like_bot_block
                else "If the homepage has moved, return a 301 to the new location rather than an error.",
                "Verify by requesting the homepage with each crawler's user-agent string and "
                "confirming a 200 with real HTML rather than a challenge page."
                if looks_like_bot_block
                else "Check whether a CDN rule or origin health check is failing.",
            ],
            effort="high", owner="developer",
            rationale="Mechanism A: the homepage is the entry point almost every crawler and "
                      "assistant tries first. If it fails, most never try anything else.",
            affected_pages=[home_url],
        )

    fetched = [p for p in pages if p.get("status") is not None]
    bad = [p for p in fetched if p["status"] != 200]
    if fetched and bad:
        rate = pct(len(bad), len(fetched))
        if rate >= 10:
            counts = Counter(p["status"] for p in bad)
            result.add(
                id_hint="high-non-200-rate",
                title="{}% of crawled pages do not return HTTP 200".format(rate),
                severity="high" if rate >= 25 else "medium", confidence="high",
                evidence="{} of {} fetched URLs returned a non-200 status ({}). Examples: {}.".format(
                    len(bad), len(fetched),
                    ", ".join("{}x{}".format(v, k) for k, v in sorted(counts.items())),
                    ", ".join(sample([p["url"] for p in bad], 5))),
                mechanism="A", root_cause="non-200",
                summary="Fix or redirect the URLs that are still linked but no longer resolve.",
                how_to_fix=[
                    "Export the failing URLs and check each against the current site structure.",
                    "301 anything that moved; remove links to anything that is genuinely gone.",
                    "Return 410 rather than 404 for pages deliberately retired, so crawlers stop "
                    "re-requesting them.",
                ],
                effort="medium", owner="developer",
                rationale="Mechanism A: every failing URL is a page that cannot be read or cited, "
                          "and a high failure rate reduces how deeply crawlers explore the site.",
                affected_pages=[p["url"] for p in bad],
            )
        else:
            result.skip("non-200-rate",
                        "{}% of fetched URLs were non-200, below the 10% threshold where this "
                        "indicates a systemic problem".format(rate))
    elif fetched:
        result.skip("non-200-rate", "every fetched URL returned HTTP 200")

    long_chains = [p for p in pages if len(p.get("redirect_chain") or []) > 2]
    if long_chains:
        result.add(
            id_hint="long-redirect-chains",
            title="Some URLs redirect more than twice before resolving",
            severity="low", confidence="high",
            evidence="{} crawled URL(s) had a redirect chain longer than 2 hops. Examples: {}.".format(
                len(long_chains), ", ".join(sample([p["url"] for p in long_chains], 5))),
            mechanism="A", root_cause="redirect-chain",
            summary="Collapse redirect chains so each old URL points straight at the final page.",
            how_to_fix=[
                "List the chains and rewrite each rule to target the final destination directly.",
                "Watch for the common stack: http to https, then non-www to www, then a "
                "trailing-slash rule. Combine these into one rule.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: some crawlers stop following after a small number of hops, "
                      "and every hop adds latency to a fetch an assistant is doing live.",
            affected_pages=[p["url"] for p in long_chains],
        )

    noindexed = []
    for page in ok_pages:
        if page.get("page_type") not in CONTENT_TYPES:
            continue
        directives = "{} {}".format(page.get("meta_robots", ""), page.get("x_robots_tag", "")).lower()
        if "noindex" in directives:
            noindexed.append(page)
    if noindexed:
        result.add(
            id_hint="noindex-on-content-pages",
            title="{} content page(s) carry a noindex directive".format(len(noindexed)),
            severity="high", confidence="high",
            evidence="Pages with `noindex` in a meta robots tag or X-Robots-Tag header: {}.".format(
                ", ".join(sample([p["url"] for p in noindexed], 5))),
            mechanism="A", root_cause="noindex",
            summary="Remove `noindex` from pages you want found and quoted.",
            how_to_fix=[
                "For each page listed, check the <meta name=\"robots\"> tag and the "
                "X-Robots-Tag response header; the directive can come from either.",
                "Remove `noindex` where the page should be public.",
                "If the directive is left over from a staging environment, check the CMS "
                "environment settings rather than editing each page.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: `noindex` tells a crawler to read the page and then discard "
                      "it. The content is fetched and then thrown away, so it can never be cited.",
            affected_pages=[p["url"] for p in noindexed],
        )
    else:
        result.skip("noindex-on-content-pages", "no crawled content page carries a noindex directive")

    _check_canonicals(result, snapshot, ok_pages)


def _check_canonicals(result, snapshot, ok_pages):
    origin_host = strip_www(urlparse(snapshot["origin"]).netloc.lower())
    known_status = {p["url"]: p.get("status") for p in snapshot.get("pages") or []}
    off_domain, broken = [], []

    with_canonical = [p for p in ok_pages if p.get("canonical")]
    if not with_canonical:
        result.skip("canonical-targets", "no crawled page declares a canonical URL")
        return

    for page in with_canonical:
        canonical = page["canonical"]
        host = strip_www(urlparse(canonical).netloc.lower())
        if host and host != origin_host:
            off_domain.append((page["url"], canonical))
        elif canonical in known_status and known_status[canonical] not in (None, 200):
            broken.append((page["url"], canonical, known_status[canonical]))

    if off_domain:
        result.add(
            id_hint="canonical-points-off-domain",
            title="{} page(s) declare a canonical URL on a different domain".format(len(off_domain)),
            severity="high", confidence="high",
            evidence="Examples: {}.".format(
                "; ".join("{} -> {}".format(a, b) for a, b in sorted(off_domain)[:5])),
            mechanism="A", root_cause="canonical-broken",
            summary="Point each canonical tag at the page's own URL on this domain.",
            how_to_fix=[
                "For each page listed, set <link rel=\"canonical\"> to that page's own absolute URL.",
                "A cross-domain canonical is only correct when the content genuinely is a copy "
                "of a page you own elsewhere and you want the credit to go there.",
                "Check whether a template or plugin is hard-coding a domain from a previous site.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: a canonical tag tells consumers 'the real version is over "
                      "there'. Pointing off-domain hands attribution for this content to another site.",
            affected_pages=[a for a, _ in sorted(off_domain)],
        )

    if broken:
        result.add(
            id_hint="canonical-points-to-broken-url",
            title="{} page(s) declare a canonical URL that does not return 200".format(len(broken)),
            severity="medium", confidence="high",
            evidence="Examples: {}.".format(
                "; ".join("{} -> {} ({})".format(a, b, s) for a, b, s in sorted(broken)[:5])),
            mechanism="A", root_cause="canonical-broken",
            summary="Fix each canonical URL so it resolves to a live page.",
            how_to_fix=[
                "Request each canonical target and confirm it returns 200.",
                "Correct the tag, or restore the target page.",
                "Watch for trailing-slash and http/https mismatches, which are the usual cause.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism A: a canonical pointing at a dead URL leaves consumers unsure "
                      "which version of the page is authoritative, so some drop both.",
            affected_pages=[a for a, _, _ in sorted(broken)],
        )

    if not off_domain and not broken:
        result.skip("canonical-targets",
                    "all {} declared canonical URLs are on this domain and resolve".format(
                        len(with_canonical)))


def _check_transport_and_hosts(result, snapshot, ok_pages):
    result.check("https-transport")
    result.check("canonical-host-consistency")

    host = urlparse(snapshot["origin"]).hostname or ""
    # A local or IP-addressed origin is a development server, not a published
    # site. Demanding a certificate for 127.0.0.1 would be noise.
    is_local = host in ("localhost", "127.0.0.1", "::1") or re.match(r"^\d+\.\d+\.\d+\.\d+$", host)

    if is_local:
        result.skip("https-transport",
                    "the audited origin is a local or IP-addressed host, so transport security "
                    "is a deployment concern rather than a site defect")
    elif snapshot["origin"].startswith("http://"):
        result.add(
            id_hint="site-served-over-http",
            title="The site is served over plain HTTP",
            severity="medium", confidence="high",
            evidence="The audited origin is {}. No HTTPS redirect was observed.".format(snapshot["origin"]),
            mechanism="A", root_cause="insecure-transport",
            summary="Serve the whole site over HTTPS and redirect HTTP to it.",
            how_to_fix=[
                "Obtain a certificate (free from Let's Encrypt, or included with most hosts).",
                "Add a permanent redirect from http:// to https:// for every path.",
                "Update internal links, canonical tags and the sitemap to the https:// form.",
            ],
            effort="medium", owner="developer",
            rationale="Mechanism A: browsers warn on HTTP pages and several crawlers deprioritise "
                      "or refuse them, which suppresses the site before content is ever assessed.",
        )
    else:
        result.skip("https-transport", "the site is served over HTTPS")

    hosts = Counter()
    for page in ok_pages:
        canonical = page.get("canonical")
        if canonical:
            host = urlparse(canonical).netloc.lower()
            if host:
                hosts[host] += 1
    if len(hosts) > 1:
        result.add(
            id_hint="mixed-canonical-hosts",
            title="Canonical URLs are split across more than one hostname",
            severity="medium", confidence="high",
            evidence="Canonical hostnames seen: {}.".format(
                ", ".join("{} ({} pages)".format(h, n) for h, n in sorted(hosts.items()))),
            mechanism="A", root_cause="host-inconsistency",
            summary="Pick one hostname (www or bare) and use it in every canonical tag.",
            how_to_fix=[
                "Choose the hostname you want to be the real one.",
                "Redirect the other permanently to it at the server or CDN level.",
                "Regenerate canonical tags, internal links and the sitemap using that hostname.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism D: two hostnames serving the same content look like two sources "
                      "that half-agree, splitting the signals that would otherwise reinforce one "
                      "authoritative version.",
        )
    else:
        result.skip("canonical-host-consistency",
                    "every canonical URL uses a single hostname" if hosts
                    else "no canonical URLs were declared, so there is no host split to assess")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-network", action="store_true",
                        help="skip the bot-user-agent probe and sitemap HEAD checks")
    args = parser.parse_args(argv)

    result = run(load_snapshot(args.snapshot), allow_network=not args.no_network)
    result.write(args.out)
    print("{}: {} finding(s), {} extra request(s)".format(
        SKILL, len(result.findings), result.extra_requests_made), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
