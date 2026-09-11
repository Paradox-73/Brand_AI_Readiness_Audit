# The extractability checks, one section each

`SKILL.md` names the questions in reading order. This file holds the measurement: the
sentence shapes each check recognises, every exclusion, and the real report behind it.
`references/answerability-rules.md` is the companion — the sentence patterns an assistant can and
cannot lift, with worked before-and-after rewrites for each core fact.

---

## The register: ten check ids, and what each one settles

These are the exact strings that appear in `checks_run[]` and `not_applicable[]` in every
report. `SKILL.md` groups them into eight reading steps, and two of the steps there are
cross-cutting rules rather than checks; this is the ungrouped set a reader can tick off
against a real run.

Two of the ten exist only to **refuse**. `core-fact-founding-facts` and
`core-fact-location-or-service-area` are registered when this audit finds something shaped
like the fact and cannot settle whose it is — a year beside a founding word it could not
attribute, an address-shaped run it could not confirm. They produce no finding ever. They
exist so that "the site never states its founding facts" is not printed over a sentence the
same report quotes.

### `entity-definition` — does any page say, in one sentence, what this is?

- **Measures:** in order — the H1 and first two H2s joined to the first 150 words of body
  text on home and about; the line under an H1 that names the brand; the whole readable text
  of home and about; the whole text of every other non-listing page; the `description` on an
  `Organization` or `WebSite` node; the `meta_description`. The prose shapes come from
  `defining_sentence` in `audit_common.py`, the one function `structured-data-audit` also
  calls for the `description` of its paste-ready block, so the two skills cannot give one
  site two identities.
- **Thresholds.** `DEFINITION_WINDOW_WORDS = 150` — an assistant reads the top of a page
  first, and a definition below this is rarely used. `_LINE_REPEAT_LIMIT = 3` — the same
  number the report composer uses to decide a sentence is the site's furniture, for the same
  reason: a block repeated across three pages belongs to the theme rather than to any one
  page. The heading-definition window (4 to 25 words, within 300 characters) is **chosen, not
  measured**; the rule behind it is argued in the code and the three numbers are not.
- **Declines — and this is the check's distinguishing behaviour.** Before it may deny that
  any page defines the brand, it asks a second, subject-agnostic question: does *some*
  subject get defined here under a name the site calls itself? Where the answer is yes the
  check refuses the absence claim outright and says so — *"The sentence exists; the two names
  disagree, which is what the brand-naming check reports. Claiming no page states what the
  brand is would be false."* It also refuses to say where a definition sits when no homepage
  was read. Where a definition exists only in markup, the pass says so: *"though no paragraph
  on the site says it where a person would see it."*
- **Ceiling:** **high**, and only where the brand name is never stated at all and the opening
  says "we" or "our" three or more times; **medium** otherwise; one step milder again where
  the crawl read under half the page-shaped URLs it could have. Confidence `high`.
  `mechanism="B"`, `root_cause="no-entity-definition"`.

### `definition-on-the-homepage` — is the definition where a machine fetching one page will find it?

- **Measures:** raised only when `entity-definition` found a definition somewhere other than
  the top of the homepage. Before firing it re-reads the homepage independently, through a
  second detector that shares nothing with the first.
- **Threshold:** none numeric. The severity is a lookup on where the definition sits.
- **Declines:** where the homepage's own markup declares a definition, the finding is not
  raised at all, and the reason quotes that declaration.
- **Ceiling:** **medium**, and only where the definition is on another page entirely;
  **low** where it is further down the homepage. Confidence `high` — one page, read in full,
  twice, by two detectors that share nothing, so this one is not scaled to the sample.
  `mechanism="B"`, `root_cause="no-entity-definition"`.

### `heading-hierarchy` — is there a top-level heading at all?

- **Measures:** every crawled page, not only the content-typed ones, minus soft-404s and
  unrendered stubs. A page counts as having none only where **both** `headings.h1` (the
  visible document) and `headings_in_markup.h1` (the whole markup) are empty.
- **Threshold:** none. Presence or absence.
- **Declines:** multiple H1s and skipped heading levels are **measured against nothing** and
  the pass says so in as many words. Reporting them fired this check on 27 of 29 real sites.
