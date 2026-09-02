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
import time
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
    CHALLENGE_TEXT_CEILING, CONTENT_TYPES, example_urls,
    explain_fetch_error, Fetcher, FetchError, link_verdict, load_snapshot,
    looks_like_soft_404, pages_of, pct, plural, REFUSED_STATUS, sample,
    SkillResult, strip_www, USER_AGENT
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

# Demand, counted rather than assumed: the sitemap probe asks for up to 8, the
# bot-manager comparison for 2, the canonical probe for 3. Thirteen against a
# ceiling of ten meant the canonical probe was starved by running last, so on
# any site with a sitemap it silently never ran. The ceiling now covers what
# the checks actually ask for, with three spare.
MAX_EXTRA_REQUESTS = 16
# Canonical targets the crawl never reached. Capped low: this is a
# diagnostic, not a link checker, and the site did not ask to be crawled
# harder than the budget already allows.
# Measured across 13 real sites: the median site declares zero canonical
# targets the crawl did not already fetch, and the worst declared one. Three
# has never been the binding constraint, and raising it would buy nothing.
CANONICAL_PROBE_LIMIT = 3
SITEMAP_PROBE_LIMIT = 8


def _deadline(time_budget):
    """Turn a remaining-seconds allowance into a monotonic deadline.

    The orchestrator knows how much of the five-minute ceiling the crawl
    already spent; this check does not, so it is told what is left rather
    than assuming a fixed slice. `None` means no ceiling, which is what a
    direct command-line run of this one skill gets.
    """
    if time_budget is None:
        return None
    return time.monotonic() + max(0.0, float(time_budget))

def run(snapshot, allow_network=True, time_budget=None):
    result = SkillResult(SKILL)
    robots = snapshot.get("robots") or {}
    origin = snapshot["origin"]
    pages = snapshot.get("pages") or []
    ok_pages = pages_of(snapshot)
    crawl_info = snapshot.get("crawl") or {}

    fetcher = None
    if allow_network:
        try:
            fetcher = Fetcher(max_requests=MAX_EXTRA_REQUESTS,
                              deadline=_deadline(time_budget))
        except FetchError:
            fetcher = None

    # When the host itself never answered, everything downstream is a
    # restatement of that one fact. The entrypoint's SKILL.md says so - "stop
    # and report one critical finding" - and this did not: auditing a domain
    # that does not resolve produced four findings, including "No XML sitemap
    # is available" and "robots.txt could not be fetched" about a hostname with
    # no DNS record, one of them carrying an effort estimate of several days of
    # development time to fix a typo.
    home_url = origin.rstrip("/") + "/"
    home = next((p for p in pages if p["url"] == home_url), None)
    if home is not None and home.get("status") is None and not pages_of(snapshot):
        _check_status_and_indexability(result, snapshot, pages, ok_pages, fetcher)
        for name in ("robots-txt-reachable", "robots-blocks-all-crawlers",
                     "robots-blocks-ai-answer-crawlers", "sitemap-present",
                     "sitemap-parses", "sitemap-urls-resolve", "canonical-targets",
                     "bot-manager-user-agent-comparison", "https-transport",
                     "bot-manager-challenge-page", "llms-txt-presence"):
            result.skip(name, "the host did not respond at all, so nothing else about it "
                              "could be examined; fixing that is the only action available")
        result.signal("host_unreachable", True)
        return result

    _check_challenge_pages(result, snapshot)
    _check_robots_reachable(result, robots)
    _check_robots_blocks(result, robots, origin)
    _check_sitemaps(result, snapshot, fetcher)
    _check_bot_manager(result, snapshot, robots, fetcher, allow_network)
    _check_status_and_indexability(result, snapshot, pages, ok_pages, fetcher)
    _check_transport_and_hosts(result, snapshot, ok_pages)

    result.check("llms-txt-presence")
    llms_txt = snapshot.get("llms_txt") or {}
    # Same rule as the sitemap above. This was recorded as "ran and found
    # nothing wrong" on a site whose llms.txt request came back 429, which
    # reads to a reporter as a pass on a question that was never answered.
    if llms_txt.get("status") in REFUSED_STATUS:
        result.skip("llms-txt-presence",
                    "the request for /llms.txt was refused by the site's edge (HTTP {}), so "
                    "whether one is published could not be determined".format(
                        llms_txt.get("status")))
    result.signal("llms_txt_present", bool(llms_txt.get("present")))
    result.signal("robots_present", robots.get("status") == 200)
    result.signal("sitemap_present", any(s.get("status") == 200 for s in snapshot.get("sitemaps") or []))
    result.signal("pages_crawled", crawl_info.get("pages_crawled", 0))

    if fetcher is not None:
        result.extra_requests_made = fetcher.count
    return result


