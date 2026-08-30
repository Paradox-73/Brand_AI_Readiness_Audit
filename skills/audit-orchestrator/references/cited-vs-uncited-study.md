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

### Prediction, written 30 August 2026, before the first holdout page was fetched

Recorded here rather than in a working note, because a prediction that can be edited after
the run is not a prediction. The holdout is 24 sites across six categories none of the
thresholds were fitted on: law firms, hospitals, university departments, restaurant groups,
developer tools and charities. Institutional and content-led rather than direct-to-consumer,
which is the point — every threshold in this marketplace was set by looking at retail and
SaaS.

**The numbers.** Against a current training-set baseline of 1.17 critical-or-high findings
per site and 12.0 findings in total:

| | Training set | Predicted on the holdout |
|---|---|---|
| Critical + high per site | 1.17 | 0.8 – 2.2 |
| Total findings per site | 12.0 | 10 – 15 |
| Sites that cannot be crawled | 19% | 20 – 30% |
| Any single check above 70% | none | none |

The crawlability range is higher than the training set on evidence gathered before the run:
6 of the 24 refused a plain `HEAD` request outright. Whether they also refuse a declared,
robots-respecting crawler is the open question.

**The three most common findings, predicted:** `no-org-schema`, `no-entity-definition`,
`long-sentences`.

**Directional calls, each with the reason.** These matter more than the totals, because a
threshold that generalises should move for a reason we can state in advance.

| Finding | Call | Why |
|---|---|---|
| `long-sentences` | **up** from 34% | Legal and academic prose is the longest-sentence writing there is |
| `no-org-schema` | **up** from 41% | Universities and hospitals rarely mark themselves up; retailers do it for rich results |
| `alt-missing` | **down** from 48% | Public-sector and healthcare sites carry accessibility obligations retail does not |
| `robots-block` | **down** from 55% | Blocking AI crawlers is a commercial reflex; institutions have less reason to |
| `no-product-schema` | **near 0%** | Nothing here sells a product. This is the applicability guard on trial, not the check |
| `no-date-signal` | **down** from 48% | These are publishing organisations; news and research carry dates |

If `no-product-schema` fires anywhere above a couple of percent, the page-type guard is
broken and that matters more than any threshold in the table.

### Result — the holdout, run 30 August 2026

24 sites, one pass each, scored against the prediction above without editing it.

| | Predicted | Actual | |
|---|---|---|---|
| Critical + high per site | 0.8 – 2.2 | **1.86** | pass |
| Total findings per site | 10 – 15 | **12.6** | pass |
| Sites that cannot be crawled | 20 – 30% | **12%** | miss |
| Any single check above 70% | none | **three** | miss |
| `no-product-schema` | near 0% | **0%** | pass |

Directional calls: **five of six**. Long sentences up, missing alt text down, robots blocks
down, missing date signals down — each for the reason given in advance. The sixth was wrong:
we expected institutions to mark themselves up less than retailers, and `no-org-schema` went
slightly *down*, 41% to 38%.

Predicted top three: one of three. We said `no-org-schema`, `no-entity-definition`,
`long-sentences`; it was `no-entity-definition`, `meta-hygiene`, `weak-corroboration`.

**The crawlability miss is in our favour and worth stating.** Six of the 24 refused a plain
`HEAD` request before the run. Only three refused a declared, robots-respecting crawler.
Identifying yourself properly gets you in where a generic request does not, which is the
behaviour this marketplace argues for and had not previously measured.

**`no-product-schema` at zero across 21 sites that sell nothing** is the result that matters
most here. It is not a threshold passing; it is the page-type guard working. The audit never
asked a hospital for Product markup.

Findings by severity across the whole sample: 5 critical, 39 high, 168 medium, 59 low. Three
of the 21 sites came back with no critical or high finding at all, and the worst had four.

| Category | Sites | Mean critical + high |
|---|---|---|
| University departments | 4 | 1.0 |
| Charities | 3 | 1.0 |
| Law firms | 3 | 1.7 |
| Developer tools | 4 | 2.2 |
| Hospitals | 3 | 2.3 |
| Restaurant groups | 4 | 2.5 |

