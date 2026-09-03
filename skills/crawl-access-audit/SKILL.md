---
name: crawl-access-audit
description: Check whether crawlers and AI answer engines are allowed to fetch a site at all. Audits robots.txt (including which named AI crawlers are blocked and whether the block affects answer engines or only training corpora), sitemap presence and validity, WAF and bot-manager blocks that robots.txt cannot show, HTTP status codes, redirect chains, noindex directives, canonical targets and transport security. Use when a brand is completely absent from AI answers and search, when pages are known to exist but are never cited, or as the first gate of a full AI-readiness audit. A block here makes every other improvement irrelevant.
license: MIT
compatibility: Requires Python 3.10+ with requests. Makes up to 16 extra read-only requests; pass --no-network to make none.
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

1. **Was a verification page served instead of the content?** A bot manager that answers
   403 announces itself in the status line. The one that answers 2xx does not: measured on
   real commercial homepages, one returns HTTP 202 with zero characters of readable text and
   another returns 200 with thirty-two, both carrying a vendor's challenge script. Detect the
   vendor by its own scaffolding - AWS WAF, Akamai Bot Manager, Cloudflare, DataDome,
   PerimeterX, Imperva or Distil - gated on the page carrying under 800 characters, so an
   article *about* bot management is never mistaken for one. Name the vendor in the finding:
   the fix is an allow rule in that specific product. Those pages are then excluded from
   every content check, because a challenge describes the crawler's reception and not the
   site. Without this the audit reports a JavaScript shell, absent structured data, no
   quotable fact and thin content - four confident findings about a homepage that is fine.

2. **Is robots.txt resolvable?** Fetch it. A 404 means no restrictions and is not a
   defect. A 5xx *is* a defect and a non-obvious one: major crawlers treat a persistent
   5xx on robots.txt as "disallow everything" for weeks. An unreachable file is a medium
   finding. Malformed lines are low: rules above the first `User-agent` line apply to
   nobody, so the policy the site believes it has is not the one crawlers see.

3. **Does robots.txt shut everyone out?** `Disallow: /` under `User-agent: *` with no
   `Allow` carve-out is critical, full stop.

4. **Which AI crawlers are named and blocked?** Split them into two groups, because they
   are not the same decision. Four roles, from `references/ai-crawler-user-agents.md`:
   - **Search index agents** index pages so the brand can be linked in an answer
     (`OAI-SearchBot`, `Claude-SearchBot`, `PerplexityBot`, `Applebot`).
   - **Live-fetch agents** fetch one page because a person just asked about it
     (`ChatGPT-User`, `Claude-User`, `Perplexity-User`, `Meta-ExternalFetcher`).
     Blocking either of these two removes the brand from answers. Two or more blocked is
     high; one is medium.
   - **Training crawlers** collect corpora (`GPTBot`, `ClaudeBot`, `Amazonbot`, `CCBot`,
     `Meta-ExternalAgent`). Blocking them is a legitimate rights choice, not a
     discoverability defect. Report as `info` so it never inflates the counts.
   - **Opt-out tokens** are not crawlers at all (`Google-Extended`, `Applebot-Extended`).
     They fetch nothing, so blocking one cannot cost a citation. `Google-Extended` is not
     `Googlebot` and does not affect Google Search.

   **Never tell an owner to allow a training crawler in order to be cited.** The first
   version of this skill had `GPTBot`, `ClaudeBot`, `Amazonbot`, `Bytespider` and
   `Meta-ExternalAgent` in the answer group and told owners, at high severity and rank two
   of "Start here", to allow-list them so their pages could be quoted. All five are training
   crawlers by their operators' own published descriptions, and following that advice would
   have reopened a site to training collection its owner had deliberately opted out of, for
   no citation gain. When reporting a training block, name the same operators' search and
   live-fetch agents instead, and confirm those are open.

   Every agent in the report is named with its operator and that operator's own statement of
   what it does. `references/ai-crawler-user-agents.md` carries the URL of the page each
   statement came from and the date it was read; two of the rows have no published statement
   at all and say so. Do not assert a role you cannot source.

