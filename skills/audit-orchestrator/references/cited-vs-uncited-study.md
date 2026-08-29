# Field study: what separates brands assistants name from brands they ignore

The brief says to go and find real websites that assistants cite well versus ones they
ignore, and work out what makes the difference. This is that work, including the results
that went against us.

Three checks in this marketplace were changed because of what follows, and two thresholds
are now measured numbers rather than reasoned ones.

---

## Method

**Within category, matched pairs.** Six categories. In each, three brands an assistant
readily names for a buying-intent question, against three real competitors in the same
category, selling comparable products at comparable prices, that it does not name.

| Category | Named | Not named |
|---|---|---|
| Trail running shoes | 3 | 3 |
| Standing desks | 3 | 3 |
| CRM for small business | 3 | 3 |
| Coffee subscriptions | 3 | 3 |
| Password managers | 3 | 3 |
| Mattresses | 3 | 3 |

36 sites, 15 pages each, audited with this marketplace. The prominence labels are our own
reading of each category, corroborated against live search results for buying-intent queries
in several of them. They are a judgement, not a measurement, and the Limits section at the
end says what that costs us.

**Why matched pairs.** An earlier version of this study compared an encyclopedia and a
national health service against design agencies. Everything separated, and none of it was
useful, because it was measuring fame. Running shoes against running shoes controls for
category, buyer intent and page structure, so what is left is closer to something a brand
can act on.

**The bar for changing a check.** A signal counts only if it moves the same direction in at
least five of the six categories. Pooled averages are reported but never acted on alone.

---

## Result 1 — Seven of 36 sites could not be crawled at all

19% returned nothing to a polite, identified, robots-respecting crawler. Both cohorts:
a major running-shoe brand, a well-known CRM, a password manager, a mattress brand.

Blocking crawlers does not stop a big brand being named — reputation carries it. It does
stop everyone else. This is gate A, and it is the cheapest catastrophic failure on the list.

---

## Result 2 — Off-site profile breadth separates, in every category

**The strongest signal in the study, and the only one that moved the same way in all six.**

| Category | Named | Not named |
|---|---|---|
| CRM | 12.5 | 7.0 |
| Trail shoes | 8.0 | 5.0 |
| Mattress | 7.7 | 5.0 |
| Password manager | 6.0 | 3.5 |
| Standing desk | 6.0 | 5.5 |
| Coffee | 5.0 | 2.7 |
| **Mean** | **7.2** | **4.6** |

Breadth, not prestige. The gap is spread across ordinary platforms — Instagram, YouTube, X,
Facebook, LinkedIn, Pinterest — not concentrated in Wikipedia or Crunchbase. Named brands
were on more places, not better ones.

**Change made.** The old check counted nine hand-picked "authoritative" platforms and
required three. It fired on 94% of named brands and 100% of unnamed ones, so it separated
nothing and carried no information. It now measures breadth across every recognised
platform, passes at 6 or more, and reports the observed benchmark in its evidence.
`R-SAMEAS-WIKIDATA` is keyed to the same number.

This is the most actionable finding in the study. A brand cannot become famous this quarter.
It can claim six profiles this week and put the same sentence on all of them.

---

## Result 3 — Date coverage separates, but the earlier threshold was wrong

| Category | Named | Not named |
|---|---|---|
| Trail shoes | 0.83 | 0.10 |
| Standing desk | 0.43 | 0.13 |
| Mattress | 0.20 | 0.03 |
| Coffee | 0.11 | 0.04 |
| Password manager | 0.39 | 0.37 |
| CRM | 0.20 | 0.40 |
| **Mean** | **0.34** | **0.17** |

Five of six categories, and roughly double overall. But note the absolute numbers: named
brands date only about a third of their pages.

**Change made.** The earlier flawed study put the trigger at 50% coverage, taken from
encyclopedias that date 85% of pages. Against real brands that bar would have fired on most
of the ones assistants *do* name. `R-DATE-SIGNALS` now triggers below **25%**, which sits
between the two observed means.

---

## Result 4 — Structured data coverage runs backwards

| Category | Named | Not named |
|---|---|---|
| Coffee | 0.07 | 0.67 |
| CRM | 0.33 | 0.73 |
| Password manager | 0.05 | 0.23 |
| Standing desk | 0.52 | 1.00 |
| Trail shoes | 0.73 | 1.00 |
| Mattress | 1.00 | 0.03 |
| **Mean** | **0.44** | **0.62** |

Brands assistants ignore carry **more** JSON-LD than brands they name, in five of six
categories. 62% of the named brands had no Organization markup anywhere.

The likely reading: structured data is what a brand adds when it is trying to be found.
Brands that are already found have less need of it and often skip it.