### Second holdout, prediction written 30 August 2026

21 crawlable sites is a thin basis for a claim about generalisation, and a
holdout costs nothing but machine time as long as nothing is tuned on it. So a
second one, larger and chosen to be harder: 32 sites across eight categories,
none used anywhere before — local news, government, industrial B2B, estate
agents, museums, banking and insurance, edtech, and one category that should
break us.

**The one that should break us.** Every text check here is English. Sentence
length, answer-first sections, imperative call-to-action verbs, and the
"`<Brand>` is a ..." definition pattern have never run against a page that is
not in English. Four of the 32 are French, German, Spanish and Dutch.

| | First holdout | Predicted here |
|---|---|---|
| Critical + high per site | 1.81 | 1.5 – 2.5 |
| Total findings per site | 12.4 | 11 – 16 |
| Sites that cannot be crawled | 12% | 25 – 40% |

The crawlability range goes up because 11 of the 32 refused a plain `HEAD`
before the run — banks and museums run the most aggressive bot management of
any category tried so far.

**On the four non-English sites, specifically:** we expect `no-entity-definition`
to fire on all four, because the copular pattern is English-only, and
`long-sentences` and `fluff-first` to be unreliable. The right behaviour would
be to detect the page language and decline the prose checks with a reason. We do
not do that today. If the prediction holds, that is a limitation to document and
fix, not a threshold to tune — and it is the most likely thing on this list to
be quietly wrong in front of a judge auditing a non-English brand.

**A named failure signal:** if any check fires on more than 70% of the whole
sample, or if the four non-English sites average more than double the findings
of the other 28, the audit is measuring language rather than quality.

### Result — second holdout

| | First holdout | This one | Predicted | |
|---|---|---|---|---|
| Critical + high per site | 1.81 | **1.81** | 1.5 – 2.5 | pass |
| Total findings per site | 12.4 | **12.6** | 11 – 16 | pass |
| Sites that cannot be crawled | 12% | **16%** | 25 – 40% | miss |

The headline numbers came back identical to the first holdout on a sample chosen to be
harder and sharing none of its categories. Two independent samples, 48 sites between them,
same answer: the thresholds are not fitted to the sites they were set on.

Crawlability was over-predicted for the second time, in the same direction and for the same
reason. Eleven of the 32 refused a plain `HEAD`; five refused a declared, robots-respecting
crawler. Being explicit about who you are gets you in.

| Category | Sites | Mean findings | Mean critical + high |
|---|---|---|---|
| Banking and insurance | 4 | 9.8 | 0.8 |
| Government | 4 | 10.2 | 1.0 |
| Industrial B2B | 4 | 11.0 | 2.5 |
| Edtech | 3 | 13.0 | 1.7 |
| Estate agents | 3 | 13.3 | 1.7 |
| Museums | 3 | 14.3 | 1.3 |
| Local news | 4 | 16.0 | 2.8 |

### The non-English prediction was wrong, and it still found two bugs

We predicted the four non-English sites would trip the definition check across the board and
that the prose checks would be unreliable, because every text check here is English. The
failure signal was set in advance: more than double the findings of the other sites.

**It did not happen.** Non-English sites averaged 14.5 findings against 12.4 for the rest —
a ratio of 1.17, nowhere near the 2.0 that would have meant the audit was measuring language
rather than quality. The definition check fired on one of the two crawlable sites, not both.

Two caveats, because the result is weaker than it looks. Only two of the four were crawlable,
so this rests on a sample of two. And the checks are still English-only: they did not produce
nonsense here, but nothing in the code detects the page language and declines, so the correct
reading is "no evidence of harm on two sites", not "handles other languages".

What the category did do is surface two defects nothing else had:

**A brand's own name came back corrupted.** `requests` implements HTTP/1.1 faithfully: a
`text/*` response with no `charset` in the header is ISO-8859-1. Almost nothing on the web
means that — most pages declare UTF-8 in a `<meta>` tag and say nothing in the header. A
Spanish retailer's name therefore arrived with a replacement character inside it and was
printed that way in the evidence line. Decoding now reads the header, then the document's own
declaration, then charset detection, then UTF-8. Every fixture was pure ASCII, which is why
219 passing tests had nothing to say about it.