# --------------------------------------------------------------------------

def _check_challenge_pages(result, snapshot):
    """Did a bot manager serve a verification page instead of the content?

    A bot manager that answers 403 is easy - the status says so. This is the
    one that answers 2xx. Measured on real commercial homepages, one returns
    HTTP 202 with zero characters of readable text and another returns 200 with
    thirty-two; both are verification pages carrying a vendor's challenge
    script. Without this the audit reads them as ordinary pages and reports a
    JavaScript shell, missing structured data, no quotable fact and thin
    content - four confident findings about a site that is fine.
    """
    result.check("bot-manager-challenge-page")
    challenged = [p for p in snapshot.get("pages") or [] if p.get("challenge")]
    if not challenged:
        result.skip("bot-manager-challenge-page",
                    "no page answered with a bot-manager verification page in place of its "
                    "content")
        return

    fetched = [p for p in snapshot.get("pages") or [] if p.get("status") is not None]
    vendors = sorted({p["challenge"] for p in challenged})
    share = pct(len(challenged), len(fetched)) if fetched else 100
    result.signal("challenge_pages", len(challenged))
    result.signal("challenge_vendors", vendors)

    result.add(
        id_hint="bot-manager-serves-a-challenge-page",
        title="{} is served to crawlers instead of the page itself".format(
            " and ".join(vendors)),
        # A wall across the whole site makes every other check meaningless; a
        # wall on part of it hides that part and no more.
        severity="critical" if share >= 50 else "high",
        confidence="high",
        # The status is read from the records, not asserted. It said "a 2xx
        # response" whatever the pages actually returned, and a bank's report
        # then described its 403s as 2xx - contradicted by the next finding in
        # the same report, which quoted the real status. An evidence sentence
        # that states a value it did not read is a fabrication however small.
        evidence="{} of {} fetched URLs ({}%) returned a verification page rather than "
                 "content: HTTP {} carrying {} challenge scaffolding and under {} "
                 "characters of readable text. Examples: {}. Those pages are excluded from "
                 "every content check in this report, because they describe the crawler's "
                 "reception and not the site.".format(
                     len(challenged), len(fetched), share,
                     ", ".join(str(s) for s in sorted(
                         {p.get("status") for p in challenged if p.get("status")})) or "no status",
                     " and ".join(vendors), CHALLENGE_TEXT_CEILING,
                     "; ".join(p["url"] for p in sorted(challenged, key=lambda x: x["url"])[:3])),
        mechanism="A", root_cause="bot-manager-block",
        summary="Allow the AI answer crawlers through {} so they receive the page, not the "
                "challenge.".format(" and ".join(vendors)),
        how_to_fix=[
            "Open the bot-management rules in {} and find the rule that serves a JavaScript "
            "challenge to unrecognised user agents.".format(" and ".join(vendors)),
            "Add an allow rule for the AI answer crawlers by user agent - OAI-SearchBot, "
            "PerplexityBot, ClaudeBot and Google-Extended - keeping your rate limits in place. "
            "These fetch a page to answer a question; they are not the traffic the rule is for.",
            "Verify with `curl -A OAI-SearchBot https://your-site/` and confirm you get real "
            "HTML back rather than a challenge page.",
        ],
        effort="medium", owner="developer",
        rationale="An assistant fetching this page receives a verification screen. "
                  "It cannot solve the challenge, so it reads nothing and cites nothing. To the "
                  "site's analytics this looks like a bot being correctly turned away; to every "
                  "AI answer engine it looks like a site with no content.",
        affected_pages=[p["url"] for p in sorted(challenged, key=lambda x: x["url"])],
    )


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
            rationale="A crawler that cannot resolve robots.txt cannot "
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
            rationale="An unresolvable robots.txt fails the first gate for "
                      "every crawler, whatever the rest of the site looks like.",
        )
        return

    if status in REFUSED_STATUS:
        # A refusal is not an answer. A university's robots.txt returned 403 to
        # this crawler and 200 to curl, and the report said five times over
        # that there were "no rules to evaluate, which means nothing is
        # disallowed" - about a file containing `Crawl-delay: 20` and several
        # real disallow rules. The sitemap check in the same report got this
        # right and said "unchecked"; this one contradicted it.
        result.skip("robots-txt-rules",
                    "the request for robots.txt was refused by the site's edge (HTTP {}), so "
                    "its rules could not be read. This is not evidence that the file is absent "
                    "or that nothing is disallowed - it is evidence that this crawler was not "
                    "allowed to look".format(status))
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
            rationale="Rules that do not parse silently do nothing, so the "
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
            rationale="A crawler that is not let in never reaches the "
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
            rationale="These crawlers fetch live pages to build cited answers. "
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
            rationale="Blocking training collection limits what a model absorbs "
                      "offline, but retrieval-time citation depends on the answer crawlers, "
                      "which are controlled separately. Reported for awareness, not as a defect.",
        )

    content_blocks = [rule for rule in substantive_disallows(robots, "*")
                      if rule not in ("/", "/*")]
    # Real robots.txt files disallow dozens of paths for good reasons, and
    # listing them all fired this on 86% of real sites. Only report when a
    # whole top-level section is closed - a single-segment path with no
    # wildcard, which is what blocking real content actually looks like.
    content_blocks = [r for r in content_blocks
                      if "*" not in r and r.strip("/").count("/") == 0 and len(r.strip("/")) > 2]
    if len(content_blocks) >= 2:
        result.add(
            id_hint="robots-blocks-content-paths",
            title="robots.txt disallows {} that look like real content".format(
                plural(len(content_blocks), "path")),
            severity="medium", confidence="medium",
            # The old wording named only one of the three exclusions, so a
            # reader checking the robots.txt against the report found rules
            # missing from the list for a reason the report had not given.
            evidence="Disallowed for all crawlers: {}. Three kinds of rule were excluded "
                     "before this list was drawn up: admin, cart, checkout, search, asset "
                     "and machine-file paths, because blocking those is normal; any rule "
                     "containing a wildcard, because it usually filters query strings rather "
                     "than blocking a section; and any path more than one segment deep. The "
                     "paths above were not fetched - robots.txt disallows them and this audit "
                     "respects that - so what is behind them is unverified.".format(
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
            rationale="A disallowed path is invisible to every compliant crawler, "
                      "so the content behind it cannot be retrieved or cited.",
        )
    else:
        result.skip("robots-blocks-content-paths",
                    "the only disallowed paths are standard admin, cart, account or search routes")


def _check_sitemaps(result, snapshot, fetcher):
    result.check("sitemap-present")
    result.check("sitemap-parses")
    # `sitemap-lastmod-coverage` is deliberately NOT registered here.
    # freshness-corroboration-audit owns date signals and reports it, and
    # registering it in both places put one check under two skills: the report
    # listed 71 entries for 70 distinct checks, so the README's count and the
    # report's count disagreed and both looked wrong.
    result.check("sitemap-urls-resolve")

    sitemaps = snapshot.get("sitemaps") or []
    reachable = [s for s in sitemaps if s.get("status") == 200 and not s.get("parse_error")]
    broken = [s for s in sitemaps if s.get("status") == 200 and s.get("parse_error")]
    # Sitemaps the crawl fetched but could not read to the end. A limit this
    # audit imposes on itself is not a defect in the site, so they are neither
    # "reachable" nor "broken" - they are unchecked, and said to be.
    truncated = [s for s in sitemaps if s.get("truncated")]
    if truncated and not reachable and not broken:
        result.skip("sitemap-present",
                    "a sitemap is published at {} and was fetched, but {}".format(
                        truncated[0]["url"], truncated[0].get("unchecked_reason")
                        or "it could not be read in full"))
        result.skip("sitemap-parses", "the sitemap was not read in full, so it was not parsed")
        result.skip("sitemap-urls-resolve",
                    "the sitemap was not read in full, so its URLs were not sampled")
        result.signal("sitemap_truncated", True)
        return

    # A request the edge refused is not an answer about whether the file
    # exists. A museum's sitemap.xml returned 429 along with everything else on
    # the site, and the report said "No XML sitemap is available" as settled
    # fact - in the same document where it correctly hedged the identically
    # blocked robots.txt with "absence of robots.txt is not a defect". One
    # blocked request cannot be evidence of absence in one paragraph and
    # evidence of nothing in another.
    refused = [s for s in sitemaps if s.get("status") in REFUSED_STATUS]
    if not reachable and not broken and refused:
        result.skip("sitemap-present",
                    "every sitemap request was refused by the site's edge (HTTP {}), so whether "
                    "a sitemap exists could not be determined. It is reported as unchecked "
                    "rather than as missing".format(
                        ", ".join(str(s) for s in sorted({s["status"] for s in refused}))))
        result.skip("sitemap-parses", "no sitemap could be fetched to parse")
        result.skip("sitemap-urls-resolve", "no sitemap could be fetched to read URLs from")
        result.signal("sitemap_checked", False)
        return

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
            rationale="Without a sitemap a crawler only finds what it can reach by "
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
            rationale="A sitemap that fails to parse is worse than none, because "
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
            rationale="Robots.txt is the first file a crawler reads, so a Sitemap "
                      "line is the cheapest way to hand it the full page list.",
        )

    # Whether the sitemap's dates are meaningful is mechanism D, and
    # freshness-corroboration-audit owns it. This skill owns only whether the
    # sitemap exists, parses, and resolves.
    _check_sitemap_urls_resolve(result, snapshot, reachable, fetcher)


