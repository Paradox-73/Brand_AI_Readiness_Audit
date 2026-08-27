# Severity, confidence, effort and priority

Two different questions get two different answers, and conflating them is why most audit
reports are hard to act on:

- **Severity** — how bad is this, on its own terms?
- **Priority** — what should be done first?

A critical problem affecting one obscure page can be worth less attention than a trivial fix
that improves every page on the site. Severity alone cannot express that, so the report
carries both.

---

## Severity

Assigned by the sub-skill that found the problem, from the observation alone.

| Severity | Weight | Means | Example |
|---|---|---|---|
| `critical` | 4 | A gate is shut. Nothing downstream can compensate. | robots.txt disallows all crawlers; homepage is an empty JS shell; WAF blocks answer crawlers |
| `high` | 3 | Substantial loss of visibility, accuracy or visitors. | No Organization markup anywhere; no quotable definition of the brand; markup contradicts the page |
| `medium` | 2 | A real improvement worth scheduling. | Missing schema properties; sections that never answer first; stale content |
| `low` | 1 | Hygiene. Cheap, small individual gain. | Missing `lang`; long redirect chains; over-long forms |
| `info` | 0 | Observed and deliberately not a defect. | Training crawlers blocked — a rights decision, not a discoverability problem |

`info` exists so the audit can show its reasoning without inflating the counts. It is
reported, counted separately, and excluded from "Start here".

---

## Confidence

Independent of severity: how sure is the audit that this observation is correct?

- **high** — a direct, unambiguous reading. robots.txt says `Disallow: /`. The page returns
  404. The JSON-LD failed to parse and here is the error.
- **medium** — a strong inference from several signals, but interpretation is involved. A
  page is judged a JavaScript shell from two or more independent signals; markup contradicts
  the visible text on a page showing several prices.
- **low** — a heuristic that a human should sanity-check.

Confidence is never used to suppress a finding. It is printed beside it, so a reader can
weigh a `medium`-confidence item accordingly rather than discovering later that a number was
softer than it looked.

---

## Effort

The realistic cost of the fix, which divides the priority score.

| Effort | Divisor | Time | Typically |
|---|---|---|---|
| `low` | 1.0 | under an hour | Edit a template, add a tag, change a robots.txt line |
| `medium` | 1.5 | half a day to a day | Rewrite page copy, add markup across a template, audit third-party tags |
| `high` | 2.0 | several days of development | Enable server-side rendering, restructure content, fix a redirect estate |

Every finding also names an **owner** — developer, content owner or marketing — because "who
does this" is the first question a marketing manager asks and most audits never answer it.

---

## Priority

```
priority_score = severity_weight × reach ÷ effort_divisor
```

**Reach** is the share of crawled pages a finding affects:

```
reach = max(0.25, min(1.0, affected_page_count / pages_crawled))
```

- The **0.25 floor** stops a critical problem on a single page being buried under cosmetic
  issues that happen to appear everywhere.
- Findings with **no attributed pages get full reach (1.0)**. These are site-wide by
  construction — robots.txt, entity ambiguity, boilerplate inconsistency — and a skill leaves
  `affected_pages` empty precisely because the problem is not located on particular pages.

The score maps to a label for the report:

| Score | Label |
|---|---|
| ≥ 2.0 | critical |
| ≥ 1.0 | high |
| ≥ 0.45 | medium |
| < 0.45 | low |

Both are emitted: `suggested_action.priority` is the label (the required schema field, a
string) and `suggested_action.priority_score` is the number, so a machine can sort exactly
and a person can read it.

### Worked examples

| Finding | Severity | Pages | Effort | Score | Label |
|---|---|---|---|---|---|
| robots.txt blocks 4 answer crawlers | high (3) | site-wide (1.0) | low (1.0) | 3.00 | critical |
| Homepage is a JS shell | critical (4) | 1 of 6 (0.25 floor) | high (2.0) | 0.50 | medium |
| No Organization markup | high (3) | 4 of 4 (1.0) | low (1.0) | 3.00 | critical |
| One page missing `lang` | low (1) | 1 of 12 (0.25 floor) | low (1.0) | 0.25 | low |

The second row is the interesting one, and it is working as intended. An empty homepage is
**critical severity** — it is genuinely as bad as this audit gets. But it is one page and
several days of development, so among things to do *this week* it ranks below a robots.txt
line that takes a minute and unblocks the whole site. The report shows both numbers so
neither reading is lost: the severity section still lists it under Critical, and "Start here"
sends you to the cheap unblock first.

---

## Ordering and stable IDs

Two different orderings, deliberately:

- **IDs** are assigned by sorting on `(severity_rank, mechanism_letter, id_hint)`, then
  numbering `F-001` upward. Not by priority — priority depends on how many pages the crawl
  happened to reach, so ID-by-priority would mean `F-003` referring to a different problem
  after a crawl that saw two more pages. Sorting by severity and a stable slug keeps a
  finding's ID meaningful between runs.
- **Display** within each severity group is by priority score, descending.

Nothing inside a finding carries a timestamp; `audited_at` exists once, at the report root.
That is what makes the determinism test possible: the same snapshot must produce
byte-identical `findings[]` on every run.

---

## Deduplication

Two skills can legitimately notice one problem from different angles. Reporting it twice
inflates the counts and reads as padding, so `compose_report.py` merges findings that share
`(mechanism, root_cause, affected_pages)` — the earlier skill in gate order keeps the finding
and the later one's evidence is appended.

Merging happens **only across skills**. A single skill emitting several findings that share a
root cause is doing so deliberately: robots.txt can block answer crawlers, block training
crawlers, and disallow content paths, and those are three separate decisions with three
separate fixes. Every merge is listed in the report's appendix so nothing disappears quietly.