**Change made.** `no-org-schema` and `no-product-schema` drop from `high` to `medium`.
Absence is still worth fixing — markup is the one place identity is stated as data rather
than inferred from prose — but calling it `high` asserts more than the evidence supports.

**Deliberately not changed.** `invalid-jsonld` and `schema-text-mismatch` stay at `high`.
Mismatch was one of the few root causes that leaned the right way (23% of unnamed brands
against 12% of named). Correctness of markup matters even where quantity does not, which is
a sharper claim than "add more schema" and a better one.

---

## Result 5 — Noise, and what removing it did

The first run flagged an average of 15.4 findings on brands assistants name and 13.5 on
brands they ignore. Our audit found *more* problems on the brands that were working.

That was not an awkward truth about the world. It was our own noise. Fourteen checks fired
on more than 40% of every site audited, and several fired *more often on the named brands* —
the reliable signature of a check measuring "is a website" rather than "is broken".

Nine were corrected. The bugs were more instructive than the thresholds:

| Problem | What it did | Fix |
|---|---|---|
| Navigation selector | Read a promotional banner as a whole site's menu, then reported "the primary navigation has 1 item" | Skip when the menu cannot be isolated, and say so |
| Navigation upper bound | Flagged a 29-item megamenu. Every retailer has one | Bound removed; only "fewer than 3" survives |
| Homepage fallback | Judged a blog post's navigation when no homepage was crawled | Requires an actual homepage |
| `www` duplication | Crawled a site with and without the `www.` prefix as two pages, then reported duplicate titles | One page, one crawl |
| Section detection | Counted 57 product tiles as prose sections and complained none opened with a fact | A section needs real paragraph text |
| Heading structure | Flagged multiple H1s and skipped levels — valid HTML that machines read fine | Only a page with no heading at all |
| Jargon density | 25% of sentences over 30 words is ordinary technical prose | Raised to 40% |
| Meta hygiene | Flagged one missing description on a 7-page crawl; called 75-char titles long | Needs 25% of pages; range widened to 90 |
| Entity definition severity | `high` on 61% of sites, including ones assistants name constantly | `high` only when the brand is never named at all |

Result across four full re-runs of all 36 sites:

| Measure | Before | After |
|---|---|---|
| Critical + high, named brands | 2.31 | **1.25** |
| Critical + high, unnamed brands | 2.46 | **1.15** |
| `weak-corroboration` fires on | 97% of sites | 55%, and now correctly more often on unnamed (10) than named (6) |
| `no-orientation` fires on | 93% | 41% |
| `heading-structure` fires on | 93% | 48% |
| `robots-block` fires on | 86% | 55% |

Two things to take from this. A check that fires on almost every site carries no information
however sound its reasoning, and the fastest way to find one is to look for checks that fire
*more* on the sites that are working. And the finding count in a report is a work list, not
a grade — `report.md` opens with a plain-language verdict and never a score.

## Result 6 — Re-enabling four dead checks, and what it cost

A later pass found that four checks could never fire: shell detection required heavy inline
script, so a framework build with only external bundles scored nothing; the dead-end check
required no call to action but scanned the whole document, so a footer link counted as a
page's next step; the visible-breadcrumb check read the same field as the markup check; and
cross-page contact details were never compared at all. Fixing those turns four silent checks
into loud ones at once, which is exactly the change most likely to make reports worse.

So the cohort was re-run twice: once immediately after the fixes, once after correcting what
the first re-run exposed.

| | Original | Checks re-enabled | After correction |
|---|---|---|---|
| Critical + high per site | 1.21 | 1.30 | **1.17** |
| Total findings per site | 11.8 | 12.1 | **12.0** |
| Highest-firing check | 66% | 67% | **62%** |

Report length did not move. What moved is what is behind it: four checks that reported
nothing now report something, and two findings that were filed under the wrong name are filed
under the right one.

**Three things the middle column hid.**

`dead-end` went from 38% to 67% of sites. The threshold said "fewer than three onward links",
which is not what a dead end is. A page offering one relevant link is not a cul-de-sac, and a
contact page whose entire purpose is its form was being reported as offering nothing to do.
Now it means no internal link in the main content, no call to action and no form — 17%.

`nap-inconsistency` went from 7% to 23%, and the evidence did not survive reading. One site
"published five telephone numbers": `0235240`, `2003003`, `5055055`, `5250252`. Those are not
telephone numbers. Any long run of digits looks like one to a regular expression. Narrowing
to `tel:` links removed the junk and introduced a different error — a site with a sales line,
a support line and a returns line was reported as contradicting itself. The comparison now
runs on Organization markup only: a site may publish as many numbers as it has departments,
but it may not declare two different primary numbers for itself. That fires on 0 of 36, and
the 6% that remains is the original same-page schema-versus-text check, whose two cases both
hold up.