def _check_sitemap_urls_resolve(result, snapshot, reachable, fetcher):
    """Find sitemap entries that 404, preferring statuses the crawl already has."""
    result.check("sitemap-urls-resolve")
    known = {p["url"]: p.get("status") for p in snapshot.get("pages") or []}
    refusals = {p["url"]: bool(p.get("edge_refusal"))
                for p in snapshot.get("pages") or []}
    listed = []
    for record in reachable:
        for entry in record["urls"]:
            listed.append(entry["loc"])
    if not listed:
        result.skip("sitemap-urls-resolve", "no URLs listed in the sitemap")
        return

    # A refusal is not a dead URL. `link_verdict` is shared with the two other
    # checks in this marketplace that probe a link, so all three mean the same
    # thing by a 403.
    dead, checked, unchecked = [], 0, 0
    for url in listed:
        if url in known:
            verdict = link_verdict(known[url], refusals.get(url, False))
            if verdict == "dead":
                dead.append((url, known[url]))
                checked += 1
            elif verdict == "alive":
                checked += 1
            elif known[url] is not None:
                unchecked += 1

    head_supported = (snapshot.get("crawl") or {}).get("head_supported")
    to_probe = [u for u in sample(listed, SITEMAP_PROBE_LIMIT) if u not in known]
    if head_supported is False:
        unchecked += len(to_probe)
        to_probe = []
    if fetcher is not None:
        for url in to_probe:
            response = fetcher.try_get(url, method="HEAD")
            verdict = link_verdict(response.status_code if response is not None else None)
            if verdict == "dead":
                dead.append((url, response.status_code))
                checked += 1
            elif verdict == "alive":
                checked += 1
            else:
                unchecked += 1
    elif to_probe:
        result.skip("sitemap-urls-resolve",
                    "network probes disabled; only the {} sitemap URL(s) already crawled "
                    "were checked".format(checked))

    result.signal("sitemap_urls_checked", checked)
    result.signal("sitemap_urls_unchecked", unchecked)

    if not checked:
        if head_supported is False:
            result.skip("sitemap-urls-resolve",
                        "this site refuses HEAD requests while answering GET normally, so the "
                        "{} sitemap URL(s) the crawl did not reach are unchecked rather than "
                        "assumed dead".format(unchecked))
        return
    if not dead:
        return

    rate = pct(len(dead), checked)
    result.add(
        id_hint="sitemap-lists-dead-urls",
        title="The sitemap lists URLs that do not return 200",
        severity="high" if rate >= 25 else "medium", confidence="high",
        evidence="{} of {} checked sitemap entries ({}%) are gone. Examples: {}.{}".format(
                     len(dead), checked, rate,
                     "; ".join("{} -> {}".format(u, s) for u, s in sorted(dead)[:5]),
                     " A further {} entry could not be verified and is excluded from the "
                     "rate.".format(unchecked) if unchecked == 1 else
                     " A further {} entries could not be verified and are excluded from the "
                     "rate.".format(unchecked) if unchecked else ""),
        mechanism="A", root_cause="sitemap-broken",
        summary="Remove dead URLs from the sitemap, or restore the pages they point to.",
        how_to_fix=[
            "For each dead URL, decide whether the page should exist. If it should, restore it.",
            "If it should not, remove it from the sitemap and add a 301 to the closest live page.",
            "Set the sitemap to regenerate on publish so deleted pages drop out automatically.",
        ],
        effort="medium", owner="developer",
        rationale="A sitemap full of dead links teaches crawlers that this site's "
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
        rationale="Robots.txt permission is irrelevant if the edge returns a "
                  "challenge page. The crawler receives no content, so the brand cannot appear "
                  "in an answer even though the site looks open on paper.",
        affected_pages=[home_url],
    )


