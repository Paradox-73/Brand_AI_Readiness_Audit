---
name: crawl-access-audit
description: Check whether crawlers and AI answer engines are allowed to fetch a site at all. Audits robots.txt (including which named AI crawlers are blocked and whether the block affects answer engines or only training corpora), sitemap presence and validity, WAF and bot-manager blocks that robots.txt cannot show, HTTP status codes, redirect chains, noindex directives, canonical targets and transport security. Use when a brand is completely absent from AI answers and search, when pages are known to exist but are never cited, or as the first gate of a full AI-readiness audit. A block here makes every other improvement irrelevant.
license: MIT
compatibility: Requires Python 3.10+ with requests. Makes up to 10 extra read-only requests; pass --no-network to make none.
allowed-tools: Bash(python3:*) Bash(python:*) Read
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "A"
  gate: "let in"
---

# Crawl access audit (gate A: is the crawler let in?)

## When to use

Run this first, always. Mechanism A is a sequence of three gates and this is the first:
a crawler that is refused entry never reaches the reading or extraction stages, so a
finding here invalidates any conclusion drawn from the rest of the site.

Reach for it on its own when a brand is entirely absent from AI answers rather than
described badly, or when someone reports "our pages exist but nothing ever cites them".

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.
- `--no-network` to skip the bot-user-agent probe and sitemap HEAD checks.

## Procedure

1. **Is robots.txt resolvable?** Fetch it. A 404 means no restrictions and is not a
   defect. A 5xx *is* a defect and a non-obvious one: major crawlers treat a persistent
   5xx on robots.txt as "disallow everything" for weeks. An unreachable file is a medium
   finding. Malformed lines are low: rules above the first `User-agent` line apply to
   nobody, so the policy the site believes it has is not the one crawlers see.

2. **Does robots.txt shut everyone out?** `Disallow: /` under `User-agent: *` with no
   `Allow` carve-out is critical, full stop.

3. **Which AI crawlers are named and blocked?** Split them into two groups, because they
   are not the same decision:
   - **Answer crawlers** fetch live pages to build a cited answer. Blocking them removes
     the brand from answers. Two or more blocked is high; one is medium.
   - **Training crawlers** collect corpora. Blocking them is a legitimate rights choice,
     not a discoverability defect. Report as `info` so it never inflates the counts.
   See `references/ai-crawler-user-agents.md` for the list and the group of each.

4. **Which paths are disallowed?** Ignore admin, cart, checkout, account, login and search
   paths — blocking those is correct. Report only disallow rules covering what looks like
   real content.

5. **Sitemaps.** Referenced from robots, or present at `/sitemap.xml`? Does it parse? What
   share of entries carry `<lastmod>`? Do the URLs it advertises resolve? Prefer statuses
   the crawl already collected; probe at most 8 more with HEAD. A sitemap full of dead URLs
   teaches crawlers the sitemap is unreliable.

6. **Bot manager probe.** robots.txt permission means nothing if the edge returns a
   challenge page. Fetch the homepage once with the user agent of the first answer crawler
   robots.txt does *not* disallow, and compare the status against the crawl's baseline.
   If it differs, fetch once more to confirm; a single differing response is more often a
   rate limiter than a policy. Two matching blocks is critical. Skip the probe entirely if
   robots.txt already disallows every answer crawler, because the robots finding covers it.
   Maximum two requests.

7. **Status and indexability across the crawled pages.** Homepage non-200 is critical. A
   non-200 rate at or above 10% is a systemic problem; below that, stay quiet. Redirect
   chains longer than two hops are low. `noindex` in a meta tag or `X-Robots-Tag` on a
   content page is high: the page is fetched and then thrown away. Canonical tags pointing
   off-domain are high; pointing at a non-200 URL is medium.

8. **Transport and hostnames.** Plain HTTP is medium, skipped for localhost and bare IP
   origins. Canonical tags split across `www` and non-`www` are medium, because two
   hostnames serving one site split every signal that would otherwise reinforce it.

9. **`/llms.txt`.** Record presence. Absence is never a finding; it feeds a proactive
   recommendation instead.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made`, `signals{}`. Signals passed upward include
`blocked_answer_crawlers`, `blocked_training_crawlers`, `bot_manager_block`,
`llms_txt_present`, `sitemap_present` and `sitemap_url_count`.

Every finding carries mechanism `A` and a `root_cause` from the shared vocabulary
(`robots-block`, `bot-manager-block`, `sitemap-missing`, `sitemap-broken`, `non-200`,
`redirect-chain`, `noindex`, `canonical-broken`, `insecure-transport`,
`host-inconsistency`).

## Not applicable when

- robots.txt returns a non-200: there are no rules, so nothing is disallowed. Recorded as
  not applicable, never as a finding.
- Every AI answer crawler is already disallowed: the bot-manager probe is skipped so the
  audit does not send a request it has been asked not to send.
- `--no-network` was passed: the probe and sitemap HEAD checks are skipped and said so.
- The origin is localhost or a bare IP: transport security is a deployment concern there,
  not a site defect.
- The homepage was never fetched: there is no baseline for the bot comparison.

## References

- `references/ai-crawler-user-agents.md` — every user agent checked, which group it is in,
  who operates it, and what blocking it actually costs.