- **Ceiling:** **medium** at medium confidence — nothing corroborates it, and an H1 this
  audit cannot see is still an H1. **low** when neither the homepage nor a quarter of the
  crawled pages is affected. The title states the count. `mechanism="C"`,
  `root_cause="heading-structure"`.

### `headings-name-their-topic` — do the H2s use words the page uses?

- **Measures:** each H2's content words against the whole page's text, matched on a
  six-character prefix so an inflected form still counts. The minimum word length comes from
  `dominant_script`: four letters for Latin, Cyrillic and Greek, two otherwise.
- **Thresholds.** `SLOGAN_HEADING_SHARE = 0.8` — nearly every heading using vocabulary the
  page never uses again. `SLOGAN_MIN_SECTIONS = 5` — below five headings the share is one bad
  heading rather than a pattern. The six-character prefix is **chosen, not measured**.
- **Declines:** listing pages, where the headings *are* the content and the result is
  guaranteed by the page's shape before anything is measured; headings made entirely of short
  or common words, which carry no vocabulary to match; and — the important one — where no
  page reached the five-heading floor, the reason says the vocabulary **could not be
  measured** rather than printing a favourable answer about text nobody split into words.
  Declines with the other three English checks on a non-English site.
- **Ceiling:** **low** at medium confidence — a vocabulary overlap is not a reading of
  meaning. `mechanism="B"`, `root_cause="heading-structure"`.

### `answer-first-paragraphs` — does the first sentence under each heading assert or defer?

- **Measures:** pages typed pricing, FAQ, product, service, location or comparison. Per
  section, the first sentence is tested against `DEFERS_RE`, a closed English vocabulary in
  five categories: greeting, announcing intent, restating the question, setting a scene, and
  stating a belief. **A sentence the deferring vocabulary does not recognise counts as an
  answer** — a check that accuses a page of saying nothing has to be able to show the
  warm-up.
- **Thresholds.** `ANSWER_FIRST_MIN_SECTIONS = 3` — below three sections the share is noise.
  `FLUFF_FIRST_SHARE = 0.7` — 50% fired on 69% of real sites, because a section opening with
  context is normal writing. `FLUFF_FIRST_MIN_OFFENDING = 4` — a share on its own is not a
  quantity; below four offending sections there is no pattern whatever the share.
- **Declines:** navigational sections and any section over 70% link text, because judging a
  related-links block for not opening with a number would penalise exactly the wayfinding
  this marketplace recommends elsewhere. Gated on the language check.
- **Ceiling:** **medium** at medium confidence, single tier. `mechanism="B"`,
  `root_cause="fluff-first"`.

### `core-facts-present` — are price, place, contact route and origin stated in text?

- **Measures:** four facts, each read two ways and recorded **with the sentence that states
  it**. Price: a currency figure whose surrounding sentence says it is what something costs,
  an explicit "contact us for pricing", an explicit "free", or a declared `offers.price`.
  Location: a postal-code-bearing address run, a declared `PostalAddress`, or an English
  service-area statement. Contact: a `tel:` or `mailto:` href, a click-to-chat link, a
  declared `contactPoint`, or a number in the header, footer or `<address>` of any page.
  Origin: a year beside a founding word, a declared `foundingDate`, or a named person in one
  of four shapes.
- **Thresholds.** `CORE_FACTS_MIN_COVERAGE = 0.5` — the measured one, and the reason is
  recorded: *"below that the sample is smaller than the part it is describing, and the
  missing half is exactly where an about page or a contact page would be. Measured against
  the two blocked sites that produced the false findings — 4 of 60 and 2 of 60 readable — and
  against every unblocked site in the same batches of runs, none of which fell below 0.9."*
  `ABSENCE_TITLE_MIN_COVERAGE = 0.5` — below this share a title saying "never" says how many
  pages it speaks for instead, and the severity drops a step. `PRICE_STATEMENT_MIN = 12`
  characters — a statement that something costs nothing is complete in three words, while the
  facts the general floor was set for need a clause around them. `ADDRESS_LINE_WINDOW = 48`
  characters — room for a locality and a county and no more, the same window the extractor
  uses to decide which postal code belongs to which street.