def _ua_string(name):
    """The probe identifies itself as both the crawler and this audit tool."""
    return "{} ({}; comparison probe)".format(name, USER_AGENT)


def _check_status_and_indexability(result, snapshot, pages, ok_pages, fetcher=None):
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
            evidence="{} {}.{}".format(
                home_url,
                "returned HTTP {}".format(status) if status
                else explain_fetch_error(home.get("error")),
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
            rationale="The homepage is the entry point almost every crawler and "
                      "assistant tries first. If it fails, most never try anything else.",
            affected_pages=[home_url],
        )

    fetched = [p for p in pages if p.get("status") is not None]
    # An edge refusing this crawler in a ten-byte body is not a deleted page,
    # and the crawl already worked that out - `_mark_edge_refusals` flags them
    # and the report's own appendix says "one edge refusing this crawler, not
    # 19 deleted pages". This check was not reading the flag, so the same
    # report carried "32.8% of crawled pages do not return HTTP 200 - fix or
    # redirect the URLs that no longer resolve" at the top, about pages that
    # are live, and the correction two sections below where nobody reads.
    refused_by_edge = [p for p in fetched if p.get("edge_refusal")]
    bad = [p for p in fetched if p["status"] != 200 and not p.get("edge_refusal")]
    if refused_by_edge:
        result.signal("edge_refused_urls", len(refused_by_edge))
    # A percentage of one page is not a second observation. A museum whose
    # homepage was blocked produced exactly one fetch, and the report carried
    # both "The homepage refuses this crawler" (critical) and "100.0% of
    # crawled pages refuse this crawler" (high) with separate effort estimates
    # of several days each - one blocked request, priced twice. Below three
    # fetches the rate says nothing the homepage finding has not already said.
    if len(fetched) < 3 and bad:
        result.skip(
            "non-200-rate",
            "only {} URL(s) were fetched before the site stopped answering, so a percentage "
            "would restate the homepage finding rather than add to it".format(len(fetched)))
    elif fetched and bad:
        rate = pct(len(bad), len(fetched))
        if rate >= 10:
            counts = Counter(p["status"] for p in bad)
            # A refusal and a dead page are both "not 200" and need opposite
            # fixes. The homepage branch above has distinguished them since it
            # was written; this one did not, so on a bot-managed site it told
            # the owner to add redirects for 42 pages that work perfectly in a
            # browser. Measured on a real retail site: 42 of 44 URLs answered
            # 403 to an identified, robots-respecting crawler.
            refused = [p for p in bad if p["status"] in REFUSED_STATUS]
            mostly_refused = len(refused) > len(bad) / 2
            result.add(
                id_hint="high-non-200-rate",
                title="{}% of crawled pages refuse this crawler".format(rate)
                      if mostly_refused
                      else "{}% of crawled pages do not return HTTP 200".format(rate),
                severity="high" if rate >= 25 else "medium", confidence="high",
                evidence="{} of {} fetched URLs returned a non-200 status ({}).{} Examples: {}.".format(
                    len(bad), len(fetched),
                    ", ".join("{}x{}".format(v, k) for k, v in sorted(counts.items())),
                    " {} of them are refusals rather than missing pages: the request identified "
                    "itself as an audit crawler, respected robots.txt and was rate limited, so "
                    "this is a bot-management rule and not an outage.".format(len(refused))
                    if mostly_refused else "",
                    ", ".join(example_urls([p["url"] for p in bad]))),
                mechanism="A", root_cause="bot-manager-block" if mostly_refused else "non-200",
                summary="Allow identified crawlers to fetch the site, not just the homepage."
                        if mostly_refused
                        else "Fix or redirect the URLs that are still linked but no longer resolve.",
                how_to_fix=[
                    "Open your CDN or WAF bot-management rules. A blanket challenge on "
                    "unrecognised user agents also blocks every AI answer crawler."
                    if mostly_refused
                    else "Export the failing URLs and check each against the current site structure.",
                    "Allow-list the AI answer crawlers by user agent, keeping rate limits in place."
                    if mostly_refused
                    else "301 anything that moved; remove links to anything that is genuinely gone.",
                    "Verify by requesting several of the listed URLs with each crawler's "
                    "user-agent string and confirming a 200 with real HTML rather than a "
                    "challenge page."
                    if mostly_refused
                    else "Return 410 rather than 404 for pages deliberately retired, so crawlers "
                         "stop re-requesting them.",
                ],
                effort="high" if mostly_refused else "medium", owner="developer",
                rationale="A page a crawler cannot fetch cannot be read or cited, "
                          "and at this rate the site is largely invisible to the systems that "
                          "would quote it." if mostly_refused else
                          "Mechanism A: every failing URL is a page that cannot be read or cited, "
                          "and a high failure rate reduces how deeply crawlers explore the site.",
                affected_pages=[p["url"] for p in bad],
            )
        else:
            result.skip("non-200-rate",
                        "{}% of fetched URLs were non-200, below the 10% threshold where this "
                        "indicates a systemic problem".format(rate))
    elif fetched and refused_by_edge:
        result.skip("non-200-rate",
                    "{} URL(s) were refused by the site's edge with an identical short body, "
                    "which is one refusal rather than that many broken pages, and every other "
                    "fetched URL returned HTTP 200".format(len(refused_by_edge)))
    elif fetched:
        result.skip("non-200-rate", "every fetched URL returned HTTP 200")

    long_chains = [p for p in pages if len(p.get("redirect_chain") or []) > 2]
    if long_chains:
        result.add(
            id_hint="long-redirect-chains",
            title="Some URLs redirect more than twice before resolving",
            severity="low", confidence="high",
            evidence="{} crawled URL(s) had a redirect chain longer than 2 hops. Examples: {}.".format(
                len(long_chains), ", ".join(example_urls([p["url"] for p in long_chains]))),
            mechanism="A", root_cause="redirect-chain",
            summary="Collapse redirect chains so each old URL points straight at the final page.",
            how_to_fix=[
                "List the chains and rewrite each rule to target the final destination directly.",
                "Watch for the common stack: http to https, then non-www to www, then a "
                "trailing-slash rule. Combine these into one rule.",
            ],
            effort="low", owner="developer",
            rationale="Some crawlers stop following after a small number of hops, "
                      "and every hop adds latency to a fetch an assistant is doing live.",
            affected_pages=[p["url"] for p in long_chains],
        )

    noindexed = []
    soft_404s = []
    duplicates = []
    for page in ok_pages:
        if page.get("page_type") not in CONTENT_TYPES:
            continue
        directives = "{} {}".format(page.get("meta_robots", ""), page.get("x_robots_tag", "")).lower()
        if "noindex" not in directives:
            continue
        # `noindex` on a page whose own title says it is missing is the site
        # doing the right thing. Reported as a defect, the fix - "remove
        # `noindex` where the page should be public" - would put a broken page
        # into the index.
        if looks_like_soft_404(page):
            soft_404s.append(page)
            continue
        # A filtered or sorted view of a listing carries `noindex` and a
        # canonical pointing at the listing itself. That is the textbook way to
        # keep a duplicate out of an index, and it was reported as a
        # high-severity defect in a retailer's top three, with the fix "remove
        # `noindex` where the page should be public" - which would index the
        # duplicate the site is deliberately hiding.
        canonical = (page.get("canonical") or "").strip()
        if canonical and canonical.rstrip("/") != (page.get("url") or "").rstrip("/") \
                and canonical.rstrip("/") != (page.get("final_url") or "").rstrip("/"):
            duplicates.append(page)
            continue
        noindexed.append(page)
    if soft_404s:
        result.signal("noindexed_soft_404s", [p["url"] for p in soft_404s])
    if noindexed:
        result.add(
            id_hint="noindex-on-content-pages",
            title="{} a noindex directive".format(
            plural(len(noindexed), "content page carries", "content pages carry")),
            severity="high", confidence="high",
            evidence="Pages with `noindex` in a meta robots tag or X-Robots-Tag header: {}.".format(
                ", ".join(example_urls([p["url"] for p in noindexed]))),
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
            rationale="`noindex` tells a crawler to read the page and then discard "
                      "it. The content is fetched and then thrown away, so it can never be cited.",
            affected_pages=[p["url"] for p in noindexed],
        )
    else:
        detail = "no crawled content page carries a noindex directive"
        if duplicates:
            detail = ("the content page(s) carrying noindex ({}) each name a different page as "
                      "their canonical, so they are filtered or sorted duplicates the site is "
                      "deliberately keeping out of an index - which is correct, and is not "
                      "reported as a defect".format(
                          ", ".join(example_urls([p["url"] for p in duplicates]))))
        if soft_404s:
            detail = ("the only content page(s) carrying noindex are ones whose own title "
                      "says the page is missing ({}), which is the correct thing for a "
                      "site to do and is not reported as a defect".format(
                          ", ".join(example_urls([p["url"] for p in soft_404s]))))
        result.skip("noindex-on-content-pages", detail)

    _check_canonicals(result, snapshot, ok_pages, fetcher)
    _check_meta_refresh(result, snapshot, ok_pages)


