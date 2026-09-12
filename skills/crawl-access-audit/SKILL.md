---
name: crawl-access-audit
description: Check whether crawlers and AI answer engines are allowed to fetch a site at all. Audits robots.txt (including which named AI crawlers are blocked and whether the block affects answer engines or only training corpora), sitemap presence and validity, WAF and bot-manager blocks that robots.txt cannot show, HTTP status codes, redirect chains, noindex directives, canonical targets and transport security. Use when a brand is completely absent from AI answers and search, when pages are known to exist but are never cited, or as the first gate of a full AI-readiness audit. A block here makes every other improvement irrelevant.
license: MIT
compatibility: Requires Python 3.10+ with requests. Makes up to 30 extra read-only requests; pass --no-network to make none.
allowed-tools: Bash(python3:*) Bash(python:*) Read
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "A"
  gate: "let in"
---

# Crawl access audit (gate A: is the crawler let in?)

## When to use

Run this first, always. Mechanism A is a sequence of three gates and this is the first: a
crawler that is refused entry never reaches the reading or extraction stages, so a finding here
invalidates any conclusion drawn from the rest of the site.

Reach for it on its own when a brand is entirely absent from AI answers rather than described
badly, or when someone reports "our pages exist but nothing ever cites them".

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.
- `--no-network` to skip the bot-user-agent probe, the second-client name comparison, the
  sitemap HEAD checks, the rate-limit recheck and the unknown-path probe.

## Procedure

Twenty-seven checks are registered; they are grouped here into fourteen questions, in **report
order**. Each line is the question and the worst severity it can reach.
`references/access-checks.md` lists every registered check id with what it reads, its
threshold and where that number came from, where it declines, and the real report behind
each exclusion.

**Report order is not execution order.** The bot-manager user-agent comparison (item 9) runs
*first*, before anything else here, because three later checks may not call a refusal a defect
until it has answered. One report's appendix said two named search crawlers were served HTTP
200 while this audit was refused, and its headline said a bot manager serves crawlers a
verification page instead of the site. Both came out of the same run.

1. **Was a verification page served instead of the content?** A bot manager answering 2xx with
   a challenge script announces itself nowhere in the status line. Detect the vendor by its own
   scaffolding or its mitigation header; failing both, by a CAPTCHA widget; failing all three,
   by shape alone — a 2xx body of at least 5,000 bytes, 90% or more of it inline script, with
   no title, no link and at most 20 characters of text. Challenged pages are excluded from
   every content check in every skill.
2. **Is robots.txt resolvable?** A 404 means no restrictions and is not a defect. A persistent
   5xx is read by major crawlers as "disallow everything" for up to thirty days: **high**. A
   file that never answered at all is **medium**, and malformed lines are **low**. Whether the
   rules could be read is its own check, so "no rules to evaluate, which means nothing is
   disallowed" is never printed about a file that answered 403. Whether each rule is a **path**
   is another: a live file carried `Disallow: /g,mt=RegExp(`, written there by a build step,
   and a value that is not a path shape closes nothing. **Medium**, **high** where most of
   the file's rules are unreadable.
3. **Does robots.txt shut everyone out?** `Disallow: /` under `User-agent: *` with no carve-out
   is **critical**, full stop.
4. **Which AI crawlers are named and blocked?** Four roles — search index, live fetch, training,
   opt-out token — and only the first two cost citations. Two or more blocked is **high**, one is
   **medium**, training blocks are `info`. Ask two questions of every agent: is it disallowed
   from `/`, and does a group naming it close a section no `*` rule closes?
5. **Which paths are disallowed?** Only rules covering what looks like real content, with admin,
   cart, machine-readable files, build directories and wildcard query filters excluded by name.
   This check reads the `User-agent: *` group and nothing else, so its decline points at check 4.
6. **Are one agent's rules stated in two places?** Two groups sharing a token are combined by
   RFC 9309 and by the major crawlers, and taken first-match or last-match by others. **Low**,
   one edit.
7. **Sitemaps.** Referenced from robots, or at either conventional location? Does it parse? Do
   its URLs resolve? A sitemap listing per-visitor addresses — `/cart/`, `/checkout/`,
   `/my-account/`, `/wishlist/` — spends a crawler's budget on one session.
8. **Unknown paths, and what a 200 proves.** One GET of a path that cannot exist, **run before
   the sitemap, canonical and sitemap-entry checks**, because its answer settles all three.
   **Medium.**
9. **Bot manager probe**, which runs first. Fetch the homepage under an answer crawler's name
   that robots.txt allows, then a second name from a different operator, then a confirmation.
   Two matching blocks is **critical** — but only where the confirming response is itself
   blocking: a challenge body, or 401, 403, 405, 406, 429 or a 5xx. A difference that is not
   blocking-shaped is **medium**, because a crawler name redirected somewhere else has not
   been shut out. Four requests at most.
10. **Ask again through a second HTTP client.** `urllib.request` holds the client constant
    across every name, which is the confound the single-session comparison could not remove.
    Names differing is **high**; both clients refused under every name is still undecided and
    must say so.
11. **Slow is not down.** Median server response over 3 s is **medium**, over 10 s **high**,
    under its own root cause `slow-origin` rather than a non-200.
