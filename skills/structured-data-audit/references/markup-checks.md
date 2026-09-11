# The markup checks, one section each

`SKILL.md` names the questions in reading order. This file holds the measurement: what each
check reads, every guard, and the real report behind it.

Two companion files carry the vocabularies these checks read:
`references/schema-expectations-by-page-type.md` (which type belongs on which page, and why),
and `references/jsonld-templates.md` (fill-in templates for every type, with the properties
that matter most marked).

---

## The register: eighteen check ids, and what each one settles

These are the exact strings that appear in `checks_run[]` and `not_applicable[]` in every
report, so a reader can tick this list off against a real run. `SKILL.md` groups them into
ten questions; this is the ungrouped set. This skill makes **no network requests at all**.

| Check id | The question | What it reads | Threshold, and where it came from | Ceiling |
|---|---|---|---|---|
| `jsonld-validity` | Does every block parse, and is every value written as JSON rather than HTML? | `jsonld_errors`, plus a walk of every parsed block for HTML character references in values | none | **high** for a block that fails to parse and for values escaped as HTML; **medium** for an empty block, because an empty block breaks nothing that works |
| `organization-markup` | Does the site declare its identity as data? | `Organization`, `LocalBusiness` and subtypes on every crawled page, keeping only nodes that speak for the site; then `name`, `url`, `logo`, `description`, `sameAs` | none numeric | **high** — and this is the tier the earlier version of this file did not record. It is not for *absence*; it is for **identity values that are wrong**: a declared `name` that is really the page's `<title>`, or a declared `url` pointing at a section rather than the site root. Absence anywhere is **medium**, because in the field study 62% of the brands assistants name had none either; missing properties are **medium** |
| `per-location-markup` | Does a page printing its own street address declare a visitable type? | pages carrying an address of the site's own, against `LocalBusiness` and the other visitable types | `BRANCH_PAGE_MINIMUM = 3` — two is a head office and a shop; three distinct addresses is a pattern. Five unmarked pages raises it, and that 5 is chosen, not measured | **high** at five or more, **medium** otherwise |
| `product-markup` | Does a real product detail page declare `Product` or an accepted sellable subtype? | fifteen sellable types, on pages the crawl typed `product` **and** that survived being asked again | `DISTINCT_PRICE_CEILING = 5` — measured: kept 11 of 11 detail pages and excluded 29 of 29 listings on the data it was swept on | **medium**. It leans the right way in the study — 23% of unnamed brands against 6% of named — but on too few sites to justify high |
| `offer-completeness` | Does the `Offer` say what it costs and whether it is available? | `offers`, and `hasVariant[].offers`, for `price`, `priceCurrency`, `availability` | none new. **A price stated only inside a nested `priceSpecification` reads as present** — the property lookup falls through to `priceSpecification` for `price`, `priceCurrency`, `lowPrice` and `highPrice` — and the fix step says outright to add what is missing beside it rather than flattening it, because a `priceSpecification` carries more than a flat price does | **medium** |
| `listing-markup` | Is a shelf being asked for the markup a shelf carries? | pages typed `product` that six independent tests say are lists: a faceted address, headings that are also the labels of links leaving the page, a dated archive path, a pagination query, no words of its own, or eight or more links under one shelf path | `_SHELF_FAMILY_MINIMUM = 8` — above what a related-items strip carries, which is four to eight on both hosted store platforms | **low**, deliberately: a shelf with no `ItemList` is a page a machine has to read as prose, while a detail page with no `Product` markup is the fact itself unstated. Category-typed pages are excluded from the population entirely, because a blog index is a category page and this would otherwise become advice for every index on every site with a blog |
| `article-markup` | Does a post declare `Article`, its dates and its author? | nineteen article types, on pages typed `article` that survived being asked again; then `headline`, `datePublished`, `dateModified`, `author` | none. The price ceiling above is **not** asked of an article: a review of six products prints six prices and is still one piece of writing | **medium** |
| `faq-markup` | Does a page printing answers under question headings declare `FAQPage`? | question-shaped headings with an answer recorded under each, minus anything the template repeats | `CHROME_HEADING_SHARE = 0.5` over `CHROME_MINIMUM_PAGES = 4` — a heading on half the pages belongs to the template, and below four pages there is no "most pages" to measure. `_LINK_TITLE_MIN_CHARS = 12` — "Why?" as a substring of a paragraph-long card matches by accident. `MIN_ANSWERED_QUESTIONS = 2` before any markup is written | **medium** |
| `breadcrumb-markup` | Does a deep page declare `BreadcrumbList`? | the declared types, **and** the Microdata reading, so a site marking it up inline is not told it has none | three deep pages, and half of them already declaring one closes the check | **low** |
| `website-searchaction-markup` | Does the homepage declare `WebSite` + `SearchAction`, from the site's own form? | the search form's `action` and the name of the box people type into | none | **low**, at **medium** confidence |
| `brand-name-in-markup` | Does the site give its own brand one spelling? | the `brand` property on six sellable types, against the `Organization` name and every name the site asserts | `_BRAND_SPELLING_MINIMUM = 2` — one odd spelling is a typo, two is a template | **medium** |
| `jsonld-matches-visible-text` | Does the markup contradict the page? | a declared `Offer` price compared **numerically** against the prices on the page including its `<option>` labels; a declared `Organization` name against the title, headings and text | numeric tolerance 0.01. Name comparison needs three declarations and is **refused across scripts entirely** — a Latin-script company name cannot appear in an Arabic page, so its absence there says nothing | **high**, at **medium** confidence |
| `canonical-declaration` | Are duplicate addresses left with nothing saying which is real? | **two independent detectors, and the first was undocumented until now.** One compares page bodies by three-word shingles across *entirely different paths*; the other finds query-parameter twins on one path carrying the same title with no canonical and no `og:url` | `_SAME_BODY_SIMILARITY = 0.85` of three-word shingles, over `_SAME_CONTENT_MIN_CHARS = 200` — two near-empty pages are not one page, and a site's error template would otherwise fold every 404 into one duplicate group. `PARAMETER_TWIN_MINIMUM = 3` — two is an accident and three is a pattern that will keep producing more, because a filter combination is generated rather than authored | **medium** |
| `title-and-description` | Are titles and descriptions present, unique, readable, and did the template mangle them? | `<title>` and `meta_description` on **every** page the crawl fetched, not only the content-typed ones | `TITLE_MIN, TITLE_MAX = 15, 90` — the upper bound was 75 and flagged ordinary retail titles. `UNSPACED_CHARACTER_WIDTH = 2.5` — measured: a Han or Kana character carries two to three Latin characters of meaning, and counting one for one reported 40 of 57 complete titles on one city government's site as too short. A missing description needs a quarter of the pages and at least two; an off-length title needs 60% and at least four. Two readings need no threshold at all: `_says_nothing` — a tag whose whole content is punctuation, which is a description of `##` on a live site, present and unique and therefore invisible to every count above; and `_escaped_twice` — a character reference still in the parsed value, which an HTML parser resolves on its own, so one that survived was escaped after it was already escaped. **One page is enough for the second**, unlike every count above it: the fault is a template, and the template renders every page | **medium** where a title is missing, duplicated or is the page's own address, where the missing descriptions pass their threshold, or where more than one page carries a working label; **low** otherwise. The title names the fault reaching the most pages, with its count, and how many other faults sit under it. The escaping finding is its own, `meta-values-escaped-twice`, **medium**, owner *developer*, effort *low* — the copy is correct and one filter in one template is not, so the hygiene fix would be wrong advice |
| `homepage-title` | Does the front door name the business or a promotion? | the **leading segment** of the homepage `<title>`, split on the separators and the whitespace-bounded dashes | `_EVENT_SEGMENT_MAX_WORDS = 6` — a campaign banner is short, and a longer opening segment is a sentence about the business. It fires only where the segment carries no form of a name the site asserts for itself — **never a name parsed out of the title, which would make the guard circular** — and matches a closed vocabulary of things that end. "sale" carries a lookbehind excluding "for sale", so a permanent listing is untouched | **medium**: nothing is broken, the page serves and the title parses |
| `open-graph-tags` | Does the page carry the card an assistant and a chat client both read? | `og:title`, `og:description`, `og:image` | 40% of pages | **low** |
| `html-lang-attribute` | Is a language declared, and is it the one the page is written in? | the `lang` attribute against the script the prose is actually in | none | **medium** where the declaration contradicts the letters — a wrong language is worse than none — and **low** where it is simply absent |
| `microdata-and-rdfa` | Is the site relying on inline markup that breaks when the template changes? | three or more Microdata attributes with no JSON-LD block | three attributes, chosen, not measured | **low**. A page whose JSON-LD failed to parse is excluded and named: that is a broken block rather than an absent one, and the validity check reports it |