- **Declines.** Six kinds, and they are most of the value of this check. **Coverage:** under
  half the URLs the crawl reached readable, and whether the site states these facts *could
  not be determined*. **Attribution undecided:** where a founding year or an address-shaped
  run was found and this audit could not settle whose it is, the absence is refused and
  registered under its own id. **Kind:** the founding year is asked only of something that
  could be a business, shop, organisation, publication or public body, the location only of
  the first three. **Language:** the service-area pattern and the founding-and-team patterns
  are English, and on a non-English site the report says *"not checked"* and names the
  pattern rather than reporting the fact absent. **Applicability:** nothing for sale means no
  price to be missing. And the pass counts **verified** facts separately from ones merely
  declined, so four-of-four can never be printed over facts nobody checked.
- **Ceiling:** **high**, and only twice — a missing price on a site that has a pricing page
  and prints a currency figure, and a missing location on a site carrying LocalBusiness
  signals or location pages. Contact method tops out at **medium** and has no high branch at
  all. Founding facts are **medium**, or **low** where a named person was found. Confidence is
  `medium` for every one of them: an absence claim in this check is never high confidence.
  `mechanism="B"`, `root_cause="missing-core-fact"`.

### `core-fact-founding-facts` — a refusal marker, never a finding

Registered when the text names a year beside a word that puts something into existence and
the audit cannot establish that the sentence is about this site's owner. Its only output is
the decline that says so.

### `core-fact-location-or-service-area` — the same refusal for a place

Registered when a comma-separated hierarchy of place names with a code in it — the shape an
address takes where it is not written as a house number and a postal code — was found and
could not be confirmed as the site's own.

### `brand-naming-consistency` — do the names the site *asserts* for itself agree?

- **Measures:** `authoritative_variants`, which is `Organization.name` and `og:site_name` and
  nothing else. **A name split out of a `<title>` never enters this set** — "Brand | Tagline"
  splits into a brand and a tagline, and treating the tagline as a variant would fire this
  finding on almost every site on the web. Every string the finding quotes is one of those
  declared variants, printed beside a page that declares it; a test fails the build otherwise,
  after a report quoted a variant that neither named source produced.
- **Thresholds:** none. The comparison is exact after normalisation through the shared
  `name_forms` / `comparison_key`, so there is no similarity score to tune. The structural
  floors — two variants to compare, two names within a language group — are logical minimums.
- **Declines:** where the site asserts **no** name at all, this is explicitly not a pass:
  *"That absence is reported as a finding in its own right rather than counted as a pass
  here."* Names on pages declaring different languages are compared **within** a language and
  the reason names which; a name declared only under one path prefix and never on the
  homepage is that section's name and is set aside by name.
- **Ceiling:** **medium** at medium confidence. There is no high or low branch.
  `mechanism="D"`, `root_cause="name-inconsistency"`.

### `long-sentences` — is the prose too long to lift?

- **Measures:** `readability.long_sentence_share`, the share of a page's sentences running
  over 30 words, computed by the extractor with a sentence splitter that branches on script
  — Latin, CJK, and Indic-or-Arabic terminators — so an Arabic page with no capital letters
  is not returned as one sentence.
- **Thresholds.** `LONG_SENTENCE_SHARE = 0.4` — 25% fired on 76% of real sites; technical
  prose runs long, and only a page where most sentences are unliftable is a defect.
  `PROSE_MAX_LIST_TEXT_SHARE = 0.5` — an article with a bulleted list in it runs at 0.1 to
  0.4; a credits page or a catalogue *is* the list and runs at 0.9 to 1.0.
  `PROSE_MAX_AVG_SENTENCE_WORDS = 80` — 80 rather than 40 because the longest-winded real
  writing this audit has measured averages around 60, and what must not be reported is a list
  joined into one string, which arrives at 85 and 92. The eight-sentence floor for a page to
  be measurable is a bare literal with no justification in the code.
- **Declines:** a page that lists other pages; a page whose text is mostly list or table
  cells; a page with fewer than eight sentences. Gated on the language check.
- **Ceiling:** **low** at medium confidence. There is no escalation path.
  `mechanism="B"`, `root_cause="long-sentences"`.