"Three pages do not carry the site's normal navigation" was being reported as `dead-end`.
Missing header and footer is not a dead end — the page has plenty of links, they are the
wrong ones. Different symptom, different template, so `inconsistent-chrome` now.

**Why this is here rather than in a changelog.** Every fix in this section was found by
measuring, not by review. Two of the three had already passed a full test suite and a reading
by the person who wrote them. The lesson is not that we were careless; it is that a check
firing on two thirds of real sites looks completely reasonable in isolation and is obvious the
moment you count.

---

## What we did not do

**We did not reweight severities until cohort B looked worse.** On hygiene the two cohorts
genuinely score alike. Tuning until the desired answer appeared would be fitting to 29 sites,
which is the opposite of the generalisation the brief asks for.

**We did not add an authority or backlink check.** It would separate the cohorts almost
perfectly and be useless: nobody can act on "be more famous", and the brief rules out
commercial levers.

**We did not delete the structured-data skill** on the strength of one inverse result. The
mechanism argument — explicit beats implied — still holds, the correctness checks within it
lean the right way, and 29 sites is not enough to justify removing a gate.

---

## What this means for the product

These checks measure a **floor, not a ceiling**. They detect what *prevents* citation: a
blocked crawler, an empty shell, a page with no quotable sentence, markup that contradicts
the page. They cannot manufacture what *causes* it — being the canonical source, breadth of
coverage, corroboration you do not control.

A brand that fixes everything flagged here becomes eligible to be cited. It does not thereby
become the source everyone quotes. Two of the levers that *did* separate the cohorts — profile
breadth and dating discipline — are now the two best-evidenced recommendations in the
catalogue, and both are cheap.

---

## Limits

- **29 crawlable sites, 6 categories.** Enough to establish direction and move two
  thresholds. Not enough to fit a model.
- **Labels are a prominence judgement, not a measurement.** Corroborated against live search
  for several categories, but no assistant was queried systematically. Doing that properly
  needs a query set, an attribution method and repeated sampling over time — a monitoring
  programme, not a point-in-time audit.
- **15 pages per site.** On a large retailer this is a keyhole, and date coverage in
  particular will be sensitive to which pages the crawl reached.
- **Prominence is not causation.** Named brands may link more profiles because they are
  larger, not be larger because they link more profiles. The recommendation stands on the
  mechanism as well as the correlation — independent sources repeating the same description
  is what corroboration means — but the study alone cannot separate the two.

---

## The holdout, and why the sample cannot be reused

Every threshold in this marketplace was moved after looking at these 36 sites. That makes the
36 a **training set**. Re-running the audit over them and reporting the improvement measures
how well we fitted the sample, not how the audit behaves on a site nobody has seen — and the
temptation to keep tuning until the numbers look good is exactly how a tool ends up
describing its own test data.

So the sample is now closed. No threshold moves on evidence drawn from these sites again.

### Protocol

Anyone can run this and it takes about half an hour of machine time.

1. **Draw 24 sites in six categories nobody has touched.** Not the six here. The point is to
   change the buyer intent, the page shapes and the platforms at once — a law firm, a hospital
   trust, a university department, a local restaurant group, a developer-tools company, a
   charity. Four per category, chosen before anything is audited.
2. **Write down the prediction first**, in the file, before the first crawl: how many
   critical-or-high findings per site we expect, and which three root causes we expect to be
   most common. A prediction made after the run is not a prediction.
3. **Run the audit once per site.** One pass. No re-runs with adjusted settings.
4. **Compare against the prediction and stop.** Record the result whatever it is.

### What each outcome means

| Outcome | Reading |
|---|---|
| Findings per site within about one of the prediction | The thresholds generalise. Nothing to change. |
| Substantially more findings than predicted | Either the new categories really are worse, or a check is firing on a page shape we never built. Read the three most common findings and check them by hand before touching any threshold. |
| A single check firing on more than about 70% of the holdout | Treat as broken until proven otherwise. That was the signature of all nine checks removed in Result 5. |
| Findings ordered the wrong way inside a category | The check is measuring size or fame rather than the defect. |

### The rule that matters

If the holdout says a threshold is wrong, the honest move is to change it **and then draw a
fresh holdout**, because the old one has just become training data too. Tuning against the
same set twice is how the first version of this study produced a tool that found more problems
on well-cited brands than on ignored ones.

### What a holdout cannot tell you

It measures the audit against sites. It says nothing about whether a check detects the thing
it claims to detect, because on a real site the cause is never controlled — a page with no
Organization markup usually has ten other things wrong with it too. That question is answered
in `tests/mutations.py`, where the cause is introduced one at a time into a site that is
otherwise clean. The two methods are complements: the mutation suite proves the checks are
sound, the holdout proves the thresholds are not overfitted, and neither substitutes for the
other.
