#!/usr/bin/env python3
"""crawl-access-audit: is the crawler let in at all? (mechanism A)

Reads snapshot.json, writes findings JSON. Makes at most 30 extra read-only
requests: two bot-user-agent comparison fetches (plus one re-baseline and one
confirmation), up to five that re-ask one URL name by name through a second
HTTP client where a refusal is already on the table, up to eight HEAD probes of
sitemap URLs the crawl did not already visit, probes of redirect and canonical
targets, one GET that re-asks a refused URL after the crawl has stopped, one
for /sitemap_index.xml, one for a path that does not exist, up to two for
agent-readable files the site's own robots.txt announces, up to two that re-read
one page of each of the first two language editions to see whether they declare
`hreflang`, and - only where the crawl's own record does not already settle it -
one that reads whether a sitemap that would not parse is an HTML page.

Both HTTP clients go through the one guard in `Fetcher.get`: GET or HEAD only,
no state-changing or private path, one request budget, one delay, one clock.

Usage:
    python check.py --snapshot snapshot.json --out crawl-access-audit.findings.json
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, namedtuple
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

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
    CHALLENGE_TEXT_CEILING, confirm_dead, DATED_ARCHIVE_RE,
    deadline_from_budget, detect_challenge,
    cart_path_word, CART_WORDS, is_listing_page,
    example_urls, explain_fetch_error, Fetcher, FetchError, is_faceted_listing,
    incomplete_certificate_chain,
    is_search_result_page, link_verdict,
    is_forbidden_path, locale_editions,
    load_snapshot, looks_like_soft_404, make_soup, no_network_reason, page_identity,
    pages_of, pct, primary_subtag, publishing_platform, template_change_steps,
    plural, REFUSED_STATUS, response_is_an_html_page, response_text, response_timing,
    same_site, sample,
    sitemap_scope, sitemap_total_phrase, SkillResult,
    SLOW_ORIGIN_MEDIAN_MS, slow_origin_note, strip_default_port, strip_www, USER_AGENT,
    VERY_SLOW_ORIGIN_MEDIAN_MS,
    visible_text
)
from robots_parser import (  # noqa: E402
    blocks_entire_site, group_for, is_disallowed, path_of, repeated_agent_groups,
    rule_matches_path, section_disallows, substantive_disallows,
)

SKILL = "crawl-access-audit"

# ---------------------------------------------------------------------------
# What each AI user agent is actually for.
#
# Not two lists of names. A record per agent carrying the operator's own
# stated purpose, the page that states it, and the date that page was read.
# The two tuples the rest of this file uses are derived from it, so a role can
# only be changed in one place and the prose cannot drift away from the data.
#
# Four roles, because "AI crawler" is not one thing and the owner's decision
# differs for each:
#
#   search      indexes pages so the brand can be surfaced and linked in an
#               assistant's answer. Blocking costs citations.
#   user-fetch  fetches one page live because a person just asked about it.
#               Blocking costs that answer.
#   training    collects a corpus for model training. Blocking is a rights
#               decision, not a discoverability defect.
#   control     not a crawler at all - a token whose only function is to carry
#               a training opt-out. It never fetches anything, so blocking it
#               cannot cost a citation.
#
# An earlier version of this file listed GPTBot, ClaudeBot, Amazonbot,
# Bytespider and Meta-ExternalAgent as answer crawlers, put
# Meta-ExternalFetcher in the training group, and told owners to allow-list the
# first set "so your pages can be cited". Every one of those is wrong against
# the operator's own documentation. An owner who followed it would have
# reopened their site to training collection they had deliberately opted out
# of, to fix a citation problem that did not exist. `source` and `checked`
# exist so the next person verifies rather than inherits.
# ---------------------------------------------------------------------------

SEARCH, USER_FETCH, TRAINING, CONTROL = "search", "user-fetch", "training", "control"

# Roles whose block removes the brand from an answer someone is reading.
ANSWER_ROLES = (SEARCH, USER_FETCH)

CrawlerAgent = namedtuple(
    "CrawlerAgent", "token operator role purpose source checked vendor_documented")


def _agent(token, operator, role, purpose, source, checked="2026-09-03",
           vendor_documented=True):
    return CrawlerAgent(token, operator, role, purpose, source, checked,
                        vendor_documented)


_OPENAI = "https://developers.openai.com/api/docs/bots"
_ANTHROPIC = ("https://support.claude.com/en/articles/8896518-does-anthropic-"
              "crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler")
_PERPLEXITY = "https://docs.perplexity.ai/guides/bots"
_APPLE = "https://support.apple.com/en-us/119829"
_AMAZON = "https://developer.amazon.com/amazonbot"
_META = "https://developers.facebook.com/docs/sharing/webmasters/web-crawlers/"
_GOOGLE = ("https://developers.google.com/search/docs/crawling-indexing/"
           "google-common-crawlers")
_DUCK = "https://duckduckgo.com/duckduckgo-help-pages/results/duckassistbot"
_COMMONCRAWL = "https://commoncrawl.org/ccbot"
_UNDOCUMENTED = ""

AI_CRAWLER_AGENTS = (
    # --- OpenAI ---
    _agent("OAI-SearchBot", "OpenAI", SEARCH,
           "surfaces websites in search results in ChatGPT's search features", _OPENAI),
    _agent("ChatGPT-User", "OpenAI", USER_FETCH,
           "visits a web page when a user asks ChatGPT a question", _OPENAI),
    _agent("GPTBot", "OpenAI", TRAINING,
           "crawls content that may be used in training OpenAI's foundation models", _OPENAI),

    # --- Anthropic ---
    _agent("Claude-SearchBot", "Anthropic", SEARCH,
           "analyses online content to improve search result quality", _ANTHROPIC),
    _agent("Claude-User", "Anthropic", USER_FETCH,
           "accesses a website when an individual asks Claude a question", _ANTHROPIC),
    _agent("ClaudeBot", "Anthropic", TRAINING,
           "collects web content that could contribute to model training", _ANTHROPIC),
    _agent("anthropic-ai", "Anthropic", TRAINING,
           "legacy token still seen in robots.txt; superseded by the three above and no "
           "longer documented", _UNDOCUMENTED, vendor_documented=False),

    # --- Perplexity ---
    _agent("PerplexityBot", "Perplexity", SEARCH,
           "surfaces and links websites in Perplexity search results; explicitly not used "
           "for foundation-model training", _PERPLEXITY),
    _agent("Perplexity-User", "Perplexity", USER_FETCH,
           "visits a web page to help answer a question a user just asked", _PERPLEXITY),

    # --- Apple ---
    _agent("Applebot", "Apple", SEARCH,
           "powers Spotlight, Siri and Safari search", _APPLE),
    _agent("Applebot-Extended", "Apple", CONTROL,
           "opt-out token for training Apple's foundation models; does not affect Siri or "
           "Spotlight, which follow Applebot", _APPLE),

    # --- Amazon ---
    _agent("Amzn-SearchBot", "Amazon", SEARCH,
           "improves search experiences in Amazon products such as Alexa; does not crawl "
           "for generative AI training", _AMAZON),
    _agent("Amzn-User", "Amazon", USER_FETCH,
           "supports user actions such as answering an Alexa query that needs current "
           "information; does not crawl for generative AI training", _AMAZON),
    _agent("Amazonbot", "Amazon", TRAINING,
           "fetches content for Amazon products and services, and may be used to train "
           "Amazon AI models", _AMAZON),

    # --- Meta ---
    _agent("Meta-ExternalFetcher", "Meta", USER_FETCH,
           "fetches individual links at a user's request, including helping AI navigate "
           "sites to complete tasks", _META),
    _agent("Meta-ExternalAgent", "Meta", TRAINING,
           "crawls for use cases such as training foundation AI models or indexing content "
           "directly", _META),

    # --- Google ---
    _agent("Google-Extended", "Google", CONTROL,
           "opt-out token for Gemini training and grounding; does not affect Google Search "
           "indexing, which follows Googlebot", _GOOGLE),

    # --- The rest ---
    _agent("DuckAssistBot", "DuckDuckGo", SEARCH,
           "gathers passages that DuckAssist cites in instant answers", _DUCK),
    _agent("YouBot", "You.com", SEARCH,
           "indexes pages for an answer engine that links its sources", _UNDOCUMENTED,
           vendor_documented=False),
    _agent("MistralAI-User", "Mistral", USER_FETCH,
           "fetches a page when a Le Chat user asks about it", _UNDOCUMENTED,
           vendor_documented=False),
    _agent("Bytespider", "ByteDance", TRAINING,
           "collects training data for ByteDance's models; ByteDance publishes no crawler "
           "documentation, so this role is inferred from observed behaviour",
           _UNDOCUMENTED, vendor_documented=False),
    _agent("cohere-ai", "Cohere", TRAINING,
           "corpus collection; no published purpose statement", _UNDOCUMENTED,
           vendor_documented=False),
    _agent("CCBot", "Common Crawl", TRAINING,
           "builds the open Common Crawl corpus that many models train on", _COMMONCRAWL),
    _agent("omgilibot", "Webz.io", TRAINING,
           "collects web data for licensing to model builders", _UNDOCUMENTED,
           vendor_documented=False),
    _agent("Webzio-Extended", "Webz.io", CONTROL,
           "training opt-out token for Webz.io datasets", _UNDOCUMENTED,
           vendor_documented=False),
    _agent("Diffbot", "Diffbot", TRAINING,
           "extracts pages into a commercial knowledge graph", _UNDOCUMENTED,
           vendor_documented=False),
    _agent("Timpibot", "Timpi", TRAINING,
           "builds a distributed index sold as training data", _UNDOCUMENTED,
           vendor_documented=False),
    _agent("PanguBot", "Huawei", TRAINING,
           "collects training data for the PanGu models", _UNDOCUMENTED,
           vendor_documented=False),
    _agent("ImagesiftBot", "ImageSift", TRAINING,
           "collects images for dataset building", _UNDOCUMENTED,
           vendor_documented=False),
)

AGENT_BY_TOKEN = {agent.token.lower(): agent for agent in AI_CRAWLER_AGENTS}

# Blocking one of these costs the brand an answer. Search agents come first so
# that the live user-agent probe, which uses the first unblocked name, asks
# with the agent whose refusal would matter most.
ANSWER_CRAWLERS = tuple(
    agent.token for agent in AI_CRAWLER_AGENTS if agent.role == SEARCH
) + tuple(
    agent.token for agent in AI_CRAWLER_AGENTS if agent.role == USER_FETCH
)

# Corpus collectors and the opt-out tokens that control them. Blocking these is
# a rights decision about training data, not a discoverability defect, so it is
# reported as info and never inflates the severity counts.
TRAINING_CRAWLERS = tuple(
    agent.token for agent in AI_CRAWLER_AGENTS if agent.role in (TRAINING, CONTROL))

_ROLE_PHRASE = {
    SEARCH: "search index",
    USER_FETCH: "live fetch when a user asks",
    TRAINING: "model training",
    CONTROL: "training opt-out token, fetches nothing",
}


def describe_agents(tokens, limit=8):
    """Name each agent with its operator and what that operator says it does.

    The old evidence line was a bare list of tokens under a heading claiming
    all of them fetched pages to build cited answers. Printing the role beside
    each name means the sentence cannot outrun the data again, and the owner
    can tell a search crawler from a training one without looking anything up.
    """
    tokens = list(tokens)
    parts = []
    for token in tokens[:limit]:
        agent = AGENT_BY_TOKEN.get(token.lower())
        if agent is None:
            parts.append("`{}`".format(token))
            continue
        parts.append("`{}` ({}, {})".format(
            token, agent.operator, _ROLE_PHRASE[agent.role]))
    # A truncated list read as the whole list. A broadcaster blocks ten
    # training crawlers; the evidence named eight and stopped, so an owner
    # working through it would have left two they never saw. Saying how many
    # were left out costs four words.
    if len(tokens) > limit:
        parts.append("and {} more".format(len(tokens) - limit))
    return ", ".join(parts)



def _every_path_or_a_named_sample(paths, shown=5):
    """Every path, or the first few plus a count of the rest.

    Never a silent truncation: the number in a finding's title is the number a
    reader will count in its evidence.
    """
    listed = ", ".join("`{}`".format(rule) for rule in paths[:shown])
    if len(paths) <= shown:
        return listed
    return "{} and {} more".format(listed, len(paths) - shown)


def answer_side_counterparts(tokens):
    """The same operators' search and live-fetch agents.

    The point of the training finding is not "you blocked a training crawler".
    It is: you blocked one, that is your right, and here are the agents from the
    same company that actually decide whether you get cited - check those are
    open. Blocking GPTBot costs nothing an assistant's reader sees; blocking
    OAI-SearchBot and ChatGPT-User costs the citation.
    """
    operators = []
    for token in tokens:
        agent = AGENT_BY_TOKEN.get(token.lower())
        if agent is not None and agent.operator not in operators:
            operators.append(agent.operator)
    return [agent.token for agent in AI_CRAWLER_AGENTS
            if agent.role in ANSWER_ROLES and agent.operator in operators]

def undocumented_among(tokens):
    """Tokens whose role this audit inferred rather than read from the operator."""
    return [token for token in tokens
            if token.lower() in AGENT_BY_TOKEN
            and not AGENT_BY_TOKEN[token.lower()].vendor_documented]


def _same_location(requested, landed):
    """Is the URL the response came back from the one that was asked for?

    `requests` follows redirects, so `status_code` is the last hop's status and
    every check that reads it alone describes a URL it never asked about. A
    browser-compatibility database answers 302 to /brand-ai-readiness-audit-no-
    such-page and sends the request to /?search=<that path>; the missing-page
    check read the 200 at the end of the hop and reported "nothing in it
    distinguishes this from a real page" about a response whose own redirect
    distinguishes it.

    Scheme and a leading `www.` are not the site telling two URLs apart, so an
    http-to-https or www hop of the same path counts as the same location. A
    different path or query does not.
    """
    if not landed:
        return True
    asked, arrived = urlparse(requested), urlparse(landed)
    return ((strip_www(asked.hostname or ""), asked.path.rstrip("/"), asked.query)
            == (strip_www(arrived.hostname or ""), arrived.path.rstrip("/"), arrived.query))


def _redirect_hop(requested, response):
    """`(status, landed_url)` of the first hop, or None when nothing redirected.

    Reads `history` and `url` off the response rather than asking for the URL
    a second time without redirects, so this costs no request.
    """
    if response is None:
        return None
    hops = list(getattr(response, "history", None) or [])
    landed = getattr(response, "url", "") or requested
    if not hops or _same_location(requested, landed):
        return None
    return (hops[0].status_code, landed)


# The address of a site's own error page: `/error.aspx`, `/404`, `/not-found`,
# `/pagenotfound.html`, `/errors/missing`. Where an unknown path is redirected
# here and the page answers 200, the site has named the page as an error and
# still told every machine the address resolved.
_ERROR_PAGE_PATH_RE = re.compile(
    r"(?:^|/)(?:errors?|40[34]|not[-_]?found|page[-_]?not[-_]?found|missing|"
    r"error[-_]?page|default[-_]?error)(?:[./_-]|$)", re.I)

# Demand, counted rather than assumed: the sitemap probe asks for up to 8, the
# bot-manager comparison for 4, the canonical probe for 3. Fifteen against a
# ceiling of ten meant the canonical probe was starved by running last, so on
# any site with a sitemap it silently never ran. The ceiling covers what the
# checks actually ask for, with five spare - and the spare is what pays for a
# GET confirmation on the rare probe that HEAD says is dead.
#
# The bot-manager comparison is 4 rather than 3 because it now re-establishes
# its own baseline before reporting: a throttle the crawl itself tripped makes
# every later request differ from a baseline recorded minutes ago, and without
# that fourth request a rate limiter read as a critical bot-manager block.
#
# Four more were added for the checks that stopped this skill asserting rules
# it had not established: one GET that re-asks a refused URL after the crawl
# has stopped hammering the origin, one for /sitemap_index.xml, one that reads
# whether a "broken" sitemap is really an HTML page, and one for a path that
# does not exist.
#
# Two more for the agent-readable files robots.txt itself announces. Demand
# was then 21 and the agent-file probes were the ones that went unspent when an
# earlier check had taken the budget - which they say when it happens rather
# than reporting the file as absent.
#
# Five more for the name-by-name comparison through the second HTTP client:
# four answer-crawler names, plus one control request under this audit's own
# name where the primary client was itself refused. They are spent only where
# a refusal is already on the table - a site that answers this crawler and
# answers the two probe names the same way never reaches that code and costs
# nothing extra at all. Demand is 25 against a ceiling of 30, and the spare
# pays for a GET confirmation on the rare probe that HEAD says is dead.
#
# Two more for the `hreflang` check, which re-reads one page of each of the
# first two language editions the crawl found. It spends nothing at all on a
# site with one edition, which is nearly every site, and it runs last so a
# budget already spent is taken from it rather than from a check that costs
# nothing to be right about. Demand is 27 against a ceiling of 30.
MAX_EXTRA_REQUESTS = 30
# Canonical targets the crawl never reached. Capped low: this is a
# diagnostic, not a link checker, and the site did not ask to be crawled
# harder than the budget already allows.
# Measured across 13 real sites: the median site declares zero canonical
# targets the crawl did not already fetch, and the worst declared one. Three
# has never been the binding constraint, and raising it would buy nothing.
CANONICAL_PROBE_LIMIT = 3
SITEMAP_PROBE_LIMIT = 8
# Answer agents the bot-manager comparison will try before concluding the
# edge treats crawlers no differently, plus one confirmation request. Two
# operators rather than one, because bot rules are written per vendor.
BOT_PROBE_AGENTS = 2

# Answer agents the second HTTP client asks under, one operator each, when a
# refusal is already on the table.
#
# Four, not two and not thirteen. Two is what the primary comparison spends,
# and two names cannot produce the list an allow rule is written from: a
# museum's edge refuses `Claude-SearchBot` and `PerplexityBot` while answering
# `OAI-SearchBot`, so the second refused name sits behind the third operator
# and a two-name probe reports one of the two. Thirteen is the whole
# answer-crawler table, and printing it as a list of names to allow was the
# mistake this check was rewritten to stop making. Four covers OpenAI,
# Anthropic, Perplexity and Apple, which is every operator whose search agent
# a refusal costs a citation with.
#
# Plus one control request under this audit's own name, and only where the
# primary client was itself refused - that is the only place the control
# decides anything, because it is what separates an edge reading names from an
# edge refusing this audit's HTTP client. Where the primary client was served,
# the baseline it already has is the control.
SECOND_TRANSPORT_AGENTS = 4


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
                              deadline=deadline_from_budget(time_budget))
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
    home = _homepage_record(pages, home_url)
    if home is not None and home.get("status") is None and not pages_of(snapshot):
        _check_status_and_indexability(result, snapshot, pages, ok_pages, fetcher, UA_UNTESTED)
        unreached = ("robots-txt-reachable", "robots-blocks-all-crawlers",
                     "robots-blocks-ai-answer-crawlers", "robots-crawl-delay",
                     "robots-agent-groups-unambiguous", "sitemap-present",
                     "sitemap-excludes-private-paths",
                     "sitemap-parses", "sitemap-urls-resolve", "canonical-targets",
                     "bot-manager-user-agent-comparison", "https-transport",
                     "bot-manager-challenge-page", "unknown-paths-return-200",
                     "llms-txt-presence")
        # One reason per check. `_check_status_and_indexability` runs the
        # canonical check on its way through, and on a host that served no
        # page that check declines with "no crawled page declares a canonical
        # URL" - true of an empty list, and printed in the appendix directly
        # above this branch's own line for the same check, so a national
        # library's report listed `canonical-targets` twice with two different
        # explanations. The host's answer is the one that explains it.
        result.not_applicable = [entry for entry in result.not_applicable
                                 if entry["check"] not in unreached]
        for name in unreached:
            # A host that answered with a certificate chain one link short did
            # respond, and saying otherwise sent a national library's owner to
            # look for an outage. The skip says what did happen.
            result.skip(name, "the host answered over HTTPS with a certificate chain that "
                              "is missing its intermediate certificate, so no page, robots.txt "
                              "or sitemap could be read; installing that certificate is the "
                              "only action available"
                        if incomplete_certificate_chain(home.get("error")) else
                        "the host did not respond at all, so nothing else about it "
                        "could be examined; fixing that is the only action available")
        result.signal("host_unreachable", True)
        return result

    # The user-agent comparison runs first because three later checks may not
    # call a refusal a defect until it has answered. On a hardware manufacturer the
    # comparison established that OAI-SearchBot and Claude-SearchBot are
    # answered HTTP 200 while this audit is answered 403, and said so in the
    # appendix - while the same report led with a critical finding saying
    # Cloudflare serves crawlers a verification page instead of the site. The
    # truth was the opposite: that edge lets the AI crawlers in and turns this
    # tool away. The verdict existed; nothing consulted it in time.
    ua_verdict = _check_bot_manager(result, snapshot, robots, fetcher, allow_network,
                                    time_budget=time_budget)
    refusal_recheck = _recheck_a_refusal(result, snapshot, fetcher, robots, ua_verdict)
    _check_challenge_pages(result, snapshot, ua_verdict, refusal_recheck)
    _check_robots_reachable(result, robots, snapshot)
    # The pages are passed so a disallowed path can be priced. "robots.txt
    # disallows /store/" is a fact about a file, and an owner cannot tell from
    # it whether that is three pages or the whole catalogue; the pages this
    # crawl actually fetched are the only evidence available here about what
    # sits behind a path the audit is not allowed to open.
    _check_robots_blocks(result, robots, origin, ok_pages)
    # `Crawl-delay` is a robots.txt directive like any other and had no check
    # at all: the crawl honoured it, shrank its own sample to fit, and said so
    # in an appendix note about itself. The owner of the number never heard.
    _check_crawl_delay(result, robots, snapshot)
    # Whether the file says one thing or two. Everything above asks `group_for`
    # for the rules that govern one crawler, and `group_for` merges the groups
    # naming that crawler - which is what RFC 9309 requires and what hides the
    # fact that there was more than one group to merge.
    _check_robots_agent_groups(result, robots)
    # Whether the file's own rules are rules at all. A live robots.txt carried
    # `Disallow: /g,mt=RegExp(` - a build step had written
    # code into it - and nothing in this audit said a word about it, because
    # every rule check asks whether a URL matches a value and a value that is
    # not a path matches nothing. Zero requests: the file is already in the
    # snapshot.
    _check_robots_directive_values(result, robots)
    # The missing-page probe runs before the three checks that read a status
    # code as proof that a file exists. It asks for one address nobody
    # published, and on a site that answers 200 to everything that one answer
    # settles all of them: a 200 at /sitemap.xml, a 200 at a canonical target
    # and a 200 at a sitemap entry are then the catch-all route, not evidence
    # of a file.
    #
    # It used to run after them, and the cost was the only false positive to
    # reach the confident tier across six sites in one batch of runs. A site whose
    # server answers every unknown path with its homepage was told "An XML
    # sitemap is published but does not parse ... line 25, column 104" -
    # which is an XML parser reading 1.8 MB of HTML - and the fix it was given,
    # "Fix the XML so the sitemap can be read", is work on a file that does not
    # exist. The sitemap check could only tell HTML from XML by spending
    # another request of its own, and on that run there was none left to spend.
    # This ordering makes the discrimination free, and it is free on every run,
    # because the probe was already being paid for.
    _check_soft_404_handling(result, snapshot, fetcher, robots, time_budget=time_budget)
    _check_sitemaps(result, snapshot, fetcher, robots)
    _check_status_and_indexability(result, snapshot, pages, ok_pages, fetcher, ua_verdict,
                                   robots, refusal_recheck)
    _check_transport_and_hosts(result, snapshot, ok_pages)

    _check_agent_files(result, snapshot, robots, fetcher, time_budget=time_budget)
    # Last, because it is the only check here that spends a request on a
    # question no other check needs answered, and because it spends nothing at
    # all on a site with one language edition - which is nearly every site.
    _check_hreflang(result, snapshot, ok_pages, fetcher, robots)
    # A robots.txt that answered 200 with an HTML page is not a robots.txt. The
    # signal said `True` for it, which is the same wrong answer this file
    # corrects in four other places.
    result.signal("robots_present", _robots_was_read(robots))
    # Including the one the extra probe found. A sitemap this audit located at
    # /sitemap_index.xml after the crawl had finished is still a sitemap, and
    # leaving the signal False let the report contradict its own appendix.
    #
    # Three states, not two, for the same reason `llms_txt_present` has three.
    # A location that answered 200 with a body nothing established the type of
    # is not a sitemap and is not the absence of one, and publishing `True` for
    # it is how "an XML sitemap is published but does not parse" got written
    # about a site that publishes none.
    not_a_sitemap = set(result.signals.get("sitemap_html_shells") or [])
    unverified = set(result.signals.get("sitemap_body_unverified") or [])
    answered = [s for s in snapshot.get("sitemaps") or [] if s.get("status") == 200]
    present = (any(s["url"] not in not_a_sitemap and s["url"] not in unverified
                   for s in answered)
               or bool(result.signals.get("sitemap_probe_found")))
    result.signal("sitemap_present", None if not present and unverified else present)
    result.signal("pages_crawled", crawl_info.get("pages_crawled", 0))

    if fetcher is not None:
        result.extra_requests_made = fetcher.count
    return result


# --------------------------------------------------------------------------
# Who is running the wall, and what may therefore be asked of the reader
#
# `detect_challenge` names what it can, and the three things it can hand back
# are not the same kind of fact:
#
#   a product name    the bot manager's own challenge scaffolding was in the
#                     body, or the edge set a mitigation header spelling the
#                     product's name. Somebody holds an account with that
#                     product, and that account has rules in a console.
#   the site's edge   only the words on the screen said "verification". No
#                     product named itself at all.
#   a CAPTCHA widget  no product named itself either, and what the page did
#                     carry is a widget. A widget is embedded by whatever
#                     served the page; it is not the thing that decided to
#                     serve it, and its vendor is not the bot manager's.
#
# One sentence, written for the first of those, was being handed all three:
# "Open the bot-management rules in a CAPTCHA widget (hCaptcha) and find the
# rule that serves a JavaScript challenge." On a shop running on a hosted
# store platform, four of whose URLs answered with a widget screen, that
# sentence was ungrammatical, named a console the shopkeeper has no login to,
# and stood second in "Start here" priced at half a day of developer time. The
# challenge was the platform's own. There was no rule on that site to open.
#
# So the name is rendered per slot rather than dropped into a sentence written
# for one of them, and what the reader is told to do follows what was actually
# established:
#
#   a product named   name the console, and say in the same step what to do if
#                     the account behind it is not theirs.
#   nothing named     send nobody to a console. Prove it with the loop, then
#                     take that output to whoever hosts the site.
#
# Nothing here can establish who holds the account, so nothing here says it
# does.
# --------------------------------------------------------------------------

# What `detect_challenge` calls an edge that interfered without naming itself.
# Duplicated from `audit_common` deliberately: this file has to tell a name
# that leads somewhere from a name that does not, and `test_challenge_names.py`
# fails if the two strings ever come apart.
UNNAMED_EDGE = "the site's edge"

# Above this share of the fetched URLs the wall stands across the site rather
# than intercepting some of the requests to it.
#
# It decides two things. Severity, because a wall across everything makes every
# other check in this report meaningless while a wall across part of it hides
# that part and no more. And who is likely running it: a rule about who is
# asking refuses the same asker at every URL, since neither the client nor the
# name changed during the crawl - so a wall that took four URLs and let the
# rest through was scoring requests one at a time, which is what protection
# applied by a hosting platform does and not what a rule someone wrote about
# crawler names does.
SITE_WIDE_CHALLENGE_SHARE = 50

# Below this share of the fetched URLs, an interception no request after the
# crawl confirmed is too small a slice of one crawl to lead a report about the
# site. The same number `_check_status_and_indexability` uses further down for
# the same reason: under a tenth of one crawl is a handful of requests, and a
# handful of requests is what a limiter takes when the crawl's own volume trips
# it. Measured: a municipal site's snapshot recorded one path of sixty-one
# challenged, and re-fetching that URL under this audit's own user agent
# returned HTTP 200 with 51,856 bytes of content and no vendor marker.
UNCONFIRMED_CHALLENGE_SHARE = 10

# What the audit concluded about who can change the thing that refused.
WALL_NAMED_CONSOLE = "named-console"
WALL_HOST_LIKELY = "host-likely"
WALL_UNIDENTIFIED = "unidentified"


# --------------------------------------------------------------------------
# Carrying a vendor's code is not being refused by it
#
# `detect_challenge` fires on a vendor's marker appearing anywhere in the HTML
# plus fewer than CHALLENGE_TEXT_CEILING characters of readable text. Both
# halves are things an ordinary page does, and every marker table entry has at
# least one string that a protected site ships on pages nobody was refused:
#
#   the CAPTCHA widgets     `hcaptcha.com/1/api.js`, `h-captcha-response`,
#                           `cf-turnstile` are what a contact or comment form
#                           embeds to keep spam out. One hosted shop platform
#                           serves its own hCaptcha form bundle on *every*
#                           storefront page for exactly that.
#   the sensor scripts      Imperva Incapsula's `_incapsula_resource`,
#                           PerimeterX's `perimeterx` and `_pxhd`, DataDome's
#                           `datadome-` and `dd_cookie` are client-side
#                           telemetry those products inject site-wide.
#                           Cloudflare's `/cdn-cgi/` beacon is the same class.
#   the challenge SDKs      AWS WAF's `awswafcookiedomainlist` ships on
#                           ordinary pages of a protected site so the browser
#                           can hold a token before it is ever asked for one.
#
# So on any of those sites, any page short enough matched. Measured: a shop's
# blog index - a heading, one post with its title, date and excerpt, short
# because the blog has one post - was reported at high severity and high
# confidence inside "5 of 61 fetched URLs (8.2%) returned a verification page
# rather than content". Fetched as `python-requests/2.31` and as Chrome it
# returned 206,276 bytes both times, byte-identical. Nothing was gated.
#
# The cost was not one wrong finding. That finding also states "Those pages
# are excluded from every content check in this report", and the excluded blog
# post is the one page on that site naming where the brand is based and the
# year it was founded - so a second finding told the owner they state neither.
#
# A script tag from a challenge vendor is a page *carrying anti-spam code*. A
# challenge page is a page *the visitor was refused*. This file will not set a
# page aside for the first, and asks four questions to see the second, none of
# which costs a request:
#
#   the server said so    401, 403, 405, 406, 429 or any 5xx is a refusal the
#                         server chose to send. A 2xx is not one.
#   the edge said so      a mitigation response header. Live responses only -
#                         the snapshot keeps seven header names and none of
#                         them is one.
#   the form goes to the  a form whose action host is the challenge provider.
#   provider              An embedded anti-spam widget posts back to the site
#                         it protects; only a challenge posts to the wall.
#   the page is not part  no links into this site at all. A visitor held at
#   of the site           the door is offered no way past it, and that is as
#                         true of a 60 KB interstitial as of a 2 KB one.
#
# The last one is deliberately not a length test, and raising
# CHALLENGE_TEXT_CEILING instead would have been. The blog index that was set
# aside is short and is wired into its site; a real interstitial can be long
# and is wired into nothing. Length does not separate them and linkage does.
# --------------------------------------------------------------------------

# Links to this site's own pages, below which the document offers no way in.
# Three rather than one, because a challenge screen may still link the vendor
# it is served by and a redirect notice may link a single destination, and
# because a page carrying so much as a navigation bar clears it several times
# over.
CHALLENGE_INTERNAL_LINK_FLOOR = 3

# Host labels a challenge is actually served from, matched as bare labels
# rather than as addresses so that `test_marketplace.py` still sees no domain
# name in this file. Read against a form's `action` only, never against an
# iframe's `src`: an hCaptcha or Turnstile widget on an ordinary contact form
# renders inside an iframe on the vendor's own host, so an iframe cannot tell
# the widget from the wall - which is the whole confusion this block exists to
# undo. A form is different: the anti-spam widget's form posts back to the
# site it protects, and only a challenge posts to the provider.
CHALLENGE_PROVIDER_LABELS = (
    "hcaptcha", "recaptcha", "turnstile", "captcha-delivery", "datadome",
    "perimeterx", "px-cdn", "px-cloud", "incapsula", "imperva", "arkoselabs",
    "funcaptcha", "geetest", "awswaf", "distilnetworks", "crowdsec",
)

def _status_is_a_refusal(status):
    """Did the server itself decline to serve this request?

    The whole 5xx range as well as `REFUSED_STATUS`, because 503 is not in that
    set and 503 is what a bot manager under load answers.
    """
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return status in REFUSED_STATUS or status >= 500


def _mitigation_header_fired(headers):
    """Did the edge set a header saying it interfered with this request?

    Asked by calling `detect_challenge` with no body rather than by matching
    header names here. The header shapes it recognises live in the shared
    library, and a second copy of that pattern in this file would drift from
    it in silence. With no HTML and no text no marker and no phrase can match,
    so a name coming back can only have come from a header.
    """
    return bool(headers) and detect_challenge("", "", None, headers) is not None


def _posts_to_a_challenge_provider(actions):
    """Does any form on this page submit to a challenge provider's host?"""
    for action in actions:
        host = urlparse(action or "").netloc.lower()
        if host and any(label in host for label in CHALLENGE_PROVIDER_LABELS):
            return True
    return False


def _record_is_wired_into_the_site(page):
    """Does this snapshot record link into the rest of its own site?

    Absent link data reads as none, which keeps a partial record on the
    refused side: this question only ever overturns a challenge, so the
    conservative answer when it cannot be asked is to leave the challenge
    standing.
    """
    links = page.get("links") or {}
    return (links.get("internal_count") or 0) >= CHALLENGE_INTERNAL_LINK_FLOOR


def _html_is_wired_into_the_site(soup, url):
    """The same question, asked of a live response instead of a record.

    An unknown URL answers no: `same_site("/a", "")` is True, because both
    hosts are empty, so without this a challenge page carrying three relative
    links would clear itself. This question only ever overturns a challenge,
    so not being able to ask it leaves the challenge standing.
    """
    if not url:
        return False
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        target = urljoin(url or "", href)
        if target not in seen and same_site(target, url or ""):
            seen.add(target)
            if len(seen) >= CHALLENGE_INTERNAL_LINK_FLOOR:
                return True
    return False


def _challenge_stands(page):
    """Was the visitor refused at this URL, or is a vendor's code merely in it?

    Returns `(True, what said so)` for a refusal and `(False, what the page
    carried instead)` for a page that ships a challenge vendor's markup and its
    own content. The second string is printed, so it names the measurement.
    """
    status = page.get("status")
    if _status_is_a_refusal(status):
        return True, "HTTP {}".format(status)
    if _posts_to_a_challenge_provider(
            (form or {}).get("action") for form in page.get("forms") or ()):
        return True, "a form submitting to the challenge provider"
    if not _record_is_wired_into_the_site(page):
        return True, "no link into the site"
    links = (page.get("links") or {}).get("internal_count") or 0
    return False, "HTTP {} and {} links into the site".format(
        status if status is not None else "no status", links)