---

## 1. The entity definition

Start here; it is the single most valuable sentence on any site.

Search the first 150 words plus the opening headings of the homepage and about page, then the whole
text of every crawled page, for a sentence of the form `<Brand> is a <category> that <does what> for
<whom>` — the brand name as grammatical subject, a verb, and a predicate that says something. "Acme
is a company." is technically a definition and tells nobody anything, so the test is whether the
predicate carries a content word rather than how long it is.

The question is whether any sentence says what this is, not one template. **Five shapes count**,
because English writes it five ways and each was found on a site told it has none:

- the copula, with or without the article: `<Brand> is a ...`, `<Brand> is one of ...`
- an appositive set off by a dash, colon, pipe or comma — including a title line with no function
  words in it at all, `<Name> — Professor, Statistics, Example University`
- a defining verb: `<Brand> provides / offers / publishes / builds ...`
- the first person: `We are a ...`, `I am a ...`. A personal or departmental site never makes its own
  name the subject, and requiring that made the check unsatisfiable for it. The article after the verb
  is required, so "We are open on Sundays" is not a definition, and a quotation mark disqualifies it —
  a customer's "We are a family of four" is not.
- a declaration in markup: the `description` on an `Organization` or `WebSite` node, which is a
  definition by construction and is read as one rather than re-parsed as prose.

One determiner may stand in front of the name, and no more. `The <Brand> is a ...` and `Each <Brand>
is a ...` are the site defining itself; `Billing for <Brand> is monthly` is not, and the difference is
the whole check. Neither a bracketed alias — `<Brand> (<ALIAS>) is a ...` — nor a trailing "s" breaks
the match: a site declares `<Brand>s` in `og:site_name` and writes `<Brand>` in the sentence that
defines it. Three sites were told at high confidence that no page states what they are, over a leading
"The", a bracketed acronym and that letter.

Distinguish two cases, because the fixes and the severities differ:

- **the brand is named but never defined — medium.** This is the common case; it holds for most sites,
  including ones assistants name readily, so it is a strong recommendation rather than a severe defect.
- **the opening uses "we" and "our" three or more times and never states the brand name at all —
  high.** A machine reading that page cannot tell whose site it is.

When a definition *is* found, quote it back in the not-applicable reason. Showing the sentence that
works is as useful as flagging its absence.

The prose shapes above are one function, `defining_sentence` in `audit_common.py`.
`structured-data-audit` reads the same function for the `description` of its paste-ready Organization
block, and calls it with one extra filter of its own — a description that goes into a site-wide
template has to outlive that page's news cycle. When the rule was written twice, one report gave two
answers about one site's identity: this finding quoted the about page's definition while the snippet
three findings below published the homepage's top release announcement as what the organisation is.

---

## 2. Heading structure

One H1 per page, naming the subject rather than a slogan. Only a page with **no H1 at all** is
reported, at **medium**.

Read the headings twice — from the visible document and from the whole markup — and count a page as
having none only when both agree. A search or answer engine reads the DOM, and CMS themes routinely
ship a screen-reader-only `<h1 style="display:none">` naming the page exactly as this check asks; two
sites were told pages of theirs carry no H1 on the strength of one.

More than one H1 is valid HTML5 sectioning and a skipped heading level is near-universal on real
sites; neither stops a machine reading the page, and reporting them fired this check on 27 of 29 real
sites and drowned the findings that matter, so both are measured against nothing and the pass text
says they were not counted.

Judged across every crawled page, not only the content-typed ones — release notes and catalogue
indexes typed `other` were silently outside the sample — and a page whose own title says it is an
error page is left out, because it has no heading for the reason that it has nothing to head.

---

## 3. Slogan headings

Compare each H2's content words against the whole page's text. A heading using vocabulary the page
never uses again ("Built to fly", "Dream bigger") is a slogan.

Report only when **over 80%** of at least five judged headings are disconnected, and skip headings made
entirely of short or common words ("Who it is for") — they carry no vocabulary to match and are not
evidence either way. The bar is deliberately high: a good heading often does not repeat itself in its
own first sentence, and "Starter plan" followed by "$480 per month" is correct writing, not a defect.

