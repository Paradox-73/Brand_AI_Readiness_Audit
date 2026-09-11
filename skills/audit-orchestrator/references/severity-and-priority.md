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

### How the evidence was obtained decides which tier a finding is published in

Confidence used only to be printed beside a finding, on the theory that a reader would weigh
it. A validation pass over six unseen sites disproved that. It produced 92 findings, 41 of
them at `medium` confidence; an independent review, checking every finding against the live
page, marked 6 wrong and 11 overstated, so 22% of what was published was defective, and the single wrongest finding on one site was ranked first in
"Start here". Printing "confidence medium" in small type next to a finding does not stop
anybody acting on it.

Splitting the report on `confidence` fixed that and fixed nothing else. A later validation
pass over eight unseen sites scored **83.8% correct overall, 84.6% in the confident tier and 82.9% in
the lower one** — a split honest in construction that separated no accuracy at all. The
reason: `confidence` is a value each check writes about itself, so ordering by it orders by
how bold fourteen check authors felt. All three of the worst errors sat in the confident tier
and two plainly-true findings sat below them.

What told those five apart is the kind of evidence, and that is derivable rather than
authored. Each of the three wrong ones rests on this audit having identified something and
been wrong about what it was — whose account a URL is, which string is a street address,
which pages are product pages. Neither demoted truth identified anything: a postal address
and a founding year were absent from every page read and from the markup property that would
hold them, which is a count of zero.

So every finding carries `evidence_basis`, from `audit_common.EVIDENCE_BASIS`:

| Basis | The test | Examples |
|---|---|---|
| `observed` | The claim is settled by what the document or the HTTP exchange literally contains. A reader can confirm or refute it in View Source without agreeing with this audit about anything. | a status code, an absent `<title>`, a missing `lang` attribute, a duplicate title string, an absent JSON-LD block, a character count |
| `inferred` | The claim only holds if this audit correctly decided what something **is**. Every one of those decisions can be wrong about a document it read perfectly. | which pages are product pages, which URL is the brand's own account, which string is a street address, whether a sentence defines the brand |

One entry per root cause, and the test suite fails the build if a cause is added without one
or if the map names a cause that does not exist. Choosing the population counts as an
identification: "N of M product pages have no Product markup" rests on the page typing as
much as on the markup. A short override table keyed on `stable_id` handles the causes raised
by two checks that do not read the same kind of evidence — `no-org-schema` covers both "no
Organization block on any page" and a count of the pages a regular expression read a street
out of, and classifying the cause by its weaker half would demote one of the most reliably
true things this audit says.

The two gates together:

| Tier | Requires | Where it appears |
|---|---|---|
| `confident` | `observed`, **and** `high` confidence — or `medium` with at least `CORROBORATED_ABSENCE_SOURCES` independent sources in `checked[]` where the cause asserts a negative | The graded body: the severity sections, and the only pool "Start here" draws from |
| `worth checking` | everything else, including every `inferred` reading whatever its confidence, and every `low` one | Its own section, "Worth checking first", which tells the reader in plain words to verify it before acting |

Three things this is **not**:

- It is **not suppression**. A demoted finding stays in `findings[]`, stays in
  `summary.total_findings` and stays in the severity counts, and is written out in full with
  its evidence, its fix and its affected pages. The goal is few false positives *and* few
  misses; moving a finding down is not a miss, deleting it would be. The severity table in
  `report.md` gains a column showing exactly how many findings of each severity sit in each
  tier, so a reader can never find a `high` under "Worth checking" and conclude the table lied.
- It is **not a rule about severity**. Severity and certainty are different axes, and the
  high-severity, weakly-evidenced finding is the dangerous case precisely because it leads
  "Start here" while being the kind of reading most likely to be wrong. That is the case that
  did the most damage.
- It does **not overrule a check that calls its own reading `low`**. A check saying `low`
  knows something about that reading no map of causes can know. `medium` is different: it is
  where the two plainly-true findings were sitting, hard-coded by the check that raises them,
  and promoting a corroborated `medium` read straight off the document is what moves them.

Re-tiering fifteen real reports on disk moved 24 findings up and 26 down, and moved all five
of the findings above the right way. Two of the three errors had been leading "Start here" on
their own sites; neither is eligible now.

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

---

## The source floor, and the two causes exempt from it

The floor asks for a second place that came back empty, so it applies only where a second place
exists. `compose_report.SINGLE_PLACE_ABSENCES` exempts the two causes with one spelling each — a
page's H1 and its `lang` — after "37 of 43 crawled pages have no H1", true on every page opened
to check it, was published in the tier a reader is told to verify.

A meta description is not exempt: the same summary also lives in `og:description` and the Twitter
card.

`summary.confident` plus `summary.worth_checking` add up to `summary.total_findings`, and
`tests/test_tier_and_tailored_advice.py` pins the findings the rule was built from.

---

## "Start here", and who has to do it

Three fixes, in the order to do them, drawn from the confident tier — with one exception.

Where the verdict's paragraph names a root cause and says most of the rest follows from it, that
finding leads the list whichever tier it is in. `compose_report.verdict_diagnosis` is read by both,
so a report cannot name the main problem in its most-read sentence and leave it off the list of
what to do first, which it did on two of four sites in one validation pass.

Under the verdict, before any instruction, one line counts how many actions need somebody who
edits the files the site is built from rather than typing into an admin screen
(`summary.actions_needing_a_template_editor`). It was 7 to 9 of every 12 to 14 across four real
reports and no report said so — for an owner with no developer that is not an hour of work but a
person they do not have. `references/publishing-platform.md` is where that count comes from, and
where the fix steps get the name of the file or screen the reader has to open.

---

## One coverage rule, deciding both what may be asserted and what may be graded

`crawl.readable_pages` counts pages that answered 200, were not skipped or challenged, and carried
at least one sentence of text. `compose_report.enough_to_grade` asks two things of it: at least
`VERDICT_MIN_READABLE_PAGES` of them, and at least `READABLE_SHARE_FOR_ABSENCE` of the URLs that
could have been pages.

Fail either and the verdict states what the crawl did and did not establish instead of grading the
site, above the severity table in both documents. Where nothing readable came back, every check
that stayed quiet is echoed into `unverifiable[]` so its silence is not read as a pass.

**The count exists** because a verdict computed from severity counts alone twice told an owner "No
blocking or significant problems were found" on a run that read no text at all; `pages_crawled`
counted 1 on both.

**The share exists** because an absence claim is a claim about the pages that were *not* read as
much as the ones that were.

**They are one predicate on purpose:** when separate, a crawl of sixteen URLs with seven readable
held back eight true findings for want of coverage and printed "No blocking problems." in the same
report.

The denominator of that share is URLs the crawl went looking for a page at, so a response whose own
content type proved it was never a page is not one of them — counting nine such downloads as
refusals is what caused that suppression. When a claim is held back, the reason counts the causes
the crawl recorded (`skipped` and its reason, `challenge`, the status code, `undecoded_encoding`)
rather than naming a refusal nobody tested for.
