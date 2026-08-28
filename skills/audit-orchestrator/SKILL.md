---
name: audit-orchestrator
description: Audit any website for why an AI assistant cannot find, read, trust or correctly describe the brand, and why visitors who do arrive leave again. Crawls the site once, runs six specialist sub-skills over that one snapshot, and emits a prioritised report of evidence-backed findings with concrete fixes plus proactive recommendations. Use this when someone asks why their brand is missing from ChatGPT, Perplexity, Claude or Google AI answers, why an assistant describes them wrongly or out of date, why their site is invisible to crawlers, or why traffic arrives and bounces. This is the entrypoint for the brand-ai-readiness-audit marketplace and composes every other skill in it.
license: MIT
compatibility: Requires Python 3.10+ with requests, beautifulsoup4 and lxml, and outbound network access to the audited site. Playwright is optional and enables a rendered-DOM comparison.
allowed-tools: Bash(python3:*) Bash(python:*) Read Write WebFetch
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "A B C D E F G"
  role: "entrypoint"
---

# Brand AI-readiness audit (discoverability + engagement)

## When to use

Use this skill when someone gives you a website and asks any version of:

- "Why doesn't our brand show up when I ask ChatGPT about our category?"
- "Why does the assistant describe us with old prices, an old logo, or the wrong company?"
- "Why do people click through from an AI answer and immediately leave?"
- "Audit this site for AI discoverability."

Do not use it to change a website. This marketplace only reads and recommends.

## Inputs

- **Required:** a URL or bare hostname (`example.com`, `https://example.com/some/page`).
  A path is kept as a seed page, but the audit is of the whole origin.
- **Optional:** `--render` to add a Playwright pass, `--no-network` to suppress every
  request beyond the crawl, `--now YYYY-MM-DD` to fix the reference date for freshness
  checks, `--format html` for a shareable report.

## Procedure

Run `python run_audit.py <url>` to do all of this at once, or follow the steps directly.

1. **Normalise the target.** Reduce the input to an origin (`https://example.com`).
   Keep any supplied path as an extra seed page.

2. **Crawl once.** Run `scripts/crawl.py <origin> --out snapshot.json`. It fetches
   robots.txt, the sitemaps, `/llms.txt`, the homepage, up to 8 sitemap URLs sampled with
   seed 42 and spread across detected page types, and then breadth-first from the homepage
   to depth 2. Hard caps: 30 pages, 240 s wall clock, 10 s per request, 0.5 s between
   requests, single threaded.
   If the homepage does not respond after two attempts, stop and report one critical
   finding: an unreachable homepage makes every other check meaningless.
   If robots.txt disallows this auditor at `/`, no pages are fetched at all. Report the
   access findings from robots.txt alone and mark everything else not applicable.

3. **Run the six sub-skills in this order**, each as
   `python skills/<skill>/scripts/check.py --snapshot snapshot.json --out <skill>.findings.json`:

   1. `crawl-access-audit` — is the crawler let in?
   2. `render-readability-audit` — can a machine read what is delivered?
   3. `structured-data-audit` — are the facts machine-readable?
   4. `fact-extractability-audit` — is there a sentence worth quoting?
   5. `freshness-corroboration-audit` — is it current, corroborated and unambiguous?
   6. `engagement-audit` — does an arriving visitor stay?

   The order matters. When two skills observe the same root cause on the same pages, the
   earlier one owns the finding and the later one's evidence is merged into it.

4. **Compose.** Run `scripts/compose_report.py --snapshot snapshot.json --findings <all six>
   --out-json report.json --out-md report.md`. This deduplicates, assigns stable IDs, scores
   priority, simulates which sentence an assistant would quote from each key page, and
   appends only those proactive recommendations whose conditions were actually met.

5. **Return `report.json` and `report.md`.** Lead your answer with the verdict line and the
   three "start here" fixes. Do not re-summarise every finding in chat; the report is the
   deliverable.

### Doing this without Python

Every sub-skill's SKILL.md contains its checks in prose. Fetch the same pages by hand
(GET only, respecting robots.txt), work through each skill's procedure, and record findings
in the shape described in `references/report-schema.json`. You will reach the same findings;
the scripts are the executable form of the same logic, not a different method.

## Output

`report.json` — the required schema (`site`, `audited_at`, `summary`, `findings[]` with
`id`, `title`, `severity`, `evidence`, `suggested_action{summary, priority}`) plus
extensions: `confidence`, `mechanism`, `root_cause`, `affected_pages`, `reach`,
`detected_by`, `suggested_action.effort`, `.owner`, `.how_to_fix[]`, `.snippet`,
`.rationale`, and top-level `recommendations[]`, `citation_simulation[]`, `start_here[]`,
`crawl{}`, `checks_run[]`, `not_applicable[]`.

`report.md` — written for a marketing manager: a plain-language verdict, the top three
fixes, findings grouped by severity with evidence and paste-ready snippets, the proactive
recommendations, what an assistant would quote from each key page, and an appendix of every
check that was run and every check that did not apply and why.

Priority is `severity weight x reach / effort`, not severity alone, so a cheap fix that
affects every page outranks an expensive one that affects a corner of the site. See
`references/severity-and-priority.md`.

## Not applicable when

- The target is not reachable over HTTP, or is behind a login. This audit never
  authenticates.
- The user wants changes applied. Every skill here is read-only and recommends only.
- The user wants a competitor comparison or keyword ranking report. This audits one site's
  machine-readability and on-site experience, not its market position.
- robots.txt disallows this auditor. In that case the run stops and says so rather than
  ignoring the file.

## Safety

GET and HEAD only. No forms submitted, no cookies sent that the site did not set, nothing
fetched under `/cart`, `/checkout`, `/login`, `/account` or `/admin`, and nothing robots.txt
disallows. The auditor identifies itself as `BrandAIReadinessAudit/1.0 (+read-only audit)`.
The single AI-user-agent comparison probe in `crawl-access-audit` is the one documented
exception to the user-agent rule, it is skipped when robots.txt disallows that agent, and it
is capped at two requests. The only third-party service contacted is Wikidata's public
search API.

## References

- `references/mechanism-model.md` — the seven mechanisms every check traces back to.
- `references/cited-vs-uncited-study.md` — what actually separates sites assistants cite
  from sites they ignore, measured across nine real sites, including the finding that most
  of these checks do not separate them.
- `references/round2-failure-modes.md` — the failure modes this marketplace was built from.
- `references/severity-and-priority.md` — how severity, reach, effort and priority are set.
- `references/proactive-recommendations.md` — the catalogue and each entry's trigger condition.
- `references/report-schema.json` — the output contract.
- `references/citation-simulation.md` — how the "what would an assistant quote" column works.