5. **Which paths are disallowed?** Ignore admin, cart, checkout, account, login and search
   paths — blocking those is correct. Ignore files that exist for machines rather than
   readers (`/apple-app-site-association`, `/humans.txt`, `/ads.txt`, `/.well-known/`),
   framework and build directories (`/App_Themes`, `/_next/`, `/bin`), and error and
   maintenance pages. Two shop reports opened with "robots.txt disallows N paths that look
   like real content" and named an 89-byte Apple deep-linking manifest as the first thing to
   fix. Ignore wildcard rules too: they usually filter query strings.

   Report only disallow rules covering what looks like real content — and say in the finding
   that you did **not** fetch those paths, because robots.txt disallows them and this audit
   respects that, so what is behind them is unverified. Name all three exclusions you
   applied, not one of them; a reader comparing the report against their own robots.txt will
   otherwise find rules missing for a reason you never gave.

6. **Sitemaps.** Referenced from robots, or present at `/sitemap.xml`? Does it parse? What
   share of entries carry `<lastmod>`? Do the URLs it advertises resolve? Prefer statuses
   the crawl already collected; probe at most 8 more with HEAD, and only where the site
   answers HEAD honestly - a 403, 405 or 429 is recorded as *unchecked*, never as dead,
   because a refusal is a fact about the crawler's reception and not about the page. A
   sitemap full of dead URLs
   teaches crawlers the sitemap is unreliable.

7. **Bot manager probe.** robots.txt permission means nothing if the edge returns a
   challenge page. Fetch the homepage with the user agent of an answer crawler robots.txt
   does *not* disallow, and compare the status against the crawl's baseline. If it matches,
   try **one more agent from a different operator** before concluding the edge treats
   crawlers no differently: bot rules are written per agent and per vendor, and probing
   whichever name happened to sit first in a list turned a real WAF block into a clean pass
   the moment the list was reordered. If a status differs, fetch that agent once more to
   confirm; a single differing response is more often a rate limiter than a policy. Two
   matching blocks is critical, and the finding says which agents answered normally, so the
   reader knows the rule is per-agent rather than against every crawler. Skip the probe
   entirely if robots.txt already disallows every answer crawler, because the robots finding
   covers it. Maximum three requests.

8. **Slow is not down.** Take the median server response across the pages that answered
   200. Over 3 s is **medium**, over 10 s is **high**, and it is its own root cause
   (`slow-origin`) rather than a non-200. One site answered every page in ten to forty
   seconds; enough requests timed out that the report called it unreachable, with outage
   wording and a fix reading "read the server log for the failing request", on a site that was
   up and serving correct HTML. Where a fetch does fail on a slow origin, say so in the same
   sentence. No measurement is not a fast one: `--no-network` and a fully blocked site both
   produce none, and both decline the check.

9. **Status and indexability across the crawled pages.** Homepage non-200 is critical. A
   non-200 rate at or above 10% is a systemic problem; below that, stay quiet. Redirect
   chains longer than two hops are low. `noindex` in a meta tag or `X-Robots-Tag` on a
   content page is high: the page is fetched and then thrown away — unless the page's own
   title says it is missing ("Page Not Found", "Nothing To See Here"), in which case
   `noindex` is the correct thing for the site to do and telling the owner to remove it would
   put a broken page into the index. Say in the exemption line that you found one and why you
   left it alone. Canonical tags pointing
   off-domain are high; pointing at a non-200 URL is medium.

10. **Transport and hostnames.** Plain HTTP is medium, skipped for localhost and bare IP
   origins. Canonical tags split across `www` and non-`www` are medium, because two
   hostnames serving one site split every signal that would otherwise reinforce it.

11. **`/llms.txt`.** Record presence. Absence is never a finding; it feeds a proactive
   recommendation instead.

**A refused request is not an answer.** This applies to every step above and is the single
easiest way to write something false. If `sitemap.xml` returns 429, you have not learned that
there is no sitemap; you have learned that the edge refused you. One report said "No XML
sitemap is available" as settled fact and recorded `/llms.txt` as a clean pass, on a site
where both requests returned 429 — in the same document that correctly hedged the identically
blocked robots.txt. Report a refusal as **unchecked**, in every check, or not at all.

**A percentage of one page is not a second observation.** A site whose homepage is blocked
produces one fetch. Reporting "the homepage refuses this crawler" and "100% of crawled pages
refuse this crawler" as two findings prices one blocked request twice.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made`, `signals{}`. Signals passed upward include
`blocked_answer_crawlers`, `blocked_training_crawlers`, `bot_manager_block`,
`bot_manager_agents_probed`,
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