This is a **word-overlap score, so it is one of the English-only checks** and declines with the other
three on a site declaring another language. It used to be run from inside the structural heading check,
which runs whatever the site is written in, and so was the one that escaped the gate: a Japanese
manual's contents page was reported for slogan headings, about seven numbered section titles, on a
script that writes no spaces between words.

**A page that lists other pages is excluded** for a second reason: on a table of contents the headings
*are* the content, so "these words appear nowhere else in the page text" is guaranteed by the page's
shape before anything is measured.

---

## 4. Answer-first sections

On pages a visitor arrives at with a specific question — pricing, FAQ, product, service, location,
comparison — read the first sentence under each H2 and ask whether it defers or asserts.

Warm-up defers: it greets the reader, sets a scene, restates the question, announces what the page is
about to do, or states a belief. An answer asserts something about the subject, with or without a
number in it. A digit is not the test and neither is repeating the heading: "Discount codes cannot
combine with other discounts." is the answer, and on an FAQ page the H2s are category labels
("Shipping", "Website") that no good answer would repeat. A roaster's FAQ was reported as 5 of 5
sections warming up; one of them was.

A sentence the deferring vocabulary does not recognise counts as an answer — a check that accuses a
page of saying nothing has to be able to show the warm-up.

Report the share that are "fluff-first" when it exceeds **70%**, across three or more sections and at
least four offending ones. Half fired on 69% of real sites: a section that opens with context is normal
writing, so only a page that almost never leads with a fact is worth reporting.

**Exclude navigational sections** ("Related", "Read next", "Where to go next") and any section that is
over 70% link text: judging a related-links block for not opening with a number would penalise exactly
the wayfinding this marketplace recommends elsewhere.

---

## 5. The four core facts

Each is either found — with the URL *and the sentence that states it* — or missing. A fact recorded as
found without a quotable sentence is not found: "feel free to call" contains "free" and was read as a
stated price, "practice areas" as a stated service area, and each false pass silently removed the true
finding underneath it. When the check passes, print the sentences in the not-applicable reason, so a
coincidence of vocabulary is visible in the report instead of hidden behind a pass.

### The words the report prints have to contain the fact

A pricing quote holds a price, a service-area statement names somewhere and is not a refusal, a team
fact names a person, a founding fact carries a year.

Test that against the *trimmed* sentence the report will print, not the original: an event page whose
text arrives as one unpunctuated run is a single 4,000-character "sentence", and the figure that matched
sat past the evidence cut, so "Find More Events Explore Any Dates This Weekend This Week Next 7 Days …"
was printed as the site's stated pricing. In the same batch "Therefore, we are unable to provide locator
services for members still serving on active duty" was printed as an organisation's service area, and a
Privacy Act notice's "individuals covered by this system include `<Brand>` employees" as its leadership
fact. One rule covers all three, and each of them hid a real gap as well as printing a false line.

### A fact quoted for the brand has to be a fact about the brand

The subject of the sentence must be this site's owner: first person, or the brand's own name. "The
`<common noun>`" counts too — "The museum was founded in 1917" on a museum's own site — but only where
the noun is not the opening of somebody else's proper name. A charity's partners page supplied "The
Association of Youths Against Malaria was established in June of 1997" as the audited charity's founding
fact, seven years before it existed, and that pass hid the fact that the site states no founding fact of
its own.

The same rule applies to markup: a node naming a different organisation is that organisation's node, and
so is an address or a contact point hanging off one — the extractor records the name above each nested
node, so a partner's postal address is attributable too.

**But a sentence that names nobody belongs to whoever the sentence before it was about**, and on the
site's own page that is the site. Read one sentence at a time, the subject test says no to every sentence
whose subject is a pronoun, which is how English writes the second sentence of a paragraph: a museum's
about page reads "`<Brand>` is unlike any other. Founded in 1884, it houses … more than 500,000 objects",
and the report said no founding year appears in the page text or the page markup — while quoting the
sentence directly in front of that one, from the same paragraph, as the brand's own description. Carry
the attribution forward down the page and drop it the moment a sentence names somebody else.

And **a currency figure is a price only when it is what something costs**. A lifetime fundraising total
printed beside an appeal for gifts is neither. Read the sentences around the figure, not the figure.

