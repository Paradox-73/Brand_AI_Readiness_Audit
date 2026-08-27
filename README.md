# brand-ai-readiness-audit

This marketplace audits a website for two problems at the same time:

- **Off-site discoverability.** AI assistants cannot find, read, trust or correctly
  describe the brand.
- **On-site engagement.** Visitors who arrive from an AI answer leave again.

The audit crawls the site one time. Six specialist skills read that one crawl. The
entrypoint then writes one report. The report lists the problems, gives the evidence, and
gives the fix for each one. It also recommends improvements where the audit found no fault.

**The audit only reads.** It does not write to a site, sign in to a site, or send many
requests to a site. It uses the GET and HEAD methods only. It obeys robots.txt. It does not
send forms.

```bash
pip install -r requirements.txt
python run_audit.py https://example.com
# The audit writes out/report.json and out/report.md
```

Documentation in this repository follows Simplified Technical English (ASD-STE100). Read
`PROGRESS.md` for the current status, and `DECISIONS.md` for the decisions we made.

---

## The idea

A machine can see a page only if three gates open, in this order:

1. The crawler **gets in**.
2. The crawler **reads** what the server sends.
3. The crawler **finds the fact** that it needs.

Most audits examine the first gate only. This marketplace examines all three. It then asks
a question that few audits ask. If an assistant reads this page to answer "what is X" or
"how much does X cost", is there a sentence that it can quote? Last, it examines the person
who opens the page. Each check refers to a numbered mechanism. Each finding names the
mechanism that its fix repairs.

## The six skills

The entrypoint controls the crawl, the scoring and the recommendations. Each sub-skill
examines one gate. No sub-skill repeats the checks of another sub-skill.

| Skill | Gate | What it examines |
|---|---|---|
| **`crawl-access-audit`** | A: gets in | robots.txt, blocked AI crawlers, bot managers, sitemaps, status codes, `noindex`, canonical tags |
| **`render-readability-audit`** | A/C: reads | JavaScript shells, facts inside images, PDFs, video or iframes, catalogues behind a button |
| **`structured-data-audit`** | C: machine-readable facts | JSON-LD presence, validity and completeness for each page type, and agreement with the page |
| **`fact-extractability-audit`** | C: quotable facts | The one-sentence definition of the brand, answer-first sections, price, address, contact and founding facts |
| **`freshness-corroboration-audit`** | D: trusted | Old content, missing dates, off-site profiles, name conflicts, and pages that disagree with each other |
| **`engagement-audit`** | G: visitor stays | Orientation, dead ends, orphan pages, broken links, breadcrumbs, interstitials, form length |

Two skills examine gate A. This is deliberate. "The server refused the crawler" and "the
server sent an empty page" are different faults. They have different fixes, and different
people correct them.

## How the entrypoint joins the skills

```
run_audit.py
  |
  |- 1. crawl.py  ------------->  snapshot.json     one crawl for all six skills
  |      robots.txt, sitemaps, /llms.txt, the homepage,
  |      8 sitemap pages, then links to depth 2
  |      Limits: 30 pages, 240 s, 10 s per request, 0.5 s between requests
  |
  |- 2. six x check.py --snapshot snapshot.json --out <skill>.findings.json
  |      They run in gate order. An earlier skill owns a shared observation.
  |
  |- 3. compose_report.py  ---->  report.json and report.md
         It merges duplicates, sets IDs, scores priority, and adds recommendations.
```

Three things make this one system, and not six separate scripts:

1. **One crawl.** Each sub-skill reads fields from the snapshot. It does not read the site
   again. A few skills make a small number of extra requests. Each one declares that number
   and reports it.
2. **One vocabulary.** One finding format, one severity scale, and one fixed set of about
   45 `root_cause` tags. `audit_common.py` defines them one time. A skill that invents a tag
   fails immediately.
