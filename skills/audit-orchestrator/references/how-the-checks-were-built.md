# How the checks were built, and how we know they work

The submission README says what this marketplace does. This says why any of it should be
believed, because a check is only worth as much as the evidence behind the number in it.

Three methods, and they answer three different questions. None of them substitutes for
another, and each one found defects the others were structurally unable to see.

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