def _check_meta_refresh(result, snapshot, ok_pages):
    """Pages that redirect with markup instead of an HTTP status.

    A `<meta http-equiv="refresh">` is a redirect only for consumers that
    render HTML. Server-side crawlers - including several that feed AI answers -
    read the stub and stop, which is a handful of bytes with no heading, no
    links and nothing to quote. The audit follows it so the report describes the
    real page, and reports the stub separately, because a consumer that does not
    follow it sees what we first saw.
    """
    result.check("meta-refresh-redirects")
    stubs = [p for p in ok_pages if p.get("meta_refresh_from")]
    if not stubs:
        result.skip("meta-refresh-redirects",
                    "no crawled page redirects by meta refresh instead of an HTTP status")
        return

    home_affected = any(p.get("page_type") == "home" for p in stubs)
    result.add(
        id_hint="meta-refresh-instead-of-http-redirect",
        title="{} with markup rather than an HTTP status".format(
            plural(len(stubs), "page redirects", "pages redirect")),
        severity="high" if home_affected else "medium",
        confidence="high",
        evidence="; ".join(
            "{} answers with {} bytes and a meta refresh to {}".format(
                p["meta_refresh_from"]["url"], p["meta_refresh_from"].get("html_len", 0),
                p["url"]) for p in sorted(stubs, key=lambda x: x["url"])[:3]) + ".",
        mechanism="A", root_cause="meta-refresh",
        summary="Replace the meta refresh with a 301 redirect issued by the server.",
        how_to_fix=[
            "Return `HTTP/1.1 301 Moved Permanently` with a `Location` header instead of "
            "serving a page that contains a refresh tag.",
            "Most static hosts support this in configuration: a `_redirects` file, a "
            "`redirects` block, or a rewrite rule, depending on the platform.",
            "If the refresh exists to choose a language, redirect on `Accept-Language` at the "
            "server and give each language a real URL that can be linked and cited.",
            "Verify with `curl -sI <url>`: the first line should be a 301, and the body should "
            "be empty.",
        ],
        effort="low", owner="developer",
        rationale="A meta refresh is a redirect only for something that renders "
                  "HTML. A crawler that reads the first response and stops sees a few hundred "
                  "bytes with no heading, no links and nothing worth quoting, and concludes "
                  "the page is empty rather than that it moved.",
        affected_pages=[p["meta_refresh_from"]["url"] for p in stubs],
    )