### Two things the table cannot hold

**The fold.** Where a site publishes **no** structured data at all — no JSON-LD, no
Microdata, no RDFa, **and nothing that failed to parse** — and two or more absence findings
were raised, they collapse into one: *"This site publishes no structured data at all."* The
severity is the most urgent of them, the confidence the least sure, the effort the largest.
Nothing is dropped: the evidence quotes each folded finding's own title and page count, so
the counts are the checks' counts and cannot drift. A broken block stops the fold, because
that owner needs the syntax error found rather than a second block pasted beside it, and a
site with an `Organization` block and no `Article` markup still gets exactly the `Article`
finding.

**Two fix steps still name a shop on a site that is not one.** The `og:image` step reads
*"the article's lead image, the product photograph"* and fires on any page missing the tag,
including a legal page and a careers page. The fold's own generic step names *"the post
template, the product page, the page of questions"* whatever types were actually folded. Both
are live regressions of the rule that swept the same vocabulary out of three other skills, and
they are recorded here rather than left to be found again.

---

## Page type first, always

Every expectation below is keyed to the type the crawl detected — home, about, contact,
pricing, product, category, service, article, faq, location, comparison, press, careers,
legal. This is what stops the skill demanding Product schema from a consultancy or an author
byline from a pricing page.

**And that type is a candidate, not a verdict.** "3 of 3 product pages have no Product markup"
was said about a gift category, a best-sellers list and a search-results address. Ask the page
again before grading it — is it a list of things rather than one of them, does it price more
things than one, was it assembled from a typed query — and let the Product, Article and FAQ
checks share that one answer, naming every page it turns away.