### Price

Or an explicit "contact us for pricing" statement. Missing on a site that *has* a pricing page is
**high**; missing entirely is **medium**. An explicit refusal to publish prices is quotable; silence is
not.

**Charging nothing is a price**, said in the words a visitor uses and not only the words a licence uses:
"Entry is free", "Free admission", "Free, no booking required". A museum printing two of those was told at
high severity that no price statement appears anywhere on it, by a vocabulary written for software. The
other sense of the word does not count — "free to cancel", "free from ads" — and a statement this short is
still a whole statement, so the floor on quotable evidence is lower for this fact than for the others.

### Location or service area

A postal address, or a statement of where the business operates. Missing on a site with LocalBusiness
signals or location pages is **high**.

**A street can be named without a number.** Museums, colleges, hospitals and most named buildings have no
house number, and a pattern requiring one told a museum, as its single high-severity finding, that it never
states its location — over two crawled pages carrying "`<Brand>`, Thornbury Road, `<Town>` KT7 4LP". What
makes it an address rather than a phrase is the postal code beside it, not the word "Road".

**And the quoted address has to be a string the page contains.** A street read from one part of a page and
a postal code read from another are two facts, not one line: a coffee retailer's report quoted "27 Monmouth
Street SE16 4RA", a string on no page of that site, joining one shop's street to a different shop's postcode
four miles away. Quote the run the page prints between them, or quote one half alone.

### Contact method

An email or phone number in text, not only behind a form.

Read the header, the footer and any `<address>` element of every crawled page first: the template puts those
on every page, so a detail there is the site's own. Fall back to the whole page text only when no page states
one in its chrome — a site that prints its email only in a paragraph has printed its email — and say which of
the two readings found it. A partners page prints partners' emails in its main content, and one of those was
recorded as the audited site's contact route.

A run of digits is not a telephone number if its ISBN check digit computes, if it is thirteen digits with no
separator at all, or if the words in front of it name it as another kind of code. A university's book-review
page printed an ISBN under a caption saying so, and it was accepted as the university's contact route, which
suppressed a true finding.

### Founding year, and whoever is named as responsible

These are what distinguish this brand from another with a similar name.

