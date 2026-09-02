# How the checks were built, and how we know they work

The submission README says what this marketplace does. This says why any of it should be
believed, because a check is only worth as much as the evidence behind the number in it.

Nine methods, and they answer different questions. None substitutes for another, and each
one found defects the others were structurally unable to see. The last of them - handing the
shipped zip to agents and having them check every finding against the live page - found more
than the rest put together, and its numbers are reported below whether or not they flatter
the tool.

---


## Low False Alarms

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

## How We Know the Checks Work

Each of these answers a question the others cannot.

**We built the checks from a field study, not from opinion.** 36 sites, six categories,
matched in pairs: three brands an assistant names readily against three real competitors
selling a comparable product at a comparable price that it does not. Pairing within a
category is the point — comparing an encyclopedia against a design agency measures fame, not
anything a brand controls. The study is in
`skills/audit-orchestrator/references/cited-vs-uncited-study.md`, results and failures both.
It moved two thresholds and got nine checks deleted for firing on everything.

**We break one thing at a time to prove each check is specific.** `tests/mutations.py` takes
the clean fixture, breaks exactly one named property, and asserts the audit reports that
property **and nothing else**. Forty-six cases. Site samples cannot do this: on a real
broken site twenty things are wrong at once, so a check can look correct by coincidence.
Controlling the cause is the only way to tell detection from correlation, and it found eight
defects the fixture suite had never touched — including one check that was documented,
listed in every report, and had no implementation behind it.

**We check that our own advice works.** `tests/test_fixes_work.py` breaks the site, reads the
finding, pastes the code the report handed the user into the exact pages it named, runs again,
and requires the finding to be gone. Eight cases, covering Organization, Article, FAQPage and
WebSite markup, `robots.txt` and `sitemap.xml`. Five fixes are prose — "write one sentence
saying what your brand is" — and the file lists them as un-machine-checkable rather than
letting their absence imply coverage.

**We refuse to ship a claim we have not tested.** `tests/test_coverage.py` requires every root
cause in the vocabulary to have a case that produces it, and a companion test fails if any
finding hands out code with nothing proving the code works. **No cause is exempt.** Two were,
each with a recorded reason, and re-reading the reasons found neither survived: the one said to
need a TLS certificate reads a single string, and the one said to need a real Wikidata
collision needs a response *shaped* like one, which a stub supplies with invented labels. An
exemption is a claim that something is unprovable, and it sits in a file being read as settled
by everyone who comes after.

**We measured the thresholds instead of arguing for them.** Every number a check compares
against was set against a distribution, not a hunch: 302 pages across 13 real sites, looking at
what the quantity each threshold cuts actually does in the wild. Two of them turned out to be
unreachable - a page-weight limit 2.4x heavier than the heaviest page we could find, and a
third-party-script limit 2.5x above the worst - which is the same defect as a check with no
implementation, arrived at from the other direction. Both are now reachable and still mean
"this is pathological". The two that were already well placed are recorded as measured: one
fires on 0.6% of real forms, the other on 17% of long pages.

**We gave it to an agent that had never seen it.** The brief says the entrypoint "is the skill
we invoke", so the graded path is an agent reading `SKILL.md` and working out what to do. We
tested that directly rather than testing the commands inside the file: a fresh agent, the
extracted zip, one instruction, no help. It reached a finished report in seven commands with no
guesswork - and found four defects in documentation that everyone who built the project had
stopped being able to see, including a headline number in the README that disagreed with the
tool's own output.

**We test the tool against inputs no site would send on purpose.** A crawler is a network
program, and the failure modes that matter are not in the HTML. Measured against a deliberately
hostile local server: an unbounded response had 125 MB read into memory before its content type
was looked at, and a server sending one byte every nine seconds held a fetch open past 260
seconds, because `requests`' timeout counts silence between bytes rather than elapsed time. Both
are now capped, and `tests/test_budgets.py` keeps them capped. Separately, comparing `HEAD`
against `GET` on real bot-managed sites showed three in eight answering 403 to one and 200 to
the other - which three link checks were reporting as dead pages.

**We had agents audit real sites and check every finding against the live page.** This is
the method that found the most, because it is the only one that tests the thing the report
is for: whether a person acting on it would be doing the right thing. A fresh agent runs the
shipped zip against a site nobody here has seen, opens each finding, fetches the URLs it
names, and marks it **true**, **misleading** or **false**. Misleading is its own verdict
because it is where most of the damage was: a defensible observation with an overstated
scope, a wrong severity, or a fix that would not help.

Three rounds, and the numbers moved:

| Round | Sites | Findings checked | True | Misleading | False | Wrong in some way |
|---|---|---|---|---|---|---|
| 1 | 9 | 36 | 8 | 19 | 9 | **78%** |
| 2 | 8 | 92 | 48 | 33 | 11 | **48%** |
| 3 | 8 | 93 | 53 | 23 | 17 | **43%** |

Twenty-five sites, 221 findings, every one verified against the page it described.

Two things this found that nothing else could.

**The tool contradicting its own captured data.** A homepage reported as having 659
characters of text, in a snapshot that recorded 91,261 for the same URL. A page excluded from
the list of pages with no date because a video player's `<time datetime="PT0S">` counted as
one. A homepage weighed at 2,076,957 bytes that weighs 1,430,541, because the inline script
was added to a figure that already contained it. No unit test catches these, because each
component is behaving exactly as written.

**Fixes that would damage the site.** The worst class of defect here is not a false positive,
which wastes a day. It is advice that leaves the site worse than not running the audit:
paste-ready markup naming a shop without the apostrophe in its own name, a `noindex` removal
that would index a "Page Not Found" page, a whole privacy policy marked up as FAQ
question-and-answer pairs, an address assembled from a nail-polish price and a cosmetic
ingredient code, `sameAs` claiming a co-founder's personal account and an unrelated
encyclopedia article as the brand's own. Nine of those reached a real report before an agent
opened the page and looked.

Round 3 also re-tested the twenty named bugs from round 2 on the same four sites: **sixteen
were gone**, three remained and are fixed here, one could not be told because the page had
dropped out of the crawl sample.

The rate did not fall the way it should have: 78%, then 48%, then 54%. Round 3 was a harder
sample - four of its sites sit behind Akamai or Cloudflare - and a crash shipped that morning
cost three of the four agents a working report. But the honest reading is that fixing findings
one at a time was fixing one site at a time, and the next site broke a different check the
same way.

### What changed: four rules instead of sixty patches

Sorted by which function emitted them, the wrong findings were sixty unrelated bugs. Sorted by
what they violate, almost all of them are four:

**1. "We could not look" is not "it is not there."** The largest class by a distance. It
reached reports as "No XML sitemap is available" on a sitemap that answered 429; as "no rules
to evaluate, which means nothing is disallowed" about a robots.txt answering 403 that contains
a crawl delay and real rules; as "the site never states its founding facts", drawn from four
pages of sixty, while the page that states them sat in the same report's own crawl table
marked 403. Four skills, four separate fixes made, and the next blocked site would have found
a fifth. `compose_report` now holds every absence-claiming finding back when the crawl could
read under half of what it reached, and prints them under **Questions this audit could not
answer** with the reason. Access findings are exempt: they *are* the block, and the only thing
to act on.

**2. A count carries the scale it was drawn from.** "3 page(s) have no H1" on a site where
fifteen did; "2 pages do not declare a language" where fifty-seven did not. The observation was
right; the number was of whatever the check happened to look at, and nothing said so. Every
finding that names pages and no denominator now gets one.

**3. Text that repeats across the site is furniture.** Every selector-based attempt at this
failed identically - the list holds the shapes we have already been burned by, and the next
site uses one nobody wrote down. Stripping `header`, `nav`, `footer` and `aside` missed a
cosmetics retailer's `<div class="promo-bar__text">`, so four locales' worth of free-shipping
thresholds were read as the prices on a product page, and the page's own correct price was
reported as contradicting them. Repetition is what actually defines chrome and needs no
knowledge of any site's conventions: a block on half the crawled pages is removed from all of
them. Measured - footers, cookie lines and banners sit at or near 1.0, the most-repeated
genuine content block observed sat at 0.34, and nothing falls between.

**4. A snippet may only contain what the audit observed.** This is the class that does real
damage, because a snippet is the part of a report people copy rather than read. What reached
real reports: a `streetAddress` of "1 million row" taken from a sentence about database rows,
a postal code taken from the price "$0.00005 / event", another from a cosmetic colour-index
code, a $90 price on a $10 product, and a `sameAs` list claiming a co-founder's personal
account and the encyclopedia article on ACID transactions. Each was fixed at its own source;
they violate one property, now enforced over every snippet any check produces. A value not
found anywhere in the crawl becomes a placeholder, and the reader is told something guessed it.

Each rule lives in one place that sees the whole run, so a check written next month inherits
it. `tests/test_structural_rules.py` pins the rules rather than the sites.

The rules caught a bug in themselves on their first live run. A medical charity is called
Doctors Without Borders, so every finding naming it contained the word "without" and the whole
report was withheld as unverifiable. Quoted names and the brand name now come out of the
sentence before it is read - which is the point of testing a general rule against sites it was
not derived from.

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

## Example Finding

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