def _check_canonicals(result, snapshot, ok_pages, fetcher=None):
    result.check("canonical-targets")
    origin_host = strip_www(urlparse(snapshot["origin"]).netloc.lower())
    known_status = {p["url"]: p.get("status") for p in snapshot.get("pages") or []}
    off_domain, broken = [], []

    with_canonical = [p for p in ok_pages if p.get("canonical")]
    if not with_canonical:
        result.skip("canonical-targets", "no crawled page declares a canonical URL")
        return

    # A canonical usually points somewhere the crawl already went, in which case
    # its status is free. The damaging case is the one it does not: a canonical
    # aimed at a URL that no longer exists tells every consumer to ignore the
    # page it is on. That target is invisible to the crawl, so a small number of
    # HEAD requests are spent resolving the distinct unknown ones.
    unknown = []
    for page in with_canonical:
        canonical = page["canonical"]
        host = strip_www(urlparse(canonical).netloc.lower())
        if host and host != origin_host:
            continue
        if canonical not in known_status and canonical not in unknown:
            unknown.append(canonical)
    # Same rule again: only probe with HEAD where HEAD is answered honestly.
    head_supported = (snapshot.get("crawl") or {}).get("head_supported")
    if head_supported is not False:
        for target in unknown[:CANONICAL_PROBE_LIMIT]:
            if fetcher is None or not fetcher.budget_left:
                break
            response = fetcher.try_get(target, method="HEAD")
            if response is not None:
                known_status[target] = response.status_code

    for page in with_canonical:
        canonical = page["canonical"]
        host = strip_www(urlparse(canonical).netloc.lower())
        if host and host != origin_host:
            off_domain.append((page["url"], canonical))
        elif (canonical in known_status
                and link_verdict(known_status[canonical]) == "dead"):
            # Was `not in (None, 200)`, which filed a 403 refusal and a 503
            # deploy blip as a broken canonical at high severity.
            broken.append((page["url"], canonical, known_status[canonical]))

    if off_domain:
        result.add(
            id_hint="canonical-points-off-domain",
            title="{} a canonical URL on a different domain".format(
            plural(len(off_domain), "page declares", "pages declare")),
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
            rationale="A canonical tag tells consumers 'the real version is over "
                      "there'. Pointing off-domain hands attribution for this content to another site.",
            affected_pages=[a for a, _ in sorted(off_domain)],
        )

    if broken:
        result.add(
            id_hint="canonical-points-to-broken-url",
            title="{} a canonical URL that does not return 200".format(
            plural(len(broken), "page declares", "pages declare")),
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
            rationale="A canonical pointing at a dead URL leaves consumers unsure "
                      "which version of the page is authoritative, so some drop both.",
            affected_pages=[a for a, _, _ in sorted(broken)],
        )

    if not off_domain and not broken:
        result.skip("canonical-targets",
                    "all {} declared canonical URLs are on this domain and resolve".format(
                        len(with_canonical)))