---

## 1. Validity before absence

Any `application/ld+json` block that fails to parse is **high**: invalid JSON-LD is discarded
entirely, so the site has paid for structured data and receives none of the benefit, and nobody
notices because the block is still visibly there. Report the parser's exact message and an
excerpt.

**Where a site publishes none of it — no JSON-LD, no Microdata, no RDFa, and nothing that failed
to parse — the checks below are one condition, not six.** Say it once: name every type that
belongs on the site with the pages and counts each check would have reported, ship the
`Organization` block as the paste-ready one because it is the only one that goes in the template
every page shares, and name the rest as the next step with their counts.

A site carrying any markup at all, or carrying a block that does not parse, gets a finding per
type exactly as below. The fold is for the site that publishes nothing, and a broken block is
markup this site wrote and got wrong.

---

## 2. Organization-level identity

Look across every crawled page for `Organization`, `LocalBusiness` or a subtype.

None anywhere, when a home, about or contact page was crawled, is **medium** — this is the only
place a machine reads the brand's identity as data rather than inferring it from marketing copy,
so absence is worth fixing, but calling it `high` overstates the evidence: in our within-category
study 62% of the brands assistants name also had no Organization markup, and JSON-LD coverage was
actually higher among the brands they ignore.

If present, check the high-value properties: `name`, `url`, `logo`, `description`, `sameAs`.
Missing ones are **medium**.

**A `Person` node on an identity page, with no article on that page**, is the correct identity type
for a personal site and the check declines rather than demanding an Organization.

`sameAs` and `logo` are dropped from the missing list when the site declares no profiles and no logo
anywhere: the check used to reward leaving our placeholder `.../<your-company>` in the snippet and
penalise deleting it.

### Which identity type the snippet declares

Use `Organization` unless the site is somewhere a customer physically goes. `LocalBusiness` is
schema.org's type for a single-location storefront, clinic or restaurant, and choosing it because an
address appeared anywhere on the site handed a global software foundation and an international
relief charity paste-ready markup declaring them local businesses. Nearly every organisation
publishes an address, for legal reasons. Earn `LocalBusiness` from the site declaring a visitable
type itself, publishing opening hours, or having location pages.

### Which string is the name

Use the brand name **as the site spells it**, not as the domain flattens it: a deli whose homepage
writes its name with an apostrophe must not be handed a snippet spelling it the way the domain
label does, without one.

And take the name from the homepage where you can. One charity declared an Organization name on a
single store page out of sixty — its training subsidiary — and that became the identity for the whole
report.

---

## 3. One LocalBusiness per branch

When three or more pages each print a street address no other page prints. **High** at five or more
such pages, otherwise **medium**.

This is the single highest-value markup a multi-location business can add, and there was no check for
it: a pizza chain with sixty branches got a report that never used the word `LocalBusiness`.

Detect the branches by reading the addresses off the pages, never only from the URL slug or from place
markup the site is missing — the markup that would have identified the chain is the markup the chain
does not have, and gating on it means the defect hides itself.

