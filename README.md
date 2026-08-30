# Brand AI-Readiness Audit
### An Agent Skill Marketplace · Adobe University Hackathon 2026 — Round 3

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Agent Skills](https://img.shields.io/badge/Agent%20Skills-agentskills.io-6E56CF?style=flat-square)
![Skills](https://img.shields.io/badge/Skills-7%20(1%20entrypoint)-0F9D58?style=flat-square)
![Tests](https://img.shields.io/badge/Tests-242%20passing-2EA043?style=flat-square)
![Read Only](https://img.shields.io/badge/Mode-Read--only-FF6F00?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-4169E1?style=flat-square)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [The Mechanism Model](#2-the-mechanism-model)
3. [Architecture](#3-architecture)
   - [3.1 Entrypoint & Orchestration](#31-entrypoint--orchestration)
   - [3.2 The Six Sub-Skills](#32-the-six-sub-skills)
   - [3.3 Composition & Scoring](#33-composition--scoring)
4. [Repository Structure](#4-repository-structure)
5. [Detection Design](#5-detection-design)
   - [5.1 Low False Alarms](#51-low-false-alarms)
   - [5.2 How We Know the Checks Work](#52-how-we-know-the-checks-work)
   - [5.3 Example Finding](#53-example-finding)
6. [Setup & Installation](#6-setup--installation)
7. [Command Reference](#7-command-reference)
8. [Running the Audit](#8-running-the-audit)
9. [Notes & Caveats](#9-notes--caveats)

---

## 1. Project Overview

This repository contains an **Agent Skill Marketplace** that audits any website for two
problems at the same time:

- **Off-site discoverability** — AI assistants cannot find, read, trust or correctly
  describe the brand.
- **On-site engagement** — visitors who arrive from an AI answer leave again.

One entrypoint skill crawls the site one time. Six specialist sub-skills read that single
crawl. The entrypoint then merges their findings into one report. The report gives the
evidence for each problem, the fix, the person who does the work, and the approximate time.

> **Read-only.** GET and HEAD only, robots.txt respected, 0.5 s between requests. The
> audit never signs in, submits a form, or touches `/cart`, `/checkout`, `/login` or
> `/admin`. It recommends changes; it never makes them.

---

## 2. The Mechanism Model

Every check refers to a numbered mechanism. Each finding names the mechanism that its fix
repairs. A check that cannot refer to a mechanism does not enter the marketplace.

| ID | Mechanism | Owned by |
|---|---|---|
| **A** | Three gates in order: the crawler gets in, it reads the page, it finds the fact | `crawl-access-audit`, `render-readability-audit` |
| **B** | Assistants quote what is easy to reach, read and lift | `fact-extractability-audit` |
| **C** | A page that looks complete to a person can be empty to a machine | `render-readability-audit`, `structured-data-audit` |
| **D** | Trust comes from agreement across independent sources | `freshness-corroboration-audit` |
| **E** | Assistants change the answer to suit the person who asks | Recommendations |
| **F** | Email summaries are built from readable text | Recommendations |
| **G** | A visitor who arrives mid-journey carries context the site cannot see | `engagement-audit` |

Mechanism **G** is an addition. The brief describes mechanisms A to F. All six describe
machine behaviour. None describes what happens after a person opens the page — and half the
symptoms brands actually report ("traffic arrives and bounces") live there. We added the
letter rather than stretching one of the six, so it is obvious in every finding which part of
the model is ours and which came from the brief.

---

## 3. Architecture

### 3.1 Entrypoint & Orchestration

`skills/audit-orchestrator` is the entrypoint. `marketplace.json` marks it, and it is the
only skill with `entrypoint: true`. It controls the crawl, the sub-skill order, the scoring
and the report.

The crawl budget is fixed, so two runs read the same pages:

| Limit | Value | Reason |
|---|---|---|
| Pages | 30 maximum | Keeps a run inside the 5-minute limit |
| Sample seed | 42 | Two runs select the same sitemap pages |
| Wall clock | 240 seconds | The crawler stops cleanly and reports the fact |
| Per request | 10 seconds | One slow page cannot stop the run |
| Between requests | 0.5 seconds | The audit does not overload a site |
| Threads | 1 | The audit sends one request at a time |

### 3.2 The Six Sub-Skills

Each sub-skill examines one gate. No sub-skill repeats the checks of another sub-skill.

| Skill | Gate | What it examines |
|---|---|---|
| `crawl-access-audit` | A — gets in | robots.txt, blocked AI crawlers, bot managers, sitemaps, status codes, `noindex`, canonical tags |
| `render-readability-audit` | A/C — reads | JavaScript shells, facts inside images, PDFs, video or iframes, catalogues behind a button |
| `structured-data-audit` | C — machine-readable | JSON-LD presence, validity and completeness for each page type, and agreement with the visible page |
| `fact-extractability-audit` | C — quotable | The one-sentence definition of the brand, answer-first sections, price, address, contact and founding facts |
| `freshness-corroboration-audit` | D — trusted | Old content, missing dates, off-site profiles, name conflicts, pages that disagree with each other |
| `engagement-audit` | G — visitor stays | Orientation, dead ends, orphan pages, broken links, breadcrumbs, interstitials, form length |

Two skills examine gate A. This is deliberate. "The server refused the crawler" and "the
server sent an empty page" are different faults. They have different fixes, and different
people correct them.

**Extra requests.** Most skills read the snapshot and nothing else. Three need to reach the
network, and each declares a ceiling in its `SKILL.md`, reports what it actually used, and
is held to that number by a test. `--no-network` drops all of them to zero.

| Skill | Extra requests | Purpose |
|---|---|---|
| `crawl-access-audit` | 10 | Bot user-agent probe, sitemap URL checks |
| `freshness-corroboration-audit` | 4 | Wikidata name search |
| `engagement-audit` | 20 | Internal link checks |
| All other skills | 0 | — |

### 3.3 Composition & Scoring

`compose_report.py` merges the six result files. It removes duplicates, sets stable IDs,
scores priority, simulates citations, and adds the recommendations whose conditions occur.

Priority is not severity alone:

```
priority = severity weight x reach / effort
```

**Reach** is the share of crawled pages that the finding affects, with a minimum of 0.25. A
finding with no listed pages affects the whole site, and it gets a reach of 1.0. **Effort**
divides the score: 1.0 for low, 1.5 for medium, 2.0 for high.

| Severity | Weight | Effort | Divisor |
|---|---|---|---|
| `critical` | 4 | `low` (under an hour) | 1.0 |
| `high` | 3 | `medium` (half a day to a day) | 1.5 |
| `medium` | 2 | `high` (several days) | 2.0 |
| `low` | 1 | | |
| `info` | 0 | | |

The three highest scores become the **Start here** section of the report. A one-line
robots.txt correction that unblocks a whole site therefore comes before a rewrite that
corrects one page.

---

## 4. Repository Structure

```text
brand-ai-readiness-audit/              <- zip this directory to submit
├── marketplace.json                   # Manifest. Lists 7 skills, marks 1 entrypoint
├── README.md
├── LICENSE
├── requirements.txt
├── pytest.ini
├── run_audit.py                       # Convenience runner for the whole audit
├── package.py                         # Builds and checks the submission zip
├── evals/
│   └── README.md                      # Three judge prompts and expected behaviour
├── skills/
│   ├── audit-orchestrator/            <- ENTRYPOINT
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   │   ├── audit_common.py        # Finding format, vocabulary, page types, HTTP client
│   │   │   ├── crawl.py               # The single shared crawl
│   │   │   ├── page_extract.py        # Turns one page into snapshot fields
│   │   │   ├── robots_parser.py       # Per-agent robots.txt parser
│   │   │   ├── compose_report.py      # Merge, score, recommend, render
│   │   │   └── validate_marketplace.py
│   │   └── references/
│   │       ├── mechanism-model.md
│   │       ├── cited-vs-uncited-study.md   # The field study the checks are built on
│   │       ├── round2-failure-modes.md
│   │       ├── severity-and-priority.md
│   │       ├── proactive-recommendations.md
│   │       ├── citation-simulation.md
│   │       └── report-schema.json
│   ├── crawl-access-audit/
│   │   ├── SKILL.md
│   │   ├── scripts/check.py
│   │   └── references/ai-crawler-user-agents.md
│   ├── render-readability-audit/
│   │   ├── SKILL.md
│   │   ├── scripts/check.py
│   │   └── references/js-render-heuristics.md
│   ├── structured-data-audit/
│   │   ├── SKILL.md
│   │   ├── scripts/check.py
│   │   └── references/
│   │       ├── schema-expectations-by-page-type.md
│   │       └── jsonld-templates.md
│   ├── fact-extractability-audit/
│   │   ├── SKILL.md
│   │   ├── scripts/check.py
│   │   └── references/answerability-rules.md
│   ├── freshness-corroboration-audit/
│   │   ├── SKILL.md
│   │   ├── scripts/check.py
│   │   └── references/entity-disambiguation.md
│   └── engagement-audit/
│       ├── SKILL.md
│       ├── scripts/check.py
│       └── references/engagement-heuristics.md
└── tests/
    ├── conftest.py                    # Serves a site, audits it, caches the result
    ├── fixture_server.py              # Test server. Can refuse a named bot
    ├── test_checks.py                 # Does each check fire on the correct site?
    ├── test_runtime.py                # Schema, determinism, budget, safety
    ├── test_marketplace.py            # Manifest and SKILL.md format
    ├── mutations.py                   # 43 ways to break one property of a clean site
    ├── test_mutations.py              # Does each check catch its own cause, and only it?
    ├── test_fixes_work.py             # Paste the code we hand the user. Does the finding go?
    ├── test_render.py                 # The optional browser pass, when Playwright is present
    ├── test_encoding.py               # Non-ASCII text must survive the crawl
    ├── test_coverage.py               # Every root cause must have a case that produces it
    ├── fixtures/                      # 6 local websites, one per fault type
    │   ├── good-site/                 # No faults. Must produce zero findings
    │   ├── js-shell-site/
    │   ├── blocked-site/
    │   ├── no-schema-site/
    │   ├── stale-site/
    │   └── dead-end-site/
    └── golden/                        # Expected root causes for each fixture
```

The audit writes `out/` at run time. `.gitignore` excludes that directory.

---

## 5. Detection Design

### 5.1 Low False Alarms

Six correct findings are worth more than 30 uncertain ones. Five rules produce that result:

| Rule | Effect |
|---|---|
| Each check states when it does not apply | The report appendix gives the reason. Silence is not a result |
| Expectations follow the page type | The audit never asks for Product markup from a site that sells nothing |
| Each limit is a number with a reason | 300 characters, because a shorter page holds no quotable fact |
| A deliberate choice is not a fault | A block on AI training crawlers is information, not a problem |
| One test site has no faults | `good-site` must produce zero findings. It does |

> **The zero-findings rule found seven real bugs.** Each one also fires on a normal,
> well-built site. The worst compared prices as text, so `480.00` in the markup and `$480`
> on the page were reported as a contradiction.

### 5.2 How We Know the Checks Work

Three methods, because each one answers a question the others cannot.

**We built the checks from a field study, not from opinion.** 36 sites, six categories,
matched in pairs: three brands an assistant names readily against three real competitors
selling a comparable product at a comparable price that it does not. Pairing within a
category is the point — comparing an encyclopedia against a design agency measures fame, not
anything a brand controls. The study is in
`skills/audit-orchestrator/references/cited-vs-uncited-study.md`, results and failures both.
It moved two thresholds and got nine checks deleted for firing on everything.

**We break one thing at a time to prove each check is specific.** `tests/mutations.py` takes
the clean fixture, breaks exactly one named property, and asserts the audit reports that
property **and nothing else**. Thirty-seven cases. Site samples cannot do this: on a real
broken site twenty things are wrong at once, so a check can look correct by coincidence.
Controlling the cause is the only way to tell detection from correlation, and it found eight
defects the fixture suite had never touched — including one check that was documented,
listed in every report, and had no implementation behind it.

**We check that our own advice works.** `tests/test_fixes_work.py` breaks the site, reads the
finding, pastes the code the report handed the user into the exact pages it named, runs again,
and requires the finding to be gone. Ten cases, covering Organization, Article, FAQPage and
WebSite markup, `robots.txt` and `sitemap.xml`. Five fixes are prose — "write one sentence
saying what your brand is" — and the file lists them as un-machine-checkable rather than
letting their absence imply coverage.

**We refuse to ship a claim we have not tested.** `tests/test_coverage.py` requires every root
cause in the vocabulary to have a case that produces it, and a companion test fails if any
finding hands out code with nothing proving the code works. Two causes are exempt with the
reason recorded: one needs a TLS origin, one needs a real Wikidata name collision.

**We held out three samples and wrote the prediction down first.** The 36 study sites are
training data: every threshold moved after looking at them. So we drew fresh samples in
categories nobody had touched — 24, then 32, then 40 sites, spanning law firms, hospitals,
government, museums, banks, storefronts, documentation, podcasts, open source and four
languages — recorded the expected numbers in the study file *before* each first crawl, and ran
once.

The first two returned **1.81 serious findings per site each**, on samples sharing no
categories with one another or with the training set. Predictions that missed are reported
alongside the ones that held: crawlability was over-predicted twice, the predicted top-three
findings were one-for-three, and an expectation that non-English sites would break the prose
checks turned out wrong.

No threshold has ever been moved on holdout evidence. Where a holdout pointed at a defect, the
fix had to be demonstrable on its own — as a mutation case that never touches those sites —
before it was made.

### 5.3 Example Finding

This text comes from a real run against `tests/fixtures/js-shell-site`:

> ### F-001 — The homepage is delivered as an empty JavaScript shell
>
> **Priority medium** · confidence medium · mechanism C · found by render-readability-audit
>
> **What we found.** `https://example.com/`: framework root `#__next` contains 0 chars;
> content held in `__NEXT_DATA__`, `__INITIAL_STATE__` rather than in HTML; `<noscript>`
> tells the visitor to enable JavaScript.
>
> **Why it matters.** Mechanism C: a page that looks complete to a human can be empty to a
> machine. Many crawlers and assistant fetchers read only the first HTML response, so an
> empty shell means the brand has no homepage at all.
>
> **What to do.** Server-render the homepage, or pre-render it to static HTML at build time.
> Who does it: developer. Roughly: several days of development time.

The report also simulates citations. For each important page it gives the sentence that an
assistant will most probably quote. If no sentence is suitable, it prints `Nothing
quotable`. On this test site every page gives that answer.

---

## 6. Setup & Installation

### Prerequisites

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.10+ | Runs every script |
| `requests` | 2.32+ | HTTP client |
| `beautifulsoup4` | 4.12+ | HTML parsing |
| `lxml` | 5.3+ | Parser back end |
| `pytest` | 8.3+ | Development only |
| `playwright` | 1.49+ | Optional. Adds the rendered-page comparison |
| `skills-ref` | any | Optional. Validates each skill against the official specification |

### Step 1 — Install the Python dependencies

```bash
cd brand-ai-readiness-audit
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

# Verify
python -c "import requests, bs4, lxml; print('Dependencies OK')"
```

> **Windows path limit.** Do not unzip this marketplace into a directory with a long
> path. `pip` cannot install `lxml` if the total path is longer than 260 characters. Use a
> short path such as `C:\audit`, or turn on long path support in Windows.

### Step 2 — Validate the marketplace

```bash
python skills/audit-orchestrator/scripts/validate_marketplace.py
```

This checks the manifest, every skill directory, and every `SKILL.md` frontmatter field. It
exits with a non-zero code if it finds a problem.

### Step 3 — Run the tests

```bash
pip install pytest
python -m pytest tests/ -q
```

242 tests, about nine minutes. The suite starts a local web server, audits six test sites, and
checks the results.

Most of the time is the mutation suite: 43 cases that each take a copy of the clean fixture,
break exactly one property, and assert the audit reports that property **and nothing else**.
It is what tells us a check detects its own cause rather than correlating with it, and it runs
alongside ten tests that paste the code a report hands the user and require the finding to go
away. Skip both while iterating and it drops to about 90 seconds:

```bash
python -m pytest tests/ -q -m "not mutation"
```

### Step 4 — Optional: install the official validator

```bash
pip install skills-ref
python -c "
import skills_ref, pathlib
for d in sorted(pathlib.Path('skills').iterdir()):
    skills_ref.validate(str(d)); print('PASS', d.name)
"
```

### Step 5 — Optional: install Playwright

Playwright adds a rendered-page comparison. It measures the text that JavaScript adds.

```bash
pip install playwright
playwright install chromium
```

> ℹ️ Playwright is optional. If it is absent, the audit says so in the report appendix. The
> report never presents its absence as a fault of the audited site.

---

## 7. Command Reference

### `run_audit.py`

| Option | Default | Description |
|---|---|---|
| `target` | required | Site URL or host name, for example `example.com` |
| `--out-dir` | `out` | Directory for the snapshot and the report |
| `--format` | `md` | `md`, `html` or `both`. `report.json` is always written |
| `--render` | off | Add a Playwright pass, if Playwright is installed |
| `--no-network` | off | Make no extra requests after the crawl |
| `--budget` | `240` | Wall-clock seconds for the crawl |
| `--max-pages` | `30` | Maximum pages to read |
| `--now` | today | Reference date for the freshness checks, as `YYYY-MM-DD` |
| `--keep-intermediates` | off | Keep the six result files from the sub-skills |

### `package.py`

| Option | Default | Description |
|---|---|---|
| `--out` | `brand-ai-readiness-audit.zip` | Path of the zip to write |
| `--check` | off | Validate the marketplace and run the tests before the build |

### `check.py` (each sub-skill)

| Option | Description |
|---|---|
| `--snapshot` | Path to `snapshot.json` from the crawl |
| `--out` | Path for the result file |
| `--no-network` | Make no extra requests |
| `--now` | `freshness-corroboration-audit` only. Reference date |

---

## 8. Running the Audit

### Audit a website

```bash
source .venv/bin/activate

python run_audit.py https://example.com
# Writes out/report.json and out/report.md
```

### Audit a website and send the report to a colleague

```bash
python run_audit.py https://example.com --format both
# Also writes out/report.html
```

### Run each step separately

The runner is a convenience. These commands do the same work:

```bash
S=skills/audit-orchestrator/scripts

python $S/crawl.py https://example.com --out out/snapshot.json

for skill in crawl-access-audit render-readability-audit structured-data-audit \
             fact-extractability-audit freshness-corroboration-audit engagement-audit; do
    python skills/$skill/scripts/check.py \
        --snapshot out/snapshot.json --out out/$skill.findings.json
done

python $S/compose_report.py --snapshot out/snapshot.json \
    --findings out/*.findings.json \
    --out-json out/report.json --out-md out/report.md
```

### Audit one of the test sites

```bash
python -c "
import sys, time; sys.path.insert(0, 'tests')
from fixture_server import FixtureServer
s = FixtureServer('tests/fixtures/js-shell-site').__enter__()
print(s.base_url); time.sleep(600)
"
# Then, in a second terminal:
python run_audit.py <the printed URL>
```

### Build the submission zip

```bash
python package.py --check
```

---

## 9. Notes & Caveats

- **Thirty pages is a sample on a large site.** The report states how many pages it read,
  and every finding describes only those pages.
- **Static-first by default.** Without Playwright the JavaScript gap is inferred from the
  delivered HTML rather than measured, so the homepage-shell finding drops to medium
  confidence instead of claiming certainty it does not have.
- **Nothing behind a login is visible.** The audit never authenticates.
- **Name conflicts are checked against Wikidata only.** That tells you how many entities
  share the brand's name, not which one an assistant currently prefers.
- **Engagement is read from markup, not analytics.** It finds the structural reasons a
  visitor would leave. It cannot tell you that one did.
- **Heuristics are labelled as heuristics.** Every finding carries a confidence level, and
  the uncertain ones say so rather than being dressed up.
- **A robots.txt block stops the run.** If the file refuses this crawler the audit reads
  nothing, and the verdict explains why instead of presenting an empty report as a result.
- **`out/` is not tracked.** `.gitignore` covers the snapshot and the reports.
- **Every `SKILL.md` states its checks in prose.** An agent without Python can work through
  them by hand and reach the same findings; the scripts are the executable form of the same
  logic, not a different method.

---

<div align="center">
  <sub>Adobe University Hackathon 2026 · Round 3 · Brand AI-Readiness Audit</sub>
</div>