**A sentinel value was printed as a measurement.** The call-to-action extractor uses one
million to mean "this link exists but its label is not in the body copy", which happens when
the label lives in an `aria-label` or inside an image. A German retailer's report read: *the
first call to action ("discover") appears 1000000 characters into the body text.* It now says
the label is not in the page copy at all, which is both true and a more useful thing to know.

Neither bug needed a non-English site. Both needed a site nobody had built a fixture for.

### Third holdout, prediction written 30 August 2026

40 sites, ten categories, none used in any earlier sample. Chosen against the gaps the first
two left rather than for variety: small storefronts and marketplaces, because the product
page-type bug was found in a fixture and nothing has confirmed the fix on a real shop;
documentation sites, which are enormous, templated and thin per page; local trades, the low
end of the professional web; podcasts, where the transcript check lives; open-source projects,
which have no commercial motive at all; four more non-English sites, because the last attempt
reached only two; and four sites run by accessibility organisations, where alt text and `lang`
should be right and our checks should therefore stay quiet.

| | Holdouts 1 and 2 | Predicted here |
|---|---|---|
| Critical + high per site | 1.81, 1.81 | 1.5 – 2.5 |
| Total findings per site | 12.4, 12.6 | 11 – 16 |
| Sites that cannot be crawled | 12%, 16% | 10 – 20% |

Five of the 40 refused a plain `HEAD`, the lowest rate of any sample so far — small commerce
and open source run far less bot management than banks and museums.

**Three specific calls, each of which can be wrong on its own.**

`no-product-schema` should now fire on real storefronts that lack Product markup. Before the
page-type fix a `/products/<item>` page without the phrase "add to cart" in its crawlable text
was classified as a listing, and listings are never asked for Product markup — so the check
was exempting exactly the sites that needed it. If it fires on none of the eight commerce
sites, the fix did not take.

`alt-missing` and `missing-lang` should stay **quiet on the four accessibility organisations**.
Those sites publish accessibility statements and are audited against them. If we report either
on all four, our checks are wrong, not the sites.

`no-transcript` should fire on at least one of the four podcast sites, and probably not all
four — two of them publish full transcripts. This is the only check in the marketplace that
has never fired on a real site in any sample, so it has never been shown to work outside a
fixture.

**Failure signals, set in advance:** any check above 70% of the sample; the accessibility
category scoring worse than the average; or `no-product-schema` firing on zero commerce sites.

### Result — third holdout

40 sites, ten categories, chosen against the gaps the first two left rather than for variety.

| | Predicted | Actual | |
|---|---|---|---|
| Critical + high per site | 1.5 – 2.5 | **1.34** | miss |
| Total findings per site | 11 – 16 | **12.1** | pass |
| Sites that cannot be crawled | 10 – 20% | **20%** | pass, at the edge |

The serious-findings figure came in *below* the predicted floor, which is a miss even though it
is in our favour. Documentation sites are the reason: four of them averaged 0.2 critical or high
findings between them. They are templated, server-rendered, densely marked up and written by
people whose job is being read by machines. Nothing was wrong with them, and the audit agreed.

| Category | Sites | Mean findings | Mean critical + high |
|---|---|---|---|
| Local trades | 3 | 6.3 | 1.3 |
| Documentation | 4 | 9.2 | 0.2 |
| Podcasts | 4 | 10.5 | 1.2 |
| Recruitment | 3 | 10.7 | 1.0 |
| **Accessibility organisations** | 4 | **10.8** | **1.0** |
| Marketplaces | 2 | 13.5 | 2.5 |
| Non-English | 2 | 13.5 | 2.5 |
| Small storefronts | 4 | 14.8 | 2.0 |
| Open source | 4 | 16.0 | 1.2 |
| Private healthcare | 2 | 18.0 | 1.5 |

### The three named calls

**`no-product-schema` had to fire on real storefronts. It fired on 2 of 6.** The page-type fix
took: before it, a `/products/<item>` page whose crawlable text did not contain "add to cart"
was classified as a listing, and listings are never asked for Product markup — the check was
exempting exactly the sites that needed it. Two of six is the right shape rather than a weak
result: the other four already publish Product markup, because their platform emits it.

