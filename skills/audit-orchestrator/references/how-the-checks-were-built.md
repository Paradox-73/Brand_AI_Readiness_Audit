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
property **and nothing else**. Forty-eight cases. Site samples cannot do this: on a real
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

### Two more rules the fifth round forced

Two more rounds have run since the table above, on the same method and on sites the previous
round's fixes were not derived from. Continuing it:

| Round | Sites | Findings checked | Wrong or misleading |
|---|---|---|---|
| 4 | 8 | 77 | 40% |
| 5 | 10 | 135 | **20%** |
| 6 | 4 | 42 | **18%** |
| 7 | 8 | 84 | 43% |

Round 5 covered a city government, a university department, a German manufacturer, a bilingual
broadcaster, a national newspaper, a public radio programme, a developer-tools company, an
open-source framework, a pizza chain and a single restaurant. Round 6 re-ran the zip built from
round 5's fixes against four more: a gym chain with 276 branch pages, a retailer behind a bot
manager, a humanitarian organisation serving six languages under path prefixes, and an
Arabic-script news site.

Rules 1 and 3 held on every site that exercised them. Rules 2 and 4 each had a bug of their
own, both found by sites they were not derived from, both fixed. Two classes were left that no
existing rule covered, and both are now rules in the same sense - one place, whole-run scope,
inherited by checks not yet written.

**5. A claim about a third party carries its source.** The worst thing this audit has ever
said was ranked second in "Start here", marked high and "do first": *allow-list GPTBot,
ClaudeBot, Amazonbot, Bytespider and Meta-ExternalAgent, because they fetch live pages to build
cited answers.* All five are training crawlers by their operators' own published descriptions,
and Meta-ExternalFetcher - which really does fetch a page because a person asked - sat in the
training group. An owner who followed that advice would have reopened their site to training
collection they had deliberately opted out of, to fix a citation problem that did not exist.

A corrected list would have rotted the same way, so the shape changed instead. `crawl-access-audit`
now holds one record per agent carrying the operator's own words, the URL that states them and
the date it was read; two roles where there was one, because a search index and a live
per-question fetch fail differently; and a fourth role for tokens like `Google-Extended` that
are not crawlers at all and fetch nothing. Every crawler name in the report is rendered from
that table. `tests/test_crawler_roles.py` fails the build if the reference file and the table
disagree, and fails it again if any skill types a crawler name into a string. Four of the five
round-5 sites exercised this; it held on all four.

**6. A value must be observed *and* about the subject.** Rule 4 asks whether a value appears
on the site. It cannot catch a value that appears on the site and is about something else. A
restaurant's paste-ready `LocalBusiness` block came out describing the brand as *"not only
bold in colour but also in versatility - this bag has it all for your every day storage
needs"*: the homepage states no definition, so the search fell through in URL order to a
product page, where a sentence about a tote bag matched on the word "is". The same report
correctly said, in a separate finding, that no page states what the brand is.

The rule is about provenance. A claim about the whole brand may only be read off a page that
speaks for the whole brand - home, about, contact - and a profile only corroborates a brand if
it names the brand. That second half removed a contributor's personal GitHub account and the
Wikipedia article on pure functions from an open-source project's corroboration count, while
its own organisation page, linked dozens of times but only ever as a repository path, had
never been counted at all.

### Two bugs worth naming separately

Both were found by round 5, both were silent, and neither is a heuristic.

**A robots.txt token matches by prefix, not by substring.** A broadcaster ships a legacy
`User-agent: Fetch` blocklist entry, and because "fetch" occurs inside
"Meta-ExternalFetcher" the report said that crawler was disallowed from the whole site. The
group that governs it there is `*`, which disallows nothing. Substring matching cannot be
tuned safe - "bot" is inside almost every crawler name - and prefix matching is what RFC 9309
specifies and what a crawler itself looks for.

**A comparison that can only have one answer is not a measurement.** Two of these, found on
two different sites. A Latin-script company name cannot appear in an Arabic page, so its
absence there says nothing - but the check that compares declared `Organization` names against
the visible text asserted a contradiction at high severity, about markup that was correct and
a page that was correct. And the English prose checks - the 30-word sentence threshold, the
`<Brand> is a ...` pattern, the call-to-action verbs - ran on every content page once a
site-wide majority vote said "English", including the German half of a bilingual broadcaster
whose vote English won 28 pages to 18. Both now read the page's own declaration first: a name
is looked for only where it could be written, and an English threshold measures English.

**A page we failed to read is not a page with nothing on it.** The most credibility-damaging
error this audit can make, and round 6 found it. A news site's liveblog wraps server-rendered
Arabic prose in a nested `<div id="wysiwyg">` that none of the content selectors reach; the
extractor kept 89 of the page's 1,660 visible characters, and two findings followed - *"the
homepage is delivered as an empty JavaScript shell"* and *"too little text to be quoted"* -
about a page that is complete. The fix offered was several days of development work. The
snapshot records both numbers, so the two are now compared: a large shortfall with no state
blob and no empty framework root is a failure of ours, and both checks stand down. A site is
not obliged to use markup we recognise.