def _landed_on_https(ok_pages):
    """Did the pages the crawl actually read come back over HTTPS?

    Prefer what was observed over what was assumed. This is the general shape
    of the bug it fixes: a check reasoning about a value it could have read
    from the record, using instead the value it was handed at the start.
    """
    landed = [(p.get("final_url") or p.get("url") or "") for p in ok_pages]
    landed = [u for u in landed if u]
    return bool(landed) and all(u.startswith("https://") for u in landed)


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
    elif snapshot["origin"].startswith("http://") and _landed_on_https(ok_pages):
        # The crawl watched the redirect happen and the check did not look.
        #
        # A bank's report said "The site is served over plain HTTP. No HTTPS
        # redirect was observed" while the same snapshot recorded
        # `final_url: https://www.example-bank.test/` for that very fetch. The origin
        # is where the audit started - which can be http:// because that is
        # what someone typed, or because a certificate check failed on the
        # auditing machine - and it is not where the site serves from.
        result.skip("https-transport",
                    "the audited origin was entered as http://, and every page fetched "
                    "redirected to https://, so the site does serve over HTTPS. The scheme "
                    "the audit started from is not a fact about the site")
    elif snapshot["origin"].startswith("http://"):
        result.add(
            id_hint="site-served-over-http",
            title="The site is served over plain HTTP",
            severity="medium", confidence="high",
            evidence="The audited origin is {}, and no page fetched redirected to an https:// "
                     "address.".format(snapshot["origin"]),
            mechanism="A", root_cause="insecure-transport",
            summary="Serve the whole site over HTTPS and redirect HTTP to it.",
            how_to_fix=[
                "Obtain a certificate (free from Let's Encrypt, or included with most hosts).",
                "Add a permanent redirect from http:// to https:// for every path.",
                "Update internal links, canonical tags and the sitemap to the https:// form.",
            ],
            effort="medium", owner="developer",
            rationale="Browsers warn on HTTP pages and several crawlers deprioritise "
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
            rationale="Two hostnames serving the same content look like two sources "
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
    parser.add_argument("--time-budget", type=float,
                        help="seconds of wall clock this check may spend on its own requests; unreached targets are reported as unchecked rather than guessed at")
    parser.add_argument("--no-network", action="store_true",
                        help="skip the bot-user-agent probe and sitemap HEAD checks")
    args = parser.parse_args(argv)

    result = run(load_snapshot(args.snapshot), allow_network=not args.no_network,
                 time_budget=args.time_budget)
    result.write(args.out)
    print("{}: {} finding(s), {} extra request(s)".format(
        SKILL, len(result.findings), result.extra_requests_made), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