3. **Ordered merge.** Two skills can see the same fault. The earlier skill keeps the
   finding. The report lists each merge, so nothing disappears.

## How the report sets priority

```
priority = severity weight x reach / effort
```

Severity alone is not sufficient. A serious fault on one page can matter less than a small
fix that improves every page. Reach is the share of crawled pages that the finding affects.
Effort divides the score. The three highest scores become the **Start here** section.

`skills/audit-orchestrator/references/severity-and-priority.md` explains the numbers.

## We designed the audit to report few false alarms

Six correct findings are worth more than 30 uncertain ones. Therefore:

- **Each check states when it does not apply.** The report appendix gives the reason. "This
  site has no product pages, so Product markup is not expected" is a result, not silence.
- **Expectations follow the page type.** The audit never asks for Product markup from a site
  that sells nothing. It never asks for an author on a pricing page.
- **Each limit is a number with a reason.** 300 characters, because a shorter page holds no
  quotable fact. 18 months, because "recent" is not correct after that time.
- **A deliberate choice is not a fault.** A site can block AI training crawlers. The report
  records this as information. A cookie banner alone is not an interruption. An empty `alt`
  on a decorative image is correct.
- **`tests/fixtures/good-site` proves this.** That site has no faults, and it must produce
  zero findings. It does. This test found seven real bugs.

## Example from a report

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

The report also simulates citations. For each important page, it gives the sentence that an
assistant will most probably quote. If no sentence is suitable, it says `Nothing quotable`.
On this test site, every page gives that answer. This makes the problem clear.

## What the audit writes

`report.json` contains the required fields: `site`, `audited_at`, `summary` and `findings`.
It adds these fields: `confidence`, `mechanism`, `root_cause`, `affected_pages`, `reach`,
`detected_by`, and the fix details. It also adds `recommendations`, `citation_simulation`,
`start_here`, `crawl`, `checks_run` and `not_applicable`.

`report.md` is for a marketing manager, not an engineer. Each fix names the person who
usually does the work. It also gives the approximate time.

## How to run the audit

```bash
python run_audit.py example.com                  # Write report.md and report.json to ./out
python run_audit.py example.com --format html    # Also write a report to send to a colleague
python run_audit.py example.com --render         # Add a Playwright pass
python run_audit.py example.com --no-network     # Make no extra requests
python run_audit.py example.com --now 2026-01-31 # Set the date for the freshness checks
```

You can also run each step: `crawl.py`, then each `check.py`, then `compose_report.py`.

Each SKILL.md also gives its checks in words. An agent without Python can do the same checks
by hand. It will find the same problems.

To check the marketplace and the tests:

```bash
python skills/audit-orchestrator/scripts/validate_marketplace.py
python -m pytest tests/ -q
```

To build the submission zip:

```bash
python package.py
```

## What you need

Python 3.10 or later, `requests`, `beautifulsoup4` and `lxml`. Playwright is optional. The
audit detects Playwright. If Playwright is absent, the report says so. The report never
presents this as a fault of the site.

**Note for Windows.** Do not unzip this marketplace into a directory with a long path.
`pip` cannot install `lxml` if the total path is longer than 260 characters. Use a short
path such as `C:\audit`, or turn on long path support in Windows.

## Limits

- **The crawler reads 30 pages.** On a large site this is a sample. The report gives the
  number of pages. The findings describe those pages.
- **The audit reads the HTML that the server sends.** Without Playwright it calculates the
  JavaScript gap. It does not measure the gap.
- **The audit does not sign in.** It cannot see pages behind a login.
- **The audit checks name conflicts with Wikidata only.** It counts the entities that share
  a name. It cannot tell you which entity an assistant prefers.
- **The audit reads markup, not analytics.** It finds the reasons that a visitor leaves. It
  cannot tell you that a visitor left.
- **The checks use rules of thumb.** Each finding gives a confidence level. The report marks
  the uncertain findings.

## Licence

MIT. Read `LICENSE`.
