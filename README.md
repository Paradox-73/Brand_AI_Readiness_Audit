# brand-ai-readiness-audit

An Agent Skill Marketplace that audits any website for two things at once:

- **Off-site discoverability** — why AI assistants cannot find, read, trust or correctly
  describe the brand.
- **On-site engagement** — why visitors who *do* arrive from an AI answer leave again.

It crawls a site once, runs six specialist skills over that single snapshot, and emits one
prioritised report of evidence-backed findings with concrete fixes, plus proactive
recommendations that apply even where nothing is broken.

**Recommend-only.** Nothing here writes to, logs into, or hammers a website. GET and HEAD
only, robots.txt respected, forms never submitted.

```bash
pip install -r requirements.txt
python run_audit.py https://example.com
# -> out/report.json  and  out/report.md
```

---

## The idea in one paragraph

A page is only visible to a machine if three gates open in order: the crawler is **let in**,
it can **read** what is delivered, and it can **extract** the specific fact it needs. Most
audits check the first gate and stop. This marketplace checks all three, then asks the
question almost nobody encodes — *if an assistant fetched this page to answer "what is X" or
"how much does X cost", is there a sentence it could lift verbatim?* — and finally checks
what happens to the human who clicks through. Every check traces back to a numbered
mechanism, and every finding cites the one it repairs.

## The six skills, and why they are six

The entrypoint owns the crawl, composition, scoring and recommendations. Each sub-skill owns
exactly one mechanism gate, and no skill duplicates another's checks.

| Skill | Gate | Answers |
|---|---|---|
| **`crawl-access-audit`** | A — let in | robots.txt, which *named* AI crawlers are blocked and whether that costs citations or only training data, WAF/bot-manager blocks robots.txt cannot show, sitemaps, status codes, `noindex`, canonicals |
| **`render-readability-audit`** | A/C — can read | JavaScript shells, facts locked in images, PDFs, video or iframes, uncrawlable "load more" pagination |
| **`structured-data-audit`** | C — machine-readable facts | JSON-LD presence, validity and completeness **keyed to detected page type**, and whether the markup agrees with the visible page |
| **`fact-extractability-audit`** | C — quotable facts | Is there a `<Brand> is a <category> that...` sentence? Do sections answer first or warm up? Are price, location, contact and founding facts stated in plain text at all? |
| **`freshness-corroboration-audit`** | D — trusted | Staleness, missing date signals, how many authoritative profiles corroborate the brand, whether other entities share its name, and whether the site contradicts itself |
| **`engagement-audit`** | G — visitor stays | Orientation above the fold, dead ends, orphan pages, broken links, breadcrumbs, title–body drift, interstitials, form friction |

Gate A is split across two skills on purpose: "the crawler was refused" and "the crawler was
served an empty div" are different failures with different owners and different fixes.

## How the entrypoint composes them

```
run_audit.py
  │
  ├─ 1. crawl.py  ──────────────►  snapshot.json      one crawl, shared by everything
  │      robots.txt · sitemaps · /llms.txt · homepage
  │      · 8 sitemap URLs sampled seed-42 across page types · BFS to depth 2
  │      caps: 30 pages · 240 s wall clock · 10 s/request · 0.5 s delay · single thread
  │
  ├─ 2. six × check.py --snapshot snapshot.json --out <skill>.findings.json
  │      run in gate order; earlier skills own overlapping observations
  │
  └─ 3. compose_report.py  ─────►  report.json + report.md
         dedup · stable IDs · priority · citation simulation · recommendations
```

Three things make this a composition rather than six scripts in a trench coat:

1. **One crawl.** Sub-skills read fields off the snapshot instead of re-fetching. A few make
   small, declared extra requests (bot-UA probe: 2; sitemap probes: 8; Wikidata: 4; internal
   link checks: 20) and each reports its own count.
2. **A shared vocabulary.** One finding schema, one severity scale, one fixed set of ~45
   `root_cause` tags, defined once in `skills/audit-orchestrator/scripts/audit_common.py`.
   A skill that invents a tag fails immediately rather than silently failing to deduplicate.
3. **Ordered dedup.** When two skills observe the same root cause on the same pages, the
   earlier one keeps the finding and the later one's evidence is merged in. Merges are listed
   in the report so nothing disappears quietly.

## Prioritisation

`priority = severity_weight × reach ÷ effort`

Severity alone would put a critical problem on one page above a cheap fix affecting every
page. Reach is the share of crawled pages a finding touches (floored at 0.25; findings with
no attributed pages are site-wide and get full reach); effort divides by 1, 1.5 or 2. The top
three become the report's **Start here** section. Details in
`skills/audit-orchestrator/references/severity-and-priority.md`.

## Low false positives is the design goal

A report of six correct findings beats a report of thirty maybes. So:

- **Every check declares when it does not apply**, and says so in the report's appendix.
  "No product pages were detected on this site, so Product markup is not expected" is an
  output, not a silence.