def _sentence_case(text):
    """Upper-case the first letter and leave every other one alone.

    `str.capitalize` lower-cases the rest, and the rest of a title built here
    is a product's own spelling: `hCaptcha` and `AWS WAF` are not `Hcaptcha`
    and `Aws waf`.
    """
    return text[:1].upper() + text[1:] if text else text


def _is_widget_name(vendor):
    """Was this name guessed from a CAPTCHA widget embedded in the page body?"""
    return (vendor or "").lower().startswith("a captcha widget")


def _names_a_console(vendor):
    """Does this name a bot-management product whose rules somebody can open?"""
    return bool(vendor) and not _is_widget_name(vendor) and vendor != UNNAMED_EDGE


def _challenge_page_phrase(vendors):
    """What came back, as a noun phrase that reads in any of the three cases.

    "a verification page from Cloudflare"; "a verification page carrying a
    CAPTCHA widget (hCaptcha)". The widget was not the thing serving the page,
    so it is never put in the slot that says it was.
    """
    served_by = [v for v in vendors if not _is_widget_name(v)]
    carried = [v for v in vendors if _is_widget_name(v)]
    clauses = []
    if served_by:
        clauses.append("from {}".format(" and ".join(served_by)))
    if carried:
        clauses.append("carrying {}".format(" and ".join(carried)))
    return " ".join(["a verification page"] + [" and ".join(clauses)]).strip()


def _challenge_scaffolding_phrase(vendors):
    """What the response body held, for the evidence line.

    Three shapes, because "a CAPTCHA widget (hCaptcha) challenge scaffolding"
    is not English and "the site's edge challenge scaffolding" is not either.
    """
    named = [v for v in vendors if _names_a_console(v)]
    unnamed = [v for v in vendors if v == UNNAMED_EDGE]
    widgets = [v for v in vendors if _is_widget_name(v)]
    parts = []
    if named:
        parts.append("{} challenge scaffolding".format(" and ".join(named)))
    if unnamed:
        parts.append("challenge scaffolding from {}".format(UNNAMED_EDGE))
    if widgets:
        parts.append(" and ".join(widgets))
    return " and ".join(parts) or "challenge scaffolding"


def _who_runs_the_wall(vendors, challenged=0, fetched=0):
    """Which of the two fixes this reader is in a position to carry out.

    Not a claim about who holds the hosting account - no request this audit
    sends can see that. It is a claim about what was identified:

      WALL_NAMED_CONSOLE  a bot-management product named itself, so there is a
                          console to name. Whose account it is is still open,
                          and the step says so.
      WALL_HOST_LIKELY    nothing named itself, and the wall took some of the
                          fetched URLs rather than all of them. Both readings
                          point at protection applied by whoever serves the
                          site, and neither proves it, so the wording says
                          "most likely" and gives the reader the question that
                          settles it.
      WALL_UNIDENTIFIED   nothing named itself and the wall is across the whole
                          crawl. Either case is live; the step is written both
                          ways.
    """
    if any(_names_a_console(v) for v in vendors):
        return WALL_NAMED_CONSOLE
    if fetched and challenged < fetched and pct(challenged, fetched) < SITE_WIDE_CHALLENGE_SHARE:
        return WALL_HOST_LIKELY
    return WALL_UNIDENTIFIED


def _what_was_carried_instead(vendors):
    """Where no product named itself, what the pages did carry.

    Two different readings arrive at the same "nothing named itself", and a
    sentence that describes one of them on a run that saw the other is a
    fabrication: a CAPTCHA widget is markup this audit found in the body, and
    `the site's edge` means the only thing identifying the page was the wording
    it shows a visitor.
    """
    if any(_is_widget_name(v) for v in vendors):
        return ("what the pages carried is a CAPTCHA widget, which is embedded by whatever "
                "served them")
    return ("what identifies these as verification pages is the wording they show a "
            "visitor, which names no product")


def _host_ticket_steps(vendors, wall, ua_verdict, named, challenged, fetched):
    """The fix where no console can be named: prove it, then ask the host.

    The loop is the same measurement the undetermined branch below prints, and
    it is doing a second job here - a support ticket that says "your bot
    protection is blocking AI crawlers" gets a shrug, and one that attaches a
    status line per crawler name, taken from outside the reader's network, is
    something the host can act on.
    """
    if wall == WALL_HOST_LIKELY:
        opening = (
            "Most likely this is bot protection applied by whoever hosts the site rather "
            "than a rule written for the site, so start by settling which: no bot-management "
            "product named itself in these responses - not in its own challenge scaffolding "
            "and not in a mitigation response header - {}, and {} of {} fetched URLs "
            "were intercepted while the rest came back with content. The question that "
            "settles it: does anyone here have a login to a CDN or WAF account for this "
            "site? If not, there is no rule here for you to open and the steps below are "
            "the whole fix.".format(_what_was_carried_instead(vendors), challenged, fetched))
    else:
        opening = (
            "Which side of this you are on was not established, so here it is both ways. No "
            "bot-management product named itself in these responses - not in its own "
            "challenge scaffolding and not in a mitigation response header, and {} - so this "
            "audit cannot name a console for you to open. If you or your developer run a CDN "
            "or WAF account for this site, the rule serving this challenge is in it. If you "
            "do not, because the site is on a hosted platform, this is that platform's own "
            "protection: you cannot see it or change it, and the steps below are the whole "
            "fix.".format(_what_was_carried_instead(vendors)))
    steps = [opening, _name_by_name_probe_step()]
    if named is not None and ua_verdict == UA_NAMES_DIFFER:
        # Already measured, so the ticket carries names rather than a request
        # for the host to go and find out which crawlers they are refusing.
        steps.append(
            "Open a support ticket with whoever hosts the site. Attach that output, name the "
            "URLs listed above, and ask for {} to be allowed through the bot protection - a "
            "second HTTP client was already refused under those names here and answered under "
            "{}, so that is the list and nothing else needs opening.".format(
                ", ".join("`{}`".format(n) for n in named.refused),
                ", ".join("`{}`".format(n) for n in named.served)))
    else:
        steps.append(
            "Open a support ticket with whoever hosts the site. Attach that output, name the "
            "URLs listed above, and ask for the crawler names that came back non-200 to be "
            "allowed through the bot protection. Those names are the whole request: the ones "
            "already answered 200 need nothing.")
    steps.append(
        "If every name in that output is refused, including your own browser's, nothing is "
        "reading the crawler name and the ask changes: quote the same output and ask why "
        "requests from outside a browser are being challenged on these URLs, and for the "
        "reason logged against them.")
    return steps


def _challenge_steps(vendors, ua_verdict, named=None, challenged=0, fetched=0):
    """What to do about a verification page, given what was actually measured.

    Two fixes, not one. Where a bot-management product named itself there is a
    console to open and a rule inside it; where nothing named itself the reader
    may have no console at all, and telling them to open one names a screen
    they cannot reach - see the block above this function.

    The allow-list step is only earned by UA_KEYED and UA_NAMES_DIFFER, the
    two verdicts where the client was held constant and the name was the one
    thing that moved. Everywhere else the list of names to allow is a guess:
    on a charity it was printed at full length, thirteen answer crawlers, on
    an edge that was already answering four of them HTTP 200 and refusing
    three - and the three were named nowhere in the report. So where the cause
    is undetermined the first step is the measurement that names them, and the
    allow rule follows the output of it rather than this file's whole table.

    Under UA_NAMES_DIFFER the audit has already run that measurement itself,
    so `named` carries the two lists and the step names them instead of asking
    the reader to go and find them.
    """
    wall = _who_runs_the_wall(vendors, challenged, fetched)
    if wall != WALL_NAMED_CONSOLE:
        steps = _host_ticket_steps(vendors, wall, ua_verdict, named, challenged, fetched)
        steps.append(
            "Leave the training crawlers where they are. They are separate user agents, "
            "they do not produce citations, and allowing them here buys you nothing.")
        return steps

    vendor_names = " and ".join(v for v in vendors if _names_a_console(v))
    steps = [
        "Open the bot-management rules in {} and find the rule that serves a JavaScript "
        "challenge. It may be matching on unrecognised user agents, or on a bot score "
        "computed from the client's TLS and header fingerprint. If nobody here has a login "
        "for {} - it came with the hosting rather than being something this site set up - "
        "then those rules are your host's, and the support-ticket step below is the fix "
        "instead.".format(vendor_names, vendor_names),
    ]
    if ua_verdict == UA_NAMES_DIFFER and named is not None:
        return steps + _named_block_steps(named)
    if ua_verdict in (UA_KEYED, UA_NAMES_DIFFER):
        # Rendered from the agent table, never written out here. This step used
        # to name "OAI-SearchBot, PerplexityBot, ClaudeBot and Google-Extended"
        # as crawlers that "fetch a page to answer a question". ClaudeBot is a
        # training crawler and Google-Extended is an opt-out token that fetches
        # nothing, so the sentence told owners to reopen a training opt-out for
        # a citation gain of zero - while the robots.txt finding forty lines
        # away, built from the table, got the same distinction right. A crawler
        # name typed into a string is a claim nothing checks.
        steps.append(
            "Add an allow rule for these user agents, keeping your rate limits in place: "
            "{}. Each one either indexes pages so you can be linked in an answer, or "
            "fetches a page because someone just asked about it.".format(
                describe_agents(ANSWER_CRAWLERS, limit=6)))
        # A verification step whose result is the same either way tells the
        # owner nothing, so both outcomes are written down.
        steps.append(
            "Verify with `curl -A {} https://your-site/`. Real HTML back means the allow "
            "rule works; a challenge page back means this rule was not the one refusing "
            "that name.".format(ANSWER_CRAWLERS[0]))
    else:
        steps.append(_name_by_name_probe_step())
        steps.append(
            "Write the allow rule from that output, not from a list of every AI crawler. "
            "The names answered 200 are already getting through and need no rule; the names "
            "refused are the ones to allow, with your rate limits left in place.")
        steps.append(
            "If every name in that output is refused, including your own browser's, the rule "
            "is not reading the crawler name at all. Ask {} for the reason logged against "
            "these requests and look at bot score, TLS fingerprint, IP reputation and rate "
            "limits instead.".format(vendor_names))
    # The console was named; who holds the account was not, and a reader with
    # no login for it has been sent to a screen they cannot open unless this
    # step is here. It is the same fix the unnamed-console branch leads with.
    steps.append(
        "If there is no console for you to open - the bot protection came with your hosting - "
        "open a support ticket with your host instead, naming the URLs listed above and the "
        "crawler names refused, and ask for those names to be allowed through.")
    steps.append(
        "Leave the training crawlers where they are. They are separate user agents, "
        "they do not produce citations, and allowing them here buys you nothing.")
    return steps


def _check_challenge_pages(result, snapshot, ua_verdict=None, recheck=None):
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
    marked = [p for p in snapshot.get("pages") or [] if p.get("challenge")]
    if not marked:
        result.skip("bot-manager-challenge-page",
                    "no page answered with a bot-manager verification page in place of its "
                    "content")
        return

    # The crawl marks a page on a vendor marker plus a short body, and both are
    # things an ordinary page does - see the block above `_challenge_stands`.
    # Every marked page is asked again here whether anything actually refused
    # the visitor, and the ones that carried the vendor's code inside their own
    # content are not counted, not named and not priced.
    challenged, embedded = [], []
    for page in marked:
        stands, why = _challenge_stands(page)
        (challenged if stands else embedded).append((page, why))
    embedded_urls = sorted(p["url"] for p, _ in embedded if p.get("url"))
    if embedded_urls:
        # Read by nothing else in this file. It is here because the crawl's own
        # record still carries `challenge` on these URLs, so `pages_of` in the
        # shared library still hides them from every content check in every
        # other skill - which is how a page stating where a brand is based was
        # excluded and then reported missing. This skill can stop asserting the
        # block; it cannot lift it.
        result.signal("challenge_markers_without_a_refusal", embedded_urls)

    fetched = [p for p in snapshot.get("pages") or [] if p.get("status") is not None]
    if not challenged:
        result.skip(
            "bot-manager-challenge-page",
            "{} of {} fetched URL(s) carry a challenge vendor's markup - {} - but none of "
            "them was refused: each answered with a status the server uses to serve, set no "
            "mitigation header, submitted no form to the vendor, and carried the site's own "
            "links ({}). An anti-spam widget on a contact or comment form, or a bot "
            "manager's site-wide sensor script, is code the page ships and not a wall the "
            "visitor was held at, so no verification page is reported. The crawl's own record "
            "still marks these URLs, so content checks elsewhere in this report did not read "
            "them: {}".format(
                len(embedded), len(fetched),
                ", ".join(sorted({p["challenge"] for p, _ in embedded})),
                "; ".join(why for _, why in embedded[:3]),
                "; ".join(embedded_urls[:3])))
        result.signal("challenge_pages", 0)
        return

    challenged = [p for p, _ in challenged]
    vendors = sorted({p["challenge"] for p in challenged})
    share = pct(len(challenged), len(fetched)) if fetched else 100
    result.signal("challenge_pages", len(challenged))
    result.signal("challenge_vendors", vendors)

    # The named AI crawlers were served and this tool was not, so the
    # verification page is what this audit received, not what a crawler
    # receives. Reporting it as a site defect published the opposite of the
    # truth on a hardware manufacturer, at critical severity, on a single fetched URL.
    served = result.signals.get("crawlers_served_while_audit_refused") or ()
    if ua_verdict == UA_AUDIT_ONLY and served:
        # Three corrections. The comparison may have run on a refused inner
        # page rather than the homepage, so the URL is read from the signal;
        # what it establishes is that those names were served and this client
        # was not, which two different rules produce; and the last sentence
        # counts what got through instead of describing content findings that
        # may not exist.
        # The embedded set comes off this count as well. Those URLs did come
        # back with content, but the crawl's record still marks them and
        # `pages_of` in the shared library still hides them, so counting them
        # here would claim a content check read a page it never saw.
        got_through = len(fetched) - len(challenged) - len(embedded_urls)
        result.skip(
            "bot-manager-challenge-page",
            "{} of {} fetched URL(s) came back as {}, but the user-agent "
            "comparison then requested {} naming {} and got HTTP 200 with the page itself. "
            "This edge answered those crawler names and refused this audit, so the "
            "verification page is a limit on this tool and not a defect in the site. {}".format(
                len(challenged), len(fetched), _challenge_page_phrase(vendors),
                result.signals.get("ua_comparison_url") or "the homepage",
                describe_agents(tuple(served), limit=3),
                "No URL on this site came back with content, so nothing anywhere in this "
                "report describes what the site says" if got_through <= 0 else
                "The {} URL(s) that did come back with content are all any content check in "
                "this report had to read".format(got_through)))
        result.signal("challenge_is_audit_only", True)
        return

    # A rate limiter the crawl itself tripped answers every later request the
    # same way a bot rule does. a museum refused 57 of 60 pages during the
    # crawl and answers a single request normally, and the report called it a
    # bot-management rule. `confirm_dead` in audit_common is the same rule for
    # links: a cheap probe may say alive, it may never say dead on its own.
    if recheck is not None and recheck[0] == REFUSAL_TRANSIENT:
        result.skip(
            "bot-manager-challenge-page",
            "{} of {} fetched URL(s) came back as {} during the crawl, "
            "but {} answered HTTP 200 with the page itself when it was asked once more "
            "afterwards, under this same audit's user agent. That is a rate limiter the "
            "crawl's own request volume tripped, not a rule about who is asking, so it is "
            "not reported as a bot-management block".format(
                len(challenged), len(fetched), _challenge_page_phrase(vendors), recheck[1]))
        result.signal("challenge_was_rate_limiting", True)
        return

    # Whether this run provoked the block it is about to report.
    #
    # A wall that took some of this crawl's requests and let the rest through -
    # same host, same user agent, same run - is two things at once. A rule
    # about who is asking refuses the same asker at every URL. A rate limiter
    # refuses whatever it happens to be holding when the crawl's own request
    # volume trips it, and lets the rest through.
    #
    # Measured: a municipal site's snapshot recorded one path challenged, six
    # words of readable text, vendor named. Re-fetching that exact URL under
    # this audit's own user agent - `BrandAIReadinessAudit/1.0 (+read-only
    # audit)` - returned HTTP 200 with 51,856 bytes of full content and no
    # vendor marker at all. It was published at high severity and led the
    # report. A block this audit provoked by its own request rate is a fact
    # about this run, not a fact about the site.
    #
    # One request separates them and `_recheck_a_refusal` already spends it, so
    # this costs nothing extra: REFUSAL_TRANSIENT returned above, which means a
    # recheck that reaches here came back refused a second time and settles the
    # question. Where no recheck could be made - no network, no budget, no
    # clock left - nothing settled it, and the finding says so rather than
    # claiming a standing rule at high confidence.
    served_normally = [p for p in fetched
                       if p.get("status") == 200 and not p.get("challenge")]
    mostly_served = bool(served_normally) and share < SITE_WIDE_CHALLENGE_SHARE
    unconfirmed = mostly_served and recheck is None
    # Two levers, because they answer two questions and the report reads only
    # one of them. Certainty is `confidence`, and nothing downstream orders by
    # it: `compose_report.score` is severity x reach / effort. So a finding
    # this run could not confirm still led the report as long as it stayed
    # `high`. Severity comes down as well, and only where the intercepted set
    # is too small a slice of one crawl to be a property of the site.
    too_few_to_lead = unconfirmed and share < UNCONFIRMED_CHALLENGE_SHARE
    if unconfirmed:
        result.signal("challenge_unconfirmed_after_the_crawl", True)
    rate_note = "" if not mostly_served else (
        " The same host answered {} of the {} fetched URLs with the page itself during this "
        "same crawl, under this same user agent.{}".format(
            len(served_normally), len(fetched),
            " A rule about who is asking refuses that asker at every URL, so a wall that took "
            "{} and served the rest may instead be a rate limiter this crawl's own request "
            "volume tripped. No request after the crawl had stopped could be made to tell "
            "them apart, so this is a reading of one crawl and not an established standing "
            "rule.".format(len(challenged))
            if unconfirmed else
            " A URL this crawl was turned away from was asked again after the crawl had "
            "stopped, under this same user agent, and was refused again ({} -> HTTP {}), so "
            "this is not a limiter the crawl's own request volume tripped.".format(
                recheck[1], recheck[2])
            if recheck is not None else ""))

    # What the count above deliberately leaves out, said in the finding rather
    # than only in a signal. These URLs carry the same vendor's markup and were
    # not refused, so counting them would have inflated the share the severity
    # is decided from - and on the shop this fix was measured against, they
    # were the whole finding.
    embedded_note = "" if not embedded_urls else (
        " A further {} fetched URL(s) carry the same vendor's markup inside their own "
        "content - {} - and are not counted here: an anti-spam widget or a site-wide sensor "
        "script is code the page ships, not a wall. Examples: {}.".format(
            len(embedded_urls),
            "; ".join(why for _, why in embedded[:2]),
            "; ".join(embedded_urls[:2])))

    # Who can change the thing that refused, decided once and used by the
    # title, the summary, the fix steps and the effort. A CAPTCHA widget's
    # vendor is not a bot manager's name, and the sentences below used to read
    # as though it were.
    wall = _who_runs_the_wall(vendors, len(challenged), len(fetched))
    page_phrase = _challenge_page_phrase(vendors)
    consoles = [v for v in vendors if _names_a_console(v)]
    # What was, and was not, established about who put the wall there. Silent
    # where a product named itself: the fix step names that console and says
    # what to do if the account is not the reader's.
    wall_note = "" if wall == WALL_NAMED_CONSOLE else (
        " No bot-management product named itself in these responses - neither its own "
        "challenge scaffolding nor a mitigation response header - and {}.{}".format(
            _what_was_carried_instead(vendors),
            " With {} of {} fetched URLs intercepted while the rest came back with content, "
            "that reads as protection applied by whoever hosts this site rather than a rule "
            "written for it; no request this audit can send sees the hosting account, so it "
            "is a reading and not a fact.".format(len(challenged), len(fetched))
            if wall == WALL_HOST_LIKELY else
            " Which product is refusing, and whether anyone at this site can open its rules, "
            "was not established."))
    result.add(
        id_hint="bot-manager-serves-a-challenge-page",
        # Two titles, because "served to crawlers" is a claim about clients
        # this run never sent. Only UA_KEYED earns it: there the client was
        # held constant across both requests and the name was the one thing
        # that moved. UA_CLIENT_REFUSED used to earn it too, and does not - a
        # request naming a crawler is still this audit's HTTP client, and on a
        # charity whose edge refused that client under every name, plain curl
        # was answered HTTP 200 under four of the same names.
        #
        # The subject is the verification page in both, because the other
        # subject available here is whatever name `detect_challenge` returned,
        # and a CAPTCHA widget does not serve anything: "a CAPTCHA widget
        # (hCaptcha) serves a verification page" says the embedded thing is
        # the thing that decided to embed it.
        title=_sentence_case(
            "{} is served to crawlers instead of the page itself".format(page_phrase)
            if ua_verdict in (UA_KEYED, UA_NAMES_DIFFER) else
            "{} is returned to this crawler instead of the page itself".format(page_phrase)),
        # A wall across the whole site makes every other check meaningless; a
        # wall on part of it hides that part and no more. And a wall that took
        # part of one crawl while the same host served the rest, with no
        # request after the crawl to confirm it, has not been shown to be a
        # standing rule at all - so it is not priced as one and does not lead
        # the report.
        severity=("critical" if share >= SITE_WIDE_CHALLENGE_SHARE else
                  "medium" if too_few_to_lead else "high"),
        confidence="medium" if unconfirmed else "high",
        # The status is read from the records, not asserted. It said "a 2xx
        # response" whatever the pages actually returned, and a bank's report
        # then described its 403s as 2xx - contradicted by the next finding in
        # the same report, which quoted the real status. An evidence sentence
        # that states a value it did not read is a fabrication however small.
        # What received the challenge is this audit's HTTP client. Whether a
        # named AI crawler receives it too is a separate measurement, and the
        # last sentence says whether this run made it. Measured on a museum
        # and a hardware manufacturer, the same user-agent string was answered
        # 200 through curl and through urllib and 403 through the client this
        # audit uses, so "unrecognised user agent" is one explanation of a
        # challenge and the client's TLS and header fingerprint is another.
        evidence="{} of {} fetched URLs ({}%) returned a verification page rather than "
                 "content: HTTP {} carrying {} and under {} "
                 "characters of readable text. Examples: {}. Those pages are excluded from "
                 "every content check in this report, because they describe the crawler's "
                 "reception and not the site.{}{}{}{}".format(
                     len(challenged), len(fetched), share,
                     ", ".join(str(s) for s in sorted(
                         {p.get("status") for p in challenged if p.get("status")})) or "no status",
                     _challenge_scaffolding_phrase(vendors), CHALLENGE_TEXT_CEILING,
                     "; ".join(p["url"] for p in sorted(challenged, key=lambda x: x["url"])[:3]),
                     " The same request was refused under AI crawler names as well, from two "
                     "different HTTP clients, so this is not a property of one HTTP library "
                     "- but which crawler names this edge refuses was still not established, "
                     "because an edge refusing this address refuses both clients alike. The "
                     "fix steps below start with the one measurement that establishes it."
                     if ua_verdict == UA_CLIENT_REFUSED else
                     " A second HTTP client, held constant across every request, was refused "
                     "under {} and answered with the page under {}, so this edge reads the "
                     "crawler name.".format(
                         ", ".join("`{}`".format(n) for n in
                                   (result.signals.get("edge_refuses_crawler_names") or [])),
                         ", ".join("`{}`".format(n) for n in
                                   (result.signals.get("edge_serves_crawler_names") or [])))
                     if ua_verdict == UA_NAMES_DIFFER else
                     " A second HTTP client was answered with the page under every crawler "
                     "name it tried and under this audit's own name, so what is refused here "
                     "is the client rather than any name."
                     if ua_verdict == UA_CLIENT_SCORED else
                     " An AI crawler name was refused where this audit's own request from the "
                     "same client was not, so this edge reads the name."
                     if ua_verdict == UA_KEYED else
                     " Whether a named AI answer crawler receives this same page was not "
                     "established: the comparison requests that would show it could not be "
                     "made. The rule may read the user-agent string, or it may score the "
                     "HTTP client's TLS and header fingerprint or the network it came "
                     "from.",
                     rate_note, wall_note, embedded_note),
        mechanism="A", root_cause="bot-manager-block",
        summary="Allow the AI answer crawlers through {} so they receive the page, not the "
                "challenge.".format(" and ".join(consoles))
                if wall == WALL_NAMED_CONSOLE else
                "Prove the block with one curl loop, then ask whoever hosts this site to let "
                "the AI answer crawlers through the bot protection.",
        how_to_fix=_challenge_steps(vendors, ua_verdict, _named_probe_from_signals(result),
                                    len(challenged), len(fetched)),
        # Priced from the steps this finding actually prints. Where no console
        # was identified they are a curl loop and a support ticket - under an
        # hour, and nobody's developer - and "half a day to a day" of developer
        # time priced a console visit that the reader may have no login for.
        effort="medium" if wall == WALL_NAMED_CONSOLE else "low",
        owner="developer" if wall == WALL_NAMED_CONSOLE else "content owner",
        rationale="An assistant fetching this page receives a verification screen. "
                  "It cannot solve the challenge, so it reads nothing and cites nothing. To the "
                  "site's analytics this looks like a bot being correctly turned away; to every "
                  "AI answer engine it looks like a site with no content.",
        affected_pages=[p["url"] for p in sorted(challenged, key=lambda x: x["url"])],
    )


