# Field study: what separates cited sites from ignored ones

The brief says to go and find real websites that assistants cite well versus ones they
ignore, and work out what makes the difference. This file is that work, including the
result we did not want.

**Headline: most of what this marketplace checks does not separate the two cohorts.** One
signal does. That finding shaped how the report is framed, and it is why the proactive
recommendations are not an afterthought.

---

## Method

Two cohorts, nine sites, audited with the marketplace at 15 pages each.

**Cohort A — reference-grade sites that assistants cite constantly.** Sites that appear as
sources across many topics and many assistants: a developer documentation set, an
encyclopedia, a national health service, a finance reference.

**Cohort B — ordinary brand sites.** The kind that go unnamed in answers about their own
category: two design agencies, a consultancy, a DTC retailer, a manufacturer, an SMB SaaS
product, a high-street services chain.

Cohort membership was assigned by role and reputation, not measured. We did not query an
assistant to confirm citation rates — see *Limits* below. Site names are deliberately
absent from this repository.

Two of the cohort B sites returned zero crawlable pages (one 403 to any unrecognised agent,
one redirect loop) and are excluded from the averages. That is itself a finding: two of
seven ordinary brand sites were not readable by a polite, identified crawler at all.

---

## Result

| Signal | Cohort A (n=4) | Cohort B (n=5) | Separates? |
|---|---|---|---|
| **Date coverage** | **85%** | **35%** | **Yes** |
| **Median body text** | **12,899 chars** | **5,167 chars** | **Yes (2.5x)** |
| JSON-LD coverage | 73% | 80% | No — B is better |
| Meta description coverage | 75% | 99% | No — B is better |
| Quotable-sentence share | 69% | 86% | No — B is better |
| Breadcrumb coverage | 68% | 67% | No |
| H2 sections per page | 7.6 | 8.0 | No |
| Critical + high findings | 2.5 | 2.6 | **No — identical** |

Root-cause frequency told the same story. Not one of our ~45 root causes fired more often
in cohort B than cohort A by a margin worth reporting. Several ran backwards: the
heavily-cited sites carried *more* `stale-content`, `jargon-density`, `no-breadcrumbs` and
`alt-missing` findings than the brand sites. Three of four cohort A sites declared no
Organization markup at all; three of five cohort B sites did.

---

## What this means

### 1. Our checks measure a floor, not a ceiling

They detect what **prevents** citation: a blocked crawler, an empty JavaScript shell, a page
with no quotable sentence, markup that contradicts the page. They cannot manufacture what
**causes** citation: being the canonical source for a fact, breadth of topic coverage, and
corroboration from sources you do not control.

A brand that fixes everything this audit flags becomes *eligible* to be cited. It does not
thereby become an encyclopedia. Any tool claiming otherwise is selling something.

This is why `report.md` leads with a verdict rather than a score. A score would imply a
ranking the evidence does not support.

### 2. Ordinary brand sites already pass the hygiene bar

Cohort B beat cohort A on structured-data coverage, meta descriptions and quotable-sentence
share. The average count of critical and high findings was 2.5 versus 2.6 — statistically
indistinguishable.

The uncomfortable implication: run this marketplace on a typical competent brand site and
the defect list will be short and unremarkable. That is an accurate reading of the site, not
a failure of the audit — but it means **the recommendations, not the findings, are where the
upside is** for most sites. The catalogue is conditioned on observed signals precisely so
that it stays useful when the defect list is empty.

It is also the reason `tests/fixtures/good-site` produces zero findings and still receives
recommendations. That is the common case, not an edge case.

### 3. Dates are the exception, and they are cheap

Date coverage was the one hygiene signal that tracked the divide, and it is not close: 85%
against 35%. It is mechanism D observable in the wild — recency is one of the few quality
signals a machine can evaluate cheaply, and it decides which of two sources making the same
claim survives into an answer.

It is also the cheapest thing on the list to fix. A brand cannot become the canonical source
for its category this quarter. It can date its pages this week.

**Change made:** `R-DATE-SIGNALS` now fires when site-wide date coverage falls below 50%, not
only when dates are absent entirely, and the recommendation carries this evidence.
`freshness-corroboration-audit` emits `date_coverage` as a signal for that purpose.

### 4. Depth separates, but it is not a defect

Cohort A pages carried 2.5 times the body text. That is content strategy rather than a fault
we can flag, and a thin page is not broken. It appears in the catalogue as
`R-COMPARISON-PAGES` and `R-USE-CASE-PAGES` — coverage breadth rather than page length,
because the mechanism is that assistants answer the question as it was asked, and a brand
with one page for one framing is absent from every other framing.

We deliberately did **not** add a "your pages are too short" check. Length is a proxy for
substance, and a check that fires on short pages would penalise a well-written pricing page.

---

## What we did not change, and why

Two temptations were rejected.

**We did not reweight severities to make cohort B look worse.** The cohorts genuinely do
score alike on hygiene. Tuning thresholds until the "right" answer appeared would be fitting
to nine sites, which is the opposite of the generalisation the brief asks for.

**We did not add an authority or backlink check.** It would separate the cohorts perfectly
and be useless: no site owner can act on "be more famous", and the brief explicitly rules
out commercial levers. A check that a reader cannot act on is noise with extra steps.

---

## Limits

Say these out loud rather than letting a reader assume otherwise.

- **n = 9, of which two returned no pages.** This indicates a direction; it does not
  establish a threshold. The 50% date-coverage trigger is a judgement informed by the data,
  not derived from it.
- **Cohort membership was assigned, not measured.** We did not query ChatGPT, Gemini,
  Claude or Perplexity to confirm citation rates. Doing so properly needs a query set, an
  attribution method, and repeated sampling over time. That is a monitoring programme, and
  it is out of scope for a point-in-time audit.
- **Cohort A is authority-dominated.** Reference sites are cited partly for reasons no
  amount of markup will reproduce. A better cohort A would be mid-sized commercial sites
  that are demonstrably cited in their category — harder to identify without the
  measurement above.
- **15 pages per site.** On sites with millions of pages this is a keyhole.

The honest summary: this study is strong enough to have changed one recommendation and
reframed the report, and not strong enough to justify a scoring model.
