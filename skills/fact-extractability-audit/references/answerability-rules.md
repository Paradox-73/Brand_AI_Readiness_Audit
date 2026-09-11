# Answerability rules

What makes a sentence liftable, with worked rewrites. The test throughout: **could this
sentence be pulled out of its paragraph and stand alone as an answer?**

---

## The entity definition

The single most valuable sentence on any site, and the one most sites never write.

An assistant needs to know *what a brand is* before it can say anything else about it. If the
site does not state it, the description gets assembled from whatever a third party wrote —
which is how brands end up described in a competitor's words, or as "a company that provides
solutions".

### The shape

```
<Brand> is a <category> that <does what> for <whom>.
```

Four slots, all load-bearing. The brand name is the grammatical **subject** — not "we", not
"our team", not a pronoun the reader is expected to resolve.

### What the check requires

- A sentence that says what this is. The brand name as the subject of a copular verb (`is`,
  `are`, `was`, `remains`) is the shape to write, and it is not the only one that counts:
  a defining verb (`provides`, `publishes`, `builds`), an appositive after a dash, a colon
  or a comma, a title line — *"Ada Lovelace — Professor, Statistics, Example University."* —
  and the first person, *"We are a bakery on Fossgate"*, all state an identity plainly. A
  personal or departmental site never writes its own name as the subject of anything, and a
  check that demanded it could not be satisfied by any sentence such a site would write.
- The article after the copula is optional. *"Acme is one of the largest branded footwear
  retailers in the country"* and *"Acme is the largest ..."* say what kind of thing this is
  as plainly as *"Acme is a ..."* does, and a rule that reads only the third one is asking
  for a template rather than for a definition.
- **The subject is a name the site writes, not the one it declares.** A trailing "s" is the
  difference between the two far more often than anything else: a retailer declares
  `<Brand>s` in `og:site_name` and in every page title, and writes `<Brand>` as the subject
  of the sentence on its about page that says what it is. Every shape above matched that
  sentence and none of them was tried, so the finding said no page states in one sentence
  what the brand is and the verdict paragraph at the top of the report repeated it. The
  subject pattern now accepts the name with or without that letter — the last word of the
  name only, only where three letters are left, and never on a name already ending "ss",
  where dropping the letter leaves a different word. `names_match` has read a plural as the
  same name since it was written; this is the definition rule catching up with it, and
  `name_forms` is untouched, so no other check inherits a spelling the site never wrote.
- A predicate that says something. *"Acme is a company."* is technically a definition and
  tells nobody anything, so the test is whether the predicate names a category, not how many
  characters it runs to. *"Acme is one of the leading global brands trusted by innovative
  teams"* fails it too: "one" is a function word, not the content word that saves a predicate
  made of nothing else.
- The first person needs the article: *"We are a `<category>`"*, not *"We are open on
  Sundays"*. And not in quotation marks — a customer's *"We are a family of four"* is a
  statement about the customer.
- Inside the **first 150 words** or the opening headings of the homepage or about page.
  A definition below the fold is rarely the passage an assistant reaches for.

### Rewrites

| Before | After |
|---|---|
| "We help ambitious brands unlock their potential." | "Vellum Studio is a brand design consultancy that builds identity systems for companies with 50 to 500 employees." |
| "Welcome to Coppergate." | "Coppergate Ceramics is a pottery in York making wheel-thrown stoneware tableware for home cooks and independent restaurants." |
| "Innovation, delivered." | "Ironbark Supply is an architectural ironmongery merchant supplying hand-forged hinges and latches to restoration builders across the UK." |

Two failure modes, reported differently because the fixes differ:

- **Named but never defined.** The brand appears; no sentence says what it is.
- **Never named at all.** The opening uses "we" and "our" three or more times and the brand
  name does not appear. A machine reading that page cannot tell whose site it is. This is
  common and almost always unintentional — the writer knows whose site it is.

### Where else the same sentence goes

Verbatim, character for character: the about page, `Organization.description`, the LinkedIn
company page, the press kit, app store listings, every directory. Identical wording across
independent sources is the strongest corroboration signal available (mechanism D). Rewriting
it per channel throws that away for no gain.