- **Expectations are keyed to detected page type.** Never Product schema on a site with no
  products; never an author byline on a pricing page.
- **Thresholds are numbers with reasons.** 300 characters of visible text, because below
  that a page carries no quotable fact. 18 months, because that is where "recent" stops being
  defensible.
- **Deliberate choices are not defects.** Blocking training crawlers is reported as `info`,
  not as a problem. A cookie banner alone is not an intrusive interstitial. `alt=""` on a
  decorative image is correct and is not counted as missing.
- **`tests/fixtures/good-site` is the guard.** A well-built site must produce **zero**
  findings. It currently does. Reaching zero is what surfaced seven real bugs — including a
  price comparison that read `480.00` as different from `$480`, and "founded **in 2019**"
  being scored as a staleness claim.

## Sample report excerpt

Real output, from `tests/fixtures/js-shell-site`:

> ### F-001 — The homepage is delivered as an empty JavaScript shell
>
> **Priority medium** · confidence medium · mechanism C · found by render-readability-audit
>
> **What we found.** `https://example.com/`: framework root `#__next` contains 0 chars;
> content held in `__NEXT_DATA__`, `__INITIAL_STATE__` rather than in HTML; `<noscript>`
> tells the visitor to enable JavaScript. No rendered pass was available on this machine, so
> it could not be confirmed whether JavaScript recovers the text.
>
> **Why it matters.** Mechanism C: a page that looks complete to a human can be empty to a
> machine. Many crawlers and assistant fetchers read only the first HTML response, so an
> empty shell means the brand has no homepage at all.
>
> **What to do.** Server-render the homepage, or pre-render it to static HTML at build time.
> Who does it: developer. Roughly: several days of development time.
>
> - Turn on server-side rendering or static generation for the homepage in your framework
>   (Next.js, Nuxt, Remix, Angular Universal and SvelteKit all support this).
> - Verify with `curl -s <url> | grep -c '<h1'` or View Source, **not** DevTools: DevTools
>   shows the page after JavaScript has run.
> - At minimum, put the H1, the one-sentence brand description and the primary facts into the
>   server response even if the rest hydrates client-side.

The report also includes a **citation simulation** — for each key page, the sentence an
assistant would most likely quote, or `Nothing quotable`. On the same fixture, every page
returns *"no sentence on this page both stands alone and states a fact"*, which makes the
abstract failure immediate.

## Output

`report.json` keeps the required schema exactly (`site`, `audited_at`,
`summary{total_findings, critical, high, medium}`, `findings[]{id, title, severity, evidence,
suggested_action{summary, priority}}`) and extends it with `confidence`, `mechanism`,
`root_cause`, `affected_pages`, `reach`, `detected_by`, `suggested_action.effort`, `.owner`,
`.how_to_fix[]`, `.snippet`, `.rationale`, plus top-level `recommendations[]`,
`citation_simulation[]`, `start_here[]`, `crawl{}`, `checks_run[]` and `not_applicable[]`.

`report.md` is written for a marketing manager, not an engineer: every fix names who
typically does it and roughly how long it takes.

## Running it

```bash
python run_audit.py example.com                  # md + json into ./out
python run_audit.py example.com --format html    # add a shareable HTML report
python run_audit.py example.com --render         # add a Playwright rendered-DOM pass
python run_audit.py example.com --no-network     # snapshot only, zero extra requests
python run_audit.py example.com --now 2026-01-31 # fix the freshness reference date
```

Or drive the steps individually — `crawl.py`, then each `check.py`, then
`compose_report.py`. Every sub-skill's `SKILL.md` also states its checks in prose, so an
agent with no Python can work through them by hand and reach the same findings.

Validate the marketplace itself:

```bash
python skills/audit-orchestrator/scripts/validate_marketplace.py
python -m pytest tests/ -q
```

## Requirements

Python 3.10+, `requests`, `beautifulsoup4`, `lxml`. Optional: `playwright` (auto-detected;
its absence is reported as an audit-environment limitation, never as a site defect).

## Limitations, stated plainly

- **Bounded crawl.** 30 pages. On a large site this is a sample, and the report says how many
  pages it saw. Findings describe what was crawled, not what exists.
- **Static-first.** Without Playwright, JavaScript-rendered content is judged as a crawler
  that does not execute JavaScript would see it — which is the point, but it means the render
  gap is inferred rather than measured.
- **No authentication.** Anything behind a login is invisible to this audit.
- **Entity ambiguity uses Wikidata only.** It counts entities sharing a name; it cannot tell
  you which one an assistant currently prefers. Where the runtime offers web search, the
  suggested query is recorded in the report rather than guessed at.
- **Engagement is inferred from markup**, not from analytics. It finds the structural reasons
  a visitor would leave; it cannot tell you that they did.
- **Heuristics are heuristics.** Every finding carries a `confidence` field, and the
  low-confidence ones are marked as such rather than dressed up.

## Licence

MIT. See `LICENSE`.