**One branch's address is not the company's address.** A gym chain's paste-ready Organization
block carried `"streetAddress": "122 The Broadway", "postalCode": "SW19 1RH"` - the Wimbledon
branch's own address, taken from the single branch page a 60-page crawl reached out of 276, and
presented as the company's. The registered office is four miles and one postcode away, printed
on the site's own privacy page. The telephone reader three functions above already refused to
do exactly this, for exactly this reason, and the guard had never been extended to the address.
On a multi-location site the brand's address now comes only from a node that speaks for the
business; a single-location business is untouched, because there its one place really is the
company.

**A finding may only claim the checks its own function registered.** The list tracking "which
checks might this finding be about" was a run-long accumulator that only ever emptied on an
explicit decline. A check registered in one function and answered by neither a finding nor a
decline sat in it until some unrelated finding later in the same skill swept it up as "fired" -
which excludes it from the passed list without putting it anywhere else. On one site, seven of
seventy-one checks that ran appeared nowhere in the report at all, and among them was whether
robots.txt blocks AI crawlers. The README promises every quiet check says why; those said
nothing. Groups are now keyed by the function that registered them.

### Two more rules the seventh round forced

Round 7 ran the shipped zip against eight sites no earlier round had touched: an online
retailer, an art museum, a Japanese-language postal group, a global law firm, a developer-tools
company, a personal blog, a hospital group and a software documentation site. Eighty-four
numbered findings, forty-eight true, thirty-six wrong or misleading. That is 43%, against 20%
and 18% in the two rounds before it, and it is the honest number: rounds 5 and 6 measured
easier shapes, and the same code scores 43% on shapes it had not met. Every class below was
found on more than one of the eight.

**7. A claim that something is present carries the words that make it present.** The check
that asks whether a site states its prices, its contact routes and its service area passed on
five of the eight sites by coincidence of vocabulary. "Feel free to call" contains "free" and
was read as a stated price. A navigation label reading "Practice areas" was read as a stated
service area. Each false pass is worse than a false finding, because a check that passes says
nothing and so suppresses the true finding underneath it - three of those five sites do not in
fact state a price anywhere, and the report said they did.

No pattern list fixes this; the next site coins a different coincidence. So the shape changed:
every fact the code records as found now stores the sentence that states it, and the line that
reports the check as quiet prints those sentences. A check that cannot quote itself cannot
pass. The same rule made the definition search readable - it now shows which sentence it
believes defines the brand, and the quote table shows the sentence rather than a claim about
one. Two helpers in `audit_common.py` hold it: `sentence_with`, which returns the sentence a
match sits in, and the 15-to-220-character bounds that decide whether a sentence is worth
printing as evidence.

**8. A sentence that says nothing was found may only describe what was looked at.** A global
law firm's report said "No Organization or LocalBusiness markup anywhere on the site" and "too
few pages carry an address to treat this as a business with separate places to visit". The
crawl read 60 pages. The firm's own sitemap lists 2,692 lawyer profiles, and its site lists 31
offices in 26 countries, none of which the crawl reached. Both sentences were true of the
sample and false of the site. Rule 2 sizes a finding against the pages this audit read; this
sizes those pages against the site, which is the number a reader assumes when a sentence says
"anywhere on the site". `state_the_coverage` in `compose_report.py` appends both numbers to
any finding whose wording claims the whole site, when the sitemap is at least twice the crawl.

Three more sentences were the same shape. A skip line said dates were not expected on pages
whose type the classifier had failed to work out, which is a statement about our classifier
printed as a statement about the site; a page with real prose is now content whatever its
type. A refusal cause - always Critical, always "do first" - was asserted from the status code
alone, so a bot manager and an address block produced the same first move; the audit now sends
one further request that separates them, and names which of the two it tested. And the
appendix header promised to show its arithmetic; it now prints it.

**Text that is not the page's own words was read as the page's own.** Five of the eight. A
blog's comment thread, a retailer's review list and a testimonial carousel were counted as the
page's prose, which moved the sentence-length numbers, the definition search and the quote
table. `without_other_peoples_words` removes visitor-contributed regions and keeps chrome - a
footer telephone number is the site's own - and `content_soup` drops them only if 300
characters of the page's own words remain, so a page that is mostly its comment thread is not
emptied into an empty-page finding.

**Measurement that assumed English.** A Japanese postal group's contact page carries its
address in the order prefecture, ward, block, with no word meaning "street" to match on, and
its articles date themselves in kanji as year, month, day; the report said the site stated no
address and carried no dates. An Arabic news page has no capital letter to mark where the next
sentence starts, so the splitter returned the whole page as one sentence and the readability
numbers were nonsense. Japanese and Chinese put no spaces between words, so a word count was a
count of lines. `audit_common.py` now decides what to measure from the script the page is
written in: `dominant_script`, `words_are_separated`, and a sentence splitter with a branch
each for Latin, CJK and Indic-or-Arabic terminators.

Smaller items from the same round, each fixed where it belongs: sitemap totals now say "at
least N" when an index names more sub-sitemaps than the audit read; an image whose alt text is
bound by a template is not an image missing alt text; a heading that is a logo image is a
heading, and its alt text is what it says; the `SearchAction` snippet reads the target and
field name from the page's own search form instead of guessing; and a challenge page is
recognised from the mitigation headers a vendor sets, not only from a phrase list.
`tests/test_round_seven.py` pins all of it - 35 cases, including one that fails the build if
any source file holds a control character, after a shell heredoc silently turned two regular
expressions' word-boundary escape into a backspace byte.

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