That list is a marketing department's list, and it is the *list* that is a claim about the
site rather than the instruction. A program has no press kit and a national weather service
has no company page on a professional network — it was advised to claim one, and to claim a
profile on a startup-funding database as well. Where `site_kind` confidently says the site is
none of a business, a shop or an organisation, the same instruction names the places that
kind of site actually has: a repository summary, a package or registry entry, a directory
listing, a profile page. The rule — say the same sentence everywhere something describes you
— does not change, because it is true of everything.

---

## Answer-first sections

On pages a visitor arrives at with a specific question — pricing, FAQ, product, service,
location, comparison — the first sentence under each H2 should **assert something about the
subject**, rather than defer.

An assistant lifts a short passage, usually the opening of the most relevant section. If that
opening is throat-clearing, the passage that gets quoted contains no facts, and a
competitor's page gets used instead.

### The test

Read only the first sentence of each section. Does it assert something a reader could
disagree with, or does it defer?

Warm-up defers in five ways, and every one of them is about something other than the
heading: it greets the reader ("Welcome to..."), sets a scene ("In today's fast-moving
market..."), restates the question ("We are often asked..."), announces what the page is
about to do ("In this guide we will...") or states a belief ("We believe...").

A digit is not the test. *"Discount codes cannot combine with other discounts."* is the
answer, in sentence one, and holds no number. Nor is repeating the heading: on an FAQ page
the H2s are category labels — "Shipping", "Website" — and a good answer never repeats them.
Both of those readings were counted as throat-clearing, and a roaster's FAQ where one section
of five warms up was reported at 100%.

| Section | Fluff-first | Answer-first |
|---|---|---|
| How much does it cost? | "Every project is different, and we believe in pricing that reflects the value we deliver." | "Projects start at £18,000. Retainers are £4,500 per month. Both are quoted fixed, not by the hour." |
| How long does it take? | "We know timing matters to our clients, and we pride ourselves on responsiveness." | "Median implementation is 11 working days from signature to first output." |
| Where are you? | "We are proud to serve customers across the region from our long-standing base." | "Our office is at 418 Harbour Street, Portland, OR 97204. Reception is open 09:00–17:00 Pacific." |

The persuasion is not deleted — it moves. Context works better *after* the fact than before
it, for human readers too.

### What is excluded

Navigational sections — "Related", "Read next", "Where to go next", "See also" — and any
section that is over 70% link text. Judging a related-links block for not opening with a
number would penalise exactly the wayfinding this marketplace recommends elsewhere.

---

## The four core facts

Each is either stated in plain text somewhere on the site, or it is not.

### Which sites are asked for which fact

Two of the four are facts about a body with premises and customers, and they were asked of
everything. A command-line JSON processor — a program, with a source repository, a licence
and a manual — was told:

| Finding | The fix it was given | Why it is unanswerable |
| --- | --- | --- |
| The site never states its founding facts in plain text | Add a short paragraph on the about page: "`<Brand>` was founded in `<year>` in `<place>`." | a program is released, not founded, and not in a place |
| The site never states its location or service area | "`<Brand>` serves customers across `<regions>`." | it has no premises and no customers |

`site_kind` in `audit_common.py` decides what the site is from its own structure — a
registry-controlled address suffix, the identity its markup declares, whether anything is for
sale, whether there is a place to visit, a team or a careers page, whether a repository and a
licence are published, and how the crawled pages divide between documentation and articles.
Its answers are `local-business`, `online-seller`, `organisation`, `project`, `public-body`,
`personal-or-academic`, `publication`, or undetermined.

- A **founding year** is asked of a local business, an online seller, an organisation, a
  publication or a public body.
- A **location or service area** is asked of a local business, an online seller or an
  organisation.
- **Price** and a **contact method** are asked of everything. Every site can be free, and
  every site can be written to.

Three rules make this safe to rely on:

1. **An undetermined or low-confidence answer gates nothing.** The gate is `might_be`, which
   answers True for both, so every site the classifier cannot place is asked for all four
   facts exactly as it was before the classifier existed. Guessing wrong is what produced the
   failures above; a weak guess may not delete a finding.
2. **The expectation is gated, never the observation.** A project that publishes a postal
   address has its address read, quoted and reported found, and `structured-data-audit` still
   asks it to mark that address up. What goes quiet is only the claim that the absence is a
   defect.
3. **A gated fact declines out loud.** The report's appendix carries the check, what was
   concluded, and the evidence it was concluded from: "this site reads as a software project,
   which has maintainers rather than a leadership team and no premises (a source repository
   and a licence are published, nothing is for sale and there is no team, careers page or
   address), so a founding year in a place is not a fact it is expected to state." Silence is
   what that appendix exists to prevent.

**Whose fact it is decides whether it counts.** The sentence quoted for a fact has to be
about this site's owner: written in the first person, or naming the brand. "The
`<common noun>`" — "The museum was founded in 1917" — counts as well, unless the noun is the
opening of another organisation's proper name. A charity's partners page supplied "The
Association of Youths Against Malaria was established in June of 1997" as the audited
charity's own founding fact; it was founded in 2004, and marking the check passed on a
stranger's sentence hid a genuine gap. The same applies to markup: a JSON-LD node naming a
different organisation carries that organisation's founding date, not this one's — and a
`PostalAddress` or `ContactPoint` names nobody, so it is attributed to the node it hangs off.

A currency figure counts as a price only when the sentences around it say what something
costs. "Funds raised US$ 1,115,503,258" beside an appeal for donations is a tally, and it was
printed as a charity's pricing.

**The words the report prints have to contain the fact.** A pattern matching somewhere on a
page is not the site stating anything, and a fact recorded as present does not merely add a
false line — it suppresses the finding that would otherwise fire, so a wrong "found" hides a
true gap. Three reached one batch of real reports and all three failed a test a reader
applies instantly:

| Recorded as | The words printed as evidence | Why it is not the fact |
| --- | --- | --- |
| Pricing | "Find More Events Explore Any Dates This Weekend This Week Next 7 Days …" | a date-filter control with no price in it |
| Service area | "Therefore, we are unable to provide locator services for members still serving on active duty." | what the organisation declines to do, not where it operates |
| Team facts | "CATEGORIES OF INDIVIDUALS COVERED BY THE SYSTEM: Individuals covered by this system include `<Brand>` employees…" | a privacy notice, naming nobody |

So a pricing quote holds a price, a service-area statement names somewhere and is not a
refusal, a team fact names a person, and a founding fact carries a year — and the test is run
against the **trimmed** sentence the report will print, not the original. The first of the
three passed only because the page's text arrives as one unpunctuated run, so the whole page
is a single "sentence" and the figure that matched sat past the 220-character evidence cut.
A claim whose evidence does not contain the evidence is not a claim.

### 1. Price

State the figure. If you genuinely cannot:

> "Vellum Studio does not publish fixed prices. Projects are quoted after a scoping call,
> typically within five working days, and start at £18,000."

An explicit refusal is **quotable**. Silence is not. An assistant asked "how much does X
cost" can repeat the first and can say nothing about the second.

### 2. Location or service area

Full postal address in an `<address>` element on the contact page, repeated in the footer and
in `Organization.address`. No premises? State the service area instead:

> "Brightpath serves retail operations teams across the United States, the United Kingdom and
> Canada."

"Customers" is the half of that sentence that is a claim about the site. Where `site_kind`
says with confidence what the site is, the second half of the advice is written in words its
owner can use: a public body is asked what area it **covers**, an organisation where it
**works**. The address half is identical for all three.

### 3. Contact method

An email address and a phone number **in text**, as `mailto:` and `tel:` links — not only
behind a form. A form is an action a machine cannot take on a visitor's behalf. Add the
response time; an answer time is itself a quotable fact.

Put it in the footer as well as on the contact page. The header, the footer and the
`<address>` element are the parts of a page that speak for the site rather than for what the
page is about, so a detail there is read as the site's own. A detail found only in body copy
still counts — a site that prints its email in a paragraph has printed its email — but the
report says so, because a partners page prints partners' emails in its main content and one
of those was recorded as the audited site's contact route.

### 4. Founding year, and whoever is named as responsible

> "Harrowgate Press was founded in Skipton in 2011 by Eileen Trask. Two editors, one designer
> and one part-time publicist work from the Otley Street office."

These are what distinguish you from another company with a similar name (mechanism D). They
are also the details that make a description feel specific rather than generic.

**A person is named in four shapes, not one.** A role title — "chief executive `<Name>`" — is
how a company writes it. A project, a personal site and an academic page use the other three:
the possessive ("`<Name>`'s mail server"), the attribution ("maintained by `<Name>`",
"founded in 2011 by `<Name>`") and the plain active sentence ("`<Name>` continues to maintain
it"). A mail server's homepage naming its maintainer twice, in two of those shapes, was told
that no named team or leadership detail appears in its page text.

**Only the year is reported missing.** The two halves are separated, and they are not
symmetrical, because the two absences are not equally knowable. A founding year is a
four-digit number beside a founding word: the check can enumerate what it is looking for, so
it can say it is not there. A named person is any capitalised run in a sentence written any of
a dozen ways, and this check has got that wrong in both directions within one batch of
runs. A check may report the absence of something it can recognise; it may not report the
absence of something it cannot. A person found is printed, and lowers the severity of the
missing year — a site that names who runs it is not in the position of a site that names
nothing at all.

**A public body is established, not founded.** It is created by a law or by the body above
it, and that is the fact a reader of its site wants; "was founded in `<year>` in `<place>`"
asks it for a sentence with no author. The finding and its severity are unchanged, and only
the paste-ready sentence moves:

> "The Coastal Warnings Service was established in 1946 under the Coastal Safety Act."

The report says why it tailored itself, quoting the evidence the classifier used, so a reader
who thinks the classification is wrong can see what it rested on and use the other wording.

---

## What a title claiming an absence has to be earned by

"The site never states its founding or team facts" and "The site never states its location or
service area" were the two do-first items of one report, over evidence reading "Across the 1
of 2 page(s) this audit read". The site's about page carries the founding year, the
headquarters and the leadership team. The crawl never reached it: the site's robots.txt asks
for a 30-second delay between requests and that consumed the whole budget.

The body text hedged honestly. The title did not, and a site owner reads the title. So a
title saying "never" or "no page" — this skill has two, the missing core facts and the
missing entity definition — is checked against the sample it rests on. Below half, the title
says how many pages it is speaking for and the severity drops a step.

The denominator is what this crawl could ever have read, not what the site publishes. A
documentation site declaring 32,820 sitemap URLs is not under-sampled by a 60-page crawl that
hit its own ceiling — sixty pages is the whole instrument. It is under-sampled when it
stopped at one, and the snapshot says so three ways: the sitemap's URL count, the number of
page-shaped URLs the crawl reached, and whether the crawl stopped with work still queued.

### A URL is in the denominator until its own response proves it was never a page

"Page-shaped", not "record the crawl wrote". A crawl of sixteen URLs on a static project site
downloaded eight release archives and a patch file; all nine answered HTTP 200 and all nine
were counted as URLs the crawler had reached and could not read. That put the coverage at 7
of 16, under the half-share bar, so the four core-fact findings were replaced by a skip and
the entity-definition finding came out a step milder and scoped to "the 7 pages this audit
could read". Seven of seven pages had been read. Nothing had refused anything.

A URL leaves the denominator only when the response itself proved it was never a page — a
content type that is not HTML on a 2xx, which is currently the only such proof. Everything
else stays, because everything else is a page-shaped URL the audit asked for and did not get:
a 403, a bot-manager challenge, a body this client could not decompress, a 200 carrying no
text, a redirect off the origin. Those are the cases the share exists to catch, and dropping
them would disarm it. `page_shaped_urls` in the report composer is the one implementation;
this skill imports it rather than keeping a second one that agrees today.

Half still separates the two populations under the honest denominator: a refused page stays
in it, so the two blocked sites that produced the original false findings are still 4 of 60
and 2 of 60, and removing responses that were never pages can only raise an unblocked site's
share.

### A check that goes quiet says what was recorded, never what it assumes

The skip read "only 7 of the 16 URLs the crawl reached could be read — the rest were refused
or challenged". Nothing was refused and nothing was challenged; every one of the sixteen
answered 200. The clause now counts the reasons the crawl itself wrote down — the `skipped`
sentence, which is where a body that could not be decompressed says so; a challenge; the
status code; and a 200 that carried no text — so a refusal shape nobody has met yet is
described as accurately as the two that have already cost marks. The same rule applies to the
whole-skill skip, which said "no content pages returned HTTP 200" without testing a status.

---

## Ambiguity traps

### Naming consistency

Pick one written form — capitalisation, spacing, punctuation — and use it everywhere. Declare
the others in `Organization.alternateName` so they are stated as the same entity rather than
inferred as different ones.

The check compares **only names the site asserts as its identity**: `Organization.name` and
`og:site_name`. Names guessed from a `<title>` are deliberately excluded, because
"Brand | Tagline" splits into a brand and a tagline and comparing them would fire this
finding on almost every site on the web.

### Sentence length

Over **40%** of a page's sentences exceeding 30 words is reported at `low`. The bar was 25%,
which fired on 76% of the real sites measured — technical prose runs long, and only a page
where most sentences are unliftable is a defect.

A quotable fact must survive being lifted out of its paragraph. A 40-word sentence carrying
three qualifications cannot be excerpted without changing its meaning, so it tends not to be
excerpted at all. One idea per sentence; keep the occasional long one for rhythm.

Measured only where there is prose to measure. A page that lists other pages is excluded, and
so is one with over half its words inside list items or table cells — which is what a credits
page and a catalogue are made of, and the test that does the work — with an average of over 80
words between full stops as a backstop for a list written as one paragraph of line breaks. A
credits page listing several thousand names and an alphabetical catalogue of authors and
titles were both reported as written in sentences too long to quote, on "averages" of 92.5 and
85.1 words — which is the extractor joining list items that end in no full stop. Neither page
contains a sentence. Both limits sit far above ordinary writing, because reporting genuinely
long sentences is the point of the check.

### Slogan headings

A heading using vocabulary the page never uses again — "Built to fly", "Dream bigger" — tells
a machine nothing about what follows.

The bar is deliberately high: over 80% of at least five judged headings. A good heading often
does *not* repeat itself in its own first sentence — "Starter plan" followed by "$480 per
month" is correct writing — and an earlier, stricter version of this check flagged exactly
that. Headings made entirely of short or common words ("Who it is for") are skipped rather
than counted against the page.

Two whole pages are skipped as well, for reasons that have nothing to do with the threshold.

**A site not in English.** This is a word-overlap score, and word overlap needs words. On a
script that writes no spaces between them there are none to compare, and the check ran anyway
on a Japanese site where its three sibling prose checks — the definition pattern, the
answer-first test and the sentence-length threshold — all correctly declined and said why. It
was called from inside the structural heading check, which runs whatever the site is written
in; it is called alongside its siblings now and declines with them.

**A page that lists other pages.** On a table of contents the headings *are* the content, so
"this heading's words appear nowhere else in the page text" is guaranteed by the page's shape
before anything is measured. A transcription manual's contents page scored 7 of 7 and was
handed the advice to rewrite "Built for speed" as "How fast the platform delivers results",
about seven numbered section titles.

---

## An FAQ block built from the site's own headings

The cheapest large win available to most sites. Take the questions your sales or support team
actually answers, phrase them as customers ask them, and answer each in two or three
sentences leading with the value.

```html
<h2>How much does <product> cost?</h2>
<p><strong><Product> costs $480 per month on the Starter plan and $1,450 on Growth.</strong>
Both include daily delivery and email support. There is no setup fee on any plan.</p>

<h2>How long does setup take?</h2>
<p><strong>Median setup is 11 working days from signature to first output.</strong>
Feed validation is the longest stage at 4 to 6 working days.</p>

<h2>Who is it not a good fit for?</h2>
<p><strong>It is not a fit for single-store retailers or businesses without a digital
inventory system</strong>, because forecasts need at least 12 months of SKU-level history.</p>
```

Then mark it up as `FAQPage` (see `structured-data-audit/references/jsonld-templates.md`),
with the answer text matching the page exactly.

That third heading is the one to keep. A page that says who it is *not* for is treated as a
more reliable source than one that claims to suit everybody — and it is a question people
genuinely ask assistants.
