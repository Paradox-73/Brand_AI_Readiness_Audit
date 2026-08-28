# Verification Plan
### Onboarding and four-way parallel review · Brand AI-Readiness Audit

![Reviewers](https://img.shields.io/badge/Reviewers-4%20parallel-6E56CF?style=flat-square)
![Onboarding](https://img.shields.io/badge/Shared%20reading-~70%20min-FF6F00?style=flat-square)
![Scope](https://img.shields.io/badge/Per%20reviewer-~2%2C500%20lines-4169E1?style=flat-square)
![Dependencies](https://img.shields.io/badge/Cross--stream%20dependencies-none-2EA043?style=flat-square)

---

## Table of Contents

1. [The Problem We Were Given](#1-the-problem-we-were-given)
2. [How the Solution Is Shaped](#2-how-the-solution-is-shaped)
3. [Shared Reading — Do This First](#3-shared-reading--do-this-first)
4. [How the Work Splits](#4-how-the-work-splits)
5. [Setup — Every Reviewer](#5-setup--every-reviewer)
6. [Reviewer A — Access and Delivery](#6-reviewer-a--access-and-delivery)
7. [Reviewer B — Machine-Readable Facts](#7-reviewer-b--machine-readable-facts)
8. [Reviewer C — Quotable Facts, Trust and Engagement](#8-reviewer-c--quotable-facts-trust-and-engagement)
9. [Reviewer D — Composition, Contract and Packaging](#9-reviewer-d--composition-contract-and-packaging)
10. [Shared Checklist](#10-shared-checklist)
11. [Reporting Findings](#11-reporting-findings)

---

## 1. The Problem We Were Given

Read this section even if you think you know the project. Everything downstream assumes it.

### The brief

Round 3 asks for **one Agent Skill Marketplace**: a package of one or more skills, each
written in the standard [agentskills.io](https://agentskills.io/specification) form — a
`SKILL.md` with YAML frontmatter plus instructions, optionally with `scripts/` and
`references/` — tied together by a manifest that marks **exactly one entrypoint**.

Given any website, that entrypoint must produce a single audit report containing:

- **Problems found** — the issues hurting the brand's AI discoverability and its on-site
  engagement, each with evidence and a severity.
- **Suggested actions** — what to change and how, prioritised. These may go beyond the
  problems found, to proactive improvements that would help even where nothing is broken.

Two constraints shape everything:

> **Recommend-only.** No skill may alter a live site. The marketplace audits and reports.
> No destructive, authenticated or rate-abusing actions. Respect robots.txt.

> **Generalisation.** No example sites are provided. Judges run it on sites nobody has seen.
> A check tuned to one site is worthless.

### What is actually being graded

The judges evaluate **the marketplace itself** — the checks it encodes, the fixes it
recommends, how the skills are decomposed, and the engineering. They do not grade one lucky
report. The rubric is: detection accuracy, suggested-action quality, output design,
skill-format hygiene, marketplace composition, and generalisation.

That matters for how you review. A check that produces an impressive finding on one site but
fires wrongly on three others is a net negative.

### The three symptoms

The underlying scenario is a brand that AI assistants handle badly, in three ways:

| Symptom | Meaning |
|---|---|
| **Invisible** | Assistants never surface or cite the brand at all |
| **Stale** | Assistants describe it with outdated facts — old prices, an old logo, discontinued products |
| **Bouncing** | Visitors who do arrive from an AI answer leave immediately |

The first two are off-site discoverability. The third is on-site engagement. The brief
requires both halves.

---

## 2. How the Solution Is Shaped

### The mechanism model

Every check in this marketplace traces to a numbered mechanism — a claim about how machines
actually find, read and repeat content. **If a check cannot name a mechanism, it does not go
in.** This is the spine of the whole design, and the thing to hold each check against.

| ID | Claim |
|---|---|
| **A** | Three gates, in order: the crawler gets in, it can read the page, it can extract the specific fact. Fail one and the page does not exist for that system |
| **B** | Assistants quote what is easy to reach, read and lift. A page can be perfect and still have no quotable sentence |
| **C** | A page that looks complete to a person can be empty to a machine — JavaScript-rendered, text in images, facts only in a PDF |
| **D** | Trust comes from agreement across independent sources. A fact stated in one place is fragile; a name shared with other entities causes mistaken identity |
| **E** | Assistants shape answers to the person asking, so content written for one framing surfaces for one framing only |
| **F** | Email summaries are built from readable text. Substance carried in an image disappears |
| **G** | A visitor arriving mid-journey from an answer carries context the site cannot see, and leaves unless the page orients them |

**G is ours, not the brief's.** The brief stops at F, and all six of its mechanisms describe
machine behaviour. None covers what happens after a person clicks. `DECISIONS.md` §2 argues
that folding engagement into E would misdescribe it. Challenge that if you disagree.

### The architecture in one picture

```
run_audit.py
  |
  |- 1. crawl.py  ------------->  snapshot.json      ONE crawl, shared by everything
  |      robots.txt, sitemaps, /llms.txt, homepage,
  |      8 sitemap pages sampled with seed 42, then links to depth 2
  |      Caps: 30 pages, 240 s, 10 s/request, 0.5 s between, single thread
  |
  |- 2. six x check.py --snapshot snapshot.json --out <skill>.findings.json
  |      Each reads fields off the snapshot. Three make a few extra requests.
  |      They run in gate order; an earlier skill owns a shared observation.
  |
  |- 3. compose_report.py  ---->  report.json + report.md
         Merge duplicates, assign IDs, score priority, simulate citations,
         append the recommendations whose conditions occurred.
```

**One crawl, six readers.** This is the single most important design decision. The
orchestrator fetches the site once and writes `snapshot.json`; sub-skills read fields off it
rather than fetching anything. It is why six skills are not six crawlers, and why a full
audit finishes in about ten seconds on a small site.

### The three things a reviewer must understand about a "finding"

1. **Severity** is how bad the problem is. **Priority** is what to do first, and it is
   `severity × reach ÷ effort`. A critical fault on one page can rank below a one-minute fix
   that unblocks the whole site. Both numbers are in the report.
2. **`not_applicable` is an output, not silence.** Every check that stays quiet records why.
   "This site has no product pages, so Product markup is not expected" is a result. This is
   the marketplace's main defence against false positives, and it is what you are checking.
3. **`root_cause` is a fixed vocabulary** of about 45 tags defined once in
   `audit_common.py`. Deduplication keys on it, so a skill inventing a tag fails loudly.

### Vocabulary you will meet in the code

| Term | Meaning |
|---|---|
| **Snapshot** | `snapshot.json`. One crawl's output: pages, headers, extracted fields, robots, sitemaps |
| **Gate** | One of the three steps in mechanism A. Each sub-skill owns one |
| **Page type** | `home`, `about`, `product`, `article`, `legal`… Detected during the crawl. Every expectation keys off it |
| **Root cause** | A tag from the fixed vocabulary, e.g. `js-shell`, `no-org-schema` |
| **Reach** | Share of crawled pages a finding affects, floored at 0.25 |
| **Fixture** | One of six local test websites under `tests/fixtures/` |
| **Golden file** | The root causes a fixture must and must not produce |
| **Citation simulation** | The sentence an assistant would most likely quote from each key page |

---

## 3. Shared Reading — Do This First

About 70 minutes. Do it before splitting up. Everybody reads the same six things, in this
order, because each one assumes the previous.

| # | Read | Time | Why |
|---|---|---|---|
| 1 | `6a8ffdf33590a_round3-handout-updated.pdf` — the brief | 15 min | The requirements everything is measured against |
| 2 | `README.md` §1–§3 | 10 min | What the marketplace does and how it is put together |
| 3 | `skills/audit-orchestrator/references/mechanism-model.md` | 15 min | The seven mechanisms. Every check refers to one |
| 4 | `skills/audit-orchestrator/SKILL.md` | 10 min | The entrypoint's procedure, in prose |
| 5 | `skills/audit-orchestrator/references/severity-and-priority.md` | 10 min | Severity, confidence, effort, reach, priority, dedup |
| 6 | `DECISIONS.md` | 10 min | Every judgement call, and where we departed from the brief |

Then, before touching any code, **run one audit and read its output**:

```bash
# Terminal 1 — serve a broken test site
python -c "
import sys, time; sys.path.insert(0, 'tests')
from fixture_server import FixtureServer
s = FixtureServer('tests/fixtures/dead-end-site').__enter__()
print(s.base_url); time.sleep(900)
"

# Terminal 2 — audit it, then read report.md end to end
python run_audit.py <printed URL> --out-dir out --now 2026-09-01
```

Read `out/report.md` as a marketing manager would: start to finish, without looking at the
code. That gives you the product before you see the implementation, which is the order the
judges will experience it in.

### Then read the code in this order

The shared library first, because every skill depends on it and nothing in a `check.py`
makes sense without it.

| # | File | What to take from it |
|---|---|---|
| 1 | `skills/audit-orchestrator/scripts/audit_common.py` | `make_finding` (the finding contract), `ROOT_CAUSES`, `MECHANISMS`, `detect_page_type`, `SkillResult`, `Fetcher` |
| 2 | `skills/audit-orchestrator/scripts/crawl.py` | How the snapshot is built and how the budget is enforced |
| 3 | `skills/audit-orchestrator/scripts/page_extract.py` | The fields every sub-skill reads. Skim the function names |
| 4 | Any one `check.py` | The shape all six share: `run()` calls small `_check_*` functions, each of which either `result.add(...)` or `result.skip(name, reason)` |

Once you can predict what `result.skip()` will print, you understand the codebase well
enough to review your stream.

---

## 4. How the Work Splits

Four streams, split by mechanism gate. Each reviewer owns whole skills, so no two people
read the same file and nobody waits on anybody. All work runs against the local test sites,
so four people can work at once without touching a live website.

| Reviewer | Owns | Code | Docs | Test sites |
|---|---|---|---|---|
| **A** | Gate A — getting in, and reading what arrives | 2,055 | 465 | `blocked-site`, `js-shell-site` |
| **B** | Gate C — machine-readable facts | 1,714 | 716 | `no-schema-site`, `good-site` |
| **C** | B, D, G — quotable, trusted, sticky | 2,057 | 708 | `stale-site`, `dead-end-site` |
| **D** | Orchestration, output contract, packaging | 2,085 | 989 | all six |

Line counts are approximate and cover only what must actually be read. Prose reviews faster
than code, so D carries more documentation against a similar code volume.

**Two rules for every stream.** A check that never fires is as broken as one that always
fires — test both directions. And if the code and a document disagree, that is a finding,
whichever one turns out to be wrong.

---

## 5. Setup — Every Reviewer

```bash
git clone <repo> && cd brand-ai-readiness-audit
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest

# Confirm the baseline before changing anything
python skills/audit-orchestrator/scripts/validate_marketplace.py
python -m pytest tests/ -q
```

Expect 169 passing tests in about a minute. If that does not happen, stop and report it
before reviewing anything else.

> ⚠️ **Windows path limit.** Do not clone or unzip into a deep directory. `pip` cannot
> install `lxml` if the total path exceeds 260 characters. Use a short path such as
> `C:\audit`.

Always pass `--now 2026-09-01` when auditing a fixture. It pins the date the freshness
checks compare against; without it, results drift as the calendar moves.

---

## 6. Reviewer A — Access and Delivery

**Your question:** if a crawler is turned away, or handed a page it cannot read, does the
audit catch it and say the right thing about it?

### Context for your area

You own **gate A** — the first two of the three gates in mechanism A. Nothing else in the
marketplace matters if your gates are shut: a brand blocked in robots.txt cannot be helped
by better structured data, and an empty JavaScript shell has no facts to mark up.

Your area also contains the only place this marketplace sends a request pretending to be
something else. `crawl-access-audit` fetches the homepage once as an AI crawler to detect a
WAF that robots.txt cannot reveal. That probe is capped, confirmed before reporting, and
skipped when robots.txt already disallows the agent. Satisfy yourself it behaves as
documented, because it is the one place we could be accused of misrepresenting ourselves.

### Read first (about 40 minutes)

| # | Read | Why |
|---|---|---|
| 1 | `skills/crawl-access-audit/SKILL.md` | The procedure in prose. Read before the code |
| 2 | `skills/crawl-access-audit/references/ai-crawler-user-agents.md` | The answer/training crawler split, and why it matters |
| 3 | `skills/render-readability-audit/SKILL.md` | The second gate |
| 4 | `skills/render-readability-audit/references/js-render-heuristics.md` | Every threshold and the false positive it prevents |
| 5 | `tests/fixtures/blocked-site/robots.txt` and `_rules.json` | The fault this fixture encodes |
| 6 | `tests/fixtures/js-shell-site/index.html` | What a framework shell looks like in delivered HTML |

### Files you own

| Path | Lines |
|---|---|
| `skills/audit-orchestrator/scripts/crawl.py` | 551 |
| `skills/audit-orchestrator/scripts/robots_parser.py` | 178 |
| `skills/crawl-access-audit/scripts/check.py` | 817 |
| `skills/render-readability-audit/scripts/check.py` | 509 |
| Both reference documents above | 262 |
| `README.md` — verify every claim against the code | 203 |

### Checks

1. **robots.txt parsing.** Longest matching user-agent token wins; `*` applies only when no
   named group matched; `Allow` beats `Disallow` at equal length; `*` and `$` work in paths.
   Write a robots.txt that stresses each rule and confirm the parser matches what Google
   documents.
2. **The crawler-group split.** This is the judgement the skill rests on. Check each
   operator's current documentation against our table. **GPTBot is the contested one** —
   `DECISIONS.md` §3 explains why we placed it in the answer group, and we want that
   challenged.
3. **Benign paths.** `/cart`, `/wp-admin`, `/search` and parameter filters must never be
   reported as blocked content. Try `/*?*add-to-cart=`, `/*/print$`, `/collections/*+*`.
4. **The bot probe.** At most two requests; skipped entirely when robots.txt already
   disallows every answer crawler; requires a second matching response before reporting. One
   stray 403 must not become a finding.
5. **Shell detection needs two independent signals.** Build a page that trips exactly one
   and confirm it is not reported. Then check the homepage severity table in
   `js-render-heuristics.md` matches the code, including medium confidence when no rendered
   pass ran.
6. **Each non-text check has a guard.** The PDF check must stay quiet when the price is also
   in the page text; the video check when the page has 800+ characters of prose. Verify both.
7. **A shell page must not be double-reported** as image-locked or iframed.
8. **`alt=""` is correct markup** on a decorative image and must never count as missing.
9. **Crawl budget.** Confirm 30 pages, seed 42, and that the wall clock stops the crawl
   cleanly and records `budget_exhausted`.

### Deliverable

A verdict on whether the answer/training crawler split holds up against current operator
documentation, plus any check that fires or stays quiet when it should do the opposite.

---

## 7. Reviewer B — Machine-Readable Facts

**Your question:** does the audit ask the right things of each kind of page, and stay quiet
on pages where those questions make no sense?

### Context for your area

You own the **page-type detector** and the structured-data checks that depend on it. This is
the marketplace's largest false-positive guard: expectations are keyed to the detected page
type, which is what stops the audit demanding Product schema from a consultancy or an author
byline from a pricing page.

An error in `detect_page_type` does not stay local. It spreads into every skill, because all
six read `page_type` off the snapshot. Treat it as the highest-risk function in the codebase.

You also own the **agreement** check — whether the markup matches the visible page. That is
the check we consider most valuable in the skill: a missing fact leaves a machine uncertain,
but a contradictory one teaches it something false with all the confidence structured data
carries.

### Read first (about 40 minutes)

| # | Read | Why |
|---|---|---|
| 1 | `skills/structured-data-audit/SKILL.md` | The procedure in prose |
| 2 | `skills/structured-data-audit/references/schema-expectations-by-page-type.md` | The expectations table and the detection order |
| 3 | `skills/audit-orchestrator/references/report-schema.json` | The output contract your findings must satisfy |
| 4 | `audit_common.py` — `detect_page_type` and its constants | The function everything keys off |
| 5 | `tests/fixtures/no-schema-site/` | Good prose, no markup, one unparseable block |
| 6 | `tests/fixtures/good-site/index.html` | What complete, correct markup looks like |

### Files you own

| Path | Lines |
|---|---|
| `skills/audit-orchestrator/scripts/page_extract.py` | 836 |
| `skills/structured-data-audit/scripts/check.py` | 878 |
| `schema-expectations-by-page-type.md`, `jsonld-templates.md` | 376 |
| `skills/audit-orchestrator/references/report-schema.json` | 214 |
| `PROGRESS.md` — verify every claim against reality | 126 |

### Checks

1. **Page-type detection first.** Feed `detect_page_type` awkward URLs: `/roundabout`,
   `/2004/Jun/29/job/`, `/products` versus `/products/thing`, `/blog` versus `/blog/a-post`,
   `/shop/item` with and without an add-to-cart control. The resolution order — dated paths,
   strong slugs, JSON-LD, remaining slugs, content — is documented. Confirm the code
   implements that order.
2. **`legal` and `careers` expect nothing.** A privacy policy with no schema, no CTA and a
   2019 date is a normal privacy policy. Confirm no check reaches those pages.
3. **Every row of the expectations table is reproducible.** Give the audit a site of that
   type, confirm the finding appears, remove the page type, confirm it disappears.
4. **The consistency check compares prices numerically.** `480.00` and `$480` are the same
   price. Try a range, a sale price, and a symbol after the number. It must also stay quiet
   when the page shows no price at all.
5. **Invalid JSON-LD outranks missing JSON-LD.** A trailing comma should produce a `high`
   finding with the parser's real message and an excerpt.
6. **Snippets must be paste-ready.** Every generated snippet should carry values pulled from
   the audited site wherever the audit could determine them. Paste one into
   `validator.schema.org` and confirm it validates.
7. **Length thresholds.** Title length is only reported when 40% or more of pages fall
   outside the range. One long title is not a defect.
8. **`report-schema.json` matches what `compose_report.py` emits** — every required field,
   every extension, every enum.

### Deliverable

A table of page-type inputs and detected types, flagging any that are wrong, plus any
expectation in the reference document that the code does not implement.

---

## 8. Reviewer C — Quotable Facts, Trust and Engagement

**Your question:** are the most interpretive checks in the marketplace defensible, and would
a site owner accept every finding?

### Context for your area

You own the three skills that carry the most judgement, and therefore the highest
false-positive risk. Be hard on them.

`fact-extractability-audit` asks a question most audits never encode: **if an assistant
fetched this page to answer "what is X" or "how much does X cost", is there a sentence it
could lift verbatim?** A site can pass every earlier gate — crawlable, readable, correctly
marked up — and still lose here, because being parseable is not the same as being quotable.

`freshness-corroboration-audit` covers mechanism D, which is the sharpest insight in our
Round 2 analysis: assistants trust the *consensus*, so a brand whose new facts exist only on
its own site loses to the stale facts repeated across directories. Note the limit — we can
only read the brand's own site, so we detect the *preconditions* for stale consensus, not
the consensus itself.

`engagement-audit` is mechanism G, the half of the brief that covers the visitor rather than
the machine.

### Read first (about 45 minutes)

| # | Read | Why |
|---|---|---|
| 1 | `skills/fact-extractability-audit/SKILL.md` | The hardest checks, in prose |
| 2 | `skills/fact-extractability-audit/references/answerability-rules.md` | Worked before-and-after rewrites |
| 3 | `skills/freshness-corroboration-audit/references/entity-disambiguation.md` | Why name collisions matter and what fixes them |
| 4 | `skills/engagement-audit/references/engagement-heuristics.md` | Every threshold, and what an AI-referred visitor needs |
| 5 | `skills/audit-orchestrator/references/round2-failure-modes.md` | Where these checks came from, and one fix we rejected |
| 6 | `tests/fixtures/dead-end-site/index.html` | A homepage that orients nobody |

### Files you own

| Path | Lines |
|---|---|
| `skills/fact-extractability-audit/scripts/check.py` | 681 |
| `skills/freshness-corroboration-audit/scripts/check.py` | 618 |
| `skills/engagement-audit/scripts/check.py` | 758 |
| The three reference documents above | 477 |
| `DECISIONS.md` — challenge the judgement calls | 231 |

### Checks

1. **The entity definition** is the highest-value check here. It must accept "Acme is a
   demand-forecasting platform that…" and reject "Acme is a company." — too short a
   predicate to be worth quoting. Confirm it separates *named but undefined* from *never
   named at all*, since the fixes differ.
2. **Answer-first sections exclude navigational blocks** — "Related", "Read next", and any
   section over 70% link text. Without that guard the check penalises the wayfinding this
   same marketplace recommends elsewhere.
3. **Slogan headings use a deliberately high bar**: over 80% of at least five judged
   headings, compared against the whole page. An earlier version flagged "Starter plan"
   followed by "$480 per month", which is correct writing. Confirm it no longer does.
4. **Naming consistency compares only asserted names** — `Organization.name` and
   `og:site_name`. Title-derived names are excluded because "Brand | Tagline" splits into a
   brand and a tagline. Confirm a site with a tagline is not reported.
5. **Freshness needs `--now`.** Run `stale-site` with two different values and confirm the
   findings move as expected. Confirm the default is today, UTC.
6. **Date parsing.** Try `3rd November 2025`, `Nov 3, 2025`, `2025-11-03`, `03/11/2025`, and
   a footer listing every year from 2002. The last two both produced real bugs.
7. **Wikidata matching must require a close label match.** Search a brand whose name
   prefixes its own product names and confirm no collision is reported.
8. **Self-contradiction is `high`**, and covers telephone, postal code and boilerplate only.
   Name and price belong to Reviewer B's skill. Confirm the two do not overlap.
9. **Engagement thresholds are all reproducible.** Especially: a cookie banner alone is not a
   defect; the chrome check compares against the most common **non-empty** navigation; orphan
   detection is skipped when the crawl sample is too small to support the conclusion.
10. **A newsletter signup is never a finding.** It triggers a recommendation, nothing more.

### Deliverable

For each of the three skills, a judgement on whether a site owner would accept every finding,
and a list of any threshold you would move, with your reasoning.

---

## 9. Reviewer D — Composition, Contract and Packaging

**Your question:** does the entrypoint compose six skills into one coherent report, and would
that report survive a judge reading it end to end?

### Context for your area

You own the parts the rubric scores under **marketplace composition** and **output design** —
the two criteria that depend on nothing being wrong anywhere else, and that a judge
experiences first.

The composition claim we are making is specific: six skills are not six scripts in a trench
coat, because they share one crawl, one finding vocabulary, and an ordered deduplication
step. Your job is to decide whether that claim survives contact with the code.

You also own **packaging**, which is the difference between a working submission and a
disqualified one.

### Read first (about 45 minutes)

| # | Read | Why |
|---|---|---|
| 1 | `skills/audit-orchestrator/SKILL.md` | The entrypoint's procedure |
| 2 | `severity-and-priority.md` | The scoring formula, with worked examples |
| 3 | `proactive-recommendations.md` | The catalogue and every trigger condition |
| 4 | `citation-simulation.md` | The three-pass sentence selection |
| 5 | `round2-failure-modes.md` | Where the checks came from |
| 6 | `evals/README.md` | Three prompts a judge is likely to type |
| 7 | The handout's report schema section | The contract `report.json` must satisfy |

### Files you own

| Path | Lines |
|---|---|
| `skills/audit-orchestrator/scripts/audit_common.py` | 697 |
| `skills/audit-orchestrator/scripts/compose_report.py` | 857 |
| `skills/audit-orchestrator/scripts/validate_marketplace.py` | 278 |
| `run_audit.py`, `package.py` | 253 |
| The five orchestrator reference documents | 658 |
| `evals/README.md` | 117 |

### Checks

1. **Deduplication merges across skills only.** One skill emitting three findings that share
   `robots-block` is deliberate — three decisions, three fixes. Confirm the same-skill case
   survives and every merge appears in the appendix.
2. **Priority arithmetic.** Verify `severity × reach ÷ effort` including the 0.25 reach floor
   and the rule that a finding with no listed pages gets full reach. Work the four examples
   in `severity-and-priority.md` by hand.
3. **IDs are stable.** They sort by severity, mechanism and slug — deliberately not by
   priority, so a finding keeps its ID when a crawl reaches two more pages. Confirm the
   numbering has no gaps.
4. **Determinism.** Run every check twice over one snapshot and diff. It must be
   byte-identical. Confirm no finding contains a timestamp; `audited_at` exists once, at the
   report root.
5. **Every recommendation has a trigger.** Construct a site that meets it and one that does
   not; confirm the recommendation appears and disappears. `R-BOILERPLATE` is the only
   unconditional entry — decide whether that is defensible.
6. **Citation simulation** runs three passes in order: brand definition, then a number, then
   any definition. Confirm the ordering and that `Nothing quotable` appears when it should.
7. **Read `report.md` as a marketing manager.** Take the `dead-end-site` report and read it
   start to finish without the code. Could you act on every fix? Does every finding name an
   owner and a duration? Is any evidence sentence merely a restatement of its title?
8. **The safety guarantees hold.** GET and HEAD only, forbidden paths never fetched,
   robots.txt respected, request ceilings enforced. These are asserted in `test_runtime.py` —
   confirm the assertions actually prove the claim rather than restating it.
9. **Packaging.** Run `python package.py --check`. Unzip into a **short** path, build a fresh
   virtual environment, install, validate, and audit a fixture. Confirm the zip contains no
   `__pycache__`, no `out/`, and exactly seven `SKILL.md` files.
10. **Portability.** Copy one skill directory outside the marketplace and run its `check.py`.
    It must fail with a clear explanation, not an import traceback. This is a known
    trade-off, argued in `DECISIONS.md` §6 — decide whether the argument is good enough.
11. **The evals.** Work through all three prompts and confirm the described behaviour is what
    actually happens.

### Deliverable

A verdict on whether the composition story holds together, and confirmation that a clean
machine can go from zip to finished report without help.

---

## 10. Shared Checklist

Every reviewer confirms these in their own area:

- [ ] Each check has an applicability guard, and the reason it gives is true
- [ ] Each threshold is a number with a stated justification, and the code uses that number
- [ ] Each finding's evidence contains counts and sampled URLs, not adjectives
- [ ] Each fix names an owner and an effort, and a non-expert could act on it
- [ ] Each `rationale` names a mechanism letter the model actually defines
- [ ] No real site name appears anywhere in the repository
- [ ] `SKILL.md` prose and `check.py` behaviour agree, step for step
- [ ] `good-site` still reports zero findings after any change you make

---

## 11. Reporting Findings

One issue per finding:

```text
Stream:     A / B / C / D
File:       skills/<skill>/scripts/check.py:412
Severity:   blocker / should-fix / nit
Claim:      What the code or document says
Reality:    What actually happens
Repro:      The exact command, and the fixture or URL
Suggestion: What you would change
```

**Blocker** — produces a wrong finding on an ordinary site, or breaks a stated guarantee.
**Should-fix** — defensible but weak. **Nit** — wording.

Before you finish, run the full suite once more and confirm 169 tests still pass.

---

<div align="center">
  <sub>Adobe University Hackathon 2026 · Round 3 · Brand AI-Readiness Audit</sub>
</div>
