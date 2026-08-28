# Verification Plan
### Four independent reviewers · Brand AI-Readiness Audit

![Reviewers](https://img.shields.io/badge/Reviewers-4%20parallel-6E56CF?style=flat-square)
![Scope](https://img.shields.io/badge/Scope-~2%2C500%20lines%20each-4169E1?style=flat-square)
![Dependencies](https://img.shields.io/badge/Cross--stream%20dependencies-none-2EA043?style=flat-square)

---

## Table of Contents

1. [How the Work Splits](#1-how-the-work-splits)
2. [Setup — Every Reviewer](#2-setup--every-reviewer)
3. [Reviewer A — Access and Delivery](#3-reviewer-a--access-and-delivery)
4. [Reviewer B — Machine-Readable Facts](#4-reviewer-b--machine-readable-facts)
5. [Reviewer C — Quotable Facts, Trust and Engagement](#5-reviewer-c--quotable-facts-trust-and-engagement)
6. [Reviewer D — Composition, Contract and Packaging](#6-reviewer-d--composition-contract-and-packaging)
7. [Shared Checklist](#7-shared-checklist)
8. [Reporting Findings](#8-reporting-findings)

---

## 1. How the Work Splits

Four streams, split by mechanism gate. Each reviewer owns whole skills, so no two people
read the same file and nobody waits on anybody else. Everything runs against local test
sites, so four reviewers can work at the same time without touching a real website.

| Reviewer | Gate | Code | Docs | Test sites |
|---|---|---|---|---|
| **A** | A — getting in, and reading what arrives | 2,055 | 465 | `blocked-site`, `js-shell-site` |
| **B** | C — machine-readable facts | 1,714 | 716 | `no-schema-site`, `good-site` |
| **C** | B, D, G — quotable, trusted, sticky | 2,057 | 708 | `stale-site`, `dead-end-site` |
| **D** | Orchestration, output contract, packaging | 2,085 | 989 | all six |

Line counts are approximate and cover code and prose that must actually be read. Reviewing
prose is faster than reviewing code, so D carries more documentation to balance a similar
code volume.

**Two rules for every stream.** First, a check that never fires is as broken as a check that
always fires — test both directions. Second, if the code and a document disagree, that is a
finding, whichever one is wrong.

---

## 2. Setup — Every Reviewer

Run this once. It takes about three minutes.

```bash
git clone <repo> && cd brand-ai-readiness-audit
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest

# Confirm the baseline before you change anything
python skills/audit-orchestrator/scripts/validate_marketplace.py
python -m pytest tests/ -q
```

Expect 169 passing tests in about a minute. If that does not happen, stop and say so before
reviewing anything else.

To audit one test site by hand and read the output:

```bash
# Terminal 1 — serve a test site
python -c "
import sys, time; sys.path.insert(0, 'tests')
from fixture_server import FixtureServer
s = FixtureServer('tests/fixtures/blocked-site').__enter__()
print(s.base_url); time.sleep(900)
"

# Terminal 2 — audit it and read the report
python run_audit.py <printed URL> --out-dir out --keep-intermediates --now 2026-09-01
cat out/report.md
```

`--now` pins the date the freshness checks compare against. Use it, or results drift as the
calendar moves.

---

## 3. Reviewer A — Access and Delivery

**Question you are answering:** if a crawler is turned away, or handed a page it cannot
read, does the audit catch it and say the right thing about it?

### Files

| Path | Lines |
|---|---|
| `skills/audit-orchestrator/scripts/crawl.py` | 551 |
| `skills/audit-orchestrator/scripts/robots_parser.py` | 178 |
| `skills/crawl-access-audit/scripts/check.py` | 817 |
| `skills/render-readability-audit/scripts/check.py` | 509 |
| `skills/crawl-access-audit/references/ai-crawler-user-agents.md` | 122 |
| `skills/render-readability-audit/references/js-render-heuristics.md` | 140 |
| `README.md` — check every claim against the code | 203 |

### Checks

1. **robots.txt parsing.** Longest matching user-agent token wins; `*` applies only when no
   named group matched; `Allow` beats `Disallow` at equal length; `*` and `$` work in paths.
   Write a robots.txt that breaks each rule and confirm the parser agrees with what Google
   documents.
2. **The crawler-group split.** Answer crawlers versus training crawlers is the judgement
   this skill rests on. Read `ai-crawler-user-agents.md` and check each operator's current
   documentation. **GPTBot is the contested one** — `DECISIONS.md` §3 explains why we put it
   in the answer group, and we want that decision challenged.
3. **Benign paths.** `/cart`, `/wp-admin`, `/search` and parameter filters must never be
   reported as blocked content. Try `/*?*add-to-cart=`, `/*/print$` and `/collections/*+*`.
4. **The bot probe.** Confirm it makes at most two requests, skips entirely when robots.txt
   already disallows every answer crawler, and requires a second matching response before
   reporting. One 403 should not become a finding.
5. **Shell detection.** Two independent signals are required. Build a page that trips
   exactly one and confirm it is not reported. Then check the homepage severity table in
   `js-render-heuristics.md` matches the code, including medium confidence when no rendered
   pass ran.
6. **Facts in images, PDFs, video, iframes.** Each has a guard that suppresses it. The PDF
   check must stay quiet when the price is also in the page text; the video check when the
   page has 800+ characters of prose. Verify both.
7. **A shell page must not be double-reported** as image-locked or iframed.
8. **`alt=""` is correct markup** on a decorative image and must never be counted as missing.

### Deliverable

A note on whether the crawler group split holds up, plus any check that fires or stays quiet
when it should do the opposite.

---

## 4. Reviewer B — Machine-Readable Facts

**Question you are answering:** does the audit ask the right things of each kind of page,
and does it stay quiet on the pages where those questions make no sense?

### Files

| Path | Lines |
|---|---|
| `skills/audit-orchestrator/scripts/page_extract.py` | 836 |
| `skills/structured-data-audit/scripts/check.py` | 878 |
| `skills/structured-data-audit/references/schema-expectations-by-page-type.md` | 122 |
| `skills/structured-data-audit/references/jsonld-templates.md` | 254 |
| `skills/audit-orchestrator/references/report-schema.json` | 214 |
| `PROGRESS.md` — check every claim against reality | 126 |

### Checks

1. **Page-type detection is the foundation.** Every expectation in this skill keys off it,
   so an error here spreads everywhere. Feed `detect_page_type` awkward URLs: `/roundabout`,
   `/2004/Jun/29/job/`, `/products` versus `/products/thing`, `/blog` versus `/blog/a-post`,
   `/shop/item` with and without an add-to-cart control. The order of resolution — dated
   paths, then strong slugs, then JSON-LD, then remaining slugs, then content — is in
   `schema-expectations-by-page-type.md`. Check the code implements that order.
2. **`legal` and `careers` expect nothing.** A privacy policy with no schema, no call to
   action and a 2019 date is a normal privacy policy. Confirm no check reaches those pages.
3. **Expectations match the table.** Every row of the expectations table should be
   reproducible: give the audit a site of that type and confirm the finding appears, then
   remove the page type and confirm it disappears.
4. **The consistency check.** Prices must compare **numerically** — `480.00` and `$480` are
   the same price. Try a price range, a sale price, and a currency symbol after the number.
   This check must also stay quiet when the page shows no price at all.
5. **Invalid JSON-LD outranks missing JSON-LD.** Confirm a trailing comma produces a `high`
   finding with the parser's real error message and an excerpt.
6. **Snippets must be paste-ready.** Every generated snippet should contain values pulled
   from the audited site, not placeholders, wherever the audit could determine them. Paste
   one into `validator.schema.org` and confirm it validates.
7. **Title and description thresholds.** Length is only reported when 40% or more of pages
   are outside the range. One long title is not a defect; confirm the code agrees.
8. **`report-schema.json` matches what `compose_report.py` actually emits.** Every required
   field, every extension, every enum. This is the output contract.

### Deliverable

A table of page-type inputs and detected types, flagging any that are wrong, plus any
expectation in the reference that the code does not implement.

---

## 5. Reviewer C — Quotable Facts, Trust and Engagement

**Question you are answering:** are the softest, most interpretive checks in the marketplace
defensible, and would a site owner accept each finding?

These three skills carry the most judgement and therefore the highest false-positive risk.
Be hard on them.

### Files

| Path | Lines |
|---|---|
| `skills/fact-extractability-audit/scripts/check.py` | 681 |
| `skills/freshness-corroboration-audit/scripts/check.py` | 618 |
| `skills/engagement-audit/scripts/check.py` | 758 |
| `skills/fact-extractability-audit/references/answerability-rules.md` | 186 |
| `skills/freshness-corroboration-audit/references/entity-disambiguation.md` | 157 |
| `skills/engagement-audit/references/engagement-heuristics.md` | 134 |
| `DECISIONS.md` — challenge the judgement calls | 231 |

### Checks

1. **The entity definition.** This is the highest-value check in the marketplace, so test it
   hard. Confirm it accepts "Acme is a demand-forecasting platform that…" and rejects "Acme
   is a company." (too short a predicate). Confirm it separates *named but undefined* from
   *never named at all*, since the fixes differ.
2. **Answer-first sections.** Navigational sections — "Related", "Read next" — must be
   excluded, along with any section over 70% link text. Without that guard the check
   penalises the wayfinding this same marketplace recommends. Verify the exclusion.
3. **Slogan headings.** The threshold is deliberately high: over 80% of at least five judged
   headings, compared against the whole page. An earlier, stricter version flagged "Starter
   plan" followed by "$480 per month", which is correct writing. Confirm it no longer does.
4. **Naming consistency compares only asserted names** — `Organization.name` and
   `og:site_name`. Title-derived names are excluded, because "Brand | Tagline" splits into a
   brand and a tagline. Confirm a site with a tagline is not reported.
5. **Freshness needs `--now`.** Run `stale-site` with two different `--now` values and
   confirm the findings move as expected. Then confirm the default is today, UTC.
6. **Date parsing.** Try `3rd November 2025`, `Nov 3, 2025`, `2025-11-03`, `03/11/2025`, and
   a footer listing every year from 2002. The last two both produced bugs.
7. **Wikidata name matching.** It must require a close label match. Search a brand whose
   name prefixes its own product names and confirm no collision is reported.
8. **Self-contradiction is `high`.** Telephone, postal code and boilerplate only — name and
   price belong to Reviewer B's skill. Confirm the two do not overlap.
9. **Engagement thresholds.** Every number in `engagement-heuristics.md` should be
   reproducible. Pay particular attention to: a cookie banner alone is not a defect; the
   chrome check compares against the most common **non-empty** navigation; orphan detection
   is skipped when the crawl sample is too small.
10. **A newsletter signup is never a finding.** It triggers a recommendation and nothing else.

### Deliverable

For each of the three skills, a judgement on whether a site owner would accept every
finding, and a list of any threshold you would move, with your reasoning.

---

## 6. Reviewer D — Composition, Contract and Packaging

**Question you are answering:** does the entrypoint compose the six skills into one coherent
report, and would that report survive a judge reading it end to end?

### Files

| Path | Lines |
|---|---|
| `skills/audit-orchestrator/scripts/audit_common.py` | 697 |
| `skills/audit-orchestrator/scripts/compose_report.py` | 857 |
| `skills/audit-orchestrator/scripts/validate_marketplace.py` | 278 |
| `run_audit.py`, `package.py` | 253 |
| `skills/audit-orchestrator/references/` — five documents | 658 |
| `evals/README.md` | 117 |

### Checks

1. **Deduplication.** It merges across skills only. One skill emitting three findings that
   share `robots-block` is deliberate — those are three decisions with three fixes. Confirm
   the same-skill case is preserved and that every merge appears in the appendix.
2. **Priority arithmetic.** Verify `severity × reach ÷ effort` against
   `severity-and-priority.md`, including the 0.25 reach floor and the rule that a finding
   with no listed pages gets full reach. Work the four examples in that document by hand.
3. **IDs are stable.** They sort by severity, mechanism and slug — deliberately not by
   priority, so a finding keeps its ID when a crawl reaches two more pages. Confirm this,
   and confirm the numbering has no gaps.
4. **Determinism.** Run every check twice over one snapshot and diff the output. It must be
   byte-identical. Then confirm no finding contains a timestamp; `audited_at` exists once,
   at the report root.
5. **Recommendation conditions.** Every entry in `proactive-recommendations.md` has a
   trigger. Construct a site that meets it and one that does not, and confirm the
   recommendation appears and disappears. `R-BOILERPLATE` is the only unconditional entry —
   satisfy yourself that is defensible.
6. **Citation simulation.** Three passes in order: brand definition, then a number, then any
   definition. Confirm the ordering, and confirm `Nothing quotable` appears when it should.
7. **Read the report as a marketing manager would.** Take `out/report.md` from
   `dead-end-site` and read it start to finish without looking at the code. Could you act on
   every fix? Does every finding name an owner and a duration? Is any evidence sentence just
   a restatement of its title?
8. **The safety guarantees.** GET and HEAD only, forbidden paths never fetched, robots.txt
   respected, request ceilings held. These are asserted in `test_runtime.py`; confirm the
   assertions actually prove the claim rather than restating it.
9. **Packaging.** Run `python package.py --check`. Unzip into a **short** path, build a
   fresh virtual environment, install, validate and audit a test site. The long-path failure
   on Windows is real — `pip` cannot install `lxml` past 260 characters.
10. **The evals.** Work through all three prompts in `evals/README.md` and confirm the
    described behaviour is what actually happens.

### Deliverable

A verdict on whether the composition story holds together, and confirmation that a clean
machine can go from zip to finished report without help.

---

## 7. Shared Checklist

Every reviewer confirms these in their own area:

- [ ] Each check has an applicability guard, and the reason it gives is true
- [ ] Each threshold is a number with a stated justification, and the code uses that number
- [ ] Each finding's evidence contains counts and sampled URLs, not adjectives
- [ ] Each fix names an owner and an effort, and a non-expert could act on it
- [ ] Each `rationale` names a mechanism letter that the model actually defines
- [ ] No real site name appears anywhere in the repository
- [ ] `SKILL.md` prose and `check.py` behaviour agree, step for step
- [ ] `good-site` still reports zero findings after any change you make

---

## 8. Reporting Findings

Open one issue per finding. Use this shape:

```text
Stream:     A / B / C / D
File:       skills/<skill>/scripts/check.py:412
Severity:   blocker / should-fix / nit
Claim:      What the code or document says
Reality:    What actually happens
Repro:      The exact command, and the fixture or URL
Suggestion: What you would change
```

**Blocker** means it produces a wrong finding on an ordinary site, or breaks a stated
guarantee. **Should-fix** means it is defensible but weak. **Nit** is wording.

Before you finish, run the full suite one more time and confirm 169 tests still pass.

---

<div align="center">
  <sub>Adobe University Hackathon 2026 · Round 3 · Brand AI-Readiness Audit</sub>
</div>