def _check_robots_reachable(result, robots, snapshot=None):
    result.check("robots-txt-reachable")
    status = robots.get("status")
    not_serving = (snapshot or {}).get("site_not_serving") or {}
    if not_serving and status == not_serving.get("status"):
        # The address answered, so "reachable" was left to pass - and on a
        # store answering 402 everywhere, what came back at that address was
        # the same refusal every other address gave, not a robots.txt. A pass
        # here says the file was read. Nothing was read.
        for name, measured in (
                ("robots-txt-reachable", "whether robots.txt is published and readable"),
                ("robots-txt-rules", "what robots.txt allows and disallows")):
            result.skip(name, _not_measured_reason(
                snapshot, measured,
                "the address answered HTTP {} like every other address on this "
                "site".format(status)))
        return

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

    # A robots.txt that came back as an HTML page is not a robots.txt with bad
    # lines in it. crawl.py records "robots.txt returned HTML, not plain text;
    # treated as absent" and stops parsing, so no line was ever read - and this
    # branch still published "robots.txt contains lines crawlers will ignore",
    # evidence saying the file was treated as absent, and the fix "open
    # robots.txt and fix the lines listed in the evidence". A file treated as
    # absent has no lines to open. It was filed under root cause `robots-block`
    # while nothing was blocked. The site it happened on answers 200 with HTML
    # for every unknown path, /robots.txt included, so there is no file there.
    parse_errors = [error for error in robots.get("errors") or []
                    if not str(error).startswith(ROBOTS_HTML_ERROR)]
    if robots.get("errors") and not parse_errors:
        absent = ("no robots.txt is published at {}: the request returned HTTP 200 carrying "
                  "an HTML page rather than plain text, which is what a site that answers "
                  "200 for every unknown path returns at that address".format(
                      robots.get("url")))
        result.skip(
            "robots-txt-reachable",
            "{}. There are no lines in it to correct. Publishing no robots.txt is a valid "
            "state meaning no crawler restrictions, and is not a defect; if you want one, "
            "create a plain-text file at that address holding a `User-agent: *` group and a "
            "`Sitemap:` line".format(absent))
        result.skip("robots-txt-rules",
                    "{}, so there are no rules to evaluate and none could be read".format(
                        absent))
        return

    if parse_errors:
        result.add(
            id_hint="robots-txt-parse-errors",
            title="robots.txt contains lines crawlers will ignore",
            severity="low", confidence="high",
            evidence="{} problem line(s): {}".format(
                len(parse_errors), "; ".join(parse_errors[:3])),
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


def _no_robots_rules_reason(robots):
    """Why there are no robots.txt rules to evaluate, in the owner's own terms.

    "no parsable robots.txt rules on this site" covered five different
    situations and read as an accusation in every one of them. It was printed
    four times in a single report about a site whose robots.txt is one valid
    `Sitemap:` line - a correct file, doing exactly what its author intended -
    and a business owner reads "no parsable rules" as "your robots.txt is
    broken". Five situations, five sentences.

    Returns `(reason, established)`. `established` is False where the file was
    never read, because the old sentence asserted "nothing is disallowed" about
    a robots.txt this crawler had been refused - the one claim this marketplace
    refuses to make anywhere else.
    """
    status = robots.get("status")
    if status is None:
        return ("robots.txt could not be fetched ({}), and a request that never arrived is "
                "not evidence about what the file says".format(
                    robots.get("fetch_error") or "no response"), False)
    if status in REFUSED_STATUS:
        return ("the request for robots.txt was refused by the site's edge (HTTP {}), which "
                "is evidence that this crawler was not allowed to look and never that "
                "nothing is disallowed".format(status), False)
    if any("HTML, not plain text" in error for error in robots.get("errors") or []):
        return ("robots.txt returns an HTML page rather than plain text, which means whatever "
                "the site serves at that address is not a robots.txt any crawler can read",
                False)
    if status != 200:
        return ("no robots.txt is published: {} returns HTTP {}, which is a valid state "
                "meaning no crawler restrictions and is not a defect".format(
                    robots.get("url"), status), True)
    declared_sitemaps = robots.get("sitemaps") or []
    return ("robots.txt is published at {} and is a valid file that sets no crawler "
            "rules{}".format(
                robots.get("url"),
                " - its only directive is the `Sitemap:` line naming {}".format(
                    declared_sitemaps[0]) if declared_sitemaps else ""), True)


# Plural and near-synonym forms of rules the shared benign vocabulary already
# excludes, and the reason this list is a list rather than a place.
#
# `_BENIGN_DISALLOW` in robots_parser.py matches an exact token followed by a
# separator, a digit or the end of the rule, so `checkout` is excused and
# `checkouts/` is not, and `order` is excused and `orders` is not. The
# commonest hosted store platform ships `Disallow: /checkouts/` and
# `Disallow: /orders` in the robots.txt it generates for every shop on it,
# beside `Disallow: /checkout` and `Disallow: /account` which the list does
# excuse. So "robots.txt disallows 2 paths that look like real content" fired
# on every storefront on that platform - ranked second in Start here on a
# coffee shop, telling the owner to unblock the customer's own checkout and
# order-status pages.
#
# The same boundary is repeated here for the same reason it exists there:
# `print` once swallowed a printer retailer's `/printers`, so a token may only
# be excused where the rule ends or a separator follows.
#
# Words with a second meaning are deliberately left out. `bag` and `bags` are
# a handbag shop's product listing before they are a synonym for a cart, and
# `tests` is a laboratory's. A rule blocking one of those is a rule worth
# reporting, and swallowing real content is the more expensive of the two
# mistakes.
# Named in the finding's own evidence, so a reader holding the report against
# their robots.txt can see which vocabulary excused a rule they expected to
# find listed.
BENIGN_CATEGORIES = (
    "admin, cart, checkout, search, asset and machine-file paths, together with the "
    "customer-account and order-status routes and the plural forms of all of them "
    "(`/checkouts/`, `/orders`, `/accounts`, `/wishlist`), the signed-in account "
    "screens (`/dashboard`, `/settings`, `/preferences`), any rule naming a file by "
    "its extension rather than a page (`/me.json`, `/strings.xml`), the metadata "
    "directory of a version-control working copy, and the machinery a site runs on - "
    "password-reset, login, registration and newsletter-subscription handlers, "
    "WordPress's own `wp-` endpoints, trackbacks, cron, feeds and Windows web server system "
    "handlers")

# The machinery a site runs on, recognised by the words in the rule rather than
# by a list of sites. Two robots.txt files were reported at confident tier,
# second in Start here, as disallowing "paths that look like real content":
# `/ForgetPassword.aspx` and `/Newsletter_Unsubscribe.aspx` on one,
# `/trackback/` and `/wp-cron.php` on the other. Every one of them is a handler
# no reader is ever sent to, and blocking it is the file doing its job.
#
# Read as words, so `ForgetPassword` and `Newsletter_Unsubscribe` split into
# their parts, and two neighbouring words are joined as well (`Sign` `In`).
# Whole words only: `/feedback` is a page and `/feed` is not, and `/passwords`
# guide pages on a security blog are still reported because the word is
# plural there and a handler never is.
_PLUMBING_WORDS = frozenset({
    "password", "passwd", "login", "logon", "logout", "logoff", "signin",
    "signout", "signup", "register", "registration", "unsubscribe", "subscribe",
    "subscription", "optout", "trackback", "xmlrpc", "cron", "feed", "rss",
    "atom", "captcha", "forgotpassword", "resetpassword",
})
# Windows web server's own handler extensions, which serve resources and web services
# rather than pages. `.aspx` is a page and is not here.
_PLUMBING_EXTENSIONS = (".axd", ".ashx", ".asmx")
_RULE_WORD_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def _names_plumbing(rule):
    """Does this robots rule close a handler rather than a page? See above."""
    path = str(rule or "").split("?")[0].strip("/*$")
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False
    for segment in segments:
        low = segment.lower()
        if low.startswith("wp-") or low.endswith(_PLUMBING_EXTENSIONS):
            return True
        words = [w.lower() for w in _RULE_WORD_RE.findall(segment.split(".")[0])]
        pairs = {a + b for a, b in zip(words, words[1:])}
        if (set(words) | pairs) & _PLUMBING_WORDS:
            return True
    return False



# The share of the crawl that has to sit behind a section block before it is
# reported as high rather than medium.
#
# Severity here follows what is behind the path, and the only evidence this
# audit has about that is the pages it already fetched. It could fetch them
# because the rule names other crawlers and not this one, which is the same
# fact that makes the block worth reporting: the section is reachable, and the
# agents that build answers are the ones shut out of it.
#
# A rule closing a prefix that holds a tenth of the crawled pages has been
# shown by those pages to close a section of the site. A rule closing a prefix
# the crawl never reached may close the catalogue or nothing, and calling that
# high would be asserting the part nobody measured. Two pages as well as a
# tenth, because on a nine-page crawl one page clears a tenth on its own and
# one page is not a section.
SECTION_BLOCK_HIGH_SHARE = 0.10
SECTION_BLOCK_HIGH_PAGES = 2

# How much of the fix the paste-ready block spells out. Both caps are above
# the size of the thing they cap on any real file - there are thirteen answer
# crawlers in this skill's table altogether - so a reader pasting the snippet
# gets the whole fix rather than the first six lines of it. The agent cap used
# to be six, which on a file naming nine crawlers left three of them blocked
# by a block labelled paste-ready.
SNIPPET_AGENT_LIMIT = 14
SNIPPET_PATH_LIMIT = 12


def _section_blocks(robots, tokens):
    """`{agent: [rules]}` for the agents whose own named group closes a section.

    `section_disallows` drops any agent that is shut out of the whole site, so
    an agent can appear in this map or in the site-wide finding and never in
    both. That is the precedence: a section inside a whole-site block is the
    same defect as the whole-site block and is reported once.
    """
    blocks = {}
    for token in tokens:
        rules = section_disallows(robots, token)
        if rules:
            blocks[token] = rules
    return blocks


def _named_in_robots(robots, tokens):
    """The agents in `tokens` this robots.txt writes a `User-agent` line for."""
    declared = {name for grp in robots.get("groups") or [] for name in grp["agents"]}
    return [token for token in tokens
            if any(name != "*" and token.lower().startswith(name) for name in declared)]


def _pages_under(pages, rules):
    """URLs of crawled pages that sit behind one of `rules`."""
    return sorted({page["url"] for page in pages or []
                   if page.get("url")
                   and any(rule_matches_path(rule, path_of(page["url"])) for rule in rules)})


def _tokens(names, limit=6):
    """A plain list of user-agent tokens, saying how many were left out."""
    shown = ", ".join("`{}`".format(name) for name in names[:limit])
    return shown if len(names) <= limit else "{} and {} more".format(
        shown, len(names) - limit)


def _no_ai_block_reason(robots, tokens, label):
    """Why no block was found, naming both shapes that were actually tested.

    A check that tests one shape of block and reports the other shape as clean
    is the worst thing in this file's history. `robots-blocks-ai-answer-
    crawlers` tested only `Disallow: /`, found none, and appeared under
    "checks that ran and found nothing wrong" on a site whose one robots.txt
    group named eleven crawlers and closed the shop to all of them. A bare
    entry in that list carries no wording, so there was nowhere for the
    qualification to go; declining says both what was tested and what was
    found.
    """
    named = _named_in_robots(robots, tokens)
    disallowing = [token for token in named
                   if (group_for(robots, token) or {}).get("disallow")]
    if not named:
        detail = "robots.txt writes a `User-agent` line for none of them"
    elif not disallowing:
        detail = ("robots.txt writes a `User-agent` line for {} of them - {} - and those "
                  "groups disallow nothing".format(len(named), _tokens(named)))
    else:
        # Not "disallows no real content": a group naming one of these agents
        # may well carry a `Disallow` line, and the sentence has to say why
        # that line was not reported rather than deny that it exists.
        detail = ("robots.txt writes a `User-agent` line for {} of them - {} - and what "
                  "those groups disallow is either ordinary hygiene or a path the "
                  "`User-agent: *` group closes to every crawler as well".format(
                      len(named), _tokens(named)))
    return ("nothing blocks the {count} {label} in this skill's crawler table, and both "
            "shapes of block were tested against every one of them: whether the agent is "
            "disallowed from `/`, and whether a group naming it closes a section of the "
            "site that no `User-agent: *` rule closes. {detail}".format(
                count=len(tokens), label=label, detail=detail))


def _add_answer_section_finding(result, robots, pages, section_answer, section_training):
    """A section closed to the crawlers that build answers, and to nobody else.

    Severity follows what is behind the path rather than how many paths there
    are: a group closing `/store/` on a shop and a group closing a two-page
    corner of a site produce the same robots.txt line and cost wildly
    different amounts, and the pages this crawl already fetched are the only
    evidence available about which one it is.
    """
    agents = sorted(section_answer)
    paths = sorted({rule for rules in section_answer.values() for rule in rules})
    crawled = [page["url"] for page in pages or [] if page.get("url")]
    inside = _pages_under(pages, paths)
    share = (float(len(inside)) / len(crawled)) if crawled else 0.0
    high = (len(inside) >= SECTION_BLOCK_HIGH_PAGES
            and share >= SECTION_BLOCK_HIGH_SHARE)
    inferred = undocumented_among(agents)
    # The evidence names every path the title counts, or says how many it did
    # not name. A title saying "6 sections" over a list of five is the reader
    # counting the report's own evidence and finding it short - the same defect
    # turned up twice in the report composer, both times a title counting one
    # set and the sentence under it listing another.
    path_list = _every_path_or_a_named_sample(paths)

    if inside:
        # The number that turns "a path is disallowed" into "this proportion of
        # your catalogue is invisible". Without it an owner cannot tell a rule
        # covering three pages from one covering the whole shop, and both are
        # one line of the same file.
        scale = ("{} of the {} pages this crawl read sit under {}, so the rule closes a "
                 "populated part of the site rather than an empty prefix.".format(
                     len(inside), len(crawled), path_list))
    else:
        scale = ("This crawl reached no page under {}, so how much sits behind them is "
                 "unverified and the severity below does not assume it is large.".format(
                     path_list))

    # Every other rule those agents' own groups carry. Named in the steps
    # below, because the snippet deliberately does not touch them and the
    # reader has to be able to see that it did not.
    kept = sorted({rule.strip()
                   for token in agents
                   for rule in (group_for(robots, token) or {}).get("disallow", [])
                   if rule.strip() and rule.strip() not in paths})
    steps = [
        "Open /robots.txt and find the group whose `User-agent` lines name {}.".format(
            _tokens(agents)),
        # An `Allow` of the same path, not a deletion, and not `Allow: /`.
        # The snippet under this finding used to be the nine user-agent lines
        # followed by `Allow: /`, and a reader pasting it over the group it
        # names reopened every other path the group closes - on one media site
        # that was the site's API, its GraphQL endpoint, its build-data
        # directory and two signed-in JSON endpoints, none of which this
        # finding asked anyone to open and all of which the step below tells
        # the owner to leave alone. A snippet that contradicts the instruction
        # printed above it is worse than no snippet.
        "Add an `Allow:` line for each of {} to that group, spelt exactly as the "
        "`Disallow:` line spells it. Allow and Disallow of the same path is a tie, and "
        "every compliant crawler gives a tie to Allow, so the path opens and every "
        "other rule in the group goes on working.".format(path_list),
        ("Leave the group's other `Disallow` lines exactly as they are - {}. The snippet "
         "below adds lines and removes none; it is not a replacement for the "
         "group.".format(", ".join("`{}`".format(rule) for rule in kept[:6]))
         if kept else
         "The group closes nothing else, so there is nothing else in it to preserve."),
        "Leave the `User-agent: *` group exactly as it is: this changes what these named "
        "crawlers may fetch and nothing else.",
    ]
    if section_training:
        # The distinction the rest of this file exists to protect. The same
        # group usually names training crawlers too, and the fix must not
        # quietly reopen a training opt-out in order to buy back a citation.
        steps.append(
            "Keep the training crawlers blocked if that is what you meant - {} - because "
            "blocking those is a rights decision about training data and costs no "
            "citation. The snippet splits the one group into two so both decisions "
            "survive.".format(_tokens(sorted(section_training))))
    steps.append(
        "Re-test with a robots.txt checker under each of those user-agent strings, and "
        "confirm a URL under {} now reads as allowed.".format(paths[0]))

    # Scoped to the paths this finding is about. `Allow: /` here was the whole
    # of the fix, and it is only the right rule on a file that closes nothing
    # else - which is not the file this finding fires on, because the finding
    # fires on a group that closes paths.
    lines = ["# Answer and search crawlers: allowed into the sections below.",
             "# Add these lines to the existing group; do not replace it. Every other",
             "# Disallow line in that group stays exactly as it is."]
    lines += ["User-agent: {}".format(token) for token in agents[:SNIPPET_AGENT_LIMIT]]
    lines += ["Allow: {}".format(rule) for rule in paths[:SNIPPET_PATH_LIMIT]]
    if section_training:
        training_paths = sorted({rule for rules in section_training.values()
                                 for rule in rules})
        lines.append("")
        lines.append("# Training crawlers: the opt-out already in this file, unchanged.")
        lines += ["User-agent: {}".format(token) for token in sorted(section_training)[:6]]
        lines += ["Disallow: {}".format(rule) for rule in training_paths[:4]]

    result.check("robots-blocks-ai-answer-crawlers")
    result.add(
        id_hint="robots-blocks-answer-crawler-sections",
        title="robots.txt closes {} to {}".format(
            plural(len(paths), "section of the site", "sections of the site"),
            plural(len(agents), "AI answer crawler it names",
                   "AI answer crawlers it names")),
        severity="high" if high else "medium",
        # The crawl either walked into the blocked section or it did not. Where
        # it did not, the rule is still there and still closes the path, but
        # what is behind it was never seen, and a confident sentence about the
        # size of the loss would be about the part that was not measured.
        confidence="high" if inside else "medium",
        evidence="Disallowed for {}: {}. No `User-agent: *` rule closes {}, so an "
                 "ordinary search-engine crawler may fetch what these agents may not - "
                 "the block is a decision taken about AI crawlers and not the site's "
                 "policy for every crawler. {} Ordinary hygiene rules were set aside "
                 "before this list ({}), and so was any path the `*` group closes as "
                 "well.{}".format(
                     describe_agents(agents), path_list,
                     "them" if len(paths) > 1 else "it", scale, BENIGN_CATEGORIES,
                     " The role of {} is inferred from observed behaviour, not from a "
                     "published statement by its operator.".format(", ".join(inferred))
                     if inferred else ""),
        mechanism="A", root_cause="robots-block",
        affected_pages=inside,
        summary="Open the blocked section to the AI crawlers that produce citations, and "
                "keep any training opt-out in a group of its own.",
        how_to_fix=steps,
        effort="low", owner="developer",
        rationale="Each of these either indexes pages so the brand can be linked in an "
                  "answer, or fetches a page live because someone just asked about it. "
                  "A section they may not fetch cannot be quoted however good the pages "
                  "in it are, and because the rule names crawlers rather than everybody, "
                  "nothing in an ordinary search report shows it.",
        snippet="\n".join(lines),
    )


def _add_training_section_finding(result, section_training, answer_side_blocked):
    """The same rule read for the agents whose block is a rights decision.

    Reported at `info` and separately, for the reason the site-wide training
    finding gives: blocking a training crawler is a choice plenty of owners
    make on purpose, and merging it into the citation defect is how an earlier
    version of this skill came to tell owners to reopen a training opt-out.
    Without this finding the training half of a mixed group would produce
    nothing at all, and `robots-blocks-ai-training-crawlers` would then certify
    as clean a file that names those agents and closes a section to them.
    """
    agents = sorted(section_training)
    paths = sorted({rule for rules in section_training.values() for rule in rules})
    # The evidence names every path the title counts, or says how many it did
    # not name. A title saying "6 sections" over a list of five is the reader
    # counting the report's own evidence and finding it short - the same defect
    # turned up twice in the report composer, both times a title counting one
    # set and the sentence under it listing another.
    path_list = _every_path_or_a_named_sample(paths)
    counterparts = [name for name in answer_side_counterparts(agents)
                    if name not in answer_side_blocked]

    result.check("robots-blocks-ai-training-crawlers")
    result.add(
        id_hint="robots-blocks-training-crawler-sections",
        title="robots.txt closes {} to AI training crawlers (this is often "
              "deliberate)".format(
                  plural(len(paths), "section of the site", "sections of the site")),
        severity="info", confidence="high",
        evidence="Disallowed for {}: {}. These collect training corpora, or are opt-out "
                 "tokens that fetch nothing at all, rather than fetching pages to answer "
                 "a live question, so closing a section to them is a rights decision "
                 "rather than a citation defect. No `User-agent: *` rule closes the same "
                 "paths.".format(describe_agents(agents), path_list),
        mechanism="A", root_cause="robots-block",
        summary="Confirm the partial training opt-out is intentional; it does not by "
                "itself stop the brand being cited.",
        how_to_fix=[
            "Confirm with whoever owns content rights that keeping {} out of training "
            "corpora is the intended policy.".format(path_list),
            "If it is, no change is needed here. Do not open these paths to fix a "
            "citation problem - these agents are not the ones that produce citations.",
            ("The agents from the same companies that do decide citation are {}, and "
             "robots.txt lets them into these paths. Keep it that way.".format(
                 ", ".join(counterparts[:6]))
             if counterparts else
             "The answer-side agents from these same companies are blocked from the same "
             "paths, which is reported separately and is the one worth reconsidering."),
            "If the opt-out was not intended, remove those `Disallow` lines from the "
            "group naming these agents.",
        ],
        effort="low", owner="marketing",
        rationale="Blocking training collection limits what a model absorbs offline, but "
                  "retrieval-time citation depends on the search and live-fetch agents, "
                  "which are separate user agents with their own rules. Reported for "
                  "awareness, not as a defect.",
    )


def _check_robots_blocks(result, robots, origin, pages=None):
    result.check("robots-blocks-all-crawlers")
    result.check("robots-blocks-ai-answer-crawlers")
    result.check("robots-blocks-ai-training-crawlers")
    result.check("robots-blocks-content-paths")

    if robots.get("status") != 200 or not robots.get("groups"):
        reason, established = _no_robots_rules_reason(robots)
        # One sentence, four times, saying the same unhelpful thing. Each check
        # now answers its own question, and a file that was never read answers
        # none of them - the old line claimed "nothing is disallowed" about a
        # robots.txt this crawler had been refused.
        for name, known, unknown in (
                ("robots-blocks-all-crawlers",
                 "no crawler is shut out of the site",
                 "whether any crawler is shut out of the site could not be determined"),
                ("robots-blocks-ai-answer-crawlers",
                 "no AI answer crawler is named or blocked",
                 "whether any AI answer crawler is blocked could not be determined"),
                ("robots-blocks-ai-training-crawlers",
                 "no AI training crawler is named or blocked",
                 "whether any AI training crawler is blocked could not be determined"),
                ("robots-blocks-content-paths",
                 "no path is closed to any crawler",
                 "whether any path is closed to crawlers could not be determined")):
            result.skip(name, "{}, so {}".format(reason, known if established else unknown))
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

    # The second shape of block. The two tests above ask only whether an agent
    # is shut out of `/`; `robots-blocks-content-paths` below reads only the
    # `User-agent: *` group. A group naming AI crawlers and disallowing one
    # path is neither, and a file whose single group named eleven crawlers and
    # carried `Disallow: /store/` was reported clean by the first and excused
    # by the second - on a shop, where that path is every product, price and
    # stock state the site has.
    section_answer = _section_blocks(robots, ANSWER_CRAWLERS)
    section_training = _section_blocks(robots, TRAINING_CRAWLERS)
    result.signal("section_blocked_answer_crawlers", sorted(section_answer))
    result.signal("section_blocked_paths",
                  sorted({rule for rules in section_answer.values() for rule in rules}))

    # Declined before any finding is raised, so that a check with nothing to
    # say is recorded as having answered its own question rather than being
    # swept up by a sibling that fired - and so that the reason names both
    # shapes. A bare entry under "checks that ran and found nothing wrong"
    # carries no wording at all, and that is exactly how a check testing only
    # for `Disallow: /` came to certify a site whose shop was closed to every
    # answer crawler it names.
    if not blocked_answer and not section_answer:
        result.skip("robots-blocks-ai-answer-crawlers",
                    _no_ai_block_reason(robots, ANSWER_CRAWLERS,
                                        "search and live-fetch agents"))
    if not blocked_training and not section_training:
        result.skip("robots-blocks-ai-training-crawlers",
                    _no_ai_block_reason(robots, TRAINING_CRAWLERS,
                                        "training crawlers and opt-out tokens"))

    if blocked_answer:
        result.check("robots-blocks-ai-answer-crawlers")
        named = [name for name in blocked_answer if group_for(robots, name)
                 and name.lower() in [a for g in robots["groups"] for a in g["agents"]]]
        inferred = undocumented_among(blocked_answer)
        result.add(
            id_hint="robots-blocks-answer-crawlers",
            title="robots.txt blocks {} AI answer crawler{} from the homepage".format(
                len(blocked_answer), "" if len(blocked_answer) == 1 else "s"),
            severity="high" if len(blocked_answer) >= 2 else "medium",
            confidence="high",
            # Every name carries its operator and that operator's own statement
            # of what it does. An earlier version printed bare tokens under a
            # sentence claiming all of them fetched pages to build cited
            # answers, which was false for five of them.
            evidence="Disallowed at `/`: {}. {}{}".format(
                describe_agents(blocked_answer),
                "Named explicitly in robots.txt: {}.".format(", ".join(named[:8])) if named
                else "These fall under a wildcard rule rather than being named.",
                " The role of {} is inferred from observed behaviour, not from a published "
                "statement by its operator.".format(", ".join(inferred)) if inferred else ""),
            mechanism="A", root_cause="robots-block",
            summary="Allow the AI answer crawlers you want to be cited by; keep blocks only where "
                    "you have deliberately opted out.",
            how_to_fix=[
                "Decide which of these you want quoting your pages: {}.".format(
                    describe_agents(blocked_answer, limit=6)),
                "For each one you want, add an explicit allow group in /robots.txt (see snippet).",
                "Leave blocks in place only where the opt-out is a deliberate rights decision, "
                "and note that decision somewhere your team will find it later.",
                "Allowing these does not opt you back in to model training: the training "
                "crawlers are separate user agents and keep whatever rules you gave them.",
                "Re-test with a robots.txt checker using each crawler's user-agent string.",
            ],
            effort="low", owner="developer",
            rationale="Each of these either indexes pages so the brand can be linked in an "
                      "answer, or fetches a page live because someone just asked about it. "
                      "None of them is a training crawler. A brand they cannot fetch cannot "
                      "be quoted, however good the content is.",
            snippet="\n\n".join(
                "User-agent: {}\nAllow: /".format(name) for name in blocked_answer[:4]
            ),
        )

    if section_answer:
        _add_answer_section_finding(result, robots, pages, section_answer, section_training)

    if blocked_training:
        result.check("robots-blocks-ai-training-crawlers")
        # The useful sentence here is not "you blocked a training crawler".
        # It is: that is your decision, and here are the agents from the same
        # companies that decide whether you get cited - check those are open.
        # Three situations, and the old two-way branch had a sentence for two.
        # Where these operators run no answer-side agent at all - a file
        # closing only a web-archive crawler and a video platform's scraper -
        # the list is empty for a reason that has nothing to do with blocking,
        # and the `else` printed "Every answer-side agent from these same
        # companies is also blocked, which is reported separately" on a site
        # where nothing else was blocked and nothing was reported.
        same_operators = answer_side_counterparts(blocked_training)
        counterparts = [name for name in same_operators if name not in blocked_answer]
        if counterparts:
            counterpart_step = (
                "The agents from the same companies that do decide citation are {}, and "
                "robots.txt already lets them in. Keep it that way.".format(
                    ", ".join(counterparts[:6])))
        elif same_operators:
            counterpart_step = (
                "Every answer-side agent from these same companies is also blocked, which is "
                "reported separately and is the one worth reconsidering.")
        else:
            counterpart_step = (
                "None of these companies runs a separate search or live-fetch agent that this "
                "audit knows of, so this opt-out closes nothing an answer engine reads.")
        result.add(
            id_hint="robots-blocks-training-crawlers",
            title="robots.txt blocks AI training crawlers (this is often deliberate)",
            severity="info", confidence="high",
            evidence="Disallowed at `/`: {}. These collect training corpora, or are opt-out "
                     "tokens that fetch nothing at all, rather than fetching pages to answer "
                     "a live question.".format(describe_agents(blocked_training)),
            mechanism="A", root_cause="robots-block",
            summary="Confirm the training-crawler opt-out is intentional; it does not by itself "
                    "stop the brand being cited.",
            how_to_fix=[
                "Confirm with whoever owns content rights that opting out of training corpora "
                "is the intended policy.",
                "If it is, no change is needed here. Do not allow these back in to fix a "
                "citation problem - they are not the agents that produce citations.",
                counterpart_step,
                "If the opt-out was not intended, remove these user-agent groups from "
                "/robots.txt.",
            ],
            effort="low", owner="marketing",
            rationale="Blocking training collection limits what a model absorbs "
                      "offline, but retrieval-time citation depends on the search and "
                      "live-fetch agents, which are separate user agents with their own "
                      "rules. Reported for awareness, not as a defect.",
        )

    if section_training:
        _add_training_section_finding(result, section_training,
                                      blocked_answer + list(section_answer))

    # Filtered once, read three times below. Filtering only `content_blocks`
    # would push `/checkouts/` and `/orders` into `set_aside`, whose sentence
    # calls what it holds "deeper or wildcard rule(s) too specific to judge
    # from the path alone" - and `/orders` is a single segment with no
    # wildcard, so that sentence would be false about it.
    # The machinery a site runs on comes out here too, with the shared benign
    # vocabulary, so it reaches neither the finding nor `set_aside`. See
    # `_names_plumbing`.
    substantive = [rule for rule in substantive_disallows(robots, "*")
                   if not _names_plumbing(rule)]
    content_blocks = [rule for rule in substantive if rule not in ("/", "/*")]
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
                     "before this list was drawn up: {}, because blocking those is normal; "
                     "any rule "
                     "containing a wildcard, because it usually filters query strings rather "
                     "than blocking a section; and any path more than one segment deep. The "
                     "paths above were not fetched - robots.txt disallows them and this audit "
                     "respects that - so what is behind them is unverified.".format(
                         ", ".join(content_blocks[:5]), BENIGN_CATEGORIES),
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
        # The finding's evidence lists the three kinds of rule it set aside.
        # This line did not, and said instead that the only disallowed paths
        # were the standard ones - on a hospital site whose robots.txt closes
        # `/health/video`, `/health/video-archive` and a department's
        # patient-guide documents to every crawler. A sentence saying nothing
        # was found may only describe what was looked at.
        set_aside = sorted(set(substantive) - set(content_blocks) - {"/", "/*"})
        # What the file actually contains, before any of this skill's filters
        # ran. a cloud host's whole robots.txt is `User-agent: *`, `Allow: /` and a
        # `Sitemap:` line - not one `Disallow` in it - and the report said "the
        # only disallowed paths are the standard admin, cart, account and
        # search routes", naming four categories of rule that are not there.
        # A sentence about what was set aside may only describe rules that
        # exist.
        star_group = group_for(robots, "*")
        declared = sorted({rule.strip() for rule in (star_group or {}).get("disallow", [])
                           if rule.strip()})
        if star_group is None:
            # The pointer at the end is the whole reason this decline was
            # safe to leave as it was and is not any more. It is true that no
            # path is closed to crawlers this file does not name, and a reader
            # who stops reading there concludes that no path is closed at all
            # - which is the opposite of the truth about a file whose only
            # group names AI crawlers and disallows a section. This check
            # reads the `*` group; the named groups are somebody else's
            # question, and the sentence now says whose.
            result.skip("robots-blocks-content-paths",
                        "robots.txt names specific crawlers and has no `User-agent: *` group, "
                        "so no path is closed to crawlers it does not name. What the named "
                        "groups close is {}".format(
                            "reported by `robots-blocks-ai-answer-crawlers` above"
                            if section_answer or blocked_answer else
                            "read by `robots-blocks-ai-answer-crawlers`, which found no "
                            "section closed to an AI answer crawler"))
        elif not declared:
            result.skip("robots-blocks-content-paths",
                        "this robots.txt disallows nothing: its `User-agent: *` group holds no "
                        "`Disallow` line at all, so every path on the site is open to every "
                        "crawler")
        elif content_blocks:
            # Exactly one. The threshold is two, because a single closed
            # section is usually deliberate and firing on it put this check on
            # most of the web - but "no whole top-level section is closed to
            # all crawlers" is a false sentence to decline with when one is,
            # and a reader holding it against their own file finds the rule
            # this audit said was not there.
            result.skip("robots-blocks-content-paths",
                        "one whole top-level section is closed to all crawlers, {} - not "
                        "reported, because a single closed section is usually deliberate and "
                        "this check needs two before it reads as a pattern. It was not "
                        "fetched, so what is behind it is unverified{}".format(
                            content_blocks[0],
                            ". The other {} disallowed path(s) are deeper or wildcard rules, "
                            "too specific to judge from the path alone".format(len(set_aside))
                            if set_aside else ""))
        else:
            result.skip("robots-blocks-content-paths",
                        "no whole top-level section is closed to all crawlers. {}".format(
                            "{} deeper or wildcard rule(s) were set aside as too specific to "
                            "judge from the path alone, among them {} - they were not fetched, "
                            "so what is behind them is unverified".format(
                                len(set_aside), ", ".join(set_aside[:3]))
                            if set_aside else
                            # Listed rather than characterised, so a reader can
                            # hold the sentence against their own file.
                            "the {} disallowed path(s) are all ordinary hygiene rules - {} - "
                            "and they are: {}".format(
                                len(declared), BENIGN_CATEGORIES, ", ".join(declared[:5]))))


# Seconds between requests above which a compliant crawler cannot finish a
# site of ordinary size.
#
# Ten. A crawler that honours the directive spends `pages x delay` seconds on
# the site and nothing else, so a sixty-page site costs ten minutes at 10s,
# half an hour at 30s and three quarters of an hour at 45s - against fetch
# timeouts that an answer engine measures in seconds, because it is answering
# somebody who is waiting. Measured here: a skincare brand's robots.txt sets
# `Crawl-delay: 45` under `User-agent: *`; this audit honoured it in full,
# which is correct, and read 0 pages in 186 seconds. The number below is the
# point at which honouring the file and reading the site stop being compatible.
#
# The largest crawler that reads the directive at all caps what it will wait;
# the largest that does not ignores the line entirely. Neither of those makes
# the number harmless, because the crawlers this audit speaks for are neither.
CRAWL_DELAY_STARVES_S = 10

# Above this the delay is not a slow crawl but a closed door: half a minute
# per page is under 200 pages a day for a crawler that never stops.
CRAWL_DELAY_SEVERE_S = 30


def _check_crawl_delay(result, robots, snapshot):
    """A `Crawl-delay` large enough to starve every crawler that obeys it.

    This audit honours the directive in full and always has - see the note the
    crawl writes when it shrinks its own sample to fit. What it never did was
    report the number to the person who can change it. On the site above, the
    delay was the highest-severity discoverability defect there was and it
    appeared in one appendix line explaining why the tool had read nothing:
    the owner set 45 and has no way of knowing what it costs.

    Read off the `*` group, which is the group every crawler that has no group
    of its own falls into, and that is every AI answer crawler on all but a
    handful of sites.
    """
    result.check("robots-crawl-delay")
    if not _robots_was_read(robots):
        result.skip("robots-crawl-delay",
                    "robots.txt was not read, so this crawl holds no `Crawl-delay` value to "
                    "judge")
        return
    group = group_for(robots, "*")
    delay = (group or {}).get("crawl_delay")
    try:
        delay = float(delay)
    except (TypeError, ValueError):
        delay = None
    if not delay or delay <= 0:
        result.skip("robots-crawl-delay",
                    "robots.txt sets no `Crawl-delay` for the `User-agent: *` group, so a "
                    "crawler is not asked to wait between requests")
        return
    if delay < CRAWL_DELAY_STARVES_S:
        result.skip("robots-crawl-delay",
                    "robots.txt asks for {:g}s between requests, under the {}s at which a "
                    "crawler that obeys it can no longer finish a site of ordinary size".format(
                        delay, CRAWL_DELAY_STARVES_S))
        return

    crawl = snapshot.get("crawl") or {}
    read = crawl.get("pages_read")
    if read is None:
        read = crawl.get("pages_crawled", 0)
    # What it cost this run, in the run's own numbers rather than in an
    # estimate. This audit is one compliant crawler and it is the one whose
    # arithmetic the reader can check against the report they are holding.
    cost = ("This audit honoured the delay and read {} page(s) of the site in {:g}s "
            "as a result.".format(read, round(crawl.get("elapsed_s") or 0, 1)))
    severe = delay >= CRAWL_DELAY_SEVERE_S
    result.add(
        id_hint="robots-crawl-delay-starves-crawlers",
        title="robots.txt asks every crawler to wait {:g} seconds between pages".format(delay),
        severity="high" if severe else "medium", confidence="high",
        evidence="`Crawl-delay: {:g}` is set for `User-agent: *` in robots.txt, so a crawler "
                 "that obeys it may fetch one page every {:g} seconds. A {}-page site then "
                 "takes {:.0f} minutes to read in full, against fetch timeouts an answer "
                 "engine measures in seconds because somebody is waiting for the answer. "
                 "{}".format(delay, delay, max(read, 1), (max(read, 1) * delay) / 60.0, cost),
        mechanism="A", root_cause="robots-block",
        summary="Lower `Crawl-delay` to a value a crawler can finish the site inside, or "
                "remove it and rate-limit at the server instead.",
        how_to_fix=[
            "Open robots.txt and find the `Crawl-delay` line under `User-agent: *`.",
            "Set it to 1, or remove it: a delay under {}s lets a crawler read a site of "
            "ordinary size in one visit.".format(CRAWL_DELAY_STARVES_S),
            "If the delay is there because crawler traffic was overloading the server, rate "
            "limit by IP address at the server or CDN instead. That throttles the traffic "
            "that is actually a problem without slowing every crawler to the same crawl.",
            "Check the result by fetching robots.txt and confirming the line is gone or the "
            "number is lower.",
        ],
        effort="low", owner="developer",
        rationale="A crawler that respects robots.txt respects this line too, so the site is "
                  "readable only as fast as the file allows. The crawlers that ignore it are "
                  "the ones the site did not want; the ones that obey it are the ones that "
                  "feed AI answers, and they give up before they have read enough of the site "
                  "to say anything about it.",
    )


# How many rules of one group to quote back. Enough to show which block is
# which; not a reprint of the file.
GROUP_RULE_EXAMPLES = 4


def _group_rules_phrase(group):
    """What one robots.txt group carries, in the file's own words."""
    lines = ["Allow: {}".format(rule) for rule in group.get("allow", [])]
    lines += ["Disallow: {}".format(rule) if rule.strip() else "Disallow: (blank, which "
              "disallows nothing)" for rule in group.get("disallow", [])]
    if group.get("crawl_delay") is not None:
        lines.append("Crawl-delay: {:g}".format(group["crawl_delay"]))
    if not lines:
        return "no rules at all"
    shown = ", ".join("`{}`".format(line) for line in lines[:GROUP_RULE_EXAMPLES])
    return (shown + " and {} more".format(plural(len(lines) - GROUP_RULE_EXAMPLES, "line"))
            if len(lines) > GROUP_RULE_EXAMPLES else shown)


def _check_robots_agent_groups(result, robots):
    """Does robots.txt state one agent's rules in more than one place?

    A WooCommerce shop's robots.txt carried the shop's own `User-agent: *`
    block with its `Disallow` rules, and then a plugin appended a second
    `User-agent: *` block with a bare `Disallow:`. RFC 9309 section 2.2.1 says
    records with the same product token are combined, so a crawler that follows
    it keeps the shop's rules; a crawler that takes the first matching group,
    or the last, or the one it happened to write over the other, does not. The
    file does not decide between them, so the site does not know what it is
    allowing: what it means depends on which parser reads it.

    Nothing in this audit had ever said so, because every reader of robots.txt
    here asks `group_for`, which merges the groups and hands back one answer.
    The structure that produced that answer was thrown away before any check
    could see it. `repeated_agent_groups` in `robots_parser` keeps it, which is
    where it belongs: the definition of where one group ends and the next
    begins lives in the parser and nowhere else.

    Zero extra requests: robots.txt was fetched by the crawl before any page
    was.
    """
    result.check("robots-agent-groups-unambiguous")
    if not _robots_was_read(robots):
        # `_no_robots_rules_reason` returns `(reason, established)`; only the
        # sentence is wanted here, because the guard above has already decided
        # that nothing was established.
        result.skip("robots-agent-groups-unambiguous", _no_robots_rules_reason(robots)[0])
        return
    repeated = repeated_agent_groups(robots)
    if not repeated:
        groups = robots.get("groups") or []
        result.skip("robots-agent-groups-unambiguous",
                    "each of the {} in robots.txt names a token no other group names, so "
                    "every crawler reading this file finds its rules in one "
                    "place".format(plural(len(groups), "user-agent group")))
        return

    token, groups = repeated[0]
    named = "`User-agent: {}`".format(token)
    blocks = "; ".join("block {} carries {}".format(number, _group_rules_phrase(group))
                       for number, group in enumerate(groups, start=1))
    result.add(
        id_hint="robots-repeats-a-user-agent-group",
        title="robots.txt states the rules for {} in {} separate blocks".format(
            "`{}`".format(token) if token != "*" else "every crawler",
            len(groups)),
        severity="low", confidence="high",
        evidence="robots.txt contains {} more than once, and the blocks do not carry the same "
                 "rules: {}. RFC 9309 section 2.2.1 says a crawler should combine records with "
                 "the same user-agent token, and the major crawlers do; others take the first "
                 "matching block, or the last, and stop. Nothing in the file decides between "
                 "those readings, so which of your rules apply depends on which crawler is "
                 "reading - and you cannot tell from the file which set you are "
                 "publishing.".format(named, blocks),
        mechanism="A", root_cause="robots-block",
        summary="Merge the blocks into one, so there is one place your rules for {} "
                "live.".format("every crawler" if token == "*" else token),
        how_to_fix=[
            "Open robots.txt and find the {} lines. There are {} of "
            "them.".format(named, len(groups)),
            "Keep one, and move every `Allow` and `Disallow` line from the other block(s) "
            "under it. The order of the rules inside one block does not matter: the longest "
            "matching rule wins, and an `Allow` beats a `Disallow` of the same length.",
            "Delete the now-empty blocks, including any bare `Disallow:` line with no path "
            "after it - that line disallows nothing and is what makes the two blocks "
            "disagree.",
            "If a plugin or a platform appends the second block on its own, the setting that "
            "generates it is the thing to change; editing the file by hand will be undone the "
            "next time it regenerates.",
        ],
        effort="low", owner="developer",
        rationale="robots.txt is the first thing a crawler reads, and it is the one file on "
                  "the site whose meaning has to be the same for every reader. A file that "
                  "two conforming crawlers read differently is one an owner cannot reason "
                  "about, and the rules it was written to enforce may or may not be in "
                  "effect.",
    )


# --------------------------------------------------------------------------
# A robots.txt that is not robots.txt any more
#
# A live file, read by hand, carried this line:
#
#     Disallow: /g,mt=RegExp(
#
# A build step had written JavaScript into the file. Nothing in this audit
# reported it: `parse_robots` takes whatever follows the colon as a path, every
# rule check then asks whether a URL matches that "path", and the answer is
# always no, so the file read as one that disallows nothing in particular.
# Every crawler reads this file first and reads it whole, so a file that is
# partly code is a file whose real rules may be somewhere in the wreckage.
#
# Two readings, and each one is a fact about the bytes rather than a guess.
# --------------------------------------------------------------------------

# Characters that cannot appear in a `Disallow` or `Allow` value on a file
# anybody wrote on purpose, and that appear in almost every line of minified
# JavaScript.
#
# Deliberately not `=`, `?`, `&`, `%`, `*` or `$`: `Disallow: /*?sort=` and
# `Disallow: /*.php$` are ordinary, correct rules that a large share of real
# sites publish, and firing on them would put this check on most of the web.
# Deliberately not whitespace either - an unencoded space in a path is a
# mistake of a different kind and a much milder one.
_NOT_IN_A_PATH_RE = re.compile(r"[(){};<>\"'`]")

# Above this share of a file's rules being unreadable, the file is not a
# robots.txt with a bad line in it; it is a different document served at that
# address. Chosen, not measured: one broken rule out of forty is a typo, and
# half of them is a build step writing over the file.
ROBOTS_MOSTLY_BROKEN_SHARE = 50


def _rule_is_not_a_path(value):
    """Why this `Allow`/`Disallow` value is not a path shape, or "".

    An empty value is legal and means "nothing is closed", so it is not read
    here at all.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("/", "*")):
        return "does not begin with `/` or `*`, which every path-matching rule must"
    match = _NOT_IN_A_PATH_RE.search(value)
    if match:
        return "contains `{}`, which no URL path holds".format(match.group(0))
    return ""


def _check_robots_directive_values(result, robots):
    """Are this file's own rules shaped like paths at all?"""
    result.check("robots-rules-are-path-shaped")
    if not _robots_was_read(robots):
        result.skip("robots-rules-are-path-shaped", _no_robots_rules_reason(robots)[0])
        return
    rules = []
    for group in robots.get("groups") or []:
        for field in ("allow", "disallow"):
            for value in group.get(field) or []:
                rules.append((field.title(), value))
    if not rules:
        result.skip("robots-rules-are-path-shaped",
                    "this robots.txt carries no `Allow` or `Disallow` line at all, so it "
                    "states no rule whose shape could be read")
        return
    broken = [(field, value, why) for field, value in rules
              for why in [_rule_is_not_a_path(value)] if why]
    if not broken:
        result.skip("robots-rules-are-path-shaped",
                    "every one of the {} `Allow`/`Disallow` value(s) in this robots.txt is "
                    "shaped like a URL path".format(len(rules)))
        return

    result.signal("robots_rules_not_path_shaped",
                  ["{}: {}".format(field, value) for field, value, _ in broken])
    share = pct(len(broken), len(rules))
    mostly = share >= ROBOTS_MOSTLY_BROKEN_SHARE
    result.add(
        id_hint="robots-txt-holds-something-that-is-not-a-rule",
        title="robots.txt contains lines that are not crawler rules",
        severity="high" if mostly else "medium", confidence="high",
        evidence="{} of the {} `Allow`/`Disallow` value(s) in {} is not a URL path. {}{}"
                 .format(
                     len(broken), len(rules), robots.get("url") or "/robots.txt",
                     "; ".join("`{}: {}` - {}".format(field, value, why)
                               for field, value, why in broken[:3]),
                     ". At {}% of the file's rules this is not a mistyped line: something "
                     "has written over the file".format(share) if mostly else "."),
        mechanism="A", root_cause="robots-block",
        summary="Republish robots.txt as a plain list of crawler rules.",
        how_to_fix=[
            "Open {} in a browser and read it top to bottom. A robots.txt holds nothing but "
            "`User-agent:`, `Allow:`, `Disallow:`, `Crawl-delay:` and `Sitemap:` lines, plus "
            "`#` comments.".format(robots.get("url") or "/robots.txt"),
            "Find what writes the file. A value holding code came from a build step, a "
            "minifier or a template that treated robots.txt as an asset rather than as text; "
            "the file on disk is usually correct and the published one is not.",
            "Republish the intended rules and confirm by requesting the file again: every "
            "line after a `User-agent:` should be a directive name, a colon, and a path "
            "starting with `/`.",
        ],
        effort="low", owner="developer",
        rationale="Every crawler reads this file before it reads a page, and it reads the "
                  "whole file. A rule that is not a path matches nothing, so whatever the "
                  "site meant to close is open - and a crawler that stops at the first line "
                  "it cannot parse never reaches the rules below it.",
    )


# How many URLs the sitemap snippet lists. Enough to show the shape and to be
# worth pasting; not a copy of the crawl, which would put sixty lines of XML
# into a finding.
SITEMAP_SNIPPET_MAX_URLS = 12

_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _observed_lastmod(page):
    """A `YYYY-MM-DD` this crawl actually read off the page, or "".

    Three sources, in the order of how much they are worth: the page's own
    `dateModified`, then `datePublished`, then any machine-readable date in
    its markup, then the `Last-Modified` header. Nothing is derived and
    nothing is guessed - a snippet that prints a date the crawl did not see is
    a snippet that tells the owner to publish a false one.
    """
    headers = page.get("headers") or {}
    # The day the server says it answered. A page generated per request sends
    # `Last-Modified` equal to that moment, and many templates stamp
    # `dateModified` with it too, so the snippet printed the audit's own date
    # on a news site's dynamic pages - directly above the next finding's
    # "Do not set <lastmod> to today's date". A date that is only the day of
    # the request is not a date the page was changed, and is left out.
    #
    # The `Date` header is one witness and the crawl's own clock is the
    # other. The page record kept neither for a long time, so this guard
    # compared against nothing and never fired: a documentation site and a
    # publisher each got the audit's date on every snippet entry. Where no
    # time of fetch is recorded at all, the day this check runs stands in
    # for it, because a snippet may never print the audit's own date.
    fetched = _header_moment(headers.get("date")) or _iso_moment(page.get("fetched_at"))
    excluded = {_day_of(fetched)} if fetched is not None else set(_audit_days())
    dates = page.get("dates") or {}
    candidates = [dates.get("jsonld_date_modified"), dates.get("jsonld_date_published")]
    candidates.extend(dates.get("machine_readable") or [])
    for value in candidates:
        match = _ISO_DATE_RE.match(str(value or "").strip())
        if match and match.group(1) not in excluded:
            return match.group(1)
    modified = _header_moment(headers.get("last-modified"))
    if modified is None:
        return ""
    # Generated on request: the server stamped the page with the moment it
    # built it. Minutes rather than the day, so a page modified the evening
    # before a morning fetch is not mistaken for one, and the day rule above
    # still catches the rest.
    if fetched is not None and abs((fetched - modified).total_seconds()) <= GENERATED_ON_REQUEST_S:
        return ""
    day = _day_of(modified)
    return "" if day in excluded else day


# How close `Last-Modified` may sit to the moment of the fetch before it is
# read as the moment the server built the response rather than the moment
# somebody changed the page. A server generating per request stamps the
# second it answered; a few minutes covers a clock that disagrees with this
# machine's and a crawl that paused between pages.
GENERATED_ON_REQUEST_S = 300


def _header_moment(raw):
    """An aware UTC datetime from an HTTP date header, or None."""
    if not raw:
        return None
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _iso_moment(raw):
    """An aware UTC datetime from the crawl's `fetched_at`, or None."""
    try:
        when = datetime.strptime(str(raw or ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc)


def _day_of(moment):
    return moment.strftime("%Y-%m-%d")


def _audit_days():
    """Today, by UTC and by this machine's clock - they differ near midnight."""
    return (datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            datetime.now().strftime("%Y-%m-%d"))


def _header_day(raw):
    """`YYYY-MM-DD` from an HTTP date header, or ""."""
    moment = _header_moment(raw)
    return _day_of(moment) if moment is not None else ""


def _sitemap_snippet(snapshot):
    """A sitemap listing pages this crawl fetched and got a 200 from.

    Both entries used to be written in: `<loc>{origin}/about</loc>` with
    `<lastmod>2026-01-31</lastmod>` beside it. That showed up on three of
    eleven sites and `/about` returned 404 on all three - one of them a
    single-page site where it cannot exist, another writing its URLs in Arabic
    slugs. The date was seven months before the audit and sat directly above
    the snippet's own bullet, "Include a `<lastmod>` date on every entry and
    keep it accurate".

    It read as a real file rather than as a shape because it is the only
    placeholder in this marketplace that is not bracketed - everything else
    says `"<logo URL - not found on the site, fill this in>"` in so many
    words. So it stops being a shape: the crawl already holds addresses that
    answered 200, and a `lastmod` is printed only where a date was read off
    the page. Where nothing was read, the element is left out entirely and the
    advice bullet still stands on its own.
    """
    origin = snapshot["origin"].rstrip("/")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    pages = pages_of(snapshot)
    # The homepage first, because that is the order a person reads a sitemap
    # in and the order every generator writes one in.
    pages.sort(key=lambda p: (0 if p.get("page_type") == "home" else 1, p["url"]))
    entries = 0
    for page in pages:
        loc = (page.get("final_url") or page.get("url") or "").strip()
        if not loc.startswith(origin):
            continue
        lastmod = _observed_lastmod(page)
        lines.append("  <url><loc>{}</loc>{}</url>".format(
            loc.replace("&", "&amp;"),
            "<lastmod>{}</lastmod>".format(lastmod) if lastmod else ""))
        entries += 1
        if entries >= SITEMAP_SNIPPET_MAX_URLS:
            break
    if not entries:
        # A crawl that read no page has no address to offer, and inventing one
        # is exactly what this function exists to stop. Bracketed, in the
        # wording the Organization snippet uses for the same situation, so a
        # reader can see at a glance that this half is theirs to fill in.
        lines.append("  <url><loc>{}/</loc></url>".format(origin))
        lines.append("  <url><loc>{}/<a path on your site - this crawl read no page "
                     "to list here, fill this in></loc></url>".format(origin))
    lines.append("</urlset>")
    return "\n".join(lines)


# The site answering "there is nothing here". Distinct from a refusal, which
# answers nothing about whether the file exists, and from no response at all.
SITEMAP_ABSENT_STATUS = frozenset({404, 410})

# The WordPress and Yoast default, and the second most common sitemap location
# after /sitemap.xml. crawl.py asks for /sitemap.xml and whatever robots.txt
# names, and nothing else - so a hardware manufacturer, which publishes a valid index
# at /sitemap_index.xml behind a robots.txt this crawler was refused, was
# reported as a site whose sitemap could not be determined to exist.
SITEMAP_INDEX_PATH = "/sitemap_index.xml"


def _sitemap_answer(url, status, shell_urls=(), redirects=None):
    """One location and what it said, in words a reader can check.

    `redirects` maps a URL to the `(status, destination)` of its first hop.
    Without it this line printed the status of whatever the redirect landed on
    as though the site had returned it at the sitemap path: a site that 302s
    /sitemap.xml to a search page was described as serving "HTTP 200 carrying
    an HTML page" at /sitemap.xml, which it never did.
    """
    hop = (redirects or {}).get(url)
    if hop:
        return "{} -> HTTP {} redirect to {}, which answered {}".format(
            url, hop[0], hop[1],
            "with an HTML page rather than XML" if url in shell_urls
            else "HTTP {}".format(status))
    if url in shell_urls:
        return "{} -> HTTP 200 carrying an HTML page rather than XML".format(url)
    if status is None:
        return "{} -> no response".format(url)
    return "{} -> HTTP {}".format(url, status)


def _probe_sitemap_index(result, snapshot, fetcher, robots, already_asked):
    """Ask for /sitemap_index.xml when nothing else produced a sitemap.

    One GET, and only on a site where the answer changes the finding: without
    it, every WordPress site whose robots.txt this crawler could not read is
    told to publish a sitemap it already publishes.

    Returns `(url, status, is_a_sitemap, is_html, redirect)`, or None when no
    request was made. `is_html` separates a site that answers 200 to everything
    from one that really serves something at this path, and `redirect` carries
    the first hop when the site sent the request somewhere else, because
    `status` is otherwise the destination's status printed against this path.
    """
    url = snapshot["origin"].rstrip("/") + SITEMAP_INDEX_PATH
    if url in already_asked:
        return None
    if fetcher is None or not fetcher.budget_left or not fetcher.time_left:
        return None
    if is_disallowed(robots or {}, USER_AGENT, path_of(url)):
        return None
    response = fetcher.try_get(url)
    if response is None:
        return None
    body = response_text(response)[:4000].lower() if response.status_code == 200 else ""
    # The document element decides this, not the status. A site that answers
    # 200 to everything hands back its homepage here too, and `is_sitemap` was
    # the only thing standing between that and "a sitemap is published at
    # /sitemap_index.xml".
    is_sitemap = "<sitemapindex" in body or "<urlset" in body
    is_html = response.status_code == 200 and response_is_an_html_page(response)
    hop = _redirect_hop(url, response)
    result.signal("sitemap_index_probe",
                  {"url": url, "status": response.status_code, "is_sitemap": is_sitemap,
                   "redirected_to": hop[1] if hop else None})
    if is_sitemap:
        result.signal("sitemap_probe_found", url)
    return (url, response.status_code, is_sitemap, is_html, hop)


def _origin_serves_a_page_for_any_path(result):
    """Does this origin answer an address nobody published with a page?

    Read from the signals `_check_soft_404_handling` sets, which is why that
    check now runs first in `run()`. `True` means one measured request found
    that a made-up path resolves to a page; `False` that the site told the
    request apart from a real one; `None` that the probe never got an answer -
    it was refused, disabled, or out of budget - and nothing may be concluded
    from a 200 anywhere on this origin.
    """
    return result.signals.get("unknown_paths_resolve")


def _catch_all_note(catch_all_url):
    """One clause naming the measurement that settled a 200, for evidence lines."""
    return (" This origin answers HTTP 200 with a page for addresses that do not exist - "
            "{} is not published and resolves - so HTTP 200 at any address here is the "
            "catch-all route answering, not proof that a file is there.".format(catch_all_url))


def _sitemaps_that_are_html(fetcher, broken, robots=None, catch_all=None):
    """Which of these "sitemaps that do not parse" are really HTML pages.

    a browser-compatibility database answers HTTP 200 with its single-page-app shell for every
    unknown path, /sitemap.xml included, and the report said "an XML sitemap is
    published but does not parse: XML parse error: syntax error: line 1, column
    0", with the fix "validate the file with any XML validator and fix the
    reported error" - about a file nobody ever wrote. crawl.py already draws
    this distinction for robots.txt and records "returned HTML, not plain text;
    treated as absent"; the sitemap path never got it.

    Three ways to settle it, in order of what they cost and what they know.

    The record. `crawl.py` records `content_type`, `body_bytes` and
    `looks_like_html` on every sitemap record, from the response it already had
    in memory. That is a direct reading of the very response the parser choked
    on, it settles every candidate rather than the first, and it costs nothing
    at all - so it works on a run with no request budget left, which is
    precisely the run that produced the false finding this function exists to
    stop. An older snapshot has no such field; `None` there means "not
    recorded", which is why the test is `is not None` and not truthiness.

    `catch_all` is the missing-page probe's answer. `True` means one request
    already established that this origin serves a page for an address nobody
    published; then every 200 here that failed to parse is that same page.
    `False` means the site does distinguish an unknown path, so a 200 here is a
    document the site chose to serve at this address. It is an inference from a
    different address, so the record beats it where both are available.

    The paid half costs one GET, spent only on a record the crawl did not
    settle and only where the parser failed the way it fails on an HTML body.

    Returns `(records, redirects, verified)`. `verified` is whether this run
    established what kind of document is at that address at all; without it
    nothing may be published about the file either way. `redirects` maps the
    URL to the first hop when the site sent this request elsewhere: the same
    site answers 302 at /sitemap.xml and lands on a search page, and reading
    only the status after the hop described that as an HTML page served at
    /sitemap.xml. Only the paid half can fill that in - a crawl record carries
    the status after the hop and not the hop - which is the one thing reading
    the record gives up, and the `catch_all` shortcut above already gave it up.
    """
    candidates = [record for record in broken
                  if (record.get("parse_error") or "").startswith("XML parse error")]
    if not candidates:
        # A gzip failure or an entity declaration is a fact about the bytes
        # that arrived, not a guess about them, so nothing needs establishing.
        return [], {}, True

    recorded = [r for r in candidates if r.get("looks_like_html")]
    unrecorded = [r for r in candidates if r.get("looks_like_html") is None]
    if not unrecorded:
        return recorded, {}, True
    if catch_all:
        # `recorded + unrecorded`, not `candidates`: a record the crawl read as
        # not-HTML is a document this origin chose to serve at that address,
        # and a direct reading of that response beats an inference drawn from a
        # different one.
        return recorded + unrecorded, {}, True
    record = unrecorded[0]
    if (fetcher is None or not fetcher.budget_left or not fetcher.time_left
            or is_disallowed(robots or {}, USER_AGENT, path_of(record["url"]))):
        # The probe's `False` is the only thing left that settles this: the
        # site answered an unpublished address with something other than a
        # page, so a 200 at the sitemap location is a file it chose to serve.
        return recorded, {}, catch_all is False
    response = fetcher.try_get(record["url"])
    if response is None or response.status_code != 200:
        return recorded, {}, catch_all is False
    if not response_is_an_html_page(response):
        return recorded, {}, True
    hop = _redirect_hop(record["url"], response)
    return recorded + [record], ({record["url"]: hop} if hop else {}), True


# A byte-order mark is not whitespace to a regular expression, and a
# robots.txt saved from a Windows editor begins with one.
BOM = chr(0xFEFF)

# The message crawl.py records when /robots.txt answers 200 with an HTML page.
ROBOTS_HTML_ERROR = "robots.txt returned HTML, not plain text"


def _robots_was_read(robots):
    """Did robots.txt actually answer with rules, or was it only asked?

    Named as a source only when it answered. A university's robots.txt
    returned 403 to this crawler and 200 to curl, and the report still said
    five times that there were no rules to evaluate. Listing an unread file
    among the places a claim of absence looked would repeat that in the one
    field a reader trusts to tell them what the claim rests on.
    """
    robots = robots or {}
    if robots.get("status") != 200:
        return False
    return not any(str(error).startswith(ROBOTS_HTML_ERROR)
                   for error in robots.get("errors") or [])


def _robots_is_absent(robots):
    """Did the site answer "there is no robots.txt here"?

    A 404 or 410 is that answer. So is an HTTP 200 carrying an HTML page, which
    crawl.py already records as "treated as absent". A refusal and a failed
    request are not: they say this crawler was not allowed to look.
    """
    robots = robots or {}
    status = robots.get("status")
    if status in (404, 410):
        return True
    return status == 200 and not _robots_was_read(robots)


# What a site calls its own page of links, in the words sites use for it.
_HUMAN_SITEMAP_RE = re.compile(
    r"/(?:site[-_]?map|plan[-_]?du[-_]?site|mapa[-_]?del[-_]?sitio|sitemappa)"
    r"(?:[.][a-z0-9]{2,5})?/?$", re.I)


def _human_sitemap_page(snapshot):
    """A crawled page whose own address says it is a sitemap for people.

    Read from the crawl record, not requested: if the crawl reached it, the
    audit has already seen it and may not write a sentence implying it has
    not.
    """
    for page in pages_of(snapshot):
        url = page.get("final_url") or page.get("url") or ""
        if _HUMAN_SITEMAP_RE.search(urlparse(url).path or ""):
            return url
    return ""


def _sitemap_line_step(robots, origin):
    """How to point robots.txt at a sitemap, given whether robots.txt exists.

    The step read "Add `Sitemap: .../sitemap.xml` as the last line of
    robots.txt" whatever the file was. On a browser-compatibility database with
    no robots.txt at all - /robots.txt answers 200 with HTML because every
    unknown path there redirects into a search page - that told the owner to
    edit the last line of a file nobody has written. And on a site whose
    robots.txt this crawler was refused, telling them to create one would
    overwrite whatever is already there.
    """
    origin = origin.rstrip("/")
    if _robots_was_read(robots):
        return ("Add `Sitemap: {}/sitemap.xml` to /robots.txt. The line can go anywhere in "
                "the file and applies to every crawler.".format(origin))
    if _robots_is_absent(robots):
        answered = ("HTTP 200 carrying an HTML page rather than a robots.txt"
                    if robots.get("status") == 200
                    else "HTTP {}".format(robots.get("status")))
        return ("There is no robots.txt to add a `Sitemap:` line to - {} answered {} - so "
                "create one as a plain-text file holding `User-agent: *`, `Allow: /` and "
                "`Sitemap: {}/sitemap.xml`.".format(
                    robots.get("url") or "/robots.txt", answered, origin))
    return ("Check whether you already publish a robots.txt before touching it: this audit's "
            "request for {} did not come back with a readable file ({}), so what is in it is "
            "unknown. If one exists, add `Sitemap: {}/sitemap.xml` to it; if none exists, "
            "create one holding that line.".format(
                robots.get("url") or "/robots.txt",
                "HTTP {}".format(robots.get("status")) if robots.get("status")
                else robots.get("fetch_error") or "no response",
                origin))


def _check_sitemaps(result, snapshot, fetcher, robots=None):
    result.check("sitemap-present")
    result.check("sitemap-parses")
    # `sitemap-lastmod-coverage` is deliberately NOT registered here.
    # freshness-corroboration-audit owns date signals and reports it, and
    # registering it in both places put one check under two skills: the report
    # listed 71 entries for 70 distinct checks, so the README's count and the
    # report's count disagreed and both looked wrong.
    result.check("sitemap-urls-resolve")

    sitemaps = snapshot.get("sitemaps") or []
    # `looks_like_html`, and it is one condition rather than a nicety. On a
    # server that answers every unknown path with its homepage, the sitemap
    # address returns 200 with 77,904 bytes of HTML and no parse error, so it
    # landed in `reachable`, the HTML-shell branch below - written for exactly
    # this case - never ran, and the report told the owner to advertise a file
    # that does not exist in their robots.txt. That was measured on a live
    # site, and the skill's own reference already documented the behaviour
    # this restores.
    reachable = [s for s in sitemaps
                 if s.get("status") == 200 and not s.get("parse_error")
                 and not s.get("looks_like_html")]
    broken = [s for s in sitemaps if s.get("status") == 200 and s.get("parse_error")]
    # A 200 whose recorded body is an HTML page, with no parse error to send
    # it through `broken`. A museum answers `/sitemap.xml` and
    # `/sitemap_index.xml` with a short page reading "the web page cannot be
    # found", served as `text/html` with status 200; the parser raised
    # nothing on it, so it was neither reachable nor broken nor absent, and
    # the report said "no sitemap request produced an answer ... a refused or
    # failed request is evidence that this crawler was not allowed to look" -
    # about two addresses that had both answered 200. An HTML document at a
    # sitemap address is not a sitemap whatever words it holds, which is the
    # rule the branch below already applies to a body that fails to parse.
    recorded_pages = [s for s in sitemaps if s.get("status") == 200
                      and not s.get("parse_error") and s.get("looks_like_html")
                      and not s.get("truncated")]
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

    # Three different situations used to share one sentence, and it was wrong
    # about two of them. A request the edge refused is not an answer about
    # whether the file exists - a museum's sitemap.xml returned 429 along with
    # everything else and the report said "No XML sitemap is available" as
    # settled fact. But a 404 *is* an answer, and a museum, whose /sitemap.xml
    # returns 404 to every client, got "every sitemap request was refused, so
    # whether a sitemap exists could not be determined" instead. The same line
    # ran on a hardware manufacturer, which publishes a valid index at
    # /sitemap_index.xml that nothing in this audit had ever asked for.
    #
    # So: 404 means absent, a refusal means unchecked, no response means
    # unchecked for a different reason, and none of them may borrow another's
    # wording.
    absent = [s for s in sitemaps if s.get("status") in SITEMAP_ABSENT_STATUS]
    refused = [s for s in sitemaps if s.get("status") in REFUSED_STATUS]
    unanswered = [s for s in sitemaps if s.get("status") is None]

    # A 200 whose body is HTML is not a broken sitemap. It is a site with no
    # sitemap and a catch-all route, and crawl.py already applies exactly this
    # rule to robots.txt - "returned HTML, not plain text; treated as absent".
    # The sitemap path never got it, so a browser-compatibility database was told its sitemap "does
    # not parse" and sent to run a single-page-app shell through an XML
    # validator.
    catch_all = _origin_serves_a_page_for_any_path(result)
    catch_all_url = snapshot["origin"].rstrip("/") + NONSENSE_PATH
    html_shells, shell_redirects, body_verified = (
        _sitemaps_that_are_html(fetcher, broken, robots, catch_all)
        if broken else ([], {}, True))
    html_shells = html_shells + recorded_pages
    shell_urls = {s["url"] for s in html_shells}
    if html_shells:
        broken = [s for s in broken if s["url"] not in shell_urls]
        absent = absent + html_shells
        result.signal("sitemap_html_shells", sorted(shell_urls))
        if catch_all:
            result.signal("sitemap_shells_from_catch_all", True)

    if not reachable and not broken:
        probe = _probe_sitemap_index(result, snapshot, fetcher, robots,
                                     {s["url"] for s in sitemaps})
        if probe is not None and probe[2]:
            result.skip("sitemap-present",
                        "a sitemap is published at {} and answered HTTP 200 with a valid "
                        "sitemap document. The crawl asks only for /sitemap.xml and whatever "
                        "robots.txt names, so this file was found by an extra probe after the "
                        "crawl had finished and its contents were not read".format(probe[0]))
            result.skip("sitemap-parses",
                        "the sitemap at {} was found after the crawl had finished, so it was "
                        "fetched but not parsed".format(probe[0]))
            result.skip("sitemap-urls-resolve",
                        "the sitemap at {} was found after the crawl had finished, so its "
                        "URLs were not sampled".format(probe[0]))
            result.signal("sitemap_checked", False)
            return

        asked = [(s["url"], s.get("status")) for s in sitemaps]
        if probe is not None:
            asked.append((probe[0], probe[1]))
            probe_record = {"url": probe[0], "status": probe[1]}
            # Where the site sent this request, so the line below reports the
            # hop instead of printing the destination's status against a path
            # the site never answered at.
            if probe[4]:
                shell_redirects = dict(shell_redirects, **{probe[0]: probe[4]})
            # A 200 that is not a sitemap document is this location answering
            # "nothing here" in the most confusing way available.
            if probe[1] in SITEMAP_ABSENT_STATUS or probe[1] == 200:
                absent = absent + [probe_record]
                if probe[3]:
                    shell_urls = shell_urls | {probe[0]}
            elif probe[1] in REFUSED_STATUS:
                refused = refused + [probe_record]
            elif probe[1] is None:
                unanswered = unanswered + [probe_record]
        asked_phrase = "; ".join(_sitemap_answer(url, status, shell_urls, shell_redirects)
                                 for url, status in sorted(asked))

        if not asked:
            result.skip("sitemap-present",
                        "no sitemap request was made at all: the wall-clock budget ran out "
                        "before robots.txt could be read for a `Sitemap:` line and before "
                        "/sitemap.xml could be requested")
            result.skip("sitemap-parses", "no sitemap was fetched to parse")
            result.skip("sitemap-urls-resolve", "no sitemap was fetched to read URLs from")
            result.signal("sitemap_checked", False)
            return

        # robots.txt names a sitemap and the request for it did not resolve.
        # Whatever the conventional locations said, the file the site itself
        # points at is the one that decides, and nothing was learned about it.
        declared_unresolved = [s for s in refused + unanswered
                               if s.get("referenced_in_robots")]
        if declared_unresolved:
            result.skip("sitemap-present",
                        "robots.txt declares a sitemap at {}, and that request did not "
                        "resolve. Locations asked: {}. A refused or failed request is "
                        "evidence that this crawler was not allowed to look, not that the "
                        "file is missing, so this is reported as unchecked".format(
                            declared_unresolved[0]["url"], asked_phrase))
            result.skip("sitemap-parses", "no sitemap could be fetched to parse")
            result.skip("sitemap-urls-resolve", "no sitemap could be fetched to read URLs from")
            result.signal("sitemap_checked", False)
            return

        if not absent:
            result.skip("sitemap-present",
                        "no sitemap request produced an answer about whether one exists: {}. "
                        "A refused or failed request is evidence that this crawler was not "
                        "allowed to look, not that the file is absent, so this is reported as "
                        "unchecked rather than as a missing sitemap".format(asked_phrase))
            result.skip("sitemap-parses", "no sitemap could be fetched to parse")
            result.skip("sitemap-urls-resolve", "no sitemap could be fetched to read URLs from")
            result.signal("sitemap_checked", False)
            return

        unresolved = refused + unanswered
        # What the claim of absence rests on, printed in the report. Two
        # places, asked separately: the locations robots.txt itself points at,
        # and the conventional paths requested directly. A hardware
        # manufacturer publishes a valid index at /sitemap_index.xml that
        # nothing here had ever asked for and was told it has no sitemap, so
        # naming the paths is the difference between a measurement and a guess.
        declared_urls = set((robots or {}).get("sitemaps") or [])
        asked_paths = sorted({path_of(url) for url, _ in asked
                              if url not in declared_urls and path_of(url)})
        checked_sources = []
        if _robots_was_read(robots):
            checked_sources.append(
                "the {} sitemap location(s) robots.txt points at".format(len(declared_urls))
                if declared_urls else "robots.txt, which names no sitemap at all")
        # The measurement that turned a 200 into an absence, named as a source
        # of its own. Where a location answered 200 with a page, this claim
        # rests on the missing-page probe as much as on the sitemap requests,
        # and a reader has to be able to re-run it: one `curl -sI` against an
        # address nobody published is the whole of it.
        if shell_urls and catch_all:
            checked_sources.append(
                "one request for {}, an address nobody published, which answered HTTP 200 "
                "with a page - so a 200 at a sitemap location on this origin is the "
                "catch-all route answering and not a file".format(catch_all_url))
        # The other way a 200 becomes an absence, and it has to be named too.
        # Where the crawl already recorded the server's own `Content-Type` at
        # the sitemap location, that record is what settled it - no probe was
        # needed and none was made. A finding that names its sources and rests
        # on one it does not name is unverifiable, which is worse than silent.
        recorded_shells = sorted(s["url"] for s in html_shells
                                 if s.get("looks_like_html"))
        if recorded_shells:
            checked_sources.append(
                "the `Content-Type` this origin returned at {}, which the crawl recorded "
                "from the response itself and which says the address served a page rather "
                "than XML".format(", ".join(recorded_shells)))
        # The constraint here is a list of two paths, not "somewhere on the
        # site", and the old wording named the place. A sitemap published at
        # any third conventional address - `/sitemap-index.xml`,
        # `/wp-sitemap.xml`, `/sitemap1.xml` - is one nothing in this audit
        # asks for, and this finding would call it absent.
        checked_sources.append(
            "the conventional sitemap locations, which are a closed list of two paths, "
            "/sitemap.xml and {}{}".format(
                SITEMAP_INDEX_PATH,
                " - requested here as {}".format(", ".join(asked_paths))
                if asked_paths else ""))
        # A page the site itself calls a sitemap, found in the crawl record
        # rather than requested. A city hall publishes one at /sitemap.html
        # with 121 internal links and links it from the footer of every page -
        # 75 occurrences in this audit's own snapshot - and the finding said
        # "every conventional location was requested and none of them holds a
        # sitemap" without ever mentioning it. The title stays true, because
        # an HTML page is not the machine-readable index this check is about,
        # but a report that has seen the page and does not say so reads as a
        # report that did not look, and the fix steps tell the owner to create
        # something they have already made half of.
        human = _human_sitemap_page(snapshot)
        # The two siblings answered before the finding is raised, and this is
        # the whole of one real defect. `check()` at the top of this function
        # registers three names; `add()` then claims every one of them that is
        # still unanswered as having contributed to the finding, and the
        # appendix prints them under "Checks that contributed to a finding
        # above". On a server answering every unknown path with its homepage a
        # real report printed `sitemap-present`, `sitemap-parses` and
        # `unknown-paths-return-200` under that heading while holding no such
        # finding, and the reader learned nothing about the catch-all.
        #
        # Neither of these two contributed to anything: there is no sitemap, so
        # there was nothing to parse and no URL list to sample. Saying so is a
        # definite answer about each of them, it leaves them out of the fired
        # set, and it is the sentence that tells a reader what happened - which
        # is the half a heading cannot supply.
        result.skip("sitemap-parses",
                    "no sitemap was published at any location this audit asked for, so "
                    "there was no XML document to parse.{}".format(
                        _catch_all_note(catch_all_url) if catch_all and shell_urls else ""))
        result.skip("sitemap-urls-resolve",
                    "no sitemap was published at any location this audit asked for, so it "
                    "listed no URLs to resolve")
        # Registered again so the finding below is stamped with the check it is
        # actually about. `_current_check` holds whichever name was registered
        # last, which was `sitemap-urls-resolve` - so "No XML sitemap is
        # available" was filed in the appendix under the check that samples a
        # sitemap's URLs, and `sitemap-present` was filed as a check that
        # merely contributed to it.
        result.check("sitemap-present")
        result.add(
            id_hint="sitemap-missing",
            title="No XML sitemap is available",
            severity="medium", confidence="high",
            checked=tuple(checked_sources),
            # Every location asked for and what each one said, rather than a
            # sentence naming one URL and implying the rest.
            # "Every conventional and declared location" claimed a completeness
            # this audit does not have: a site can also declare a sitemap with
            # `<link rel="sitemap" type="application/xml" href="...">` in the
            # page head, and nothing in this crawl records that tag, so a
            # sitemap declared only that way is not among the locations asked
            # for. The sentence now says what was asked and what was not.
            evidence="Every conventional location and every sitemap robots.txt declares was "
                     "requested, and none of them holds a sitemap: {}.{}{} A sitemap declared "
                     "only as a "
                     '`<link rel="sitemap">` tag in a page head would not have been found: '
                     "this audit does not read that tag.".format(
                         asked_phrase,
                         (" A sitemap location answering 200 with an HTML page, or redirecting "
                          "to one, is a file that was never written rather than a sitemap that "
                          "needs fixing."
                          # Where the missing-page probe settled it, say so in
                          # the same breath. A reader shown only "HTTP 200
                          # carrying an HTML page" has to take the audit's word
                          # for which of the two it is; shown the address that
                          # does not exist and answers the same way, they can
                          # check it with one request.
                          + _catch_all_note(catch_all_url) if catch_all else "")
                         if shell_urls else "",
                         " A further {} location(s) neither answered nor refused and are "
                         "excluded from this conclusion.".format(len(unresolved))
                         if unresolved else "")
                     + (" The site does publish a human sitemap page at {}, which was crawled. "
                        "That helps a visitor and is not what this finding is about: a crawler "
                        "reads the XML index, not a page of links.".format(human)
                        if human else ""),
            mechanism="A", root_cause="sitemap-missing",
            summary="Publish /sitemap.xml listing every page you want found, and reference it from robots.txt.",
            how_to_fix=[
                ("Generate an XML sitemap from your CMS or site builder (most have this "
                 "built in). Your existing page at {} lists the same URLs and is a good "
                 "check that nothing is left out.".format(human) if human else
                 "Generate a sitemap from your CMS or site builder (most have this built in)."),
                "Publish it at {}/sitemap.xml.".format(snapshot["origin"].rstrip("/")),
                _sitemap_line_step(robots, snapshot["origin"]),
                "Include a <lastmod> date on every entry and keep it accurate.",
            ],
            effort="low", owner="developer",
            rationale="Without a sitemap a crawler only finds what it can reach by "
                      "following links, so pages more than a couple of clicks deep, or linked "
                      "only from JavaScript menus, may never be fetched.",
            snippet=_sitemap_snippet(snapshot),
        )
        result.signal("sitemap_url_count", 0)
        return

    if broken and not body_verified:
        # Nothing in this run established what is actually at that address, and
        # "the XML parser failed" does not establish it: the parser fails the
        # same way on a broken sitemap and on 1.8 MB of homepage. Both were
        # measured - a real sitemap missing a closing tag gives "mismatched
        # tag: line 4, column 2", an HTML page gives "not well-formed (invalid
        # token): line 2, column 11", and neither message belongs to one of
        # them.
        #
        # The finding this replaces was the only false positive to reach the
        # confident tier across six sites in one batch: "An XML sitemap is
        # published but does not parse ... line 25, column 104", on a site
        # whose server answers every unknown path with its homepage and which
        # publishes no sitemap at all. Its fix, "Fix the XML so the sitemap can
        # be read", is work on a file that does not exist. An unchecked check
        # costs a reader one paragraph; that finding cost them an afternoon.
        unverified = broken[0]
        result.skip("sitemap-parses",
                    "{} answered HTTP 200 and the XML parser failed on it ({}), and this run "
                    "could not establish whether what arrived was a sitemap with an error in "
                    "it or an ordinary HTML page served by a catch-all route. An XML parser "
                    "reports the same kind of error for both, so the parse failure on its own "
                    "is not evidence that a sitemap is published here. Settling it takes one "
                    "request - `curl -sI {}` - and this run had none left to "
                    "spend".format(unverified["url"], unverified["parse_error"],
                                   unverified["url"]))
        if not reachable:
            result.skip("sitemap-present",
                        "{} answered HTTP 200 with something the XML parser could not read, "
                        "and whether that is a sitemap or a page was not established, so "
                        "whether this site publishes a sitemap is unknown rather than "
                        "settled either way".format(unverified["url"]))
            result.skip("sitemap-urls-resolve",
                        "no sitemap was read, so it listed no URLs to check")
            result.signal("sitemap_checked", False)
            result.signal("sitemap_body_unverified", sorted(s["url"] for s in broken))
            result.signal("sitemap_url_count", 0)
            return
        result.signal("sitemap_body_unverified", sorted(s["url"] for s in broken))
    elif broken:
        # Which file is broken decides how much this matters. A law firm's
        # report led with "An XML sitemap is published but does not parse",
        # marked do-first, about `/sitemap.xml` - a zero-byte orphan nothing
        # references. Its real sitemap is the index robots.txt declares, which
        # was fetched in the same run, parsed, and holds thirty-two working
        # sub-sitemaps. A file the site never points at is not the site's
        # sitemap, and telling an owner their sitemap is broken when it is not
        # sends them to fix the wrong thing.
        declared_ok = [s for s in reachable if s.get("referenced_in_robots")]
        stray = [s for s in broken if not s.get("referenced_in_robots")]
        orphan_only = bool(declared_ok) and len(stray) == len(broken)
        # Same accounting fault as the branch above: `_current_check` still
        # held `sitemap-urls-resolve`, the last of the three registered at the
        # top of this function, so a finding about a file that will not parse
        # was filed in the appendix under the check that samples the URLs
        # inside it.
        result.check("sitemap-parses")
        result.add(
            id_hint="sitemap-does-not-parse",
            title="An unreferenced file at the conventional sitemap location does not parse"
                  if orphan_only else "An XML sitemap is published but does not parse",
            severity="low" if orphan_only else "medium", confidence="high",
            evidence="{}: {}.{}".format(
                broken[0]["url"], broken[0]["parse_error"],
                " Your working sitemap is the one robots.txt declares, {}, which parsed and "
                "lists {}. This file is a leftover, not your sitemap.".format(
                    declared_ok[0]["url"], sitemap_total_phrase(sitemap_scope(declared_ok)))
                if orphan_only else ""),
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

    scope = sitemap_scope(sitemaps)
    total_urls = scope["urls_counted"]
    total_lastmod = scope["lastmods_counted"]
    result.signal("sitemap_url_count", total_urls)
    # Whether that number is the site's total or this audit's subtotal. Three
    # reports in one batch published a subtotal as a total, and two of them
    # then divided by it.
    result.signal("sitemap_count_complete", scope["complete"])
    if not scope["complete"]:
        result.signal("sitemap_children_unread",
                      scope["children_declared"] - scope["children_read"])

    # Two separate reads of the same file, because "the parser returned no
    # Sitemap: line" and "the file has no Sitemap: line" are different
    # statements. A byte-order mark, an `=` instead of a `:`, or a line the
    # grouping logic dropped produces the first without the second, and
    # telling an owner to add a line their file already carries sends them to
    # edit the wrong thing. The raw body is in the snapshot and nothing here
    # had ever looked at it.
    declared_in_robots = list((robots or {}).get("sitemaps") or [])
    raw_robots = ((robots or {}).get("raw") or "").replace(BOM, "")
    raw_names_sitemap = bool(
        re.search(r"^\s*sitemap\s*[:=]", raw_robots, re.IGNORECASE | re.MULTILINE))
    if raw_names_sitemap and not declared_in_robots:
        result.signal("robots_sitemap_line_unparsed", True)
    # A robots.txt this crawler was refused is not a robots.txt with no
    # Sitemap line, so the claim is not made at all in that case.
    if (reachable and _robots_was_read(robots)
            and not declared_in_robots and not raw_names_sitemap
            and not any(s.get("referenced_in_robots") for s in sitemaps)):
        result.add(
            id_hint="sitemap-not-referenced-in-robots",
            title="The sitemap is not referenced from robots.txt",
            severity="low", confidence="high",
            checked=("the `Sitemap:` lines read out of robots.txt",
                     "the raw text of robots.txt, searched line by line for a `Sitemap:` "
                     "entry the reader may have dropped"),
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

    _check_sitemap_lists_private_paths(result, reachable, snapshot.get("pages") or [])

    # Whether the sitemap's dates are meaningful is mechanism D, and
    # freshness-corroboration-audit owns it. This skill owns only whether the
    # sitemap exists, parses, and resolves.
    _check_sitemap_urls_resolve(result, snapshot, reachable, fetcher, robots)


# The addresses this finding is allowed to call a cart, an account or a
# wishlist. Every word here is one this check names to the reader, and nothing
# is here that it cannot name.
#
# It used to reuse `is_forbidden_path` for the whole judgement, on the reasoning
# that the read-only guard is the marketplace's own written statement of which
# addresses hold something different for every visitor. That was the defect. The
# guard answers "may this crawler fetch this URL", which is deliberately
# cautious and covers machine endpoints as well as private ones, and this
# finding asks "is this address a shop's basket", which is a claim about the
# site. Two different questions with two different costs of being wrong, and one
# answer was being used for both.
#
# What it cost: `api` is a segment in the guard's vocabulary, so a database
# project's documentation tree - 3250 sitemap entries, not one of them a cart,
# a checkout, an account or a wishlist, and 157 of them under `/api/` - was told
# at confident tier that 5.0% of its sitemap was per-visitor addresses, with a
# fix reading "Remove the cart, checkout, account and wishlist addresses from
# your sitemap". Following it would have deleted the project's own C-API
# reference from its own sitemap.
#
# So the vocabulary is written out here, grouped by the word the evidence uses,
# and the guard is now only consulted for the separate sentence about what this
# audit's own crawler refused to fetch - which is a fact about the audit and is
# reported as one.
#
# `api`, `graphql` and `xmlrpc.php` are in the guard and deliberately not here:
# they are machine endpoints, not per-visitor pages, and on a documentation site
# `/api/` is the reference manual. `compare` was here and is gone with them - a
# review site's `/compare/laptops` is an article anyone can read, and this
# finding may not claim a page is a session when it cannot tell.
#
# Matched as whole path segments, so a gift shop's `/wishlist-ideas` and a
# blog's `/login-security-explained` are articles and stay articles.
_PER_VISITOR_SEGMENTS = {
    # Matched by position in `_per_visitor_kind`, through
    # `audit_common.cart_path_word`, and not by the regex built below.
    "cart": CART_WORDS,
    "checkout": ("checkout",),
    "account": ("account", "accounts", "my-account", "login", "wp-login",
                "signin", "sign-in", "logout", "signout", "register",
                "signup", "sign-up", "password"),
    "administration": ("admin", "wp-admin"),
    "wishlist": ("wishlist", "wishlists"),
}

_PER_VISITOR_RES = {
    kind: re.compile(r"(?:^|/)(?:{})(?:[./]|$)".format(
        "|".join(re.escape(segment) for segment in segments)), re.I)
    for kind, segments in _PER_VISITOR_SEGMENTS.items()
}

# Above this share of the listed URLs, the sitemap is mostly pointing crawlers
# at addresses no crawler can use, which is a different size of problem from
# four stray entries in a catalogue of hundreds.
PRIVATE_PATH_HIGH_SHARE = 10

SITEMAP_PRIVATE_PATH_EXAMPLES = 6


def _per_visitor_kind(url):
    """Which kind of per-visitor address this is, in one word, or "".

    Replaces `_is_per_visitor_address`, which answered yes or no by asking
    `is_forbidden_path` - the crawler's read-only guard, whose question is not
    this one. See `_PER_VISITOR_SEGMENTS` above.

    The word is returned rather than a boolean because the finding prints it
    beside each URL. A reader given "cart, checkout, account or wishlist
    addresses" and a list has to take the classification on trust; a reader
    given `/my-account/ (account)` can check it in a second, and a wrong one is
    visible on the page instead of buried in a vocabulary.
    """
    try:
        path = urlparse(url).path or "/"
    except ValueError:
        return ""
    for kind in sorted(_PER_VISITOR_RES):
        if kind == "cart":
            # By position, not by word. A handicrafts shop files its baskets
            # at `/c/basket` - "Buy Basket Online" - and this finding named
            # that category a cart at confident tier. A basket word counts
            # where a basket lives: first in the path, first after a locale
            # prefix, or under an account or checkout segment.
            if cart_path_word(path, CART_WORDS):
                return kind
            continue
        if _PER_VISITOR_RES[kind].search(path):
            return kind
    return ""


def _fetched_as_a_listing(page):
    """Did the crawl read this address and find a shelf of things for sale?

    The page itself outranks the word in its address. A cart, an account or a
    wishlist that a crawler can fetch is an empty one, so a page typed as a
    category or a product, declaring a listing, carrying a buy control or
    reading as an index of other pages is not one visitor's session.
    """
    if not page or page.get("status") != 200:
        return False
    if page.get("page_type") in ("category", "product"):
        return True
    types = {str(t).lower() for t in page.get("jsonld_types") or []}
    if types & {"itemlist", "collectionpage", "offercatalog", "product", "productgroup"}:
        return True
    if ((page.get("add_to_cart_controls") or {}).get("count") or 0) > 0:
        return True
    return bool(is_listing_page(page))


def _check_sitemap_lists_private_paths(result, reachable, pages=None):
    """Does the sitemap offer crawlers addresses that are different per visitor?

    A WooCommerce shop's sitemap indexed `/cart/`, `/checkout/`, `/my-account/`
    and `/wishlist/`, and a 70 KB report never mentioned it. The audit had
    every piece of the answer: it read those four `<loc>` entries out of the
    sitemap, and its own read-only guard had already refused to fetch three of
    them by name. It knew what they were and did not say the site was
    publishing them.

    What it costs the owner: a crawler's budget for the site is finite, and
    every request spent on an address that renders one visitor's empty basket
    is a request not spent on a product page. The pages cannot be answered from
    either - there is nothing on them that is true for the person asking - and
    a shop platform normally marks them `noindex`, which makes a sitemap entry
    and the page's own instruction contradict each other.

    Zero extra requests: the URLs are read from the sitemap the crawl already
    fetched, and nothing here asks for any of them.

    Every sentence this raises names the kinds it actually matched and no
    others. The version before it named four kinds in the evidence and four
    again in the fix whatever it had found, so a documentation tree whose only
    match was the word `api` inside the crawler's fetch guard was told to delete
    its cart, checkout, account and wishlist addresses - none of which it had.
    """
    result.check("sitemap-excludes-private-paths")
    listed = [entry["loc"] for record in reachable for entry in record.get("urls") or []]
    if not listed:
        result.skip("sitemap-excludes-private-paths",
                    "the sitemap listed no URLs, so there was nothing to examine")
        return
    fetched = {}
    for page in pages or []:
        for key in (page.get("url"), page.get("final_url")):
            if key:
                fetched.setdefault(key.rstrip("/"), page)
    matched = sorted({(url, _per_visitor_kind(url)) for url in listed
                      if _per_visitor_kind(url)
                      and not _fetched_as_a_listing(fetched.get(url.rstrip("/")))})
    private = [url for url, _kind in matched]
    if not private:
        result.skip("sitemap-excludes-private-paths",
                    "none of the {} listed in the sitemap is a cart, checkout, account, "
                    "login, administration or wishlist address".format(
                        plural(len(listed), "URL")))
        return

    share = pct(len(private), len(listed))
    refused = [url for url in private if is_forbidden_path(url)]
    # The kinds this run really found, in the order the vocabulary lists them,
    # so the evidence and the fix name the same set and neither can name one
    # the site does not have.
    kinds = [kind for kind in _PER_VISITOR_SEGMENTS if kind in {k for _u, k in matched}]
    named = ", ".join(kinds[:-1]) + " and " + kinds[-1] if len(kinds) > 1 else kinds[0]
    result.signal("sitemap_private_paths",
                  ["{} ({})".format(url, kind)
                   for url, kind in matched[:SITEMAP_PRIVATE_PATH_EXAMPLES]])
    result.add(
        id_hint="sitemap-lists-private-paths",
        title="The sitemap offers crawlers {} that are different for every visitor".format(
            plural(len(private), "address", "addresses")),
        severity="medium" if share >= PRIVATE_PATH_HIGH_SHARE else "low",
        confidence="high",
        evidence="{} of the {} this sitemap lists ({}%) are {} addresses, each named here "
                 "with the path segment that says so: {}.{} A sitemap is a list of the pages "
                 "you want fetched, so each of these spends a crawler's budget on a page whose "
                 "content is one visitor's session and holds nothing that is true for anyone "
                 "else asking.".format(
                     len(private), plural(len(listed), "URL"), share, named,
                     "; ".join("{} ({})".format(url, kind)
                               for url, kind in matched[:SITEMAP_PRIVATE_PATH_EXAMPLES])
                     + (" and {} more".format(len(private) - SITEMAP_PRIVATE_PATH_EXAMPLES)
                        if len(private) > SITEMAP_PRIVATE_PATH_EXAMPLES else ""),
                     " This audit's own crawler refused to request {} of them, under the rule "
                     "that keeps it off private and state-changing addresses, and read them "
                     "out of the sitemap instead.".format(len(refused)) if refused else ""),
        mechanism="A", root_cause="sitemap-broken",
        summary="Remove the {} addresses listed below from your sitemap.".format(named),
        how_to_fix=[
            "In whatever generates the sitemap, exclude these pages. Every sitemap plugin and "
            "every CMS with a built-in generator has a per-page or per-post-type switch for "
            "it; the pages listed above are the ones to switch off.",
            "Check what those pages say about themselves: a shop platform normally sends them "
            "with `<meta name=\"robots\" content=\"noindex\">`. A URL that is in the sitemap "
            "and carries `noindex` gives a crawler two opposite instructions, and which one "
            "wins is up to the crawler.",
            "Leave them reachable to people. This is about what you advertise to crawlers, "
            "not about hiding the basket - a visitor still needs the link in the header.",
        ],
        effort="low", owner="developer",
        rationale="A crawler spends a limited number of requests per visit. Entries it cannot "
                  "use displace product and content pages it can, and a sitemap that lists "
                  "pages nobody can be shown teaches the crawler to trust the rest of the "
                  "file less.",
        affected_pages=private[:SITEMAP_PRIVATE_PATH_EXAMPLES],
    )


def _check_sitemap_urls_resolve(result, snapshot, reachable, fetcher, robots=None):
    """Find sitemap entries that 404, preferring statuses the crawl already has."""
    result.check("sitemap-urls-resolve")
    # The same rule as the sitemap location itself, one level down. This check
    # decides a URL is alive from its status code, and on an origin that
    # answers 200 to an address nobody published every entry is alive whether
    # the page is there or not. Silence from this check would then be read as
    # "every URL in the sitemap resolves", which is a statement the run cannot
    # support - and the site already has a finding saying exactly why.
    if _origin_serves_a_page_for_any_path(result):
        result.skip("sitemap-urls-resolve",
                    "this origin answers HTTP 200 with a page for addresses that do not exist "
                    "- {} is not published and resolves - so a 200 from a sitemap entry does "
                    "not show the page is there and the entries were not scored. Fixing the "
                    "missing-page handling is what makes this checkable".format(
                        snapshot["origin"].rstrip("/") + NONSENSE_PATH))
        return
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
    # Filter first, then sample. Drawing eight from the whole sitemap and then
    # dropping the ones the crawl already fetched left one or two probes on a
    # small site - against a SKILL.md that promises up to eight.
    #
    # And robots.txt applies here as it does everywhere else. A sitemap that
    # lists a disallowed path is an ordinary misconfiguration, and probing it
    # would break the one guarantee this marketplace makes about itself.
    candidates = [u for u in listed
                  if u not in known
                  and not is_disallowed(robots or {}, USER_AGENT, path_of(u))]
    to_probe = sample(candidates, SITEMAP_PROBE_LIMIT)
    if head_supported is False:
        unchecked += len(to_probe)
        to_probe = []
    if fetcher is not None:
        for url in to_probe:
            response = fetcher.try_get(url, method="HEAD")
            status = response.status_code if response is not None else None
            verdict = link_verdict(status)
            if verdict == "dead":
                # HEAD may say alive on its own; it may not say dead on its
                # own. Support is per-URL, not per-site: a global law firm
                # answers HEAD honestly on its homepage and 404 to HEAD on
                # individual articles that serve 200 to a reader. Three of four
                # "link targets return an error", reported at high confidence,
                # were live pages of current content. `confirm_dead` is the
                # rule the rest of the marketplace already follows; this check
                # never got it.
                status = confirm_dead(fetcher, url, status)
                verdict = link_verdict(status)
            if verdict == "dead":
                dead.append((url, status))
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


# Above this many characters of HTML the response is a site, not a holding
# screen. Cloudflare's interstitial is about 60 KB and the largest measured
# vendor page is under 120 KB.
CHALLENGE_HTML_CEILING = 250_000


def _served_challenge(response):
    """Which bot manager served this response, if any. None if it is content.

    A bot manager does not have to answer with an error status. Cloudflare's
    interstitial, AWS WAF's and Vercel Security Checkpoint all return a page
    whose body is a verification screen, and two of the three can do it under
    HTTP 200. The comparison below read the status line only, so an edge that
    hands every AI crawler a "Checking your browser" page and hands us the site
    scored as no differential at all - blind in the case the check exists for,
    and the crawl's own challenge check could not see it either, because the
    crawl never sends a crawler's name.
    """
    if response is None:
        return None
    html = response_text(response)
    # A verification page is a holding screen. Parsing a megabyte of real site
    # to be told it is not one costs a second per probe and can never say yes,
    # because `detect_challenge` returns None above its own text ceiling.
    if not html or len(html) > CHALLENGE_HTML_CEILING:
        return None
    soup = make_soup(html)
    headers = response.headers or {}
    vendor = detect_challenge(html, visible_text(soup), response.status_code, headers)
    if vendor is None:
        return None
    # A marker says a vendor's code is in this document. Being refused is a
    # different fact, and the block above `_challenge_stands` measures what
    # conflating them cost. The same four questions are asked here, with the
    # two the snapshot cannot answer - the response headers and the document's
    # own links - answered from the live response instead of from a record.
    if _status_is_a_refusal(response.status_code):
        return vendor
    if _mitigation_header_fired(headers):
        return vendor
    if _posts_to_a_challenge_provider(
            form.get("action") for form in soup.find_all("form")):
        return vendor
    if _html_is_wired_into_the_site(soup, getattr(response, "url", "") or ""):
        return None
    return vendor


def _pick_comparison_url(snapshot, home, robots):
    """Which URL to run the user-agent comparison against, and its baseline.

    The homepage, unless the crawl already found a page the site refused. On a
    museum the homepage answers this audit HTTP 200 while 57 of 60 inner pages
    answer 403, and the comparison took its one sample from the single page
    that edge does not refuse: it found no differential and was filed as a
    check that ran and found nothing wrong, in the same report as a critical
    finding saying that edge treats this crawler differently. A refusal the
    crawl already recorded is the response worth asking about.

    Returns `(url, baseline_status)`.
    """
    home_url = snapshot["origin"].rstrip("/") + "/"
    baseline = (home or {}).get("status")
    if baseline in REFUSED_STATUS:
        return home_url, baseline
    refused = sorted(
        (p for p in snapshot.get("pages") or []
         if p.get("url") and p["url"] != home_url and p.get("status") in REFUSED_STATUS
         and not is_disallowed(robots or {}, USER_AGENT, path_of(p["url"]))),
        key=lambda p: p["url"])
    if refused:
        return refused[0]["url"], refused[0]["status"]
    return home_url, baseline


def _check_bot_manager(result, snapshot, robots, fetcher, allow_network, time_budget=None):
    """Compare one URL's response for our UA against one AI crawler UA.

    A WAF or bot manager that returns 403 to a crawler is invisible in
    robots.txt, so this is the only way to see it.

    The URL is the homepage only when the crawl recorded no refusal anywhere
    else, because a homepage that answers is the least informative page on a
    site whose inner pages do not.
    """
    result.check("bot-manager-user-agent-comparison")
    home_url = snapshot["origin"].rstrip("/") + "/"
    home = next((p for p in snapshot.get("pages") or [] if p["url"] == home_url), None)

    if not allow_network or fetcher is None:
        # Not "disabled for this run" unconditionally: the orchestrator appends
        # the same flag when the caller asks for it and when the run has spent
        # its clock. See `no_network_reason`.
        result.skip("bot-manager-user-agent-comparison",
                    no_network_reason(
                        snapshot, time_budget,
                        flag_reason="extra network requests were disabled for this run",
                        exhausted_reason="no user-agent comparison was made"))
        return UA_UNTESTED
    if home is None or home.get("status") is None:
        result.skip("bot-manager-user-agent-comparison",
                    "the homepage was not fetched, so there is no baseline to compare against")
        return UA_UNTESTED

    probe_url, baseline = _pick_comparison_url(snapshot, home, robots)
    # Named in every sentence this check writes, and recorded for the report,
    # because a comparison is a statement about the one URL it asked for.
    result.signal("ua_comparison_url", probe_url)
    candidates = _probe_candidates(robots)
    if not candidates:
        result.skip("bot-manager-user-agent-comparison",
                    "robots.txt already disallows every AI answer crawler, so the robots finding "
                    "covers this and no probe request was made")
        return UA_UNTESTED

    # The baseline is this tool's own response, and when that is itself a
    # refusal the comparison is between two refused requests. It recorded
    # "no differential" and passed, on a hospital site where every page was
    # blocked - blind in exactly the case the check exists for. Asking the
    # question the other way round still answers something useful: if the
    # refusal follows the request under two crawler names as well, it is not
    # the name that is being refused.
    if baseline in REFUSED_STATUS:
        return _probe_a_refusal(result, fetcher, probe_url, baseline, candidates, robots)

    # One agent getting through does not tell you a bot manager is off. Bot
    # rules are per-agent and per-vendor: an edge that blocks Applebot while
    # answering Claude-SearchBot normally is a real configuration, and probing
    # only whichever name happened to sit first in a list reported it as clean.
    # Two agents, from two operators, on the two different roles.
    candidate = None
    response = None
    challenge = None
    tried = []
    unreachable = 0
    # What the crawl itself received under this audit's own name, recorded by
    # `page_extract`, for the URL being compared rather than for the homepage.
    # An origin already showing everyone a challenge is not showing one *to
    # crawlers*, so that vendor is not a differential.
    probed_page = next((p for p in snapshot.get("pages") or []
                        if p.get("url") == probe_url), home) or {}
    baseline_challenge = probed_page.get("challenge")
    for name in candidates:
        attempt = fetcher.try_get(probe_url, user_agent=_ua_string(name))
        tried.append(name)
        if attempt is None:
            unreachable += 1
            continue
        # Status first, then the body. An origin already serving everyone a
        # challenge is not serving one *to crawlers*, so a vendor we also see
        # under our own name is not a differential.
        vendor = None
        if attempt.status_code == baseline:
            vendor = _served_challenge(attempt)
            if vendor and vendor == baseline_challenge:
                vendor = None
            if not vendor:
                continue
        candidate, response, challenge = name, attempt, vendor
        break
    if candidate is None:
        if unreachable == len(tried):
            result.skip("bot-manager-user-agent-comparison",
                        "the comparison requests failed for a reason unrelated to the site's "
                        "bot rules")
            return UA_UNTESTED
        result.signal("bot_manager_block", False)
        result.signal("bot_manager_agents_probed", tried)
        # One URL was compared, so one URL is what this may claim. The old code
        # said nothing here and the check went into the report as "ran and
        # found nothing wrong" - on a museum whose homepage answers 200 while
        # 57 of 60 inner pages answer 403, in the same report as a critical
        # finding about that edge. Naming the URL is what stops a sample being
        # read as a verdict on the site.
        result.skip(
            "bot-manager-user-agent-comparison",
            "{} answered {} the same way for this audit and for {}, so at that URL the edge "
            "treats these crawler names no differently. The comparison covers that one URL: a "
            "rule applied to other paths would not appear in it".format(
                probe_url,
                "HTTP {}".format(baseline) if baseline is not None else "identically",
                describe_agents(tuple(tried), limit=2)))
        return UA_UNTESTED

    # Two confirmations, and the first one is under our own name.
    #
    # `baseline` was recorded minutes earlier, before the crawl spent its
    # request budget against this origin. A throttle the crawl itself tripped
    # answers 429 or 503 to everything now - including these probes - and the
    # old test only asked whether the crawler-named request differed from that
    # stale baseline. It did, so a rate limiter was reported as "a bot manager
    # or WAF serves AI crawlers a different response from the homepage", at
    # critical severity, on any origin that rate-limits.
    #
    # Re-establishing the baseline first separates the two: if our own user
    # agent no longer gets what it got before, the origin's behaviour changed
    # for everyone and the comparison has nothing to say.
    rebaseline = fetcher.try_get(probe_url)
    if rebaseline is None or rebaseline.status_code != baseline:
        result.skip("bot-manager-user-agent-comparison",
                    "this origin no longer answers this audit's own user agent the way it did "
                    "during the crawl ({} then, {} now), so a difference under a crawler's name "
                    "cannot be attributed to the name. That is a rate limiter or a transient "
                    "edge state, not a bot rule".format(
                        baseline,
                        rebaseline.status_code if rebaseline is not None else "no response"))
        return UA_UNTESTED

    confirm = fetcher.try_get(probe_url, user_agent=_ua_string(candidate))
    confirm_challenge = _served_challenge(confirm) if challenge else None
    if confirm is None or (confirm.status_code == baseline
                           and confirm_challenge != challenge):
        result.skip("bot-manager-user-agent-comparison",
                    "the first probe differed but the confirmation request matched the baseline; "
                    "treated as transient rather than a bot block")
        return UA_UNTESTED

    result.signal("bot_manager_block", True)
    result.signal("bot_manager_agents_probed", tried)
    # A verification page is a block whatever status line carries it: the
    # crawler receives no content either way.
    blocking = (bool(challenge)
                or confirm.status_code in (401, 403, 405, 406, 429)
                or confirm.status_code >= 500)
    result.signal("bot_manager_challenge", challenge or "")
    # The same name slot as the challenge finding's, and the same reason it is
    # rendered rather than substituted: `detect_challenge` may hand back a
    # CAPTCHA widget's name, and "carrying a a CAPTCHA widget (hCaptcha)
    # verification page" is what this sentence used to print.
    difference = ("HTTP {} with {}".format(
                      confirm.status_code, _challenge_page_phrase([challenge]))
                  if challenge else "HTTP {}".format(confirm.status_code))

    # One name refused is a rule; it is not the list an allow rule is written
    # from. This check probes two operators, so the finding named one agent and
    # the reader had no way to know which of the other eleven answer crawlers
    # share its fate. On a museum the refused set is `Claude-SearchBot` and
    # `PerplexityBot` and the served set is three others, and an owner acting
    # on the old finding would have allowed one of the two.
    #
    # So the list is measured, through the second HTTP client, and the client
    # is held constant across every name in it.
    probe = _ask_each_name_again(result, fetcher, probe_url, robots)
    named = probe if probe is not None and probe.refused and probe.served else None
    # High at most, and medium confidence. Every request here carried a
    # crawler's name from this audit's own address, and the crawler never
    # sends from there: it sends from its operator's published ranges. CDNs
    # routinely refuse a request that claims a verified crawler's name from
    # an address that is not that crawler's, and serve the real one. So the
    # measurement cannot tell a rule on the name from a rule checking the
    # address behind it, and two shops were told at critical, first in
    # "Start here", to allow crawlers their CDN may already let in.
    result.add(
        id_hint="bot-manager-blocks-ai-crawlers",
        title="A bot manager or WAF refuses {} and serves the rest".format(
            plural(len(named.refused), "named answer-engine crawler",
                   "named answer-engine crawlers"))
              if named else
              "A bot manager or WAF serves AI crawlers a different response from the homepage",
        severity="high" if blocking else "medium",
        confidence="medium",
        # Where the second client enumerated the names, its two lists replace
        # the two clauses that used to stand in for them - "robots.txt does not
        # disallow <candidate>" and "<the other probe name> answered normally".
        # Both are subsumed by a table that names every agent on each side, and
        # printing all four sentences said the same thing twice in a finding
        # whose whole job is to be acted on line by line.
        evidence="{} returned {} for user agent `{}` on two consecutive requests, but "
                 "HTTP {} with the page itself for the audit user agent.{}{}{}".format(
                     probe_url, difference, candidate, baseline,
                     "" if named else
                     " robots.txt does not disallow {}.{}".format(
                         candidate,
                         " {} answered normally, so the rule is written per agent rather "
                         "than against every crawler.".format(
                             ", ".join("`{}`".format(n) for n in tried if n != candidate))
                         if len(tried) > 1 else ""),
                     _named_probe_sentence(named, probe, robots, probe_url),
                     ADDRESS_CAVEAT),
        mechanism="A", root_cause="bot-manager-block",
        summary=("Check whether your CDN or WAF refuses {} by name, or only refused a request "
                 "claiming the name from an address that is not the crawler's.".format(
                     ", ".join("`{}`".format(n) for n in named.refused)) if named else
                 "Check whether your CDN or WAF refuses AI answer crawlers by name, or only "
                 "requests claiming their names from addresses that are not theirs."),
        how_to_fix=_named_block_steps(named) if named else [
            VERIFIED_BOT_STEP,
            "Open your CDN or WAF bot-management settings (Cloudflare, Akamai, Fastly, "
            "AWS WAF and similar all have a bot category list).",
            "Add {} and the other AI answer crawlers to the allow list, or set their "
            "category to 'allow' rather than 'challenge' or 'block'.".format(candidate),
            "Verify by requesting the homepage with that user-agent string and confirming "
            "a 200 with real HTML, not a challenge page.",
            "Keep rate limiting in place; allow-listing does not mean removing throttles.",
        ],
        effort="medium", owner="developer",
        # Two rationales, because a 403 to two named agents is not a challenge
        # page and the reader is being asked to act on the difference. The
        # served agents are the load-bearing half: they are why nothing else
        # about this site looks wrong.
        rationale="These agents fetch pages so an assistant can answer a question with them. "
                  "Refused at the edge they read nothing, so the brand cannot be quoted or "
                  "linked in that assistant's answer however good the page is - while the "
                  "agents that were served make the site look reachable from every other "
                  "direction, which is why this stays invisible."
                  if named else
                  "Robots.txt permission is irrelevant if the edge returns a "
                  "challenge page. The crawler receives no content, so the brand cannot appear "
                  "in an answer even though the site looks open on paper.",
        affected_pages=[probe_url],
    )
    return UA_NAMES_DIFFER if named else UA_KEYED


def _named_probe_sentence(named, probe, robots, url):
    """What the second client added to a differential the first one found.

    Three outcomes and three sentences, because the second client can confirm
    the first, can widen it, or can disagree with it - and a report that
    prints the same sentence for all three is describing a measurement it did
    not make.
    """
    if named is not None:
        unmentioned = _names_robots_does_not_mention(robots, named.refused)
        return (" A second HTTP client then asked for the same URL under {} crawler names in "
                "turn: {} were refused and {} were answered with the page. That client was "
                "held constant across all {} requests, so the name is the only thing that "
                "differed.{}".format(
                    len(named.seen),
                    describe_agents(tuple(named.refused), limit=6),
                    describe_agents(tuple(named.served), limit=6),
                    len(named.seen),
                    " robots.txt names {} nowhere, so the rule that refuses them is in a CDN "
                    "or WAF console rather than in a file the site publishes.".format(
                        ", ".join("`{}`".format(n) for n in unmentioned))
                    if unmentioned else ""))
    if probe is not None and probe.served and not probe.refused:
        return (" A second HTTP client was then answered HTTP 200 at that URL under every "
                "one of the {} crawler names it tried, including that one, so the refusal "
                "above may be about the HTTP client this audit sends with rather than about "
                "the name. Read the CDN log for both requests before writing a "
                "name-based rule.".format(len(probe.seen)))
    return ""


def _probe_a_refusal(result, fetcher, target_url, baseline, candidates, robots=None):
    """We were refused. Is it the name we sent, or the request itself?

    Not a finding of its own - the homepage-refusal finding already says the
    site refused us, and saying it twice was a separate bug. This decides
    which cause that finding is allowed to name.

    What it can decide is narrower than it used to claim, and it is narrow in
    both directions. This audit has one HTTP client, so every probe here shares
    it and the name is the only variable that moves.

    When some names get through, that separates nothing: measured on a museum
    and on a hardware manufacturer, from one machine minutes after each run and
    with one user-agent string throughout, this client was answered 403 while
    curl and urllib sending that same string were answered 200. An edge reading
    the name and an edge scoring the client while exempting known crawler names
    both produce it.

    When no name gets through, one client separates nothing either, and for
    the same reason. Measured on a charity, one request per name with plain
    curl: three crawler names were refused and four were served, while through
    this client all seven were refused. Reading uniform refusals as "the name
    is not what matters" published the reverse of that table.

    So a uniform refusal is now re-asked through a second HTTP client rather
    than reasoned about. See `_ask_each_name_again`: it changes the client and
    holds it constant across every name, which is the one arrangement that can
    tell an edge reading names from an edge refusing this client.
    """
    seen = {}
    for name in candidates:
        attempt = fetcher.try_get(target_url, user_agent=_ua_string(name))
        if attempt is not None:
            seen[name] = attempt.status_code
    result.signal("refusal_probe", seen)
    result.signal("refusal_probe_agents", sorted(seen))

    if not seen:
        result.skip("bot-manager-user-agent-comparison",
                    "{} refused this crawler (HTTP {}), and the comparison requests failed too, "
                    "so nothing could be established about what the refusal keys on".format(
                        target_url, baseline))
        return UA_UNTESTED

    got_through = sorted(name for name, status in seen.items() if status == 200)
    if got_through:
        # Recorded so the challenge, homepage-refusal and refusal-rate checks
        # can name the agents that were served. Without the list they had only
        # the verdict, and one of them published the opposite claim anyway.
        result.signal("crawlers_served_while_audit_refused", got_through)
        # Two causes fit, and this run cannot separate them. The old sentence
        # picked one: "the edge is reading the user agent", which sent a
        # hardware manufacturer's owner to write a user-agent allow rule that
        # would not have lifted a block keyed on the client.
        result.signal("refusal_cause_undetermined", True)
        result.skip(
            "bot-manager-user-agent-comparison",
            "{} refused this audit (HTTP {}) and answered {} with HTTP 200 when this same "
            "client asked again under those names. Two rules fit that pair and one HTTP "
            "client cannot "
            "separate them: the edge may be reading the user-agent string, or it may be "
            "scoring this client's TLS and header fingerprint while exempting recognised "
            "crawler names whatever client sends them. Either way the AI crawlers were "
            "served and this audit was not".format(
                target_url, baseline, describe_agents(tuple(got_through), limit=2)))
        return UA_AUDIT_ONLY

    # Every arm of the comparison so far used one HTTP client, so the client is
    # a confound it cannot see past and "the name does not matter" is not
    # available as a conclusion from it. The confound is breakable, though, and
    # this is where it gets broken: ask again through a different client, one
    # name at a time, with that client held constant throughout.
    probe = _ask_each_name_again(result, fetcher, target_url, robots, want_control=True)

    if probe is not None and probe.refused and probe.served:
        return _report_named_crawler_block(result, probe, robots, baseline)

    if probe is not None and probe.served and not probe.refused:
        # No crawler name was refused through the second client. Two states
        # hide under that, and the control request under this audit's own name
        # is what tells them apart - which is why it is only spent here.
        if probe.control == 200:
            # This audit's own name was served too, so nothing about a name is
            # being read. What that edge refuses is the HTTP client the rest of
            # this audit sends with.
            result.signal("refusal_cause_undetermined", False)
            result.skip(
                "bot-manager-user-agent-comparison",
                "{} refused this audit's HTTP client (HTTP {}) under its own name and under "
                "{}. A second HTTP client sent from this same machine seconds later was "
                "answered HTTP 200 at that URL under all {} of those names and under this "
                "audit's own name. The name is not what that edge reads: it is refusing the "
                "client, on its TLS and header fingerprint or on the address it came from, "
                "so no crawler allow rule would lift it".format(
                    probe.url, baseline, describe_agents(tuple(sorted(seen)), limit=2),
                    len(probe.served)))
            return UA_CLIENT_SCORED
        # The crawler names were served and this audit's own name was refused,
        # by both clients. The edge reads the name and decides in the crawlers'
        # favour, which is the one shape that is not a defect in the site.
        result.signal("crawlers_served_while_audit_refused", probe.served)
        # Read by `_refusal_cause_note`, which otherwise says the comparison
        # ran on one HTTP client and could not separate a name rule from a
        # client rule. Here it ran on two, and it did separate them.
        result.signal("served_by_second_client", True)
        result.signal("refusal_cause_undetermined", False)
        result.skip(
            "bot-manager-user-agent-comparison",
            "{} refused this audit (HTTP {}) under its own name from both HTTP clients this "
            "run has, and answered {} with HTTP 200 through the second one. The client "
            "changed and the outcome did not, so it is the user-agent string this edge "
            "reads, and it is deciding in the AI crawlers' favour".format(
                probe.url, baseline, describe_agents(tuple(probe.served), limit=3)))
        return UA_AUDIT_ONLY

    # Both clients refused under every name. That is more than one client's
    # worth of evidence and still not an answer: two clients can share an
    # address, and an edge refusing this network refuses both alike.
    result.signal("refusal_cause_undetermined", True)
    result.skip("bot-manager-user-agent-comparison",
                "{} refused this crawler (HTTP {}) and refused the same request under {} as "
                "well.{} What the edge reads could not be determined from here: an edge "
                "refusing every crawler name and an edge refusing this address or these "
                "clients produce the same table. What is established is that no crawler "
                "name this run tried lifted the refusal".format(
                    target_url, baseline, describe_agents(tuple(sorted(seen)), limit=2),
                    " The same URL was then asked for again through a second, unrelated HTTP "
                    "client, under {} name(s) and under this audit's own, and that client was "
                    "refused every time too, so this is not a property of one HTTP "
                    "library.".format(len(probe.seen)) if probe is not None else
                    " Every one of those requests went through this audit's one HTTP client, "
                    "and no second client was available to re-ask with, so an edge scoring "
                    "that client's fingerprint and an edge refusing every name are still "
                    "indistinguishable from here."))
    return UA_CLIENT_REFUSED


def _report_named_crawler_block(result, probe, robots, baseline=None):
    """The finding a second client earns: these names are refused, those are not.

    Distinct from the training-crawler finding, which robots.txt states, which
    is reported at info, and which says "this is often deliberate" - because
    blocking a training crawler is a rights decision an owner made on purpose.
    Nothing here was written in a file the owner can open. An answer crawler
    refused at the edge and absent from robots.txt is refused by a rule in a
    CDN or WAF console, and in every case measured it was a side effect of a
    bot category nobody read the membership of.

    Only reached where the client was held constant across every request, so
    the name is the one thing that moved.
    """
    unmentioned = _names_robots_does_not_mention(robots, probe.refused)
    statuses = sorted({probe.seen[name] for name in probe.refused})
    # The check this finding answers. Registered here rather than relied on
    # from the caller, because a finding may only claim the checks its own
    # function registered, and a check nothing claims is reported as one that
    # ran and found nothing wrong.
    result.check("bot-manager-user-agent-comparison")
    result.signal("bot_manager_block", True)
    # Medium confidence for the reason `ADDRESS_CAVEAT` gives: the name was
    # the only thing that differed in what this audit sent, and the address
    # it sent from is the one thing no crawler shares with it.
    result.add(
        id_hint="edge-refuses-named-answer-crawlers",
        title="The edge refuses {} by name while serving others".format(
            plural(len(probe.refused), "named answer-engine crawler",
                   "named answer-engine crawlers")),
        severity="high",
        confidence="medium",
        evidence="{} answered HTTP {} to {} and HTTP 200 with the page to {}. All {} "
                 "requests were sent from one HTTP client seconds apart, so the crawler "
                 "name is the only thing that differed between them.{}{}{}".format(
                     probe.url,
                     ", ".join(str(s) for s in statuses),
                     describe_agents(tuple(probe.refused), limit=6),
                     describe_agents(tuple(probe.served), limit=6),
                     len(probe.seen),
                     " robots.txt names {} nowhere, so this rule is not in a file on the "
                     "site: it is in a CDN or WAF bot-management console, where an owner "
                     "reading their own robots.txt cannot see it.".format(
                         ", ".join("`{}`".format(n) for n in unmentioned))
                     if unmentioned else
                     " robots.txt already disallows the same agents, so the edge is "
                     "enforcing a rule the file also states.",
                     " This audit's own HTTP client was refused at that URL as well{}, which "
                     "is a separate rule about the client rather than about any name.".format(
                         " (HTTP {})".format(baseline) if baseline else "")
                     if baseline else "",
                     ADDRESS_CAVEAT),
        mechanism="A", root_cause="bot-manager-block",
        summary="Check whether your CDN or WAF refuses {} by name, or only refused a request "
                "claiming the name from an address that is not the crawler's.".format(
                    ", ".join("`{}`".format(n) for n in probe.refused)),
        how_to_fix=_named_block_steps(probe),
        effort="medium", owner="developer",
        rationale="These agents fetch pages so an assistant can answer a question with "
                  "them. Refused at the edge they read nothing, so the brand cannot be "
                  "quoted or linked in that assistant's answer, however good the page is. "
                  "The agents that were served are the reason this is invisible rather than "
                  "obvious: the site looks reachable from every other direction.",
        affected_pages=[probe.url],
    )
    return UA_NAMES_DIFFER


# What a name probe cannot see. Every request this skill sends leaves from the
# machine running the audit, and an answer crawler never does: it sends from
# its operator's published address ranges, and the large CDNs verify that -
# by reverse DNS or against the published list - before treating a request as
# the crawler it claims to be. A request claiming `Claude-SearchBot` from any
# other address is an impersonator to that rule, and refusing impersonators is
# the rule working. Two shops were told at critical severity to allow crawlers
# their CDN may well have been serving all along.
ADDRESS_CAVEAT = (
    " These requests carried the crawlers' names but came from this audit's own "
    "address, not from the address ranges those crawlers publish. A CDN that checks a "
    "crawler's address before trusting its name - reverse DNS, or the operator's published "
    "list - refuses a request like this one and serves the real crawler, so this "
    "measurement cannot tell a rule on the name from a rule on the address behind it.")

VERIFIED_BOT_STEP = (
    "Before changing anything, find out which kind of rule this is. In your CDN or WAF, "
    "look at the verified-bot settings and at the firewall log for these crawler names: a "
    "rule that verifies the crawler's address (reverse DNS, or the operator's published IP "
    "list) refuses anyone else using the name and lets the real crawler in, and needs no "
    "change. Only a rule that refuses the name itself, whatever address it comes from, "
    "is the one to change.")


def _named_block_steps(probe):
    """The fix, written from the measurement rather than from the agent table."""
    steps = [
        VERIFIED_BOT_STEP,
        # The last sentence, because a rule keyed to crawler names proves
        # somebody wrote one and never proves the reader is that somebody. A
        # shop on a hosted platform has no console to open, and an instruction
        # to open one is an instruction it cannot carry out.
        "Open your CDN or WAF bot-management rules. Look for a bot category or managed "
        "rule set that covers 'AI crawlers' or 'AI scrapers' - a single category switch "
        "is what usually refuses these names, and the names inside it are not shown on "
        "the switch. If this site has no CDN or WAF account of its own, that rule set "
        "belongs to whoever hosts it: send their support the check below and the names "
        "in the next step, and ask for those names to be allowed.",
        "Set {} to allow, keeping your rate limits in place. Those are the names measured "
        "as refused here, and they are the whole list: {} were already answered HTTP 200 "
        "and need no rule.".format(
            ", ".join("`{}`".format(n) for n in probe.refused),
            ", ".join("`{}`".format(n) for n in probe.served)),
        "Verify from a machine outside your network: `for ua in {}; do curl -s -o /dev/null "
        "-w \"$ua %{{http_code}}\\n\" -A \"$ua\" {}; done`. Every name should come back "
        "200.".format(" ".join(probe.refused + probe.served), probe.url),
        "Leave your training-crawler rules alone. Those are different user agents, they "
        "produce no citations, and opening them buys nothing here.",
    ]
    if probe.control is not None and probe.control != 200:
        steps.append(
            "Separately, this audit's own user agent was answered HTTP {} at that URL by a "
            "second HTTP client too, so something is refusing unfamiliar clients as well as "
            "these names. Ask your CDN for the reason logged against those requests.".format(
                probe.control))
    return steps


def _probe_candidates(robots, limit=BOT_PROBE_AGENTS):
    """Up to `limit` answer agents robots.txt permits, from different operators.

    Picking one name off the front of a list made this check a lottery: on a
    fixture whose edge blocked five named crawlers, reordering the list changed
    the verdict from "a WAF blocks AI crawlers" to "nothing found", because the
    new first name happened to be one the edge allowed. Bot rules are written
    per agent and per vendor, so a second opinion from a different operator is
    worth one request.
    """
    picked, operators = [], []
    for name in ANSWER_CRAWLERS:
        if is_disallowed(robots, name, "/"):
            continue
        agent = AGENT_BY_TOKEN.get(name.lower())
        operator = agent.operator if agent else name
        if operator in operators:
            continue
        picked.append(name)
        operators.append(operator)
        if len(picked) >= limit:
            break
    return picked


# What the second HTTP client saw, name by name, at one URL.
#
# `refused` and `served` are the two lists an allow rule is written from.
# `control` is the status this audit's own user agent received through that
# same second client, which is what separates "the edge reads crawler names"
# from "the edge refuses the client this audit normally sends with".
NameProbe = namedtuple("NameProbe", "url refused served control seen")


def _ask_each_name_again(result, fetcher, url, robots, want_control=False,
                         limit=SECOND_TRANSPORT_AGENTS):
    """Ask one URL under each crawler name again, through a second HTTP client.

    The comparison this skill has always run holds the client fixed and moves
    the name, which answers one question in one direction: a name that is
    refused where the audit's own name is not means the edge reads names.
    The reverse never followed. Every probe left through one `requests`
    session, so a uniform refusal fits two rules with opposite fixes - an edge
    refusing every crawler name, and an edge scoring that client's TLS and
    header fingerprint and refusing it whatever name it carries. Measured on a
    charity with plain curl, one request per name: three crawler names came
    back 403 and four came back 200, while through this skill's client all
    seven came back 403. The report reasoned from the uniform 403s and
    published the reverse of that table.

    Changing the client is what breaks the tie. `Fetcher` now has a second
    one, `urllib.request` from the standard library, and it passes the same
    gate as the first: forbidden paths refused, GET or HEAD only, the request
    budget spent, the delay waited out, the wall clock checked. What differs
    is the socket, not the permission.

    Returns a `NameProbe`, or None when there was no second client, no budget,
    no clock, no name robots.txt permits, or fewer than two names answered -
    one status is not a comparison.
    """
    transport = getattr(fetcher, "second_transport", None)
    if fetcher is None or transport is None:
        return None
    if not fetcher.budget_left or not fetcher.time_left:
        return None
    names = _probe_candidates(robots or {}, limit=limit)
    if not names:
        return None

    seen, refused, served = {}, [], []
    for name in names:
        if not fetcher.budget_left or not fetcher.time_left:
            break
        attempt = fetcher.try_get(url, user_agent=_ua_string(name), transport=transport)
        if attempt is None:
            continue
        seen[name] = attempt.status_code
        # A 200 carrying a verification page is not the page. Reading the
        # status line alone is the mistake this whole file exists to stop
        # making, and it would put a challenged crawler in the served list.
        if attempt.status_code in REFUSED_STATUS or _served_challenge(attempt):
            refused.append(name)
        elif attempt.status_code == 200:
            served.append(name)

    if len(seen) < 2:
        return None

    # One request under this audit's own name, through the same second client,
    # and only where the caller says the primary client was refused. There it
    # is the request that separates an edge reading crawler names from an edge
    # refusing the client this audit sends with. Where the primary client was
    # served, that baseline is already the control and this would buy nothing.
    control = None
    if want_control and fetcher.budget_left and fetcher.time_left:
        answer = fetcher.try_get(url, transport=transport)
        if answer is not None:
            control = answer.status_code

    probe = NameProbe(url, sorted(refused), sorted(served), control,
                      dict(sorted(seen.items())))
    result.signal("second_client_probe",
                  {"url": url, "statuses": probe.seen, "control": control,
                   "transport": transport})
    if probe.refused:
        result.signal("edge_refuses_crawler_names", probe.refused)
    if probe.served:
        result.signal("edge_serves_crawler_names", probe.served)
    return probe


def _named_probe_from_signals(result):
    """The second client's table, for a check that did not run the probe itself.

    Three checks read this verdict and only one of them makes the requests. A
    check that reprints the conclusion without the two lists behind it is how
    a report came to say "the edge is reading the user agent" in one section
    and name none of the agents anywhere.
    """
    record = result.signals.get("second_client_probe") or {}
    refused = result.signals.get("edge_refuses_crawler_names") or []
    served = result.signals.get("edge_serves_crawler_names") or []
    if not (refused and served):
        return None
    return NameProbe(record.get("url") or "", list(refused), list(served),
                     record.get("control"), record.get("statuses") or {})


def _names_robots_does_not_mention(robots, names):
    """Of `names`, the ones robots.txt says nothing about at all.

    The point of the finding this feeds: a crawler refused at the edge and
    absent from robots.txt is refused by a rule written in a CDN or WAF
    console, which the owner cannot read by opening a file on their own site.
    A name robots.txt does disallow is a decision they made and can see.
    """
    groups = (robots or {}).get("groups") or []
    declared = set()
    for group in groups:
        for agent in group.get("agents") or []:
            declared.add(str(agent).strip().lower())
    return [name for name in names if name.lower() not in declared]


def _report_response_time(result, pages):
    """A site that answers, slowly, has a problem of its own.

    Every answer crawler runs on a timeout it does not publish, and a page
    that takes half a minute is a page some of them will never see. That is a
    real discoverability defect and it is not the outage the old wording
    described, so it gets its own root cause and its own fix.
    """
    result.check("origin-response-time")
    timing = response_timing(pages)
    if timing is None:
        result.skip("origin-response-time",
                    "no page was both fetched and timed, so there is nothing to measure")
        return
    if timing["median_ms"] < SLOW_ORIGIN_MEDIAN_MS:
        result.skip("origin-response-time",
                    "the median response across {} page(s) was {:.1f} s".format(
                        timing["measured_on"], timing["median_ms"] / 1000.0))
        return

    very_slow = timing["median_ms"] >= VERY_SLOW_ORIGIN_MEDIAN_MS
    result.add(
        id_hint="origin-answers-too-slowly",
        title="The site answers, but slowly enough that crawlers will give up",
        severity="high" if very_slow else "medium", confidence="high",
        evidence="Median response across {} page(s) that returned 200: {:.1f} s. Slowest: "
                 "{:.1f} s. These are server response times measured from this crawl, not "
                 "page-load scores, and they exclude every request that failed.".format(
                     timing["measured_on"], timing["median_ms"] / 1000.0,
                     timing["slowest_ms"] / 1000.0),
        mechanism="A", root_cause="slow-origin",
        summary="Get the median server response under a second, by caching pages rather than "
                "by making the template smaller.",
        how_to_fix=[
            "Measure where the time goes on one slow page: time to first byte against total "
            "transfer. A slow first byte is the server; a slow transfer is the payload.",
            "Put a full-page cache in front of anything a logged-out visitor sees, so a "
            "crawler is served from cache rather than from the application.",
            "Look for a query or a third-party call inside the page template - a stock "
            "lookup, a reviews widget, a currency conversion - that runs per request.",
            "Re-measure after each change. This is the one finding in this report where you "
            "can see the effect immediately.",
        ],
        effort="medium", owner="developer",
        rationale="Every AI crawler enforces a timeout it does not publish. A page slower "
                  "than that timeout is not slow to them, it is absent, and no amount of "
                  "markup on it can be read.",
    )

# What the user-agent comparison was able to establish about a refusal.
#
# The homepage-refusal finding used to assert the answer rather than ask for
# it. Its evidence sentence read "the request identified itself as an audit
# crawler ... so this is a bot-management rule", and its first fix was
# "allow-list the AI answer crawlers by user agent" - on three sites in one
# batch, every one of them ranked critical and first in "Start here".
#
# All three were checked by hand afterwards. A museum's edge returned 429 to a
# desktop browser string, to plain curl, and to GPTBot and ClaudeBot alike. A
# hospital's returned 200 to curl and 403 to this tool sending the same user
# agent, so the discriminator was the client and not the name. A law firm's
# 403 could not be reproduced at all in twelve requests. In none of the three
# would allow-listing a crawler name have changed anything, and in the first
# two the report sent the owner to the wrong setting entirely.
#
# The comparison that answers this already exists. What it lacked was a way to
# say "I could not tell", and a caller that asked.
UA_KEYED = "ua-keyed"          # named crawlers refused where this tool is not
# Every probe that could be made was refused - and every one of them left
# through this skill's single HTTP client, which is what both arms of the
# comparison share. A comparison whose arms share a confound cannot settle the
# question it was built to ask, so this verdict names the one fact it does
# establish and stops there.
#
# It used to be called `ua-blind`, and its consumers printed "the refusal is
# not keyed to the user agent" at high confidence. Measured afterwards on a
# charity, one request per name with plain curl from the same machine: four
# answer-crawler names and this audit's own name were answered HTTP 200 and
# three other crawler names 403, while through this client every one of them
# was answered 403. The name was exactly what that edge read, for three of
# them - and the report, reasoning from the uniform 403s this client received,
# published the opposite and named none of the three.
UA_CLIENT_REFUSED = "ua-client-refused"   # this client refused under every name tried
UA_UNTESTED = "ua-untested"    # no comparison was possible
# The mirror image of UA_KEYED, and it used to share that name. The edge reads
# the user agent, decides in the AI crawlers' favour, and refuses this audit.
# One constant meaning both directions is how a hardware manufacturer's report could
# print "the edge is reading the user agent" in its appendix and, from the same
# verdict, a critical finding claiming crawlers get a verification page.
UA_AUDIT_ONLY = "ua-audit-only"
# A second HTTP client, held constant across every request it made, was
# refused under some crawler names and answered with the page under others.
# That is the measurement the single-client comparison could never make: the
# client no longer moves, so the name is the only thing left that can explain
# the difference. It is the strongest verdict here and the only one that earns
# a list of names to allow, because the list is measured rather than copied
# out of this file's agent table.
UA_NAMES_DIFFER = "ua-names-differ"
# The same second client was answered with the page under every crawler name
# tried and under this audit's own name, while the primary client was refused
# under all of them. Nothing about any name is being read: what that edge
# refuses is the HTTP client this audit normally sends with, on its TLS and
# header fingerprint or on the address it came from. No crawler allow rule
# lifts it, and saying so is the whole point of separating this from
# UA_CLIENT_REFUSED, where the question genuinely stayed open.
UA_CLIENT_SCORED = "ua-client-scored"

# What one more request said about a refusal the crawl recorded.
REFUSAL_TRANSIENT = "transient"   # the same URL answers 200 now
REFUSAL_STANDS = "stands"         # it is refused again


def _recheck_a_refusal(result, snapshot, fetcher, robots=None, ua_verdict=None):
    """Ask one refused or challenged URL again, under this audit's own name.

    a museum refused 57 of 60 pages during the crawl and answers a single
    request with HTTP 200; the report called that a bot-management rule and
    told the owner to edit their CDN bot list. A crawl spending its whole
    request budget against one origin is the likeliest thing in the run to trip
    a rate limiter, and a rate limiter and a bot rule are indistinguishable
    from the crawl record alone.

    One extra GET separates them, on the same terms `confirm_dead` sets for
    links: a cheap probe may say alive, it may never say dead on its own. A 200
    here overturns the bot-rule claim; anything else leaves it untouched.

    Returns `(verdict, url, status)`, or None when no recheck was possible.
    """
    # Nothing to learn: the comparison already fetched this origin twice and
    # established that the refusal is aimed at this tool rather than at a
    # crawler name. UA_CLIENT_SCORED reaches the same place through a second
    # HTTP client that was answered under every name including this one's, so
    # a further request under that same refused client would only re-record a
    # refusal already accounted for.
    if ua_verdict in (UA_AUDIT_ONLY, UA_CLIENT_SCORED):
        return None
    if fetcher is None or not fetcher.budget_left or not fetcher.time_left:
        return None
    pages = snapshot.get("pages") or []
    # Only the marked pages something actually refused. Without the filter this
    # one request went to whichever marked URL sorted first, which on a shop
    # whose every page carries an hCaptcha form bundle was an ordinary product
    # page - so the request was spent, came back 200, and `_served_challenge`
    # called that 200 a challenge too. See the block above `_challenge_stands`.
    challenged = [p for p in pages if p.get("challenge") and _challenge_stands(p)[0]]
    refused = [p for p in pages if p.get("status") in REFUSED_STATUS]
    candidates = [p["url"] for p in (challenged or refused)
                  if p.get("url")
                  and not is_disallowed(robots or {}, USER_AGENT, path_of(p["url"]))]
    if not candidates:
        return None

    target = sorted(candidates)[0]
    response = fetcher.try_get(target)
    if response is None:
        return None
    status = response.status_code
    # A 200 carrying a verification page is not the page, and neither is a 200
    # at some other URL the request was sent to. Reading the status line alone
    # is the mistake this whole file exists to stop making.
    landed_elsewhere = _redirect_hop(target, response) is not None
    transient = (status == 200 and not landed_elsewhere
                 and _served_challenge(response) is None)
    verdict = REFUSAL_TRANSIENT if transient else REFUSAL_STANDS
    result.signal("refusal_recheck",
                  {"url": target, "status": status, "verdict": verdict,
                   "landed_elsewhere": landed_elsewhere})
    return (verdict, target, status)


def _ua_string(name):
    """The probe identifies itself as both the crawler and this audit tool."""
    return "{} ({}; comparison probe)".format(name, USER_AGENT)


def _name_by_name_probe_step(limit=5):
    """The measurement this skill cannot make, written so the owner can.

    Every probe this skill sends leaves through one HTTP client, so it can
    never separate a rule that reads the crawler name from one that scores the
    client. A loop run from the reader's own machine changes both, and prints
    one status line per crawler - which is the list an allow rule is written
    from, and the one fact a fix depends on.

    It is the first step wherever the cause is undetermined, because the
    alternative was handing over every answer crawler this file knows. A
    charity's owner got thirteen names to allow, several of which that edge was
    already answering HTTP 200, and none of the three it was actually refusing.
    """
    return (
        "Print one status per crawler from a machine outside your network, before changing "
        "any rule: `for ua in {}; do curl -s -o /dev/null -w \"$ua %{{http_code}}\\n\" "
        "-A \"$ua\" https://your-site/; done`. Allow-list only the names that come back "
        "non-200. A name already answered 200 needs no rule, and a run where every name "
        "including your browser's is refused means the rule is not reading names at "
        "all.".format(" ".join(ANSWER_CRAWLERS[:limit])))


def _refusal_cause_note(verdict, probed=(), served=(), named=None, both_clients=False):
    """One sentence saying what was, and was not, established about the cause.

    The count comes from the probe. It was the word "two", written into both
    branches - and `_probe_a_refusal` records only the agents whose request
    returned at all, so a homepage that refuses, one candidate that also
    refuses and one whose request times out leaves a single measurement behind
    a sentence claiming two. A number nobody measured is a fabrication however
    small, which is the rule this skill applies to everything else it prints.
    """
    count_phrase = plural(len(probed) or 1, "AI answer crawler's user agent",
                          "AI answer crawlers' user agents")
    if verdict == UA_NAMES_DIFFER and named is not None:
        # The one arrangement that settles it: a client different from the one
        # refused above, held fixed while the name moved. Everything this
        # sentence claims is a status line that was read.
        return (" A second HTTP client was then sent to that URL under {} crawler names in "
                "turn. It was refused under {} and answered with the page under {}, so that "
                "edge is reading the crawler name.{}".format(
                    len(named.seen),
                    describe_agents(tuple(named.refused), limit=4),
                    describe_agents(tuple(named.served), limit=4),
                    " It was answered under this audit's own name too, so the refusal above "
                    "is a separate rule about the HTTP client this audit sends with."
                    if named.control == 200 else ""))
    if verdict == UA_CLIENT_SCORED:
        return (" A second HTTP client was then sent to that URL under {} crawler names and "
                "under this audit's own name, and was answered HTTP 200 every time. No name "
                "is being read here: what this edge refuses is the HTTP client, on its TLS "
                "and header fingerprint or on the address it came from, so no crawler allow "
                "rule would lift it.".format(
                    len(named.seen) if named is not None else "several"))
    if verdict == UA_AUDIT_ONLY and both_clients:
        # Reached only where the crawler names were served through a second
        # HTTP client and this audit's own name was refused through both. The
        # client changed and the outcome did not, so the caveat below - which
        # exists because one client cannot separate a name rule from a client
        # rule - does not apply and must not be printed.
        return (" {} were served this URL with HTTP 200 when a second HTTP client asked "
                "again under those names, while this audit's own name was refused through "
                "both clients this run has. The client changed and the outcome did not, so "
                "it is the user-agent string this edge reads, and it is deciding in the AI "
                "crawlers' favour.".format(
                    describe_agents(tuple(served), limit=3) if served
                    else count_phrase[0].upper() + count_phrase[1:]))
    if verdict == UA_AUDIT_ONLY:
        # Two corrections in one sentence. The requests were separate, seconds
        # apart, not one request - and what they establish is that the crawler
        # names were served and this client was not, which two different rules
        # produce. Asserting "the edge is reading the user agent" sent a
        # hardware manufacturer to a user-agent allow rule that would not lift
        # a block keyed on the HTTP client's fingerprint.
        return (" {} were served this URL with HTTP 200 when this same client asked again "
                "under those names, after the crawl's own request to it was refused. Two "
                "rules fit that pair and one HTTP client cannot separate them: an edge "
                "reading the user-agent string, or "
                "one scoring this client's TLS and header fingerprint while exempting "
                "recognised crawler names whatever client sends them. What is established is "
                "that the AI crawlers were served here and this audit was not.".format(
                    describe_agents(tuple(served), limit=3) if served
                    else count_phrase[0].upper() + count_phrase[1:]))
    if verdict == UA_CLIENT_REFUSED:
        # Not "so the name is not what is read". Both arms of that comparison
        # left through one HTTP client, and an edge scoring that client's TLS
        # and header fingerprint refuses every name alike. A charity's edge did
        # exactly that: through curl it answered three crawler names 403 and
        # four others 200, and this check called the name irrelevant.
        return (" The same request was refused under {} as well, but every one of those "
                "requests came from this audit's one HTTP client, so which crawler names "
                "this edge refuses was not established here. An edge scoring the client's "
                "TLS and header fingerprint refuses all of them alike, and so does an edge "
                "refusing all of them by name.".format(count_phrase))
    if verdict == UA_KEYED:
        # The one direction a single HTTP client can establish: the client was
        # held constant across both requests and only the name changed.
        return (" {} received a different response from this audit's own request, sent from "
                "the same client seconds earlier, so the name is what that rule "
                "reads.".format(count_phrase[0].upper() + count_phrase[1:]))
    return (" Which rule causes it was not established: the comparison requests that would "
            "separate a user-agent rule from a network-level one could not be made.")


def _refusal_summary(verdict, named=None):
    if verdict == UA_NAMES_DIFFER and named is not None:
        return "Allow {} through your CDN or WAF bot rules.".format(
            ", ".join("`{}`".format(n) for n in named.refused))
    if verdict == UA_CLIENT_SCORED:
        return ("No crawler name is refused here; this audit's own HTTP client is. Ask your "
                "CDN why, then re-run this audit from a client it allows.")
    if verdict == UA_AUDIT_ONLY:
        return ("No change is needed for the AI crawlers; they are already served. Re-run this "
                "audit from an allowed client if you want the rest of the report.")
    if verdict == UA_CLIENT_REFUSED:
        return ("Measure which crawler names this edge refuses, from a client it does not "
                "already refuse, before changing a rule.")
    if verdict == UA_KEYED:
        return "Allow the AI answer crawlers to fetch the homepage."
    return "Find out why the edge refuses this request before changing any bot rule."


def _partial_read_note(pages, ok_pages):
    """How much of the site this run actually read, counted.

    The step here used to read "treat every other finding in this report as
    drawn from a partial read" whatever else the report contained. A hardware
    manufacturer's report has one finding and no content findings at all, so
    the sentence pointed at nothing and told the owner to discount findings
    that were never made. Counts come from the crawl record and describe this
    run rather than a report this skill cannot see.
    """
    fetched = [p for p in pages or [] if p.get("status") is not None]
    read = len(ok_pages or [])
    if not read:
        return ("No page on this site was read: {} URL(s) were requested and none returned a "
                "page, so nothing anywhere in this report describes the site's "
                "content.".format(len(fetched)))
    return ("This run read {} of the {} URL(s) it requested. Anything else in this report "
            "about page content was drawn from what those returned.".format(
                read, len(fetched)))


def _refusal_steps(verdict, read_note="", named=None):
    """What to do, given only what was actually observed.

    Five different first moves. Sending an owner to the crawler allow-list
    when the block is at the network level costs them the afternoon and leaves
    the site exactly as invisible as it was - and sending them to the network
    settings when two named crawlers are refused at the edge does the same in
    the other direction.
    """
    if verdict == UA_NAMES_DIFFER and named is not None:
        return [step for step in _named_block_steps(named) + [read_note] if step]
    if verdict == UA_CLIENT_SCORED:
        return [step for step in [
            "Nothing here needs a crawler allow rule. A second HTTP client sent from this "
            "same machine was answered HTTP 200 at that URL under every crawler name tried "
            "and under this audit's own name, so the name is not what is being read.",
            read_note,
            "Ask your CDN or WAF for the reason logged against this audit's refused "
            "requests. A bot score computed from the client's TLS and header fingerprint, "
            "an IP-reputation rule and a rate limit all produce this and none of them is "
            "visible from outside.",
            "Re-run this audit from a different network once you know. Its findings below "
            "describe only what a refused client could see.",
        ] if step]
    if verdict == UA_AUDIT_ONLY:
        return [step for step in [
            "Nothing here needs fixing for AI answer engines. The comparison requests were "
            "answered with the page when they named an AI crawler and refused when they came "
            "from this audit, which is the configuration most owners want.",
            read_note,
            # Not "allow the audit user agent". That fixes one of the two rules
            # that produce this result, and on the site this was written for it
            # was the other one: the same user-agent string was answered 200
            # from curl and from urllib and 403 from the HTTP client this audit
            # uses, so nothing about the name was being read.
            "If you want a full report, ask your CDN or WAF for the reason logged against "
            "this audit's refused requests. A user-agent rule is lifted by allowing `{}`; a "
            "bot-score, client-fingerprint or IP-reputation rule is not lifted by any "
            "user-agent rule, and the audit then has to run from an allowed client or "
            "network.".format(USER_AGENT),
            "To see which of the two it is, send the same URL twice from one machine, once "
            "as `curl -A '{}' https://your-site/` and once from a Python `requests` script "
            "sending that identical string. Two different answers means the client is being "
            "scored, not the name.".format(USER_AGENT),
        ] if step]
    if verdict == UA_KEYED:
        return [
            "Open your CDN or WAF bot rules. A blanket block on unrecognised user agents "
            "also blocks every AI answer crawler.",
            "Allow-list the AI answer crawlers by user agent, keeping rate limits in place: "
            "{}.".format(describe_agents(ANSWER_CRAWLERS, limit=4)),
            "Verify by requesting the homepage with each crawler's user-agent string and "
            "confirming a 200 with real HTML rather than a challenge page.",
        ]
    if verdict == UA_CLIENT_REFUSED:
        return [
            # First, because it is the measurement this run could not make and
            # everything below it is guesswork until it exists. The old first
            # step told the owner to go looking for a blanket challenge, on the
            # strength of a comparison that had proved nothing.
            _name_by_name_probe_step(),
            "Read that output before touching a rule. Some names answered and others "
            "refused means the edge is reading the name, and the refused ones are your "
            "allow list. Every name refused, your browser's included, means it is not "
            "reading names and no allow rule will help.",
            "Ask your CDN for the block reason recorded against this audit's requests. IP "
            "reputation, autonomous-system rules, TLS fingerprinting, a blanket challenge on "
            "clients that do not run JavaScript, and rate limits all produce this same "
            "response and none of them is visible from outside.",
            "Re-test from a different network before changing anything. This audit ran from "
            "one address, and a rule about that address looks identical to a rule about "
            "crawlers.",
        ]
    return [
        "Request the homepage twice from outside your network, once with your browser's "
        "user agent and once with an AI crawler's, and compare the two responses. That one "
        "comparison decides everything below it.",
        "If only the crawler is refused, allow-list the AI answer crawlers by user agent in "
        "your CDN or WAF, keeping rate limits in place.",
        "If both are refused, the rule is not about the user agent: ask your CDN for the "
        "block reason recorded against the request.",
        "Read the CDN log for the failing request rather than the origin log. A request the "
        "edge refuses never reaches the origin, so the origin log will show nothing.",
    ]


def _not_measured_reason(snapshot, measured, because):
    """Why a page-level check has nothing to say, in the site's own terms.

    A check registered with `result.check(...)` that then neither fires nor
    declines is rendered as a pass, and on a store answering HTTP 402 at every
    address this file printed `homepage-reachable` and `non-200-rate` under
    "These ran against this site and were clean - they are not omissions". The
    crawl fetched nothing. Two checks certified a site nobody had read.

    `site_not_serving` is set by the crawl when every address it reached
    answered the same status and that status speaks for the service rather
    than for the address - 402, 410, 451, 503. Where it is set it is the true
    reason, and it is a different sentence from both "clean" and "the crawler
    was refused", which is what 401, 403 and 429 mean and which the bot-manager
    checks own.
    """
    not_serving = (snapshot or {}).get("site_not_serving") or {}
    if not_serving.get("reason"):
        because = "the site is not serving. {}".format(not_serving["reason"])
    return "{} was not measured, because {}".format(measured, because)


def _homepage_record(pages, home_url):
    """The crawl's record for the site's front page, or None.

    Three ways to recognise it, and the first was the only one asked. A
    homepage that redirects, or that hands the visitor on with a meta refresh,
    is recorded under the address that answered rather than under the origin's
    own - so `p["url"] == home_url` matched nothing, the homepage branch
    neither fired nor declined, and the report printed `homepage-reachable`
    as a check that ran and was clean.
    """
    target = (home_url or "").rstrip("/")
    for page in pages or []:
        if (page.get("url") or "").rstrip("/") == target:
            return page
    for page in pages or []:
        if (page.get("final_url") or "").rstrip("/") == target:
            return page
    return next((p for p in pages or [] if p.get("page_type") == "home"), None)


def pages_including_noindexed(snapshot, ok_pages):
    """`pages_of` plus the pages the crawl set aside for carrying a noindex.

    The crawl marks a page that tells every crawler to discard it with
    `not_gradable`, `not_gradable_reason: "noindex"` and a `skipped` sentence,
    which is how `audit_common.pages_of` - and so every check in every other
    skill - stops counting it. A town library's two iCalendar export
    endpoints answered 200 with `Content-Type: text/html`, an empty body and
    `X-Robots-Tag: noindex, nofollow`, were typed `category`, and five
    findings fired on them: thin body text, no onward navigation, no `lang`,
    no viewport, few of the site's own links. Six of the ten false positives
    counted across six sites came from those two URLs.

    This skill is the exception, and it is the reason the records are kept in
    the snapshot rather than dropped: a `noindex` on a page the owner wanted
    found is itself the finding, and this is the check that raises it. Reading
    only `pages_of` here would have hidden the directive along with the pages
    it was on.
    """
    seen = {id(page) for page in ok_pages}
    out = list(ok_pages)
    for page in snapshot.get("pages") or []:
        if id(page) in seen or not page.get("not_gradable"):
            continue
        # On the directive, not on `not_gradable_reason`. The two library URLs
        # carry `noindex` and were set aside for delivering an empty body, so
        # the reason recorded is `empty-body` and the directive is still there
        # to report. A set-aside page with no directive on it - a body that
        # would not decompress, a redirect off the origin - has nothing this
        # check is looking for and stays out.
        if "noindex" in _robots_directives(page):
            out.append(page)
    return sorted(out, key=lambda p: p.get("url") or "")


def _robots_directives(page):
    """Every crawler directive this page carries, lowercased, in one string.

    Three places a `noindex` can live, and a page needs only one of them: the
    generic robots meta tag, a per-crawler meta tag such as
    `<meta name="googlebot">`, or the `X-Robots-Tag` response header. This
    joined string was being rebuilt inline in two places that had to stay in
    step, and reading `declares_noindex` instead is not enough here, because
    what separates a deliberate `noindex, follow` from a mistake is the second
    word and that field is a boolean.
    """
    by_agent = page.get("meta_robots_by_agent") or {}
    return " ".join([
        page.get("meta_robots", "") or "",
        " ".join(str(v) for v in by_agent.values()),
        page.get("x_robots_tag", "") or "",
    ]).lower()


# Query keys that say "this address is one slice of a listing". A slice is a
# near-duplicate of the listing it came from, and keeping one out of an index
# is the ordinary arrangement rather than a defect.
_PAGINATION_QUERY_KEYS = frozenset({"page", "paged", "pagenum", "page_num", "start",
                                    "offset", "from", "sort", "orderby", "order_by",
                                    "filter", "filter_by", "facet"})


def _deliberate_noindex_reason(page):
    """Why this page's `noindex` reads as the site's own decision, or "".

    This check used to give advice that makes the site worse: it reported
    `noindex` on WordPress **tag archives**,
    which carry `noindex, follow` - the default of both major SEO plugins and
    a deliberate choice on millions of sites - under the summary "Remove
    `noindex` from pages you want found". Following that publishes a set of
    thin near-duplicate pages that compete with the articles they list.

    A `noindex` is a defect only on a page the site plainly wants found: an
    article, a product, a service page, the homepage. Everywhere else it is
    the standard way to keep a near-duplicate or a per-visitor address out of
    an index while still letting a crawler follow the links through it. So
    this asks, of the address and of the directive, whether the arrangement is
    one sites make on purpose - and where it is, the check says so rather than
    prescribing a change.

    Every test below reads something the page itself carries. Nothing here is
    a guess about intent.
    """
    url = page.get("final_url") or page.get("url") or ""
    directives = _robots_directives(page)
    path = urlparse(url).path or "/"
    query = urlparse(url).query or ""

    # A basket, a checkout, an account or a wishlist holds something different
    # for every visitor. `_per_visitor_kind` is the same vocabulary the sitemap
    # check names to the reader, and a `noindex` on one of these addresses is
    # the site doing the only correct thing available to it.
    kind = _per_visitor_kind(url)
    if kind:
        return ("it is {} address, which holds something different for every visitor and "
                "which no crawler can use".format(kind))

    # The shape seen in the wild. `/tag/<tag>`, `/tags/<tag>`, `/tagged/<tag>`,
    # `/category/<slug>`, `/author/<name>`, `/archive/<slug>` and `/<x>/page/2`
    # are one address scheme saying "everything filed under this heading", and
    # `is_faceted_listing` is the marketplace's single reading of it.
    if is_faceted_listing(url):
        return ("its address is a tag, category, author or paginated listing - a near-"
                "duplicate of the pages it lists, which is the shape both major SEO "
                "plugins ship `noindex, follow` on by default")
    if DATED_ARCHIVE_RE.search(path.rstrip("/") or "/"):
        return ("its address is a dated archive listing, which repeats the posts it "
                "indexes rather than holding anything of its own")
    keys = {part.split("=")[0].strip().lower()
            for part in query.split("&") if part.strip()}
    if keys & _PAGINATION_QUERY_KEYS:
        return ("its address carries a paging, sorting or filtering parameter ({}), so it "
                "is one slice of a listing rather than a page of its own".format(
                    ", ".join(sorted(keys & _PAGINATION_QUERY_KEYS))))

    # The directive's own second word, on a page the crawl typed as a listing.
    # `noindex, follow` says exactly the thing a listing owner means: do not
    # index this list, do follow the links through it. On an article or a
    # product the same pair is still a mistake, which is why this arm asks the
    # page type as well as the directive.
    if page.get("page_type") == "category" and "follow" in re.sub(
            r"nofollow", "", directives):
        return ("it is a listing page and the directive is `noindex, follow`, which asks a "
                "crawler to skip the list and follow the links through it - the standard "
                "arrangement for a listing rather than a mistake")

    # A page the crawl set aside for a reason other than the directive
    # delivered nothing to read. A town library's two iCalendar export
    # endpoints answer 200 with `Content-Type: text/html`, an empty body and
    # `X-Robots-Tag: noindex, nofollow`; telling that library to publish its
    # calendar export to search engines is work with no reader at the end of
    # it.
    reason = page.get("not_gradable_reason") or ""
    if page.get("not_gradable") and reason and reason != "noindex":
        return ("the crawl set this address aside as something other than a page ({}), so "
                "there is nothing on it an assistant could quote".format(reason))
    return ""


# The host a TLS failure names, as the HTTP library writes it.
_TLS_POOL_HOST_RE = re.compile(r"HTTPSConnectionPool\(host='([^']+)'", re.I)


def _tls_subject(url, error):
    """Who "answered over HTTPS", in words that match the scheme used.

    A national library was audited at `http://`, the server redirected the
    request to HTTPS, and the handshake there failed. The sentence read
    "http://<host>/ answered over HTTPS", which is a contradiction a reader
    stops at: an `http://` address does not answer over HTTPS. The library's
    own error names the HTTPS host the redirect reached, so the sentence can
    say what happened in order.
    """
    if urlparse(url).scheme != "http":
        return url
    match = _TLS_POOL_HOST_RE.search(str(error or ""))
    host = match.group(1) if match else urlparse(url).hostname
    return "{} redirects to HTTPS, and https://{}/".format(url, host)


def _check_status_and_indexability(result, snapshot, pages, ok_pages, fetcher=None,
                                   ua_verdict=UA_UNTESTED, robots=None, recheck=None):
    result.check("homepage-reachable")
    result.check("non-200-rate")
    result.check("redirect-chain-length")
    result.check("noindex-on-content-pages")
    result.check("canonical-targets")

    _report_response_time(result, pages)

    home_url = snapshot["origin"].rstrip("/") + "/"
    home = _homepage_record(pages, home_url)
    served = result.signals.get("crawlers_served_while_audit_refused") or ()
    named = _named_probe_from_signals(result)
    if home is None:
        result.skip("homepage-reachable", _not_measured_reason(
            snapshot, "whether the homepage returns HTTP 200",
            "this crawl holds no record of the front page: it fetched {} URL(s) and none "
            "of them was the site's own root".format(len(pages))))
    elif home.get("status") is None and incomplete_certificate_chain(home.get("error")):
        # The host answered; its certificate chain stops one short of an
        # authority. The general branch below prescribed reading the CDN log
        # for several days of developer time, on a national library whose fix
        # is one file on the web server.
        #
        # Registered again, because `_report_response_time` registered its own
        # check after the five above and a finding is filed under whichever
        # was registered last: this one was listed under `origin-response-time`.
        result.check("homepage-reachable")
        result.add(
            id_hint="homepage-not-reachable",
            title="The homepage's HTTPS certificate is sent without its intermediate "
                  "certificate",
            severity="critical", confidence="high",
            evidence="{} {}. The TLS library's own words were \"{}\". The server answered, so "
                     "this is not an outage: browsers that already hold the intermediate "
                     "load the page, and crawlers, command-line clients and many phones "
                     "do not.".format(
                         _tls_subject(home_url, home.get("error")),
                         explain_fetch_error(home.get("error")),
                         next((words for words in ("unable to get local issuer certificate",
                                                   "unable to verify the first certificate")
                               if words in str(home.get("error") or "").lower()),
                              "certificate verify failed")),
            mechanism="A", root_cause="non-200",
            summary="Install the intermediate certificate in the server's certificate chain.",
            how_to_fix=[
                "Download the intermediate certificate your certificate authority issued "
                "with this certificate (the authority's site lists it under the certificate "
                "product's name).",
                "Append it after the site's own certificate in the chain file the server "
                "reads - `ssl_certificate` in nginx pointing at a full-chain file, "
                "`SSLCertificateFile` with the full chain in Apache 2.4.8 and later, or the "
                "certificate bundle field in a hosting panel - and reload the server.",
                "Confirm with `openssl s_client -connect {}:443 -servername {}` - the output "
                "should end \"Verify return code: 0 (ok)\" rather than \"unable to verify the "
                "first certificate\".".format(
                    urlparse(home_url).hostname, urlparse(home_url).hostname),
            ],
            effort="low", owner="developer",
            rationale="A crawler that cannot complete the TLS handshake reads nothing on the "
                      "site - no page, no robots.txt, no sitemap - so every other question "
                      "about discoverability waits on this one.",
            affected_pages=[home_url],
        )
    elif home.get("status") != 200:
        status = home.get("status")
        # A 403 or 429 to an identified, rate-limited, robots-respecting crawler
        # is a bot rule, not an outage, and the fix is completely different.
        looks_like_bot_block = status in (401, 403, 405, 406, 429)
        # The edge refused this audit and answered the named AI crawlers. That
        # is not a defect in the site: it is the limit of this run, and it has
        # to be reported as one. Ranked critical and first in "Start here", it
        # told a hardware manufacturer to fix the configuration they already have.
        #
        # UA_CLIENT_SCORED is the same situation reached by a different route:
        # a second HTTP client was answered under every crawler name and under
        # this audit's own, so the refusal above is about this client and about
        # nothing a crawler sends.
        audit_only = bool(looks_like_bot_block
                          and ((ua_verdict == UA_AUDIT_ONLY and served)
                               or ua_verdict == UA_CLIENT_SCORED))
        result.add(
            id_hint="audit-refused-while-crawlers-served" if audit_only
                    else "homepage-not-reachable",
            title="This audit could not read the site; the AI crawlers it speaks for can"
                  if audit_only
                  else "The homepage refuses this crawler" if looks_like_bot_block
                  else "The homepage does not return HTTP 200",
            severity="info" if audit_only else "critical", confidence="high",
            evidence="{} {}.{}{}".format(
                home_url,
                "returned HTTP {}".format(status) if status
                else explain_fetch_error(home.get("error")),
                # The status says this is a rule and not an outage; that much
                # is readable from the response. What the rule keys on is not,
                # and the sentence used to name it anyway.
                " The request identified itself as an audit crawler, respected robots.txt and "
                "was rate limited, so this is a rule about who is asking rather than an "
                "outage.{}".format(_refusal_cause_note(
                    ua_verdict, result.signals.get("refusal_probe_agents") or (), served,
                    named, bool(result.signals.get("served_by_second_client"))))
                if looks_like_bot_block else "",
                "" if status else slow_origin_note(pages)),
            mechanism="A", root_cause="non-200",
            summary=_refusal_summary(ua_verdict, named) if looks_like_bot_block
                    else "Restore a 200 response on the homepage.",
            how_to_fix=_refusal_steps(ua_verdict, _partial_read_note(pages, ok_pages), named)
                       if looks_like_bot_block else [
                "Request the homepage and read the server or CDN log for the failing request.",
                "If the homepage has moved, return a 301 to the new location rather than an error.",
                "Check whether a CDN rule or origin health check is failing.",
            ],
            effort="low" if audit_only else "high", owner="developer",
            rationale="This audit's own request is refused while requests naming {} are "
                      "answered with the page. Nothing an AI answer engine does was blocked "
                      "here, so there is no discoverability defect to fix - only a report "
                      "built on what one refused client could see.".format(
                          describe_agents(tuple(served), limit=3) if served else
                          "the AI answer crawlers, sent from a second HTTP client,")
                      if audit_only else
                      "The homepage is the entry point almost every crawler and "
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
    if not fetched:
        # Every arm below this is `elif fetched`, so an empty list fell off the
        # end of the chain and `non-200-rate` was rendered as a pass on a crawl
        # that fetched nothing at all. A rate with no denominator is not a
        # clean rate.
        result.skip("non-200-rate", _not_measured_reason(
            snapshot, "the share of crawled URLs that do not return HTTP 200",
            "no URL answered during this crawl, so the rate has no denominator"))
    elif len(fetched) < 3 and bad:
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
            # Same two questions the challenge check now asks before it fires.
            # A rate the crawl produced against an edge that serves the AI
            # crawlers, or against a throttle the crawl's own volume tripped,
            # is a fact about this run and not about the site.
            if mostly_refused and ua_verdict == UA_CLIENT_SCORED:
                result.skip(
                    "non-200-rate",
                    "{} of {} fetched URL(s) were refused during the crawl, but a second HTTP "
                    "client sent from this same machine was answered HTTP 200 at {} under "
                    "every crawler name it tried and under this audit's own name. The rate "
                    "measures the reception of this audit's HTTP client rather than a defect "
                    "in the site".format(
                        len(refused), len(fetched),
                        result.signals.get("ua_comparison_url") or "the homepage"))
            elif mostly_refused and ua_verdict == UA_AUDIT_ONLY and served:
                result.skip(
                    "non-200-rate",
                    "{} of {} fetched URL(s) were refused during the crawl, but {} answered "
                    "{} with HTTP 200 on the comparison request. That edge served the AI "
                    "crawlers this audit speaks for and refused this audit, so the rate "
                    "measures this tool's reception rather than a defect in the site".format(
                        len(refused), len(fetched),
                        result.signals.get("ua_comparison_url") or "the homepage",
                        describe_agents(tuple(served), limit=3)))
            elif mostly_refused and recheck is not None and recheck[0] == REFUSAL_TRANSIENT:
                result.skip(
                    "non-200-rate",
                    "{} of {} fetched URL(s) were refused during the crawl, but {} answered "
                    "HTTP 200 when it was asked once more afterwards under this same user "
                    "agent. A refusal that lifts the moment the crawl stops is rate limiting "
                    "the crawl's own request volume tripped, not a bot-management rule, so "
                    "no rule is asserted here".format(
                        len(refused), len(fetched), recheck[1]))
            else:
                result.add(
                    id_hint="high-non-200-rate",
                    title="{}% of crawled pages refuse this crawler".format(rate)
                          if mostly_refused
                          else "{}% of crawled pages do not return HTTP 200".format(rate),
                    severity="high" if rate >= 25 else "medium", confidence="high",
                    evidence="{} of {} fetched URLs returned a non-200 status ({}).{} "
                             "Examples: {}.".format(
                        len(bad), len(fetched),
                        ", ".join("{}x{}".format(v, k) for k, v in sorted(counts.items())),
                        " {} of them are refusals rather than missing pages: the request "
                        "identified itself as an audit crawler, respected robots.txt and was "
                        "rate limited, so this is a bot-management rule and not an "
                        "outage.".format(len(refused))
                        if mostly_refused else "",
                        ", ".join(example_urls([p["url"] for p in bad]))),
                    mechanism="A", root_cause="bot-manager-block" if mostly_refused else "non-200",
                    summary="Allow identified crawlers to fetch the site, not just the "
                            "homepage."
                            if mostly_refused
                            else "Fix or redirect the URLs that are still linked but no "
                                 "longer resolve.",
                    how_to_fix=[
                        "Open your CDN or WAF bot-management rules. A blanket challenge on "
                        "unrecognised user agents also blocks every AI answer crawler."
                        if mostly_refused
                        else "Export the failing URLs and check each against the current site "
                             "structure.",
                        "Allow-list the AI answer crawlers by user agent, keeping rate limits "
                        "in place."
                        if mostly_refused
                        else "301 anything that moved; remove links to anything that is "
                             "genuinely gone.",
                        "Verify by requesting several of the listed URLs with each crawler's "
                        "user-agent string and confirming a 200 with real HTML rather than a "
                        "challenge page."
                        if mostly_refused
                        else "Return 410 rather than 404 for pages deliberately retired, so "
                             "crawlers stop re-requesting them.",
                    ],
                    effort="high" if mostly_refused else "medium", owner="developer",
                    rationale="A page a crawler cannot fetch cannot be read or cited, and at "
                              "this rate the site is largely invisible to the systems that "
                              "would quote it." if mostly_refused else
                              "Mechanism A: every failing URL is a page that cannot be read "
                              "or cited, and a high failure rate reduces how deeply crawlers "
                              "explore the site.",
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
    if not pages:
        # The same shape as the two above: with no page to read a chain off,
        # this neither fired nor declined and was printed as a check that ran
        # and was clean.
        result.skip("redirect-chain-length", _not_measured_reason(
            snapshot, "how many hops crawled URLs redirect through",
            "this crawl holds no fetched URL to read a redirect chain from"))
    elif long_chains:
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
    search_pages = []
    duplicates = []
    deliberate = []
    # Every page the crawl read, plus every page it set aside for the
    # directive - and no filter on page type at all.
    #
    # It used to skip anything outside `CONTENT_TYPES`. On a test fixture one
    # page shipped `<meta name="robots" content="noindex,
    # nofollow">`, was typed `other`, and was then named in a missing-meta-
    # description finding and counted in a missing-`og:title` one - graded by
    # the hygiene checks while this check, whose whole job is to report the
    # directive, said "no crawled content page carries a noindex directive".
    # One page, graded by one half of the audit and invisible to the other.
    #
    # A page type is a guess about what a page is for; the directive is a fact
    # the page states. The guess may not be allowed to hide the fact, so the
    # population here is the audit's own reading population and the decision
    # about what is worth reporting is made below, page by page, on what each
    # page carries.
    for page in pages_including_noindexed(snapshot, ok_pages):
        directives = _robots_directives(page)
        if "noindex" not in directives:
            continue
        # `noindex` on a page whose own title says it is missing is the site
        # doing the right thing. Reported as a defect, the fix - "remove
        # `noindex` where the page should be public" - would put a broken page
        # into the index.
        if looks_like_soft_404(page):
            soft_404s.append(page)
            continue
        # A search-result page exists because somebody typed a query. Keeping
        # it out of an index is what every search-engine guideline asks for,
        # and telling the owner to remove the `noindex` would publish five
        # URLs of somebody else's search history.
        if is_search_result_page(page.get("url") or ""):
            search_pages.append(page)
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
        # The real defect, and the one that made the difference between a
        # report and advice that makes the site worse. See
        # `_deliberate_noindex_reason`.
        reason = _deliberate_noindex_reason(page)
        if reason:
            deliberate.append((page, reason))
            continue
        noindexed.append(page)
    if deliberate:
        result.signal("noindexed_by_design",
                      {p["url"]: reason for p, reason in deliberate})
    if soft_404s:
        result.signal("noindexed_soft_404s", [p["url"] for p in soft_404s])
    if search_pages:
        result.signal("noindexed_search_pages", [p["url"] for p in search_pages])
    if noindexed:
        result.add(
            id_hint="noindex-on-content-pages",
            title="{} a noindex directive".format(
            plural(len(noindexed), "page the site publishes carries",
                   "pages the site publishes carry")),
            severity="high", confidence="high",
            evidence="Pages with `noindex` in a robots meta tag, a per-crawler meta tag such "
                     "as `<meta name=\"googlebot\">`, or the X-Robots-Tag header: {}.{}".format(
                         ", ".join(example_urls([p["url"] for p in noindexed])),
                         # What was excluded and why, in the same breath. A
                         # reader whose site also noindexes its tag archives
                         # has to be able to see that the audit read those
                         # too and left them alone deliberately.
                         " {} further address(es) on this site carry the same directive and "
                         "are not reported, because a `noindex` there is the standard "
                         "arrangement rather than a defect: {}.".format(
                             len(deliberate),
                             "; ".join("{} - {}".format(p["url"], reason)
                                       for p, reason in deliberate[:3]))
                         if deliberate else ""),
            mechanism="A", root_cause="noindex",
            summary="Remove `noindex` from the pages listed here, which are pages the site "
                    "publishes to be read.",
            how_to_fix=[
                "For each page listed, check every <meta name=\"robots\"> and "
                "<meta name=\"googlebot\"> tag and the X-Robots-Tag response header; the "
                "directive can come from any of them, and a page can carry more than one.",
                "Remove `noindex` from those pages, and from those pages only. Leave it on "
                "tag, category, author and dated archives, on `/page/2` and later, on "
                "search-result URLs and on filtered listings: `noindex, follow` there is "
                "what both major SEO plugins ship by default, and it keeps near-duplicate "
                "pages out of an index while a crawler still follows the links through them.",
                "If the directive is left over from a staging environment, check the CMS "
                "environment settings rather than editing each page.",
            ],
            effort="low", owner="developer",
            rationale="`noindex` tells a crawler to read the page and then discard "
                      "it. The content is fetched and then thrown away, so it can never be cited.",
            affected_pages=[p["url"] for p in noindexed],
        )
    elif not pages_including_noindexed(snapshot, ok_pages):
        # "No page carries noindex" is an absence claim about the site, and on
        # a crawl with no page in it the claim is about nothing. The reason a
        # reader needs is why there was nothing to read.
        result.skip("noindex-on-content-pages", _not_measured_reason(
            snapshot, "whether any page carries a noindex directive",
            "this crawl read no page to look at"))
    else:
        # Every arm of this sentence names addresses, because the page a
        # reader wants to check is the page that carried the directive. The
        # arms stack rather than overwrite: a site can noindex its tag
        # archives and its search results and its filtered listings, and a
        # decline naming only the last of the three reads as a report that
        # only looked at one.
        parts = []
        if deliberate:
            parts.append(
                "{} carry it and each is an address a site keeps out of an index on "
                "purpose ({})".format(
                    plural(len(deliberate), "address does", "addresses do"),
                    "; ".join("{} - {}".format(p["url"], reason)
                              for p, reason in deliberate[:3])))
        if duplicates:
            parts.append(
                "the page(s) carrying noindex at {} each name a different page as their "
                "canonical, so they are filtered or sorted duplicates the site is "
                "deliberately keeping out of an index".format(
                    ", ".join(example_urls([p["url"] for p in duplicates]))))
        if search_pages:
            parts.append(
                "the page(s) carrying noindex at {} are search-result addresses, which "
                "exist because somebody typed a query".format(
                    ", ".join(example_urls([p["url"] for p in search_pages]))))
        if soft_404s:
            parts.append(
                "the page(s) carrying noindex at {} say in their own titles that the page "
                "is missing".format(
                    ", ".join(example_urls([p["url"] for p in soft_404s]))))
        detail = ("no page this crawl read carries a noindex directive" if not parts else
                  "no page this site publishes to be read carries a noindex directive. {}. "
                  "All of those are correct and none is reported as a defect".format(
                      "; and ".join(parts)))
        result.skip("noindex-on-content-pages", detail)

    _check_canonicals(result, snapshot, ok_pages, fetcher, robots)
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


def _check_canonicals(result, snapshot, ok_pages, fetcher=None, robots=None):
    result.check("canonical-targets")
    origin_host = strip_www(urlparse(snapshot["origin"]).netloc.lower())
    known_status = {p["url"]: p.get("status") for p in snapshot.get("pages") or []}
    off_domain, broken = [], []

    with_canonical = [p for p in ok_pages if p.get("canonical")]
    if not with_canonical:
        # Not "nothing to see". Declaring no canonical is only harmless while
        # a URL nobody published fails to resolve, and the missing-page check
        # measures exactly that; when both hold it reports the pair as one
        # finding. This sentence points at that check rather than closing the
        # question, which is how a browser-compatibility database had both
        # halves filed under checks that did not apply.
        result.skip("canonical-targets",
                    "no crawled page declares a canonical URL, so there is no canonical target "
                    "to resolve. Whether declaring none costs anything depends on whether a "
                    "URL nobody published still resolves to a page, which the missing-page "
                    "check measures")
        return

    # A canonical usually points somewhere the crawl already went, in which case
    # its status is free. The damaging case is the one it does not: a canonical
    # aimed at a URL that no longer exists tells every consumer to ignore the
    # page it is on. That target is invisible to the crawl, so a small number of
    # HEAD requests are spent resolving the distinct unknown ones.
    unknown = []
    for page in with_canonical:
        canonical = page["canonical"]
        # A canonical a CMS filled in badly can be unparsable - `href="http://["`
        # raises `ValueError: Invalid IPv6 URL` - and one of those killed the
        # whole skill. `same_site` in the shared library already wraps this;
        # these three call sites did not.
        try:
            host = strip_www(urlparse(canonical).netloc.lower())
        except ValueError:
            continue
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
            # robots.txt applies to this probe as to every other fetch.
            if is_disallowed(robots or {}, USER_AGENT, path_of(target)):
                continue
            response = fetcher.try_get(target, method="HEAD")
            if response is not None:
                status = response.status_code
                if link_verdict(status) == "dead":
                    # HEAD support is per-URL, not per-site, so a negative
                    # costs one GET to confirm. Without it a live canonical
                    # target that 404s to HEAD was reported as broken.
                    status = confirm_dead(fetcher, target, status)
                known_status[target] = status

    for page in with_canonical:
        canonical = page["canonical"]
        try:
            host = strip_www(urlparse(canonical).netloc.lower())
        except ValueError:
            continue
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
        # "and resolve" is a claim about status codes, and on an origin that
        # answers 200 to an address nobody published it is a claim about
        # nothing: every canonical target resolves there, including the ones
        # pointing at pages that were deleted. The missing-page probe has
        # already measured that, so the sentence says which half still holds.
        if _origin_serves_a_page_for_any_path(result):
            result.skip("canonical-targets",
                        "all {} declared canonical URLs are on this domain. Whether they "
                        "resolve was not scored: this origin answers HTTP 200 with a page for "
                        "addresses that do not exist - {} is not published and resolves - so "
                        "a canonical aimed at a deleted page returns 200 here too".format(
                            len(with_canonical),
                            snapshot["origin"].rstrip("/") + NONSENSE_PATH))
            return
        result.skip("canonical-targets",
                    "all {} declared canonical URLs are on this domain and resolve".format(
                        len(with_canonical)))


# A path no site publishes. Fixed rather than random so two runs of this audit
# ask the same question and can be compared.
NONSENSE_PATH = "/brand-ai-readiness-audit-no-such-page"


def _check_soft_404_handling(result, snapshot, fetcher, robots=None, time_budget=None):
    """Does this site answer HTTP 200 for a path that does not exist?

    a browser-compatibility database serves its single-page-app shell with HTTP 200 for every
    unknown path, and the audit believed every status it was given. Two wrong
    findings came out of the same fact: `non-200-rate` recorded "every fetched
    URL returned HTTP 200" as a clean pass, and the shell served at
    /sitemap.xml was reported as "an XML sitemap is published but does not
    parse", with the fix "validate the file with any XML validator and fix the
    reported error" - for a file nobody ever wrote.

    One request settles it, and the answer is worth reporting on its own:
    every consumer that decides what to keep by status code files the shell as
    a real page, so a retired URL is indistinguishable from a live one.

    Two things this originally got wrong, both fixed here. It read the status
    after `requests` had followed a redirect, so a site that answers 302 and
    routes the request into a search page was reported as one serving a 200
    shell at the path asked for. And when the same site declared no canonical
    URL anywhere, that was filed separately as a check that did not apply,
    leaving the report holding both halves of one defect and stating neither:
    every guessed URL resolves, and no page names the address its content
    belongs at.
    """
    result.check("unknown-paths-return-200")
    url = snapshot["origin"].rstrip("/") + NONSENSE_PATH

    if fetcher is None:
        # See `no_network_reason`: the flag and an exhausted wall-clock budget
        # reach this skill as the same argument, and naming the flag on a run
        # that did not pass one tells the reader they made a choice they did
        # not make.
        result.skip("unknown-paths-return-200",
                    no_network_reason(
                        snapshot, time_budget,
                        flag_reason="extra network requests were disabled for this run, so "
                                    "no path was requested to see what a missing page returns",
                        exhausted_reason="no path was requested to see what a missing page "
                                         "returns"))
        return
    if is_disallowed(robots or {}, USER_AGENT, path_of(url)):
        result.skip("unknown-paths-return-200",
                    "robots.txt disallows the path this check would have requested, and this "
                    "audit does not fetch what robots.txt closes")
        return
    if not fetcher.budget_left or not fetcher.time_left:
        result.skip("unknown-paths-return-200",
                    "the request budget for this skill was spent on earlier checks before a "
                    "missing path could be requested")
        return

    response = fetcher.try_get(url)
    if response is None:
        result.skip("unknown-paths-return-200",
                    "the request for a path that does not exist failed for a reason unrelated "
                    "to the site's status codes")
        return

    status = response.status_code
    result.signal("unknown_path_status", status)
    # What the site answered at the URL that was asked for, before `requests`
    # went anywhere else. A browser-compatibility database answers 302 here and
    # sends the request to its search page; this check read the 200 at the end
    # of that hop and told the owner to find "the catch-all route that serves
    # the shell for unmatched paths", which that site does not have. The
    # redirect is the site distinguishing the URL, which is the exact thing the
    # finding said nothing did.
    hop = _redirect_hop(url, response)
    if hop is not None:
        result.signal("unknown_path_redirect",
                      {"status": hop[0], "landed": hop[1], "final_status": status})
    if status in REFUSED_STATUS:
        result.skip("unknown-paths-return-200",
                    "the request for a path that does not exist was refused by the site's edge "
                    "(HTTP {}), so what this site returns for a missing page could not be "
                    "determined".format(status))
        return
    # Whether a URL nobody ever published still resolves to a page, whichever
    # way the site arranges it. The two halves below are what decide it.
    resolves = status == 200
    # The other half of the same problem, counted from the crawl record and
    # measured by a different means than the probe above: pages this audit
    # read, and how many of them name their own address with a canonical tag.
    # A browser-compatibility database has both halves and the report filed
    # each separately - the soft-404 as a status-code defect, "no crawled page
    # declares a canonical URL" as a check that did not apply - so the thing
    # they add up to was never said. Every mistyped or shared URL on that site
    # is an indexable duplicate of a real page, with nothing naming which
    # address the content properly lives at.
    ok_pages = pages_of(snapshot)
    declaring = [p for p in ok_pages if p.get("canonical")]
    duplicates = resolves and bool(ok_pages) and not declaring
    result.signal("unknown_paths_resolve", resolves)
    result.signal("pages_declaring_canonical", len(declaring))

    # A redirect to the site's own error page is not the site telling an
    # unknown path from a real one. An Arabic publisher answers every missing
    # address with 302 to `/error.aspx`, which answers 200: the path is still
    # never given a 404, and the page it lands on is one real-looking URL every
    # missing address collapses into. The redirect a search page receives is a
    # different case and stays a pass; one whose landing page is named as an
    # error, or says in its own words that nothing is there, is a soft 404.
    landed_html = response_text(response) if hop is not None and status == 200 else ""
    landed_soup = make_soup(landed_html) if landed_html else None
    lands_on_an_error_page = bool(landed_soup is not None and (
        _ERROR_PAGE_PATH_RE.search(urlparse(hop[1]).path or "")
        or looks_like_soft_404({
            "title": landed_soup.title.get_text(strip=True) if landed_soup.title else "",
            "headings": {"h1": [landed_soup.h1.get_text(strip=True)]
                         if landed_soup.h1 else []},
            # The body as well: a museum's not-found notice has no heading,
            # and its only words are the notice itself.
            "text": landed_soup.get_text(" ", strip=True)})))
    result.signal("unknown_path_lands_on_an_error_page", lands_on_an_error_page)

    if hop is not None and not duplicates and not lands_on_an_error_page:
        result.skip(
            "unknown-paths-return-200",
            "a path that does not exist answered HTTP {} and sent the request to {}, which is "
            "not the URL that was asked for, so this site does tell an unknown path from a "
            "real one and no missing-page defect is reported.{}".format(
                hop[0], hop[1],
                " Following the redirect ends in HTTP 200, so a client that reads only the "
                "status at the end of the hop still records a 200 for a URL that does not "
                "exist; a client that reads the redirect does not. The pages the crawl read "
                "declare canonical URLs, so a duplicate address names the page it is a copy "
                "of." if status == 200 else
                " Following the redirect ends in HTTP {}.".format(status)))
        return
    if not resolves:
        result.skip("unknown-paths-return-200",
                    "a path that does not exist returned HTTP {}, so this site's missing pages "
                    "are distinguishable from its real ones".format(status))
        return

    html = response_text(response)
    soup = make_soup(html)
    title = soup.title.get_text(strip=True) if soup.title else ""
    h1 = soup.h1.get_text(strip=True) if soup.h1 else ""
    # `looks_like_soft_404` is the shared test for "this page's own words say
    # it is missing", already used by the noindex check twenty lines above.
    admits = looks_like_soft_404({"title": title, "headings": {"h1": [h1] if h1 else []},
                                  "text": soup.get_text(" ", strip=True)})
    result.signal("soft_404_shell", hop is None)
    result.signal("soft_404_admits_in_text", admits)

    # How the site answered the URL nobody published, in one clause, because
    # two arrangements produce the same duplicate: the shell served at that
    # path, or a redirect carrying the request into the app.
    answer = ("answered HTTP {} and sent the request to {}, which answered HTTP 200".format(
                  hop[0], hop[1])
              if hop is not None else
              "returned HTTP 200 with {:,} bytes of HTML at the path that was requested, "
              "with no redirect to any other URL".format(len(html)))
    reads_as_missing = (
        "The response says so in its own words - its title is \"{}\" - so a reader is told "
        "the page is missing while every machine is told it was found.".format(title or h1)
        if admits else
        "The response is the site's ordinary shell and says nothing about the page being "
        "missing." if hop is None else
        "The page it lands on is a real page of the site and says nothing about the address "
        "that was asked for.")

    if duplicates:
        # One finding rather than two mild halves filed apart. `canonical-broken`
        # rather than `non-200` because the damage is one page reachable under
        # unlimited addresses, and because returning 404s is only half the fix:
        # the pages that do exist still have to name themselves.
        id_hint = "unknown-paths-resolve-with-no-canonical"
        title_text = ("Every URL under this domain resolves to a page, and no page declares "
                      "its own address")
        root_cause = "canonical-broken"
        evidence = (
            "Two measurements, taken separately. {} does not exist and {}. {} And of the {} "
            "page(s) this crawl read, none carries a `<link rel=\"canonical\">`. Together "
            "those mean any mistyped, truncated or shared URL under this domain is served a "
            "real-looking page, and nothing on it tells a consumer which address that "
            "content properly lives at.".format(url, answer, reads_as_missing, len(ok_pages)))
        summary = ("Declare a self-referencing canonical URL on every page, and return 404 "
                   "for addresses the site has no page for.")
        steps = [
            "Add `<link rel=\"canonical\" href=\"...\">` to every page, holding that "
            "page's own absolute URL. That is the half that stops a duplicate address "
            "competing with the real one.",
            "Return HTTP 404 for paths the application has no route for, and 410 for pages "
            "deliberately retired, rather than {}.".format(
                "redirecting them into the app" if hop is not None
                else "serving the application shell"),
            "Check both with one request each: `curl -sI {}` should start with a 404, and "
            "`curl -s https://your-site/ | grep canonical` should print that page's own "
            "URL.".format(url),
        ]
        rationale = (
            "A consumer decides what to keep from the status code and which address to keep "
            "it under from the canonical tag. This site answers neither question: every "
            "guessed URL resolves, and no page claims an address, so one page can be indexed "
            "under unlimited addresses that compete with each other.")
        affected = [url] + [p["url"] for p in ok_pages[:4] if p.get("url")]
    else:
        id_hint = "unknown-paths-return-200"
        title_text = "Pages that do not exist return HTTP 200 instead of 404"
        root_cause = "non-200"
        evidence = (
            "{} does not exist and {}. {} Every status code in this report was read from "
            "responses like that one, so \"the URL returned 200\" is not evidence on this "
            "site that a page is there.".format(url, answer, reads_as_missing))
        summary = ("Return HTTP 404 for URLs that do not exist, rather than the application "
                   "shell.")
        steps = ([
            "Find the rule that redirects unknown paths to {} - usually the custom-error "
            "setting of the web server or the framework (`customErrors`, an `ErrorDocument` "
            "pointing at a full URL, or a catch-all route) - and have it serve the error page "
            "at the requested address with status 404 instead of redirecting.".format(hop[1]),
        ] if lands_on_an_error_page else [
            "Find what serves this shell for unmatched paths. It answered at the path asked "
            "for rather than redirecting elsewhere, so look for a catch-all route or a "
            "rewrite rule sending unmatched paths to index.html, or a framework fallback.",
        ]) + [
            "Make the server answer 404 for paths the application has no route for, and 410 "
            "for pages that were deliberately retired.",
            "If the routing decision can only be made in the browser, render the not-found "
            "view from the server for those paths and send it with a 404 status.",
            "Verify with `curl -sI {}` and confirm the first line is a 404.".format(url),
        ]
        rationale = (
            "A crawler decides what to keep and what to drop from the status code. A site "
            "that answers 200 to everything tells it every URL ever linked or guessed at is "
            "a live page, so deleted pages stay in the index and the real ones compete "
            "against them.")
        affected = [url]

    result.add(
        id_hint=id_hint,
        title=title_text,
        severity="medium", confidence="high",
        evidence=evidence,
        mechanism="A", root_cause=root_cause,
        summary=summary,
        how_to_fix=steps,
        effort="medium", owner="developer",
        rationale=rationale,
        affected_pages=affected,
    )


# Files a site publishes for AI agents to read rather than for browsers to
# render, and the only names this check will recognise.
#
# The vocabulary is the constraint here, not the place. `crawl.py` builds
# exactly one address, `origin + "/llms.txt"`, while this report's own
# recommendation tells the owner to publish `/llms.txt` and `/llms-full.txt` -
# and nothing in the audit has ever requested the second. A coffee shop on a
# hosted store platform serves `/agents.md` (HTTP 200, text/markdown) and
# `/.well-known/ucp` (HTTP 200, JSON) and announces both in the first six lines
# of its robots.txt; the audit read neither. A file announced under a name that
# is not in this tuple is still not found, however plainly robots.txt names it.
AGENT_FILE_NAMES = frozenset({
    "llms.txt", "llms-full.txt", "llms_full.txt", "agents.md", "agent.md",
    "agents.txt", "ai.txt", "ai-plugin.json", "mcp.json", "ucp", "ucp.json",
})

# Absolute URLs and rooted paths anywhere in the raw robots.txt, comments
# included. These files are announced in comments, and `parse_robots` strips
# every comment before a single directive is read, so the parsed record cannot
# carry them and the raw text can.
_PATH_TOKEN_RE = re.compile(r"https?://[^\s\"'<>()\[\],;]+|/[A-Za-z0-9._~/\-]+")

# Declared agent files this check will spend a request on. Two, because the
# ceiling this skill advertises is 24 requests and the checks above it already
# demand 19.
AGENT_FILE_PROBE_LIMIT = 2

# The conventional agent-file addresses this audit does not request, checked
# against what was actually asked for at run time and named in the decline so a
# silence is not read as a measurement. `/llms.txt` and `/llms-full.txt` are
# both requested by the crawl, so nothing here is unasked by default; anything
# added to this tuple that the crawl or the probe above does reach drops out.
NEVER_REQUESTED_AGENT_FILES = ("/agents.md", "/ai.txt")


def _declared_agent_files(robots, origin):
    """Agent-interface files this site announces in its own robots.txt."""
    raw = (robots or {}).get("raw") or ""
    origin = origin.rstrip("/")
    host = strip_www(urlparse(origin).hostname or "")
    found = []
    for token in _PATH_TOKEN_RE.findall(raw):
        token = token.rstrip(".,;:)\"'")
        if token.lower().startswith("http"):
            try:
                parts = urlparse(token)
            except ValueError:
                continue
            if strip_www(parts.hostname or "") != host:
                continue
            path, url = parts.path, token
        else:
            path, url = token, origin + token
        if path.rsplit("/", 1)[-1].lower() not in AGENT_FILE_NAMES:
            continue
        if url not in found:
            found.append(url)
    return found


def _agent_file_answer(url, status=None, is_file=False, not_requested=None):
    """One address and what it said, in a form a reader can re-request."""
    if not_requested:
        return "{} -> not requested: {}".format(url, not_requested)
    if status is None:
        return "{} -> no response".format(url)
    if status in REFUSED_STATUS:
        return "{} -> HTTP {}, refused by the site's edge rather than answered".format(
            url, status)
    if 300 <= status < 400:
        return "{} -> HTTP {}, a redirect to some other address rather than a file".format(
            url, status)
    if status == 200 and not is_file:
        return "{} -> HTTP 200 carrying an HTML page rather than a text file".format(url)
    if status == 200:
        return "{} -> HTTP 200, published".format(url)
    return "{} -> HTTP {}".format(url, status)


def _check_agent_files(result, snapshot, robots, fetcher, time_budget=None):
    """Which agent-readable files this site publishes, and which were asked for.

    Absence is never a finding here - this skill's SKILL.md says so, and a
    proactive recommendation carries it instead. But absence was not being
    declined either: `llms-txt-presence` was registered and then answered by
    nothing, so the report filed it under "checks that ran and found nothing
    wrong ... they are not omissions" on a site whose snapshot recorded
    `{"present": false, "status": 302}` and whose recommendations said "Publish
    /llms.txt and /llms-full.txt". A 302 into a search page is not a file, and
    an absence the same report bills as a fix is not nothing wrong.
    """
    result.check("llms-txt-presence")
    origin = snapshot["origin"].rstrip("/")
    llms_txt = snapshot.get("llms_txt") or {}
    status = llms_txt.get("status")

    llms_url = llms_txt.get("url") or (origin + "/llms.txt")
    # Three states, not two. `present` in the crawl record is False for an
    # address that answered 404 and equally for one nobody asked about, and the
    # report's recommendation to publish `/llms.txt` reads this signal - so it
    # printed "publish one" for a site whose request for the file came back
    # HTTP 429, and for a site whose own robots.txt closes that path to this
    # auditor. The decline below already distinguishes the two in prose; the
    # signal did not, and the recommendation reads the signal.
    #
    # `True` means a file answered, `False` that the address answered and there
    # is no file at it, `None` that this run never got an answer either way.
    unchecked = llms_txt.get("unchecked_reason")
    if not unchecked and status is None:
        unchecked = "{} got no response".format(llms_url)
    elif not unchecked and status in REFUSED_STATUS:
        unchecked = "the request for {} was refused by the site's edge (HTTP {})".format(
            llms_url, status)
    if llms_txt.get("present"):
        result.signal("llms_txt_present", True)
    elif unchecked:
        result.signal("llms_txt_present", None)
        result.signal("llms_txt_unchecked_reason", unchecked)
    else:
        result.signal("llms_txt_present", False)

    # Both addresses the crawl asks for, not just the first. The crawl has
    # requested `/llms-full.txt` since the report started recommending it, and
    # this check went on reading one record and printing "`/llms-full.txt` was
    # not requested by this audit ... so whether one is published is unknown" -
    # beside a snapshot holding the answer. A sentence saying we did not look,
    # written from a record of having looked, is worse than either.
    crawled = [(llms_url, llms_txt)]
    full = snapshot.get("llms_full_txt") or {}
    if full.get("url"):
        crawled.append((full["url"], full))

    asked_urls, answers, present = [], [], []
    for url, record in crawled:
        asked_urls.append(url)
        if record.get("unchecked_reason"):
            answers.append(_agent_file_answer(
                url, not_requested=record["unchecked_reason"]))
            continue
        answers.append(_agent_file_answer(
            url, record.get("status"), bool(record.get("present"))))
        if record.get("present"):
            present.append(url)

    declared = _declared_agent_files(robots, origin)
    result.signal("agent_files_declared_in_robots", declared)
    probed = []
    for url in [u for u in declared if u not in asked_urls][:AGENT_FILE_PROBE_LIMIT]:
        asked_urls.append(url)
        if is_disallowed(robots or {}, USER_AGENT, path_of(url)):
            answers.append(_agent_file_answer(
                url, not_requested="robots.txt disallows it for this auditor"))
            continue
        # Two different reasons, and printing one of them for the other is the
        # mistake this whole file exists to stop making: a run with networking
        # switched off has not spent a budget.
        if fetcher is None:
            answers.append(_agent_file_answer(
                url,
                not_requested=no_network_reason(
                    snapshot, time_budget,
                    flag_reason="extra network requests were disabled for this run",
                    exhausted_reason="this run had no wall clock left to ask")))
            continue
        if not fetcher.budget_left or not fetcher.time_left:
            answers.append(_agent_file_answer(
                url,
                not_requested="the request budget for this skill was spent on earlier checks"))
            continue
        response = fetcher.try_get(url, allow_redirects=False)
        if response is None:
            answers.append(_agent_file_answer(url))
            probed.append({"url": url, "status": None, "present": False})
            continue
        # A 200 is not proof of a file here either. This read 400 characters
        # looking for `<html` and nothing else, so a page whose first tag sits
        # behind a licence comment or a run of preload hints was published as
        # "this site publishes /agents.md". `response_is_an_html_page` reads
        # the `Content-Type` the server itself set, which settles it outright,
        # and sniffs further into the body when the server sets nothing useful.
        is_file = response.status_code == 200 and not response_is_an_html_page(response)
        answers.append(_agent_file_answer(url, response.status_code, is_file))
        probed.append({"url": url, "status": response.status_code, "present": is_file})
        if is_file:
            present.append(url)
    if probed:
        result.signal("agent_files_probed", probed)
    result.signal("agent_files_present", present)

    never_asked = [name for name in NEVER_REQUESTED_AGENT_FILES
                   if origin + name not in asked_urls]
    unasked_note = (
        " {} was not requested by this audit - the crawl builds two addresses, `/llms.txt` and "
        "`/llms-full.txt`, and robots.txt does not name any other - so whether one is "
        "published is unknown.".format(", ".join("`{}`".format(n) for n in never_asked))
        if never_asked else "")

    if present:
        conclusion = "this site publishes {}".format(", ".join(present))
    elif unchecked:
        # A refusal is not an answer, the rule this whole skill runs on. This
        # was recorded as "ran and found nothing wrong" on a site whose
        # /llms.txt request came back 429. The same sentence now covers every
        # way this run failed to get an answer - a refusal, no response at all,
        # or a path the site's own robots.txt closes to this auditor - because
        # the recommendation downstream treats all three the same way and must
        # not treat any of them as an absence.
        conclusion = ("{}, so whether an agent-readable file is published could not be "
                      "determined and it is reported as unchecked rather than "
                      "absent".format(unchecked))
    else:
        conclusion = ("no agent-readable file answered at any address this audit asked for. "
                      "That is not a defect and is not reported as one: it is what the "
                      "report's recommendation to publish `/llms.txt` rests on")
    result.skip("llms-txt-presence",
                "{}. Addresses asked for, and what each answered: {}.{}".format(
                    conclusion, "; ".join(answers), unasked_note))


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

    # `www.` is kept here, unlike everywhere else that compares hosts: a site
    # whose canonicals name both `example.test` and `www.example.test` has a
    # real split to fix, and folding them would hide the finding. A default
    # port is not a split. Grouping on the raw `netloc` reported one site as
    # serving canonicals on "two hostnames", `www.example.test` and
    # `www.example.test:443`, which are the same hostname written twice.
    hosts = Counter()
    for page in ok_pages:
        canonical = page.get("canonical")
        if canonical:
            parts = urlparse(canonical)
            host = strip_default_port(parts.netloc.lower(), parts.scheme.lower())
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
        return

    # No canonical split, because there may be no canonical at all - and a site
    # that answers on two hostnames with nothing saying which is real is the
    # case this check most needs to catch, not the case it has nothing to say
    # about.
    #
    # A project's report filed this check as not applicable ("no canonical URLs
    # were declared, so there is no host split to assess") on a site serving
    # identical pages on `www` and the bare domain. The duplication was then
    # reported by a different skill as a duplicate-`<title>` defect, with the
    # fix "duplicated titles usually come from a template that omits the page
    # variable" - a template change, on a site whose template is fine and whose
    # DNS and canonical configuration is not.
    #
    # The absent canonical is what makes it a finding rather than what makes
    # the check inapplicable.
    duplicated = _paths_served_by_more_than_one_host(ok_pages)
    if duplicated:
        seen_hosts = sorted({host for copies in duplicated.values() for host in copies})
        example = sorted(duplicated)[0]
        result.add(
            id_hint="site-answers-on-more-than-one-hostname",
            title="The same pages are served under {} hostnames, and nothing says which "
                  "is the real one".format(len(seen_hosts)),
            severity="medium", confidence="high",
            # No `checked=`: this is an observation, not an absence. The
            # hostnames were read off the addresses the pages finally answered
            # on, after redirects, and the copies were matched on carrying the
            # same title and the same amount of body text - which is what makes
            # them one document rather than two pages sharing a path.
            evidence="{} answered on {}: {}. Each copy carries the same title and the same "
                     "body text, and no canonical URL names one of them as the original.{}"
                     .format(plural(len(duplicated), "path", "paths"),
                             " and ".join(seen_hosts),
                             ", ".join("{} ({})".format(path, ", ".join(sorted(hosts)))
                                       for path, hosts in sorted(duplicated.items())[:3]),
                             "" if hosts else
                             " No page crawled on either hostname declares a canonical URL "
                             "at all."),
            mechanism="A", root_cause="host-inconsistency",
            summary="Serve the site on one hostname, redirect the other to it, and declare "
                    "a canonical URL on every page.",
            how_to_fix=[
                "Choose the hostname you want to be the real one - {} or {}.".format(
                    seen_hosts[0], seen_hosts[1]) if len(seen_hosts) > 1 else
                "Choose the hostname you want to be the real one.",
                "Redirect every address on the other hostname permanently (301) to the same "
                "path on the one you chose.",
                "Add `<link rel=\"canonical\">` to every page naming its address on the "
                "chosen hostname, so a crawler that reaches the other one still knows.",
                "Point internal links, the sitemap and any structured-data URLs at that "
                "hostname too.",
            ],
            effort="low", owner="developer",
            rationale="Two hostnames serving the same content look like two sources that "
                      "half-agree. Links, citations and reputation split between them, and "
                      "a consumer deciding which version to quote has nothing to decide on. "
                      "It also makes a page look duplicated to anything counting pages, "
                      "which hides the real problem behind an invented one.",
            affected_pages=sorted(
                url for copies in duplicated.values() for url in copies.values()),
        )
        return

    result.skip("canonical-host-consistency",
                "every canonical URL uses a single hostname" if hosts
                else "no canonical URL was declared anywhere, and no path answered on more "
                     "than one hostname during this crawl, so there is no host split to "
                     "resolve")


def _paths_served_by_more_than_one_host(ok_pages):
    """Paths this crawl reached on two hostnames with the same content.

    Returns `{path: {hostname: url}}` for the paths where more than one
    hostname answered with the same document, and where no canonical settles
    which of them is the original.

    Sameness is the title plus the amount of body text. Byte comparison is not
    available from a snapshot, and neither is needed: two addresses differing
    only in hostname, answering with the same title and the same amount of
    prose, are one document. A path that genuinely differs between hostnames -
    a country site on `de.` against the main one - differs in both.

    A canonical that names one hostname settles the question, so those paths
    are left out: the site has said which address is real, which is what this
    finding would ask it to do.
    """
    by_path = {}
    for page in ok_pages:
        landed = page.get("final_url") or page.get("url") or ""
        parts = urlparse(landed)
        host = (parts.hostname or "").lower()
        if not host:
            continue
        key = ((parts.path.rstrip("/") or "/"), parts.query)
        by_path.setdefault(key, []).append((host, page))

    out = {}
    for (path, query), entries in by_path.items():
        hosts = {host for host, _ in entries}
        if len(hosts) < 2:
            continue
        documents = {(p.get("title") or "", p.get("body_text_len") or 0) for _, p in entries}
        if len(documents) != 1:
            continue
        canonicals = {(p.get("canonical") or "").strip() for _, p in entries}
        canonicals.discard("")
        if len(canonicals) == 1:
            continue          # the site has already named one address as real
        out[path + (("?" + query) if query else "")] = {
            host: p.get("url") for host, p in entries}
    return out


# --------------------------------------------------------------------------
# Several language editions of one site, and nothing saying which is which
#
# A three-locale site - `/en`, `/id`, `/jp` - turned up where
# `grep -c hreflang` over the pages returned 0, and this audit's crawl had
# visited the `/jp/` and `/id/` pages. Nothing in the marketplace reported it.
#
# Without `hreflang` the three editions are near-duplicates of each other with
# no declared relationship: a consumer cannot tell which one answers which
# reader, and the editions compete for the same content. This is the same
# family of defect as a missing canonical - several addresses holding one
# thing, and nothing naming how they relate - which is why it carries
# `canonical-broken`.
#
# `audit_common.locale_editions` is the marketplace's single reading of "this
# site has one edition per language". It refuses to call a two-letter segment
# an edition on shape alone: either the page's own `lang` names that language,
# or at least two language-shaped prefixes appear across the crawl. Nothing
# here loosens that.
# --------------------------------------------------------------------------

# Editions this check will read a page from. Two, not all of them: two
# editions with no link between them is the whole finding, and a third request
# adds a third address to a sentence rather than changing what it says. The
# skill's request ceiling is what this is bounded against.
HREFLANG_PROBE_LIMIT = 2

# The declaration, wherever the page puts it: a `<link rel="alternate"
# hreflang="...">` in the head, an `hreflang` attribute on an anchor, or a
# `Link:` response header carrying the same. One attribute name covers all
# three, which is what makes this a reading of the document rather than of a
# parser's idea of it - and it is the same measurement that found the
# defect, `grep -c hreflang`.
_HREFLANG_RE = re.compile(r"hreflang\s*=", re.I)


def _edition_sample(pages, editions):
    """One page per language edition, shallowest first, at most the limit."""
    by_edition = {}
    for page in pages:
        code = editions.get(page.get("url"))
        if not code:
            continue
        current = by_edition.get(code)
        if current is None or (page.get("depth") or 0) < (current.get("depth") or 0):
            by_edition[code] = page
    return [by_edition[code] for code in sorted(by_edition)][:HREFLANG_PROBE_LIMIT]


# Readable text below which a crawled page holds nothing an answer could be
# drawn from. The page this was measured on held two words, its site's name
# twice; an interstitial country picker holds a hundred characters or more
# of choices and is a legitimate `x-default`.
X_DEFAULT_EMPTY_CHARS = 20
# Below this, a page that also sends the reader on by script or meta refresh
# is a redirect stub rather than a page of its own.
X_DEFAULT_STUB_CHARS = 200


def _page_key(url):
    """`page_identity` with a final slash dropped from any path but the root."""
    key = page_identity(url or "") or ""
    head, question, query = key.partition("?")
    if head.count("/") > 3 and head.endswith("/"):
        head = head.rstrip("/")
    return head + question + query


def _crawled_record(pages, url):
    """The crawl record for `url` by requested or answering address, or None."""
    key = _page_key(url)
    if not key:
        return None
    for page in pages or []:
        if key in (_page_key(page.get("url")), _page_key(page.get("final_url"))):
            return page
    return None


def _x_default_is_empty(result, snapshot, ok_pages, editions, declaring):
    """Report an `x-default` alternate that names a page with nothing on it.

    Returns True when it reported one. `x-default` is the edition served to
    a reader whose language the site does not publish. A one-page site's
    editions all name `/` as that edition, and `/` is a document holding its
    name twice and a script that picks a language from the browser and moves
    the reader on. A crawler that runs no script and follows `x-default`
    arrives at two words. The editions themselves were linked correctly, so
    the check had passed.

    Read from the crawl record only: the target has to be a page this crawl
    fetched, and the stub is established by its own recorded text and
    redirect, never guessed from the address.
    """
    targets = []
    for page in declaring:
        for alt in page.get("hreflang") or []:
            if alt.get("hreflang") == "x-default" and alt.get("url") not in targets:
                targets.append(alt.get("url"))
    for target in targets:
        record = _crawled_record(snapshot.get("pages") or [], target)
        if record is None or record.get("status") != 200:
            continue
        text_len = record.get("text_len") or 0
        redirect = record.get("script_redirect") or record.get("meta_refresh")
        if not (text_len < X_DEFAULT_EMPTY_CHARS
                or (redirect and text_len < X_DEFAULT_STUB_CHARS)):
            continue
        # Where the reader should land instead: the edition the script names
        # when it names one, else the edition written in the language the
        # stub itself declares.
        default = ""
        named = (record.get("script_redirect") or {}).get("url") or (
            (record.get("meta_refresh") or {}).get("url") if record.get("meta_refresh") else "")
        if named and _crawled_record(ok_pages, named) is not None:
            default = named
        if not default:
            language = primary_subtag(record.get("lang"))
            for page in sorted(ok_pages, key=lambda p: (p.get("depth") or 0, p.get("url") or "")):
                code = editions.get(page.get("url"))
                if language and code and code.split("-")[0].split("_")[0] == language:
                    default = page.get("final_url") or page.get("url")
                    break
        naming = sum(1 for page in declaring
                     if any(alt.get("hreflang") == "x-default" and alt.get("url") == target
                            for alt in page.get("hreflang") or []))
        how = ("redirects by script" if record.get("script_redirect")
               else "redirects by meta refresh" if record.get("meta_refresh")
               else "delivers no content")
        result.check("hreflang-between-language-editions")
        result.add(
            id_hint="hreflang-x-default-is-an-empty-page",
            title="The `x-default` language alternate points at a page that {}".format(how),
            severity="low", confidence="high",
            evidence="{} of the {} crawled edition pages declaring `hreflang` name {} as "
                     "`hreflang=\"x-default\"`, the edition for a reader whose language the "
                     "site does not publish. This crawl fetched it: it answered HTTP 200 with "
                     "{} of readable text{}. A crawler that runs no script and follows "
                     "`x-default` gets that page and nothing else.".format(
                         naming, len(declaring), target,
                         plural(text_len, "character", "characters"),
                         ", and sends the reader on by script rather than by an HTTP redirect"
                         if record.get("script_redirect") else
                         ", and sends the reader on with a meta refresh"
                         if record.get("meta_refresh") else ""),
            mechanism="A", root_cause="canonical-broken",
            summary="Point `x-default` at the real default edition{}.".format(
                " ({})".format(default) if default else ""),
            how_to_fix=[
                "Change one attribute in the shared `<link rel=\"alternate\">` block: "
                "`<link rel=\"alternate\" hreflang=\"x-default\" href=\"{}\">`. Every edition "
                "carries the same block, so this is one edit in the template.".format(
                    default or "<the URL of the edition you want served by default>"),
                "Leave the language redirect on {} in place for visitors if you want it; "
                "the change is only to which address the alternate names.".format(target),
                "Confirm by requesting any edition and checking its `x-default` alternate "
                "names a page with the full text on it.",
            ],
            effort="low", owner="developer",
            rationale="`x-default` is the page an engine hands to a reader it has no "
                      "edition for. Pointing it at an address whose content is chosen by "
                      "script hands that reader, and every machine that does not run the "
                      "script, an empty page in place of the site.",
            affected_pages=[target],
        )
        return True
    return False


def _check_hreflang(result, snapshot, ok_pages, fetcher, robots=None):
    """Does a site serving several language editions link them to each other?"""
    result.check("hreflang-between-language-editions")
    editions = locale_editions(ok_pages)
    codes = sorted(set(editions.values()))
    if len(codes) < 2:
        result.skip(
            "hreflang-between-language-editions",
            "this crawl found {}, so there is no second edition for an `hreflang` link to "
            "point at".format(
                "one language edition on this site (/{}/)".format(codes[0]) if codes
                else "no language edition on this site: no crawled address begins with a "
                     "path segment the site's own `lang` attributes confirm is a language"))
        return

    # The record first: the crawl keeps every page's own `<link rel="alternate"
    # hreflang>` tags. The paid probe below settles what the record cannot -
    # two GETs on a multi-locale site and none at all on the monolingual
    # sites that are most of the web.
    #
    # The edition pages themselves, not any page anywhere. A furniture
    # retailer with `/au`, `/sg` and `/us` storefronts passed this check with
    # "1 crawled page(s) declare `hreflang`" - the one page was the root
    # country picker, and not one regional page names another. A declaration
    # only one side makes is ignored by the engines that read it, so the
    # picker's tags link nothing to anything.
    edition_pages = [p for p in ok_pages if editions.get(p.get("url"))]
    declaring_editions = [p for p in edition_pages if p.get("hreflang")]
    one_way = [p for p in ok_pages if p.get("hreflang") and not editions.get(p.get("url"))]
    if declaring_editions:
        if not _x_default_is_empty(result, snapshot, ok_pages, editions, declaring_editions):
            result.skip("hreflang-between-language-editions",
                        "{} of the {} crawled edition page(s) declare `hreflang`, so the "
                        "editions are linked to each other".format(
                            len(declaring_editions), len(edition_pages)))
        return

    # The sitemap is the other place a site declares its editions, and the
    # crawl has already downloaded it. A retailer with one storefront per
    # country lists every edition of every address as `<xhtml:link
    # rel="alternate" hreflang=...>` in its sitemap and nowhere in its pages'
    # heads, and this finding told it at confident tier that none of its
    # editions links to the others - while admitting in its own evidence that
    # it had not read the sitemap. A declaration there is a declaration.
    read_sitemaps = [s for s in snapshot.get("sitemaps") or []
                     if s.get("status") == 200 and s.get("urls")]
    in_sitemap = [s for s in read_sitemaps if s.get("hreflang_url_count")]
    if in_sitemap:
        first = in_sitemap[0]
        result.skip(
            "hreflang-between-language-editions",
            "the editions declare `hreflang` in the sitemap: {} gives {} of its {} "
            "`<xhtml:link rel=\"alternate\">` editions ({})".format(
                first.get("url"), first.get("hreflang_url_count"),
                plural(len(first.get("urls") or []), "entry", "entries"),
                ", ".join(first.get("hreflang_codes") or [])))
        return
    # And where the snapshot cannot say, the finding may not either. A record
    # without the count was written before the crawl read alternates, and a
    # sitemap that answered and could not be parsed may hold them - neither
    # supports "none links to the others".
    unread = [s.get("url") for s in read_sitemaps if "hreflang_url_count" not in s]
    unread += [s.get("url") for s in snapshot.get("sitemaps") or []
               if s.get("status") == 200 and s.get("parse_error")]
    if unread:
        result.skip(
            "hreflang-between-language-editions",
            "this site serves {} language editions ({}), and the sitemap - the other place "
            "`hreflang` can be declared - could not be read for it here: {}".format(
                len(codes), ", ".join("/{}/".format(c) for c in codes),
                ", ".join(unread[:3])))
        return

    sample = _edition_sample(ok_pages, editions)
    read, declaring = [], []
    for page in sample:
        url = page.get("final_url") or page.get("url") or ""
        if fetcher is None or not fetcher.budget_left or not fetcher.time_left:
            break
        if is_disallowed(robots or {}, USER_AGENT, path_of(url)):
            continue
        response = fetcher.try_get(url)
        if response is None or response.status_code != 200:
            continue
        read.append(url)
        header = str((response.headers or {}).get("link") or "")
        if _HREFLANG_RE.search(response_text(response)) or _HREFLANG_RE.search(header):
            declaring.append(url)

    if not read:
        result.skip(
            "hreflang-between-language-editions",
            "this site serves {} language editions ({}), and no page of them could be read "
            "again to see whether they link to each other: {}".format(
                len(codes), ", ".join("/{}/".format(c) for c in codes),
                no_network_reason(
                    snapshot, None,
                    flag_reason="extra network requests were disabled for this run",
                    exhausted_reason="the request budget for this skill was spent on earlier "
                                     "checks")))
        return
    if declaring:
        result.skip("hreflang-between-language-editions",
                    "the editions declare `hreflang`: {} carries it".format(declaring[0]))
        return

    # Where a page outside every edition declares them, say so - an owner
    # who opens the root and sees the tags would otherwise read this finding
    # as wrong. The tags are there; they are on the one page that is not an
    # edition, and nothing answers them.
    one_way_note = (
        " {} does declare `hreflang` alternates naming the editions, but it is not one of "
        "them, and a declaration only one side makes is ignored: each edition page has to "
        "name the others and itself.".format(
            one_way[0].get("final_url") or one_way[0].get("url"))
        if one_way else "")
    result.add(
        id_hint="no-hreflang-between-language-editions",
        title="This site publishes {} language editions and none links to the others".format(
            len(codes)),
        severity="medium", confidence="high",
        # No `checked=(...)`: `canonical-broken` reports something observed,
        # and `make_finding` refuses the field on a cause that is not an
        # absence claim. What was read and what was not is in the evidence
        # instead, which is the field both renderers print under the claim.
        evidence="This crawl reached {} language editions of this site: {}. A leading path "
                 "segment counts as an edition only where the page's own `lang` attribute "
                 "agrees with it, or where the crawl met more than one such prefix. {} "
                 "re-read in full and searched for the attribute name `hreflang` wherever it "
                 "can appear - a `<link rel=\"alternate\">`, an anchor, or a `Link:` response "
                 "header - and neither carries it: {}.{} {}".format(
                     len(codes), ", ".join("/{}/".format(c) for c in codes),
                     plural(len(read), "page was", "pages were"), ", ".join(read),
                     one_way_note,
                     "The {} this crawl read, holding {}, carry no `<xhtml:link "
                     "rel=\"alternate\">` either, which is the sitemap's way of declaring the "
                     "same thing.".format(
                         plural(len(read_sitemaps), "sitemap file", "sitemap files"),
                         sitemap_total_phrase(sitemap_scope(snapshot.get("sitemaps") or [])))
                     if read_sitemaps else
                     "This crawl found no sitemap, so there is no list of `<xhtml:link "
                     "rel=\"alternate\">` entries - the sitemap's way of declaring the same "
                     "thing - to carry it either."),
        mechanism="A", root_cause="canonical-broken",
        summary="Declare `hreflang` on every page, naming each language edition of it.",
        how_to_fix=[
            "Emit one `<link rel=\"alternate\" hreflang=\"...\" href=\"...\">` per edition of "
            "the current page, including one for the page itself, plus an `x-default` naming "
            "the edition to serve a reader whose language you do not publish.",
            # Which file that goes in, and who can edit it. The sentence "put
            # it in the base template" is not an instruction until it names the
            # file, and this skill had never called the two functions that do.
            *template_change_steps(publishing_platform(snapshot),
                                   "that set of `<link rel=\"alternate\">` tags"),
            "Every edition must list every other edition, and each listed URL must point "
            "back. A one-way declaration is ignored.",
            "Use the language code the edition is actually written in rather than the path "
            "segment where they differ - a `/jp/` path serving Japanese is `hreflang=\"ja\"`.",
            "Confirm by requesting one page of each edition and checking the head of each "
            "holds the same set of alternates.",
        ],
        effort="medium", owner="developer",
        rationale="Without this the editions are near-duplicates of each other with no "
                  "declared relationship, so a consumer cannot tell which one answers which "
                  "reader and the editions compete with each other for the same content.",
        affected_pages=read,
    )


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
