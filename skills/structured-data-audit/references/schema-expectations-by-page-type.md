# Schema expectations by page type

Expectations are keyed to the page type detected during the crawl. This is the single
largest false-positive guard in the marketplace: without it, every site gets told to add
Product markup, and a consultancy is marked down for not selling anything.

---

## Page types and how they are detected

Detection runs in a fixed order, and the order is deliberate.

**1. Homepage** — path is `/` with no query string.

**2. Strong URL slugs** — these win outright, *before* structured data is consulted:
`legal`, `careers`, `press`, `comparison`, `faq`, `pricing`, `contact`, `about`, `location`.

A pricing page that also carries `Product` + `Offer` markup is still a pricing page. Letting
the markup win would classify it as a product detail page and make every downstream
expectation wrong — the site would be asked for a SKU and an availability status for a
pricing table.

**3. Declared JSON-LD types** — the site owner asserting what a page is beats guessing from
its URL: `Product`/`ProductGroup` → product, `FAQPage`/`QAPage` → faq,
`BlogPosting`/`NewsArticle`/`Article` → article, `LocalBusiness`/`Store`/`Restaurant`/`Hotel`
→ location.

**4. Remaining URL patterns** — `article`, `product`, `category`, `service`, with two
corrections applied:
- A `/products` **listing** is a `category`, not a `product`. Product detail requires a price
  plus at least two buying affordances ("add to cart", "in stock", "free shipping", "select
  size"…). Generic commerce words like *quantity* and *SKU* are deliberately excluded — they
  appear in ordinary B2B copy and were turning service pages into product pages.
- `/blog` is a `category`; `/blog/a-post` is an `article`. A section index is a list of
  links, and demanding `datePublished` and an author from it would be wrong.
- The URL cannot finish that job. Only a fixed list of English section words is recognised
  in it, and a shop platform lets the owner name each blog handle (`/blogs/tasting-notes`)
  while a blogging platform puts the section word first (`/category/news/`). Before grading
  a page as an article, ask the page: a page whose subheadings are also the labels of links
  to other pages is an index whatever its URL says.

**5. Content fallback** — four or more question-shaped H2s → `faq`. Otherwise `other`.

---

## The type is a candidate, and the check asks again

Steps 1 to 5 are a guess made from an address and a first pass over the text, and every
grading mistake this skill has made on a real site has the same shape: a check read the guess
and treated it as settled. So the guess is re-asked, once, by every check that grades a page
against an expectation keyed to its type — Product, Article and FAQ share one answer, and a
page turned away is named in a signal and in the reason the check declined.

**A list of things is not one of the things.** Any one of these is enough: the address names a
grouping (`/collections/<handle>`, `/category/<name>`); the subheadings are also the labels of
the links leaving the page; the address ends at the date it archives; the page carries no
words of its own, only other pages' titles.

**A page that prices many things is a shelf, not an item.** Five or more different amounts of
money on a page called a product page. This is the shared library's own measured separator —
11 of 11 detail pages kept and 29 of 29 listings excluded on the data it was swept on — and
step 4 skips it whenever the path is two or more segments deep and any price appears, which is
every grid on a site that puts a language and a country in front of its paths. Asked again
here, it applies wherever the address sits. Never asked of an article: a review of six
products prints six prices and is one piece of writing.

**A page assembled from a typed query is not a page the site published.** A search-results
address is dropped before any check in this skill runs, and out of the denominator of every
"N of M pages" the skill prints. There is no markup it is failing to carry: the document
exists for the length of one query, and a `Product` block added to the template that renders
it would be published on every search a visitor ever runs.

What this cost, before the checks asked: a gift category, a best-sellers list and a
search-results address were reported together as "3 of 3 product pages have no Product
markup", on a shop whose real product pages were not among the three. And on the article
side, a shop platform's `/blogs/tasting-notes`, a blogging platform's `/category/news/` and
five monthly archives at `/blog/2025/1/` were each reported as posts with no Article markup,
one of them listing a single post — with a paste-ready block giving the January archive a
`headline`, a `datePublished`, a `dateModified` and a `Person` author, and
`mainEntityOfPage` pointing at the archive.

---

## One page reached at several addresses

A path fetched more than once with different query strings, where every member carries the
same `<title>` and no member declares a `<link rel="canonical">` or an `og:url`. All four
conditions are load-bearing:

- **the same path** — `/a` and `/b` are two pages however alike they read;
- **different query strings** — the parameters are the only difference between them;
- **the same title** — the page's own statement that these are one page. Without it,
  `?id=41` and `?id=42` on a catalogue would be caught, and canonicalising those onto one
  address would delete every item but one from every index that lists them;
- **nothing declared** — a group naming a canonical is already fixed. Whether the canonical
  it names resolves is `crawl-access-audit`'s question, and that skill declines honestly when
  a crawl carries none to resolve.

Three addresses is the floor: two is an accident, three is a template, and a filter
combination is generated rather than authored so there is no limit to how many exist.

The remedy is `<link rel="canonical">` on the listing template, naming the address with the
filter and sort parameters removed — never a robots.txt block, because a URL a crawler may
not fetch is a URL whose canonical nobody reads, so the duplicates stay and the signal that
would have merged them is hidden.

What this cost: one report said 46 of 60 crawled pages share a title, and separately that no
page anywhere declares a canonical, and joined neither sentence to the other. The 46 were one
listing reached through filter and sort parameters. The advice printed under the duplicates —
fix the template that omits the page variable — could not work, because the template omits
nothing.

---

## What is expected of each type

| Page type | Expected | Severity if absent | Never expected |
|---|---|---|---|
| **home** | Organization or LocalBusiness; WebSite + SearchAction *if the site has search* | medium / low | Product, Article, author |
| **about** | Organization (site-wide is fine) | medium | Product, Offer, dates |
| **contact** | Organization with address and telephone | medium | Product, Article |
| **product** | Product + complete Offer; BreadcrumbList | medium / medium | Article, author, dates |
| **category** | BreadcrumbList | low | Product, Offer, dates |
| **pricing** | prices in text; Offer or Product optional | — | Article, author |
| **service** | Service or Organization; BreadcrumbList | low | Product, Offer, dates |
| **article** | Article with datePublished, dateModified, author; BreadcrumbList | medium | Product, Offer, price |
| **faq** | FAQPage | medium | Product, Offer, dates |
| **location** | LocalBusiness with address, telephone, openingHours | high at 5+ unmarked branch pages, else medium | Product, Article |
| **comparison** | Article or Organization | — | Product, Offer |
| **press** | Organization | — | Product, Offer |
| **careers** | — | — | anything |
| **legal** | — | — | **anything at all** |

Two rows carry most of the weight:

**`legal` expects no markup, ever.** Privacy policies and terms pages are excluded from
`CONTENT_TYPES` entirely — no schema check, no CTA check, no freshness check. A terms page
with no Organization markup, no call to action and a 2019 date is a normal terms page.
Reporting it would be pure noise, and the volume of it would drown the real findings.

**The exception is HTML hygiene.** The title, meta description, Open Graph and `lang` checks
run over *every* page the crawl fetched, legal and careers included, because a `<title>` and a
declared language are expected of any page a machine can fetch and cost nothing to add.
Reading only the content-typed pages made those checks describe a site from a handful of it:
one report said "2 pages do not declare a language" about a site where 57 of the 60 crawled
pages had none, and someone fixing the two named would have left 55 behind.

**`careers` expects nothing.** Job listing pages have their own vocabulary (`JobPosting`)
that is outside this audit's scope, and asking a careers page for Product markup is absurd.

---

## A site with none of it has one condition, not six

The table above is a table of *types*, and on a site that publishes no structured data at all
every row of it fails at once. Measured on this repository's own fixture site with the JSON-LD
removed: six findings came out of the whole audit, all six from this skill —

```
no-article-schema     2 of 2 article pages have no Article markup
no-faq-schema         1 page of questions and answers has no FAQPage markup
no-org-schema         No Organization or LocalBusiness markup anywhere on the site
no-product-schema     The one product page crawled has no Product markup
no-breadcrumb-markup  Deep pages have no BreadcrumbList markup
no-website-schema     On-site search exists but is not described in WebSite markup
```

— each true, each shipping its own paste-ready block, and together handing a site owner six
chores for one condition. Counted per skill, 40% of the report came from one place; a
non-expert reading it saw six problems where there is one.

`_fold_absent_markup` in `scripts/check.py` runs after every check and replaces those findings
with one, titled **"This site publishes no structured data at all"**.

**Nothing is dropped.** The evidence quotes each folded finding's own title verbatim and its
own page count, so the counts cannot drift from the checks that produced them — they *are*
those checks' counts — and a reader still sees that two article pages and one product page
are involved. The severity is the most urgent of the six, so folding cannot quietly demote a
medium finding to a low one; the confidence is the least sure of the six and the effort the
largest, so it cannot over-claim in the other direction either.

**One block, not six.** The `Organization` snippet is the paste-ready one, because it is the
only one of the six that belongs in the template every page shares. Pasting an `Article` block
there would publish an `Article` node on the homepage. The remaining types are named in the
fix steps with their page counts, and each comes back as a finding of its own — with a block
filled in from the site's own values — as soon as the site carries any markup at all. That is
not a promise on trust: `tests/test_fixes_work.py` pastes the identity block, re-audits, and
asserts each deferred type reappears with a block and clears when that block is pasted.

**Two states this does not apply to**, both load-bearing:

- **A site with some markup.** An `Organization` block and no `Article` markup is a specific,
  single-type defect and gets the `Article` finding exactly as the table says. Folding
  whenever several types are missing would hide a real defect behind a claim that is false of
  the site.
- **A site whose markup does not parse.** A broken block is markup this site wrote and got
  wrong; its owner needs the syntax error found, not a second block pasted beside it. Those
  pages are already set aside from every "no X markup" count by `_set_aside_unparsed_markup`
  and reported under `invalid-jsonld`, so a page carrying one stops the fold. The
  `no-schema-site` fixture is exactly this case — one product page with a double comma in its
  block — and its golden file still requires `invalid-jsonld`, `no-org-schema`,
  `no-product-schema` and `no-faq-schema` as four separate findings.

---

## Organization is site-wide, not page-wide

`Organization` markup is looked for **across every crawled page**, not on each one
individually. It is conventionally placed in a site-wide template, so a site with it in the
footer partial satisfies the check everywhere at once. Reporting each page separately would
turn one problem into twelve findings for one fix.

The finding fires only when a home, about or contact page was actually crawled — if the
crawl never reached an identity page, there is nowhere the markup would be expected and the
check records itself as not applicable.

---

## High-value properties

Present-but-incomplete is `medium`, not `high`. The markup works; it is less useful than it
could be.

| Type | Properties checked | The one that matters most |
|---|---|---|
| Organization | `name`, `url`, `logo`, `description`, `sameAs` | **`sameAs`** — it is how a machine confirms the company on this site is the company on those profiles (mechanism D) |
| Product | none — the Product node itself is not graded on properties | the Offer inside it is what gets read |
| Offer | `price`, `priceCurrency`, `availability`; an `AggregateOffer` is graded on `lowPrice`, `highPrice`, `priceCurrency` instead | all three; a Product with no priced Offer answers "what is this" but not "what does it cost" |
| Article | `headline`, `datePublished`, `dateModified`, `author` | `dateModified` and `author` — currency and attribution |

---

## Agreement beats presence

The check that matters most is not whether markup exists but whether it is **true of the page
it sits on**.

A missing fact leaves a machine uncertain. A contradictory one teaches it something false,
with all the confidence that structured data carries — and it will be repeated. Reported at
`high`:

- an `Offer` price not among the prices shown on the same page;
- an `Organization` name appearing nowhere in the page's title, headings or body.

Price comparison is **numeric**. Comparing as strings meant `480.00` in the markup and `$480`
on the page were reported as a contradiction, which was a false positive found by running the
clean fixture.

Two guards keep this check honest: it only fires when the page shows at least one price, and
it lists every price it did find in the evidence, so a reader can see immediately whether the
audit simply missed a range or a sale price.

**Scope boundary.** `structured-data-audit` owns name and price mismatches.
`freshness-corroboration-audit` owns telephone, postal address and description boilerplate.
The two do not overlap, so neither reports the other's territory.

---

## The `Brand` node: markup that disagrees with markup

A leather-goods shop declared `"brand":{"@type":"Brand","name":"<one string>"}` on 12 product
pages, a second string on 2 more, and a third string in its `Organization` block. Three
spellings of one brand, in the site's own markup, across 14 pages — and no check in this skill
read the `brand` property at all. The audit's only naming finding came from another skill and
quoted a pair of strings that could not be found in any of the four sources it named.

`Brand.name` is the site's own assertion about who makes what it sells. Disagreeing with
`Organization.name` is the site contradicting itself in the notation that is read as fact.

**What is read.** The `brand` property of every `Product`, `Service` and other sellable node
on every crawled page, as a bare string or as a `Brand` object, whichever the template emits;
every `Brand`-typed node beside them, including one the extractor lifted out of its parent;
and the `name` on the block that speaks for the site. Every string the finding quotes comes
out of one of those three. A finding that names its sources and then quotes something from
outside them is unverifiable, which is worse than silent.

**The two guards, both required.**

1. *A declared brand is compared only when it is a spelling of a name the site asserts for
   itself* — matched with `names_match` against the `Organization` `name`, `og:site_name` and
   the variants the crawl recorded. A camera shop declaring a different maker on each product
   is publishing correct markup about other companies, and `names_match` — which requires
   every word of the shorter name to appear in the longer — says so. Without this guard every
   retailer on the web is reported.
2. *The `Organization` name joins the comparison only when every brand declared on the site is
   the site's own.* A retailer that stocks a house brand may legitimately name it differently
   from the shop, and reporting that would be this check making exactly the unverifiable claim
   it exists to replace.

One page carrying an odd spelling is a typo; two is a template. Below two pages the check
declines and says why.

**The fix is a template fix.** One spelling in `brand.name`, the same string on the
`Organization` block, or — where shop and brand really are two entities — one `Brand` node
with an `@id` that every product's `brand` points at, since one declaration cannot disagree
with itself. Other spellings go in `alternateName`.

---

## The homepage `<title>`, and examples that are guesses

### The front door titled after a promotion

A candle brand's homepage `<title>` was `Warehouse Sale | <brand>`. That is the string an
assistant reads as what the front page of the business is, and it names a promotion that ends
instead of the business that stays. The audit said nothing about it: the word "Warehouse"
appeared 0 times in the 70 KB report.

Three title tests already existed and none of them asks this. Length asks how much a title
says; duplication asks whether two pages share one; `_name_is_really_a_page_title` asks the
opposite question, whether a declared `name` is a title. A homepage title is the one title
with a fixed job, and a seasonal campaign in it is a defect the owner fixes in a minute and
would otherwise never hear about.

**The test.** Split the homepage title at the characters a theme divides a title with — the
vertical line and its fullwidth form, the guillemets, the colon, the bullet, and a dash with
whitespace on both sides so a hyphenated name stays one segment — and grade the **leading
segment**. That is where the answer to "what is this site" belongs, and it is what survives
truncation.

**The guard matters as much as the test.** `<brand> — handmade candles` is a correct and very
common homepage title: a name followed by a genuine descriptor. What separates it from a
promotion is that its leading segment is the business, so a leading segment carrying any form
of a name the site *asserts* for itself is never reported, whatever else is in it. Asserted
names only — the `Organization` `name`, `og:site_name`, the crawl's recorded variants. A name
split out of a page title would make the guard circular: "Warehouse Sale" read as a name would
match the segment it came from.

Two further guards:

- a leading segment longer than six words is a sentence about the business, not a campaign
  label, and is not graded;
- `\bsale\b` and nothing looser, so "wholesale" and "sales training" are untouched, with a
  lookbehind on `for sale` — the one common phrase in which the word describes a permanent
  business rather than an event, as an estate agent and a used-car dealer both write it.

**The vocabulary** is a promotion (sale, clearance, discount, N% off, free shipping, Black
Friday, Cyber Monday, buy one get one, limited time, last chance) or a not-trading-yet state
(coming soon, opening soon, under construction, under maintenance, closing down, grand
opening), in English, plus the unmistakable single word for a clearance sale in six other
European languages. It stops there on purpose: a word short enough or common enough to collide
across languages would fire this finding on titles that are correct. The finding's `checked[]`
states the limit, so a reader whose promotion is named in another language knows this check
did not look for it.

### An example value is read off this site or it is not printed

A Norwegian site was told, verbatim: "Set the lang attribute in your base template, for example
`<html lang="en">`." `en` is not the language that site is written in, and a reader following
the example literally declares their Norwegian pages English — creating the exact defect this
audit reports elsewhere as `lang-contradicts-page-text`.

`detect_site_language` in `audit_common.py` returns the language **and how it knows**, and the
fix step follows it:

| What the record holds | What the step prints |
|---|---|
| a `code`, from `lang` attributes elsewhere on the site | that code, saying how many pages already declare it |
| a `code`, inferred from the words | that code, said as this audit's reading of the text and not something the site states |
| a `script` and no `code` | **no example code at all**, and a sentence saying the owner has to supply the language because this audit cannot tell which it is |
| neither | the same: no code, and why |

The third row is the point. The record deliberately returns no `code` for Cyrillic, because
Cyrillic is Russian, Ukrainian, Bulgarian and Serbian, and guessing one would be worse than
the missing attribute. A step that guessed anyway would throw that judgement away at the last
moment.

The region subtag follows the same rule. "such as en-GB or pt-BR" named two regions chosen for
no reason connected to the site: the *shape* of a region subtag is worth stating, and which
region this site publishes for was never read, so it is not asserted.