**`alt-missing` and `missing-lang` had to stay quiet on the accessibility organisations. They
did:** alt text on 1 of 4, language on 0 of 4. And the category scored *better* than the sample
average, 10.8 findings against 12.1. Four organisations that publish accessibility statements
and are audited against them came out ahead on our accessibility-adjacent checks. That is the
closest thing to an external control this project has.

**`no-transcript` had to fire on at least one podcast site. It fired on none.** This was the
only check in the marketplace that had never fired on a real site in any sample, and the
holdout was set up to find out whether it worked. It half did not.

Reading the four sites by hand splits the result. Two publish full transcripts, so silence
there is correct and the check was right. The other two carry audio players — and the extractor
counted `<video>` elements and video embeds and **never looked at `<audio>` at all**. A podcast
episode page is the clearest instance in existence of content a machine cannot read without a
transcript, and every one of them was invisible to the check meant to catch it.

Worse, an episode page had no page type. `/episodes/<slug>` classified as `other`, which is
excluded from every content check, so even with audio detected the page would have been skipped.
Episodes are now articles — dated published pieces a machine should be able to read, quote and
date — and audio is extracted alongside video, native elements and the common podcast hosts
both.

Neither correction rests on holdout data: `tests/mutations.py` now contains an episode page
with a player and two lines of summary, and the check has to catch it.

### The three checks that broke 70%, and what we did about each

The protocol above says to treat those as broken until proven otherwise, and to read the
findings by hand before touching any threshold. Three checks, three different answers.

**`weak-corroboration`, 76%. Not broken; left alone.** Mean off-site profile breadth is 4.9 on
the training set and 4.9 on the holdout. The threshold is 6, which came from a measured mean
of 7.2 for brands assistants name. Six of 21 sites clear it. 76% is arithmetic on a
`low`-severity recommendation drawn from the strongest signal in this study, not a check
misfiring.

**`meta-hygiene`, 76%. A bucket, not a check.** The 29 findings behind that number were four
unrelated things sharing one tag: page titles, Open Graph tags, a missing `WebSite` type, and
a page with no `lang` attribute. Different fixes, different owners. Split into
`open-graph-incomplete`, `no-website-schema`, `missing-lang` and `microdata-only`. No
detection changed — the same findings fire on the same pages — and the counts now mean
something: Open Graph 57%, titles and descriptions 48%.

**`no-entity-definition`, 81%. A real bug, and it was hiding a second one.** The check
required the *entire* declared name as the subject of the sentence:

> No sentence of the form "Department of Computer Science, University of Oxford is a ..."
> appears in the first 150 words

Nobody writes that. The check was unsatisfiable for any organisation with a long formal name,
which is most institutions. It now tries every form the site asserts for itself, with legal
suffixes stripped repeatedly, so `Acme Holdings, Inc.` also matches `Acme`. Writing the
mutation that proves this fix immediately failed a second check: `schema-text-mismatch` was
comparing the declared name to page text verbatim, so a site declaring a formal name in
markup while writing its trading name in prose was reported as contradicting itself. Both now
use one shared `name_forms()` helper, so a third check cannot reinvent the strict version.

After the fix the check reports 76%, and on inspection it is **correct**. These sites really
do not open with a sentence saying what they are — a developer-tools homepage leads with
"Build, share and run", a law firm with a strapline. That is the central claim of this
marketplace, measured: the most common gap on real sites is also the cheapest one to close,
and it is one sentence.

### On using a holdout twice

The two bug fixes above were found because the holdout pointed at them, but neither was
*justified* by holdout data: each is demonstrated by a case in `tests/mutations.py` that never
touches these 24 sites. No threshold moved. The `meta-hygiene` split changes no detection at
all.

The sample was then re-run to confirm the fixes landed. That second pass is a confirmation,
not a second test, and it is reported as one: critical-and-high moved 1.86 to 1.81, total
findings 12.6 to 12.4, `meta-hygiene` 76% to 48%. Any further change made on the strength of
these 24 sites needs a fresh holdout first — at that point they become training data like the
other 36.

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