Set `parentOrganization` on each branch to the site-wide Organization, and never reuse the head-office
address across branches: the whole point is that they differ.

**Count an address only where it is a place, and only where it is this site's.** A PO Box is a delivery
point nobody visits, and an address on a page about a person, a book or a supplier is theirs — a
volunteer non-profit was told those three made it a business with separate branches.

---

## 4. Product and Offer

Only if product detail pages were detected.

Missing `Product` is **medium**: it leans the right way in the study — 23% of unnamed brands against 6%
of named ones — but on too few sites to justify `high`.

Present but with no `offers`, or an `offers` object missing `price`, `priceCurrency` or `availability`,
is **medium** — it answers "what is this" but not "what does it cost", which is the question being
asked.

---

## 5. Article

Only if article pages were detected. Missing is **medium**; present but lacking `datePublished`,
`dateModified`, `headline` or `author` is **medium**, because date and author are what let a consumer
decide whether to trust the writing.

**An index of posts is not a post** and the URL never settles which one a page is, so this check asks
the shared page-type answer above — which is what keeps `/blogs/tasting-notes`, `/category/news/` and
`/blog/2025/1/` out of it. All three were reported as posts with no Article markup, and the block
offered gave an archive a `headline`, two dates and an author.

---

## 6. FAQPage

Only on pages that read as an FAQ. **Medium**: question-and-answer pairs are already shaped like the
thing an assistant is trying to produce.

**A page reads as an FAQ when most of its headings are questions, not merely four of them.** A
contributor style guide with headings like "Duplication is evil" and "Code style" was classified as an
FAQ page on that weaker rule, and the generated markup then wrapped those statements in `Question`
nodes.

When you write the snippet, include **only** headings that are actually questions — never a policy
heading, never a legal section, and never a heading paired with a paragraph that is not its answer.
Markup that does not match the page is treated as spam by several consumers, which the finding itself
says. If fewer than two real questions remain, emit a placeholder rather than a fabrication.

**Leave out a heading the whole site carries.** A sidebar widget headed "Questions?" above a phone
number appears on every page, reads as a question, and was published as a `Question` node with an
`acceptedAnswer` on four pages of one site. A heading on half or more of the crawled pages belongs to
the template, not to any one page — the same reasoning that already excludes a question heading which
is the text of a link to somewhere else.

**And require an answer under every question**, before counting it and before marking it up: a page
whose question-shaped headings are the titles of the videos it links to answers none of them, both
guards above miss it, and the block offered has `acceptedAnswer` values with no source on the page.

---

## 7. BreadcrumbList, and WebSite + SearchAction

`BreadcrumbList` on deep pages, and `WebSite` + `SearchAction` on the homepage *only if the site
actually has search*. Both **low**. Declaring a SearchAction for search that does not exist would be
worse than declaring nothing.

Take the target URL and the query parameter from the search form on the page — its `action` and the
`name` of the box people type into are the whole of the target — rather than guessing the common
`search?q=` shape.

**The query field is the box someone types words into, not the first named control in the form.** A
`<select>` is a named, visible field, and a documentation site puts a two-option scope menu — which
index to search — in front of its search box. Taking "the first named visible field" read that menu, so
the report told the site to publish `.../search?s={search_term_string}` when the words go in `q`;
pasting it ships a search URL that returns nothing. Only an `<input>` of a type free text is typed into
counts, so a menu, a checkbox and a date stepper are all passed over. A form with no such field yields
no field name, and the check says so rather than guessing.

**Only from a form that submits to this site.** A `SearchAction` declares where the site's *own* search
runs, so an external engine's results URL is not a sitelinks-searchbox declaration and buys the owner
nothing. Two sites whose "search box" is a general web-search form were told to publish that engine's
address as their target; what is true of both, and worth telling an owner, is that they have no on-site
search index at all.

**Say what the form declares, never what the block printed underneath contains.** The action, the field
name, and the target they build. That target is assembled here, so it is a string no page ever showed,
and the pass that holds back values the crawl did not record replaces it with a placeholder: the
sentence "the snippet below uses the target the site's own search form declares" was printed directly
above `"target": "<target - not found on the site, fill this in>"`.

---

## 8. Agreement with the visible page

This is the check most audits skip and the one that matters most. A missing fact leaves a machine
uncertain; a **contradictory** one teaches it something false with the confidence that structured data
carries.

Report **high** when:

- an `Offer` price is not among the prices shown on the same page — compared **numerically**
  (`480.00` and `$480` are one price), and read from the page's `<option>` labels as well as its prose,
  since a shop hides all but one variant price in a `<select>`;
- an `Organization` name appears nowhere in the page's title, headings or body text.

**And read the `Brand` node.** A `brand` that is a second spelling of the site's own name is markup
disagreeing with markup rather than with the page: **medium**. The guard that keeps every retailer
declaring other makers out of it is in `references/schema-expectations-by-page-type.md`.

---

## 9. Metadata hygiene

Last — and over every page the crawl fetched, not only the content-typed ones. A `<title>`, a meta
description, Open Graph tags and a `lang` attribute are expected on any page a machine can fetch, legal
and careers pages included. Reading only content pages made these checks describe a site from a handful
of it: one report said "2 pages do not declare a language" about a site where 57 of 60 crawled pages had
none.

**Missing or duplicated `<title>`**, and a missing meta description on a quarter or more of pages, are
**medium**. Duplicates usually mean a template dropped its page variable, so fix the template.

**A `<title>` that *is* the page's own address** is that same template one step further on — nothing else
catches it, since it is present, unique and of ordinary length — and weighs the same as no title; one
that opens with an address and then names the page is a milder, different thing.

**Title length** is only reported when **60% or more** of pages, and at least four of them, fall outside
**15–90** characters. The upper bound was 75, which flagged ordinary retail titles, and real titles run
long. Count that length in characters of a script that writes with spaces: a Japanese, Chinese or Thai
character carries two to three of them, and counting one for one reported 40 of 57 complete titles on one
city government's site as too short.

Missing Open Graph tags are **low**, and only over 40%+ of pages; missing `lang` is **low**; Microdata
with no JSON-LD is **low**, because it breaks when the template changes.

**The homepage title is graded separately and must name the business, not a promotion, and no fix step
may print an example `lang` this audit did not read off the site** — a candle shop titled "Warehouse
Sale | \<brand\>" and a Norwegian site told to set `<html lang="en">`, both in
`references/schema-expectations-by-page-type.md`.

### "No JSON-LD block" means none was written, not that one failed to parse

A page's `jsonld` list holds the blocks that parsed, so a page shipping one
`<script type="application/ld+json">` with a syntax error in it looks, from that list alone, exactly like
a page shipping nothing: read `jsonld_errors` before concluding anything from an empty `jsonld`.

One report named the same URL twice — once with "microdata attributes but no JSON-LD block", once as a
page whose JSON-LD does not parse — and both cannot be true of one page. The advice differs: a broken
block needs its syntax error found, a missing one needs the markup written, and broken-block pages are
named by the JSON-LD validity check.

### Duplicate titles that come from parameters are a canonical problem, not a writing problem

One report said 46 of 60 pages share a title, and separately that no page declares a canonical, joining
neither sentence to the other — so it told the owner to write different titles for pages that really are
one page.

Report the trap instead: one path fetched with different query strings, every member carrying the same
`<title>`, and no member declaring a canonical or an `og:url`.
`references/schema-expectations-by-page-type.md` says why each condition matters.

---

## 10. Generate the fix, do not describe it

Every snippet in the report is pre-filled with values already extracted from the site — real brand name,
real URL, real logo, real `sameAs` targets, real prices, real FAQ questions taken from the page's own
H2s. A snippet the owner can paste beats an instruction to "add structured data".

---

# The five rules that govern every check above

## Three notations, one grader

JSON-LD, Microdata (`itemscope`/`itemtype`/`itemprop`) and RDFa (`typeof`/`property`) state the same
things, and a site that states a fact in any of them has stated it.

The crawl reads all three into one node shape, so every check — type presence, property completeness,
markup-versus-page agreement — grades whichever notation the site chose. Reading JSON-LD alone told a shop
whose theme emits `itemprop="price"` that its Product markup omits the price, the availability and the
SKU, all three of them on the page.

Open Graph is not RDFa: `<meta property="og:title">` borrows the attribute for a social card and is never
folded into an entity.

## A value about the brand comes off a page about the brand

Every snippet value is read from the site, and the page it is read from has to be entitled to speak for
the subject.

A restaurant's `LocalBusiness` description came out as a tote bag's marketing copy, because the homepage
had no defining sentence and the search fell through in URL order to a product page where "Brawn's blue
tote **is** not only bold…" matched on the word "is". The same report correctly said elsewhere that no
page states what the brand is.

Read a whole-brand claim from home, about or contact, and from nowhere else.

## And off a node that speaks for the brand