A person is named in four shapes and only one of them is a job title: the possessive ("`<Name>`'s mail
server"), the attribution ("maintained by `<Name>`", "founded in 2011 by `<Name>`"), the plain active
sentence ("`<Name>` continues to maintain it") and the role. A mail server's homepage naming its maintainer
in two of those was told no named team or leadership detail appears on it.

**A year beside a founding word is not always an origin.** "Founded", "established" and "incorporated" mean
one thing; "since" is equally the far end of a count. An open-source project's report printed "The number of
issues `<Brand>` has in `<Tool>` … is now lower than it has been since 2016" as its founding fact — a
sentence that names the brand, so the attribution test passes it — and that false pass hid the true finding
that the project states no founding year at all. Where the sentence is comparing or counting something, a
bare "since" is the span of the count.

**Only the missing year is reported.** A year is a four-digit number beside a founding word, so the check can
enumerate what it looks for and can say it is not there; a named person is any capitalised run in a sentence
written a dozen ways, and this check has got that wrong in both directions within one batch of runs. A
check may report the absence of what it can recognise, not of what it cannot. A person found is printed and
lowers the severity of the missing year.

---

## 6. An absence claim has to be earned by the sample

Two findings ranked do-first read "The site never states its founding or team facts" and "The site never
states its location or service area", over evidence saying "Across the 1 of 2 page(s) this audit read" — on a
site whose about page carries both, and which the crawl never reached because robots.txt asks for a 30-second
delay.

Below **half** of what the crawl could have read, a title saying "never" or "no page" says how many pages it
is speaking for instead, and the severity drops a step.

The denominator is every **page-shaped URL** this crawl could have read — a refusal and an empty 200 count, a
response that proved it was never a page does not — capped by the crawl's own ceiling, so a 60-page sample of
a 32,820-URL sitemap is not scoped down.

---

## 7. Ambiguity traps

### Naming consistency

Compare only names the site *asserts* as its identity — `Organization.name` and `og:site_name`. Never compare
names guessed from a `<title>`: "Brand | Tagline" splits into a brand and a tagline, and treating the tagline
as a variant would fire this finding on almost every site on the web. A second name already declared in
`alternateName` is not a contradiction; the site has said the two are one entity.

Two more exclusions, both from real reports:

- **Compare within one language.** A name on a page declaring `lang="en"` and a name on a page declaring
  `lang="ja"` are a translation, and telling a Japanese university to pick one spelling of its English and
  Japanese names is nonsense.
- **A name declared only under one path prefix, and never on the homepage, is that section's name.** A
  documentation subsite or a campaign microsite declares its own `og:site_name` — unless every declared name
  is section-scoped, in which case there is no site-wide name to measure against and none may be dropped.

Say in the decline which names were set aside and why.

### Jargon density

Over **40%** of a page's sentences exceeding 30 words is **low**. A quotable fact must survive being lifted
out of its paragraph, and a 40-word sentence carrying three qualifications cannot be excerpted without
changing its meaning. The bar was 25% and fired on 76% of real sites — technical prose runs long, so only a
page where most sentences are unliftable is a defect.

**Establish that the page contains prose before measuring it.** A page that lists other pages is excluded, and
so is one whose text averages over 70 words between full stops or carries an internal link for every 8 words:
a credits page of several thousand names and an alphabetical author catalogue were both reported as "written
in sentences too long to quote" on averages of 92.5 and 85.1 words, which is the extractor joining list items
that end in no full stop. The two limits sit far above ordinary writing on purpose — legal and academic prose
really does run to 40 and 60 words a sentence, and reporting that is what this check is for.

---

## 8. Do not double-report

Facts locked in images, PDFs or video belong to `render-readability-audit`. This skill only asks whether the
fact is stated in text.

---

## Why the language gates are worded as they are

**The site is not in English.** Four checks reason wholly about English sentences: the definition pattern,
the answer-first test, the sentence-length threshold and the heading-vocabulary overlap. On a site declaring
another language they decline and say so, rather than guessing. Heading order and name consistency are
structural and still run.

**Core facts are the mixed case, and calling the whole check structural was wrong.** The address reading, the
declared JSON-LD and the contact routes run in any language. The service-area pattern, and the founding-year
and named-person patterns, do not: they are English, the check knows it, and on a non-English site it reports
those facts as **not checked** — naming the pattern that could not be applied — rather than reporting them
absent. That is the difference between a limitation and a false finding, which is why it is written out here
rather than left inside the phrase "structural".

**The site declares no language and is not written in Latin letters.** A declaration is one way to know the
language and not the only one. A plain-HTML library with no `lang` attribute on any page was measured with the
English matchers and told, at medium severity, that no page says in one sentence what it is and that no
founding year appears anywhere — about a homepage whose one Cyrillic sentence states both. Text overwhelmingly
in Cyrillic, Greek, Han, Kana, Hangul, Arabic, Hebrew, Devanagari or Thai is not English whatever the markup
says or fails to say, and the same four checks decline, naming the script. The script is named and the language
is not: Cyrillic is four languages and picking one would be an invention.

**The site is not the kind of thing the fact belongs to.** `site_kind` in `audit_common.py` reads what a site is
from its own structure — a program with a repository and a licence, a body under a government or university
address suffix, a publication whose output is articles. The founding year is asked only of a site that could be
a business, a shop, an organisation, a publication or a public body; the location or service area only of the
first three.

A command-line JSON processor was told "The site never states its founding facts", fixed by "`<Brand>` was
founded in `<year>` in `<place>`", and "The site never states its location or service area", fixed by "`<Brand>`
serves customers across `<regions>`" — it is a program, and neither sentence is one its owner could write.

Each decline names what was concluded and the evidence behind it. An undetermined or low-confidence answer gates
nothing, so a site the classifier cannot place is asked for all four facts exactly as before, and a fact the site
*does* state is still read and reported whatever kind it is: what is gated is the expectation, never the
observation. The same reading picks the words for the facts that stay — a public body is asked when it was
**established** and under what, not who founded it in what place, and an organisation is asked where it **works**
rather than which customers it serves.