12. **Status and indexability.** Homepage non-200 is **critical**; a non-200 rate at or above
    25% is **high** and above 10% **medium**; `noindex` is **high** — but only on a page the
    site publishes to be read, never on a tag, category, author, dated or paginated archive, a
    search-result address, a filtered listing or a per-visitor address, where it is the standard
    arrangement and the report says so instead; a
    canonical pointing off-domain is **high** and at a non-200 URL **medium**; a
    `<meta http-equiv="refresh">` standing in for an HTTP redirect is `meta-refresh`, **high**
    on the homepage. Redirect chains over two hops are **low**.
13. **Transport, hostnames and agent-readable files.** Plain HTTP is **medium**. One site served
    on `www` and the bare domain with nothing saying which copy is real is **medium** — and an
    absent canonical is what makes that a finding, not what makes the check inapplicable.
    `/llms.txt` and any other agent file robots.txt names are requested and recorded; absence
    here is never a finding, only a recommendation.
14. **Language editions.** A site with one edition per language and no `hreflang` between them
    publishes near-duplicates with no declared relationship, so nothing says which edition
    answers which reader. Editions are path prefixes (`/fr/`) or, failing two of those,
    hostnames of their own (`ja.<domain>`): at least two such hostnames, each linked from at
    least half the crawled pages. One page of each of the first two path editions is re-read,
    or the front page alone for hostname editions; **medium**, and it costs nothing at all on
    the single-edition sites that are most of the web.

Two rules govern all fourteen, and are what to carry over when working through this by hand.

- **A refused request is not an answer.** A 429 on `sitemap.xml` teaches you that the edge
  refused you, not that there is no sitemap. One report called a sitemap absent and `/llms.txt`
  a clean pass on a site where both requests returned 429. Report a refusal as **unchecked**, in
  every check, or not at all.
- **A percentage of one page is not a second observation.** A site whose homepage is blocked
  produces one fetch. "The homepage refuses this crawler" and "100% of crawled pages refuse this
  crawler" are one finding, not two.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made`, `signals{}`. Signals passed upward include `blocked_answer_crawlers`,
`blocked_training_crawlers`, `section_blocked_answer_crawlers` and `section_blocked_paths` (shut
out of *part* of the site rather than all of it, and which paths), `bot_manager_block`,
`bot_manager_agents_probed`, `llms_txt_present`, `llms_txt_unchecked_reason`,
`agent_files_declared_in_robots`, `agent_files_probed`, `agent_files_present`,
`unknown_path_status`, `unknown_path_redirect`, `unknown_paths_resolve`,
`pages_declaring_canonical`, `sitemap_present`, `sitemap_html_shells`, `sitemap_body_unverified`,
`sitemap_private_paths` and `sitemap_url_count`.

`llms_txt_present` has three states, and a consumer that reads it as a boolean gets the
recommendation wrong. `true` means a file answered, `false` that the address answered and there
is no file at it, and **`null` that this run never got an answer either way** — the request was
refused, got no response, or was to a path the site's own robots.txt closes to this auditor,
and `llms_txt_unchecked_reason` says which. Only `false` is grounds for recommending that the
owner publish one. `sitemap_present` has the same three states; `null` there means a location
answered 200 with a body of unestablished type.

Every finding carries mechanism `A` and a `root_cause` from the shared vocabulary
(`robots-block`, `bot-manager-block`, `sitemap-missing`, `sitemap-broken`, `non-200`,
`slow-origin`, `redirect-chain`, `noindex`, `meta-refresh`, `canonical-broken`,
`insecure-transport`, `host-inconsistency`).

A finding here that says something is **missing** carries `checked[]`, the independent sources it
consulted and found empty. One source caps it at medium confidence and the report says so. Working
through this skill by hand, do the same: say where you looked, and look in a second place before
stating at high confidence that something is not there. Every false positive this marketplace has
made on a real site was one detector with a finite list of shapes, reported as a fact about the site.

## Not applicable when

- robots.txt returns a non-200: there are no rules, so nothing is disallowed. Recorded as not
  applicable, never as a finding.
- Every AI answer crawler is already disallowed: the bot-manager probe is skipped so the audit
  does not send a request it has been asked not to send.
- No extra requests could be made: the probe, the sitemap HEAD checks, the missing-path probe
  and the agent-file requests are skipped, and the reason says **which** of the two causes
  applies — a caller's `--no-network` or a spent wall clock, which the flag alone cannot tell
  apart because the orchestrator appends it in both cases.
- The origin is localhost or a bare IP: transport security is a deployment concern there, not a
  site defect.
- The homepage was never fetched: there is no baseline for the bot comparison.

`references/access-checks.md` gives the wording each of these reasons has to reach, and the
report that made each one necessary.

## References

- `references/access-checks.md` — every one of the twenty-seven registered check ids: what it
  reads, its threshold and where that number came from, the severity it can reach, where it
  declines, and the real report behind each exclusion.
- `references/ai-crawler-user-agents.md` — every user agent checked, which group it is in, who
  operates it, what blocking it actually costs, and the source and date of each operator's own
  statement.
- `references/robots-path-vocabulary.md` — which paths are content and which are plumbing, with
  the boundary each is matched on.
- `references/second-client-comparison.md` — the four outcomes of the two-client refusal
  comparison, what each may claim, and the measurements behind them.