Nested JSON-LD is flattened so a presence check can see it, and a flattened node is indistinguishable
from one the page declared about itself.

A ticket marketplace's Organization block was handed a `streetAddress` lifted out of an Event's
`location` on a performer page — an arena's address, installed as the company's head office, in the field
a consumer treats as ground truth about where a company is.

A `publisher` or `contactPoint` naming the brand is the site speaking about itself; a `location`,
`performer`, `author` or `brand` naming somebody else is not. Apply this to every value a snippet
publishes — address, telephone, email, `sameAs`, logo, founder — not only to the one that was caught.

## A description is pasteable or it is not published

It has to start where a sentence starts and end where one ends.

A national archive's block carried `"description": "En Español The National Records Office is the
nation's record keeper. … available to you, whether"`: the first two words are the label on the
language-switcher link, swept in from the same run of visible text, and the last word is where the
crawl's paragraph limit landed. Nothing in it is invented and none of it can be pasted.

Strip a leading link label, drop a final clause with no terminator — and leave a one-line tagline with no
full stop alone, because that is written whole rather than cut.

### One rule decides what the brand's defining sentence is

`fact-extractability-audit` answers "does any page say in one sentence what this is?" and this skill fills
the snippet's `description` with the answer. Two implementations of one idea, and the weaker one fed the
block a developer pastes: an open-source project's snippet said the organisation *is* "A new minor
release, Example 8.1 "Hoare", is now available for download" — its homepage's top news item — while the
same report printed the about page's real definition three findings earlier.

The rule is now one function, `defining_sentence` in `audit_common.py`, beside the other definitions two
skills share: four sentence shapes, the brand opening its own sentence, a predicate that names a category,
and text that reads as prose rather than as a row of a table. Both skills call it, so neither can drift.
What this skill adds is a filter and never a shape — the news test below, handed to `defining_sentence` as
its `accept` callback.

### A description has to outlive the page it was read off

The block goes into a site-wide template and is then read as the company's identity until somebody edits
it. A sentence naming a version, a release, a specific calendar date or an event is news: true this month,
wrong next quarter.

A founding year is not — "since 1934" is as true next year — so the test is whether the sentence is pinned
to a moment, not whether it holds a number.

Applied to the two descriptions this skill derives, and not to a `description` the site declared on its own
Organization block, which is the site's deliberate statement of who it is.

## A logo comes from something the site presents as its logo

`logo` is the field a knowledge panel renders as the brand mark, so a wrong value is visible to every user
of every assistant that reads it, and an `og:image` is a social card rather than a mark.

The three sources that qualify, in order, are in `references/jsonld-templates.md`; where none exists the
snippet carries an admitted placeholder.

---

## Why the not-applicable reasons are worded as they are

- **No product detail pages were detected.** Product and Offer markup is not expected. Where pages were
  *called* product pages and none earned it, the decline says that instead and names each page and the test
  that turned it away.
- **No page was reached at a second address differing only in its query parameters**, or those twins carry
  different titles and so are different pages.
- **No article, FAQ or location pages were detected**, and no other page carries a question-and-answer
  block — same, per type. An unmarked FAQ on an ordinary page is FAQ content; the report used to say none
  was detected and then recommend writing one.
- **The site has no on-site search.** SearchAction would describe a capability that does not exist. A search
  box is not the only shape that counts: a same-origin link to `/search`, or a form whose action carries
  `?q=` or `?s=`, is on-site search too. Two sites in one batch of runs were told they had none while linking their
  own search page from their own header. A form submitting to another host is the reverse case and does not
  count: say the site has no on-site search index, rather than handing it another site's search URL to
  declare.
- **The place advice is worded for what the site is, and never withheld.** `site_kind` in
  `audit_common.py` answers "is this somewhere you can walk into" once, from the whole structure of the
  site, and both the identity type and the per-place fix read that one answer: an institution whose address
  suffix a registry controls no longer gets a site-wide block declaring the whole body to be a single
  storefront, and a site whose places do not trade is asked for a `Place` block with an address rather than
  a `LocalBusiness` block with a telephone and `openingHoursSpecification` per branch. The finding itself is
  never suppressed — a page printing its own street address and declaring no place markup cannot answer
  "where" for a machine whoever owns it — and an undetermined or low-confidence answer changes nothing at
  all.
- **Fewer than three deep pages were crawled.** Breadcrumb markup would not meaningfully change how the site
  is understood.
- **Half or more of the deep pages already declare `BreadcrumbList`.**
- **No content page returned HTTP 200.**
